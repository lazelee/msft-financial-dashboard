"""
Generate the Power BI semantic model (TMDL) for the MSFT Financial Model project.

    python powerbi/build_model.py

Tables load the CSVs in data/ through one Power Query function, from GitHub by default or a local
folder (set the DataFolder parameter). Measures implement the P&L logic in DAX:
  - actuals where they exist, otherwise the scenario picked in the Scenario slicer
  - subtotals built from leaf lines (gross profit, operating income, pretax, net income)
  - live "flex" what-if parameters on forecast periods (revenue growth, operating expenses)
  - a P&L template table driving one matrix with per-row formatting (dynamic format strings)
  - plan vs actual variance with favourable/unfavourable flags
"""
import csv
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
NAME = "MSFT Financial Model"
MODEL_DIR = ROOT / "powerbi" / f"{NAME}.SemanticModel"
DEF = MODEL_DIR / "definition"
# "Measures" is reserved by the engine (it's the MDX name for all measures); Power BI refuses a table
# with that name. The leading underscore is the usual convention and sorts the table first.
MEASURES_TABLE = "_Measures"
DEFAULT_DATA_FOLDER = "https://raw.githubusercontent.com/lazelee/msft-financial-dashboard/main/data/"
T = "\t"

# ---------------------------------------------------------------------------
# Tables loaded from CSV: (table name, csv file, {column: (dataType, options)})
# options: hidden, sortBy, format, summarize (default none)
# ---------------------------------------------------------------------------
INT, DEC, TEXT, BOOL, DATE = "int64", "double", "string", "boolean", "dateTime"
CSV_TABLES = [
    ("Period", "dim_period", "Fiscal periods: years and quarters (Microsoft's fiscal year ends June 30).", {
        "PeriodKey": (INT, {"hidden": True}), "Fiscal Year": (INT, {"format": "0"}), "Fiscal Quarter": (INT, {"format": "0"}),
        "Period Type": (TEXT, {}), "Fiscal Year Label": (TEXT, {"sortBy": "Fiscal Year"}),
        "Period Label": (TEXT, {"sortBy": "Sort"}), "Quarter Label": (TEXT, {}), "Period End": (DATE, {"format": "yyyy-mm-dd"}),
        "Sort": (INT, {"hidden": True}), "Is Forecast": (BOOL, {}), "Years From Last Actual": (INT, {}), "Timeline": (TEXT, {}),
    }),
    ("Account", "dim_account", "Income statement lines as reported. Sign: +1 adds to profit, −1 reduces it.", {
        "AccountKey": (INT, {"hidden": True}), "Code": (TEXT, {}), "Account": (TEXT, {"sortBy": "Sort"}),
        "Line Group": (TEXT, {}), "Sign": (INT, {}), "Sort": (INT, {"hidden": True}),
    }),
    ("Version", "dim_version", "Actuals, the three forecast scenarios, and the model-rebuilt plan for the last actual year.", {
        "VersionKey": (INT, {"hidden": True}), "Version": (TEXT, {"sortBy": "Sort"}), "Scenario": (TEXT, {}),
        "Version Type": (TEXT, {}), "Sort": (INT, {"hidden": True}),
    }),
    ("Scenario", "dim_scenario", "Scenario picker (disconnected from facts; measures read the selection).", {
        "Scenario": (TEXT, {"sortBy": "Sort"}), "Sort": (INT, {"hidden": True}), "Probability": (DEC, {"format": "0%"}),
    }),
    ("Segment", "dim_segment", "Microsoft's three reporting segments (FY2025 basis, history recast by Microsoft).", {
        "SegmentKey": (INT, {"hidden": True}), "Segment": (TEXT, {"sortBy": "Sort"}), "Short Name": (TEXT, {}),
        "Sort": (INT, {"hidden": True}),
    }),
    ("P&L", "fact_pl", "Income statement facts, $M. Costs are positive amounts; Account[Sign] gives direction.", {
        "PeriodKey": (INT, {"hidden": True}), "AccountKey": (INT, {"hidden": True}), "VersionKey": (INT, {"hidden": True}),
        "Amount": (DEC, {"hidden": True, "format": "#,##0"}),
    }),
    ("Segment P&L", "fact_segment", "Segment revenue, cost of revenue, operating expenses and operating income, $M.", {
        "PeriodKey": (INT, {"hidden": True}), "SegmentKey": (INT, {"hidden": True}), "VersionKey": (INT, {"hidden": True}),
        "Metric": (TEXT, {}), "Amount": (DEC, {"hidden": True, "format": "#,##0"}),
    }),
    ("KPI", "fact_kpi", "Per-share, cash-flow and Microsoft Cloud metrics. $M except EPS ($) and shares (M).", {
        "PeriodKey": (INT, {"hidden": True}), "VersionKey": (INT, {"hidden": True}), "Metric": (TEXT, {}),
        "Value": (DEC, {"hidden": True, "format": "#,##0.00"}),
    }),
    ("Revenue Mix", "fact_revenue_mix", "Revenue by product line, revenue type and geography (actuals), $M. Each breakdown sums to total revenue.", {
        "PeriodKey": (INT, {"hidden": True}), "Breakdown": (TEXT, {}), "Item": (TEXT, {}),
        "Amount": (DEC, {"hidden": True, "format": "#,##0"}),
    }),
    ("Bridge", "fact_bridge", "Exact after-tax bridges from the last actual year to the first forecast year.", {
        "Scenario": (TEXT, {}), "Bridge": (TEXT, {}), "Step": (TEXT, {"sortBy": "Step Order"}),
        "Step Order": (INT, {"hidden": True}), "Step Type": (TEXT, {}), "Value": (DEC, {"hidden": True, "format": "#,##0.00"}),
    }),
    ("Sensitivity", "fact_sensitivity", "First forecast year diluted EPS (Base) across revenue growth and operating margin shifts.", {
        "Revenue Growth Delta (pp)": (INT, {"hidden": True}), "Operating Margin Delta (bp)": (INT, {"hidden": True}),
        "Revenue ($M)": (DEC, {"hidden": True, "format": "#,##0"}), "Operating Margin (%)": (DEC, {"hidden": True, "format": "0.0%"}),
        "Net Income ($M)": (DEC, {"hidden": True, "format": "#,##0"}), "Diluted EPS ($)": (DEC, {"hidden": True, "format": "$0.00"}),
        "Revenue Growth Label": (TEXT, {"sortBy": "Revenue Growth Delta (pp)"}),
        "Operating Margin Label": (TEXT, {"sortBy": "Operating Margin Delta (bp)"}),
    }),
    ("Backtest", "fact_backtest", "One-year-ahead forecasts from each year since FY2013 vs what happened, model and naive.", {
        "Origin Year": (INT, {"format": "0"}), "Target Year": (INT, {"format": "0"}), "Metric": (TEXT, {}),
        "Actual": (DEC, {"format": "#,##0.00"}), "Model": (DEC, {"format": "#,##0.00"}), "Naive": (DEC, {"format": "#,##0.00"}),
        "Model Error %": (DEC, {"format": "0.0%"}), "Naive Error %": (DEC, {"format": "0.0%"}),
        "One-off Year": (BOOL, {}), "One-off Reason": (TEXT, {}),
    }),
    ("Assumptions", "fact_assumptions", "Forecast drivers by scenario, segment and year (from assumptions/assumptions.csv).", {
        "Driver": (TEXT, {}), "Segment": (TEXT, {}), "Scenario": (TEXT, {}), "Fiscal Year": (INT, {"format": "0"}),
        "Value": (DEC, {"hidden": True, "format": "#,##0.0"}), "Unit": (TEXT, {}), "Rationale": (TEXT, {}),
    }),
    ("Checks", "fact_checks", "Every reconciliation the pipeline ran: extracted lines vs reported subtotals, breakdowns vs revenue.", {
        "Area": (TEXT, {}), "Period": (TEXT, {}), "Check": (TEXT, {}), "Computed": (DEC, {"format": "#,##0.0"}),
        "Reported": (DEC, {"format": "#,##0.0"}), "Difference": (DEC, {"format": "#,##0.0"}), "Status": (TEXT, {}),
    }),
]

