"""DTOD Distribution Time-of-Day resultant rates per docs/plans/
sced-rebaseline-spec-2026-05-13.md §8.

Source: ComEd April 2026 Tariff Worksheet, Residential Single Family
Without Electric Space Heat (DTOD class). Resultant rates include all
riders (IDUF, RBAF, TPAF, DGRA, DSPR). IEDT is a separate flat
per-kWh charge that applies regardless of hour.

This module is FROZEN at OSF filing. Rate changes mid-experiment are
protocol deviations requiring amendment.
"""
from __future__ import annotations

# Illinois Electricity Distribution Tax — flat per-kWh, applies to
# every hour regardless of DTOD period. ComEd April 2026 tariff.
IEDT_C_PER_KWH = 0.126


# (start_hour_inclusive, end_hour_exclusive, resultant_c_per_kwh)
# Covers all 24 hours with no gap. Overnight wraps across midnight so
# it appears as two ranges.
DTOD_PERIODS_RESULTANT_C: tuple[tuple[int, int, float], ...] = (
    ( 6, 13,  4.428),   # Morning
    (13, 19, 11.727),   # Mid-Day Peak
    (19, 21,  4.142),   # Evening
    (21, 24,  3.311),   # Overnight (late half: 21:00-24:00)
    ( 0,  6,  3.311),   # Overnight (early half: 00:00-06:00)
)

TARIFF_SOURCE = (
    "ComEd April 2026 Tariff Worksheet, Residential Single Family "
    "Without Electric Space Heat (DTOD class)"
)
TARIFF_EFFECTIVE_DATE = "2026-04-25"


def dtod_resultant_c_per_kwh(hour_ct: int) -> float:
    """Distribution Facilities resultant rate (¢/kWh) for the given CT
    hour-of-day (0..23). Includes all per-kWh riders rolled up into
    the DTOD class. EXCLUDES IEDT — use ``dtod_total_delivery_c_per_kwh``
    for the full delivery total.

    Raises ValueError for hours outside [0, 23].
    """
    if not 0 <= hour_ct <= 23:
        raise ValueError(f"hour_ct must be in [0, 23], got {hour_ct}")
    for start, end, rate in DTOD_PERIODS_RESULTANT_C:
        if start <= hour_ct < end:
            return rate
    # Coverage invariant: the table covers all 24 hours. Reaching this
    # branch means the table has a gap — a bug to fix at edit time.
    raise RuntimeError(f"DTOD schedule does not cover hour_ct={hour_ct}")


def dtod_total_delivery_c_per_kwh(hour_ct: int) -> float:
    """Total per-kWh delivery rate = DTOD resultant + IEDT.

    This is the right-most column of the spec §8 DTOD table — the
    full per-kWh delivery component that combines with the supply
    side (rt_hrl_lmps + PEA + transmission + misc procurement) to
    produce HVAC$ per spec §4.
    """
    return dtod_resultant_c_per_kwh(hour_ct) + IEDT_C_PER_KWH
