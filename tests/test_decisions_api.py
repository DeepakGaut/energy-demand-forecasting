"""Tests for ``GET /decisions`` (Phase 6 backend addition).

These run against an in-memory SQLite database wired in via FastAPI's
dependency-override mechanism, so they exercise the real endpoint + ORM query
without needing Postgres or the Docker stack running.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.database import Base
from backend.decision import record_decision
from backend.main import app, get_db


@pytest.fixture()
def client_and_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app), db
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
        engine.dispose()


def _seed(db, job_id, minutes_ago, saving, recommended_time=None):
    submitted = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc) - timedelta(
        minutes=minutes_ago
    )
    return record_decision(
        db,
        job_id=job_id,
        default_region="ER",
        recommended_region="NER",
        predicted_saving_gco2e=saving,
        urgency_weight=0.0,
        recommended_time=recommended_time,
        submitted_at=submitted,
    )


def test_decisions_empty(client_and_session):
    client, _ = client_and_session
    resp = client.get("/decisions")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"count": 0, "limit": 50, "decisions": []}


def test_decisions_returns_newest_first(client_and_session):
    client, db = client_and_session
    _seed(db, "job-oldest", minutes_ago=30, saving=100.0)
    _seed(db, "job-middle", minutes_ago=20, saving=200.0)
    _seed(db, "job-newest", minutes_ago=10, saving=300.0)

    resp = client.get("/decisions")
    assert resp.status_code == 200
    body = resp.json()

    assert body["count"] == 3
    assert body["limit"] == 50
    ids = [d["job_id"] for d in body["decisions"]]
    assert ids == ["job-newest", "job-middle", "job-oldest"]


def test_decisions_record_shape(client_and_session):
    client, db = client_and_session
    rec_time = datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)
    _seed(db, "job-1", minutes_ago=5, saving=123.45, recommended_time=rec_time)

    body = client.get("/decisions").json()
    row = body["decisions"][0]
    assert set(row) == {
        "job_id",
        "submitted_at",
        "default_region",
        "recommended_region",
        "recommended_time",
        "predicted_saving_gco2e",
    }
    assert row["job_id"] == "job-1"
    assert row["default_region"] == "ER"
    assert row["recommended_region"] == "NER"
    assert row["predicted_saving_gco2e"] == pytest.approx(123.45)
    assert row["recommended_time"] is not None


def test_decisions_run_now_has_null_time(client_and_session):
    client, db = client_and_session
    _seed(db, "job-run-now", minutes_ago=5, saving=50.0, recommended_time=None)

    row = client.get("/decisions").json()["decisions"][0]
    assert row["recommended_time"] is None


def test_decisions_respects_limit(client_and_session):
    client, db = client_and_session
    for i in range(5):
        _seed(db, f"job-{i}", minutes_ago=i, saving=float(i + 1))

    body = client.get("/decisions?limit=2").json()
    assert body["count"] == 2
    assert body["limit"] == 2
    # Newest-first: job-0 (0 min ago) then job-1 (1 min ago).
    assert [d["job_id"] for d in body["decisions"]] == ["job-0", "job-1"]


@pytest.mark.parametrize("bad_limit", [0, -1, 201, 1000])
def test_decisions_limit_out_of_range_is_422(client_and_session, bad_limit):
    client, _ = client_and_session
    resp = client.get(f"/decisions?limit={bad_limit}")
    assert resp.status_code == 422
