"""
backend/carbon.py

Green Algorithms carbon-footprint calculator, implemented as a plain,
dependency-free Python function so the math can be unit-tested in isolation
before it is wired to an API route.

Reference formula (Green Algorithms, Lannelongue et al. 2021):

    C = t x [ (Nc x Pc x Uc) + (Nm x Pm) ] x PUE x CI x 10^-3

where
    C   = carbon footprint            (gCO2e)
    t   = running time                (hours)
    Nc  = number of cores requested   (must be <= the device's total cores)
    Pc  = power draw per core         (Watts)
    Uc  = usage factor of the cores   (0..1)
    Nm  = memory allocated            (GB)
    Pm  = power draw per GB of memory (Watts/GB)
    PUE = data-centre power usage effectiveness (dimensionless)
    CI  = grid carbon intensity       (gCO2e/kWh)
    10^-3 converts Watt-hours to kilo-Watt-hours

Per-core power (Pc) is NOT stored directly. ``hardware_specs`` stores each
device's *total* TDP and its *total core count*, so we derive:

    Pc = total_device_tdp_watts / total_device_cores

and the job requests some number of those cores (Nc), which must not exceed
the device's total core count.
"""

from __future__ import annotations

from dataclasses import dataclass

# Green Algorithms global defaults.
DEFAULT_PUE = 1.67          # global average data-centre PUE
MEMORY_POWER_PER_GB_W = 0.3725  # Watts drawn per GB of allocated memory


@dataclass(frozen=True)
class CarbonEstimate:
    """Result of a carbon-footprint calculation."""

    power_per_core_watts: float
    energy_kwh: float
    carbon_gco2e: float


def compute_carbon_footprint(
    runtime_hours: float,
    n_cores: int,
    total_device_tdp_watts: float,
    total_device_cores: int,
    carbon_intensity_gco2e_per_kwh: float,
    usage_factor: float = 1.0,
    memory_gb: float = 0.0,
    pue: float = DEFAULT_PUE,
    memory_power_per_gb_watts: float = MEMORY_POWER_PER_GB_W,
) -> CarbonEstimate:
    """Compute the energy use and carbon footprint of a compute job.

    Per-core power is derived as ``total_device_tdp_watts / total_device_cores``.
    Returns a :class:`CarbonEstimate` with the derived per-core power (W), the
    energy drawn (kWh) and the resulting carbon footprint (gCO2e).

    Raises ``ValueError`` on inputs that are physically meaningless (negative
    quantities, a usage factor outside ``0..1``, a non-positive PUE, a
    non-positive device core count, or a core request that exceeds the number
    of cores the device actually has).
    """
    if runtime_hours < 0:
        raise ValueError("runtime_hours must be non-negative")
    if total_device_cores <= 0:
        raise ValueError("total_device_cores must be greater than 0")
    if total_device_tdp_watts < 0:
        raise ValueError("total_device_tdp_watts must be non-negative")
    if n_cores < 0:
        raise ValueError("n_cores must be non-negative")
    if n_cores > total_device_cores:
        raise ValueError(
            f"n_cores ({n_cores}) exceeds the device's total core count "
            f"({total_device_cores})"
        )
    if not 0.0 <= usage_factor <= 1.0:
        raise ValueError("usage_factor must be between 0 and 1")
    if memory_gb < 0:
        raise ValueError("memory_gb must be non-negative")
    if pue <= 0:
        raise ValueError("pue must be greater than 0")
    if carbon_intensity_gco2e_per_kwh < 0:
        raise ValueError("carbon_intensity_gco2e_per_kwh must be non-negative")

    power_per_core_w = total_device_tdp_watts / total_device_cores
    compute_power_w = n_cores * power_per_core_w * usage_factor
    memory_power_w = memory_gb * memory_power_per_gb_watts

    energy_kwh = runtime_hours * (compute_power_w + memory_power_w) * pue * 1e-3
    carbon_gco2e = energy_kwh * carbon_intensity_gco2e_per_kwh

    return CarbonEstimate(
        power_per_core_watts=power_per_core_w,
        energy_kwh=energy_kwh,
        carbon_gco2e=carbon_gco2e,
    )
