"""
tests/test_forecast_api.py

Integration test for ``GET /forecast/{region}`` (Phase 4 Day 5).

Exercises the real serving path end to end:
    FastAPI -> forecasting.serve.forecast_region -> saved ARIMA model
             -> ci_timeseries (PostgreSQL), with Redis caching in front.

It therefore needs the Docker stack (postgres + redis) up and the ARIMA models
present under ``models/arima/``. If those dependencies are unreachable the whole
module is skipped, so the pure-unit suite (test_carbon.py) still runs standalone.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from forecasting.features import REGIONS

client = TestClient(app)


def _deps_available() -> bool:
    """True only if the API's postgres + redis dependencies are reachable."""
    try:
        return client.get("/health").status_code == 200
    except Exception:  # noqa: BLE001 - any failure means deps are unavailable
        return False


pytestmark = pytest.mark.skipif(
    not _deps_available(),
    reason="backend dependencies (postgres/redis) not reachable",
)


@pytest.mark.parametrize("region", REGIONS)
def test_forecast_response_shape(region: str) -> None:
    """Every region returns a well-formed single-day forecast from ARIMA."""
    resp = client.get(f"/forecast/{region}", params={"horizon": 1})
    assert resp.status_code == 200

    body = resp.json()
    assert body["region"] == region
    assert body["model"] == "ARIMA"
    assert body["granularity"] == "daily"
    assert body["horizon_days"] == 1
    assert "generated_from" in body
    # Window is anchored to today's wall-clock date and honestly reports the last
    # real observation, which is at or before the forecast origin.
    assert body["last_real_data"] <= body["generated_from"]

    forecast = body["forecast"]
    assert isinstance(forecast, list)
    assert len(forecast) == 1

    point = forecast[0]
    assert set(point) == {"date", "ci_gco2e_per_kwh"}
    assert isinstance(point["ci_gco2e_per_kwh"], (int, float))
    # CI is strictly positive; day 1 of a wall-clock forecast == the origin.
    assert point["ci_gco2e_per_kwh"] > 0
    assert point["date"] == body["generated_from"]


def test_forecast_multi_day_horizon() -> None:
    """A horizon > 1 returns that many consecutive, ordered daily points."""
    horizon = 7
    resp = client.get("/forecast/NR", params={"horizon": horizon})
    assert resp.status_code == 200

    body = resp.json()
    assert body["horizon_days"] == horizon

    forecast = body["forecast"]
    assert len(forecast) == horizon
    dates = [p["date"] for p in forecast]
    assert dates == sorted(dates)
    assert len(set(dates)) == horizon


def test_forecast_default_horizon_is_60() -> None:
    """The calculator flow default is a ~2-month wall-clock-anchored outlook."""
    resp = client.get("/forecast/NR")
    assert resp.status_code == 200
    body = resp.json()
    assert body["horizon_days"] == 60
    assert len(body["forecast"]) == 60
    dates = [p["date"] for p in body["forecast"]]
    assert dates == sorted(dates)
    assert len(set(dates)) == 60
    # First forecast day is the anchor (today); all points are on/after it.
    assert dates[0] == body["generated_from"]


def test_forecast_unknown_region_404() -> None:
    """A syntactically valid but unknown region is a clean 404, not a 500."""
    resp = client.get("/forecast/ZZ")
    assert resp.status_code == 404


def test_forecast_compare_all_regions() -> None:
    """GET /forecast/compare returns a forecast for every region in one call."""
    resp = client.get("/forecast/compare", params={"horizon": 1})
    assert resp.status_code == 200

    body = resp.json()
    assert "requested_region" not in body
    assert body["granularity"] == "daily"
    assert body["horizon_days"] == 1

    forecasts = body["forecasts"]
    assert {f["region"] for f in forecasts} == set(REGIONS)
    for f in forecasts:
        assert f["model"] == "ARIMA"
        assert "generated_from" in f
        assert f["last_real_data"] <= f["generated_from"]
        assert len(f["forecast"]) == 1
        point = f["forecast"][0]
        assert set(point) == {"date", "ci_gco2e_per_kwh"}
        assert point["ci_gco2e_per_kwh"] > 0


def test_forecast_compare_multi_day_horizon() -> None:
    """GET /forecast/compare honours the horizon for every region."""
    horizon = 5
    resp = client.get("/forecast/compare", params={"horizon": horizon})
    assert resp.status_code == 200

    body = resp.json()
    assert body["horizon_days"] == horizon
    assert {f["region"] for f in body["forecasts"]} == set(REGIONS)
    for f in body["forecasts"]:
        assert len(f["forecast"]) == horizon



def test_forecast_horizon_out_of_range_422() -> None:
    """Horizon outside the allowed 1..90 range is rejected by validation."""
    assert client.get("/forecast/NR", params={"horizon": 0}).status_code == 422
    assert client.get("/forecast/NR", params={"horizon": 99}).status_code == 422
