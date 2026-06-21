"""Decision-trace reason codes for the hvac-scheduler decision tracer.

Per `docs/plans/archive/decision-trace-plan.md` — append-only enums locked at the
OSF commit hash. Existing codes never change meaning; new codes can be
added in subsequent phases without breaking downstream Loki / LogQL
consumers.

Phase 1 shipped `PriceOverlayCode`, which is the only enum that survives.
(Phase 2's `LayerResolutionCode`, Phase 3's `SupervisorCode`, Phase 4's
`PrecoolCode`, and Phase 5's `DayTypeCode` were removed in the
commissioning-controller rewrite along with the layer-priority resolver,
the software safety supervisor, the day-ahead precool path, and day-type
classification — safety is now device-owned and the controller is a single
config-driven comfort baseline.)

The codes are derived from caller-observable state (prev tier, new tier,
current price, stale-feed flag) — NOT from the internal price-overlay
state machine. The "held in tier" outcome therefore collapses the
"hold-active" vs "price-above-release" distinction the state machine
makes internally; the trace surfaces enough fields (`hold_minutes_remaining`,
`price_cents`) for an operator to reconstruct the internal reason.
"""
from __future__ import annotations

from enum import Enum


class PriceOverlayCode(str, Enum):
    """Reason codes for one `evaluate_price_overlay` invocation, classified
    at the caller side from observable state."""

    # Tier unchanged this tick.
    NORMAL_BELOW_TRIGGER = "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"
    HELD_IN_TIER = "PRICE_OVERLAY_HELD_IN_TIER"

    # NEW (spec §3.7): recency gate refused a would-be downgrade because
    # the latest bucket is older than the 7-min fresh threshold.
    HELD_DOWNGRADE_BUCKET_AGE = "PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"

    # Tier changed this tick. Classified by priority comparison of
    # prev vs new tier (NOT per-name branches), so the 4th `extreme`
    # tier is first-class: e.g. extreme->scarcity is a DOWNGRADE, not a
    # mislabelled release-to-normal.
    UPGRADED_TO_ELEVATED = "PRICE_OVERLAY_UPGRADED_TO_ELEVATED"
    UPGRADED_TO_SCARCITY = "PRICE_OVERLAY_UPGRADED_TO_SCARCITY"
    UPGRADED_TO_EXTREME = "PRICE_OVERLAY_UPGRADED_TO_EXTREME"
    DOWNGRADED_TO_ELEVATED = "PRICE_OVERLAY_DOWNGRADED_TO_ELEVATED"
    DOWNGRADED_TO_SCARCITY = "PRICE_OVERLAY_DOWNGRADED_TO_SCARCITY"
    RELEASED_TO_NORMAL = "PRICE_OVERLAY_RELEASED_TO_NORMAL"

    # Feed-unavailable branches.
    FEED_UNAVAILABLE_TIER_PRESERVED = "PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED"
    # Renamed from STALE_FEED_RELEASED — was True only when sample was None
    # at release time. Spec §3.5 / §3.7 forensic-split.
    RELEASED_NO_DATA = "PRICE_OVERLAY_RELEASED_NO_DATA"
    RELEASED_PERSISTENT_STALE = "PRICE_OVERLAY_RELEASED_PERSISTENT_STALE"
