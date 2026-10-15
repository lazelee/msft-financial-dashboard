"""
Generate the Power BI report (PBIR format) for the MSFT Financial Model project.

    python powerbi/build_report.py

Pages: Overview, Income Statement, Quarterly, Segments, Forecast & Scenarios, Plan vs Actual,
Sensitivity, Model Validation. Every file is checked against Microsoft's published PBIR JSON
schemas by tests/test_powerbi.py.
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "MSFT Financial Model"
REPORT = ROOT / "powerbi" / f"{NAME}.Report"
DEF = REPORT / "definition"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
VISUAL_SCHEMA = f"{SCHEMA}/visualContainer/2.0.0/schema.json"
W, H = 1280, 720
MEASURES_TABLE = "_Measures"  # must match build_model.MEASURES_TABLE

# Palette: neutral greys with one accent, plus semantic green/red for favourable/unfavourable.
INK, MUTED, ACCENT = "#1F2328", "#5B6470", "#0F6CBD"
SEGMENT_COLORS = ["#0F6CBD", "#5BA4E5", "#A7C8EB"]


# ---------- expression helpers ----------
def lit(value):
    if isinstance(value, bool):
        v = "true" if value else "false"
    elif isinstance(value, int):
        v = f"{value}L"
    elif isinstance(value, float):
        v = f"{value}D"
    else:
        v = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": v}}}


def measure(name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": MEASURES_TABLE}}, "Property": name}}


def column(table, col):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}}


def proj(field, display=None):
    kind = "Measure" if "Measure" in field else "Column"
    ref = f'{field[kind]["Expression"]["SourceRef"]["Entity"]}.{field[kind]["Property"]}'
    p = {"field": field, "queryRef": ref, "nativeQueryRef": field[kind]["Property"]}
    if display:
        p["displayName"] = display
    return p


def _source(field, alias):
    kind = "Column"
    return {kind: {"Expression": {"SourceRef": {"Source": alias}}, "Property": field[kind]["Property"]}}


def filter_in(table, col, values, name):
    """Visual/page filter: table[col] IN values."""
    field = column(table, col)
    alias = table[0].lower()
    return {
        "name": name, "field": field, "type": "Categorical",
        "filter": {"Version": 2, "From": [{"Name": alias, "Entity": table, "Type": 0}],
                   "Where": [{"Condition": {"In": {"Expressions": [_source(field, alias)],
                                                   "Values": [[{"Literal": {"Value": _literal(v)}}] for v in values]}}}]},
    }


def filter_compare(table, col, kind, value, name):
    """Visual/page filter: table[col] <op> value. kind: 0 =, 1 >, 2 >=, 3 <, 4 <=."""
    field = column(table, col)
    alias = table[0].lower()
    return {
        "name": name, "field": field, "type": "Advanced",
        "filter": {"Version": 2, "From": [{"Name": alias, "Entity": table, "Type": 0}],
                   "Where": [{"Condition": {"Comparison": {"ComparisonKind": kind, "Left": _source(field, alias),
                                                           "Right": {"Literal": {"Value": _literal(value)}}}}}]},
    }


def _literal(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return f"{v}L"
    return "'" + str(v).replace("'", "''") + "'"


# ---------- visuals ----------
class Page:
    def __init__(self, name, display, subtitle):
        self.name, self.display, self.subtitle = name, display, subtitle
        self.visuals, self.filters, self.interactions = [], [], []
        self.text("title", 24, 14, 900, 40, display, 20, bold=True)
        self.text("subtitle", 24, 52, 1000, 24, subtitle, 10, color=MUTED)

    def add(self, name, vtype, x, y, w, h, roles=None, title=None, objects=None, filters=None, sort=None, extra_vc=None):
        visual = {"visualType": vtype}
        if roles:
            visual["query"] = {"queryState": {role: {"projections": ps} for role, ps in roles.items()}}
            if sort:
                visual["query"]["sortDefinition"] = {"sort": sort}
        if objects:
            visual["objects"] = objects
        vc = dict(extra_vc or {})
        if title:
            vc["title"] = [{"properties": {"show": lit(True), "text": lit(title), "fontSize": lit(11.0), "fontColor": _color(INK)}}]
        if vc:
            visual["visualContainerObjects"] = vc
        visual["drillFilterOtherVisuals"] = True
        container = {"$schema": VISUAL_SCHEMA, "name": f"{self.name}_{name}",
                     "position": {"x": x, "y": y, "z": len(self.visuals) * 1000, "width": w, "height": h,
                                  "tabOrder": len(self.visuals) * 1000},
                     "visual": visual}
        if filters:
            container["filterConfig"] = {"filters": filters}
        self.visuals.append(container)
        return container

    def text(self, name, x, y, w, h, value, size, bold=False, color=INK):
        style = {"fontFamily": "Segoe UI Semibold" if bold else "Segoe UI", "fontSize": f"{size}pt", "color": color}
        if bold:
            style["fontWeight"] = "bold"
        objects = {"general": [{"properties": {"paragraphs": [{"textRuns": [{"value": value, "textStyle": style}]}]}}]}
        return self.add(name, "textbox", x, y, w, h, objects=objects)

    def card(self, name, x, y, w, h, measure_name, label):
        objects = {"labels": [{"properties": {"fontSize": lit(20.0), "color": _color(INK)}}],
                   "categoryLabels": [{"properties": {"show": lit(True), "fontSize": lit(9.0), "color": _color(MUTED)}}]}
        return self.add(name, "card", x, y, w, h, roles={"Values": [proj(measure(measure_name), label)]}, objects=objects,
                        extra_vc={"border": [{"properties": {"show": lit(True), "color": _color("#E1E4E8"), "radius": lit(6.0)}}]})

    def slicer(self, name, x, y, w, h, table, col, title, default=None, single=True, dropdown=True):
        objects = {"data": [{"properties": {"mode": lit("Dropdown" if dropdown else "Basic")}}],
                   "selection": [{"properties": {"singleSelect": lit(single), "selectAllCheckboxEnabled": lit(False)}}],
                   "header": [{"properties": {"show": lit(True), "text": lit(title), "fontColor": _color(MUTED)}}]}
        if default is not None:
            alias = table[0].lower()
            f = column(table, col)
            objects["general"] = [{"properties": {"filter": {"filter": {
                "Version": 2, "From": [{"Name": alias, "Entity": table, "Type": 0}],
                "Where": [{"Condition": {"In": {"Expressions": [_source(f, alias)],
                                                "Values": [[{"Literal": {"Value": _literal(default)}}]]}}}]}}}}]
        return self.add(name, "slicer", x, y, w, h, roles={"Values": [proj(column(table, col))]}, objects=objects)

    def scenario_slicer(self, x=1056, y=16, w=200, h=56):
        return self.slicer("scenario", x, y, w, h, "Scenario", "Scenario", "Scenario", default="Base")

    def write(self, pages_dir):
        folder = pages_dir / self.name
        (folder / "visuals").mkdir(parents=True, exist_ok=True)
        page = {"$schema": f"{SCHEMA}/page/1.4.0/schema.json", "name": self.name, "displayName": self.display,
                "displayOption": "FitToPage", "height": H, "width": W}
        if self.filters:
            page["filterConfig"] = {"filters": self.filters}
        if self.interactions:
            page["visualInteractions"] = [{"source": f"{self.name}_{src}", "target": f"{self.name}_{dst}", "type": "NoFilter"}
                                          for src, dst in self.interactions]
        _dump(folder / "page.json", page)
        for v in self.visuals:
            vdir = folder / "visuals" / v["name"]
            vdir.mkdir(parents=True, exist_ok=True)
            _dump(vdir / "visual.json", v)


def _color(hex_color):
    return {"solid": {"color": lit(hex_color)}}


def _measure_color(name):
    return {"solid": {"color": {"expr": measure(name)}}}


def _dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def no_totals():
    return {"subTotals": [{"properties": {"rowSubtotals": lit(False), "columnSubtotals": lit(False)}}]}


YEARS = filter_in("Period", "Period Type", ["Year"], "f_years")


def since(year, name="f_since"):
    return filter_compare("Period", "Fiscal Year", 2, year, name)


FY = column("Period", "Fiscal Year Label")
QUARTER = column("Period", "Period Label")
LINE = column("P&L Line", "Line")
SEGMENT = column("Segment", "Segment")


# ---------- pages ----------
def overview():
    p = Page("overview", "Overview", "Microsoft FY2026 results and FY2027–29 outlook · $ millions except per share · source: SEC 10-K/10-Q filings")
    p.scenario_slicer()
    cards = [("LY Revenue", "FY26 revenue"), ("LY Revenue YoY %", "FY26 revenue growth"),
             ("LY Operating Margin %", "FY26 operating margin"), ("LY Diluted EPS", "FY26 diluted EPS"),
             ("LY Free Cash Flow", "FY26 free cash flow"), ("NY Diluted EPS", "FY27E EPS (scenario)"),
             ("NY Diluted EPS (Probability-Weighted)", "FY27E EPS (probability-weighted)")]
    cw = (W - 48 - 6 * 12) / 7
    for i, (m, label) in enumerate(cards):
        p.card(f"card{i}", 24 + i * (cw + 12), 88, cw, 84, m, label)
    p.add("revenue_margin", "lineClusteredColumnComboChart", 24, 188, 740, 252,
          roles={"Category": [proj(FY)], "Y": [proj(measure("Revenue"), "Revenue ($M)")],
                 "Y2": [proj(measure("Operating Margin %"), "Operating margin")]},
          title="Revenue and operating margin, FY2016–FY2029E (forecast = selected scenario)",
          filters=[YEARS, since(2016)], sort=[{"field": FY, "direction": "Ascending"}])
    p.add("segment_mix", "columnChart", 776, 188, 480, 252,
          roles={"Category": [proj(FY)], "Series": [proj(SEGMENT)], "Y": [proj(measure("Segment Revenue"), "Revenue ($M)")]},
          title="Revenue by segment ($M)", filters=[YEARS, since(2023)], sort=[{"field": FY, "direction": "Ascending"}])
    p.add("eps_trend", "lineChart", 24, 452, 740, 248,
          roles={"Category": [proj(FY)], "Y": [proj(measure("Diluted EPS"), "Diluted EPS")]},
          title="Diluted EPS ($)", filters=[YEARS, since(2016)], sort=[{"field": FY, "direction": "Ascending"}])
    p.add("fcf", "clusteredColumnChart", 776, 452, 480, 248,
          roles={"Category": [proj(FY)], "Y": [proj(measure("Operating Cash Flow"), "Operating cash flow"),
                                               proj(measure("Capital Expenditures"), "Capex")]},
          title="Cash flow vs AI-driven capex ($M)", filters=[YEARS, since(2020)], sort=[{"field": FY, "direction": "Ascending"}])
    return p


def income_statement():
    p = Page("income_statement", "Income Statement", "Annual P&L · actuals FY2022–FY2026, forecast FY2027E–FY2029E for the selected scenario · $ millions (costs negative)")
    p.scenario_slicer()
    p.add("pl", "pivotTable", 24, 88, 1232, 420,
          roles={"Rows": [proj(LINE)], "Columns": [proj(FY)], "Values": [proj(measure("P&L Value"), "Value")]},
          title="Income statement", objects=no_totals(), filters=[YEARS, since(2022)],
          sort=[{"field": LINE, "direction": "Ascending"}])
    p.add("pl_yoy", "pivotTable", 24, 520, 1232, 184,
          roles={"Rows": [proj(LINE)], "Columns": [proj(FY)], "Values": [proj(measure("P&L YoY"), "YoY")]},
          title="Year-over-year change (% for amounts, percentage points for ratios)", objects=no_totals(),
          filters=[YEARS, since(2023), filter_in("P&L Line", "Line",
                   ["Revenue", "Gross profit", "Gross margin %", "Total operating expenses", "Operating income",
                    "Operating margin %", "Net income", "Diluted EPS"], "f_lines")],
          sort=[{"field": LINE, "direction": "Ascending"}])
    return p


def quarterly():
    p = Page("quarterly", "Quarterly", "Quarterly P&L · Q1 FY25 to Q4 FY27E (FY2027 phased with FY24–26 seasonality) · $ millions")
    p.scenario_slicer()
    quarters = filter_in("Period", "Period Type", ["Quarter"], "f_quarters")
    p.add("pl_q", "pivotTable", 24, 88, 1232, 360,
          roles={"Rows": [proj(LINE)], "Columns": [proj(QUARTER)], "Values": [proj(measure("P&L Value"), "Value")]},
          title="Quarterly income statement", objects=no_totals(),
          filters=[quarters, since(2025), filter_in("P&L Line", "Line",
                   ["Revenue", "Gross profit", "Gross margin %", "Total operating expenses", "Operating income",
                    "Operating margin %", "Other income (expense), net", "Net income", "Diluted EPS"], "f_lines")],
          sort=[{"field": LINE, "direction": "Ascending"}])
    p.add("rev_q", "columnChart", 24, 460, 820, 244,
          roles={"Category": [proj(QUARTER)], "Series": [proj(SEGMENT)], "Y": [proj(measure("Segment Revenue"), "Revenue ($M)")]},
          title="Quarterly revenue by segment ($M)", filters=[quarters, since(2024)],
          sort=[{"field": QUARTER, "direction": "Ascending"}])
    p.add("om_q", "lineChart", 856, 460, 400, 244,
          roles={"Category": [proj(QUARTER)], "Y": [proj(measure("Operating Margin %"), "Operating margin")]},
          title="Quarterly operating margin", filters=[quarters, since(2024)],
          sort=[{"field": QUARTER, "direction": "Ascending"}])
    return p


def segments():
    p = Page("segments", "Segments", "Productivity and Business Processes · Intelligent Cloud · More Personal Computing (Microsoft's FY2025 basis, history recast)")
    p.scenario_slicer()
    p.add("seg_table", "pivotTable", 24, 88, 760, 200,
          roles={"Rows": [proj(SEGMENT)], "Columns": [proj(FY)],
                 "Values": [proj(measure("Segment Revenue"), "Revenue"), proj(measure("Segment Operating Margin %"), "Op. margin")]},
          title="Segment revenue ($M) and operating margin", objects=no_totals(), filters=[YEARS, since(2024)])
    p.add("seg_margin", "lineChart", 796, 88, 460, 200,
          roles={"Category": [proj(FY)], "Series": [proj(SEGMENT)], "Y": [proj(measure("Segment Gross Margin %"), "Gross margin")]},
          title="Segment gross margin (AI infrastructure cost shows in Intelligent Cloud)", filters=[YEARS, since(2023)],
          sort=[{"field": FY, "direction": "Ascending"}])
    lines = filter_in("Revenue Mix", "Breakdown", ["Product line"], "f_breakdown")
    fy26 = filter_in("Period", "Fiscal Year Label", ["FY2026"], "f_fy26")
    item = column("Revenue Mix", "Item")
    p.add("products", "clusteredBarChart", 24, 300, 620, 404,
          roles={"Category": [proj(item)], "Y": [proj(measure("Mix Revenue"), "FY26 revenue ($M)")]},
          title="FY2026 revenue by product line ($M)", filters=[lines, fy26],
          sort=[{"field": measure("Mix Revenue"), "direction": "Descending"}])
    p.add("product_table", "tableEx", 656, 300, 600, 300,
          roles={"Values": [proj(item, "Product line"), proj(measure("Mix Revenue"), "FY26 revenue"),
                            proj(measure("Mix Revenue YoY %"), "YoY"), proj(measure("Mix Share %"), "Share")]},
          title="Product lines: growth and mix (FY2026)", filters=[lines, fy26],
          sort=[{"field": measure("Mix Revenue"), "direction": "Descending"}])
    p.card("cloud", 656, 612, 290, 92, "Microsoft Cloud Revenue", "FY26 Microsoft Cloud revenue ($M)").setdefault(
        "filterConfig", {"filters": [fy26, YEARS]})
    p.card("cloud_share", 966, 612, 290, 92, "Microsoft Cloud % of Revenue", "Microsoft Cloud share of revenue").setdefault(
        "filterConfig", {"filters": [fy26, YEARS]})
    return p


def forecast():
    p = Page("forecast", "Forecast & Scenarios", "Driver-based forecast built up from segments · flex the drivers live · bridges reconcile exactly")
    p.scenario_slicer(x=1056)
    p.slicer("rev_flex", 640, 16, 200, 56, "Revenue Growth Flex", "Revenue Growth Flex", "Revenue growth flex (pp/yr)", default=None)
    p.slicer("opex_flex", 848, 16, 200, 56, "Opex Flex", "Opex Flex", "Opex flex", default=None)
    for i, (m, label) in enumerate([("NY Revenue", "FY27E revenue"), ("NY Revenue YoY %", "FY27E revenue growth"),
                                    ("NY Operating Margin %", "FY27E operating margin"), ("NY Diluted EPS", "FY27E diluted EPS"),
                                    ("NY Diluted EPS YoY %", "FY27E EPS growth"), ("EPS CAGR (3y)", "EPS CAGR FY26–29E")]):
        p.card(f"card{i}", 24 + i * 206, 88, 194, 80, m, label)
    step = column("Bridge", "Step")
    for i, (bridge, title) in enumerate([("Net income", "Net income bridge FY2026 → FY2027E ($M, after tax)"),
                                         ("Diluted EPS", "EPS bridge FY2026 → FY2027E ($/share)")]):
        p.add(f"bridge{i}", "waterfallChart", 24 + i * 416, 180, 404, 280,
              roles={"Category": [proj(step)], "Y": [proj(measure("Bridge Value"), "Change")]},
              title=title, sort=[{"field": step, "direction": "Ascending"}],
              filters=[filter_in("Bridge", "Bridge", [bridge], "f_bridge"),
                       filter_in("Bridge", "Step Type", ["Start", "Change"], "f_steps")])
    p.add("seg_bridge", "waterfallChart", 856, 180, 400, 280,
          roles={"Category": [proj(step)], "Y": [proj(measure("Bridge Value"), "Change")]},
          title="Operating income growth by segment ($M)", sort=[{"field": step, "direction": "Ascending"}],
          filters=[filter_in("Bridge", "Bridge", ["Operating income by segment"], "f_bridge"),
                   filter_in("Bridge", "Step Type", ["Start", "Change"], "f_steps")])
    p.add("scenarios", "tableEx", 24, 472, 520, 232,
          roles={"Values": [proj(column("Scenario", "Scenario")), proj(column("Scenario", "Probability"), "Probability"),
                            proj(measure("NY Revenue"), "FY27E revenue"), proj(measure("NY Operating Margin %"), "FY27E op. margin"),
                            proj(measure("NY Diluted EPS"), "FY27E EPS"), proj(measure("Y3 Diluted EPS"), "FY29E EPS")]},
          title="Scenarios side by side", sort=[{"field": column("Scenario", "Scenario"), "direction": "Ascending"}])
    p.interactions.append(("scenario", "scenarios"))  # the comparison shows every scenario whatever is selected
    p.add("drivers", "pivotTable", 556, 472, 700, 232,
          roles={"Rows": [proj(column("Assumptions", "Driver")), proj(column("Assumptions", "Segment"))],
                 "Columns": [proj(column("Assumptions", "Fiscal Year"))],
                 "Values": [proj(measure("Assumption Value"), "Value")]},
          title="Drivers for the selected scenario (%, bp or $M; edit assumptions/assumptions.csv)", objects=no_totals(),
          filters=[filter_in("Assumptions", "Driver", ["Revenue growth", "Gross margin change", "Operating expense growth",
                                                       "Other income (expense)", "Effective tax rate", "Diluted share count change",
                                                       "Capex % of revenue"], "f_drivers")])
    return p


def plan_vs_actual():
    p = Page("plan_vs_actual", "Plan vs Actual", "FY2026 actual vs the plan this model would have produced from FY2025 actuals (no hindsight) · green = favourable")
    fy = filter_in("Period", "Fiscal Year Label", ["FY2026"], "f_fy26")
    color = {"values": [{"properties": {"fontColor": _measure_color("Variance Color")},
                         "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{MEASURES_TABLE}.P&L Variance"}},
                        {"properties": {"fontColor": _measure_color("Variance Color")},
                         "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{MEASURES_TABLE}.P&L Variance %"}}]}
    p.add("variance", "pivotTable", 24, 88, 720, 616,
          roles={"Rows": [proj(LINE)], "Values": [proj(measure("P&L Plan"), "Plan"), proj(measure("P&L Actual"), "Actual"),
                                                  proj(measure("P&L Variance"), "Variance"), proj(measure("P&L Variance %"), "Variance %")]},
          title="FY2026 income statement: plan vs actual ($M)", objects={**no_totals(), **color},
          filters=[fy, YEARS], sort=[{"field": LINE, "direction": "Ascending"}])
    p.add("seg_variance", "tableEx", 756, 88, 500, 200,
          roles={"Values": [proj(SEGMENT), proj(measure("Segment Revenue Plan"), "Revenue plan"),
                            proj(measure("Segment Revenue Actual"), "Revenue actual"),
                            proj(measure("Segment Revenue Variance %"), "Var %"),
                            proj(measure("Segment OM % Plan"), "OM plan"), proj(measure("Segment OM % Actual"), "OM actual")]},
          title="By segment", filters=[fy, YEARS])
    p.text("notes", 756, 304, 500, 400,
           "How to read this: the plan holds each segment's gross margin, fades revenue growth toward its long-run rate, "
           "and grows opex at last year's pace. FY2026 beat on revenue because Intelligent Cloud grew far faster than any "
           "fade rule expects, but missed on Intelligent Cloud margin as AI infrastructure depreciation came through cost "
           "of revenue. Other income was a one-off gain the plan couldn't anticipate.", 10, color=MUTED)
    return p


def sensitivity():
    p = Page("sensitivity", "Sensitivity", "FY2027E diluted EPS (Base) as revenue growth and operating margin move · costs flex 25% with revenue")
    growth, margin = column("Sensitivity", "Revenue Growth Label"), column("Sensitivity", "Operating Margin Label")
    colors = {"values": [{"properties": {"backColor": _measure_color("Sensitivity Color")},
                          "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"{MEASURES_TABLE}.Sensitivity EPS"}}]}
    p.add("grid", "pivotTable", 24, 88, 820, 360,
          roles={"Rows": [proj(growth, "Revenue growth vs base")], "Columns": [proj(margin, "Operating margin vs base")],
                 "Values": [proj(measure("Sensitivity EPS"), "EPS")]},
          title="FY2027E diluted EPS: revenue growth (rows) × operating margin (columns)", objects={**no_totals(), **colors},
          sort=[{"field": growth, "direction": "Ascending"}])
    p.add("grid_pct", "pivotTable", 24, 460, 820, 244,
          roles={"Rows": [proj(growth, "Revenue growth vs base")], "Columns": [proj(margin, "Operating margin vs base")],
                 "Values": [proj(measure("Sensitivity vs Base"), "vs base")]},
          title="Change vs Base EPS", objects=no_totals(), sort=[{"field": growth, "direction": "Ascending"}])
    p.text("notes", 856, 88, 400, 616,
           "Each 1pp of extra revenue growth in every segment adds roughly 1% to EPS once a quarter of it is spent on opex; "
           "each 100bp of operating margin is worth about 2%. Margin matters more than growth at Microsoft's scale, which is "
           "why Intelligent Cloud gross margin (AI depreciation) is the driver to watch.", 10, color=MUTED)
    return p


def validation():
    p = Page("validation", "Model Validation", "Reconciliation of every extracted number, and a one-year-ahead backtest of the model since FY2013")
    metric = filter_in("Backtest", "Metric", ["Net income"], "f_metric")
    for i, (m, label, filt) in enumerate([("Checks Passed", "Reconciliation checks passed", None),
                                          ("Checks Failed", "Checks failed", None),
                                          ("Model MAPE", "Model error, net income (all years)", metric),
                                          ("Naive MAPE", "Naive error, net income (all years)", metric),
                                          ("Model MAPE ex One-offs", "Model error ex one-offs", metric),
                                          ("Naive MAPE ex One-offs", "Naive error ex one-offs", metric)]):
        c = p.card(f"card{i}", 24 + i * 206, 88, 194, 80, m, label)
        if filt:
            c["filterConfig"] = {"filters": [filt]}
    target = column("Backtest", "Target Year")
    p.add("backtest", "tableEx", 24, 180, 760, 524,
          roles={"Values": [proj(target, "Year"), proj(column("Backtest", "Metric")), proj(column("Backtest", "Actual")),
                            proj(column("Backtest", "Model")), proj(column("Backtest", "Model Error %"), "Model error"),
                            proj(column("Backtest", "Naive Error %"), "Naive error"), proj(column("Backtest", "One-off Reason"), "One-off")]},
          title="One-year-ahead forecasts vs actuals ($M; EPS in $)",
          sort=[{"field": target, "direction": "Descending"}])
    p.add("accuracy", "clusteredBarChart", 796, 180, 460, 250,
          roles={"Category": [proj(column("Backtest", "Metric"))],
                 "Y": [proj(measure("Model MAPE ex One-offs"), "Model"), proj(measure("Naive MAPE ex One-offs"), "Naive")]},
          title="Average error excluding one-off years: model vs naive")
    p.add("checks", "tableEx", 796, 442, 460, 262,
          roles={"Values": [proj(column("Checks", "Area")), proj(measure("Checks Passed"), "Passed"), proj(measure("Checks Failed"), "Failed")]},
          title="Reconciliation checks by area")
    return p


# ---------- report scaffolding ----------
def build():
    if DEF.exists():
        shutil.rmtree(DEF)
    pages = [overview(), income_statement(), quarterly(), segments(), forecast(), plan_vs_actual(), sensitivity(), validation()]
    pages_dir = DEF / "pages"
    for p in pages:
        p.write(pages_dir)
    _dump(pages_dir / "pages.json", {"$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
                                     "pageOrder": [p.name for p in pages], "activePageName": pages[0].name})
    _dump(DEF / "version.json", {"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    _dump(DEF / "report.json", {
        "$schema": f"{SCHEMA}/report/1.3.0/schema.json",
        "layoutOptimization": "None",
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.61", "type": "SharedResources"},
            "customTheme": {"name": "ClearFinance.json", "reportVersionAtImport": "5.61", "type": "RegisteredResources"},
        },
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "ClearFinance.json", "path": "ClearFinance.json", "type": "CustomTheme"}]},
        ],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True},
    })
    res = REPORT / "StaticResources"
    (res / "RegisteredResources").mkdir(parents=True, exist_ok=True)
    _dump(res / "RegisteredResources" / "ClearFinance.json", {
        "name": "ClearFinance",
        "dataColors": SEGMENT_COLORS + ["#1B7F3B", "#B3261E", "#8A6D00", "#5B6470", "#2E4A62"],
        "background": "#FFFFFF", "foreground": INK, "tableAccent": ACCENT,
        "good": "#1B7F3B", "bad": "#B3261E", "neutral": "#8A6D00",
        "textClasses": {"label": {"fontFace": "Segoe UI", "fontSize": 10, "color": INK},
                        "title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": INK}},
    })
    base = REPORT / "StaticResources" / "SharedResources" / "BaseThemes" / "CY24SU10.json"
    if not base.exists():
        raise SystemExit(f"Missing base theme {base}; copy it from any Power BI Desktop project saved with the CY24SU10 theme.")
    (REPORT / "definition.pbir").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2) + "\n", encoding="utf-8")
    (REPORT / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": "5b8f0c1e-6c0e-4c55-9b7a-3d1e0f6a2c41"}}, indent=2) + "\n", encoding="utf-8")
    (ROOT / "powerbi" / f"{NAME}.pbip").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True}}, indent=2) + "\n", encoding="utf-8")
    return pages


if __name__ == "__main__":
    pages = build()
    print(f"Wrote {len(pages)} pages, {sum(len(p.visuals) for p in pages)} visuals to {REPORT.relative_to(ROOT)}")