RELATIONSHIPS = [  # from (many) → to (one)
    ("P&L", "PeriodKey", "Period", "PeriodKey"), ("P&L", "AccountKey", "Account", "AccountKey"),
    ("P&L", "VersionKey", "Version", "VersionKey"),
    ("Segment P&L", "PeriodKey", "Period", "PeriodKey"), ("Segment P&L", "SegmentKey", "Segment", "SegmentKey"),
    ("Segment P&L", "VersionKey", "Version", "VersionKey"),
    ("KPI", "PeriodKey", "Period", "PeriodKey"), ("KPI", "VersionKey", "Version", "VersionKey"),
    ("Revenue Mix", "PeriodKey", "Period", "PeriodKey"),
    ("Bridge", "Scenario", "Scenario", "Scenario"), ("Assumptions", "Scenario", "Scenario", "Scenario"),
]

# ---------------------------------------------------------------------------
# P&L template: one row per line in a matrix. Format: M = $ millions, P = %, E = $ per share,
# S = millions of shares. Favourable: +1 if higher is better, −1 if lower is better.
# ---------------------------------------------------------------------------
PL_LINES = [
    ("Revenue", 1, "M", 1, "Header"), ("Cost of revenue", 2, "M", 1, "Line"), ("Gross profit", 3, "M", 1, "Subtotal"),
    ("Gross margin %", 4, "P", 1, "Ratio"), ("Research and development", 5, "M", 1, "Line"),
    ("Sales and marketing", 6, "M", 1, "Line"), ("General and administrative", 7, "M", 1, "Line"),
    ("Impairment and restructuring", 8, "M", 1, "Line"), ("Total operating expenses", 9, "M", 1, "Subtotal"),
    ("Opex % of revenue", 10, "P", -1, "Ratio"), ("Operating income", 11, "M", 1, "Subtotal"),
    ("Operating margin %", 12, "P", 1, "Ratio"), ("Other income (expense), net", 13, "M", 1, "Line"),
    ("Income before income taxes", 14, "M", 1, "Subtotal"), ("Provision for income taxes", 15, "M", 1, "Line"),
    ("Effective tax rate", 16, "P", -1, "Ratio"), ("Net income", 17, "M", 1, "Total"), ("Net margin %", 18, "P", 1, "Ratio"),
    ("Diluted shares (M)", 19, "S", -1, "Line"), ("Diluted EPS", 20, "E", 1, "Total"),
]

# ---------------------------------------------------------------------------
# Measures: (name, DAX, format string, display folder, description)
# ---------------------------------------------------------------------------
M, PCT, EPS, PP = "#,##0;(#,##0)", "0.0%;-0.0%", "$0.00;($0.00)", "+0.0%;-0.0%;0.0%"


