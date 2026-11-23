"""The legacy blood-test data (Google Sheet 'Program Clean', tab 'Blood Test') and the Blood Test dashboard's rows.

The rules are the ones the sheet's Apps Script dashboard ('Program & Dr. Consultation Dashboard') used, ported 1:1:
- programs whose status is Not Started Yet / Dropout / Dropout- Not Started Yet / Refunded / Cancelled / Past Client- No data / OnBreak are left out
- Test 1: due on the program start date; eligible for 90+ day programs, Fresh / Fresh-B2B / Fresh-VIP always, Repeat only when the previous plan
  was under 90 days or its 2nd blood test wasn't done
- Test 2: due on the program end date; eligible for every 90+ day program
- done = 'Report Shared/ Uploaded' or 'Refunded and Reports uploaded'
Live programs (Shopify orders tracked here, booked through Redcliffe) are folded in with the same card groups, matched to sheet rows by order id.
"""
import csv, datetime as dt, io, re

EXCLUDED = {"not started yet", "dropout", "dropout- not started yet", "refunded", "cancelled", "past client- no data", "onbreak"}
DONE = {"report shared/uploaded", "report shared/ uploaded", "refunded and reports uploaded"}
FRESH = {"fresh", "fresh-b2b", "fresh-vip"}
MON = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
SHEET = "Blood Test" # the tab the dashboard reads
SHEET_ID = "10AVYW90pbRe7BSlahszOQuxhk1bFkVAyOMA8UxOLoFE" # Google Sheet 'Program Clean'
SYNC = {"Blood Test": 762303133, "Program-Main": 1097961607, "Blood Test Dashboard": 726412692, "BT spend tracking": 94946262,
        "BT_Additonal Parameters": 863895610, "H. pylori+MultiVitamin": 865683926} # tab -> gid; copied to BigQuery every hour
SHEETS = tuple(SYNC)
TYPED = ("Blood Test", "Program-Main") # tabs kept with their cell types (date / number / text): the dashboards compute from these
OPS = "Program-Main" # the tab the old dashboard's Program Onboarding / Dr. Consultation / Program Renewal views read
T2_DUE = 15 # Test 2 is due 15 days before the program ends, in the sheet's rows as in ours (main.T2_DUE)

# Our own test states -> (card group, status shown). Card groups are the sheet dashboard's.
LIVE = {"report": ("done", "Report ready (Redcliffe)"), "collected": ("booked", "Sample collected"), "booked": ("booked", "Booked (Redcliffe)"),
        "check": ("booked", "Booked, collection day passed"), "awaiting": ("inprogress", "Booking WhatsApp sent"),
        "followup": ("followup", "Follow up required"), "cancelled": ("followup", "Booking cancelled"), "missed": ("followup", "Booking window missed"),
        "to_send": ("blank", "Booking WhatsApp not sent yet"), "upcoming": ("blank", "Not open yet"),
        "untracked": ("noaction", "Window over before tracking"), "closed": ("noteligible", "Closed by team"),
        "covered": ("noteligible", "Not eligible: previous plan's Test 2 counts")}
QUIET = ("to_send", "upcoming", "untracked") # our system hasn't done anything for this test yet: the sheet's status stands


CANON = {"report shared/ uploaded": "Report Shared/ Uploaded", "refunded and reports uploaded": "Refunded and Reports uploaded", "booked": "Booked",
         "blocked": "Blocked", "sample collected": "Sample collected", "spoken + test form shared": "Spoken + Test Form Shared",
         "will do in future": "Will Do In Future", "follow up required": "Follow up required", "need to reschedule": "Need to Reschedule",
         "denied by client": "Denied by client", "non-serviceable": "Non-serviceable", "international client": "International Client",
         "not eligible": "Not Eligible", "program refund": "Program Refund", "dropout": "Dropout", "no response": "No response"}
