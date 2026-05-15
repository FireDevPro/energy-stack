"""Decision-trace reason codes for the hvac-scheduler decision tracer.

Per `docs/plans/decision-trace-plan.md` — append-only enums locked at the
OSF commit hash. Existing codes never change meaning; new codes can be
added in subsequent phases without breaking downstream Loki / LogQL
consumers.

Phase 1 shipped `PriceOverlayCode`. Phase 2 shipped `LayerResolutionCode`.
Phase 3 shipped `SupervisorCode`. Phase 4 (this PR) adds `PrecoolCode`.
Phase 5 will extend with `DayTypeCode`.

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


class SupervisorCode(str, Enum):
    """Reason codes for one `validate_setpoints` invocation, classified
    at the caller side from the SupervisorDecision dataclass.

    Order of precedence inside the supervisor: emergency-overheat
    (indoor >= 86F) > clamp (out-of-range cool/heat) > approved. The
    classifier mirrors this — an `emergency` decision always maps to
    EMERGENCY_OVERHEAT regardless of whether clamping would also have
    been needed.

    `CLAMPED_MULTIPLE` fires when both cool AND heat were clamped in the
    same call — distinguishes a single-axis controller bug from a more
    serious double-bound violation.

    The plan-aspirational `EMERGENCY_NO_INDOOR_TEMP` is NOT in the enum
    because the production supervisor doesn't escalate to emergency on
    missing indoor_temp — it falls through to clamp/approved. The
    diagnostic is surfaced via the `indoor_temp_available: bool` field on
    the trace line instead. Operator can filter
    `decision_trace.supervisor` by `indoor_temp_available=false` to see
    when the safety floor was running blind."""

    APPROVED = "SUPERVISOR_APPROVED"
    CLAMPED_COOL_FLOOR = "SUPERVISOR_CLAMPED_COOL_FLOOR"
    CLAMPED_COOL_CEILING = "SUPERVISOR_CLAMPED_COOL_CEILING"
    CLAMPED_HEAT_FLOOR = "SUPERVISOR_CLAMPED_HEAT_FLOOR"
    CLAMPED_HEAT_CEILING = "SUPERVISOR_CLAMPED_HEAT_CEILING"
    CLAMPED_MULTIPLE = "SUPERVISOR_CLAMPED_MULTIPLE"
    EMERGENCY_OVERHEAT = "SUPERVISOR_EMERGENCY_OVERHEAT"


class PrecoolCode(str, Enum):
    """Reason codes for the §7 day-ahead price-aware precool decision,
    classified at the caller side from the wrapper's output.

    The shipped enum reflects actual code branches in
    `compute_price_aware_precool_window` + `should_add_price_aware_precool`:

      * SELECTED — happy path, returns a (hour_ct, depth_f) window.
      * REJECTED_NO_DA_LMP_DATA — day-ahead LMP fetch returned None.
      * REJECTED_NO_FORECAST — tomorrow's NWS forecast row missing.
      * REJECTED_DA_LMP_INCOMPLETE — DA vector has < 24 hours.
      * REJECTED_NO_CHEAP_WINDOW — no consecutive cheap-hour window in
        the 06:00-15:00 search range.
      * REJECTED_NO_SPIKE_WINDOW_AFTER_GAP — no evening spike-hour
        window after the minimum gap from the chosen cheap window.

    Plan-aspirational codes dropped (no matching branch in current
    production code):
      * GAP_TOO_SHORT — collapsed into NO_SPIKE_WINDOW_AFTER_GAP. The
        gap requirement is enforced by starting the spike search after
        `cheap_start + MIN_GAP_BETWEEN_CHEAP_AND_SPIKE_HOURS`; "no spike
        beyond the gap" is the only observable outcome from the caller.
      * DAY_TYPE_NOT_ELIGIBLE — `compute_price_aware_precool_window` is
        called at 21:00 regardless of the decided day_type. No
        day-type gate exists in the function.
      * ALREADY_DEEPER_VIA_HOT_STREAK — that interaction is handled by
        `merge_same_hour_actions_deepest_wins` AFTER selection, not as
        a precool rejection inside the §7 path.

    Removing these from the shipped enum matches the Phase 1/3
    reconciliation pattern: codes are append-only / OSF-bound, so the
    enum should describe production behaviour, not aspiration."""

    SELECTED = "PRECOOL_SELECTED"
    REJECTED_NO_DA_LMP_DATA = "PRECOOL_REJECTED_NO_DA_LMP_DATA"
    REJECTED_NO_FORECAST = "PRECOOL_REJECTED_NO_FORECAST"
    REJECTED_DA_LMP_INCOMPLETE = "PRECOOL_REJECTED_DA_LMP_INCOMPLETE"
    REJECTED_NO_CHEAP_WINDOW = "PRECOOL_REJECTED_NO_CHEAP_WINDOW"
    REJECTED_NO_SPIKE_WINDOW_AFTER_GAP = "PRECOOL_REJECTED_NO_SPIKE_WINDOW_AFTER_GAP"
