"""Sova program blood tests: Shopify program orders -> Redcliffe home blood tests -> Briza WhatsApp -> BigQuery -> dashboard.
Test 1 in the program's first week; Test 2 20 days before the program ends (start date editable on the profile). WhatsApp (all Utility templates except the OTP):
book (button to the booking page) -> reminder the day before with two link buttons, Yes (thank-you) / Reschedule (booking page)
-> sample collected (report in 3-4 days) -> the report PDF. All buttons are links, so nothing lands in the chat for Briza's AI bot.
The booking page asks for the WhatsApp number + a WhatsApp OTP first. A booking can be rescheduled twice (3 bookings in total).
Routes (one Cloud Run service, entry point `app`):
POST /tick?token= hourly (Cloud Scheduler): sync SKUs + program orders, send due WhatsApps, refresh Redcliffe status
GET /c/<tok> Template 2's Yes button: thank-you on WhatsApp (once per booking) + a short page
GET|POST /v/<tok> customer proves it's them: WhatsApp number -> OTP on WhatsApp -> 30-minute verified cookie
GET|POST /b/<tok> booking page (the WhatsApp button): address (+ map pin) + Redcliffe coverage check, then a slot; or reschedule.
GET /r/<tok> the report button: an Open / Download page (no code), then the PDF streamed from Redcliffe (its link never reaches the chat)
POST /rcl-hook/<key> Redcliffe's webhooks (every type): status, Redcliffe-side reschedules and reports land at once; due WhatsApps go out
POST /reports?token= 11:00, 13:00, 17:00 IST (Cloud Run job bt-reports, trigger bt-checks): Redcliffe status of every scheduled or
                    rescheduled booking, final reports, the due sample-collected / report WhatsApps, and a summary row for Analytics
                    Ops book on a customer's behalf with ?ops=1 (signed in); without it everyone gets the customer page
POST /shop-hook/<store> Shopify orders/create + orders/cancelled (signed with the store app's secret): a program order lands at once
GET / /p /analytics /settings /skus dashboard (same logins as the SovaX adherence desk); POST /act dashboard actions (incl. cancel)
GET /analytics.csv /results.csv exports: one insight's customers for a date range; every test result with its biomarkers and report link
Test 1's booking WhatsApp goes the day after the order; not booked -> booking reminders on order day 3, 5 and 7. A reschedule is confirmed
with the booked template. The team can send one customer a personalised test (its own Redcliffe package codes) from their page.
SEND_MODE: off = nothing goes out, test = only TEST_NUMBERS get real WhatsApps and bookings (others logged "planned"), live = everyone.
"""
import ast, base64, csv, datetime as dt, io, hashlib, hmac, json, os, re, secrets, time, urllib.error, urllib.parse, urllib.request, uuid
import functions_framework
import legacy_bt as LB
import ui

E = os.environ.get
PROJECT = E("PROJECT", "sova-production")
DS = E("DATASET", PROJECT + ".sova")
PROGRAMS, LOG, SKUS, CONFIG, ACCOUNTS = (DS + t for t in (".bt_programs", ".bt_log", ".bt_skus", ".bt_config", ".dashboard_users"))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
RCL = E("REDCLIFFE_HOST", "https://apiqa.redcliffelabs.com").rstrip("/") # staging; production is https://api.redcliffelabs.com
MAX_RESCHEDULES = 2 # Redcliffe's own limit: first booking + 2 reschedules = 3 bookings
FOLLOWUP_HOURS = 48 # Template 1 sent this long ago and still not booked -> follow-up queue
SLOT_DAYS = 5 # Redcliffe lists slots for today + the next 4 days only
DEFAULT_T = {"t1_template": "bt_welcome_book", "t1_next_template": "bt_book_cta", "t2_template": "bt_reminder_links", # Briza / Meta names
             "thanks_template": "bt_confirm_thanks", "booked_template": "bt_booked", "rescheduled_template": "bt_rescheduled",
             "collected_template": "bt_sample_collected", "report_template": "bt_report_link", "otp_template": "bt_otp",
             "nudge_template": "bt_book_reminder", "custom_template": "bt_custom_test", "cancelled_template": "bt_cancelled"}
# A reschedule (by the customer, the team or Redcliffe) is confirmed with the booked template and the new slot (bt_rescheduled is no longer sent)
NUDGE_DAYS = (2, 4, 6) # not booked: a booking reminder 2, 4 and 6 days after the booking window opens (order day + 3, + 5, + 7)
RESULTS = DS + ".bt_results" # one row per report: link, parsed biomarkers and the PDF's text, for Test 1, Test 2 and personalised tests
CUSTOM = "CUSTOM" # sku of a personalised test the team sends one customer (its own package codes)
LEGACY = DS + ".bt_legacy" # the legacy Google Sheet (Program Clean): one row per sheet row, cells as a JSON list
ADH_LOG = DS + ".adherence_log" # the SovaX desk's WhatsApp log: every message a customer sends Sova, on any topic
SM = "https://secretmanager.googleapis.com/v1/projects/%s/secrets/" % PROJECT
PROD_KEY = "bt-redcliffe-prod-key" # the Redcliffe production key, saved from Settings (never shown back)
MIN_DAYS = 90 # programs of 3 months or more get blood tests
AGE_MIN, AGE_MAX = 5, 110 # Redcliffe refuses a booking outside 5-150 ("Age should be in range 5 to 150")
# Both Shopify stores sell programs. Each is read with the SovaX desk's own connection (its Shopify app token in Secret Manager);
# sova.in orders are #RE12345, sova.health orders plain numbers, so order names never clash.
STORES = {"in": ("sova.in", E("SHOP_IN", "sova-in.myshopify.com"), E("SHOP_IN_SECRET", "adh-shopify-access-token"), E("STORE_IN_URL", "https://sova-in.myshopify.com")),
          "health": ("sova.health", E("SHOP_HEALTH", "sova-health-6053.myshopify.com"), E("SHOP_HEALTH_SECRET", "adh-shopify-health-token"),
                     E("STORE_URL", "https://sova.health"))}
HEALTH_APP = E("SHOP_HEALTH_APP_SECRET", "bt-shopify-health-app") # sova.health's own Shopify app: {"client_id", "client_secret"}, saved from Settings
# Each store's own Dev Dashboard app ("LSQ Test Takers - Sova IN / - Sova Health"): Client ID + secret saved from Settings. Used first when saved
# (the SovaX desk's token stays the fallback); its secret also signs the store's order webhooks (/shop-hook/<store>).
APP_SECRET = {"in": E("SHOP_IN_APP_SECRET", "bt-shopify-in-app"), "health": HEALTH_APP}
SAMPLE = "/sample-report.pdf" # test profiles' report (Move to next stage); not a real report
CHECKS = "_checks" # bt_log program_id of the 11:00 / 13:00 / 17:00 check summaries (Analytics shows them)
ASKS = ("resched",) # customer opened the reschedule page: waits in the ops queue until the booking moves or ops log a follow-up


def now(): return dt.datetime.now(dt.timezone.utc)
def today(): return now().astimezone(IST).date()


# ---------- rules ----------
def t1_delay(): # Test 1's booking WhatsApp goes the day after the order (order day + 1); Settings can change it
    try: return max(0, min(7, int(setting("t1_delay_days") or 1)))
    except ValueError: return 1


def is_custom(p): return p.get("sku") == CUSTOM


T2_LEAD, T2_DUE = 20, 15 # Test 2's booking WhatsApp goes 20 days before the program ends; it should be done by 15 days before


def program_start(p):
    """The program's start (IST date): the purchase date, unless the team set another (profile or the dates CSV).
    It moves Test 2 and the program end only; Test 1 always follows the purchase date."""
    return p.get("program_start") or p["purchased_at"].astimezone(IST).date()


def program_end(p):
    """The program's end: the team's end date (profile or the dates CSV), else start + the program's days. Test 2 hangs off it."""
    return p.get("program_end") or program_start(p) + dt.timedelta(days=int(p.get("days") or 0))


def tests_for(p):
    """[(test_no, opens, due)] as IST dates. Test 1: opens the day after the purchase (t1_delay), due 6 days later. Test 2: opens 20 days
    before the program ends (from its start date, which the team can change), due 15 days before the end; only when that is 30+ days
    into the program, so a short plan gets one test. A personalised test (sku CUSTOM) is one test, open from the day the team sends it."""
    start, days = p["purchased_at"].astimezone(IST).date(), int(p["days"] or 0)
    if p.get("source") == "linked": return [] # a linked user (Add user) gets no program tests: the team sends them personalised tests
    if is_custom(p): return [(1, start, start + dt.timedelta(days=7))]
    o1 = start + dt.timedelta(days=t1_delay())
    out = [(1, o1, o1 + dt.timedelta(days=6))]
    if days - T2_DUE >= 30: # the same plans as before get a Test 2
        end = program_end(p)
        out.append((2, end - dt.timedelta(days=T2_LEAD), end - dt.timedelta(days=T2_DUE)))
    if legacy(p): out = [t for t in out if t[0] == 2] # bought before go-live: only Test 2
    if p.get("t1_covered"): out = [t for t in out if t[0] != 1] # repeat buyer: the previous plan's Test 2 is this plan's starting point
    return out


def legacy(p):
    """Bought before go-live: Test 1 happened before tracking, so there is no Redcliffe data for it ('test taken, data expired')."""
    lf = live_from()
    return bool(lf and p["purchased_at"].astimezone(IST).date() < lf and p.get("sku") not in ("TEST", CUSTOM))


def live_from():
    """Go-live date (IST): orders from it get the whole flow; earlier buyers only Test 2, and only while it's still ahead."""
    try: return dt.date.fromisoformat(setting("live_from"))
    except ValueError: return None


BOOKABLE = ("to_send", "awaiting", "followup", "missed", "untracked", "cancelled")


def counts(r, real):
    """A send that reached the customer, or one logged as planned while sends are off for them (going live re-sends those)."""
    return r["status"] == "sent" or r["status"] == "planned" and not real


def test_state(p, n, opens, due, logs, at=None):
    """Everything the dashboard and the jobs need about one test, folded from the program's log rows."""
    at = at or now()
    day, real = at.astimezone(IST).date(), is_real(p["phone"])
    L = [r for r in logs if r.get("test_no") == n]
    bks = [r for r in L if r["kind"] in ("booked", "rescheduled")]
    cur = bks[-1] if bks else None
    last_new = max((i for i, r in enumerate(bks) if r["kind"] == "booked"), default=0)
    keep_sent = n == 2 and bool(p.get("program_start") or p.get("program_end")) # dates set by the team: Test 2's booking WhatsApp never goes twice
    t1 = [r for r in L if r["kind"] == "msg" and r.get("note") in ("t1", "custom") and counts(r, real) and
          (keep_sent or r["at"].astimezone(IST).date() >= opens)] # a refill can move the window
    nudges = [r for r in L if r["kind"] == "msg" and r.get("note") == "nudge" and counts(r, real)]
    sent1 = [r["at"] for r in t1 if r["status"] == "sent"]
    rcl = [r for r in L if r["kind"] == "rcl" and cur and r["at"] >= cur["at"] and str(r.get("booking_id") or cur["booking_id"]) == str(cur["booking_id"])]
    report = next((jd(r)["report_link"] for r in reversed(rcl) if jd(r).get("report_link")), None) # a later status update doesn't hide it
    track = next((jd(r)["tracking_link"] for r in reversed(rcl) if str(jd(r).get("tracking_link", "")).startswith("https://")), None) # phlebo's live location
    rstat = (rcl[-1]["status"] or "") if rcl else ""
    cx = [r for r in L if r["kind"] == "cancelled"]
    off = bool(cur and cx and cx[-1]["at"] > cur["at"]) # cancelled from the dashboard
    oks = [r for r in L if r["kind"] in ("booked", "rescheduled", "cancelled") or r["kind"] == "msg" and r["status"] in ("sent", "planned")]
    errs = [r for r in L if r["kind"] == "error" or r["kind"] == "msg" and r["status"] == "failed"]
    failed = errs[-1] if errs and (not oks or errs[-1]["at"] > oks[-1]["at"]) else None
    fu = [r for r in L if r["kind"] == "followup"]
    reps = [r for r in L if r["kind"] == "reply"] # WhatsApp button taps
    tap = reps[-1] if reps else None
    added = p.get("added_at") or at
    added = (added if isinstance(added, dt.datetime) else dt.datetime.fromisoformat(str(added))).astimezone(IST).date()
    if any(r["kind"] == "closed" for r in logs): status = "closed"
    elif cur and report: status = "report"
    elif cur and (off or re.search(r"cancel", rstat, re.I)): status = "cancelled"
    elif cur and re.search(r"collect|pick|sample|process|sync|report" if cur["collection_date"] <= day.isoformat() else r"collected|report", rstat, re.I):
        status = "collected" # an upcoming booking needs an explicit "collected" (a scheduled pickup isn't one)
    elif cur: status = "booked" if cur["collection_date"] >= day.isoformat() else "check"
    elif day < opens: status = "upcoming"
    elif sent1 and at - max(sent1[-1:] + [r["at"] for r in fu + reps]) > dt.timedelta(hours=FOLLOWUP_HOURS): status = "followup"
    elif t1: status = "awaiting"
    elif added > due + dt.timedelta(days=7): status = "untracked" # its window was over before this program reached us
    elif day > due + dt.timedelta(days=7): status = "missed"
    else: status = "to_send"
    acted = [r["at"] for r in L if r["kind"] in ("booked", "rescheduled", "cancelled", "followup")]
    ask = tap["note"] if tap and tap["note"] in ASKS and not any(a > tap["at"] for a in acted) and status in ("booked", "check") else None
    return dict(program=p, n=n, opens=opens, due=due, status=status, booking=cur, history=bks, failed=failed, real=real,
                resched=sum(r["kind"] == "rescheduled" for r in bks[last_new:]), t1_at=t1[-1]["at"] if t1 else None,
                rcl_status=rstat, report=report, tracking=track, late=bool(bks) and bks[0]["collection_date"] > due.isoformat(), bookable=status in BOOKABLE,
                tap=tap, ask=ask, ask_at=tap["at"] if ask else None, nudges=len(nudges), custom=is_custom(p),
                confirmed=bool(status == "booked" and tap and tap["note"] == "yes" and tap["at"] > cur["at"]))


def rows_for(programs, logs, at=None):
    return [test_state(p, n, o, d, logs.get(p["program_id"], []), at) for p in programs for n, o, d in tests_for(p)]


def guess_days(sku, product_type, name="", store="health"):
    """A program's length in days, 0 if it isn't a program. Programs: sova.health Coaching products (PR-/PT- SKUs) and sova.in SovaX
    plans (SU-SX... SKUs). The length is the SKU's end (PR-WGL-120 = 120 days) or the title ('4 Months'); supplements never count."""
    sku = (sku or "").strip().upper()
    if sku.startswith("SU-SX"): return 0 # Sova X (Precision Probiotics) is a supplement, not a program (team decision, 20 Nov 2026)
    if not ((product_type or "").strip().lower() in ("coaching", "program") or re.match(r"P[RT]-", sku)): return 0
    m = re.search(r"-(\d{2,3})$", sku)
    t = re.search(r"(\d+)\s*(month|week|day)s?\b", name or "", re.I)
    d = int(m.group(1)) if m else int(t.group(1)) * {"month": 30, "week": 7, "day": 1}[t.group(2).lower()] if t else 0
    return d if d >= 30 else 0 # -001 style pack numbers are not lengths


def norm_phone(v):
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 11 and d.startswith("0"): d = d[1:]
    if len(d) == 10: d = "91" + d
    return d if len(d) == 12 and d.startswith("91") and d[2] in "6789" else ""


def jd(r):
    try: return json.loads((r or {}).get("detail") or "{}") or {}
    except ValueError: return {}


def fmt_day(d): return dt.date.fromisoformat(str(d)[:10]).strftime("%a, %d %b")


# ---------- booking link ----------
def _bsig(payload): return hmac.new((E("TOKEN", "\0") + "|book").encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]


def tok(pid, n):
    payload = "%s|%d" % (pid, n)
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + _bsig(payload)


def untok(t):
    try:
        b, sig = t.split(".", 1)
        payload = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode()
        pid, n = payload.rsplit("|", 1)
        return (pid, int(n)) if hmac.compare_digest(sig, _bsig(payload)) else None
    except (ValueError, UnicodeDecodeError):
        return None


# ---------- integrations ----------
def http(url, body=None, headers=None, method=None, form=False):
    data = None if body is None else (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json", "User-Agent": "sova-bloodtests", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=40) as r: st, raw = r.status, r.read()
    except urllib.error.HTTPError as e: st, raw = e.code, e.read()
    except Exception as e: return 0, {"error": str(e)[:300]}
    try: return st, json.loads(raw or b"{}")
    except ValueError: return st, {"raw": raw[:500].decode(errors="replace")}


def shop_raw(store): return secret(STORES[store][2]).strip()


def desk_token(store):
    """The SovaX desk's token for the store, or ''. The desk's app may save the whole OAuth answer ({"access_token": ...})."""
    v = shop_raw(store)
    if v.startswith("{"):
        try: v = str(json.loads(v).get("access_token") or "")
        except (ValueError, AttributeError): v = ""
    return v if v.startswith("shp") else ""


def shop_tokens(store):
    """Tokens to try, best first: the store's own app (Settings > Go live), then the SovaX desk's connection."""
    out = []
    for t in (app_token(store), desk_token(store)):
        if t and t not in out: out.append(t)
    return out


def shop_token(store):
    """The store's Shopify token, or '' while that store isn't connected."""
    return (shop_tokens(store) or [""])[0]


_app, _app_err = {}, {}


def app_grant(store, app):
    """Shopify's client credentials grant for the store's own app -> (token, seconds valid, scopes) or ('', 0, why)."""
    st, r = http("https://%s/admin/oauth/access_token" % STORES[store][1], {"grant_type": "client_credentials", "client_id": app.get("client_id", ""),
                 "client_secret": app.get("client_secret", "")}, method="POST", form=True)
    r = r if isinstance(r, dict) else {}
    if st == 200 and str(r.get("access_token", "")).startswith("shp"): return r["access_token"], int(r.get("expires_in") or 86399), r.get("scope", "")
    return "", 0, "%s %s" % (st, str(r.get("error_description") or r.get("error") or r.get("errors") or "")[:150])


def store_app(store):
    """The store's own app as saved in Settings: {"client_id", "client_secret"} or {"access_token"}; {} when nothing is saved."""
    try: app = json.loads(secret(APP_SECRET[store]) or "{}")
    except ValueError: app = {}
    return app if isinstance(app, dict) else {}


def app_token(store):
    """The store's own app (Settings > Go live): its token lasts 24 hours, so it is fetched again an hour before it runs out."""
    c = _app.get(store)
    if c and time.time() < c[0]: return c[1]
    app = store_app(store)
    if str(app.get("access_token", "")).startswith("shp"): return app["access_token"] # the app's own Admin API key, saved as is
    if not app.get("client_id"): return ""
    tok, ttl, why = app_grant(store, app)
    _app[store], _app_err[store] = (time.time() + (max(ttl - 3600, 300) if tok else 300), tok), "" if tok else why # a refusal is retried after 5 minutes
    return tok


def shop_status(store):
    """For Settings: connected (and through which app), or why not (never the token itself)."""
    if _blocked.get(store) and time.time() - _blocked[store][0] < 3600:
        return "own app blocked by Shopify (%s); using the SovaX desk's connection" % _blocked[store][1] if desk_token(store) else \
            "not connected (Shopify blocked the store's own app: %s)" % _blocked[store][1]
    if app_token(store): return "connected (own app)"
    if desk_token(store): return "connected (SovaX desk)" + ("; own app refused: %s" % _app_err[store] if _app_err.get(store) else "")
    if _app_err.get(store): return "not connected (Shopify refused the store's app: %s)" % _app_err[store]
    raw, st = shop_raw(store), _sec_st.get(STORES[store][2])
    if st not in (None, 200, 404): return "not connected (can't read its secret: %s)" % st
    return "not connected yet" if not raw or raw.lower() == "pending" else "not connected (its saved token is in a format we don't recognise)"


_blocked = {} # store -> (when, why): the own app's token was refused (e.g. its IP allowlist doesn't include Google Cloud)


def shopify(query, variables=None, store="health"):
    """One Admin GraphQL call. Tries the store's own app first and falls back to the SovaX desk's token when Shopify refuses the app
    (401 / 403, e.g. an IP allowlist that doesn't include Google Cloud)."""
    toks = shop_tokens(store)
    if _blocked.get(store) and time.time() - _blocked[store][0] < 3600 and len(toks) > 1: toks = toks[1:] # skip a blocked app for an hour
    for i, token in enumerate(toks or [""]):
        for wait in (0, 2, 4, 8): # Shopify refills its query-cost bucket within seconds: a throttled page waits and tries again
            time.sleep(wait)
            st, r = http("https://%s/admin/api/2026-10/graphql.json" % STORES[store][1], {"query": query, "variables": variables or {}},
                         {"X-Shopify-Access-Token": token})
            if "THROTTLED" not in str(isinstance(r, dict) and r.get("errors")): break
        if st in (401, 403) and i + 1 < len(toks):
            _blocked[store] = (time.time(), "%s %s" % (st, str(r)[:120])); print(STORES[store][0], "own app refused, using the desk token:", st)
            continue
        break
    if isinstance(r, dict) and (r.get("extensions") or {}).get("search"): print("Shopify search warning:", str(r["extensions"]["search"])[:400])
    if st != 200 or not isinstance(r, dict) or r.get("errors"): raise RuntimeError("Shopify %s %s" % (st, str(r)[:300]))
    return r["data"]


def briza_send(phone, template, components):
    st, r = http("https://appapi.brizaai.com/api/v1/external/messages/send-template",
                 {"phoneNumber": phone, "templateName": template, "components": components}, {"x-api-key": E("BRIZA_API_KEY", "")})
    return st == 200 and isinstance(r, dict) and bool(r.get("success")), r


def send_otp(phone, code):
    """Meta's authentication template: the code goes in the body and in its copy-code button."""
    one = [{"type": "text", "text": code}]
    return briza_send(phone, setting("otp_template"), [{"type": "body", "parameters": one}, {"type": "button", "sub_type": "url", "index": "0", "parameters": one}])


def prod(): return setting("rcl_env") == "prod"


def rcl(method, path, body=None, params=None):
    """Redcliffe staging (env) until Settings switches to production (host there, key in Secret Manager)."""
    host, key = ((setting("rcl_prod_host") or "https://api.redcliffelabs.com").rstrip("/"), secret(PROD_KEY)) if prod() else (RCL, E("REDCLIFFE_KEY", ""))
    return http(host + path + ("?" + urllib.parse.urlencode(params) if params else ""), body, {"key": key}, method)


def rcl_ok(r): return isinstance(r, dict) and str(r.get("status", "")).lower().startswith(("succ", "sucess"))


def rcl_msg(r):
    if not isinstance(r, dict): return str(r)[:200]
    m = r.get("message") or r.get("error") or r.get("detail") or r.get("errors") or r
    return (", ".join(map(str, m)) if isinstance(m, list) else str(m))[:200]


def slots(lat, lng, day):
    """[(slot_id, '07:00 AM - 08:00 AM')] with a phlebotomist free, plus Redcliffe's message when there are none."""
    st, r = rcl("GET", "/api/booking/v2/get-time-slot-list/", params={"collection_date": day, "latitude": lat, "longitude": lng})
    rows = (r.get("results") or r.get("data") or []) if isinstance(r, dict) else []
    out = [(s["id"], "%s - %s" % (s["format_12_hrs"]["start_time"], s["format_12_hrs"]["end_time"]))
           for s in sorted(rows, key=lambda s: str((s.get("format_24_hrs") or {}).get("start_time", "")) if isinstance(s, dict) else "")
           if isinstance(s, dict) and (s.get("available_slot") or 0) > 0 and s.get("format_12_hrs")]
    return out, "" if out else rcl_msg(r) if st else "Could not reach Redcliffe right now."


def geocode(place):
    """Area text -> (lat, lng) via Redcliffe's eloc search, or None."""
    st, r = rcl("GET", "/api/partner/v2/get-partner-location-2-eloc/", params={"place_query": place})
    res = (r.get("results") or r.get("data") or []) if isinstance(r, dict) else []
    if not res or not isinstance(res[0], dict) or not res[0].get("eloc"): return None
    st, g = rcl("GET", "/api/partner/v2/get-partner-loc-2-eloc/", params={"eloc": res[0]["eloc"].strip()})
    try: return float(g["latitude"]), float(g["longitude"])
    except (KeyError, TypeError, ValueError): return None


def serviceable(lat, lng):
    """Redcliffe's coverage check for a map point -> (True | False | None when it couldn't be checked, Redcliffe's message)."""
    st, r = rcl("GET", "/api/center/v2/is-location-serviceable/", params={"longitude": lng, "latitude": lat})
    if not st or not isinstance(r, dict) or not r.get("status"): return None, rcl_msg(r)
    return str(r["status"]).lower() == "success", rcl_msg(r)


def device_point(lat, lng):
    """The phone's own location from "Use my current location", if it is a real point in India."""
    try: lat, lng = float(lat), float(lng)
    except (TypeError, ValueError): return None
    return (lat, lng) if 6 <= lat <= 37.5 and 68 <= lng <= 97.5 else None


def consolidated_report(bid):
    """Redcliffe's final PDF link for a booking (GET consolidated report), or None while it isn't ready."""
    st, r = rcl("GET", "/api/external/v2/get-consolidated-report/%s" % urllib.parse.quote(str(bid)))
    d = r[0] if isinstance(r, list) and r else r
    d = d.get("data") if isinstance(d, dict) and isinstance(d.get("data"), dict) else d
    link = next((v for k, v in d.items() if k.lower().replace("_", "") in ("reporturl", "reportlink") and v), None) if isinstance(d, dict) else None
    return link if isinstance(link, str) and link.startswith("http") else None


def fetch_pdf(url):
    """The report's bytes for the customer's browser (so the Redcliffe link stays out of WhatsApp), or None."""
    if urllib.parse.urlparse(url or "").path == SAMPLE: return open(os.path.join(os.path.dirname(__file__), "sample-report.pdf"), "rb").read()
    if not (url or "").startswith("https://"): return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sova-bloodtests"}), timeout=40) as r: data = r.read(25_000_000)
    except Exception as e: print("report fetch failed", e); return None
    return data if data[:4] == b"%PDF" else None


_gtok, _sec, _sec_st = {"at": 0, "v": ""}, {}, {}


def gcp_token():
    if time.time() > _gtok["at"]:
        st, r = http("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token", headers={"Metadata-Flavor": "Google"}, method="GET")
        if st != 200 or not isinstance(r, dict) or not r.get("access_token"): raise RuntimeError("no service account token (%s)" % st)
        _gtok.update(at=time.time() + int(r.get("expires_in") or 300) - 60, v=r["access_token"])
    return _gtok["v"]


def secret(name):
    """Latest value of a Secret Manager secret, '' when it has none. Cached 5 minutes, so a key saved in Settings applies within 5 minutes."""
    c = _sec.get(name)
    if c and time.time() - c[0] < 300: return c[1]
    try:
        st, r = http(SM + name + "/versions/latest:access", headers={"Authorization": "Bearer " + gcp_token()}, method="GET")
        v = base64.b64decode(r["payload"]["data"]).decode() if st == 200 else ""
        _sec_st[name] = st # 404 = nothing saved yet; anything else but 200 is a problem worth showing
    except Exception as e:
        print("secret read failed", name, e); v = c[1] if c else ""; _sec_st[name] = str(e)[:80]
    _sec[name] = (time.time(), v)
    return v


def set_secret(name, value):
    st, r = http(SM + name + ":addVersion", {"payload": {"data": base64.b64encode(value.encode()).decode()}}, {"Authorization": "Bearer " + gcp_token()}, "POST")
    if st == 200: _sec[name] = (time.time(), value)
    return st == 200


def chatting(phone):
    """The time the customer last wrote to Sova on WhatsApp, on any topic (the SovaX desk logs every message), if that was within the
    quiet window: automatic messages wait so they don't land in the middle of another conversation. None when they are free (or unknown)."""
    mins = int(setting("quiet_minutes") or 30)
    if not phone or mins <= 0: return None
    try:
        r = q("SELECT MAX(`at`) t FROM `%s` WHERE kind IN ('inbound', 'response') AND channel = 'whatsapp' AND `at` > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), "
              "INTERVAL %d MINUTE) AND RIGHT(REGEXP_REPLACE(IFNULL(phone, ''), r'[^0-9]', ''), 10) = @p" % (ADH_LOG, mins), p=phone[-10:])
    except Exception as e:
        print("chat check failed", e); return None
    return r[0]["t"] if r and r[0].get("t") else None


def redcliffe_name(s):
    s = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z ]", " ", s or "")).strip()[:30] # Redcliffe: letters only, 3-30 chars
    return s if len(s) >= 3 else "Sova Customer"


