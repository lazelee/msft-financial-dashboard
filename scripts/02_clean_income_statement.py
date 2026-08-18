import pandas as pd
import re

def clean_sec_data(ticker="MSFT"):
    # load raw data
    df_raw = pd.read_csv(f"data/raw/{ticker}_income_statement_raw.csv")

    # find date columns using regex
    date_pattern = re.compile(r'\d{4}-\d{2}-\d{2}')
    date_columns = [col for col in df_raw.columns if date_pattern.search(col)]

    # keep concept label and date columns
    columns_to_keep = ['concept', 'label'] + date_columns
    df_clean = df_raw[columns_to_keep].copy()

    # unpivot data so years in same table
    df_melted = df_clean.melt(
        id_vars=['concept', 'label'], 
        var_name='date', 
        value_name='Amount'
    )

    # extract year from date
    df_melted['date'] = df_melted['date'].str.replace(r' \(FY\)', '', regex=True)
    df_melted['date'] = pd.to_datetime(df_melted['date'], errors='coerce')
    df_melted['Year'] = df_melted['date'].dt.year

    # drop empty rows
    df_melted.dropna(subset=['Amount'], inplace=True)

    # map SEC concept to account category
    def map_category(concept):
        concept = str(concept).lower()
        if 'revenue' in concept:
            return 'Revenue'
        elif 'cost' in concept:
            return 'COGS'
        elif 'expense' in concept:
            return 'Expenses'
        elif 'profit' in concept or 'margin' in concept:
            return 'Gross Profit'
        elif 'operatingincome' in concept:
            return 'Operating Income'
        elif 'netincome' in concept:
            return 'Net Income'
        elif 'tax' in concept:
            return 'Taxes'
        else:
            return 'Other'

    df_melted['Account Category'] = df_melted['concept'].apply(map_category)

    # make COGS expenses and taxes negative
    def apply_sign(row):
        category = row['Account Category']
        amount = row['Amount']
        if category in ['COGS', 'Expenses', 'Taxes']:
            return -abs(amount) # force negative
        return amount

    df_melted['Amount'] = df_melted.apply(apply_sign, axis=1)

    # rename label to account name
    df_melted.rename(columns={'label': 'Account Name'}, inplace=True)

    # filter to final columns
    final_columns = ['Account Category', 'Account Name', 'Year', 'Amount']
    df_final = df_melted[final_columns]

    # save clean file
    output_path = f"data/processed/{ticker}_income_statement_clean.csv"
    df_final.to_csv(output_path, index=False)

    print(f"Data successfully cleaned and saved to {output_path}")
    print("\nData Preview:")
    print(df_final.head())

if __name__ == "__main__":
    clean_sec_data("MSFT")