def line(code):
    return f"CALCULATE ( [_Amount], Account[Code] = \"{code}\" )"


MEASURES = [
    # ---- engine ----
    ("Selected Scenario", 'SELECTEDVALUE ( Scenario[Scenario], "Base" )', None, "Engine",
     "Scenario picked in the slicer; Base when none or several are selected."),
    ("Last Actual FY", 'CALCULATE ( MAX ( Period[Fiscal Year] ), REMOVEFILTERS ( Period ), Period[Is Forecast] = FALSE () )', "0", "Engine",
     "Latest fiscal year with reported results."),
    ("_Amount", """
VAR _scenario = [Selected Scenario]
RETURN
    IF (
        ISFILTERED ( Version ),
        SUM ( 'P&L'[Amount] ),
        VAR _actual = CALCULATE ( SUM ( 'P&L'[Amount] ), Version[Version Type] = "Actual" )
        RETURN IF ( NOT ISBLANK ( _actual ), _actual, CALCULATE ( SUM ( 'P&L'[Amount] ), Version[Scenario] = _scenario ) )
    )""", M, "Engine",
     "Actuals where reported, otherwise the selected scenario. If a visual filters Version (e.g. Plan), that version is used as is."),
    ("_Is Forecast", "IF ( MAX ( Period[Years From Last Actual] ) > 0 && NOT ISFILTERED ( Version ), 1, 0 )", "0", "Engine",
     "1 for forecast periods (where flex parameters apply)."),
    ("Revenue Flex Value", "SELECTEDVALUE ( 'Revenue Growth Flex'[Revenue Growth Flex], 0 )", PP, "Engine",
     "What-if: extra revenue growth per forecast year (compounding), cost of revenue moves with it."),
    ("Opex Flex Value", "SELECTEDVALUE ( 'Opex Flex'[Opex Flex], 0 )", PP, "Engine",
     "What-if: change to operating expenses in forecast years (costs don't flex with revenue: operating leverage)."),
    ("_Revenue Factor", """
IF ( [_Is Forecast] = 1, POWER ( 1 + [Revenue Flex Value], MAX ( Period[Years From Last Actual] ) ), 1 )""", "0.000", "Engine",
     "Multiplier applied to forecast revenue and cost of revenue."),
    ("_Opex Factor", "IF ( [_Is Forecast] = 1, 1 + [Opex Flex Value], 1 )", "0.000", "Engine", "Multiplier applied to forecast opex."),
    ("_Base Tax Rate", f"DIVIDE ( {line('TAX')}, {line('REV')} - {line('COGS')} - CALCULATE ( [_Amount], Account[Line Group] = \"Operating expenses\" ) + {line('OIE')} )",
     PCT, "Engine", "Effective tax rate of the underlying (unflexed) numbers; applied to flexed pretax income."),

    # ---- income statement ----
    ("Revenue", f"{line('REV')} * [_Revenue Factor]", M, "Income statement", "Revenue, $M."),
    ("Cost of Revenue", f"{line('COGS')} * [_Revenue Factor]", M, "Income statement", "Cost of revenue, $M (positive)."),
    ("Gross Profit", "[Revenue] - [Cost of Revenue]", M, "Income statement", "Revenue − cost of revenue."),
    ("Research and Development", f"{line('RD')} * [_Opex Factor]", M, "Income statement", None),
    ("Sales and Marketing", f"{line('SM')} * [_Opex Factor]", M, "Income statement", None),
    ("General and Administrative", f"{line('GA')} * [_Opex Factor]", M, "Income statement", None),
    ("Impairment and Restructuring", line("IMP"), M, "Income statement",
     "Impairment, integration and restructuring (FY2012 aQuantive, FY2015–17 Nokia)."),
    ("Operating Expenses",
     "[Research and Development] + [Sales and Marketing] + [General and Administrative] + [Impairment and Restructuring]",
     M, "Income statement", "Total operating expenses."),
    ("Operating Income", "[Gross Profit] - [Operating Expenses]", M, "Income statement", "Gross profit − operating expenses."),
    ("Other Income (Expense)", line("OIE"), M, "Income statement", "Other income (expense), net; keeps its reported sign."),
    ("Pretax Income", "[Operating Income] + [Other Income (Expense)]", M, "Income statement", "Income before income taxes."),
    ("Income Tax", f"IF ( [_Is Forecast] = 1, [Pretax Income] * [_Base Tax Rate], {line('TAX')} )", M, "Income statement",
     "Provision for income taxes; in forecasts, the scenario's tax rate on (flexed) pretax income."),
    ("Net Income", "[Pretax Income] - [Income Tax]", M, "Income statement", "Net income, $M."),

    # ---- ratios ----
    ("Gross Margin %", "DIVIDE ( [Gross Profit], [Revenue] )", PCT, "Ratios", None),
    ("Opex % of Revenue", "DIVIDE ( [Operating Expenses], [Revenue] )", PCT, "Ratios", None),
    ("R&D % of Revenue", "DIVIDE ( [Research and Development], [Revenue] )", PCT, "Ratios", None),
    ("Operating Margin %", "DIVIDE ( [Operating Income], [Revenue] )", PCT, "Ratios", None),
    ("Effective Tax Rate", "DIVIDE ( [Income Tax], [Pretax Income] )", PCT, "Ratios", None),
    ("Net Margin %", "DIVIDE ( [Net Income], [Revenue] )", PCT, "Ratios", None),

    # ---- per share and cash ----
    ("_KPI", """
VAR _scenario = [Selected Scenario]
VAR _actual = CALCULATE ( SUM ( KPI[Value] ), Version[Version Type] = "Actual" )
RETURN
    IF (
        ISFILTERED ( Version ),
        SUM ( KPI[Value] ),
        IF ( NOT ISBLANK ( _actual ), _actual, CALCULATE ( SUM ( KPI[Value] ), Version[Scenario] = _scenario ) )
    )""", "#,##0.00", "Engine", "KPI value with the same actual-else-scenario logic as _Amount."),
    ("Diluted Shares (M)", 'CALCULATE ( [_KPI], KPI[Metric] = "Diluted shares (M)" )', "#,##0", "Per share and cash",
     "Weighted average diluted shares, millions."),
    ("Diluted EPS", "DIVIDE ( [Net Income], [Diluted Shares (M)] )", EPS, "Per share and cash",
     "Net income ÷ diluted shares (matches reported EPS within rounding)."),
    ("Operating Cash Flow", 'CALCULATE ( [_KPI], KPI[Metric] = "Operating cash flow" )', M, "Per share and cash", None),
    ("Capital Expenditures", 'CALCULATE ( [_KPI], KPI[Metric] = "Capital expenditures" )', M, "Per share and cash",
     "Additions to property and equipment."),
    ("Free Cash Flow", "[Operating Cash Flow] - [Capital Expenditures]", M, "Per share and cash", None),
    ("Capex % of Revenue", "DIVIDE ( [Capital Expenditures], [Revenue] )", PCT, "Per share and cash", None),
    ("FCF Margin %", "DIVIDE ( [Free Cash Flow], [Revenue] )", PCT, "Per share and cash", None),
    ("Microsoft Cloud Revenue", 'CALCULATE ( [_KPI], KPI[Metric] = "Microsoft Cloud revenue" )', M, "Per share and cash",
     "Microsoft Cloud (overlaps segments; not additive with them)."),
    ("Microsoft Cloud % of Revenue", "DIVIDE ( [Microsoft Cloud Revenue], [Revenue] )", PCT, "Per share and cash", None),

    # ---- time comparisons (prior year = same quarter one year earlier) ----
    ("Revenue PY", "VAR _k = SELECTEDVALUE ( Period[PeriodKey] ) RETURN CALCULATE ( [Revenue], REMOVEFILTERS ( Period ), Period[PeriodKey] = _k - 10 )",
     M, "Growth", "Revenue in the same period a year earlier."),
    ("Revenue YoY %", "DIVIDE ( [Revenue], [Revenue PY] ) - 1", PP, "Growth", None),
    ("Operating Income YoY %", "VAR _k = SELECTEDVALUE ( Period[PeriodKey] ) RETURN DIVIDE ( [Operating Income], CALCULATE ( [Operating Income], REMOVEFILTERS ( Period ), Period[PeriodKey] = _k - 10 ) ) - 1",
     PP, "Growth", None),
    ("Diluted EPS YoY %", "VAR _k = SELECTEDVALUE ( Period[PeriodKey] ) RETURN DIVIDE ( [Diluted EPS], CALCULATE ( [Diluted EPS], REMOVEFILTERS ( Period ), Period[PeriodKey] = _k - 10 ) ) - 1",
     PP, "Growth", None),

    # ---- headline (anchored to the last actual / next forecast year, regardless of page filters) ----
    ("LY Revenue", 'VAR _fy = [Last Actual FY] RETURN CALCULATE ( [Revenue], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', M, "Headline",
     "Last actual fiscal year revenue."),
    ("LY Revenue YoY %", 'VAR _fy = [Last Actual FY] RETURN CALCULATE ( [Revenue YoY %], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', PP, "Headline", None),
    ("LY Operating Margin %", 'VAR _fy = [Last Actual FY] RETURN CALCULATE ( [Operating Margin %], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', PCT, "Headline", None),
    ("LY Diluted EPS", 'VAR _fy = [Last Actual FY] RETURN CALCULATE ( [Diluted EPS], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', EPS, "Headline", None),
    ("LY Free Cash Flow", 'VAR _fy = [Last Actual FY] RETURN CALCULATE ( [Free Cash Flow], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', M, "Headline", None),
    ("NY Revenue", 'VAR _fy = [Last Actual FY] + 1 RETURN CALCULATE ( [Revenue], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', M, "Headline",
     "Next fiscal year revenue, selected scenario."),
    ("NY Revenue YoY %", 'VAR _fy = [Last Actual FY] + 1 RETURN CALCULATE ( [Revenue YoY %], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', PP, "Headline", None),
    ("NY Operating Margin %", 'VAR _fy = [Last Actual FY] + 1 RETURN CALCULATE ( [Operating Margin %], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', PCT, "Headline", None),
    ("NY Diluted EPS", 'VAR _fy = [Last Actual FY] + 1 RETURN CALCULATE ( [Diluted EPS], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', EPS, "Headline",
     "Next fiscal year diluted EPS, selected scenario (with flex)."),
    ("NY Diluted EPS YoY %", 'VAR _fy = [Last Actual FY] + 1 RETURN CALCULATE ( [Diluted EPS YoY %], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', PP, "Headline", None),
    ("Y3 Diluted EPS", 'VAR _fy = [Last Actual FY] + 3 RETURN CALCULATE ( [Diluted EPS], REMOVEFILTERS ( Period ), Period[Fiscal Year] = _fy, Period[Period Type] = "Year" )', EPS, "Headline",
     "Diluted EPS three years out, selected scenario."),
    ("EPS CAGR (3y)", "DIVIDE ( [Y3 Diluted EPS], [LY Diluted EPS] ) ^ ( 1 / 3 ) - 1", PP, "Headline", None),
    ("NY Diluted EPS (Probability-Weighted)", """
SUMX (
    ALL ( Scenario ),
    Scenario[Probability] * CALCULATE ( [NY Diluted EPS] )
)""", EPS, "Headline", "Next-year EPS weighted by scenario probabilities (assumptions file)."),

    # ---- segments ----
    ("_Segment Amount", """
VAR _scenario = [Selected Scenario]
VAR _actual = CALCULATE ( SUM ( 'Segment P&L'[Amount] ), Version[Version Type] = "Actual" )
RETURN
    IF (
        ISFILTERED ( Version ),
        SUM ( 'Segment P&L'[Amount] ),
        IF ( NOT ISBLANK ( _actual ), _actual, CALCULATE ( SUM ( 'Segment P&L'[Amount] ), Version[Scenario] = _scenario ) )
    )""", M, "Engine", "Segment amount with the actual-else-scenario logic."),
    ("Segment Revenue", "CALCULATE ( [_Segment Amount], 'Segment P&L'[Metric] = \"Revenue\" )", M, "Segments", None),
    ("Segment Operating Income", "CALCULATE ( [_Segment Amount], 'Segment P&L'[Metric] = \"Operating income\" )", M, "Segments", None),
    ("Segment Gross Margin %", "DIVIDE ( [Segment Revenue] - CALCULATE ( [_Segment Amount], 'Segment P&L'[Metric] = \"Cost of revenue\" ), [Segment Revenue] )",
     PCT, "Segments", "Available from FY2023 annual and FY2025 quarterly (segment costs disclosed from FY2025)."),
    ("Segment Operating Margin %", "DIVIDE ( [Segment Operating Income], [Segment Revenue] )", PCT, "Segments", None),
    ("Segment Revenue YoY %", "VAR _k = SELECTEDVALUE ( Period[PeriodKey] ) RETURN DIVIDE ( [Segment Revenue], CALCULATE ( [Segment Revenue], REMOVEFILTERS ( Period ), Period[PeriodKey] = _k - 10 ) ) - 1",
     PP, "Segments", None),
    ("Segment Revenue Share %", "DIVIDE ( [Segment Revenue], CALCULATE ( [Segment Revenue], REMOVEFILTERS ( Segment ) ) )", PCT, "Segments", None),

    # ---- revenue mix ----
    ("Mix Revenue", "SUM ( 'Revenue Mix'[Amount] )", M, "Revenue mix", "Revenue by the breakdown on the visual (actuals)."),
    ("Mix Revenue YoY %", "VAR _k = SELECTEDVALUE ( Period[PeriodKey] ) RETURN DIVIDE ( [Mix Revenue], CALCULATE ( [Mix Revenue], REMOVEFILTERS ( Period ), Period[PeriodKey] = _k - 10 ) ) - 1",
     PP, "Revenue mix", None),
    ("Mix Share %", "DIVIDE ( [Mix Revenue], CALCULATE ( [Mix Revenue], REMOVEFILTERS ( 'Revenue Mix'[Item] ) ) )", PCT, "Revenue mix", None),

    # ---- P&L template ----
    ("P&L Value", """
VAR _line = SELECTEDVALUE ( 'P&L Line'[Line] )
RETURN
    SWITCH (
        _line,
        "Revenue", [Revenue],
        "Cost of revenue", - [Cost of Revenue],
        "Gross profit", [Gross Profit],
        "Gross margin %", [Gross Margin %],
        "Research and development", - [Research and Development],
        "Sales and marketing", - [Sales and Marketing],
        "General and administrative", - [General and Administrative],
        "Impairment and restructuring", IF ( [Impairment and Restructuring] <> 0, - [Impairment and Restructuring] ),
        "Total operating expenses", - [Operating Expenses],
        "Opex % of revenue", [Opex % of Revenue],
        "Operating income", [Operating Income],
        "Operating margin %", [Operating Margin %],
        "Other income (expense), net", [Other Income (Expense)],
        "Income before income taxes", [Pretax Income],
        "Provision for income taxes", - [Income Tax],
        "Effective tax rate", [Effective Tax Rate],
        "Net income", [Net Income],
        "Net margin %", [Net Margin %],
        "Diluted shares (M)", [Diluted Shares (M)],
        "Diluted EPS", [Diluted EPS]
    )""", None, "P&L template",
     "Value for the P&L Line on the row; costs shown negative. Formatting follows the line ($M, %, $/share)."),
    ("P&L YoY", """
VAR _k = SELECTEDVALUE ( Period[PeriodKey] )
VAR _now = [P&L Value]
VAR _prior = CALCULATE ( [P&L Value], REMOVEFILTERS ( Period ), Period[PeriodKey] = _k - 10 )
RETURN
    IF (
        NOT ISBLANK ( _now ) && NOT ISBLANK ( _prior ),
        IF ( SELECTEDVALUE ( 'P&L Line'[Format] ) = "P", _now - _prior, DIVIDE ( _now - _prior, ABS ( _prior ) ) )
    )""", PP, "P&L template", "Year-over-year change: % for amounts, percentage points for ratios."),
    ("P&L Actual", 'CALCULATE ( [P&L Value], Version[Version Type] = "Actual" )', None, "P&L template", None),
    ("P&L Plan", 'CALCULATE ( [P&L Value], Version[Version Type] = "Plan" )', None, "P&L template",
     "What the model would have forecast a year earlier (rules applied to prior-year actuals, no hindsight)."),
    ("P&L Variance", "IF ( NOT ISBLANK ( [P&L Plan] ), [P&L Actual] - [P&L Plan] )", None, "P&L template",
     "Actual − plan (pp for ratios). Costs are negative, so a positive variance on any $ line is favourable."),
    ("P&L Variance %", """
IF (
    SELECTEDVALUE ( 'P&L Line'[Format] ) IN { "M", "E" },
    DIVIDE ( [P&L Variance], ABS ( [P&L Plan] ) )
)""", PP, "P&L template", None),
    ("Variance Color", """
VAR _v = [P&L Variance] * SELECTEDVALUE ( 'P&L Line'[Favourable], 1 )
RETURN SWITCH ( TRUE (), ISBLANK ( _v ), BLANK (), _v >= 0, "#1B7F3B", "#B3261E" )""", None, "P&L template",
     "Green when the variance is favourable, red when not (lower is better for costs ratios and tax rate)."),

    # ---- segment plan vs actual ----
    ("Segment Revenue Plan", 'CALCULATE ( [Segment Revenue], Version[Version Type] = "Plan" )', M, "Segments", None),
    ("Segment Revenue Actual", 'CALCULATE ( [Segment Revenue], Version[Version Type] = "Actual" )', M, "Segments", None),
    ("Segment Revenue Variance %", "DIVIDE ( [Segment Revenue Actual] - [Segment Revenue Plan], [Segment Revenue Plan] )", PP, "Segments", None),
    ("Segment OM % Plan", 'CALCULATE ( [Segment Operating Margin %], Version[Version Type] = "Plan" )', PCT, "Segments", None),
    ("Segment OM % Actual", 'CALCULATE ( [Segment Operating Margin %], Version[Version Type] = "Actual" )', PCT, "Segments", None),

    # ---- bridges, sensitivity, validation ----
    ("Bridge Value", "SUM ( Bridge[Value] )", "#,##0.00", "Bridges", "Bridge step value ($M for net income / operating income, $ for EPS)."),
    ("Sensitivity EPS", "SUM ( Sensitivity[Diluted EPS ($)] )", EPS, "Sensitivity", None),
    ("Sensitivity vs Base", """
VAR _base = CALCULATE ( SUM ( Sensitivity[Diluted EPS ($)] ), REMOVEFILTERS ( Sensitivity ), Sensitivity[Revenue Growth Delta (pp)] = 0, Sensitivity[Operating Margin Delta (bp)] = 0 )
RETURN DIVIDE ( [Sensitivity EPS], _base ) - 1""", PP, "Sensitivity", None),
    ("Sensitivity Color", """
VAR _d = [Sensitivity vs Base]
RETURN SWITCH ( TRUE (), _d > 0.10, "#7FC8A0", _d > 0.03, "#C9E8D5", _d >= -0.03, "#F2F2F2", _d >= -0.10, "#F5CBC7", "#E8908A" )""",
     None, "Sensitivity", "Cell shading: green above base, red below."),
    ("Model MAPE", "AVERAGEX ( Backtest, ABS ( Backtest[Model Error %] ) )", PCT, "Validation", "Mean absolute % error of the model."),
    ("Naive MAPE", "AVERAGEX ( Backtest, ABS ( Backtest[Naive Error %] ) )", PCT, "Validation", "Same, for last-year-repeats."),
    ("Model Bias", "AVERAGE ( Backtest[Model Error %] )", PP, "Validation", "Average signed error: + means over-forecast."),
    ("Model MAPE ex One-offs", "CALCULATE ( [Model MAPE], Backtest[One-off Year] = FALSE () )", PCT, "Validation", None),
    ("Naive MAPE ex One-offs", "CALCULATE ( [Naive MAPE], Backtest[One-off Year] = FALSE () )", PCT, "Validation", None),
    ("Checks Passed", 'COUNTROWS ( FILTER ( Checks, Checks[Status] = "Pass" ) ) + 0', "#,##0", "Validation", None),
    ("Checks Failed", 'COUNTROWS ( FILTER ( Checks, Checks[Status] = "Fail" ) ) + 0', "#,##0", "Validation", None),
    ("Assumption Value", "SUM ( Assumptions[Value] )", "#,##0.0", "Assumptions", "Driver value in its unit (%, bp or $M)."),
]

