"""ComEd RTP price-spike reactivity overlay (Arm B layer 2).

Continuously evaluated overlay on the active scheduled cool setpoint. When
ComEd's hourly average price crosses configured thresholds, the overlay
proposes a warmer cool setpoint (additive offset for elevated, hard
override for scarcity) so the AC contributes less load while the price is
high.

The overlay is **stateful**: a 30-minute minimum hold prevents thrashing
on borderline prices, and 2c/kWh hysteresis on each tier prevents a single
tier-boundary fluctuation from triggering repeated transitions.

Locked threshold values per EXPERIMENT_DESIGN.md Appendix A (frozen at the
OSF commit hash):

  Tier      Trigger     Release    Action
  -------   --------    --------   ----------------------------------------
  Elevated  >= 10c/kWh  < 8c/kWh   +3F to active cool setpoint
  Scarcity  >= 20c/kWh  < 18c/kWh  cool setpoint = 85F (effective shutoff)

Trigger thresholds correspond to the P95 (10c) and P99 (20c) of the 2025
ComEd hourly price distribution. The release thresholds are the trigger
minus a 2c hysteresis buffer.

The overlay is layered above the schedule baseline by the §4
layer-priority resolver in app.py with "warmer wins" semantics: the
overlay never makes the house cooler than the schedule intended, only
warmer. The safety supervisor's [65, 86]F clamp still applies after.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Optional


# ---- Locked tier definitions ----------------------------------------------


@dataclass(frozen=True)
class PriceTier:
    """One tier of the price-spike overlay. The combination of
    ``cool_setpoint_offset_f`` and ``cool_setpoint_override_f`` selects
    additive vs. replacement semantics:

      * offset only (override=None): effective = schedule + offset
      * override set: effective = override (offset is ignored)

    ``priority`` is used by the state machine to compare tiers for
    upgrade decisions. Higher number = higher priority. Setting it once
    on the tier object keeps the priority ordering as a single source of
    truth (vs. duplicating it in a name->priority lookup); future
    amendments adding a tier between elevated and scarcity only need to
    update the tuple entry.

    Locked at the OSF commit hash."""
    name: str
    trigger_price_cents_per_kwh: float
    release_price_cents_per_kwh: float
    cool_setpoint_offset_f: int
    cool_setpoint_override_f: Optional[int]
    priority: int


# Order matters for the state machine: highest-priority tier first.
PRICE_TIERS: tuple[PriceTier, ...] = (
    PriceTier(
        name="scarcity",
        trigger_price_cents_per_kwh=20.0,
        release_price_cents_per_kwh=18.0,
        cool_setpoint_offset_f=0,
        cool_setpoint_override_f=85,
        priority=2,
    ),
    PriceTier(
        name="elevated",
        trigger_price_cents_per_kwh=10.0,
        release_price_cents_per_kwh=8.0,
        cool_setpoint_offset_f=3,
        cool_setpoint_override_f=None,
        priority=1,
    ),
)

NORMAL_TIER_NAME = "normal"
NORMAL_TIER_PRIORITY = 0
DEFAULT_MINIMUM_HOLD_MINUTES = 30
_PRIORITY_BY_NAME = {NORMAL_TIER_NAME: NORMAL_TIER_PRIORITY,
                     **{t.name: t.priority for t in PRICE_TIERS}}


# ---- State machine ---------------------------------------------------------


@dataclass(frozen=True)
class PriceOverlayState:
    """Immutable state carried across scheduler ticks.

    ``current_tier`` is one of "normal", "elevated", "scarcity".
    ``triggered_at_utc`` is the UTC timestamp of the most recent transition
    *into* the current tier, used by the minimum-hold check. None when the
    state has been ``normal`` since process startup.
    """
    current_tier: str = NORMAL_TIER_NAME
    triggered_at_utc: Optional[datetime] = None


def _tier_by_name(name: str) -> Optional[PriceTier]:
    if name == NORMAL_TIER_NAME:
        return None
    for t in PRICE_TIERS:
        if t.name == name:
            return t
    return None


def hold_elapsed(state: PriceOverlayState, now_utc: datetime,
                 minimum_hold_minutes: int) -> bool:
    """True when the minimum-hold window has expired since the last
    transition into the current tier. ``triggered_at_utc=None`` (cold start
    in normal) is treated as already elapsed since there's nothing to hold."""
    if state.triggered_at_utc is None:
        return True
    return now_utc - state.triggered_at_utc >= timedelta(minutes=minimum_hold_minutes)


