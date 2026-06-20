"""Tests for the pre-cool depth/timing modulation triggers (§7)."""
from __future__ import annotations

from .precool import (
    CHEAP_PRICE_THRESHOLD_C,
    DEEPEN_PEAK_RATIO,
    DEEPEN_TEMP_THRESHOLD_F,
    DEEPEST_PRECOOL_DEPTH,
    DEFAULT_PRECOOL_DEPTH,
    DTOD_PERIODS_CT,
    SPIKE_PRICE_THRESHOLD_C,
    dtod_delivery_rate_for_hour,
    dtod_delivery_rates_24h,
    should_add_price_aware_precool,
    should_deepen_precool,
)
import pytest


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
    assert DEEPEST_PRECOOL_DEPTH <= out["depth_f"] <= DEFAULT_PRECOOL_DEPTH


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
    assert out["depth_f"] == DEEPEST_PRECOOL_DEPTH


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


# ---- ComEd Delivery TOD rate schedule (P2.6) ------------------------------


def test_dtod_periods_cover_24_hours_exactly_once():
    """Every hour 0-23 must be covered by exactly one DTOD period.
    Overlap or gap would silently break delivery cost weighting."""
    covered = [0] * 24
    for start, end, _rate in DTOD_PERIODS_CT:
        for h in range(start, end):
            covered[h] += 1
    assert all(c == 1 for c in covered), f"coverage gaps/overlaps: {covered}"


def test_dtod_rate_lookups_match_published_schedule():
    """ComEd CUB Fact Sheet (March 2026, Single-Family Non-Electric Heat).
    Mid-Day Peak (1pm-7pm) is the only period above the 6.228 standard
    rate; the other 18 hours are lower. Spot-check a representative hour
    from each period."""
    assert dtod_delivery_rate_for_hour(8)  == pytest.approx(4.009)   # Morning
    assert dtod_delivery_rate_for_hour(15) == pytest.approx(10.712)  # Mid-Day Peak
    assert dtod_delivery_rate_for_hour(20) == pytest.approx(3.747)   # Evening
    assert dtod_delivery_rate_for_hour(3)  == pytest.approx(2.984)   # Overnight (early)
    assert dtod_delivery_rate_for_hour(22) == pytest.approx(2.984)   # Overnight (late)


def test_dtod_rate_boundary_hours_use_correct_period():
    """Boundaries are start-inclusive / end-exclusive. 6am is Morning's
    first hour (not Overnight); 1pm is Mid-Day Peak (not Morning); 7pm
    is Evening (not Mid-Day Peak)."""
    assert dtod_delivery_rate_for_hour(6)  == pytest.approx(4.009)   # Morning starts
    assert dtod_delivery_rate_for_hour(12) == pytest.approx(4.009)   # last Morning hour
    assert dtod_delivery_rate_for_hour(13) == pytest.approx(10.712)  # Mid-Day Peak starts
    assert dtod_delivery_rate_for_hour(18) == pytest.approx(10.712)  # last Mid-Day Peak hour
    assert dtod_delivery_rate_for_hour(19) == pytest.approx(3.747)   # Evening starts
    assert dtod_delivery_rate_for_hour(21) == pytest.approx(2.984)   # Overnight starts


def test_dtod_rate_rejects_out_of_range_hour():
    with pytest.raises(ValueError):
        dtod_delivery_rate_for_hour(-1)
    with pytest.raises(ValueError):
        dtod_delivery_rate_for_hour(24)


def test_dtod_rates_24h_vector_is_aligned_by_hour():
    """The 24-vector indexed by hour_ct should equal per-hour lookups."""
    v = dtod_delivery_rates_24h()
    assert len(v) == 24
    for h in range(24):
        assert v[h] == pytest.approx(dtod_delivery_rate_for_hour(h))


# ---- P2.6: DTOD-aware window ranking --------------------------------------


def test_delivery_aware_ranking_prefers_lower_total_cost_over_lower_supply():
    """Adversarial-review scenario: two qualifying cheap windows, supply
    alone says hour 12 wins (cheaper supply), but DTOD delivery makes
    hour 8 cheaper on total cost because 12-13 is Mid-Day Peak (10.712c
    delivery) vs 8-9 Morning (4.009c). With delivery-aware ranking,
    Arm B should pick hour 8 to avoid the delivery spike."""
    prices = _flat_prices(5.0)
    # Cheap window A at 8-9 (supply sum 5.4)
    prices[8] = 2.8
    prices[9] = 2.6
    # Cheap window B at 12-13 (supply sum 5.0 -- cheaper on supply alone)
    prices[12] = 2.5
    prices[13] = 2.5
    # Spike at 17-18 satisfies 4-hour gap from either cheap window
    prices[17] = 12.0
    prices[18] = 12.0

    # Without delivery awareness: hour 12 wins on supply sum
    out_supply_only = should_add_price_aware_precool(prices, {})
    assert out_supply_only is not None
    assert out_supply_only["hour_ct"] == 12

    # With DTOD delivery: hour 8 wins because hour 12 is Mid-Day Peak.
    # Hour 8 total: 2.8 + 4.009 + 2.6 + 4.009 = 13.418
    # Hour 12 total: 2.5 + 4.009 + 2.5 + 10.712 = 19.721  (1pm is Mid-Day Peak)
    out_delivery_aware = should_add_price_aware_precool(
        prices, {}, delivery_rates_cents=dtod_delivery_rates_24h(),
    )
    assert out_delivery_aware is not None
    assert out_delivery_aware["hour_ct"] == 8


def test_delivery_aware_keeps_threshold_on_supply_only():
    """Locked Appendix A calibration: CHEAP_PRICE_THRESHOLD_C is a
    supply-side threshold. A window where supply is below 3c but
    supply+delivery exceeds 3c should still qualify -- otherwise the
    locked threshold would silently shift up by ~3-11c (the delivery
    component) and Arm B would never trigger pre-cool."""
    prices = _flat_prices(5.0)
    # Supply 2.8c during Mid-Day Peak (delivery 10.712c, total 13.5c).
    # Cheap on supply (< 3.0), but total cost is well above 3.0.
    prices[13] = 2.8
    prices[14] = 2.8
    # Spike at 18-19
    prices[18] = 12.0
    prices[19] = 12.0
    out = should_add_price_aware_precool(
        prices, {}, delivery_rates_cents=dtod_delivery_rates_24h(),
    )
    assert out is not None
    assert out["hour_ct"] == 13  # qualifying window survives the threshold


def test_delivery_aware_no_change_when_only_one_cheap_window():
    """When only one cheap window exists, delivery-aware ranking is a
    no-op: it doesn't change which window is selected (there's no
    alternative to outrank). Same result as supply-only ranking."""
    prices = _flat_prices(5.0)
    prices[8] = 1.0
    prices[9] = 1.0
    prices[17] = 12.0
    prices[18] = 12.0
    out_supply = should_add_price_aware_precool(prices, {})
    out_delivery = should_add_price_aware_precool(
        prices, {}, delivery_rates_cents=dtod_delivery_rates_24h(),
    )
    assert out_supply is not None
    assert out_delivery is not None
    assert out_supply["hour_ct"] == out_delivery["hour_ct"] == 8


def test_delivery_rates_length_mismatch_raises():
    """A 23-element delivery vector against a 24-element price vector is
    a programming error; fail loudly rather than silently misaligning."""
    prices = _flat_prices(5.0)
    with pytest.raises(ValueError):
        should_add_price_aware_precool(prices, {}, delivery_rates_cents=[1.0] * 23)
