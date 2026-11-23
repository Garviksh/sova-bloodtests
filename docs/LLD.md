# Sova Program Blood Tests — Low-Level Design (LLD)

Version: revision 00058 (23 Nov 2026) · Owner: Garviksh (garviksh@sova.health)

One Python service turns Shopify program orders into Redcliffe home blood tests, talks to customers only through approved WhatsApp templates sent by Briza, keeps every event in BigQuery, and gives the ops team a dashboard that also carries the legacy "Program Clean" Google Sheet.

---

## 1. Runtime and deployment

| Item | Value |
|---|---|
| Hosting | Google Cloud Run service `sova-bloodtests`, region `asia-south1`, project `sova-production` |
| Runtime | Python 3.12, `functions-framework`, entry point `app(request)` (Flask request) |
| Resources | 1 vCPU, 1 GiB memory (raised from 512 MiB after one out-of-memory crash on 20 Nov) |
| Storage | BigQuery dataset `sova-production.sova` (no other database) |
| Secrets | Cloud Run env vars and Secret Manager (see §9); never in code |
| Deploy | `gcloud run deploy sova-bloodtests --source . --function app --base-image python312 --region asia-south1 --memory 1Gi`; the full test suite (`python3 test_main.py`, 650+ assertions) runs first and blocks the deploy if it fails |

### Source layout

| File | Lines | Role |
|---|---|---|
| `main.py` | ~3,000 | Routing (`app`), business logic, BigQuery access, integrations (Shopify, Redcliffe, Briza, Google APIs), jobs, security |
| `ui.py` | ~1,500 | Every HTML page as Python string templates; one shared stylesheet (`CSS`); no JS framework |
| `legacy_bt.py` | ~450 | Parsing the legacy Google Sheet: status/lab normalisation, row → test mapping, matching to orders, the old Apps Script dashboard maths |
| `test_main.py` | ~1,900 | End-to-end tests with fake BigQuery, fake HTTP (Shopify, Redcliffe, Briza) and fake Google APIs |
| `DESIGN.md` | — | Design tokens and component rules for the dashboard |

---

## 2. Component view

```
 Shopify (sova.in, sova.health) ──webhook / hourly read──►┐
                                                          │
 Customer (WhatsApp links) ──/b /v /c /r pages──────────►  sova-bloodtests (Cloud Run) ──►  BigQuery sova.*
                                                          │        │   │   │
 Ops team (browser) ──dashboard pages + forms───────────►┘        │   │   └──► Briza send-template API ──► WhatsApp
                                                                   │   └──────► Redcliffe API (slots, bookings, status, PDFs)
 Cloud Scheduler: bloodtests-tick (hourly :10)  ─────────────────►│◄───────── Redcliffe webhook /rcl-hook/<key>
                  bt-legacy-sync (hourly :17)   ─────────────────►│
 Cloud Run job bt-reports (11:00, 13:00, 17:00) ─────────────────►│──► Google Sheets/Drive (read-only), Cloud Scheduler + Logging (read-only, insights)
 Claude weekday task (12:00 Mon–Fri) ──/insights?key=───────────►┘
```

---

## 3. Data model (BigQuery `sova-production.sova`)

Append-only: rows are never updated in place; the newest row for a key wins. A test's state is **never stored** — it is folded from the program row plus its log rows every time (`test_state`).

| Table | Key | Columns (main) | Written by |
|---|---|---|---|
| `bt_programs` | `program_id` = order + SKU (`57014-PR-CGH-120`; split orders keep members) | customer_name, phone (`91XXXXXXXXXX`), email, address, city, state, pincode, lat, lng, product, sku, days, source, order_name, purchased_at, added_at, members | `program_row()` from `consider_order()`; test/linked/custom profiles |
| `bt_log` | `id` (insert id = dedupe) | at, program_id, test_no, kind, status, note, booking_id, collection_date, slot, by_user, detail (JSON) | `log()` — streaming insert |
| `bt_skus` | sku | days (0 = not a program), name, rule | `sync_skus()`, `set_sku()` |
| `bt_config` | key | value, at, by_user | `set_cfg()` |
| `bt_results` | program_id + test | report link, biomarkers, PDF text | `save_result()` |
| `bt_legacy` | sheet + row_no | cells (JSON list), batch, imported_at, imported_by; plus `<tab>|types` and `__ops_html` rows | `legacy_store()` — load jobs |
| `bt_removed_programs` / `_log` / `_results` | as source | same columns as the live tables | `purge_tests()` backups |
| `dashboard_users` | email | role (admin/view), pw_hash, active, at, by_user, action | shared with the SovaX desk |

