"""python3 test_main.py -- rules, booking, reschedule cap, sends and routes, with BigQuery / Shopify / Redcliffe / Briza faked."""
import base64, datetime as dt, io, json, os
os.environ.update(TOKEN="test-token", DASH_PASSWORD="pw-setup", REDCLIFFE_KEY="k", BRIZA_API_KEY="b")
import flask
import main as M
import ui

IST, UTC = M.IST, dt.timezone.utc
NOW = dt.datetime(2026, 11, 11, 6, 0, tzinfo=UTC) # 11:30 IST
DB = {"programs": [], "log": [], "cfg": {"send_mode": "test", "test_numbers": "919900000001", "package_codes": "PKG1", "t1_delay_days": "0"}, "skus": {}}
CALLS = []
COVER = {"status": "success", "message": "Location is serviceable"}
SECRETS, REPORTS, CHATS = {}, {}, {} # Secret Manager, Redcliffe consolidated reports by booking id, last WhatsApp from a customer (adherence log)


def reset(**cfg):
    DB.update(programs=[], log=[], skus={}, results=[]); DB["cfg"] = {"send_mode": "test", "test_numbers": "919900000001", "package_codes": "PKG1", "t1_delay_days": "0", **cfg}; CALLS.clear()
    SECRETS.clear(); REPORTS.clear(); CHATS.clear(); M._sec.clear()


def fake_log(**row):
    row = {"id": str(len(DB["log"])), "at": M.now(), "test_no": None, "status": None, "note": None, "collection_date": None, "slot": None,
           "booking_id": None, "by_user": None, **row}
    if isinstance(row.get("detail"), (dict, list)): row["detail"] = json.dumps(row["detail"], default=str)
    DB["log"].append(row); return row


def fake_load(pid=None): # as main.load: with pid, every program of that person (same number, not a test profile)
    ph = next((p["phone"] for p in DB["programs"] if p["program_id"] == pid and p.get("source") != "test"), "")
    progs = M.only_programs([dict(p) for p in DB["programs"] if not pid or p["program_id"] == pid or ph and p["phone"] == ph])
    ids = {p["program_id"] for p in progs}
    logs = {}
    for r in DB["log"]:
        if not pid or r["program_id"] in ids: logs.setdefault(r["program_id"], []).append(r)
    return M.apply_shifts(M.plans(progs, logs, pid), logs), logs


class FakeBQ:
    def insert_rows_json(self, table, rows, row_ids=None):
        if table.endswith(".bt_results"): DB.setdefault("results", []).extend(dict(r) for r in rows); return []
        if table.endswith(".bt_log"): # batch log rows (the dates CSV)
            for r in rows: fake_log(**{**{k: v for k, v in r.items() if k != "id"}, "at": dt.datetime.fromisoformat(r["at"]) if isinstance(r.get("at"), str) else M.now()})
            return []
        for r in rows:
            r = dict(r)
            for k in ("purchased_at", "added_at"):
                if isinstance(r.get(k), str): r[k] = dt.datetime.fromisoformat(r[k].replace("Z", "+00:00"))
            DB["programs"].append(r)
        return []


def fake_http(url, body=None, headers=None, method=None, form=False):
    CALLS.append((url, body))
    if "send-template" in url: return 200, {"success": True, "data": {"messageId": "wamid.S%d" % len(CALLS)}}
    if "center-create-booking" in url: return 200, {"status": "success", "booking_id": 555}
    if "center-confirm-booking" in url: return 200, {"status": "Success", "message": "Booking has been created successfully"}
    if "center-update-booking" in url: return 200, {"status": "success", "booking_status": "rescheduled"}
    if "get-time-slot-list" in url: return 200, {"status": "success", "results": [
        {"id": 43, "available_slot": 1, "format_24_hrs": {"start_time": "21:00"}, "format_12_hrs": {"start_time": "09:00 PM", "end_time": "09:30 PM"}},
        {"id": 2, "available_slot": 3, "format_24_hrs": {"start_time": "07:00"}, "format_12_hrs": {"start_time": "07:00 AM", "end_time": "08:00 AM"}},
        {"id": 3, "available_slot": 0, "format_24_hrs": {"start_time": "08:00"}, "format_12_hrs": {"start_time": "08:00 AM", "end_time": "09:00 AM"}}]}
    if "center-get-booking" in url: return 200, {"status": "Success", "data": [{"booking_id": 555, "booking_status": {"id": 1, "value": "Phlebo Assigned"}}]}
    if "is-location-serviceable" in url: return 200, dict(COVER)
    if "metadata.google.internal" in url: return 200, {"access_token": "sa-token", "expires_in": 3600}
    if "secretmanager.googleapis.com" in url:
        name = url.split("/secrets/")[1].split("/")[0].split(":")[0]
        if url.endswith(":addVersion"): SECRETS[name] = base64.b64decode(body["payload"]["data"]).decode(); return 200, {"name": name}
        return (200, {"payload": {"data": base64.b64encode(SECRETS[name].encode()).decode()}}) if name in SECRETS else (404, {"error": {"code": 404}})
    if "get-consolidated-report" in url:
        bid = url.rstrip("/").split("/")[-1]
        return 200, ({"id": int(bid), "Report_url": REPORTS[bid]} if bid in REPORTS else {"errors": ["Dont have a matching booking"]})
    if "location-2-eloc" in url: return 200, {"results": [{"eloc": "ABC123"}]}
    if "loc-2-eloc" in url: return 200, {"latitude": 28.61, "longitude": 77.36}
    return 404, {}


M.now = lambda: NOW
M.log, M.load, M.http = fake_log, fake_load, fake_http
M.cfg = lambda refresh=False: DB["cfg"]
M.set_cfg = lambda k, v, by: DB["cfg"].__setitem__(k, v)
M.sku_table = lambda: DB["skus"]
M.set_sku = lambda sku, name, pt, days, note, by: DB["skus"].__setitem__(sku, {"sku": sku, "name": name, "product_type": pt, "days": days, "note": note})
ACC = {}
M.accounts = lambda refresh=False: ACC
M.save_account = lambda email, role, h, active, by, action: ACC.__setitem__(email, {"email": email, "role": role, "pw_hash": h, "active": active, "action": action})
M.chatting = lambda phone: CHATS.get(phone)
M._bq = FakeBQ()
TRUE_SHOPIFY = M.shopify
_net_fetch = M.fetch_pdf
M.fetch_pdf = lambda url: None if str(url).startswith("https://") else _net_fetch(url) # tests never download a real report


def prog(pid="1001-PR-WGL-120", days=120, phone="919900000001", at=NOW, added=None, lat=28.6):
    p = dict(program_id=pid, order_name="#" + pid.split("-")[0], source="web", purchased_at=at, sku="PR-WGL-120", product="Gut x Weight Loss", days=days,
             customer_name="asha rao", phone=phone, email="a@x.in", address="12 MG Road", city="Noida", state="UP", pincode="201301", lat=lat, lng=77.3 if lat else None,
             added_at=added or at)
    DB["programs"].append(p); return p


def state(pid, n=1, at=None):
    progs, logs = M.load(pid)
    return [t for t in M.rows_for(progs, logs, at or NOW) if t["n"] == n][0]


def at_ist(y, m, d, h=11): return dt.datetime(y, m, d, h, 0, tzinfo=IST).astimezone(UTC)


# ---- schedule rules
p = {"purchased_at": at_ist(2026, 11, 11), "days": 120}
assert M.tests_for(p) == [(1, dt.date(2026, 11, 11), dt.date(2026, 11, 17)), (2, dt.date(2027, 2, 19), dt.date(2027, 2, 24))], M.tests_for(p)
assert M.tests_for({**p, "days": 90})[1][2] == dt.date(2026, 11, 11) + dt.timedelta(days=75)
assert len(M.tests_for({**p, "days": 30})) == 1 and len(M.tests_for({**p, "days": 45})) == 2
late_night = {"purchased_at": dt.datetime(2026, 11, 10, 20, 0, tzinfo=UTC), "days": 120} # 01:30 IST on the 11th
assert M.tests_for(late_night)[0][1] == dt.date(2026, 11, 11), "purchase date is the IST date"

# ---- helpers
assert M.norm_phone("+91 98765-43210") == "919876543210" and M.norm_phone("09876543210") == "919876543210" and M.norm_phone("12345") == ""
assert M.norm_phone("5876543210") == "", "Indian mobiles start with 6-9"
assert M.guess_days("PR-WGL-120", "Coaching") == 120 and M.guess_days("SU-GHB-90", "Supplement") == 0 and M.guess_days("TE-GMT-001", "Coaching") == 0
assert M.redcliffe_name("A.") == "Sova Customer" and M.redcliffe_name("Dr. A.") == "Dr A" and M.redcliffe_name("Asha Rao-2") == "Asha Rao"
t = M.tok("1001-PR-WGL-120", 2)
assert M.untok(t) == ("1001-PR-WGL-120", 2) and M.untok(t[:-1] + ("0" if t[-1] != "0" else "1")) is None and M.untok("junk") is None

# ---- state machine
reset()
prog()
assert state("1001-PR-WGL-120")["status"] == "to_send"
assert state("1001-PR-WGL-120", 2)["status"] == "upcoming"
progs, logs = M.load()
rows = M.rows_for(progs, logs)
for r in rows: r["logs"] = logs.get(r["program"]["program_id"], [])
assert [(r["n"], w) for r, w in M.plan(rows)] == [(1, "t1")]
assert M.send(rows[0], "t1", "schedule") == "sent" # test number -> real send
sent = [c for c in CALLS if "send-template" in c[0]][0][1]
params = {x["parameter_name"]: x["text"] for x in sent["components"][0]["parameters"]}
assert sent["templateName"] == "bt_welcome_book" and params == {"first_name": "Asha", "book_by": "Tue, 17 Nov", "program": "Gut x Weight Loss"}, \
    "exactly the variables submitted to Meta"
assert sent["components"][1]["sub_type"] == "url" and M.untok(sent["components"][1]["parameters"][0]["text"]) == ("1001-PR-WGL-120", 1), "button = this test's page"
assert state("1001-PR-WGL-120")["status"] == "awaiting"
assert state("1001-PR-WGL-120", at=NOW + dt.timedelta(hours=49))["status"] == "followup"
fake_log(program_id="1001-PR-WGL-120", test_no=1, kind="followup", note="called", at=NOW + dt.timedelta(hours=48))
assert state("1001-PR-WGL-120", at=NOW + dt.timedelta(hours=49))["status"] == "awaiting", "a follow-up clears the queue for 48h"

# real vs planned: a non-test number is only planned in test mode, and gets the real send once live
reset()
prog(phone="919811111111")
rows = M.rows_for(*M.load())
assert M.send(rows[0], "t1", "schedule") == "planned" and not CALLS
assert state("1001-PR-WGL-120")["status"] == "awaiting"
assert state("1001-PR-WGL-120", at=NOW + dt.timedelta(hours=72))["status"] == "awaiting", "nobody to follow up: nothing really went out"
DB["cfg"]["send_mode"] = "live"
assert state("1001-PR-WGL-120")["status"] == "to_send", "planned sends are re-sent for real after going live"
DB["cfg"]["send_mode"] = "off"
assert M.send(M.rows_for(*M.load())[0], "t1", "x") == "planned"

# a number Briza rejects: retried by the hourly job 3 times, then it waits in Failed
reset()
prog()
M.http = lambda url, body=None, headers=None, method=None: (400, {"success": False, "error": "invalid number"})
for i in range(4):
    rows = M.rows_for(*M.load())
    for r in rows: r["logs"] = M.load()[1].get(r["program"]["program_id"], [])
    due = [w for _, w in M.plan(rows)]
    assert due == (["t1"] if i < 3 else []), (i, due)
    if due: assert M.send(rows[0], "t1", "schedule") == "failed"
assert state("1001-PR-WGL-120")["failed"] and state("1001-PR-WGL-120")["status"] == "to_send"
M.http = fake_http

# template not approved yet, or not in Briza at all (what staging answered) -> held, nothing logged, banner until a send works
reset()
prog()
M.http = lambda url, body=None, headers=None, method=None: (400, {"success": False, "error": "TEMPLATE_NOT_APPROVED"})
assert M.send(M.rows_for(*M.load())[0], "t1", "schedule") == "held" and not DB["log"]
M.http = lambda url, body=None, headers=None, method=None: (400, {"success": False, "error": {"code": "TEMPLATE_NOT_FOUND", "message": "Template does not exist"}})
assert M.send(M.rows_for(*M.load())[0], "t1", "schedule") == "held" and not DB["log"] and DB["cfg"]["wa_hold"] == "bt_welcome_book"
M.http = fake_http
assert M.send(M.rows_for(*M.load())[0], "t1", "schedule") == "sent" and DB["cfg"]["wa_hold"] == ""

# window already over when the program reached us -> before tracking, not an exception; missed if we had it
reset()
prog(at=NOW - dt.timedelta(days=40), added=NOW)
assert state("1001-PR-WGL-120")["status"] == "untracked"
reset()
prog(at=NOW - dt.timedelta(days=40))
assert state("1001-PR-WGL-120")["status"] == "missed"

# ---- booking page: dates, slots, booking, reminder, reschedules (max 2), Redcliffe status (the OTP step has its own tests at the end)
real_verified, M.verified = M.verified, lambda request, phone: True
reset()
prog(lat=None)
app = flask.Flask(__name__)
tk = M.tok("1001-PR-WGL-120", 1)
with app.test_request_context("/b/" + tk):
    html, st = M.booking_page(tk, flask.request)
assert st == 200 and "1. Your address" in html and "Use my current location" in html and "Pick a day" not in html and "Back to profile" not in html
assert "value='12 MG Road'" in html and "value='201301'" in html, "the order's address, ready to confirm"
addr = {"act": "address", "house": "12 MG Road", "area": "", "landmark": "Near park", "city": "Noida", "pincode": "201301", "lat": "", "lng": ""}
with app.test_request_context("/b/" + tk, method="POST", data=dict(addr, pincode="2013")):
    html, st = M.booking_page(tk, flask.request)
assert "6-digit pincode" in html and "1. Your address" in html and not [c for c in CALLS if "serviceable" in c[0]]
with app.test_request_context("/b/" + tk, method="POST", data={"act": "book", "d": "2026-11-12", "slot": "2|x", "age": "34", "gender": "female"}):
    html, st = M.booking_page(tk, flask.request)
assert "confirm your address first" in html and not [c for c in CALLS if "create-booking" in c[0]]
with app.test_request_context("/b/" + tk, method="POST", data=addr):
    assert M.booking_page(tk, flask.request) == ("./" + tk, 303)
chk = json.loads([r for r in DB["log"] if r["kind"] == "profile"][-1]["detail"])
assert chk["lat"] == 28.61 and chk["serviceable"] is True and chk["located_by"] == "pincode" and chk["landmark"] == "Near park", chk
assert "latitude=28.61" in [c for c in CALLS if "is-location-serviceable" in c[0]][-1][0]
with app.test_request_context("/b/" + tk):
    html, st = M.booking_page(tk, flask.request)
assert st == 200 and "Pick a day" in html and "Wed 11 Nov" in html and "Sun 15 Nov" in html and "Mon 16 Nov" not in html
assert "home collection is available at this address" in html and "href='?step=address'" in html and "?d=2026-11-12" in html and "ops=1" not in html
with app.test_request_context("/b/%s?d=2026-11-12" % tk):
    html, st = M.booking_page(tk, flask.request)
assert "07:00 AM - 08:00 AM" in html and "08:00 AM - 09:00 AM" not in html and "Book collection" in html
assert html.index("Morning") < html.index("07:00-08:00 AM") < html.index("Evening") < html.index("09:00-09:30 PM"), "sorted and grouped by time of day"
assert ui.short("11:30 AM - 12:00 PM") == "11:30 AM-12:00 PM" and ui.short("odd") == "odd"
form = {"act": "book", "d": "2026-11-12", "slot": "2|07:00 AM - 08:00 AM", "age": "34", "gender": "female", "landmark": "Near park"}
with app.test_request_context("/b/" + tk, method="POST", data=dict(form, age="")):
    html, st = M.booking_page(tk, flask.request)
assert "fill in age and gender" in html and not [c for c in CALLS if "create-booking" in c[0]]
for bad_age in ("3", "0", "111"): # Redcliffe refuses under 5 ("Age should be in range 5 to 150"): we stop it on our page with a clear message
    with app.test_request_context("/b/" + tk, method="POST", data=dict(form, age=bad_age)):
        html, st = M.booking_page(tk, flask.request)
    assert "age between 5 and 110" in html and not [c for c in CALLS if "create-booking" in c[0]], bad_age
with app.test_request_context("/b/" + tk, method="POST", data=dict(form, slot="3|08:00 AM - 09:00 AM")):
    html, st = M.booking_page(tk, flask.request)
assert "just taken" in html and not [c for c in CALLS if "create-booking" in c[0]]
with app.test_request_context("/b/" + tk, method="POST", data=dict(form, slot="2|Midnight, any time")):
    html, st = M.booking_page(tk, flask.request)
assert "Booked for Thu, 12 Nov" in html, html
assert [r for r in DB["log"] if r["kind"] == "booked"][0]["slot"] == "07:00 AM - 08:00 AM", "label from Redcliffe, not the form"
body = [c for c in CALLS if "center-create-booking" in c[0]][0][1]
assert body["customer_phonenumber"] == "9900000001" and body["customer_gender"] == "female" and body["package_code"] == ["PKG1"] and body["is_credit"] is True
assert body["collection_slot"] == 2 and body["customer_name"] == "asha rao" and body["customer_latitude"] == 28.61
s = state("1001-PR-WGL-120")
assert s["status"] == "booked" and s["booking"]["booking_id"] == "555" and s["resched"] == 0
# Template 2 goes out the day before, once per date
day_before = at_ist(2026, 11, 11, 12)
rows = M.rows_for(*M.load(), at=day_before)
for r in rows: r["logs"] = M.load()[1]["1001-PR-WGL-120"]
assert [w for _, w in M.plan(rows, day_before)] == ["t2"]
M.send([r for r in rows if r["n"] == 1][0], "t2", "schedule")
rows = M.rows_for(*M.load(), at=day_before)
for r in rows: r["logs"] = M.load()[1]["1001-PR-WGL-120"]
assert M.plan(rows, day_before) == []
t2 = json.loads(DB["log"][-1]["detail"])["params"]
assert t2["date"] == "Thu, 12 Nov" and t2["slot"] == "07:00 AM - 08:00 AM"
# two reschedules allowed, the third is refused without calling Redcliffe
for d in ("2026-11-13", "2026-11-14"):
    with app.test_request_context("/b/" + tk, method="POST", data={"act": "resched", "d": d, "slot": "2|07:00 AM - 08:00 AM"}):
        html, st = M.booking_page(tk, flask.request)
    assert "Rescheduled" in html, html
s = state("1001-PR-WGL-120")
assert s["resched"] == 2 and s["booking"]["collection_date"] == "2026-11-14" and len(s["history"]) == 3
rows = M.rows_for(*M.load(), at=at_ist(2026, 11, 13, 12))
for r in rows: r["logs"] = M.load()[1]["1001-PR-WGL-120"]
assert [w for _, w in M.plan(rows, at_ist(2026, 11, 13, 12))] == ["t2"], "new date gets its own reminder"
n_upd = len([c for c in CALLS if "update-booking" in c[0]])
with app.test_request_context("/b/" + tk, method="POST", data={"act": "resched", "d": "2026-11-15", "slot": "2|07:00 AM - 08:00 AM"}):
    html, st = M.booking_page(tk, flask.request)
assert "used all 2 reschedules Sova allows" in html and len([c for c in CALLS if "update-booking" in c[0]]) == n_upd
# the reschedule screen: the booked slot, the earlier one, and how many moves are left
s1 = dict(s, resched=1, history=s["history"][:2], booking=s["history"][1])
page1 = ui.booking(s1, DB["programs"][0], "resched", [], "", [], "", "", M.MAX_RESCHEDULES)
assert "Sova allows only 2 reschedules for each test. You have 1 left: after this one, the time can" in page1 and page1.count("(moved)") == 1
assert "Friday, 13 Nov" in page1 and "Thursday, 12 Nov" in page1 and "Your booked slot" in page1, "current and earlier slot"
assert "You have 2 left." in ui.booking(dict(s, resched=0, history=s["history"][:1], booking=s["history"][0]), DB["programs"][0], "resched", [], "", [], "", "", 2)
ok, msg = M.reschedule(state("1001-PR-WGL-120"), "2026-11-15", "2", "x", "ops")
assert not ok and "already been rescheduled 2 times" in msg
# Redcliffe status: collected, then report
later = at_ist(2026, 11, 15)
fake_log(program_id="1001-PR-WGL-120", test_no=1, kind="rcl", status="Sample Collected", detail={}, at=later)
assert state("1001-PR-WGL-120", at=later)["status"] == "collected"
fake_log(program_id="1001-PR-WGL-120", test_no=1, kind="rcl", status="Report Generated", detail={"report_link": "https://r/x.pdf"}, at=later)
assert state("1001-PR-WGL-120", at=later)["status"] == "report"
assert state("1001-PR-WGL-120", at=at_ist(2026, 11, 15))["late"] is False

# test mode: a real customer's link can't book
reset()
prog(phone="919811111111")
tk = M.tok("1001-PR-WGL-120", 1)
with app.test_request_context("/b/" + tk, method="POST", data=addr):
    M.booking_page(tk, flask.request)
with app.test_request_context("/b/" + tk, method="POST", data={"act": "book", "d": "2026-11-12", "slot": "2|x", "age": "30", "gender": "male"}):
    html, st = M.booking_page(tk, flask.request)
assert "paused while the Sova team is testing" in html and not [c for c in CALLS if "create-booking" in c[0]]

