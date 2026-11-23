# Sova Health — Program Blood Tests

## The company

Sova Health ([sova.health](https://sova.health)) is a Gurugram-based, full-stack gut-health company. It combines gut microbiome and food-intolerance testing, personalised supplements and expert coaching into paid health programs. Program customers get blood tests at fixed points in the program (a baseline test and a follow-up test), and those tests are what this project is about.

## My seat

**Product owner — Program Blood Tests platform** · 2026 → Present. I took the blood-test step of every Sova program from a hand-run Google Sheet to a self-running service: spec, build, data migration, go-live and operations. The work covered the Shopify order, the Redcliffe Labs home collection, the customer's WhatsApp journey on Briza, and the ops dashboard the team works from. The North Star was simple: **every program customer gets each test on time without anyone chasing it by hand.**

---

## Go-live at a glance

| 458 | 441 | 0 | 100% | 57 |
| --- | --- | --- | --- | --- |
| programs migrated and reconciled | program dates corrected from the source sheet | failed WhatsApps since go-live | of first-3-day bookings made by customers themselves | production releases, each behind the full test suite |

Live in production since 21 Nov 2026. In the first three days: ~170 automated WhatsApps sent with none failing, 17 home collections booked (all self-served by customers through their link), 3 reschedules handled without ops, 30 phone numbers verified, 50 new programs picked up from Shopify automatically, and 73 hourly sheet syncs with zero failures. Every figure comes from the service's own event log, not from memory.

---

## The problem I was handed

Blood tests for program customers lived in a shared Google Sheet. Ops read new Shopify orders, worked out when each customer's test window opened, booked the home collection with Redcliffe, sent the WhatsApp reminders by hand, and chased reports back. That broke in predictable ways: windows were missed when nobody looked at the sheet, the sheet's program dates drifted away from the real program start, and there was no single place to see who needed a call today.

## What I shipped

**1. An end-to-end blood-test pipeline.** Shopify program order → test plan per customer → Redcliffe Labs booking → WhatsApp journey on Briza → results back into BigQuery. It runs on its own every hour; ops only step in where the system asks.

**2. Customer self-booking.** Each customer gets a personal, signed link on WhatsApp to verify their number and pick a home-collection slot. Bookings, reschedules and cancellations go straight to Redcliffe; status updates (phlebo assigned, sample collected, report ready) come back by webhook and trigger the next message.

**3. The ops dashboard.** One place for every program and test: today's collections, open customer requests, tests that need a follow-up call or a check with Redcliffe, a send-WhatsApp page, analytics, and a customer page with the full timeline and a one-click Briza chat link.

**4. Legacy migration with a safety net.** A replica of the old "Program Clean" sheet, synced every hour, so the sheet stays the source of truth for program dates during the transition. Dates are matched by order (and by phone within 7 days of purchase) and applied automatically; where the sheet's own rows disagree, the program is left alone and flagged for a person.

**5. Daily health insights.** A read-only report (no names or phone numbers) that a scheduled task reads every weekday at noon IST: job health, message failures, app errors, sheet-sync runs, date mismatches and what needs a person today.

---

## How it was built

- **Runtime.** One Python service on Google Cloud Run (asia-south1), BigQuery as the system of record, Cloud Scheduler for the hourly run, the hourly sheet sync and the 11:00 / 13:00 / 17:00 checks.
- **Event-sourced state.** Programs plus an append-only event log; every test's status is folded from its events, so the timeline on the dashboard is the audit trail.
- **Safe by default.** A send mode (off / test / live) and a Redcliffe environment switch gate every outbound action; templates on hold in Briza are held, not sent; customer links are HMAC-signed and only ever reach the customer's own number.
- **Release discipline.** Every change is applied on top of the live source, run through the full test suite, and deployed only if it passes (57 revisions to date), with a handbook, a change log and an LLD in the GitHub repo.
- **Go-live hygiene.** Test data purged with a backup before launch, memory sized from real load, input validation (age 5–110) matched to the lab's rules.

---

## Impact

- **Manual work removed:** booking, reminders, status chasing and date upkeep no longer need a person per customer; ops work an exception list instead of a sheet.
- **Customers serve themselves:** every booking in the first days of live came from the customer's own link.
- **Data you can trust:** 458 programs reconciled against the source sheet; 441 wrong program dates corrected and kept in step every hour.
- **Nothing silent:** failures, held templates, open customer requests and date mismatches surface in the dashboard and in a noon report every weekday.

---

> **What stays private:** customer names, phone numbers and health data, raw revenue, internal targets and vendor commercials. Figures here are operational counts from the service's own log. Vendor names (Shopify, Redcliffe Labs, Briza) appear because they are part of the public product.

---

## Working list — before publishing

- [ ] Confirm my title and start date at Sova
- [ ] Add 30-day numbers once the first full month of live data is in (on-time test rate, self-booking share)
- [ ] Link the GitHub repo and LLD

---

*Portfolio table row to add (Company · Duration · What they do):*
Sova Health — https://sova.health · Dashboard: Sova Health — Program Blood Tests · 2026 → Present · Full-stack gut-health company (microbiome testing, personalised supplements, coaching programs). Product owner for the Program Blood Tests platform.
