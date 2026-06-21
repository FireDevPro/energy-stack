"""ComEd RTP price-spike reactivity overlay (Arm B warm-only core).

Continuously evaluated overlay on the active comfort baseline. When
ComEd's hourly average price crosses configured thresholds, the overlay
warms the cool setpoint so the AC contributes less load while the price
is high. Warm-only: the effective setpoint is never below the baseline,
never above the comfort ceiling.

The overlay is **stateful**: a minimum-hold window (config
``hold_ttl_minutes``) prevents thrashing on borderline prices, and a
per-tier hysteresis buffer (config ``hysteresis_cents``) prevents a
single tier-boundary fluctuation from triggering repeated transitions.

Four tiers, all warmer-or-equal to baseline; every threshold and offset
is config (no hardcoded numbers — see ``ControllerConfig``):

  Tier      Trigger              Release                 Effective setpoint
  -------   ------------------   ---------------------   ------------------------
  elevated  >= elevated_at       < elevated_at - hyst    baseline + warm_band
  scarcity  >= scarcity_at       < scarcity_at - hyst    baseline + warm_band + spike_extra
  extreme   >= extreme_at        < extreme_at - hyst     comfort_max (snap to ceiling)

The effective setpoint comes from ``effective_cool_for_tier`` (the pinned
formula), not from per-tier offsets carried on the tier object. The
overlay is layered above the comfort baseline with "warmer wins"
semantics: it only ever makes the house warmer than the baseline, never
cooler, and never above the comfort ceiling. The device's own setpoint
min/max limits are the hard cap (device-owned safety; no software
supervisor).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .controller_config import ControllerConfig


# ---- Tier definitions ------------------------------------------------------


@dataclass(frozen=True)
class PriceTier:
    """One tier of the price-spike overlay.

    Tiers no longer carry a setpoint offset/override — the effective
    setpoint is computed by ``effective_cool_for_tier`` from the config.
    A tier only carries the trigger / release thresholds (in c/kWh) and
    its ``priority`` (higher number = higher priority), which the state
    machine uses to compare tiers for upgrade/downgrade decisions.
    """
    name: str
    trigger_price_cents_per_kwh: float
    release_price_cents_per_kwh: float
    priority: int


NORMAL_TIER_NAME = "normal"
NORMAL_TIER_PRIORITY = 0


def build_price_tiers(cfg: "ControllerConfig") -> tuple[PriceTier, ...]:
    """Build the active tier tuple from config, highest priority first.

    Release threshold for each tier is ``trigger - hysteresis_cents``.
    """
    pt = cfg.price_tiers_cents
    return (
        PriceTier("extreme", pt.extreme_at, pt.extreme_at - pt.hysteresis_cents, priority=3),
        PriceTier("scarcity", pt.scarcity_at, pt.scarcity_at - pt.hysteresis_cents, priority=2),
        PriceTier("elevated", pt.elevated_at, pt.elevated_at - pt.hysteresis_cents, priority=1),
    )


# ---- State machine ---------------------------------------------------------


@dataclass(frozen=True)
class PriceOverlayState:
    """Immutable state carried across scheduler ticks.

    ``current_tier`` is one of "normal", "elevated", "scarcity", "extreme".
    ``triggered_at_utc`` is the UTC timestamp of the most recent transition
    *into* the current tier, used by the minimum-hold check. None when the
    state has been ``normal`` since process startup.
    """
    current_tier: str = NORMAL_TIER_NAME
    triggered_at_utc: Optional[datetime] = None


def _priority_by_name(tiers: tuple[PriceTier, ...]) -> dict[str, int]:
    """Name -> priority lookup derived from the passed tiers (+ normal)."""
    return {NORMAL_TIER_NAME: NORMAL_TIER_PRIORITY,
            **{t.name: t.priority for t in tiers}}


def tier_priority(name: str, tiers: tuple[PriceTier, ...]) -> int:
    """Higher number = higher priority. Used for upgrade/downgrade
    comparisons. Falls back to NORMAL_TIER_PRIORITY for unknown names so a
    future state-corruption bug can't cause silent priority inversion."""
    return _priority_by_name(tiers).get(name, NORMAL_TIER_PRIORITY)


