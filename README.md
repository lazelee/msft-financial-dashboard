# Microsoft (MSFT) Financial Forecast & P&L Dashboard

## Project Overview
This project is an interactive Financial Planning & Analysis (FP&A) dashboard built in Power BI, supported by a custom Python data pipeline. It ingests raw Microsoft SEC filings to construct a historical Profit & Loss (P&L) statement and models a dynamic projected income statement for the 2027 fiscal year. 

Designed with corporate finance professionals in mind, this tool allows users to adjust revenue and expense growth assumptions in real-time, instantly visualizing the bottom-line impact and year-over-year variances across different business scenarios. 

## Key Skills
* **Financial Modeling & FP&A:** Scenario planning, dynamic forecasting, variance analysis, and margin tracking.
* **Data Engineering (Python):** API integration, data extraction, and programmatic data cleaning/transformation.
* **Business Intelligence (Power BI):** Advanced DAX (dynamic measures, `SWITCH` functions, context transition), interactive bookmarks, and custom visual formatting.

## Dashboard
![image alt](https://github.com/lazelee/msft-financial-dashboard/blob/b98b78c013247a97a1bc345bef857131b72770ac/images/msft%20p%26l%20dashboard.png)

---

## Python Pipeline & Data Processing
The foundation of this dashboard is a clean, relational dataset generated via a custom Python script, ensuring accuracy directly from audited financial reports.

### 1. SEC Filings Extraction
* Leveraged the SEC EDGAR API (utilizing user identity headers) to programmatically download raw MSFT 10-K filings across historical years.
* Parsed XBRL/HTML data to extract unstructured income statement tables directly from the filings.

### 2. Data Cleaning & Transformation
* Consolidated raw line items into a standardized reporting schema (`MSFT_income_statement_clean`) to normalize category names across different reporting years.
* Maintained negative sign formatting on expenses and Cost of Goods Sold (COGS) to align strictly with standard financial statement representations.
* Engineered a custom sort-order index to ensure P&L line items flow in the correct hierarchical sequence (Revenue down to Net Income) within Power BI matrix visuals.

### 3. YoY Variance Calculations
* Computed historical year-over-year (YoY) dollar changes and percentage variances.
* Applied absolute values (`ABS`) to base-year amounts when calculating expense growth rates to prevent mathematical sign errors and ensure accurate variance percentages.
* Exported the final structured dataset to a CSV format optimized for the Power BI data model.

---

## Dashboard Features & Interactive Elements
* **Scenario Forecasting (What-If Parameters):** Users can adjust independent sliders for Revenue Growth and Expense Growth.
* **Interactive Bookmarks:** Built-in scenario buttons allow users to instantly toggle between predefined growth environments:
  * **Bull Case:** High revenue growth, reduced expense scaling.
  * **Base Case:** Flat/historical baseline.
  * **Bear Case:** Revenue contraction, increased expenses.
* **Variance Analysis Bridge:** A dynamic waterfall chart bridges the 2026 actual Net Income to the 2027 projected Net Income, isolating the exact dollar impact of revenue and expense assumptions.
* **Actual vs. Forecast Matrix:** A standard FP&A matrix displaying 2026 Actuals, 2027 Forecast, Variance ($), and Variance (%). Conditional formatting highlights positive/favorable variances in green and negative/unfavorable variances in red.

---

## Technical Notes & DAX Logic
To ensure accurate forecasting, specific DAX methodologies were implemented:
* **Handling Negative Expenses:** Because the raw SEC data represents expenses as negative integers, the DAX logic relies on addition (rather than subtraction) when calculating totals like Operating Income and Net Income. Absolute values are used in division denominators to ensure KPI percentages render correctly.
* **Context Clearing:** Functions like `REMOVEFILTERS` were heavily utilized in the underlying variable logic to ensure that global base amounts could be calculated across the entire matrix, preventing blank returns on calculated rows like Gross Profit and Operating Income.

---

## How to Navigate This Repository
1. **`data/` Folder:** Contains the raw and cleaned `MSFT_income_statement_clean.csv` utilized in the data model.
2. **`scripts/` Folder:** Contains the Python `.py` or Jupyter Notebook files used for the SEC EDGAR API extraction and data processing.
3. **`dashboard/` Folder:** Contains the `.pbix` Power BI file.
4. **`images/` Folder:** Contains high-resolution screenshots of the dashboard.

## How to Use
* download `.pbix` file.
* open in Power BI Desktop.
* use scenario buttons to change growth parameters.
* view pdf export if Power BI is not installed.
