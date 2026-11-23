"""Reconciliation: legacy sheet -> BigQuery copy -> panel, against the old Apps Script dashboard's own getData(). Run in the deployed source dir."""
import collections, json, sys
import main as M, legacy_bt as LB

def trim(rows):
    out = []
    for r in rows:
        r = [("" if c is None else str(c)) for c in r]
        while r and not r[-1].strip(): r.pop()
        out.append(r)
    while out and not out[-1]: out.pop()
    return [r for r in out if any(c.strip() for c in r)]

print("== 1. COMPLETENESS: live sheet (as shown) vs the BigQuery copy the panel reads")
FILES = {"Blood Test": "bt", "Program-Main": "pm", "Blood Test Dashboard": "btd", "BT spend tracking": "spend", "BT_Additonal Parameters": "params", "H. pylori+MultiVitamin": "hp"}
all_ok = True
for sheet, f in FILES.items():
    live = trim(json.load(open("tabs/%s.json" % f))["formatted"])
    copy = trim(M.legacy_table(sheet))
    diff = [(i + 1, j, l[j] if j < len(l) else "", c[j] if j < len(c) else "") for i, (l, c) in enumerate(zip(live, copy))
            for j in range(max(len(l), len(c))) if (l[j] if j < len(l) else "").strip() != (c[j] if j < len(c) else "").strip()]
    ok = len(live) == len(copy) and not diff
    all_ok &= ok
    print("%-26s live rows %6d | copy rows %6d | cells differing %5d  %s" % (sheet, len(live), len(copy), len(diff), "MATCH" if ok else "DIFF " + str(diff[:3])))
print("COMPLETE" if all_ok else "INCOMPLETE")

print("\n== 2. FIELD-BY-FIELD: original getData() (old dashboard) vs the panel's port")
ref = json.load(open("ref.json"))
if ref.get("error"): print("REF ERROR", ref["error"][:300]); sys.exit(1)
ours_rows, ours_months = LB.ops_data(M.legacy_table(LB.OPS), M.legacy_types(LB.OPS))
ours_bt = LB.ops_bt_exact(M.legacy_table(LB.SHEET), M.legacy_types(LB.SHEET))
print("cell types kept: Program-Main %s, Blood Test %s" % (M.legacy_types(LB.OPS) is not None, M.legacy_types(LB.SHEET) is not None))
import re as _re, datetime as _dt
JSD = _re.compile(r"^[A-Z][a-z]{2} ([A-Z][a-z]{2}) (\d{2}) (\d{4}) \d{2}:\d{2}:\d{2} GMT")
def jsdate(v):
    """The old dashboard printed a date cell in a text field as 'Sat Jul 25 2026 00:00:00 GMT+0530 (...)'; the panel prints '25 Jul 26'."""
    m = JSD.match(v) if isinstance(v, str) else None
    return "%d %s %s" % (int(m.group(2)), m.group(1), m.group(3)[2:]) if m else v
def cmp(name, R, O):
    print("%s: old %d rows, panel %d rows" % (name, len(R), len(O)))
    if len(R) != len(O):
        rk = collections.Counter((r["order_id"], r["name"]) for r in R); ok_ = collections.Counter((r["order_id"], r["name"]) for r in O)
        print("   only in old:", list((rk - ok_).items())[:5], "| only in panel:", list((ok_ - rk).items())[:5])
    bad, fmt_only = collections.defaultdict(list), collections.Counter()
    for r, o in zip(R, O):
        for k in r:
            a, b = r[k], o.get(k)
            if isinstance(a, float) and a.is_integer(): a = int(a)
            if a == b or (a in ("", None) and b in ("", None)): continue
            if jsdate(a) == b: fmt_only[k] += 1; continue
            bad[k].append((r.get("order_id"), a, b))
    if fmt_only: print("   same date, shown as '25 Jul 26' instead of JavaScript's long date text: %s" % dict(fmt_only))
    fields = len(R[0]) if R else 0
    print("   fields compared: %d x %d rows = %d values; mismatching values: %d" % (fields, len(R), fields * len(R), sum(len(v) for v in bad.values())))
    for k, v in sorted(bad.items(), key=lambda kv: -len(kv[1])): print("   %-18s %5d  e.g. %s" % (k, len(v), v[:3]))
    return bad