PL_VALUE_FORMAT = """
SWITCH (
    SELECTEDVALUE ( 'P&L Line'[Format] ),
    "P", "0.0%;-0.0%",
    "E", "$0.00;($0.00)",
    "S", "#,##0",
    "#,##0;(#,##0)"
)"""
VARIANCE_FORMAT = """
SWITCH (
    SELECTEDVALUE ( 'P&L Line'[Format] ),
    "P", "+0.0%;-0.0%",
    "E", "+$0.00;-$0.00",
    "S", "+#,##0;-#,##0",
    "+#,##0;-#,##0"
)"""
DYNAMIC_FORMATS = {"P&L Value": PL_VALUE_FORMAT, "P&L Actual": PL_VALUE_FORMAT, "P&L Plan": PL_VALUE_FORMAT,
                   "P&L Variance": VARIANCE_FORMAT}


# ---------------------------------------------------------------------------
# TMDL writers
# ---------------------------------------------------------------------------
def q(name):
    """Quote a TMDL object name when needed."""
    return f"'{name}'" if any(c in name for c in " .=:'&()%/-") or name[0].isdigit() else name


def lineage(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "msft-financial-model/" + "/".join(parts)))


def expr_block(text, depth):
    """Indent a multi-line DAX/M expression for TMDL (one level deeper than the object's properties)."""
    lines = [ln for ln in text.strip("\n").split("\n")]
    return "\n".join((T * depth + ln) if ln.strip() else "" for ln in lines)


