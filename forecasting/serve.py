"""
forecasting/serve.py

Serving-time forecasting helper behind ``GET /forecast/{region}`` (Phase 4 Day 5).

Selects, per region, the model that won the Phase 4 evaluation and produces a
genuine out-of-sample forecast that begins the day *after* the last real
observation in ``ci_timeseries``.

Model selection
---------------
The Phase 4 eval (``data/processed/baseline_metrics.csv``) had ARIMA win every
region on both MAE and MAPE, so the winner is recorded here in a single
:data:`BEST_MODEL` constant. This is deliberately **not** a dynamic read of the
eval CSV: it keeps the serving path decoupled from a mutable dev artifact and
avoids pulling PyTorch / Prophet into the API process. If a future retrain flips
a region's winner, change the one line below (and register the model in
:data:`_FORECASTERS`).

Forecast origin
---------------
The saved ARIMA model was fit on the *training split only* (the final
:data:`forecasting.features.TEST_HORIZON_DAYS` days were held out for
evaluation). To forecast the real future we reload that model and extend it with
the held-out actual observations via ``ARIMAResults.append(refit=False)`` --
reusing the already-fitted parameters, without refitting -- so the forecast
origin advances to the last observed day in the series.

Wall-clock anchoring
--------------------
ARIMA (like any time-series model) can only forecast forward from the end of its
training data; it has no concept of "today". When ``from_today=True`` the
forecast is **date-remapped**: the model produces its usual out-of-sample steps
1..N (step 1 == the day after the last real observation), and their date labels
are shifted so step 1 aligns with today's wall-clock date. The predicted values
are unchanged -- only the dates move. ``last_observed_date`` is always returned
so callers can surface how far past real data the labels have been shifted.

Granularity note
----------------
``ci_timeseries`` is **daily**, so the plan's "next 24 hours" maps to the next
**one** day at daily resolution. ``horizon`` is therefore expressed in days.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session
from statsmodels.tsa.arima.model import ARIMAResults

from forecasting.features import REGIONS, build_region_frame, train_test_split_frame

_ARIMA_DIR = Path(__file__).resolve().parent.parent / "models" / "arima"

# Per-region winning model from the Phase 4 evaluation. ARIMA swept every region
# on MAE and MAPE, so all five resolve to ARIMA. One-line change per region if a
# retrain ever flips a winner.
BEST_MODEL: dict[str, str] = {
    "NR": "ARIMA",
    "SR": "ARIMA",
    "ER": "ARIMA",
    "WR": "ARIMA",
    "NER": "ARIMA",
}


def _forecast_arima(
    region: str, horizon: int, session: Session | None, from_today: bool
) -> tuple[pd.Timestamp, pd.DatetimeIndex, np.ndarray]:
    """Out-of-sample ARIMA forecast.

    By default the forecast starts the day after the last observation. When
    ``from_today`` is True the forecast is **date-remapped** to today's
    wall-clock date: the model still produces its usual out-of-sample steps 1..N
    (step 1 == the day after the last real observation), but their date labels
    are shifted so step 1 aligns with today. The predicted CI *values* are
    unchanged -- only the dates attached to them move -- under the assumption
    that the grid patterns learned through the last real day continue to hold.
    """
    model = ARIMAResults.load(str(_ARIMA_DIR / f"{region}.pkl"))
    frame = build_region_frame(region, session=session)
    # The saved model was fit on the training split only; extend it with the
    # real held-out observations (no refit) so the forecast origin is the last
    # real day in the series rather than the start of the held-out window.
    _, test = train_test_split_frame(frame)
    extended = model.append(test["y"].to_numpy(dtype=float), refit=False)
    last_date = frame.index[-1]
    values = np.asarray(extended.forecast(steps=horizon), dtype=float)

    if from_today:
        # Relabel step 1..N so day 1 == today's wall-clock date. Values unchanged.
        future_index = pd.date_range(pd.Timestamp(date.today()), periods=horizon, freq="D")
    else:
        # date_range from the last observed day gives horizon+1 points; drop the
        # first (the last observed day) to get the future days only.
        future_index = pd.date_range(last_date, periods=horizon + 1, freq="D")[1:]
    return last_date, future_index, values


# Registry mapping a model name to its serving forecaster. Add an entry here when
# BEST_MODEL starts pointing at a non-ARIMA model for some region.
_FORECASTERS = {
    "ARIMA": _forecast_arima,
}


def forecast_region(
    region: str, horizon: int = 1, session: Session | None = None,
    from_today: bool = False,
) -> tuple[str, date, list[date], list[float]]:
    """Forecast the next ``horizon`` days of CI for ``region``.

    When ``from_today`` is True the forecast window is anchored to today's
    wall-clock date (bridging any gap since the last real observation) instead of
    starting the day after the last observation.

    Returns ``(model_name, last_observed_date, forecast_dates, forecast_values)``
    using plain Python types so callers need no pandas/numpy dependency.
    ``last_observed_date`` is always the last *real* day in the series, letting
    callers surface the extrapolation gap.
    """
    region = region.upper()
    if region not in REGIONS:
        raise ValueError(f"Unknown region {region!r}; expected one of {REGIONS}.")
    if horizon <= 0:
        raise ValueError("horizon must be a positive number of days.")

    model_name = BEST_MODEL[region]
    forecaster = _FORECASTERS[model_name]
    last_date, future_index, values = forecaster(region, horizon, session, from_today)

    last_observed = last_date.date()
    forecast_dates = [ts.date() for ts in future_index]
    forecast_values = [float(v) for v in values]
    return model_name, last_observed, forecast_dates, forecast_values