# Redcliffe refuses -> error logged, shown in the Failed queue
reset()
prog()
M.http = lambda url, body=None, headers=None, method=None: (200, {"status": "failure", "message": "Location is non-serviceable."}) if "create-booking" in url else fake_http(url, body)
ok, msg = M.book(state("1001-PR-WGL-120"), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-12", "2", "x", "customer")
assert not ok and msg == "Location is non-serviceable." and state("1001-PR-WGL-120")["failed"]["note"].startswith("Booking failed")
M.http = fake_http
DB["cfg"]["package_codes"] = ""
ok, msg = M.book(state("1001-PR-WGL-120"), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-12", "2", "x", "customer")
assert not ok and "package" in msg

# address step: the phone's location wins (no map lookup); an unchanged address reuses the order's point; no coverage -> Sova's contact
reset(contact_phone="98765 43210", contact_email="care@sova.health")
prog()
tk = M.tok("1001-PR-WGL-120", 1)
with app.test_request_context("/b/" + tk, method="POST", data=dict(addr, house="Flat 4B, Tower 2", area="Sector 62", lat="28.535512", lng="77.391029", how="gps")):
    assert M.booking_page(tk, flask.request)[1] == 303
chk = json.loads(DB["log"][-1]["detail"])
assert (chk["lat"], chk["lng"], chk["located_by"]) == (28.535512, 77.391029, "gps") and not [c for c in CALLS if "eloc" in c[0]]
assert chk["address"] == "Flat 4B, Tower 2, Sector 62" and chk["house"] == "Flat 4B, Tower 2" and chk["area"] == "Sector 62", "house + area go to Redcliffe"
with app.test_request_context("/b/%s?step=address" % tk):
    html = M.booking_page(tk, flask.request)[0]
assert "value='Flat 4B, Tower 2'" in html and "value='Sector 62'" in html and "maps.googleapis.com" not in html, "no key: no map, the form still works"
os.environ["MAPS_KEY"] = "test-maps-key"
with app.test_request_context("/b/%s?step=address" % tk):
    html = M.booking_page(tk, flask.request)[0]
assert '"key": "test-maps-key"' in html and "<div id=sheet class=sheet hidden" in html and '"lat": 28.535512' in html and "Use my current location" in html
assert "maps.googleapis.com/maps/api/js?key='+encodeURIComponent" in html, "the map loads only when the sheet first opens"
with app.test_request_context("/b/%s?ops=1&step=address" % tk):
    html = M.booking_page(tk, flask.request, by="ops@sova.health")[0]
assert "id=map" in html and "Set location on map" in html and "Use my current location" not in html and '"staff": true' in html, "ops place the pin by hand"
del os.environ["MAPS_KEY"]
with app.test_request_context("/b/" + tk, method="POST", data=dict(addr, house="", lat="28.5", lng="77.3")):
    assert "flat / house no." in M.booking_page(tk, flask.request)[0]
with app.test_request_context("/b/" + tk, method="POST", data=dict(addr, house="Flat 4B, Tower 2", area="Sector 62", lat="0", lng="0")): # not in India: ignored
    M.booking_page(tk, flask.request)
assert json.loads(DB["log"][-1]["detail"])["located_by"] == "saved" and json.loads(DB["log"][-1]["detail"])["lat"] == 28.535512
with app.test_request_context("/b/" + tk, method="POST", data=dict(addr, house="44 Sector 18")):
    M.booking_page(tk, flask.request)
assert json.loads(DB["log"][-1]["detail"])["located_by"] == "pincode", "a new address is looked up again"
n_rows = len(DB["log"])
COVER.update(status="failure", message="We cannot fetch latitude and longitude for this address. eloc_status not available.")
with app.test_request_context("/b/" + tk, method="POST", data=addr):
    html, st = M.booking_page(tk, flask.request)
assert "find this address on the map" in html and len(DB["log"]) == n_rows
COVER.update(status="failure", message="Location is non-serviceable")
with app.test_request_context("/b/" + tk, method="POST", data=addr):
    assert M.booking_page(tk, flask.request)[1] == 303
assert json.loads(DB["log"][-1]["detail"])["serviceable"] is False
with app.test_request_context("/b/" + tk):
    html, st = M.booking_page(tk, flask.request)
assert "can't collect samples at this address" in html and "tel:+919876543210" in html and "https://wa.me/919876543210" in html
assert "mailto:care@sova.health" in html and "Change address" in html and "Pick a day" not in html
with app.test_request_context("/b/" + tk, method="POST", data={"act": "book", "d": "2026-11-12", "slot": "2|x", "age": "30", "gender": "male"}):
    assert "confirm your address first" in M.booking_page(tk, flask.request)[0] and not [c for c in CALLS if "create-booking" in c[0]]
with app.test_request_context("/b/%s?step=address" % tk):
    assert "1. Your address" in M.booking_page(tk, flask.request)[0]
COVER.update(status="success", message="Location is serviceable")
with app.test_request_context("/b/" + tk, method="POST", data=addr):
    M.booking_page(tk, flask.request)
M.http = lambda url, body=None, headers=None, method=None: (200, {"status": "success", "message": "0 slots are available for 2026-11-13", "results": []}) \
    if "time-slot" in url else fake_http(url, body)
with app.test_request_context("/b/%s?d=2026-11-13" % tk):
    html = M.booking_page(tk, flask.request)[0]
assert "No free slots on Friday, 13 Nov" in html and "tel:+919876543210" in html and "0 slots are available" not in html
M.http = fake_http
with app.test_request_context("/b/%s?ops=1" % tk):
    html = M.booking_page(tk, flask.request, by="ops@sova.health")[0]
assert "Use my current location" not in html, "ops booking for a customer: their own location would be wrong"
assert "Customer support contact" in ui.settings(DB["cfg"], {}, {"email": "a", "role": "admin"}, "", None, {}, "test", [], "h")
assert M.act({"a": "cfg", "k": "contact_phone", "v": "12345"}, {"email": "a", "role": "admin"})[0].startswith("Enter the support number")

# ---- Shopify sync, both stores: every SKU (catalogues, recent orders, the SovaX SKU list), programs of 3+ months only, POS orders too
assert M.guess_days("PR-WGL-120", "Coaching") == 120 and M.guess_days("SU-GHB-90", "Supplement") == 0 and M.guess_days("TE-GMT-001", "Coaching") == 0
assert M.guess_days("PR-WGL", "Coaching", "Gut x Weight Loss - 4 Months") == 120 and M.guess_days("SU-SXP-180", "", "", "in") == 0
assert M.guess_days("SU-SXP-180", "", "") == 0 and M.guess_days("SU-BGR-90", "Supplement", "90 Sachets (3 Months)") == 0, "supplements are never programs"
reset()
SECRETS.update({"adh-shopify-health-token": "shpat_health", "adh-shopify-access-token": "shpat_in\n"})
CATALOG_BOTH = True
SOVAX = [{"sku": "SU-SXP-180", "name": "Personalised Supplements - 6 months", "days": 180}, {"sku": "TE-GMT-001", "name": "Gut Microbiome Test", "days": 0}]
M.q = lambda sql, **k: [{"program_id": "OLD-PR-WGL-120"}] if "bt_programs" in sql else SOVAX if "sova_skus" in sql else []
CATALOG = {"sova.health": [{"title": "Gut x Weight Loss", "product_type": "Coaching", "variants": [{"sku": "PR-WGL-120", "title": "4 Months"}]},
                           {"title": "Gut Health Booster", "product_type": "Supplement", "variants": [{"sku": "SU-GHB-90", "title": "90 Sachets"}]},
                           {"title": "Short Reset", "product_type": "Coaching", "variants": [{"sku": "PR-SHT-060", "title": "2 Months"}]}],
           "sova-in": [{"title": "Personalised Probiotics", "product_type": "", "variants": [{"sku": "SU-SXQ-90", "title": "3 Months"}]},
                       {"title": "Gut x Weight Loss", "product_type": "Coaching", "variants": [{"sku": "PR-WGL-120", "title": "4 Months"}]}]}
M.http = lambda url, body=None, headers=None, method=None: (200, {"products": next((v for k, v in CATALOG.items() if k in url), [])}) if "page=1" in url else (
    (200, {"products": []}) if "products.json" in url else fake_http(url, body, headers, method))
ORDERS = {"health": [{"name": "#2001", "createdAt": "2026-11-10T09:00:00Z", "cancelledAt": None, "sourceName": "pos", "email": "b@x.in", "phone": None,
                      "customer": {"firstName": "Ravi", "lastName": "K", "phone": "+919822222222"}, "shippingAddress": None,
                      "billingAddress": {"address1": "Store pickup", "city": "Pune", "province": "MH", "zip": "411001"},
                      "lineItems": {"nodes": [{"sku": "PR-WGL-120", "title": "Gut x Weight Loss", "variantTitle": "4 Months"}, {"sku": "SU-GHB-90", "title": "Booster"},
                                              {"sku": "PR-SHT-060", "title": "Short Reset"}]}},
                     {"name": "#OLD", "createdAt": "2026-10-01T09:00:00Z", "cancelledAt": "2026-10-02T09:00:00Z", "lineItems": {"nodes": [{"sku": "pr-wgl-120"}]}}],
          "in": [{"name": "#RE5001", "createdAt": "2026-11-09T09:00:00Z", "cancelledAt": None, "sourceName": "web", "email": "c@x.in", "phone": "+919833333333",
                  "customer": {"firstName": "Meera", "lastName": "S"}, "shippingAddress": {"address1": "7 Lake Rd", "city": "Kolkata", "province": "WB", "zip": "700029"},
                  "lineItems": {"nodes": [{"sku": "SU-SXP-180", "title": "Personalised Supplements", "variantTitle": "6 months"}]}}]}
real_shopify = M.shopify
M.shopify = lambda query, variables=None, store="health": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                                                      "nodes": [{"lineItems": {"nodes": [{"sku": "POS-ONLY-1", "title": "Store kit"}]}}]}} \
    if query == M.RECENT_Q else {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": ORDERS[store]}}
assert M.sync_skus() == 7, DB["skus"].keys()
days = {k: v["days"] for k, v in DB["skus"].items()}
assert days == {"PR-WGL-120": 120, "SU-GHB-90": 0, "PR-SHT-060": 60, "SU-SXQ-90": 0, "POS-ONLY-1": 0, "SU-SXP-180": 0, "TE-GMT-001": 0}, days
assert DB["skus"]["PR-WGL-120"]["name"] == "Gut x Weight Loss - 4 Months" and DB["skus"]["PR-WGL-120"]["note"] == "sova.health, sova.in" and DB["skus"]["SU-SXQ-90"]["note"] == "sova.in"
assert M.sync_skus() == 0, "unchanged SKUs aren't written again"
assert M.sync_orders() == "sova.in: 0 new (1 orders read); sova.health: 1 new (2 orders read)", M.sync_orders()
p = [x for x in DB["programs"] if x["program_id"] == "2001-PR-WGL-120"][0]
assert p["phone"] == "919822222222" and p["source"] == "sova.health pos" and p["pincode"] == "411001" and p["days"] == 120 and p["customer_name"] == "Ravi K"
assert not [x for x in DB["programs"] if "PR-SHT-060" in x["program_id"]], "a 2-month program gets no blood tests"
assert not [x for x in DB["programs"] if x["program_id"] == "RE5001-SU-SXP-180"], "Sova X (SU-SX...) is a supplement, not a program"
assert [r["program_id"] for r in DB["log"] if r["kind"] == "closed"] == ["OLD-PR-WGL-120"]
assert M.shop_status("in") == "connected (SovaX desk)", M.shop_status("in")
SECRETS["adh-shopify-access-token"] = json.dumps({"access_token": "shpca_in2", "scope": "read_orders"}); M._sec.clear()
assert M.shop_token("in") == "shpca_in2", "the whole OAuth answer saved as the secret"
SECRETS["adh-shopify-access-token"] = "abc123"; M._sec.clear()
assert M.shop_status("in") == "not connected (its saved token is in a format we don't recognise)" and "abc123" not in M.shop_status("in")
SECRETS["adh-shopify-access-token"] = "shpat_in"; SECRETS["adh-shopify-health-token"] = "pending"; M._sec.clear()
calls, real_http, real_sleep = [], M.http, M.time.sleep
M.http, M.time.sleep = (lambda *x, **k: real_http(*x, **k) if "graphql" not in x[0] else calls.append(x[0]) or (
    (200, {"errors": [{"extensions": {"code": "THROTTLED"}}]}) if len(calls) == 1 else (200, {"data": {"orders": 1}})), lambda s: None)
assert real_shopify("{x}", store="in") == {"orders": 1} and len(calls) == 2 and "sova-in.myshopify.com" in calls[0], "a throttled page is tried again"
M.http, M.time.sleep = real_http, real_sleep
assert M.shop_status("health") == "not connected yet"
SECRETS.pop("adh-shopify-health-token"); M._sec.clear()
assert M.sync_orders().endswith("; sova.health not connected"), "a store without its Shopify app connected is skipped"

# go-live rule: orders from the go-live date get the whole flow, earlier ones only while their Test 2 is ahead; later runs read only changes
assert DB["cfg"]["live_from"] == "2026-11-11" and DB["cfg"]["orders_synced_in"], "the first sync sets the go-live date and remembers where it got to"
seen, orders_fake = [], M.shopify
OLD_NEW = [dict(ORDERS["health"][0], name=n, createdAt=c) for n, c in (("#3001", "2026-05-01T09:00:00Z"), ("#3002", "2026-09-15T09:00:00Z"),
                                                                       ("#3003", "2026-11-11T08:00:00Z"))]
M.shopify = lambda query, variables=None, store="health": seen.append(variables) or {
    "orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": OLD_NEW if store == "in" else []}}
assert M.sync_orders().startswith("sova.in: 2 new (3 orders read)")
assert seen[-1]["sort"] == "UPDATED_AT" and seen[-1]["q"].startswith("updated_at:>=2026-11-10 AND ("), seen[-1]
got = {x["program_id"] for x in DB["programs"]}
assert {"3002-PR-WGL-120", "3003-PR-WGL-120"} <= got and "3001-PR-WGL-120" not in got, "before go-live: only programs whose Test 2 is still ahead"
assert [n for n, *_ in M.tests_for(prog(at=dt.datetime(2026, 9, 15, 9, tzinfo=UTC)))] == [2], "bought before go-live: only Test 2"
assert [n for n, *_ in M.tests_for(prog())] == [1, 2], "from go-live: both tests"
assert [n for n, *_ in M.tests_for(dict(prog(at=dt.datetime(2026, 9, 15, 9, tzinfo=UTC)), sku="TEST"))] == [1, 2], "test profiles keep the whole flow"
M.shopify = orders_fake

# sova.health's own app: Shopify's client credentials grant, a 24-hour token fetched once and reused
admin = {"email": "ops@sova.health", "role": "admin"}
SECRETS["adh-shopify-health-token"] = "pending"; SECRETS[M.HEALTH_APP] = json.dumps({"client_id": "cid12345", "client_secret": "csec12345"})
M._sec.clear(); M._app.clear()
grants, base_http = [], M.http
ok_grant = lambda url, body=None, headers=None, method=None, form=False: grants.append((url, body, form)) or (
    200, {"access_token": "shpat_cc", "expires_in": 86399, "scope": "read_orders,read_all_orders,read_customers"}) \
    if url.endswith("/admin/oauth/access_token") else base_http(url, body, headers, method)
M.http = ok_grant
assert M.shop_token("health") == "shpat_cc" == M.shop_token("health") and len(grants) == 1, "the 24-hour token is fetched once, then reused"
assert grants[0][0] == "https://sova-health-6053.myshopify.com/admin/oauth/access_token" and grants[0][1]["grant_type"] == "client_credentials" and grants[0][2]
assert M.shop_status("health") == "connected (own app)"
M._app.clear()
M.http = lambda url, body=None, headers=None, method=None, form=False: (400, {"error": "invalid_client"}) if "oauth" in url else base_http(url, body, headers, method)
assert M.shop_token("health") == "" and M.shop_status("health") == "not connected (Shopify refused the store's app: 400 invalid_client)", M.shop_status("health")
before = SECRETS[M.HEALTH_APP]
assert M.act({"a": "shop_app", "cid": "newid12345", "cs": "shpss_newsecret"}, admin)[0].startswith("Shopify didn't accept that app for sova.health (400 invalid_client)")
assert SECRETS[M.HEALTH_APP] == before, "an app Shopify refuses is not saved"
M.http = ok_grant
msg = M.act({"a": "shop_app", "cid": "newid12345", "cs": "shpss_newsecret"}, admin)[0]
assert msg.startswith("sova.health app saved. Access granted: read_orders,read_all_orders,read_customers. "), msg
assert json.loads(SECRETS[M.HEALTH_APP]) == {"client_id": "newid12345", "client_secret": "shpss_newsecret"} and "shpss_newsecret" not in msg
assert M.shop_status("health") == "connected (own app)" and DB["cfg"]["shop_app_saved"].endswith("by ops@sova.health")
assert M.act({"a": "shop_app", "cid": "short", "cs": "x"}, admin)[0] == "Paste the app's Client ID and Client secret."
M.http = base_http

# going Live moves the go-live date to that day; the date can also be set by hand
mode_before = DB["cfg"].get("send_mode")
DB["cfg"]["live_from"], DB["cfg"]["send_mode"] = "2026-11-01", "test"
M.act({"a": "cfg", "k": "send_mode", "v": "live"}, admin)
assert DB["cfg"]["live_from"] == "2026-11-11" and DB["cfg"]["send_mode"] == "live", "going live: new orders start that day"
assert M.act({"a": "cfg", "k": "live_from", "v": "20-11-2026"}, admin)[0] == "Enter the date like 2026-11-20."
assert M.act({"a": "cfg", "k": "live_from", "v": "2026-11-20"}, admin)[0] == "Saved." and DB["cfg"]["live_from"] == "2026-11-20"
for k in ("live_from", "orders_synced_in", "orders_synced_health", "shop_app_saved", "send_mode"): DB["cfg"].pop(k, None)
if mode_before is not None: DB["cfg"]["send_mode"] = mode_before
SECRETS.pop(M.HEALTH_APP, None); SECRETS.pop("adh-shopify-health-token", None); M._sec.clear(); M._app.clear(); M._app_err.clear()
html = ui.settings(DB["cfg"], DB["skus"], {"email": "a", "role": "admin"}, "", None, {k: M.setting(k) for k in M.DEFAULT_T}, "test", [], "h", "",
                   {"env": "staging", "key": False, "stores": {"sova.in": "connected", "sova.health": "not connected yet"},
                    "apps": {"in": ("sova.in", "we6g0c-t1.myshopify.com", True, "h/shop-hook/in"), "health": ("sova.health", "sova-health-6053.myshopify.com", False, "h/shop-hook/health")}}, [])
real_sync_skus, M.sync_skus = M.sync_skus, lambda: 2
assert M.act({"a": "sync_skus"}, {"email": "ops@sova.health", "role": "admin"})[0] == "SKUs checked in both stores: 2 new or changed."
M.sync_skus = real_sync_skus
real_sync_orders, M.sync_orders = M.sync_orders, lambda: "sova.in: 1 new; sova.health: 2 new"
assert M.act({"a": "sync_orders"}, {"email": "ops@sova.health", "role": "admin"})[0] == "Orders checked. sova.in: 1 new; sova.health: 2 new."
M.sync_orders = real_sync_orders
assert "Get new orders now" in html and "value=sync_orders" in html
assert "value=shop_app" in html and "name=cs type=password" in html and "5. Go-live date" in html and "Not saved yet." in html
assert html.count("value=shop_app") == 2 and "name=store value=in" in html and "value=shop_hooks" in html and "h/shop-hook/health" in html and "t1_delay_days" in html
assert "Programs that get blood tests" in html and "Show the 1 program<" in html and "Check both stores now" in html and "PR-SHT-060" not in html and "days_" not in html
assert "sova.in <span class='chip ok'>connected" in html and "sova.health <span class='chip warn'>not connected yet" in html and "shopify_token" not in html
M.http = fake_http

# ---- dashboard actions and routes
reset()
prog()
M.load = fake_load
admin, viewer = {"email": "ops@sova.health", "role": "admin"}, {"email": "v@sova.health", "role": "view"}
assert M.act({"a": "followup", "pid": "1001-PR-WGL-120", "n": "1", "note": ""}, admin)[0].startswith("Write")
assert M.act({"a": "followup", "pid": "1001-PR-WGL-120", "n": "1", "note": "Called"}, viewer)[0].startswith("View-only")
assert M.act({"a": "followup", "pid": "1001-PR-WGL-120", "n": "1", "note": "Called"}, admin)[0] == "Follow-up saved."
assert M.act({"a": "send", "pid": "1001-PR-WGL-120", "n": "1"}, admin)[0] == "WhatsApp sent."
n_sent = len([c for c in CALLS if "send-template" in c[0]])
assert M.act({"a": "send", "pid": "1001-PR-WGL-120", "n": "1"}, admin)[0] == "Already sent at 11:30. To send it again, wait 10 minutes."
assert len([c for c in CALLS if "send-template" in c[0]]) == n_sent, "a double click sends once"
assert ui.msg_name({"note": "t1", "test_no": 2}) == "Template 1b (book Test 2)" and ui.msg_name({"note": "t1", "test_no": 1}) == "Template 1 (welcome + book)"
assert M.act({"a": "cfg", "k": "send_mode", "v": "loud"}, admin)[0].startswith("Pick")
DB["log"].append({"id": "b", "at": NOW, "program_id": "1001-PR-WGL-120", "test_no": 1, "kind": "booked", "status": None, "note": None,
                  "collection_date": "2026-11-14", "slot": "07:00 AM - 08:00 AM", "booking_id": "556", "by_user": "customer", "detail": "{}"})
n_before = len([c for c in CALLS if "send-template" in c[0]])
assert "can only go out on Fri, 13 Nov" in M.act({"a": "send", "pid": "1001-PR-WGL-120", "n": "1"}, admin)[0]
assert len([c for c in CALLS if "send-template" in c[0]]) == n_before, "no early 'tomorrow' reminder"
DB["log"].pop()
assert M.act({"a": "skus", "days_PR-WGL-120": "90"}, admin)[0] == "Unknown action.", "program lengths are automatic now"
msg, back = M.act({"a": "test_program", "name": "Me", "phone": "9876543210", "address": "1 Rd", "pincode": "110001", "days": "120", "ago": "101"}, admin)
assert back.startswith("p?id=TEST-") and "919876543210" in DB["cfg"]["test_numbers"]
tp = [x for x in DB["programs"] if x["program_id"].startswith("TEST-")][0]
assert state(tp["program_id"], 2)["status"] == "to_send", "a program started 101 days ago is in its Test 2 window"

with app.test_request_context("/tick"):
    assert M.app(flask.request)[1] == 403
with app.test_request_context("/b/bad.token"):
    assert M.app(flask.request)[1] == 404
with app.test_request_context("/"):
    r = M.app(flask.request)
    assert r[1] == 302 and r[2]["Location"].startswith("login?")
with app.test_request_context("/login", method="POST", data={"email": "sova", "password": "pw-setup", "next": "./"}):
    r = M.app(flask.request)
    assert r[1] == 302 and r[2]["Set-Cookie"].startswith("bt=")
    cookie = r[2]["Set-Cookie"].split(";")[0].split("=", 1)[1]
with app.test_request_context("/login", method="POST", data={"email": "sova", "password": "nope"}):
    assert M.app(flask.request)[1] == 401
