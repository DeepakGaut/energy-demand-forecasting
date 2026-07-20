"""FastAPI application for serving regional carbon-intensity data."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from collections.abc import Generator
from datetime import date, datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.carbon import DEFAULT_PUE, compute_carbon_footprint
from backend.db.database import SessionLocal, engine
from backend.db.models import CITimeseries, HardwareSpecs, SchedulingDecision
from backend.decision import record_decision, score_scheduling
from forecasting.features import REGIONS
from forecasting.serve import BEST_MODEL, forecast_region


app = FastAPI(title="EcoCompute Backend", version="0.1.0")
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

# Allow the frontend (a separate origin) to call the API from the browser.
# Origins are configurable via FRONTEND_ORIGINS (comma-separated) so the Vercel
# deployment URL can be added without a code change; defaults to local dev.
_frontend_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Hard ceiling on how long any single request may run before the client gets a
# clean 504 instead of a connection that hangs indefinitely (e.g. if a forecast
# or database call stalls). Configurable via env for slower deployments.
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))


@app.middleware("http")
async def timeout_middleware(request: Request, call_next):
    """Fail slow requests cleanly with a 504 rather than hanging the client."""
    try:
        return await asyncio.wait_for(
            call_next(request), timeout=REQUEST_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "detail": (
                    f"Request timed out after {REQUEST_TIMEOUT_SECONDS:.0f}s. "
                    "Please try again."
                )
            },
        )


@app.exception_handler(SQLAlchemyError)
async def _sqlalchemy_error_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    """Turn any database failure into a clean 503 (never a raw 500)."""
    return JSONResponse(
        status_code=503,
        content={"detail": "Database temporarily unavailable. Please try again."},
    )


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Safety net: no unexpected error ever leaks as an unhandled 500."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


# Cache time-to-live (seconds). CI data refreshes at most daily, so an hour is
# a safe bound; the date component in the key also forces a daily refresh.
CI_CACHE_TTL = 3600
CALC_CACHE_TTL = 3600
# Forecasts refresh at most every 30 minutes (plan requirement); the date
# component in the key also forces a daily refresh as new observations land.
FORECAST_CACHE_TTL = 1800
# Region comparison depends on each region's current CI, which updates at most
# daily; the date component in the key also forces a daily refresh.
COMPARE_CACHE_TTL = 3600


def forecast_assumption(last_real_date: date) -> str:
    """Honesty caveat attached to any wall-clock-remapped forecast/estimate."""
    d = last_real_date.isoformat()
    return (
        f"Forecast values are generated from the model trained on data through "
        f"{d}; dates have been remapped so day 1 aligns with today's wall-clock "
        f"date, under the assumption that grid carbon-intensity patterns from "
        f"that period continue to hold. No new data has been incorporated since "
        f"{d}."
    )


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
    target_date: date | None = Field(
        None,
        description=(
            "Wall-clock date the job is planned to run. Defaults to today. If it "
            "falls on today or within the next 60 days, the region's "
            "wall-clock-remapped forecast for that date is used "
            "(ci_source='forecasted'); otherwise the latest real CI is used "
            "(ci_source='measured')."
        ),
    )


class ForecastPoint(BaseModel):
    """A single day's predicted carbon intensity."""

    date: date
    ci_gco2e_per_kwh: float


class ForecastResponse(BaseModel):
    """Out-of-sample CI forecast for one region, served from its best model."""

    region: str
    model: str
    # Data is daily, so "next 24 hours" == the next 1 day at daily resolution.
    granularity: str
    horizon_days: int
    # First day of the forecast window == today's wall-clock date. The model's
    # step-1 forecast (the day after the last real observation) is remapped to
    # this date; subsequent steps follow day by day. Values are unchanged.
    generated_from: date
    # Last real observation in ci_timeseries. Dates from generated_from are the
    # model's out-of-sample steps relabelled forward from here, so
    # (generated_from - last_real_data) is how far the labels were shifted.
    last_real_data: date
    # Honesty caveat spelling out the remapping assumption (see below).
    assumption: str
    forecast: list[ForecastPoint]


class RegionForecast(BaseModel):
    """One region's forecast within a multi-region ``?compare=true`` response."""

    region: str
    model: str
    generated_from: date
    last_real_data: date
    assumption: str
    forecast: list[ForecastPoint]