def evaluate_price_overlay(
    current_price_cents: float,
    state: PriceOverlayState,
    now_utc: datetime,
    minimum_hold_minutes: int = DEFAULT_MINIMUM_HOLD_MINUTES,
) -> tuple[Optional[PriceTier], PriceOverlayState]:
    """Decide whether the overlay should fire this tick and return the
    next state.

    Returns ``(active_tier_or_None, new_state)``. When the active tier is
    ``None``, the overlay does not propose a setpoint change (caller uses
    the schedule baseline).

    Decision rules:

      * **Upgrade** (e.g. elevated -> scarcity, or normal -> scarcity):
        crossing a higher tier's trigger threshold is immediate; no hold
        required for upgrades, since the upgrade itself is more aggressive
        protection and the operator wants it to fire fast.

      * **Downgrade / release** (e.g. scarcity -> elevated, or any tier
        -> normal): requires both (a) the minimum-hold window has elapsed
        since entering the current tier, and (b) the price has crossed
        the current tier's release threshold (downward).

      * **Hold within tier**: if neither upgrade nor release fires, the
        current tier persists. State is returned unchanged.

    Hysteresis is encoded in the tier objects (trigger >= 10/20c, release
    < 8/18c). The 2c gap prevents a price oscillating around the trigger
    from causing repeated transitions inside the hold window.
    """
    # Step 1: scan tiers from highest to lowest priority. The highest tier
    # whose trigger condition is met wins for upgrades.
    target_tier: Optional[PriceTier] = None
    for tier in PRICE_TIERS:
        if current_price_cents >= tier.trigger_price_cents_per_kwh:
            target_tier = tier
            break

    current = state.current_tier

    # Step 2: upgrade path. If target tier is strictly higher than current,
    # transition immediately.
    if target_tier is not None and tier_priority(target_tier.name) > tier_priority(current):
        new_state = PriceOverlayState(
            current_tier=target_tier.name,
            triggered_at_utc=now_utc,
        )
        return target_tier, new_state

    # Step 3: hold-within-tier or release path. If the price still satisfies
    # the current tier's hold condition (>= release_price), stay put.
    current_tier_obj = _tier_by_name(current)
    if current_tier_obj is None:
        # Already in normal: target_tier is what should fire next, even if
        # equal to None. (Equal target is effectively no-op return.)
        return target_tier, state

    # In an active tier: check release.
    if not hold_elapsed(state, now_utc, minimum_hold_minutes):
        # Hold still active; stay in current tier regardless of price
        # (so a brief dip below release doesn't drop us early).
        return current_tier_obj, state

    if current_price_cents >= current_tier_obj.release_price_cents_per_kwh:
        # Hold elapsed but price still elevated above release: stay.
        return current_tier_obj, state

    # Hold elapsed and price below release: downgrade. The new tier is
    # whichever lower tier the price still fits, or normal.
    if target_tier is not None and tier_priority(target_tier.name) < tier_priority(current):
        new_state = PriceOverlayState(
            current_tier=target_tier.name,
            triggered_at_utc=now_utc,
        )
        return target_tier, new_state

    # Drop to normal.
    new_state = PriceOverlayState(
        current_tier=NORMAL_TIER_NAME,
        triggered_at_utc=now_utc,
    )
    return None, new_state


def tier_priority(name: str) -> int:
    """Higher number = higher priority. Used for upgrade comparisons.
    Falls back to NORMAL_TIER_PRIORITY for unknown names so a future
    state-corruption bug can't cause silent priority inversion."""
    return _PRIORITY_BY_NAME.get(name, NORMAL_TIER_PRIORITY)


# ---- Convenience: lookup the offset/override for a tier name --------------


def offset_and_override_for_tier(
    tier_name: str,
) -> tuple[int, Optional[int]]:
    """Return ``(offset_f, override_f)`` for a tier, defaulting to (0, None)
    when the tier is normal or unknown. Used by the §4 layer-priority
    resolver."""
    tier = _tier_by_name(tier_name)
    if tier is None:
        return 0, None
    return tier.cool_setpoint_offset_f, tier.cool_setpoint_override_f