for path in ("/", "/?s=todo", "/p?id=1001-PR-WGL-120", "/settings", "/export.csv"):
    with app.test_request_context(path, headers={"Cookie": "bt=" + cookie}):
        r = M.app(flask.request)
        assert r[1] == 200, (path, r[1])
tk = M.tok("1001-PR-WGL-120", 1)
DB["cfg"]["send_mode"] = "live"
with app.test_request_context("/b/%s?ops=1" % tk, method="POST", headers={"Cookie": "bt=" + cookie}, data=addr):
    assert M.app(flask.request)[2]["Location"] == "./%s?ops=1" % tk
with app.test_request_context("/b/%s?ops=1&d=2026-11-12" % tk, method="POST", headers={"Cookie": "bt=" + cookie},
                              data={"act": "book", "d": "2026-11-12", "slot": "2|x", "age": "40", "gender": "male"}):
    M.app(flask.request)
assert [r for r in DB["log"] if r["kind"] == "booked"][-1]["by_user"] == "sova", "booked on behalf by the logged-in user"
DB["cfg"]["send_mode"] = "test"
with app.test_request_context("/act", method="POST", headers={"Cookie": "bt=" + cookie, "Origin": "https://evil.example"}, data={"a": "cfg"}):
    assert M.app(flask.request)[1] == 403
with app.test_request_context("/", headers={"Cookie": "bt=" + cookie}):
    html = M.app(flask.request)[0]
assert "Needs follow-up" in html and "Asha" not in html and "asha rao" in html and "Test mode" in html and "WhatsApp on hold" not in html
DB["cfg"]["wa_hold"] = "bt_book_test"
with app.test_request_context("/", headers={"Cookie": "bt=" + cookie}):
    assert "WhatsApp on hold" in M.app(flask.request)[0]
# ops can ask Redcliffe for the status of a booking at any time
DB["log"].append({"id": "x", "at": NOW, "program_id": "1001-PR-WGL-120", "test_no": 1, "kind": "booked", "status": None, "note": None,
                  "collection_date": "2026-11-14", "slot": "07:00 AM - 08:00 AM", "booking_id": "555", "by_user": "customer", "detail": "{}"})
msg, _ = M.act({"a": "refresh", "pid": "1001-PR-WGL-120", "n": "1"}, admin)
assert msg == "Redcliffe says: Phlebo Assigned; no report yet" and state("1001-PR-WGL-120")["rcl_status"] == "Phlebo Assigned", msg
assert M.act({"a": "refresh", "pid": "1001-PR-WGL-120", "n": "1"}, admin)[0].endswith("(no change); no report yet")
M.http = lambda url, body=None, headers=None, method=None: (200, [{"pk": 555, "booking_id": 555, "booking_status": "sample collected", "pickup_status": "picked"}])
msg, _ = M.act({"a": "refresh", "pid": "1001-PR-WGL-120", "n": "1"}, admin) # staging shape: a bare list
assert msg == "Redcliffe says: sample collected · pickup picked; no report yet" and state("1001-PR-WGL-120")["status"] == "collected", msg
M.http = fake_http


# ---- Template 2: two link buttons. Yes -> /c/ (thank-you once per booking), Reschedule -> the booking page (opening it = "wants to reschedule")
def last_send(): return [c for c in CALLS if "send-template" in c[0]][-1][1]
def named(call): return {x["parameter_name"]: x["text"] for x in call["components"][0]["parameters"]}


