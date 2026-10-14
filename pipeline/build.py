"""
Build the Power BI star schema in data/ from SEC filings and the driver model.

    python -m pipeline.build

Every run reconciles the extracted lines to Microsoft's reported subtotals and the segment /
revenue breakdowns to consolidated revenue, and checks that forecasts and bridges add up.
Any break stops the build, so the dashboard never shows numbers that don't tie out.
"""
import csv
from datetime import date

from . import extract, model
from .model import SCENARIOS, SEGMENTS

DATA = model.ROOT / "data"
FIRST_QUARTER_FY = 2017  # FY2016 quarters weren't restated for ASC 606, so derived Q4s don't tie out
FORECAST_YEARS = [2027, 2028, 2029]
TOLERANCE = 1.5  # $M; SEC values are in whole dollars, so this only absorbs rounding in derived lines

ACCOUNTS = [  # key, code, name, group, sign, sort
    (1, "REV", "Revenue", "Revenue", 1, 10),
    (2, "COGS", "Cost of revenue", "Cost of revenue", -1, 20),
    (3, "RD", "Research and development", "Operating expenses", -1, 30),
    (4, "SM", "Sales and marketing", "Operating expenses", -1, 40),
    (5, "GA", "General and administrative", "Operating expenses", -1, 50),
    (6, "IMP", "Impairment, integration and restructuring", "Operating expenses", -1, 60),
    (7, "OIE", "Other income (expense), net", "Other income (expense)", 1, 70),
    (8, "TAX", "Provision for income taxes", "Income taxes", -1, 80),
]
ACCOUNT_KEY = {code: key for key, code, *_ in ACCOUNTS}
VERSIONS = [  # key, version, scenario, type, sort
    (1, "Actual", "Actual", "Actual", 1),
    (2, "Forecast - Base", "Base", "Forecast", 2),
    (3, "Forecast - Upside", "Upside", "Forecast", 3),
    (4, "Forecast - Downside", "Downside", "Forecast", 4),
    (5, "Plan (model, prior year)", "Plan", "Plan", 5),
]
VERSION_KEY = {"Actual": 1, "Base": 2, "Upside": 3, "Downside": 4, "Plan": 5}
SEGMENT_KEY = {s: i + 1 for i, s in enumerate(SEGMENTS)}
SEGMENT_SHORT = {"Productivity and Business Processes": "PBP", "Intelligent Cloud": "IC", "More Personal Computing": "MPC"}
KPI_NAMES = {
    "EPS_DILUTED": "Diluted EPS", "EPS_BASIC": "Basic EPS", "SHARES_DILUTED": "Diluted shares (M)",
    "OCF": "Operating cash flow", "CAPEX": "Capital expenditures", "FCF": "Free cash flow",
    "SBC": "Stock-based compensation", "DIVIDENDS": "Dividends paid", "BUYBACKS": "Share repurchases",
    "CLOUD": "Microsoft Cloud revenue",
}


class ReconciliationError(Exception):
    pass


def period_key(fy, q):
    return fy * 10 + q


def _period_end(fy, q):
    if q in (0, 4):
        return date(fy, 6, 30)
    return {1: date(fy - 1, 9, 30), 2: date(fy - 1, 12, 31), 3: date(fy, 3, 31)}[q]


def _write(name, rows, columns):
    DATA.mkdir(exist_ok=True)
    with open(DATA / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})
    return len(rows)