class MultiRegionForecastResponse(BaseModel):
    """Forecasts for every region in one call (``GET /forecast/compare``).

    Lets the decision engine get all regions' predicted CI without five separate
    round trips.
    """

    granularity: str
    horizon_days: int
    forecasts: list[RegionForecast]



class CalculateResponse(BaseModel):
    region: str
    model_name: str
    hardware_type: str
    tdp_watts: float
    total_cores: int
    power_per_core_watts: float
    ci_gco2e_per_kwh: float
    ci_timestamp: datetime
    # "forecasted" when the CI came from the wall-clock-remapped forecast for a
    # target date in today..+60d; "measured" when it came from the latest real row.
    ci_source: str
    # Present only when ci_source == "forecasted"; spells out the remap caveat.
    assumption: str | None = None
    runtime_hours: float
    n_cores: int
    usage_factor: float
    memory_gb: float
    pue: float
    energy_kwh: float
    carbon_gco2e: float


class RegionComparison(BaseModel):
    """One region's carbon cost for a given job, relative to the worst region."""

    region: str
    ci_gco2e_per_kwh: float
    ci_timestamp: datetime
    carbon_gco2e: float
    # How much less carbon this region emits than the worst-ranked region, as a
    # percentage (0.0 for the worst region itself, highest for the greenest).
    pct_vs_worst: float


class CompareRegionsResponse(BaseModel):
    """Ranked per-region carbon cost for one job (greenest region first)."""

    model_name: str
    hardware_type: str
    n_cores: int
    runtime_hours: float
    usage_factor: float
    memory_gb: float
    pue: float
    # Energy is region-independent (only CI differs), so it is reported once.
    energy_kwh: float
    greenest_region: str
    worst_region: str
    # "forecasted" when each region's CI came from the wall-clock-remapped
    # forecast (day 1 == today), consistent with /calculate and /forecast;
    # "measured" only as a fallback if forecasting is unavailable.
    ci_source: str
    # Present when ci_source == "forecasted"; spells out the remap caveat.
    assumption: str | None = None
    ranking: list[RegionComparison]


# Weight applied to the spatial (region-shift) term of the decision score.
# Kept at 1.0 so the temporal and spatial savings are compared on equal footing;
# exposed as a constant here so it is easy to tune later.
SPATIAL_WEIGHT = 1.0


class ScheduleRequest(BaseModel):
    """A compute job plus how flexibly it may be scheduled."""

    region: str = Field(
        ..., min_length=2, max_length=10, pattern=r"^[A-Za-z]+$",
        description="The region the job would run in by default.",
    )
    model_name: str = Field(..., min_length=1, max_length=100)
    n_cores: int = Field(
        ...,
        ge=0,
        description=(
            "Number of cores the job requests on the selected device. Must not "
            "exceed the device's total core count."
        ),
    )
    runtime_hours: float = Field(..., gt=0)
    usage_factor: float = Field(1.0, ge=0.0, le=1.0)
    memory_gb: float = Field(0.0, ge=0.0)
    pue: float = Field(DEFAULT_PUE, gt=0.0)
    flexibility_window_hours: int = Field(
        0,
        ge=0,
        le=14 * 24,
        description=(
            "How many hours the job may be deferred. CI data is daily, so only "
            "whole-day multiples (>= 24h) add future forecast slots; a window "
            "shorter than a day allows only a region shift, not a time shift."
        ),
    )
    urgency_flag: bool = Field(
        False,
        description="If true the job must run now; temporal shifting is disabled.",
    )
    job_id: str | None = Field(
        None,
        max_length=64,
        description="Optional caller-supplied job id; one is generated if omitted.",
    )


