"""Per-hour HVAC$ computation per
docs/plans/sced-rebaseline-spec-2026-05-13.md §4.

Formula (one hour, returns COST IN CENTS):

    HVAC$_hour = HVAC_kWh_hour x (
          rt_hrl_lmps_per_mwh / 10               # PJM settled hourly LMP, c/kWh
        + pea_c_per_kwh                          # Purchased Electricity Adjustment
        + transmission_c_per_kwh                 # Transmission Services Charge
        + misc_procurement_c_per_kwh             # Misc Procurement Components
        + dtod_total_delivery_c_per_kwh          # DTOD resultant + IEDT
        + variable_riders_c_per_kwh              # Sum of per-kWh riders
        + carbon_free_credit_c_per_kwh           # CFE Resource Adjustment (negative)
    )

The rt_hrl_lmps_per_mwh -> c/kWh conversion is 1/10: 1 $/MWh = 0.1 c/kWh.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HourlyRateInputs:
    """All rate components in c/kWh except ``rt_hrl_lmps_per_mwh``.

    The LMP is given in $/MWh because that is the raw form returned by
    PJM DataMiner2 ``rt_hrl_lmps``. ``carbon_free_credit_c_per_kwh`` is
    expected to be negative for the typical April-2026 snapshot
    (-3.186 c/kWh).
    """

    rt_hrl_lmps_per_mwh: float
    pea_c_per_kwh: float
    transmission_c_per_kwh: float
    misc_procurement_c_per_kwh: float
    dtod_total_delivery_c_per_kwh: float
    variable_riders_c_per_kwh: float
    carbon_free_credit_c_per_kwh: float


def hourly_rate_c_per_kwh(inputs: HourlyRateInputs) -> float:
    """Bill-canonical per-kWh rate at one hour, in c/kWh.

    Used both for HVAC$ computation and for the rate primitive
    consumed by cost-matched exclusion (spec §5).
    """
    supply_lmp_c = inputs.rt_hrl_lmps_per_mwh / 10.0
    return (
        supply_lmp_c
        + inputs.pea_c_per_kwh
        + inputs.transmission_c_per_kwh
        + inputs.misc_procurement_c_per_kwh
        + inputs.dtod_total_delivery_c_per_kwh
        + inputs.variable_riders_c_per_kwh
        + inputs.carbon_free_credit_c_per_kwh
    )


def hvac_dollars_for_hour(hvac_kwh: float, inputs: HourlyRateInputs) -> float:
    """Returns HVAC cost for the hour in CENTS. Callers convert to dollars
    by dividing by 100. The kWh bucket already carries the 1-hour time
    conversion (W -> kWh via mean(power_w) * 1h).
    """
    return hvac_kwh * hourly_rate_c_per_kwh(inputs)
