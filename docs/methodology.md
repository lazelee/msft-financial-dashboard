# Methodology

How the numbers are sourced, reconciled, forecast and validated. All amounts are US$ millions
except per-share data. Microsoft's fiscal year ends June 30 (FY2026 = July 2025 – June 2026).

## 1. Data lineage

| Source | What it provides | How it's used |
| --- | --- | --- |
| SEC XBRL company facts API (`data.sec.gov/api/xbrl/companyfacts`) | Every consolidated fact Microsoft has filed | Income statement FY2010–FY2026, quarters FY2017–FY2026, EPS, diluted shares, cash flow |
| XBRL instance documents of each 10-K/10-Q since FY2025 | Dimensional facts | Segment P&L, revenue by product line / revenue type / geography, Microsoft Cloud revenue |

**Selection rules**

- Each P&L line maps to the XBRL tags Microsoft used for it over time (e.g. revenue was
  `SalesRevenueNet` until FY2017, then `RevenueFromContractWithCustomerExcludingAssessedTax`).
- When a period was filed more than once (restatements, comparative columns), the **most recently
  filed** value wins, so history is on the latest basis of presentation.
- Fiscal Q4 isn't filed as a quarter; it's derived as **FY − (Q1 + Q2 + Q3)**.
- Quarterly history starts in FY2017. Microsoft restated FY2016–17 annual figures for ASC 606
  but not FY2016 quarters, so an FY2016 Q4 derived from them would be meaningless.
- "Impairment, integration and restructuring" has no consistent tag. It is derived as
  gross margin − R&D − S&M − G&A − operating income, and matches the reported charges exactly
  (FY2012 $6,193M aQuantive; FY2015 $10,011M Nokia; FY2016 $1,110M; FY2017 $306M).
- Segments use Microsoft's FY2025 structure. Filings since then restate comparatives on the new
  basis, giving annual FY2023–FY2026 and quarterly FY2024–FY2026. Segment cost of revenue and
  operating expenses were first disclosed in FY2025 under ASU 2023-07, so FY2024 quarters only
  have revenue and operating income.
- Renamed product lines with identical values (Gaming → Xbox; Search and news advertising →
  Search advertising) are mapped to the current name so each line is one continuous series.

**Breakdowns are kept separate.** Segments, product lines, revenue type (product vs service),
geography and Microsoft Cloud are different cuts of the *same* revenue. Each is its own
dimension and is never added to another. (The previous version of this project summed them all,
which put FY2026 revenue at $1.9 trillion and gross margin at 11.9%; the actual figures are
$331.8B and 67.9%.)

## 2. Reconciliation (every build)

The build stops if any check fails. Results are published to `data/fact_checks.csv` and shown on
the *Model Validation* page.

| Check | Periods |
| --- | --- |
| Revenue − cost of revenue = reported gross margin | every year and quarter |
| Lines (revenue − costs ± other income − tax) = reported net income | every year and quarter |
| Operating income + other income = reported pretax income | wherever pretax income is tagged |
| Segment revenue and operating income sum to consolidated | every segment period |
| Each segment: revenue − cost of revenue − opex = operating income | where segment costs are disclosed |
| Product lines, revenue type and geography each sum to revenue | every period |
| Forecast segments sum to consolidated; forecast quarters sum to the year | every scenario |

## 3. Forecast model

Built bottom-up from the three segments, for FY2027E–FY2029E in three scenarios. Every driver is
an explicit, documented input in [`assumptions/assumptions.csv`](../assumptions/assumptions.csv).

**Segment build** (s = segment, t = year):

```
Revenue(s,t)          = Revenue(s,t−1) × (1 + revenue growth(s,t))
Gross margin %(s,t)   = Gross margin %(s,t−1) + gross margin change(s,t)
Cost of revenue(s,t)  = Revenue(s,t) × (1 − Gross margin %(s,t))
Opex(s,t)             = Opex(s,t−1) × (1 + opex growth(s,t))
Operating income(s,t) = Revenue − Cost of revenue − Opex
```

Opex grows at its own rate, not as a % of revenue. Most operating costs (headcount, R&D programs)
are semi-fixed, so faster revenue growth widens margins and slower growth narrows them. This is
operating leverage, and modelling opex as a fixed % of revenue would hide it.

