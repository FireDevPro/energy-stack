"""Pre-cool depth/timing modulation (§7).

Two layers on top of the day-type schedule's baseline pre-cool:

  1. **Forecast 5CP-risk deepening** -- escalates a HOT day to
     HOT_STREAK_DAY1 (03:00 start at 66F) when tomorrow's PJM peak
     forecast exceeds the season-to-date 5th highest by 5%+ AND the
     forecast high reaches 90F. Triggers on single-day risk, in addition
     to the existing multi-day-heat HOT_STREAK_DAY1 trigger.

  2. **Day-ahead price-modulated pre-cool** -- proposes a supplementary
     pre-cool window when tomorrow's day-ahead price forecast shows a
     cheap morning window followed by an evening spike. Captures
     shoulder-season days where the temperature-driven base decision
     would have skipped pre-cool entirely.

Both functions are pure transforms; callers fetch inputs from InfluxDB
(NWS forecast for tomorrow's high, PJM load forecast for tomorrow's
peak, ComEd day-ahead LMP for the 24-hour price vector) and pass them in.

Locked thresholds per EXPERIMENT_DESIGN.md Appendix A.
"""
from __future__ import annotations

from typing import Optional


# ---- Thresholds (locked) --------------------------------------------------

# Forecast 5CP-risk deepening
DEEPEN_PEAK_RATIO = 1.05
DEEPEN_TEMP_THRESHOLD_F = 90

# Price-aware pre-cool
CHEAP_PRICE_THRESHOLD_C = 3.0       # below this counts as "cheap"
CHEAP_WINDOW_HOURS = 2              # need at least N consecutive cheap hours
SPIKE_PRICE_THRESHOLD_C = 10.0      # locked elevated-tier trigger from §2
SPIKE_WINDOW_HOURS = 1              # need at least N consecutive spike hours
MIN_GAP_BETWEEN_CHEAP_AND_SPIKE_HOURS = 4
CHEAP_WINDOW_SEARCH_START_HOUR_CT = 6
CHEAP_WINDOW_SEARCH_END_HOUR_CT = 15  # exclusive

# Pre-cool depth
DEFAULT_PRECOOL_DEPTH_F = 68
DEEPEST_PRECOOL_DEPTH_F = 66


def should_deepen_precool(
    forecast_tomorrow: dict,
    season_5th_mw: float,
) -> bool:
    """True when tomorrow's forecast warrants the HOT_STREAK_DAY1 deeper
    pre-cool schedule (03:00 start at 66F) even without a multi-day heat
    streak.

    Inputs:
      forecast_tomorrow["max_temp_f"] -- NWS forecast high for tomorrow.
      forecast_tomorrow["peak_load_mw"] -- PJM load forecast peak for
                                            tomorrow (from pjm.load_forecast).
      season_5th_mw -- Current season-to-date 5th-highest hourly load.
    """
    peak_mw = forecast_tomorrow.get("peak_load_mw") or 0
    max_temp = forecast_tomorrow.get("max_temp_f") or 0
    return (
        peak_mw > season_5th_mw * DEEPEN_PEAK_RATIO
        and max_temp >= DEEPEN_TEMP_THRESHOLD_F
    )


def _find_consecutive_window_below(
    prices: list[float], threshold: float, min_hours: int,
    *, start_hour: int = 0, end_hour: Optional[int] = None,
) -> Optional[tuple[int, int]]:
    """Return ``(start_hour, end_hour_exclusive)`` of the cheapest
    consecutive window of length >= ``min_hours`` whose every hour is
    below ``threshold``, searched within the half-open hour range
    ``[start_hour, end_hour)``.

    Returns None if no qualifying window exists. When multiple qualifying
    windows exist, the cheapest by sum is returned (tiebreak: earliest
    start hour)."""
    if end_hour is None:
        end_hour = len(prices)
    end_hour = min(end_hour, len(prices))

    best: Optional[tuple[int, int]] = None
    best_sum = float("inf")

    h = start_hour
    while h <= end_hour - min_hours:
        # Find the longest run starting at h whose every hour is below
        # threshold. The window we score is the first ``min_hours`` of
        # that run (so we report the start of the cheapest qualifying
        # window, not necessarily the longest).
        window = prices[h:h + min_hours]
        if all(p < threshold for p in window):
            window_sum = sum(window)
            if window_sum < best_sum:
                best_sum = window_sum
                best = (h, h + min_hours)
        h += 1
    return best