BY_GROUP = {"done": "Report Shared/ Uploaded", "booked": "Booked", "inprogress": "Spoken + Test Form Shared", "followup": "Follow up required",
            "noaction": "Denied by client", "notserviceable": "Non-serviceable", "noteligible": "Not Eligible", "blank": ""} # the sheet's word per group
LABS = (("redcl", "Redcliffe"), ("redli", "Redcliffe"), ("health", "Healthians"), ("metro", "Metropolis"), ("thyro", "Thyrocare"))


def canon_status(v):
    """A status as typed -> (the sheet's own spelling, known?). Extra spaces, case and slash spacing don't matter; '0' is blank; a status that
    says the report was shared or uploaded is done."""
    raw = re.sub(r"\s+", " ", str(v or "")).strip()
    k = re.sub(r"\s*/\s*", "/ ", raw.lower()).replace("non serviceable", "non-serviceable").replace("followup", "follow up")
    k = k.replace("report shared/uploaded", "report shared/ uploaded")
    if k in ("", "0", "-", "na", "n/a"): return "", True
    if k in CANON: return CANON[k], True
    if k.startswith("refunded") and "report" in k: return CANON["refunded and reports uploaded"], True
    if re.search(r"report[s]? (shared|upl?oa?ded)|upl?oa?ded", k): return CANON["report shared/ uploaded"], True
    return raw, False


def canon_lab(v):
    """Lab column -> (lab name, note): a known lab by name (any spelling); anything else in the column (dates, notes) becomes a note."""
    raw = re.sub(r"\s+", " ", str(v or "")).strip()
    for k, name in LABS:
        if k in raw.lower(): return name, raw if raw.lower() != name.lower() else ""
    return "", raw


OWN_LAB = re.compile(r"\bown\b|\bcx shared\b|client shared|shared (the |his |her )?reports?|self[- ]?(test|done)|outside lab|other lab|external lab|by (him|her)self", re.I)


def platform(lab, *texts):
    """Where a test was (or is being) done: the lab named in the sheet, or 'Customer's own lab' when the remarks say the customer had it
    done themselves and shared the report; '' when the sheet doesn't say."""
    if lab: return lab
    return "Customer's own lab" if any(OWN_LAB.search(t or "") for t in texts) else ""


def other_platform(pf): return bool(pf) and pf != "Redcliffe" # Redcliffe is ours; anything else is another platform


def with_ids(rows):
    """A stable id per sheet person-row for their profile here: S-<order> (S-<order>-2 for a second row of the same order), S-R<row> without an order."""
    seen = {}
    for r in rows:
        k = "S-" + (order_key(r["order"]) or "R%d" % r["row"])
        seen[k] = seen.get(k, 0) + 1
        r["lid"] = k if seen[k] == 1 else "%s-%d" % (k, seen[k])
    return rows


def first_date(*cells):
    for c in cells:
        for m in re.finditer(r"\d{1,2}[\s\-/.]+[A-Za-z]{3,9}\.?[\s\-/.,]+\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}", str(c or "")):
            d = sheet_date(m.group(0))
            if d: return d
    return ""


# ---------- cell types: the sheet's own date / number / text, as the Apps Script saw them ----------
EPOCH = dt.date(1899, 12, 30)


def types_from(formatted, unformatted):
    """Per row: {"d": {col: serial}} for date cells, "z": [cols] for number-0 cells (blank to the old dashboard). From the Sheets API's
    unformatted values (dates as serial numbers) beside the values as shown."""
    out = []
    for i, ra in enumerate(unformatted):
        rf = formatted[i] if i < len(formatted) else []
        d, z = {}, []
        for j, x in enumerate(ra):
            if isinstance(x, bool) or not isinstance(x, (int, float)): continue
            f = str(rf[j] if j < len(rf) else "").strip()
            if f and not re.fullmatch(r"[-+]?[\d,]*\.?\d+%?", f): d[str(j)] = x
            elif x == 0: z.append(j)
        out.append({"d": d, "z": z} if d or z else {})
    return out


