"""Tests for the ComEd RTP price-spike overlay state machine (§2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from price_overlay import (
    DEFAULT_MINIMUM_HOLD_MINUTES,
    NORMAL_TIER_NAME,
    PRICE_TIERS,
    PriceOverlayState,
    PriceTier,
    evaluate_price_overlay,
    offset_and_override_for_tier,
)


T0 = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)
HOLD = timedelta(minutes=DEFAULT_MINIMUM_HOLD_MINUTES)


# ---- Locked tier definitions ----------------------------------------------


def test_locked_tier_thresholds():
    """Threshold values are pre-committed before OSF and frozen at the OSF
    commit hash. If these change, the OSF amendment process is required."""
    by_name = {t.name: t for t in PRICE_TIERS}
    assert by_name["elevated"].trigger_price_cents_per_kwh == 10.0
    assert by_name["elevated"].release_price_cents_per_kwh == 8.0
    assert by_name["elevated"].cool_setpoint_offset_f == 3
    assert by_name["elevated"].cool_setpoint_override_f is None
    assert by_name["scarcity"].trigger_price_cents_per_kwh == 20.0
    assert by_name["scarcity"].release_price_cents_per_kwh == 18.0
    assert by_name["scarcity"].cool_setpoint_override_f == 85


def test_offset_and_override_lookup_returns_normal_defaults():
    """The §4 layer resolver calls this; for normal tier we want a no-op
    (offset=0, override=None) so the schedule baseline passes through."""
    assert offset_and_override_for_tier(NORMAL_TIER_NAME) == (0, None)
    assert offset_and_override_for_tier("unknown") == (0, None)


def test_offset_and_override_lookup_for_elevated():
    assert offset_and_override_for_tier("elevated") == (3, None)


def test_offset_and_override_lookup_for_scarcity():
    assert offset_and_override_for_tier("scarcity") == (0, 85)


# ---- State machine: upgrades ----------------------------------------------


def test_below_all_tiers_returns_no_overlay():
    """5c is below the 10c elevated trigger, so no overlay fires."""
    tier, state = evaluate_price_overlay(5.0, PriceOverlayState(), T0)
    assert tier is None
    assert state.current_tier == NORMAL_TIER_NAME


def test_crossing_elevated_threshold_triggers_elevated():
    """10c trigger fires immediately; the new state records the trigger
    timestamp for the minimum-hold check on later ticks."""
    tier, state = evaluate_price_overlay(10.5, PriceOverlayState(), T0)
    assert tier is not None
    assert tier.name == "elevated"
    assert state.current_tier == "elevated"
    assert state.triggered_at_utc == T0


def test_crossing_scarcity_threshold_from_normal_triggers_scarcity_directly():
    """Normal -> scarcity skips elevated when the price is high enough; the
    upgrade-immediate rule applies regardless of intermediate tiers."""
    tier, state = evaluate_price_overlay(22.0, PriceOverlayState(), T0)
    assert tier is not None
    assert tier.name == "scarcity"
    assert state.current_tier == "scarcity"


def test_upgrade_from_elevated_to_scarcity_does_not_require_hold():
    """The minimum hold blocks downgrades, not upgrades. Upgrading is more
    aggressive protection; we want it to fire as soon as the trigger
    crosses, even within the hold window."""
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    # Only 5 minutes after entering elevated -- still well within hold.
    tier, new_state = evaluate_price_overlay(20.5, state, T0 + timedelta(minutes=5))
    assert tier is not None and tier.name == "scarcity"
    assert new_state.current_tier == "scarcity"


# ---- State machine: hold prevents thrashing -------------------------------


def test_price_drops_to_release_within_hold_stays_in_current_tier():
    """At t=15min we entered elevated. At t=20min the price drops to 7c
    (below the 8c release). Hold has not elapsed (need 30 min), so we
    stay elevated."""
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    tier, new_state = evaluate_price_overlay(7.0, state, T0 + timedelta(minutes=20))
    assert tier is not None and tier.name == "elevated"
    assert new_state == state  # state unchanged


def test_within_hold_high_price_stays_in_tier():
    """Prices that stay above release keep us in tier indefinitely; the
    hold floor is for downgrades only."""
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    tier, new_state = evaluate_price_overlay(9.5, state, T0 + timedelta(minutes=10))
    assert tier is not None and tier.name == "elevated"
    assert new_state == state


# ---- State machine: downgrade and release --------------------------------


def test_drops_to_normal_after_hold_when_price_below_release():
    """Hold elapsed (30 min), price below 8c release -> back to normal.
    The new state's triggered_at_utc records the transition into normal,
    not the original tier entry."""
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=1)
    tier, new_state = evaluate_price_overlay(7.0, state, later)
    assert tier is None
    assert new_state.current_tier == NORMAL_TIER_NAME
    assert new_state.triggered_at_utc == later


def test_drops_from_scarcity_to_elevated_after_hold():
    """Scarcity at t=0; at t=35min price = 12c (above elevated trigger,
    below scarcity release of 18c). Hold elapsed -> downgrade to elevated,
    not all the way to normal."""
    state = PriceOverlayState(current_tier="scarcity", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=5)
    tier, new_state = evaluate_price_overlay(12.0, state, later)
    assert tier is not None and tier.name == "elevated"
    assert new_state.current_tier == "elevated"
    assert new_state.triggered_at_utc == later  # hold restarts at downgrade time


def test_does_not_release_above_release_threshold_even_after_hold():
    """Hold elapsed but price 9c is still >= 8c release -> stay in
    elevated. The release floor protects against premature drop on a
    barely-cooled price."""
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=5)
    tier, new_state = evaluate_price_overlay(9.0, state, later)
    assert tier is not None and tier.name == "elevated"
    assert new_state == state


# ---- Hysteresis ------------------------------------------------------------


def test_hysteresis_prevents_oscillation_at_elevated_boundary():
    """Price oscillates 9.5 -> 10.5 -> 9.5. We trigger at 10.5 and stay
    elevated through subsequent 9.5 readings within the hold window."""
    s0 = PriceOverlayState()
    # 9.5: nothing fires
    tier, s1 = evaluate_price_overlay(9.5, s0, T0)
    assert tier is None
    # 10.5: triggers elevated
    tier, s2 = evaluate_price_overlay(10.5, s1, T0 + timedelta(minutes=5))
    assert tier is not None and tier.name == "elevated"
    # 9.5 within hold: stays elevated (no thrashing)
    tier, s3 = evaluate_price_overlay(9.5, s2, T0 + timedelta(minutes=15))
    assert tier is not None and tier.name == "elevated"
    assert s3 == s2  # unchanged


def test_hysteresis_at_scarcity_boundary():
    """Same hysteresis pattern at the scarcity boundary: trigger at 20c,
    release at 18c. A 19c reading inside the hold keeps us in scarcity."""
    s = PriceOverlayState(current_tier="scarcity", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=1)
    tier, new_s = evaluate_price_overlay(19.0, s, later)
    assert tier is not None and tier.name == "scarcity"
    assert new_s == s


# ---- Replay-style integration ---------------------------------------------


def test_replay_normal_to_scarcity_to_elevated_to_normal_sequence():
    """End-to-end shape: a typical 2025 spike day saw prices climb past
    20c, hold for ~1 hour, then drop. State should walk normal -> scarcity
    -> elevated -> normal as the price recedes through each release."""
    state = PriceOverlayState()

    # 13:00 -- price 5c, normal
    tier, state = evaluate_price_overlay(5.0, state, T0)
    assert tier is None and state.current_tier == NORMAL_TIER_NAME

    # 13:30 -- price 22c, scarcity fires
    tier, state = evaluate_price_overlay(22.0, state, T0 + timedelta(minutes=30))
    assert tier is not None and tier.name == "scarcity"
    scarcity_at = state.triggered_at_utc

    # 14:30 -- price 12c (above elevated trigger, below scarcity release).
    # Hold elapsed since 13:30 -> downgrade to elevated.
    tier, state = evaluate_price_overlay(12.0, state, T0 + timedelta(minutes=90))
    assert tier is not None and tier.name == "elevated"
    assert state.triggered_at_utc != scarcity_at  # hold restarted

    # 15:30 -- price 5c, hold elapsed, drop to normal.
    tier, state = evaluate_price_overlay(5.0, state, T0 + timedelta(minutes=150))
    assert tier is None
    assert state.current_tier == NORMAL_TIER_NAME
