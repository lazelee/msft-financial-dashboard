import pandas as pd
from edgar import set_identity, Company

def extract_financial_data(ticker="MSFT"):
    # SEC is strict about who pings their database
    # identify with email so they know we arent bots
    # update email address in set_identity() before running
    set_identity("calebj.lee@alumni.utoronto.ca") 

    print(f"Connecting to SEC EDGAR for {ticker}...")

    # create company object as gateway to public filings
    company = Company(ticker)

    # ask API to fetch recent financial statements
    financials = company.get_financials()

    # grab income statement for revenue and operating expenses
    # convert straight to pandas dataframe to avoid raw XBRL
    income_statement = financials.income_statement()
    df_income = income_statement.to_dataframe()

    # pull balance sheet for assets and debt
    balance_sheet = financials.balance_sheet()
    df_balance = balance_sheet.to_dataframe()

    # save direct to data/raw folder for cleaning script
    # dropped ../ so it saves correctly from script location
    df_income.to_csv(f"data/raw/{ticker}_income_statement_raw.csv")
    df_balance.to_csv(f"data/raw/{ticker}_balance_sheet_raw.csv")

    print(f"Successfully extracted and saved SEC filings for {ticker}!")

    # print first few rows to sanity check data
    print("\nIncome Statement Preview:")
    print(df_income.head())

if __name__ == "__main__":
    # run function with MSFT as test case
    extract_financial_data("MSFT")