class Grid:
    """A kept tab with its cell types: s() is a cell's text as the old dashboard read it, d() its date (None for text and blanks).
    Without types (a hand-uploaded CSV), dates are read from the text."""
    def __init__(self, table, types=None):
        self.t, self.ty = table, types
    def s(self, i, j):
        r = self.t[i] if i < len(self.t) else []
        v = (r[j] if 0 <= j < len(r) else "").strip()
        if self.ty is not None and v and i < len(self.ty) and j in (self.ty[i] or {}).get("z", ()): return ""
        return v
    def d(self, i, j):
        if j < 0: return None
        if self.ty is None: return _d(self.s(i, j))
        t = (self.ty[i] if i < len(self.ty) else None) or {}
        ser = t.get("d", {}).get(str(j))
        return None if ser is None else EPOCH + dt.timedelta(days=int(ser // 1))
    def rows(self): return range(1, len(self.t))


def card_key(status):
    """The sheet dashboard's btCardKey: a status -> its card group."""
    v = (status or "").strip().lower()
    if v in DONE: return "done"
    if v in ("booked", "sample collected", "blocked"): return "booked"
    if v == "spoken + test form shared": return "inprogress"
    if v in ("will do in future", "follow up required", "need to reschedule"): return "followup"
    if v == "denied by client": return "noaction"
    if v in ("non-serviceable", "international client"): return "notserviceable"
    if v in ("not eligible", "program refund", "dropout"): return "noteligible"
    return "blank"


def sheet_date(v):
    """A date as the sheet shows it ('16 Jul 26', '13 June 2026 cx shared...', '2026-07-16', '16/07/2026') -> ISO date, or ''.
    Blank cells (shown as 30 Dec 1899) and anything before 1970 are no date."""
    s = str(v or "").strip()
    m = re.match(r"(\d{1,2})[\s\-/.]+([A-Za-z]{3,9})\.?[\s\-/.,]+(\d{4}|\d{2})(?!\d)", s)
    try:
        if m and m.group(2)[:3].lower() in MON:
            y = int(m.group(3)); y = y + 2000 if y < 100 else y
            d = dt.date(y, MON[m.group(2)[:3].lower()], int(m.group(1)))
        elif re.match(r"\d{4}-\d{1,2}-\d{1,2}", s):
            d = dt.date(*map(int, re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s).groups()))
        elif re.match(r"\d{1,2}/\d{1,2}/\d{4}", s):
            a, b, y = map(int, re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s).groups())
            d = dt.date(y, b, a)
        else: return ""
    except ValueError: return ""
    return d.isoformat() if d.year >= 1970 else ""


def parse_csv(text):
    return [r for r in csv.reader(io.StringIO(text.lstrip("﻿")))]


def order_key(v):
    """'#RE2255-1 ' -> 'RE2255-1': sheet and Shopify order names compare equal."""
    return re.sub(r"[^A-Z0-9-]", "", str(v or "").upper())


def phone10(v):
    d = re.sub(r"\D", "", str(v or ""))
    return d[-10:] if len(d) >= 10 else ""


def _cols(header):
    """Header -> {clean lower name: [column indexes]} (the sheet has two 'Lab name' and two sets of 'Follow-up 1..3' columns)."""
    out = {}
    for i, h in enumerate(header): out.setdefault(re.sub(r"\s+", " ", str(h).replace("\n", " ")).strip().lower(), []).append(i)
    return out


def _plus(iso, days): return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat() if iso else ""