def m_query(table, csv_name, columns):
    types = {INT: "Int64.Type", DEC: "type number", TEXT: "type text", BOOL: "type logical", DATE: "type date"}
    pairs = ", ".join(f'{{"{c}", {types[t]}}}' for c, (t, _) in columns.items())
    return f"""let
    Source = LoadCsv("{csv_name}.csv"),
    Typed = Table.TransformColumnTypes(Source, {{{pairs}}}, "en-US")
in
    Typed"""


def write_table(name, csv_name, description, columns):
    out = [f"/// {description}", f"table {q(name)}", f"{T}lineageTag: {lineage(name)}", ""]
    for col, (dtype, opt) in columns.items():
        out.append(f"{T}column {q(col)}")
        out.append(f"{T*2}dataType: {dtype}")
        if opt.get("format"):
            out.append(f"{T*2}formatString: {opt['format']}")
        if opt.get("hidden"):
            out.append(f"{T*2}isHidden")
        out.append(f"{T*2}lineageTag: {lineage(name, col)}")
        out.append(f"{T*2}summarizeBy: none")
        out.append(f"{T*2}sourceColumn: {col}")
        if opt.get("sortBy"):
            out.append(f"{T*2}sortByColumn: {q(opt['sortBy'])}")
        out.append("")
        out.append(f"{T*2}annotation SummarizationSetBy = Automatic")
        out.append("")
    out.append(f"{T}partition {q(name)} = m")
    out.append(f"{T*2}mode: import")
    out.append(f"{T*2}source =")
    out.append(expr_block(m_query(name, csv_name, columns), 4))
    out.append("")
    out.append(f"{T}annotation PBI_ResultType = Table")
    return "\n".join(out) + "\n"


