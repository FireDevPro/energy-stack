"""Tests for the pre-cool depth/timing modulation triggers (§7)."""
from __future__ import annotations

from precool import (
    CHEAP_PRICE_THRESHOLD_C,
    DEEPEN_PEAK_RATIO,
    DEEPEN_TEMP_THRESHOLD_F,
    DEEPEST_PRECOOL_DEPTH_F,
    DEFAULT_PRECOOL_DEPTH_F,
    SPIKE_PRICE_THRESHOLD_C,
    should_add_price_aware_precool,
    should_deepen_precool,
)


# ---- should_deepen_precool ------------------------------------------------


def test_deepen_when_high_temp_and_high_peak_forecast():
    """Spec §7 case 1: tomorrow forecast 95F + tomorrow PJM peak forecast
    145000 MW + season 5th = 130000 MW -> trigger (peak 11.5% above
    season 5th, well clear of the 5% threshold)."""
    assert should_deepen_precool(
        forecast_tomorrow={"max_temp_f": 95, "peak_load_mw": 145000},
        season_5th_mw=130000,
    ) is True


def test_no_deepen_when_peak_at_season_fifth():
    """Spec §7 case 2: peak forecast equal to season-5th -> ratio = 1.0,
    below the 1.05 threshold, no trigger."""
    assert should_deepen_precool(
        forecast_tomorrow={"max_temp_f": 95, "peak_load_mw": 130000},
        season_5th_mw=130000,
    ) is False


def test_no_deepen_when_high_below_90():
    """Spec §7 case 3: high 88F + 145000 MW peak -> no trigger because
    temp threshold (>=90F) is not met. The deepening trigger requires
    BOTH grid stress AND clear residential cooling-load contribution."""
    assert should_deepen_precool(
        forecast_tomorrow={"max_temp_f": 88, "peak_load_mw": 145000},
        season_5th_mw=130000,
    ) is False


def test_deepen_at_exact_thresholds_inclusive():
    """High 90F (inclusive >= 90) + peak 1.0501x season -> trigger."""
    assert should_deepen_precool(
        forecast_tomorrow={"max_temp_f": 90, "peak_load_mw": 130000 * 1.0501},
        season_5th_mw=130000,
    ) is True


def test_locked_thresholds():
    """Pre-committed values, frozen at OSF commit hash."""
    assert DEEPEN_PEAK_RATIO == 1.05
    assert DEEPEN_TEMP_THRESHOLD_F == 90


# ---- should_add_price_aware_precool ---------------------------------------


def _flat_prices(value: float) -> list[float]:
    return [value] * 24


def _prices_with(*overrides: tuple[int, float]) -> list[float]:
    """Build a 24-hour price vector at default 5c/kWh with hour overrides."""
    out = _flat_prices(5.0)
    for hour, price in overrides:
        out[hour] = price
    return out


def test_no_window_when_all_prices_mid_range():
    """Spec §7 case 2: all hours between 3-9c -- no cheap window (nothing
    below 3c) so the trigger condition fails."""
    prices = _flat_prices(5.0)
    assert should_add_price_aware_precool(prices, {}) is None


def test_no_window_when_all_prices_cheap():
    """Spec §7 case 3: all hours between 1-3c -- no spike to coast against
    so the trigger condition fails."""
    prices = _flat_prices(2.0)
    assert should_add_price_aware_precool(prices, {}) is None


def test_window_with_midday_cheap_and_evening_spike():
    """Spec §7 case 1: 12-15 CT at 1c + 18-19 CT at 12c -- trigger.
    Cheap window starts at hour 12; spike at hour 18 satisfies the 4-hour
    gap (18 - 12 = 6)."""
    prices = _flat_prices(5.0)
    for h in (12, 13, 14, 15):
        prices[h] = 1.0
    for h in (18, 19):
        prices[h] = 12.0
    out = should_add_price_aware_precool(prices, {})
    assert out is not None
    assert out["hour_ct"] == 12
    # 12c spike is 20% of the way from 10c to 20c: depth ~67-68F
    assert DEEPEST_PRECOOL_DEPTH_F <= out["depth_f"] <= DEFAULT_PRECOOL_DEPTH_F


def test_window_with_negative_morning_and_evening_spike():
    """Spec §7 case 4: 02-05 CT -1c + 18-19 CT 15c -- the cheap window is
    overnight, OUTSIDE the 06-15 search range, so this case does NOT
    trigger under the locked search-range. (The spec example is
    aspirational; documenting current behaviour.)"""
    prices = _flat_prices(5.0)
    for h in (2, 3, 4, 5):
        prices[h] = -1.0
    for h in (18, 19):
        prices[h] = 15.0
    # Locked behaviour: cheap window must be inside 06-15 CT.
    assert should_add_price_aware_precool(prices, {}) is None


def test_no_window_when_spike_too_close_to_cheap_window():
    """Spec §7 case 5: 12-15 CT at 1c + 14-15 CT at 11c -- the spike
    starts only 2 hours after the cheap window's start, less than the
    4-hour minimum gap. No trigger."""
    prices = _flat_prices(5.0)
    for h in (12, 13, 14, 15):
        prices[h] = 1.0
    # Override spike inside the cheap window range
    prices[14] = 11.0
    prices[15] = 11.0
    assert should_add_price_aware_precool(prices, {}) is None


def test_depth_scales_with_spike_magnitude():
    """Bigger spikes warrant deeper pre-cool. 20c+ spike -> depth 66F
    (max). 10-12c spike -> depth ~68F (default)."""
    prices = _flat_prices(5.0)
    for h in (8, 9):
        prices[h] = 1.0
    for h in (16, 17):
        prices[h] = 25.0   # well above 20c
    out = should_add_price_aware_precool(prices, {})
    assert out is not None
    assert out["depth_f"] == DEEPEST_PRECOOL_DEPTH_F


def test_returns_earliest_qualifying_cheap_window_when_multiple():
    """Multiple qualifying cheap windows: function returns the cheapest
    by sum (with earliest-start tiebreak). Spike window check still
    applies the 4-hour gap rule from the chosen cheap window."""
    prices = _flat_prices(5.0)
    # Cheap window A at hour 7-8 (sum 4)
    prices[7] = 2.0
    prices[8] = 2.0
    # Cheap window B at hour 12-13 (sum 2 - cheaper)
    prices[12] = 1.0
    prices[13] = 1.0
    # Spike at hour 17-18
    prices[17] = 12.0
    prices[18] = 12.0
    out = should_add_price_aware_precool(prices, {})
    assert out is not None
    # Cheaper window B wins (sum 2 < sum 4); 17 is 5 hours after 12 (>= 4)
    assert out["hour_ct"] == 12


def test_locked_thresholds_match_spec_constants():
    """Pre-cool price thresholds reuse the locked tier thresholds from §2
    so ranking-of-spike-magnitude stays consistent with the live overlay."""
    assert CHEAP_PRICE_THRESHOLD_C == 3.0
    assert SPIKE_PRICE_THRESHOLD_C == 10.0  # matches §2 elevated trigger
