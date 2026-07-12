import pandas as pd
import openpyxl

FILE = "CO2_Database_V_21.0.xlsx"


def load_data_sheet(path=FILE):
    """Load the 'Data' sheet: one clean table, unit-level records for FY2024-25."""
    df = pd.read_excel(path, sheet_name="Data", header=0, engine="openpyxl")
    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
    df = df.dropna(how="all")
    return df


def load_raw_sheet(sheet_name, path=FILE):
    """For report-style sheets: load as a raw grid with no assumed header."""
    df = pd.read_excel(path, sheet_name=sheet_name, header=None, engine="openpyxl")
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    return df


def load_all_sheets(path=FILE):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = {}
    for name in wb.sheetnames:
        if name == "Data":
            sheets[name] = load_data_sheet(path)
        else:
            sheets[name] = load_raw_sheet(name, path)
    return sheets


if __name__ == "__main__":
    sheets = load_all_sheets()

    for name, df in sheets.items():
        print(f"\n=== {name} ===")
        print("shape:", df.shape)
        print(df.head(3))

    data_df = sheets["Data"]
    print("\nData sheet columns:")
    print(list(data_df.columns))

    print("\nSample rows:")
    print(data_df[["NAME", "STATE", "SYSTEM", "TYPE", "FUEL 1"]].head())