def legacy_rows(table, dedupe=True, issues=None, types=None):
    """The 'Blood Test' tab (header row first) -> (rows, exact duplicate rows dropped). One dict per sheet row with the Apps Script's
    eligibility, plus, cleaned on read (the sheet itself is never changed):
    - statuses in the sheet's own spelling (case, spaces, '0', 'report uploaded' variants)
    - lab names (Redcliffe / Healthians / Metropolis / Thyrocare); anything else typed in the lab column becomes a note
    - Test 2 due 15 days before the program end (as our own Test 2), the sheet's 'Eligible for 2nd blood test' date kept as b2_win
    - done dates: the first date in the remark, else in the follow-up columns, else the due date marked approximate (b?_dq)
    'excluded' marks the program statuses the dashboard leaves out. issues (a list) collects rows the team should fix in the sheet."""
    if not table: return [], 0
    G = Grid(table, types)
    C = _cols(table[0])
    def col(name, k=0):
        ix = C.get(name.lower(), [])
        return ix[k] if len(ix) > k else -1
    get = lambda r, i: (r[i] if 0 <= i < len(r) else "").strip()
    I = dict(month=col("blood test") if col("month") < 0 else col("month"), plan=col("plan"), order=col("order id"), email=col("email"), paid=col("paid at"),
             item=col("lineitem name"), dur=col("program duration"), name=col("billing name"), phone=col("billingphone"), alt=col("alternate number"),
             s1=col("test 1 status"), r1=col("remark"), lab1=col("lab name"), fu1=col("followup status (test 1)"),
             el2=col("eligible for 2nd blood test"), s2=col("test 2 status"), r2=col("remark of test 2"), lab2=col("lab name", 1), fu2=col("followup status (test 2)"),
             coach=col("coach name"), pstat=col("program status"), start=col("program start date"), end=col("end date"), prev=col("previous plan"), done2=col("2nd done or not"))
    f1, f2, f3 = C.get("follow-up 1", []), C.get("follow-up 2", []), C.get("follow-up 3", [])
    out, seen, dups = [], set(), 0
    flag = (lambda n, r, kind, detail: issues.append({"row": n, "order": get(r, I["order"]), "name": get(r, I["name"]), "issue": kind, "detail": detail})) \
        if issues is not None else (lambda *a: None)
    for n, r in enumerate(table[1:], 2):
        if not (get(r, I["name"]) or get(r, I["order"]) or get(r, I["phone"])): continue
        key = tuple(x.strip() for x in r)
        if dedupe and key in seen: dups += 1; flag(n, r, "Duplicate row", "an exact copy of an earlier row; left out"); continue
        seen.add(key)
        plan, pstat = get(r, I["plan"]), get(r, I["pstat"])
        try: dur = int(float(get(r, I["dur"]) or 0))
        except ValueError: dur = int((re.match(r"\d+", get(r, I["dur"])) or [0])[0])
        prev, done2 = get(r, I["prev"]), get(r, I["done2"]).lower()
        el1 = False
        if dur >= 90:
            if plan.lower() in FRESH: el1 = True
            elif plan.lower() == "repeat":
                pd = int((re.search(r"\d+", prev) or [0])[0])
                el1 = pd < 90 or done2 != "done"
        i = n - 1
        start, end = _iso(G.d(i, I["start"])), _iso(G.d(i, I["end"]))
        if pstat.lower() not in EXCLUDED:
            if not start: flag(n, r, "No start date", get(r, I["start"]) or "blank")
            if not end and dur >= 90: flag(n, r, "No end date", get(r, I["end"]) or "blank")
        row = dict(src="legacy", row=n, pid="", month=get(r, I["month"]), plan=plan, order=get(r, I["order"]), email=get(r, I["email"]),
                   paid=_iso(G.d(i, I["paid"])), item=get(r, I["item"]), dur=dur, name=get(r, I["name"]), phone=get(r, I["phone"]),
                   alt=get(r, I["alt"]), coach=get(r, I["coach"]), pstat=pstat, excluded=pstat.lower() in EXCLUDED,
                   start=start, end=end, prev=prev, done2=done2 or "not done", b1_el=el1, b2_el=dur >= 90, b2_win=_iso(G.d(i, I["el2"])))
        for t, sk, rk, lk, fk, due in ((1, "s1", "r1", "lab1", "fu1", start), (2, "s2", "r2", "lab2", "fu2", _plus(end, -T2_DUE))):
            raw = get(r, I[sk])
            st, known = canon_status(raw)
            if not known: flag(n, r, "Unknown Test %d status" % t, raw)
            k = card_key(st)
            lab, note = canon_lab(get(r, I[lk]))
            if note and get(r, I[lk]): flag(n, r, "Test %d lab column" % t, get(r, I[lk]))
            fus = [x for x in (get(r, I[fk]), *(get(r, ix[t - 1]) for ix in (f1, f2, f3) if len(ix) >= t)) if x]
            rm = get(r, I[rk])
            done, dq = "", ""
            if k == "done":
                done, dq = _iso(G.d(i, I[rk])) or first_date(rm), "remark"
                if not done: done, dq = first_date(*fus), "follow-up"
                if not done and due: done, dq = due, "approx"
                if dq == "approx" or not done: flag(n, r, "Test %d done, no date" % t, "remark: %s" % (rm or "blank"))
            row.update({"b%d_st" % t: st, "b%d_raw" % t: raw, "b%d_k" % t: k, "b%d_due" % t: due, "b%d_done" % t: done, "b%d_dq" % t: dq if done else "",
                        "b%d_pf" % t: platform(lab, rm, *fus),
                        "b%d_lab" % t: lab, "b%d_rm" % t: " · ".join(x for x in (rm, ("lab column: " + note) if note else "") if x), "b%d_fu" % t: " | ".join(fus)})
        out.append(row)
    return out, dups


