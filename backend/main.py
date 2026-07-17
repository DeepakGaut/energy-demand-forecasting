"""FastAPI application for serving regional carbon-intensity data."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import datetime

import redis
from fastapi import Depends, FastAPI, HTTPException, Path
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.db.database import SessionLocal, engine
from backend.db.models import CITimeseries


app = FastAPI(title="EcoCompute Backend", version="0.1.0")
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


class CIRecord(BaseModel):
    """A daily carbon-intensity observation and its generation mix."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    ci_gco2e_per_kwh: float
    coal_pct: float | None
    hydro_pct: float | None
    wind_pct: float | None
    solar_pct: float | None
    nuclear_pct: float | None
    gas_pct: float | None


class CIResponse(BaseModel):
    region: str
    days: int
    data: list[CIRecord]


def get_db() -> Generator[Session, None, None]:
    """Provide a database session for one request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "EcoCompute backend is up"}


@app.get("/health")
def health() -> dict[str, str]:
    """Confirm that the API's PostgreSQL and Redis dependencies are reachable."""
    status = {"postgres": "unknown", "redis": "unknown"}

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        status["postgres"] = "ok"
    except Exception:  # noqa: BLE001 - do not expose infrastructure details
        status["postgres"] = "error"

    try:
        redis_client.ping()
        status["redis"] = "ok"
    except Exception:  # noqa: BLE001 - do not expose infrastructure details
        status["redis"] = "error"

    if "error" in status.values():
        raise HTTPException(status_code=503, detail=status)
    return status


@app.get("/ci/{region}", response_model=CIResponse)
def get_recent_ci(
    region: str = Path(..., min_length=2, max_length=10, pattern=r"^[A-Za-z]+$"),
    db: Session = Depends(get_db),
) -> CIResponse:
    """Return the latest 30 daily CI records for one region, oldest first."""
    normalized_region = region.upper()
    latest_rows = (
        db.execute(
            select(CITimeseries)
            .where(CITimeseries.region == normalized_region)
            .order_by(CITimeseries.timestamp.desc())
            .limit(30)
        )
        .scalars()
        .all()
    )

    if not latest_rows:
        raise HTTPException(status_code=404, detail=f"No CI data found for region '{normalized_region}'")

    return CIResponse(
        region=normalized_region,
        days=len(latest_rows),
        data=[CIRecord.model_validate(row) for row in reversed(latest_rows)],
    )
