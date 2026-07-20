"""
scripts/ablation_study.py

Phase 7 Day 3 -- scheduler ablation study.

On the *same* 50-job trace as ``scripts.evaluate_scheduling`` (same seed, same
hardware, same wall-clock-anchored forecasts), compares three scheduler
variants to isolate how much each scheduling dimension contributes:

  * spatial-only  -- best region, run immediately (time fixed to now);
  * temporal-only -- same (default) region, greenest time in the flexibility
                     window;
  * full          -- best region AND greenest time jointly.

For every variant the saving is measured as the **true delivered saving**
  saving = carbon(default region, now) - carbon(chosen region, chosen time)
from a single CI lookup at the chosen region+time. For the *full* condition
this is deliberately NOT the sum of the spatial and temporal components: we've
confirmed that additive sum overstates the real saving when both a region and a
time shift happen together (documented in POST /schedule's response model). The
script also prints the additive sum next to the true full saving so the
overstatement is visible.

Reuses the production carbon + forecast functions so numbers stay consistent
with the live API and with Day 2.

Usage:
    python -m scripts.ablation_study
    python -m scripts.ablation_study --n-jobs 50 --seed 42 \
        --save data/processed/phase7_ablation.csv
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from backend.carbon import compute_carbon_footprint
from backend.db.database import SessionLocal
from backend.db.models import HardwareSpecs
from forecasting.features import REGIONS
from scripts.evaluate_scheduling import (
    MAX_FUTURE_DAYS,
    Job,
    build_job_trace,
    precompute_forecasts,
)


@dataclass
class VariantChoice:
    """A variant's chosen region/time and the resulting delivered carbon."""

    region: str
    day_offset: int
    ci: float
    carbon_gco2e: float
    saving_gco2e: float


@dataclass
class JobAblation:
    job: Job
    baseline_ci: float
    baseline_carbon_gco2e: float
    spatial: VariantChoice
    temporal: VariantChoice
    full: VariantChoice


def _carbon(job: Job, ci: float) -> float:
    """Delivered carbon (gCO2e) for a job at a given carbon intensity."""
    return compute_carbon_footprint(
        runtime_hours=job.runtime_hours,
        n_cores=job.n_cores,
        total_device_tdp_watts=job.tdp_watts,
        total_device_cores=job.total_cores,
        carbon_intensity_gco2e_per_kwh=ci,
        usage_factor=job.usage_factor,
        memory_gb=job.memory_gb,
        pue=job.pue,
    ).carbon_gco2e


def _greenest_region_now(region_ci_now: dict[str, float], current: str) -> str:
    """Greenest region by today's CI, keeping the current region on a tie."""
    return min(
        region_ci_now,
        key=lambda r: (region_ci_now[r], r != current, r),
    )