reset(test_numbers="919900000001,919900000002")
prog(), prog(pid="1002-PR-WGL-120", phone="919900000002") # two customers
later = lambda h: NOW + dt.timedelta(hours=h)
for pid in ("1001-PR-WGL-120", "1002-PR-WGL-120"):
    assert M.book(state(pid), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-12", "2", "07:00 AM - 08:00 AM", "customer")[0]
    assert M.send(state(pid), "t2", "schedule") == "sent"
t2_call = last_send()
assert t2_call["templateName"] == "bt_reminder_links" and named(t2_call) == {"first_name": "Asha", "date": "Thu, 12 Nov", "slot": "07:00 AM - 08:00 AM"}
assert [(c["index"], M.untok(c["parameters"][0]["text"])) for c in t2_call["components"][1:]] == [("0", ("1002-PR-WGL-120", 1)), ("1", ("1002-PR-WGL-120", 1))]
M.now = lambda: later(1)
tk1 = M.tok("1001-PR-WGL-120", 1)
with app.test_request_context("/c/" + tk1):
    body, st, _ = M.app(flask.request)
assert st == 200 and "confirmed" in body and "Asha" not in body and "12 Nov" not in body, "no personal details on an open link"
assert last_send()["templateName"] == "bt_confirm_thanks" and named(last_send()) == {"first_name": "Asha", "date": "Thu, 12 Nov", "slot": "07:00 AM - 08:00 AM"}
assert state("1001-PR-WGL-120")["confirmed"] and not state("1002-PR-WGL-120")["confirmed"]
n_sends = len(CALLS)
with app.test_request_context("/c/" + tk1):
    assert M.app(flask.request)[1] == 200 and len(CALLS) == n_sends, "opening Yes again doesn't thank twice"
with app.test_request_context("/c/bad.link"):
    assert M.app(flask.request)[1] == 404
M.now = lambda: later(2)
tk2 = M.tok("1002-PR-WGL-120", 1)
for i in range(2):
    with app.test_request_context("/b/" + tk2):
        assert "Reschedule your blood test" in M.booking_page(tk2, flask.request)[0]
assert state("1002-PR-WGL-120")["ask"] == "resched" and len([r for r in DB["log"] if r["kind"] == "reply" and r["program_id"] == "1002-PR-WGL-120"]) == 1
with app.test_request_context("/b/" + tk2):
    M.booking_page(tk2, flask.request, by="ops@sova.health")
assert len([r for r in DB["log"] if r["kind"] == "reply" and r["program_id"] == "1002-PR-WGL-120"]) == 1, "ops opening it is not a customer request"
M.now = lambda: later(2.5)
assert M.reschedule(state("1002-PR-WGL-120"), "2026-11-14", "2", "07:00 AM - 08:00 AM", "customer")[0]
assert state("1002-PR-WGL-120")["ask"] is None, "the request closes once the booking moves"

# ops cancel: needs a reason, calls Redcliffe, frees the test to be booked again (with fresh reschedules)
M.now = lambda: later(4)
assert M.act({"a": "cancel", "pid": "1001-PR-WGL-120", "n": "1"}, admin)[0] == "Pick a reason to cancel."
assert M.act({"a": "cancel", "pid": "1001-PR-WGL-120", "n": "1", "reason": "Customer asked to cancel"}, viewer)[0].startswith("View-only")
msg, _ = M.act({"a": "cancel", "pid": "1001-PR-WGL-120", "n": "1", "reason": "Customer asked to cancel"}, admin)
assert msg == "Booking 555 cancelled in Redcliffe. Cancellation WhatsApp sent.", msg
cx = [c for c in CALLS if "send-template" in c[0]][-1][1]
assert cx["templateName"] == "bt_cancelled" and {p_["parameter_name"] for p_ in cx["components"][0]["parameters"]} == {"first_name", "date", "slot"} and \
    cx["components"][1]["parameters"][0]["text"] == M.tok("1001-PR-WGL-120", 1), "the customer hears which slot was cancelled, with a Book again link"
body = [c for c in CALLS if "update-booking" in c[0]][-1][1]
assert body["booking_status"] == "cancelled" and body["booking_id"] == 555 and "Customer asked to cancel" in body["remark"]
s1 = state("1001-PR-WGL-120")
assert s1["status"] == "cancelled" and s1["bookable"] and not s1["failed"] and not s1["confirmed"]
assert M.act({"a": "cancel", "pid": "1001-PR-WGL-120", "n": "1", "reason": "x"}, admin)[0].startswith("There's no active booking")
n_sends = len(CALLS)
with app.test_request_context("/c/" + tk1):
    assert "has changed" in M.app(flask.request)[0] and len(CALLS) == n_sends, "no thank-you for a cancelled visit"
M.http = lambda url, body=None, headers=None, method=None: (200, {"status": "failure", "message": "Phlebo already assigned"}) if "update-booking" in url else fake_http(url, body)
M.now = lambda: later(4.2)
s1["status"], s1["booking"] = "booked", {"booking_id": "555", "collection_date": "2026-11-14", "slot": "x"}
assert M.cancel(s1, "x", "ops")[1] == "Redcliffe did not cancel it: Phlebo already assigned" and state("1001-PR-WGL-120")["failed"]
M.http = fake_http
M.now = lambda: later(4.5)
# booked again on the customer's behalf from the profile: the booking page sends ops back to the profile
tk = M.tok("1001-PR-WGL-120", 1)
with app.test_request_context("/b/" + tk):
    html = M.booking_page(tk, flask.request)[0]
assert "Your previous booking" in html and "Cancelled" in html and "555" in html and "Thursday, 12 Nov" in html and "12 MG Road" in html
with app.test_request_context("/b/%s?ops=1" % tk, method="POST", data=addr):
    assert M.booking_page(tk, flask.request, by="ops@sova.health") == ("./%s?ops=1" % tk, 303)
with app.test_request_context("/b/" + tk, method="POST", data={"act": "book", "d": "2026-11-13", "slot": "2|x", "age": "30", "gender": "male"}):
    body, st = M.booking_page(tk, flask.request, by="ops@sova.health")
assert st == 303 and body.startswith("../p?id=1001-PR-WGL-120&m=Booked"), (st, body[:200])
s1 = state("1001-PR-WGL-120")
assert s1["status"] == "booked" and s1["resched"] == 0 and not s1["failed"]
with app.test_request_context("/b/" + tk):
    html, st = M.booking_page(tk, flask.request, by="ops@sova.health")
assert "Back to profile" in html and "ops@sova.health" in html
with app.test_request_context("/b/" + tk):
    assert "Back to profile" not in M.booking_page(tk, flask.request)[0]

# the dashboard and the profile show the request and the ops controls
M.now = lambda: later(5)
with app.test_request_context("/b/" + tk1):
    M.booking_page(tk1, flask.request)
for path, must in (("/?s=asked", ("Customer asked", "Wants to reschedule", "tel:+919900000001")),
                   ("/p?id=1001-PR-WGL-120", ("tapped \u201cReschedule\u201d", "Cancel booking in Redcliffe", ">Reschedule</a>", "Thank-you (after Yes)")),
                   ("/export.csv", ("Wants to reschedule",)), ("/settings", ("bt_report_link", "bt_welcome_book", "bt_book_cta", "bt_otp", "Authentication", "Go live (production)", "Team logins", "Test profiles"))):
    with app.test_request_context(path, headers={"Cookie": "bt=" + cookie}):
        out = M.app(flask.request)[0]
    for x in must: assert x in out, (path, x)

# ---- Redcliffe: collected -> "sample collected" once per booking; report ready -> the report PDF once
reset()
prog()
assert M.book(state("1001-PR-WGL-120"), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-11", "2", "07:00 AM - 08:00 AM", "customer")[0]


def due_now():
    progs, logs = M.load()
    rows = M.rows_for(progs, logs)
    for r in rows: r["logs"] = logs.get(r["program"]["program_id"], [])
    return [(t, w) for t, w in M.plan(rows) if t["n"] == 1]


assert due_now() == []
fake_log(program_id="1001-PR-WGL-120", test_no=1, kind="rcl", status="Sample Collected", booking_id="555", detail={})
assert [w for _, w in due_now()] == ["collected"]
assert M.send(due_now()[0][0], "collected", "schedule") == "sent" and last_send()["templateName"] == "bt_sample_collected"
assert named(last_send()) == {"first_name": "Asha"} and due_now() == [], "once per booking"
# the morning report run (job bt-reports) asks Redcliffe; a new report goes out as a View report button, never as the Redcliffe link
assert M.reports_run()["found"] == 0 and due_now() == [], "nothing until Redcliffe has the final report"
REPORTS["555"] = "https://reports.redcliffelabs.com/x.pdf"
CHATS["919900000001"] = NOW # the customer is talking to Sova about something else right now
r = M.reports_run()
assert r["checked"] == 1 and r["found"] == 1 and r["deferred"] == 1 and r["sent"] == 0, r
assert [w for _, w in due_now()] == ["report"], "it waits for the next run"
CHATS.clear()
assert M.reports_run()["sent"] == 1
rep_msg = last_send()
assert rep_msg["templateName"] == "bt_report_link" and named(rep_msg) == {"first_name": "Asha", "valid_till": "Fri, 11 Dec"}, named(rep_msg)
assert rep_msg["components"][1]["sub_type"] == "url" and M.untok(rep_msg["components"][1]["parameters"][0]["text"]) == ("r:1001-PR-WGL-120", 1)
assert "redcliffelabs" not in json.dumps(rep_msg), "the Redcliffe link never reaches WhatsApp"
assert due_now() == [] and M.reports_run()["sent"] == 0, "once per booking"
# the button: the Open / Download page, then the PDF from our address, with Redcliffe's latest link fetched at click time
tk = M.tok("r:1001-PR-WGL-120", 1)
for other in (M.tok("1001-PR-WGL-120", 1), M.tok("r:9999-NOPE", 1), "garbage"):
    with app.test_request_context("/r/" + other):
        assert M.report_page(other, flask.request)[1] == 404, "a booking or reminder link can't open the report"
real_fetch, M.fetch_pdf = M.fetch_pdf, lambda url: b"%PDF-1.4 report" if url == REPORTS["555"] else None
with app.test_request_context("/r/" + tk):
    body, st, hd = M.report_page(tk, flask.request)
assert st == 200 and "Open report" in body and "href='?file=download' download=" in body and "Fri, 11 Dec" in body and "Hi Asha" in body
assert not [r for r in DB["log"] if r["kind"] == "report_view"], "the page alone isn't an open"
with app.test_request_context("/r/%s?file=open" % tk):
    body, st, hd = M.report_page(tk, flask.request)
assert (body, st) == (REPORTS["555"], 303), "the customer goes to Redcliffe's own (latest) report link; our saved copy is never shared"
with app.test_request_context("/r/%s?file=download" % tk):
    body, st, hd = M.report_page(tk, flask.request)
assert (body, st) == (REPORTS["555"], 303)
with app.test_request_context("/r/%s?file=open" % tk):
    assert M.app(flask.request)[2]["Location"] == REPORTS["555"]
assert len([r for r in DB["log"] if r["kind"] == "report_view"]) == 1, "opened twice in a day = one timeline row"
M.now = lambda: NOW + dt.timedelta(days=31)
with app.test_request_context("/r/" + tk):
    body, st, hd = M.report_page(tk, flask.request)
assert st == 410 and "expired" in body
M.now, M.fetch_pdf = (lambda: NOW), real_fetch
assert M.fetch_pdf("http://insecure.example/x.pdf") is None and M.fetch_pdf(M.SAMPLE).startswith(b"%PDF")

# test profiles: Move to next stage plays Redcliffe's part (collected -> report ready -> Test 2 opens), each step sends its WhatsApp
reset()
prog(pid="TEST-ABC123")
DB["programs"][0]["source"] = "test"
assert M.next_stage(state("TEST-ABC123"), "ops@sova.health").startswith("Nothing to move yet")
assert M.book(state("TEST-ABC123"), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-12", "2", "07:00 AM - 08:00 AM", "customer")[0]
assert M.next_stage(state("TEST-ABC123"), "ops@sova.health") == "Moved to sample collected. WhatsApp sent." and last_send()["templateName"] == "bt_sample_collected"
assert M.next_stage(state("TEST-ABC123"), "ops@sova.health") == "Moved to report ready. WhatsApp sent." and last_send()["templateName"] == "bt_report_link"
assert state("TEST-ABC123")["report"] == M.SAMPLE
with app.test_request_context("/r/%s?file=open" % M.tok("r:TEST-ABC123", 1)):
    body, st, hd = M.report_page(M.tok("r:TEST-ABC123", 1), flask.request)
assert st == 200 and body.startswith(b"%PDF") and not [c for c in CALLS if "consolidated" in c[0]], "the sample is served from the package"
assert M.next_stage(state("TEST-ABC123"), "ops@sova.health").startswith("Test 2 is open now")
assert state("TEST-ABC123", 2)["status"] == "to_send" and state("TEST-ABC123")["status"] == "report"
M.send(state("TEST-ABC123", 2), "t1", "ops@sova.health")
assert last_send()["templateName"] == "bt_book_cta" and named(last_send()) == {"first_name": "Asha", "book_by": named(last_send())["book_by"]}, "Test 2: no welcome"
prog(pid="1009-PR-WGL-120")
assert M.next_stage(state("1009-PR-WGL-120"), "x").startswith("Only test profiles")
with app.test_request_context("/p?id=TEST-ABC123"):
    html = ui.program(*(lambda ps, ls: (ps[0], M.rows_for(ps, ls), ls["TEST-ABC123"]))(*M.load("TEST-ABC123")), {1: "x", 2: "y"}, admin, "", 2, "test")
assert "Next stage (test): open Test 2" not in html and "Moved on (test)" in html and "Report link WhatsApp sent" in html

# automatic WhatsApps wait while the customer is chatting with Sova (any topic); booking pages and confirmations never wait
reset()
prog()
stub = M.sync_skus, M.sync_orders
M.sync_skus = M.sync_orders = lambda: 0
CHATS["919900000001"] = NOW
r = M.tick()
assert r["deferred"] == 1 and r["sent"] == 0 and not [c for c in CALLS if "send-template" in c[0]], r
CHATS.clear()
assert M.tick()["sent"] == 1
M.sync_skus, M.sync_orders = stub

# Settings: production keys go to Secret Manager (never shown back); Redcliffe switches to production only with key + package code
reset()
prog()
assert M.act({"a": "secret", "k": "rcl_prod_key", "v": "short"}, admin)[0].startswith("That doesn't look like a key")
assert M.act({"a": "cfg", "k": "rcl_env", "v": "prod"}, admin)[0] == "Save the production API key and package code first."
assert M.act({"a": "secret", "k": "rcl_prod_key", "v": "prod-key-123456"}, admin)[0].startswith("Saved securely") and SECRETS[M.PROD_KEY] == "prod-key-123456"
assert "rcl_prod_key_saved" in DB["cfg"] and M.act({"a": "secret", "k": "nope", "v": "x" * 10}, admin)[0] == "Unknown setting."
DB["cfg"]["prod_package_codes"] = "PRODPKG"
assert M.act({"a": "cfg", "k": "rcl_env", "v": "prod"}, admin)[0] == "Saved." and M.prod()
CALLS.clear()
M.slots(28.6, 77.3, "2026-11-12")
assert CALLS[-1][0].startswith("https://api.redcliffelabs.com/api/booking/v2/get-time-slot-list/"), CALLS[-1][0]
assert M.book(state("1001-PR-WGL-120"), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-12", "2", "x", "customer")[0]
assert [c for c in CALLS if "create-booking" in c[0]][-1][1]["package_code"] == ["PRODPKG"]
html = ui.settings(DB["cfg"], {}, admin, "", None, {k: M.setting(k) for k in M.DEFAULT_T}, "test", [], "https://api.redcliffelabs.com", "",
                   {"env": "prod", "host": "h", "key": True, "stores": {}}, [])
assert "prod-key-123456" not in html and "Saved " in html and "Production (real home collections)" in html
assert M.act({"a": "cfg", "k": "quiet_minutes", "v": "abc"}, admin)[0].startswith("Enter a number")
DB["cfg"].pop("rcl_env")

# Team logins (shared with the SovaX desk): create, reset password (old sessions end), switch off/on
assert M.act({"a": "user_add", "email": "x@gmail.com", "role": "view", "password": "longpassword"}, admin)[0].startswith("Use a work email")
assert M.act({"a": "user_add", "email": "new@sova.health", "role": "view", "password": "short"}, admin)[0].startswith("Password needs")
assert M.act({"a": "user_add", "email": "new@sova.health", "role": "view", "password": "longpassword"}, admin)[0].startswith("Login created")
assert ACC["new@sova.health"]["role"] == "view" and M.check_pw("longpassword", ACC["new@sova.health"]["pw_hash"])
assert M.act({"a": "user_add", "email": "new@sova.health", "role": "admin", "password": "longpassword"}, admin)[0].endswith("use Reset password.")
old = ACC["new@sova.health"]["pw_hash"]
assert M.act({"a": "user_reset", "email": "new@sova.health", "password": "anotherpass1"}, admin)[0].startswith("Password reset")
assert ACC["new@sova.health"]["pw_hash"] != old and M.check_pw("anotherpass1", ACC["new@sova.health"]["pw_hash"])
assert M.act({"a": "user_off", "email": "new@sova.health"}, admin)[0].endswith("can no longer sign in.") and not ACC["new@sova.health"]["active"]
assert M.act({"a": "user_on", "email": "new@sova.health"}, admin)[0].endswith("can sign in again.")
assert M.act({"a": "user_off", "email": "ops@sova.health"}, admin)[0] == "No login for ops@sova.health."
assert M.act({"a": "user_add", "email": "z@sova.health", "role": "admin", "password": "longpassword"}, viewer)[0].startswith("View-only")
assert "Reset password" in ui.settings(DB["cfg"], {}, admin, "", None, {k: M.setting(k) for k in M.DEFAULT_T}, "test", [], "h", "", None, list(ACC.values()))
ACC.clear()

# ---- customers verify the number with a WhatsApp OTP before the booking page opens (signed-in ops skip it)
M.verified = real_verified
M._otp_sends.clear()
reset()
prog()
prog(pid="1003-PR-WGL-120", phone="919811111111")


def visit(path, method="GET", data=None, cookies=""):
    with app.test_request_context(path, method=method, data=data, headers={"Cookie": cookies} if cookies else {}):
        return M.app(flask.request)


def set_cookie(hd, name): return next(v for k, v in hd if k == "Set-Cookie" and v.startswith(name + "=")).split(";")[0]


tk = M.tok("1001-PR-WGL-120", 1)
r = visit("/b/" + tk)
assert r[1] == 303 and r[2]["Location"] == "../v/" + tk
body, st, hd = visit("/v/" + tk)
assert st == 200 and "registered number" in body and "\u20220001" in body and "name=phone" not in body, "the code only goes to the number on the order"
body, st, hd = visit("/v/" + M.tok("1003-PR-WGL-120", 1), "POST", {"act": "send"})
assert "paused while the Sova team is testing" in body and not CALLS, "test mode: only test numbers get codes"
body, st, hd = visit("/v/" + tk, "POST", {"act": "send", "phone": "9811111111"}) # a typed-in number is ignored
otp = last_send()
code = otp["components"][0]["parameters"][0]["text"]
assert otp["phoneNumber"] == "919900000001" and otp["templateName"] == "bt_otp"
assert otp["components"][1] == {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": code}]}
assert len(code) == 6 and code.isdigit() and "6-digit code" in body and "Send a new code" in body and "another number" not in body
oc = set_cookie(hd, "otp")
assert code not in oc, "the cookie never holds the code"
body, st, hd = visit("/v/" + tk, "POST", {"act": "check", "code": "%06d" % ((int(code) + 1) % 10 ** 6)}, oc)
assert "code isn" in body and "t right" in body
r = visit("/v/" + tk, "POST", {"act": "check", "code": code}, oc)
assert r[1] == 303 and ("Location", "../b/" + tk) in r[2]
cv = set_cookie(r[2], "cv")
body, st = visit("/b/" + tk, cookies=cv)[:2]
assert st == 200 and "1. Your address" in body and any(x["kind"] == "verified" for x in DB["log"])
assert visit("/b/" + tk, cookies="bt=" + cookie)[1] == 303, "a dashboard login alone doesn't skip the customer's OTP"
assert "Sova team: booking for" in visit("/b/%s?ops=1" % tk, cookies="bt=" + cookie)[0]
assert visit("/b/%s?ops=1" % tk)[1] == 303, "ops=1 without a login is just the customer's page"
assert "report isn&#x27;t ready yet" in visit("/r/" + M.tok("r:1001-PR-WGL-120", 1))[0], "the report link opens without a code"
assert visit("/r/" + tk)[1] == 404, "the booking link's code doesn't open the report page"
assert visit("/b/" + M.tok("1003-PR-WGL-120", 1), cookies=cv)[1] == 303, "verified for one number only"
assert visit("/b/" + tk, cookies="cv=" + cv[3:-1] + ("0" if cv[-1] != "0" else "1"))[1] == 303, "a tampered cookie is not trusted"
for i in range(5):
    body = visit("/v/" + tk, "POST", {"act": "check", "code": "%06d" % ((int(code) + 1) % 10 ** 6)}, oc)[0]
assert "Too many wrong codes" in body
assert "registered number" in visit("/v/" + tk, "POST", {"act": "check", "code": code}, oc)[0], "after 5 wrong tries even the right code needs a new send"
for i in range(2): visit("/v/" + tk, "POST", {"act": "send"})
assert "3 codes in the last 15 minutes" in visit("/v/" + tk, "POST", {"act": "send"})[0]

# ---- WhatsApp confirmations: after a booking, and after a reschedule (with the old time), whoever made it
reset()
prog()
assert M.book(state("1001-PR-WGL-120"), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-13", "2", "07:00 AM - 07:30 AM", "customer")[0]
assert last_send()["templateName"] == "bt_booked" and named(last_send()) == {"first_name": "Asha", "date": "Fri, 13 Nov", "slot": "07:00 AM - 07:30 AM"}
M.now = lambda: NOW + dt.timedelta(hours=1)
ok, msg = M.reschedule(state("1001-PR-WGL-120"), "2026-11-14", "3", "08:00 AM - 08:30 AM", "ops@sova.health")
assert ok and msg == "Moved from Fri, 13 Nov, 07:00 AM - 07:30 AM to Sat, 14 Nov, 08:00 AM - 08:30 AM.", msg
assert last_send()["templateName"] == "bt_booked" and named(last_send()) == {"first_name": "Asha", "date": "Sat, 14 Nov", "slot": "08:00 AM - 08:30 AM"}, \
    "a reschedule is confirmed with the booked template and the new slot"
assert [r["note"] for r in DB["log"] if r["kind"] == "msg"] == ["booked", "booked"] and all(r["booking_id"] == "555" for r in DB["log"] if r["kind"] == "msg")
M.now = lambda: NOW
tk = M.tok("1001-PR-WGL-120", 1)
with app.test_request_context("/b/" + tk):
    html = M.booking_page(tk, flask.request, by="ops@sova.health")[0] # same page a verified customer gets
assert "Your booked slot" in html and "Saturday, 14 Nov" in html and "08:00 AM - 08:30 AM" in html and "555" in html and "12 MG Road" in html
assert "<dt>Earlier slot</dt><dd><b>Friday, 13 Nov</b>, 07:00 AM - 07:30 AM <span class='m small'>(moved)</span>" in html, "the slot it was moved from"
assert "Sova allows only 2 reschedules for each test. You have 1 left" in html

# ---- 11:00 / 13:00 / 17:00 checks: Redcliffe status of upcoming (scheduled / rescheduled) bookings too, and a summary row for Analytics
reset()
prog()
M.http = fake_http
DB["log"].append({"id": "b1", "at": NOW, "program_id": "1001-PR-WGL-120", "test_no": 1, "kind": "booked", "status": None, "note": None,
                  "collection_date": "2026-11-14", "slot": "07:00 AM - 08:00 AM", "booking_id": "555", "by_user": "customer", "detail": "{}"})
r = M.reports_run()
assert r["bookings_checked"] == 1 and r["status_updates"] == 1 and r["checked"] == 0, r # upcoming: its status yes, no report yet
assert state("1001-PR-WGL-120")["rcl_status"] == "Phlebo Assigned" and state("1001-PR-WGL-120")["status"] == "booked"
chk = [x for x in DB["log"] if x["program_id"] == M.CHECKS]
assert len(chk) == 1 and chk[0]["kind"] == "check" and json.loads(chk[0]["detail"])["bookings_checked"] == 1
M.http = lambda url, body=None, headers=None, method=None, form=False: (200, {"data": [{"booking_id": 555, "booking_status": "Booking Confirmed",
    "pickup_status": "scheduled"}]}) if "center-get-booking" in url else fake_http(url, body, headers, method)
M.reports_run()
assert state("1001-PR-WGL-120")["status"] == "booked", "a scheduled pickup on an upcoming booking isn't a collected sample"
assert not [c for c in CALLS if "send-template" in c[0]], "nothing to send"
M.http = fake_http

# ---- Analytics: stages now; WhatsApps and responses within 48 hours (a link used, or a WhatsApp reply); bookings, reschedules, reports, checks
M.now = lambda: NOW + dt.timedelta(hours=1)
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="msg", note="t2", status="sent", by_user="schedule")
M.now = lambda: NOW + dt.timedelta(hours=2)
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="reply", note="yes", status="Yes", by_user="customer")
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="msg", note="booked", status="planned", by_user="schedule")
M.now = lambda: NOW
prog(pid="1002-PR-WGL-120", phone="919900000002")
M.log(program_id="1002-PR-WGL-120", test_no=1, kind="msg", note="t1", status="sent", by_user="schedule")
real_writes, M.wa_writes = M.wa_writes, lambda phones, since: {"9900000002": [NOW + dt.timedelta(hours=5)]} if "9900000002" in phones else {}
a = M.analytics_data(30)
assert a["programs"] == 2 and a["stages"][1]["booked"] == 1 and a["stages"][1]["awaiting"] == 1, a["stages"]
assert a["resp"]["t2"]["sent"] == 1 and a["resp"]["t2"]["yes"] == 1 and a["resp"]["t2"]["any"] == 1 and a["resp"]["t2"]["resched"] == 0
assert a["resp"]["t1"]["wrote"] == 1 and a["resp"]["t1"]["acted"] == 0 and a["resp"]["booked"]["planned"] == 1, a["resp"]
assert a["moves"]["booked"]["customer"] == 1 and a["used"] == {0: 1, 1: 0, 2: 0} and len(a["checks"]) == 2 and a["daily"][0][1] == 1
html = ui.analytics(a, admin, "test")
assert "Where every test is now" in html and "Day-before reminder" in html and "Status checks (11:00, 13:00, 17:00 IST)" in html
assert "<a href='./?s=booked'>1</a>" in html and "Template 2 (reminder)" in html and "100%" in html and "the last 30 days" in html
with app.test_request_context("/analytics"):
    assert M.app(flask.request)[1] == 302, "analytics needs a login"
with app.test_request_context("/analytics?days=7", headers={"Cookie": "bt=" + cookie}):
    body, st, _ = M.app(flask.request)
assert st == 200 and "<h1 class=m0>Analytics</h1>" in body and "the last 7 days" in body and "href='analytics'" in body
M.wa_writes = real_writes

# ---- security: 5 wrong passwords lock that account for 10 minutes, whatever address the caller claims
M._fails.clear()
for i in range(5):
    with app.test_request_context("/login", method="POST", data={"email": "sova", "password": "nope"}, headers={"X-Forwarded-For": "10.0.0.%d" % i}):
        assert M.app(flask.request)[1] == 401
with app.test_request_context("/login", method="POST", data={"email": "sova", "password": "pw-setup"}, headers={"X-Forwarded-For": "10.0.0.99"}):
    assert M.app(flask.request)[1] == 429, "a made-up address doesn't reset the count"
M._fails.clear()
os.environ["DASH_PASSWORD"] = " "
assert M.check_login("sova", " ") is None and M.check_login("sova", "") is None, "an empty setup password logs nobody in"
os.environ["DASH_PASSWORD"] = "pw-setup"
assert M.check_login("sova", "pw-setup")["role"] == "admin"
# the CSV export never hands Sheets / Excel a formula from an order's name
DB["programs"][-1]["customer_name"] = '=HYPERLINK("http://x.example")'
out = ui.export_csv(M.dashboard_data())
assert "'=HYPERLINK" in out and ",=HYPERLINK" not in out and ',"=HYPERLINK' not in out

# ---- a check that reaches its sends while the hourly run (:10) is sending leaves them to it: nothing goes out twice
M.http = lambda url, body=None, headers=None, method=None, form=False: (200, {"data": [{"booking_id": 555, "booking_status": "sample collected"}]}) \
    if "center-get-booking" in url else fake_http(url, body, headers, method)
CALLS.clear()
M.now = lambda: at_ist(2026, 11, 13, 11) + dt.timedelta(minutes=9)
r = M.reports_run()
assert r["waiting"] == 1 and r["sent"] == 0 and not [c for c in CALLS if "send-template" in c[0]], r
M.now = lambda: at_ist(2026, 11, 13, 11) + dt.timedelta(minutes=30)
assert M.reports_run()["sent"] == 1 and last_send()["templateName"] == "bt_sample_collected"
M.now, M.http = (lambda: NOW), fake_http

# ---- Redcliffe webhooks: every type lands on the right test at once; reschedules move the booking; reports and samples go out on WhatsApp
reset()
prog()
M.http = fake_http
assert M.book(state("1001-PR-WGL-120"), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-13", "2", "07:00 AM - 08:00 AM", "customer")[0]
real_q = M.q
M.q = lambda sql, **k: [{"program_id": r["program_id"], "test_no": r["test_no"]} for r in reversed(DB["log"])
                        if r.get("booking_id") == k.get("b") and r["kind"] in ("booked", "rescheduled")][:1] if "booking_id = @b" in sql else []
key = M.hook_key()


def hook(payload, raw=None, k=None):
    with app.test_request_context("/rcl-hook/" + (k or key), method="POST", data=raw if raw is not None else json.dumps(payload), content_type="application/json"):
        r = M.app(flask.request)
        return r[1], (json.loads(r[0])["result"] if r[1] == 200 and r[0] != "ok" else r[0])


ev = lambda kind, status, day="2026-11-13", **x: {"data": {"booking_id": 555, "type": "status_updated", "data": {
    "customer_status": status, "sample_collection_date": day, "start_time": "07:00:00", **x}}, "webhook_type": kind}
assert hook(ev("rescheduled", "rescheduled", "2026-11-13", ref_booking_start_time="07:00:00", ref_booking_end_time="08:00:00")) == (200, "same slot"), "our own move"
assert hook(ev("rescheduled", "rescheduled", "2026-11-14", ref_booking_start_time="09:00:00", ref_booking_end_time="10:00:00")) == (
    200, "moved to 2026-11-14, 09:00 AM - 10:00 AM"), "moved at Redcliffe's end"
s = state("1001-PR-WGL-120")
assert s["booking"]["collection_date"] == "2026-11-14" and s["booking"]["slot"] == "09:00 AM - 10:00 AM" and s["resched"] == 1
assert last_send()["templateName"] == "bt_booked" and named(last_send()) == {"first_name": "Asha", "date": "Sat, 14 Nov", "slot": "09:00 AM - 10:00 AM"}, \
    "the customer hears the new time (booked template)"
assert hook(ev("phleboassigned", "phlebo assigned", "2026-11-14", tracking_link="https://redcliffelabs.com/plb/x")) == (200, "logged")
assert json.loads([x for x in DB["log"] if x["kind"] == "rcl"][-1]["detail"])["tracking_link"] == "https://redcliffelabs.com/plb/x"
assert state("1001-PR-WGL-120")["tracking"] == "https://redcliffelabs.com/plb/x", "ops see the phlebo's live location"
progs_, logs_ = M.load("1001-PR-WGL-120")
assert "Track phlebo</a>" in ui.program(progs_[0], M.rows_for(progs_, logs_, NOW), logs_["1001-PR-WGL-120"], {1: "b", 2: "b"}, admin, "", 2, "test")
assert hook({"data": {"booking_id": 555, "data": {"booking_status": "5"}}, "webhook_type": "phlebo_started_journey"}) == (200, "logged")
assert state("1001-PR-WGL-120")["status"] == "booked" and state("1001-PR-WGL-120")["rcl_status"] == "Phlebo on the way"
assert hook(ev("pickup", "sample collected", "2026-11-14")) == (200, "logged; WhatsApp sent") and last_send()["templateName"] == "bt_sample_collected"
assert state("1001-PR-WGL-120")["status"] == "collected" and hook(ev("pickup", "sample collected", "2026-11-14")) == (200, "no change"), "repeats change nothing"
rep_url = "https://report.redcliffelabs.com/PDF/20261114_Plain/abc_Header.pdf"
assert hook({"id": 555, "name": "Asha", "report_url": rep_url, "webhook_type": "partialreport"}) == (200, "logged") and state("1001-PR-WGL-120")["report"] is None
assert hook({"id": 555, "name": "Asha", "report_url": rep_url, "webhook_type": "consolidatereport"}) == (200, "logged; WhatsApp sent")
assert last_send()["templateName"] == "bt_report_link" and state("1001-PR-WGL-120")["report"] == rep_url
assert hook({"id": 555, "name": "Asha", "report_url": rep_url, "webhook_type": "consolidatereport"}) == (200, "no change"), "a resent report adds nothing"
assert "Track phlebo" not in ui.program(*(lambda p, l: (p[0], M.rows_for(p, l, NOW), l["1001-PR-WGL-120"]))(*M.load("1001-PR-WGL-120")), {1: "b", 2: "b"}, admin, "", 2, "test"), \
    "no tracking once the sample is collected"
assert hook(ev("samplesync", "sample received", "2026-11-14")) == (200, "logged") and state("1001-PR-WGL-120")["report"] == rep_url, "a later status keeps the report"
assert hook({"id": 555, "report_url": "https://evil.example/r.pdf", "webhook_type": "consolidatereport"})[0] == 200
assert "report_link" not in json.loads([x for x in DB["log"] if x["kind"] == "rcl"][-1]["detail"]) and state("1001-PR-WGL-120")["report"] == rep_url, "Redcliffe links only"
assert len([c for c in CALLS if "send-template" in c[0] and c[1]["templateName"] == "bt_report_link"]) == 1, "the report WhatsApp went once"
assert hook({"id": 999, "webhook_type": "booking_created", "booking_status": "order booked"}) == (200, "not ours") and DB["log"][-1]["program_id"] == "_rcl"
assert hook(None, raw="{'id': 998, 'webhook_type': 'booking_created', 'reference_data': None}") == (200, "not ours"), "Python-style payloads too"
assert hook({"id": 1}, k="wrong")[0] == 403 and hook(None, raw="not json")[0] == 400
with app.test_request_context("/rcl-hook/" + key):
    assert M.app(flask.request) == ("ok", 200), "an address check by Redcliffe"
M.q = real_q

# ---- sova.health's own app key from Settings: Shopify confirms it can read orders before it's saved, and it's never shown back
tokv = "shpca_" + "0" * 32
M.http = lambda url, body=None, headers=None, method=None, form=False: (200, {"data": {"shop": {"myshopifyDomain": "sova-health-6053.myshopify.com"},
    "orders": {"nodes": []}}}) if "graphql" in url else fake_http(url, body, headers, method)
msg = M.act({"a": "shop_token", "v": tokv}, admin)[0]
assert msg == "sova.health connected: Shopify confirmed it can read orders." and json.loads(SECRETS[M.HEALTH_APP]) == {"access_token": tokv}
M._sec.clear(); M._app.clear()
assert M.shop_token("health") == tokv and M.shop_status("health") == "connected (own app)"
M.http = lambda url, body=None, headers=None, method=None, form=False: (200, {"errors": [{"message": "Access denied for orders field."}]}) \
    if "graphql" in url else fake_http(url, body, headers, method)
assert M.act({"a": "shop_token", "v": "shpat_" + "1" * 32}, admin)[0].startswith("Shopify didn't accept it") and json.loads(SECRETS[M.HEALTH_APP])["access_token"] == tokv
assert M.act({"a": "shop_token", "v": "hello"}, admin)[0].startswith("That doesn't look like")
M.http = fake_http
SECRETS.pop(M.HEALTH_APP); M._sec.clear(); M._app.clear()

# ---- Not booked: tick customers and send the booking WhatsApp to all of them at once (24-hour guard against double sends)
reset()
prog(), prog(pid="1002-PR-WGL-120", phone="919900000002"), prog(pid="1003-PR-WGL-120", phone="")
real_sleep, M.time.sleep = M.time.sleep, lambda s: None
html = ui.unbooked(M.dashboard_data(), {}, admin, "test")
assert "value='1001-PR-WGL-120|1'" in html and "value='1002-PR-WGL-120|1'" in html and "1003-PR-WGL-120" not in html and "Send booking WhatsApp to ticked" in html
pick = ["1001-PR-WGL-120|1", "1002-PR-WGL-120|1", "1003-PR-WGL-120|1"]
r = M.act({"a": "bulk_send", "t": pick}, admin)[0]
assert r.startswith("Booking WhatsApp: 1 sent, 1 planned (test mode), 0 failed, 0 held, 1 skipped"), r
assert M.act({"a": "bulk_send", "t": pick}, admin)[0].startswith("Booking WhatsApp: 0 sent, 0 planned (test mode), 0 failed, 0 held, 3 skipped"), "no double sends"
assert M.act({"a": "bulk_send"}, admin)[0] == "Tick the customers to message first."
M.time.sleep = real_sleep
with app.test_request_context("/unbooked?s=awaiting", headers={"Cookie": "bt=" + cookie}):
    body, st, _ = M.app(flask.request)
assert st == 200 and "<h1>Not booked yet</h1>" in body and "aria-current=page>Awaiting booking (2)" in body

# ---- one blood-test plan per order (its longest program), and one booking WhatsApp per person in a bulk send
two = [dict(program_id="RE1-SU-SX-U-90", order_name="#RE1", source="sova.in web", days=90), dict(program_id="RE1-SU-SX-U-180", order_name="#RE1", source="sova.in web", days=180),
       dict(program_id="RE2-PR-CGH-090", order_name="#RE2", source="sova.in web", days=90), dict(program_id="RE2-SU-SX-U-90", order_name="#RE2", source="sova.in web", days=90),
       dict(program_id="1-PR-WGL-120", order_name="#1", source="sova.health web", days=120), dict(program_id="TEST-X", order_name="TEST-X", source="test", days=120)]
assert [p["program_id"] for p in M.one_per_order(two)] == ["RE1-SU-SX-U-180", "RE2-SU-SX-U-90", "1-PR-WGL-120", "TEST-X"]
reset()
DB["skus"] = {"SU-SX-U-90": {"sku": "SU-SX-U-90", "days": 90}, "PR-CGH-090": {"sku": "PR-CGH-090", "days": 90}, "SU-SX-U-180": {"sku": "SU-SX-U-180", "days": 180}}
DB["cfg"]["live_from"] = "2026-11-01"
real_q, M.q = M.q, lambda sql, **k: []
SECRETS["adh-shopify-access-token"] = "shpat_in"; M._sec.clear()
order = dict(ORDERS["in"][0], name="#RE7001", createdAt="2026-11-10T09:00:00Z", lineItems={"nodes": [{"sku": "SU-SX-U-90", "title": "SovaX"},
                                                                                                    {"sku": "PR-CGH-090", "title": "Gut Heal"}]})
real_shopify, M.shopify = M.shopify, lambda query, variables=None, store="health": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                                                                              "nodes": [order] if store == "in" else []}}
assert M.sync_orders().startswith("sova.in: 1 new (1 orders read)") and [p["program_id"] for p in DB["programs"]] == ["RE7001-SU-SX-U-90"], "one plan per order"
M.q, M.shopify = real_q, real_shopify
SECRETS.clear(); M._sec.clear(); DB["cfg"].pop("live_from")
reset()
prog(), prog(pid="1004-PR-WGL-120", at=NOW - dt.timedelta(days=200)) # same person: an earlier plan (ended) and this one
real_sleep, M.time.sleep = M.time.sleep, lambda s: None
r = M.act({"a": "bulk_send", "t": ["1001-PR-WGL-120|1", "1004-PR-WGL-120|1"]}, admin)[0]
assert r.startswith("Booking WhatsApp: 1 sent, 0 planned (test mode), 0 failed, 0 held, 1 skipped"), r
M.time.sleep = real_sleep

# ---- refills: one plan per person. A program bought before the plan ends stretches it to the latest end: Test 2 moves, no second Test 1
reset()
prog(at=NOW - dt.timedelta(days=30), days=90) # day 30 of 90
prog(pid="1005-SU-SX-U-R-90", at=NOW, days=90) # the refill, bought today: the plan now ends on day 120
prog(pid="1006-PR-WGL-120", at=NOW - dt.timedelta(days=300)) # ended long ago: a plan of its own
ps, ls = M.load()
assert sorted((p["program_id"], p["days"], p["members"]) for p in ps) == [("1001-PR-WGL-120", 120, ["1001-PR-WGL-120", "1005-SU-SX-U-R-90"]),
                                                                          ("1006-PR-WGL-120", 120, ["1006-PR-WGL-120"])]
plan_ = next(p for p in ps if p["program_id"] == "1001-PR-WGL-120")
assert [x[0] for x in M.tests_for(plan_)] == [1, 2] and M.tests_for(plan_)[1][2] == (NOW - dt.timedelta(days=30)).astimezone(IST).date() + dt.timedelta(days=105)
assert M.load("1005-SU-SX-U-R-90")[0][0]["program_id"] == "1001-PR-WGL-120", "the refill's own id (a link, a webhook) finds the plan"
assert "+ refill #1005" in ui.program(plan_, M.rows_for([plan_], ls, NOW), ls["1001-PR-WGL-120"], {1: "b", 2: "b"}, admin, "", 2, "test")
M.log(program_id="1005-SU-SX-U-R-90", kind="closed", note="Order cancelled in Shopify", by_user="sync")
assert [p["days"] for p in M.load()[0] if p["program_id"] == "1001-PR-WGL-120"] == [90], "a cancelled refill gives the time back"
reset()
prog(at=NOW - dt.timedelta(days=103)) # Test 2 window open (due on day 105)
M.send(state("1001-PR-WGL-120", 2), "t1", "schedule")
assert state("1001-PR-WGL-120", 2)["status"] == "awaiting"
prog(pid="1007-SU-SX-U-R-90", at=NOW, days=90) # refill before the end: the plan runs to day 193
assert state("1001-PR-WGL-120", 2)["status"] == "upcoming" and state("1007-SU-SX-U-R-90", 2)["program"]["program_id"] == "1001-PR-WGL-120"
assert state("1001-PR-WGL-120", 2, NOW + dt.timedelta(days=74))["status"] == "to_send", "the new window gets a fresh booking WhatsApp"

# ---- day + 1: Test 1's booking WhatsApp goes the day after the order; not booked -> booking reminders on order day 3, 5 and 7 (2, 4, 6 days later)
reset()
DB["cfg"].pop("t1_delay_days")
p = prog()
assert M.tests_for(p)[0] == (1, dt.date(2026, 11, 12), dt.date(2026, 11, 18)), M.tests_for(p)
assert state("1001-PR-WGL-120")["status"] == "upcoming", "order day: nothing yet"
day_ = lambda d, h=11: at_ist(2026, 11, d, h)


def due_at(at):
    progs, logs = M.load()
    rows = M.rows_for(progs, logs, at)
    for x in rows: x["logs"] = logs.get(x["program"]["program_id"], [])
    return [(x["n"], w) for x, w in M.plan(rows, at)]


assert due_at(day_(12)) == [(1, "t1")], "order day + 1"
M.now = lambda: day_(12)
assert M.send(state("1001-PR-WGL-120", at=day_(12)), "t1", "schedule") == "sent"
assert due_at(day_(13)) == [] and due_at(day_(14)) == [(1, "nudge")] and due_at(day_(16)) == [(1, "nudge")] and due_at(day_(18)) == [(1, "nudge")]
assert due_at(day_(15)) == [] and due_at(day_(17)) == [] and due_at(day_(19)) == [], "only on order day 3, 5 and 7"
M.now = lambda: day_(14)
assert M.send(state("1001-PR-WGL-120", at=day_(14)), "nudge", "schedule") == "sent"
nudge = last_send()
assert nudge["templateName"] == "bt_book_reminder" and named(nudge) == {"first_name": "Asha", "book_by": "Wed, 18 Nov"} and \
    nudge["components"][1]["parameters"][0]["text"] == M.tok("1001-PR-WGL-120", 1), "the reminder carries the same booking link"
assert due_at(day_(14, 15)) == [], "one reminder per day"
assert state("1001-PR-WGL-120", at=day_(14))["nudges"] == 1
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="booked", booking_id="777", collection_date="2026-11-20", slot="07:00 AM - 07:30 AM", by_user="customer")
assert (1, "nudge") not in due_at(day_(16)), "booked: no more reminders"
M.now = lambda: NOW

# ---- personalised test: the team sends another package to one customer; it's its own plan with its own codes and WhatsApp
reset()
prog()
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="profile", by_user="customer", detail={"house": "Flat 4", "area": "Sector 62", "address": "Flat 4, Sector 62",
      "city": "Noida", "pincode": "201309", "lat": 28.62, "lng": 77.36, "age": 34, "gender": "female", "serviceable": True})
