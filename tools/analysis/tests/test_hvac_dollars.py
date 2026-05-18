"""Tests for tools.analysis.hvac_dollars per spec §4."""
from __future__ import annotations

import pytest

from tools.analysis.hvac_dollars import (
    HourlyRateInputs,
    hourly_rate_c_per_kwh,
    hvac_dollars_for_hour,
)


def _april_2026_inputs(lmp_per_mwh: float,
                       dtod_total: float = 4.554) -> HourlyRateInputs:
    return HourlyRateInputs(
        rt_hrl_lmps_per_mwh=lmp_per_mwh,
        pea_c_per_kwh=1.773,
        transmission_c_per_kwh=1.083,
        misc_procurement_c_per_kwh=0.062,
        dtod_total_delivery_c_per_kwh=dtod_total,
        variable_riders_c_per_kwh=1.16,
        carbon_free_credit_c_per_kwh=-3.186,
    )


def test_basic_computation_morning_band():
    inputs = _april_2026_inputs(lmp_per_mwh=25.32, dtod_total=4.554)
    # rate = 2.532 + 1.773 + 1.083 + 0.062 + 4.554 + 1.16 - 3.186 = 7.978
    # cost = 1.5 * 7.978 = 11.967 cents
    assert hvac_dollars_for_hour(1.5, inputs) == pytest.approx(11.967)


def test_zero_kwh_yields_zero_cost():
    inputs = HourlyRateInputs(
        rt_hrl_lmps_per_mwh=50.0,
        pea_c_per_kwh=1.0,
        transmission_c_per_kwh=1.0,
        misc_procurement_c_per_kwh=0.0,
        dtod_total_delivery_c_per_kwh=5.0,
        variable_riders_c_per_kwh=1.0,
        carbon_free_credit_c_per_kwh=0.0,
    )
    assert hvac_dollars_for_hour(0.0, inputs) == 0.0


def test_negative_lmp_handled():
    """During negative-LMP hours, supply is a credit. The math still works."""
    inputs = _april_2026_inputs(lmp_per_mwh=-2.94, dtod_total=3.437)
    # rate = -0.294 + 1.773 + 1.083 + 0.062 + 3.437 + 1.16 - 3.186 = 4.035
    assert hvac_dollars_for_hour(1.0, inputs) == pytest.approx(4.035)


def test_rate_in_cents_per_kwh_not_dollars():
    """Sanity: rate primitives use c/kWh; cost = kWh x c/kWh = c."""
    inputs = _april_2026_inputs(lmp_per_mwh=0.0, dtod_total=0.0)
    inputs2 = HourlyRateInputs(
        rt_hrl_lmps_per_mwh=0.0,
        pea_c_per_kwh=0.0,
        transmission_c_per_kwh=0.0,
        misc_procurement_c_per_kwh=0.0,
        dtod_total_delivery_c_per_kwh=10.0,
        variable_riders_c_per_kwh=0.0,
        carbon_free_credit_c_per_kwh=0.0,
    )
    # rate = 10 c/kWh exactly; cost over 1 kWh = 10 cents
    assert hourly_rate_c_per_kwh(inputs2) == pytest.approx(10.0)
    assert hvac_dollars_for_hour(1.0, inputs2) == pytest.approx(10.0)


def test_lmp_dollars_per_mwh_divided_by_ten_to_cents_per_kwh():
    """1 $/MWh == 0.1 c/kWh. The LMP scale comes in straight from PJM DM2."""
    inputs = HourlyRateInputs(
        rt_hrl_lmps_per_mwh=100.0,
        pea_c_per_kwh=0.0,
        transmission_c_per_kwh=0.0,
        misc_procurement_c_per_kwh=0.0,
        dtod_total_delivery_c_per_kwh=0.0,
        variable_riders_c_per_kwh=0.0,
        carbon_free_credit_c_per_kwh=0.0,
    )
    # 100 $/MWh -> 10 c/kWh; over 1 kWh -> 10 cents
    assert hvac_dollars_for_hour(1.0, inputs) == pytest.approx(10.0)
