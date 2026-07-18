"""FastAPI application for serving regional carbon-intensity data."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Generator
from datetime import date, datetime

import redis
from fastapi import Depends, FastAPI, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.carbon import DEFAULT_PUE, compute_carbon_footprint
from backend.db.database import SessionLocal, engine
from backend.db.models import CITimeseries, HardwareSpecs


app = FastAPI(title="EcoCompute Backend", version="0.1.0")
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

# Cache time-to-live (seconds). CI data refreshes at most daily, so an hour is
# a safe bound; the date component in the key also forces a daily refresh.
CI_CACHE_TTL = 3600
CALC_CACHE_TTL = 3600


def cache_get(key: str) -> str | None:
    """Read a cached value, treating any Redis failure as a cache miss."""
    try:
        value = redis_client.get(key)
    except redis.RedisError:
        return None
    return value.decode() if isinstance(value, bytes) else value


def cache_set(key: str, value: str, ttl: int) -> None:
    """Write a cached value, ignoring Redis failures so the API stays up."""
    try:
        redis_client.set(key, value, ex=ttl)
    except redis.RedisError:
        pass


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


class CalculateRequest(BaseModel):
    """Specs for a compute job whose carbon footprint we want to estimate."""

    region: str = Field(..., min_length=2, max_length=10, pattern=r"^[A-Za-z]+$")
    model_name: str = Field(..., min_length=1, max_length=100)
    n_cores: int = Field(
        ...,
        ge=0,
        description=(
            "Number of cores the job requests on the selected device. Must not "
            "exceed the device's total core count. Per-core power is derived as "
            "tdp_watts / total_cores; partial core use goes in usage_factor."
        ),
    )
    runtime_hours: float = Field(..., gt=0)
    usage_factor: float = Field(1.0, ge=0.0, le=1.0)
    memory_gb: float = Field(0.0, ge=0.0)
    pue: float = Field(DEFAULT_PUE, gt=0.0)


class CalculateResponse(BaseModel):
    region: str
    model_name: str
    hardware_type: str
    tdp_watts: float
    total_cores: int
    power_per_core_watts: float
    ci_gco2e_per_kwh: float
    ci_timestamp: datetime
    runtime_hours: float
    n_cores: int
    usage_factor: float
    memory_gb: float
    pue: float
    energy_kwh: float
    carbon_gco2e: float


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

    # Cache key: region + current date, so it refreshes at most once a day.
    cache_key = f"ci:{normalized_region}:{date.today().isoformat()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return CIResponse.model_validate_json(cached)

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

    response = CIResponse(
        region=normalized_region,
        days=len(latest_rows),
        data=[CIRecord.model_validate(row) for row in reversed(latest_rows)],
    )
    cache_set(cache_key, response.model_dump_json(), CI_CACHE_TTL)
    return response


@app.post("/calculate", response_model=CalculateResponse)
def calculate_footprint(
    request: CalculateRequest,
    db: Session = Depends(get_db),
) -> CalculateResponse:
    """Estimate a compute job's carbon footprint via the Green Algorithms model.

    Looks up the device TDP from ``hardware_specs`` and the most recent carbon
    intensity for the requested region from ``ci_timeseries``.
    """
    normalized_region = request.region.upper()

    # Cache key: region + a stable hash of all job inputs (+ current date, so a
    # new day's CI is not served from a stale calculation).
    payload = request.model_dump()
    payload["region"] = normalized_region
    payload["_date"] = date.today().isoformat()
    inputs_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    cache_key = f"calc:{normalized_region}:{inputs_hash}"
    cached = cache_get(cache_key)
    if cached is not None:
        return CalculateResponse.model_validate_json(cached)

    hardware = (
        db.execute(
            select(HardwareSpecs).where(HardwareSpecs.model_name == request.model_name)
        )
        .scalars()
        .first()
    )
    if hardware is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown hardware model '{request.model_name}'"
        )

    latest_ci = (
        db.execute(
            select(CITimeseries)
            .where(CITimeseries.region == normalized_region)
            .order_by(CITimeseries.timestamp.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if latest_ci is None:
        raise HTTPException(
            status_code=404, detail=f"No CI data found for region '{normalized_region}'"
        )

    try:
        estimate = compute_carbon_footprint(
            runtime_hours=request.runtime_hours,
            n_cores=request.n_cores,
            total_device_tdp_watts=hardware.tdp_watts,
            total_device_cores=hardware.total_cores,
            carbon_intensity_gco2e_per_kwh=latest_ci.ci_gco2e_per_kwh,
            usage_factor=request.usage_factor,
            memory_gb=request.memory_gb,
            pue=request.pue,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = CalculateResponse(
        region=normalized_region,
        model_name=hardware.model_name,
        hardware_type=hardware.hardware_type,
        tdp_watts=hardware.tdp_watts,
        total_cores=hardware.total_cores,
        power_per_core_watts=estimate.power_per_core_watts,
        ci_gco2e_per_kwh=latest_ci.ci_gco2e_per_kwh,
        ci_timestamp=latest_ci.timestamp,
        runtime_hours=request.runtime_hours,
        n_cores=request.n_cores,
        usage_factor=request.usage_factor,
        memory_gb=request.memory_gb,
        pue=request.pue,
        energy_kwh=estimate.energy_kwh,
        carbon_gco2e=estimate.carbon_gco2e,
    )
    cache_set(cache_key, response.model_dump_json(), CALC_CACHE_TTL)
    return response
