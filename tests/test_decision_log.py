"""Unit tests for the decision-log persistence helper (backend.decision.record_decision).

These tests run against an in-memory SQLite database created from the ORM
metadata, so they exercise the real model + helper without requiring Postgres
or the Docker stack to be running.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.database import Base
from backend.db.models import SchedulingDecision
from backend.decision import record_decision


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def test_record_decision_persists_row(session):
    submitted = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    recommended = datetime(2026, 7, 19, 15, 0, tzinfo=timezone.utc)

    decision = record_decision(
        session,
        job_id="job-123",
        default_region="NR",
        recommended_region="SR",
        predicted_saving_gco2e=250.5,
        urgency_weight=0.2,
        recommended_time=recommended,
        submitted_at=submitted,
    )

    assert decision.id is not None

    rows = session.query(SchedulingDecision).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.job_id == "job-123"
    assert row.default_region == "NR"
    assert row.recommended_region == "SR"
    assert row.predicted_saving_gco2e == pytest.approx(250.5)
    assert row.urgency_weight == pytest.approx(0.2)
    assert row.recommended_time is not None


def test_record_decision_defaults_submitted_at_and_allows_run_now(session):
    decision = record_decision(
        session,
        job_id="job-now",
        default_region="WR",
        recommended_region="WR",  # no region shift
        predicted_saving_gco2e=0.0,
        urgency_weight=1.0,
        recommended_time=None,  # run now
    )

    assert decision.submitted_at is not None
    assert decision.recommended_time is None
    assert decision.recommended_region == "WR"


def test_record_decision_logs_every_call(session):
    for i in range(3):
        record_decision(
            session,
            job_id=f"job-{i}",
            default_region="NR",
            recommended_region="SR",
            predicted_saving_gco2e=float(i + 1),
            urgency_weight=0.5,
        )

    rows = session.query(SchedulingDecision).order_by(SchedulingDecision.job_id).all()
    assert [r.job_id for r in rows] == ["job-0", "job-1", "job-2"]


def test_record_decision_rejects_none_saving(session):
    with pytest.raises(ValueError):
        record_decision(
            session,
            job_id="job-none",
            default_region="NR",
            recommended_region="SR",
            predicted_saving_gco2e=None,  # type: ignore[arg-type]
            urgency_weight=0.5,
        )


def test_record_decision_rejects_zero_saving_on_region_shift(session):
    # Recommending a different region with zero saving is a placeholder, not a
    # genuine computed value -> must be rejected.
    with pytest.raises(ValueError):
        record_decision(
            session,
            job_id="job-zero-region",
            default_region="NR",
            recommended_region="SR",  # region shift
            predicted_saving_gco2e=0.0,
            urgency_weight=0.0,
        )


def test_record_decision_rejects_zero_saving_on_time_shift(session):
    from datetime import datetime, timezone

    with pytest.raises(ValueError):
        record_decision(
            session,
            job_id="job-zero-time",
            default_region="NR",
            recommended_region="NR",  # same region ...
            recommended_time=datetime(2026, 7, 20, tzinfo=timezone.utc),  # ... but deferred
            predicted_saving_gco2e=0.0,
            urgency_weight=0.0,
        )


def test_record_decision_allows_zero_saving_for_run_now(session):
    # Zero saving is legitimate only for "run now in the default region".
    decision = record_decision(
        session,
        job_id="job-run-now",
        default_region="NR",
        recommended_region="NR",
        recommended_time=None,
        predicted_saving_gco2e=0.0,
        urgency_weight=1.0,
    )
    assert decision.id is not None
    assert decision.predicted_saving_gco2e == pytest.approx(0.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"predicted_saving_gco2e": -1.0, "urgency_weight": 0.5},
        {"predicted_saving_gco2e": 10.0, "urgency_weight": 1.5},
        {"predicted_saving_gco2e": 10.0, "urgency_weight": -0.1},
    ],
)
def test_record_decision_invalid_inputs_raise(session, kwargs):
    with pytest.raises(ValueError):
        record_decision(
            session,
            job_id="bad",
            default_region="NR",
            recommended_region="SR",
            **kwargs,
        )
