"""Decision-trace reason codes for the hvac-scheduler decision tracer.

Per `docs/plans/archive/decision-trace-plan.md` — append-only enums locked at the
OSF commit hash. Existing codes never change meaning; new codes can be
added in subsequent phases without breaking downstream Loki / LogQL
consumers.

Phase 1 shipped `PriceOverlayCode`. Phase 2 shipped `LayerResolutionCode`.
Phase 4 shipped `PrecoolCode`. Phase 5 added `DayTypeCode`. (Phase 3's
`SupervisorCode` was removed with the software safety supervisor in the
commissioning-controller rewrite — safety is now device-owned.)

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

    # Tier changed this tick.
    UPGRADED_TO_ELEVATED = "PRICE_OVERLAY_UPGRADED_TO_ELEVATED"
    UPGRADED_TO_SCARCITY = "PRICE_OVERLAY_UPGRADED_TO_SCARCITY"
    DOWNGRADED_TO_ELEVATED = "PRICE_OVERLAY_DOWNGRADED_TO_ELEVATED"
    RELEASED_TO_NORMAL = "PRICE_OVERLAY_RELEASED_TO_NORMAL"

    # Feed-unavailable branches.
    FEED_UNAVAILABLE_TIER_PRESERVED = "PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED"
    # Renamed from STALE_FEED_RELEASED — was True only when sample was None
    # at release time. Spec §3.5 / §3.7 forensic-split.
    RELEASED_NO_DATA = "PRICE_OVERLAY_RELEASED_NO_DATA"
    RELEASED_PERSISTENT_STALE = "PRICE_OVERLAY_RELEASED_PERSISTENT_STALE"


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


class DayTypeCode(str, Enum):
    """Reason codes for one `decide_day_type` invocation, one code per
    evaluated rule branch.

    The evaluation tape records EVERY rule the function checked, with
    `fired: bool` indicating whether that rule's predicate triggered.
    This surfaces "negative branches" — rules considered and rejected —
    so the operator can see "HOT_STREAK was considered but day2_high_f=
    81 < 85 so it didn't fire" rather than just "winning day_type =
    NORMAL".

    Each tape entry carries one of these codes plus `threshold` and
    `actual` fields so the trace is self-explanatory without re-reading
    the rule code. Codes are append-only / OSF-bound.

    Shipped codes reflect the rule branches actually present in
    `decide_day_type` and its `_classify_with_tape` helper:

      * HOT path:
        - HOT_HEAT_ADVISORY — `is_heat_advisory == True`
        - HOT_HIGH_GE_85 — `high_f >= HOT_TEMP_THRESHOLD_F`
        - HOT_APPARENT_GE_90 — `apparent_max_f >= HOT_APPARENT_THRESHOLD_F`
      * HOT_STREAK_DAY1 escalation paths:
        - HOT_STREAK_MULTI_DAY — base=HOT AND day2=HOT
        - HOT_STREAK_5CP_RISK — base=HOT AND §7 PJM peak-forecast risk
      * NORMAL paths:
        - NORMAL_HIGH_75_TO_84 — `NORMAL_TEMP_THRESHOLD_F <= high_f < HOT_TEMP_THRESHOLD_F`
        - NORMAL_MISSING_TEMPS_FALLBACK — forecast present but
          high_f + apparent_max_f both None (P2.7 safe fallback,
          NORMAL not MILD)
        - NORMAL_NO_FORECAST_FALLBACK — forecast itself None/empty
      * MILD path:
        - MILD_HIGH_LT_75 — `high_f < NORMAL_TEMP_THRESHOLD_F`
    """

    HOT_HEAT_ADVISORY = "DAY_TYPE_HOT_HEAT_ADVISORY"
    HOT_HIGH_GE_85 = "DAY_TYPE_HOT_HIGH_GE_85"
    HOT_APPARENT_GE_90 = "DAY_TYPE_HOT_APPARENT_GE_90"
    HOT_STREAK_MULTI_DAY = "DAY_TYPE_HOT_STREAK_MULTI_DAY"
    HOT_STREAK_5CP_RISK = "DAY_TYPE_HOT_STREAK_5CP_RISK"
    NORMAL_HIGH_75_TO_84 = "DAY_TYPE_NORMAL_HIGH_75_TO_84"
    NORMAL_MISSING_TEMPS_FALLBACK = "DAY_TYPE_NORMAL_MISSING_TEMPS_FALLBACK"
    NORMAL_NO_FORECAST_FALLBACK = "DAY_TYPE_NORMAL_NO_FORECAST_FALLBACK"
    MILD_HIGH_LT_75 = "DAY_TYPE_MILD_HIGH_LT_75"
