"""Rev 4 tier machine — three tiers, no time locks. Spec: rev 4 §Reactive core.
Engage on one fresh-strict bucket; release on confirm-count distinct buckets
at/below the release threshold; stale backstop hard-releases to normal.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .config import ControllerConfig
from .pricing import PriceSample

NORMAL = "normal"
ELEVATED = "elevated"
SCARCITY = "scarcity"
_ORDER = {NORMAL: 0, ELEVATED: 1, SCARCITY: 2}


@dataclass(frozen=True)
class TierState:
    tier: str = NORMAL
    confirm_count: int = 0
    last_confirm_bucket: datetime | None = None
    last_fresh_utc: datetime | None = None


def _target_for_price(cents: float, cfg: ControllerConfig) -> str:
    if cents >= cfg.scarcity_at:
        return SCARCITY
    if cents >= cfg.elevated_at:
        return ELEVATED
    return NORMAL


def _release_threshold(tier: str, cfg: ControllerConfig) -> float:
    trigger = cfg.scarcity_at if tier == SCARCITY else cfg.elevated_at
    return trigger - cfg.hysteresis_cents


def evaluate_tier(state: TierState, sample: PriceSample | None,
                  cfg: ControllerConfig, now_utc: datetime) -> tuple[TierState, str]:
    from .pricing import is_fresh_strict

    if sample is None:
        if state.tier != NORMAL and state.last_fresh_utc is not None and \
                (now_utc - state.last_fresh_utc).total_seconds() >= cfg.stale_release_minutes * 60:
            return TierState(tier=NORMAL), "REV4_RELEASED_STALE_BACKSTOP"
        return state, "REV4_FEED_MISSING"

    fresh = is_fresh_strict(sample)
    if fresh:
        state = replace(state, last_fresh_utc=now_utc)

    # Stale backstop (only bites while holding a tier)
    if state.tier != NORMAL and not fresh:
        anchor = state.last_fresh_utc
        if anchor is not None and (now_utc - anchor).total_seconds() >= cfg.stale_release_minutes * 60:
            return TierState(tier=NORMAL), "REV4_RELEASED_STALE_BACKSTOP"

    target = _target_for_price(sample.cents, cfg)

    # Upgrades: one fresh bucket, immediate.
    if _ORDER[target] > _ORDER[state.tier]:
        if not fresh:
            reason = ("REV4_ENGAGE_BLOCKED_NOT_FRESH" if state.tier == NORMAL
                      else "REV4_HELD_IN_TIER")
            return state, reason
        new = TierState(tier=target, last_fresh_utc=state.last_fresh_utc)
        return new, ("REV4_UPGRADED_TO_SCARCITY" if target == SCARCITY
                     else "REV4_UPGRADED_TO_ELEVATED")

    if state.tier == NORMAL:
        return state, "REV4_NORMAL_BELOW_TRIGGER"

    # Downgrade path: fresh bucket AT/BELOW the current tier's release threshold
    # (spec §Release policy: "at/below"; strictly-above resets).
    if fresh and sample.cents <= _release_threshold(state.tier, cfg):
        if sample.bucket_time_utc == state.last_confirm_bucket:
            return state, "REV4_RELEASE_CONFIRMING"  # same bucket, already counted
        count = state.confirm_count + 1
        if count >= cfg.release_confirm_buckets:
            new_tier = target  # where the confirming prices actually point
            new = TierState(tier=new_tier, last_fresh_utc=state.last_fresh_utc)
            if new_tier == NORMAL:
                return new, "REV4_RELEASED_TO_NORMAL"
            return new, "REV4_DOWNGRADED_TO_ELEVATED"
        return replace(state, confirm_count=count,
                       last_confirm_bucket=sample.bucket_time_utc), "REV4_RELEASE_CONFIRMING"

    # In-band (hysteresis, strictly above release threshold) or non-fresh:
    # hold tier, reset confirmations.
    if state.confirm_count:
        state = replace(state, confirm_count=0, last_confirm_bucket=None)
    return state, "REV4_HELD_IN_TIER"