class ScheduleResponse(BaseModel):
    """The decision engine's recommendation for one job.

    ``predicted_saving_gco2e`` is genuinely computed as the decision score (a CI
    difference in gCO2e/kWh) multiplied by the job's energy use (kWh), and it is
    the sum of ``temporal_saving_gco2e`` and ``spatial_saving_gco2e``.

    KNOWN LIMITATION (additive approximation for combined shifts):
        When BOTH a region shift and a time shift are recommended together,
        ``predicted_saving_gco2e`` is an *additive approximation*
        (temporal_saving + spatial_saving) and may OVERSTATE the true
        single-baseline saving. Both component savings are measured against the
        same run-now / default-region baseline, so summing them double-counts
        that baseline: the true saving of "run in region R at time T" is
        ``CI(default_region, now) - CI(recommended_region, recommended_time)``,
        a single difference, not the sum of two independent differences.

        Measured gap on real data (default SR -> region NER, +1 day): additive
        336.78 vs. true 326.06 gCO2e/kWh, i.e. ~3.3% overstated (~26 gCO2e for a
        ~2.4 kWh job). The overstatement equals the default region's temporal
        drop minus the recommended region's own CI change over the same interval,
        so it is small when either shift dominates but can grow when the temporal
        and spatial savings are both large and comparable.

        This matters for Phase 7's ablation study (spatial-only vs. temporal-only
        vs. full): the "full" number here is additive by construction and should
        NOT be treated as a clean single-baseline saving until the formula is
        revisited. Single-shift recommendations (region-only OR time-only) are
        exact; only the combined case is approximate.

    ``confidence`` is a transparent heuristic, NOT a statistical interval: it is
    the fraction of the job's default (run-now) carbon that the recommendation
    is expected to save, clamped to [0, 1]. The underlying ARIMA forecasts do
    not currently expose prediction intervals, so treat this as a relative
    "how much is at stake" signal rather than a calibrated probability.
    """

    job_id: str
    default_region: str
    recommended_region: str
    recommended_time: datetime | None  # null == run now
    run_now: bool
    urgency_flag: bool
    urgency_weight: float
    flexibility_window_hours: int
    model_name: str
    hardware_type: str
    energy_kwh: float
    current_ci_gco2e_per_kwh: float
    baseline_carbon_gco2e: float
    predicted_saving_gco2e: float = Field(
        ...,
        description=(
            "Estimated carbon saved (gCO2e) = decision score x energy. For a "
            "combined region+time recommendation this is an ADDITIVE "
            "APPROXIMATION (temporal + spatial) that may overstate the true "
            "single-baseline saving by double-counting the run-now baseline; "
            "single-shift recommendations are exact. See the model docstring."
        ),
    )
    temporal_saving_gco2e: float = Field(
        ...,
        description=(
            "Carbon saved (gCO2e) attributed to shifting the run time, measured "
            "against the default region's run-now CI."
        ),
    )
    spatial_saving_gco2e: float = Field(
        ...,
        description=(
            "Carbon saved (gCO2e) attributed to shifting the region, measured "
            "against the default region's run-now CI."
        ),
    )
    confidence: float


class DecisionRecord(BaseModel):
    """One logged scheduling decision, as returned by ``GET /decisions``."""

    job_id: str
    submitted_at: datetime
    default_region: str
    recommended_region: str
    recommended_time: datetime | None  # null == run now
    predicted_saving_gco2e: float


class DecisionsResponse(BaseModel):
    """The most recent scheduling decisions, newest first."""

    count: int
    limit: int
    decisions: list[DecisionRecord]


class HardwareSpec(BaseModel):
    """One selectable hardware device, as returned by ``GET /hardware``."""

    model_name: str
    hardware_type: str
    tdp_watts: float
    total_cores: int


class HardwareListResponse(BaseModel):
    """All hardware devices available for job specs (drives frontend dropdowns)."""

    count: int
    hardware: list[HardwareSpec]


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


@app.get("/forecast/compare", response_model=MultiRegionForecastResponse)
def get_forecast_compare(
    horizon: int = Query(
        60,
        ge=1,
        le=90,
        description=(
            "Number of days to forecast, anchored to today's wall-clock date. "
            "Data is daily; the default of 60 returns a ~2-month outlook "
            "starting today. Note: long horizons flatten toward the seasonal "
            "mean and should be read as trend, not precise daily values."
        ),
    ),
    db: Session = Depends(get_db),
) -> MultiRegionForecastResponse:
    """Return the next ``horizon`` days of predicted CI for every region.

    Forecasts all regions in one call so the decision engine avoids five round
    trips. Each region is served by whichever model won the Phase 4 evaluation
    for it (currently ARIMA for all five). The window is anchored to today's
    wall-clock date (bridging the gap since the last real data). Cached in Redis
    with a 30-minute TTL.
    """
    cache_key = f"forecast:multi:{horizon}:{date.today().isoformat()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return MultiRegionForecastResponse.model_validate_json(cached)

    forecasts: list[RegionForecast] = []
    for reg in BEST_MODEL:
        try:
            model_name, last_observed, forecast_dates, forecast_values = (
                forecast_region(reg, horizon, session=db, from_today=True)
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Forecast model unavailable for region '{reg}'",
            ) from exc
        forecasts.append(
            RegionForecast(
                region=reg,
                model=model_name,
                generated_from=forecast_dates[0] if forecast_dates else date.today(),
                last_real_data=last_observed,
                assumption=forecast_assumption(last_observed),
                forecast=[
                    ForecastPoint(date=d, ci_gco2e_per_kwh=round(v, 3))
                    for d, v in zip(forecast_dates, forecast_values)
                ],
            )
        )

    response = MultiRegionForecastResponse(
        granularity="daily",
        horizon_days=horizon,
        forecasts=forecasts,
    )
    cache_set(cache_key, response.model_dump_json(), FORECAST_CACHE_TTL)
    return response