**Base revenue growth** fades from the latest actual rate toward a long-run rate:
`g(t) = g_LR + (g_last − g_LR) × 0.6^t`. That gives Intelligent Cloud 29.7% → 24.2% / 20.9% /
19.0%. Upside and Downside shift growth by segment-specific amounts. **Cost stickiness:** in those
scenarios opex moves by only 25% of the revenue change, because costs don't fall as fast as revenue.

**Consolidated:**

```
Opex lines (R&D, S&M, G&A) = total segment opex × last actual year's mix
Pretax income              = operating income + other income (expense)
Tax                        = pretax income × effective tax rate
Net income                 = pretax income − tax
Diluted shares(t)          = shares(t−1) × (1 + share count change)
Diluted EPS                = net income ÷ diluted shares
Free cash flow             = revenue × OCF % − revenue × capex %
```

**Quarterly phasing (FY2027E).** Revenue, cost of revenue and opex are each split by the segment's
average quarterly share of the year: revenue from FY2024–26, costs from FY2025–26 (the years
disclosed). Other income is spread evenly, and the year's tax rate is applied to each quarter.

**Probability-weighted outlook.** Base 50%, Upside 25%, Downside 25% (editable in the assumptions file).

## 4. Analysis outputs

**Net income bridge (exact, after tax).** With margin m = OI/R, gross margin gm, opex ratio ox and
tax rate t:

```
ΔOI = ΔR·m0  +  R1·(gm1 − gm0)  −  R1·(ox1 − ox0)          (revenue growth, GM rate, opex leverage)
ΔNI = [ΔOI + ΔOther income]·(1 − t0)  −  PBT1·(t1 − t0)      (after tax, then the tax-rate effect)
ΔEPS = ΔNI / S0  +  NI1·(1/S1 − 1/S0)                         (adds the share-count effect)
```

Steps sum exactly to the change; the build asserts it. For FY2026 → FY2027E (Base), revenue growth
adds $2.64/share, but FY2026's one-off $10.7B other-income gain not repeating costs $1.01/share.
That is why EPS grows about 11% while operating income grows about 16%.

**Sensitivity.** FY2027E Base EPS across ±6pp revenue growth (every segment) × ±300bp operating
margin. Opex absorbs 25% of the revenue change, matching the scenarios.

**Plan vs actual.** A "plan" for FY2026, rebuilt by applying the model's standing rules to FY2025
actuals only (no hindsight): revenue growth faded toward long-run, gross margin held, opex at the
prior year's growth, other income and tax at their 3-year averages. FY2026 beat that plan on
revenue (+3.9%, all Intelligent Cloud) but missed on Intelligent Cloud margin (41.3% vs 44.0%) as
AI infrastructure depreciation came through cost of revenue.

## 5. Validation (backtest)

One-year-ahead forecasts from every fiscal year since FY2013, using the same mechanics at the
consolidated level with only information available at the time. Each is compared with a naive
forecast (last year's growth repeats, margins held).

| Metric | Model error (MAPE), all years | Naive, all years | Model, ex one-off years | Naive, ex one-off years |
| --- | --- | --- | --- | --- |
| Revenue | 4.4% | 4.6% | 3.9% | 4.5% |
| Operating income | 12.5% | 14.0% | 6.6% | 7.9% |
| Net income | 21.4% | 26.3% | 8.4% | 9.6% |
| Diluted EPS | 21.5% | 26.1% | 8.4% | 9.7% |

*One-off years* are flagged by rule: impairments over 1% of revenue, a tax rate more than 7pp off
the assumed rate, or other income moving by more than 2% of revenue. That flags FY2015 (Nokia),
FY2016, FY2018 (US tax reform), FY2019 and FY2026 (other-income gain). The model is better than
naive on every line, but only modestly on revenue. A driver model earns its keep on margins and
below-the-line items more than on the top line.

## 6. Limitations

- This is an illustrative outside-in model built from public filings, not guidance or advice.
  Scenario drivers are the analyst's assumptions, each with a written rationale.
- Segment history on the current basis only goes back to FY2023, so segment drivers rest on
  three years of data. Long-run history is consolidated only.
- R&D / S&M / G&A forecasts use a fixed mix, because segments don't report opex by function.
- Other income (OpenAI-related equity-method results and investment gains) is volatile and
  modelled as a flat assumption per scenario.
- Microsoft Cloud revenue overlaps the segments and is shown as a KPI only.
