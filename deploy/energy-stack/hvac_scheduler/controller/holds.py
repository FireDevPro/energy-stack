"""Rev 4 hold math — pure functions, no I/O. Spec: rev 4 §Reactive core
(setpoint rule, precondition, hold lifecycle rules 1-4), §Safety #3, §Manual holds.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .config import ControllerConfig
from .tiers import ELEVATED, NORMAL, SCARCITY

REFRESH_MARGIN_SEC: int = 300
CLEANUP_GRACE_SEC: int = 300


def compute_target(tier: str, schedule_cool: float,
                   cfg: ControllerConfig) -> float | None:
    """Warm-only target with the engage/extend precondition folded in.
    Returns None when the program already sits at/above anything this tier
    would command (also neutralizes the floor>ceiling inversion)."""
    if tier == ELEVATED:
        target = min(schedule_cool + cfg.elevated_offset, cfg.scarcity_absolute)
    elif tier == SCARCITY:
        target = cfg.scarcity_absolute
    else:
        return None
    if schedule_cool >= target:
        return None
    return target


def hold_until_minutes(now_local: datetime, ttl_minutes: int) -> int:
    expiry = now_local + timedelta(minutes=ttl_minutes)
    minutes = expiry.hour * 60 + expiry.minute
    return (minutes // 15) * 15


def _matches_own(own: Any, snap: Any) -> bool:
    return (own is not None and snap.hold_active
            and snap.hold_until_minutes == own.until_minutes
            and snap.cool_setpoint == own.value)


def decide(tier: str, snap: Any, own: Any, cfg: ControllerConfig,
           now_utc: datetime, now_local: datetime,
           humidity_blocked: bool) -> tuple[str, float | None, int | None, str]:
    """-> (kind, cool_target, until_minutes, reason). kind: none|push|release.
    snap/own are Any by design: holds.py is pure logic and must not import
    device.py/ownhold.py (duck-typed fields documented in the plan)."""
    none = ("none", None, None, "")

    # Normal tier, matching own record (rev 4.1 §Hold lifecycle rule 4):
    # a LIVE own hold has no business existing once the tier is normal
    # (confirmed release, or the stale backstop landing here) -> active
    # release. Expired past grace -> zombie cleanup. Expired but within
    # grace -> leave alone (the device's own lapse edge is imminent).
    if tier == NORMAL:
        if _matches_own(own, snap):
            expiry = datetime.fromisoformat(own.expiry_utc)
            elapsed = (now_utc - expiry).total_seconds()
            if elapsed > CLEANUP_GRACE_SEC:
                return ("release", None, None, "REV4_ZOMBIE_RELEASED")
            if elapsed <= 0:
                return ("release", None, None, "REV4_SPIKE_END_RELEASE")
        return none

    if snap.schedule_cool is None:
        return ("none", None, None, "REV4_NO_SCHEDULE_READ")

    target = compute_target(tier, snap.schedule_cool, cfg)
    until = hold_until_minutes(now_local, cfg.hold_ttl_minutes)

    if _matches_own(own, snap):
        if target is None:
            if own.value < snap.schedule_cool:
                return ("release", None, None, "REV4_WARM_ONLY_RELEASE")
            return none
        # Humidity outranks BOTH correction and extension, and under rev 4.1
        # it is a release trigger while holding: the live controller sends
        # the device home so the program can cycle and dry the air.
        if humidity_blocked:
            return ("release", None, None, "REV4_HUMIDITY_RELEASE")
        if target != own.value:
            return ("push", target, until, "REV4_CORRECTED")
        expiry = datetime.fromisoformat(own.expiry_utc)
        if (expiry - now_utc).total_seconds() <= REFRESH_MARGIN_SEC:
            return ("push", target, until, "REV4_EXTENDED")
        return none

    if snap.hold_active:  # a hold we don't own: manual. Price wins only warmward.
        if target is not None and target > snap.cool_setpoint and not humidity_blocked:
            return ("push", target, until, "REV4_ENGAGED_OVER_MANUAL")
        return ("none", None, None, "REV4_MANUAL_HOLD_RESPECTED")

    if target is None:
        return ("none", None, None, "REV4_PRECONDITION_PROGRAM_WARMER")
    if humidity_blocked:
        return ("none", None, None, "REV4_HUMIDITY_BLOCKED_ENGAGE")
    return ("push", target, until, "REV4_ENGAGED")