### `bt_log.kind` values

`msg` (automatic WhatsApp) · `msg_manual` (Send WhatsApp page) · `booked` · `rescheduled` · `cancelled` · `rcl` (Redcliffe status / report) · `click` (customer opened a link) · `verified` (OTP passed) · `reply` · `followup` · `profile` (address, age, gender, package) · `start` / `end` (program dates; `detail.via` = `sheet` | `csv` | `profile`) · `shift` (test profiles) · `closed` · `linked` · `report_view` · `check` (11/13/17 summaries) · `legacy_sync` · `legacy_import` · `resync` · `purge` · `sheet_dates` · `error`.

### Settings (`bt_config` keys)

`send_mode` (off | test | live) · `test_numbers` · `live_from` · `max_sends_per_run` (200) · `quiet_minutes` (30) · `t1_delay_days` (1) · `report_link_days` (30) · 12 × `*_template` (Briza names) · `wa_hold` · `rcl_env` (staging | prod) · `rcl_prod_host` · `prod_package_codes` · `package_codes` · `contact_phone` · `contact_email` · `briza_chat_url` · `removed_programs` · `orders_synced_in` / `orders_synced_health` · `dates_csv_last`.

---

## 4. Core domain logic

### 4.1 Which orders are programs

`consider_order(order, store, known)` keeps a line item when its SKU maps to ≥ 90 days in `bt_skus` (`guess_days()` reads the SKU suffix; Sova X `SU-SX-*` = 0). Orders before `live_from` are added only while their Test 2 is still ahead. Ids in the `removed_programs` setting are never re-added.

### 4.2 Program dates

`program_start(p)` = latest `start` log row, else the purchase date. `program_end(p)` = latest `end` row, else start + days.

`sheet_dates(apply)` reconciles every program with the legacy sheet:

1. Read the Blood Test tab (`LB.legacy_rows`) and Program-Main (`LB.ops_data`).
2. Match by order key (split `-1/-2` rows included), else by phone with the sheet's paid date within 7 days of purchase.
3. If the preferred tab's rows disagree on the start date, mark **sheet rows disagree** and leave the program alone.
4. A date set by the team (`via` = profile or csv) wins.
5. Otherwise, write `start` + `end` rows (`date_rows`, `via` = `sheet`).

The hourly `legacy_sync()` calls `sheet_dates(apply=True)` after copying the tabs.

### 4.3 Tests and windows (`tests_for`, `test_state`)

| Test | Opens | Due |
|---|---|---|
| Test 1 | purchase + `t1_delay_days` (skipped for pre-go-live orders and repeat buyers whose previous Test 2 was done) | opens + 5 |
| Test 2 | end − 20 days | end − 15 days |
| Custom (personalised) | when the team creates it | — |

`test_state()` folds the log into one dict: status, booking (latest booked/rescheduled not cancelled), reschedules used, ask (open customer tap), failed send, report link, tracking link, t1_at (when its booking WhatsApp went), bookable, opens, due.

**Statuses:** `upcoming` → `to_send` → `awaiting` → (`followup` after 48 h) → `booked` → `check` (Redcliffe unclear) → `collected` → `report`. Exits: `missed`, `cancelled`, `closed`, `untracked` (before tracking), `handed` (moved to a linked user).

### 4.4 Messages (`plan`, `send`, `compose`, `briza_send`)

`plan(t)` decides the one message a test needs now. `send(t, which)` composes and sends it, logs `msg`, and enforces:

