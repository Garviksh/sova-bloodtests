"""Parity check: the dashboard's rules (legacy_bt) on the BigQuery copy vs the sheet's Apps Script numbers. Run in the source dir."""
import json, sys
import main as M, legacy_bt as LB
ORDER = ("done", "booked", "inprogress", "followup", "noaction", "notserviceable", "noteligible", "blank")
def bt(rows, n, f, t):
    sg = dict.fromkeys(ORDER, 0); tot = 0
    for r in rows:
        if r["excluded"]: continue
        d = r["b%d_due" % n]
        if not d or not f <= d <= t: continue
        tot += 1; k, el = r["b%d_k" % n], r["b%d_el" % n]
        sg["noteligible" if not el and k not in ("notserviceable", "done") else k] += 1
    return [tot] + [sg[k] for k in ORDER]
def side(st, k, b2):
    v = (st or "").strip().lower()
    if k == "done": return "done"
    if v in ("program refund", "dropout", "non-serviceable", "international client"): return "out"
    if b2 and v == "not eligible": return "out"
    return "noaction" if k in ("noaction", "noteligible") else "pending"
def combo(rows, f, t):
    base = [r for r in rows if not r["excluded"] and r["b2_el"] and r["b2_due"] and f <= r["b2_due"] <= t]
    a = [r for r in base if side(r["b1_st"], r["b1_k"], False) != "out" and side(r["b2_st"], r["b2_k"], True) != "out"]
    d1, d2 = (lambda r: r["b1_k"] == "done"), (lambda r: r["b2_k"] == "done")
    return [len(base), len(a), sum(d1(r) and d2(r) for r in a), sum(d1(r) and not d2(r) for r in a), sum(not d1(r) and d2(r) for r in a),
            sum(not d1(r) and not d2(r) for r in a), sum(side(r["b1_st"], r["b1_k"], False) == "pending" or side(r["b2_st"], r["b2_k"], True) == "pending" for r in a)]
table = M.legacy_table(LB.SHEET)
for dd in (False, True):
    rows, dups = LB.legacy_rows(table, dedupe=dd)
    out = {"rows": sum(not r["excluded"] for r in rows), "bt1_nov": bt(rows, 1, "2026-11-01", "2026-11-30"), "bt1_oct": bt(rows, 1, "2026-10-01", "2026-10-31"),
           "bt2_nov": bt(rows, 2, "2026-11-01", "2026-11-30"), "bt2_oct": bt(rows, 2, "2026-10-01", "2026-10-31"), "combo_oct": combo(rows, "2026-10-01", "2026-10-31"),
           "bt1_day": bt(rows, 1, "2026-11-03", "2026-11-03")}
    print("DEDUPE" if dd else "AS-SHEET", "dups", dups, json.dumps(out, separators=(",", ":")))
d = M.bt_dashboard_data()["meta"]
print("META", json.dumps(d, default=str))
