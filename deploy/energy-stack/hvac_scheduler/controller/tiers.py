"""Rev 4 tier machine — three tiers, no time locks. Spec: rev 4 §Reactive core.
This file lands minimal (normal-only) in the tracer slice; Task 5 completes
engage/confirm-release/stale-backstop.
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


def evaluate_tier(state: TierState, sample: PriceSample | None,
                  cfg: ControllerConfig, now_utc: datetime) -> tuple[TierState, str]:
    """Returns (new_state, reason_code). Minimal tracer version: stays normal."""
    if sample is None:
        return state, "REV4_FEED_MISSING"
    return state, "REV4_NORMAL_BELOW_TRIGGER"