# ---------- storage ----------
_bq = None


def bq():
    global _bq
    if not _bq:
        from google.cloud import bigquery # imported on first use: customer pages start faster
        _bq = bigquery.Client(project=PROJECT)
    return _bq


def q(sql, **params):
    from google.cloud import bigquery
    cfg_ = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in params.items()])
    return [dict(r) for r in bq().query(sql, job_config=cfg_).result()]


def log(**row):
    row = {"id": uuid.uuid4().hex, "at": now().isoformat(), **row}
    if isinstance(row.get("detail"), (dict, list)): row["detail"] = json.dumps(row["detail"], default=str)[:60000]
    try: errs = bq().insert_rows_json(LOG, [row], row_ids=[row["id"]]) # the row id is BigQuery's insert id: a retried insert is dropped
    except Exception as ex: errs = str(ex)[:300]
    if errs: print("log insert failed", errs, "ROW", json.dumps(row, default=str))
    return row


def load(pid=None):
    """Blood-test plans (newest first) and their log rows in time order; with pid, the plan that program belongs to."""
    who = (" WHERE program_id = @p OR phone != '' AND phone = (SELECT phone FROM `%s` WHERE program_id = @p AND source != 'test' LIMIT 1)" % PROGRAMS) if pid else ""
    progs = one_per_order(q("SELECT * FROM `%s`%s ORDER BY purchased_at DESC" % (PROGRAMS, who), **({"p": pid} if pid else {})))
    progs = only_programs(progs)
    logs = {}
    for r in q("SELECT * FROM `%s`%s ORDER BY `at`" % (LOG, " WHERE program_id IN (SELECT program_id FROM `%s`%s)" % (PROGRAMS, who) if pid else ""),
               **({"p": pid} if pid else {})):
        r["at"] = r["at"] if isinstance(r["at"], dt.datetime) else dt.datetime.fromisoformat(r["at"])
        logs.setdefault(r["program_id"], []).append(r)
    return apply_shifts(plans(progs, logs, pid), logs), logs


def plans(progs, logs, pid=None):
    """One blood-test plan per person (WhatsApp number): a program bought before their plan ends is a refill, so the plan runs from the first
    purchase to the latest end. Test 2 moves to 15 days before that end, and no second Test 1 comes. Each order's id finds the plan's log rows.
    Test profiles, orders without a number and cancelled orders stand alone. -> plans, newest first (only the one holding pid, if given)"""
    closed = {k for k, L in logs.items() if any(r["kind"] == "closed" for r in L)}
    day = lambda p: p["purchased_at"].astimezone(IST).date()
    heads, last = [], {}
    for p in sorted(progs, key=lambda p: p["purchased_at"]):
        k = p.get("phone") if p.get("source") not in ("test", "custom", "linked") and p["program_id"] not in closed else None # personalised tests and linked users stand alone
        h = last.get(k) if k else None
        if h and day(p) < day(h) + dt.timedelta(days=int(h["days"] or 0)):
            h["days"] = max(int(h["days"] or 0), (day(p) - day(h)).days + int(p["days"] or 0))
            h["members"].append(p["program_id"])
            continue
        p["members"] = [p["program_id"]]
        heads.append(p)
        if k: last[k] = p
    t2_done = lambda h: int(h.get("days") or 0) >= 90 and any(r["kind"] == "rcl" and r.get("test_no") == 2 and jd(r).get("report_link")
                                                              for m in h["members"] for r in logs.get(m, []))
    sheet = None
    for i, h in enumerate(heads):
        k = h.get("phone") if h.get("source") not in ("test", "custom", "linked") else None
        if not k or int(h.get("days") or 0) < MIN_DAYS: continue
        if any(r.get("test_no") == 1 and r["kind"] in ("msg", "booked", "rescheduled", "rcl", "cancelled") for m in h["members"] for r in logs.get(m, [])):
            continue # Test 1 already under way: it goes on
        bought = day(h)
        if any(o.get("phone") == k and day(o) < bought and t2_done(o) for o in heads[:i]):
            h["t1_covered"] = "previous plan's Test 2 (this desk)"; continue
        if sheet is None: sheet = legacy_t2_done()
        if any(e and e <= (bought + dt.timedelta(days=30)).isoformat() for e in sheet.get(k[-10:], ())):
            h["t1_covered"] = "previous plan's Test 2 (legacy sheet)"
    for h in heads:
        if len(h["members"]) > 1:
            L = sorted((r for m in h["members"] for r in logs.get(m, [])), key=lambda r: r["at"])
            for m in h["members"]: logs[m] = L
    return [h for h in heads[::-1] if not pid or pid in h["members"]]


def one_per_order(progs):
    """One blood-test plan per order: an order with two program SKUs keeps its longest (rows saved before this rule included)."""
    key = lambda p: ((p.get("source") or "").split(" ")[0], p.get("order_name") or p["program_id"])
    best = {}
    for p in progs:
        if key(p) not in best or (int(p.get("days") or 0), p["program_id"]) > (int(best[key(p)].get("days") or 0), best[key(p)]["program_id"]): best[key(p)] = p
    return [p for p in progs if best[key(p)] is p]


def apply_shifts(progs, logs):
    """The team's start date (latest 'start' row) and test profiles moved on by hand ('shift' rows: that many days earlier, so the next
    test's window is open now)."""
    for p in progs:
        L = logs.get(p["program_id"], [])
        for k in ("start", "end"): # the team's latest start / end date (profile or the dates CSV)
            v = next((jd(r).get("date") for r in reversed(L) if r["kind"] == k), None)
            try: p["program_" + k] = dt.date.fromisoformat(v) if v else None
            except ValueError: p["program_" + k] = None
        d = sum(int(jd(r).get("days") or 0) for r in L if r["kind"] == "shift")
        if d:
            p["purchased_at"] = p["purchased_at"] - dt.timedelta(days=d)
            for k in ("program_start", "program_end"):
                if p[k]: p[k] -= dt.timedelta(days=d)
    return progs


_cfg = {"at": 0, "v": {}}


def cfg(refresh=False):
    """Settings changed from the dashboard; env values are the defaults. Cached 60 s per instance, so a switch reaches every instance within a minute."""
    if refresh or time.time() - _cfg["at"] > 60:
        try:
            rows = q("SELECT key, ARRAY_AGG(value ORDER BY `at` DESC LIMIT 1)[OFFSET(0)] v FROM `%s` GROUP BY key" % CONFIG)
            _cfg.update(at=time.time(), v={r["key"]: r["v"] for r in rows})
        except Exception as e:
            print("config read failed", e)
    return _cfg["v"]


def setting(k): return cfg().get(k) or E(k.upper(), "") or DEFAULT_T.get(k, "")
def send_mode(): return setting("send_mode") or "test"
def test_numbers(): return {norm_phone(p) for p in setting("test_numbers").split(",") if norm_phone(p)}


def set_cfg(key, value, by):
    q("INSERT `%s` (key, value, `at`, by_user) VALUES (@k, @v, CURRENT_TIMESTAMP(), @b)" % CONFIG, k=key, v=value, b=by)
    cfg(refresh=True)


def is_real(phone):
    m = send_mode()
    return bool(phone) and (m == "live" or m == "test" and phone in test_numbers())


def sku_table():
    return {r["sku"]: r for r in q("SELECT x.* FROM (SELECT ARRAY_AGG(t ORDER BY `at` DESC LIMIT 1)[OFFSET(0)] x FROM `%s` t GROUP BY sku)" % SKUS)}


_skc = {"at": 0, "v": None}


def not_programs():
    """SKUs the team marked as not a program (0 or under 90 days, e.g. Sova X): orders already pulled in for them drop out of every view
    and flow (the rows stay in bt_programs, so re-mapping the SKU brings them back). Cached 5 minutes; saving a SKU clears it."""
    if _skc["v"] is None or time.time() - _skc["at"] > 300:
        try: _skc.update(at=time.time(), v={s for s, r in sku_table().items() if int(r.get("days") or 0) < MIN_DAYS})
        except Exception as e: print("sku table read failed", e); _skc.update(at=time.time(), v=_skc["v"] or set())
    return _skc["v"]


def only_programs(progs):
    """Program rows minus orders of SKUs marked not a program (test profiles and personalised tests have their own SKUs and stay)."""
    drop = not_programs()
    return [p for p in progs if p.get("source") in ("test", "custom") or (p.get("sku") or "").upper() not in drop] if drop else progs


def set_sku(sku, name, ptype, days, note, by):
    _skc["v"] = None
    q("INSERT `%s` (sku, name, product_type, days, note, `at`, by_user) VALUES (@s, @n, @t, CAST(@d AS INT64), @o, CURRENT_TIMESTAMP(), @b)" % SKUS,
      s=sku, n=name, t=ptype, d=str(int(days)), o=note, b=by)


# ---------- test results: every report's link, its biomarkers and its text, kept for Test 1, Test 2 and personalised tests ----------
_NUM = r"[<>]?\s*\d+(?:\.\d+)?"
BIO_LINE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9 ()/,.'+\-]{1,70}?)\s+(?P<value>%s)(?:\s+(?P<unit>(?!\d)[^\s]{1,15}|\d+\^\d+/[^\s]+))?"
                      r"(?:\s+(?P<range>(?:%s\s*-\s*%s|[<>]=?\s*\d+(?:\.\d+)?)))?\s*(?P<flag>\b[HL]\b|High|Low)?\s*$" % (_NUM, r"\d+(?:\.\d+)?", r"\d+(?:\.\d+)?"), re.I)
NOT_BIO = re.compile(r"^(age|gender|sex|patient|name|date|sample|collected|reported|registered|booking|page|ref|barcode|lab|phone|mobile|id|uhid|"
                     r"test name|end of report)\b", re.I)


def parse_biomarkers(pdf):
    """The report PDF -> ([{name, value, unit, range, flag}], full text). Reads each 'Test  Result  Unit  Reference range' line; the text is
    kept too, so a report laid out differently can be read again later."""
    try:
        import io, pypdf
        text = "\n".join((pg.extract_text() or "") for pg in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    except Exception as e:
        print("report text failed", e); return [], ""
    out, seen = [], set()
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        m = BIO_LINE.match(line)
        if not m or NOT_BIO.match(line) or len(m.group("name").strip()) < 2: continue
        name, val = m.group("name").strip(" :-"), m.group("value").replace(" ", "")
        rng, unit = (m.group("range") or "").replace(" ", ""), (m.group("unit") or "")
        flag = (m.group("flag") or "")[:1].upper()
        if not flag and rng:
            try:
                v = float(val.lstrip("<>"))
                lo_hi = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", rng)
                if lo_hi: flag = "L" if v < float(lo_hi.group(1)) else "H" if v > float(lo_hi.group(2)) else ""
                elif rng[0] == "<": flag = "H" if v >= float(rng.lstrip("<=")) else ""
                elif rng[0] == ">": flag = "L" if v <= float(rng.lstrip(">=")) else ""
            except ValueError: pass
        if name.lower() in seen: continue
        seen.add(name.lower())
        out.append({"name": name, "value": val, "unit": unit, "range": rng, "flag": flag})
    return out, text


PDF_MAX = 7_000_000 # BigQuery takes rows up to 10 MB; the PDF goes base64-encoded, so a copy is kept for reports up to ~7 MB


def save_result(p, n, booking_id, link, source, by, partial=False):
    """A report became ready: keep its link, biomarkers, text and a copy of the PDF itself in BigQuery (report_pdf), once per report link.
    The PDF is read now, while Redcliffe's link works; the dashboard and the customer's report page open this copy later."""
    try:
        if q("SELECT 1 x FROM `%s` WHERE program_id = @p AND IFNULL(booking_id, '') = @b AND report_url = @u LIMIT 1" % RESULTS,
             p=p["program_id"], b=str(booking_id or ""), u=link): return "kept already"
    except Exception as e: print("results read failed", e)
    pdf = fetch_pdf(link)
    bio, text = parse_biomarkers(pdf) if pdf else ([], "")
    row = {"id": uuid.uuid4().hex, "at": now().isoformat(), "program_id": p["program_id"], "test_no": n, "booking_id": str(booking_id or ""), "report_url": link,
           "status": "partial" if partial else "ready" if pdf else "link saved; report couldn't be read (link expired?)", "source": source, "by_user": by,
           "customer_name": p.get("customer_name") or "", "phone": p.get("phone") or "", "biomarkers": json.dumps(bio), "report_text": text[:200000],
           "report_pdf": base64.b64encode(pdf).decode() if pdf and len(pdf) <= PDF_MAX else None}
    if pdf and len(pdf) > PDF_MAX: row["status"] += "; PDF too large to copy (%d MB), link kept" % (len(pdf) // 1_000_000)
    try: errs = bq().insert_rows_json(RESULTS, [row], row_ids=[row["id"]])
    except Exception as ex: errs = str(ex)[:300]
    if errs: print("result insert failed", errs)
    return "%d biomarkers kept%s" % (len(bio), ", PDF copied" if row["report_pdf"] else "")


def keep(t, link, source, by, partial=False):
    """save_result for a test row; never breaks the flow that found the report."""
    try: return save_result(t["program"], t["n"], (t.get("booking") or {}).get("booking_id"), link, source, by, partial)
    except Exception as e: print("keeping the result failed", t["program"]["program_id"], e); return "not kept"


def results_for(pids=None):
    """Saved results (newest first), for some programs or all; without the PDF and text themselves (has_pdf says a copy is kept)."""
    try:
        return q("SELECT * EXCEPT(report_pdf, report_text), report_pdf IS NOT NULL has_pdf FROM `%s`%s ORDER BY `at` DESC"
                 % (RESULTS, " WHERE program_id IN UNNEST(SPLIT(@p, '|'))" if pids else ""), **({"p": "|".join(pids)} if pids else {}))
    except Exception as e: print("results read failed", e); return []


# ---------- legacy Google Sheet + the Blood Test dashboard ----------
_leg = {}


def legacy_key():
    """The key of /legacy-import/<key>: set in bt_config (legacy_import_key) only while a copy is being made, then cleared. Empty = closed."""
    k = (cfg(refresh=True).get("legacy_import_key") or "").strip()
    return k if len(k) >= 24 else None


OPS_HTML = "__ops_html" # the old Apps Script dashboard's page (Index.html), kept in the DB; no longer served


def legacy_store(sheet, text, by, keep_types=False):
    """Keep one tab of the legacy sheet (CSV) in BigQuery, replacing the tab's earlier copy. A load job, not streaming: the old copy can be
    deleted at once. -> (rows kept, header)"""
    from google.cloud import bigquery
    table = [["html"], [text]] if sheet == OPS_HTML else LB.parse_csv(text)
    while table and not any(c.strip() for c in table[-1]): table.pop() # trailing blank rows; blank rows inside stay, so row n is sheet row n
    if not table: raise ValueError("the file has no rows")
    batch, at = uuid.uuid4().hex, now().isoformat()
    schema = [bigquery.SchemaField(n, t) for n, t in (("sheet", "STRING"), ("row_no", "INT64"), ("cells", "STRING"), ("batch", "STRING"),
                                                         ("imported_at", "TIMESTAMP"), ("imported_by", "STRING"))]
    bq().create_table(bigquery.Table(LEGACY, schema=schema), exists_ok=True)
    rows = [{"sheet": sheet, "row_no": i, "cells": json.dumps(r, ensure_ascii=False), "batch": batch, "imported_at": at, "imported_by": by}
            for i, r in enumerate(table, 1)]
    bq().load_table_from_json(rows, LEGACY, job_config=bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")).result()
    q("DELETE FROM `%s` WHERE sheet = @s AND batch != @b" % LEGACY, s=sheet, b=batch)
    _leg.clear(); _lp["v"] = None
    _t2.clear()
    if sheet in LB.TYPED and not keep_types: q("DELETE FROM `%s` WHERE sheet = @s" % LEGACY, s=sheet + "|types") # a hand upload has no cell types
    log(program_id="_legacy", kind="legacy_import", by_user=by, note=sheet, detail={"sheet": sheet, "rows": len(table) - 1, "columns": len(table[0]),
                                                                                     "sha": hashlib.sha256(text.encode()).hexdigest()[:20]})
    return len(table) - 1, table[0]


def legacy_store_types(sheet, types, sha, by):
    """The cell types of a kept tab (one row per sheet row), so dates are read exactly as the old dashboard read them."""
    from google.cloud import bigquery
    batch, at = uuid.uuid4().hex, now().isoformat()
    schema = [bigquery.SchemaField(n, t) for n, t in (("sheet", "STRING"), ("row_no", "INT64"), ("cells", "STRING"), ("batch", "STRING"),
                                                         ("imported_at", "TIMESTAMP"), ("imported_by", "STRING"))]
    rows = [{"sheet": sheet + "|types", "row_no": i, "cells": json.dumps(t, separators=(",", ":")), "batch": batch, "imported_at": at, "imported_by": by}
            for i, t in enumerate(types, 1)]
    bq().load_table_from_json(rows, LEGACY, job_config=bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")).result()
    q("DELETE FROM `%s` WHERE sheet = @s AND batch != @b" % LEGACY, s=sheet + "|types", b=batch)
    _leg.clear(); _t2.clear(); _lp["v"] = None
    log(program_id="_legacy", kind="legacy_import", by_user=by, note=sheet + "|types", detail={"sheet": sheet + "|types", "rows": len(types), "sha": sha})


def legacy_types(sheet):
    """The kept cell types of a tab, or None (hand-uploaded CSV: dates are then read from the text)."""
    t = legacy_table(sheet + "|types")
    return t if t else None


def legacy_table(sheet):
    """A kept tab, header row first (cached 10 minutes per instance; an import clears it)."""
    c = _leg.get(sheet)
    if c and time.time() - c[0] < 600: return c[1]
    try: rows = [json.loads(r["cells"]) for r in q("SELECT cells FROM `%s` WHERE sheet = @s ORDER BY row_no" % LEGACY, s=sheet)]
    except Exception as e: print("legacy read failed", e); rows = []
    _leg[sheet] = (time.time(), rows)
    return rows


_t2, _stok = {}, {"at": 0, "v": ""}


def legacy_t2_done():
    """{last 10 phone digits: [end dates]} of the sheet's 90+ day programs whose Test 2 is done (cached an hour; a sync clears it)."""
    if _t2 and time.time() - _t2["at"] < 3600: return _t2["v"]
    v = {}
    try:
        rows, _ = LB.legacy_rows(legacy_table(LB.SHEET), types=legacy_types(LB.SHEET))
        for r in rows:
            ph = LB.phone10(r["phone"])
            if ph and r["dur"] >= 90 and r["b2_k"] == "done": v.setdefault(ph, []).append(r["end"])
    except Exception as e: print("legacy test 2 read failed", e)
    _t2.update(at=time.time(), v=v)
    return v


def sheets_token():
    """The service account's token for reading the sheet (Drive read-only): the sheet is shared with it as a viewer."""
    if time.time() > _stok["at"]:
        sc = urllib.parse.quote("https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/spreadsheets.readonly", safe="")
        st, r = http("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token?scopes=" + sc,
                     headers={"Metadata-Flavor": "Google"}, method="GET")
        if st != 200 or not isinstance(r, dict) or not r.get("access_token"): raise RuntimeError("no service account token for the sheet (%s)" % st)
        _stok.update(at=time.time() + int(r.get("expires_in") or 300) - 60, v=r["access_token"])
    return _stok["v"]


def sheet_values(sheet, opts):
    """One tab through the Sheets API (values.get with the given render options)."""
    url = "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s?%s" % (LB.SHEET_ID, urllib.parse.quote("'%s'" % sheet, safe=""), opts)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + sheets_token()}), timeout=240) as r:
        return json.loads(r.read()).get("values", [])


def sheet_csv(sheet):
    """One tab of the sheet as CSV, exactly as shown (Sheets' own export; the Sheets API as a fallback)."""
    tok = sheets_token()
    url = "https://docs.google.com/spreadsheets/d/%s/export?format=csv&gid=%d" % (LB.SHEET_ID, LB.SYNC[sheet])
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + tok}), timeout=90) as r:
            body, ctype = r.read(), r.headers.get("Content-Type", "")
        if "csv" in ctype: return body.decode("utf-8-sig")
        why = "export gave %s" % ctype[:40]
    except urllib.error.HTTPError as e: why = "export %s" % e.code
    except Exception as e: why = "export failed: %s" % str(e)[:80]
    api = "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s?valueRenderOption=FORMATTED_VALUE" % (LB.SHEET_ID, urllib.parse.quote("'%s'" % sheet, safe=""))
    st, r = http(api, headers={"Authorization": "Bearer " + tok}, method="GET")
    if st != 200 or "values" not in r:
        raise RuntimeError("can't read the sheet (%s; Sheets API %s). Share 'Program Clean' with the service account as a Viewer." % (why, st))
    out = io.StringIO(); csv.writer(out).writerows(r["values"])
    return out.getvalue()


def legacy_typed(sheet):
    """A tab read three ways through the Sheets API: as shown, unformatted with dates as serial numbers, and unformatted with dates as text.
    A cell that is a number in the second and text in the third is a date. -> {"formatted": rows, "typed": rows ({"d": serial} for dates)}"""
    get = lambda opts: sheet_values(sheet, opts)
    f = get("valueRenderOption=FORMATTED_VALUE")
    a = get("valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=SERIAL_NUMBER")
    b = get("valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=FORMATTED_STRING")
    typed = []
    for i, ra in enumerate(a):
        rb = b[i] if i < len(b) else []
        typed.append([{"d": x} if isinstance(x, (int, float)) and not isinstance(x, bool) and j < len(rb) and isinstance(rb[j], str) else x for j, x in enumerate(ra)])
    return {"sheet": sheet, "formatted": f, "typed": typed}


def legacy_sync(by="hourly sync"):
    """Copy every synced tab from the sheet; a tab whose content hasn't changed since its last copy is skipped. -> {tab: result}"""
    last = {}
    try:
        for r in q("SELECT note, ARRAY_AGG(detail ORDER BY `at` DESC LIMIT 1)[OFFSET(0)] d FROM `%s` WHERE program_id = '_legacy' AND kind = 'legacy_import' GROUP BY note" % LOG):
            last[r["note"]] = jd({"detail": r["d"]}).get("sha")
    except Exception as e: print("last sync read failed", e)
    res = {}
    for sheet in LB.SYNC:
        try:
            text = sheet_csv(sheet)
            sha = hashlib.sha256(text.encode()).hexdigest()[:20]
            fresh = sha != last.get(sheet)
            if fresh: n, hdr = legacy_store(sheet, text, by, keep_types=True)
            if sheet in LB.TYPED and last.get(sheet + "|types") != sha:
                try:
                    shown = LB.parse_csv(text)
                    legacy_store_types(sheet, LB.types_from(shown, sheet_values(sheet, "valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=SERIAL_NUMBER")), sha, by)
                except Exception as e:
                    q("DELETE FROM `%s` WHERE sheet = @s" % LEGACY, s=sheet + "|types"); _leg.clear()
                    res[sheet] = "%s; cell types failed (%s)" % ("%d rows" % n if fresh else "unchanged", str(e)[:120]); continue
            res[sheet] = "%d rows" % n if fresh else "unchanged"
        except Exception as e: res[sheet] = "failed: %s" % str(e)[:200]; print("legacy sync", sheet, e)
    try: # the sheet holds each program's real start / end (set on the coaching platform): every sync brings new and changed dates in
        sd = sheet_dates(apply=True, by="sheet sync")
        res["program dates"] = "%d changes" % sd["changes"]
    except Exception as e: res["program dates"] = "failed: %s" % str(e)[:200]; print("sheet dates", e)
    log(program_id="_legacy", kind="legacy_sync", by_user=by, status="ok" if all(not v.startswith("failed") for v in res.values()) else "failed", detail=res)
    return res


def last_sync():
    try:
        r = q("SELECT `at` t, status, detail FROM `%s` WHERE program_id = '_legacy' AND kind = 'legacy_sync' ORDER BY `at` DESC LIMIT 1" % LOG)
        return {"at": r[0]["t"], "status": r[0]["status"], "detail": jd({"detail": r[0]["detail"]})} if r else None
    except Exception as e: print("last sync read failed", e); return None


def _safe(v):
    """Text for the old dashboard page, which writes values into HTML as they are: no tags or quotes from a sheet cell."""
    if isinstance(v, str): return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    if isinstance(v, dict): return {k: _safe(x) for k, x in v.items()}
    if isinstance(v, list): return [_safe(x) for x in v]
    return v


def legacy_meta():
    ist = lambda t: t.astimezone(IST).strftime("%Y-%m-%d %H:%M") if hasattr(t, "astimezone") else str(t)[:16]
    try: return [{**r, "at": ist(r.pop("copied_at"))} for r in q("SELECT sheet, COUNT(*) - 1 n, MAX(imported_at) copied_at, ANY_VALUE(imported_by) by_user FROM `%s` "
                                                             "WHERE sheet != '__ops_html' AND NOT ENDS_WITH(sheet, '|types') GROUP BY sheet ORDER BY sheet" % LEGACY)]
    except Exception as e: print("legacy meta failed", e); return []


