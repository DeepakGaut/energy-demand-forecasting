"""Tests for ``GET /hardware`` (Phase 6 backend addition).

Runs against an in-memory SQLite database wired in via FastAPI's
dependency-override mechanism, so it exercises the real endpoint + ORM query
without needing Postgres or the Docker stack running.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.database import Base
from backend.db.models import HardwareSpecs
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


def _seed(db, model_name, hardware_type, tdp_watts, total_cores):
    db.add(
        HardwareSpecs(
            model_name=model_name,
            hardware_type=hardware_type,
            tdp_watts=tdp_watts,
            total_cores=total_cores,
        )
    )
    db.commit()


def test_hardware_empty(client_and_session):
    client, _ = client_and_session
    resp = client.get("/hardware")
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "hardware": []}


def test_hardware_record_shape(client_and_session):
    client, db = client_and_session
    _seed(db, "NVIDIA A100", "GPU", 400.0, 6912)

    body = client.get("/hardware").json()
    assert body["count"] == 1
    row = body["hardware"][0]
    assert set(row) == {"model_name", "hardware_type", "tdp_watts", "total_cores"}
    assert row["model_name"] == "NVIDIA A100"
    assert row["hardware_type"] == "GPU"
    assert row["tdp_watts"] == pytest.approx(400.0)
    assert row["total_cores"] == 6912


def test_hardware_ordered_by_type_then_name(client_and_session):
    client, db = client_and_session
    _seed(db, "AMD EPYC 9654", "CPU", 360.0, 96)
    _seed(db, "NVIDIA A100", "GPU", 400.0, 6912)
    _seed(db, "Intel Xeon 8480", "CPU", 350.0, 56)

    body = client.get("/hardware").json()
    names = [(h["hardware_type"], h["model_name"]) for h in body["hardware"]]
    # CPU before GPU; within a type, alphabetical by model name.
    assert names == [
        ("CPU", "AMD EPYC 9654"),
        ("CPU", "Intel Xeon 8480"),
        ("GPU", "NVIDIA A100"),
    ]
