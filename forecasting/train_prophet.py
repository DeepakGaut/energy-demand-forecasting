"""
forecasting/train_prophet.py

Train a Prophet baseline on the historical daily carbon-intensity series for each
region and save the fitted model to ``models/prophet/{region}.pkl``.

Like the ARIMA trainer, Prophet is fit on the training split only (the final
:data:`forecasting.features.TEST_HORIZON_DAYS` days are held out via the shared
split), keeping later evaluation leakage-free.

Seasonality
-----------
Weekly and yearly seasonality are enabled so Prophet models the two cycles the
daily series can actually express. Daily (intraday) seasonality is disabled: the
data is one point per day, so there is no sub-daily signal to fit (same reason
``features.py`` omits an hour-of-day feature).

Models are pickled with the standard library ``pickle`` module, which Prophet
supports for a fitted model.

Usage:
    python -m forecasting.train_prophet
    python -m forecasting.train_prophet --region WR
"""

from __future__ import annotations

import argparse
import logging
import pickle
from pathlib import Path

from prophet import Prophet

from forecasting.features import (
    REGIONS,
    TEST_HORIZON_DAYS,
    build_region_frame,
    to_prophet,
    train_test_split_frame,
)

# Quieten Prophet/cmdstanpy's very chatty INFO logging during fitting.
logging.getLogger("prophet").setLevel(logging.WARNING)
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "prophet"


def train_region(region: str, output_dir: Path = _MODEL_DIR) -> dict:
    """Train and persist the Prophet model for one region; return a summary dict."""
    frame = build_region_frame(region)
    train, test = train_test_split_frame(frame)
    prophet_train = to_prophet(train)[["ds", "y"]]

    model = Prophet(
        weekly_seasonality=True,
        yearly_seasonality=True,
        daily_seasonality=False,
    )
    model.fit(prophet_train)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"{region}.pkl"
    with open(model_path, "wb") as handle:
        pickle.dump(model, handle)

    return {
        "region": region,
        "train_rows": len(train),
        "test_rows": len(test),
        "path": model_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Prophet baselines per region.")
    parser.add_argument("--region", help="Train only this region (default: all).")
    args = parser.parse_args()

    regions = [args.region.upper()] if args.region else list(REGIONS)

    print(f"Training Prophet (held-out test = {TEST_HORIZON_DAYS} days)\n")
    for region in regions:
        summary = train_region(region)
        print(
            f"{summary['region']:>3}: "
            f"train={summary['train_rows']} test={summary['test_rows']}  "
            f"-> {summary['path']}"
        )


if __name__ == "__main__":
    main()