def bt_live(progs, logs):
    """Our programs for the Blood Test dashboard: dates and, per test, our state, due date and the day its report came."""
    res = {}
    for r in results_for():
        k = (r["program_id"], r.get("test_no"))
        a = r["at"] if isinstance(r["at"], dt.datetime) else dt.datetime.fromisoformat(str(r["at"]))
        res[k] = min(res.get(k, a), a)
    out = {}
    for t in rows_for(progs, logs):
        p = t["program"]
        if p.get("source") in ("test", "linked", "custom"): continue # test profiles and personalised tests aren't program blood tests
        L = logs.get(p["program_id"], [])
        d = out.setdefault(p["program_id"], {"pid": p["program_id"], "order": p.get("order_name") or "", "name": p.get("customer_name") or "",
                                             "orders": [m.split("-")[0] for m in p["members"]], # program ids start with the order number
                                             "phone": p.get("phone") or "", "email": p.get("email") or "", "product": p.get("product") or "",
                                             "days": p.get("days"), "purchased": p["purchased_at"].astimezone(IST).date().isoformat(),
                                             "start": program_start(p).isoformat(), "end": program_end(p).isoformat(),
                                             "closed": any(r["kind"] == "closed" for r in L), "tests": {}})
        done = ""
        if t["status"] == "report":
            hit = [res[(m, t["n"])] for m in p["members"] if (m, t["n"]) in res]
            rep = [r["at"] for r in L if r["kind"] == "rcl" and r.get("test_no") == t["n"] and jd(r).get("report_link")]
            when = min(hit) if hit else (rep[0] if rep else None)
            done = when.astimezone(IST).date().isoformat() if when else ""
        d["tests"][t["n"]] = {"status": t["status"], "due": t["due"].isoformat(), "done": done, "rcl": t["rcl_status"] or "",
                              "collection": (t["booking"] or {}).get("collection_date") or ""}
    for p in progs:
        if p.get("t1_covered") and not legacy(p) and p["program_id"] in out: # bought before tracking: Test 1 isn't ours to count
            o1 = p["purchased_at"].astimezone(IST).date() + dt.timedelta(days=t1_delay())
            out[p["program_id"]]["tests"][1] = {"status": "covered", "due": (o1 + dt.timedelta(days=6)).isoformat(), "done": "", "rcl": p["t1_covered"], "collection": ""}
    return list(out.values())


_lp = {"at": 0, "key": None, "v": None}


def legacy_people(progs=None):
    """The sheet's Blood Test rows with their ids, each matched to our programs where it can be (order, '-1' split orders, phone + end date):
    rows left with src 'legacy' are people only in the sheet. Cached 5 minutes (a sync clears it); progs=None reuses the cache or loads."""
    table = legacy_table(LB.SHEET)
    if _lp["v"] is not None and time.time() - _lp["at"] < 300 and (progs is None or _lp["key"] == (len(progs), id(table))): return _lp["v"]
    if progs is None: progs = load()[0]
    leg, _ = LB.legacy_rows(table, types=legacy_types(LB.SHEET))
    LB.with_ids(leg)
    simple = [{"pid": p["program_id"], "order": p.get("order_name") or "", "orders": [m.split("-")[0] for m in p["members"]], "phone": p.get("phone") or "",
               "name": p.get("customer_name") or "", "email": p.get("email") or "", "product": p.get("product") or "", "days": p.get("days"),
               "purchased": p["purchased_at"].astimezone(IST).date().isoformat(), "start": program_start(p).isoformat(), "end": program_end(p).isoformat(),
               "tests": {}} for p in progs if p.get("source") not in ("test", "custom", "linked")]
    LB.unify(leg, simple, today().isoformat())
    _lp.update(at=time.time(), key=(len(progs), id(table)), v=leg)
    return leg


def legacy_tests(progs):
    """Overview rows for people only in the legacy sheet: one per test the sheet tracks, status 'sheet' (never in a queue, a count or a send)."""
    out = []
    for r in legacy_people(progs):
        if r["src"] != "legacy": continue
        prog = {"program_id": r["lid"], "customer_name": r["name"], "phone": re.sub(r"\D", "", r["phone"] or ""), "order_name": r["order"], "source": "legacy sheet",
                "sku": r["item"] or "", "days": r["dur"], "email": r["email"], "product": r["item"],
                "purchased_at": dt.datetime.combine(dt.date.fromisoformat(r["paid"] or r["start"] or "1970-01-01"), dt.time(), IST)}
        for n in (1, 2):
            b = "b%d_" % n
            if not (r[b + "st"] or r[b + "due"]): continue
            out.append(dict(program=prog, n=n, opens=r[b + "due"] or r["start"] or r["paid"], due=r[b + "due"], status="sheet", booking=None, history=[], failed=None,
                            real=False, resched=0, t1_at=None, rcl_status="", report=None, tracking=None, late=False, bookable=False, tap=None, ask=None, ask_at=None,
                            nudges=0, custom=False, confirmed=False, logs=[], sheet_st=r[b + "st"] or "No status", platform=r[b + "pf"], done=r[b + "done"],
                            other_platform=r[b + "k"] == "done" and LB.other_platform(r[b + "pf"]), pstat=r["pstat"], coach=r["coach"]))
    return out


def bt_dashboard_data(raw=False):
    """Every blood test the dashboard shows: the legacy sheet's rows and our programs, as compact JSON."""
    leg, dups = LB.legacy_rows(legacy_table(LB.SHEET), types=legacy_types(LB.SHEET))
    LB.with_ids(leg)
    progs, logs = load()
    rows, matched = LB.unify(leg, bt_live(progs, logs), today().isoformat())
    if raw: return rows
    meta = {"today": today().isoformat(), "legacy_rows": len(leg), "legacy_dups": dups, "matched": matched,
            "live": sum(r["src"] == "live" for r in rows), "imported": {m["sheet"]: (str(m["at"])[:16], m["n"]) for m in legacy_meta()},
            "t2_due_days": LB.T2_DUE}
    return {**LB.compact(rows), "meta": meta}


def stored_pdf(result_id=None, pid=None, n=None):
    """Our saved copy of a report (bytes), by result id, or the newest one for a program's test. None when there's no copy."""
    try:
        if result_id: r = q("SELECT report_pdf FROM `%s` WHERE id = @i AND report_pdf IS NOT NULL LIMIT 1" % RESULTS, i=result_id)
        else: r = q("SELECT report_pdf FROM `%s` WHERE program_id IN UNNEST(SPLIT(@p, '|')) AND test_no = CAST(@n AS INT64) AND report_pdf IS NOT NULL "
                    "ORDER BY `at` DESC LIMIT 1" % RESULTS, p=pid, n=str(n))
    except Exception as e: print("saved report read failed", e); return None
    v = r[0]["report_pdf"] if r else None
    if isinstance(v, str): v = base64.b64decode(v)
    return v if v and v[:4] == b"%PDF" else None


def profile(p, logs):
    """Program row + the newest customer-given details (address change, age, gender, landmark, lat/lng)."""
    out = dict(p)
    for r in logs:
        if r["kind"] == "profile": out.update({k: v for k, v in jd(r).items() if v not in (None, "")})
    return out


# ---------- jobs ----------
# Shopify refuses a query whose worst case costs over 1000 points, and nested lists multiply (orders x line items): keep pages small
RECENT_Q = """query($q:String!,$after:String){orders(first:25,after:$after,query:$q){pageInfo{hasNextPage endCursor}
nodes{lineItems(first:30){nodes{sku title variantTitle}}}}}"""


def sync_skus():
    """Every SKU of both stores -> bt_skus with its program length (0 = not a program); a row is written only when it is new or changed.
    sova.health: its public catalogue. sova.in: the SovaX desk's SKU list (sova_skus) and its public catalogue. Both: SKUs from the last
    3 days of orders (in-store items that aren't listed online)."""
    known, seen, where = sku_table(), {}, {}
    for store, (label, _, _, url) in STORES.items():
        for page in range(1, 20):
            st, r = http("%s/products.json?limit=250&page=%d" % (url, page))
            ps = r.get("products") if isinstance(r, dict) else None
            if not ps: break
            for pr in ps:
                for v in pr.get("variants") or []:
                    sku = (v.get("sku") or "").strip().upper()
                    seen.setdefault(sku, ((pr.get("title") or "") + ("" if v.get("title") == "Default Title" else " - %s" % v.get("title")), pr.get("product_type") or "", store))
                    where.setdefault(sku, set()).add(label)
        try:
            after, more = None, bool(shop_token(store))
            while more:
                d = shopify(RECENT_Q, {"q": "created_at:>=%s" % (today() - dt.timedelta(days=3)), "after": after}, store)["orders"]
                for o in d["nodes"]:
                    for li in o["lineItems"]["nodes"]:
                        sku = (li.get("sku") or "").strip().upper()
                        seen.setdefault(sku, (" - ".join(x for x in (li.get("title"), li.get("variantTitle")) if x), "", store))
                        where.setdefault(sku, set()).add(label)
                more, after = d["pageInfo"]["hasNextPage"], d["pageInfo"]["endCursor"]
        except Exception as e: print(label, "recent order SKUs:", e)
    try:
        for r in q("SELECT * FROM `%s.sova_skus`" % DS): # every SovaX SKU, kept by the SovaX desk
            sku = (r.get("sku") or "").strip().upper()
            seen.setdefault(sku, (r.get("name") or "", "", "in"))
            where.setdefault(sku, set()).add("sova.in")
    except Exception as e: print("SovaX SKUs:", e)
    changed = 0
    for sku, (name, pt, store) in seen.items():
        if not sku: continue
        d, note = guess_days(sku, pt, name, store), ", ".join(sorted(where.get(sku) or {STORES[store][0]}))
        old = known.get(sku)
        if old and manual(old): continue # mapped by the team in Settings > SKUs: the sync never overrides it
        if old and int(old.get("days") or 0) == d and (old.get("note") or "") == note: continue
        set_sku(sku, name, pt, d, note, "sync")
        changed += 1
    return changed


def manual(row): return bool(row.get("by_user")) and row.get("by_user") != "sync"


REVIEW = re.compile(r"\b(\d+\s*(month|week|day)s?|program|programme|plan|coaching|course|journey)\b", re.I)


def needs_review(row):
    """A SKU that looks like a program (name says months / program / plan / coaching) but has no length yet: the team should map it.
    Supplements (SU- SKUs other than the SovaX SU-SX plans) never count, whatever their pack size says."""
    sku = (row.get("sku") or "").upper()
    if sku.startswith("SU-") and not sku.startswith("SU-SX"): return False
    return not manual(row) and int(row.get("days") or 0) == 0 and bool(REVIEW.search(row.get("name") or ""))


ORDERS_Q = """query($q:String!,$after:String,$sort:OrderSortKeys){orders(first:20,after:$after,query:$q,sortKey:$sort){pageInfo{hasNextPage endCursor}
nodes{name createdAt cancelledAt sourceName email phone customer{firstName lastName phone email}
shippingAddress{firstName lastName phone address1 address2 city province zip latitude longitude}
billingAddress{phone address1 address2 city province zip}
lineItems(first:30){nodes{sku title variantTitle}}}}}"""
ORDERS_Q_NOCUST = ORDERS_Q.replace(" customer{firstName lastName phone email}", "") # an app without read_customers: the order's own contact fields


def date_rows(p, sd, ed, by, via):
    """The 'start' / 'end' log rows for new dates on a program (only what changed), or an error. sd / ed None = back to the default
    (purchase date / start + days). Both kept on the profile timeline with the old value."""
    bought = p["purchased_at"].astimezone(IST).date()
    sd = sd or bought
    ed = ed or sd + dt.timedelta(days=int(p.get("days") or 0))
    if not bought - dt.timedelta(days=400) <= sd <= today() + dt.timedelta(days=365): return [], "Start date should be within a year of today."
    if not sd + dt.timedelta(days=30) <= ed <= sd + dt.timedelta(days=1000): return [], "End date should be 30 to 1000 days after the start date."
    old_s, old_e = program_start(p), program_end(p)
    out = []
    for kind, new, old, default in (("start", sd, old_s, bought), ("end", ed, old_e, sd + dt.timedelta(days=int(p.get("days") or 0)))):
        if new == old: continue
        out.append(dict(id=uuid.uuid4().hex, at=now().isoformat(), program_id=p["program_id"], kind=kind, by_user=by,
                        note="Program %s %s (was %s)%s" % (kind, fmt_day(new), fmt_day(old), " · dates CSV" if via == "csv" else " · legacy sheet" if via == "sheet" else ""),
                        detail=json.dumps({"date": "" if new == default and kind == "start" else new.isoformat(), "from": old.isoformat(), "via": via})))
    return out, ""


def dates_message(pid, changed):
    p2, l2 = load(pid)
    p = p2[0]
    t2 = next((t for t in rows_for(p2, l2) if t["n"] == 2), None)
    head = "Dates saved: %s to %s." % (fmt_day(program_start(p)), fmt_day(program_end(p))) if changed else "Those are already the dates."
    if not t2: return head + " Too short for a Test 2. Test 1 is unchanged."
    done = t2["booking"] or t2["status"] in ("collected", "report")
    return head + (" Test 2 is already %s; it stays as it is and won't be sent again." % (
        "booked for " + fmt_day(t2["booking"]["collection_date"]) if t2["booking"] else STAGE_WORD.get(t2["status"], t2["status"])) if done else
        " Test 2's booking WhatsApp goes on %s (20 days before the end)%s. Test 1 is unchanged." % (
            fmt_day(t2["opens"]), "; it was sent before, so only its reminders go" if t2["t1_at"] else ""))


STAGE_WORD = {"collected": "collected", "report": "done (report ready)"}
DATE_FMTS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%m/%d/%Y")


def parse_day(v):
    v = (v or "").strip()
    if not v: return None
    for fm in DATE_FMTS:
        try: return dt.datetime.strptime(v, fm).date()
        except ValueError: pass
    raise ValueError(v)


def dates_csv(text, by):
    """Settings / Overview > Update dates: a CSV of order_id, start_date, end_date -> each order's program gets those dates (logged on its
    profile as start / end changes). Test 1 never moves; Test 2 follows the new end (20 days before), and is never sent or booked twice.
    -> (rows for the result table, summary)."""
    rd = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    norm = lambda k: re.sub(r"[^a-z]", "", (k or "").lower())
    cols = {norm(k): k for k in (rd.fieldnames or [])}
    ko = cols.get("orderid") or cols.get("order") or cols.get("ordername") or cols.get("orderno")
    ks, ke = cols.get("startdate") or cols.get("start"), cols.get("enddate") or cols.get("end")
    if not ko or not (ks or ke): return [], "The CSV needs the columns order_id, start_date and end_date (download the sample)."
    progs, logs = load()
    by_order = {}
    for p in progs:
        if p.get("source") in ("linked", "custom"): continue
        for m in p["members"]:
            by_order.setdefault(m.split("-")[0].upper(), p)
        by_order.setdefault((p.get("order_name") or "").lstrip("#").upper(), p)
    out, new_rows, seen = [], [], set()
    for i, r in enumerate(rd, start=2):
        if i > 5001: out.append({"row": i, "order": "", "result": "Stopped: 5,000 rows at most per file"}); break
        oid = (r.get(ko) or "").strip().lstrip("#").upper()
        if not oid and not any((v or "").strip() for v in r.values()): continue
        res = {"row": i, "order": oid}
        p = by_order.get(oid)
        try: sd, ed = parse_day(r.get(ks) if ks else ""), parse_day(r.get(ke) if ke else "")
        except ValueError as e: out.append({**res, "result": "Not a date: %s (use YYYY-MM-DD)" % e}); continue
        if not oid: out.append({**res, "result": "No order id"}); continue
        if not p: out.append({**res, "result": "Order not found (not a program order of 90+ days, or not synced yet)"}); continue
        if oid in seen: out.append({**res, "result": "Skipped: this order is already in the file above"}); continue
        seen.add(oid)
        sd = sd or program_start(p)
        if not ed: ed = (p.get("program_end") if not (ks and r.get(ks)) else None) or sd + dt.timedelta(days=int(p.get("days") or 0))
        rows, err = date_rows(p, sd, ed, by, "csv")
        if err: out.append({**res, "customer": p.get("customer_name"), "result": err}); continue
        new_rows += rows
        p2 = dict(p, program_start=sd if sd != p["purchased_at"].astimezone(IST).date() else None, program_end=ed)
        t2 = next((t for t in rows_for([p2], logs) if t["n"] == 2), None)
        if not rows: msg = "No change"
        elif not t2: msg = "Updated (too short for a Test 2)"
        elif t2["booking"] or t2["status"] in ("collected", "report"): msg = "Updated; Test 2 already %s, it won't be sent again" % ("booked" if t2["booking"] else "done")
        else: msg = "Updated; Test 2 WhatsApp on %s%s" % (fmt_day(t2["opens"]), " (sent before: reminders only)" if t2["t1_at"] else "")
        out.append({**res, "customer": p.get("customer_name"), "start": sd.isoformat(), "end": ed.isoformat(), "result": msg})
    for k in range(0, len(new_rows), 500):
        chunk = new_rows[k:k + 500]
        bad = bq().insert_rows_json(LOG, chunk, row_ids=[x["id"] for x in chunk])
        if bad: raise RuntimeError("log insert failed: %s" % str(bad)[:200])
    upd = sum(1 for o in out if o["result"].startswith("Updated"))
    summ = "%d order%s updated, %d unchanged, %d not updated (see the table)." % (upd, "" if upd == 1 else "s", sum(o["result"] == "No change" for o in out),
                                                                                sum(not o["result"].startswith(("Updated", "No change")) for o in out))
    set_cfg("dates_csv_last", "%s · %s · %s" % (now().astimezone(IST).strftime("%d %b %Y %H:%M"), by, summ), by)
    return out, summ


DATES_SAMPLE = "order_id,start_date,end_date\nRE14837,2026-11-20,2027-02-18\n#1001,20-11-2026,18-02-2027\n"


def program_skus():
    return {s: int(r["days"]) for s, r in sku_table().items() if int(r.get("days") or 0) >= MIN_DAYS}


def order_page(store, variables):
    """One page of orders; an app without read_customers can't read order.customer, so that page is read again without it."""
    try: return shopify(ORDERS_Q, variables, store)["orders"]
    except RuntimeError as e:
        if not re.search(r"access denied|customer", str(e), re.I): raise
        return shopify(ORDERS_Q_NOCUST, variables, store)["orders"]


def consider_order(o, label, progs, known, closed, lf):
    """One Shopify order (online, in-store or a completed draft order) -> a new program row, or None. Cancelled orders close their
    program. Orders from the go-live date get the whole flow; earlier ones only when their Test 2 is still ahead."""
    start = dt.datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(IST).date()
    items = sorted(((progs[sku], sku, li) for li in o["lineItems"]["nodes"] for sku in [(li.get("sku") or "").strip().upper()] if sku in progs),
                   key=lambda x: (x[0], x[1]))
    if o.get("cancelledAt"):
        for _, sku, _ in items:
            pid = "%s-%s" % (o["name"].lstrip("#"), sku)
            if pid in known and pid not in closed:
                log(program_id=pid, kind="closed", note="Order cancelled in Shopify", by_user="sync"); closed.add(pid)
        return None
    if not items: return None
    days, sku, li = items[-1] # one blood-test plan per order: its longest program
    pid, t2 = "%s-%s" % (o["name"].lstrip("#"), sku), start + dt.timedelta(days=days - 15)
    if pid in known or start < lf and not (days - 15 >= 30 and t2 >= today()): return None
    known.add(pid)
    return program_row(o, li, sku, days, label)


def sync_orders(only=None):
    """Purchases of 3-month-plus programs on both stores (online, in-store and completed draft orders) not seen yet -> bt_programs.
    Orders from the go-live date get the whole flow; earlier ones only when their Test 2 is still ahead (no other old data). After the
    first pass, each run reads only orders changed since the last one (new or cancelled). Cancelled orders close their program. A store
    not connected is skipped. only=[skus]: a full pass for SKUs the team just mapped (everyone who bought them), marks untouched."""
    progs = program_skus()
    if only: progs = {s: d for s, d in progs.items() if s in set(only)}
    if not progs: return "no programs of 3+ months found"
    lf = live_from()
    if not lf: lf = today(); set_cfg("live_from", lf.isoformat(), "sync") # the first sync sets it; going Live moves it to that day
    since = (today() - dt.timedelta(days=max(progs.values()) + 10)).isoformat()
    skus = " OR ".join('sku:"%s"' % s for s in sorted(progs))
    known = {r["program_id"] for r in q("SELECT program_id FROM `%s`" % PROGRAMS)} | removed_programs() # removed test data never comes back
    closed = {r["program_id"] for r in q("SELECT program_id FROM `%s` WHERE kind = 'closed'" % LOG)}
    new, out, marks = [], [], {}
    for store, (label, *_) in STORES.items():
        if not shop_token(store): out.append("%s not connected" % label); continue
        mark, started = (None if only else setting("orders_synced_" + store)), now()
        qs = ("updated_at:>=%s AND (%s)" % ((dt.datetime.fromisoformat(mark).astimezone(IST).date() - dt.timedelta(days=1)).isoformat(), skus) if mark
              else "created_at:>=%s AND (%s)" % (since, skus))
        after, n0, read = None, len(new), 0
        while True:
            d = order_page(store, {"q": qs, "after": after, "sort": "UPDATED_AT" if mark else "CREATED_AT"})
            read += len(d["nodes"])
            for o in d["nodes"]:
                row = consider_order(o, label, progs, known, closed, lf)
                if row: new.append(row)
            if not d["pageInfo"]["hasNextPage"]: break
            after = d["pageInfo"]["endCursor"]
        if not only: marks[store] = started.isoformat()
        out.append("%s: %d new (%d orders read)" % (label, len(new) - n0, read))
    if new:
        bad = bq().insert_rows_json(PROGRAMS, new)
        if bad: raise RuntimeError(bad)
    for store, m in marks.items(): set_cfg("orders_synced_" + store, m, "sync") # only once the programs are saved
    return "; ".join(out)


# ---------- Shopify order webhooks (orders/create, orders/cancelled): a new program order lands at once, Test 1 goes out the next day ----------
def rest_order(o):
    """A webhook's REST order -> the GraphQL shape the sync reads."""
    adr = lambda a: {k2: (a or {}).get(k1) for k1, k2 in (("first_name", "firstName"), ("last_name", "lastName"), ("phone", "phone"), ("address1", "address1"),
                                                          ("address2", "address2"), ("city", "city"), ("province", "province"), ("zip", "zip"),
                                                          ("latitude", "latitude"), ("longitude", "longitude"))} if a else {}
    c = o.get("customer") or {}
    return {"name": o.get("name") or "", "createdAt": o.get("created_at") or now().isoformat(), "cancelledAt": o.get("cancelled_at"),
            "sourceName": o.get("source_name") or "", "email": o.get("email") or o.get("contact_email"), "phone": o.get("phone"),
            "customer": {"firstName": c.get("first_name"), "lastName": c.get("last_name"), "phone": c.get("phone"), "email": c.get("email")},
            "shippingAddress": adr(o.get("shipping_address")), "billingAddress": adr(o.get("billing_address")),
            "lineItems": {"nodes": [{"sku": li.get("sku"), "title": li.get("title"), "variantTitle": li.get("variant_title")} for li in o.get("line_items") or []]}}


