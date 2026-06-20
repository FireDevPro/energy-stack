"""Safety supervisor — wraps proposed setpoint commands with hard bounds
and an emergency-indoor-temperature override before they reach the
thermostat.

This is RBC infrastructure that runs regardless of which controller
variant is generating the proposed action. Any future controller
hangs off the same gate.

All bound constants are expressed in the controller's ``temp_scale``
(see app.Config.temp_scale). With the current default scale they carry
the same numeric Fahrenheit values they always had; the unit is now a
controller-level parameter rather than a hardcoded-°F assumption baked
into these names.

What this catches (v1 scope):

  1. Out-of-range setpoint commands. Cool ∈ [SAFE_COOL_MIN, SAFE_COOL_MAX]
     = [65, 86]. Heat ∈ [SAFE_HEAT_MIN, SAFE_HEAT_MAX] = [55, 75].
     A controller bug that produces e.g. cool=55 or cool=95 gets clamped
     to the nearest bound rather than reaching the thermostat. Those are
     ranges where the equipment either over-cools wastefully (low) or
     simply won't engage (high).

  2. Emergency indoor temperature. If the thermostat snapshot reports
     indoor > EMERGENCY_INDOOR (= 86), regardless of what the
     scheduled action or layer-resolved setpoint says, override cool
     to EMERGENCY_COOL_TARGET (= 74). Catches HOT_COAST periods (or
     a price-scarcity / 5CP layer pushed to 85°) that overshoot in
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


# Range bounds, expressed in the controller's ``temp_scale``. Outside
# these, the equipment is either operating in regions outside its design
# envelope (won't engage at high) or wasting energy with no comfort
# benefit (overcooling at low). Picked to accommodate VACATION setpoints
# (cool=83) and any reasonable HOT_5CP shutoff (cool=85), with a small
# margin. Default numeric values are the historical Fahrenheit bounds.
SAFE_COOL_MIN = 65.0
SAFE_COOL_MAX = 86.0
SAFE_HEAT_MIN = 55.0
SAFE_HEAT_MAX = 75.0

# Indoor-temperature emergency threshold (in ``temp_scale``). Above this,
# the household is uncomfortable enough that no scheduled action should be
# allowed to leave the AC sitting idle.
EMERGENCY_INDOOR = 86.0

# Cool setpoint to apply during an emergency (in ``temp_scale``).
# Aggressive enough to actually pull indoor down quickly. Within the safe
# range above.
EMERGENCY_COOL_TARGET = 74.0

# Decision kinds. Free strings (instead of an enum) so they're easy to
# write directly to InfluxDB as a tag value.
DECISION_APPROVED = "approved"
DECISION_CLAMPED = "clamped"
DECISION_EMERGENCY = "emergency"


@dataclass(frozen=True)
class SupervisorDecision:
    """Result of validating one proposed setpoint command.

    cool_setpoint / heat_setpoint: what the caller should ACTUALLY
        apply (may be clamped or overridden vs what was proposed), in the
        controller's ``temp_scale``.
    decision: which path was taken (approved/clamped/emergency).
    reason: short human-readable string for audit logging. None when
        decision is APPROVED.
    """
    cool_setpoint: float
    heat_setpoint: float
    decision: str
    reason: str | None = None

    @property
    def needs_alert(self) -> bool:
        """True when the operator should know the supervisor intervened.
        Emergency overrides always need alerting; clamps usually indicate
        a controller bug worth surfacing too."""
        return self.decision != DECISION_APPROVED


def validate_setpoints(
    proposed_cool: float,
    proposed_heat: float,
    snapshot: dict[str, Any],
) -> SupervisorDecision:
    """Decide what setpoints to actually push to the thermostat.

    Proposed setpoints and the returned setpoints are in the controller's
    ``temp_scale``; the snapshot indoor temperature is read on the same
    scale-agnostic basis.

    Order of precedence (first match wins):
      1. Emergency indoor-temperature override (snapshot says household is
         dangerously hot) -> emergency cool target.
      2. Out-of-range clamp -> nearest bound.
      3. Approved -> proposed values pass through unchanged.
    """
    indoor = snapshot.get("indoor_temp_f")

    # Emergency override: household too hot. Trumps the schedule.
    if isinstance(indoor, (int, float)) and indoor >= EMERGENCY_INDOOR:
        return SupervisorDecision(
            cool_setpoint=EMERGENCY_COOL_TARGET,
            heat_setpoint=_clamp(proposed_heat, SAFE_HEAT_MIN, SAFE_HEAT_MAX),
            decision=DECISION_EMERGENCY,
            reason=f"indoor_{indoor:.1f}_above_{EMERGENCY_INDOOR:.0f}",
        )

    # Clamp out-of-range setpoints.
    cool_clamped = _clamp(proposed_cool, SAFE_COOL_MIN, SAFE_COOL_MAX)
    heat_clamped = _clamp(proposed_heat, SAFE_HEAT_MIN, SAFE_HEAT_MAX)
    if cool_clamped != proposed_cool or heat_clamped != proposed_heat:
        reasons = []
        if cool_clamped != proposed_cool:
            reasons.append(f"cool_{proposed_cool}_to_{cool_clamped}")
        if heat_clamped != proposed_heat:
            reasons.append(f"heat_{proposed_heat}_to_{heat_clamped}")
        return SupervisorDecision(
            cool_setpoint=cool_clamped,
            heat_setpoint=heat_clamped,
            decision=DECISION_CLAMPED,
            reason=",".join(reasons),
        )

    return SupervisorDecision(
        cool_setpoint=proposed_cool,
        heat_setpoint=proposed_heat,
        decision=DECISION_APPROVED,
        reason=None,
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