b1 = cmp("Program-Main rows (Onboarding / Dr. consult / Renewal)", ref["rows"], ours_rows)
print("   months: %s" % ("MATCH" if ref["months"] == ours_months else "DIFF old %s panel %s" % (ref["months"][-4:], ours_months[-4:])))
b2 = cmp("Blood Test rows (old dashboard's BT tab)", ref["bt_rows"], ours_bt)

print("\n== 2b. HEADLINE NUMBERS of the other views (old vs panel)")
for f, t, lab in (("2026-09-01", "2026-09-30", "Sep 26"), ("2026-10-01", "2026-10-31", "Oct 26"), ("2026-11-01", "2026-11-30", "Nov 26")):
    def heads(rows):
        inr = lambda v: bool(v) and f <= v <= t
        return dict(paid=sum(inr(r["paid_iso"]) for r in rows), started=sum(inr(r["start_iso"]) for r in rows),
                    dc1_due=sum(r["dc_eligible"] and inr(r["dc1_due_iso"]) for r in rows), dc1_done=sum(r["dc_eligible"] and inr(r["dc1_due_iso"]) and r["dc1_status"].lower() == "yes" for r in rows),
                    dc2_due=sum(r["dc_eligible"] and inr(r["dc2_due_iso"]) for r in rows), ending=sum(inr(r["end_iso"]) for r in rows),
                    chase_from=sum(inr(r["renewal_iso"]) for r in rows))
    a, b = heads(ref["rows"]), heads(ours_rows)
    print("%s old   %s" % (lab, a)); print("%s panel %s %s" % (lab, b, "MATCH" if a == b else "DIFF"))

print("\n== 3. BLOOD TEST CARDS: old dashboard vs new Blood Test dashboard (/bt)")
ORDER = ("done", "booked", "inprogress", "followup", "noaction", "blank", "noteligible", "notserviceable")
def old_cards(rows, n, f, t):
    sg = collections.Counter(); tot = 0
    for r in rows:
        d = r["bt%d_due_iso" % n]
        if not d or not f <= d <= t: continue
        tot += 1; k = LB.card_key(r["bt%d_status" % n]); el = r["bt%d_eligible" % n]
        sg["noteligible" if not el and k not in ("notserviceable", "done") else k] += 1
    return [tot] + [sg[k] for k in ORDER]
def new_cards(rows, n, f, t, src=None):
    sg = collections.Counter(); tot = 0
    for r in rows:
        if r["excluded"] or (src and r["src"] not in src): continue
        d = r["b%d_due" % n]
        if not d or not f <= d <= t: continue
        tot += 1; k, el = r["b%d_k" % n], r["b%d_el" % n]
        sg["noteligible" if not el and k not in ("notserviceable", "done") else k] += 1
    return [tot] + [sg[k] for k in ORDER]
leg, _ = LB.legacy_rows(M.legacy_table(LB.SHEET), types=M.legacy_types(LB.SHEET))
uni = M.bt_dashboard_data(raw=True)
print("columns: total, done, booked, in progress, follow up, no action, no response, not eligible, not serviceable")
for n in (1, 2):
    for f, t, lab in (("2026-08-01", "2026-08-31", "Aug 26"), ("2026-09-01", "2026-09-30", "Sep 26"), ("2026-10-01", "2026-10-31", "Oct 26"), ("2026-11-01", "2026-11-30", "Nov 26")):
        print("BT%d %s old dashboard   %s" % (n, lab, old_cards(ref["bt_rows"], n, f, t)))
        print("BT%d %s ops page (old)  %s" % (n, lab, old_cards(ours_bt, n, f, t)))
        print("BT%d %s /bt sheet only  %s" % (n, lab, new_cards(leg, n, f, t)))
        print("BT%d %s /bt sheet+live  %s" % (n, lab, new_cards(uni, n, f, t)))
print("COMPARE-DONE")