def shop_hook(store, raw, hmac_header, topic):
    """One Shopify webhook, checked with the store's own app secret (Shopify signs every delivery with it). -> (http status, result)"""
    sec = str(store_app(store).get("client_secret") or "")
    if not sec: return 401, "the store's own app isn't saved in Settings"
    good = base64.b64encode(hmac.new(sec.encode(), raw, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(good, hmac_header or ""): return 401, "bad signature"
    try: o = json.loads(raw)
    except ValueError: return 400, "bad payload"
    if not isinstance(o, dict) or not o.get("name"): return 200, "ignored"
    progs = program_skus()
    lf = live_from() or today()
    oo = rest_order(o)
    pids = ["%s-%s" % (oo["name"].lstrip("#"), (li.get("sku") or "").strip().upper()) for li in oo["lineItems"]["nodes"]]
    known = ({r["program_id"] for r in q("SELECT program_id FROM `%s` WHERE program_id IN UNNEST(SPLIT(@p, '|'))" % PROGRAMS, p="|".join(pids))} if pids else set()) | removed_programs()
    closed = {r["program_id"] for r in q("SELECT program_id FROM `%s` WHERE kind = 'closed' AND program_id IN UNNEST(SPLIT(@p, '|'))" % LOG, p="|".join(pids))} if pids else set()
    row = consider_order(oo, STORES[store][0], progs, known, closed, lf)
    if not row: return 200, "%s: nothing to add (%s)" % (topic, "cancelled" if oo.get("cancelledAt") else "not a new program order")
    bad = bq().insert_rows_json(PROGRAMS, [row])
    if bad: print("webhook insert failed", bad); return 500, "save failed" # Shopify retries
    first_test = tests_for({**row, "purchased_at": dt.datetime.fromisoformat(str(row["purchased_at"]).replace("Z", "+00:00"))})[0]
    return 200, "added %s (Test %d booking WhatsApp from %s)" % (row["program_id"], first_test[0], fmt_day(first_test[1]))


HOOKS_Q = "{webhookSubscriptions(first:50){nodes{id topic uri}}}"
HOOK_ADD = """mutation($t:WebhookSubscriptionTopic!,$u:String!){webhookSubscriptionCreate(topic:$t,webhookSubscription:{uri:$u,format:JSON}){
webhookSubscription{id topic uri} userErrors{field message}}}"""


def register_hooks(base):
    """Settings button: subscribe each store's own app to orders/create and orders/cancelled at /shop-hook/<store> (once each)."""
    out = []
    for store, (label, *_) in STORES.items():
        if not store_app(store).get("client_secret"): out.append("%s: save its app's Client ID and secret first" % label); continue
        url = "%s/shop-hook/%s" % (base, store)
        try:
            have = {(n["topic"], n.get("uri")) for n in shopify(HOOKS_Q, None, store)["webhookSubscriptions"]["nodes"]}
            made = []
            for topic in ("ORDERS_CREATE", "ORDERS_CANCELLED"):
                if (topic, url) in have: continue
                r = shopify(HOOK_ADD, {"t": topic, "u": url}, store)["webhookSubscriptionCreate"]
                if r.get("userErrors"): raise RuntimeError("; ".join(x["message"] for x in r["userErrors"]))
                made.append(topic.lower().replace("_", "/"))
            out.append("%s: %s" % (label, "subscribed to " + ", ".join(made) if made else "already subscribed"))
        except Exception as e: out.append("%s: failed (%s)" % (label, str(e)[:160]))
    return "; ".join(out)


def program_row(o, li, sku, days, store="sova.health"):
    c, s, b = o.get("customer") or {}, o.get("shippingAddress") or {}, o.get("billingAddress") or {}
    a = s if s.get("address1") or s.get("zip") else b
    phone = next((norm_phone(x) for x in (s.get("phone"), o.get("phone"), c.get("phone"), b.get("phone")) if norm_phone(x)), "")
    name = " ".join(x for x in (s.get("firstName") or c.get("firstName"), s.get("lastName") or c.get("lastName")) if x)
    return dict(program_id="%s-%s" % (o["name"].lstrip("#"), sku), order_name=o["name"], source="%s %s" % (store, o.get("sourceName") or ""),
                purchased_at=o["createdAt"], sku=sku, product=" - ".join(x for x in (li.get("title"), li.get("variantTitle")) if x),
                days=days, customer_name=name, phone=phone, email=o.get("email") or c.get("email") or "",
                address=", ".join(x for x in (a.get("address1"), a.get("address2")) if x), city=a.get("city") or "",
                state=a.get("province") or "", pincode=re.sub(r"\D", "", a.get("zip") or "")[:6],
                lat=s.get("latitude"), lng=s.get("longitude"), added_at=now().isoformat())


def first(p): return ((p.get("customer_name") or "there").split() or ["there"])[0].title()


def compose(t, which):
    """(named body values, other components) for one template. Link buttons carry the test's token (/b/ book or reschedule, /c/ confirm)."""
    p, b = t["program"], t["booking"] or {}
    name = {"first_name": first(p)}
    buttons = [{"type": "button", "sub_type": "url", "index": str(i), # Briza names a link's {{1}} like the body's named variables
                "parameters": [{"type": "text", "parameter_name": "1", "text": tok(p["program_id"], t["n"])}]} for i in range(2)]
    book_by = {"book_by": fmt_day(max(t["due"], today() + dt.timedelta(days=SLOT_DAYS - 1)))}
    if which == "custom": return {**name, "test_name": p.get("product") or "personalised blood test", **book_by}, buttons[:1]
    if which == "nudge": return {**name, **book_by}, buttons[:1] # day 3 / 5 / 7 booking reminder: the same booking link
    if which == "cancelled": return {**name, "date": fmt_day(b["collection_date"]), "slot": b["slot"]}, buttons[:1] # + Book again (same link)
    if which == "t1":
        v = {**name, **book_by}
        return ({**v, "program": (p.get("product") or "Sova").split(" - ")[0]} if t["n"] == 1 else v), buttons[:1] # Test 1: the welcome
    if which == "t2": return {**name, "date": fmt_day(b["collection_date"]), "slot": b["slot"]}, buttons # Yes -> /c/, Reschedule -> /b/
    if which in ("thanks", "booked"): return {**name, "date": fmt_day(b["collection_date"]), "slot": b["slot"]}, []
    if which == "rescheduled":
        old = t["history"][-2]
        return {**name, "old_slot": "%s, %s" % (fmt_day(old["collection_date"]), old["slot"]), "date": fmt_day(b["collection_date"]), "slot": b["slot"]}, []
    if which == "report": # View report -> /r/ with a code of its own, so a booking or reminder link can't open the report
        rb = dict(buttons[0], parameters=[{"type": "text", "parameter_name": "1", "text": tok("r:" + p["program_id"], t["n"])}])
        return {**name, "valid_till": fmt_day(today() + dt.timedelta(days=report_days()))}, [rb]
    return name, [] # collected


def report_days(): return max(1, int(setting("report_link_days") or 30))


def removed_programs(): return {x for x in setting("removed_programs").split(",") if x}

# ---------- daily insights: one read-only report on jobs, sends, errors, data and what needs a person (no names or phones) ----------
def insights_key(): return hmac.new((E("TOKEN", "\0") + "|insights").encode(), b"insights", hashlib.sha256).hexdigest()[:32]


_ins = {"at": 0, "key": None, "v": None}
JOBS = {"bloodtests-tick": 75, "bt-legacy-sync": 75, "bt-checks": 26 * 60} # job -> minutes after which its last run is stale


def _gcp(url, body=None):
    try: return http(url, body, headers={"Authorization": "Bearer " + gcp_token()}, method="POST" if body is not None else "GET")
    except Exception as e: return 0, {"error": str(e)[:200]}


def insights(hours=None):
    """Everything worth knowing about the last `hours` (default: 24, or 72 on Monday to cover the weekend) plus what needs a person now."""
    at = now()
    hours = int(hours or (72 if at.astimezone(IST).weekday() == 0 else 24))
    if _ins["key"] == hours and time.time() - _ins["at"] < 300: return _ins["v"]
    since = at - dt.timedelta(hours=hours)
    alerts, out = [], {"generated_at": at.astimezone(IST).isoformat(timespec="minutes"), "window_hours": hours, "revision": E("K_REVISION", "?")}
    # settings
    cfg_ = {"send_mode": send_mode(), "rcl_env": setting("rcl_env") or "staging", "live_from": setting("live_from"), "wa_hold": setting("wa_hold"),
            "briza_chat_link": bool(setting("briza_chat_url"))}
    out["settings"] = cfg_
    if cfg_["send_mode"] != "live": alerts.append("Send mode is %s, not live: customers get no WhatsApps." % cfg_["send_mode"])
    if cfg_["rcl_env"] != "prod": alerts.append("Redcliffe is on %s, not production." % cfg_["rcl_env"])
    if cfg_["wa_hold"]: alerts.append("WhatsApp template(s) on hold in Briza: %s." % cfg_["wa_hold"])
    # scheduled jobs (Cloud Scheduler)
    st, r = _gcp("https://cloudscheduler.googleapis.com/v1/projects/%s/locations/asia-south1/jobs" % PROJECT)
    jobs = {}
    for j in (r.get("jobs") or []) if st == 200 else []:
        k = j["name"].rsplit("/", 1)[-1]
        if k not in JOBS: continue
        last = j.get("lastAttemptTime")
        age = int((at - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() // 60) if last else None
        jobs[k] = {"state": j.get("state"), "schedule": j.get("schedule"), "last_run_minutes_ago": age, "last_status": (j.get("status") or {}).get("code", 0)}
        if j.get("state") != "ENABLED": alerts.append("Job %s is %s." % (k, j.get("state")))
        elif age is None or age > JOBS[k]: alerts.append("Job %s has not run for %s minutes." % (k, age if age is not None else "?"))
        if (j.get("status") or {}).get("code"): alerts.append("Job %s's last run failed (code %s)." % (k, j["status"]["code"]))
    sched_st = st
    for k in JOBS:
        if st == 200 and k not in jobs: alerts.append("Job %s is missing." % k)
    out["jobs"] = jobs
    # activity in the window
    rows = q("SELECT kind, note, status, test_no, program_id, by_user, booking_id, collection_date, SUBSTR(detail, 1, 300) d, `at` FROM `%s` WHERE `at` >= TIMESTAMP(@s) "
             "AND program_id NOT IN ('_legacy', '_checks', '_admin', '_manual') OR (`at` >= TIMESTAMP(@s) AND kind IN ('msg_manual', 'legacy_sync', 'check'))" % LOG, s=since.isoformat())
    order = {}
    for p in load()[0]: order[p["program_id"]] = p.get("order_name") or p["program_id"]
    msgs = {}
    for r_ in rows:
        if r_["kind"] in ("msg", "msg_manual"):
            k = "%s %s" % (("manual " if r_["kind"] == "msg_manual" else "") + (r_.get("note") or "?"), r_.get("status") or "")
            msgs[k.strip()] = msgs.get(k.strip(), 0) + 1
    out["whatsapps"] = dict(sorted(msgs.items(), key=lambda x: -x[1]))
    failed = [r_ for r_ in rows if r_["kind"] in ("msg", "msg_manual") and r_.get("status") == "failed"]
    out["failed_whatsapps"] = [{"order": order.get(r_["program_id"], r_["program_id"]), "template": r_.get("note"), "at": r_["at"].astimezone(IST).strftime("%d %b %H:%M")} for r_ in failed][:20]
    if failed: alerts.append("%d WhatsApp(s) failed: see failed_whatsapps." % len(failed))
    held = sum(1 for r_ in rows if r_["kind"] == "msg" and r_.get("status") == "held")
    if held: alerts.append("%d WhatsApp(s) held (template not approved in Briza)." % held)
    errs = [r_ for r_ in rows if r_["kind"] == "error"]
    out["app_errors"] = [{"order": order.get(r_["program_id"], r_["program_id"]), "error": (r_.get("note") or "")[:160], "at": r_["at"].astimezone(IST).strftime("%d %b %H:%M")} for r_ in errs][:20]
    if errs: alerts.append("%d error(s) logged by the app (bookings / Redcliffe / Briza): see app_errors." % len(errs))
    count = lambda kind, **f: sum(1 for r_ in rows if r_["kind"] == kind and all(r_.get(a) == b for a, b in f.items()))
    rcl = {}
    for r_ in rows:
        if r_["kind"] == "rcl" and r_.get("status"): rcl[r_["status"]] = rcl.get(r_["status"], 0) + 1
    out["activity"] = {"booked": count("booked"), "booked_by_customer": count("booked", by_user="customer"), "rescheduled": count("rescheduled"),
                       "cancelled": count("cancelled"), "link_clicks": count("click"), "number_verified": count("verified"),
                       "redcliffe_updates": dict(sorted(rcl.items(), key=lambda x: -x[1])),
                       "reports_sent": sum(1 for r_ in rows if r_["kind"] == "msg" and r_.get("note") == "report" and r_.get("status") == "sent"),
                       "new_programs": sum(1 for p in load()[0] if p["purchased_at"] >= since and p.get("source") not in ("test", "custom", "linked"))}
    syncs = [r_ for r_ in rows if r_["kind"] == "legacy_sync"]
    last_sync = max(syncs, key=lambda r_: r_["at"]) if syncs else None
    out["sheet_sync"] = {"runs": len(syncs), "failed_runs": sum(1 for r_ in syncs if r_.get("status") != "ok"),
                         "last": last_sync["at"].astimezone(IST).strftime("%d %b %H:%M") if last_sync else None,
                         "date_changes_applied": sum(int(m_.group(1)) for r_ in syncs for m_ in [re.search(r'program dates": "(\d+) changes', r_.get("d") or "")] if m_)}
    if syncs and out["sheet_sync"]["failed_runs"]: alerts.append("%d legacy-sheet sync run(s) failed." % out["sheet_sync"]["failed_runs"])
    checks = [r_ for r_ in rows if r_["kind"] == "check"]
    out["checks_11_13_17"] = len(checks)
    if sched_st != 200: # no Cloud Scheduler access: each job's last run from the rows it writes to bt_log
        jobs = {"source": "app log (Cloud Scheduler not readable: %s)" % sched_st}
        for k, kind in (("bloodtests-tick", "tick"), ("bt-legacy-sync", "legacy_sync"), ("bt-checks", "check")):
            rs = [r_ for r_ in rows if r_["kind"] == kind]
            last = max((r_["at"] for r_ in rs), default=None)
            age = int((at - last).total_seconds() // 60) if last else None
            jobs[k] = {"runs": len(rs), "last_run_minutes_ago": age, "failed_runs": sum(1 for r_ in rs if kind == "tick" and r_.get("status") == "failed")}
            if age is None or age > JOBS[k]: alerts.append("Job %s has not run for %s minutes." % (k, age if age is not None else "%d+" % (hours * 60)))
            if jobs[k]["failed_runs"]: alerts.append("%d hourly run(s) could not sync Shopify orders / SKUs." % jobs[k]["failed_runs"])
        out["jobs"] = jobs
    # what needs a person now (every program, folded fresh)
    progs, logs = load()
    tests = rows_for(progs, logs)
    by = {}
    for t in tests: by[t["status"]] = by.get(t["status"], 0) + 1
    today_, tom = today(), today() + dt.timedelta(days=1)
    early = [t for t in tests if t["booking"] and t["status"] in ("booked", "check") and t["booking"]["collection_date"] < (t["opens"] - dt.timedelta(days=2)).isoformat()]
    out["now"] = {"tests_by_status": dict(sorted(by.items(), key=lambda x: -x[1])),
                  "customer_requests_open": sum(1 for t in tests if t["ask"]),
                  "needs_follow_up": by.get("followup", 0), "check_with_redcliffe": by.get("check", 0),
                  "collections_today": sum(1 for t in tests if t["booking"] and t["status"] in ("booked", "check") and t["booking"]["collection_date"] == today_.isoformat()),
                  "collections_tomorrow": sum(1 for t in tests if t["booking"] and t["status"] in ("booked", "check") and t["booking"]["collection_date"] == tom.isoformat()),
                  "booking_messages_due_next_2_days": sum(1 for t in tests if t["status"] in ("upcoming", "to_send") and today_ <= t["opens"] <= today_ + dt.timedelta(days=2)),
                  "booked_before_window": [order.get(t["program"]["program_id"]) for t in early][:20]}
    if out["now"]["customer_requests_open"]: alerts.append("%d customer request(s) open on the dashboard (e.g. wants to reschedule)." % out["now"]["customer_requests_open"])
    if by.get("check"): alerts.append("%d booking(s) need a check with Redcliffe." % by["check"])
    if by.get("followup"): alerts.append("%d test(s) need a follow-up call." % by["followup"])
    # program dates vs the legacy sheet
    try:
        sd = sheet_dates(apply=False)
        odd = [r_ for r_ in sd["programs"] if r_["result"].startswith(("sheet rows disagree", "invalid", "to fix"))]
        out["dates_vs_sheet"] = {"tally": sd["tally"], "needs_attention": [{"order": r_["order"], "issue": r_["result"][:80]} for r_ in odd][:20]}
        if any(r_["result"] == "to fix" for r_ in odd): alerts.append("Some program dates differ from the legacy sheet and were not applied yet.")
    except Exception as e: out["dates_vs_sheet"] = {"unavailable": str(e)[:200]}
    # service errors in Cloud Logging
    flt = ('resource.type="cloud_run_revision" AND resource.labels.service_name="sova-bloodtests" AND severity>=ERROR AND timestamp>="%s"'
           % since.strftime("%Y-%m-%dT%H:%M:%SZ"))
    st, r = _gcp("https://logging.googleapis.com/v2/entries:list", {"resourceNames": ["projects/" + PROJECT], "filter": flt, "orderBy": "timestamp desc", "pageSize": 200})
    if st == 200:
        top = {}
        for en in r.get("entries") or []:
            txt = (en.get("textPayload") or json.dumps(en.get("jsonPayload") or {}))
            line = next((l_ for l_ in reversed(txt.strip().splitlines()) if l_.strip()), txt)[:160]
            top[line] = top.get(line, 0) + 1
        n_ = sum(top.values())
        out["service_errors"] = {"count": n_, "top": [{"message": k, "times": v} for k, v in sorted(top.items(), key=lambda x: -x[1])[:5]],
                                 "out_of_memory": sum(v for k, v in top.items() if "Memory limit" in k)}
        if n_: alerts.append("%d service error log line(s) in Cloud Logging: see service_errors." % n_)
    else: out["service_errors"] = {"unavailable": "Cloud Logging read failed (%s)" % st}
    out["alerts"], out["ok"] = alerts, not alerts
    _ins.update(at=time.time(), key=hours, v=out)
    return out


def sheet_dates(apply=False, by="admin (token)"):
    """Program start / end dates from the legacy sheet for every program order we track (the sheet holds the real start, which the coaching
    platform sets; the purchase date is only our fallback). Match by order id (split '-1' orders too), else by phone with the sheet's paid
    date within 7 days of our purchase. Blood Test tab first, then Program-Main. A program whose sheet rows disagree is left alone.
    apply=True writes the sheet's dates as start / end changes on each mismatching program (the same rows as Update dates)."""
    progs, logs = load()
    progs = [p for p in progs if p.get("source") not in ("test", "custom", "linked")]
    src = []
    bt, _ = LB.legacy_rows(legacy_table(LB.SHEET), types=legacy_types(LB.SHEET))
    src += [dict(tab="Blood Test", order=r["order"], phone=r["phone"], paid=r.get("paid") or "", status=r.get("pstat") or "", start=r.get("start") or "",
                 end=r.get("end") or "", excluded=bool(r.get("excluded"))) for r in bt]
    pm, _ = LB.ops_data(legacy_table(LB.OPS), legacy_types(LB.OPS))
    src += [dict(tab="Program-Main", order=r.get("order_id") or "", phone=r.get("phone") or "", paid=r.get("paid_iso") or "", status=r.get("prog_status") or "",
                 start=r.get("start_iso") or "", end=r.get("end_iso") or "", excluded=(r.get("prog_status") or "").strip().lower() in LB.EXCLUDED) for r in pm]
    by_order, by_phone = {}, {}
    for r in src:
        k = LB.order_key(r["order"])
        if k:
            by_order.setdefault(k, []).append(r)
            base = re.sub(r"-\d+$", "", k)
            if base != k: by_order.setdefault(base, []).append(r)
        if LB.phone10(r["phone"]): by_phone.setdefault(LB.phone10(r["phone"]), []).append(r)
    def near(a, b, days=7):
        try: return abs((dt.date.fromisoformat(a) - b).days) <= days
        except (TypeError, ValueError): return False
    out, new_rows, tally = [], [], {}
    for p in progs:
        bought = p["purchased_at"].astimezone(IST).date()
        keys = {LB.order_key(o) for o in [p.get("order_name")] + [m.split("-")[0] for m in p["members"]] if o}
        hits = [r for k in keys for r in by_order.get(k, ())]
        how = "order"
        if not hits: hits, how = [r for r in by_phone.get(LB.phone10(p.get("phone")), ()) if near(r["paid"], bought)], "phone"
        dated = [r for r in hits if r["start"]]
        row = {"program_id": p["program_id"], "order": p.get("order_name"), "name": p.get("customer_name"), "bought": bought.isoformat(),
               "our_start": program_start(p).isoformat(), "our_end": program_end(p).isoformat(), "match": how if hits else ""}
        t2 = next((t for t in rows_for([p], logs) if t["n"] == 2), None)
        if t2: row.update(t2_status=t2["status"], t2_booked=(t2["booking"] or {}).get("collection_date"), t2_sent=bool(t2.get("t1_at")))
        manual = next((jd(r).get("via") for r in reversed(logs.get(p["program_id"], [])) if r["kind"] in ("start", "end")), None) in ("profile", "csv")
        if not hits: kind = "not in sheet"
        elif not dated: kind = "no start date in sheet"
        else:
            starts = {r["start"] for r in dated}
            first_tab = [r for r in dated if r["tab"] == dated[0]["tab"]] # the tab we take dates from (Blood Test when it has them)
            first_tab = [r for r in dated if r["tab"] == "Blood Test"] or first_tab
            if len({r["start"] for r in first_tab}) > 1: kind = "sheet rows disagree"
            else:
                r = next((x for x in dated if x["tab"] == "Blood Test"), dated[0])
                sd = dt.date.fromisoformat(r["start"])
                ed = dt.date.fromisoformat(r["end"]) if r["end"] else sd + dt.timedelta(days=int(p.get("days") or 0))
                row.update(sheet_start=sd.isoformat(), sheet_end=ed.isoformat(), sheet_tab=r["tab"], sheet_status=r["status"], excluded=r["excluded"])
                if sd == program_start(p) and ed == program_end(p): kind = "matches"
                elif manual: kind = "kept: dates set by the team on the dashboard"
                else:
                    rows_, err = date_rows(p, sd, ed, by, "sheet")
                    kind = "invalid sheet dates: " + err if err else "fixed" if apply else "to fix"
                    if not err: new_rows += rows_
        row["result"] = kind
        tally[kind.split(":")[0]] = tally.get(kind.split(":")[0], 0) + 1
        out.append(row)
    if apply:
        for k in range(0, len(new_rows), 500):
            chunk = new_rows[k:k + 500]
            bad = bq().insert_rows_json(LOG, chunk, row_ids=[x["id"] for x in chunk])
            if bad: raise RuntimeError("log insert failed: %s" % str(bad)[:200])
        log(program_id="_admin", kind="sheet_dates", note="Program dates set from the legacy sheet: %d changes" % len(new_rows), by_user=by, detail=tally)
        _lp["v"] = None
    return {"tally": tally, "changes": len(new_rows), "programs": out}


def purge_tests(extra, cancels, by, dry=True):
    """Remove test data: every TEST- program plus `extra` program ids. Cancels the listed Redcliffe bookings first (no WhatsApp), copies
    every row into bt_removed_* (same columns) before deleting from bt_programs / bt_log / bt_results, and lists the ids in the
    removed_programs setting so the order sync never adds them back. dry=True only reports."""
    ids = sorted({r["program_id"] for r in q("SELECT DISTINCT program_id FROM `%s` WHERE program_id LIKE 'TEST-%%'" % PROGRAMS)} | set(extra))
    counts = {t: q("SELECT COUNT(*) n FROM `%s` WHERE program_id IN UNNEST(SPLIT(@i, '|'))" % (DS + "." + t), i="|".join(ids))[0]["n"] for t in ("bt_programs", "bt_log", "bt_results")}
    out = {"programs": ids, "rows": counts, "cancels": [], "dry": dry}
    for c in cancels:
        pid, bid = c["program_id"], str(c["booking_id"])
        if pid not in ids: out["cancels"].append({"booking_id": bid, "result": "skipped: program not in the removal list"}); continue
        mine = q("SELECT COUNT(*) n FROM `%s` WHERE program_id = @p AND booking_id = @b AND kind IN ('booked', 'rescheduled')" % LOG, p=pid, b=bid)[0]["n"]
        if not mine: out["cancels"].append({"booking_id": bid, "result": "skipped: not this program's booking"}); continue
        if dry: out["cancels"].append({"booking_id": bid, "result": "would cancel"}); continue
        why = "Cancelled by %s: test booking removed" % by
        st, r = rcl("POST", "/api/external/v2/center-update-booking/", {"booking_status": "cancelled", "booking_id": int(bid), "remark": why, "remarks": why})
        out["cancels"].append({"booking_id": bid, "result": "cancelled" if rcl_ok(r) else "FAILED: " + rcl_msg(r)})
        if not rcl_ok(r): out["stopped"] = "a Redcliffe cancel failed: nothing was deleted"; return out
    if dry: return out
    for t in ("bt_programs", "bt_log", "bt_results"):
        src, bak = DS + "." + t, DS + ".bt_removed_" + t[3:]
        q("CREATE TABLE IF NOT EXISTS `%s` LIKE `%s`" % (bak, src))
        q("INSERT `%s` SELECT * FROM `%s` WHERE program_id IN UNNEST(SPLIT(@i, '|'))" % (bak, src), i="|".join(ids))
        q("DELETE FROM `%s` WHERE program_id IN UNNEST(SPLIT(@i, '|'))" % src, i="|".join(ids))
    set_cfg("removed_programs", ",".join(sorted(removed_programs() | set(ids))), by)
    log(program_id="_admin", kind="purge", note="Removed %d test programs" % len(ids), by_user=by, detail=out)
    out["backup"] = "bt_removed_programs, bt_removed_log, bt_removed_results"
    return out


# ---------- send a template by hand: any number, any of our templates (Settings -> Send a WhatsApp) ----------
MANUAL = [("t1_template", "t1", 1), ("t1_next_template", "t1", 2), ("nudge_template", "nudge", 1), ("custom_template", "custom", 1),
          ("t2_template", "t2", 1), ("thanks_template", "thanks", 1), ("booked_template", "booked", 1), ("cancelled_template", "cancelled", 1),
          ("collected_template", "collected", 1), ("report_template", "report", 1)] # settings key, compose() kind, test number


def find_program(ref):
    """A program by its id, its order (#RE123 / RE123) or its customer's phone (latest one)."""
    ref = (ref or "").strip()
    if not ref: return None
    progs = load(ref)[0] if not re.fullmatch(r"#?[A-Za-z]*\d+|\+?[\d\s-]{10,15}", ref) else []
    if progs: return progs[0]
    allp = load()[0]
    ph, ok_ = norm_phone(ref), LB.order_key(ref)
    hit = [p for p in allp if LB.order_key(p.get("order_name")) == ok_ or any(m.split("-")[0] == ok_ for m in p["members"])] or \
          ([p for p in allp if ph and p.get("phone") == ph] if ph else [])
    return sorted(hit, key=lambda p: p["purchased_at"], reverse=True)[0] if hit else None


def manual_message(key, ref="", name=""):
    """(template, named values, button components, program or None, test) for one template: from a customer's own program and booking when
    ref finds one (their buttons then open their real booking / report page), else sample values (buttons open a 'not found' page)."""
    which, n = next(((w, k) for kk, w, k in MANUAL if kk == key), (None, None))
    if not which: raise ValueError("unknown template")
    p = find_program(ref)
    t = None
    if p:
        progs, logs = load(p["program_id"])
        tests = [x for x in rows_for(progs, logs) if x["program"]["program_id"] == progs[0]["program_id"]]
        t = next((x for x in tests if x["n"] == n), tests[0] if tests else None)
    tom = today() + dt.timedelta(days=1)
    sample = {"booking_id": "SAMPLE", "collection_date": tom.isoformat(), "slot": "07:00 AM - 08:00 AM"}
    if t is None:
        prog = p or {"program_id": "SAMPLE", "customer_name": name or "Customer", "product": "Gut Heal 3 Month Program"}
        t = {"program": prog, "n": n, "booking": None, "due": today() + dt.timedelta(days=6), "history": []}
    t = dict(t, program=dict(t["program"], customer_name=name or t["program"].get("customer_name")), n=n)
    b = t.get("booking") or sample
    t["booking"] = b
    t["history"] = (t.get("history") or [])[-1:] if len(t.get("history") or []) >= 2 else [dict(sample, collection_date=today().isoformat(), slot="06:00 AM - 07:00 AM"), b]
    params, buttons = compose(t, which)
    return setting(key), params, buttons, p, n


def manual_send(phone, key, params, ref, by):
    """Send one template to one number now (whatever the send mode) and log it. -> (ok, Briza's answer, template)"""
    ph = norm_phone(phone)
    if not ph: raise ValueError("enter a valid mobile number")
    tpl, defaults, buttons, p, n = manual_message(key, ref)
    if p and buttons and ph != p.get("phone") and ph not in test_numbers(): # a customer's own links (the report one needs no OTP) only go to them
        raise ValueError("this customer's links only go to their own number (ending %s) or a test number. Clear the customer field to send "
                         "example values to another number" % (p.get("phone") or "")[-4:])
    vals = {k: (params.get(k) or "").strip() or str(v) for k, v in defaults.items()}
    body = [{"type": "body", "parameters": [{"type": "text", "parameter_name": k, "text": v} for k, v in vals.items()]}]
    ok, res = briza_send(ph, tpl, body + buttons)
    log(program_id=p["program_id"] if p else "_manual", test_no=n if p else None, kind="msg_manual", note=key.replace("_template", ""), status="sent" if ok else "failed",
        by_user=by, detail={"template": tpl, "phone": ph, "params": vals, "result": res})
    return ok, res, tpl


def manual_log(limit=25):
    try: return q("SELECT `at`, by_user, status, note, program_id, detail FROM `%s` WHERE kind = 'msg_manual' ORDER BY `at` DESC LIMIT %d" % (LOG, limit))
    except Exception as e: print("manual log read failed", e); return []


def send(t, which, by):
    """One template for one test -> 'sent' | 'planned' | 'held' | 'failed'. t1 book (link), booked / rescheduled (confirmations),
    t2 reminder (links Yes / Reschedule), thanks (after Yes), collected (report in 3-4 days), report (the PDF)."""
    p, n, b = t["program"], t["n"], t["booking"]
    if which in ("collected", "report") and any(r["kind"] == "msg" and r.get("note") == which and r.get("booking_id") == b["booking_id"] and
                                                counts(r, is_real(p["phone"])) for r in load(p["program_id"])[1].get(p["program_id"], [])):
        return "skipped" # another run sent it a moment ago
    params, extra = compose(t, which)
    tpl = setting("t1_next_template") if which == "t1" and n > 1 else setting(which + "_template")
    real = is_real(p["phone"])
    body = [{"type": "body", "parameters": [{"type": "text", "parameter_name": k, "text": str(v)} for k, v in params.items()]}]
    ok, res = briza_send(p["phone"], tpl, body + extra) if real else (True, "planned (send mode %s)" % send_mode())
    held = real and not ok and bool(re.search(r"TEMPLATE_NOT_(APPROVED|FOUND)", str(res)))
    hold = set(filter(None, setting("wa_hold").split(",")))
    if real and (held or ok) and (hold | {tpl} if held else hold - {tpl}) != hold:
        set_cfg("wa_hold", ",".join(sorted(hold | {tpl} if held else hold - {tpl})), by) # the banner on every page
    if held: return "held" # template not in Briza / not approved by Meta yet: retry next hour, nothing logged
    status = ("sent" if real else "planned") if ok else "failed"
    log(program_id=p["program_id"], test_no=n, kind="msg", note=which, status=status, by_user=by, booking_id=b["booking_id"] if b else None,
        collection_date=b["collection_date"] if which == "t2" else today().isoformat() if which == "nudge" else None,
        detail={"template": tpl, "params": params, "result": res})
    return status


def first_msg(t): return "custom" if t.get("custom") else "t1" # the booking WhatsApp: personalised tests have their own


def plan(rows, at=None):
    """Due sends: (row, which). t1 once the window opens (until a week past due); t2 the day before collection, once per date;
    collected / report once per booking when Redcliffe's status gets there. Three failures stop the retries (it waits in Failed)."""
    day = (at or now()).astimezone(IST).date()
    out = []
    for t in rows:
        b, w = t["booking"], {"to_send": first_msg(t), "booked": "t2", "collected": "collected", "report": "report"}.get(t["status"])
        if t["status"] in ("awaiting", "followup") and t["t1_at"] and (day - t["opens"]).days in NUDGE_DAYS: w = "nudge" # not booked yet: day 3 / 5 / 7
        if t["status"] == "cancelled" and b and b["collection_date"] >= (day - dt.timedelta(days=2)).isoformat(): w = "cancelled" # tell them, once
        if not t["program"]["phone"] or not w or w == "t2" and b["collection_date"] != (day + dt.timedelta(days=1)).isoformat(): continue
        same = [r for r in t.get("logs", []) if r["kind"] == "msg" and r.get("test_no") == t["n"] and r.get("note") == w and
                (w in ("t1", "custom") or (r.get("collection_date") == (b["collection_date"] if w == "t2" else day.isoformat()) if w in ("t2", "nudge")
                                           else str(r.get("booking_id")) == str(b["booking_id"])))]
        if sum(r["status"] == "failed" for r in same) < 3 and (w in ("t1", "custom") or not any(counts(r, t["real"]) for r in same)): out.append((t, w))
    return out


def refresh_status(t):
    """Booked tests on/after their date: Redcliffe booking status + report link (logged only when it changes)."""
    b = t["booking"]
    st, r = rcl("GET", "/api/external/v2/center-get-booking", params={"booking_id": b["booking_id"]})
    rows = r if isinstance(r, list) else (r.get("data") if isinstance(r, dict) and isinstance(r.get("data"), list) else [])
    d = next((x for x in rows if isinstance(x, dict) and str(x.get("booking_id") or x.get("pk")) == str(b["booking_id"])), rows[0] if rows and isinstance(rows[0], dict) else {})
    bs, pick = d.get("booking_status"), d.get("pickup_status")
    status = " · ".join(str(x) for x in ((bs.get("value") if isinstance(bs, dict) else bs), "pickup %s" % pick if pick and str(pick).lower() != "pending" else None) if x)
    rep_ = d.get("report") if isinstance(d.get("report"), list) else []
    link = next((x.get("report_link") for x in rep_ if isinstance(x, dict) and x.get("report_link")), None) or d.get("report_url") or d.get("Report_url")
    changed = bool(status) and (status != t["rcl_status"] or bool(link) and link != t["report"])
    if changed:
        log(program_id=t["program"]["program_id"], test_no=t["n"], kind="rcl", status=str(status)[:100], booking_id=b["booking_id"],
            by_user="redcliffe", detail={"report_link": link, "phlebo": d.get("phlebo_detail") or d.get("phlebo_details"), "raw": d})
        if link and str(link).startswith("https://") and link != t["report"]: keep(t, link, "booking status", "redcliffe")
    return changed, status or rcl_msg(r)


def tick():
    cfg(refresh=True)
    out = {"mode": send_mode()}
    if out["mode"] == "off": return out
    for name, job in (("skus", sync_skus), ("orders", sync_orders)):
        try: out[name] = job()
        except Exception as e: out[name] = "failed: %s" % str(e)[:200]; print(name, "sync failed:", e) # keep serving known programs
    progs, logs = load()
    n = {"sent": 0, "planned": 0, "failed": 0, "held": 0, "skipped": 0, "waiting": 0, "deferred": 0, "status_updates": 0}
    for t in rows_for(progs, logs): # Redcliffe first: a collected sample or a ready report is messaged in this same run
        if t["status"] in ("booked", "check", "collected") and t["booking"]["collection_date"] <= today().isoformat() \
                and t["booking"]["collection_date"] >= (today() - dt.timedelta(days=12)).isoformat():
            try: n["status_updates"] += refresh_status(t)[0]
            except Exception as e: print("status", t["program"]["program_id"], e)
    if n["status_updates"]: progs, logs = load()
    rows = rows_for(progs, logs)
    for t in rows: t["logs"] = logs.get(t["program"]["program_id"], [])
    hour, budget = now().astimezone(IST).hour, int(setting("max_sends_per_run") or 200)
    for t, which in plan(rows):
        real = is_real(t["program"]["phone"])
        if real and (not 10 <= hour < 19 or budget <= 0): n["waiting"] += 1; continue # real sends 10:00-19:00 IST only
        if real and chatting(t["program"]["phone"]): n["deferred"] += 1; continue # in another chat with Sova: next run
        n[send(t, which, "schedule")] += 1
        if real: budget -= 1; time.sleep(1.1) # Briza allows 60 per minute
    return {**out, **n}


def reports_run():
    """11:00, 13:00 and 17:00 IST (Cloud Run job bt-reports, trigger bt-checks -> POST /reports): Redcliffe's status for every scheduled or
    rescheduled booking (12 days back to 2 weeks ahead), the final report of every booking collected in the last 30 days that has none yet,
    then the due sample-collected / report WhatsApps (View report opens the report page). Each run leaves a summary row for Analytics."""
    cfg(refresh=True)
    if send_mode() == "off": return {"mode": "off"}
    progs, logs = load()
    n = {"mode": send_mode(), "bookings_checked": 0, "status_updates": 0, "checked": 0, "found": 0, "sent": 0, "planned": 0, "failed": 0, "held": 0,
         "skipped": 0, "deferred": 0, "waiting": 0}
    lo, hi = (today() - dt.timedelta(days=12)).isoformat(), (today() + dt.timedelta(days=14)).isoformat()
    for t in rows_for(progs, logs):
        if t["status"] in ("booked", "check", "collected") and lo <= t["booking"]["collection_date"] <= hi:
            n["bookings_checked"] += 1
            try: n["status_updates"] += refresh_status(t)[0]
            except Exception as e: print("status", t["program"]["program_id"], e)
    if n["status_updates"]: progs, logs = load()
    for t in rows_for(progs, logs):
        b = t["booking"]
        if t["status"] not in ("booked", "check", "collected") or not today() - dt.timedelta(days=30) <= dt.date.fromisoformat(b["collection_date"]) <= today(): continue
        n["checked"] += 1
        try: link = consolidated_report(b["booking_id"])
        except Exception as e: print("report", t["program"]["program_id"], e); continue
        if link:
            n["found"] += 1
            log(program_id=t["program"]["program_id"], test_no=t["n"], kind="rcl", status="Report ready", booking_id=b["booking_id"],
                by_user="redcliffe", detail={"report_link": link, "source": "consolidated report"})
            keep(t, link, "consolidated report", "bt-reports")
    progs, logs = load()
    rows = rows_for(progs, logs)
    for t in rows: t["logs"] = logs.get(t["program"]["program_id"], [])
    for t, which in plan(rows):
        if which not in ("collected", "report", "cancelled"): continue
        if 8 <= now().astimezone(IST).minute < 20: n["waiting"] += 1; continue # the hourly run (:10) is sending: it takes these, no doubles
        real = is_real(t["program"]["phone"])
        if real and not 9 <= now().astimezone(IST).hour < 20: n["waiting"] += 1; continue # the hourly run sends it later
        if real and chatting(t["program"]["phone"]): n["deferred"] += 1; continue
        n[send(t, which, "schedule")] += 1
    log(program_id=CHECKS, kind="check", note=now().astimezone(IST).strftime("%H:%M"), by_user="bt-reports", detail=n)
    return n


def hook_key(): return hmac.new((E("TOKEN", "\0") + "|rcl-hook").encode(), b"redcliffe", hashlib.sha256).hexdigest()[:32]


HOOK_STATUS = {"booking_created": "Order booked", "phlebo_started_journey": "Phlebo on the way", "phlebo_end_journey": "Phlebo reached",
               "consolidatereport": "Report ready", "partialreport": "Partial report ready"}


def slot_label(start, end=None):
    """Redcliffe's '08:00:00' (+ end) -> '08:00 AM - 09:00 AM', our booking pages' format."""
    a = dt.datetime.strptime(str(start)[:8], "%H:%M:%S")
    b = dt.datetime.strptime(str(end)[:8], "%H:%M:%S") if end else a + dt.timedelta(hours=1)
    return "%s - %s" % (a.strftime("%I:%M %p"), b.strftime("%I:%M %p"))


def rcl_hook(payload):
    """One Redcliffe webhook (any type) -> a status row on the test it belongs to. A Redcliffe-side reschedule moves the booking (and tells
    the customer); a final report link (Redcliffe's own address only) makes the report ready; the due WhatsApp goes out at once. -> result"""
    kind = str(payload.get("webhook_type") or "")
    inner = payload["data"] if isinstance(payload.get("data"), dict) else {}
    d = inner["data"] if isinstance(inner.get("data"), dict) else {}
    bid = str(inner.get("booking_id") or payload.get("id") or "")
    if not bid: return "no booking id"
    url = payload.get("report_url") if kind in ("consolidatereport", "partialreport") else None
    if url and not re.fullmatch(r"https://([a-z0-9-]+\.)*redcliffelabs\.com/.*", str(url)): url = None # only Redcliffe's own report links
    status = HOOK_STATUS.get(kind) or str(d.get("customer_status") or kind).strip()
    if kind == "resample": status = "Resample requested (%s)" % status
    hit = q("SELECT program_id, test_no FROM `%s` WHERE booking_id = @b AND kind IN ('booked', 'rescheduled') ORDER BY `at` DESC LIMIT 1" % LOG, b=bid)
    if not hit: # not one of ours (or from before a reset): kept for the record
        log(program_id="_rcl", kind="rcl", status=status[:100], booking_id=bid, note=kind, by_user="redcliffe-webhook", detail=payload)
        return "not ours"
    pid, n = hit[0]["program_id"], int(hit[0]["test_no"])
    progs, logs = load(pid)
    tl = [x for x in tests_for(progs[0]) if x[0] == n] if progs else []
    if not tl: return "test not found"
    t = test_state(progs[0], *tl[0], logs.get(pid, []))
    current = bool(t["booking"]) and str(t["booking"]["booking_id"]) == bid
    if kind == "rescheduled" and current and d.get("sample_collection_date"):
        b, day = t["booking"], str(d["sample_collection_date"])[:10]
        label = slot_label(d.get("ref_booking_start_time") or d.get("start_time") or "08:00:00", d.get("ref_booking_end_time"))
        if day == b["collection_date"] and b["slot"][:8] == label[:8]: return "same slot" # our own reschedule, already logged
        row = log(program_id=pid, test_no=n, kind="rescheduled", booking_id=bid, collection_date=day, slot=label, by_user="redcliffe",
                  detail={"from": [b["collection_date"], b["slot"]], "webhook": payload})
        send({**t, "booking": row, "history": t["history"] + [row]}, "booked", "redcliffe") # tells the customer the new time (booked template)
        return "moved to %s, %s" % (day, label)
    last = next((r for r in reversed(logs.get(pid, [])) if r["kind"] == "rcl" and str(r.get("booking_id")) == bid), None)
    if last and last.get("status") == status[:100] and (not url or url in (jd(last).get("report_link"), jd(last).get("partial_report_link"))): return "no change"
    detail = {"webhook": kind, "raw": payload, **({"report_link" if kind == "consolidatereport" else "partial_report_link": url} if url else {}),
              **({"tracking_link": d["tracking_link"]} if d.get("tracking_link") else {})}
    log(program_id=pid, test_no=n, kind="rcl", status=status[:100], booking_id=bid, by_user="redcliffe-webhook", detail=detail)
    if not current: return "logged (an earlier booking)"
    if url: keep(t, url, kind, "redcliffe-webhook", partial=kind == "partialreport")
    progs, logs = load(pid)
    rows = [r for r in rows_for(progs, logs) if r["n"] == n]
    for r in rows: r["logs"] = logs.get(pid, [])
    for r, which in plan(rows):
        if which not in ("collected", "report", "cancelled"): continue # Redcliffe cancelled it: the customer hears at once
        if is_real(r["program"]["phone"]) and (not 9 <= now().astimezone(IST).hour < 20 or chatting(r["program"]["phone"])):
            return "logged; the WhatsApp goes with the next run"
        return "logged; WhatsApp %s" % send(r, which, "redcliffe")
    return "logged"


def report_page(token, request):
    """The report button: Open (in the browser) or Download (to the phone), no code needed; the link works for report_link_days after the
    message. The PDF comes from our address. -> (body, status, headers)"""
    ids = untok(token)
    ids = (ids[0][2:], ids[1]) if ids and ids[0].startswith("r:") else None # only the report button's own code
    progs, logs = load(ids[0]) if ids else ([], {})
    tl = [x for x in tests_for(progs[0]) if x[0] == ids[1]] if progs else []
    if not tl: return ui.message_page("This link isn't valid", "Please use the latest message from Sova on WhatsApp."), 404, {}
    p, L = progs[0], logs.get(ids[0], [])
    t = test_state(p, *tl[0], L)
    if not t["report"]: return ui.message_page("Your report isn't ready yet", "We'll send it to you here on WhatsApp as soon as Redcliffe Labs shares it."), 200, {}
    sent = [r for r in L if r["kind"] == "msg" and r.get("note") == "report" and r.get("test_no") == t["n"] and r["status"] in ("sent", "planned")]
    if sent and now() - sent[-1]["at"] > dt.timedelta(days=report_days()):
        return ui.message_page("This link has expired", "For your privacy, report links work for %d days. Please message Sova and we'll send a new one." % report_days()), 410, {}
    how = request.args.get("file")
    if how not in ("open", "download"):
        till = (sent[-1]["at"] if sent else now()) + dt.timedelta(days=report_days())
        got = next((r for r in reversed(L) if r["kind"] == "rcl" and r.get("test_no") == t["n"] and re.search(r"collect|pick", r.get("status") or "", re.I)), None)
        return ui.report(first(p), got["at"].astimezone(IST).strftime("%d %b") if got else "", fmt_day(till.astimezone(IST).date().isoformat())), 200, {}
    sample = t["report"].endswith(SAMPLE)
    url = None if sample else (consolidated_report(t["booking"]["booking_id"]) or t["report"]) # Redcliffe's latest link for this booking
    if not sample and not (url or "").startswith("https://"):
        return ui.message_page("We couldn't open your report", "Please try again in a few minutes, or message Sova and we'll send it to you."), 502, {}
    if not any(r["kind"] == "report_view" and r.get("test_no") == t["n"] and now() - r["at"] < dt.timedelta(hours=12) for r in L):
        log(program_id=p["program_id"], test_no=t["n"], kind="report_view", note=how, booking_id=t["booking"]["booking_id"], by_user="customer")
    if not sample: return url, 303, {} # the customer gets Redcliffe's own report link; our saved copy stays on our side (team only)
    pdf = fetch_pdf(SAMPLE)
    return pdf, 200, {"Content-Type": "application/pdf", "Cache-Control": "no-store, private",
                      "Content-Disposition": '%s; filename="Sova blood test report.pdf"' % ("attachment" if how == "download" else "inline")}


# ---------- booking (customer link and dashboard) ----------
def book(t, prof, day, slot_id, slot_label, by):
    """Create + confirm a Redcliffe home collection. -> (ok, message)"""
    p, n = t["program"], t["n"]
    codes = [c.strip() for c in (prof.get("package_codes") or setting("prod_package_codes" if prod() else "package_codes")).split(",") if c.strip()]
    if not codes: return False, "The test package isn't set up yet. Please message Sova and we'll book it for you."
    ph = prof["phone"][-10:]
    body = {"booking_date": today().isoformat(), "collection_date": day, "collection_slot": int(slot_id),
            "customer_name": redcliffe_name(prof.get("customer_name")), "customer_age": int(prof["age"]), "customer_gender": prof["gender"],
            "customer_email": prof.get("email") or "product@sova.health", "customer_phonenumber": ph, "customer_altphonenumber": ph,
            "customer_whatsappnumber": ph, "customer_whatsapppnumber": ph, # the doc spells it both ways
            "customer_address": prof.get("address") or "", "address_line2": ", ".join(x for x in (prof.get("city"), prof.get("state")) if x),
            "customer_landmark": prof.get("landmark") or prof.get("address") or "", "pincode": prof.get("pincode") or "",
            "customer_latitude": float(prof["lat"]), "customer_longitude": float(prof["lng"]), "package_code": codes, "is_credit": True,
            "additional_member": [], "reference_data": p["program_id"], "client_refid": "%s-T%d-%d" % (p["program_id"], n, int(time.time()))}
    st, r = rcl("POST", "/api/external/v2/center-create-booking/", body)
    bid = r.get("booking_id") or r.get("pk") if isinstance(r, dict) else None
    if not bid:
        log(program_id=p["program_id"], test_no=n, kind="error", note="Booking failed: " + rcl_msg(r), by_user=by, detail={"status": st, "response": r})
        return False, rcl_msg(r)
    st2, r2 = rcl("POST", "/api/external/v2/center-confirm-booking/", {"booking_id": bid, "is_confirmed": True})
    if not rcl_ok(r2) or "cancel" in str(r2.get("message", "")).lower():
        log(program_id=p["program_id"], test_no=n, kind="error", note="Confirm failed: " + rcl_msg(r2), booking_id=str(bid), by_user=by,
            detail={"create": r, "confirm": r2})
        return False, "Redcliffe couldn't confirm the slot (%s). Please try again." % rcl_msg(r2)
    row = log(program_id=p["program_id"], test_no=n, kind="booked", booking_id=str(bid), collection_date=day, slot=slot_label, by_user=by,
              detail={"slot_id": slot_id, "create": r, "confirm": r2})
    send({**t, "booking": row, "history": t["history"] + [row]}, "booked", by) # confirmation on WhatsApp
    return True, "Booked for %s, %s (booking ID %s)." % (fmt_day(day), slot_label, bid)


def reschedule(t, day, slot_id, slot_label, by):
    b, p, n = t["booking"], t["program"], t["n"]
    if t["resched"] >= MAX_RESCHEDULES: return False, "This booking has already been rescheduled %d times, the most allowed." % MAX_RESCHEDULES
    why = "Rescheduled by customer (WhatsApp link)" if by == "customer" else "Rescheduled by %s" % by
    st, r = rcl("POST", "/api/external/v2/center-update-booking/", {"booking_status": "rescheduled", "booking_id": int(b["booking_id"]),
                                                                    "remark": why, "remarks": why, "collection_date": day, "collection_slot": int(slot_id)})
    if not rcl_ok(r):
        log(program_id=p["program_id"], test_no=n, kind="error", note="Reschedule failed: " + rcl_msg(r), booking_id=b["booking_id"], by_user=by, detail=r)
        return False, rcl_msg(r)
    row = log(program_id=p["program_id"], test_no=n, kind="rescheduled", booking_id=b["booking_id"], collection_date=day, slot=slot_label, by_user=by,
              detail={"slot_id": slot_id, "from": [b["collection_date"], b["slot"]], "response": r})
    send({**t, "booking": row, "history": t["history"] + [row]}, "booked", by) # confirmation of the new time, with the booked template
    return True, "Moved from %s, %s to %s, %s." % (fmt_day(b["collection_date"]), b["slot"], fmt_day(day), slot_label)


def cancel(t, reason, by):
    """Cancel the current Redcliffe booking (ops, from the dashboard). The test can then be booked again."""
    b, p, n = t["booking"], t["program"], t["n"]
    why = "Cancelled by %s: %s" % (by, reason)
    st, r = rcl("POST", "/api/external/v2/center-update-booking/", {"booking_status": "cancelled", "booking_id": int(b["booking_id"]),
                                                                    "remark": why, "remarks": why})
    if not rcl_ok(r):
        log(program_id=p["program_id"], test_no=n, kind="error", note="Cancel failed: " + rcl_msg(r), booking_id=b["booking_id"], by_user=by, detail=r)
        return False, "Redcliffe did not cancel it: " + rcl_msg(r)
    log(program_id=p["program_id"], test_no=n, kind="cancelled", note=reason, booking_id=b["booking_id"], collection_date=b["collection_date"],
        slot=b["slot"], by_user=by, detail={"response": r})
    res = send(t, "cancelled", by) # "your <date>, <slot> collection has been cancelled" + Book again
    return True, "Booking %s cancelled in Redcliffe. Cancellation WhatsApp %s." % (b["booking_id"], {"sent": "sent", "planned": "logged as planned (test mode)",
        "held": "held (template bt_cancelled not approved yet)", "failed": "failed: see the timeline", "skipped": "skipped"}[res])


def booking_dates(t):
    start = max(t["opens"], today())
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(SLOT_DAYS) if start + dt.timedelta(days=i) <= today() + dt.timedelta(days=SLOT_DAYS - 1)]


# ---------- purchases from payments.sova.health / payx.sova.health (added 21 Nov 2026; additive, nothing above changed) ----------
# A person who paid for a blood test there gets a one-test row here, the same shape as a personalised test (sku CUSTOM, source custom),
# so every existing rule, view and reminder treats it exactly like one. The order name carries the "Blood test <> Purchase" mark. The
# payments service calls POST /purchase with its own key (derived from TOKEN, header X-Purchase-Key) and these actions:
#   create  -> the row (once per order) + the booking WhatsApp (custom template, under the send mode), returns the /b/ booking link
#   status  -> the test's state and booking;  address / slots / book / resched -> the booking page's own steps for the payments page
#   (that page verifies the customer's WhatsApp with its own OTP first; Redcliffe bookings go through book() / reschedule() here)
PURCHASE_MARK = "Blood test <> Purchase"


def purchase_key(): return hmac.new((E("TOKEN", "\0") + "|purchase").encode(), b"payments", hashlib.sha256).hexdigest()[:32]


def purchase_pid(store, order):
    o = re.sub(r"[^A-Za-z0-9]", "", str(order or ""))
    if not o: raise ValueError("order is required")
    return ("SX" if str(store or "").startswith("x.") else "") + o + "-BTP"


def _purchase_test(pid):
    progs, logs = load(pid)
    p = next((x for x in progs if x["program_id"] == pid), None)
    if not p: return None, None, []
    L = logs.get(pid, [])
    tl = [x for x in tests_for(p) if x[0] == 1]
    return (test_state(p, *tl[0], L) if tl else None), p, L


def _purchase_view(t):
    b = t["booking"] or {}
    return {"status": t["status"], "bookable": t["bookable"], "resched_left": MAX_RESCHEDULES - t["resched"],
            "booking": {"id": b.get("booking_id"), "date": b.get("collection_date"), "slot": b.get("slot")} if b else None, "report": bool(t["report"])}


def purchase_api(f, base, by="payments page"):
    """One JSON call from the payments service. -> (http status, dict)"""
    a = f.get("action")
    if a == "create":
        ph = norm_phone(f.get("phone"))
        if not ph: return 400, {"error": "a valid Indian mobile number is required"}
        codes = [c.strip().upper() for c in re.split(r"[,\s]+", f.get("package_codes") or "") if c.strip()]
        if not codes or not all(re.fullmatch(r"[A-Z0-9_\-]{2,30}", c) for c in codes): return 400, {"error": "Redcliffe package code(s) are required"}
        try: pid = purchase_pid(f.get("store"), f.get("order"))
        except ValueError as e: return 400, {"error": str(e)}
        existing = bool(q("SELECT program_id FROM `%s` WHERE program_id = @p LIMIT 1" % PROGRAMS, p=pid))
        if not existing:
            name = re.sub(r"\s+", " ", str(f.get("name") or "")).strip()[:80]
            row = dict(program_id=pid, order_name="%s · %s" % (str(f.get("order") or "").strip()[:30], PURCHASE_MARK), source="custom", sku=CUSTOM,
                       product=re.sub(r"\s+", " ", str(f.get("product") or "")).strip()[:120] or "Sova blood test", purchased_at=now().isoformat(),
                       days=30, added_at=now().isoformat(), customer_name=name, phone=ph, email=str(f.get("email") or "")[:120],
                       address=str(f.get("address") or "")[:200], city=str(f.get("city") or "")[:60], state=str(f.get("state") or "")[:40],
                       pincode=re.sub(r"\D", "", str(f.get("pincode") or ""))[:6], lat=None, lng=None)
            bad = bq().insert_rows_json(PROGRAMS, [row])
            if bad: return 500, {"error": "could not create it: %s" % str(bad)[:200]}
            log(program_id=pid, test_no=1, kind="profile", by_user=by, detail={"package_codes": ",".join(codes), "purchase": {
                k: str(f.get(k) or "")[:120] for k in ("store", "order", "ref", "plan")}})
        t, p, L = _purchase_test(pid)
        if not t: return 500, {"error": "created, but the test isn't readable yet; call create again"}
        wa = "skipped"
        if f.get("send_whatsapp") and not any(r["kind"] == "msg" and r.get("note") == "custom" and r["status"] in ("sent", "planned") for r in L):
            wa = send(t, "custom", by)
            t = _purchase_test(pid)[0] or t
        return 200, {"ok": True, "program_id": pid, "existing": existing, "whatsapp": wa, "link": base + "/b/" + tok(pid, 1), **_purchase_view(t)}
    pid = str(f.get("program_id") or "")
    if not pid.endswith("-BTP"): return 400, {"error": "program_id of a purchase is required"}
    t, p, L = _purchase_test(pid)
    if not t: return 404, {"error": "not found"}
    if a == "status": return 200, {"ok": True, "program_id": pid, "link": base + "/b/" + tok(pid, 1), **_purchase_view(t)}
    prof = profile(p, L)
    if a == "address":
        if not t["bookable"]: return 409, {"error": "This test is already booked or done."}
        new = {k: str(f.get(k) or "").strip()[:150] for k in ("house", "area", "city", "pincode", "landmark")}
        new["address"] = ", ".join(x for x in (new["house"], new["area"]) if x)
        if not re.fullmatch(r"\d{6}", new["pincode"]) or not new["house"]: return 400, {"error": "Please enter your flat / house no. and a 6-digit pincode."}
        dev = device_point(f.get("lat"), f.get("lng"))
        g = dev or geocode("%s %s" % (new["city"], new["pincode"])) or geocode(new["pincode"])
        ok, why = serviceable(*g) if g else (None, "")
        if not g or ok is False and "latitude" in why.lower(): return 400, {"error": "We couldn't find this address on the map. Please check the pincode, or use your current location."}
        if ok is None: return 503, {"error": "We couldn't check this address with Redcliffe just now. Please try again in a minute."}
        log(program_id=pid, test_no=1, kind="profile", by_user=by, detail={**new, "lat": g[0], "lng": g[1], "serviceable": ok,
                                                                           "located_by": "gps" if dev else "pincode", "redcliffe": why})
        return 200, {"ok": True, "serviceable": ok, "message": "" if ok else "Redcliffe doesn't collect at this address yet: " + why}
    if a == "slots":
        dates = booking_dates(t)
        day = f.get("day") if f.get("day") in dates else (dates[0] if dates else "")
        if not prof.get("lat"): return 409, {"error": "Please confirm your address first.", "dates": dates}
        sl, why = slots(prof["lat"], prof["lng"], day) if day else ([], "")
        return 200, {"ok": True, "dates": dates, "day": day, "slots": [{"id": i, "label": lb} for i, lb in sl], "message": why}
    if a in ("book", "resched"):
        day, sid = str(f.get("day") or ""), str(f.get("slot_id") or "")
        mode = "resched" if t["status"] == "booked" else "book" if t["status"] in BOOKABLE else "done"
        checked = next((r for r in reversed(L) if r["kind"] == "profile" and "serviceable" in jd(r)), None)
        if mode != a: return 409, {"error": "Please pick a date and a time slot again.", **_purchase_view(t)}
        if a == "book" and not (checked and jd(checked)["serviceable"]): return 409, {"error": "Please confirm your address first."}
        free = dict(slots(prof["lat"], prof["lng"], day)[0]) if day in booking_dates(t) and prof.get("lat") and sid.isdigit() else {}
        if not free: return 409, {"error": "Please pick a date and a time slot again."}
        if int(sid) not in free: return 409, {"error": "That slot was just taken. Please pick another time."}
        if not is_real(p["phone"]): return 409, {"error": "Bookings are paused while the Sova team is testing. Please message Sova to book."}
        if a == "resched":
            ok, msg = reschedule(t, day, sid, free[int(sid)], by)
        else:
            age, gender = str(f.get("age") or "").strip(), str(f.get("gender") or "").lower()
            if not age.isdigit() or not AGE_MIN <= int(age) <= AGE_MAX or gender not in ("male", "female"):
                return 400, {"error": "Please enter an age between %d and %d, and pick a gender." % (AGE_MIN, AGE_MAX)}
            prof.update(age=int(age), gender=gender, landmark=str(f.get("landmark") or prof.get("landmark") or "").strip()[:150])
            ok, msg = book(t, prof, day, sid, free[int(sid)], by)
            if ok: log(program_id=pid, test_no=1, kind="profile", by_user=by, detail={"age": prof["age"], "gender": gender, "landmark": prof["landmark"]})
        t2, _, _ = _purchase_test(pid)
        return (200 if ok else 409), {"ok": ok, "message": msg, **_purchase_view(t2 or t)}
    return 400, {"error": "unknown action"}


# ---------- WhatsApp OTP to the number on the order: proves it's the customer before booking (a forwarded link alone can't book) ----------
OTP_MIN, VERIFIED_MIN, _otp_sends, _otp_tries = 10, 30, {}, {}


def _ssig(kind, payload): return hmac.new((E("TOKEN", "\0") + "|" + kind).encode(), payload.encode(), hashlib.sha256).hexdigest()


def seal(kind, payload, secret=""):
    """Signed cookie value; payload ends with |<expiry unix time>. An OTP cookie is signed with the code too, so it never holds the code."""
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + _ssig(kind, payload + "|" + secret)


def peek(value):
    try: b = (value or "").split(".", 1)[0]; return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode().split("|")
    except (ValueError, UnicodeDecodeError): return []


def unseal(kind, value, secret=""):
    parts = peek(value)
    try: ok = hmac.compare_digest(value.split(".", 1)[1], _ssig(kind, "|".join(parts) + "|" + secret)) and int(parts[-1]) > time.time()
    except (AttributeError, IndexError, ValueError): ok = False
    return parts if ok else None


def verified(request, phone): return (unseal("cv", request.cookies.get("cv")) or [""])[0] == phone != ""


def cookie(name, value, minutes):
    return ("Set-Cookie", "%s=%s; Path=/; Max-Age=%d; HttpOnly; Secure; SameSite=Lax" % (name, value, minutes * 60))


def verify_page(token, request):
    """Step 1: send a code to the WhatsApp number on the order (never another number). Step 2: enter it. -> (html or redirect, status, [headers])"""
    ids = untok(token)
    progs, logs = load(ids[0]) if ids else ([], {})
    if not progs: return ui.message_page("This link isn't valid", "Please use the latest link from Sova's WhatsApp message."), 404, []
    p, f, msg, hd = progs[0], request.form, "", []
    pending = peek(request.cookies.get("otp"))
    step = "code" if len(pending) == 3 and pending[0] == p["program_id"] and pending[2].isdigit() and int(pending[2]) > time.time() else "send"
    act = f.get("act") if request.method == "POST" else ""
    if act == "send":
        ph = p["phone"]
        recent = [x for x in _otp_sends.get(ph, []) if time.time() - x < 900]
        if not ph: msg = "There's no WhatsApp number on this order. Please message Sova and we'll help you book."
        elif not is_real(ph): msg = "Bookings are paused while the Sova team is testing. Please message Sova to book."
        elif len(recent) >= 3: msg = "We've sent 3 codes in the last 15 minutes. Please wait a little and try again."
        else:
            code = "%06d" % secrets.randbelow(10 ** 6)
            ok, r = send_otp(ph, code)
            if not ok: msg = "We couldn't send the code on WhatsApp just now. Please try again in a minute, or message Sova."
            else:
                _otp_sends[ph] = recent + [time.time()]
                step, hd = "code", [cookie("otp", seal("otp", "%s|%s|%d" % (p["program_id"], ph, time.time() + OTP_MIN * 60), code), OTP_MIN)]
                msg = "We've sent a 6-digit code to your WhatsApp."
    elif act == "check" and step == "code":
        c = request.cookies.get("otp")
        if len(_otp_tries) > 5000: _otp_tries.clear() # per-instance memory; a code still expires in 10 minutes
        tries = _otp_tries[c] = _otp_tries.get(c, 0) + 1
        if unseal("otp", c, re.sub(r"\D", "", f.get("code") or "")) and tries <= 5:
            log(program_id=p["program_id"], kind="verified", note="WhatsApp OTP", by_user="customer")
            return "../b/" + token, 303, [
                cookie("otp", "", 0), cookie("cv", seal("cv", "%s|%d" % (p["phone"], time.time() + VERIFIED_MIN * 60)), VERIFIED_MIN)]
        if tries >= 5: step, hd, msg = "send", [cookie("otp", "", 0)], "Too many wrong codes. Please ask for a new one."
        else: msg = "That code isn't right. Please check the latest code on WhatsApp."
    return ui.verify(step, msg, "+91 \u2022\u2022\u2022\u2022\u2022\u2022" + (p["phone"] or "")[-4:]), 200, hd


def confirm_page(token):
    """Template 2's Yes link: a thank-you on WhatsApp, once per booking, and a short page. No personal details on the page."""
    ids = untok(token)
    progs, logs = load(ids[0]) if ids else ([], {})
    tl = [x for x in tests_for(progs[0]) if x[0] == ids[1]] if progs else []
    if not tl: return ui.message_page("This link isn't valid", "Please use the latest message from Sova on WhatsApp."), 404
    t = test_state(progs[0], *tl[0], logs.get(ids[0], []))
    if t["status"] != "booked": return ui.message_page("This booking has changed", "Please check your latest WhatsApp message from Sova."), 200
    if not t["confirmed"]:
        log(program_id=ids[0], test_no=t["n"], kind="reply", note="yes", status="Yes", booking_id=t["booking"]["booking_id"], by_user="customer")
        send(t, "thanks", "customer")
    return ui.message_page("Thank you!", "Your home blood sample collection is confirmed. We've sent the details on WhatsApp. You can close this page.", ok=True), 200


def booking_page(token, request, by="customer"):
    """Customer: OTP first, then (new booking) confirm the address -> Redcliffe coverage check -> day, time, age, gender -> book;
    (booked) the current booking -> a new day and time. by = a staff email when ops book on the customer's behalf (?ops=1)."""
    ids = untok(token)
    if not ids: return ui.message_page("This link isn't valid", "Please use the latest link from Sova's WhatsApp message."), 404
    progs, logs = load(ids[0])
    if not progs: return ui.message_page("Booking not found", "Please message Sova and we'll help you book."), 404
    p, L = progs[0], logs.get(ids[0], [])
    if by == "customer" and request.method == "GET" and not request.args and not any(
            r["kind"] == "click" and r.get("test_no") == ids[1] and now() - r["at"] < dt.timedelta(minutes=30) for r in L):
        log(program_id=p["program_id"], test_no=ids[1], kind="click", note="booking link", by_user="customer") # opened from WhatsApp (once per 30 min)
    if by == "customer" and not verified(request, p["phone"]): return "../v/" + token, 303 # number + WhatsApp OTP first
    tl = [x for x in tests_for(p) if x[0] == ids[1]]
    if not tl: return ui.message_page("Booking not found", "Please message Sova and we'll help you book."), 404
    t = test_state(p, *tl[0], L)
    prof = profile(p, L)
    f, msg = request.form, ""
    staff = "" if by == "customer" else by
    q = "ops=1&" if staff else "" # keeps ops on the ops page across day links and redirects
    to_profile = lambda m: ("../p?id=%s&m=%s" % (urllib.parse.quote(p["program_id"]), urllib.parse.quote(m)), 303)
    mode = "resched" if t["status"] == "booked" else "book" if t["status"] in BOOKABLE else "done"
    checked = next((r for r in reversed(L) if r["kind"] == "profile" and "serviceable" in jd(r)), None) # last address confirmation
    step = "slots" if mode != "book" else "address" if request.args.get("step") == "address" or not checked or \
        now() - checked["at"] > dt.timedelta(hours=2) else "slots" if jd(checked)["serviceable"] else "blocked"
    if not staff and mode == "resched" and request.method == "GET" and not t["ask"]: # shows in "Customer asked" until the booking moves
        log(program_id=p["program_id"], test_no=t["n"], kind="reply", note="resched", status="Reschedule", booking_id=t["booking"]["booking_id"], by_user="customer")
    if request.method == "POST":
        act = f.get("act")
        if act == "address" and mode == "book":
            new = {k: (f.get(k) or "").strip()[:150] for k in ("house", "area", "city", "pincode", "landmark")}
            new["address"] = ", ".join(x for x in (new["house"], new["area"]) if x) # what Redcliffe gets as the customer address
            dev = device_point(f.get("lat"), f.get("lng")) # the map pin (or the phone's location when the map can't load)
            how = f.get("how") if f.get("how") in ("gps", "pin", "search", "address") else "pin"
            if not re.fullmatch(r"\d{6}", new["pincode"]) or not new["house"]: msg = "Please enter your flat / house no. and a 6-digit pincode."
            else:
                same = prof.get("lat") and prof.get("lng") and all(new[k] == str(prof.get(k) or "").strip() for k in ("address", "pincode"))
                g = dev or (same and (float(prof["lat"]), float(prof["lng"]))) or geocode("%s %s" % (new["city"], new["pincode"])) or geocode(new["pincode"])
                ok, why = serviceable(*g) if g else (None, "")
                if not g or ok is False and "latitude" in why.lower(): # Redcliffe's "cannot fetch latitude and longitude" = a bad point, not coverage
                    msg = "We couldn't find this address on the map. Please check the pincode, or tap Use my current location."
                elif ok is None: msg = "We couldn't check this address with Redcliffe just now. Please try again in a minute."
                else:
                    log(program_id=p["program_id"], test_no=t["n"], kind="profile", by_user=by,
                        detail={**new, "lat": g[0], "lng": g[1], "serviceable": ok, "located_by": how if dev else "saved" if same else "pincode", "redcliffe": why})
                    return "./" + token + ("?ops=1" if staff else ""), 303
            step = "address"
        elif act in ("book", "resched"):
            day, sid = f.get("d", ""), (f.get("slot") or "").split("|", 1)[0]
            free = dict(slots(prof["lat"], prof["lng"], day)[0]) if day in booking_dates(t) and prof.get("lat") and sid.isdigit() and step == "slots" else {}
            if act == "book" and step != "slots": msg = "Please confirm your address first."
            elif mode != act or not free: msg = "Please pick a date and a time slot again."
            elif int(sid) not in free: msg = "That slot was just taken. Please pick another time."
            elif not p["phone"]: msg = "We need your mobile number to book. Please message Sova."
            elif not is_real(p["phone"]): msg = "Bookings are paused while the Sova team is testing. Please message Sova to book."
            elif act == "resched":
                ok, msg = reschedule(t, day, sid, free[int(sid)], by)
                if ok and staff: return to_profile(msg)
                if ok: return ui.message_page("Rescheduled", msg + " We've sent the new time on WhatsApp.", ok=True), 200
            else:
                age, gender = (f.get("age") or "").strip(), (f.get("gender") or "").lower()
                if not age.isdigit() or not AGE_MIN <= int(age) <= AGE_MAX or gender not in ("male", "female"):
                    msg = "Please enter an age between %d and %d, and pick a gender." % (AGE_MIN, AGE_MAX) if age.isdigit() else "Please fill in age and gender."
                else:
                    prof.update(age=int(age), gender=gender, landmark=(f.get("landmark") or prof.get("landmark") or "").strip()[:150])
                    ok, msg = book(t, prof, day, sid, free[int(sid)], by)
                    if ok:
                        log(program_id=p["program_id"], test_no=t["n"], kind="profile", by_user=by,
                            detail={"age": prof["age"], "gender": gender, "landmark": prof["landmark"]})
                        if staff: return to_profile(msg)
                        return ui.message_page("You're booked", msg + " We've sent the details on WhatsApp. Please keep 12 hours of fasting before the visit "
                                                                     "(water is fine).", ok=True), 200
    day = request.args.get("d") if request.args.get("d") in booking_dates(t) else (f.get("d") if f.get("d") in booking_dates(t) else "")
    sl, why = slots(prof["lat"], prof["lng"], day) if day and prof.get("lat") and step == "slots" and mode != "done" else ([], "")
    return ui.booking(t, prof, mode, booking_dates(t), day, sl, why, msg or request.args.get("m", ""), MAX_RESCHEDULES, staff, step,
                      {"phone": setting("contact_phone"), "email": setting("contact_email")}, q, E("MAPS_KEY", "")), 200


# ---------- dashboard accounts: the SovaX adherence desk's logins (table sova.dashboard_users + the two setup logins) ----------
_acc = {"at": 0, "rows": {}}


def hash_pw(pw, salt):
    return "pbkdf2$%s$%s" % (salt, hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex())


def check_pw(pw, stored):
    try: return hmac.compare_digest(hash_pw(pw, stored.split("$")[1]), stored)
    except (IndexError, ValueError): return False


def accounts(refresh=False):
    if refresh or time.time() - _acc["at"] > 60:
        try:
            rows = q("SELECT x.* FROM (SELECT ARRAY_AGG(t ORDER BY `at` DESC LIMIT 1)[OFFSET(0)] x FROM `%s` t GROUP BY email)" % ACCOUNTS)
            _acc.update(at=time.time(), rows={r["email"]: r for r in rows})
        except Exception as e: print("accounts read failed", e)
    return _acc["rows"]


def check_account_form(email, pw=None, role=None):
    """Same rules as the SovaX desk (the two dashboards share these logins)."""
    if not re.fullmatch(r"[a-z0-9._%+-]+@sova\.health", email or ""): return "Use a work email ending in @sova.health."
    if pw is not None and (len(pw) < 8 or pw.lower() == email or pw.lower() in ("password", "12345678")): return "Password needs at least 8 characters and must not be the email."
    if role is not None and role not in ("admin", "view"): return "Pick Admin or View."
    return None


def save_account(email, role, pw_hash, active, by, action):
    q("INSERT `%s` (email, role, pw_hash, active, `at`, by_user, action) VALUES (@e, @r, @h, @a = 'true', CURRENT_TIMESTAMP(), @b, @x)" % ACCOUNTS,
      e=email, r=role, h=pw_hash, a="true" if active else "false", b=by, x=action)
    accounts(refresh=True)


def setup_logins():
    logins = {**dict(l.split(":", 1) for l in E("DASH_USERS", "").splitlines() if ":" in l), "sova": E("DASH_PASSWORD", "")}
    return {u: pw for u, pw in logins.items() if pw.strip()} # an empty or missing password never logs anyone in


def check_login(user, pw):
    user = (user or "").strip()
    acc = accounts().get(user.lower())
    if acc and acc["active"] and check_pw(pw, acc["pw_hash"]):
        return {"email": acc["email"], "role": acc["role"], "fp": acc["pw_hash"][-16:]}
    logins = setup_logins()
    if user in logins and hmac.compare_digest(pw.encode(), logins[user].strip().encode()):
        return {"email": user, "role": "admin", "fp": hashlib.sha256(logins[user].strip().encode()).hexdigest()[:16]}
    return None


SESSION_HOURS, _fails = 12, {}


def _sig(payload): return hmac.new((E("TOKEN", "\0") + "|session").encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(who):
    payload = "%s|%d|%s" % (who["email"], int(time.time()) + SESSION_HOURS * 3600, who["fp"])
    return base64.urlsafe_b64encode(payload.encode()).decode() + "." + _sig(payload)


def from_session(cookie):
    try:
        b, sig = (cookie or "").split(".", 1)
        payload = base64.urlsafe_b64decode(b.encode()).decode()
        email, exp, fp = payload.rsplit("|", 2)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sig(payload)) or int(exp) < time.time(): return None
    acc = accounts().get(email)
    if acc: return {"email": email, "role": acc["role"], "fp": fp} if acc["active"] and acc["pw_hash"][-16:] == fp else None
    lg = setup_logins()
    return {"email": email, "role": "admin", "fp": fp} if email in lg and hashlib.sha256(lg[email].strip().encode()).hexdigest()[:16] == fp else None


def too_many_fails(ip):
    _fails[ip] = [x for x in _fails.get(ip, []) if time.time() - x < 600]
    return len(_fails[ip]) >= 5


# ---------- dashboard actions ----------
def next_stage(t, by):
    """Test profiles only: what Redcliffe would do next, so the whole journey can be tried in minutes. Each step sends its WhatsApp."""
    p, n, b = t["program"], t["n"], t["booking"]
    if p.get("source") != "test": return "Only test profiles can be moved on by hand."
    if t["status"] in ("booked", "check"):
        log(program_id=p["program_id"], test_no=n, kind="rcl", status="Sample collected (test)", booking_id=b["booking_id"], by_user=by, detail={"test": True})
        stage, which = "Moved to sample collected", "collected"
    elif t["status"] == "collected":
        log(program_id=p["program_id"], test_no=n, kind="rcl", status="Report ready (test)", booking_id=b["booking_id"], by_user=by,
            detail={"report_link": SAMPLE, "test": True})
        keep(t, SAMPLE, "test (sample report)", by)
        stage, which = "Moved to report ready", "report"
    elif t["status"] == "report" and n == 1 and len(tests_for(p)) > 1 and tests_for(p)[1][1] > today(): # Test 2 not open yet
        log(program_id=p["program_id"], test_no=2, kind="shift", note="Test 2 window opened early (test)", by_user=by,
            detail={"days": (tests_for(p)[1][1] - today()).days})
        return "Test 2 is open now: send its booking WhatsApp from the Test 2 card."
    else: return "Nothing to move yet: the customer books with the WhatsApp link first (or use Book for customer)."
    progs, logs = load(p["program_id"])
    t2 = next(x for x in rows_for(progs, logs) if x["n"] == n)
    res = send(t2, which, by)
    return "%s. %s" % (stage, {"sent": "WhatsApp sent.", "planned": "WhatsApp logged as planned (not a test number).", "held": "WhatsApp held: template not approved yet.",
                               "failed": "WhatsApp failed: see the timeline.", "skipped": "WhatsApp already sent."}[res])


def act(f, who, base=""):
    """One dashboard form -> (message, back)."""
    a, back = f.get("a", ""), f.get("back") or "./"
    if who["role"] != "admin": return "View-only login: ask an admin to do this.", back
    if a in ("send", "followup", "refresh", "cancel", "next_stage"):
        progs, logs = load(f["pid"]) if f.get("pid") else ([], {}) # no id: nothing, never "every program"
        rows = [t for t in rows_for(progs, logs) if str(t["n"]) == f.get("n")]
        if not rows: return "Program not found.", back
        t = rows[0]
        if a == "cancel":
            reason = (f.get("reason") or "").strip()[:200]
            if t["status"] not in ("booked", "check"): return "There's no active booking to cancel.", back
            if not reason: return "Pick a reason to cancel.", back
            return cancel(t, reason, who["email"])[1], back
        if a == "refresh":
            if not t["booking"]: return "No booking to check yet.", back
            changed, st = refresh_status(t)
            rep_ = consolidated_report(t["booking"]["booking_id"]) if not t["report"] else None # the same check as the 11/13/17 checks
            if rep_:
                log(program_id=t["program"]["program_id"], test_no=t["n"], kind="rcl", status="Report ready", booking_id=t["booking"]["booking_id"],
                    by_user="redcliffe", detail={"report_link": rep_, "source": "consolidated report"})
                keep(t, rep_, "consolidated report", who["email"])
            return "Redcliffe says: %s%s%s" % (st or "no status yet", "" if changed else " (no change)",
                                               "; report ready (it goes out with the next run, or press Send)" if rep_ else "; no report yet"), back
        if a == "next_stage":
            return next_stage(t, who["email"]), back
        if a == "followup":
            note = (f.get("note") or "").strip()[:500]
            if not note: return "Write what happened on the follow-up.", back
            log(program_id=t["program"]["program_id"], test_no=t["n"], kind="followup", note=note, by_user=who["email"])
            return "Follow-up saved.", back
        if not t["program"]["phone"]: return "No WhatsApp number on this order.", back
        which = "t2" if t["status"] == "booked" else first_msg(t)
        if f.get("which") == "nudge" and t["status"] in BOOKABLE: which = "nudge" # the profile's follow-up dropdown: a booking reminder
        if which != "t2" and t["status"] not in BOOKABLE: return "Nothing to re-send for this test.", back
        day_before = dt.date.fromisoformat(t["booking"]["collection_date"]) - dt.timedelta(days=1) if which == "t2" else None
        if day_before and day_before != today(): # Template 2 says "tomorrow"
            return "The reminder says \"tomorrow\", so it can only go out on %s (the hourly run sends it then by itself)." % fmt_day(day_before), back
        just = [r["at"] for r in logs.get(t["program"]["program_id"], []) if r["kind"] == "msg" and r.get("test_no") == t["n"] and r.get("note") == which
                and r["status"] == "sent" and now() - r["at"] < dt.timedelta(minutes=10)]
        if just: return "Already sent at %s. To send it again, wait 10 minutes." % just[-1].astimezone(IST).strftime("%H:%M"), back # a double click
        res = send(t, which, who["email"])
        return {"sent": "WhatsApp sent.", "planned": "Logged as planned: send mode is %s (or not a test number)." % send_mode(),
                "held": "Nothing sent: template %s isn't in Briza yet (or Meta hasn't approved it)." % setting(which + "_template"),
                "failed": "Briza did not send it; see the timeline."}[res], back
    if a == "cfg":
        k, v = f.get("k", ""), (f.get("v") or "").strip()
        if k == "send_mode" and v not in ("off", "test", "live"): return "Pick off, test or live.", back
        if k not in ("send_mode", "test_numbers", "package_codes", "contact_phone", "contact_email", "rcl_env", "rcl_prod_host", "prod_package_codes",
                     "report_link_days", "quiet_minutes", "live_from", "t1_delay_days", "briza_chat_url", *DEFAULT_T): return "Unknown setting.", back
        if k == "t1_delay_days" and not (v.isdigit() and int(v) <= 7): return "Enter 0 to 7 days.", back
        if k == "live_from":
            try: dt.date.fromisoformat(v)
            except ValueError: return "Enter the date like 2026-11-20.", back
        if k == "send_mode" and v == "live" and send_mode() != "live": set_cfg("live_from", today().isoformat(), who["email"]) # "new orders" start now
        if k in ("report_link_days", "quiet_minutes") and not (v.isdigit() and int(v) <= 365): return "Enter a number of %s." % ("days" if k[0] == "r" else "minutes"), back
        if k == "rcl_prod_host" and v and not re.fullmatch(r"https://[a-z0-9.-]+\.[a-z]{2,}/?", v): return "Enter the host like https://api.redcliffelabs.com.", back
        if k == "rcl_env":
            if v not in ("staging", "prod"): return "Pick staging or production.", back
            if v == "prod" and not (secret(PROD_KEY) and setting("prod_package_codes")):
                return "Save the production API key and package code first.", back
        if k == "test_numbers": v = ",".join(sorted({norm_phone(x) for x in v.split(",") if norm_phone(x)}))
        if k == "contact_phone" and v and not 10 <= len(re.sub(r"\D", "", v)) <= 12: return "Enter the support number with 10 digits (or 91 and 10 digits).", back
        if k == "briza_chat_url" and v and not re.fullmatch(r"https://[^\s'\"<>]+", v): return "Enter the Briza chat address starting with https:// (put {phone} where the customer's number goes).", back
        set_cfg(k, v, who["email"])
        return "Saved.", back
    if a == "secret":
        name = {"rcl_prod_key": PROD_KEY}.get(f.get("k", ""))
        v = (f.get("v") or "").strip()
        if not name: return "Unknown setting.", back
        if not 8 <= len(v) <= 400 or re.search(r"\s", v): return "That doesn't look like a key. Paste it again.", back
        if not set_secret(name, v): return "Couldn't save it in Secret Manager. Nothing changed.", back
        set_cfg(f["k"] + "_saved", "%s by %s" % (now().astimezone(IST).strftime("%d %b %Y, %H:%M"), who["email"]), who["email"])
        return "Saved securely. It's used within 5 minutes.", back
    if a == "shop_token":
        v = (f.get("v") or "").strip()
        if not re.fullmatch(r"shp[a-z]{2}_[0-9a-f]{32}", v): return "That doesn't look like a Shopify Admin API access token (shpat_ / shpca_). Nothing saved.", back
        st, r = http("https://%s/admin/api/2026-10/graphql.json" % STORES["health"][1], {"query": "{shop{myshopifyDomain} orders(first:1){nodes{name}}}"},
                     {"X-Shopify-Access-Token": v})
        r = r if isinstance(r, dict) else {}
        if st != 200 or r.get("errors") or ((r.get("data") or {}).get("shop") or {}).get("myshopifyDomain") != STORES["health"][1]:
            return "Shopify didn't accept it for sova.health, or it can't read orders (%s %s). Nothing saved." % (st, str(r.get("errors") or "")[:150]), back
        if not set_secret(HEALTH_APP, json.dumps({"access_token": v})): return "Couldn't save it in Secret Manager. Nothing changed.", back
        _app.pop("health", None); _app_err.pop("health", None)
        set_cfg("shop_app_saved", "%s by %s" % (now().astimezone(IST).strftime("%d %b %Y, %H:%M"), who["email"]), who["email"])
        return "sova.health connected: Shopify confirmed it can read orders.", back
    if a == "bulk_send":
        picked = set(f.getlist("t") if hasattr(f, "getlist") else f.get("t") or [])
        if not picked: return "Tick the customers to message first.", back
        progs, logs = load()
        rows = [t for t in rows_for(progs, logs) if "%s|%d" % (t["program"]["program_id"], t["n"]) in picked]
        n = dict.fromkeys(("sent", "planned", "failed", "held", "skipped"), 0)
        cap = int(setting("max_sends_per_run") or 200)
        phones = set()
        for t in rows[:cap]:
            ph = t["program"]["phone"]
            if not ph or ph in phones or t["status"] not in BOOKABLE or t["t1_at"] and now() - t["t1_at"] < dt.timedelta(hours=24):
                n["skipped"] += 1; continue # booked by now, no number, one message per person per send, or messaged in the last 24 hours
            phones.add(ph)
            res = send(t, first_msg(t), who["email"])
            n[res] += 1
            if res == "sent": time.sleep(1.1) # Briza allows 60 a minute
        return ("Booking WhatsApp: %(sent)d sent, %(planned)d planned (test mode), %(failed)d failed, %(held)d held, %(skipped)d skipped "
                "(booked, no number, a second test for the same person, or messaged in the last 24 hours)" % n + ("; %d more ticked, send again for them." % (len(rows) - cap) if len(rows) > cap else ".")), back
    if a == "shop_app":
        store = f.get("store") if f.get("store") in STORES else "health"
        app = {"client_id": (f.get("cid") or "").strip(), "client_secret": (f.get("cs") or "").strip()}
        if not all(8 <= len(v) <= 200 and not re.search(r"\s", v) for v in app.values()): return "Paste the app's Client ID and Client secret.", back
        tok, ttl, info = app_grant(store, app) # info: the access Shopify granted, or why it refused
        if not tok: return "Shopify didn't accept that app for %s (%s). Nothing saved." % (STORES[store][0], info), back
        if not set_secret(APP_SECRET[store], json.dumps(app)): return "Couldn't save it in Secret Manager. Nothing changed.", back
        _app[store], _app_err[store] = (time.time() + max(ttl - 3600, 300), tok), ""
        _blocked.pop(store, None)
        set_cfg("shop_app_saved" + ("" if store == "health" else "_" + store), "%s by %s" % (now().astimezone(IST).strftime("%d %b %Y, %H:%M"), who["email"]), who["email"])
        try: shopify("{shop{myshopifyDomain}}", None, store); reach = "Shopify answered from Google Cloud."
        except Exception as e: reach = "But the Admin API refused it from Google Cloud (%s): check the app's IP allowlist." % str(e)[:120]
        return "%s app saved. Access granted: %s. %s" % (STORES[store][0], info or "as set in the app", reach), back
    if a == "start_date":
        progs, logs = load(f["pid"]) if f.get("pid") else ([], {})
        if not progs: return "Program not found.", back
        p = progs[0]
        if is_custom(p) or p.get("source") == "linked": return "Only a program has start and end dates.", back
        try:
            sd = dt.date.fromisoformat(f["date"].strip()) if (f.get("date") or "").strip() else None
            ed = dt.date.fromisoformat(f["end"].strip()) if (f.get("end") or "").strip() else None
        except ValueError: return "Pick a date.", back
        if "end" not in f: ed = program_end({**p, "program_start": sd or p["purchased_at"].astimezone(IST).date(), "program_end": None}) # start only: end follows
        rows, err = date_rows(p, sd, ed, who["email"], "profile")
        if err: return err, back
        for r in rows: log(**{k: v for k, v in r.items() if k not in ("id", "at")})
        return dates_message(p["program_id"], bool(rows)), back
    if a == "shop_hooks":
        return "Order webhooks: %s." % register_hooks(base), back
    if a == "sku_map":
        sku, d = (f.get("sku") or "").strip().upper(), (f.get("days") or "").strip()
        old = sku_table().get(sku)
        if not old: return "Unknown SKU %s." % sku, back
        if not d.isdigit() or int(d) > 730: return "Enter the program length in days (0 = not a program), up to 730.", back
        set_sku(sku, old.get("name") or "", old.get("product_type") or "", int(d), old.get("note") or "", who["email"])
        if int(d) < MIN_DAYS: return "%s saved as %s: it doesn't get blood tests (programs of %d+ days do)." % (sku, "not a program" if d == "0" else d + " days", MIN_DAYS), back
        try: got = sync_orders(only=[sku])
        except Exception as e: got = "couldn't read the stores just now (%s); the hourly run picks them up" % str(e)[:120]
        return "%s mapped to %s days. Everyone who bought it: %s." % (sku, d, got), back
    if a == "add_user":
        progs, logs = load(f["link"]) if f.get("link") else ([], {})
        if not progs: return "Pick the buyer this person is linked to.", back
        b_ = progs[0]
        if b_.get("source") in ("linked", "custom"): return "Link to the buyer's own profile (the Shopify order), not a linked user.", back
        name = re.sub(r"\s+", " ", (f.get("name") or "").strip())[:60]
        rel = (f.get("relation") or "").strip().lower()
        rel = ((f.get("relation_other") or "").strip()[:30] or "other") if rel == "other" else rel
        if len(name) < 2: return "Enter the person's name.", back
        if rel not in RELATIONS and not (f.get("relation") == "other"): return "Pick the relation to the buyer.", back
        raw_ph = (f.get("phone") or "").strip()
        ph = norm_phone(raw_ph) if raw_ph else b_.get("phone") or ""
        if raw_ph and not ph: return "Enter a valid 10-digit Indian mobile number (or leave it empty to use the buyer's).", back
        age, gender = (f.get("age") or "").strip(), (f.get("gender") or "").strip().lower()
        if age and not (age.isdigit() and AGE_MIN <= int(age) <= AGE_MAX): return "Age should be a number from %d to %d." % (AGE_MIN, AGE_MAX), back
        pin = re.sub(r"\D", "", f.get("pincode") or "")[:6]
        pid = "%s-L%s" % (b_["program_id"].split("-")[0], ("%X" % time.time_ns())[-6:])
        row = dict(program_id=pid, order_name=b_["order_name"], source="linked", sku=b_["sku"], product=b_.get("product"), days=b_["days"],
                   purchased_at=b_["purchased_at"].isoformat(), customer_name=name, phone=ph, email=(f.get("email") or "").strip()[:80],
                   address=(f.get("address") or "").strip()[:150] or b_.get("address") or "", city=(f.get("city") or "").strip()[:60] or b_.get("city") or "",
                   state=b_.get("state") or "", pincode=pin or b_.get("pincode") or "", lat=None, lng=None, added_at=now().isoformat())
        bad = bq().insert_rows_json(PROGRAMS, [row])
        if bad: return "Could not add them: %s" % str(bad)[:200], back
        log(program_id=pid, kind="profile", by_user=who["email"], detail={"linked_to": b_["program_id"], "relation": rel, "notes": (f.get("notes") or "").strip()[:300],
            **({"age": int(age)} if age else {}), **({"gender": gender} if gender in ("male", "female") else {}), "own_number": bool(raw_ph)})
        log(program_id=b_["program_id"], kind="linked", note="%s (%s) added as a linked user" % (name, rel), by_user=who["email"],
            detail={"pid": pid, "name": name, "relation": rel, "phone": ph})
        return ("%s added, linked to %s (%s, parent ID %s). The buyer's own blood tests stay as they are. Send %s a personalised test below; "
                "its WhatsApps go to %s." % (name, b_.get("customer_name") or b_["order_name"], rel, b_["program_id"], name.split(" ")[0],
                                             "their own number" if raw_ph else "the buyer's number (%s)" % ph), "p?id=" + urllib.parse.quote(pid) + "#other")
    if a == "custom_test":
        progs, logs = load(f["pid"]) if f.get("pid") else ([], {})
        if not progs: return "Program not found.", back
        p = progs[0]
        codes = [c.strip().upper() for c in re.split(r"[,\s]+", f.get("codes") or "") if c.strip()]
        if not codes or not all(re.fullmatch(r"[A-Z0-9_\-]{2,30}", c) for c in codes): return "Enter the Redcliffe package code(s), e.g. PL70 or PL70, CAMP123.", back
        if not p.get("phone"): return "No WhatsApp number on this order.", back
        name = re.sub(r"\s+", " ", (f.get("name") or "").strip())[:60] or "personalised blood test"
        pid = "%s-X%s" % (p["program_id"].split("-")[0], ("%X" % time.time_ns())[-6:])
        row = {k: p.get(k) for k in ("customer_name", "phone", "email", "address", "city", "state", "pincode", "lat", "lng")}
        bad = bq().insert_rows_json(PROGRAMS, [dict(row, program_id=pid, order_name="%s · personalised" % p["order_name"], source="custom", sku=CUSTOM, product=name,
                                                    purchased_at=now().isoformat(), days=30, added_at=now().isoformat())])
        if bad: return "Could not create it: %s" % str(bad)[:200], back
        prof = profile(p, logs.get(p["program_id"], []))
        log(program_id=pid, test_no=1, kind="profile", by_user=who["email"], detail={"package_codes": ",".join(codes), "made_from": p["program_id"],
            **{k: prof.get(k) for k in ("house", "area", "address", "landmark", "city", "pincode", "lat", "lng", "age", "gender") if prof.get(k) not in (None, "")}})
        np_, nl = load(pid)
        t = next((x for x in rows_for(np_, nl) if x["n"] == 1), None)
        res = send(t, "custom", who["email"]) if t else "failed"
        return ("Personalised test (%s: %s) created. WhatsApp %s." % (name, ", ".join(codes), {"sent": "sent", "planned": "logged as planned (test mode)",
                "held": "held: template bt_custom_test isn't approved yet; press Send on its page once it is", "failed": "failed: see its timeline",
                "skipped": "skipped"}[res]), "p?id=" + urllib.parse.quote(pid))
    if a in ("user_add", "user_reset", "user_off", "user_on"):
        email, pw, role = (f.get("email") or "").strip().lower(), f.get("password") or "", f.get("role")
        acc = accounts(refresh=True).get(email)
        if a == "user_add":
            bad = check_account_form(email, pw, role)
            if bad: return bad, back
            if acc and acc["active"]: return "%s already has a login: use Reset password." % email, back
            save_account(email, role, hash_pw(pw, os.urandom(16).hex()), True, who["email"], "created")
            return "Login created for %s. Share the password with them privately." % email, back
        if not acc: return "No login for %s." % email, back
        if email == who["email"] and a == "user_off": return "You can't switch off your own login.", back
        if a == "user_reset":
            bad = check_account_form(email, pw)
            if bad: return bad, back
            save_account(email, acc["role"], hash_pw(pw, os.urandom(16).hex()), acc["active"], who["email"], "password reset")
            return "Password reset for %s. Their old sessions are signed out." % email, back
        save_account(email, acc["role"], acc["pw_hash"], a == "user_on", who["email"], "reactivated" if a == "user_on" else "deactivated")
        return "%s %s." % (email, "can sign in again" if a == "user_on" else "can no longer sign in"), back
    if a == "sync_orders":
        try: return "Orders checked. %s." % sync_orders(), back
        except Exception as e: return "Couldn't read the stores just now: %s" % str(e)[:150], back
    if a == "sync_skus":
        try: n = sync_skus()
        except Exception as e: return "Couldn't read the stores just now: %s" % str(e)[:150], back
        return "SKUs checked in both stores: %d new or changed." % n, back
    if a == "test_program":
        ph, pin = norm_phone(f.get("phone")), re.sub(r"\D", "", f.get("pincode") or "")
        if not ph or len(pin) != 6 or not (f.get("address") or "").strip(): return "Test program needs a phone, address and 6-digit pincode.", back
        ago, days = int(f.get("ago") or 0), int(f.get("days") or 120)
        pid = "TEST-%s" % ("%X" % time.time_ns())[-6:]
        bad = bq().insert_rows_json(PROGRAMS, [dict(program_id=pid, order_name=pid, source="test", sku="TEST", product="Test program (%d days)" % days,
                                                    purchased_at=(now() - dt.timedelta(days=ago)).isoformat(), days=days,
                                                    customer_name=(f.get("name") or "Test Customer").strip()[:60], phone=ph, email="",
                                                    address=f.get("address").strip()[:150], city=(f.get("city") or "").strip()[:60], state="",
                                                    pincode=pin, lat=None, lng=None, added_at=now().isoformat())])
        if bad: return "Could not create it: %s" % str(bad)[:200], back
        if ph not in test_numbers(): set_cfg("test_numbers", ",".join(sorted(test_numbers() | {ph})), who["email"])
        return "Test program %s created (its number is now a test number). It gets Template 1 at the next hourly run, or press Send." % pid, "p?id=" + pid
    return "Unknown action.", back


def dashboard_data(legacy_rows=True):
    progs, logs = load()
    rows = rows_for(progs, logs)
    for t in rows: t["logs"] = logs.get(t["program"]["program_id"], [])
    if legacy_rows:
        try: rows += legacy_tests(progs)
        except Exception as e: print("legacy rows for the overview failed", e)
    return rows


def wa_writes(phones, since):
    """When these customers wrote to Sova on WhatsApp since `since`, on any topic (the SovaX desk logs every message) -> {last 10 digits: [times]}."""
    if not phones: return {}
    try:
        rows = q("SELECT RIGHT(REGEXP_REPLACE(IFNULL(phone, ''), r'[^0-9]', ''), 10) ph, `at` FROM `%s` WHERE kind IN ('inbound', 'response') AND channel = "
                 "'whatsapp' AND `at` >= TIMESTAMP(@s) AND RIGHT(REGEXP_REPLACE(IFNULL(phone, ''), r'[^0-9]', ''), 10) IN UNNEST(SPLIT(@ps, ','))"
                 % ADH_LOG, s=since.isoformat(), ps=",".join(sorted(phones)))
    except Exception as e: print("whatsapp replies:", e); return {}
    out = {}
    for r in rows: out.setdefault(r["ph"], []).append(r["at"])
    return out


STAGES = ("upcoming", "to_send", "awaiting", "followup", "booked", "check", "collected", "report", "cancelled", "missed", "closed", "handed", "untracked")
ACTS = ("click", "verified", "reply", "profile", "booked", "rescheduled", "report_view") # what a customer does after a message (by_user "customer")
BOOK_MSGS = ("t1", "custom", "nudge") # the messages that ask the customer to book
RELATIONS = ("father", "mother", "aunty", "uncle", "friend", "child", "spouse", "sibling", "grandparent", "other")


def linked_info(p, logs):
    """For the profile: whose Shopify order this linked user belongs to (+ relation), and the buyer's linked users."""
    prof = profile(p, logs)
    to, made_for = (prof.get("linked_to") if p.get("source") == "linked" else None), None
    if p.get("source") == "custom" and prof.get("made_from"): # a personalised test: whose profile it was sent from (and that person's parent)
        mp, ml = load(prof["made_from"])
        if mp:
            made_for = mp[0]
            if mp[0].get("source") == "linked":
                mprof = profile(mp[0], ml.get(mp[0]["program_id"], []))
                to, prof = mprof.get("linked_to"), {**prof, "relation": mprof.get("relation")}
    buyer = None
    if to:
        bp, _ = load(to)
        buyer = bp[0] if bp else {"program_id": to, "customer_name": to, "order_name": ""}
    users = [jd(r) for r in logs if r["kind"] == "linked"]
    return {"buyer": buyer, "relation": prof.get("relation"), "users": users, "notes": prof.get("notes") if p.get("source") == "linked" else None,
            "made_for": made_for, "moved": False} # buyers always keep their own tests (older 'handed' rows are ignored)


def find_buyers(qq):
    """Add user: the buyer search (name, phone, order, email) over Shopify orders (not linked users or personalised tests)."""
    qq = (qq or "").strip().lower()
    if len(qq) < 2: return []
    progs, _ = load()
    hit = [p for p in progs if p.get("source") not in ("linked", "custom") and
           qq in " ".join(str(p.get(k) or "") for k in ("customer_name", "phone", "order_name", "email")).lower()]
    return hit[:25]
FUNNEL = (("first", "First message (book your test)"), ("r1", "Reminder 1 (day 3)"), ("r2", "Reminder 2 (day 5)"), ("r3", "Reminder 3, the last (day 7)"))


def booking_funnel(progs, logs, since, until):
    """Each booking message sent between since and until -> (slot, plan, test no, message, clicked?, booked?). Slots: the first message,
    then reminders 1, 2 and 3 (the last) in the order they went. A click is the customer opening the booking link; a booking is one the
    customer made. Both count for the latest booking message before them, up to the next one."""
    out = []
    for p in progs:
        L = logs.get(p["program_id"], [])
        for n in sorted({r.get("test_no") for r in L if r["kind"] == "msg" and r.get("note") in BOOK_MSGS}):
            ms = [r for r in L if r["kind"] == "msg" and r.get("test_no") == n and r.get("note") in BOOK_MSGS and r["status"] in ("sent", "planned")]
            clicks = [r["at"] for r in L if r["kind"] == "click" and r.get("test_no") == n]
            books = [r["at"] for r in L if r["kind"] == "booked" and r.get("test_no") == n and r.get("by_user") == "customer"]
            k = 0
            for i, m in enumerate(ms):
                if m.get("note") == "nudge": k += 1
                slot = "first" if m.get("note") != "nudge" else "r%d" % min(k, 3)
                if not since <= m["at"] < until: continue
                nxt = ms[i + 1]["at"] if i + 1 < len(ms) else m["at"] + dt.timedelta(days=30)
                out.append((slot, p, n, m, any(m["at"] < c <= nxt for c in clicks), any(m["at"] < c <= nxt for c in books)))
    return out


def period(args):
    """?from=YYYY-MM-DD&to=YYYY-MM-DD (IST days, both included), or ?days=7/30/90/0 -> (start date, end date, days or None)."""
    try:
        a, b = dt.date.fromisoformat(args.get("from") or ""), dt.date.fromisoformat(args.get("to") or "")
        return (min(a, b), max(a, b), None)
    except ValueError: pass
    d = args.get("days", "30")
    d = int(d) if d in ("7", "30", "90", "0") else 30
    return (today() - dt.timedelta(days=(d or 3650) - 1), today(), d)


def analytics_data(start=None, end=None, days=None):
    """The Analytics page: where every test is now, and what happened between start and end (IST days): messages and how customers
    responded within 48 hours (a link opened / used, or a WhatsApp reply), bookings, reschedules, reports, and the 11/13/17 checks."""
    if isinstance(start, int): start, days = None, start # analytics_data(30): the last 30 days
    if start is None: start, end, days = period({"days": str(days if days is not None else 30)})
    progs, logs = load()
    rows = rows_for(progs, logs)
    since = dt.datetime.combine(start, dt.time(), IST)
    until = dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), IST)
    phone = {m: (p.get("phone") or "")[-10:] for p in progs for m in p["members"]}
    ev = [r for p in progs for r in logs.get(p["program_id"], []) if since <= r["at"] < until] # each plan once, refills included
    msgs = [r for r in ev if r["kind"] == "msg"]
    wrote = wa_writes({phone[r["program_id"]] for r in msgs if r["status"] == "sent" and phone[r["program_id"]]}, since)
    acts = {pid: [r for r in L if r["kind"] in ACTS and r.get("by_user") == "customer"] for pid, L in logs.items()}
    win, resp = dt.timedelta(hours=48), {}
    for m in msgs:
        k = resp.setdefault(m.get("note") or "?", dict.fromkeys(("sent", "planned", "failed", "held", "acted", "wrote", "any", "yes", "resched"), 0))
        k[m["status"]] = k.get(m["status"], 0) + 1
        if m["status"] != "sent": continue
        after = [a for a in acts.get(m["program_id"], []) if m["at"] < a["at"] <= m["at"] + win]
        w = any(m["at"] < t <= m["at"] + win for t in wrote.get(phone[m["program_id"]], []))
        k["acted"], k["wrote"], k["any"] = k["acted"] + bool(after), k["wrote"] + w, k["any"] + (bool(after) or w)
        k["yes"] += any(a["kind"] == "reply" and a.get("note") == "yes" for a in after)
        k["resched"] += any(a["kind"] == "rescheduled" or a["kind"] == "reply" and a.get("note") == "resched" for a in after)
    stages = {n: dict.fromkeys(STAGES, 0) for n in (1, 2)}
    for t in rows: stages[t["n"]][t["status"]] += 1
    stores = {}
    for p in progs: stores[(p.get("source") or "?").split(" ")[0]] = stores.get((p.get("source") or "?").split(" ")[0], 0) + 1
    moves = {k: {"customer": 0, "team": 0} for k in ("booked", "rescheduled", "cancelled")}
    for r in ev:
        if r["kind"] in moves: moves[r["kind"]]["customer" if r.get("by_user") == "customer" else "team"] += 1
    held = [t for t in rows if t["status"] in ("booked", "check", "collected", "report") and t["booking"]]
    used = {i: sum(min(t["resched"], MAX_RESCHEDULES) == i for t in held) for i in range(MAX_RESCHEDULES + 1)}
    views = [r for r in ev if r["kind"] == "report_view"]
    is_msg = lambda r, note: r["kind"] == "msg" and r.get("note") == note and r["status"] in ("sent", "planned")
    daily, by_day = [], {}
    for r in ev: by_day.setdefault(r["at"].astimezone(IST).date(), []).append(r)
    for i in range(min((end - start).days + 1, 31)):
        d = end - dt.timedelta(days=i)
        de = by_day.get(d, [])
        daily.append((d, sum(r["kind"] == "booked" for r in de), sum(r["kind"] == "rescheduled" for r in de),
                      sum(r["kind"] == "reply" and r.get("note") == "yes" for r in de), sum(is_msg(r, "collected") for r in de),
                      sum(is_msg(r, "report") for r in de), sum(r["kind"] == "report_view" for r in de)))
    checks = [(r["at"], jd(r)) for r in sorted(logs.get(CHECKS, []), key=lambda r: r["at"])[-10:]][::-1]
    funnel = {k: {"sent": 0, "clicked": 0, "booked": 0} for k, _ in FUNNEL}
    for slot, _, _, m, c, b in booking_funnel(progs, logs, since, until):
        if m["status"] != "sent": continue
        funnel[slot]["sent"] += 1; funnel[slot]["clicked"] += c; funnel[slot]["booked"] += b
    return dict(days=days, start=start, end=end, tests=len(rows), programs=len(progs), stores=stores, funnel=funnel, funnel_labels=FUNNEL, stages=stages, order=STAGES, resp=resp, moves=moves, used=used, max_r=MAX_RESCHEDULES,
                at_limit=sum(t["status"] == "booked" and t["resched"] >= MAX_RESCHEDULES for t in rows), daily=daily, checks=checks,
                views={"open": sum(r.get("note") == "open" for r in views), "download": sum(r.get("note") == "download" for r in views),
                       "tests": len({(r["program_id"], r.get("test_no")) for r in views})})


EXPORT_COLS = ["customer", "phone", "email", "order", "store", "program", "sku", "purchased", "test", "status", "opens", "due", "booking_id",
               "collection_date", "slot", "reschedules", "redcliffe_status", "report_link", "event", "event_at", "event_by"]


def analytics_export(kind, key, start, end):
    """One insight's customers as CSV rows (dicts with EXPORT_COLS). kind: stage (key '1:booked'), msg (key 'nudge:any' / 'report:sent' ...),
    move (booked / rescheduled / cancelled), report (ready / sent / viewed), all (every test). Events are those between start and end."""
    progs, logs = load()
    rows = rows_for(progs, logs)
    since, until = dt.datetime.combine(start, dt.time(), IST), dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), IST)
    by_test = {(t["program"]["program_id"], t["n"]): t for t in rows}

    def line(t, ev=None):
        p, b = t["program"], t["booking"] or {}
        return {"customer": p.get("customer_name"), "phone": p.get("phone"), "email": p.get("email"), "order": p.get("order_name"),
                "store": (p.get("source") or "").split(" ")[0], "program": p.get("product"), "sku": p.get("sku"),
                "purchased": p["purchased_at"].astimezone(IST).date().isoformat(), "test": t["n"], "status": t["status"], "opens": t["opens"].isoformat(),
                "due": t["due"].isoformat(), "booking_id": b.get("booking_id", ""), "collection_date": b.get("collection_date", ""), "slot": b.get("slot", ""),
                "reschedules": t["resched"], "redcliffe_status": t["rcl_status"], "report_link": t["report"] or "",
                "event": (ev or {}).get("kind", "") + (":" + ev["note"] if ev and ev.get("note") else ""),
                "event_at": ev["at"].astimezone(IST).strftime("%Y-%m-%d %H:%M") if ev else "", "event_by": (ev or {}).get("by_user", "")}

    events = [r for p in progs for r in logs.get(p["program_id"], []) if since <= r["at"] < until]
    find = lambda r: by_test.get((next((p["program_id"] for p in progs if r["program_id"] in p["members"]), r["program_id"]), r.get("test_no") or 1))
    out = []
    if kind == "stage":
        n, st = (key.split(":", 1) + [""])[:2]
        out = [line(t) for t in rows if str(t["n"]) == n and (t["status"] == st or not st)]
    elif kind == "msg":
        note, metric = (key.split(":", 1) + ["sent"])[:2]
        acts = {pid: [r for r in L if r["kind"] in ACTS and r.get("by_user") == "customer"] for pid, L in logs.items()}
        for m in (r for r in events if r["kind"] == "msg" and r.get("note") == note):
            responded = any(m["at"] < a["at"] <= m["at"] + dt.timedelta(hours=48) for a in acts.get(m["program_id"], []))
            if metric in ("sent", "planned", "failed", "held") and m["status"] != metric: continue
            if metric in ("any", "none") and (m["status"] != "sent" or responded != (metric == "any")): continue
            t = find(m)
            if t: out.append(line(t, m))
    elif kind == "funnel": # key "r1:clicked" / "first:booked" / "r3:sent"
        slot, metric = (key.split(":", 1) + ["sent"])[:2]
        for s_, p, n, m, c, b in booking_funnel(progs, logs, since, until):
            if s_ != slot or m["status"] != "sent" or metric == "clicked" and not c or metric == "booked" and not b: continue
            t = by_test.get((p["program_id"], n))
            if t: out.append(line(t, m))
    elif kind == "move":
        out = [line(t, r) for r in events if r["kind"] == key for t in [find(r)] if t]
    elif kind == "report":
        if key == "ready": out = [line(t) for t in rows if t["status"] == "report"]
        else: out = [line(t, r) for r in events if (r["kind"] == "report_view" if key == "viewed" else r["kind"] == "msg" and r.get("note") == "report"
                                                    and r["status"] in ("sent", "planned")) for t in [find(r)] if t]
    else: out = [line(t) for t in rows]
    return out


