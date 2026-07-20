"""
forecasting/sweep_lstm.py

Phase 4 Day 3: hyperparameter sweep for the per-region LSTM forecasters.

Grid-searches three hyperparameters — **hidden size**, **sequence length**, and
**learning rate** — for each region, training one LSTM per combination and
logging every run's hyperparameters and validation metrics to MLflow
(experiment ``ecocompute-lstm-sweep``). The best configuration per region is
chosen by **recursive multi-step validation RMSE** — the model forecasts the
60-day validation window one day at a time, feeding back its own predictions
(never any real future value). This is the identical objective Day 4 reports, so
selection optimises the metric that actually matters rather than a 1-step proxy.

For each region the winning config is then retrained into the canonical
``models/lstm/{region}.pt`` path (training is seeded and therefore reproduces the
swept run exactly), replacing the Day 1 default-config model. A tidy summary of
the best config per region is written to
``data/processed/lstm_best_configs.csv``.

Usage:
    python -m forecasting.sweep_lstm
    python -m forecasting.sweep_lstm --region NER
"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import replace
from pathlib import Path

import pandas as pd

from forecasting.features import REGIONS, TEST_HORIZON_DAYS
from forecasting.train_lstm import (
    VAL_HORIZON_DAYS,
    LSTMConfig,
    setup_mlflow,
    train_region,
)

# Search grid. 3 x 3 x 3 = 27 configurations per region.
# Widened after the initial sweep pinned every region to the upper bound on
# hidden size and learning rate: hidden now reaches 128, lr reaches 2e-3, and
# seq_len reaches 90, so a boundary win is no longer forced by the grid.
HIDDEN_SIZES = (32, 64, 128)
SEQ_LENS = (30, 60, 90)
LEARNING_RATES = (5e-4, 1e-3, 2e-3)

_SWEEP_EXPERIMENT = "ecocompute-lstm-sweep"
_TMP_DIR = Path(__file__).resolve().parent.parent / "models" / "lstm" / "_sweep_tmp"
_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "lstm"
_BEST_CONFIG_CSV = (
    Path(__file__).resolve().parent.parent / "data" / "processed" / "lstm_best_configs.csv"
)


def sweep_region(region: str, base: LSTMConfig) -> dict:
    """Run the full grid for one region; return the best run's summary dict."""
    best: dict | None = None
    n_configs = len(HIDDEN_SIZES) * len(SEQ_LENS) * len(LEARNING_RATES)
    i = 0
    for hidden in HIDDEN_SIZES:
        for seq_len in SEQ_LENS:
            for lr in LEARNING_RATES:
                i += 1
                config = replace(base, hidden_size=hidden, seq_len=seq_len, lr=lr)
                run_name = f"{region}_h{hidden}_s{seq_len}_lr{lr:g}"
                summary = train_region(
                    region,
                    config,
                    output_dir=_TMP_DIR,
                    log_mlflow=True,
                    run_name=run_name,
                )
                summary["config"] = config
                print(
                    f"  [{i:>2}/{n_configs}] {run_name:<22} "
                    f"val_loss={summary['val_loss']:.5f}  "
                    f"MAPE={summary['MAPE']:.3f}%  best_epoch={summary['best_epoch']}"
                )
                if best is None or summary["val_loss"] < best["val_loss"]:
                    best = summary
    assert best is not None
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperparameter sweep for LSTM forecasters.")
    parser.add_argument("--region", help="Sweep only this region (default: all).")
    args = parser.parse_args()

    setup_mlflow(_SWEEP_EXPERIMENT)
    base = LSTMConfig()
    regions = [args.region.upper()] if args.region else list(REGIONS)

    print(
        f"LSTM hyperparameter sweep "
        f"(hidden={HIDDEN_SIZES}, seq_len={SEQ_LENS}, lr={LEARNING_RATES})\n"
        f"held-out test = {TEST_HORIZON_DAYS} days, validation = {VAL_HORIZON_DAYS} days\n"
        f"MLflow experiment: {_SWEEP_EXPERIMENT}\n"
    )

    rows = []
    for region in regions:
        print(f"Region {region}:")
        best = sweep_region(region, base)
        cfg = best["config"]
        # Retrain the winning config into the canonical path (seeded => identical
        # to the swept run). Not re-logged: the run already exists in the sweep.
        train_region(region, cfg, output_dir=_MODEL_DIR, log_mlflow=False)
        print(
            f"  -> BEST {region}: hidden={cfg.hidden_size} seq_len={cfg.seq_len} "
            f"lr={cfg.lr:g}  val_loss={best['val_loss']:.5f}  "
            f"MAPE={best['MAPE']:.3f}%  saved -> {_MODEL_DIR / f'{region}.pt'}\n"
        )
        rows.append(
            {
                "region": region,
                "hidden_size": cfg.hidden_size,
                "seq_len": cfg.seq_len,
                "lr": cfg.lr,
                "best_epoch": best["best_epoch"],
                "val_loss": best["val_loss"],
                "val_MAE": best["MAE"],
                "val_RMSE": best["RMSE"],
                "val_MAPE": best["MAPE"],
            }
        )

    # Clean up the throwaway sweep checkpoints.
    if _TMP_DIR.exists():
        shutil.rmtree(_TMP_DIR)

    df = pd.DataFrame(rows)
    _BEST_CONFIG_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(_BEST_CONFIG_CSV, index=False)

    print("Best config per region:")
    print(df.to_string(index=False))
    print(f"\nSaved best-config summary -> {_BEST_CONFIG_CSV}")


if __name__ == "__main__":
    main()