real_q, M.q = M.q, lambda sql, **k: []
assert M.act({"a": "custom_test", "pid": "1001-PR-WGL-120", "codes": "pl70; bad code!"}, admin)[0].startswith("Enter the Redcliffe package code(s)")
msg, back = M.act({"a": "custom_test", "pid": "1001-PR-WGL-120", "codes": "PL70, camp123", "name": "Advanced Thyroid Panel"}, admin)
assert msg == "Personalised test (Advanced Thyroid Panel: PL70, CAMP123) created. WhatsApp sent.", msg
cp = [x for x in DB["programs"] if x["sku"] == "CUSTOM"][0]
assert back == "p?id=" + cp["program_id"] and cp["source"] == "custom" and cp["phone"] == "919900000001" and cp["program_id"].startswith("1001-X")
sent_ = last_send()
assert sent_["templateName"] == "bt_custom_test" and named(sent_) == {"first_name": "Asha", "test_name": "Advanced Thyroid Panel", "book_by": "Wed, 18 Nov"}
assert sent_["components"][1]["parameters"][0]["text"] == M.tok(cp["program_id"], 1)
ps, ls = M.load()
assert sorted(len(p["members"]) for p in ps) == [1, 1], "a personalised test never merges into the program as a refill"
ct = state(cp["program_id"])
assert ct["status"] == "awaiting" and ct["custom"] and [n for n, *_ in M.tests_for(cp)] == [1]
assert state("1001-PR-WGL-120")["status"] == "to_send", "the program's own Test 1 is untouched"
prof_ = M.profile(cp, ls[cp["program_id"]])
assert prof_["package_codes"] == "PL70,CAMP123" and prof_["pincode"] == "201309", "codes and the known address go with it"
assert M.book(ct, {**prof_, "age": 34, "gender": "female"}, "2026-11-13", "2", "07:00 AM - 08:00 AM", "customer")[0]
assert [c for c in CALLS if "create-booking" in c[0]][-1][1]["package_code"] == ["PL70", "CAMP123"], "Redcliffe books exactly these codes"
assert M.act({"a": "send", "pid": cp["program_id"], "n": "1"}, admin)[0].startswith("The reminder says"), "booked: Send is the day-before reminder"
html = ui.program(ps[0] if ps[0]["sku"] != "CUSTOM" else ps[1], [], [], {}, admin, "", 2, "test", "", {"siblings": [cp], "codes": "PL70", "results": []})
assert "Book a different test" in html and "value=custom_test" in html and "Advanced Thyroid Panel" in html
assert "Your personalised blood test (Advanced Thyroid Panel)" in ui.booking(state(cp["program_id"]), prof_, "book", ["2026-11-13"], "", [], "", "", 2, step="slots")
M.q = real_q

# ---- SKUs: every SKU listed; the team maps a length; the sync never overrides it; mapping picks up everyone who bought it
reset()
DB["skus"].update({"PR-NEW-X": {"sku": "PR-NEW-X", "name": "Gut Reset Program", "product_type": "", "days": 0, "note": "sova.health", "by_user": "sync"},
                   "SU-GHB-90": {"sku": "SU-GHB-90", "name": "Gut Heal Biotic 3 Months", "product_type": "", "days": 0, "note": "sova.in", "by_user": "sync"},
                   "TEE": {"sku": "TEE", "name": "T-shirt", "product_type": "", "days": 0, "note": "sova.in", "by_user": "sync"}})
assert [s for s, r in DB["skus"].items() if M.needs_review(r)] == ["PR-NEW-X"], "looks like a program, no length: review (supplements never)"
real_set, real_sync = M.set_sku, M.sync_orders
M.set_sku = lambda sku, name, pt, days, note, by: DB["skus"].__setitem__(sku, {"sku": sku, "name": name, "product_type": pt, "days": days, "note": note, "by_user": by})
picked = []
M.sync_orders = lambda only=None: picked.append(only) or "sova.in: 0 new (0 orders read); sova.health: 3 new (3 orders read)"
assert M.act({"a": "sku_map", "sku": "PR-NEW-X", "days": "abc"}, admin)[0].startswith("Enter the program length")
msg = M.act({"a": "sku_map", "sku": "pr-new-x", "days": "120"}, admin)[0]
assert msg == "PR-NEW-X mapped to 120 days. Everyone who bought it: sova.in: 0 new (0 orders read); sova.health: 3 new (3 orders read).", msg
assert picked == [["PR-NEW-X"]] and DB["skus"]["PR-NEW-X"]["days"] == 120 and M.manual(DB["skus"]["PR-NEW-X"]) and not M.needs_review(DB["skus"]["PR-NEW-X"])
assert M.act({"a": "sku_map", "sku": "SU-GHB-90", "days": "0"}, admin)[0].startswith("SU-GHB-90 saved as not a program") and len(picked) == 1
assert M.act({"a": "sku_map", "sku": "NOPE", "days": "90"}, admin)[0] == "Unknown SKU NOPE."
M.sync_orders = real_sync
html = ui.skus(DB["skus"], {"s": ""}, admin, "test", "", M.MIN_DAYS, M.needs_review, M.manual)
assert "PR-NEW-X" in html and "T-shirt" in html and "value=sku_map" in html and "Mapped by the team (2)" in html
M.set_sku = real_set
# the hourly SKU sync keeps a length the team set
real_q2, M.q = M.q, lambda sql, **k: []
real_http2, M.http = M.http, lambda url, body=None, headers=None, method=None, form=False: (200, {"products": [{"title": "Gut Reset Program", "product_type": "",
    "variants": [{"sku": "PR-NEW-X", "title": "Default Title"}]}]}) if "products.json" in url and "page=1" in url else (200, {"products": []})
before = dict(DB["skus"]["PR-NEW-X"])
M.sync_skus()
assert DB["skus"]["PR-NEW-X"] == before, "a mapped SKU is never overwritten by the sync"
M.q, M.http = real_q2, real_http2

# ---- Shopify order webhooks: signed with the store app's secret; a program order lands at once (Test 1 the next day); cancelled closes it
reset()
DB["skus"].update({"PR-WGL-120": {"sku": "PR-WGL-120", "name": "Gut x Weight Loss", "days": 120, "by_user": "sync"}})
DB["cfg"]["live_from"] = "2026-11-01"
DB["cfg"].pop("t1_delay_days")
SECRETS[M.APP_SECRET["in"]] = json.dumps({"client_id": "cid-in-123", "client_secret": "shpss_in_secret"}); M._sec.clear()
real_q3, M.q = M.q, lambda sql, **k: [{"program_id": "RE9001-PR-WGL-120"}] if "kind = 'closed'" not in sql and DB["programs"] else []
order_json = {"name": "#RE9001", "created_at": "2026-11-11T10:00:00+05:30", "source_name": "shopify_draft_order", "email": "neha@x.in", "phone": None,
              "customer": {"first_name": "Neha", "last_name": "S", "phone": "+91 98200 11111"}, "shipping_address": {"first_name": "Neha", "last_name": "S",
              "phone": "9820011111", "address1": "4 Hill Rd", "city": "Mumbai", "province": "MH", "zip": "400050"},
              "line_items": [{"sku": "PR-WGL-120", "title": "Gut x Weight Loss", "variant_title": "4 Months"}, {"sku": "TEE", "title": "T-shirt"}]}
raw = json.dumps(order_json).encode()
sig = base64.b64encode(M.hmac.new(b"shpss_in_secret", raw, M.hashlib.sha256).digest()).decode()
assert M.shop_hook("in", raw, "bad", "orders/create") == (401, "bad signature")
assert M.shop_hook("health", raw, sig, "orders/create") == (401, "the store's own app isn't saved in Settings")
st_, res_ = M.shop_hook("in", raw, sig, "orders/create")
assert (st_, res_) == (200, "added RE9001-PR-WGL-120 (Test 1 booking WhatsApp from Thu, 12 Nov)"), res_
np = DB["programs"][-1]
assert np["phone"] == "919820011111" and np["source"] == "sova.in shopify_draft_order" and np["customer_name"] == "Neha S" and np["pincode"] == "400050"
assert M.shop_hook("in", raw, sig, "orders/create")[1].startswith("orders/create: nothing to add"), "a repeat delivery adds nothing"
cancel_raw = json.dumps(dict(order_json, cancelled_at="2026-11-11T12:00:00+05:30")).encode()
csig = base64.b64encode(M.hmac.new(b"shpss_in_secret", cancel_raw, M.hashlib.sha256).digest()).decode()
assert M.shop_hook("in", cancel_raw, csig, "orders/cancelled")[0] == 200 and DB["log"][-1]["kind"] == "closed"
with app.test_request_context("/shop-hook/in", method="POST", data=raw, headers={"X-Shopify-Hmac-Sha256": sig, "X-Shopify-Topic": "orders/create"}):
    assert M.app(flask.request)[1] == 200
with app.test_request_context("/shop-hook/nowhere", method="POST", data=raw):
    assert M.app(flask.request)[1] == 404
M.q = real_q3
# registering the webhooks needs the store's app; each topic once
hooks_made = []
real_shop = M.shopify
M.shopify = lambda query, variables=None, store="health": {"webhookSubscriptions": {"nodes": [{"topic": "ORDERS_CREATE", "uri": "https://x/shop-hook/in"}]}} \
    if "webhookSubscriptions" in query else hooks_made.append((store, variables)) or {"webhookSubscriptionCreate": {"webhookSubscription": {"id": "1"}, "userErrors": []}}
assert M.register_hooks("https://x") == "sova.in: subscribed to orders/cancelled; sova.health: save its app's Client ID and secret first", M.register_hooks("https://x")
assert hooks_made[0] == ("in", {"t": "ORDERS_CANCELLED", "u": "https://x/shop-hook/in"})
M.shopify = real_shop
# the store's own app first, the SovaX desk's token when Shopify refuses the app (IP allowlist)
SECRETS["adh-shopify-access-token"] = "shpat_desk"; M._sec.clear(); M._app.clear(); M._blocked.clear()
seen_tok = []
M.http = lambda url, body=None, headers=None, method=None, form=False: (200, {"access_token": "shpat_app", "expires_in": 86399, "scope": "read_orders"}) \
    if url.endswith("/oauth/access_token") else (seen_tok.append(headers["X-Shopify-Access-Token"]) or ((403, {"errors": "IP not allowed"})
    if headers["X-Shopify-Access-Token"] == "shpat_app" else (200, {"data": {"ok": 1}}))) if "graphql" in url else fake_http(url, body, headers, method)
assert M.shop_tokens("in") == ["shpat_app", "shpat_desk"]
assert TRUE_SHOPIFY("{x}", store="in") == {"ok": 1} and seen_tok == ["shpat_app", "shpat_desk"], "blocked app -> desk token"
assert TRUE_SHOPIFY("{x}", store="in") == {"ok": 1} and seen_tok[-1] == "shpat_desk" and len(seen_tok) == 3, "the blocked app is skipped for an hour"
assert M.shop_status("in").startswith("own app blocked by Shopify (403")
M.http = fake_http
SECRETS.clear(); M._sec.clear(); M._app.clear(); M._blocked.clear(); DB["cfg"].pop("live_from")

# ---- results: each report's link, its biomarkers and its text are kept per test; exports show Test 1 and Test 2; legacy Test 1 = data expired
reset()
p = prog()
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="booked", booking_id="555", collection_date="2026-11-11", slot="07:00 AM - 08:00 AM", by_user="customer")
def results_q(sql, **k):
    if "report_pdf FROM" in sql: # a saved copy: by id, or the newest for a program's test
        hit = [r for r in reversed(DB["results"]) if r.get("report_pdf") and (r["id"] == k["i"] if "id = @i" in sql else
               r["program_id"] in k["p"].split("|") and str(r["test_no"]) == k["n"])]
        return hit[:1]
    if "LIMIT 1" in sql: return [r for r in DB["results"] if r["report_url"] == k.get("u")][:1]
    return [dict(r, has_pdf=bool(r.get("report_pdf"))) for r in reversed(DB["results"])]


real_q4, M.q = M.q, results_q
assert M.next_stage(state("1001-PR-WGL-120"), "ops@sova.health") == "Only test profiles can be moved on by hand." and not DB["results"]
p["source"] = "test"
assert M.next_stage(state("1001-PR-WGL-120"), "ops@sova.health").startswith("Moved to sample collected")
assert M.next_stage(state("1001-PR-WGL-120"), "ops@sova.health").startswith("Moved to report ready")
res = DB["results"]
assert len(res) == 1 and res[0]["test_no"] == 1 and res[0]["report_url"] == M.SAMPLE and res[0]["status"] == "ready", res
bio = json.loads(res[0]["biomarkers"])
assert {"name": "TSH", "value": "3.10", "unit": "uIU/mL", "range": "0.35-4.94", "flag": ""} in bio and \
    {"name": "Total Cholesterol", "value": "212", "unit": "mg/dL", "range": "<200", "flag": "H"} in bio, bio
assert "Redcliffe Labs" in res[0]["report_text"]
assert base64.b64decode(res[0]["report_pdf"]) == open(os.path.join(os.path.dirname(M.__file__), "sample-report.pdf"), "rb").read(), "a copy of the PDF itself is kept"
assert M.stored_pdf(res[0]["id"])[:4] == b"%PDF" and M.stored_pdf(pid="1001-PR-WGL-120", n=1)[:4] == b"%PDF" and M.stored_pdf("nope") is None
big, M.PDF_MAX = M.PDF_MAX, 10
assert M.save_result(p, 1, "556", "/sample-report.pdf?big=1", "test", "x").endswith("kept") and DB["results"][-1]["report_pdf"] is None and \
    "PDF too large to copy" in DB["results"][-1]["status"], "too big for a BigQuery row: the link and biomarkers are kept"
DB["results"].pop(); M.PDF_MAX = big
assert M.keep(state("1001-PR-WGL-120"), M.SAMPLE, "again", "x") == "kept already", "once per report link"
ps, ls = M.load()
rows_ = M.results_rows(ps, ls, M.results_for())
assert [(r["test"], r["biomarker"], r["value"], r["flag"]) for r in rows_ if r.get("biomarker") == "HDL Cholesterol"] == [(1, "HDL Cholesterol", "38", "L")]
assert [r for r in rows_ if r["test"] == 2][0]["status"] == "not open yet", "Test 2 is listed too, with where it stands"
csv_ = ui.csv_rows(rows_, M.RESULT_COLS)
assert csv_.startswith("customer,phone,email,order") and "HbA1c" in csv_
html = ui.program(ps[0], M.rows_for(ps, ls, NOW), ls["1001-PR-WGL-120"], {1: "b", 2: "b"}, admin, "", 2, "test", "",
                  {"results": M.results_for(), "legacy": False, "siblings": [], "codes": "PKG1"})
assert "Test results" in html and "Total Cholesterol" in html and "chip bad'>High" in html and "results.csv?id=1001-PR-WGL-120" in html
assert "pdf?id=%s" % res[0]["id"] in html and "Saved copy (PDF)" in html
assert {r["pdf_saved"] for r in rows_ if r.get("biomarker")} == {"yes"}
# a program bought before go-live: Test 1 was taken before tracking, no Redcliffe data
DB["cfg"]["live_from"] = "2026-11-20"
old = prog(pid="1009-PR-WGL-120", phone="919811112222")
assert M.legacy(old) and [n for n, *_ in M.tests_for(old)] == [2]
ps, ls = M.load("1009-PR-WGL-120")
lr = M.results_rows(ps, ls, [])
assert lr[0]["test"] == 1 and lr[0]["status"] == "test taken before tracking; data expired (no Redcliffe data)" and lr[1]["test"] == 2
assert "Data expired" in ui.program(ps[0], M.rows_for(ps, ls, NOW), [], {2: "b"}, admin, "", 2, "test", "", {"legacy": True, "live_from": "2026-11-20"})
DB["cfg"].pop("live_from")
with app.test_request_context("/results.csv?id=1001-PR-WGL-120", headers={"Cookie": "bt=" + M.make_session({"email": "sova", "fp": M.hashlib.sha256(b"pw-setup").hexdigest()[:16]})}):
    out = M.app(flask.request)
    assert out[1] == 200 and "HbA1c" in out[0] and "sova-test-results-1001-PR-WGL-120.csv" in out[2]["Content-Disposition"], out[:2]
ck = {"Cookie": "bt=" + M.make_session({"email": "sova", "fp": M.hashlib.sha256(b"pw-setup").hexdigest()[:16]})}
with app.test_request_context("/pdf?id=" + res[0]["id"], headers=ck):
    out = M.app(flask.request)
    assert out[1] == 200 and out[0][:4] == b"%PDF" and out[2]["Content-Type"] == "application/pdf", "the team opens the saved copy"
with app.test_request_context("/pdf?id=missing", headers=ck):
    assert M.app(flask.request)[1] == 404
with app.test_request_context("/pdf?id=" + res[0]["id"]):
    assert M.app(flask.request)[1] == 302, "signed-in team only"
# a real report: the customer is sent to Redcliffe's link, never to our saved copy
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="rcl", status="Report ready", booking_id="555", by_user="redcliffe",
      detail={"report_link": "https://report.redcliffelabs.com/r1.pdf"})
with app.test_request_context("/r/x?file=open"):
    body, st, hd = M.report_page(M.tok("r:1001-PR-WGL-120", 1), flask.request)
    assert (body, st) == ("https://report.redcliffelabs.com/r1.pdf", 303), (st, body[:80])
M.q = real_q4

# ---- analytics: any date range, and each insight's customers as CSV
reset()
prog(), prog(pid="1003-PR-WGL-120", phone="919811111111")
M.send(state("1001-PR-WGL-120"), "t1", "schedule")
assert M.period({"from": "2026-11-12", "to": "2026-11-01"}) == (dt.date(2026, 11, 1), dt.date(2026, 11, 12), None)
assert M.period({"days": "7"}) == (dt.date(2026, 11, 5), dt.date(2026, 11, 11), 7) and M.period({"from": "bad"})[2] == 30
a = M.analytics_data(dt.date(2026, 11, 1), dt.date(2026, 11, 11))
assert a["resp"]["t1"]["sent"] == 1 and a["start"] == dt.date(2026, 11, 1) and len(a["daily"]) == 11
assert M.analytics_data(dt.date(2026, 10, 1), dt.date(2026, 10, 31))["resp"] == {}, "outside the range: nothing"
ex = M.analytics_export("msg", "t1:sent", dt.date(2026, 11, 1), dt.date(2026, 11, 11))
assert [(r["customer"], r["test"], r["event"]) for r in ex] == [("asha rao", 1, "msg:t1")]
assert {r["phone"] for r in M.analytics_export("stage", "1:to_send", dt.date(2026, 11, 1), dt.date(2026, 11, 11))} == {"919811111111"}
assert len(M.analytics_export("all", "", dt.date(2026, 11, 1), dt.date(2026, 11, 11))) == 4
assert [r["phone"] for r in M.analytics_export("msg", "t1:none", dt.date(2026, 11, 1), dt.date(2026, 11, 11))] == ["919900000001"]
html = ui.analytics(a, admin, "test")
assert "type=date name=from value='2026-11-01'" in html and "analytics.csv?kind=msg&key=t1%3Aany&from=2026-11-01&to=2026-11-11" in html and "Export all tests" in html
assert "01 Nov 2026 to 11 Nov 2026" in html