def write_calculated(name, description, dax, columns, hidden=False):
    """Calculated table; columns: [(name, dataType, sortBy, format, isHidden[, source column in the DAX result])]."""
    out = [f"/// {description}", f"table {q(name)}"]
    if hidden:
        out.append(f"{T}isHidden")
    out += [f"{T}lineageTag: {lineage(name)}", ""]
    for col, dtype, sort_by, fmt, col_hidden, *src in columns:
        source = src[0] if src else col
        out.append(f"{T}column {q(col)}")
        out.append(f"{T*2}dataType: {dtype}")
        if fmt:
            out.append(f"{T*2}formatString: {fmt}")
        if col_hidden:
            out.append(f"{T*2}isHidden")
        out.append(f"{T*2}lineageTag: {lineage(name, col)}")
        out.append(f"{T*2}summarizeBy: none")
        if source == col:
            out.append(f"{T*2}isNameInferred")
        out.append(f"{T*2}sourceColumn: [{source}]")
        if sort_by:
            out.append(f"{T*2}sortByColumn: {q(sort_by)}")
        out.append("")
    out.append(f"{T}partition {q(name)} = calculated")
    out.append(f"{T*2}mode: import")
    out.append(f"{T*2}source =")
    out.append(expr_block(dax, 4))
    out.append("")
    return "\n".join(out) + "\n"


