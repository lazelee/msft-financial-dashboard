"""
Extract Microsoft's reported financials from SEC EDGAR into tidy, reconciled tables.

Two sources:
- Company facts API: every consolidated (non-dimensional) fact, all years. Used for the P&L,
  per-share data and cash-flow KPIs.
- XBRL instance documents of recent 10-K/10-Qs: dimensional facts. Used for segment P&L,
  revenue by product line / revenue type / geography, and Microsoft Cloud revenue.

Conventions
- Microsoft's fiscal year ends June 30: FY2026 = Jul 2025 – Jun 2026; Q1 ends Sep 30.
- Amounts are in US$ millions. Revenue and income are positive; costs are positive amounts whose
  sign (−1) lives on the account dimension; "Other income (expense), net" keeps its reported sign.
- When a period was reported more than once (restatements, later comparatives), the most
  recently filed value wins, so history reflects the latest basis of presentation.
- Fiscal Q4 isn't filed as a quarter; it's derived as full year − (Q1 + Q2 + Q3).
"""
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date

from . import sec

FIRST_FY = 2010

# P&L lines as reported on the income statement, with the XBRL tags used for them over time
# (newest first). "IMP" has no single tag across years, so it's derived and checked (see below).
PL_TAGS = {
    "REV": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "COGS": ["CostOfGoodsAndServicesSold", "CostOfRevenue"],
    "RD": ["ResearchAndDevelopmentExpense"],
    "SM": ["SellingAndMarketingExpense"],
    "GA": ["GeneralAndAdministrativeExpense"],
    "OIE": ["NonoperatingIncomeExpense"],
    "TAX": ["IncomeTaxExpenseBenefit"],
}
# Reported subtotals, used only to reconcile the lines above.
CHECK_TAGS = {
    "GP": ["GrossProfit"],
    "OI": ["OperatingIncomeLoss"],
    "PBT": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
    "NI": ["NetIncomeLoss"],
}
KPI_TAGS = {
    "EPS_DILUTED": (["EarningsPerShareDiluted"], 1.0),
    "EPS_BASIC": (["EarningsPerShareBasic"], 1.0),
    "SHARES_DILUTED": (["WeightedAverageNumberOfDilutedSharesOutstanding"], 1e-6),
    "OCF": (["NetCashProvidedByUsedInOperatingActivities"], 1e-6),
    "CAPEX": (["PaymentsToAcquirePropertyPlantAndEquipment"], 1e-6),
    "SBC": (["ShareBasedCompensation"], 1e-6),
    "DIVIDENDS": (["PaymentsOfDividendsCommonStock"], 1e-6),
    "BUYBACKS": (["PaymentsForRepurchaseOfCommonStock"], 1e-6),
}


