"""
Week 2, Day 1 — Clean NR/WR data + compute daily Carbon Intensity (CI)

Pipeline:
  1. Load raw region CSV (nr.csv / wr.csv)
  2. Check + remove duplicate dates (log what was removed)
  3. Reindex to a full daily calendar range, flag + fill gaps
  4. Sanity-check daily total_generation against a CSEP reference file
  5. Compute CI = Σ(generation_source × EF_source) / total_generation
  6. Write cleaned + CI-augmented CSV to data/processed/

Usage:
    python clean_and_compute_ci.py --region NR --input data/raw/nr.csv
    python clean_and_compute_ci.py --region WR --input data/raw/wr.csv
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# ----------------------------------------------------------------------
# CONFIG — edit these two blocks to match your actual files
# ----------------------------------------------------------------------

# Matches the actual nr.csv / wr.csv headers:
# date, region, coal_mwh, hydro_mwh, wind_mwh, solar_mwh, nuclear_mwh,
# gas_mwh, others_mwh
GENERATION_COLUMNS = {
    "coal_mwh": "coal",
    "hydro_mwh": "hydro",
    "wind_mwh": "wind",
    "solar_mwh": "solar",
    "nuclear_mwh": "nuclear",
    "gas_mwh": "gas",
    "others_mwh": "others",
}

DATE_COLUMN = "date"

# Emission factors (kg CO2e / kWh generated).
# Derived from CO2_Database_V_21.0.xlsx ("Data" sheet, 2024-25 columns),
# generation-weighted per fuel type — see scripts/derive_emission_factors.py
# for the full method and to reproduce/verify these numbers.
#   coal:   weighted avg of FUEL 1 == COAL        (2,407,043 GWh)
#   gas:    weighted avg of FUEL 1 == GAS          (57,748 GWh)
#   others: weighted avg of LIGN/OIL/NAPT/DISL     (61,675 GWh combined)
#   hydro/nuclear: confirmed 0 in every row of the database
#   wind/solar: not present in this database (thermal/hydro/nuclear
#     only) — standard zero-direct-emissions assumption applied
EMISSION_FACTORS = {
    "coal": 0.9691,
    "gas": 0.4523,
    "hydro": 0.0,
    "wind": 0.0,
    "solar": 0.0,
    "nuclear": 0.0,
    "others": 1.2763,
}

# Tolerance for the CSEP sanity check (fraction, e.g. 0.05 = 5%)
SANITY_TOLERANCE = 0.05


# ----------------------------------------------------------------------
# Step 1: Load
# ----------------------------------------------------------------------
def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df[DATE_COLUMN] = pd.to_datetime(df[DATE_COLUMN])
    return df


# ----------------------------------------------------------------------
# Step 2: Duplicate dates
# ----------------------------------------------------------------------
def remove_duplicates(df: pd.DataFrame, region: str, out_dir: Path) -> pd.DataFrame:
    dupe_mask = df.duplicated(subset=DATE_COLUMN, keep="first")
    n_dupes = dupe_mask.sum()

    if n_dupes:
        dupes = df[df.duplicated(subset=DATE_COLUMN, keep=False)]
        dupes.to_csv(out_dir / f"{region}_duplicates_removed.csv", index=False)
        print(f"[{region}] Found {n_dupes} duplicate date rows. "
              f"Kept first occurrence, logged all copies to "
              f"{region}_duplicates_removed.csv for manual review.")
        df = df[~dupe_mask]

    return df.sort_values(DATE_COLUMN).reset_index(drop=True)


# ----------------------------------------------------------------------
# Step 3: Fill / flag gaps
# ----------------------------------------------------------------------
def fill_gaps(df: pd.DataFrame, region: str) -> pd.DataFrame:
    full_range = pd.date_range(df[DATE_COLUMN].min(), df[DATE_COLUMN].max(), freq="D")
    df = df.set_index(DATE_COLUMN).reindex(full_range)
    df.index.name = DATE_COLUMN

    df["was_filled"] = df[list(GENERATION_COLUMNS.keys())[0]].isna()
    n_missing = df["was_filled"].sum()

    if n_missing:
        print(f"[{region}] {n_missing} missing dates found in range "
              f"{full_range.min().date()} to {full_range.max().date()}. "
              f"Filling via linear interpolation and flagging in 'was_filled'.")

    numeric_cols = list(GENERATION_COLUMNS.keys())
    df[numeric_cols] = df[numeric_cols].interpolate(method="linear", limit_direction="both")

    return df.reset_index()


# ----------------------------------------------------------------------
# Step 4: Sanity-check totals against CSEP reference
# ----------------------------------------------------------------------
def sanity_check(df: pd.DataFrame, region: str, csep_path: Path, out_dir: Path) -> pd.DataFrame:
    df["total_generation"] = df[list(GENERATION_COLUMNS.keys())].sum(axis=1)

    if not csep_path.exists():
        print(f"[{region}] No CSEP reference file found at {csep_path} — "
              f"skipping sanity check. (Expected columns: date,total_generation)")
        return df

    ref = pd.read_csv(csep_path)
    ref[DATE_COLUMN] = pd.to_datetime(ref[DATE_COLUMN])
    ref = ref.rename(columns={"total_generation": "csep_total_generation"})

    merged = df.merge(ref[[DATE_COLUMN, "csep_total_generation"]], on=DATE_COLUMN, how="left")
    merged["pct_deviation"] = (
        (merged["total_generation"] - merged["csep_total_generation"]).abs()
        / merged["csep_total_generation"]
    )

    flagged = merged[merged["pct_deviation"] > SANITY_TOLERANCE]
    if len(flagged):
        flagged.to_csv(out_dir / f"{region}_sanity_flags.csv", index=False)
        print(f"[{region}] {len(flagged)} days deviate more than "
              f"{SANITY_TOLERANCE:.0%} from CSEP reference. "
              f"Logged to {region}_sanity_flags.csv for review.")
    else:
        print(f"[{region}] All days within {SANITY_TOLERANCE:.0%} of CSEP reference. Good.")

    return merged.drop(columns=["csep_total_generation", "pct_deviation"], errors="ignore")


# ----------------------------------------------------------------------
# Step 5: Compute CI
# ----------------------------------------------------------------------
def compute_ci(df: pd.DataFrame) -> pd.DataFrame:
    numerator = pd.Series(0.0, index=df.index)
    for raw_col, clean_name in GENERATION_COLUMNS.items():
        ef = EMISSION_FACTORS.get(clean_name, 0.0)
        numerator += df[raw_col] * ef

    df["ci_gco2e_per_kwh"] = (numerator / df["total_generation"]) * 1000  # kg->g per kWh
    return df


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True, help="e.g. NR, WR")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--csep-ref", type=Path, default=Path("data/reference/csep_totals.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    df = load_data(args.input)
    df = remove_duplicates(df, args.region, args.out_dir)
    df = fill_gaps(df, args.region)
    df = sanity_check(df, args.region, args.csep_ref, args.out_dir)
    df = compute_ci(df)

    out_path = args.out_dir / f"{args.region.lower()}_cleaned_ci.csv"
    df.to_csv(out_path, index=False)
    print(f"[{args.region}] Done. Wrote cleaned + CI data to {out_path} "
          f"({len(df)} rows).")


if __name__ == "__main__":
    sys.exit(main())