def _tier_by_name(name: str, tiers: tuple[PriceTier, ...]) -> Optional[PriceTier]:
    if name == NORMAL_TIER_NAME:
        return None
    for t in tiers:
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
    tiers: tuple[PriceTier, ...],
    minimum_hold_minutes: int,
) -> tuple[Optional[PriceTier], PriceOverlayState]:
    """Decide whether the overlay should fire this tick and return the
    next state.

    ``tiers`` (highest priority first) and ``minimum_hold_minutes`` are
    config-driven — passed by the caller (see ``build_price_tiers`` and
    ``ControllerConfig.hold_ttl_minutes``).

    Returns ``(active_tier_or_None, new_state)``. When the active tier is
    ``None``, the overlay does not propose a setpoint change (caller uses
    the comfort baseline).

    Decision rules:

      * **Upgrade** (e.g. elevated -> scarcity, or normal -> extreme):
        crossing a higher tier's trigger threshold is immediate; no hold
        required for upgrades, since the upgrade itself is more aggressive
        protection and the operator wants it to fire fast.

      * **Downgrade / release** (e.g. extreme -> scarcity, or any tier
        -> normal): requires both (a) the minimum-hold window has elapsed
        since entering the current tier, and (b) the price has crossed
        the current tier's release threshold (downward).

      * **Hold within tier**: if neither upgrade nor release fires, the
        current tier persists. State is returned unchanged.

    Hysteresis is encoded in the tier objects (release = trigger -
    hysteresis_cents). The gap prevents a price oscillating around the
    trigger from causing repeated transitions inside the hold window.
    """
    # Step 1: scan tiers from highest to lowest priority. The highest tier
    # whose trigger condition is met wins for upgrades.
    target_tier: Optional[PriceTier] = None
    for tier in tiers:
        if current_price_cents >= tier.trigger_price_cents_per_kwh:
            target_tier = tier
            break

    current = state.current_tier

    # Step 2: upgrade path. If target tier is strictly higher than current,
    # transition immediately.
    if (target_tier is not None
            and tier_priority(target_tier.name, tiers) > tier_priority(current, tiers)):
        new_state = PriceOverlayState(
            current_tier=target_tier.name,
            triggered_at_utc=now_utc,
        )
        return target_tier, new_state

    # Step 3: hold-within-tier or release path. If the price still satisfies
    # the current tier's hold condition (>= release_price), stay put.
    current_tier_obj = _tier_by_name(current, tiers)
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
    if (target_tier is not None
            and tier_priority(target_tier.name, tiers) < tier_priority(current, tiers)):
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


# ---- Effective-setpoint formula (pinned in spec) --------------------------


def effective_cool_for_tier(
    tier_name: str, baseline: float, cfg: "ControllerConfig",
) -> float:
    """Resolve the effective cool setpoint for a tier (in the controller's
    native ``temp_scale``):

        effective_cool = clamp(baseline + offset,
                               floor = baseline, ceiling = comfort_max)

    where offset is 0 (normal) / warm_band (elevated) /
    warm_band + spike_extra (scarcity) / -> comfort_max (extreme snaps
    straight to the ceiling). Unknown tier -> baseline (conservative).
    """
    cmax = cfg.ceiling.comfort_max
    if tier_name == "elevated":
        target = baseline + cfg.flexibility.warm_band
    elif tier_name == "scarcity":
        target = baseline + cfg.flexibility.warm_band + cfg.flexibility.spike_extra
    elif tier_name == "extreme":
        target = cmax                       # snap to ceiling
    else:                                   # normal / unknown
        target = baseline
    return min(max(target, baseline), cmax)  # floor=baseline, ceiling=comfort_max
