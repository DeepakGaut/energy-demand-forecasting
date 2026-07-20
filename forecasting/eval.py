"""
forecasting/eval.py

Shared evaluation harness for the forecasting baselines (Phase 3 Day 4).

Uses the same fixed train/test split as the trainers (the final
:data:`forecasting.features.TEST_HORIZON_DAYS` days held out) and scores each
saved model on that untouched test window with MAE, RMSE, and MAPE.

The design is model-agnostic: metrics operate on plain ``(y_true, y_pred)``
arrays, and each model is wrapped by a forecaster in :data:`FORECASTERS` that
returns a length-``horizon`` prediction. Adding a new model later (e.g. the
Phase 4 LSTM) means adding one entry to that registry -- no metric code changes.

Usage:
    python -m forecasting.eval
    python -m forecasting.eval --region WR
"""

from __future__ import annotations

import argparse
import pickle
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import torch
from statsmodels.tsa.arima.model import ARIMAResults

from forecasting.features import (
    REGIONS,
    TEST_HORIZON_DAYS,
    build_region_frame,
    train_test_split_frame,
)
from forecasting.train_lstm import LSTMForecaster, recursive_forecast

_ARIMA_DIR = Path(__file__).resolve().parent.parent / "models" / "arima"
_PROPHET_DIR = Path(__file__).resolve().parent.parent / "models" / "prophet"
_LSTM_DIR = Path(__file__).resolve().parent.parent / "models" / "lstm"

# ---------------------------------------------------------------------------
# Fairness caveat -- printed with every report so the comparison is never read
# as a clean apples-to-apples result without this context.
# ---------------------------------------------------------------------------
CAVEAT = (
    "CAVEAT -- model-capability asymmetry, not pure fit quality:\n"
    "  Prophet natively models yearly (and weekly) seasonality; the baseline\n"
    "  ARIMA is non-seasonal (per the plan). The ARIMA residuals show NO\n"
    "  significant weekly (lag-7) autocorrelation (Ljung-Box p > 0.95 for\n"
    "  NR/WR), so weekly seasonality is NOT a source of unfair advantage.\n"
    "  However, over the {horizon}-day test horizon a non-seasonal ARIMA\n"
    "  forecast reverts toward the series mean and cannot express yearly /\n"
    "  monsoon-driven structure that Prophet projects. If Prophet wins, part\n"
    "  of the gap may reflect this seasonality-SUPPORT difference rather than\n"
    "  intrinsically better modeling. A fully fair seasonal comparison would\n"
    "  use SARIMA; treat cross-model deltas with that in mind.\n"
    "  LSTM: uses only CI history + calendar (fuel-mix dropped to avoid\n"
    "  leakage), and forecasts the {horizon}-day window RECURSIVELY -- feeding\n"
    "  its own predictions back in, never any real future value. Its\n"
    "  information set therefore matches the baselines, so its errors compound\n"
    "  over the horizon exactly as a real deployment's would."
).format(horizon=TEST_HORIZON_DAYS)


