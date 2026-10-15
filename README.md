# Microsoft (MSFT) FP&A Model: P&L, Segments and Driver-Based Forecast

A Power BI financial model of Microsoft built from SEC filings. It covers 17 years of reconciled
income statements, segment and product-line detail, and a driver-based FY2027–FY2029 forecast in
three scenarios. It also includes exact net income and EPS bridges, a sensitivity grid, a
plan-vs-actual variance and a backtest of the model's own accuracy.

Everything is reproducible from code:
- a Python pipeline pulls the filings, reconciles every number and runs the forecast
- the Power BI project (model and report) is generated from code and checked by 116 automated tests

## What's in the report

| Page | What it shows |
| --- | --- |
| **Overview** | FY2026 headline KPIs; FY2027E EPS for the selected scenario and probability-weighted; revenue and operating margin FY2016–FY2029E; segment mix; EPS trend; cash flow vs AI capex |
| **Income Statement** | Full P&L FY2022–FY2029E from one P&L-template matrix (costs negative, per-row formatting), plus YoY growth and margin changes |
| **Quarterly** | Q1 FY25 – Q4 FY27E P&L; FY2027 phased with historical seasonality |
| **Segments** | Productivity and Business Processes, Intelligent Cloud, More Personal Computing: revenue, margins, gross-margin compression in Intelligent Cloud; product-line growth and mix; Microsoft Cloud |
| **Forecast & Scenarios** | Scenario switch (Base / Upside / Downside), live what-if sliders for revenue growth and opex, net income / EPS / segment operating income bridges, scenarios side by side, driver table |
| **Plan vs Actual** | FY2026 actual vs the plan the model would have produced from FY2025 (no hindsight), with favourable / unfavourable colouring |
| **Sensitivity** | FY2027E EPS across ±6pp revenue growth × ±300bp operating margin |
| **Model Validation** | 286 reconciliation checks; backtest since FY2013 against a naive forecast |

## Headline numbers (from the current build)

| | FY2026 actual | FY2027E Base | FY2027E Upside | FY2027E Downside |
| --- | --- | --- | --- | --- |
| Revenue ($B) | 331.8 | 383.9 (+15.7%) | 393.3 (+18.5%) | 369.3 (+11.3%) |
| Operating margin | 46.8% | 47.1% | 48.2% | 45.6% |
| Diluted EPS | $17.95 | $19.97 | $21.38 | $17.65 |

Probability-weighted FY2027E EPS: **$19.75**. Revenue growth adds $2.64/share, but FY2026's
one-off $10.7B other-income gain doesn't repeat (−$1.01). That's why EPS grows about 11% while
operating income grows about 16%.

## FP&A techniques used

- **Driver-based, segment-level build** with explicit assumptions per segment, scenario and year,
  each with a written rationale ([`assumptions/assumptions.csv`](assumptions/assumptions.csv))
- **Operating leverage and cost stickiness:** opex is semi-fixed and only partly follows revenue
- **Exact after-tax bridges** (volume, gross margin rate, opex leverage, other income, tax rate,
  share count) that sum to the change by construction
- **Scenario analysis** with probability weighting, plus **sensitivity** on the two drivers that matter most
- **Plan vs actual variance** with favourable/unfavourable logic per line
- **Model validation:** backtest against a naive benchmark, with one-off years identified by rule
- **Data integrity:** every extracted number reconciles to Microsoft's reported subtotals, and
  revenue breakdowns are kept as separate dimensions so they can never double count
- **DAX:** actual-else-scenario logic, a P&L template table with dynamic format strings, what-if
  parameters, time comparisons on fiscal periods, and probability-weighted measures

How each number is produced, with formulas: [docs/methodology.md](docs/methodology.md).

## Open it in Power BI

You need a current version of **Power BI Desktop** (free, Windows; update it if it's older than late 2026). The project uses
the Power BI Project format (PBIP), so it's plain text in this repo rather than a binary `.pbix` file.

1. Download this repo: **Code → Download ZIP** and unzip it, or `git clone`.
2. Open `powerbi/MSFT Financial Model.pbip` in Power BI Desktop.
3. Select **Refresh**. The data loads straight from this repo's `data/` folder on GitHub. If Power
   BI asks how to connect to `raw.githubusercontent.com`, choose **Anonymous**.

To use a local copy of the data instead (for example after rebuilding it), go to
**Transform data → Edit parameters** and set **DataFolder** to the folder path, ending in a
backslash, e.g. `C:\Users\you\msft-financial-dashboard\data\`.

To save a single shareable file, use **File → Save as** and choose `.pbix`.

## Rebuild the data from SEC

Python 3.9+:

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name your.email@example.com"   # SEC requires a contact; Windows: $env:SEC_USER_AGENT="..."
python -m pipeline.build        # pull filings, reconcile, forecast, write data/*.csv
python -m pytest                # 116 checks: data, forecast math, Power BI project
```

To change the forecast, edit [`assumptions/assumptions.csv`](assumptions/assumptions.csv) and run
`python -m pipeline.build` again. When Microsoft files a new 10-K or 10-Q, the same command picks it
up. The Power BI files only need regenerating if you change the model or report definitions
(`python powerbi/build_model.py`, `python powerbi/build_report.py`).

## Repository layout

```
assumptions/assumptions.csv     forecast drivers by segment, scenario and year, with rationale
pipeline/
  sec.py                        SEC EDGAR client (company facts, filings, XBRL instances; cached)
  extract.py                    consolidated P&L, KPIs, segments and revenue breakdowns
  model.py                      driver model, quarterly phasing, bridges, sensitivity, plan, backtest
  build.py                      runs everything, reconciles, writes the star schema to data/
data/                           Power BI tables (dim_* and fact_*), regenerated by the pipeline
powerbi/
  MSFT Financial Model.pbip     open this in Power BI Desktop
  MSFT Financial Model.SemanticModel/   tables, relationships, 89 DAX measures (TMDL)
  MSFT Financial Model.Report/          8 report pages (PBIR)
  build_model.py, build_report.py       generate the two folders above
docs/methodology.md             sources, reconciliation, formulas, validation, limitations
tests/                          data, model and Power BI project tests
```

## Notes

- Data: SEC EDGAR XBRL (Microsoft 10-K and 10-Q filings).
- This is an illustrative model for learning and portfolio purposes, not investment advice or
  company guidance. Forecast assumptions are the author's, documented line by line.
