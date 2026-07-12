"""
parser_all_regions.py
-----------------------
Parses the downloaded NLDC/Grid-India Daily PSP Report Excel files
(data/raw_excel/*.xls) and extracts per-region, per-fuel generation into
the shared team schema:

    date, region, coal_mwh, hydro_mwh, wind_mwh, solar_mwh,
    nuclear_mwh, gas_mwh, others_mwh

Writes one CSV per region: data/raw/nr.csv, wr.csv, sr.csv, er.csv, ner.csv
(All regions are produced since they're all in the same source file --
just take whichever ones your task needs, e.g. nr.csv + wr.csv.)

SOURCE STRUCTURE (sheet "MOP_E" of each .xls):
Each daily report has TWO separate places generation shows up:
  - Section A ("Hydro Gen (MU)", "Wind Gen (MU)", "Solar Gen (MU)*")
    -- individual Hydro/Wind/Solar figures per region.
  - Section G ("G. Sourcewise generation (Gross) (MU)")
    -- Coal, Lignite, Hydro, Nuclear, Gas/Naptha/Diesel, and a COMBINED
       "RES (Wind, Solar, Biomass & Others)" figure, plus a Total row.

We pull Coal / Nuclear / Gas / Total from Section G, and Wind / Solar
individually from Section A (they're NOT split out in Section G). Hydro
is consistent between both sections so either works.

"others_mwh" = Total - Coal - Hydro - Nuclear - Gas - Wind - Solar
This folds in Lignite + Biomass + any other misc RES category, since our
shared schema doesn't have separate columns for those. Flag this to the
team if a more granular breakdown turns out to be needed later.

NOTE: some cells use '-' instead of 0 for "no generation" (seen on ER's
wind/solar in some reports) -- this script treats '-' as 0.0.

Row positions are NOT hardcoded to fixed line numbers -- section lengths
can shift (e.g. "C. Power Supply Position in States" varies by year as
states/entities are added), so this searches for each section by its
label text and finds the nearest matching header row instead.
"""

import os
import re
import glob
import pandas as pd

IN_DIR = "data/raw_excel"
OUT_DIR = "data/raw"
MU_TO_MWH = 1000
REGIONS = ["NR", "WR", "SR", "ER", "NER"]

SCHEMA_COLUMNS = [
    "date", "region", "coal_mwh", "hydro_mwh", "wind_mwh",
    "solar_mwh", "nuclear_mwh", "gas_mwh", "others_mwh",
]


