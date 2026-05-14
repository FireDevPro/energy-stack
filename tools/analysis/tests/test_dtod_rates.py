"""Tests for tools/analysis/dtod_rates.py.

Spec: docs/plans/sced-rebaseline-spec-2026-05-13.md §8 (DTOD table).

Test values are hand-pinned from the spec table, NOT derived from the
implementation module. Per the plan, expected values must be
independent of implementation code so a buggy rate table would surface
as a test failure rather than self-confirming.
"""
from __future__ import annotations

import pytest

from tools.analysis.dtod_rates import (
    IEDT_C_PER_KWH,
    TARIFF_EFFECTIVE_DATE,
    TARIFF_SOURCE,
    dtod_resultant_c_per_kwh,
    dtod_total_delivery_c_per_kwh,
)


# ---- IEDT (Illinois Electricity Distribution Tax) ------------------------


def test_iedt_constant_pinned_to_spec():
    """Spec §8: IEDT is a flat 0.126 ¢/kWh applied regardless of hour."""
    assert IEDT_C_PER_KWH == pytest.approx(0.126)


def test_tariff_source_pinned():
    """Provenance: the rate table comes from ComEd's April 2026 tariff
    worksheet, Residential Single Family Without Electric Space Heat
    (DTOD class). Frozen at OSF filing — changes are protocol
    deviations."""
    assert "April 2026" in TARIFF_SOURCE
    assert "DTOD" in TARIFF_SOURCE
    assert TARIFF_EFFECTIVE_DATE == "2026-04-25"


# ---- Resultant ¢/kWh by hour, hand-pinned from spec §8 ------------------


@pytest.mark.parametrize("hour_ct,expected_c_per_kwh", [
    # Overnight (21:00-06:00 wrap): 3.311
    (0,  3.311),
    (1,  3.311),
    (2,  3.311),
    (3,  3.311),
    (4,  3.311),
    (5,  3.311),
    # Morning (06:00-13:00): 4.428
    (6,  4.428),
    (7,  4.428),
    (8,  4.428),
    (9,  4.428),
    (10, 4.428),
    (11, 4.428),
    (12, 4.428),
    # Mid-Day Peak (13:00-19:00): 11.727
    (13, 11.727),
    (14, 11.727),
    (15, 11.727),
    (16, 11.727),
    (17, 11.727),
    (18, 11.727),
    # Evening (19:00-21:00): 4.142
    (19, 4.142),
    (20, 4.142),
    # Overnight (21:00-06:00 wrap, late half): 3.311
    (21, 3.311),
    (22, 3.311),
    (23, 3.311),
])
def test_resultant_by_hour_covers_full_24h_with_spec_values(hour_ct, expected_c_per_kwh):
    assert dtod_resultant_c_per_kwh(hour_ct) == pytest.approx(expected_c_per_kwh)


def test_resultant_boundary_06_is_morning_not_overnight():
    """Spec §8: Morning starts at 06:00 inclusive."""
    assert dtod_resultant_c_per_kwh(6) == pytest.approx(4.428)


def test_resultant_boundary_13_is_mid_day_peak_not_morning():
    """Spec §8: Mid-Day Peak starts at 13:00 inclusive."""
    assert dtod_resultant_c_per_kwh(13) == pytest.approx(11.727)


def test_resultant_boundary_19_is_evening_not_mid_day_peak():
    """Spec §8: Evening starts at 19:00 inclusive."""
    assert dtod_resultant_c_per_kwh(19) == pytest.approx(4.142)


def test_resultant_boundary_21_is_overnight_not_evening():
    """Spec §8: Overnight starts at 21:00 inclusive."""
    assert dtod_resultant_c_per_kwh(21) == pytest.approx(3.311)


# ---- Total delivery ¢/kWh (resultant + IEDT) -----------------------------


@pytest.mark.parametrize("hour_ct,expected_total_c_per_kwh", [
    # Spec §8 right-most column:
    (10, 4.554),    # Morning: 4.428 + 0.126
    (15, 11.853),   # Mid-Day Peak: 11.727 + 0.126
    (20, 4.268),    # Evening: 4.142 + 0.126
    (22, 3.437),    # Overnight: 3.311 + 0.126
    (3,  3.437),    # Overnight early: same
])
def test_total_delivery_matches_spec_table_right_column(hour_ct, expected_total_c_per_kwh):
    assert dtod_total_delivery_c_per_kwh(hour_ct) == pytest.approx(expected_total_c_per_kwh)


# ---- Out-of-range handling -----------------------------------------------


def test_hour_24_raises():
    with pytest.raises(ValueError):
        dtod_resultant_c_per_kwh(24)


def test_negative_hour_raises():
    with pytest.raises(ValueError):
        dtod_resultant_c_per_kwh(-1)


def test_total_delivery_negative_hour_raises():
    with pytest.raises(ValueError):
        dtod_total_delivery_c_per_kwh(-1)