def ablate_job(
    job: Job,
    region_ci_now: dict[str, float],
    region_window: dict[str, list[float]],
) -> JobAblation:
    baseline_ci = region_ci_now[job.region]
    baseline_carbon = _carbon(job, baseline_ci)

    # Flexibility window (days) -- same slicing convention as the live API.
    n_future_days = min(job.flexibility_window_hours // 24, MAX_FUTURE_DAYS)
    window_len = n_future_days + 1  # index 0 == today

    def make(region: str, day_offset: int) -> VariantChoice:
        ci = region_window[region][day_offset]
        carbon = _carbon(job, ci)
        return VariantChoice(
            region=region,
            day_offset=day_offset,
            ci=ci,
            carbon_gco2e=carbon,
            saving_gco2e=baseline_carbon - carbon,
        )

    # --- Spatial-only: greenest region today, run now. ---
    spatial_region = _greenest_region_now(region_ci_now, job.region)
    spatial = make(spatial_region, 0)

    # --- Temporal-only: same region, greenest slot in the window. ---
    own_window = region_window[job.region][:window_len]
    t_best = min(range(window_len), key=own_window.__getitem__)
    temporal = make(job.region, t_best)

    # --- Full: jointly greenest (region, time) over the window. ---
    best_region = job.region
    best_offset = 0
    best_ci = region_window[job.region][0]
    for reg in REGIONS:
        for t in range(window_len):
            ci = region_window[reg][t]
            if ci < best_ci:
                best_ci = ci
                best_region = reg
                best_offset = t
    full = make(best_region, best_offset)

    return JobAblation(
        job=job,
        baseline_ci=baseline_ci,
        baseline_carbon_gco2e=baseline_carbon,
        spatial=spatial,
        temporal=temporal,
        full=full,
    )


def _pct(saving: float, baseline: float) -> float:
    return (saving / baseline * 100.0) if baseline > 0 else 0.0


def print_report(results: list[JobAblation]) -> None:
    n = len(results)
    total_baseline = sum(r.baseline_carbon_gco2e for r in results)

    def totals(attr: str) -> tuple[float, int, int]:
        """(total saving, #region-shifted, #time-shifted) for a variant."""
        total = sum(getattr(r, attr).saving_gco2e for r in results)
        n_region = sum(
            1 for r in results if getattr(r, attr).region != r.job.region
        )
        n_time = sum(1 for r in results if getattr(r, attr).day_offset > 0)
        return total, n_region, n_time

    sp_total, sp_reg, sp_time = totals("spatial")
    tp_total, tp_reg, tp_time = totals("temporal")
    fl_total, fl_reg, fl_time = totals("full")

    # Additive sum (spatial + temporal per job) vs the true full saving --
    # demonstrates the documented overstatement of the additive approximation.
    additive_total = sp_total + tp_total

    bar = "=" * 76
    print(bar)
    print(f"Scheduler ablation -- {n} jobs, same trace as Day 2")
    print("Saving = C(default, now) - C(chosen region, chosen time)  [non-additive]")
    print(bar)
    print(f"Total baseline carbon: {total_baseline:,.1f} gCO2e\n")

    header = (
        f"{'Variant':16s} | {'Saved gCO2e':>14s} | {'Reduction':>9s} | "
        f"{'Avg/job':>10s} | {'reg':>4s} | {'time':>4s}"
    )
    print(header)
    print("-" * len(header))
    for name, total, reg, tshift in (
        ("spatial-only", sp_total, sp_reg, sp_time),
        ("temporal-only", tp_total, tp_reg, tp_time),
        ("full", fl_total, fl_reg, fl_time),
    ):
        print(
            f"{name:16s} | {total:14,.1f} | {_pct(total, total_baseline):8.2f}% | "
            f"{total / n:10,.1f} | {reg:4d} | {tshift:4d}"
        )
    print("-" * len(header))
    print(bar)
    print("Dimension contribution (of full's total saving):")
    if fl_total > 0:
        print(f"  spatial-only captures : {sp_total / fl_total * 100:6.2f}% of full")
        print(f"  temporal-only captures: {tp_total / fl_total * 100:6.2f}% of full")
    print(bar)
    print("Additive-vs-true check (the reason 'full' is measured directly):")
    print(f"  additive sum (spatial + temporal): {additive_total:14,.1f} gCO2e")
    print(f"  true full (single CI lookup)     : {fl_total:14,.1f} gCO2e")
    print(
        f"  additive overstatement           : {additive_total - fl_total:14,.1f} gCO2e "
        f"({_pct(additive_total - fl_total, fl_total):.2f}% of true full)"
    )
    print(bar)


def save_csv(results: list[JobAblation], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "job_id",
                "default_region",
                "flexibility_window_hours",
                "baseline_carbon_gco2e",
                "spatial_region",
                "spatial_saving_gco2e",
                "temporal_day_offset",
                "temporal_saving_gco2e",
                "full_region",
                "full_day_offset",
                "full_saving_gco2e",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.job.job_id,
                    r.job.region,
                    r.job.flexibility_window_hours,
                    f"{r.baseline_carbon_gco2e:.4f}",
                    r.spatial.region,
                    f"{r.spatial.saving_gco2e:.4f}",
                    r.temporal.day_offset,
                    f"{r.temporal.saving_gco2e:.4f}",
                    r.full.region,
                    r.full.day_offset,
                    f"{r.full.saving_gco2e:.4f}",
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-jobs", type=int, default=50, help="Number of jobs.")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed.")
    parser.add_argument(
        "--save",
        type=str,
        default="data/processed/phase7_ablation.csv",
        help="Per-job ablation CSV path.",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        hardware = list(session.execute(select(HardwareSpecs)).scalars().all())
        if not hardware:
            raise SystemExit(
                "No hardware_specs rows found. Seed them first "
                "(scripts/seed_hardware_specs.py)."
            )
        jobs = build_job_trace(hardware, args.n_jobs, args.seed)
        region_ci_now, region_window = precompute_forecasts(session)
    finally:
        session.close()

    results = [ablate_job(job, region_ci_now, region_window) for job in jobs]

    print_report(results)

    if args.save:
        out = Path(args.save)
        save_csv(results, out)
        print(f"Saved per-job ablation -> {out}")


if __name__ == "__main__":
    main()
