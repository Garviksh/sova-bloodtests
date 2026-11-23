# Sova Program Blood Tests

Turns Shopify program orders into Redcliffe home blood tests, talks to customers only through approved WhatsApp templates (Briza), keeps every event in BigQuery, and gives the ops team one dashboard. That dashboard also carries the legacy "Program Clean" Google Sheet.

- **Live:** Cloud Run service `sova-bloodtests` (asia-south1, project `sova-production`)
- **Design:** [docs/LLD.md](docs/LLD.md) (low-level design) · [DESIGN.md](DESIGN.md) (UI tokens)

## What it does

- Picks program orders (90+ days) from both stores, by SKU. Sova X isn't a program.
- Sends Test 1 and Test 2 booking WhatsApps, reminders, confirmations, collection reminders, sample-collected and report messages, each once, at the right time. Test 2 is timed from the real program dates in the legacy sheet.
- Customers book, verify by OTP, reschedule and open reports through signed links. Nothing goes into the WhatsApp chat.
- Redcliffe bookings, status webhooks, 11/13/17 checks, report PDFs and biomarkers.
- Ops dashboard pages:
  - Overview and queues
  - Customer page with an Open Briza chat button
  - BT dashboard (matches the old Apps Script dashboard)
  - Not booked, Analytics and Legacy data
  - Send WhatsApp, SKUs and Settings
- An hourly copy of the legacy sheet, with program dates applied automatically.
- A read-only `/insights` report for the weekday health check.

## Run the tests

```bash
pip install -r requirements.txt
python3 test_main.py        # prints "all tests passed"
```

The tests fake BigQuery, Shopify, Redcliffe, Briza and Google APIs, so they need no keys or network.

## Deploy

```bash
gcloud run deploy sova-bloodtests --source . --function app \
  --base-image python312 --region asia-south1 --memory 1Gi
```

Run the tests first and deploy only if they pass. The service reads its configuration from environment variables and Secret Manager; see `.env.example` for the names. **No keys are in this repository.**

## Files

| File | What |
|---|---|
| `main.py` | Routes, business logic, integrations, jobs |
| `ui.py` | All HTML pages and the stylesheet |
| `legacy_bt.py` | Legacy Google Sheet parsing and the old dashboard's maths |
| `test_main.py` | End-to-end tests (fakes for every external service) |
| `docs/LLD.md` | Low-level design |
| `DESIGN.md` | UI design system |
| `setup.sh`, `addons.sh`, `maps.sh` | One-time Cloud Shell setup (tables, secrets, jobs); print no secret values |
| `deploy.sh` | Test, then redeploy |
| `reset.sh` | Show state / wipe for a clean test round (backs up first) |
| `compare.py`, `parity.py`, `ref.js` | Parity checks against the old Apps Script dashboard |
| `docs/SOVA_NOTION_PAGE.md` | Project impact write-up (portfolio page) |