def results_rows(progs, logs, results):
    """Test results for export: one row per biomarker (or one per test without any), Test 1 and Test 2, with the report link. Programs bought
    before go-live show Test 1 as 'test taken before tracking, data expired'."""
    out, by = [], {}
    for r in results: by.setdefault((r["program_id"], int(r.get("test_no") or 1)), []).append(r)
    for p in progs:
        base = {"customer": p.get("customer_name"), "phone": p.get("phone"), "email": p.get("email"), "order": p.get("order_name"), "program": p.get("product"),
                "sku": p.get("sku"), "purchased": p["purchased_at"].astimezone(IST).date().isoformat()}
        tests = [n for n, *_ in tests_for(p)]
        if legacy(p): out.append(dict(base, test=1, status="test taken before tracking; data expired (no Redcliffe data)"))
        for n in tests:
            rs = sorted((x for m in p["members"] for x in by.get((m, n), [])), key=lambda x: str(x["at"]), reverse=True)
            if not rs:
                t = next((x for x in rows_for([p], logs) if x["n"] == n), None)
                out.append(dict(base, test=n, status=STATUS_TEXT.get(t["status"], t["status"]) if t else "", report_link=(t or {}).get("report") or ""))
                continue
            r = rs[0]
            bio = json.loads(r.get("biomarkers") or "[]") or [{}]
            for x in bio:
                out.append(dict(base, test=n, status=r.get("status"), booking_id=r.get("booking_id"), report_at=str(r["at"])[:16], report_link=r.get("report_url"),
                                pdf_saved="yes" if r.get("has_pdf") or r.get("report_pdf") else "no",
                                biomarker=x.get("name", ""), value=x.get("value", ""), unit=x.get("unit", ""), range=x.get("range", ""), flag=x.get("flag", "")))
    return out


