"""
forecasting/train_lstm.py

Train a per-region LSTM (PyTorch) on the daily carbon-intensity series produced
by :mod:`forecasting.features`, and log every run's hyperparameters and metrics
to a local MLflow tracking store (``mlruns/`` at the repo root).

What the model can and cannot learn
-----------------------------------
The underlying data is **one point per day**. An LSTM over a 30-day window can
therefore capture:

* **weekly demand patterns** (7-day cycle within each window), and
* **monsoon / seasonal hydro variation** (slow shifts across windows).

It **cannot** capture intraday "daily solar cycles": there is no sub-daily
signal in daily data (the same reason ``features.py`` omits an hour-of-day
feature and Prophet disables daily seasonality). The ``month`` / ``dayofweek``
features give the network explicit calendar context for the seasonal/weekly
structure.

Leakage safety
--------------
* The final :data:`forecasting.features.TEST_HORIZON_DAYS` days are held out via
  the shared split and are **never** seen here (reserved for Phase 4 Day 4 eval).
* Within the training portion, the last ``VAL_HORIZON_DAYS`` days form a
  chronological validation window. It is scored by a **recursive multi-step
  forecast** (the model feeds back its own predictions; no real future value is
  consumed), and that multi-step RMSE drives both early stopping and config
  selection -- the same objective Day 4 reports.
* Feature/target standardisation statistics are fit on the training sequences
  only (excluding the validation tail), so validation metrics are leakage-free.
* The generation-mix features are excluded from the input entirely (see
  ``FEATURE_COLS``); only CI history and calendar are used, both of which are
  genuinely knowable at forecast time.

Each region's artifact is saved to ``models/lstm/{region}.pt`` as a dict holding
the ``state_dict``, the scaler parameters, the feature list, and the config,
so it can be reloaded for inference without re-deriving anything.

Usage:
    python -m forecasting.train_lstm
    python -m forecasting.train_lstm --region WR
    python -m forecasting.train_lstm --region NER --epochs 120 --hidden-size 64
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path

import mlflow
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from forecasting.features import (
    REGIONS,
    TEST_HORIZON_DAYS,
    build_region_frame,
    make_sequences,
    train_test_split_frame,
)

# Feature columns fed to the LSTM at each timestep.
#
# Only variables that are genuinely knowable at forecast time are used:
#   * ``y``          -- CI history; fed back recursively during multi-step rollout
#   * ``dayofweek``  -- deterministic for any future date (weekly pattern)
#   * ``month``      -- deterministic for any future date (seasonal/monsoon pattern)
#
# The generation-mix percentages (hydro/wind/solar/coal/nuclear/gas/other) are
# deliberately EXCLUDED: a real deployment cannot know a future day's actual fuel
# mix in advance, so feeding real future mix into a 60-day recursive forecast
# would be data leakage. Dropping them also matches the information set of the
# ARIMA/Prophet baselines (CI history + calendar), making the comparison fair.
# ``ci_lag_1`` is likewise dropped -- it is just ``y`` shifted, already carried
# by the sequence window. The 6-fuel DB schema and ``features.py`` are unchanged.
FEATURE_COLS = [
    "y",
    "dayofweek",
    "month",
]

# Days of the training portion reserved for validation / early stopping.
VAL_HORIZON_DAYS = 60

_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "lstm"
_MLFLOW_DB = Path(__file__).resolve().parent.parent / "mlflow.db"
_EXPERIMENT = "ecocompute-lstm"


def setup_mlflow(experiment: str = _EXPERIMENT) -> None:
    """Point MLflow at the repo-local SQLite backend and select the experiment."""
    _MLFLOW_DB.parent.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{_MLFLOW_DB.as_posix()}")
    mlflow.set_experiment(experiment)


@dataclass
class LSTMConfig:
    """Hyperparameters for one training run."""

    seq_len: int = 30
    hidden_size: int = 32
    num_layers: int = 1
    dropout: float = 0.0
    lr: float = 1e-3
    epochs: int = 100
    batch_size: int = 32
    patience: int = 12
    seed: int = 42


class LSTMForecaster(nn.Module):
    """A small LSTM followed by a linear head predicting the next-day CI."""

    def __init__(self, n_features: int, hidden_size: int, num_layers: int, dropout: float):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=n_features,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Linear(hidden_size, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # noqa: D102
        out, _ = self.lstm(x)
        last = out[:, -1, :]  # final timestep's hidden state
        return self.head(last).squeeze(-1)


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    err = y_pred - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err**2)))
    mape = float(np.mean(np.abs(err / y_true)) * 100.0)
    return {"MAE": mae, "RMSE": rmse, "MAPE": mape}


def _feature_row(date, y_value: float, feature_cols: list[str]) -> np.ndarray:
    """Build one raw feature row for a future ``date`` given a predicted ``y``.

    Every non-``y`` feature must be deterministically derivable from the calendar
    date (that is the whole point of the reduced feature set); anything else would
    not be knowable at forecast time.
    """
    row = []
    for col in feature_cols:
        if col == "y":
            row.append(y_value)
        elif col == "dayofweek":
            row.append(float(date.dayofweek))
        elif col == "month":
            row.append(float(date.month))
        else:
            raise ValueError(
                f"Feature '{col}' is not knowable at forecast time; "
                "the recursive forecaster only supports y/dayofweek/month."
            )
    return np.asarray(row, dtype=float)


def recursive_forecast(
    model: nn.Module,
    history: "pd.DataFrame",
    future_index,
    feature_cols: list[str],
    seq_len: int,
    scaler: dict,
) -> np.ndarray:
    """Roll ``model`` forward ``len(future_index)`` days, feeding back its own CI.

    The seed window is the final ``seq_len`` rows of ``history`` (all real, known
    at forecast time). For each future day the model predicts CI; that prediction
    becomes the ``y`` input for the next step, while calendar features come from
    the future date itself. No actual future value is ever consumed, so the
    rollout is leakage-free and matches how the model would run in production.
    """
    f_mean = scaler["f_mean"]
    f_std = scaler["f_std"]
    t_mean = scaler["t_mean"]
    t_std = scaler["t_std"]

    seed_raw = history[feature_cols].to_numpy(dtype=float)[-seq_len:]
    window = (seed_raw - f_mean) / f_std  # scaled (seq_len, n_features)

    model.eval()
    preds: list[float] = []
    with torch.no_grad():
        for date in future_index:
            x = torch.tensor(window[None], dtype=torch.float32)
            yhat_scaled = float(model(x).item())
            yhat = yhat_scaled * t_std + t_mean
            preds.append(yhat)
            next_raw = _feature_row(date, yhat, feature_cols)
            next_scaled = (next_raw - f_mean) / f_std
            window = np.vstack([window[1:], next_scaled])
    return np.asarray(preds, dtype=float)



def train_region(
    region: str,
    config: LSTMConfig | None = None,
    output_dir: Path = _MODEL_DIR,
    log_mlflow: bool = True,
    run_name: str | None = None,
) -> dict:
    """Train and persist the LSTM for one region; return a summary dict."""
    config = config or LSTMConfig()
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    frame = build_region_frame(region)
    train_df, _test_df = train_test_split_frame(frame)  # test held out for eval

    X_all, y_all = make_sequences(train_df, FEATURE_COLS, seq_len=config.seq_len)
    n_val = VAL_HORIZON_DAYS
    # Training targets exclude the final ``n_val`` days; those days form the
    # chronological validation window, forecast recursively (multi-step) below.
    X_tr, y_tr = X_all[:-n_val], y_all[:-n_val]

    # Standardise using training sequences only (no validation leakage).
    n_features = X_tr.shape[2]
    f_mean = X_tr.reshape(-1, n_features).mean(axis=0)
    f_std = X_tr.reshape(-1, n_features).std(axis=0) + 1e-8
    t_mean = float(y_tr.mean())
    t_std = float(y_tr.std()) + 1e-8
    scaler = {"f_mean": f_mean, "f_std": f_std, "t_mean": t_mean, "t_std": t_std}

    def scale_x(a: np.ndarray) -> np.ndarray:
        return (a - f_mean) / f_std

    def scale_y(a: np.ndarray) -> np.ndarray:
        return (a - t_mean) / t_std

    X_tr_s = torch.tensor(scale_x(X_tr), dtype=torch.float32)
    y_tr_s = torch.tensor(scale_y(y_tr), dtype=torch.float32)

    # Multi-step validation setup: forecast the last ``n_val`` days recursively
    # from history that ends just before them (matches the Day-4 rollout).
    val_history = train_df.iloc[:-n_val]
    val_index = train_df.index[-n_val:]
    y_val = train_df["y"].to_numpy(dtype=float)[-n_val:]

    loader = DataLoader(
        TensorDataset(X_tr_s, y_tr_s),
        batch_size=config.batch_size,
        shuffle=True,
    )

    model = LSTMForecaster(
        n_features=n_features,
        hidden_size=config.hidden_size,
        num_layers=config.num_layers,
        dropout=config.dropout,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=config.lr)
    loss_fn = nn.MSELoss()

    best_val = float("inf")
    best_state: dict | None = None
    best_epoch = 0
    epochs_no_improve = 0

    for epoch in range(1, config.epochs + 1):
        model.train()
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            optimizer.step()

        # Selection criterion: recursive multi-step validation RMSE (original
        # scale) -- the same objective Day 4 reports, so training, early stopping,
        # and config selection all optimise the metric that actually matters.
        val_pred = recursive_forecast(
            model, val_history, val_index, FEATURE_COLS, config.seq_len, scaler
        )
        val_rmse = float(np.sqrt(np.mean((val_pred - y_val) ** 2)))

        if val_rmse < best_val - 1e-6:
            best_val = val_rmse
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= config.patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    # Final validation metrics from the recursive multi-step forecast.
    val_pred = recursive_forecast(
        model, val_history, val_index, FEATURE_COLS, config.seq_len, scaler
    )
    metrics = _metrics(y_val, val_pred)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"{region}.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "config": asdict(config),
            "feature_cols": FEATURE_COLS,
            "scaler": {
                "f_mean": f_mean,
                "f_std": f_std,
                "t_mean": t_mean,
                "t_std": t_std,
            },
        },
        model_path,
    )

    summary = {
        "region": region,
        "train_seqs": int(len(X_tr)),
        "val_days": int(n_val),
        "best_epoch": best_epoch,
        "val_loss": best_val,
        "path": model_path,
        **metrics,
    }

    if log_mlflow:
        with mlflow.start_run(run_name=run_name or region):
            mlflow.log_params(asdict(config))
            mlflow.log_param("region", region)
            mlflow.log_param("n_features", n_features)
            mlflow.log_param("feature_cols", ",".join(FEATURE_COLS))
            mlflow.log_param("val_type", "recursive_multistep")
            mlflow.log_metric("best_epoch", best_epoch)
            mlflow.log_metric("val_multistep_RMSE", best_val)
            mlflow.log_metric("val_multistep_MAE", metrics["MAE"])
            mlflow.log_metric("val_multistep_MAPE", metrics["MAPE"])
            mlflow.log_artifact(str(model_path))

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Train per-region LSTM forecasters.")
    parser.add_argument("--region", help="Train only this region (default: all).")
    parser.add_argument("--seq-len", type=int, default=LSTMConfig.seq_len)
    parser.add_argument("--hidden-size", type=int, default=LSTMConfig.hidden_size)
    parser.add_argument("--num-layers", type=int, default=LSTMConfig.num_layers)
    parser.add_argument("--dropout", type=float, default=LSTMConfig.dropout)
    parser.add_argument("--lr", type=float, default=LSTMConfig.lr)
    parser.add_argument("--epochs", type=int, default=LSTMConfig.epochs)
    parser.add_argument("--batch-size", type=int, default=LSTMConfig.batch_size)
    parser.add_argument("--patience", type=int, default=LSTMConfig.patience)
    parser.add_argument("--seed", type=int, default=LSTMConfig.seed)
    args = parser.parse_args()

    config = LSTMConfig(
        seq_len=args.seq_len,
        hidden_size=args.hidden_size,
        num_layers=args.num_layers,
        dropout=args.dropout,
        lr=args.lr,
        epochs=args.epochs,
        batch_size=args.batch_size,
        patience=args.patience,
        seed=args.seed,
    )

    setup_mlflow()

    regions = [args.region.upper()] if args.region else list(REGIONS)

    print(
        f"Training LSTM (held-out test = {TEST_HORIZON_DAYS} days, "
        f"validation = {VAL_HORIZON_DAYS} days)\n"
        f"MLflow tracking: {_MLFLOW_DB}  experiment: {_EXPERIMENT}\n"
    )
    for region in regions:
        summary = train_region(region, config)
        print(
            f"{summary['region']:>3}: "
            f"train_seqs={summary['train_seqs']} val_days={summary['val_days']}  "
            f"best_epoch={summary['best_epoch']:>3}  "
            f"multi-step val MAE={summary['MAE']:.3f} RMSE={summary['RMSE']:.3f} "
            f"MAPE={summary['MAPE']:.2f}%  -> {summary['path']}"
        )


if __name__ == "__main__":
    main()