- **Once per event:** booking, collection date, test, or day for reminders.
- **Send mode:** in `test` mode only `test_numbers` get real messages; others are logged as `planned`.
- **Hours:** 10:00–19:00 IST for the hourly run, 09:00–20:00 for Redcliffe-triggered messages.
- **Busy chats:** skip while the customer is chatting with Sova (`chatting()`, SovaX desk `adherence_log`, `quiet_minutes`).
- **Limits:** `max_sends_per_run` per run, 1.1 s apart (Briza's limit is 60 a minute).
- **Retries:** 3 failures stop retries.
- **Templates not approved:** `TEMPLATE_NOT_FOUND` / `TEMPLATE_NOT_APPROVED` → `held` + `wa_hold` banner, retried next run.

| Trigger | Template |
|---|---|
| Test 1 opens | `bt_welcome_book` |
| Test 2 opens | `bt_book_cta` |
| Not booked on day 3/5/7 | `bt_book_reminder` |
| Booked / rescheduled | `bt_booked` |
| Day before collection | `bt_reminder_links` (Yes / Reschedule links) |
| Customer taps Yes | `bt_confirm_thanks` |
| Cancelled | `bt_cancelled` |
| Sample collected | `bt_sample_collected` |
| Report ready | `bt_report_link` |
| Personalised test | `bt_custom_test` |
| Phone verification | `bt_otp` |

Body values are sent by name. Buttons are URL buttons whose `{{1}}` is a signed token (`tok(pid, n)`), so nothing posts into the Briza chat.

### 4.5 Booking (`booking_page`, `book`, `reschedule`, `cancel`)

1. `/b/<tok>`: the customer verifies their WhatsApp number (OTP via `bt_otp`, 30-minute signed cookie).
2. They give the address plus a map pin (`geocode`, `serviceable`), age 5–110, and gender.
3. Slots come from Redcliffe (`slots`, today + 4 days).
4. `book()` creates the Redcliffe booking, logs `booked` and sends `bt_booked`.
5. Reschedules are capped at 2 (Redcliffe allows 3 bookings).
6. A cancel by the team needs a reason.

### 4.6 Redcliffe status (`refresh_status`, `rcl_hook`, `reports_run`)

- The webhook `/rcl-hook/<hook_key>` and the 11/13/17 job pull status updates.
- Each new status is logged as `rcl`.
- Sample collected → `bt_sample_collected`.
- Report ready → the PDF is fetched and stored (`stored_pdf`), biomarkers are parsed (`parse_biomarkers`) and `bt_report_link` is sent.
- `/r/<tok>` streams the PDF from Sova's own address.

### 4.7 Manual sends (`find_program`, `manual_message`, `manual_send`)

The `/send` page (admins only) sends any of 10 templates now, whatever the send mode, and logs `msg_manual`. With a customer reference, it uses their real values and links, which may only go to that customer's own number or a test number. The report link needs no OTP, so this rule stops a wrong number from opening someone's report.

### 4.8 Legacy sheet (`legacy_sync`, `legacy_bt.py`)

- **What's read:** 6 tabs, by gid. Each tab is read as CSV, plus every cell's type (UNFORMATTED_VALUE / SERIAL_NUMBER) through the service account with read-only Drive and Sheets scopes. Only changed tabs are stored (sha256).
- **What it feeds:**
  - the BT dashboard (`/bt`), which matches the old Apps Script numbers;
  - sheet-only people (`S-…` profiles);
  - lab platform and the "Tested on other platform" filter;
  - the issues list;
  - program dates (§4.2).

### 4.9 Test-data removal (`purge_tests`)

- **Scope:** every `TEST-%` program plus listed ids.
- **Cancel first:** listed Redcliffe bookings are cancelled (no WhatsApp). A failed cancel stops the whole run.
- **Backup, delete, block:** rows are copied to `bt_removed_*` and deleted, and the ids are added to `removed_programs`.
- **Safe by default:** a dry run unless `"dry": false`.

### 4.10 Daily insights (`insights`)

A read-only JSON report for the weekday Claude task. It covers 24 hours, or 72 on Monday.

**Sections:**
- **Settings:** send mode, Redcliffe environment, holds.
- **Jobs:** each scheduler job's state, last run and status, read from the Cloud Scheduler API. The service account can't read Cloud Scheduler (403), so the report falls back to the rows each job writes to `bt_log`: the hourly run's `tick` heartbeat (program_id `_tick`, written by `/tick` after every run, `failed` if the Shopify sync failed), `legacy_sync` and `check`. A job whose last row is older than its limit (75 min, 75 min, 26 h) is an alert.
- **WhatsApps:** sent by template and status, plus the failed ones.
- **App errors:** `bt_log` rows of kind `error`.
- **Activity:** bookings, reschedules, cancels, clicks, verifications, Redcliffe statuses, reports, new programs.
- **Sheet sync:** runs, failures, date changes.
- **Now:** tests by status, open customer requests, follow-ups, Redcliffe checks, collections today and tomorrow, booking messages due in the next 2 days, bookings made before their window.
- **Dates vs sheet:** the tally and the items needing attention.
- **Service errors:** count, top messages and out-of-memory lines, from the Cloud Logging API. Reported as unavailable while the service account lacks `roles/logging.viewer`; app-level errors still come from `bt_log`.

Every finding is also listed in `alerts`, and `ok` is true when there are none. The report contains no names or phone numbers, only order numbers. It is cached for 5 minutes.

---

## 5. HTTP interface

| Route | Auth | Purpose |
|---|---|---|
| `GET /` `/bt` `/bt.json` `/unbooked` `/analytics` `/legacy*` `/skus` `/settings` `/send` `/p?id=` `/dates` `/add-user` | dashboard session (admin or view) | Ops dashboard |
| `POST /act` | session + same-origin check; admin for changes | Every dashboard action (cfg, book, cancel, send, follow-up, dates, test stages…) |
| `/login` `/logout` | — | Password login (salted hash, lockout `too_many_fails`), signed session cookie (HttpOnly, Secure, SameSite=Lax) |
| `/b/<tok>` `/v/<tok>` `/c/<tok>` `/r/<tok>` | signed token (+ OTP cookie for `/b`) | Customer booking, verify, Yes, report |
| `POST /tick?token=` | `TOKEN` | Hourly run |
| `POST /reports?token=` | `TOKEN` | 11/13/17 checks |
| `/legacy-sync?token=` | `TOKEN` | Hourly sheet copy + program dates |
| `POST /manual-send?token=` | `TOKEN` | Same as `/send` (end-to-end checks) |
| `POST /purge-tests?token=` | `TOKEN` | Test-data removal (dry by default) |
| `/sheet-dates?token=` (`POST &apply=1` writes) | `TOKEN` | Dates report / apply |
| `/insights?key=` | `insights_key()` = HMAC(TOKEN, "insights") | Read-only daily report |
| `POST /resync-orders?token=` · `/legacy-typed?token=` | `TOKEN` | Maintenance |
| `POST /rcl-hook/<key>` | `hook_key()` = HMAC(TOKEN) | Redcliffe webhook |
| `POST /shop-hook/<store>` | Shopify HMAC (app secret) | Order created / cancelled |
| Paid blood-test purchase API (`purchase_api`, added 21 Nov) | `purchase_key()` = HMAC(TOKEN) | Orders from payments.sova.health |
| `/ops` `/ops.json` | — | Removed; 302 to `/bt` |

---

## 6. Scheduled work

| What | When (IST) | Does |
|---|---|---|
| Cloud Scheduler `bloodtests-tick` → `/tick` | hourly at :10 | SKU + order sync, Redcliffe status, due WhatsApps |
| Cloud Scheduler `bt-legacy-sync` → `/legacy-sync` | hourly at :17 | Copy changed sheet tabs, apply program dates |
| Cloud Run job `bt-reports` (trigger `bt-checks`) → `/reports` | 11:00, 13:00, 17:00 | Redcliffe status + reports, `check` summary row |
| Claude scheduled task → `/insights` | Mon–Fri around 12:00 | Health report + insights to the owner |

---

## 7. UI (ui.py)

- Server-rendered HTML with one stylesheet. All tokens are listed in `DESIGN.md`: colours, 4 px spacing, radii, two shadows.
- `frame()` draws the sticky header: brand, send-mode chip, account, tab nav, and the Downloads menu.
- **Customer page** (`program()`), redesigned 23 Nov:
  - a header card with Open Briza chat, Call and WhatsApp;
  - customer requests as a banner;
  - each test with a four-step progress bar, a key-facts grid, actions (Cancel kept apart), booking history and follow-ups;
  - a side column with details, program dates (and where they came from), linked users, results and booking another test;
  - a timeline filterable by WhatsApp, Bookings or Redcliffe.
- Everything is escaped with `html.escape(quote=True)`. Forms post to `/act` with a same-origin check.

---

## 8. Error handling and observability

- Every integration call returns `(status, json)`; failures are logged to `bt_log` as `error` or `failed` with the provider's answer in `detail`.
- Sends are idempotent: insert ids plus once-per-event rules.
- A failed Redcliffe cancel stops a purge before any delete.
- The sheet sync reports per tab and never deletes data on failure.
- `/insights` surfaces job health (the hourly heartbeat), `bt_log` errors and failed sends, and Cloud Logging errors (including out-of-memory) every weekday.

---

## 9. Security and secrets

| Secret | Where | Used for |
|---|---|---|
| `TOKEN` | Cloud Run env (also in scheduler URIs) | Job routes; signs customer tokens; derives hook, purchase and insights keys |
| `BRIZA_API_KEY` | Cloud Run env | Briza API |
| `REDCLIFFE_KEY`, `REDCLIFFE_HOST` | Cloud Run env | Redcliffe staging |
| `bt-redcliffe-prod-key` | Secret Manager | Redcliffe production |
| `bt-shopify-in-app`, `bt-shopify-health-app`, `adh-shopify-*` | Secret Manager | Shopify apps and webhook HMAC |
| `MAPS_KEY` | Cloud Run env | Address pin |
| `DASH_USERS`, `DASH_PASSWORD` | Cloud Run env | Bootstrap login |

Other protections:
- The service account has read-only Drive and Sheets scopes for the legacy sheet.
- Customer links are HMAC-signed tokens.
- Report links need no OTP, so a customer's links only ever go to their own number.
- Admin-only changes; view-only logins can't send or change anything.
- No secret is in the repository; `.env.example` lists names only.

---

## 10. Testing

`test_main.py` runs the whole app in-process with fake BigQuery (in-memory tables), fake HTTP for Shopify, Redcliffe and Briza, and fake Google APIs. It has 650+ assertions across:
- order intake;
- program detection (Sova X excluded);
- every message trigger and its once-per rules;
- send modes and quiet hours;
- booking, reschedule and cancel;
- OTP and tokens;
- the legacy sheet maths (matching the old dashboard exactly);
- program dates from the sheet (team dates win, split-row conflicts left alone);
- manual sends (report links only to the customer's own number);
- test-data removal (dry run, cancel-first, backups);
- insights alerts;
- the UI (header, customer page, settings).

Every deploy runs it first.

---

## 11. Operational runbook (short)

1. **Deploy:** fetch the latest source zip from `gs://run-sources-sova-production-asia-south1/services/sova-bloodtests/`, apply the change, run `python3 test_main.py`, then `gcloud run deploy …` (see §1).
2. **Pause / resume sending:** `gcloud scheduler jobs pause|resume bloodtests-tick --location asia-south1`.
3. **Health:** open `/insights?key=<insights key>`, or read the weekday report.
4. **Dates:** `GET /sheet-dates?token=&format=csv` (report); the hourly sync applies changes.
5. **Remove test data:** `POST /purge-tests?token=` with `{"programs": [...], "cancels": [...], "dry": true}`, then `false`.