def live_test(row, n, t):
    """Fill test n of a dashboard row from one of our tests ({status, due, done, rcl, collection})."""
    k, label = LIVE.get(t["status"], ("blank", t["status"]))
    # we only track tests a program is due, so they're eligible (a closed one still lands in Not Eligible by its group)
    row.update({"b%d_el" % n: True, "b%d_st" % n: label, "b%d_k" % n: k, "b%d_due" % n: t["due"],
                "b%d_done" % n: t.get("done") or "", "b%d_dq" % n: "report" if t.get("done") else "", "b%d_lab" % n: "Redcliffe", "b%d_pf" % n: "Redcliffe", "b%d_rm" % n: t.get("rcl") or (("Collection " + t["collection"]) if t.get("collection") else ""),
                "b%d_live" % n: t["status"]})


def unify(legacy, live, today):
    """Sheet rows + our programs -> the dashboard's rows. A program whose order is in the sheet becomes one row: the sheet's coach, plan and
    program status, and for each test our status once our system has acted on it (a booking WhatsApp, a booking, a report); a test the sheet
    already marks done stays done. Programs not in the sheet are added as rows of their own ('live')."""
    by_order, by_phone = {}, {}
    for r in legacy:
        if r["order"]:
            k = order_key(r["order"])
            by_order.setdefault(k, []).append(r)
            base = re.sub(r"-\d+$", "", k) # the sheet splits one order into '#27433-1', '#27433-2'
            if base != k: by_order.setdefault(base, []).append(r)
        if phone10(r["phone"]): by_phone.setdefault(phone10(r["phone"]), []).append(r)
    known = set(by_phone)
    out, matched, taken = list(legacy), 0, set()
    def near(a, b, days=20):
        try: return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days) <= days
        except (TypeError, ValueError): return False
    for p in sorted(live, key=lambda p: p["purchased"]):
        keys = {order_key(o) for o in [p["order"]] + list(p.get("orders") or []) if o}
        hits = [r for k in keys for r in by_order.get(k, ())]
        if not hits: # same person, same program under another order id: same phone and an end date within 20 days
            hits = [r for r in by_phone.get(phone10(p["phone"]), ()) if id(r) not in taken and near(r["end"], p["end"])][:1]
        hits = list({id(r): r for r in hits}.values())
        taken.update(id(r) for r in hits)
        if hits:
            matched += 1
            for r in hits:
                r["pid"], r["src"] = p["pid"], "both"
                for n, t in p["tests"].items():
                    if t["status"] in QUIET: continue
                    if r["b%d_k" % n] == "done" and t["status"] != "report": continue
                    live_test(r, n, t)
            continue
        ph = phone10(p["phone"])
        end = p["end"]
        r = dict(src="live", row=0, pid=p["pid"], month="", plan="Repeat" if ph and ph in known else "Fresh", order=p["order"] or "", email=p.get("email") or "",
                 paid=p["purchased"], item=p.get("product") or "", dur=int(p["days"] or 0), name=p["name"] or "", phone=p["phone"] or "", alt="", coach="",
                 pstat="Closed" if p.get("closed") else ("Ended" if end and end < today else "InPlan"), excluded=False, start=p["start"], end=end, prev="", done2="",
                 b1_el=False, b1_st="", b1_k="blank", b1_due="", b1_done="", b1_dq="", b1_lab="", b1_pf="", b1_rm="", b1_fu="",
                 b2_el=False, b2_st="", b2_k="blank", b2_due="", b2_done="", b2_dq="", b2_lab="", b2_pf="", b2_rm="", b2_fu="", b2_win="", lid="")
        for n, t in p["tests"].items(): live_test(r, n, t)
        if ph: known.add(ph)
        out.append(r)
    return out, matched


