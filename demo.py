"""Live demo: submit a handful of jobs through the real EcoCompute API.

Run this against a running backend (``docker compose up -d`` then this script)
to show the before/after carbon comparison the scheduler produces. It is
reliable regardless of the calendar date: the API remaps each region's forecast
so day 1 always aligns with today's wall-clock date, so "today" always has a
valid forecasted carbon intensity to schedule against.

Usage:
    python demo.py                      # hit http://localhost:8000
    DEMO_API_BASE_URL=http://host:8000 python demo.py

This is the *backend-only fallback* for a live demo; the primary demo path is the
frontend walkthrough (Calculator -> Schedule -> Regions -> History).
"""

from __future__ import annotations

import os
import sys

import requests

BASE_URL = os.getenv("DEMO_API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 60  # seconds; first forecast call can be slow while models warm up.

# Full names for the five regional grids, for readable output.
REGION_NAMES = {
    "NR": "Northern",
    "SR": "Southern",
    "ER": "Eastern",
    "WR": "Western",
    "NER": "North-Eastern",
}

# A small, representative set of jobs. Default regions are deliberately varied so
# the spatial (region-shift) signal is visible; one job already sits in the
# greenest region (NER) to show the scheduler honestly leaving it in place.
DEMO_JOBS = [
    {
        "label": "LLM fine-tune (flexible, 12h on 8x H100)",
        "region": "ER",  # Eastern is typically the dirtiest grid.
        "model_name": "NVIDIA H100 SXM",
        "n_cores": 16896,
        "runtime_hours": 12.0,
        "usage_factor": 0.9,
        "memory_gb": 640.0,
        "flexibility_window_hours": 72,
    },
    {
        "label": "Genomics batch (flexible, 24h on EPYC CPU)",
        "region": "WR",
        "model_name": "AMD EPYC 7763",
        "n_cores": 64,
        "runtime_hours": 24.0,
        "usage_factor": 0.8,
        "memory_gb": 256.0,
        "flexibility_window_hours": 168,
    },
    {
        "label": "Batch render (flexible, 6h on RTX 4090)",
        "region": "SR",
        "model_name": "NVIDIA RTX 4090",
        "n_cores": 16384,
        "runtime_hours": 6.0,
        "usage_factor": 1.0,
        "memory_gb": 24.0,
        "flexibility_window_hours": 48,
    },
    {
        "label": "Urgent inference (must run now, A100)",
        "region": "NR",
        "model_name": "NVIDIA A100 80GB",
        "n_cores": 6912,
        "runtime_hours": 2.0,
        "usage_factor": 0.7,
        "memory_gb": 80.0,
        "flexibility_window_hours": 0,
        "urgency_flag": True,
    },
    {
        "label": "Nightly ETL (already in greenest region)",
        "region": "NER",
        "model_name": "Intel Xeon Gold 6338",
        "n_cores": 32,
        "runtime_hours": 4.0,
        "usage_factor": 0.6,
        "memory_gb": 128.0,
        "flexibility_window_hours": 72,
    },
]


def check_backend() -> None:
    """Fail fast with a clear message if the backend is unreachable/unhealthy."""
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
    except requests.RequestException as exc:
        sys.exit(
            f"Cannot reach the backend at {BASE_URL}. Is it running "
            f"(docker compose up -d)?\n  {exc}"
        )
    if resp.status_code != 200:
        sys.exit(f"Backend health check returned {resp.status_code}: {resp.text}")
    body = resp.json()
    if body.get("postgres") != "ok" or body.get("redis") != "ok":
        sys.exit(f"Backend is up but not healthy: {body}")
    print(f"Backend healthy at {BASE_URL}  (postgres ok, redis ok)\n")


def region_label(code: str) -> str:
    """'ER' -> 'Eastern (ER)'."""
    return f"{REGION_NAMES.get(code, code)} ({code})"


def submit(job: dict) -> dict:
    """POST one job to /schedule, returning the recommendation payload."""
    payload = {k: v for k, v in job.items() if k != "label"}
    resp = requests.post(f"{BASE_URL}/schedule", json=payload, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(
            f"/schedule failed ({resp.status_code}) for '{job['label']}': {resp.text}"
        )
    return resp.json()


def describe_shift(result: dict) -> str:
    """Human-readable summary of what the scheduler recommends."""
    parts = []
    if result["recommended_region"] != result["default_region"]:
        parts.append(
            f"move {region_label(result['default_region'])} "
            f"-> {region_label(result['recommended_region'])}"
        )
    if result["recommended_time"] is not None:
        parts.append(f"defer to {result['recommended_time'][:10]}")
    if not parts:
        return "run now, no change (already optimal)"
    return " + ".join(parts)


def main() -> None:
    print("=" * 74)
    print("EcoCompute live demo - carbon-aware scheduling (real API)")
    print("=" * 74)
    check_backend()

    total_baseline = 0.0
    total_scheduled = 0.0

    for i, job in enumerate(DEMO_JOBS, start=1):
        result = submit(job)
        baseline = result["baseline_carbon_gco2e"]
        saved = result["predicted_saving_gco2e"]
        scheduled = baseline - saved
        pct = (saved / baseline * 100.0) if baseline > 0 else 0.0

        total_baseline += baseline
        total_scheduled += scheduled

        print(f"[{i}] {job['label']}")
        print(f"      hardware        : {result['model_name']} ({result['hardware_type']})")
        print(f"      recommendation  : {describe_shift(result)}")
        print(f"      baseline (now)  : {baseline:>12,.1f} gCO2e")
        print(f"      scheduled       : {scheduled:>12,.1f} gCO2e")
        print(
            f"      saved           : {saved:>12,.1f} gCO2e  ({pct:5.1f}%)  "
            f"[spatial {result['spatial_saving_gco2e']:,.1f} / "
            f"temporal {result['temporal_saving_gco2e']:,.1f}]"
        )
        print()

    total_saved = total_baseline - total_scheduled
    total_pct = (total_saved / total_baseline * 100.0) if total_baseline > 0 else 0.0

    print("-" * 74)
    print(f"  Total baseline  : {total_baseline:>14,.1f} gCO2e")
    print(f"  Total scheduled : {total_scheduled:>14,.1f} gCO2e")
    print(f"  Total saved     : {total_saved:>14,.1f} gCO2e  ({total_pct:.1f}%)")
    print("-" * 74)
    print(
        "\nNote: with the current ARIMA forecasts the intensity curve flattens\n"
        "within a couple of days, so the temporal (time-shift) saving is ~0 and\n"
        "essentially all of the reduction comes from the spatial (region-shift)\n"
        "signal - routing jobs to the greenest grid available today."
    )


if __name__ == "__main__":
    main()