# ---------------------------------------------------------------------------
# Metrics -- pure functions on (y_true, y_pred).
# ---------------------------------------------------------------------------
def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean absolute percentage error (%). CI values are strictly positive."""
    return float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100.0)


METRICS: dict[str, Callable[[np.ndarray, np.ndarray], float]] = {
    "MAE": mae,
    "RMSE": rmse,
    "MAPE": mape,
}


def score(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Return all metrics for one forecast. Works for any model's predictions."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return {name: fn(y_true, y_pred) for name, fn in METRICS.items()}


# ---------------------------------------------------------------------------
# Forecasters -- each returns a length-`horizon` prediction for the test window.
# ---------------------------------------------------------------------------
def forecast_arima(region: str, horizon: int) -> np.ndarray:
    result = ARIMAResults.load(str(_ARIMA_DIR / f"{region}.pkl"))
    return np.asarray(result.forecast(steps=horizon), dtype=float)


def forecast_prophet(region: str, horizon: int) -> np.ndarray:
    with open(_PROPHET_DIR / f"{region}.pkl", "rb") as handle:
        model = pickle.load(handle)
    future = model.make_future_dataframe(periods=horizon, freq="D")
    forecast = model.predict(future)
    return forecast["yhat"].to_numpy(dtype=float)[-horizon:]


def forecast_lstm(region: str, horizon: int) -> np.ndarray:
    """Recursive multi-step LSTM forecast over the held-out test window.

    Rebuilds the saved model, then rolls it forward ``horizon`` days from the
    training history, feeding back its own predictions (calendar features come
    from the future dates). No real future value is used, so this is the honest
    deployment-equivalent forecast -- errors compound across the horizon.
    """
    ckpt = torch.load(_LSTM_DIR / f"{region}.pt", weights_only=False)
    cfg = ckpt["config"]
    feature_cols = ckpt["feature_cols"]
    scaler = ckpt["scaler"]
    model = LSTMForecaster(
        n_features=len(feature_cols),
        hidden_size=cfg["hidden_size"],
        num_layers=cfg["num_layers"],
        dropout=cfg["dropout"],
    )
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    frame = build_region_frame(region)
    train, test = train_test_split_frame(frame, horizon_days=horizon)
    return recursive_forecast(model, train, test.index, feature_cols, cfg["seq_len"], scaler)


FORECASTERS: dict[str, Callable[[str, int], np.ndarray]] = {
    "ARIMA": forecast_arima,
    "Prophet": forecast_prophet,
    "LSTM": forecast_lstm,
}


def evaluate_region(region: str, horizon: int = TEST_HORIZON_DAYS) -> dict[str, dict[str, float]]:
    """Score every registered model for one region on the held-out test window."""
    frame = build_region_frame(region)
    _, test = train_test_split_frame(frame, horizon_days=horizon)
    y_true = test["y"].to_numpy(dtype=float)

    results: dict[str, dict[str, float]] = {}
    for model_name, forecaster in FORECASTERS.items():
        y_pred = forecaster(region, horizon)
        results[model_name] = score(y_true, y_pred)
    return results


def evaluate_all(regions: list[str], horizon: int = TEST_HORIZON_DAYS) -> dict[str, dict[str, dict[str, float]]]:
    return {region: evaluate_region(region, horizon) for region in regions}


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------
def _print_report(all_results: dict[str, dict[str, dict[str, float]]]) -> None:
    models = list(FORECASTERS)
    regions = list(all_results)

    print("=" * 72)
    print(f"Model evaluation -- held-out test = {TEST_HORIZON_DAYS} days (lower is better)")
    print("=" * 72)

    for metric in METRICS:
        header = f"{'Region':>7} | " + " | ".join(f"{m:>10}" for m in models) + " |  winner"
        print(f"\n{metric}:")
        print(header)
        print("-" * len(header))
        for region in regions:
            cells = []
            for m in models:
                cells.append(f"{all_results[region][m][metric]:>10.3f}")
            best_model = min(models, key=lambda m: all_results[region][m][metric])
            print(f"{region:>7} | " + " | ".join(cells) + f" |  {best_model}")
        # Mean across regions.
        means = []
        for m in models:
            means.append(np.mean([all_results[r][m][metric] for r in regions]))
        best_mean = models[int(np.argmin(means))]
        mean_cells = " | ".join(f"{v:>10.3f}" for v in means)
        print("-" * len(header))
        print(f"{'MEAN':>7} | " + mean_cells + f" |  {best_mean}")

    print("\n" + "=" * 72)
    print(CAVEAT)
    print("=" * 72)


def _difficulty_stats(
    region: str,
    all_results: dict[str, dict[str, dict[str, float]]],
    horizon: int = TEST_HORIZON_DAYS,
) -> dict[str, float | str]:
    """Data-driven signals for how hard a region is to forecast.

    - best_model / best_mape: the lower MAPE achieved by any baseline (a proxy
      for the region's achievable forecast accuracy).
    - test_cv: coefficient of variation of the test window (spread relative to
      level) -- higher means a more variable target.
    - daily_change: mean absolute day-over-day % change in the test window --
      the intrinsic day-to-day unpredictability a 1-step model must chase.
    - level_shift: mean(test) vs mean(train) as a % -- a large shift is what
      punishes trend-extrapolating models (e.g. Prophet) most.
    """
    frame = build_region_frame(region)
    train, test = train_test_split_frame(frame, horizon_days=horizon)
    y_train = train["y"].to_numpy(dtype=float)
    y_test = test["y"].to_numpy(dtype=float)

    best_model = min(FORECASTERS, key=lambda m: all_results[region][m]["MAPE"])
    return {
        "best_model": best_model,
        "best_mape": all_results[region][best_model]["MAPE"],
        "test_cv": float(np.std(y_test) / np.mean(y_test) * 100.0),
        "daily_change": float(np.mean(np.abs(np.diff(y_test) / y_test[:-1])) * 100.0),
        "level_shift": float((np.mean(y_test) - np.mean(y_train)) / np.mean(y_train) * 100.0),
    }


def _print_difficulty(
    all_results: dict[str, dict[str, dict[str, float]]], regions: list[str]
) -> None:
    """Rank regions hardest-first by best-baseline MAPE, with the 'why' signals."""
    stats = {r: _difficulty_stats(r, all_results) for r in regions}
    ranked = sorted(regions, key=lambda r: stats[r]["best_mape"], reverse=True)

    print("\n" + "=" * 72)
    print("FORECAST DIFFICULTY (hardest first) -- 'best MAPE' = lowest of any baseline")
    print("=" * 72)
    header = (
        f"{'Region':>7} | {'best MAPE':>9} | {'best model':>10} | "
        f"{'test CV%':>8} | {'daily d%':>8} | {'lvl shift%':>10}"
    )
    print(header)
    print("-" * len(header))
    for r in ranked:
        s = stats[r]
        print(
            f"{r:>7} | {s['best_mape']:>8.2f}% | {s['best_model']:>10} | "
            f"{s['test_cv']:>7.2f}% | {s['daily_change']:>7.2f}% | {s['level_shift']:>+9.2f}%"
        )
    hardest = ranked[0]
    biggest_shift = max(regions, key=lambda r: abs(stats[r]["level_shift"]))
    print("-" * len(header))
    print(
        f"Hardest: {hardest} (best MAPE {stats[hardest]['best_mape']:.2f}%). "
        f"Largest train->test level shift: {biggest_shift} "
        f"({stats[biggest_shift]['level_shift']:+.1f}%), which most penalises "
        f"trend-extrapolating models."
    )
    print("=" * 72)


def results_to_frame(
    all_results: dict[str, dict[str, dict[str, float]]]
) -> pd.DataFrame:
    """Flatten results into a tidy region x model x metric DataFrame."""
    rows = []
    for region, per_model in all_results.items():
        for model_name, metrics in per_model.items():
            rows.append({"region": region, "model": model_name, **metrics})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate forecasting baselines.")
    parser.add_argument("--region", help="Evaluate only this region (default: all).")
    parser.add_argument(
        "--save",
        nargs="?",
        const="data/processed/baseline_metrics.csv",
        help="Write the results table to CSV (default path if no value given).",
    )
    args = parser.parse_args()

    regions = [args.region.upper()] if args.region else list(REGIONS)
    all_results = evaluate_all(regions)
    _print_report(all_results)
    _print_difficulty(all_results, regions)

    if args.save:
        out_path = Path(args.save)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        results_to_frame(all_results).to_csv(out_path, index=False)
        print(f"\nSaved results table -> {out_path}")


if __name__ == "__main__":
    main()
