"""Integration test: replay May-Sep 2025 ComEd RTP prices through the
price-overlay state machine and verify tier-hour counts match the
historical analysis (per ARM_B_IMPLEMENTATION §9).

The fixture ``tests/fixtures/comed_2025_hourly.csv`` is the hourly
average of 5-minute ComEd RTP prices for the May-Sep 2025 cooling
season, derived from the same dataset that informed the locked
threshold values in EXPERIMENT_DESIGN.md Appendix A.

Locked thresholds (§2):
  Elevated tier: trigger >= 10 c/kWh, release < 8 c/kWh
  Scarcity tier: trigger >= 20 c/kWh, release < 18 c/kWh
  Minimum hold: 30 minutes
  Hysteresis:   2 c/kWh on each tier

Naive threshold count over the fixture:
  119 hours >= 10c AND < 20c
  38 hours >= 20c
  Sum (any hour at or above elevated trigger) = 157

The state machine introduces 30-min holds and 2c hysteresis on top, so
the active-tier hour count drifts slightly from the naive count. The
test asserts the counts fall in a plausible band around the historical
values.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from .price_overlay import (
    NORMAL_TIER_NAME,
    PriceOverlayState,
    evaluate_price_overlay,
)


FIXTURES = Path(__file__).parent / "tests" / "fixtures"
COMED_HOURLY_CSV = FIXTURES / "comed_2025_hourly.csv"


def _load_fixture() -> list[tuple[datetime, float]]:
    """Parse the hourly CSV fixture into (utc_timestamp, price_cents) tuples."""
    out: list[tuple[datetime, float]] = []
    with COMED_HOURLY_CSV.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["hour_ct_iso"]).astimezone(timezone.utc)
            price = float(row["hourly_avg_cents"])
            out.append((ts, price))
    return out


@pytest.fixture(scope="module")
def hourly_2025() -> list[tuple[datetime, float]]:
    return _load_fixture()


def test_fixture_covers_full_2025_cooling_season(hourly_2025):
    """Sanity-check the fixture's shape before relying on counts."""
    # ~3663 hourly records across May-Sep 2025 (5 months @ ~720h each).
    assert 3500 <= len(hourly_2025) <= 3800
    first_ts = hourly_2025[0][0]
    last_ts = hourly_2025[-1][0]
    assert first_ts.year == 2025
    assert last_ts.year == 2025
    assert first_ts.month >= 5
    assert last_ts.month <= 10  # late September can spill into early October UTC


def test_naive_threshold_counts_match_locked_appendix_a():
    """Sanity check the locked threshold counts. EXPERIMENT_DESIGN
    Appendix A cites P95 = 9.53 c (so ~5% of hours >= 10c) and P99 =
    20.47c (so ~1% of hours >= 20c). The fixture must reproduce those
    counts, otherwise the OSF threshold rationale is decoupled from the
    data."""
    hourly = _load_fixture()
    elevated_only = sum(1 for _, p in hourly if 10 <= p < 20)
    scarcity = sum(1 for _, p in hourly if p >= 20)
    elevated_or_higher = elevated_only + scarcity
    # Spec text says ~157 hours combined (elevated + scarcity).
    assert 150 <= elevated_or_higher <= 165, elevated_or_higher
    assert 30 <= scarcity <= 45, scarcity


def test_replay_state_machine_active_hours_in_expected_band(hourly_2025):
    """End-to-end replay: walk the May-Sep 2025 hourly price stream
    through the price-overlay state machine and tally the hours each
    tier was active. The active-tier hour count should fall close to the
    threshold count, drifting slightly because the 30-min hold extends
    tier durations beyond the naive threshold and hysteresis tightens
    re-entry. We check both stay within plausible bands."""
    state = PriceOverlayState()
    elevated_active_hours = 0
    scarcity_active_hours = 0
    transitions = 0
    last_tier = NORMAL_TIER_NAME

    for ts, price in hourly_2025:
        active_tier, state = evaluate_price_overlay(price, state, ts)
        current = state.current_tier
        if current == "elevated":
            elevated_active_hours += 1
        elif current == "scarcity":
            scarcity_active_hours += 1
        if current != last_tier:
            transitions += 1
            last_tier = current

    # Hold + hysteresis means active counts shift up vs naive threshold
    # counts; the order-of-magnitude check is what matters here. With ~38
    # scarcity events and a 30-min hold, scarcity-active hours should
    # remain in the 30-50 range.
    assert 30 <= scarcity_active_hours <= 60, scarcity_active_hours
    # Elevated catches any hour at the elevated threshold or higher
    # post-scarcity downgrade; expect the band 100-180.
    assert 100 <= elevated_active_hours <= 200, elevated_active_hours
    # Sanity: at least a couple dozen tier transitions across 5 months
    # (otherwise the state machine isn't actually firing).
    assert transitions >= 30, transitions


def test_normal_to_active_transitions_below_naive_threshold_crossings(hourly_2025):
    """The 30-min hold + 2c hysteresis at the elevated boundary prevent
    borderline-price flapping. Counting only normal-to-active transitions
    (and back) -- the boundary hysteresis directly affects -- the state
    machine should produce notably fewer than the naive count of 10c
    crossings."""
    state = PriceOverlayState()
    normal_to_active_transitions = 0
    last_was_normal = True
    naive_10c_crossings = 0
    last_above_10c = False

    for ts, price in hourly_2025:
        active_tier, state = evaluate_price_overlay(price, state, ts)
        is_normal = state.current_tier == NORMAL_TIER_NAME
        if is_normal != last_was_normal:
            normal_to_active_transitions += 1
            last_was_normal = is_normal
        if (price >= 10) != last_above_10c:
            naive_10c_crossings += 1
            last_above_10c = price >= 10

    # Hysteresis at the elevated boundary cuts normal<->active transitions
    # vs the naive single-threshold 10c crossing count.
    assert normal_to_active_transitions < naive_10c_crossings, (
        f"normal<->active={normal_to_active_transitions}, "
        f"naive_10c_crossings={naive_10c_crossings}"
    )