# ---- location off / blocked on "Use my current location": the browser's own prompt first, then a Sova dialog with how to switch it on
reset()
prog()
html = ui.booking(state("1001-PR-WGL-120"), {**DB["programs"][0]}, "book", ["2026-11-13"], "", [], "", "", 2, step="address", maps_key="test-key")
assert "<dialog id=lochelp" in html and "id=lochelp-retry>Try again" in html and "id=lochelp-map>Pick on the map instead" in html
assert "locHelp(e&&e.code)" in html and "permissions.query({name:'geolocation'})" in html and "s.state==='denied')locHelp(1)" in html
assert "Allow location access" in html and "Turn on your location" in html and "width:calc(100% - 32px)" in html
assert "<dialog id=lochelp" not in ui.booking(state("1001-PR-WGL-120"), {**DB["programs"][0]}, "book", ["2026-11-13"], "", [], "", "", 2, step="address", maps_key=""), \
    "no map, no dialog (the plain form handles it)"

# ---- program dates from the profile (start + end): Test 2 follows the end (20 days before); Test 1 stays purchase + 1; both logged
reset()
DB["cfg"].pop("t1_delay_days")
p = prog() # bought 11 Nov, 120 days
assert M.tests_for(M.load()[0][0]) == [(1, dt.date(2026, 11, 12), dt.date(2026, 11, 18)), (2, dt.date(2027, 2, 19), dt.date(2027, 2, 24))]
assert M.act({"a": "start_date", "pid": "1001-PR-WGL-120", "date": "not-a-date"}, admin)[0] == "Pick a date."
assert M.act({"a": "start_date", "pid": "1001-PR-WGL-120", "date": "2029-01-01"}, admin)[0] == "Start date should be within a year of today."
assert M.act({"a": "start_date", "pid": "1001-PR-WGL-120", "date": "2026-12-01", "end": "2026-12-10"}, admin)[0].startswith("End date should be 30")
msg = M.act({"a": "start_date", "pid": "1001-PR-WGL-120", "date": "2026-12-01"}, admin)[0]
assert msg == "Dates saved: Tue, 01 Dec to Wed, 31 Mar. Test 2's booking WhatsApp goes on Thu, 11 Mar (20 days before the end). Test 1 is unchanged.", msg
ps, ls = M.load()
assert M.tests_for(ps[0]) == [(1, dt.date(2026, 11, 12), dt.date(2026, 11, 18)), (2, dt.date(2027, 3, 11), dt.date(2027, 3, 16))], "only Test 2 moves"
rows_ = [r for r in DB["log"] if r["kind"] in ("start", "end")]
assert [(r["kind"], r["note"]) for r in rows_] == [("start", "Program start Tue, 01 Dec (was Wed, 11 Nov)"), ("end", "Program end Wed, 31 Mar (was Thu, 11 Mar)")]
assert all(r["by_user"] == "ops@sova.health" and json.loads(r["detail"])["via"] == "profile" for r in rows_)
msg = M.act({"a": "start_date", "pid": "1001-PR-WGL-120", "date": "2026-12-01", "end": "2027-04-30"}, admin)[0]
assert msg.startswith("Dates saved: Tue, 01 Dec to Fri, 30 Apr. Test 2's booking WhatsApp goes on Sat, 10 Apr"), msg
assert [r["kind"] for r in DB["log"] if r["kind"] in ("start", "end")] == ["start", "end", "end"], "only what changed is logged"
assert M.act({"a": "start_date", "pid": "1001-PR-WGL-120", "date": "2026-12-01", "end": "2027-04-30"}, admin)[0] == "Those are already the dates. " \
    "Test 2's booking WhatsApp goes on Sat, 10 Apr (20 days before the end). Test 1 is unchanged."
ps, ls = M.load()
html = ui.program(ps[0], M.rows_for(ps, ls, NOW), ls["1001-PR-WGL-120"], {1: "b", 2: "b"}, admin, "", 2, "test")
assert "Update dates" in html and "name=date value='2026-12-01'" in html and "name=end value='2027-04-30'" in html and "Set by ops@sova.health" in html
assert "30 Apr 2027" in html and "Program start date changed (Test 2 moves)" in html and "Program end date changed (Test 2 moves)" in html
assert "Update dates" not in ui.program(ps[0], [], [], {}, {"email": "v", "role": "view"}, "", 2, "test"), "view-only logins can't change it"
# a booked Test 2 stays as it is
M.log(program_id="1001-PR-WGL-120", test_no=2, kind="booked", booking_id="901", collection_date="2027-04-12", slot="07:00 AM - 08:00 AM", by_user="customer")
assert M.act({"a": "start_date", "pid": "1001-PR-WGL-120", "date": "2026-12-05", "end": "2027-05-05"}, admin)[0].endswith(
    "Test 2 is already booked for Mon, 12 Apr; it stays as it is and won't be sent again.")
# back to the purchase date and the program's own length
assert M.act({"a": "start_date", "pid": "1001-PR-WGL-120", "date": "2026-11-11", "end": "2027-03-11"}, admin)[0].startswith("Dates saved: Wed, 11 Nov to Thu, 11 Mar.")
assert M.load()[0][0]["program_start"] is None and M.tests_for(M.load()[0][0])[1][1] == dt.date(2027, 2, 19)

# ---- dates CSV: order_id, start_date, end_date -> each program's dates; Test 2 never sent twice; sample CSV; logged on the profile
reset()
DB["cfg"].pop("t1_delay_days")
prog(), prog(pid="1003-PR-WGL-120", phone="919811111111"), prog(pid="1004-PR-WGL-120", phone="919822222222")
M.log(program_id="1003-PR-WGL-120", test_no=2, kind="msg", note="t1", status="sent", by_user="schedule") # Test 2's WhatsApp already went once
M.log(program_id="1004-PR-WGL-120", test_no=2, kind="booked", booking_id="77", collection_date="2027-02-20", slot="07:00 AM - 08:00 AM", by_user="customer")
csv_ = ("Order ID,Start Date,End Date\n#1001,2026-12-01,2027-03-31\n1003,01-12-2026,31/03/2027\n1004,2026-12-01,2027-03-31\n"
        "9999,2026-12-01,2027-03-31\n1001,2026-12-02,2027-04-01\n1003,soon,2027-03-31\n,,\n")
rows_, summ = M.dates_csv(csv_, "ops@sova.health")
res = {(r["row"], r["order"]): r["result"] for r in rows_}
assert res[(2, "1001")] == "Updated; Test 2 WhatsApp on Thu, 11 Mar", rows_
assert res[(3, "1003")] == "Updated; Test 2 WhatsApp on Thu, 11 Mar (sent before: reminders only)"
assert res[(4, "1004")] == "Updated; Test 2 already booked, it won't be sent again"
assert res[(5, "9999")].startswith("Order not found") and res[(6, "1001")].startswith("Skipped: this order is already") and res[(7, "1003")].startswith("Not a date")
assert summ == "3 orders updated, 0 unchanged, 3 not updated (see the table).", summ
assert len([r for r in DB["log"] if r["kind"] in ("start", "end") and json.loads(r["detail"])["via"] == "csv"]) == 6 and \
    "· dates CSV" in [r for r in DB["log"] if r["kind"] == "start"][0]["note"]
ps, ls = M.load()
s3 = next(x for x in M.rows_for(ps, ls, M.IST.localize(dt.datetime(2027, 3, 11, 11)) if hasattr(M.IST, "localize") else at_ist(2027, 3, 11)) if x["program"]["program_id"] == "1003-PR-WGL-120" and x["n"] == 2)
assert s3["status"] in ("awaiting", "followup") and s3["t1_at"], "the earlier Test 2 WhatsApp still counts: it's never sent twice"
t2_1001 = next(x for x in M.rows_for(ps, ls, at_ist(2027, 3, 11)) if x["program"]["program_id"] == "1001-PR-WGL-120" and x["n"] == 2)
assert t2_1001["status"] == "to_send" and t2_1001["opens"] == dt.date(2027, 3, 11)
assert next(x for x in M.rows_for(ps, ls, NOW) if x["program"]["program_id"] == "1001-PR-WGL-120" and x["n"] == 1)["opens"] == dt.date(2026, 11, 12), "Test 1 stays"
assert M.dates_csv("foo,bar\n1,2\n", "x")[1].startswith("The CSV needs the columns")
rows2, summ2 = M.dates_csv(csv_.split("\n")[0] + "\n#1001,2026-12-01,2027-03-31\n", "ops")
assert rows2[0]["result"] == "No change" and summ2.startswith("0 orders updated, 1 unchanged")
assert DB["cfg"]["dates_csv_last"].endswith("0 orders updated, 1 unchanged, 0 not updated (see the table).")
ck = {"Cookie": "bt=" + M.make_session({"email": "sova", "fp": M.hashlib.sha256(b"pw-setup").hexdigest()[:16]})}
with app.test_request_context("/dates/sample.csv", headers=ck):
    out = M.app(flask.request)
    assert out[1] == 200 and out[0].startswith("order_id,start_date,end_date") and "attachment" in out[2]["Content-Disposition"]
with app.test_request_context("/dates", method="POST", headers=ck, data={"file": (io.BytesIO(b"order_id,start_date,end_date\n1001,2026-12-03,2027-04-02\n"), "d.csv")},
                              content_type="multipart/form-data"):
    out = M.app(flask.request)
    assert out[1] == 200 and "1 order updated" in out[0] and "Updated; Test 2 WhatsApp on" in out[0], out[0][-600:]
with app.test_request_context("/dates", headers=ck):
    out = M.app(flask.request)
    assert out[1] == 200 and "Download sample CSV" in out[0] and "enctype=multipart/form-data" in out[0] and "Last upload:" in out[0]
assert "Upload dates (CSV)" in ui.dashboard(M.rows_for(ps, ls, NOW), {}, admin, "test")

# ---- booking follow-ups: clicks on the booking link, the profile's follow-up dropdown, and clicks / bookings per message in Analytics
reset()
DB["cfg"].pop("t1_delay_days")
prog()
day_ = lambda d, h=11: at_ist(2026, 11, d, h)
M.now = lambda: day_(12)
M.send(state("1001-PR-WGL-120", at=day_(12)), "t1", "schedule")
M.now = lambda: day_(12, 15)
tk = M.tok("1001-PR-WGL-120", 1)
assert visit("/b/" + tk)[1] == 303 and visit("/b/" + tk)[1] == 303
assert [r["kind"] for r in DB["log"]].count("click") == 1, "one click per 30 minutes"
M.now = lambda: day_(14)
msg = M.act({"a": "send", "pid": "1001-PR-WGL-120", "n": "1", "which": "nudge"}, admin)[0]
assert msg == "WhatsApp sent." and last_send()["templateName"] == "bt_book_reminder", msg
assert due_at(day_(14, 15)) == [], "the dropdown's reminder counts as that day's reminder"
M.now = lambda: day_(14, 16)
visit("/b/" + tk)
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="booked", booking_id="777", collection_date="2026-11-16", slot="07:00 AM - 08:00 AM", by_user="customer")
M.now = lambda: day_(15)
f = M.analytics_data(dt.date(2026, 11, 1), dt.date(2026, 11, 15))["funnel"]
assert f["first"] == {"sent": 1, "clicked": 1, "booked": 0} and f["r1"] == {"sent": 1, "clicked": 1, "booked": 1} and f["r2"]["sent"] == 0, f
assert [r["customer"] for r in M.analytics_export("funnel", "r1:booked", dt.date(2026, 11, 1), dt.date(2026, 11, 15))] == ["asha rao"]
assert M.analytics_export("funnel", "first:booked", dt.date(2026, 11, 1), dt.date(2026, 11, 15)) == []
html = ui.analytics(M.analytics_data(dt.date(2026, 11, 1), dt.date(2026, 11, 15)), admin, "test")
assert "Booking messages: clicks and bookings" in html and "Reminder 3, the last (day 7)" in html and "kind=funnel&key=r1%3Abooked" in html
ps, ls = M.load()
ph = ui.program(ps[0], M.rows_for(ps, ls, day_(13)), ls["1001-PR-WGL-120"], {1: "b", 2: "b"}, admin, "", 2, "test")
assert "Booking follow-ups (2)" in ph and "Reminder 1" in ph and "chip ok'>Booked" in ph and "Customer opened the booking link" in ph
st_ = M.rows_for(ps, {}, day_(12))
assert "<option value=nudge>" in ui.followup_form(dict(st_[0], custom=False), "p?id=x")
assert "Send follow-up" in ui.program(ps[0], st_, [], {1: "b", 2: "b"}, admin, "", 2, "test"), "the dropdown shows on a bookable test"
M.now = lambda: NOW

# ---- Redcliffe cancels a booking: the customer gets the cancellation WhatsApp (their date and slot + Book again), once
reset()
prog()
assert M.book(state("1001-PR-WGL-120"), {**DB["programs"][0], "age": 30, "gender": "male"}, "2026-11-13", "2", "07:00 AM - 08:00 AM", "customer")[0]
real_qc = M.q
M.q = lambda sql, **k: [{"program_id": r["program_id"], "test_no": r["test_no"]} for r in reversed(DB["log"])
                        if r.get("booking_id") == k.get("b") and r["kind"] in ("booked", "rescheduled")][:1] if "booking_id = @b" in sql else []
cev = {"data": {"booking_id": 555, "type": "status_updated", "data": {"customer_status": "cancelled", "sample_collection_date": "2026-11-13",
       "start_time": "07:00:00", "cancel_reason": "Cancelled at client end"}}, "webhook_type": "cancelled"}
assert hook(cev) == (200, "logged; WhatsApp sent"), hook
assert last_send()["templateName"] == "bt_cancelled" and named(last_send()) == {"first_name": "Asha", "date": "Fri, 13 Nov", "slot": "07:00 AM - 08:00 AM"}
assert hook(cev)[1] == "no change" and len([c for c in CALLS if "send-template" in c[0] and c[1]["templateName"] == "bt_cancelled"]) == 1, "once"
M.q = real_qc

# ---- Add user: a person linked to a buyer (parent ID); the buyer keeps their tests; the linked user gets personalised tests from their profile
reset()
prog() # the buyer (parent): asha rao, 919900000001 (a test number)
assert [p["program_id"] for p in M.find_buyers("asha")] == ["1001-PR-WGL-120"] and M.find_buyers("a") == []
assert M.act({"a": "add_user", "name": "Kamla Rao", "relation": "mother"}, admin)[0] == "Pick the buyer this person is linked to."
assert M.act({"a": "add_user", "link": "1001-PR-WGL-120", "name": "Kamla Rao", "relation": ""}, admin)[0] == "Pick the relation to the buyer."
assert M.act({"a": "add_user", "link": "1001-PR-WGL-120", "name": "Kamla Rao", "relation": "mother", "phone": "12345"}, admin)[0].startswith("Enter a valid 10-digit")
msg, back = M.act({"a": "add_user", "link": "1001-PR-WGL-120", "name": "Kamla Rao", "relation": "mother", "phone": "98111 22233", "age": "62",
                   "gender": "female", "notes": "Diabetic"}, admin)
assert msg == ("Kamla Rao added, linked to asha rao (mother, parent ID 1001-PR-WGL-120). The buyer's own blood tests stay as they are. Send Kamla a "
               "personalised test below; its WhatsApps go to their own number."), msg
lu = [p for p in DB["programs"] if p["source"] == "linked"][0]
assert back == "p?id=%s#other" % lu["program_id"] and lu["phone"] == "919811122233" and lu["order_name"] == "#1001" and lu["address"] == "12 MG Road"
assert state("1001-PR-WGL-120")["status"] == "to_send", "the buyer keeps their own blood tests"
ps, ls = M.load()
lp = next(p for p in ps if p["source"] == "linked")
assert M.tests_for(lp) == [] and M.rows_for([lp], ls, NOW) == [], "no program tests come to a linked user by themselves"
assert sorted(len(p["members"]) for p in ps) == [1, 1], "a linked user never merges into the buyer's plan"
info = M.linked_info(lp, ls[lu["program_id"]])
assert info["buyer"]["program_id"] == "1001-PR-WGL-120" and info["relation"] == "mother" and info["notes"] == "Diabetic"
bp = next(p for p in ps if p["program_id"] == "1001-PR-WGL-120")
binfo = M.linked_info(bp, ls["1001-PR-WGL-120"])
assert [u["name"] for u in binfo["users"]] == ["Kamla Rao"] and not binfo["moved"]
html = ui.program(lp, [], ls[lu["program_id"]], {}, admin, "", 2, "test", "", {"linked": info, "codes": "PL70"})
assert "Mother of <b>asha rao</b>" in html and "<p class=fk>Parent ID</p><p class=fv>1001-PR-WGL-120</p>" in html and "Open Asha's profile" in html
assert "it goes to their own number" in html and "Book a different test" in html and "+ Add linked user" not in html
html = ui.program(bp, M.rows_for([bp], ls, NOW), ls["1001-PR-WGL-120"], {1: "b", 2: "b"}, admin, "", 2, "test", "", {"linked": binfo})
assert "Kamla Rao" in html and "p?id=" + lu["program_id"] in html and "add-user?link=1001-PR-WGL-120" in html and "moved to them" not in html
# a personalised test from the linked user's profile: their own number, their own Redcliffe booking with these codes
real_qa, M.q = M.q, lambda sql, **k: []
msg = M.act({"a": "custom_test", "pid": lu["program_id"], "codes": "PL70", "name": "Thyroid Panel"}, admin)[0]
assert msg.startswith("Personalised test (Thyroid Panel: PL70) created. WhatsApp logged as planned"), msg # 919811122233 isn't a test number
ct = [p for p in DB["programs"] if p["source"] == "custom"][0]
assert ct["phone"] == "919811122233" and ct["customer_name"] == "Kamla Rao"
ps, ls = M.load(ct["program_id"])
ci = M.linked_info(ps[0], ls[ct["program_id"]])
assert ci["made_for"]["program_id"] == lu["program_id"] and ci["buyer"]["program_id"] == "1001-PR-WGL-120", "the test shows who it's for and the parent"
assert "Personalised test for Kamla Rao" in ui.program(ps[0], M.rows_for(ps, ls, NOW), ls[ct["program_id"]], {1: "b"}, admin, "", 2, "test", "", {"linked": ci})
cst = state(ct["program_id"])
assert M.book(cst, {**M.profile(ps[0], ls[ct["program_id"]]), "age": 62, "gender": "female", "lat": 28.6, "lng": 77.3}, "2026-11-13", "2", "07:00 AM - 08:00 AM", "ops")[0]
cb = [c for c in CALLS if "create-booking" in c[0]][-1][1]
assert cb["package_code"] == ["PL70"] and cb["customer_phonenumber"] == "9811122233" and cb["customer_name"] == "Kamla Rao"
# no number given: everything goes to the parent's number
msg = M.act({"a": "add_user", "link": "1001-PR-WGL-120", "name": "Ravi Rao", "relation": "other", "relation_other": "cousin"}, admin)[0]
assert msg.endswith("its WhatsApps go to the buyer's number (919900000001)."), msg
rv = [p for p in DB["programs"] if p["customer_name"] == "Ravi Rao"][0]
assert rv["phone"] == "919900000001"
CALLS.clear()
assert M.act({"a": "custom_test", "pid": rv["program_id"], "codes": "PL70"}, admin)[0].endswith("WhatsApp sent.")
assert last_send()["phoneNumber"] == "919900000001" and last_send()["templateName"] == "bt_custom_test", "the parent's number gets it"
rc = [p for p in DB["programs"] if p["source"] == "custom" and p["phone"] == "919900000001"][0]
assert M.load(rc["program_id"])[0][0]["program_id"] == rc["program_id"], "sharing the parent's number never mixes the plans"
assert [x["program_id"] for x in M.load("1001-PR-WGL-120")[0]] == ["1001-PR-WGL-120"]
M.q = real_qa
assert M.act({"a": "add_user", "link": lu["program_id"], "name": "X Y", "relation": "friend"}, admin)[0].startswith("Link to the buyer's own profile")
assert M.act({"a": "add_user", "link": "1001-PR-WGL-120", "name": "X Y", "relation": "friend"}, viewer)[0].startswith("View-only")
page = ui.add_user(M.find_buyers("asha"), "asha", [], admin, "test", "", M.RELATIONS)
assert "Link to this buyer" in page and "add-user?link=1001-PR-WGL-120" in page
page = ui.add_user([], "", [bp], admin, "test", "", M.RELATIONS)
assert "name=a value=add_user" in page and "<option value=aunty>Aunty</option>" in page and "Parent ID: <b>1001-PR-WGL-120</b>" in page
assert "name=move" not in page and "card stack adduser" in page
ps, ls = M.load()
assert "+ Add user" in ui.dashboard(M.rows_for(ps, ls, NOW), {}, admin, "test")

# an older 'handed' row (from the first Add user version) no longer stops the buyer's tests
reset()
prog()
M.log(program_id="1001-PR-WGL-120", kind="handed", note="Blood tests moved to X", by_user="ops")
assert state("1001-PR-WGL-120")["status"] == "to_send" and not M.linked_info(M.load()[0][0], DB["log"])["moved"]

# ---- legacy sheet + Blood Test dashboard (the sheet's Apps Script rules)
import legacy_bt as LB
assert LB.sheet_date("16 Jul 26") == "2026-07-16" and LB.sheet_date("13 June 2026 cx shared the reports") == "2026-06-13"
assert LB.sheet_date("30 Dec 1899") == "" and LB.sheet_date("Not Answered") == "" and LB.sheet_date("2026-7-3") == "2026-07-03" and LB.sheet_date("05/08/2026") == "2026-08-05"
assert LB.sheet_date("31 Feb 26") == "" and LB.order_key("#RE2255-1 ") == "RE2255-1"
assert [LB.card_key(x) for x in ("Report Shared/ Uploaded", "Blocked", "Spoken + Test Form Shared", "Need to Reschedule", "Denied by client", "International Client",
        "Program Refund", "No response", "")] == ["done", "booked", "inprogress", "followup", "noaction", "notserviceable", "noteligible", "blank", "blank"]
