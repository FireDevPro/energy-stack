"""Tests for the ComEd RTP price-spike overlay state machine (§2).

Post-Slice-B: tiers are config-driven (built from a ``ControllerConfig``
via ``build_price_tiers``), there are four tiers (normal -> elevated ->
scarcity -> extreme), release = trigger - ``hysteresis_cents``, the
minimum-hold comes from ``hold_ttl_minutes``, and the effective setpoint
comes from the pinned ``effective_cool_for_tier`` formula (no per-tier
offset/override on the tier object).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .controller_config import (
    Ceiling,
    ControllerConfig,
    Flexibility,
    HumidityGuard,
    Modes,
    PriceTiersCents,
    Stage1Ramp,
)
from .price_overlay import (
    NORMAL_TIER_NAME,
    PriceOverlayState,
    PriceTier,
    build_price_tiers,
    effective_cool_for_tier,
    evaluate_price_overlay,
)


T0 = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)
MIN_HOLD = 30
HOLD = timedelta(minutes=MIN_HOLD)


def _cfg(
    *,
    elevated_at: float = 10.0,
    scarcity_at: float = 20.0,
    extreme_at: float = 50.0,
    hysteresis_cents: float = 2.0,
    warm_band: float = 1.0,
    spike_extra: float = 1.0,
    comfort_max: float = 29.0,
    hold_ttl_minutes: int = MIN_HOLD,
) -> ControllerConfig:
    """A minimal ControllerConfig for the overlay tests. Values are not
    grid-validated here (that lives in load_controller_config)."""
    return ControllerConfig(
        temp_scale="C",
        comfort_program=({"from": "00:00", "to": "00:00", "cool": 25.5},),
        heat_floor=18.5,
        flexibility=Flexibility(warm_band=warm_band, spike_extra=spike_extra),
        price_tiers_cents=PriceTiersCents(
            elevated_at=elevated_at, scarcity_at=scarcity_at,
            extreme_at=extreme_at, hysteresis_cents=hysteresis_cents,
        ),
        humidity_guard=HumidityGuard(rh_max_pct=65, rh_clear_pct=62),
        ceiling=Ceiling(comfort_max=comfort_max),
        hold_ttl_minutes=hold_ttl_minutes,
        modes=Modes(stage1_ramp=Stage1Ramp(enabled=False)),
    )


TIERS = build_price_tiers(_cfg())


def _eval(price: float, state: PriceOverlayState, now: datetime,
          *, tiers: tuple[PriceTier, ...] = TIERS, hold: int = MIN_HOLD,
          ) -> tuple[PriceTier | None, PriceOverlayState]:
    return evaluate_price_overlay(price, state, now, tiers, hold)


# ---- Config-driven tier construction --------------------------------------


def test_build_price_tiers_from_config_thresholds_and_release():
    """Tiers are built from config; release = trigger - hysteresis_cents,
    highest priority first (extreme, scarcity, elevated)."""
    tiers = build_price_tiers(_cfg(hysteresis_cents=2.0))
    by_name = {t.name: t for t in tiers}
    assert [t.name for t in tiers] == ["extreme", "scarcity", "elevated"]
    assert by_name["elevated"].trigger_price_cents_per_kwh == 10.0
    assert by_name["elevated"].release_price_cents_per_kwh == 8.0
    assert by_name["scarcity"].trigger_price_cents_per_kwh == 20.0
    assert by_name["scarcity"].release_price_cents_per_kwh == 18.0
    assert by_name["extreme"].trigger_price_cents_per_kwh == 50.0
    assert by_name["extreme"].release_price_cents_per_kwh == 48.0
    assert by_name["extreme"].priority > by_name["scarcity"].priority > by_name["elevated"].priority


def test_release_threshold_uses_config_hysteresis():
    """A different hysteresis value flows straight through to the release
    thresholds — nothing is hardcoded."""
    tiers = build_price_tiers(_cfg(hysteresis_cents=3.0))
    by_name = {t.name: t for t in tiers}
    assert by_name["elevated"].release_price_cents_per_kwh == 7.0
    assert by_name["scarcity"].release_price_cents_per_kwh == 17.0
    assert by_name["extreme"].release_price_cents_per_kwh == 47.0


# ---- Effective-setpoint formula -------------------------------------------


def test_effective_cool_normal_is_baseline():
    cfg = _cfg(warm_band=1.0, spike_extra=1.0, comfort_max=29.0)
    assert effective_cool_for_tier("normal", 25.5, cfg) == 25.5
    assert effective_cool_for_tier("unknown", 25.5, cfg) == 25.5  # conservative


def test_effective_cool_elevated_adds_warm_band():
    cfg = _cfg(warm_band=1.0, spike_extra=1.0, comfort_max=29.0)
    assert effective_cool_for_tier("elevated", 25.5, cfg) == 26.5


def test_effective_cool_scarcity_adds_warm_band_plus_spike_extra():
    cfg = _cfg(warm_band=1.0, spike_extra=1.0, comfort_max=29.0)
    assert effective_cool_for_tier("scarcity", 25.5, cfg) == 27.5


def test_effective_cool_extreme_snaps_to_comfort_max():
    cfg = _cfg(warm_band=1.0, spike_extra=1.0, comfort_max=29.0)
    assert effective_cool_for_tier("extreme", 25.5, cfg) == 29.0


def test_effective_cool_clamps_to_comfort_max_when_offset_would_exceed():
    """High baseline: scarcity offset would exceed comfort_max -> clamp."""
    cfg = _cfg(warm_band=1.0, spike_extra=1.0, comfort_max=29.0)
    # baseline 28.5 + warm_band 1 + spike_extra 1 = 30.5 -> clamp to 29.0
    assert effective_cool_for_tier("scarcity", 28.5, cfg) == 29.0


def test_effective_cool_floor_holds_for_in_range_ceiling():
    """Floor invariant within the sane envelope (comfort_max >= baseline):
    no tier ever resolves below the baseline."""
    cfg = _cfg(warm_band=1.0, spike_extra=1.0, comfort_max=29.0)
    for tier in ("normal", "elevated", "scarcity", "extreme", "unknown"):
        assert effective_cool_for_tier(tier, 25.5, cfg) >= 25.5


# ---- State machine: upgrades ----------------------------------------------


def test_below_all_tiers_returns_no_overlay():
    """5c is below the 10c elevated trigger, so no overlay fires."""
    tier, state = _eval(5.0, PriceOverlayState(), T0)
    assert tier is None
    assert state.current_tier == NORMAL_TIER_NAME


def test_crossing_elevated_threshold_triggers_elevated():
    """10c trigger fires immediately; the new state records the trigger
    timestamp for the minimum-hold check on later ticks."""
    tier, state = _eval(10.5, PriceOverlayState(), T0)
    assert tier is not None
    assert tier.name == "elevated"
    assert state.current_tier == "elevated"
    assert state.triggered_at_utc == T0


def test_crossing_scarcity_threshold_from_normal_triggers_scarcity_directly():
    """Normal -> scarcity skips elevated when the price is high enough."""
    tier, state = _eval(22.0, PriceOverlayState(), T0)
    assert tier is not None
    assert tier.name == "scarcity"
    assert state.current_tier == "scarcity"


def test_crossing_extreme_threshold_from_normal_triggers_extreme_directly():
    """Normal -> extreme skips elevated and scarcity at >= extreme_at."""
    tier, state = _eval(55.0, PriceOverlayState(), T0)
    assert tier is not None
    assert tier.name == "extreme"
    assert state.current_tier == "extreme"


def test_upgrade_from_elevated_to_scarcity_does_not_require_hold():
    """The minimum hold blocks downgrades, not upgrades."""
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    tier, new_state = _eval(20.5, state, T0 + timedelta(minutes=5))
    assert tier is not None and tier.name == "scarcity"
    assert new_state.current_tier == "scarcity"


def test_upgrade_from_scarcity_to_extreme_does_not_require_hold():
    state = PriceOverlayState(current_tier="scarcity", triggered_at_utc=T0)
    tier, new_state = _eval(60.0, state, T0 + timedelta(minutes=5))
    assert tier is not None and tier.name == "extreme"
    assert new_state.current_tier == "extreme"


# ---- Full 4-tier ladder ---------------------------------------------------


def test_full_ladder_normal_to_elevated_to_scarcity_to_extreme():
    """A climbing price walks the whole ladder (upgrades are immediate)."""
    state = PriceOverlayState()
    tier, state = _eval(5.0, state, T0)
    assert state.current_tier == NORMAL_TIER_NAME
    tier, state = _eval(12.0, state, T0 + timedelta(minutes=1))
    assert tier is not None and tier.name == "elevated"
    tier, state = _eval(25.0, state, T0 + timedelta(minutes=2))
    assert tier is not None and tier.name == "scarcity"
    tier, state = _eval(55.0, state, T0 + timedelta(minutes=3))
    assert tier is not None and tier.name == "extreme"


def test_extreme_downgrades_to_scarcity_after_hold():
    """Extreme at t=0; at t=35min price = 25c (above scarcity trigger,
    below extreme release of 48c). Hold elapsed -> downgrade to scarcity,
    not all the way to normal."""
    state = PriceOverlayState(current_tier="extreme", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=5)
    tier, new_state = _eval(25.0, state, later)
    assert tier is not None and tier.name == "scarcity"
    assert new_state.current_tier == "scarcity"
    assert new_state.triggered_at_utc == later


# ---- State machine: hold prevents thrashing -------------------------------


def test_price_drops_to_release_within_hold_stays_in_current_tier():
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    tier, new_state = _eval(7.0, state, T0 + timedelta(minutes=20))
    assert tier is not None and tier.name == "elevated"
    assert new_state == state


def test_within_hold_high_price_stays_in_tier():
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    tier, new_state = _eval(9.5, state, T0 + timedelta(minutes=10))
    assert tier is not None and tier.name == "elevated"
    assert new_state == state


def test_min_hold_sourced_from_config_hold_ttl_minutes():
    """A longer hold_ttl keeps a tier locked past the default 30 min."""
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    # 45 min elapsed, price below release; with hold=60 the downgrade is
    # blocked (would fire with the old 30-min default).
    tier, new_state = _eval(5.0, state, T0 + timedelta(minutes=45), hold=60)
    assert tier is not None and tier.name == "elevated"
    assert new_state == state
    # With hold=30 the same elapsed time releases.
    tier2, new_state2 = _eval(5.0, state, T0 + timedelta(minutes=45), hold=30)
    assert tier2 is None
    assert new_state2.current_tier == NORMAL_TIER_NAME


# ---- State machine: downgrade and release --------------------------------


def test_drops_to_normal_after_hold_when_price_below_release():
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=1)
    tier, new_state = _eval(7.0, state, later)
    assert tier is None
    assert new_state.current_tier == NORMAL_TIER_NAME
    assert new_state.triggered_at_utc == later


def test_drops_from_scarcity_to_elevated_after_hold():
    state = PriceOverlayState(current_tier="scarcity", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=5)
    tier, new_state = _eval(12.0, state, later)
    assert tier is not None and tier.name == "elevated"
    assert new_state.current_tier == "elevated"
    assert new_state.triggered_at_utc == later


def test_does_not_release_above_release_threshold_even_after_hold():
    state = PriceOverlayState(current_tier="elevated", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=5)
    tier, new_state = _eval(9.0, state, later)
    assert tier is not None and tier.name == "elevated"
    assert new_state == state


# ---- Hysteresis ------------------------------------------------------------


def test_hysteresis_prevents_oscillation_at_elevated_boundary():
    s0 = PriceOverlayState()
    tier, s1 = _eval(9.5, s0, T0)
    assert tier is None
    tier, s2 = _eval(10.5, s1, T0 + timedelta(minutes=5))
    assert tier is not None and tier.name == "elevated"
    tier, s3 = _eval(9.5, s2, T0 + timedelta(minutes=15))
    assert tier is not None and tier.name == "elevated"
    assert s3 == s2


def test_hysteresis_at_scarcity_boundary():
    s = PriceOverlayState(current_tier="scarcity", triggered_at_utc=T0)
    later = T0 + HOLD + timedelta(minutes=1)
    tier, new_s = _eval(19.0, s, later)
    assert tier is not None and tier.name == "scarcity"
    assert new_s == s


# ---- Replay-style integration ---------------------------------------------


def test_replay_normal_to_scarcity_to_elevated_to_normal_sequence():
    state = PriceOverlayState()

    tier, state = _eval(5.0, state, T0)
    assert tier is None and state.current_tier == NORMAL_TIER_NAME

    tier, state = _eval(22.0, state, T0 + timedelta(minutes=30))
    assert tier is not None and tier.name == "scarcity"
    scarcity_at = state.triggered_at_utc

    tier, state = _eval(12.0, state, T0 + timedelta(minutes=90))
    assert tier is not None and tier.name == "elevated"
    assert state.triggered_at_utc != scarcity_at

    tier, state = _eval(5.0, state, T0 + timedelta(minutes=150))
    assert tier is None
    assert state.current_tier == NORMAL_TIER_NAME
