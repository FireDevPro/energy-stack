"""Decision-trace reason codes for the hvac-scheduler decision tracer.

Per `docs/plans/decision-trace-plan.md` — append-only enums locked at the
OSF commit hash. Existing codes never change meaning; new codes can be
added in subsequent phases without breaking downstream Loki / LogQL
consumers.

Phase 1 shipped `PriceOverlayCode`. Phase 2 (this PR) adds
`LayerResolutionCode`. Phases 3-5 will extend with `SupervisorCode`,
`PrecoolCode`, and `DayTypeCode`.

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

    # Tier changed this tick.
    UPGRADED_TO_ELEVATED = "PRICE_OVERLAY_UPGRADED_TO_ELEVATED"
    UPGRADED_TO_SCARCITY = "PRICE_OVERLAY_UPGRADED_TO_SCARCITY"
    DOWNGRADED_TO_ELEVATED = "PRICE_OVERLAY_DOWNGRADED_TO_ELEVATED"
    RELEASED_TO_NORMAL = "PRICE_OVERLAY_RELEASED_TO_NORMAL"

    # Feed-unavailable branches.
    FEED_UNAVAILABLE_TIER_PRESERVED = "PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED"
    STALE_FEED_RELEASED = "PRICE_OVERLAY_STALE_FEED_RELEASED"


class LayerResolutionCode(str, Enum):
    """Reason codes for one `resolve_layer_priority` invocation, classified
    at the caller side from the LayerResolution dataclass.

    "Warmer wins" — the schedule baseline, the price overlay, and the
    5CP shutoff each propose a cool setpoint; effective is `max` across
    them. The winning layer is the one whose proposal equals the
    effective cool setpoint. When more than one layer proposes the same
    value (tie at the warmest), `TIE_WARMER_WINS` records that the
    resolution was over-determined (multiple layers agreed)."""

    SCHEDULE_WINS = "LAYER_RESOLUTION_SCHEDULE_WINS"
    PRICE_OVERLAY_WINS = "LAYER_RESOLUTION_PRICE_OVERLAY_WINS"
    FIVECP_WINS = "LAYER_RESOLUTION_5CP_WINS"
    TIE_WARMER_WINS = "LAYER_RESOLUTION_TIE_WARMER_WINS"
