"""
forecasting/features.py

Build one clean, model-ready DataFrame per region from the ``ci_timeseries``
table, for use by the ARIMA and Prophet baselines (Phase 3) and later the LSTM
(Phase 4).

Data-granularity note
---------------------
The ``ci_timeseries`` data is **daily** (one row per region per calendar day,
all timestamps at 00:00 UTC), not hourly. The project plan mentions "past-24h CI
values" and "hour of day"; at daily resolution those map to:

  * "past-24h CI value" -> the previous day's carbon intensity (a lag-1 feature,
    emitted as ``ci_lag_1``).
  * "hour of day"        -> constant (always 0) and therefore useless as a
    feature, so it is intentionally **omitted**.

The remaining plan features (day of week, month, generation-mix percentages) are
all produced. The schema carries a richer six-fuel mix than the plan's
"hydro/wind/solar/other", so all six percentages are kept and a residual
``other_pct`` is derived so the mix is complete.

Output contract (per region)
---------------------------
A pandas ``DataFrame`` indexed by a daily ``DatetimeIndex`` named ``date``,
sorted ascending, reindexed to a gap-free daily frequency, with columns:

    y                 target: carbon intensity (gCO2e/kWh)
    ci_lag_1          previous day's carbon intensity ("past 24h")
    dayofweek         0=Monday ... 6=Sunday
    month             1..12
    coal_pct          generation-mix percentages ...
    hydro_pct
    wind_pct
    solar_pct
    nuclear_pct
    gas_pct
    other_pct         residual = 100 - sum(six fuel percentages), clipped >= 0

The first row (which has no previous day for ``ci_lag_1``) is dropped so the
frame is immediately usable by models without further cleaning.

Both baseline libraries can consume this directly:
  * ARIMA (statsmodels) uses the daily-frequency ``y`` series (``df["y"]``).
  * Prophet expects columns ``ds`` and ``y``; use :func:`to_prophet`.

Run directly to build every region and print a summary:

    python -m forecasting.features
    python -m forecasting.features --save            # write CSVs
    python -m forecasting.features --region WR        # single region
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.database import SessionLocal
from backend.db.models import CITimeseries

# The five Indian regional grids present in ci_timeseries.
REGIONS: tuple[str, ...] = ("NR", "SR", "ER", "WR", "NER")

# Held-out test horizon: the final ~2 months of each region's daily series.
# Shared by the baseline trainers and the Phase 3 Day 4 evaluation harness so
# the train/test boundary is identical everywhere and models never see the test
# window during fitting.
TEST_HORIZON_DAYS: int = 60

# Six measured fuel-mix percentage columns carried by ci_timeseries.
_FUEL_PCT_COLUMNS: tuple[str, ...] = (
    "coal_pct",
    "hydro_pct",
    "wind_pct",
    "solar_pct",
    "nuclear_pct",
    "gas_pct",
)

# Where CSVs are written when --save is passed.
_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed" / "features"


def _load_raw_region(session: Session, region: str) -> pd.DataFrame:
    """Read the raw rows for one region into a DataFrame, oldest first."""
    stmt = (
        select(
            CITimeseries.timestamp,
            CITimeseries.ci_gco2e_per_kwh,
            CITimeseries.coal_pct,
            CITimeseries.hydro_pct,
            CITimeseries.wind_pct,
            CITimeseries.solar_pct,
            CITimeseries.nuclear_pct,
            CITimeseries.gas_pct,
        )
        .where(CITimeseries.region == region)
        .order_by(CITimeseries.timestamp.asc())
    )
    rows = session.execute(stmt).all()
    frame = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "ci_gco2e_per_kwh",
            *_FUEL_PCT_COLUMNS,
        ],
    )
    return frame


def build_region_frame(region: str, session: Session | None = None) -> pd.DataFrame:
    """Return the clean, model-ready feature DataFrame for a single region.

    Parameters
    ----------
    region:
        One of :data:`REGIONS` (case-insensitive).
    session:
        Optional SQLAlchemy session to reuse. If ``None`` a short-lived session
        is opened and closed internally.
    """
    region = region.upper()
    if region not in REGIONS:
        raise ValueError(f"Unknown region {region!r}; expected one of {REGIONS}.")

    owns_session = session is None
    session = session or SessionLocal()
    try:
        raw = _load_raw_region(session, region)
    finally:
        if owns_session:
            session.close()

    if raw.empty:
        raise ValueError(f"No ci_timeseries rows found for region {region!r}.")

    # Normalise the timestamp to a tz-naive daily DatetimeIndex named "date".
    dates = pd.to_datetime(raw["timestamp"], utc=True).dt.tz_localize(None).dt.normalize()
    raw = raw.drop(columns=["timestamp"])
    raw.index = pd.DatetimeIndex(dates, name="date")
    raw = raw.sort_index()

    # Reindex onto a gap-free daily calendar so lags and model frequencies are
    # well defined. Any genuinely missing day becomes NaN and is forward-filled
    # (carrying the last known value), which is the standard approach for short
    # gaps in a daily grid series.
    full_range = pd.date_range(raw.index.min(), raw.index.max(), freq="D", name="date")
    n_missing = len(full_range) - len(raw.index)
    raw = raw.reindex(full_range)
    if n_missing:
        raw = raw.ffill()

    df = pd.DataFrame(index=raw.index)
    df["y"] = raw["ci_gco2e_per_kwh"].astype(float)

    # "Past 24h" carbon intensity at daily resolution = previous day's value.
    df["ci_lag_1"] = df["y"].shift(1)

    # Calendar features (hour of day omitted: constant at daily resolution).
    df["dayofweek"] = df.index.dayofweek.astype(int)
    df["month"] = df.index.month.astype(int)

    # Generation-mix percentages, plus a residual "other" bucket so the mix is
    # complete (the six measured fuels typically sum to ~99%).
    for col in _FUEL_PCT_COLUMNS:
        df[col] = raw[col].astype(float)
    df["other_pct"] = (100.0 - df[list(_FUEL_PCT_COLUMNS)].sum(axis=1)).clip(lower=0.0)

    # Drop the first row: it has no previous day, so ci_lag_1 is NaN.
    df = df.dropna(subset=["ci_lag_1"])

    return df


def build_all_regions(session: Session | None = None) -> dict[str, pd.DataFrame]:
    """Return a ``{region: DataFrame}`` mapping for every region in :data:`REGIONS`."""
    owns_session = session is None
    session = session or SessionLocal()
    try:
        return {region: build_region_frame(region, session=session) for region in REGIONS}
    finally:
        if owns_session:
            session.close()


def train_test_split_frame(
    df: pd.DataFrame, horizon_days: int = TEST_HORIZON_DAYS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a region frame into (train, test) by holding out the final days.

    The last ``horizon_days`` rows become the test set; everything before is the
    training set. Using a fixed trailing window keeps the split deterministic and
    identical across every model and the evaluation harness.
    """
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive.")
    if horizon_days >= len(df):
        raise ValueError(
            f"horizon_days ({horizon_days}) must be smaller than the series "
            f"length ({len(df)})."
        )
    train = df.iloc[:-horizon_days]
    test = df.iloc[-horizon_days:]
    return train, test


