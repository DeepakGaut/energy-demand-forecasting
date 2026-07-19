"""
tests/test_compare_regions_api.py

Integration test for ``GET /compare-regions`` (Phase 5 Day 1).

Exercises the real path: FastAPI -> Green Algorithms calculator -> latest CI per
region from ``ci_timeseries`` (PostgreSQL), with Redis caching in front. Needs
the Docker stack up and ``hardware_specs`` seeded; skips cleanly otherwise so the
pure-unit suite still runs standalone.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from forecasting.features import REGIONS

client = TestClient(app)

# A device that exists in scripts/seed_hardware_specs.py (see test_carbon.py).
_HARDWARE = "AMD EPYC 9654"
_JOB = {"model_name": _HARDWARE, "n_cores": 32, "runtime_hours": 10, "memory_gb": 64}


def _deps_available() -> bool:
    try:
        return client.get("/health").status_code == 200
    except Exception:  # noqa: BLE001 - any failure means deps are unavailable
        return False


pytestmark = pytest.mark.skipif(
    not _deps_available(),
    reason="backend dependencies (postgres/redis) not reachable",
)


def test_compare_regions_shape_and_ranking() -> None:
    """Returns every region ranked greenest first with a valid saving scale."""
    resp = client.get("/compare-regions", params=_JOB)
    assert resp.status_code == 200

    body = resp.json()
    assert body["model_name"] == _HARDWARE
    assert body["energy_kwh"] > 0

    ranking = body["ranking"]
    assert {r["region"] for r in ranking} == set(REGIONS)

    carbons = [r["carbon_gco2e"] for r in ranking]
    # Ranking is greenest (lowest carbon) first.
    assert carbons == sorted(carbons)
    assert body["greenest_region"] == ranking[0]["region"]
    assert body["worst_region"] == ranking[-1]["region"]

    # The worst region saves 0% vs itself; the greenest saves the most.
    assert ranking[-1]["pct_vs_worst"] == pytest.approx(0.0)
    assert ranking[0]["pct_vs_worst"] >= ranking[-1]["pct_vs_worst"]
    for r in ranking:
        assert 0.0 <= r["pct_vs_worst"] < 100.0
        assert r["ci_gco2e_per_kwh"] > 0
        assert r["carbon_gco2e"] > 0


def test_compare_regions_unknown_hardware_404() -> None:
    resp = client.get("/compare-regions", params={**_JOB, "model_name": "NopeGPU"})
    assert resp.status_code == 404


def test_compare_regions_invalid_cores_422() -> None:
    # Requesting more cores than the device has is a clean validation error.
    resp = client.get("/compare-regions", params={**_JOB, "n_cores": 10_000_000})
    assert resp.status_code == 422