def to_float(val):
    """Handles '-' placeholders and stray commas/whitespace."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s in ("-", "", "nan", "NaN"):
        return 0.0
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None  # genuinely unparseable -- caller should flag this


def find_row(df, label_prefix, start=0):
    for i in range(start, len(df)):
        val = df.iat[i, 0]
        if isinstance(val, str) and val.strip().lower().startswith(label_prefix.lower()):
            return i
    return None


def find_region_header_row(df, near_row, window=4):
    """Find the row (within `window` rows of near_row) that has region
    codes like 'NR', 'WR' as column headers."""
    for i in range(max(0, near_row - window), min(len(df), near_row + window)):
        row_vals = [v.strip() if isinstance(v, str) else v for v in df.iloc[i]]
        if "NR" in row_vals and "WR" in row_vals:
            return i
    return None


def build_col_map(df, header_row):
    header_vals = list(df.iloc[header_row])
    return {
        v.strip(): idx for idx, v in enumerate(header_vals)
        if isinstance(v, str) and v.strip() in REGIONS
    }


def extract_date_from_filename(filename: str) -> str:
    """Filenames look like '01.04.26_NLDC_PSP_726.xls' -> '2026-04-01'."""
    m = re.search(r"(\d{2})[._-](\d{2})[._-](\d{2})", filename)
    if m:
        dd, mm, yy = m.groups()
        yyyy = f"20{yy}"
        return f"{yyyy}-{mm}-{dd}"
    return None  # flag for manual review


def parse_one_file(path: str):
    """Returns a dict {region: {schema fields}} or None if parsing failed."""
    df = pd.read_excel(path, sheet_name="MOP_E", header=None)

    g_label_row = find_row(df, "G. Sourcewise generation")
    if g_label_row is None:
        return None
    g_header_row = find_region_header_row(df, g_label_row + 1)
    if g_header_row is None:
        return None
    g_col_map = build_col_map(df, g_header_row)

    coal_row = find_row(df, "Coal", start=g_header_row)
    hydro_row = find_row(df, "Hydro", start=g_header_row)
    nuclear_row = find_row(df, "Nuclear", start=g_header_row)
    gas_row = find_row(df, "Gas,", start=g_header_row)
    total_row = find_row(df, "Total", start=g_header_row)

    wind_row = find_row(df, "Wind Gen")
    solar_row = find_row(df, "Solar Gen")
    a_header_row = find_region_header_row(df, 4)
    if a_header_row is None or wind_row is None or solar_row is None:
        return None
    a_col_map = build_col_map(df, a_header_row)

    if None in (coal_row, hydro_row, nuclear_row, gas_row, total_row):
        return None

    results = {}
    for region in REGIONS:
        if region not in g_col_map or region not in a_col_map:
            continue
        gc = g_col_map[region]
        ac = a_col_map[region]

        coal = to_float(df.iat[coal_row, gc])
        hydro = to_float(df.iat[hydro_row, gc])
        nuclear = to_float(df.iat[nuclear_row, gc])
        gas = to_float(df.iat[gas_row, gc])
        total = to_float(df.iat[total_row, gc])
        wind = to_float(df.iat[wind_row, ac])
        solar = to_float(df.iat[solar_row, ac])

        if None in (coal, hydro, nuclear, gas, total, wind, solar):
            continue  # unparseable cell somewhere -- skip this region for this day

        others = round(total - coal - hydro - nuclear - gas - wind - solar, 4)

        results[region] = {
            "coal_mwh": coal * MU_TO_MWH,
            "hydro_mwh": hydro * MU_TO_MWH,
            "wind_mwh": wind * MU_TO_MWH,
            "solar_mwh": solar * MU_TO_MWH,
            "nuclear_mwh": nuclear * MU_TO_MWH,
            "gas_mwh": gas * MU_TO_MWH,
            "others_mwh": others * MU_TO_MWH,
        }
    return results


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(IN_DIR, "*.xls")) +
                    glob.glob(os.path.join(IN_DIR, "*.xlsx")))
    print(f"Found {len(files)} Excel files in {IN_DIR}/")

    records = {region: [] for region in REGIONS}
    failed = []

    for i, path in enumerate(files, 1):
        filename = os.path.basename(path)
        report_date = extract_date_from_filename(filename)
        if report_date is None:
            failed.append(f"{filename} (could not parse date from filename)")
            continue

        try:
            parsed = parse_one_file(path)
        except Exception as e:
            failed.append(f"{filename} (error: {e})")
            continue

        if not parsed:
            failed.append(f"{filename} (sheet structure not recognized)")
            continue

        for region, values in parsed.items():
            row = {"date": report_date, "region": region}
            row.update(values)
            records[region].append(row)

        if i % 100 == 0:
            print(f"  ...parsed {i}/{len(files)}")

    for region in REGIONS:
        df_out = pd.DataFrame(records[region], columns=SCHEMA_COLUMNS)
        df_out = df_out.sort_values("date")
        out_path = os.path.join(OUT_DIR, f"{region.lower()}.csv")
        df_out.to_csv(out_path, index=False)
        print(f"Saved {len(df_out)} rows to {out_path}")

    if failed:
        print(f"\n{len(failed)} files failed or need review.")
        with open("data_quality_notes.md", "a") as f:
            f.write("\n## Excel parsing issues (parser_all_regions.py)\n")
            for item in failed:
                f.write(f"- {item}\n")
        print("Logged to data_quality_notes.md")


if __name__ == "__main__":
    main()