def fiscal_period(start, end):
    """(fiscal_year, quarter) for a duration; quarter 0 = full year; None for YTD/other spans."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    days = (e - s).days
    fy = e.year + (1 if e.month >= 7 else 0)
    if 350 <= days <= 380 and e.month == 6:
        return fy, 0
    if 80 <= days <= 100:
        return fy, {9: 1, 10: 1, 12: 2, 1: 2, 3: 3, 4: 3, 6: 4, 7: 4}.get(e.month)
    return None


def _latest_by_period(facts_by_tag, tags, scale):
    """{(fy, q): value} taking, per period, the first tag (in priority order) that has it and its latest filing."""
    out, filed = {}, {}
    for tag in tags:
        for unit_facts in facts_by_tag.get(tag, {}).get("units", {}).values():
            for f in unit_facts:
                if f.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A") or "start" not in f:
                    continue
                p = fiscal_period(f["start"], f["end"])
                if not p or p[1] is None or p[0] < FIRST_FY:
                    continue
                if p in out and out[p][0] != tag:
                    continue  # a higher-priority tag already covers this period
                if p not in out or f["filed"] > filed[p]:
                    out[p], filed[p] = (tag, f["val"] * scale), f["filed"]
    return {p: v for p, (_, v) in out.items()}


def _with_q4(series, flow=True):
    """Add derived fiscal Q4 = FY − Q1 − Q2 − Q3 where all four are known (flows only)."""
    out = dict(series)
    if not flow:
        return out
    for (fy, q), v in series.items():
        if q == 0 and all((fy, k) in series for k in (1, 2, 3)) and (fy, 4) not in series:
            out[(fy, 4)] = v - sum(series[(fy, k)] for k in (1, 2, 3))
    return out


def consolidated():
    """
    Returns (pl, kpi, checks):
      pl:   {(fy, q, account): value}  P&L leaf lines, $M
      kpi:  {(fy, q, metric): value}   per-share and cash-flow KPIs
      checks: list of reconciliation results (dicts)
    """
    facts = sec.company_facts()["facts"]["us-gaap"]
    lines = {acc: _with_q4(_latest_by_period(facts, tags, 1e-6)) for acc, tags in PL_TAGS.items()}
    subtotals = {k: _with_q4(_latest_by_period(facts, tags, 1e-6)) for k, tags in CHECK_TAGS.items()}

    periods = sorted(set(lines["REV"]) & set(lines["TAX"]) & set(subtotals["OI"]) & set(subtotals["NI"]))
    pl, checks = {}, []
    for p in periods:
        rev, cogs = lines["REV"].get(p), lines["COGS"].get(p)
        if cogs is None and p in subtotals["GP"]:
            cogs = rev - subtotals["GP"][p]  # a few early quarters only tag gross margin
        opex = [lines[a].get(p) for a in ("RD", "SM", "GA")]
        if cogs is None or None in opex or p not in lines["OIE"]:
            continue
        oi = subtotals["OI"][p]
        # "Impairment, integration and restructuring" (FY2012 aQuantive, FY2015–17 Nokia) has no
        # consistent tag; it's what's left between gross margin, the three opex lines and operating income.
        imp = round(rev - cogs - sum(opex) - oi, 3)
        values = {"REV": rev, "COGS": cogs, "RD": opex[0], "SM": opex[1], "GA": opex[2], "IMP": imp,
                  "OIE": lines["OIE"][p], "TAX": lines["TAX"][p]}
        for acc, v in values.items():
            if acc != "IMP" or abs(v) > 0.5:
                pl[(p[0], p[1], acc)] = v
        ni = rev - cogs - sum(opex) - imp + values["OIE"] - values["TAX"]
        checks.append({"period": p, "check": "Net income = lines", "computed": ni, "reported": subtotals["NI"][p]})
        if p in subtotals["GP"]:
            checks.append({"period": p, "check": "Gross margin", "computed": rev - cogs, "reported": subtotals["GP"][p]})
        if p in subtotals["PBT"]:
            checks.append({"period": p, "check": "Pretax income", "computed": oi + values["OIE"], "reported": subtotals["PBT"][p]})

    kpi = {}
    for metric, (tags, scale) in KPI_TAGS.items():
        flow = metric not in ("EPS_DILUTED", "EPS_BASIC", "SHARES_DILUTED")
        series = _with_q4(_latest_by_period(facts, tags, scale), flow=flow)
        for (fy, q), v in series.items():
            kpi[(fy, q, metric)] = v
    # Q4 diluted shares aren't filed; the annual weighted average is ≈ the mean of the four quarters.
    for (fy, q, m), v in list(kpi.items()):
        if m == "SHARES_DILUTED" and q == 0 and all((fy, k, m) in kpi for k in (1, 2, 3)) and (fy, 4, m) not in kpi:
            kpi[(fy, 4, m)] = 4 * v - sum(kpi[(fy, k, m)] for k in (1, 2, 3))
    return pl, kpi, checks


# ---------- dimensional facts from instance documents ----------
NS = {"xbrli": "http://www.xbrl.org/2003/instance", "xbrldi": "http://xbrl.org/2006/xbrldi"}
SEGMENT_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
BREAKDOWN_AXES = {
    "srt:ProductOrServiceAxis": None,  # split below into product lines vs revenue type
    "srt:StatementGeographicalAxis": "Geography",
    "msft:ProductsOrServicesSecondaryCategorizationAxis": "Microsoft Cloud",
}
REVENUE_TYPE_MEMBERS = {"us-gaap:ProductMember": "Product", "us-gaap:ServiceOtherMember": "Service and other"}
SEGMENT_METRICS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
    "CostOfGoodsAndServicesSold": "Cost of revenue",
    "OperatingExpenses": "Operating expenses",
    "OperatingIncomeLoss": "Operating income",
}
# Members whose names don't read well when split from CamelCase.
MEMBER_LABELS = {
    "country:US": "United States",
    "us-gaap:NonUsMember": "Other countries",
    "msft:MicrosoftThreeSixFiveCommercialProductsAndCloudServicesMember": "Microsoft 365 Commercial products and cloud services",
    "msft:MicrosoftThreeSixFiveConsumerProductsAndCloudServicesMember": "Microsoft 365 Consumer products and cloud services",
}


# Proper names, and renames between filings with identical values (checked: Gaming = Xbox and
# Search and news advertising = Search advertising for every overlapping period), mapped to the
# current name so each line is one continuous series.
LABEL_FIXES = {
    "Intelligent cloud": "Intelligent Cloud",
    "More personal computing": "More Personal Computing",
    "Productivity and business processes": "Productivity and Business Processes",
    "Microsoft cloud": "Microsoft Cloud",
    "Linked in corporation": "LinkedIn",
    "Gaming": "Xbox",
    "Search and news advertising": "Search advertising",
}


def _member_label(member):
    if member in MEMBER_LABELS:
        return MEMBER_LABELS[member]
    name = member.split(":")[1].removesuffix("Member")
    words = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    label = words[0] + words[1:].lower()
    return LABEL_FIXES.get(label, label)


def _parse_instance(xml_bytes):
    """Yield (concept, dims, fiscal period, value) for single-dimension duration facts in USD."""
    root = ET.fromstring(xml_bytes)
    # unitRef is an id ("U_USD"); resolve it to the measure it declares.
    usd_units = {u.get("id") for u in root.findall("xbrli:unit", NS)
                 if [m.text.strip() for m in u.findall("xbrli:measure", NS)] == ["iso4217:USD"]}
    contexts = {}
    for c in root.findall("xbrli:context", NS):
        period = c.find("xbrli:period", NS)
        start, end = period.find("xbrli:startDate", NS), period.find("xbrli:endDate", NS)
        if start is None:
            continue
        dims = {m.get("dimension"): m.text.strip() for m in c.findall(".//xbrldi:explicitMember", NS)}
        fp = fiscal_period(start.text, end.text)
        if fp and fp[1] is not None and len(dims) == 1:
            contexts[c.get("id")] = (fp, dims)
    for el in root:
        ref = el.get("contextRef")
        if ref in contexts and el.get("unitRef") in usd_units and el.text:
            concept = el.tag.split("}")[1]
            fp, dims = contexts[ref]
            yield concept, dims, fp, float(el.text) * 1e-6


def dimensional(first_fiscal_year_on_current_basis=2025):
    """
    Segment P&L and revenue breakdowns from every 10-K/10-Q since Microsoft's FY2025 segment
    re-alignment. Those filings restate comparatives on the new basis, so taking the latest-filed
    value per period gives a consistent FY2024+ history.

    Returns (segments, breakdowns):
      segments:   {(fy, q, segment, metric): value}
      breakdowns: {(fy, q, breakdown, item): value}
    """
    seg, brk, filed = {}, {}, {}
    for f in sorted(sec.filings(), key=lambda f: f["report_date"]):
        rd = date.fromisoformat(f["report_date"])
        if rd.year + (1 if rd.month >= 7 else 0) < first_fiscal_year_on_current_basis:
            continue
        for concept, dims, (fy, q), value in _parse_instance(sec.instance_document(f)):
            (axis, member), = dims.items()
            if axis == SEGMENT_AXIS and concept in SEGMENT_METRICS:
                key = ("seg", fy, q, _member_label(member), SEGMENT_METRICS[concept])
            elif axis in BREAKDOWN_AXES and concept == "RevenueFromContractWithCustomerExcludingAssessedTax":
                group = BREAKDOWN_AXES[axis] or ("Revenue type" if member in REVENUE_TYPE_MEMBERS else "Product line")
                label = REVENUE_TYPE_MEMBERS.get(member) or _member_label(member)
                key = ("brk", fy, q, group, label)
            else:
                continue
            if key not in filed or f["report_date"] >= filed[key]:
                (seg if key[0] == "seg" else brk)[key[1:]] = value
                filed[key] = f["report_date"]
    return _q4_dims(seg), _q4_dims(brk)


def _q4_dims(data):
    out = dict(data)
    groups = defaultdict(dict)
    for (fy, q, *rest), v in data.items():
        groups[(fy, *rest)][q] = v
    for (fy, *rest), qs in groups.items():
        if 0 in qs and all(k in qs for k in (1, 2, 3)) and 4 not in qs:
            out[(fy, 4, *rest)] = qs[0] - qs[1] - qs[2] - qs[3]
    return out