def build():
    pl, kpi, checks = extract.consolidated()
    seg, brk = extract.dimensional()
    drivers, assumption_rows = model.load_assumptions()
    check_rows = []

    def check(area, period, what, computed, reported, tol=TOLERANCE):
        ok = abs(computed - reported) <= tol
        check_rows.append({"Area": area, "Period": period, "Check": what, "Computed": computed,
                           "Reported": reported, "Difference": computed - reported, "Status": "Pass" if ok else "Fail"})
        return ok

    # ---- actuals: keep annual from FY2010, quarters only where the year's quarters reconcile ----
    bad_quarters = set()
    for c in checks:
        fy, q = c["period"]
        label = f"FY{fy}" + (f" Q{q}" if q else "")
        if q and fy < FIRST_QUARTER_FY:
            continue
        if not check("Consolidated P&L", label, c["check"], c["computed"], c["reported"]) and q:
            bad_quarters.add((fy, q))
    if bad_quarters:
        raise ReconciliationError(f"Quarters failing reconciliation: {sorted(bad_quarters)}")
    keep = lambda fy, q: q == 0 or fy >= FIRST_QUARTER_FY  # noqa: E731
    actual_periods = sorted({(fy, q) for fy, q, _ in pl if keep(fy, q)})
    last_fy = max(fy for fy, q in actual_periods if q == 0)

    for (fy, q) in sorted({(k[0], k[1]) for k in brk}):
        label = f"FY{fy}" + (f" Q{q}" if q else "")
        for group in ("Product line", "Revenue type", "Geography"):
            total = sum(v for k, v in brk.items() if k[:3] == (fy, q, group))
            check("Revenue breakdown", label, f"{group} sums to revenue", total, pl[(fy, q, "REV")])
        for metric, acc in (("Revenue", None), ("Operating income", None)):
            total = sum(v for k, v in seg.items() if k[:2] == (fy, q) and k[3] == metric)
            s = model.pl_summary({(fy, a): pl.get((fy, q, a), 0.0) for a in ACCOUNT_KEY}, fy)
            check("Segments", label, f"Segment {metric.lower()} sums to consolidated", total,
                  s["rev"] if metric == "Revenue" else s["oi"])
        for s_name in SEGMENTS:
            if (fy, q, s_name, "Cost of revenue") not in seg:
                continue  # segment costs were first disclosed in FY2025 (ASU 2023-07); FY2024 quarters have none
            r = {m: seg[(fy, q, s_name, m)] for m in ("Revenue", "Cost of revenue", "Operating expenses", "Operating income")}
            check("Segments", label, f"{SEGMENT_SHORT[s_name]}: revenue − costs = operating income",
                  r["Revenue"] - r["Cost of revenue"] - r["Operating expenses"], r["Operating income"])

    # ---- forecasts ----
    runs = {s: model.project(seg, pl, kpi, drivers, s, last_fy, FORECAST_YEARS) for s in SCENARIOS}
    first = FORECAST_YEARS[0]
    quarters = {s: model.phase_quarters(runs[s], seg, pl, first) for s in SCENARIOS}
    for s, run in runs.items():
        for fy in FORECAST_YEARS:
            seg_sum = sum(run["segment"][(fy, x, "Revenue")] for x in SEGMENTS)
            check("Forecast", f"FY{fy} {s}", "Segment revenue = consolidated", seg_sum, run["pl"][(fy, "REV")], 1e-6)
        seg_q, pl_q, _ = quarters[s]
        for acc in ("REV", "COGS", "RD", "SM", "GA", "OIE"):
            check("Forecast", f"FY{first} {s}", f"Quarters sum to year: {acc}",
                  sum(pl_q[(first, q, acc)] for q in (1, 2, 3, 4)), run["pl"][(first, acc)], 1e-6)

    plan_d = model.plan_drivers(drivers, seg, pl, kpi, last_fy - 1)
    plan = model.project(seg, pl, kpi, {**drivers, **plan_d}, "Plan", last_fy - 1, [last_fy])

    failures = [c for c in check_rows if c["Status"] == "Fail"]
    if failures:
        raise ReconciliationError(f"{len(failures)} checks failed, e.g. {failures[:3]}")

    # ---- dimensions ----
    periods = set(actual_periods) | {(fy, 0) for fy in FORECAST_YEARS} | {(first, q) for q in (1, 2, 3, 4)}
    dim_period = []
    for fy, q in sorted(periods):
        dim_period.append({
            "PeriodKey": period_key(fy, q), "Fiscal Year": fy, "Fiscal Quarter": q,
            "Period Type": "Year" if q == 0 else "Quarter",
            # "E" marks estimates, the usual convention in forecasts and broker models (FY2027E, Q1 FY27E).
            "Fiscal Year Label": f"FY{fy}" + ("E" if fy > last_fy else ""),
            "Period Label": (f"FY{fy}" if q == 0 else f"Q{q} FY{str(fy)[2:]}") + ("E" if fy > last_fy else ""),
            "Quarter Label": "" if q == 0 else f"Q{q}",
            "Period End": _period_end(fy, q).isoformat(), "Sort": period_key(fy, q),
            "Is Forecast": fy > last_fy, "Years From Last Actual": fy - last_fy,
            "Timeline": "Forecast" if fy > last_fy else "Actual",
        })
    dim_account = [{"AccountKey": k, "Code": c, "Account": n, "Line Group": g, "Sign": s, "Sort": o} for k, c, n, g, s, o in ACCOUNTS]
    dim_version = [{"VersionKey": k, "Version": v, "Scenario": s, "Version Type": t, "Sort": o} for k, v, s, t, o in VERSIONS]
    probability = {s: drivers[("Scenario probability", "Consolidated", s)][first] / 100 for s in SCENARIOS}
    dim_scenario = [{"Scenario": s, "Sort": i + 1, "Probability": probability[s]} for i, s in enumerate(SCENARIOS)]
    dim_segment = [{"SegmentKey": SEGMENT_KEY[s], "Segment": s, "Short Name": SEGMENT_SHORT[s], "Sort": SEGMENT_KEY[s]} for s in SEGMENTS]

    # ---- facts ----
    fact_pl, fact_seg, fact_kpi = [], [], []
    for (fy, q, acc), v in pl.items():
        if keep(fy, q):
            fact_pl.append({"PeriodKey": period_key(fy, q), "AccountKey": ACCOUNT_KEY[acc], "VersionKey": 1, "Amount": v})
    for (fy, q, s, m), v in seg.items():
        fact_seg.append({"PeriodKey": period_key(fy, q), "SegmentKey": SEGMENT_KEY[s], "VersionKey": 1, "Metric": m, "Amount": v})
    for (fy, q, m), v in kpi.items():
        if keep(fy, q) and (fy, q) in set(actual_periods):
            fact_kpi.append({"PeriodKey": period_key(fy, q), "VersionKey": 1, "Metric": KPI_NAMES[m], "Value": v})
    for fy, q in actual_periods:
        if (fy, q, "OCF") in kpi and (fy, q, "CAPEX") in kpi:
            fact_kpi.append({"PeriodKey": period_key(fy, q), "VersionKey": 1, "Metric": KPI_NAMES["FCF"],
                             "Value": kpi[(fy, q, "OCF")] - kpi[(fy, q, "CAPEX")]})
    for (fy, q, group, item), v in brk.items():
        if group == "Microsoft Cloud":
            fact_kpi.append({"PeriodKey": period_key(fy, q), "VersionKey": 1, "Metric": KPI_NAMES["CLOUD"], "Value": v})

    def add_forecast(version, run_pl, run_seg, run_kpi, q=0):
        vk = VERSION_KEY[version]
        for key, v in run_pl.items():
            fy, qq, acc = key if len(key) == 3 else (key[0], q, key[1])
            fact_pl.append({"PeriodKey": period_key(fy, qq), "AccountKey": ACCOUNT_KEY[acc], "VersionKey": vk, "Amount": v})
        for key, v in run_seg.items():
            fy, qq, s, m = key if len(key) == 4 else (key[0], q, key[1], key[2])
            fact_seg.append({"PeriodKey": period_key(fy, qq), "SegmentKey": SEGMENT_KEY[s], "VersionKey": vk, "Metric": m, "Amount": v})
        for key, v in run_kpi.items():
            fy, qq, m = key if len(key) == 3 else (key[0], q, key[1])
            fact_kpi.append({"PeriodKey": period_key(fy, qq), "VersionKey": vk, "Metric": KPI_NAMES[m], "Value": v})
        fcf_periods = {(k[0], k[1]) if len(k) == 3 else (k[0], q) for k in run_kpi}
        for fy, qq in fcf_periods:
            get = (lambda m: run_kpi.get((fy, qq, m), run_kpi.get((fy, m)))) if True else None
            fact_kpi.append({"PeriodKey": period_key(fy, qq), "VersionKey": vk, "Metric": KPI_NAMES["FCF"],
                             "Value": get("OCF") - get("CAPEX")})

    for s in SCENARIOS:
        add_forecast(s, runs[s]["pl"], runs[s]["segment"], runs[s]["kpi"])
        seg_q, pl_q, kpi_q = quarters[s]
        add_forecast(s, pl_q, seg_q, kpi_q)
    add_forecast("Plan", plan["pl"], plan["segment"], plan["kpi"])

    # ---- revenue mix (actuals) ----
    fact_mix = [{"PeriodKey": period_key(fy, q), "Breakdown": g, "Item": i, "Amount": v}
                for (fy, q, g, i), v in brk.items() if g != "Microsoft Cloud"]

    # ---- bridges, sensitivity, backtest ----
    # One fixed position per step label (Power BI sorts Step by Step Order, which must be 1:1).
    step_order = {"Revenue growth": 1, "Gross margin rate": 2, "Operating expense leverage": 3,
                  "Other income (expense)": 4, "Tax rate": 5, "Share count": 6,
                  **{x: 10 + i for i, x in enumerate(SEGMENTS)}}
    fact_bridge = []
    a0 = model.pl_summary(pl, last_fy)
    for s in SCENARIOS:
        a1 = model.pl_summary(runs[s]["pl"], first)
        ni, eps = model.net_income_bridge(a0, a1, kpi[(last_fy, 0, "SHARES_DILUTED")], runs[s]["kpi"][(first, "SHARES_DILUTED")])
        for bridge, steps in (("Net income", ni), ("Diluted EPS", eps)):
            for step, v in steps:
                kind = {"Start": "Start", "End": "End"}.get(step, "Change")
                label = {"Start": f"FY{last_fy} actual", "End": f"FY{first} {s.lower()}"}.get(step, step)
                order = {"Start": 0, "End": 99}.get(step, step_order.get(step))
                fact_bridge.append({"Scenario": s, "Bridge": bridge, "Step": label, "Step Order": order, "Step Type": kind, "Value": v})
        steps = [(f"FY{last_fy} actual", a0["oi"])]
        steps += [(x, runs[s]["segment"][(first, x, "Operating income")] - seg[(last_fy, 0, x, "Operating income")]) for x in SEGMENTS]
        steps += [(f"FY{first} {s.lower()}", a1["oi"])]
        for i, (step, v) in enumerate(steps):
            kind = "Start" if i == 0 else "End" if i == len(steps) - 1 else "Change"
            order = {"Start": 0, "End": 99}.get(kind, step_order.get(step))
            fact_bridge.append({"Scenario": s, "Bridge": "Operating income by segment", "Step": step, "Step Order": order,
                                "Step Type": kind, "Value": v})
    sensitivity = model.eps_sensitivity(runs["Base"], seg, pl, kpi, drivers, last_fy, first)
    for r in sensitivity:
        dg, dm = r["Revenue Growth Delta (pp)"], r["Operating Margin Delta (bp)"]
        r["Revenue Growth Label"] = "Base" if dg == 0 else f"{dg:+d}pp"
        r["Operating Margin Label"] = "Base" if dm == 0 else f"{dm:+d}bp"
    backtest = model.backtest(pl, kpi)

    # ---- assumptions (long format, plus the derived plan drivers) ----
    fact_assumptions = []
    for row in assumption_rows:
        for col, v in row.items():
            if col.startswith("FY") and v.strip():
                fact_assumptions.append({"Driver": row["Driver"], "Segment": row["Segment"], "Scenario": row["Scenario"],
                                         "Fiscal Year": int(col[2:]), "Value": float(v), "Unit": row["Unit"],
                                         "Rationale": row["Rationale"]})
    units = {"Revenue growth": "%", "Gross margin change": "bp", "Operating expense growth": "%", "Other income (expense)": "$M",
             "Effective tax rate": "%", "Diluted share count change": "%", "Operating cash flow % of revenue": "%", "Capex % of revenue": "%"}
    for (driver, segment, scen), years in plan_d.items():
        for fy, v in years.items():
            fact_assumptions.append({"Driver": driver, "Segment": segment, "Scenario": scen, "Fiscal Year": fy, "Value": v,
                                     "Unit": units[driver], "Rationale": f"Model rules applied to FY{last_fy - 1} actuals (no hindsight)"})

    counts = {
        "dim_period": _write("dim_period", dim_period, list(dim_period[0])),
        "dim_account": _write("dim_account", dim_account, list(dim_account[0])),
        "dim_version": _write("dim_version", dim_version, list(dim_version[0])),
        "dim_scenario": _write("dim_scenario", dim_scenario, list(dim_scenario[0])),
        "dim_segment": _write("dim_segment", dim_segment, list(dim_segment[0])),
        "fact_pl": _write("fact_pl", fact_pl, ["PeriodKey", "AccountKey", "VersionKey", "Amount"]),
        "fact_segment": _write("fact_segment", fact_seg, ["PeriodKey", "SegmentKey", "VersionKey", "Metric", "Amount"]),
        "fact_kpi": _write("fact_kpi", fact_kpi, ["PeriodKey", "VersionKey", "Metric", "Value"]),
        "fact_revenue_mix": _write("fact_revenue_mix", fact_mix, ["PeriodKey", "Breakdown", "Item", "Amount"]),
        "fact_bridge": _write("fact_bridge", fact_bridge, ["Scenario", "Bridge", "Step", "Step Order", "Step Type", "Value"]),
        "fact_sensitivity": _write("fact_sensitivity", sensitivity, list(sensitivity[0])),
        "fact_backtest": _write("fact_backtest", backtest, list(backtest[0])),
        "fact_assumptions": _write("fact_assumptions", fact_assumptions, list(fact_assumptions[0])),
        "fact_checks": _write("fact_checks", check_rows, list(check_rows[0])),
    }
    return {"last_actual_year": last_fy, "checks": len(check_rows), "rows": counts, "runs": runs, "plan": plan,
            "pl": pl, "kpi": kpi, "seg": seg}


if __name__ == "__main__":
    out = build()
    print(f"Last actual year FY{out['last_actual_year']}; {out['checks']} reconciliation checks passed.")
    for name, n in out["rows"].items():
        print(f"  data/{name}.csv  {n:>6,} rows")