def write_measures_table():
    out = ["/// All model measures, grouped in display folders.", f"table {MEASURES_TABLE}", f"{T}lineageTag: {lineage(MEASURES_TABLE)}", ""]
    for name, dax, fmt, folder, desc in MEASURES:
        if desc:
            out.append(f"{T}/// {desc}")
        body = dax.strip("\n")
        if "\n" in body:
            out.append(f"{T}measure {q(name)} =")
            out.append(expr_block(body, 3))
        else:
            out.append(f"{T}measure {q(name)} = {body}")
        if fmt:
            out.append(f"{T*2}formatString: {fmt}")
        if name.startswith("_"):
            out.append(f"{T*2}isHidden")
        out.append(f"{T*2}displayFolder: {folder}")
        out.append(f"{T*2}lineageTag: {lineage(MEASURES_TABLE, name)}")
        if name in DYNAMIC_FORMATS:
            out.append("")
            out.append(f"{T*2}formatStringDefinition =")
            out.append(expr_block(DYNAMIC_FORMATS[name], 4))
        out.append("")
    out.append(f"{T}column Placeholder")
    out.append(f"{T*2}dataType: string")
    out.append(f"{T*2}isHidden")
    out.append(f"{T*2}lineageTag: {lineage(MEASURES_TABLE, 'Placeholder')}")
    out.append(f"{T*2}summarizeBy: none")
    out.append(f"{T*2}isNameInferred")
    out.append(f"{T*2}sourceColumn: [Placeholder]")
    out.append("")
    out.append(f"{T}partition {MEASURES_TABLE} = calculated")
    out.append(f"{T*2}mode: import")
    out.append(f"{T*2}source = ROW ( \"Placeholder\", \"\" )")
    out.append("")
    return "\n".join(out) + "\n"


