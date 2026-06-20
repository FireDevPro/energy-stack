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
     would have skipped pre-cool entirely. On ComEd Delivery TOD the
     decision also incorporates per-hour delivery charges (P2.6
     adversarial-review fix) so supply-cheap-but-delivery-expensive
     windows aren't preferred over a slightly-more-supply but
     delivery-cheap alternative.

Both functions are pure transforms; callers fetch inputs from InfluxDB
(NWS forecast for tomorrow's high, PJM load forecast for tomorrow's
peak, ComEd day-ahead LMP for the 24-hour price vector) and pass them in.

Locked thresholds per EXPERIMENT_DESIGN.md Appendix A.
"""
from __future__ import annotations

from typing import Any, Optional


# ---- Thresholds (locked) --------------------------------------------------

# Forecast 5CP-risk deepening
DEEPEN_PEAK_RATIO = 1.05
DEEPEN_TEMP_THRESHOLD_F = 90

# Price-aware pre-cool
CHEAP_PRICE_THRESHOLD_C = 3.0       # below this counts as "cheap" (supply only)
CHEAP_WINDOW_HOURS = 2              # need at least N consecutive cheap hours
SPIKE_PRICE_THRESHOLD_C = 10.0      # locked elevated-tier trigger from §2
SPIKE_WINDOW_HOURS = 1              # need at least N consecutive spike hours
MIN_GAP_BETWEEN_CHEAP_AND_SPIKE_HOURS = 4
CHEAP_WINDOW_SEARCH_START_HOUR_CT = 6
CHEAP_WINDOW_SEARCH_END_HOUR_CT = 15  # exclusive

# Pre-cool depth (values in the controller's temp_scale; default "F")
DEFAULT_PRECOOL_DEPTH = 68
DEEPEST_PRECOOL_DEPTH = 66


# ---- ComEd Delivery TOD rate schedule (P2.6) ------------------------------
#
# Source: ComEd Delivery Time-of-Day Fact Sheet (CitizensUtilityBoard.org,
# March 2026). Single-Family Non-Electric Heat rate class (3500 sqft
# single-family residence with gas furnace per EXPERIMENT_DESIGN §3
# boundary conditions). Standard non-TOD rate for this class: 6.228 ¢/kWh.
#
# Rates effective March 2026. If ComEd revises the schedule, update the
# constants here and capture the revision date in the docstring. The
# per-period boundaries (which hours map to which period) are part of the
# program design and don't vary by rate class.
DTOD_RATE_SCHEDULE_SOURCE = "ComEd CUB Fact Sheet March 2026, Single-Family Non-Electric Heat"

# (start_hour_inclusive, end_hour_exclusive, cents_per_kwh)
# Hours are CT-local; the schedule is fixed (365 days/year per the
# program design). Mid-Day Peak is the only period above the standard
# non-TOD rate; the other three (18 hours of the day) are lower.
DTOD_PERIODS_CT: tuple[tuple[int, int, float], ...] = (
    ( 6, 13,  4.009),  # Morning (6am-1pm)
    (13, 19, 10.712),  # Mid-Day Peak (1pm-7pm)  -- THE expensive period
    (19, 21,  3.747),  # Evening (7pm-9pm)
    (21, 24,  2.984),  # Overnight (9pm-midnight) [split across day boundary]
    ( 0,  6,  2.984),  # Overnight (midnight-6am) [other half]
)


def dtod_delivery_rate_for_hour(hour_ct: int) -> float:
    """Return the ComEd DTOD delivery rate in ¢/kWh for the given
    Chicago-local hour (0-23). The schedule is fixed year-round per the
    DTOD program design."""
    if not 0 <= hour_ct <= 23:
        raise ValueError(f"hour_ct must be in [0, 23], got {hour_ct}")
    for start, end, rate in DTOD_PERIODS_CT:
        if start <= hour_ct < end:
            return rate
    raise RuntimeError(f"DTOD schedule does not cover hour_ct={hour_ct}")


def dtod_delivery_rates_24h() -> list[float]:
    """Build a 24-element vector of DTOD delivery rates for every hour
    of the day, indexed by hour_ct."""
    return [dtod_delivery_rate_for_hour(h) for h in range(24)]


def should_deepen_precool(
    forecast_tomorrow: dict[str, Any],
    season_5th_mw: Optional[float],
) -> bool:
    """True when tomorrow's forecast warrants the HOT_STREAK_DAY1 deeper
    pre-cool schedule (03:00 start at 66F) even without a multi-day heat
    streak.

    Inputs:
      forecast_tomorrow["max_temp_f"] -- NWS forecast high for tomorrow.
      forecast_tomorrow["peak_load_mw"] -- PJM load forecast peak for
                                            tomorrow (from pjm.load_forecast).
      season_5th_mw -- Current season-to-date 5th-highest hourly load,
                      or None when insufficient current-season official
                      metered-load history exists (< 168 distinct hourly
                      observations). When None, returns False (no
                      deepening); the caller's day-type decision falls
                      back to weather/price logic per binding spec §11 #14.
    """
    if season_5th_mw is None:
        return False
    peak_mw = forecast_tomorrow.get("peak_load_mw") or 0
    max_temp = forecast_tomorrow.get("max_temp_f") or 0
    return (
        peak_mw > season_5th_mw * DEEPEN_PEAK_RATIO
        and max_temp >= DEEPEN_TEMP_THRESHOLD_F
    )


def _find_consecutive_window_below(
    prices: list[float], threshold: float, min_hours: int,
    *, start_hour: int = 0, end_hour: Optional[int] = None,
    rank_by: Optional[list[float]] = None,
) -> Optional[tuple[int, int]]:
    """Return ``(start_hour, end_hour_exclusive)`` of the cheapest
    consecutive window of length >= ``min_hours`` whose every hour is
    below ``threshold``, searched within the half-open hour range
    ``[start_hour, end_hour)``.

    Qualifying-window threshold is checked against ``prices``. When
    ``rank_by`` is provided (the total-cost vector for DTOD), the
    cheapest window is selected by ``rank_by`` sum rather than
    ``prices`` sum. This lets P2.6 keep the locked CHEAP_PRICE_THRESHOLD_C
    on supply alone (Appendix A calibration) while ranking by
    supply+delivery to avoid the supply-cheap-but-delivery-expensive
    pre-cool window the reviewer flagged.

    Returns None if no qualifying window exists. When multiple qualifying
    windows exist, the cheapest by (rank_by or prices) sum is returned
    (tiebreak: earliest start hour)."""
    if end_hour is None:
        end_hour = len(prices)
    end_hour = min(end_hour, len(prices))
    rank_vector = rank_by if rank_by is not None else prices

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
            rank_window = rank_vector[h:h + min_hours]
            window_sum = sum(rank_window)
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
    forecast_tomorrow: dict[str, Any],  # accepted but not currently consumed; reserved
                                        # for future depth-scaling against forecast high.
    delivery_rates_cents: Optional[list[float]] = None,
    *,
    trace_reason: Optional[list[str]] = None,
) -> Optional[dict[str, int]]:
    """Identify a price-aware pre-cool window for tomorrow.

    Returns ``{"hour_ct": int, "depth_f": int}`` when both:

      * At least ``CHEAP_WINDOW_HOURS`` consecutive cheap hours
        (supply price < ``CHEAP_PRICE_THRESHOLD_C``) exist within the
        06:00-15:00 CT search range, AND
      * At least ``SPIKE_WINDOW_HOURS`` consecutive spike hours
        (supply price >= ``SPIKE_PRICE_THRESHOLD_C``) exist starting at
        least ``MIN_GAP_BETWEEN_CHEAP_AND_SPIKE_HOURS`` after the cheap
        window's start.

    The cheap window is scored by sum (cheapest wins); the spike window
    is the first qualifying one after the gap requirement. ``depth_f``
    scales with spike magnitude: 68F base, dropping toward 66F as the
    spike's max price rises (capped at ``DEEPEST_PRECOOL_DEPTH``).

    When ``delivery_rates_cents`` is provided (24-element ¢/kWh vector
    aligned to ``day_ahead_prices`` by hour_ct), the qualifying-window
    threshold stays on supply alone (preserves the Appendix A locked
    calibration of CHEAP_PRICE_THRESHOLD_C / SPIKE_PRICE_THRESHOLD_C)
    but cheap-window *ranking* uses supply+delivery total cost. This
    fixes the P2.6 adversarial-review case where a 1pm cheap window
    (supply 2.5¢, delivery 10.712¢ Mid-Day Peak) was preferred over a
    10am window (supply 2.8¢, delivery 4.009¢ Morning) -- total cost
    13.2¢ vs 6.8¢. Without delivery awareness Arm B picks the
    delivery-spike hour and pays more, not less.

    Returns None when either window is missing or the gap is too short.

    Optional ``trace_reason``: when caller passes a mutable list, the
    function appends ONE PrecoolCode value reflecting the outcome
    ("PRECOOL_SELECTED" on happy path; one of the rejection codes
    otherwise). Default ``None`` means no overhead and no behaviour
    change — existing callers unaffected. The dict-mutation-via-out-
    param pattern matches Phase 5's ``decide_day_type[evaluation_tape]``
    and avoids either return-shape change OR re-implementation of the
    rejection tree in the caller.
    """
    # Local helper to keep the rejection paths terse without depending
    # on a decision_codes import in this branch — code strings stay
    # in sync with the PrecoolCode enum (single source of truth lives
    # there; tests verify both agree).
    def _reject(code: str) -> None:
        if trace_reason is not None:
            trace_reason.append(code)

    if len(day_ahead_prices) < 24:
        _reject("PRECOOL_REJECTED_DA_LMP_INCOMPLETE")
        return None
    if delivery_rates_cents is not None and len(delivery_rates_cents) != len(day_ahead_prices):
        raise ValueError(
            f"delivery_rates_cents length ({len(delivery_rates_cents)}) "
            f"must match day_ahead_prices ({len(day_ahead_prices)})"
        )
    _ = forecast_tomorrow  # reserved for future depth modulation

    total_costs: Optional[list[float]] = None
    if delivery_rates_cents is not None:
        total_costs = [s + d for s, d in zip(day_ahead_prices, delivery_rates_cents)]

    cheap_window = _find_consecutive_window_below(
        day_ahead_prices,
        CHEAP_PRICE_THRESHOLD_C,
        CHEAP_WINDOW_HOURS,
        start_hour=CHEAP_WINDOW_SEARCH_START_HOUR_CT,
        end_hour=CHEAP_WINDOW_SEARCH_END_HOUR_CT,
        rank_by=total_costs,
    )
    if cheap_window is None:
        _reject("PRECOOL_REJECTED_NO_CHEAP_WINDOW")
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
        _reject("PRECOOL_REJECTED_NO_SPIKE_WINDOW_AFTER_GAP")
        return None

    spike_start, spike_end = spike_window
    spike_max = max(day_ahead_prices[spike_start:spike_end])

    # Depth scaling: 68F at the trigger threshold (10c), 66F when the
    # spike doubles to 20c+. Linear interpolation, clamped.
    if spike_max >= 20.0:
        depth_f = DEEPEST_PRECOOL_DEPTH
    elif spike_max <= SPIKE_PRICE_THRESHOLD_C:
        depth_f = DEFAULT_PRECOOL_DEPTH
    else:
        # 10c -> 68F, 20c -> 66F linearly
        scaled = DEFAULT_PRECOOL_DEPTH - (
            (spike_max - SPIKE_PRICE_THRESHOLD_C)
            / (20.0 - SPIKE_PRICE_THRESHOLD_C)
        ) * (DEFAULT_PRECOOL_DEPTH - DEEPEST_PRECOOL_DEPTH)
        depth_f = int(round(scaled))

    if trace_reason is not None:
        trace_reason.append("PRECOOL_SELECTED")
    return {"hour_ct": cheap_start, "depth_f": depth_f}