COLS = ("src", "row", "pid", "lid", "plan", "order", "dur", "name", "phone", "coach", "pstat", "excluded", "start", "end", "prev", "done2", "paid", "item",
        "b1_el", "b1_st", "b1_k", "b1_due", "b1_done", "b1_dq", "b1_lab", "b1_pf", "b1_rm", "b1_fu", "b2_el", "b2_st", "b2_k", "b2_due", "b2_done", "b2_dq",
        "b2_lab", "b2_pf", "b2_rm", "b2_fu", "b2_win")


def compact(rows):
    """Rows -> column names + value lists (half the JSON of a list of dicts)."""
    return {"cols": list(COLS), "rows": [[(int(r.get(c)) if isinstance(r.get(c), bool) else r.get(c, "")) for c in COLS] for r in rows]}


# ---------- the old dashboard's other tabs (Program Onboarding, Dr. Consultation, Program Renewal): its getData(), ported ----------
MN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _d(v):
    """A cell as the Apps Script's toDateObj saw it: a date, or None for text and blanks. 'Jul 2025' (a month cell) is the 1st."""
    iso = sheet_date(v)
    if not iso:
        m = re.match(r"\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\s*$", str(v or ""))
        if m and m.group(1).lower() in MON: iso = "%s-%02d-01" % (m.group(2), MON[m.group(1).lower()])
    return dt.date.fromisoformat(iso) if iso else None


def _ok(d): return d is not None and d.year >= 1970 # the old dashboard shows no date before 1970 (a blank date cell is 30 Dec 1899)
def _fmt(d): return "%d %s %s" % (d.day, MN[d.month - 1], str(d.year)[2:]) if _ok(d) else ""
def _ym(d): return "%s %d" % (MN[d.month - 1], d.year) if _ok(d) else ""
def _iso(d): return d.isoformat() if _ok(d) else ""
def _letter(c): return sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(c.upper()))) - 1