@app.get("/forecast/{region}", response_model=ForecastResponse)
def get_forecast(
    region: str = Path(..., min_length=2, max_length=10, pattern=r"^[A-Za-z]+$"),
    horizon: int = Query(
        60,
        ge=1,
        le=90,
        description=(
            "Number of days to forecast, anchored to today's wall-clock date. "
            "Data is daily; the default of 60 returns a ~2-month outlook "
            "starting today. Note: long horizons flatten toward the seasonal "
            "mean and should be read as trend, not precise daily values."
        ),
    ),
    db: Session = Depends(get_db),
) -> ForecastResponse:
    """Return the next ``horizon`` days of predicted CI for one region.

    Serves whichever model won the Phase 4 evaluation for the region (currently
    ARIMA for all five). The window is anchored to today's wall-clock date
    (bridging the gap since the last real data). Cached in Redis with a 30-minute
    TTL. Use ``GET /forecast/compare`` to get all regions at once.
    """
    normalized_region = region.upper()
    if normalized_region not in BEST_MODEL:
        raise HTTPException(
            status_code=404, detail=f"Unknown region '{normalized_region}'"
        )

    # Cache key: region + horizon + current date, refreshed at most every 30 min
    # by the TTL and at least once a day by the date component.
    cache_key = f"forecast:{normalized_region}:{horizon}:{date.today().isoformat()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return ForecastResponse.model_validate_json(cached)

    try:
        model_name, last_observed, forecast_dates, forecast_values = forecast_region(
            normalized_region, horizon, session=db, from_today=True
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Forecast model unavailable for region '{normalized_region}'",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    response = ForecastResponse(
        region=normalized_region,
        model=model_name,
        granularity="daily",
        horizon_days=horizon,
        generated_from=forecast_dates[0] if forecast_dates else date.today(),
        last_real_data=last_observed,
        assumption=forecast_assumption(last_observed),
        forecast=[
            ForecastPoint(date=d, ci_gco2e_per_kwh=round(v, 3))
            for d, v in zip(forecast_dates, forecast_values)
        ],
    )
    cache_set(cache_key, response.model_dump_json(), FORECAST_CACHE_TTL)
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
    # new day's CI is not served from a stale calculation). mode="json" so
    # non-primitive fields (e.g. target_date) serialize cleanly.
    payload = request.model_dump(mode="json")
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

    # Pick the carbon intensity to bill the job against. If the job's target date
    # is today or within the next 60 days, use the wall-clock-remapped forecast
    # for that date (day 1 == today); otherwise fall back to the latest real CI.
    today = date.today()
    target = request.target_date or today
    offset = (target - today).days
    if 0 <= offset <= 59:
        try:
            _, last_observed, _f_dates, f_values = forecast_region(
                normalized_region, offset + 1, session=db, from_today=True
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Forecast model unavailable for region '{normalized_region}'",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ci_value = f_values[-1]
        ci_ts = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
        ci_source = "forecasted"
        assumption = forecast_assumption(last_observed)
    else:
        ci_value = latest_ci.ci_gco2e_per_kwh
        ci_ts = latest_ci.timestamp
        ci_source = "measured"
        assumption = None

    try:
        estimate = compute_carbon_footprint(
            runtime_hours=request.runtime_hours,
            n_cores=request.n_cores,
            total_device_tdp_watts=hardware.tdp_watts,
            total_device_cores=hardware.total_cores,
            carbon_intensity_gco2e_per_kwh=ci_value,
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
        ci_gco2e_per_kwh=ci_value,
        ci_timestamp=ci_ts,
        ci_source=ci_source,
        assumption=assumption,
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


@app.get("/compare-regions", response_model=CompareRegionsResponse)
def compare_regions(
    model_name: str = Query(..., min_length=1, max_length=100),
    n_cores: int = Query(
        ...,
        ge=0,
        description=(
            "Number of cores the job requests on the selected device. Must not "
            "exceed the device's total core count."
        ),
    ),
    runtime_hours: float = Query(..., gt=0),
    usage_factor: float = Query(1.0, ge=0.0, le=1.0),
    memory_gb: float = Query(0.0, ge=0.0),
    pue: float = Query(DEFAULT_PUE, gt=0.0),
    db: Session = Depends(get_db),
) -> CompareRegionsResponse:
    """Rank all regions by the carbon cost of running one job today.

    Runs the Green Algorithms calculator for the same job across every region
    using each region's wall-clock-remapped forecast for today (day 1 of the
    forecast), consistent with /calculate and /forecast, and returns the regions
    ordered greenest first, with each region's saving versus the worst region.
    """
    # Cache key: a stable hash of all job inputs + current date, so a new day's
    # CI is not served from a stale ranking.
    payload = {
        "model_name": model_name,
        "n_cores": n_cores,
        "runtime_hours": runtime_hours,
        "usage_factor": usage_factor,
        "memory_gb": memory_gb,
        "pue": pue,
        "_date": date.today().isoformat(),
    }
    inputs_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    cache_key = f"compare:{inputs_hash}"
    cached = cache_get(cache_key)
    if cached is not None:
        return CompareRegionsResponse.model_validate_json(cached)

    hardware = (
        db.execute(
            select(HardwareSpecs).where(HardwareSpecs.model_name == model_name)
        )
        .scalars()
        .first()
    )
    if hardware is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown hardware model '{model_name}'"
        )

    # Compute the job's carbon cost in each region using that region's
    # wall-clock-remapped forecast for TODAY (day 1 of the forecast == today),
    # so this ranking is consistent with /calculate and /forecast about what
    # "today" means. Energy is region-independent, so capture it once.
    today = date.today()
    today_ts = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    entries: list[tuple[str, float, float]] = []  # (region, ci_value, carbon)
    energy_kwh: float | None = None
    last_observed: date | None = None
    for region in REGIONS:
        try:
            _, region_last_obs, _f_dates, f_values = forecast_region(
                region, 1, session=db, from_today=True
            )
        except (FileNotFoundError, ValueError):
            # Region has no model or no data; skip it from the ranking.
            continue
        last_observed = region_last_obs
        ci_value = f_values[0]
        try:
            estimate = compute_carbon_footprint(
                runtime_hours=runtime_hours,
                n_cores=n_cores,
                total_device_tdp_watts=hardware.tdp_watts,
                total_device_cores=hardware.total_cores,
                carbon_intensity_gco2e_per_kwh=ci_value,
                usage_factor=usage_factor,
                memory_gb=memory_gb,
                pue=pue,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        energy_kwh = estimate.energy_kwh
        entries.append((region, ci_value, estimate.carbon_gco2e))

    if not entries:
        raise HTTPException(status_code=404, detail="No CI data found for any region")

    worst_carbon = max(carbon for _, _, carbon in entries)
    ranking = [
        RegionComparison(
            region=region,
            ci_gco2e_per_kwh=round(ci_value, 3),
            ci_timestamp=today_ts,
            carbon_gco2e=round(carbon, 3),
            pct_vs_worst=(
                round(100.0 * (worst_carbon - carbon) / worst_carbon, 3)
                if worst_carbon > 0
                else 0.0
            ),
        )
        for region, ci_value, carbon in entries
    ]
    ranking.sort(key=lambda r: r.carbon_gco2e)  # greenest (lowest carbon) first

    response = CompareRegionsResponse(
        model_name=hardware.model_name,
        hardware_type=hardware.hardware_type,
        n_cores=n_cores,
        runtime_hours=runtime_hours,
        usage_factor=usage_factor,
        memory_gb=memory_gb,
        pue=pue,
        energy_kwh=round(energy_kwh, 6) if energy_kwh is not None else 0.0,
        greenest_region=ranking[0].region,
        worst_region=ranking[-1].region,
        ci_source="forecasted",
        assumption=forecast_assumption(last_observed) if last_observed else None,
        ranking=ranking,
    )
    cache_set(cache_key, response.model_dump_json(), COMPARE_CACHE_TTL)
    return response


@app.post("/schedule", response_model=ScheduleResponse)
def schedule_job(
    request: ScheduleRequest,
    db: Session = Depends(get_db),
) -> ScheduleResponse:
    """Recommend where and when to run a job to minimise its carbon footprint.

    Combines two signals through the decision engine:

    * spatial  - each region's current CI (as in ``GET /compare-regions``), to
      find a greener region right now;
    * temporal - the default region's CI forecast across the flexibility window
      (as in ``GET /forecast/compare``), to find a greener future slot.

    The engine's score (a CI difference in gCO2e/kWh) is multiplied by the job's
    energy use (kWh) to produce a genuine ``predicted_saving_gco2e``. Every
    recommendation is logged to ``scheduling_decisions``. This route is not
    cached, so the log captures every call.
    """
    default_region = request.region.upper()
    if default_region not in BEST_MODEL:
        raise HTTPException(status_code=404, detail=f"Unknown region '{default_region}'")

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

    # Current CI per region (spatial signal), anchored to today's wall-clock
    # forecast (day 1 of each region's remapped forecast) so this matches what
    # /compare-regions and /calculate mean by "today's CI", instead of the last
    # real observation.
    region_ci_now: dict[str, float] = {}
    for reg in REGIONS:
        try:
            _, _reg_last_obs, _fd, fvals = forecast_region(
                reg, 1, session=db, from_today=True
            )
        except (FileNotFoundError, ValueError):
            continue
        region_ci_now[reg] = fvals[0]

    if default_region not in region_ci_now:
        raise HTTPException(
            status_code=404, detail=f"No CI data found for region '{default_region}'"
        )
    ci_now = region_ci_now[default_region]

    # Energy (kWh) and the run-now baseline carbon for the default region. Energy
    # is region-independent, so this single call supplies the kWh used to turn
    # the CI-difference score into an absolute gCO2e saving.
    try:
        baseline = compute_carbon_footprint(
            runtime_hours=request.runtime_hours,
            n_cores=request.n_cores,
            total_device_tdp_watts=hardware.tdp_watts,
            total_device_cores=hardware.total_cores,
            carbon_intensity_gco2e_per_kwh=ci_now,
            usage_factor=request.usage_factor,
            memory_gb=request.memory_gb,
            pue=request.pue,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    energy_kwh = baseline.energy_kwh
    baseline_carbon = baseline.carbon_gco2e

    urgency_weight = 1.0 if request.urgency_flag else 0.0

    # Temporal signal: CI forecast for the default region across the flexibility
    # window. Data is daily, so only whole days of flexibility add future slots.
    # ci_now is already today's forecast (day 1), so request one extra day and
    # drop day 1 to avoid double-counting today; the remaining days are the
    # genuinely future slots the job could wait for.
    n_future_days = min(request.flexibility_window_hours // 24, 14)
    ci_window: list[float] = [ci_now]
    forecast_dates: list[date] = []
    if n_future_days >= 1:
        try:
            _, _, f_dates, f_values = forecast_region(
                default_region, n_future_days + 1, session=db, from_today=True
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Forecast model unavailable for region '{default_region}'",
            ) from exc
        # Drop day 1 (today == ci_now); keep the future days after today.
        ci_window.extend(f_values[1:])
        forecast_dates = list(f_dates[1:])

    result = score_scheduling(
        current_region=default_region,
        region_ci_now=region_ci_now,
        ci_window=ci_window,
        urgency_weight=urgency_weight,
        spatial_weight=SPATIAL_WEIGHT,
    )

    # Split the score into the parts we would actually act on. A time shift only
    # counts when the job can wait (temporal contribution > 0); a region shift
    # only counts when a genuinely greener region exists. This keeps the score,
    # the recommendation, and the logged saving mutually consistent.
    temporal_contrib = result.temporal_saving * (1.0 - urgency_weight)
    spatial_contrib = result.spatial_saving * SPATIAL_WEIGHT

    shift_time = result.best_time_index > 0 and temporal_contrib > 0.0
    shift_region = result.best_region != default_region and spatial_contrib > 0.0

    recommended_region = result.best_region if shift_region else default_region
    recommended_time: datetime | None = None
    if shift_time:
        d = forecast_dates[result.best_time_index - 1]
        recommended_time = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)

    # Genuine saving = CI-difference score (gCO2e/kWh) x job energy (kWh).
    # NOTE (known limitation): when BOTH shift_time and shift_region are true,
    # this sum is an additive approximation and may overstate the true
    # single-baseline saving (it double-counts the run-now baseline). Measured
    # gap on real data ~3.3% (SR->NER, +1d). Single-shift cases are exact. See
    # ScheduleResponse docstring; relevant to Phase 7's ablation study.
    predicted_saving_gco2e = result.score * energy_kwh
    temporal_saving_gco2e = temporal_contrib * energy_kwh
    spatial_saving_gco2e = spatial_contrib * energy_kwh

    run_now = recommended_region == default_region and recommended_time is None

    # Heuristic confidence: fraction of the run-now carbon we expect to save.
    confidence = 0.0
    if baseline_carbon > 0:
        confidence = min(1.0, max(0.0, predicted_saving_gco2e / baseline_carbon))

    job_id = request.job_id or uuid.uuid4().hex
    submitted_at = datetime.now(timezone.utc)

    # Log every decision. record_decision rejects an obviously invalid saving
    # (None/negative, or zero on a real shift) rather than logging a placeholder.
    record_decision(
        db,
        job_id=job_id,
        default_region=default_region,
        recommended_region=recommended_region,
        predicted_saving_gco2e=predicted_saving_gco2e,
        urgency_weight=urgency_weight,
        recommended_time=recommended_time,
        submitted_at=submitted_at,
    )

    return ScheduleResponse(
        job_id=job_id,
        default_region=default_region,
        recommended_region=recommended_region,
        recommended_time=recommended_time,
        run_now=run_now,
        urgency_flag=request.urgency_flag,
        urgency_weight=urgency_weight,
        flexibility_window_hours=request.flexibility_window_hours,
        model_name=hardware.model_name,
        hardware_type=hardware.hardware_type,
        energy_kwh=round(energy_kwh, 6),
        current_ci_gco2e_per_kwh=round(ci_now, 3),
        baseline_carbon_gco2e=round(baseline_carbon, 3),
        predicted_saving_gco2e=round(predicted_saving_gco2e, 3),
        temporal_saving_gco2e=round(temporal_saving_gco2e, 3),
        spatial_saving_gco2e=round(spatial_saving_gco2e, 3),
        confidence=round(confidence, 4),
    )


@app.get("/decisions", response_model=DecisionsResponse)
def get_decisions(
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description="Maximum number of most-recent decisions to return.",
    ),
    db: Session = Depends(get_db),
) -> DecisionsResponse:
    """Return the most recent scheduling decisions, newest first.

    Powers the frontend's decision-history view. Ordered by ``submitted_at``
    descending (with ``id`` as a stable tiebreaker) and capped at ``limit``.
    """
    rows = (
        db.execute(
            select(SchedulingDecision)
            .order_by(
                SchedulingDecision.submitted_at.desc(),
                SchedulingDecision.id.desc(),
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )

    decisions = [
        DecisionRecord(
            job_id=row.job_id,
            submitted_at=row.submitted_at,
            default_region=row.default_region,
            recommended_region=row.recommended_region,
            recommended_time=row.recommended_time,
            predicted_saving_gco2e=row.predicted_saving_gco2e,
        )
        for row in rows
    ]

    return DecisionsResponse(count=len(decisions), limit=limit, decisions=decisions)


@app.get("/hardware", response_model=HardwareListResponse)
def get_hardware(db: Session = Depends(get_db)) -> HardwareListResponse:
    """List every hardware device in ``hardware_specs``.

    Powers the Calculator/Schedule dropdowns and lets the frontend validate that
    a job's requested cores do not exceed the device's total. Ordered by type
    (CPU/GPU) then model name for a stable, readable list.
    """
    rows = (
        db.execute(
            select(HardwareSpecs).order_by(
                HardwareSpecs.hardware_type,
                HardwareSpecs.model_name,
            )
        )
        .scalars()
        .all()
    )

    hardware = [
        HardwareSpec(
            model_name=row.model_name,
            hardware_type=row.hardware_type,
            tdp_watts=row.tdp_watts,
            total_cores=row.total_cores,
        )
        for row in rows
    ]

    return HardwareListResponse(count=len(hardware), hardware=hardware)

