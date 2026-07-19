"""
forecasting/train_arima.py

Train an ARIMA baseline (statsmodels) on the historical daily carbon-intensity
series for each region and save the fitted model to
``models/arima/{region}.pkl``.

The model is fit on the training split only (the final
:data:`forecasting.features.TEST_HORIZON_DAYS` days are held out, matching the
shared split used by the Phase 3 Day 4 evaluation harness), so later evaluation
on the test window is leakage-free.

Order selection
---------------
A small (p, d, q) grid is searched and the order with the lowest AIC that fits
successfully is kept. This is the "tune if the default fit is poor" step from the
plan, done automatically rather than by hand. The grid is intentionally modest so
training all five regions stays fast; widen ``P_RANGE`` / ``Q_RANGE`` below if a
region needs it.

Note: this is a non-seasonal ARIMA per the plan. Weekly/yearly seasonality is
left to the Prophet baseline, which models it natively.

Usage:
    python -m forecasting.train_arima
    python -m forecasting.train_arima --region WR
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
from statsmodels.tsa.arima.model import ARIMA

from forecasting.features import (
    REGIONS,
    TEST_HORIZON_DAYS,
    build_region_frame,
    train_test_split_frame,
)

# Default candidate-order bounds for the AIC grid search. p and q upper bounds
# are configurable (see --max-p / --max-q) so a region whose optimum presses
# against the wall can be re-searched with a wider grid; d stays in {0, 1}.
DEFAULT_MAX_P = 3
DEFAULT_MAX_Q = 3
D_RANGE = range(0, 2)

_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "arima"


def _select_order(
    y: np.ndarray, max_p: int = DEFAULT_MAX_P, max_q: int = DEFAULT_MAX_Q
) -> tuple[tuple[int, int, int], float]:
    """Return the (p, d, q) order with the lowest AIC over the candidate grid."""
    best_order: tuple[int, int, int] | None = None
    best_aic = np.inf
    # ARIMA fitting emits convergence / frequency warnings for some orders that
    # are expected during a grid search; silence them to keep output readable.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for p in range(0, max_p + 1):
            for d in D_RANGE:
                for q in range(0, max_q + 1):
                    if p == 0 and q == 0:
                        continue
                    try:
                        result = ARIMA(y, order=(p, d, q)).fit()
                    except Exception:  # noqa: BLE001 - unstable orders are skipped
                        continue
                    if np.isfinite(result.aic) and result.aic < best_aic:
                        best_aic = result.aic
                        best_order = (p, d, q)
    if best_order is None:
        raise RuntimeError("No ARIMA order in the grid produced a valid fit.")
    return best_order, best_aic


def train_region(
    region: str,
    output_dir: Path = _MODEL_DIR,
    max_p: int = DEFAULT_MAX_P,
    max_q: int = DEFAULT_MAX_Q,
) -> dict:
    """Train and persist the ARIMA model for one region; return a summary dict."""
    frame = build_region_frame(region)
    train, test = train_test_split_frame(frame)
    y_train = train["y"].to_numpy(dtype=float)

    order, aic = _select_order(y_train, max_p=max_p, max_q=max_q)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = ARIMA(y_train, order=order).fit()

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"{region}.pkl"
    result.save(str(model_path))

    # Flag if the chosen order sits on the p or q wall: that means the true AIC
    # optimum may lie outside the grid and the search should be widened.
    on_boundary = order[0] == max_p or order[2] == max_q

    return {
        "region": region,
        "order": order,
        "aic": round(float(aic), 2),
        "max_p": max_p,
        "max_q": max_q,
        "on_boundary": on_boundary,
        "train_rows": len(train),
        "test_rows": len(test),
        "path": model_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train ARIMA baselines per region.")
    parser.add_argument("--region", help="Train only this region (default: all).")
    parser.add_argument(
        "--max-p", type=int, default=DEFAULT_MAX_P, help="Upper bound for AR order p."
    )
    parser.add_argument(
        "--max-q", type=int, default=DEFAULT_MAX_Q, help="Upper bound for MA order q."
    )
    args = parser.parse_args()

    regions = [args.region.upper()] if args.region else list(REGIONS)

    print(
        f"Training ARIMA (held-out test = {TEST_HORIZON_DAYS} days, "
        f"grid p<={args.max_p} d<=1 q<={args.max_q})\n"
    )
    for region in regions:
        summary = train_region(region, max_p=args.max_p, max_q=args.max_q)
        boundary = "  <-- ON BOUNDARY" if summary["on_boundary"] else ""
        print(
            f"{summary['region']:>3}: order={summary['order']}  "
            f"AIC={summary['aic']:>10}  "
            f"train={summary['train_rows']} test={summary['test_rows']}  "
            f"-> {summary['path']}{boundary}"
        )


if __name__ == "__main__":
    main()
