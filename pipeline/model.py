"""
Driver-based forecast for Microsoft, built bottom-up from the three reporting segments.

For each scenario and forecast year t (drivers from assumptions/assumptions.csv):

  Segment revenue      R(s,t)  = R(s,t-1) × (1 + revenue growth(s,t))
  Segment gross margin GM%(s,t) = GM%(s,t-1) + gross margin change(s,t)          [bp]
  Segment cost of rev  C(s,t)  = R(s,t) × (1 − GM%(s,t))
  Segment opex         X(s,t)  = X(s,t-1) × (1 + opex growth(s,t))                [semi-fixed costs:
                                                                                    operating leverage]
  Segment op. income   OI(s,t) = R − C − X                                        (reconciles exactly)

  Consolidated         Revenue, cost of revenue and opex are segment sums; opex is split into
                       R&D / S&M / G&A by the last actual year's mix (segments don't report it).
  Pretax income        PBT = OI + other income (expense)
  Tax                  = PBT × effective tax rate
  Net income           = PBT − tax
  Diluted shares       S(t) = S(t-1) × (1 + share count change)
  Diluted EPS          = net income ÷ diluted shares
  Cash                 OCF = revenue × OCF%;  capex = revenue × capex%;  FCF = OCF − capex

Also produced here: quarterly phasing of the first forecast year, net income / EPS / segment
bridges, an EPS sensitivity grid, a "plan vs actual" for the last actual year (the model run from
the year before), and a one-year-ahead backtest of the model's mechanics since FY2013.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEGMENTS = ["Productivity and Business Processes", "Intelligent Cloud", "More Personal Computing"]
SCENARIOS = ["Base", "Upside", "Downside"]
OPEX_LINES = ["RD", "SM", "GA"]


# ---------- assumptions ----------
def load_assumptions(path=ROOT / "assumptions" / "assumptions.csv"):
    """{(driver, segment, scenario): {fiscal_year: value}} plus the rows themselves (for the report)."""
    drivers, rows = {}, []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            years = {int(k[2:]): float(v) for k, v in row.items() if k.startswith("FY") and v.strip()}
            drivers[(row["Driver"], row["Segment"], row["Scenario"])] = years
            rows.append(row)
    return drivers, rows


def _pct(drivers, name, seg, scen, fy):
    return drivers[(name, seg, scen)][fy] / 100.0


# ---------- core engine ----------
def project(actual_seg, actual_pl, actual_kpi, drivers, scenario, last_fy, years, driver_years=None):
    """
    Run the driver model from `last_fy` actuals for the given forecast years.
    actual_seg: {(fy, 0, segment, metric): $M}; actual_pl: {(fy, 0, account): $M}; actual_kpi likewise.
    driver_years maps each forecast year to the assumption column to use (for plan/backtest reruns).
    Returns {"segment": {(fy, seg, metric): v}, "pl": {(fy, acc): v}, "kpi": {(fy, metric): v}}.
    """
    driver_years = driver_years or {fy: fy for fy in years}
    seg_out, pl_out, kpi_out = {}, {}, {}
    rev = {s: actual_seg[(last_fy, 0, s, "Revenue")] for s in SEGMENTS}
    gm = {s: 1 - actual_seg[(last_fy, 0, s, "Cost of revenue")] / rev[s] for s in SEGMENTS}
    opex = {s: actual_seg[(last_fy, 0, s, "Operating expenses")] for s in SEGMENTS}
    opex_total = sum(actual_pl[(last_fy, 0, a)] for a in OPEX_LINES)
    mix = {a: actual_pl[(last_fy, 0, a)] / opex_total for a in OPEX_LINES}
    shares = actual_kpi[(last_fy, 0, "SHARES_DILUTED")]

    for fy in years:
        d = driver_years[fy]
        for s in SEGMENTS:
            rev[s] *= 1 + _pct(drivers, "Revenue growth", s, scenario, d)
            gm[s] += drivers[("Gross margin change", s, scenario)][d] / 10000.0
            opex[s] *= 1 + _pct(drivers, "Operating expense growth", s, scenario, d)
            cost = rev[s] * (1 - gm[s])
            seg_out.update({(fy, s, "Revenue"): rev[s], (fy, s, "Cost of revenue"): cost,
                            (fy, s, "Operating expenses"): opex[s], (fy, s, "Operating income"): rev[s] - cost - opex[s]})
        total_rev = sum(rev.values())
        total_cost = sum(seg_out[(fy, s, "Cost of revenue")] for s in SEGMENTS)
        total_opex = sum(opex.values())
        oie = drivers[("Other income (expense)", "Consolidated", scenario)][d]
        pbt = total_rev - total_cost - total_opex + oie
        tax = pbt * _pct(drivers, "Effective tax rate", "Consolidated", scenario, d)
        pl_out.update({(fy, "REV"): total_rev, (fy, "COGS"): total_cost, (fy, "OIE"): oie, (fy, "TAX"): tax})
        for a in OPEX_LINES:
            pl_out[(fy, a)] = total_opex * mix[a]
        shares *= 1 + _pct(drivers, "Diluted share count change", "Consolidated", scenario, d)
        ni = pbt - tax
        ocf = total_rev * _pct(drivers, "Operating cash flow % of revenue", "Consolidated", scenario, d)
        capex = total_rev * _pct(drivers, "Capex % of revenue", "Consolidated", scenario, d)
        kpi_out.update({(fy, "SHARES_DILUTED"): shares, (fy, "EPS_DILUTED"): ni / shares,
                        (fy, "OCF"): ocf, (fy, "CAPEX"): capex})
    return {"segment": seg_out, "pl": pl_out, "kpi": kpi_out}


def pl_summary(pl, fy):
    """Subtotals from leaf lines for one year (dict keyed by account, either {(fy, acc)} or {(fy, 0, acc)})."""
    get = (lambda a: pl.get((fy, a), pl.get((fy, 0, a), 0.0)))
    rev, cogs = get("REV"), get("COGS")
    opex = sum(get(a) for a in OPEX_LINES) + get("IMP")
    oi = rev - cogs - opex
    pbt = oi + get("OIE")
    ni = pbt - get("TAX")
    return {"rev": rev, "cogs": cogs, "gp": rev - cogs, "opex": opex, "oi": oi, "oie": get("OIE"),
            "pbt": pbt, "tax": get("TAX"), "ni": ni, "etr": get("TAX") / pbt if pbt else 0.0}


# ---------- quarterly phasing ----------
def phase_quarters(annual, actual_seg, actual_pl, fy, history=(2024, 2025, 2026)):
    """
    Split forecast year `fy` into quarters using each segment's average quarterly share of the
    year (revenue, cost of revenue and opex separately) over the years in `history` that disclose it. Other income is spread
    evenly; tax uses the year's effective rate; shares are held at the annual average.
    """
    share = {}
    for s in SEGMENTS:
        for m in ("Revenue", "Cost of revenue", "Operating expenses"):
            # Only years with all four quarters disclosed (segment costs start in FY2025).
            years = [y for y in history if all((y, q, s, m) in actual_seg for q in (0, 1, 2, 3, 4))]
            shares = [sum(actual_seg[(y, q, s, m)] / actual_seg[(y, 0, s, m)] for y in years) / len(years)
                      for q in (1, 2, 3, 4)]
            total = sum(shares)
            share[(s, m)] = [x / total for x in shares]
    seg_q, pl_q, kpi_q = {}, {}, {}
    year = pl_summary(annual["pl"], fy)
    etr = year["etr"]
    opex_total = sum(annual["pl"][(fy, a)] for a in OPEX_LINES)
    for q in (1, 2, 3, 4):
        for s in SEGMENTS:
            for m in ("Revenue", "Cost of revenue", "Operating expenses"):
                seg_q[(fy, q, s, m)] = annual["segment"][(fy, s, m)] * share[(s, m)][q - 1]
            seg_q[(fy, q, s, "Operating income")] = (seg_q[(fy, q, s, "Revenue")] - seg_q[(fy, q, s, "Cost of revenue")]
                                                     - seg_q[(fy, q, s, "Operating expenses")])
        rev = sum(seg_q[(fy, q, s, "Revenue")] for s in SEGMENTS)
        cogs = sum(seg_q[(fy, q, s, "Cost of revenue")] for s in SEGMENTS)
        opex = sum(seg_q[(fy, q, s, "Operating expenses")] for s in SEGMENTS)
        oie = annual["pl"][(fy, "OIE")] / 4
        pbt = rev - cogs - opex + oie
        pl_q.update({(fy, q, "REV"): rev, (fy, q, "COGS"): cogs, (fy, q, "OIE"): oie, (fy, q, "TAX"): pbt * etr})
        for a in OPEX_LINES:
            pl_q[(fy, q, a)] = opex * annual["pl"][(fy, a)] / opex_total
        shares = annual["kpi"][(fy, "SHARES_DILUTED")]
        kpi_q.update({(fy, q, "SHARES_DILUTED"): shares, (fy, q, "EPS_DILUTED"): pbt * (1 - etr) / shares,
                      (fy, q, "OCF"): annual["kpi"][(fy, "OCF")] * rev / year["rev"],
                      (fy, q, "CAPEX"): annual["kpi"][(fy, "CAPEX")] * rev / year["rev"]})
    return seg_q, pl_q, kpi_q


# ---------- bridges ----------
def net_income_bridge(a0, a1, shares0, shares1):
    """
    Exact after-tax bridge from year 0 to year 1 (a0/a1 from pl_summary):
      OI1 − OI0 = ΔR·m0 + R1·(gm1 − gm0) − R1·(opex%1 − opex%0)        with m = OI/R
      NI1 − NI0 = [ΔOI + ΔOIE]·(1 − t0) − PBT1·(t1 − t0)
      EPS1 − EPS0 = ΔNI / S0 + NI1·(1/S1 − 1/S0)
    Returns (ni_steps, eps_steps) as lists of (step, value) including start/end rows.
    """
    r0, r1 = a0["rev"], a1["rev"]
    m0 = a0["oi"] / r0
    gm0, gm1 = a0["gp"] / r0, a1["gp"] / r1
    ox0, ox1 = a0["opex"] / r0, a1["opex"] / r1
    t0, t1 = a0["etr"], a1["etr"]
    keep = 1 - t0
    steps = [
        ("Revenue growth", (r1 - r0) * m0 * keep),
        ("Gross margin rate", r1 * (gm1 - gm0) * keep),
        ("Operating expense leverage", -r1 * (ox1 - ox0) * keep),
        ("Other income (expense)", (a1["oie"] - a0["oie"]) * keep),
        ("Tax rate", -a1["pbt"] * (t1 - t0)),
    ]
    ni = [("Start", a0["ni"])] + steps + [("End", a1["ni"])]
    assert abs(a0["ni"] + sum(v for _, v in steps) - a1["ni"]) < 1e-6, "NI bridge doesn't close"
    eps0, eps1 = a0["ni"] / shares0, a1["ni"] / shares1
    eps = [("Start", eps0)] + [(name, v / shares0) for name, v in steps] + [
        ("Share count", a1["ni"] * (1 / shares1 - 1 / shares0)), ("End", eps1)]
    assert abs(sum(v for name, v in eps if name not in ("Start", "End")) + eps0 - eps1) < 1e-9, "EPS bridge doesn't close"
    return ni, eps


# ---------- sensitivity ----------
def eps_sensitivity(base, actual_seg, actual_pl, actual_kpi, drivers, last_fy, fy,
                    growth_deltas=(-6, -4, -2, 0, 2, 4, 6), margin_deltas=(-300, -200, -100, 0, 100, 200, 300)):
    """
    First forecast year EPS (Base) when every segment's revenue growth moves by Δg (pp) and the
    operating margin by Δm (bp). Gross margin % holds; opex absorbs 25% of the revenue change
    (the same cost stickiness as the scenarios); Δm then applies on top as a margin shift.
    """
    out = []
    shares = base["kpi"][(fy, "SHARES_DILUTED")]
    etr = pl_summary(base["pl"], fy)["etr"]
    for dg in growth_deltas:
        d = {k: dict(v) for k, v in drivers.items()}
        for s in SEGMENTS:
            d[("Revenue growth", s, "Base")][fy] += dg
            d[("Operating expense growth", s, "Base")][fy] += 0.25 * dg
        run = project(actual_seg, actual_pl, actual_kpi, d, "Base", last_fy, [fy])
        y = pl_summary(run["pl"], fy)
        for dm in margin_deltas:
            pbt = y["oi"] + y["rev"] * dm / 10000.0 + y["oie"]
            ni = pbt * (1 - etr)
            out.append({"Revenue Growth Delta (pp)": dg, "Operating Margin Delta (bp)": dm,
                        "Revenue": y["rev"], "Operating Margin": (y["oi"] + y["rev"] * dm / 10000.0) / y["rev"],
                        "Net Income": ni, "Diluted EPS": ni / shares})
    return out


# ---------- backtest ----------
def backtest(pl, kpi, first_origin=2013, long_run=0.10, fade=0.6):
    """
    One-year-ahead forecasts from each fiscal year since `first_origin`, using the model's
    consolidated mechanics with only information available at the time:
      revenue growth fades from last year's rate toward 10% (60% of the gap kept),
      gross margin % held, opex grows at half the revenue growth (operating leverage),
      other income = 3-year average, tax rate = 3-year median (one odd tax year, like FY2018's US
      tax reform charge, shouldn't set next year's rate), shares follow their 3-year trend.
    Compared with a naive forecast (last year's revenue growth repeats, all margins held).
    """
    rows = []
    years = sorted({k[0] for k in pl if k[1] == 0})
    for t in years:
        if t < first_origin or t + 1 not in years:
            continue
        a0, a1, prior = pl_summary(pl, t), pl_summary(pl, t + 1), pl_summary(pl, t - 1)
        g0 = a0["rev"] / prior["rev"] - 1
        hist = [pl_summary(pl, y) for y in (t - 2, t - 1, t)]
        # Model
        g = long_run + (g0 - long_run) * fade
        rev = a0["rev"] * (1 + g)
        gp = rev * a0["gp"] / a0["rev"]
        opex = a0["opex"] * (1 + 0.5 * g)
        oie = sum(h["oie"] for h in hist) / 3
        etr = sorted(h["etr"] for h in hist)[1]
        oi = gp - opex
        ni = (oi + oie) * (1 - etr)
        s_trend = (kpi[(t, 0, "SHARES_DILUTED")] / kpi[(t - 2, 0, "SHARES_DILUTED")]) ** 0.5
        shares = kpi[(t, 0, "SHARES_DILUTED")] * s_trend
        model = {"Revenue": rev, "Operating income": oi, "Net income": ni, "Diluted EPS": ni / shares}
        # Naive: growth repeats, margins and tax rate held, shares flat
        n_rev = a0["rev"] * (1 + g0)
        naive = {"Revenue": n_rev, "Operating income": n_rev * a0["oi"] / a0["rev"],
                 "Net income": n_rev * a0["ni"] / a0["rev"]}
        naive["Diluted EPS"] = naive["Net income"] / kpi[(t, 0, "SHARES_DILUTED")]
        actual = {"Revenue": a1["rev"], "Operating income": a1["oi"], "Net income": a1["ni"],
                  "Diluted EPS": kpi[(t + 1, 0, "EPS_DILUTED")]}
        # Years no driver model could foresee: impairment/restructuring over 1% of revenue, a tax rate
        # more than 7pp off the assumed rate (e.g. FY2018 US tax reform), or other income swinging by
        # more than 2% of revenue.
        reasons = []
        if abs(pl.get((t + 1, 0, "IMP"), 0)) > 0.01 * a1["rev"]:
            reasons.append("impairment/restructuring")
        if abs(a1["etr"] - etr) > 0.07:
            reasons.append("tax rate shift")
        if abs(a1["oie"] - oie) > 0.02 * a1["rev"]:
            reasons.append("other income swing")
        for metric in actual:
            rows.append({"Origin Year": t, "Target Year": t + 1, "Metric": metric, "Actual": actual[metric],
                         "Model": model[metric], "Naive": naive[metric],
                         "Model Error %": model[metric] / actual[metric] - 1,
                         "Naive Error %": naive[metric] / actual[metric] - 1,
                         "One-off Year": bool(reasons), "One-off Reason": ", ".join(reasons)})
    return rows


# ---------- plan (what the model would have said a year earlier) ----------
def plan_drivers(drivers, actual_seg, pl, kpi, origin):
    """
    Drivers for a one-year plan made at the end of fiscal year `origin`, using only data known then
    and the model's standing rules (no hindsight):
      revenue growth  = long-run + (last year's growth − long-run) × fade      per segment
      gross margin    = held at last year's level
      opex growth     = last year's opex growth                                 per segment
      other income    = 3-year average; tax rate = 3-year average; shares = 3-year trend
    """
    fy = origin + 1
    fade = drivers[("Growth fade", "Consolidated", "Base")][min(drivers[("Growth fade", "Consolidated", "Base")])] / 100
    d = {}
    for s in SEGMENTS:
        lr = drivers[("Long-run revenue growth", s, "Base")]
        lr = lr[min(lr)] / 100
        g0 = actual_seg[(origin, 0, s, "Revenue")] / actual_seg[(origin - 1, 0, s, "Revenue")] - 1
        x0 = actual_seg[(origin, 0, s, "Operating expenses")] / actual_seg[(origin - 1, 0, s, "Operating expenses")] - 1
        d[("Revenue growth", s, "Plan")] = {fy: 100 * (lr + (g0 - lr) * fade)}
        d[("Gross margin change", s, "Plan")] = {fy: 0.0}
        d[("Operating expense growth", s, "Plan")] = {fy: 100 * x0}
    hist = [pl_summary(pl, y) for y in (origin - 2, origin - 1, origin)]
    d[("Other income (expense)", "Consolidated", "Plan")] = {fy: sum(h["oie"] for h in hist) / 3}
    d[("Effective tax rate", "Consolidated", "Plan")] = {fy: 100 * sum(h["tax"] for h in hist) / sum(h["pbt"] for h in hist)}
    trend = (kpi[(origin, 0, "SHARES_DILUTED")] / kpi[(origin - 2, 0, "SHARES_DILUTED")]) ** 0.5 - 1
    d[("Diluted share count change", "Consolidated", "Plan")] = {fy: 100 * trend}
    ocf = sum(kpi[(y, 0, "OCF")] / pl_summary(pl, y)["rev"] for y in (origin - 2, origin - 1, origin)) / 3
    d[("Operating cash flow % of revenue", "Consolidated", "Plan")] = {fy: 100 * ocf}
    d[("Capex % of revenue", "Consolidated", "Plan")] = {fy: 100 * kpi[(origin, 0, "CAPEX")] / pl_summary(pl, origin)["rev"]}
    return d
