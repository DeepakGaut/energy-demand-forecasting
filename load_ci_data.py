"""Load merged carbon-intensity data into ``ci_timeseries``.

The input CSV must contain the columns produced by
``scripts/clean_and_compute_ci.py`` for all regions.  Duplicate
``(region, timestamp)`` entries update the existing database row, which makes
this loader safe to run again after the source data is refreshed.

Usage:
    python load_ci_data.py
    python load_ci_data.py --input data/processed/merged_ci.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from backend.db.database import engine
from backend.db.models import CITimeseries


DEFAULT_INPUT = Path("data/processed/merged_ci.csv")
FUEL_COLUMNS = ("coal", "hydro", "wind", "solar", "nuclear", "gas")
REQUIRED_COLUMNS = {
    "date",
    "region",
    "ci_gco2e_per_kwh",
    "total_generation",
    *(f"{fuel}_mwh" for fuel in FUEL_COLUMNS),
}


def read_ci_csv(path: Path) -> pd.DataFrame:
    """Read and validate the merged CI CSV, deriving generation percentages."""
    if not path.is_file():
        raise FileNotFoundError(f"Merged CI CSV not found: {path}")

    frame = pd.read_csv(path)
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(sorted(missing))}")

    frame["timestamp"] = pd.to_datetime(frame["date"], errors="raise")
    frame["region"] = frame["region"].astype(str).str.strip().str.upper()

    if frame["region"].eq("").any():
        raise ValueError("CSV contains an empty region value")
    if frame["total_generation"].le(0).any():
        raise ValueError("total_generation must be greater than zero")
    if frame.duplicated(["region", "timestamp"]).any():
        raise ValueError("CSV contains duplicate region/date records")

    for fuel in FUEL_COLUMNS:
        frame[f"{fuel}_pct"] = frame[f"{fuel}_mwh"] / frame["total_generation"] * 100

    return frame


def load_ci_data(path: Path) -> int:
    """Upsert all records from *path* and return the number of source rows."""
    frame = read_ci_csv(path)
    columns = [
        "region",
        "timestamp",
        "ci_gco2e_per_kwh",
        *(f"{fuel}_pct" for fuel in FUEL_COLUMNS),
    ]
    records = frame[columns].where(pd.notna(frame[columns]), None).to_dict("records")

    statement = insert(CITimeseries.__table__).values(records)
    update_columns = {
        column: getattr(statement.excluded, column)
        for column in columns
        if column not in {"region", "timestamp"}
    }
    statement = statement.on_conflict_do_update(
        constraint="uq_region_timestamp",
        set_=update_columns,
    )

    with engine.begin() as connection:
        connection.execute(statement)

    return len(records)


def verify_row_counts(path: Path) -> dict[str, int]:
    """Check that the database contains the expected row count for each region."""
    expected = read_ci_csv(path).groupby("region").size().to_dict()
    query = select(CITimeseries.region, func.count()).group_by(CITimeseries.region)

    with engine.connect() as connection:
        actual = dict(connection.execute(query).all())

    if actual != expected:
        raise RuntimeError(
            "Row-count verification failed. "
            f"Expected {expected}; ci_timeseries contains {actual}."
        )

    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Merged CI CSV path")
    args = parser.parse_args()

    inserted = load_ci_data(args.input)
    row_counts = verify_row_counts(args.input)
    print(f"Loaded {inserted} CI records from {args.input}.")
    for region, count in sorted(row_counts.items()):
        print(f"{region}: source={count}, database={count}")


if __name__ == "__main__":
    main()