HDR = ["Blood Test", "Plan", "Order ID", "Email", "Paid at", "Lineitem name", "Program\nDuration", "Billing Name", "BillingPhone", "Alternate number",
       "Test 1 Status", "Remark", "Lab name", "Followup Status\n(Test 1)", "Follow-up 1", "Follow-up 2", "Follow-up 3", "Eligible for 2nd blood test ",
       "Test 2 Status", "Remark of Test 2", "Lab name", "Followup Status\n(Test 2)", "Follow-up 1", "Follow-up 2", "Follow-up 3", "Coach Name", "Program Status",
       "Program Start Date", "Program Start Month", "End Date", "End Month", "previous plan", "2nd done or not "]
def srow(order, plan="Fresh", dur="90", s1="", r1="", s2="", r2="", pstat="InPlan", start="1 Nov 26", end="30 Jan 27", prev="", done2="", name="Asha", phone="9900000001"):
    r = [""] * len(HDR)
    for i, v in ((1, plan), (2, order), (6, dur), (7, name), (8, phone), (10, s1), (11, r1), (12, "Redcliffe"), (14, "dnp1"), (18, s2), (19, r2), (20, "Healthians"),
                 (25, "Coach A"), (26, pstat), (27, start), (29, end), (31, prev), (32, done2)): r[i] = v
    return r
sheet = [HDR, srow("#A1", s1="Report Shared/ Uploaded", r1="5 Nov 26 report shared"), srow("#A2", plan="Repeat", prev="90", done2="Done"),
         srow("#A3", plan="Repeat", prev="60"), srow("#A4", dur="30"), srow("#A5", pstat="Dropout- Not Started Yet"), srow("#A1", s1="Report Shared/ Uploaded", r1="5 Nov 26 report shared"),
         srow("1001", name="Asha Rao", s1="No response"), [""] * len(HDR)]
leg, dups = LB.legacy_rows(sheet)
assert dups == 1 and len(leg) == 6, (dups, len(leg))
L = {r["order"]: r for r in leg}
assert L["#A1"]["b1_k"] == "done" and L["#A1"]["b1_done"] == "2026-11-05" and L["#A1"]["b1_due"] == "2026-11-01" and L["#A1"]["b2_due"] == "2027-01-15"
assert L["#A1"]["b1_fu"] == "dnp1" and L["#A1"]["b2_lab"] == "Healthians" and L["#A1"]["b1_lab"] == "Redcliffe" and L["#A1"]["coach"] == "Coach A"
assert not L["#A2"]["b1_el"] and L["#A3"]["b1_el"] and not L["#A4"]["b1_el"] and not L["#A4"]["b2_el"] and L["#A5"]["excluded"], "Apps Script eligibility"
reset()
prog() # order #1001, 120 days, bought 11 Nov 2026
M.log(program_id="1001-PR-WGL-120", kind="msg", status="sent", note="t1", test_no=1)
progs_, logs_ = M.load()
live_ = M.bt_live(progs_, logs_)
assert live_[0]["tests"][1]["status"] == "awaiting" and live_[0]["tests"][2]["status"] == "upcoming"
rows_, matched = LB.unify([dict(r) for r in leg], live_, "2026-11-11")
assert matched == 1 and len(rows_) == 6
asha = [r for r in rows_ if r["order"] == "1001"][0]
assert asha["src"] == "both" and asha["pid"] == "1001-PR-WGL-120" and asha["b1_k"] == "inprogress" and asha["b1_st"] == "Booking WhatsApp sent"
assert asha["b2_k"] == "blank" and asha["b2_st"] == "" and asha["coach"] == "Coach A", "a test our system hasn't acted on keeps the sheet's status"
rows_, matched = LB.unify([dict(r) for r in leg if r["order"] != "1001"], live_, "2026-11-11")
mine = [r for r in rows_ if r["src"] == "live"][0]
assert matched == 0 and mine["plan"] == "Repeat" and mine["b1_due"] == "2026-11-17" and mine["b2_due"] == "2027-02-24" and mine["pstat"] == "InPlan", "same phone in the sheet = repeat"
M.legacy_table, M.legacy_meta = (lambda sheet: sheet_ if sheet == LB.SHEET else []), (lambda: [{"sheet": "Blood Test", "n": 8, "at": "2026-11-19 10:00", "by_user": "sheet import"}])
sheet_ = sheet
d = M.bt_dashboard_data()
assert d["cols"][0] == "src" and len(d["rows"]) == 6 and d["meta"]["legacy_dups"] == 1 and d["meta"]["matched"] == 1 and d["meta"]["imported"]["Blood Test"][1] == 8
cookie = M.make_session({"email": "sova", "fp": M.hashlib.sha256(b"pw-setup").hexdigest()[:16]})
body, st, hd = visit("/bt", cookies="bt=" + cookie)
assert st == 200 and "Blood Test dashboard" in body and "bt.json" in body and "Specific day" in body and "data-basis=done" in body
body, st, hd = visit("/bt.json", cookies="bt=" + cookie)
assert st == 200 and json.loads(body)["meta"]["legacy_rows"] == 6
body, st, hd = visit("/legacy?sheet=Blood%20Test&q=asha", cookies="bt=" + cookie)
assert st == 200 and "Asha Rao" in body and ">8<" in body, "row numbers are the sheet's"
assert visit("/legacy-import/wrong?sheet=Blood%20Test", "POST")[1] == 403 and M.legacy_key() is None, "closed unless a key is set"
DB["cfg"]["legacy_import_key"] = "k" * 32
assert visit("/legacy-import/wrong?sheet=Blood%20Test", "POST")[1] == 403
STORED = []
M.legacy_store = lambda sheet, text, by, **k: (STORED.append((sheet, text, by)), (len(LB.parse_csv(text)) - 1, LB.parse_csv(text)[0]))[1]
with app.test_request_context("/legacy-import/%s?sheet=Blood%%20Test" % M.legacy_key(), method="POST", data="a,b\n1,2\n", content_type="text/plain"):
    r = M.app(flask.request)
assert r[1] == 200 and json.loads(r[0])["rows"] == 1 and STORED[-1][2] == "sheet import"
with app.test_request_context("/legacy-import/%s?sheet=Other" % M.legacy_key(), method="POST", data="a\n"):
    assert M.app(flask.request)[1] == 400, "only the blood-test tabs"
assert "Legacy Google Sheet" in ui.settings(DB["cfg"], {}, admin, "", None, {}, "test", [], "h", legacy=M.legacy_meta())

# ---- the fixes: cleaned statuses / labs, done-date fallback, issues, the old dashboard's other views, sync, repeat buyers' Test 1
assert LB.canon_status("report shared/uploaded ") == ("Report Shared/ Uploaded", True) and LB.canon_status("International client")[0] == "International Client"
assert LB.canon_status("0") == ("", True) and LB.canon_status("Denied by client/ Report upoaded")[0] == "Report Shared/ Uploaded" and LB.canon_status("hmm") == ("hmm", False)
assert LB.canon_lab("Redliffe") == ("Redcliffe", "Redliffe") and LB.canon_lab("Healthians, resampled") == ("Healthians", "Healthians, resampled")
assert LB.canon_lab("20 May 2026 call not picked") == ("", "20 May 2026 call not picked") and LB.canon_lab("Redcliffe") == ("Redcliffe", "")
r1 = srow("#B1", s1="Report Shared/ Uploaded", r1="report shared"); r1[14] = "done on 7 Nov 26"
r2 = srow("#B2", s1="Report Shared/ Uploaded", r1="", s2="report shared/uploaded", r2="", start="2 Nov 26"); r2[12] = "Redliffe"
r3 = srow("#B3", s1="weird status")
iss = []
rows3, _ = LB.legacy_rows([HDR, r1, r2, r3, r1], issues=iss)
B = {r["order"]: r for r in rows3}
assert B["#B1"]["b1_done"] == "2026-11-07" and B["#B1"]["b1_dq"] == "follow-up", "no date in the remark: the follow-up's date"
assert B["#B2"]["b1_done"] == "2026-11-02" and B["#B2"]["b1_dq"] == "approx" and B["#B2"]["b1_lab"] == "Redcliffe" and B["#B2"]["b2_k"] == "done"
assert B["#B3"]["b1_k"] == "blank" and B["#B3"]["b1_st"] == "weird status"
kinds = {i["issue"] for i in iss}
assert {"Duplicate row", "Unknown Test 1 status", "Test 1 done, no date", "Test 2 done, no date", "Test 1 lab column"} <= kinds, kinds
OPS_HDR = ["Month", "Plan", "Order ID", "Email", "Paid at", "Lineitem name", "Duration", "Billing Name", "Billing Phone", "Alt", "x", "y", "Remark", "Coach Name",
           "Program Status", "Program Start Date", "End Date", "DC1", "DC1 Status", "Dr. Consult 2", "DC2 Status", "Why not started", "DC2 Eligible or not?"]
def orow(**k):
    r = [""] * len(OPS_HDR)
    for i, v in ((0, "Jul 2025"), (1, "Fresh"), (2, "#9"), (4, "1 Jul 25"), (6, "120"), (7, "Ravi"), (8, "9811111111"), (12, "note"), (13, "Iqra"), (14, "InPlan"),
                 (15, "11 Jul 25"), (16, "7 Nov 25"), (17, "15 Aug 25"), (18, "Yes")): r[i] = v
    for kk, v in k.items(): r[OPS_HDR.index(kk)] = v
    return r
orows, months = LB.ops_data([OPS_HDR, orow(), orow(**{"Paid at": "3 Mar 25"}), orow(**{"Program Start Date": "Travelling", "Order ID": "#10"})])
assert len(orows) == 2 and months == ["Jul 2025"], (len(orows), months)
o = orows[0]
assert o["days_to_start"] == 10 and o["dc1_due_iso"] == "2025-08-10" and o["dc2_due_iso"] == "2025-10-23" and o["renewal_iso"] == "2025-10-28" and o["dc_eligible"]
assert o["dc1_date"] == "15 Aug 25" and o["start_date"] == "11 Jul 25" and o["remark"] == "note" and orows[1]["start_iso"] == "" and orows[1]["days_to_start"] is None
bt_old = LB.ops_bt_rows([dict(r) for r in rows_])
assert all("bt1_due_iso" in x and "bt2_status" in x for x in bt_old) and not any(x["order_id"] == "#A5" for x in bt_old), "programs the old dashboard left out stay out"
# the repeat rule: previous 90d+ plan's Test 2 done -> no Test 1 for the new plan, unless Test 1 is already under way
reset()
old_ = prog(pid="900-PR-WGL-90", days=90, at=NOW - dt.timedelta(days=120))
M.log(program_id="900-PR-WGL-90", kind="rcl", test_no=2, status="Report ready", detail={"report_link": "https://x/r.pdf"})
prog(pid="1001-PR-WGL-120")
assert [n for n, _, _ in M.tests_for(M.load("1001-PR-WGL-120")[0][0])] == [2], "repeat buyer: Test 2 only"
assert M.load("1001-PR-WGL-120")[0][0]["t1_covered"].endswith("(this desk)")
M.log(program_id="1001-PR-WGL-120", kind="msg", status="sent", note="t1", test_no=1)
assert [n for n, _, _ in M.tests_for(M.load("1001-PR-WGL-120")[0][0])] == [1, 2], "a Test 1 already sent goes on"
reset()
prog(pid="1001-PR-WGL-120")
M.legacy_t2_done = lambda: {"9900000001": ["2026-11-20"]}
assert M.load("1001-PR-WGL-120")[0][0]["t1_covered"].endswith("(legacy sheet)")
M.legacy_t2_done = lambda: {"9900000001": ["2027-03-01"]}
assert not M.load("1001-PR-WGL-120")[0][0].get("t1_covered"), "the sheet row of this same program doesn't count"
M.legacy_t2_done = lambda: {}
progs_, logs_ = M.load()
assert 1 in M.bt_live(progs_, logs_)[0]["tests"]
# the hourly sync: unchanged tabs are skipped
SH = {k: "a,b\n1,2\n" for k in LB.SYNC}
M.sheet_csv = lambda sheet: SH[sheet]
TYPES = []
M.sheet_values = lambda sheet, opts: [["a", "b"], [1, 2]]
M.legacy_store_types = lambda sheet, types, sha, by: TYPES.append((sheet, types))
STORED.clear()
res = M.legacy_sync()
assert sorted(s_ for s_, _ in TYPES) == sorted(LB.TYPED), "cell types kept for the tabs the dashboards compute from"
assert all(v == "1 rows" for k, v in res.items() if k != "program dates") and "program dates" in res and len(STORED) == len(LB.SYNC)
with app.test_request_context("/legacy-sync?token=test-token"):
    r = M.app(flask.request)
assert r[1] == 200 and DB["log"][-1]["kind"] == "legacy_sync"
assert visit("/legacy-sync?token=nope")[1] == 403
# the old dashboard page, served with our data
M.legacy_table = lambda sheet: {LB.SHEET: sheet_, LB.OPS: [OPS_HDR, orow(**{"Billing Name": "<b>x</b>"})],
                                M.OPS_HTML: [["html"], ["<html><head><title>t</title></head><body><div id=loginOverlay></div><script>function loadData(){}</script></body></html>"]]}.get(sheet, [])
for u_ in ("/ops", "/ops.json"):
    body, st, hd = visit(u_, cookies="bt=" + cookie)
    assert st == 302 and hd["Location"] == "bt", "Program ops removed: old links go to the BT dashboard"
ex = LB.ops_bt_exact(sheet)
assert len(ex) == 6 and ex[0]["bt2_due_iso"] == "2027-01-30" and ex[0]["bt1_done"] and not any(x["order_id"] == "#A5" for x in ex), "the old rows exactly"
assert visit("/legacy-typed?token=nope&sheet=Blood%20Test")[1] == 403
body, st, hd = visit("/legacy-issues", cookies="bt=" + cookie)
assert st == 200 and "Duplicate row" in body
assert "Sync now" in ui.settings(DB["cfg"], {}, admin, "", None, {}, "test", [], "h", legacy=[], sync={"at": NOW, "status": "ok", "detail": {"Blood Test": "unchanged"}})

# matching our programs to sheet rows: plan orders, '-1' split orders, same phone with the same end date
lv = [{"pid": "P1", "order": "#X9", "orders": ["X9", "A3"], "phone": "", "name": "a", "days": 90, "purchased": "2026-11-01", "start": "2026-11-02", "end": "2027-01-30", "tests": {}},
      {"pid": "P2", "order": "#Z7", "orders": ["Z7"], "phone": "919900000001", "name": "b", "days": 90, "purchased": "2026-10-20", "start": "2026-11-01", "end": "2027-01-25", "tests": {}}]
sp = [HDR, srow("#A3", phone="9800000000"), srow("#Q1-1", phone="9800000003")]
lg, _ = LB.legacy_rows(sp + [srow("#Q2", phone="9900000001")])
u, mt = LB.unify(lg, lv, "2026-11-20")
assert mt == 2 and len(u) == 3 and {r["pid"] for r in u if r["pid"]} == {"P1", "P2"}, [(r["order"], r["pid"]) for r in u]
lv2 = [dict(lv[0], pid="P3", order="#Q1", orders=["Q1"])]
u2, mt2 = LB.unify(LB.legacy_rows(sp)[0], lv2, "2026-11-20")
assert mt2 == 1 and [r["pid"] for r in u2 if r["order"] == "#Q1-1"] == ["P3"]

# a SKU marked "not a program" (Sova X): orders already pulled in drop out everywhere; mapping it again brings them back
reset()
prog()
sku_ = M.load()[0][0]["sku"]
M.load = fake_load
M.sku_table, keep_st = (lambda: {sku_: {"sku": sku_, "days": 0}}), M.sku_table
M._skc["v"] = None
assert M.load()[0] == [] and M.not_programs() == {sku_}
M.sku_table = lambda: {sku_: {"sku": sku_, "days": 120}}
M._skc["v"] = None
assert len(M.load()[0]) == 1
M.sku_table = keep_st; M._skc["v"] = None

assert visit("/resync-orders?token=nope", "POST")[1] == 403

# ---- cell types: text in a date column, blank date cells (30 Dec 1899), number-0 cells — as the old Apps Script read them
shown = [["Month", "Plan", "Order ID", "Email", "Paid at", "Lineitem name", "Duration", "Billing Name", "Billing Phone", "Program Start Date", "End Date", "DC1"],
         ["Jan 2025", "Fresh", "#1", "", "2025-01-03 13:50", "Gut", "90", "Text paid", "98", "5 Jun 25", "30 Dec 99", "0"],
         ["Jul 2025", "Fresh", "#2", "", "3 Jul 25", "Gut", "120", "Date paid", "97", "Not Answered", "29 Apr 00", ""],
         ["Jul 2025", "0", "#3", "", "4 Jul 25", "Gut", "90", "Zero plan", "96", "9 Jul 25", "7 Oct 25", ""]]
unf = [shown[0], [45658, "Fresh", "#1", "", "2025-01-03 13:50", "Gut", 90, "Text paid", 98, 45813, 0, 0],
       [45839, "Fresh", "#2", "", 45841, "Gut", 120, "Date paid", 97, "Not Answered", 120, ""],
       [45839, 0, "#3", "", 45842, "Gut", 90, "Zero plan", 96, 45847, 45937, ""]]
ty = LB.types_from(shown, unf)
assert ty[1]["d"] == {"0": 45658, "9": 45813, "10": 0} and ty[1]["z"] == [11] and ty[3]["z"] == [1] and "4" not in ty[1]["d"], ty
o_t, m_t = LB.ops_data(shown, ty)
o_n, _ = LB.ops_data(shown)
names = [r["name"] for r in o_t]
assert names == ["Text paid", "Date paid", "Zero plan"], "a text 'Paid at' is no date: kept, as the old dashboard kept it"
assert [r["name"] for r in o_n] == ["Date paid", "Zero plan"], "without types the text looks like a January date (the bug found)"
tp = o_t[0]
assert tp["paid_at"] == "" and tp["start_iso"] == "2025-06-05" and tp["end_iso"] == "" and tp["dc2_due_iso"] == "" and tp["dc1_date"] == "", "blank date cell = no date"
assert o_t[1]["start_iso"] == "" and o_t[1]["end_iso"] == "" and o_t[1]["days_to_start"] is None and o_t[2]["plan"] == "" and o_t[2]["end_iso"] == "2025-10-07"

# ---- every sheet person in the panel: legacy rows in the overview (never counted or queued), profiles, lab / platform
assert LB.platform("", "cx shared the reports") == "Customer's own lab" and LB.platform("Healthians", "") == "Healthians" and LB.platform("", "dnp1") == ""
assert LB.other_platform("Healthians") and not LB.other_platform("Redcliffe") and not LB.other_platform("")
assert M.guess_days("SU-SX-U-90", "", "Sova X 3 Months", "in") == 0, "Sova X is never guessed as a program"
reset()
prog() # our program: order #1001, phone 9900000001
ppl = [HDR, srow("#H1", s1="Report Shared/ Uploaded", r1="5 Nov 26", s2="Report Shared/ Uploaded", r2="1 Jan 27", name="Hema", phone="9811111111"),
       srow("#H2", s1="Report Shared/ Uploaded", r1="cx shared the reports", name="Own Lab", phone="9822222222"), srow("1001", name="Asha Rao")]
ppl[1][12] = "Redcliffe"; ppl[1][20] = "Healthians"; ppl[2][12] = ""
M.legacy_table = lambda sheet: ppl if sheet == LB.SHEET else []
M.load = fake_load; M._lp["v"] = None
rows_all = M.dashboard_data()
sh = [t_ for t_ in rows_all if t_["status"] == "sheet"]
assert {t_["program"]["program_id"] for t_ in sh} == {"S-H1", "S-H2"}, "1001 is ours (matched), the other two are sheet-only people"
assert {(t_["program"]["program_id"], t_["n"]): (t_["platform"], t_["other_platform"]) for t_ in sh}[("S-H1", 2)] == ("Healthians", True)
assert {(t_["program"]["program_id"], t_["n"]): t_["platform"] for t_ in sh}[("S-H2", 1)] == "Customer's own lab"
html_all = ui.dashboard(rows_all, {}, admin, "test")
html_sheet = ui.dashboard(rows_all, {"s": "sheet"}, admin, "test")
html_other = ui.dashboard(rows_all, {"s": "other"}, admin, "test")
assert "Hema" not in html_all and "Hema" in html_sheet and "Own Lab" in html_sheet and "Other platform" in html_other and "Hema" in html_other
assert "Hema" in ui.dashboard(rows_all, {"q": "hema"}, admin, "test"), "search finds sheet people"
csv_all = ui.export_csv(rows_all)
assert "Hema" in csv_all and "S-H1" in csv_all or "#H1" in csv_all, "the export has the legacy people too"
body, st, hd = visit("/?s=sheet", cookies="bt=" + cookie)
assert st == 200 and "Hema" in body
body, st, hd = visit("/", cookies="bt=" + cookie)
assert st == 200 and "Hema" not in body
assert ">1<" in html_all.split("Active programs")[0] or "Active programs" in html_all
body, st, hd = visit("/p?id=S-H1", cookies="bt=" + cookie)
assert st == 200 and "Legacy (sheet)" in body and "Healthians" in body and "Other platform" in body and "Hema" in body
assert visit("/p?id=S-NOPE", cookies="bt=" + cookie)[1] == 404
body, st, hd = visit("/p?id=1001-PR-WGL-120", cookies="bt=" + cookie)
assert st == 200 and "From the legacy sheet" in body and "sheet row 4" in body, "our program shows its sheet history"

# ---- Send a WhatsApp: any template to any number, with a customer's details or example values
M.http = fake_http
reset()
prog() # 1001-PR-WGL-120, Asha, phone 919900000001
for key, which, n in M.MANUAL:
    tpl, vals, btns, p_, n_ = M.manual_message(key)
    assert tpl == DB["cfg"].get(key, M.DEFAULT_T[key]) and p_ is None and "first_name" in vals, (key, vals)
    assert ("date" in vals and "slot" in vals) == (which in ("t2", "thanks", "booked", "cancelled")), (key, vals)
    assert len(btns) == {"t2": 2, "thanks": 0, "booked": 0, "collected": 0}.get(which, 1), (key, len(btns))
