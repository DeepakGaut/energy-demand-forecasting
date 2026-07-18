"""Unit tests for the Green Algorithms carbon calculator (backend/carbon.py).

Per-core model:  Pc = total_device_tdp_watts / total_device_cores

    C = t x [ (Nc x Pc x Uc) + (Nm x Pm) ] x PUE x CI x 10^-3

Device TDP / core counts below are taken from real rows in
scripts/seed_hardware_specs.py (chosen so the per-core power divides cleanly
and every numeric expectation can still be hand-verified):

    Intel Xeon Platinum 8480+ : 350 W / 56 cores  -> 6.25   W/core
    AMD EPYC 9654             : 360 W / 96 cores  -> 3.75   W/core
    AMD EPYC 7763             : 280 W / 64 cores  -> 4.375  W/core
    AMD Ryzen 9 7950X         : 170 W / 16 cores  -> 10.625 W/core

CI values are still placeholders on purpose, to isolate the formula from the
database. (A live DB->formula->API run is covered separately as an
integration test.)
"""

import pytest

from backend.carbon import (
    DEFAULT_PUE,
    MEMORY_POWER_PER_GB_W,
    CarbonEstimate,
    compute_carbon_footprint,
)


def test_compute_only_pue_one():
    # Intel Xeon Platinum 8480+ : Pc = 350/56 = 6.25 W/core
    # compute = 8 cores x 6.25 W x 1.0 = 50 W
    # energy = 10h x 50 W x 1.0 x 1e-3 = 0.5 kWh
    # carbon = 0.5 kWh x 475 = 237.5 gCO2e
    result = compute_carbon_footprint(
        runtime_hours=10,
        n_cores=8,
        total_device_tdp_watts=350,
        total_device_cores=56,
        carbon_intensity_gco2e_per_kwh=475,
        usage_factor=1.0,
        memory_gb=0,
        pue=1.0,
    )
    assert isinstance(result, CarbonEstimate)
    assert result.power_per_core_watts == pytest.approx(6.25)
    assert result.energy_kwh == pytest.approx(0.5)
    assert result.carbon_gco2e == pytest.approx(237.5)


def test_memory_only():
    # AMD EPYC 9654 : Pc = 360/96 = 3.75 W/core, but n_cores = 0 -> compute = 0
    # memory = 16 GB x 0.3725 W/GB = 5.96 W
    # energy = 2h x 5.96 W x 1.0 x 1e-3 = 0.01192 kWh
    # carbon = 0.01192 kWh x 1000 = 11.92 gCO2e
    result = compute_carbon_footprint(
        runtime_hours=2,
        n_cores=0,
        total_device_tdp_watts=360,
        total_device_cores=96,
        carbon_intensity_gco2e_per_kwh=1000,
        memory_gb=16,
        pue=1.0,
    )
    assert result.energy_kwh == pytest.approx(0.01192)
    assert result.carbon_gco2e == pytest.approx(11.92)


def test_usage_factor_and_pue():
    # Intel Xeon Platinum 8480+ : Pc = 350/56 = 6.25 W/core
    # compute = 32 cores x 6.25 W x 0.5 = 100 W
    # energy = 1h x 100 W x 1.67 x 1e-3 = 0.167 kWh
    # carbon = 0.167 kWh x 500 = 83.5 gCO2e
    result = compute_carbon_footprint(
        runtime_hours=1,
        n_cores=32,
        total_device_tdp_watts=350,
        total_device_cores=56,
        carbon_intensity_gco2e_per_kwh=500,
        usage_factor=0.5,
        memory_gb=0,
        pue=1.67,
    )
    assert result.power_per_core_watts == pytest.approx(6.25)
    assert result.energy_kwh == pytest.approx(0.167)
    assert result.carbon_gco2e == pytest.approx(83.5)