def ops_data(table, types=None):
    """Program-Main -> (rows, months) exactly as the Apps Script's getData() built DATA (programs paid from 1 May 2025). types: the cells'
    own types (types_from); without them dates are read from the text."""
    if not table: return [], []
    G = Grid(table, types)
    h = {}
    for i, x in enumerate(table[0]): h[str(x).replace("\n", " ").strip()] = i # later duplicates win, as in the script
    def exact(name):
        if name in h: return h[name]
        return next((i for k, i in h.items() if k.lower() == name.lower()), -1)
    def kw(word): return next((i for k, i in h.items() if word.lower() in k.lower()), -1)
    CI = dict(month=kw("Month"), plan=exact("Plan"), order=exact("Order ID"), paid=exact("Paid at"), item=kw("Lineitem name"), dur=exact("Duration"),
              name=exact("Billing Name"), phone=exact("Billing Phone"), status=exact("Program Status"), coach=exact("Coach Name"), start=kw("Program Start Date"),
              end=kw("End Date"), dc1=exact("DC1"), dc1s=exact("DC1 Status"), dc2=kw("Dr. Consult 2"), dc2s=exact("DC2 Status"), dc2e=exact("DC2 Eligible or not?"),
              remark=_letter("M"), why=_letter("V"), dc1r=_letter("Z"), dc2r=_letter("AD"))
    g = lambda i, k: G.s(i, CI[k]) if CI[k] >= 0 else ""
    cutoff, rows, months = dt.date(2025, 5, 1), [], set()
    for i in G.rows():
        if not (g(i, "month") or g(i, "order") or g(i, "name")): continue
        paid = G.d(i, CI["paid"])
        if paid and paid < cutoff: continue
        month = _ym(G.d(i, CI["month"]))
        plan = g(i, "plan")
        try: dur = int(re.match(r"-?\d+", g(i, "dur")).group(0))
        except (AttributeError, ValueError): dur = 0
        start, end = G.d(i, CI["start"]), G.d(i, CI["end"])
        start = start if start and start.year > 1970 else None
        to_start = (start - paid).days if start and paid and 0 <= (start - paid).days < 3000 else None
        dc_el = dur >= 90 and plan in ("Fresh", "Fresh-B2B", "Fresh-VIP", "Repeat")
        e2 = g(i, "dc2e").lower()
        dc2_el = True if e2 == "eligible" else False if e2 == "not eligible" else dc_el
        dc1, dc2 = G.d(i, CI["dc1"]), G.d(i, CI["dc2"])
        due1 = start + dt.timedelta(days=30) if start else None
        due2 = end - dt.timedelta(days=15) if end else None
        pym = _ym(paid) or month
        if pym: months.add(pym)
        if month: months.add(month)
        rows.append(dict(month=month, plan=plan, order_id=g(i, "order"), name=g(i, "name"), phone=g(i, "phone"), lineitem=g(i, "item"),
                         coach=g(i, "coach"), duration=dur, prog_status=g(i, "status"), start_date=_fmt(start), start_iso=_iso(start),
                         end_date=_fmt(end), end_iso=_iso(end), renewal_iso=_iso(end - dt.timedelta(days=10)) if end else "", paid_at=_fmt(paid),
                         paid_iso=_iso(paid), days_to_start=to_start, dc_eligible=dc_el, dc2_eligible=dc2_el, dc1_date=_fmt(dc1), dc1_iso=_iso(dc1),
                         dc1_status=g(i, "dc1s"), dc1_remark=g(i, "dc1r"), dc2_date=_fmt(dc2), dc2_iso=_iso(dc2),
                         dc2_status=g(i, "dc2s"), dc2_remark=g(i, "dc2r"), dc1_due_iso=_iso(due1), dc1_due_date=_fmt(due1),
                         dc2_due_iso=_iso(due2), dc2_due_date=_fmt(due2), paid_ym=pym, remark=g(i, "remark"), not_started_reason=g(i, "why")))
    key = lambda m: (int(m[4:]), MN.index(m[:3]))
    return rows, sorted((m for m in months if re.fullmatch(r"[A-Za-z]{3} \d{4}", m)), key=key)