STATUS_TEXT = {"upcoming": "not open yet", "to_send": "booking WhatsApp not sent yet", "awaiting": "not booked yet", "followup": "not booked (follow-up)",
               "booked": "booked", "check": "collection date passed, check with Redcliffe", "collected": "sample collected, report awaited",
               "report": "report ready (not read yet)", "missed": "window missed", "untracked": "before tracking", "cancelled": "booking cancelled",
               "closed": "order cancelled"}
RESULT_COLS = ["customer", "phone", "email", "order", "program", "sku", "purchased", "test", "status", "booking_id", "report_at", "report_link", "pdf_saved",
               "biomarker", "value", "unit", "range", "flag"]


def siblings(pids):
    """The personalised tests sent from this profile (each is its own plan), for the program page."""
    if not pids: return []
    try:
        return q("SELECT p.program_id, p.product, p.purchased_at FROM `%s` p JOIN `%s` l ON l.program_id = p.program_id AND l.kind = 'profile' "
                 "WHERE p.source = 'custom' AND JSON_VALUE(l.detail, '$.made_from') IN UNNEST(SPLIT(@p, '|')) ORDER BY p.purchased_at DESC" % (PROGRAMS, LOG),
                 p="|".join(pids))
    except Exception as e: print("personalised tests read failed", e); return []


