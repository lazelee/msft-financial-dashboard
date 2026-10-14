"""
Data and model checks that run offline against data/*.csv (rebuild with `python -m pipeline.build`)
plus unit tests of the forecast arithmetic on hand-checkable numbers.
"""
import csv
from collections import defaultdict
from pathlib import Path

import pytest

from pipeline import model
from pipeline.extract import fiscal_period

DATA = Path(__file__).resolve().parent.parent / "data"


def rows(name):
    with open(DATA / f"{name}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pl_by(version_key):
    """{(period_key, account_code): amount}"""
    codes = {r["AccountKey"]: r["Code"] for r in rows("dim_account")}
    return {(int(r["PeriodKey"]), codes[r["AccountKey"]]): float(r["Amount"])
            for r in rows("fact_pl") if r["VersionKey"] == str(version_key)}


def summary(pl, key):
    return model.pl_summary({(key, a): v for (k, a), v in pl.items() if k == key}, key)


# ---------- extraction ----------
@pytest.mark.parametrize("start,end,expected", [
    ("2025-07-01", "2026-06-30", (2026, 0)),
    ("2025-07-01", "2025-09-30", (2026, 1)),
    ("2025-10-01", "2025-12-31", (2026, 2)),
    ("2026-01-01", "2026-03-31", (2026, 3)),
    ("2026-04-01", "2026-06-30", (2026, 4)),
    ("2025-07-01", "2025-12-31", None),  # six-month year-to-date
])
def test_fiscal_periods(start, end, expected):
    assert fiscal_period(start, end) == expected


def test_every_reconciliation_check_passes():
    checks = rows("fact_checks")
    assert len(checks) > 250
    failed = [c for c in checks if c["Status"] != "Pass"]
    assert not failed, failed[:5]


def test_known_reported_figures():
    """Spot checks against Microsoft's FY2026 10-K income statement ($M)."""
    s = summary(pl_by(1), 20260)
    assert s["rev"] == pytest.approx(331839)
    assert s["gp"] == pytest.approx(225465)
    assert s["oi"] == pytest.approx(155237)
    assert s["oie"] == pytest.approx(10697)  # a gain: the old dashboard forced this negative
    assert s["pbt"] == pytest.approx(165934)
    assert s["ni"] == pytest.approx(133749)
    assert s["gp"] / s["rev"] == pytest.approx(0.6794, abs=1e-4)  # not the 11.9% the old dashboard showed


def test_impairment_line_matches_reported_charges():
    pl = pl_by(1)
    for fy, amount in ((2012, 6193), (2015, 10011), (2016, 1110), (2017, 306)):
        assert pl[(fy * 10, "IMP")] == pytest.approx(amount)


def test_breakdowns_never_double_count():
    """Each revenue breakdown sums to total revenue in every period (the original dashboard's bug)."""
    revenue = {k: v for (k, a), v in pl_by(1).items() if a == "REV"}
    totals = defaultdict(float)
    for r in rows("fact_revenue_mix"):
        totals[(int(r["PeriodKey"]), r["Breakdown"])] += float(r["Amount"])
    assert totals
    for (key, breakdown), total in totals.items():
        assert total == pytest.approx(revenue[key], abs=1.5), (key, breakdown)


def test_quarters_sum_to_years():
    pl = pl_by(1)
    for (key, acc), v in pl.items():
        if key % 10 == 0 and all((key + q, acc) in pl for q in (1, 2, 3, 4)):
            assert sum(pl[(key + q, acc)] for q in (1, 2, 3, 4)) == pytest.approx(v, abs=1e-6)


# ---------- forecast outputs ----------
def test_forecast_segments_sum_to_consolidated():
    seg = defaultdict(float)
    for r in rows("fact_segment"):
        if r["VersionKey"] in ("2", "3", "4") and r["Metric"] == "Revenue":
            seg[(r["VersionKey"], int(r["PeriodKey"]))] += float(r["Amount"])
    for vk in (2, 3, 4):
        pl = pl_by(vk)
        for (v, key), total in seg.items():
            if v == str(vk):
                assert total == pytest.approx(pl[(key, "REV")], rel=1e-9)


def test_forecast_quarters_sum_to_year():
    for vk in (2, 3, 4):
        pl = pl_by(vk)
        for acc in ("REV", "COGS", "RD", "SM", "GA", "OIE", "TAX"):
            assert sum(pl[(20270 + q, acc)] for q in (1, 2, 3, 4)) == pytest.approx(pl[(20270, acc)], rel=1e-9)


def test_scenarios_are_ordered():
    eps = {}
    shares = {(r["VersionKey"], r["PeriodKey"]): float(r["Value"]) for r in rows("fact_kpi") if r["Metric"] == "Diluted shares (M)"}
    for vk in (2, 3, 4):
        s = summary(pl_by(vk), 20270)
        eps[vk] = s["ni"] / shares[(str(vk), "20270")]
    assert eps[3] > eps[2] > eps[4], eps


@pytest.mark.parametrize("bridge", ["Net income", "Diluted EPS", "Operating income by segment"])
def test_bridges_close(bridge):
    by_scenario = defaultdict(list)
    for r in rows("fact_bridge"):
        if r["Bridge"] == bridge:
            by_scenario[r["Scenario"]].append(r)
    assert set(by_scenario) == {"Base", "Upside", "Downside"}
    for scenario, steps in by_scenario.items():
        start = sum(float(r["Value"]) for r in steps if r["Step Type"] == "Start")
        change = sum(float(r["Value"]) for r in steps if r["Step Type"] == "Change")
        end = sum(float(r["Value"]) for r in steps if r["Step Type"] == "End")
        assert start + change == pytest.approx(end, rel=1e-9, abs=1e-5), scenario  # CSVs keep 6 decimals


def test_bridge_steps_have_one_sort_position():
    orders = defaultdict(set)
    for r in rows("fact_bridge"):
        orders[r["Step"]].add(r["Step Order"])
    assert all(len(v) == 1 for v in orders.values()), orders


def test_sensitivity_center_is_the_base_case():
    shares = {(r["VersionKey"], r["PeriodKey"]): float(r["Value"]) for r in rows("fact_kpi") if r["Metric"] == "Diluted shares (M)"}
    base_eps = summary(pl_by(2), 20270)["ni"] / shares[("2", "20270")]
    center = [r for r in rows("fact_sensitivity") if r["Revenue Growth Delta (pp)"] == "0" and r["Operating Margin Delta (bp)"] == "0"]
    assert len(center) == 1
    assert float(center[0]["Diluted EPS"]) == pytest.approx(base_eps, abs=1e-5)  # CSVs keep 6 decimals


def test_sensitivity_is_monotonic():
    grid = {(int(r["Revenue Growth Delta (pp)"]), int(r["Operating Margin Delta (bp)"])): float(r["Diluted EPS"])
            for r in rows("fact_sensitivity")}
    gs, ms = sorted({g for g, _ in grid}), sorted({m for _, m in grid})
    for m in ms:
        assert [grid[(g, m)] for g in gs] == sorted(grid[(g, m)] for g in gs)
    for g in gs:
        assert [grid[(g, m)] for m in ms] == sorted(grid[(g, m)] for m in ms)


def test_plan_uses_only_prior_year_information():
    plan = [r for r in rows("fact_assumptions") if r["Scenario"] == "Plan"]
    assert plan and all("no hindsight" in r["Rationale"] for r in plan)
    assert {r["Fiscal Year"] for r in plan} == {"2026"}


def test_backtest_flags_known_one_offs():
    flagged = {r["Target Year"] for r in rows("fact_backtest") if r["One-off Year"] == "True"}
    assert {"2015", "2018"} <= flagged  # Nokia write-down; US tax reform


# ---------- arithmetic on hand-checkable numbers ----------
def test_net_income_bridge_is_exact():
    a0 = {"rev": 100.0, "gp": 70.0, "opex": 30.0, "oi": 40.0, "oie": 2.0, "pbt": 42.0, "etr": 0.2, "ni": 33.6}
    a1 = {"rev": 120.0, "gp": 81.6, "opex": 33.0, "oi": 48.6, "oie": -1.0, "pbt": 47.6, "etr": 0.25, "ni": 35.7}
    ni, eps = model.net_income_bridge(a0, a1, 10.0, 9.8)
    steps = dict(ni)
    assert steps["Revenue growth"] == pytest.approx(20 * 0.40 * 0.8)          # ΔR × prior margin × (1 − t0)
    assert steps["Gross margin rate"] == pytest.approx(120 * (0.68 - 0.70) * 0.8)
    assert steps["Operating expense leverage"] == pytest.approx(-120 * (0.275 - 0.30) * 0.8)
    assert steps["Tax rate"] == pytest.approx(-47.6 * 0.05)
    assert sum(v for k, v in ni if k not in ("Start", "End")) == pytest.approx(35.7 - 33.6)
    assert dict(eps)["End"] == pytest.approx(35.7 / 9.8)


def test_projection_mechanics():
    segs = model.SEGMENTS
    seg = {}
    for s in segs:
        seg.update({(2026, 0, s, "Revenue"): 100.0, (2026, 0, s, "Cost of revenue"): 40.0,
                    (2026, 0, s, "Operating expenses"): 20.0})
    pl = {(2026, 0, "RD"): 30.0, (2026, 0, "SM"): 20.0, (2026, 0, "GA"): 10.0}
    kpi = {(2026, 0, "SHARES_DILUTED"): 10.0}
    d = {}
    for s in segs:
        d[("Revenue growth", s, "Base")] = {2027: 10.0}
        d[("Gross margin change", s, "Base")] = {2027: -100.0}
        d[("Operating expense growth", s, "Base")] = {2027: 5.0}
    d.update({("Other income (expense)", "Consolidated", "Base"): {2027: 3.0},
              ("Effective tax rate", "Consolidated", "Base"): {2027: 20.0},
              ("Diluted share count change", "Consolidated", "Base"): {2027: -1.0},
              ("Operating cash flow % of revenue", "Consolidated", "Base"): {2027: 50.0},
              ("Capex % of revenue", "Consolidated", "Base"): {2027: 30.0}})
    run = model.project(seg, pl, kpi, d, "Base", 2026, [2027])
    s0 = segs[0]
    assert run["segment"][(2027, s0, "Revenue")] == pytest.approx(110.0)
    assert run["segment"][(2027, s0, "Cost of revenue")] == pytest.approx(110.0 * 0.41)   # GM 60% → 59%
    assert run["segment"][(2027, s0, "Operating expenses")] == pytest.approx(21.0)
    oi = 3 * (110.0 - 45.1 - 21.0)
    assert run["pl"][(2027, "RD")] == pytest.approx(63.0 * 0.5)  # opex split by prior-year mix
    ni = (oi + 3.0) * 0.8
    assert run["kpi"][(2027, "EPS_DILUTED")] == pytest.approx(ni / 9.9)