def ops_bt_rows(rows):
    """Dashboard rows -> the old dashboard's bt_rows (its Blood Test tab). Programs it left out stay out; our live states use the sheet's words
    so its card rules sort them the same way."""
    out = []
    for r in rows:
        if r["excluded"]: continue
        d = lambda iso: _fmt(dt.date.fromisoformat(iso)) if iso else ""
        x = dict(month=r.get("month", ""), plan=r["plan"], order_id=r["order"], name=r["name"], phone=r["phone"], duration=r["dur"], coach=r["coach"],
                 prog_status=r["pstat"], prev_plan=r["prev"], prev_bt2_done=r["done2"] or "not done", start_date=d(r["start"]), end_date=d(r["end"]))
        for n in (1, 2):
            b = "b%d_" % n
            live = r.get(b + "live")
            x.update({"bt%d_eligible" % n: bool(r[b + "el"]), "bt%d_done" % n: r[b + "k"] == "done", "bt%d_due" % n: d(r[b + "due"]),
                      "bt%d_due_iso" % n: r[b + "due"], "bt%d_status" % n: BY_GROUP[r[b + "k"]] if live else r[b + "st"],
                      "bt%d_remark" % n: (r[b + "st"] + (" · " + r[b + "rm"] if r[b + "rm"] else "")) if live else r[b + "rm"], "bt%d_lab" % n: r[b + "lab"]})
        out.append(x)
    return out


def ops_bt_exact(table, types=None):
    """The 'Blood Test' tab -> the old dashboard's bt_rows exactly as its getData() built them (no cleaning, no live tests, Test 2 due = end date).
    The Program ops page (the old dashboard) uses these, so its Blood Test tab matches the old one number for number."""
    if not table: return []
    G = Grid(table, types)
    h = {}
    for i, x in enumerate(table[0]): h[str(x).replace("\n", " ").strip()] = i
    def col(name):
        if name in h: return h[name]
        return next((i for k, i in h.items() if k.lower() == name.lower()), -1)
    BT = {k: col(n) for k, n in (("month", "Month"), ("plan", "Plan"), ("order", "Order ID"), ("dur", "Program Duration"), ("name", "Billing Name"),
                                  ("phone", "BillingPhone"), ("s1", "Test 1 Status"), ("r1", "Remark"), ("s2", "Test 2 Status"), ("r2", "Remark of Test 2"),
                                  ("coach", "Coach Name"), ("ps", "Program Status"), ("start", "Program Start Date"), ("end", "End Date"),
                                  ("prev", "previous plan"), ("done2", "2nd done or not"))}
    labs = [i for i, x in enumerate(table[0]) if str(x).replace("\n", " ").strip().lower() == "lab name"]
    BT["lab1"], BT["lab2"] = (labs + [-1, -1])[0], (labs + [-1, -1])[1]
    g = lambda i, k: G.s(i, BT[k]) if BT[k] >= 0 else ""
    out = []
    for i in G.rows():
        if not (g(i, "name") or g(i, "order") or g(i, "phone")): continue
        plan, ps = g(i, "plan"), g(i, "ps")
        try: dur = int(re.match(r"-?\d+", g(i, "dur")).group(0))
        except (AttributeError, ValueError): dur = 0
        if ps.lower() in EXCLUDED: continue
        prev, prev2 = g(i, "prev"), g(i, "done2").lower()
        s1, s2 = g(i, "s1"), g(i, "s2")
        el1 = False
        if dur >= 90:
            if plan.lower() in FRESH: el1 = True
            elif plan.lower() == "repeat":
                m = re.search(r"\d+", prev)
                el1 = (int(m.group(0)) if m else 0) < 90 or prev2 != "done"
        start, end = G.d(i, BT["start"]), G.d(i, BT["end"])
        out.append(dict(month=g(i, "month"), plan=plan, order_id=g(i, "order"), name=g(i, "name"), phone=g(i, "phone"), duration=dur,
                        coach=g(i, "coach"), prog_status=ps, prev_plan=prev, prev_bt2_done=prev2 or "not done", start_date=_fmt(start), end_date=_fmt(end),
                        bt1_eligible=el1, bt1_done=s1.lower() in DONE, bt1_due=_fmt(start), bt1_due_iso=_iso(start), bt1_status=s1, bt1_remark=g(i, "r1"),
                        bt1_lab=g(i, "lab1"), bt2_eligible=dur >= 90, bt2_done=s2.lower() in DONE, bt2_due=_fmt(end), bt2_due_iso=_iso(end), bt2_status=s2,
                        bt2_remark=g(i, "r2"), bt2_lab=g(i, "lab2")))
    return out
