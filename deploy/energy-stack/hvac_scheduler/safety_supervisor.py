"""Safety supervisor — wraps proposed setpoint commands with hard bounds
and an emergency-indoor-temperature override before they reach the
thermostat.

This is RBC infrastructure that runs regardless of which controller
variant is generating the proposed action. Any future controller
hangs off the same gate.

What this catches (v1 scope):

  1. Out-of-range setpoint commands. Cool ∈ [SAFE_COOL_MIN, SAFE_COOL_MAX]
     = [65, 86]. Heat ∈ [SAFE_HEAT_MIN, SAFE_HEAT_MAX] = [55, 75].
     A controller bug that produces e.g. cool=55 or cool=95 gets clamped
     to the nearest bound rather than reaching the thermostat. Those are
     ranges where the equipment either over-cools wastefully (low) or
     simply won't engage (high).

  2. Emergency indoor temperature. If the thermostat snapshot reports
     indoor > EMERGENCY_INDOOR_F (= 86), regardless of what the
     scheduled action or layer-resolved setpoint says, override cool
     to EMERGENCY_COOL_TARGET_F (= 74). Catches HOT_COAST periods (or
     a price-scarcity / 5CP layer pushed to 85°F) that overshoot in
     real heat-wave conditions, plus any case where the household is
     uncomfortably hot for any reason.

What this does NOT catch (deferred to a follow-up):

  * Setpoint slew-rate limits across consecutive applies (needs state
    across calls; not currently tracked between scheduler ticks).
  * Forecast staleness (would require checking nws.forecast freshness
    at decision time, not setpoint time; a cleaner integration with
    decide_day_type than with execute_action).
  * Manual halt sentinel (touch /data/scheduler_halt to disable);
    needs sysadmin convention agreement.
  * Telegram alert routing on emergency_override (needs the existing
    telegram-notifier service queue/topic — separate PR).

Returned decision is a frozen dataclass; the caller substitutes the
resolved setpoints for what was originally requested and logs the
decision to hvac.actions for audit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Range bounds. Outside these, the equipment is either operating in
# regions outside its design envelope (won't engage at high) or wasting
# energy with no comfort benefit (overcooling at low). Picked to
# accommodate VACATION setpoints (cool=83) and any reasonable HOT_5CP
# shutoff (cool=85), with a small margin.
SAFE_COOL_MIN_F = 65
SAFE_COOL_MAX_F = 86
SAFE_HEAT_MIN_F = 55
SAFE_HEAT_MAX_F = 75

# Indoor-temperature emergency threshold. Above this, the household is
# uncomfortable enough that no scheduled action should be allowed to
# leave the AC sitting idle.
EMERGENCY_INDOOR_F = 86.0

# Cool setpoint to apply during an emergency. Aggressive enough to
# actually pull indoor down quickly. Within the safe range above.
EMERGENCY_COOL_TARGET_F = 74

# Decision kinds. Free strings (instead of an enum) so they're easy to
# write directly to InfluxDB as a tag value.
DECISION_APPROVED = "approved"
DECISION_CLAMPED = "clamped"
DECISION_EMERGENCY = "emergency"


@dataclass(frozen=True)
class SupervisorDecision:
    """Result of validating one proposed setpoint command.

    cool_setpoint_f / heat_setpoint_f: what the caller should ACTUALLY
        apply (may be clamped or overridden vs what was proposed).
    decision: which path was taken (approved/clamped/emergency).
    reason: short human-readable string for audit logging. None when
        decision is APPROVED.
    """
    cool_setpoint_f: int
    heat_setpoint_f: int
    decision: str
    reason: str | None = None

    @property
    def needs_alert(self) -> bool:
        """True when the operator should know the supervisor intervened.
        Emergency overrides always need alerting; clamps usually indicate
        a controller bug worth surfacing too."""
        return self.decision != DECISION_APPROVED


def validate_setpoints(
    proposed_cool_f: int,
    proposed_heat_f: int,
    snapshot: dict[str, Any],
) -> SupervisorDecision:
    """Decide what setpoints to actually push to the thermostat.

    Order of precedence (first match wins):
      1. Emergency indoor-temperature override (snapshot says household is
         dangerously hot) -> emergency cool target.
      2. Out-of-range clamp -> nearest bound.
      3. Approved -> proposed values pass through unchanged.
    """
    indoor_f = snapshot.get("indoor_temp_f")

    # Emergency override: household too hot. Trumps the schedule.
    if isinstance(indoor_f, (int, float)) and indoor_f >= EMERGENCY_INDOOR_F:
        return SupervisorDecision(
            cool_setpoint_f=EMERGENCY_COOL_TARGET_F,
            heat_setpoint_f=_clamp(proposed_heat_f, SAFE_HEAT_MIN_F, SAFE_HEAT_MAX_F),
            decision=DECISION_EMERGENCY,
            reason=f"indoor_{indoor_f:.1f}F_above_{EMERGENCY_INDOOR_F:.0f}F",
        )

    # Clamp out-of-range setpoints.
    cool_clamped = _clamp(proposed_cool_f, SAFE_COOL_MIN_F, SAFE_COOL_MAX_F)
    heat_clamped = _clamp(proposed_heat_f, SAFE_HEAT_MIN_F, SAFE_HEAT_MAX_F)
    if cool_clamped != proposed_cool_f or heat_clamped != proposed_heat_f:
        reasons = []
        if cool_clamped != proposed_cool_f:
            reasons.append(f"cool_{proposed_cool_f}_to_{cool_clamped}")
        if heat_clamped != proposed_heat_f:
            reasons.append(f"heat_{proposed_heat_f}_to_{heat_clamped}")
        return SupervisorDecision(
            cool_setpoint_f=cool_clamped,
            heat_setpoint_f=heat_clamped,
            decision=DECISION_CLAMPED,
            reason=",".join(reasons),
        )

    return SupervisorDecision(
        cool_setpoint_f=proposed_cool_f,
        heat_setpoint_f=proposed_heat_f,
        decision=DECISION_APPROVED,
        reason=None,
    )


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))
