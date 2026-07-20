"""
scripts/evaluate_scheduling.py

Phase 7 Day 2 -- scheduling-effectiveness evaluation.

Runs a fixed trace of ~50 synthetic jobs (varied hardware / cores / memory /
runtime / region / flexibility / urgency) through two conditions and compares
their total carbon cost:

  * baseline  -- every job runs immediately in its submitted region;
  * treatment -- every job goes through the carbon-aware scheduler and runs in
                 the recommended region at the recommended time.

To stay consistent with what the deployed API actually reports, this script
reuses the *same* production functions the live endpoints use:

  * ``forecasting.serve.forecast_region(..., from_today=True)`` for the
    wall-clock-anchored CI convention (day 1 == today);
  * ``backend.decision.score_scheduling`` for the region/time recommendation;
  * ``backend.carbon.compute_carbon_footprint`` for energy + carbon.

Carbon intensities already embed the real CEA generation-weighted emission
factors (baked into ``ci_gco2e_per_kwh`` at data-load time).

IMPORTANT -- honest, non-additive saving:
    The live ``POST /schedule`` reports ``predicted_saving_gco2e`` as an
    *additive* approximation (spatial + temporal) that can slightly overstate a
    combined shift. This evaluation instead measures the **true delivered
    saving** = carbon(default region, now) - carbon(chosen region, chosen time),
    computed from a single CI lookup at the chosen region+time. That is the
    figure that reflects the actual carbon a scheduled job would emit.

Usage:
    python -m scripts.evaluate_scheduling
    python -m scripts.evaluate_scheduling --n-jobs 50 --seed 42 \
        --save data/processed/phase7_scheduling_effectiveness.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from backend.carbon import DEFAULT_PUE, compute_carbon_footprint
from backend.db.database import SessionLocal
from backend.db.models import HardwareSpecs
from backend.decision import score_scheduling
from forecasting.features import REGIONS
from forecasting.serve import forecast_region

# Matches backend/main.py's SPATIAL_WEIGHT and the temporal-window cap.
SPATIAL_WEIGHT = 1.0
MAX_FUTURE_DAYS = 14

# Flexibility-window options offered by the frontend Schedule page (hours).
FLEX_OPTIONS = (0, 24, 48, 72, 168, 336)
MEMORY_OPTIONS = (8, 16, 32, 64, 128, 256, 512)
URGENCY_PROB = 0.2


@dataclass
class Job:
    job_id: str
    model_name: str
    hardware_type: str
    tdp_watts: float
    total_cores: int
    region: str
    n_cores: int
    memory_gb: float
    runtime_hours: float
    usage_factor: float
    pue: float
    flexibility_window_hours: int
    urgency_flag: bool


@dataclass
class JobResult:
    job: Job
    baseline_ci: float
    treatment_ci: float
    baseline_carbon_gco2e: float
    treatment_carbon_gco2e: float
    saving_gco2e: float
    recommended_region: str
    recommended_day_offset: int  # 0 == run now
    region_shifted: bool
    time_shifted: bool


def build_job_trace(hardware: list[HardwareSpecs], n_jobs: int, seed: int) -> list[Job]:
    """Generate a deterministic trace of varied synthetic jobs."""
    rng = random.Random(seed)
    jobs: list[Job] = []
    for i in range(n_jobs):
        hw = rng.choice(hardware)
        # Cores: a realistic partial allocation of the device, at least 1.
        if hw.hardware_type == "CPU":
            n_cores = rng.randint(1, hw.total_cores)
        else:
            # GPUs: allocate a whole or large fraction of the device.
            n_cores = rng.randint(max(1, hw.total_cores // 4), hw.total_cores)
        jobs.append(
            Job(
                job_id=f"eval-{i:03d}",
                model_name=hw.model_name,
                hardware_type=hw.hardware_type,
                tdp_watts=hw.tdp_watts,
                total_cores=hw.total_cores,
                region=rng.choice(list(REGIONS)),
                n_cores=n_cores,
                memory_gb=float(rng.choice(MEMORY_OPTIONS)),
                runtime_hours=round(rng.uniform(0.5, 48.0), 1),
                usage_factor=round(rng.uniform(0.6, 1.0), 2),
                pue=DEFAULT_PUE,
                flexibility_window_hours=rng.choice(FLEX_OPTIONS),
                urgency_flag=rng.random() < URGENCY_PROB,
            )
        )
    return jobs


def precompute_forecasts(session) -> tuple[dict[str, float], dict[str, list[float]]]:
    """Forecast every region once (wall-clock anchored) and cache the results.

    Because every job in the trace is "submitted now", today's CI and the
    forward window are identical across jobs. Returns:
      * region_ci_now: {region: CI today}
      * region_window: {region: [CI today, CI +1d, ..., CI +MAX_FUTURE_DAYS]}
    """
    region_ci_now: dict[str, float] = {}
    region_window: dict[str, list[float]] = {}
    for reg in REGIONS:
        _, _, _, values = forecast_region(
            reg, MAX_FUTURE_DAYS + 1, session=session, from_today=True
        )
        region_window[reg] = [float(v) for v in values]
        region_ci_now[reg] = float(values[0])
    return region_ci_now, region_window


def evaluate_job(
    job: Job,
    region_ci_now: dict[str, float],
    region_window: dict[str, list[float]],
) -> JobResult:
    """Score one job exactly as the live API would, then measure true carbon."""
    ci_now = region_ci_now[job.region]

    # Temporal window for the default region, same slicing as POST /schedule.
    urgency_weight = 1.0 if job.urgency_flag else 0.0
    n_future_days = min(job.flexibility_window_hours // 24, MAX_FUTURE_DAYS)
    ci_window = region_window[job.region][: n_future_days + 1]

    result = score_scheduling(
        current_region=job.region,
        region_ci_now=region_ci_now,
        ci_window=ci_window,
        urgency_weight=urgency_weight,
        spatial_weight=SPATIAL_WEIGHT,
    )

    # Resolve the recommended shifts using the same gating as the API.
    temporal_contrib = result.temporal_saving * (1.0 - urgency_weight)
    spatial_contrib = result.spatial_saving * SPATIAL_WEIGHT
    time_shifted = result.best_time_index > 0 and temporal_contrib > 0.0
    region_shifted = result.best_region != job.region and spatial_contrib > 0.0

    recommended_region = result.best_region if region_shifted else job.region
    day_offset = result.best_time_index if time_shifted else 0

    # True delivered CI at the chosen region AND chosen time (non-additive).
    treatment_ci = region_window[recommended_region][day_offset]

    baseline = compute_carbon_footprint(
        runtime_hours=job.runtime_hours,
        n_cores=job.n_cores,
        total_device_tdp_watts=job.tdp_watts,
        total_device_cores=job.total_cores,
        carbon_intensity_gco2e_per_kwh=ci_now,
        usage_factor=job.usage_factor,
        memory_gb=job.memory_gb,
        pue=job.pue,
    )
    treatment = compute_carbon_footprint(
        runtime_hours=job.runtime_hours,
        n_cores=job.n_cores,
        total_device_tdp_watts=job.tdp_watts,
        total_device_cores=job.total_cores,
        carbon_intensity_gco2e_per_kwh=treatment_ci,
        usage_factor=job.usage_factor,
        memory_gb=job.memory_gb,
        pue=job.pue,
    )

    return JobResult(
        job=job,
        baseline_ci=ci_now,
        treatment_ci=treatment_ci,
        baseline_carbon_gco2e=baseline.carbon_gco2e,
        treatment_carbon_gco2e=treatment.carbon_gco2e,
        saving_gco2e=baseline.carbon_gco2e - treatment.carbon_gco2e,
        recommended_region=recommended_region,
        recommended_day_offset=day_offset,
        region_shifted=region_shifted,
        time_shifted=time_shifted,
    )


def print_report(results: list[JobResult]) -> None:
    n = len(results)
    total_baseline = sum(r.baseline_carbon_gco2e for r in results)
    total_treatment = sum(r.treatment_carbon_gco2e for r in results)
    total_saving = total_baseline - total_treatment
    pct = (total_saving / total_baseline * 100.0) if total_baseline > 0 else 0.0
    avg_saving = total_saving / n if n else 0.0
    n_region_shift = sum(1 for r in results if r.region_shifted)
    n_time_shift = sum(1 for r in results if r.time_shifted)
    n_urgent = sum(1 for r in results if r.job.urgency_flag)
    per_job_pct = [
        (r.saving_gco2e / r.baseline_carbon_gco2e * 100.0)
        if r.baseline_carbon_gco2e > 0
        else 0.0
        for r in results
    ]
    best = max(results, key=lambda r: r.saving_gco2e)

    bar = "=" * 72
    print(bar)
    print(f"Scheduling effectiveness -- {n} synthetic jobs (baseline vs scheduler)")
    print("True delivered saving = C(default, now) - C(chosen region, chosen time)")
    print(bar)
    print(f"{'Total baseline carbon':32s}: {total_baseline:14.1f} gCO2e")
    print(f"{'Total scheduled carbon':32s}: {total_treatment:14.1f} gCO2e")
    print(f"{'Total carbon saved':32s}: {total_saving:14.1f} gCO2e")
    print(f"{'Carbon reduction vs baseline':32s}: {pct:14.2f} %")
    print(f"{'Average saving per job':32s}: {avg_saving:14.1f} gCO2e")
    print(
        f"{'Per-job reduction (min/mean/max)':32s}: "
        f"{min(per_job_pct):.2f}% / "
        f"{sum(per_job_pct) / n:.2f}% / "
        f"{max(per_job_pct):.2f}%"
    )
    print("-" * 72)
    print(f"{'Jobs region-shifted':32s}: {n_region_shift:3d} / {n}")
    print(f"{'Jobs time-shifted':32s}: {n_time_shift:3d} / {n}")
    print(f"{'Jobs flagged urgent':32s}: {n_urgent:3d} / {n}")
    print(
        f"{'Best single-job saving':32s}: {best.saving_gco2e:.1f} gCO2e "
        f"({best.job.region}->{best.recommended_region}, {best.job.model_name})"
    )
    print(bar)

    # Per-region breakdown of where jobs were sent.
    dest_counts: dict[str, int] = {}
    for r in results:
        dest_counts[r.recommended_region] = dest_counts.get(r.recommended_region, 0) + 1
    print("Recommended-region distribution:")
    for reg in REGIONS:
        print(f"  {reg:4s}: {dest_counts.get(reg, 0):3d}")
    print(bar)


def save_csv(results: list[JobResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "job_id",
                "model_name",
                "hardware_type",
                "default_region",
                "recommended_region",
                "n_cores",
                "memory_gb",
                "runtime_hours",
                "usage_factor",
                "flexibility_window_hours",
                "urgency_flag",
                "recommended_day_offset",
                "region_shifted",
                "time_shifted",
                "baseline_ci",
                "treatment_ci",
                "baseline_carbon_gco2e",
                "treatment_carbon_gco2e",
                "saving_gco2e",
            ]
        )
        for r in results:
            j = r.job
            writer.writerow(
                [
                    j.job_id,
                    j.model_name,
                    j.hardware_type,
                    j.region,
                    r.recommended_region,
                    j.n_cores,
                    j.memory_gb,
                    j.runtime_hours,
                    j.usage_factor,
                    j.flexibility_window_hours,
                    j.urgency_flag,
                    r.recommended_day_offset,
                    r.region_shifted,
                    r.time_shifted,
                    f"{r.baseline_ci:.4f}",
                    f"{r.treatment_ci:.4f}",
                    f"{r.baseline_carbon_gco2e:.4f}",
                    f"{r.treatment_carbon_gco2e:.4f}",
                    f"{r.saving_gco2e:.4f}",
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-jobs", type=int, default=50, help="Number of jobs.")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed.")
    parser.add_argument(
        "--save",
        type=str,
        default="data/processed/phase7_scheduling_effectiveness.csv",
        help="Per-job results CSV path.",
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

    results = [evaluate_job(job, region_ci_now, region_window) for job in jobs]

    print_report(results)

    if args.save:
        out = Path(args.save)
        save_csv(results, out)
        print(f"Saved per-job results -> {out}")


if __name__ == "__main__":
    main()