# ---------- web ----------
def safe_next(n):
    return n if isinstance(n, str) and re.fullmatch(r"\./?[\w\-?=&#./%]*", n or "") else "./"


@functions_framework.http
def app(request):
    p, base = request.path.rstrip("/") or "/", "https://" + request.host
    H = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
    if p == "/tick":
        if not hmac.compare_digest(request.args.get("token", ""), E("TOKEN", "\0")): return "forbidden", 403
        res = tick()
        log(program_id="_tick", kind="tick", by_user="hourly run", note=res.get("mode"), # heartbeat: the daily insights read job health from these
            status="failed" if any(isinstance(v, str) and v.startswith("failed") for v in res.values()) else "ok", detail=res)
        return json.dumps(res, default=str), 200
    if p == "/reports":
        if not hmac.compare_digest(request.args.get("token", ""), E("TOKEN", "\0")): return "forbidden", 403
        return json.dumps(reports_run(), default=str), 200
    if p.startswith("/rcl-hook/"):
        if not hmac.compare_digest(p[len("/rcl-hook/"):], hook_key()): return "forbidden", 403
        if request.method != "POST": return "ok", 200 # Redcliffe may check the address first
        raw = request.get_data(cache=False)[:65536]
        try: payload = json.loads(raw)
        except ValueError:
            try: payload = ast.literal_eval(raw.decode()) # Redcliffe's samples are Python-style dicts
            except (ValueError, SyntaxError, UnicodeDecodeError, RecursionError, MemoryError): payload = None
        if not isinstance(payload, dict): return "bad payload", 400
        try: return json.dumps({"ok": True, "result": rcl_hook(payload)}), 200, {"Content-Type": "application/json"}
        except Exception as e: print("redcliffe webhook failed", e, str(raw[:300])); return "error", 500 # Redcliffe sends it again
    if p.startswith("/shop-hook/"):
        store = p[len("/shop-hook/"):]
        if store not in STORES or request.method != "POST": return "not found", 404
        try: st, res = shop_hook(store, request.get_data(cache=False)[:1_000_000], request.headers.get("X-Shopify-Hmac-Sha256"), request.headers.get("X-Shopify-Topic", ""))
        except Exception as e: print("shopify webhook failed", e); st, res = 500, "error" # Shopify retries
        return json.dumps({"result": res}), st, {"Content-Type": "application/json"}
    if p == "/legacy-sync": # hourly (Cloud Scheduler bt-legacy-sync): copy the legacy sheet's tabs that changed
        if not hmac.compare_digest(request.args.get("token", ""), E("TOKEN", "\0")): return "forbidden", 403
        return json.dumps(legacy_sync(), default=str), 200, {"Content-Type": "application/json"}
    if p == "/insights": # read-only daily report for the weekday check (its own key; no names or phone numbers)
        if not hmac.compare_digest(request.args.get("key", ""), insights_key()): return "forbidden", 403
        h = request.args.get("hours", "")
        return json.dumps(insights(int(h) if h.isdigit() and 1 <= int(h) <= 168 else None), default=str, indent=1), 200, {"Content-Type": "application/json"}
    if p == "/manual-send": # token-only: the same send as Settings -> Send a WhatsApp (used to check a template end to end)
        if not hmac.compare_digest(request.args.get("token", ""), E("TOKEN", "\0")): return "forbidden", 403
        if request.method != "POST": return "POST only", 405
        f = request.get_json(silent=True) or {}
        try: ok, res, tpl = manual_send(f.get("phone"), f.get("template", ""), f.get("params") or {}, f.get("ref", ""), "admin (token)")
        except ValueError as e: return json.dumps({"ok": False, "error": str(e)}), 400, {"Content-Type": "application/json"}
        return json.dumps({"ok": ok, "template": tpl, "result": res}, default=str), 200, {"Content-Type": "application/json"}
    if p == "/purchase": # payments.sova.health / payx.sova.health hand a paid blood test over; its own derived key (see purchase_api)
        if request.method != "POST": return "POST only", 405
        if not hmac.compare_digest(request.headers.get("X-Purchase-Key", ""), purchase_key()): return "forbidden", 403
        try: st, out = purchase_api(request.get_json(silent=True) or {}, base)
        except Exception as e: print("purchase failed", e); st, out = 500, {"error": "failed: %s" % str(e)[:200]}
        return json.dumps(out, default=str), st, {"Content-Type": "application/json"}
    if p == "/purge-tests": # token-only: remove test data (dry run unless "dry": false); see purge_tests()
        if not hmac.compare_digest(request.args.get("token", ""), E("TOKEN", "\0")): return "forbidden", 403
        if request.method != "POST": return "POST only", 405
        f = request.get_json(silent=True) or {}
        out = purge_tests(f.get("programs") or [], f.get("cancels") or [], "admin (token)", f.get("dry", True) is not False)
        return json.dumps(out, default=str), 200, {"Content-Type": "application/json"}
    if p == "/sheet-dates": # token-only: program start / end from the legacy sheet; a report unless ?apply=1 (POST)
        if not hmac.compare_digest(request.args.get("token", ""), E("TOKEN", "\0")): return "forbidden", 403
        go = request.method == "POST" and request.args.get("apply") == "1"
        res = sheet_dates(apply=go)
        if request.args.get("format") == "csv":
            f = io.StringIO(); keys = ["program_id", "order", "name", "bought", "our_start", "our_end", "sheet_start", "sheet_end", "sheet_tab", "sheet_status",
                                       "excluded", "match", "t2_status", "t2_sent", "t2_booked", "result"]
            w = csv.DictWriter(f, keys, extrasaction="ignore"); w.writeheader(); w.writerows(res["programs"])
            return f.getvalue(), 200, {"Content-Type": "text/csv"}
        return json.dumps({k: v for k, v in res.items() if k != "programs"}, default=str), 200, {"Content-Type": "application/json"}
    if p == "/resync-orders": # re-read both stores for every program SKU (after a SKU is unmapped, e.g. Sova X): orders whose program was missed get it
        if not hmac.compare_digest(request.args.get("token", ""), E("TOKEN", "\0")): return "forbidden", 403
        if request.method != "POST": return "POST only", 405
        skus = sorted(program_skus())
        try: res = sync_orders(only=skus)
        except Exception as e: res = "failed: %s" % str(e)[:300]
        log(program_id="_sync", kind="resync", by_user="admin (token)", note=str(res)[:500], detail={"skus": skus})
        return json.dumps({"skus": skus, "result": res}), 200, {"Content-Type": "application/json"}
    if p == "/legacy-typed": # verification: a tab as the old Apps Script saw it (dates / numbers / text) plus as shown; token only
        if not hmac.compare_digest(request.args.get("token", ""), E("TOKEN", "\0")): return "forbidden", 403
        sheet = request.args.get("sheet", "")
        if sheet not in LB.SYNC: return "bad request", 400
        try: body = json.dumps(legacy_typed(sheet), separators=(",", ":"), default=str).encode()
        except Exception as e: return "error: %s" % str(e)[:300], 500
        import gzip
        return gzip.compress(body, 6), 200, {"Content-Type": "application/json", "Content-Encoding": "gzip"}
    if p.startswith("/legacy-import/"): # the one-time copy of the legacy sheet, posted from the sheet's own browser tab (text/plain CSV)
        key = legacy_key()
        if not key or not hmac.compare_digest(p[len("/legacy-import/"):], key): return "forbidden", 403
        sheet = request.args.get("sheet", "")
        if request.method != "POST" or sheet not in LB.SHEETS + (OPS_HTML,): return "bad request", 400
        raw = request.get_data(cache=False)[:20_000_000]
        try: n, hdr = legacy_store(sheet, raw.decode("utf-8-sig"), "sheet import")
        except Exception as e: print("legacy import failed", e); return "error: %s" % str(e)[:200], 500
        return json.dumps({"sheet": sheet, "rows": n, "columns": len(hdr)}), 200, {"Content-Type": "application/json"}
    if p.startswith("/r/"):
        body, st, hd = report_page(p[3:], request)
        return (body, st, {**H, **hd}) if st != 303 else ("", 303, {"Location": body})
    if p == SAMPLE: return open(os.path.join(os.path.dirname(__file__), "sample-report.pdf"), "rb").read(), 200, {"Content-Type": "application/pdf"}
    if p.startswith("/c/"): return (*confirm_page(p[3:]), H)
    if p.startswith("/v/"):
        body, st, hd = verify_page(p[3:], request)
        return (body, st, list(H.items()) + hd) if st != 303 else ("", 303, hd + [("Location", body)])
    if p.startswith("/b/"):
        staff = from_session(request.cookies.get("bt")) if request.args.get("ops") == "1" else None # the customer link stays a customer page
        body, st = booking_page(p[3:], request, staff["email"] if staff else "customer")
        return (body, st, H) if st != 303 else ("", 303, {"Location": body})
    if p == "/logo.webp":
        f = os.path.join(os.path.dirname(__file__), "logo.webp")
        if not os.path.exists(f): return "not found", 404
        return open(f, "rb").read(), 200, {"Content-Type": "image/webp", "Cache-Control": "public, max-age=86400"}
    if p == "/login":
        if request.method != "POST": return ui.login_page("", safe_next(request.args.get("next")), ""), 200, H
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[-1].strip() # Google appends the real caller last
        email, nxt = request.form.get("email", ""), safe_next(request.form.get("next"))
        keys = (ip, "@" + email.strip().lower()) # per address and per account: a made-up address can't reset the count
        if any(too_many_fails(k) for k in keys): return ui.login_page("Too many attempts. Wait 10 minutes and try again.", nxt, email), 429, H
        who = check_login(email, request.form.get("password", ""))
        if not who:
            for k in keys: _fails.setdefault(k, []).append(time.time())
            return ui.login_page("That email and password don't match an active account.", nxt, email), 401, H
        return "", 302, {"Location": nxt, "Set-Cookie": "bt=%s; Path=/; Max-Age=%d; HttpOnly; Secure; SameSite=Lax" % (make_session(who), SESSION_HOURS * 3600)}
    if p == "/logout": return "", 302, {"Location": "login", "Set-Cookie": "bt=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"}
    who = from_session(request.cookies.get("bt"))
    if not who: return "", 302, {"Location": "login?" + urllib.parse.urlencode({"next": "." + request.full_path.rstrip("?") if request.method == "GET" else "./"})}
    if p == "/act" and request.method == "POST":
        if urllib.parse.urlparse(request.headers.get("Origin") or request.headers.get("Referer") or "").netloc not in ("", request.host):
            return "Blocked: the form came from another site.", 403
        m, back = act(request.form, who, base)
        back = safe_next(back if back.startswith("./") else "./" + back)
        return "", 303, {"Location": back + ("&" if "?" in back else "?") + "m=" + urllib.parse.quote(m)}
    hold = setting("wa_hold")
    if p == "/": # the legacy sheet's people are read only when asked for (their filters or a search): the usual overview stays fast
        return ui.dashboard(dashboard_data(request.args.get("s") in ("sheet", "other") or bool((request.args.get("q") or "").strip())), request.args, who,
                            send_mode(), hold), 200, H
    if p == "/p" and request.args.get("id", "").startswith("S-"):
        allp, _ = load()
        leg = legacy_people()
        me = next((r for r in leg if r["lid"] == request.args["id"]), None)
        if not me: return ui.message_page("Not found", "No legacy sheet record with that id."), 404, H
        ph = LB.phone10(me["phone"])
        hist = [r for r in leg if r is me or ph and LB.phone10(r["phone"]) == ph]
        ops = {LB.order_key(r["order_id"]): r for r in LB.ops_data(legacy_table(LB.OPS), legacy_types(LB.OPS))[0]}
        here = [x for x in allp if ph and (x.get("phone") or "")[-10:] == ph]
        return ui.legacy_profile(me, hist, ops, here, who, send_mode(), hold), 200, H
    if p == "/p":
        progs, logs = load(request.args["id"]) if request.args.get("id") else ([], {})
        if not progs: return ui.message_page("Not found", "No program with that id."), 404, H
        rows = rows_for(progs, logs)
        extra = {"results": results_for(progs[0]["members"]), "legacy": legacy(progs[0]), "live_from": setting("live_from"), "briza": setting("briza_chat_url"), "t1_covered": progs[0].get("t1_covered"),
                 "linked": linked_info(progs[0], logs.get(progs[0]["program_id"], [])),
                 "siblings": [s_ for s_ in siblings(progs[0]["members"]) if s_["program_id"] not in progs[0]["members"]],
                 "codes": setting("prod_package_codes" if prod() else "package_codes")}
        try:
            mine = set(progs[0]["members"]); ph = (progs[0].get("phone") or "")[-10:]
            extra["sheet"] = [r for r in legacy_people() if r.get("pid") in mine or ph and LB.phone10(r["phone"]) == ph]
        except Exception as e: print("sheet history failed", e); extra["sheet"] = []
        return ui.program(progs[0], rows, logs.get(progs[0]["program_id"], []), {t["n"]: base + "/b/" + tok(progs[0]["program_id"], t["n"]) + "?ops=1" for t in rows},
                          who, request.args.get("m", ""), MAX_RESCHEDULES, send_mode(), hold, extra), 200, H
    if p == "/unbooked": return ui.unbooked(dashboard_data(False), request.args, who, send_mode(), hold), 200, H
    if p == "/analytics": return ui.analytics(analytics_data(*period(request.args)), who, send_mode(), hold), 200, H
    if p == "/analytics.csv":
        a, b, _ = period(request.args)
        kind, key = request.args.get("kind", "all"), request.args.get("key", "")
        name = re.sub(r"[^a-z0-9_-]+", "-", "%s-%s-%s-%s" % (kind, key, a, b), flags=re.I)
        return ui.csv_rows(analytics_export(kind, key, a, b), EXPORT_COLS), 200, {"Content-Type": "text/csv", "Content-Disposition": "attachment; filename=sova-%s.csv" % name}
    if p == "/results.csv":
        pid = request.args.get("id")
        progs, logs = load(pid) if pid else load()
        res = results_for([m for p_ in progs for m in p_["members"]] if pid else None)
        return ui.csv_rows(results_rows(progs, logs, res), RESULT_COLS), 200, {"Content-Type": "text/csv",
               "Content-Disposition": "attachment; filename=sova-test-results%s.csv" % ("-" + re.sub(r"[^A-Za-z0-9-]", "", pid) if pid else "")}
    if p == "/pdf": # the team opens a saved report copy from the customer's page
        pdf = stored_pdf(request.args.get("id", "")[:64]) if request.args.get("id") else None
        if not pdf: return ui.message_page("No saved copy", "This report's PDF wasn't saved (too large, or Redcliffe's link had already expired)."), 404, H
        return pdf, 200, {"Content-Type": "application/pdf", "Cache-Control": "no-store, private", "Content-Disposition": 'inline; filename="report.pdf"'}
    if p == "/dates/sample.csv":
        return DATES_SAMPLE, 200, {"Content-Type": "text/csv", "Content-Disposition": "attachment; filename=sova-program-dates-sample.csv"}
    if p == "/dates":
        if request.method == "POST":
            if urllib.parse.urlparse(request.headers.get("Origin") or request.headers.get("Referer") or "").netloc not in ("", request.host):
                return "Blocked: the form came from another site.", 403
            if who.get("role") != "admin": return ui.dates_page(who, send_mode(), hold, [], "View-only login: ask an admin to upload.", setting("dates_csv_last")), 403, H
            fobj = request.files.get("file")
            raw = fobj.read(5_000_000) if fobj else b""
            if not raw: return ui.dates_page(who, send_mode(), hold, [], "Choose a CSV file first.", setting("dates_csv_last")), 400, H
            try: text = raw.decode("utf-8-sig")
            except UnicodeDecodeError: text = raw.decode("latin-1")
            try: rows, summ = dates_csv(text, who["email"])
            except Exception as e: rows, summ = [], "Upload failed: %s" % str(e)[:200]
            return ui.dates_page(who, send_mode(), hold, rows, summ, setting("dates_csv_last")), 200, H
        return ui.dates_page(who, send_mode(), hold, [], request.args.get("m", ""), setting("dates_csv_last")), 200, H
    if p == "/add-user":
        link = request.args.get("link")
        chosen = load(link)[0][:1] if link else []
        return ui.add_user(find_buyers(request.args.get("q")), request.args.get("q", ""), chosen, who, send_mode(), hold, RELATIONS,
                           request.args.get("m", "")), 200, H
    if p == "/skus":
        return ui.skus(sku_table(), request.args, who, send_mode(), hold, MIN_DAYS, needs_review, manual), 200, H
    if p == "/settings":
        pk = None
        if request.args.get("packages"):
            st, r = rcl("GET", "/api/external/v2/center-package-data/", params={"search": request.args.get("packages")})
            pk = r.get("data") if isinstance(r, dict) and isinstance(r.get("data"), list) else rcl_msg(r)
        cfg(refresh=True)
        live = {"env": "prod" if prod() else "staging", "host": (setting("rcl_prod_host") or "https://api.redcliffelabs.com") if prod() else RCL,
                "key": bool(secret(PROD_KEY)), "stores": {label: shop_status(k) for k, (label, *_) in STORES.items()}, "hook": base + "/rcl-hook/" + hook_key(),
                "apps": {k: (label, STORES[k][1], bool(store_app(k).get("client_id")), base + "/shop-hook/" + k) for k, (label, *_) in STORES.items()}}
        users = sorted(accounts(refresh=True).values(), key=lambda r: (not r["active"], r["email"]))
        return ui.settings(cfg(), sku_table(), who, request.args.get("m", ""), pk, {k: setting(k) for k in DEFAULT_T},
                           send_mode(), sorted(test_numbers()), live["host"], hold, live, users, legacy_meta(), last_sync()), 200, H
    if p == "/send":
        a = request.values
        key = a.get("template") or "t1_template"
        msg, ok = "", None
        if request.method == "POST":
            if urllib.parse.urlparse(request.headers.get("Origin") or request.headers.get("Referer") or "").netloc not in ("", request.host):
                return "Blocked: the form came from another site.", 403
            if who.get("role") != "admin": msg, ok = "View-only login: ask an admin to send.", False
            else:
                try:
                    ok, res, tpl = manual_send(a.get("phone"), key, {k[2:]: v for k, v in a.items() if k.startswith("v_")}, a.get("ref", ""), who["email"])
                    msg = ("Sent %s to %s." % (tpl, norm_phone(a.get("phone")))) if ok else "Briza didn't send it: %s" % str(res)[:300]
                except ValueError as e: msg, ok = str(e).capitalize() + ".", False
        try: tpl, vals, buttons, prog, n = manual_message(key, a.get("ref", ""), a.get("name", ""))
        except ValueError: tpl, vals, buttons, prog, n = "", {}, [], None, 1
        return ui.send_page(MANUAL, key, tpl, vals, buttons, prog, n, a, msg, ok, manual_log(), who, send_mode(), hold), 200, H
    if p == "/bt": return ui.bt_page(who, send_mode(), hold), 200, H
    if p == "/bt.json":
        body = json.dumps(bt_dashboard_data(), separators=(",", ":"), default=str).encode()
        hd = {"Content-Type": "application/json", "Cache-Control": "no-store, private"}
        if "gzip" in (request.headers.get("Accept-Encoding") or ""):
            import gzip
            body, hd["Content-Encoding"] = gzip.compress(body, 6), "gzip"
        return body, 200, hd
    if p in ("/ops", "/ops.json"): return "", 302, {"Location": "bt"} # the old Program ops page was removed; old links land on the BT dashboard
    if p in ("/legacy-issues", "/legacy-issues.csv"):
        iss = []
        LB.legacy_rows(legacy_table(LB.SHEET), issues=iss, types=legacy_types(LB.SHEET))
        if p.endswith(".csv"):
            return ui.csv_rows(iss, ["row", "order", "name", "issue", "detail"]), 200, {"Content-Type": "text/csv", "Content-Disposition": "attachment; filename=legacy-sheet-issues.csv"}
        return ui.legacy_issues(iss, request.args.get("issue", ""), who, send_mode(), hold), 200, H
    if p in ("/legacy", "/legacy.csv"):
        if request.method == "POST":
            if urllib.parse.urlparse(request.headers.get("Origin") or request.headers.get("Referer") or "").netloc not in ("", request.host):
                return "Blocked: the form came from another site.", 403
            if who.get("role") != "admin": return "", 303, {"Location": "settings?m=" + urllib.parse.quote("View-only login: ask an admin to upload.") + "#legacy"}
            if request.form.get("a") == "sync":
                res = legacy_sync(who["email"])
                m = "Synced from the sheet: " + "; ".join("%s %s" % kv for kv in res.items())
                return "", 303, {"Location": "settings?m=" + urllib.parse.quote(m[:900]) + "#legacy"}
            sheet, fobj = request.form.get("sheet", ""), request.files.get("file")
            raw = fobj.read(20_000_000) if fobj else b""
            if sheet not in LB.SHEETS or not raw: m = "Pick the tab and a CSV file."
            else:
                try: text = raw.decode("utf-8-sig")
                except UnicodeDecodeError: text = raw.decode("latin-1")
                try: n, hdr = legacy_store(sheet, text, who["email"]); m = "Saved '%s': %d rows, %d columns. The Blood Test dashboard uses it now." % (sheet, n, len(hdr))
                except Exception as e: m = "Upload failed: %s" % str(e)[:200]
            return "", 303, {"Location": "settings?m=" + urllib.parse.quote(m) + "#legacy"}
        sheet = request.args.get("sheet", LB.SHEET)
        table = legacy_table(sheet) if sheet in LB.SHEETS else []
        if p == "/legacy.csv":
            out = io.StringIO(); csv.writer(out).writerows(table)
            return out.getvalue(), 200, {"Content-Type": "text/csv", "Content-Disposition": "attachment; filename=legacy-%s.csv" % re.sub(r"[^A-Za-z0-9]+", "-", sheet)}
        return ui.legacy_page(sheet, table, LB.SHEETS, legacy_meta(), request.args.get("q", ""), who, send_mode(), hold), 200, H
    if p == "/export.csv": return ui.export_csv(dashboard_data()), 200, {"Content-Type": "text/csv", "Content-Disposition": "attachment; filename=sova-blood-tests.csv"}
    return "not found", 404