def pl_line_dax():
    rows = ",\n".join(f'        {{ "{n}", {s}, "{f}", {fav}, "{style}" }}' for n, s, f, fav, style in PL_LINES)
    return f"""DATATABLE (
    "Line", STRING,
    "Sort", INTEGER,
    "Format", STRING,
    "Favourable", INTEGER,
    "Style", STRING,
    {{
{rows}
    }}
)"""


def check_csv_headers():
    """Fail early if a CSV's header doesn't match the declared columns."""
    for name, csv_name, _, columns in CSV_TABLES:
        with open(DATA / f"{csv_name}.csv", newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        if header != list(columns):
            raise SystemExit(f"{csv_name}.csv columns {header} don't match table {name} {list(columns)}")


def build():
    check_csv_headers()
    if DEF.exists():
        shutil.rmtree(DEF)  # regenerate from scratch so removed objects don't linger
    (DEF / "tables").mkdir(parents=True)
    files = {}
    files["database.tmdl"] = "database\n\tcompatibilityLevel: 1601\n"
    table_names = [t[0] for t in CSV_TABLES] + ["P&L Line", "Revenue Growth Flex", "Opex Flex", MEASURES_TABLE]
    files["model.tmdl"] = (
        "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tdiscourageImplicitMeasures\n"
        "\tsourceQueryCulture: en-US\n\tdataAccessOptions\n\t\tlegacyRedirects\n\t\treturnErrorValuesAsNull\n\n"
        "annotation PBI_ProTooling = [\"TMDL-Extension\"]\n\n"
        + "\n".join(f"ref table {q(t)}" for t in table_names) + "\n"
    )
    files["expressions.tmdl"] = (
        "/// Where the CSVs live: a URL ending in / (default: this repo on GitHub) or a local folder path ending in \\\n"
        f'expression DataFolder = "{DEFAULT_DATA_FOLDER}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
        f"\tlineageTag: {lineage('DataFolder')}\n\n"
        f"\tannotation PBI_ResultType = Text\n\n"
        "/// Load one CSV from DataFolder (web or local) as a table with promoted headers.\n"
        "expression LoadCsv =\n"
        + expr_block("""(fileName as text) as table =>
let
    Raw =
        if Text.StartsWith(DataFolder, "http")
        then Web.Contents(DataFolder, [RelativePath = fileName])
        else File.Contents(DataFolder & fileName),
    Parsed = Csv.Document(Raw, [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Parsed, [PromoteAllScalars = true])
in
    Promoted""", 2)
        + f"\n\tlineageTag: {lineage('LoadCsv')}\n\n\tannotation PBI_ResultType = Function\n"
    )
    rel = []
    for f_tab, f_col, t_tab, t_col in RELATIONSHIPS:
        rel.append(f"relationship {lineage('rel', f_tab, f_col, t_tab)}\n\tfromColumn: {q(f_tab)}.{q(f_col)}\n\ttoColumn: {q(t_tab)}.{q(t_col)}\n")
    files["relationships.tmdl"] = "\n".join(rel)
    for name, csv_name, desc, columns in CSV_TABLES:
        files[f"tables/{name}.tmdl"] = write_table(name, csv_name, desc, columns)
    files["tables/P&L Line.tmdl"] = write_calculated(
        "P&L Line", "Rows of the income statement matrix, in order, with how to format and judge each.", pl_line_dax(),
        [("Line", "string", "Sort", None, False), ("Sort", "int64", None, None, True), ("Format", "string", None, None, True),
         ("Favourable", "int64", None, None, True), ("Style", "string", None, None, True)])
    files["tables/Revenue Growth Flex.tmdl"] = write_calculated(
        "Revenue Growth Flex", "What-if: extra revenue growth applied to every forecast year (−10pp to +10pp).",
        "GENERATESERIES ( -0.10, 0.10, 0.01 )", [("Revenue Growth Flex", "double", None, PP, False, "Value")])
    files["tables/Opex Flex.tmdl"] = write_calculated(
        "Opex Flex", "What-if: change to forecast operating expenses (−10% to +10%).",
        "GENERATESERIES ( -0.10, 0.10, 0.01 )", [("Opex Flex", "double", None, PP, False, "Value")])
    files[f"tables/{MEASURES_TABLE}.tmdl"] = write_measures_table()

    for rel_path, content in files.items():
        path = DEF / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (MODEL_DIR / "definition.pbism").write_text(
        '{\n  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",\n'
        '  "version": "4.2",\n  "settings": {}\n}\n', encoding="utf-8")
    (MODEL_DIR / ".platform").write_text(
        '{\n  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",\n'
        f'  "metadata": {{\n    "type": "SemanticModel",\n    "displayName": "{NAME}"\n  }},\n'
        f'  "config": {{\n    "version": "2.0",\n    "logicalId": "{lineage("semantic-model")}"\n  }}\n}}\n', encoding="utf-8")
    return files


if __name__ == "__main__":
    files = build()
    print(f"Wrote {len(files)} TMDL files to {DEF.relative_to(ROOT)}; {len(MEASURES)} measures.")