def test_default_pue_is_applied():
    # No pue argument -> DEFAULT_PUE (1.67) is used.
    # AMD EPYC 7763 : Pc = 280/64 = 4.375 W/core ; all 64 cores -> compute = 280 W
    # energy = 1h x 280 W x 1.67 x 1e-3 = 0.4676 kWh ; carbon = 467.6 gCO2e
    result = compute_carbon_footprint(
        runtime_hours=1,
        n_cores=64,
        total_device_tdp_watts=280,
        total_device_cores=64,
        carbon_intensity_gco2e_per_kwh=1000,
    )
    assert result.power_per_core_watts == pytest.approx(4.375)
    assert result.energy_kwh == pytest.approx(0.4676)
    assert result.carbon_gco2e == pytest.approx(467.6)
    # sanity: DEFAULT_PUE really is what drove it
    assert result.energy_kwh == pytest.approx(1 * 280 * DEFAULT_PUE * 1e-3)


def test_combined_compute_and_memory():
    # Intel Xeon Platinum 8480+ : Pc = 350/56 = 6.25 W/core
    # compute = 28 cores x 6.25 W x 0.8 = 140 W
    # memory = 32 GB x 0.3725 W/GB = 11.92 W
    # power = 151.92 W
    # energy = 4h x 151.92 W x 1.5 x 1e-3 = 0.91152 kWh
    # carbon = 0.91152 kWh x 700 = 638.064 gCO2e
    result = compute_carbon_footprint(
        runtime_hours=4,
        n_cores=28,
        total_device_tdp_watts=350,
        total_device_cores=56,
        carbon_intensity_gco2e_per_kwh=700,
        usage_factor=0.8,
        memory_gb=32,
        pue=1.5,
    )
    assert result.power_per_core_watts == pytest.approx(6.25)
    assert result.energy_kwh == pytest.approx(0.91152)
    assert result.carbon_gco2e == pytest.approx(638.064)


def test_all_cores_requested_is_allowed():
    # AMD EPYC 7763 : n_cores == total_device_cores (64) is a valid full-device request.
    # Pc = 280/64 = 4.375 W/core ; compute = 64 x 4.375 x 1.0 = 280 W (= full TDP)
    # energy = 1h x 280 W x 1.0 x 1e-3 = 0.28 kWh ; carbon = 0.28 x 300 = 84 gCO2e
    result = compute_carbon_footprint(
        runtime_hours=1,
        n_cores=64,
        total_device_tdp_watts=280,
        total_device_cores=64,
        carbon_intensity_gco2e_per_kwh=300,
        pue=1.0,
    )
    assert result.energy_kwh == pytest.approx(0.28)
    assert result.carbon_gco2e == pytest.approx(84.0)


def test_zero_runtime_is_zero_carbon():
    # Intel Xeon Platinum 8480+
    result = compute_carbon_footprint(
        runtime_hours=0,
        n_cores=8,
        total_device_tdp_watts=350,
        total_device_cores=56,
        carbon_intensity_gco2e_per_kwh=900,
    )
    assert result.energy_kwh == 0.0
    assert result.carbon_gco2e == 0.0


def test_memory_coefficient_constant():
    assert MEMORY_POWER_PER_GB_W == pytest.approx(0.3725)


def test_requesting_more_cores_than_device_has_raises():
    # AMD Ryzen 9 7950X has 16 cores; asking for 17 is nonsensical.
    with pytest.raises(ValueError, match="exceeds"):
        compute_carbon_footprint(
            runtime_hours=1,
            n_cores=17,
            total_device_tdp_watts=170,
            total_device_cores=16,
            carbon_intensity_gco2e_per_kwh=500,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"runtime_hours": -1},
        {"n_cores": -1},
        {"total_device_tdp_watts": -5},
        {"total_device_cores": 0},
        {"usage_factor": 1.5},
        {"usage_factor": -0.1},
        {"memory_gb": -8},
        {"pue": 0},
        {"pue": -1},
        {"carbon_intensity_gco2e_per_kwh": -10},
    ],
)
def test_invalid_inputs_raise(kwargs):
    # Base = AMD Ryzen 9 7950X (170 W / 16 cores), a real seed row.
    base = {
        "runtime_hours": 1,
        "n_cores": 4,
        "total_device_tdp_watts": 170,
        "total_device_cores": 16,
        "carbon_intensity_gco2e_per_kwh": 500,
    }
    base.update(kwargs)
    with pytest.raises(ValueError):
        compute_carbon_footprint(**base)