tpl, vals, btns, p_, n_ = M.manual_message("t1_template", "#1001")
assert p_["program_id"] == "1001-PR-WGL-120" and vals["first_name"] == "Asha" and btns[0]["parameters"][0]["text"] == M.tok("1001-PR-WGL-120", 1), "the customer's own link"
assert M.manual_message("t1_template", "9900000001")[3]["program_id"] == "1001-PR-WGL-120" and M.manual_message("t1_next_template", "1001-PR-WGL-120")[4] == 2
assert M.manual_message("booked_template", "", "Ravi")[1]["first_name"] == "Ravi" and M.manual_message("t1_template", "#NOPE")[3] is None
try: M.manual_send("98765 43210", "report_template", {}, "#1001", "ops"); assert False, "a customer's report link must not go to another number"
except ValueError as e_: assert "own number" in str(e_)
CALLS.clear()
assert M.manual_send("9900000001", "report_template", {}, "#1001", "ops")[0] and [c for c in CALLS if "send-template" in c[0]], "their own number is fine"
assert M.manual_send("98765 43210", "booked_template", {}, "#1001", "ops")[0], "no buttons: any number"
CALLS.clear()
ok_, res_, tpl_ = M.manual_send("98765 43210", "booked_template", {"slot": "09:00 AM - 10:00 AM"}, "", "ops@sova.health")
sent_ = [c for c in CALLS if "send-template" in c[0]][-1][1]
assert ok_ and sent_["phoneNumber"] == "919876543210" and sent_["templateName"] == DB["cfg"].get("booked_template", "bt_booked")
assert {p_["parameter_name"]: p_["text"] for p_ in sent_["components"][0]["parameters"]}["slot"] == "09:00 AM - 10:00 AM", "an edited value is sent"
lg = DB["log"][-1]
assert lg["kind"] == "msg_manual" and lg["status"] == "sent" and lg["program_id"] == "_manual" and M.jd(lg)["phone"] == "919876543210"
try: M.manual_send("12", "booked_template", {}, "", "x"); assert False
except ValueError: pass
real_bs = M.briza_send
M.briza_send = lambda ph, tp, comp: (False, {"success": False, "error": "TEMPLATE_NOT_FOUND"})
ok_, res_, tpl_ = M.manual_send("9900000001", "t2_template", {}, "#1001", "ops")
assert not ok_ and DB["log"][-1]["status"] == "failed" and DB["log"][-1]["program_id"] == "1001-PR-WGL-120"
M.briza_send = real_bs
html_ = ui.send_page(M.MANUAL, "t1_template", *M.manual_message("t1_template", "#1001"), {"phone": "9876543210", "ref": "#1001"}, "", None,
                     [dict(r, at=NOW) for r in DB["log"] if r["kind"] == "msg_manual"], admin, "test")
assert "Send a WhatsApp" in html_ and "Asha" in html_ and "using asha" in html_.lower() and "Send to 9876543210" in html_ and "TEMPLATE_NOT_FOUND" in html_
body, st, hd = visit("/send?phone=9876543210&template=report_template&ref=%231001", cookies="bt=" + cookie)
assert st == 200 and "valid_till" in body and "report page" in body
assert visit("/manual-send?token=nope", "POST")[1] == 403

# the header: every page reachable, no Program ops, downloads in a menu, sign-out
hdr = ui.frame(admin, "test", "<p>x</p>")
for href in ("'./'", "'bt'", "'unbooked'", "'send'", "'analytics'", "'legacy'", "'skus'", "'settings'", "'export.csv'", "'results.csv'", "'legacy.csv'", "'logout'"):
    assert "href=" + href in hdr, href
assert "Program ops" not in hdr and "href='ops'" not in hdr and "Test mode" in hdr and admin["email"] in hdr
for u_ in ("/", "/bt", "/unbooked", "/send", "/analytics", "/legacy", "/legacy-issues", "/skus", "/settings"):
    body, st, hd = visit(u_, cookies="bt=" + cookie)
    assert st == 200 and "class=top" in body and "href='ops'" not in body and "href=ops" not in body, (u_, st)
# ---- removing test data: dry run reports, a real run cancels listed bookings (no WhatsApp), backs up, deletes, and the sync skips them after
QS = []
real_q = M.q
def fake_q(sql, **p):
    QS.append((sql, p))
    assert all(isinstance(v, str) for v in p.values()), ('BigQuery parameters are STRING only', p)
    if "SELECT DISTINCT program_id" in sql and "TEST-" in sql: return [{"program_id": "TEST-AAA"}, {"program_id": "TEST-BBB"}]
    if sql.startswith("SELECT COUNT(*) n FROM") and "kind IN ('booked', 'rescheduled')" in sql: return [{"n": 1 if p.get("b") == "19816365" else 0}]
    if sql.startswith("SELECT COUNT(*) n FROM"): return [{"n": 3}]
    return real_q(sql, **p) if "bt_config" in sql or M.CONFIG in sql else []
M.q = fake_q
CALLS.clear(); QS.clear()
out = M.purge_tests(["RE22687-XE700F8", "RE22687-PR-CGH-090"], [{"program_id": "RE22687-XE700F8", "booking_id": "19816365"}], "t", dry=True)
assert out["programs"] == ["RE22687-PR-CGH-090", "RE22687-XE700F8", "TEST-AAA", "TEST-BBB"] and out["cancels"][0]["result"] == "would cancel"
assert not [c for c in CALLS if "center-update-booking" in c[0]] and not [s for s, _ in QS if s.startswith(("DELETE", "INSERT", "CREATE"))], "dry run changes nothing"
out = M.purge_tests(["RE22687-XE700F8"], [{"program_id": "RE22687-XE700F8", "booking_id": "999"}], "t", dry=True)
assert out["cancels"][0]["result"].startswith("skipped: not this program"), "only the program's own booking is cancelled"
CALLS.clear(); QS.clear()
out = M.purge_tests(["RE22687-XE700F8", "RE22687-PR-CGH-090"], [{"program_id": "RE22687-XE700F8", "booking_id": "19816365"}], "t", dry=False)
assert out["cancels"][0]["result"] == "cancelled" and [c for c in CALLS if "center-update-booking" in c[0]] and not [c for c in CALLS if "send-template" in c[0]]
order = [s.split("`")[0].strip() + " " + s.split("`")[1].split(".")[-1] for s, _ in QS if s.startswith(("CREATE", "INSERT", "DELETE"))]
assert order[:3] == ["CREATE TABLE IF NOT EXISTS bt_removed_programs", "INSERT bt_removed_programs", "DELETE FROM bt_programs"] and len(order) == 9, order
assert M.removed_programs() >= {"TEST-AAA", "RE22687-PR-CGH-090", "RE22687-XE700F8"}
real_http = M.http
M.http = lambda url, body=None, headers=None, method=None, form=False: (400, {"status": "failure", "message": "Booking already cancelled"}) if "center-update-booking" in url else real_http(url, body, headers, method, form)
QS.clear()
out = M.purge_tests(["RE22687-XE700F8"], [{"program_id": "RE22687-XE700F8", "booking_id": "19816365"}], "t", dry=False)
assert out.get("stopped") and not [s for s, _ in QS if s.startswith("DELETE")], "a failed cancel deletes nothing"
M.http = real_http
M.q = real_q
assert visit("/purge-tests?token=nope", "POST")[1] == 403
# ---- program dates from the legacy sheet: report first, then apply; Test 2 moves with them
reset()
bought = (NOW - dt.timedelta(days=100)).astimezone(M.IST).date()
prog("57014-PR-CGH-120", days=120, phone="918506885688", at=NOW - dt.timedelta(days=100))
prog("57099-PR-CGH-120", days=120, phone="919000000099", at=NOW - dt.timedelta(days=50))
prog("57111-PR-CGH-120", days=120, phone="919000000111", at=NOW - dt.timedelta(days=30))
real_rows, real_ops, real_lt, real_ty = LB.legacy_rows, LB.ops_data, M.legacy_table, M.legacy_types
sd_real = bought + dt.timedelta(days=36)
LB.legacy_rows = lambda table, **k: ([dict(order="#57014", phone="8506885688", paid=bought.isoformat(), pstat="InPlan", start=sd_real.isoformat(),
                                           end=(sd_real + dt.timedelta(days=120)).isoformat(), excluded=False)], 0)
LB.ops_data = lambda table, types=None: ([dict(order_id="#57099", phone="9000000099", paid_iso="", prog_status="InPlan",
                                               start_iso=(NOW - dt.timedelta(days=50)).astimezone(M.IST).date().isoformat(),
                                               end_iso=((NOW - dt.timedelta(days=50)).astimezone(M.IST).date() + dt.timedelta(days=120)).isoformat())], [])
M.legacy_table = lambda sheet: [["h"]]; M.legacy_types = lambda sheet: None
rep = M.sheet_dates(apply=False)
r = {x["program_id"]: x for x in rep["programs"]}
assert r["57014-PR-CGH-120"]["result"] == "to fix" and r["57014-PR-CGH-120"]["sheet_start"] == sd_real.isoformat(), r["57014-PR-CGH-120"]
assert r["57099-PR-CGH-120"]["result"] == "matches" and r["57111-PR-CGH-120"]["result"] == "not in sheet" and rep["changes"] == 2
assert not [x for x in DB["log"] if x["kind"] in ("start", "end")], "a report writes nothing"
rep = M.sheet_dates(apply=True)
p = M.load("57014-PR-CGH-120")[0][0]
assert M.program_start(p) == sd_real and M.program_end(p) == sd_real + dt.timedelta(days=120), (M.program_start(p), M.program_end(p))
t2 = [x for x in M.rows_for(*M.load("57014-PR-CGH-120")) if x["n"] == 2][0]
assert t2["opens"] == sd_real + dt.timedelta(days=100), "Test 2 now opens 20 days before the sheet's end"
assert "legacy sheet" in [x for x in DB["log"] if x["kind"] == "start"][-1]["note"]
assert M.sheet_dates(apply=False)["tally"].get("matches") == 2, "applying again changes nothing"
pp = M.load("57111-PR-CGH-120")[0][0]
rows_, err = M.date_rows(pp, bought + dt.timedelta(days=80), None, "ops@sova.health", "profile")
DB["log"].extend(dict(r, at=NOW) for r in rows_)
LB.legacy_rows = (lambda f: (lambda table, **k: (f(table, **k)[0] + [dict(order="#57111", phone="9000000111", paid="", pstat="InPlan", start=bought.isoformat(),
                                                                          end=(bought + dt.timedelta(days=120)).isoformat(), excluded=False)], 0)))(LB.legacy_rows)
assert {x["program_id"]: x["result"] for x in M.sheet_dates(apply=False)["programs"]}["57111-PR-CGH-120"].startswith("kept"), "the team's own dates win"
LB.legacy_rows = lambda table, **k: ([dict(order="#57099-1", phone="", paid="", pstat="InPlan", start="2026-09-01", end="2026-12-01", excluded=False),
                                      dict(order="#57099-2", phone="", paid="", pstat="InPlan", start="2026-09-20", end="2026-12-20", excluded=False)], 0)
assert {x["program_id"]: x["result"] for x in M.sheet_dates(apply=False)["programs"]}["57099-PR-CGH-120"] == "sheet rows disagree", "split rows with different starts: left alone"
LB.legacy_rows, LB.ops_data, M.legacy_table, M.legacy_types = real_rows, real_ops, real_lt, real_ty
assert visit("/sheet-dates?token=nope")[1] == 403

# ---------- POST /purchase: payments.sova.health / payx.sova.health hand a paid blood test over (additive) ----------
reset()
real_q_p = M.q
M.q = lambda sql, **k: [{"program_id": x["program_id"]} for x in DB["programs"] if x["program_id"] == k.get("p")] if "bt_programs" in sql else []
def buy(body, key=None, method="POST"):
    with app.test_request_context("/purchase", method=method, json=body, headers={"X-Purchase-Key": M.purchase_key() if key is None else key}):
        r = M.app(flask.request)
        return r[1], (json.loads(r[0]) if r[0].startswith("{") else r[0])
assert buy({"action": "status"}, key="nope")[0] == 403 and buy({}, method="GET")[0] == 405
assert buy({"action": "create", "store": "sova.health", "order": "#2001", "phone": "9900000001"})[1]["error"].startswith("Redcliffe package code")
assert buy({"action": "create", "store": "sova.health", "order": "#2001", "phone": "12345", "package_codes": "PL70"})[0] == 400
asha = {"action": "create", "store": "sova.health", "order": "#2001", "ref": "SVABC-1", "name": "Asha Rao", "phone": "9900000001", "email": "a@x.in",
        "address": "12 MG Road", "city": "Bengaluru", "state": "KA", "pincode": "560001", "product": "Sova blood test (73 markers)", "package_codes": "pl70, CAMP1",
        "send_whatsapp": True}
st, r = buy(asha)
assert st == 200 and r["program_id"] == "2001-BTP" and not r["existing"] and r["whatsapp"] == "sent" and "/b/" in r["link"] and r["status"] == "awaiting", r
row = DB["programs"][-1]
assert row["source"] == "custom" and row["sku"] == "CUSTOM" and row["order_name"] == "#2001 · Blood test <> Purchase" and row["phone"] == "919900000001"
assert last_send()["templateName"] == "bt_custom_test" and named(last_send())["test_name"] == "Sova blood test (73 markers)"
assert M.jd([x for x in DB["log"] if x["kind"] == "profile" and x["program_id"] == "2001-BTP"][0])["package_codes"] == "PL70,CAMP1"
n_sends = len([c for c in CALLS if "send-template" in c[0]])
st, r = buy(asha)
assert st == 200 and r["existing"] and r["whatsapp"] == "skipped" and len(DB["programs"]) == 1, "a retry creates and sends nothing new"
assert len([c for c in CALLS if "send-template" in c[0]]) == n_sends
assert buy(dict(asha, store="x.sova.health", order="#2001"))[1]["program_id"] == "SX2001-BTP", "Sova X orders never clash with sova.health numbers"
t = [x for x in M.rows_for(*M.load("2001-BTP")) if x["program"]["program_id"] == "2001-BTP"]
assert len(t) == 1 and t[0]["n"] == 1 and t[0]["custom"], "one test, like a personalised test"
assert buy({"action": "slots", "program_id": "2001-BTP"})[0] == 409, "address first"
assert buy({"action": "address", "program_id": "2001-BTP", "house": "", "pincode": "560001"})[0] == 400
st, r = buy({"action": "address", "program_id": "2001-BTP", "house": "12", "area": "MG Road", "city": "Bengaluru", "pincode": "560001"})
assert st == 200 and r["serviceable"], r
st, r = buy({"action": "slots", "program_id": "2001-BTP", "day": "2026-11-12"})
assert st == 200 and r["day"] == "2026-11-12" and [x["label"] for x in r["slots"]][0] == "07:00 AM - 08:00 AM", r
assert buy({"action": "book", "program_id": "2001-BTP", "day": "2026-11-12", "slot_id": "2", "age": "3", "gender": "female"})[0] == 400
st, r = buy({"action": "book", "program_id": "2001-BTP", "day": "2026-11-12", "slot_id": "2", "age": "34", "gender": "female"})
assert st == 200 and r["ok"] and r["status"] == "booked" and r["booking"]["slot"] == "07:00 AM - 08:00 AM", r
made = [c[1] for c in CALLS if "center-create-booking" in c[0]][-1]
assert made["package_code"] == ["PL70", "CAMP1"] and made["reference_data"] == "2001-BTP" and made["customer_age"] == 34
assert buy({"action": "book", "program_id": "2001-BTP", "day": "2026-11-12", "slot_id": "2", "age": "34", "gender": "female"})[0] == 409, "already booked"
st, r = buy({"action": "resched", "program_id": "2001-BTP", "day": "2026-11-13", "slot_id": "2"})
assert st == 200 and r["ok"] and r["booking"]["date"] == "2026-11-13" and r["resched_left"] == M.MAX_RESCHEDULES - 1, r
st, r = buy({"action": "status", "program_id": "2001-BTP"})
assert st == 200 and r["status"] == "booked", r
st, r = buy(dict(asha, order="#2002", phone="9811111111"))
assert st == 200 and r["whatsapp"] == "planned", "a real customer while sends are in test mode: logged as planned, like every other send"
buy({"action": "address", "program_id": "2002-BTP", "house": "1", "pincode": "560001"})
assert "paused" in buy({"action": "book", "program_id": "2002-BTP", "day": "2026-11-12", "slot_id": "2", "age": "30", "gender": "male"})[1]["error"]
assert buy({"action": "status", "program_id": "1001-PR-WGL-120"})[0] == 400, "only purchase rows"
assert buy({"action": "nope", "program_id": "2001-BTP"})[0] == 400
M.q = real_q_p
# ---- customer page: header with contact buttons (Open Briza chat from Settings), progress steps, ask banner, filterable timeline
reset()
prog()
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="msg", note="t1", status="sent", by_user="system")
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="booked", booking_id="777", collection_date=(NOW + dt.timedelta(days=3)).astimezone(M.IST).date().isoformat(),
      slot="07:00 AM - 08:00 AM", by_user="customer")
body, st, hd = visit("/p?id=1001-PR-WGL-120", cookies="bt=" + cookie)
assert st == 200 and "class='card phead'" in body and ">AR<" in body and "Set up Briza chat link" in body and "href='tel:+919900000001'" in body
assert "https://wa.me/919900000001" in body and "<ol class=steps" in body and "class='on'><span class=bar></span><b>Booked</b>" in body
assert "data-f='rcl'" in body and "<tr data-k='book'>" in body and "<tr data-k='msg'>" in body, "timeline filter"
assert "Purchase date" in body and "Change dates" in body and "Customer details" in body and "Cancel booking" in body and "id=fu1" in body
assert M.act({"a": "cfg", "k": "briza_chat_url", "v": "javascript:alert(1)"}, admin)[0].startswith("Enter the Briza chat address")
assert M.act({"a": "cfg", "k": "briza_chat_url", "v": "https://x.test/c?p={phone}' onmouseover='x"}, admin)[0].startswith("Enter the Briza chat address")
assert M.act({"a": "cfg", "k": "briza_chat_url", "v": "https://app.briza.test/chats?phone={phone}"}, admin)[0] == "Saved."
M._cfg["at"] = 0
body, st, hd = visit("/p?id=1001-PR-WGL-120", cookies="bt=" + cookie)
assert "href='https://app.briza.test/chats?phone=919900000001' target=_blank rel=noopener>" in body and "Open Briza chat" in body and "Set up Briza" not in body
M.act({"a": "cfg", "k": "briza_chat_url", "v": "https://app.briza.test/c/{phone10}"}, admin); M._cfg["at"] = 0
assert "https://app.briza.test/c/9900000001'" in visit("/p?id=1001-PR-WGL-120", cookies="bt=" + cookie)[0]
html_ = ui.settings(M.cfg(), {}, admin, "", "", {k: M.setting(k) for k in M.DEFAULT_T}, "test", [], "h")
assert "id=c_briza_chat_url" in html_ and "{phone}" in html_
# ---- daily insights: read-only report with alerts, its own key, no names or phone numbers
reset()
prog()
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="msg", note="t1", status="sent", by_user="system")
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="msg", note="nudge", status="failed", by_user="system")
M.log(program_id="1001-PR-WGL-120", test_no=1, kind="error", note="Booking failed: Age should be in range 5 to 150.", by_user="customer")
real_q, real_gcp = M.q, M._gcp
def ins_q(sql, **p):
    if "SUBSTR(detail, 1, 300) d" in sql:
        assert "TIMESTAMP(@s)" in sql and isinstance(p["s"], str)
        return [dict(r, d=r.get("detail") or "") for r in DB["log"]]
    return real_q(sql, **p)
fresh = (NOW - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
def ins_gcp(url, body=None):
    if "cloudscheduler" in url:
        return 200, {"jobs": [{"name": "x/jobs/bloodtests-tick", "state": "PAUSED", "lastAttemptTime": fresh, "schedule": "10 * * * *"},
                              {"name": "x/jobs/bt-legacy-sync", "state": "ENABLED", "lastAttemptTime": fresh, "status": {"code": 2}}]}
    if "logging" in url:
        assert "severity>=ERROR" in body["filter"]
        return 200, {"entries": [{"textPayload": "Traceback\n  File x\nValueError: boom"}, {"textPayload": "Memory limit of 512 MiB exceeded"}]}
    return 404, {}
M.q, M._gcp = ins_q, ins_gcp
M._ins["at"] = 0
real_sd = M.sheet_dates
M.sheet_dates = lambda apply=False, by="": {"tally": {"matches": 1}, "programs": [{"order": "#9", "result": "sheet rows disagree"}]}
r_ = M.insights(24)
txt = json.dumps(r_, default=str)
assert not r_["ok"] and "asha" not in txt.lower() and "919900000001" not in txt, "no names or numbers"
assert r_["jobs"]["bloodtests-tick"]["state"] == "PAUSED" and any("bloodtests-tick is PAUSED" in a for a in r_["alerts"]) and any("bt-checks is missing" in a for a in r_["alerts"])
assert any("bt-legacy-sync's last run failed" in a for a in r_["alerts"])
assert r_["failed_whatsapps"][0]["order"] == "#1001" and r_["app_errors"][0]["error"].startswith("Booking failed") and r_["whatsapps"]["t1 sent"] == 1
assert r_["service_errors"]["count"] == 2 and r_["service_errors"]["out_of_memory"] == 1 and r_["service_errors"]["top"][0]["message"] in ("ValueError: boom", "Memory limit of 512 MiB exceeded")
assert r_["dates_vs_sheet"]["needs_attention"][0]["order"] == "#9" and "Send mode is test" in " ".join(r_["alerts"])
assert visit("/insights?key=nope")[1] == 403
body, st, hd = visit("/insights?key=" + M.insights_key() + "&hours=48")
assert st == 200 and json.loads(body)["window_hours"] == 48
# no Cloud Scheduler access: job health from the app's own log rows (the hourly run now writes a heartbeat)
M._gcp = lambda url, body=None: (403, {})
before = len(DB["log"])
body, st = visit("/tick?token=" + M.E("TOKEN"))[:2]
assert st == 200 and [r for r in DB["log"][before:] if r["kind"] == "tick" and r["program_id"] == "_tick" and r["status"] in ("ok", "failed")]
M.log(program_id="_legacy", kind="legacy_sync", by_user="hourly sync", status="ok", detail={})
M.log(program_id="_checks", kind="check", by_user="system", note="11:00")
M._ins["at"] = 0
r_ = M.insights(24)
assert r_["jobs"]["source"].startswith("app log") and r_["jobs"]["bloodtests-tick"]["last_run_minutes_ago"] == 0 and r_["jobs"]["bt-checks"]["runs"] == 1
assert not any("has not run" in a for a in r_["alerts"]), r_["alerts"]
assert r_["service_errors"]["unavailable"].startswith("Cloud Logging read failed")
M.q, M._gcp, M.sheet_dates = real_q, real_gcp, real_sd
print("all tests passed")