def _find_consecutive_window_above(
    prices: list[float], threshold: float, min_hours: int,
    *, start_hour: int = 0, end_hour: Optional[int] = None,
) -> Optional[tuple[int, int]]:
    """Return the earliest ``(start_hour, end_hour_exclusive)`` of any
    consecutive window of length >= ``min_hours`` whose every hour
    exceeds ``threshold``."""
    if end_hour is None:
        end_hour = len(prices)
    end_hour = min(end_hour, len(prices))

    h = start_hour
    while h <= end_hour - min_hours:
        window = prices[h:h + min_hours]
        if all(p >= threshold for p in window):
            return h, h + min_hours
        h += 1
    return None


def should_add_price_aware_precool(
    day_ahead_prices: list[float],
    forecast_tomorrow: dict,  # accepted but not currently consumed; reserved
                              # for future depth-scaling against forecast high.
) -> Optional[dict]:
    """Identify a price-aware pre-cool window for tomorrow.

    Returns ``{"hour_ct": int, "depth_f": int}`` when both:

      * At least ``CHEAP_WINDOW_HOURS`` consecutive cheap hours
        (< ``CHEAP_PRICE_THRESHOLD_C``) exist within the
        06:00-15:00 CT search range, AND
      * At least ``SPIKE_WINDOW_HOURS`` consecutive spike hours
        (>= ``SPIKE_PRICE_THRESHOLD_C``) exist starting at least
        ``MIN_GAP_BETWEEN_CHEAP_AND_SPIKE_HOURS`` after the cheap
        window's start.

    The cheap window is scored by sum (cheapest wins); the spike window
    is the first qualifying one after the gap requirement. ``depth_f``
    scales with spike magnitude: 68F base, dropping toward 66F as the
    spike's max price rises (capped at ``DEEPEST_PRECOOL_DEPTH_F``).

    Returns None when either window is missing or the gap is too short.
    """
    if len(day_ahead_prices) < 24:
        return None  # need a full 24-hour day for the search
    _ = forecast_tomorrow  # reserved for future depth modulation

    cheap_window = _find_consecutive_window_below(
        day_ahead_prices,
        CHEAP_PRICE_THRESHOLD_C,
        CHEAP_WINDOW_HOURS,
        start_hour=CHEAP_WINDOW_SEARCH_START_HOUR_CT,
        end_hour=CHEAP_WINDOW_SEARCH_END_HOUR_CT,
    )
    if cheap_window is None:
        return None
    cheap_start, _cheap_end = cheap_window
    earliest_spike_start = cheap_start + MIN_GAP_BETWEEN_CHEAP_AND_SPIKE_HOURS

    spike_window = _find_consecutive_window_above(
        day_ahead_prices,
        SPIKE_PRICE_THRESHOLD_C,
        SPIKE_WINDOW_HOURS,
        start_hour=earliest_spike_start,
    )
    if spike_window is None:
        return None

    spike_start, spike_end = spike_window
    spike_max = max(day_ahead_prices[spike_start:spike_end])

    # Depth scaling: 68F at the trigger threshold (10c), 66F when the
    # spike doubles to 20c+. Linear interpolation, clamped.
    if spike_max >= 20.0:
        depth_f = DEEPEST_PRECOOL_DEPTH_F
    elif spike_max <= SPIKE_PRICE_THRESHOLD_C:
        depth_f = DEFAULT_PRECOOL_DEPTH_F
    else:
        # 10c -> 68F, 20c -> 66F linearly
        scaled = DEFAULT_PRECOOL_DEPTH_F - (
            (spike_max - SPIKE_PRICE_THRESHOLD_C)
            / (20.0 - SPIKE_PRICE_THRESHOLD_C)
        ) * (DEFAULT_PRECOOL_DEPTH_F - DEEPEST_PRECOOL_DEPTH_F)
        depth_f = int(round(scaled))

    return {"hour_ct": cheap_start, "depth_f": depth_f}