def make_sequences(
    df: pd.DataFrame,
    feature_cols: list[str],
    target_col: str = "y",
    seq_len: int = 30,
) -> tuple[np.ndarray, np.ndarray]:
    """Build sliding-window sequences for a sequence model (e.g. an LSTM).

    For each position ``t`` from ``seq_len`` onward, the input is the block of
    ``feature_cols`` over days ``[t - seq_len, t - 1]`` and the target is
    ``target_col`` on day ``t``. All inputs therefore strictly precede the
    target day, so there is no look-ahead leakage.

    Returns
    -------
    X : np.ndarray, shape (n_samples, seq_len, n_features)
    y : np.ndarray, shape (n_samples,)
    """
    if seq_len <= 0:
        raise ValueError("seq_len must be positive.")
    if seq_len >= len(df):
        raise ValueError(
            f"seq_len ({seq_len}) must be smaller than the frame length ({len(df)})."
        )
    feats = df[feature_cols].to_numpy(dtype=float)
    target = df[target_col].to_numpy(dtype=float)
    windows = [feats[i - seq_len : i] for i in range(seq_len, len(df))]
    targets = target[seq_len:]
    return np.asarray(windows, dtype=float), np.asarray(targets, dtype=float)


def to_prophet(df: pd.DataFrame) -> pd.DataFrame:
    """Convert a region frame to Prophet's expected ``ds`` / ``y`` layout.

    Extra feature columns are preserved so they can be registered as Prophet
    extra regressors if desired.
    """
    out = df.reset_index().rename(columns={"date": "ds"})
    return out


def _save_frames(frames: dict[str, pd.DataFrame], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for region, frame in frames.items():
        path = output_dir / f"{region}.csv"
        frame.to_csv(path, index=True)
        print(f"  wrote {path}  ({len(frame)} rows)")


def _summarise(frames: dict[str, pd.DataFrame]) -> None:
    for region, frame in frames.items():
        start = frame.index.min().date()
        end = frame.index.max().date()
        print(
            f"{region:>3}: {len(frame):>5} rows  {start} -> {end}  "
            f"cols={list(frame.columns)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build per-region forecasting features.")
    parser.add_argument(
        "--region",
        help="Build only this region (default: all). Case-insensitive.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Write each region's frame to data/processed/features/{region}.csv.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="Directory for --save output (default: data/processed/features).",
    )
    args = parser.parse_args()

    if args.region:
        frames = {args.region.upper(): build_region_frame(args.region)}
    else:
        frames = build_all_regions()

    _summarise(frames)

    # Show a quick preview of the first region so the shape is visible.
    first_region = next(iter(frames))
    print(f"\nHead of {first_region}:")
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(frames[first_region].head())

    if args.save:
        print("\nSaving:")
        _save_frames(frames, args.output_dir)


if __name__ == "__main__":
    main()
