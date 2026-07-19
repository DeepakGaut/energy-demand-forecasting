"""
tests/test_schedule_api.py

Integration test for ``POST /schedule`` (Phase 5 Day 5).

Exercises the real path: FastAPI -> decision engine, combining the spatial
signal (each region's current CI from ``ci_timeseries``) with the temporal
signal (the default region's ARIMA forecast across the flexibility window),
turning the CI-difference score into an absolute gCO2e saving via the job's
energy use, and logging every decision to ``scheduling_decisions``. Needs the
Docker stack up, ``hardware_specs`` seeded, and the ARIMA models on disk; skips
cleanly otherwise so the pure-unit suite still runs standalone.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.db.database import SessionLocal
from backend.db.models import SchedulingDecision
from backend.main import app

client = TestClient(app)

_HARDWARE = "AMD EPYC 9654"
_JOB = {"model_name": _HARDWARE, "n_cores": 32, "runtime_hours": 10, "memory_gb": 64}
# ER is the worst (highest-CI) region in the seeded data, so a flexible job
# submitted there should have a genuinely greener region to move to.
_DEFAULT_REGION = "ER"


def _deps_available() -> bool:
    try:
        return client.get("/health").status_code == 200
    except Exception:  # noqa: BLE001 - any failure means deps are unavailable
        return False


pytestmark = pytest.mark.skipif(
    not _deps_available(),
    reason="backend dependencies (postgres/redis) not reachable",
)


def test_schedule_flexible_job_recommends_a_real_shift() -> None:
    """A flexible job gets a genuinely different, lower-carbon region or time."""
    job_id = f"test-{uuid.uuid4().hex}"
    resp = client.post(
        "/schedule",
        json={
            **_JOB,
            "region": _DEFAULT_REGION,
            "flexibility_window_hours": 72,  # 3 days -> temporal shifting possible
            "urgency_flag": False,
            "job_id": job_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["default_region"] == _DEFAULT_REGION
    # A greener option exists, so it must NOT be a "run now, stay here" answer.
    assert body["run_now"] is False
    assert (
        body["recommended_region"] != _DEFAULT_REGION
        or body["recommended_time"] is not None
    )
    # The recommendation carries a genuinely positive, computed saving.
    assert body["predicted_saving_gco2e"] > 0
    assert 0.0 < body["confidence"] <= 1.0
    # Saving is genuinely energy x CI-difference, not a placeholder: the two
    # components add up to the total (within rounding).
    assert body["temporal_saving_gco2e"] + body["spatial_saving_gco2e"] == pytest.approx(
        body["predicted_saving_gco2e"], abs=0.01
    )
    assert body["energy_kwh"] > 0


def test_schedule_logs_decision_to_db() -> None:
    """Every /schedule call writes exactly one scheduling_decisions row."""
    job_id = f"test-{uuid.uuid4().hex}"
    resp = client.post(
        "/schedule",
        json={
            **_JOB,
            "region": _DEFAULT_REGION,
            "flexibility_window_hours": 48,
            "urgency_flag": False,
            "job_id": job_id,
        },
    )
    assert resp.status_code == 200

    session = SessionLocal()
    try:
        rows = (
            session.query(SchedulingDecision)
            .filter(SchedulingDecision.job_id == job_id)
            .all()
        )
    finally:
        session.close()

    assert len(rows) == 1
    row = rows[0]
    assert row.default_region == _DEFAULT_REGION
    assert row.predicted_saving_gco2e == pytest.approx(
        resp.json()["predicted_saving_gco2e"], abs=0.01
    )


def test_schedule_urgent_job_never_shifts_time() -> None:
    """An urgent job must run now (no time shift), though it may still relocate."""
    resp = client.post(
        "/schedule",
        json={
            **_JOB,
            "region": _DEFAULT_REGION,
            "flexibility_window_hours": 72,  # would allow a time shift if not urgent
            "urgency_flag": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["urgency_weight"] == 1.0
    assert body["recommended_time"] is None
    assert body["temporal_saving_gco2e"] == pytest.approx(0.0)


def test_schedule_zero_flexibility_has_no_time_shift() -> None:
    """With no flexibility window, a time shift is impossible (region shift ok)."""
    resp = client.post(
        "/schedule",
        json={
            **_JOB,
            "region": _DEFAULT_REGION,
            "flexibility_window_hours": 0,
            "urgency_flag": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_time"] is None
    assert body["temporal_saving_gco2e"] == pytest.approx(0.0)


def test_schedule_unknown_region_404() -> None:
    resp = client.post(
        "/schedule",
        json={**_JOB, "region": "ZZ", "flexibility_window_hours": 24},
    )
    assert resp.status_code == 404


def test_schedule_unknown_hardware_404() -> None:
    resp = client.post(
        "/schedule",
        json={
            "model_name": "Definitely Not A Real CPU",
            "n_cores": 8,
            "runtime_hours": 5,
            "region": _DEFAULT_REGION,
        },
    )
    assert resp.status_code == 404


def test_schedule_too_many_cores_422() -> None:
    resp = client.post(
        "/schedule",
        json={
            **_JOB,
            "n_cores": 100000,  # far more than the device has -> calculator rejects
            "region": _DEFAULT_REGION,
        },
    )
    assert resp.status_code == 422
