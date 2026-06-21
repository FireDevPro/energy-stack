"""HVAC pre-cooling scheduler.

Reads tomorrow's NWS forecast + current ComEd HP price from InfluxDB,
decides a day-type (MILD/NORMAL/HOT_5CP_RISK), and pushes COOL_SETPOINT
+ HOLD_MODE commands to the Honeywell thermostat via Control4 Director
(pyControl4) at scheduled time boundaries.

Design:
  * One persistent process, asyncio main loop, ticks every 60s.
  * Daily at DECISION_HOUR (default 21:00 local), decides tomorrow's
    day-type from latest nws.forecast snapshot. Decision written to
    `hvac.decisions` measurement for traceability.
  * At each `SCHEDULER_REVISIT_HOURS` (default 06:00, 11:00 local),
    re-evaluates today's day-type against the latest forecast and
    overwrites the stored decision if it shifted. Catches forecast-bust
    days where the 21:00-yesterday commitment was wrong (NWS day-1
    max-T forecasts mis-classify ~1 in 3 marginal Midwest summer days
    per NSSL/Brooks public-forecast verification).
  * At each schedule transition time (e.g., 06:00, 13:00, 19:00, 22:00),
    looks up the day-type for TODAY and pushes the corresponding
    COOL_SETPOINT_F + HOLD_MODE='Permanent' to the thermostat.
  * Every action also writes to `hvac.actions` for audit.

Safety nets:
  * SCHEDULER_MODE env var (REQUIRED, no default; spec §3): shadow =
    never writes (logs only), experiment = writes ONLY during Arm B
    inside the locked 2026-06-01..2026-11-16 calendar, production =
    writes always (off-protocol). Module refuses to start on missing
    or invalid value.
  * Skips setpoint changes when thermostat HVAC_MODE != Cool/Auto
    (i.e., heating-season no-op).
  * Director token persisted to /data/director_token.json -- one cloud
    auth at startup OR on 401, then pure LAN.
  * Pi failure mode: thermostat keeps last-set setpoint; not a safety
    issue, just degraded scheduling.

Day-type rules (locked per EXPERIMENT_DESIGN.md Appendix A, recalibrated
May 2026 against the 2025 ComEd RTP price-spike distribution):
  * MILD             -- forecast high < 75F             -- no actions
  * NORMAL           -- 75 <= forecast high < 85F       -- standard schedule
  * HOT_5CP_RISK     -- forecast high >= 85F OR
                        forecast apparent >= 90F OR
                        active heat advisory             -- aggressive schedule

Environment variables:
    CONTROL4_EMAIL              Control4 account email
    CONTROL4_PASSWORD           Control4 account password
    CONTROL4_CONTROLLER_IP      Director IP (default 192.168.1.30)
    CONTROL4_THERMOSTAT_ID      C4 item id (default 3231)
    SCHEDULER_MODE              "shadow" | "experiment" | "production" (REQUIRED;
                                no default). shadow = never writes; experiment =
                                writes during Arm B inside the locked
                                2026-06-01..2026-11-16 calendar; production =
                                writes always (excluded from study analysis).
                                Module refuses to start (sys.exit(2)) on missing
                                or invalid value. Spec §3 lock.
    SCHEDULER_DRY_RUN           Retired by SCHEDULER_MODE. If still set in the
                                env, it is logged-and-ignored at Config load.
    SCHEDULER_DECISION_HOUR     Hour-of-day to decide tomorrow (default 21)
    SCHEDULER_REVISIT_HOURS     Comma-separated local hours at which to re-poll
                                today's forecast and re-classify if it shifted
                                (default "6,11"; empty disables)
    TEMP_SCALE                  Temperature scale the controller logic operates
                                in (default "F"). Behavior-preserving: with "F"
                                every scale-agnostic controller value carries
                                its historical Fahrenheit number and the °F
                                telemetry fields are written unchanged.
    SCHEDULER_TZ                IANA tz (default America/Chicago)
    INFLUXDB_URL                http://influxdb:8086
    INFLUXDB_TOKEN              admin or write token
    INFLUXDB_ORG                org
    INFLUXDB_BUCKET             bucket
    DIRECTOR_TOKEN_FILE         path to persisted token (default /data/director_token.json)
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from influxdb_client import InfluxDBClient  # type: ignore[attr-defined]  # influxdb_client lacks __all__/stubs; main() owns this single import for client wiring
from influxdb_client.client.write_api import SYNCHRONOUS
from pyControl4.account import C4Account
from pyControl4.director import C4Director
from pyControl4.climate import C4Climate

from .arm_calendar import ARM_CALENDAR, current_arm_at  # local copy, hash-sync-checked in CI
from .controller_config import ControllerConfig, load_controller_config
from .controller_core import comfort_baseline_cool
from .pjm_5cp import (
    COMED_SCOPE,
    RTO_SCOPE,
    FiveCPState,
    cooling_season_window_utc,
    evaluate_for_scope,
    fetch_forecast_peak_for_date,
    fetch_forecast_peak_today,
    in_cooling_season,
    update_season_5th_highest,
)
from .precool import (
    dtod_delivery_rates_24h,
    should_add_price_aware_precool,
    should_deepen_precool,
)
from .price_overlay import (
    DEFAULT_MINIMUM_HOLD_MINUTES,
    NORMAL_TIER_NAME,
    PriceOverlayState,
    evaluate_price_overlay,
    hold_elapsed,
    offset_and_override_for_tier,
    tier_priority,
)
from .decision_codes import (
    DayTypeCode,
    LayerResolutionCode,
    PrecoolCode,
    PriceOverlayCode,
)


# ---- Config ----------------------------------------------------------------

DAYTYPE_MILD = "MILD"
DAYTYPE_NORMAL = "NORMAL"
DAYTYPE_HOT = "HOT_5CP_RISK"
DAYTYPE_HOT_STREAK_DAY1 = "HOT_STREAK_DAY1"  # tomorrow + day-after both HOT -> extra mass build

# Heat setpoint floor for Auto mode. 65F is a comfortable winter "don't freeze"
# target that gives a 15F deadband against typical cool setpoints (70-80F),
# well above the ASHRAE 90.1 5F minimum (and safely above the CTK04
# ISU 300 default of 3F which is below code).
HEAT_SETPOINT_FLOOR = 65

# Dewpoint threshold above which comfort fails at typical coast setpoints
# (per ASHRAE 55-2020 PMV math + PNNL-26478 humidity studies). Above this,
# the AC needs to keep cycling on low stage for latent removal even if dry-bulb
# is acceptable. Chicago July dewpoints regularly hit 70-72F so this matters.
HUMID_DEWPOINT_F = 65


@dataclass(frozen=True)
class ScheduleAction:
    hour: int
    minute: int
    label: str
    # Setpoints below are in the controller's ``temp_scale`` (default "F").
    # cool_setpoint is None when release_hold=True; the action only flips
    # the thermostat back to schedule mode without changing setpoints.
    cool_setpoint: float | None = None
    heat_setpoint: float = HEAT_SETPOINT_FLOOR
    fan_mode: str | None = None  # 'Auto' | 'On' | 'Circulate' | None=don't touch
    cool_setpoint_humid: float | None = None  # used if today's max dewpoint > HUMID_DEWPOINT_F
    # When True: clear the thermostat's Permanent hold so the device's own
    # baseline schedule resumes. Used by MILD_SCHEDULE to release a hold left
    # over from yesterday's NORMAL/HOT cycle. Skips setpoint and fan_mode
    # writes; only calls set_hold_mode("Schedule").
    release_hold: bool = False


# NORMAL day: 75-85F forecast (and apparent <90F). Pre-cool, coast, recover, sleep.
# Sleep at 21:00 captures full DTOD overnight cheap window (9 PM-6 AM, 2.984c/kWh).
# Coast humid override drops 79->75 when dewpoint >65F to keep low-stage AC running
# for latent removal (per PNNL-26478, ASHRAE 55 humidity comfort).
NORMAL_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(6,  0, "PRE_COOL", cool_setpoint=70),
    ScheduleAction(13, 0, "COAST",    cool_setpoint=79, fan_mode="Circulate",
                   cool_setpoint_humid=75),
    ScheduleAction(19, 0, "RECOVER",  cool_setpoint=75, fan_mode="Auto"),
    ScheduleAction(21, 0, "SLEEP",    cool_setpoint=73),
]

# HOT/5CP-risk day: forecast >=85F max OR apparent >=90F (per Appendix A).
#
# ComEd Hourly Pricing capacity charges use TWO separate peak sets:
#   - PJM 5CP: 5 highest RTO demand hours/year. 2025 empirics: 4 of 5 landed
#     in the 16-17 CDT hour, 1 (6/25/2025) hit 13-14 CDT.
#   - ComEd 5CP: 5 highest ComEd-zone demand hours/year. Historical window
#     noon-18:00 per the ComEd Hourly Pricing FAQ.
#
# Schedule (Arm B post-prereg):
#   04:00 HOT_PRE_COOL (68°F)   bank thermal mass off-peak
#   12:00 HOT_COAST (80°F)      coast through the high-risk afternoon
#   19:00 HOT_RECOVER (75°F)    transition out of coast
#   21:00 SLEEP (73°F)
#
# The fixed 14:00-18:00 CT shutoff window from the original scheduler
# design is DROPPED here per EXPERIMENT_DESIGN.md §3 (Arm B). The dynamic
# layers (§2 real-time RTP price-spike reactivity + §3 dual-scope 5CP
# detector) drive shutoff timing instead -- they can push the effective
# cool to 85°F mid-period via "warmer wins" layer priority when actual
# conditions warrant. This replaces a historical-window assumption with
# real-time conditioning; 2025 data showed the actual RTO peak hour at
# 18:00 CT, not 14-17 as the fixed window assumed.
#
# Spec also describes an "optional fallback floor of 13:00-20:00 CT
# shutoff" for when the price feed is unavailable. That fallback is
# tracked as a separate follow-up; for now the dynamic layers carry
# the shutoff responsibility under normal feed conditions.
#
# Capacity-charge impact: a load reduction during one PJM Five Peak hour
# shifts the customer's Average-Customer-Coincident-Peak-Load (ACustCPL)
# by roughly kW/5, and a reduction during one ComEd Five Peak hour shifts
# Average-Customer-Peak-Load (ACustPL) by roughly kW/5. The final CPLC
# (Capacity Peak Load Contribution, billed for the next delivery year)
# is computed via PJM OATT Attachment M-2 §2 with a conditional branch on
# whether ACustCPL >= ACustPL or not, plus a portfolio-wide term involving
# ComEd weather-normalized peak. Single-hour reductions are diluted
# through the relevant five-hour average, not full-year-per-kW. See O2
# in EXPERIMENT_DESIGN.md for the modeling approach.
HOT_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(4,  0, "HOT_PRE_COOL",     cool_setpoint=68),
    ScheduleAction(12, 0, "HOT_COAST",        cool_setpoint=80, fan_mode="Circulate",
                   cool_setpoint_humid=76),
    ScheduleAction(19, 0, "HOT_RECOVER",      cool_setpoint=75, fan_mode="Auto"),
    ScheduleAction(21, 0, "SLEEP",            cool_setpoint=73),
]

# MILD day: forecast <75F. No pre-cool (it's cool out), but the Pi still OWNS
# the day so the real-time price overlay can actuate (the whole point of the
# mild-full-controller fix — without a Pi-pushed baseline, last_schedule_cool
# stays None and the mid-period overlay re-push short-circuits). Setpoints
# mirror the CTK04 comfort program the house already ran on mild days, now
# Pi-pushed with Permanent holds. The §7 day-ahead price-aware pre-cool can
# still inject here (not day-type-gated) on a grid-event night.
MILD_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(6,  0, "MILD_MORNING", cool_setpoint=73, fan_mode="Auto"),
    ScheduleAction(13, 0, "MILD_DAY",     cool_setpoint=78, fan_mode="Circulate"),
    ScheduleAction(19, 0, "MILD_RECOVER", cool_setpoint=75, fan_mode="Auto"),
    ScheduleAction(21, 0, "SLEEP",        cool_setpoint=73, fan_mode="Auto"),
]

# HOT STREAK DAY 1: tomorrow AND day-after both forecast HOT. Heat wave starting.
# Pre-cool starts an HOUR earlier and goes 2 degrees DEEPER than a one-day HOT
# event, to build extra thermal mass that day 2 can coast on. Day 2 of the
# streak just runs the regular HOT_SCHEDULE since the mass is already there.
HOT_STREAK_DAY1_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(3,  0, "STREAK_PRE_COOL_EARLY", cool_setpoint=66),
    ScheduleAction(12, 0, "HOT_COAST",             cool_setpoint=80, fan_mode="Circulate",
                   cool_setpoint_humid=76),
    ScheduleAction(19, 0, "HOT_RECOVER",           cool_setpoint=75, fan_mode="Auto"),
    ScheduleAction(21, 0, "SLEEP",                 cool_setpoint=73),
]


# ---- Overrides -------------------------------------------------------------
# Manual overrides live in /data/overrides.json on the persistent volume.
# Two flavors:
#
#   1. day_type override -- force today to be MILD/NORMAL/HOT_5CP_RISK
#      regardless of forecast. Useful for "today is a holiday and I'm home"
#      (force NORMAL on a forecast-MILD day) or testing.
#
#   2. flat / vacation override -- ignore the schedule entirely. Apply one
#      cool_setpoint_f + heat_setpoint_f all day. Useful for trips when
#      maintaining tight comfort isn't worth the energy.
#
# Schema (JSON list, top-level array):
#   [
#     {
#       "from_date": "2026-06-15",      # inclusive ISO date
#       "to_date":   "2026-06-22",      # inclusive
#       "day_type":  "NORMAL",          # set this OR setpoints, not both
#       "cool_setpoint_f": null,
#       "heat_setpoint_f": null,
#       "fan_mode": null,
#       "note": "test forced NORMAL day"
#     },
#     {
#       "from_date": "2026-07-04",
#       "to_date":   "2026-07-08",
#       "day_type":  null,
#       "cool_setpoint_f": 83,          # vacation: flat 83F all day
#       "heat_setpoint_f": 60,
#       "fan_mode": "Auto",
#       "note": "lake trip - dogs at sitter"
#     }
#   ]
#
# Edit via: docker exec -it hvac-scheduler nano /data/overrides.json
# Or: docker cp ... in/out for offline editing.

OVERRIDES_FILE_DEFAULT = "/data/overrides.json"
VACATION_PING_INTERVAL_HOURS = 6


@dataclass(frozen=True)
class Override:
    from_date: str  # ISO date YYYY-MM-DD
    to_date: str    # inclusive
    day_type: str | None = None
    cool_setpoint_f: int | None = None
    heat_setpoint_f: int | None = None
    fan_mode: str | None = None
    note: str = ""

    def applies_today(self, today_iso: str) -> bool:
        return self.from_date <= today_iso <= self.to_date

    def is_vacation(self) -> bool:
        return self.cool_setpoint_f is not None

    def is_day_type_override(self) -> bool:
        return self.day_type is not None and not self.is_vacation()


def load_overrides(path: Path) -> list[Override]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            log("warn", "overrides_not_a_list", path=str(path))
            return []
        out = []
        for item in data:
            try:
                out.append(Override(
                    from_date=item["from_date"],
                    to_date=item["to_date"],
                    day_type=item.get("day_type"),
                    cool_setpoint_f=item.get("cool_setpoint_f"),
                    heat_setpoint_f=item.get("heat_setpoint_f"),
                    fan_mode=item.get("fan_mode"),
                    note=item.get("note", ""),
                ))
            except Exception as exc:
                log("warn", "override_parse_failed", item=item, error=str(exc))
        return out
    except Exception as exc:
        log("warn", "overrides_load_failed", path=str(path), error=str(exc))
        return []


def find_active_override(overrides: list[Override], today_iso: str) -> Override | None:
    """Return the first override whose date range covers today, or None."""
    for o in overrides:
        if o.applies_today(today_iso):
            return o
    return None


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


def _trace(event_name: str, *, level: str, tick_id: str,
           now_ct: datetime, **fields: Any) -> None:
    """Best-effort decision-trace emission. Wraps `log()` and never raises.

    Per `docs/plans/archive/decision-trace-plan.md` Phase 1:
      * Reads `SCHEDULER_DECISION_TRACE_VERBOSE` from os.environ on each
        call (orthogonal to `SCHEDULER_MODE`; tests can monkeypatch.setenv).
        When false, `debug`-level lines are suppressed.
      * Auto-inlines `tick_id`, `scheduler_mode`, and `arm` (when
        `current_arm_at(now_ct)` returns A/B; omitted otherwise) into every
        emitted line so trace lines correlate with the canonical
        `hvac.arm_mode` rows.
      * Failure isolation: any exception (Loki down, stdout closed, bad
        field type) is swallowed. Trace failure must not interrupt the
        calling control path.
    """
    try:
        verbose = os.environ.get(
            "SCHEDULER_DECISION_TRACE_VERBOSE", "false"
        ).lower() in ("1", "true", "yes")
        if level == "debug" and not verbose:
            return
        mode = os.environ.get("SCHEDULER_MODE", SCHEDULER_MODE)
        # current_arm_at expects naive CT; strip tzinfo if present.
        when_naive = now_ct.replace(tzinfo=None) if now_ct.tzinfo else now_ct
        arm = current_arm_at(when_naive)
        extras: dict[str, Any] = {
            "tick_id": tick_id,
            "scheduler_mode": mode,
            **fields,
        }
        if arm is not None:
            extras["arm"] = arm
        log(level, event_name, **extras)
    except Exception:
        # Trace failure must never propagate into the control path.
        return


def _price_overlay_hold_minutes_remaining(
    state: PriceOverlayState, now_utc: datetime,
) -> float | None:
    """Minutes left on the price-overlay minimum-hold window, or None
    when in NORMAL tier / no triggered_at timestamp. Surfaces internal
    state-machine timing to the trace caller without re-implementing
    the state machine."""
    if state.triggered_at_utc is None:
        return None
    elapsed = (now_utc - state.triggered_at_utc).total_seconds() / 60.0
    remaining = DEFAULT_MINIMUM_HOLD_MINUTES - elapsed
    return max(0.0, remaining)


def _classify_layer_resolution(
    lr: "LayerResolution",
) -> tuple[LayerResolutionCode, str]:
    """Return (reason_code, winning_layer) from a LayerResolution.

    "Warmer wins" — schedule and price overlay each propose a cool
    setpoint; effective is `max` across them. A layer contributes when its
    own proposal matches effective AND it is actually active:
      * Price overlay contributes when the tier is non-normal AND
        `price_cool` equals effective.
      * Schedule contributes when `schedule_cool` equals effective.

    **5CP is excluded from this classification** (binding spec §11 #14):
    5CP no longer contributes to ``effective_cool`` so it cannot be a
    `winning_layer`. The ``LayerResolutionCode.FIVECP_WINS`` and
    ``TIE_WARMER_WINS`` enum values are preserved for back-compat with
    existing dashboards / archived trace rows but are no longer emitted.
    Post-hoc analysis of when 5CP WOULD have fired uses the preserved
    ``fivecp_active`` / ``fivecp_cool`` fields on the trace row, plus
    ``hvac.5cp_state`` and ``fivecp_eval`` telemetry.
    """
    eff = lr.effective_cool
    price_overlay_contributes = (
        lr.price_overlay_tier != NORMAL_TIER_NAME
        and lr.price_cool == eff
    )
    schedule_matches = lr.schedule_cool == eff

    if price_overlay_contributes:
        return LayerResolutionCode.PRICE_OVERLAY_WINS, "price_overlay"
    if schedule_matches:
        return LayerResolutionCode.SCHEDULE_WINS, "schedule"
    # Defense in depth: should be unreachable since effective = max of
    # schedule and price. Fall back to SCHEDULE_WINS rather than raise
    # from a diagnostic helper.
    return LayerResolutionCode.SCHEDULE_WINS, "schedule"


def _trace_day_type(
    *,
    tick_id: str, now_ct: datetime,
    decision_for_date: str, winning_day_type: str,
    reasons: dict[str, Any],
) -> None:
    """Emit one `decision_trace.day_type_decision` line per call to
    `decide_day_type`. Fires at 21:00 nightly (`run_decision`) and at
    each revisit hour (`run_decision_revisit`). Always `info` level —
    the cadence is too low to be noisy.

    Inlines the `evaluation_tape` from the reasons dict so operators can
    see which rules were considered and which fired without having to
    cross-reference any other measurement. The tape is the negative-
    branch reasoning Chris asked for during the grilling: 'HOT_STREAK
    was considered but day2_high_f=81, didn't fire' becomes a single
    log-line lookup."""
    _trace(
        "decision_trace.day_type_decision",
        level="info",
        tick_id=tick_id,
        now_ct=now_ct,
        decision_for_date=decision_for_date,
        winning_day_type=winning_day_type,
        evaluation_tape=reasons.get("evaluation_tape", []),
        # Surface the existing reasons fields too so the trace is
        # self-contained for forensics.
        winning_reason=reasons.get("reason"),
        high_f=reasons.get("high_f"),
        apparent_max_f=reasons.get("apparent_max_f"),
        max_dewpoint_f=reasons.get("max_dewpoint_f"),
        is_heat_advisory=reasons.get("is_heat_advisory"),
        day2_high_f=reasons.get("day2_high_f"),
    )


def _trace_precool(
    *,
    tick_id: str, now_ct: datetime,
    decision_for_date: str, day_type: str,
    window: dict[str, Any] | None, reason_code: str,
) -> None:
    """Emit one `decision_trace.precool_decision` line per call to the
    Phase 4 wrapper (one per night, at 21:00). Always `info` level — the
    cadence is too low to be noisy. `window` is the selected dict on
    happy path or None on rejection; the trace surfaces hour_ct + depth_f
    when present so a SELECTED row carries the chosen window inline."""
    _trace(
        "decision_trace.precool_decision",
        level="info",
        tick_id=tick_id,
        now_ct=now_ct,
        decision_for_date=decision_for_date,
        day_type=day_type,
        selected=(window is not None),
        hour_ct=(int(window["hour_ct"]) if window is not None else None),
        depth_f=(int(window["depth_f"]) if window is not None else None),
        reason_code=reason_code,
    )


def _trace_layer_resolution(
    *,
    tick_id: str, now_ct: datetime,
    firing: FiringState, layer_resolution: "LayerResolution",
    layer_inputs: "LayerInputs | None" = None,
) -> None:
    """Emit one `decision_trace.layer_resolution` line per
    `resolve_layer_priority` call. Caller passes the `LayerResolution`
    plus optional `LayerInputs` (used for the 5CP scope detail field).
    `firing` is mutated to update `last_eval_effective_cool`."""
    reason_code, winning_layer = _classify_layer_resolution(layer_resolution)
    prev_eff = firing.last_eval_effective_cool
    new_eff = layer_resolution.effective_cool
    # info on effective change (operator-visible event); debug on
    # no-change (suppressed unless verbose=true).
    level = "info" if prev_eff != new_eff else "debug"
    _trace(
        "decision_trace.layer_resolution",
        level=level,
        tick_id=tick_id,
        now_ct=now_ct,
        schedule_cool_f=layer_resolution.schedule_cool,
        price_overlay_tier=layer_resolution.price_overlay_tier,
        price_cool_f=layer_resolution.price_cool,
        fivecp_active=layer_resolution.fivecp_active,
        fivecp_scopes_fired=(
            list(layer_inputs.fivecp_scopes_fired)
            if layer_inputs is not None else []
        ),
        fivecp_cool_f=layer_resolution.fivecp_cool,
        effective_cool_f=new_eff,
        prev_effective_cool_f=prev_eff,
        winning_layer=winning_layer,
        reason_code=reason_code.value,
    )
    firing.last_eval_effective_cool = new_eff


# ---- SCHEDULER_MODE (spec §3) ---------------------------------------------
#
# Three explicit top-level modes gate the setpoint-write path:
#   - shadow     : never writes; logs decisions/telemetry only
#   - experiment : writes ONLY during Arm B periods inside the locked
#                  2026-06-01..2026-11-16 calendar; outside the window =
#                  no writes (no implicit "preserve pre-experiment"
#                  fallback per spec §3 lock)
#   - production : writes always; ignores A/B calendar (deliberate
#                  non-study operation; excluded from analysis dataset)
#
# Unknown / missing values: refuse to start (sys.exit(2)). Validation
# runs at module import so misconfiguration is visible BEFORE any write
# path could run.
#
# The legacy SCHEDULER_DRY_RUN env var is retired; if present alongside
# SCHEDULER_MODE it is ignored with a warning logged in Config.from_env.
VALID_SCHEDULER_MODES = ("shadow", "experiment", "production")


def _validate_scheduler_mode_or_exit() -> str:
    mode = os.environ.get("SCHEDULER_MODE")
    if mode not in VALID_SCHEDULER_MODES:
        log(
            "error",
            "scheduler_mode_invalid",
            value=mode,
            valid=VALID_SCHEDULER_MODES,
            message=(
                "SCHEDULER_MODE must be set explicitly to one of: "
                "shadow, experiment, production. Refusing to start."
            ),
        )
        sys.exit(2)
    log("info", "scheduler_mode_active", mode=mode)
    return mode


SCHEDULER_MODE = _validate_scheduler_mode_or_exit()


def _writes_allowed(when_ct: datetime) -> bool:
    """Per spec §3 SCHEDULER_MODE gating.

    Reads os.environ on each call (not the module-level constant) so
    tests can ``monkeypatch.setenv("SCHEDULER_MODE", ...)`` without
    reloading the module. Module-level validation guarantees the env
    var was valid at startup; tests are expected to use only valid
    values when overriding.

    ``when_ct`` may be tz-aware; the locked arm calendar uses naive
    CT-local datetimes so we strip tzinfo before comparing.
    """
    mode = os.environ.get("SCHEDULER_MODE", SCHEDULER_MODE)
    # Defense in depth: if mode was mutated at runtime to something
    # invalid (after import-time validation passed), fail closed
    # rather than fall through to the experiment branch (which would
    # silently consult the calendar and write on Arm B periods).
    if mode not in VALID_SCHEDULER_MODES:
        return False
    if mode == "shadow":
        return False
    if mode == "production":
        return True
    # mode == "experiment"
    if when_ct.tzinfo is not None:
        when_ct = when_ct.replace(tzinfo=None)
    return current_arm_at(when_ct) == "B"


@dataclass(frozen=True)
class Config:
    email: str
    password: str
    controller_ip: str
    thermostat_id: int
    dry_run: bool
    mode: str
    temp_scale: str
    decision_trace_verbose: bool
    decision_hour: int
    tz_name: str
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    token_file: Path
    overrides_file: Path
    revisit_hours: tuple[int, ...]
    # Set when CONTROLLER_CONFIG_FILE env var is present; None otherwise.
    # Nothing in the control loop consumes this yet — later tasks wire it in.
    controller_config: ControllerConfig | None = None

    @staticmethod
    def from_env() -> "Config":
        def required(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                log("error", "missing_env", var=name)
                sys.exit(2)
            return v

        revisit_raw = os.environ.get("SCHEDULER_REVISIT_HOURS", "6,11")
        try:
            revisit_hours = tuple(sorted({
                int(h.strip()) for h in revisit_raw.split(",") if h.strip()
            }))
        except ValueError:
            log("error", "invalid_revisit_hours", raw=revisit_raw)
            sys.exit(2)

        # SCHEDULER_DRY_RUN retired in favor of SCHEDULER_MODE (spec §3,
        # plan standing rule). If both are set, ignore SCHEDULER_DRY_RUN
        # with a warning so the misconfiguration is visible. Read mode
        # from os.environ (not the module-level SCHEDULER_MODE constant)
        # so the value reflects the current process state — startup
        # validation already guaranteed it was valid at import.
        mode = os.environ.get("SCHEDULER_MODE", SCHEDULER_MODE)
        legacy_dry_run = os.environ.get("SCHEDULER_DRY_RUN")
        if legacy_dry_run is not None:
            log(
                "warn",
                "scheduler_dry_run_ignored",
                value=legacy_dry_run,
                scheduler_mode=mode,
                message=(
                    "SCHEDULER_DRY_RUN is retired; SCHEDULER_MODE is the "
                    "single source of truth for write-gating. Ignoring."
                ),
            )

        decision_trace_verbose = os.environ.get(
            "SCHEDULER_DECISION_TRACE_VERBOSE", "false"
        ).lower() in ("1", "true", "yes")

        # CONTROLLER_CONFIG_FILE: when set, load and attach the parsed
        # ControllerConfig. When unset, leave None — default behavior
        # is unchanged (nothing in the control loop consumes it yet).
        controller_config_path = os.environ.get("CONTROLLER_CONFIG_FILE")
        controller_config: ControllerConfig | None = None
        if controller_config_path:
            try:
                controller_config = load_controller_config(controller_config_path)
            except Exception as exc:
                log("error", "controller_config_load_failed",
                    path=controller_config_path, error=str(exc))
                sys.exit(2)

            # A3: unit coherence — controller logic runs in the env unit
            # (TEMP_SCALE); config values are authored in the YAML unit.
            # If they disagree the controller operates in one unit while
            # setpoints are authored in another — a silent unit bug.
            # Fail fast with a clear error so misconfiguration is visible
            # immediately rather than producing subtly wrong setpoints.
            env_temp_scale = os.environ.get("TEMP_SCALE", "F")
            if controller_config.temp_scale != env_temp_scale:
                log(
                    "error",
                    "temp_scale_mismatch",
                    env_temp_scale=env_temp_scale,
                    config_temp_scale=controller_config.temp_scale,
                    message=(
                        f"TEMP_SCALE env ({env_temp_scale!r}) disagrees with "
                        f"controller_config.temp_scale ({controller_config.temp_scale!r}). "
                        "Set TEMP_SCALE to match the YAML temp_scale or update the "
                        "YAML. Refusing to start."
                    ),
                )
                sys.exit(2)

        return Config(
            email=required("CONTROL4_EMAIL"),
            password=required("CONTROL4_PASSWORD"),
            controller_ip=os.environ.get("CONTROL4_CONTROLLER_IP", "192.168.1.30"),
            thermostat_id=int(os.environ.get("CONTROL4_THERMOSTAT_ID", "3231")),
            # dry_run derived from mode — defense in depth alongside the
            # SCHEDULER_MODE gate inside execute_action.
            dry_run=(mode == "shadow"),
            mode=mode,
            # Temperature scale the controller logic operates in. Default
            # "F" preserves historical behavior: every scale-agnostic
            # controller value (setpoints, bounds, offsets) carries the
            # same numeric Fahrenheit value it always did, and the °F
            # telemetry fields receive it unchanged.
            temp_scale=os.environ.get("TEMP_SCALE", "F"),
            # Documents the env var at startup. Runtime gating is in
            # `_trace`, which reads os.environ on each call so tests can
            # monkeypatch.setenv without reloading the module.
            decision_trace_verbose=decision_trace_verbose,
            decision_hour=int(os.environ.get("SCHEDULER_DECISION_HOUR", "21")),
            tz_name=os.environ.get("SCHEDULER_TZ", "America/Chicago"),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
            token_file=Path(os.environ.get("DIRECTOR_TOKEN_FILE", "/data/director_token.json")),
            overrides_file=Path(os.environ.get("OVERRIDES_FILE", OVERRIDES_FILE_DEFAULT)),
            # Local hours at which to re-poll today's NWS forecast and re-classify
            # the day-type if it shifted enough to change the schedule. Default
            # 06:00 + 11:00 catches the morning forecast refresh AND the late-
            # morning update before the noon coast transition. NWS day-1 max-T
            # forecasts mis-classify ~1 in 3 marginal Midwest summer days
            # (per NSSL/Brooks public-forecast verification); the day-ahead-only
            # commitment leaves that error in place all day. Empty = disabled.
            revisit_hours=revisit_hours,
            controller_config=controller_config,
        )


# ---- Influx queries --------------------------------------------------------

from .freshness import Freshness, classify, THRESHOLDS  # noqa: E402
from .influx_adapter import project_record, TypedRecord, write_point  # noqa: E402

# PriceSample: per-tick ComEd read bundles value + bucket _time + freshness
# label. Per spec §3.3 — the per-tick freshness label uses the data-source
# wall clock (now - sample.source_ts), the cockpit's `"comed.prices"` 7-min
# threshold. Do NOT use sample.source_ts as the safety-release clock (spec §3.5).


@dataclass(frozen=True)
class PriceSample:
    cents_per_kwh: float
    source_ts: datetime  # The bucket's _time (interval-end of the 5-min window).
    freshness: Freshness  # "fresh" | "warn" | "stale" (never "missing" — that's the None return).


def fq_latest_forecast(bucket: str, for_period: str) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -3h)
  |> filter(fn: (r) => r._measurement == "nws.forecast" and r.for_period == "{for_period}")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''


def fq_latest_comed_5min(bucket: str) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "5min")
  |> last()
'''


def fetch_latest_forecast(query_api: Any, bucket: str, for_period: str) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for table in query_api.query(fq_latest_forecast(bucket, for_period)):
        for record in table.records:
            rows.append(record.values)
    if not rows:
        return None
    # After pivot we get one row with all fields as columns
    return rows[0]


def fetch_latest_comed(query_api: Any, bucket: str, *, now_utc: datetime) -> "PriceSample | None":
    """Read the latest comed.prices 5-min bucket, bundle value + _time + freshness.

    Returns None when:
      - No bucket exists in the 30-min Influx query window, OR
      - The latest row has a null `_time` (malformed Influx state — log error,
        do not raise; supervisor-continuity per spec §7).
    """
    for table in query_api.query(fq_latest_comed_5min(bucket)):
        for record in table.records:
            try:
                rec = project_record(record)
            except ValueError as exc:
                # project_record raises ValueError when any required
                # attribute (value/time/field/measurement) is missing or
                # malformed. Preserve prior spec §7 semantic: malformed
                # Influx state for the latest comed.prices row is logged
                # and short-circuited (supervisor-continuity — do not
                # raise). The earlier code split this into value=skip vs
                # time=log+None; collapsing both into the malformed path
                # is safe because under the live Flux query both indicate
                # a broken row that cannot produce a usable PriceSample.
                log("error", "comed_row_missing_time", bucket=bucket, error=str(exc))
                return None
            age_ms = int((now_utc - rec.time_utc).total_seconds() * 1000)
            label = classify("comed.prices", age_ms)
            return PriceSample(
                cents_per_kwh=rec.value,
                source_ts=rec.time_utc,
                freshness=label,
            )
    return None


def fetch_rto_peak_forecast_today(query_api: Any, bucket: str) -> float | None:
    """Read the latest PJM RTO projected daily-peak load from
    ``pjm.peak_forecast_rto`` (sourced from PJM DM2's
    ``ops_sum_frcst_peak_rto`` feed, area="PJM RTO"). PJM publishes
    twice daily (06:00 + 13:00 CT, cooling-season only) and may revise
    the same day's projection; we take the latest row generated since
    midnight UTC of today's UTC date. The poller already tags rows
    with the EPT generated_at, but for the gate-condition use we just
    want the most recent scalar.

    This replaces the cross-scale bug where the RTO scope was being
    handed the COMED-area hourly forecast peak (~10-22 GW scale) as
    its gate input -- a number that never exceeds RTO season-5th
    (~150 GW) so the RTO scope could never fire. Now each scope reads
    its own scope-appropriate projected peak.

    Returns None when no row exists (off-season ticks, or the poller
    hasn't run since midnight on the first cooling-season day).
    """
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: -24h)
          |> filter(fn: (r) => r._measurement == "pjm.peak_forecast_rto"
                                and r.area == "PJM RTO"
                                and r._field == "load_forecast_mw")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: 1)
    """
    for table in query_api.query(flux):
        for record in table.records:
            try:
                rec = project_record(record)
            except ValueError:
                continue
            return rec.value
    return None


# ---- Day-type decision -----------------------------------------------------

# Day-type thresholds (locked per EXPERIMENT_DESIGN.md Appendix A; recalibrated
# May 2026 against the 2025 ComEd RTP price-spike distribution). The earlier
# >=95F HOT threshold reflected absolute heat severity; the new threshold is
# tuned to capture price-spike risk: 54% of 2025 spike days had max temp
# >=85F or apparent >=90F. The remaining 46% of spikes are grid-event-driven
# (forecast-mild but PJM-stressed) and are addressed by the price-overlay
# layer (§2) and 5CP detector (§3), not by day-type classification.
HOT_TEMP_THRESHOLD_F = 85
HOT_APPARENT_THRESHOLD_F = 90
NORMAL_TEMP_THRESHOLD_F = 75


def _classify_one_day(forecast: dict[str, Any] | None) -> str:
    """Single-day classification helper without the full reasons dict.

    Triggers (any one fires HOT):
      * forecast high >= HOT_TEMP_THRESHOLD_F (85F)
      * forecast apparent_max_f >= HOT_APPARENT_THRESHOLD_F (90F) -- humidity
        and wind-driven heat risk that dry-bulb alone misses; sourced from
        the forecastGridData `apparentTemperature` field (§0a)
      * active heat advisory -- preserves the existing alert-driven path
    """
    if not forecast:
        return DAYTYPE_NORMAL
    high_f = forecast.get("high_f")
    apparent_max_f = forecast.get("apparent_max_f")
    is_heat_adv = bool(forecast.get("is_heat_advisory", 0))
    if is_heat_adv:
        return DAYTYPE_HOT
    if high_f is not None and high_f >= HOT_TEMP_THRESHOLD_F:
        return DAYTYPE_HOT
    if apparent_max_f is not None and apparent_max_f >= HOT_APPARENT_THRESHOLD_F:
        return DAYTYPE_HOT
    if high_f is not None and high_f >= NORMAL_TEMP_THRESHOLD_F:
        return DAYTYPE_NORMAL
    if high_f is None and apparent_max_f is None:
        # P2.7: forecast row present but both temperature fields missing
        # (degraded NWS parse / API issue). Treat as missing data, not
        # as a MILD day -- MILD clears holds and disables active
        # scheduling, which on an actually-hot day would be unsafe.
        # Fall back to NORMAL (standard schedule still runs) and log
        # the degraded path so the operator can investigate.
        log("warn", "forecast_no_temperature_fields_falling_back_to_normal",
            forecast_keys=sorted(forecast.keys()) if hasattr(forecast, "keys") else [])
        return DAYTYPE_NORMAL
    return DAYTYPE_MILD


def _classify_with_tape(forecast: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
    """Phase-5 helper: same classification logic as `_classify_one_day`
    but also returns an evaluation tape — one entry per rule branch
    evaluated, recording threshold/actual/fired/reason_code. Used by
    `decide_day_type` to populate `reasons["evaluation_tape"]`.

    Kept in lock-step with `_classify_one_day` (same thresholds, same
    precedence, same fallbacks). A drift between the two would surface
    as an existing-test failure on `_classify_one_day`'s call sites OR
    a new-test failure on the tape's `fired` flags. Both functions
    consult the same module-level threshold constants so threshold
    drift is structurally prevented."""
    tape: list[dict[str, Any]] = []
    if not forecast:
        tape.append({
            "rule": "no_forecast_fallback",
            "threshold": None, "actual": None, "fired": True,
            "reason_code": DayTypeCode.NORMAL_NO_FORECAST_FALLBACK.value,
        })
        return DAYTYPE_NORMAL, tape

    high_f = forecast.get("high_f")
    apparent_max_f = forecast.get("apparent_max_f")
    is_heat_adv = bool(forecast.get("is_heat_advisory", 0))

    # Rule 1: heat advisory (highest priority HOT trigger).
    fired = is_heat_adv
    tape.append({
        "rule": "heat_advisory",
        "threshold": True, "actual": is_heat_adv, "fired": fired,
        "reason_code": DayTypeCode.HOT_HEAT_ADVISORY.value,
    })
    if fired:
        return DAYTYPE_HOT, tape

    # Rule 2: high_f >= HOT threshold.
    fired = high_f is not None and high_f >= HOT_TEMP_THRESHOLD_F
    tape.append({
        "rule": "high_ge_hot",
        "threshold": HOT_TEMP_THRESHOLD_F, "actual": high_f, "fired": fired,
        "reason_code": DayTypeCode.HOT_HIGH_GE_85.value,
    })
    if fired:
        return DAYTYPE_HOT, tape

    # Rule 3: apparent_max_f >= HOT_APPARENT threshold.
    fired = apparent_max_f is not None and apparent_max_f >= HOT_APPARENT_THRESHOLD_F
    tape.append({
        "rule": "apparent_ge_hot",
        "threshold": HOT_APPARENT_THRESHOLD_F, "actual": apparent_max_f, "fired": fired,
        "reason_code": DayTypeCode.HOT_APPARENT_GE_90.value,
    })
    if fired:
        return DAYTYPE_HOT, tape

    # Rule 4: high_f >= NORMAL threshold (and < HOT, by precedence).
    fired = high_f is not None and high_f >= NORMAL_TEMP_THRESHOLD_F
    tape.append({
        "rule": "high_ge_normal",
        "threshold": NORMAL_TEMP_THRESHOLD_F, "actual": high_f, "fired": fired,
        "reason_code": DayTypeCode.NORMAL_HIGH_75_TO_84.value,
    })
    if fired:
        return DAYTYPE_NORMAL, tape

    # Rule 5: missing-temps fallback (P2.7 safe NORMAL not MILD).
    fired = high_f is None and apparent_max_f is None
    tape.append({
        "rule": "missing_temps_fallback",
        "threshold": None, "actual": None, "fired": fired,
        "reason_code": DayTypeCode.NORMAL_MISSING_TEMPS_FALLBACK.value,
    })
    if fired:
        # Preserve the existing P2.7 warn log that `_classify_one_day`
        # emits on this path. The plan's "no removal of existing log
        # lines" rule applies — `decide_day_type` now consumes this
        # helper instead of `_classify_one_day`, so the warn must fire
        # here too or the production log stream silently loses the
        # degraded-forecast alert.
        log("warn", "forecast_no_temperature_fields_falling_back_to_normal",
            forecast_keys=sorted(forecast.keys()) if hasattr(forecast, "keys") else [])
        return DAYTYPE_NORMAL, tape

    # Rule 6: MILD default (high_f present and < NORMAL threshold).
    tape.append({
        "rule": "mild_default",
        "threshold": NORMAL_TEMP_THRESHOLD_F, "actual": high_f, "fired": True,
        "reason_code": DayTypeCode.MILD_HIGH_LT_75.value,
    })
    return DAYTYPE_MILD, tape


def decide_day_type(forecast: dict[str, Any] | None,
                    day2_forecast: dict[str, Any] | None = None,
                    *,
                    tomorrow_peak_load_mw: float | None = None,
                    season_5th_highest_mw: float | None = None
                    ) -> tuple[str, dict[str, Any]]:
    """Return (day_type, reasoning_dict).

    Two paths escalate a HOT day to HOT_STREAK_DAY1 (the deepest pre-cool
    schedule, 03:00 start at 66F):

      * **Multi-day heat path** (existing): if `day2_forecast` is provided
        AND tomorrow is HOT AND day-after is also HOT, escalate to
        HOT_STREAK_DAY1 to bank multi-day thermal mass.

      * **Forecast 5CP-risk path** (§7, NEW): if both
        `tomorrow_peak_load_mw` and `season_5th_highest_mw` are provided
        and `precool.should_deepen_precool` returns True (peak forecast
        > season-5th * 1.05 AND tomorrow's high >= 90F), escalate even
        on a single-day HOT forecast. Captures grid-stress days that
        aren't part of a multi-day heat streak.
    """
    if not forecast:
        # Phase 5: even the no-forecast fallback gets a tape entry so
        # the trace can show "NO_FORECAST_FALLBACK fired" rather than
        # implying no rules were evaluated.
        _, no_forecast_tape = _classify_with_tape(forecast)
        return DAYTYPE_NORMAL, {
            "reason": "no_forecast_available",
            "fallback": True,
            "evaluation_tape": no_forecast_tape,
        }
    high_f = forecast.get("high_f")
    apparent_max_f = forecast.get("apparent_max_f")
    is_heat_adv = bool(forecast.get("is_heat_advisory", 0))
    dewpoint_f = forecast.get("max_dewpoint_f")

    # Phase 5: evaluation tape — one entry per rule branch evaluated.
    # `_classify_with_tape` runs the same rule precedence as
    # `_classify_one_day` and stops as soon as one rule fires; the tape
    # records the rules that ran (both fired-True and fired-False up to
    # the winner). Streak-path rules are appended below.
    base_type, evaluation_tape = _classify_with_tape(forecast)

    reasons = {
        "high_f": high_f,
        "apparent_max_f": apparent_max_f,
        "is_heat_advisory": is_heat_adv,
        "max_dewpoint_f": dewpoint_f,
        "alert_summary": forecast.get("alert_summary", ""),
    }

    if base_type == DAYTYPE_HOT:
        # Lookahead: if day-after is ALSO HOT, escalate to streak.
        day2_type = _classify_one_day(day2_forecast) if day2_forecast else None
        multi_day_fired = (day2_type == DAYTYPE_HOT)
        evaluation_tape.append({
            "rule": "streak_multi_day",
            "threshold": DAYTYPE_HOT, "actual": day2_type, "fired": multi_day_fired,
            "reason_code": DayTypeCode.HOT_STREAK_MULTI_DAY.value,
        })
        if multi_day_fired:
            reasons["reason"] = "hot_streak_starting"
            reasons["day2_high_f"] = (day2_forecast or {}).get("high_f")
            reasons["day2_apparent_max_f"] = (day2_forecast or {}).get("apparent_max_f")
            reasons["day2_is_heat_advisory"] = bool((day2_forecast or {}).get("is_heat_advisory", 0))
            reasons["evaluation_tape"] = evaluation_tape
            return DAYTYPE_HOT_STREAK_DAY1, reasons
        # §7 single-day forecast 5CP-risk path: deepen pre-cool when PJM
        # peak forecast clearly exceeds the season-to-date 5th highest
        # AND tomorrow's high reaches 90F.
        risk_inputs_present = (
            tomorrow_peak_load_mw is not None
            and season_5th_highest_mw is not None
        )
        if risk_inputs_present:
            assert season_5th_highest_mw is not None  # narrowing for mypy
            risk_fired = should_deepen_precool(
                {"max_temp_f": high_f, "peak_load_mw": tomorrow_peak_load_mw},
                season_5th_highest_mw,
            )
            risk_status = "ok"
        else:
            risk_fired = False
            # Distinguish the two non-fired causes in the tape so a
            # future debugger doesn't have to spelunk to figure out
            # WHY a hot day didn't escalate to HOT_STREAK_DAY1.
            # Binding spec §11 #14 commits to recording
            # "insufficient_current_season_history" when the §7 path
            # falls back due to baseline unavailability.
            if season_5th_highest_mw is None:
                risk_status = "insufficient_current_season_history"
            else:  # tomorrow_peak_load_mw is None
                risk_status = "missing_pjm_forecast"
        evaluation_tape.append({
            "rule": "streak_5cp_risk",
            "threshold": "should_deepen_precool",
            "actual": {
                "tomorrow_peak_load_mw": tomorrow_peak_load_mw,
                "season_5th_highest_mw": season_5th_highest_mw,
            } if risk_inputs_present else None,
            "fired": risk_fired,
            "reason_code": DayTypeCode.HOT_STREAK_5CP_RISK.value,
            "status": risk_status,
        })
        if risk_fired:
            reasons["reason"] = "forecast_5cp_risk_single_day"
            reasons["tomorrow_peak_load_mw"] = tomorrow_peak_load_mw
            reasons["season_5th_highest_mw"] = season_5th_highest_mw
            reasons["evaluation_tape"] = evaluation_tape
            return DAYTYPE_HOT_STREAK_DAY1, reasons
        if is_heat_adv:
            reasons["reason"] = "heat_advisory"
        elif high_f is not None and high_f >= HOT_TEMP_THRESHOLD_F:
            reasons["reason"] = f"high_ge_{HOT_TEMP_THRESHOLD_F}"
        else:
            reasons["reason"] = f"apparent_ge_{HOT_APPARENT_THRESHOLD_F}"
        reasons["evaluation_tape"] = evaluation_tape
        return DAYTYPE_HOT, reasons
    if base_type == DAYTYPE_NORMAL:
        # Pre-Phase-5 this branch always wrote "high_75_to_84" — but
        # base_type == DAYTYPE_NORMAL can be reached via TWO paths:
        # the real "high in [75, 84]" range OR the P2.7 missing-temps
        # safe fallback (forecast row present but both temp fields
        # None). The pre-Phase-5 reason string lied about the second
        # case. Phase 5 surfaces the distinction via the tape; this
        # branch checks the last-fired tape entry to write a reason
        # that matches the actual rule that fired, so the trace's
        # `winning_reason` and `reason_code` don't contradict.
        last_fired = next(
            (e for e in reversed(evaluation_tape) if e["fired"]),
            None,
        )
        if (last_fired is not None
                and last_fired["reason_code"]
                == DayTypeCode.NORMAL_MISSING_TEMPS_FALLBACK.value):
            reasons["reason"] = "missing_temps_fallback"
        else:
            reasons["reason"] = f"high_{NORMAL_TEMP_THRESHOLD_F}_to_{HOT_TEMP_THRESHOLD_F - 1}"
        reasons["evaluation_tape"] = evaluation_tape
        return DAYTYPE_NORMAL, reasons
    reasons["reason"] = f"high_lt_{NORMAL_TEMP_THRESHOLD_F}"
    reasons["evaluation_tape"] = evaluation_tape
    return DAYTYPE_MILD, reasons


def schedule_for(day_type: str) -> list[ScheduleAction]:
    return {
        DAYTYPE_HOT_STREAK_DAY1: HOT_STREAK_DAY1_SCHEDULE,
        DAYTYPE_HOT:             HOT_SCHEDULE,
        DAYTYPE_NORMAL:          NORMAL_SCHEDULE,
        DAYTYPE_MILD:            MILD_SCHEDULE,
    }.get(day_type, NORMAL_SCHEDULE)


def action_in_effect_at(
    schedule: list[ScheduleAction], minutes_since_midnight: int
) -> ScheduleAction | None:
    """The schedule action in effect at the given minute-of-day: the latest
    action whose start (hour*60+minute) is <= minutes_since_midnight. None if
    no action starts at or before that minute. The caller derives the baseline
    (release_hold -> None; otherwise resolve_cool_setpoint)."""
    in_effect: ScheduleAction | None = None
    for a in schedule:
        start = a.hour * 60 + a.minute
        if start <= minutes_since_midnight and (
            in_effect is None or start > in_effect.hour * 60 + in_effect.minute
        ):
            in_effect = a
    return in_effect


# Locked per EXPERIMENT_DESIGN.md Appendix A. Effective cool setpoint applied
# during a 5CP-eligibility window or scarcity-tier price spike; 85F is high
# enough to functionally shut the AC off while still being a valid cool setpoint.
COOL_SHUTOFF = 85


@dataclass(frozen=True)
class LayerResolution:
    """Audit-grade record of the layer-priority resolution applied to one
    scheduler tick. Fields populate hvac.actions so the operator can replay
    why the effective setpoint differs from the schedule baseline.

    The resolution rule is "warmer wins": the schedule baseline and the
    price-overlay layer each propose a cool setpoint, and the effective
    setpoint is the max of those proposals (ceiling enforcement is Slice C;
    no software supervisor exists — safety is device-owned).

    5CP is preserved as telemetry (``fivecp_active``, ``fivecp_cool``)
    so post-hoc analysis can reconstruct when 5CP would have fired, but
    per binding spec §11 #14 it is NOT included in the ``effective_cool``
    max — 5CP does not independently force live setpoint changes.

    Setpoint fields are in the controller's ``temp_scale`` (default "F").
    """
    schedule_cool: float
    price_overlay_tier: str           # "normal" | "elevated" | "scarcity"
    price_cool: float                 # Schedule baseline if no tier active
    fivecp_active: bool               # Telemetry only; not part of effective_cool
    fivecp_cool: float                # Telemetry only: what 5CP WOULD have proposed
    effective_cool: float             # max(schedule, price) -- 5CP excluded


def resolve_layer_priority(
    schedule_cool: float,
    *,
    price_overlay_tier: str = "normal",
    price_offset: float = 0,
    price_override: float | None = None,
    fivecp_active: bool = False,
    fivecp_shutoff: float = COOL_SHUTOFF,
) -> LayerResolution:
    """Resolve the effective cool setpoint across schedule / price layers.

    Layers (warmer-wins; the floor clamp in ``_push_baseline_if_changed``
    ensures the effective never falls below the baseline; no ceiling yet —
    that is Slice C; no software supervisor — safety is device-owned):

      1. **Schedule baseline** -- the day-type schedule's cool_setpoint_f
         after `resolve_cool_setpoint` applies humid-override logic.
      2. **Price overlay** (§2) -- elevated tier adds ``price_offset`` to
         the schedule baseline; scarcity tier replaces it with
         ``price_override``. ``price_overlay_tier="normal"`` means no
         overlay is active.

    **5CP is NOT a live layer** (binding spec §11 #14): ``fivecp_active`` and
    ``fivecp_cool`` are preserved on the returned ``LayerResolution`` for
    telemetry / post-hoc analysis of when 5CP would have fired, but they
    do not contribute to ``effective_cool``. A bare ``fivecp_active=True``
    with normal prices does not change the effective cool setpoint. Severe
    price events still drive shutoff via the price overlay (scarcity tier
    override to 85F).

    The function is a pure transform; it doesn't read any global state. §2
    evaluates its condition and passes the resulting arguments in;
    ``fivecp_active``/``fivecp_shutoff`` are accepted for telemetry only.
    """
    if price_override is not None:
        price_cool = price_override
    else:
        price_cool = schedule_cool + price_offset

    # 5CP is telemetry-only: compute what it WOULD have proposed, but
    # exclude it from the effective_cool max. (Binding spec §11 #14.)
    fivecp_cool = fivecp_shutoff if fivecp_active else price_cool
    effective_cool = max(schedule_cool, price_cool)

    return LayerResolution(
        schedule_cool=schedule_cool,
        price_overlay_tier=price_overlay_tier,
        price_cool=price_cool,
        fivecp_active=fivecp_active,
        fivecp_cool=fivecp_cool,
        effective_cool=effective_cool,
    )


def resolve_cool_setpoint(action: ScheduleAction, today_dewpoint_f: float | None) -> tuple[float, str]:
    """Return (setpoint_to_apply, reason) — picks the humid override if dewpoint
    is high enough and an override is defined for this action.

    For release_hold actions there is no setpoint to apply; returns (0,
    "release_hold") so callers can record a sentinel without dispatching a
    setpoint write.
    """
    if action.release_hold:
        return 0, "release_hold"
    if (action.cool_setpoint_humid is not None
            and today_dewpoint_f is not None
            and today_dewpoint_f > HUMID_DEWPOINT_F):
        return action.cool_setpoint_humid, f"humid_override (dewpoint {today_dewpoint_f:.1f}F > {HUMID_DEWPOINT_F}F)"
    # Non-release_hold ScheduleAction always carries cool_setpoint (per
    # field-level invariant in the dataclass docstring); release_hold is
    # the only path that leaves it None and that case returns above.
    assert action.cool_setpoint is not None
    return action.cool_setpoint, "standard"


# ---- Control4 client wrapper ----------------------------------------------

class C4Client:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._account: C4Account | None = None
        self._director: C4Director | None = None
        self._climate: C4Climate | None = None
        self._token: str | None = None
        self._common_name: str | None = None

    def _load_token(self) -> dict[str, Any] | None:
        if not self.cfg.token_file.exists():
            return None
        try:
            result: dict[str, Any] = json.loads(self.cfg.token_file.read_text())
            return result
        except Exception as exc:
            log("warn", "token_load_failed", error=str(exc))
            return None

    def _save_token(self, token: str, common_name: str) -> None:
        self.cfg.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.token_file.write_text(json.dumps({
            "token": token,
            "common_name": common_name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }))
        try:
            os.chmod(self.cfg.token_file, 0o600)
        except Exception:
            pass

    async def _cloud_auth(self) -> None:
        log("info", "cloud_auth_starting", email=self.cfg.email)
        account = C4Account(self.cfg.email, self.cfg.password)
        await account.get_account_bearer_token()
        controllers = await account.get_account_controllers()
        common_name = controllers["controllerCommonName"]
        token_data = await account.get_director_bearer_token(common_name)
        token = token_data["token"]
        self._account = account
        self._token = token
        self._common_name = common_name
        self._save_token(token, common_name)
        log("info", "cloud_auth_ok", common_name=common_name)

    async def ensure_director(self) -> C4Director:
        if self._director and self._token:
            return self._director
        cached = self._load_token()
        if cached:
            self._token = cached["token"]
            self._common_name = cached.get("common_name")
            log("info", "token_loaded_from_disk")
        else:
            await self._cloud_auth()
        # Both paths above set self._token to a non-None bearer string
        # (_cloud_auth sets it from the director-token endpoint; cached
        # branch reads it off the saved token file).
        assert self._token is not None
        self._director = C4Director(self.cfg.controller_ip, self._token)
        self._climate = C4Climate(self._director, self.cfg.thermostat_id)
        return self._director

    async def get_climate(self) -> C4Climate:
        await self.ensure_director()
        assert self._climate is not None
        return self._climate

    async def call_with_reauth(self, coro_fn: Callable[[], Awaitable[Any]]) -> Any:
        """Run a director call; on 401, re-auth and retry once."""
        try:
            return await coro_fn()
        except Exception as exc:
            txt = str(exc).lower()
            if "401" in txt or "unauthorized" in txt or "forbidden" in txt:
                log("warn", "director_token_invalid_reauth", error=str(exc))
                await self._cloud_auth()
                # _cloud_auth sets self._token from the director-token
                # endpoint; non-None on success, raises on failure.
                assert self._token is not None
                self._director = C4Director(self.cfg.controller_ip, self._token)
                self._climate = C4Climate(self._director, self.cfg.thermostat_id)
                return await coro_fn()
            raise


# ---- Scheduler core --------------------------------------------------------

@dataclass
class FiringState:
    """Track what's already fired today so we don't double-execute, plus
    the persistent state for the price-overlay (§2) and 5CP-detection
    (§3) state machines that span ticks."""
    last_decision_date: str = ""
    fired_actions: set[tuple[str, int, int]] = field(default_factory=set)  # (date, hour, minute)
    # (date, revisit_hour) tuples for the intra-day forecast revisit checks.
    # Separate set so the revisit cadence doesn't interact with action firing.
    fired_revisits: set[tuple[str, int]] = field(default_factory=set)
    # Price-overlay state machine (§2). Survives across scheduler ticks but
    # not container restarts; cold-start re-evaluates from current price
    # within the 30-min minimum-hold window so behaviour stabilizes fast.
    price_overlay_state: PriceOverlayState = field(default_factory=PriceOverlayState)
    # 5CP-detector state machines (§3). Two scopes, one state each:
    # ComEd zone (catches ComEd 5CPs) and PJM RTO (catches PJM 5CPs).
    # Both contribute to next-year residential capacity charges; the
    # scheduler ORs their triggers. Same persistence semantics as the
    # price overlay; cold-start defaults to inactive for each.
    fivecp_state_comed: FiveCPState = field(default_factory=FiveCPState)
    fivecp_state_rto: FiveCPState = field(default_factory=FiveCPState)
    # Mid-period re-push tracking (§4 / Critical #2). The most recently
    # fired non-release-hold action's schedule-baseline setpoint and the
    # last effective cool setpoint pushed to the thermostat (in the
    # controller's temp_scale). Reset to None
    # on release_hold actions. (It persists across midnight in normal
    # operation -- there is no day-boundary reset -- so a restart is the
    # only source of a mid-stream None; startup reconstruction repairs it.)
    last_schedule_cool: float | None = None
    last_action_label: str = ""
    last_pushed_effective_cool: float | None = None
    # One-shot guard for startup baseline reconstruction. False only on a
    # fresh process. Flipped True on the first run_schedule_check tick; after
    # that the normal action-fire / release-hold flow owns the baseline
    # (including its legitimate Nones, which must NOT be reconstructed).
    baseline_initialized: bool = False
    # Phase 2 decision-trace: the effective cool setpoint computed at the
    # last layer-resolution evaluation. Distinct from
    # ``last_pushed_effective_cool`` (post-supervisor + actually-pushed) —
    # this is the pre-supervisor effective for the per-eval trace's
    # info/debug level gating. None until the first resolve_layer_priority
    # call lands.
    last_eval_effective_cool: float | None = None
    # Throttle for hvac.5cp_state audit writes. Spec calls for ~every-5-min
    # cadence (288 rows/day) so dashboards can plot the ratio + derivative
    # trace without flooding the bucket at the 1-min scheduler tick rate.
    last_5cp_audit_at_utc: datetime | None = None
    # Throttle for hvac.arm_mode + hvac.switch_event + hvac.input_feed_health
    # writes. Same ~5-min cadence as 5cp_state so analysis sees a uniform
    # 288-rows/day arm-mode trace per spec §11 #2.
    last_arm_mode_audit_at_utc: datetime | None = None
    # Track the most recently observed arm letter so switch-event logging
    # can detect transitions across ticks (spec §11 #3). ``arm_observed``
    # is False on cold start (process boot) and True once the first tick
    # has populated ``last_observed_arm`` — this distinguishes a mid-arm
    # restart (no boundary, don't log) from a real None->A transition at
    # experiment start (boundary, log).
    last_observed_arm: str | None = None
    arm_observed: bool = False
    # Per spec §3.6: timestamp of the bucket's _time on the most recent
    # tick where fetch_latest_comed returned a sample with
    # freshness == "fresh". The audit telemetry's broad-feed-health
    # derivation (price_feed_healthy, §3.6) uses this. The safety-release
    # timer uses a SEPARATE controller-observation field
    # (nonfresh_after_hold_started_at_utc, §3.5) added in Phase 2;
    # do not conflate the two clocks.
    last_fresh_bucket_source_ts: datetime | None = None
    # Per spec §3.5 controller-observation wall-clock safety-release timer.
    # Set to `now_utc` on the first tick where (a) min-hold has elapsed for
    # the current non-normal tier AND (b) the current sample is non-fresh.
    # Cleared on any fresh sample / return to normal / min-hold-not-elapsed.
    # The release fires when (now_utc - nonfresh_after_hold_started_at_utc)
    # >= PRICE_FEED_STALE_THRESHOLD.
    #
    # CRITICAL: this is CONTROLLER-OBSERVATION wall-clock, NOT the bucket's
    # _time (sample.source_ts). The data-source clock counts bucket aging
    # during min-hold against the controller, which is wrong. See spec
    # §3.5 guard: "Do not use sample.source_ts or last_fresh_bucket_source_ts
    # as the safety-release clock."
    nonfresh_after_hold_started_at_utc: datetime | None = None


def fetch_day_ahead_prices_for_date(
    query_api: Any, bucket: str, target_date_iso: str, tz: ZoneInfo,
) -> list[float] | None:
    """Pull the 24 hourly day-ahead LMPs for ``target_date_iso`` from
    ``pjm.lmp_da_hourly`` and convert them to cents/kWh (the unit the
    §2 price overlay tier thresholds and the §7 cheap/spike thresholds
    are measured in).

    ``$/MWh ÷ 10 = ¢/kWh``. PJM publishes ``total_lmp_da`` in $/MWh.

    EPT-vs-CT day-boundary handling (not DST-specific). PJM publishes
    DA LMP indexed by Eastern Prevailing Time calendar day; the
    scheduler operates on CT calendar day. EPT runs **1 hour ahead of
    CT year-round** — both zones observe DST simultaneously, so the
    offset is constant (EST/CST in winter, EDT/CDT in summer). At the
    17:00 CT day-D publish, PJM's "tomorrow EPT day" batch covers the
    physical hours CT 23:00 day D through CT 22:00 day D+1 — 23 of the
    24 CT-tomorrow hours (CT 00:00-22:00 day D+1). The 24th CT hour
    (CT 23:00 day D+1) belongs to "EPT day D+2", which PJM does not
    publish until 17:00 CT day D+1.

    At a 21:00 CT day-D decision for tomorrow's precool window, CT
    hour 23 is therefore **structurally unavailable** by the
    publish-schedule of the PJM market, not by any missing-data fault.
    This function treats the "only CT hour 23 missing" case as valid
    coverage: pads hour 23 with hour 22's price so the returned
    24-element vector is complete and the §7 cheap-window search range
    (hours 6-14) and typical spike search (hours 10-22, all real PJM
    data) are unaffected. Padding produces no false-positive spike — a
    spike at hour 22 already detected extends to hour 23 with no new
    maximum; absence of a spike at hour 22 means no spike at the padded
    hour 23 either.

    Any other coverage gap (interior missing hours, fewer than 23
    contiguous hours from CT hour 0, etc.) returns None — those are
    genuine insufficient coverage and the §7 decision must short-
    circuit rather than guess.
    """
    target = datetime.fromisoformat(target_date_iso).replace(tzinfo=tz)
    start_local = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).isoformat()
    end_utc = end_local.astimezone(timezone.utc).isoformat()
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: {start_utc}, stop: {end_utc})
          |> filter(fn: (r) => r._measurement == "pjm.lmp_da_hourly"
                                and r.zone == "COMED"
                                and r._field == "total_lmp_da")
          |> sort(columns: ["_time"])
    """
    # Map each row to its CT-hour-of-day via the record's _time. The
    # explicit hour mapping lets us distinguish the structural
    # end-of-day boundary case (hour 23 missing) from interior gaps
    # — implicit list ordering alone collapses both into "fewer than
    # 24 rows" with no recoverable signal.
    prices_by_hour: dict[int, float] = {}
    for table in query_api.query(flux):
        for record in table.records:
            try:
                rec = project_record(record)
            except ValueError:
                continue
            time_ct = rec.time_utc.astimezone(tz)
            if time_ct.date().isoformat() != target_date_iso:
                # Row landed in our UTC range but maps to a different
                # CT calendar date (can happen if PJM EPT-hour 00:00
                # of the target EPT day = CT 23:00 of the prior CT
                # day). Skip; it belongs to a different precool
                # decision.
                continue
            prices_by_hour[time_ct.hour] = rec.value

    if not prices_by_hour:
        return None

    missing_hours = set(range(24)) - set(prices_by_hour)
    if missing_hours == {23}:
        # EPT-vs-CT structural boundary — pad hour 23 with hour 22.
        # See module docstring.
        prices_by_hour[23] = prices_by_hour[22]
    elif missing_hours:
        # Genuinely insufficient coverage; short-circuit.
        return None

    # $/MWh -> cents/kWh: ÷ 10.
    return [prices_by_hour[h] / 10.0 for h in range(24)]


def compute_price_aware_precool_window(
    query_api: Any, bucket: str, target_date_iso: str, tz: ZoneInfo,
    *, forecast_period: str = "tomorrow",
    trace_reason: list[str] | None = None,
) -> dict[str, int] | None:
    """Resolve the §7 day-ahead price-aware pre-cool window for the
    target date. Composes fetch_day_ahead_prices_for_date,
    fetch_latest_forecast, and the pure ``should_add_price_aware_precool``
    decision rule.

    ``forecast_period`` selects which ``nws.forecast`` row the function
    reads ("tomorrow" at 21:00 the night before; "today" for runtime
    re-evaluation in run_schedule_check). Returns None when either
    input is unavailable or the decision rule says no window applies.

    Optional ``trace_reason``: when caller passes a mutable list, the
    function appends ONE PrecoolCode value reflecting the outcome
    ("PRECOOL_SELECTED" on happy path; one of the rejection codes
    otherwise). Default ``None`` means no overhead and no behaviour
    change. This is the Phase 4 dict-mutation-via-out-param pattern,
    propagated from the inner ``should_add_price_aware_precool`` call.

    The ComEd Delivery TOD rate schedule (P2.6) is always layered on
    top of the supply prices for cheap-window *ranking*. Chris is
    enrolled in DTOD; the schedule is fixed year-round and identical
    every day, so we build the delivery vector from the static table
    rather than from InfluxDB.
    """
    prices = fetch_day_ahead_prices_for_date(query_api, bucket, target_date_iso, tz)
    if prices is None:
        if trace_reason is not None:
            trace_reason.append(PrecoolCode.REJECTED_NO_DA_LMP_DATA.value)
        return None
    forecast = fetch_latest_forecast(query_api, bucket, forecast_period)
    if forecast is None:
        if trace_reason is not None:
            trace_reason.append(PrecoolCode.REJECTED_NO_FORECAST.value)
        return None
    return should_add_price_aware_precool(
        prices, forecast, delivery_rates_cents=dtod_delivery_rates_24h(),
        trace_reason=trace_reason,
    )


def write_precool_window(
    write_api: Any, bucket: str, target_date_iso: str, window: dict[str, int],
) -> None:
    """Persist a §7 price-aware pre-cool window to InfluxDB so the
    schedule-check tick can read it back the next day. ``hvac.precool_window``
    is event-sourced (one row per decision); the schedule check looks up
    the latest row matching today's target_date tag."""
    write_point(
        write_api, bucket, "hvac.precool_window",
        tags={
            "target_date": target_date_iso,
            "source": "decision",  # vs "schedule_check_recompute"
        },
        fields={
            "hour_ct": int(window["hour_ct"]),
            "depth_f": int(window["depth_f"]),
        },
    )


def read_precool_window_for_date(
    query_api: Any, bucket: str, target_date_iso: str,
) -> dict[str, int] | None:
    """Look up the most recent ``hvac.precool_window`` row for
    ``target_date_iso``. Returns ``{"hour_ct": int, "depth_f": int}`` or
    None when no row was written (no qualifying day-ahead pattern, or
    the 21:00 decision didn't run)."""
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: -36h)
          |> filter(fn: (r) => r._measurement == "hvac.precool_window"
                                and r.target_date == "{target_date_iso}")
          |> last()
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    """
    for table in query_api.query(flux):
        for record in table.records:
            hour_ct = record.values.get("hour_ct")
            depth_f = record.values.get("depth_f")
            if hour_ct is not None and depth_f is not None:
                return {"hour_ct": int(hour_ct), "depth_f": int(depth_f)}
    return None


def merge_same_hour_actions_deepest_wins(
    schedule: list[ScheduleAction],
) -> list[ScheduleAction]:
    """Per ARM_B_IMPLEMENTATION §7: 'If pre-cool would land on the same
    hour through multiple decisions, deepest setpoint wins.' Used after
    a §7 price-aware pre-cool action is layered on top of the base
    day-type schedule. release_hold actions don't carry a setpoint so
    they're treated as 'absent setpoint' for the merge — a conflict
    between release_hold and a setpoint action is resolved in favour of
    the setpoint (running a setpoint is strictly more conservative than
    clearing the hold).
    """
    by_time: dict[tuple[int, int], ScheduleAction] = {}
    for action in schedule:
        key = (action.hour, action.minute)
        existing = by_time.get(key)
        if existing is None:
            by_time[key] = action
            continue
        if existing.release_hold and not action.release_hold:
            by_time[key] = action
            continue
        if action.release_hold and not existing.release_hold:
            continue  # keep existing setpoint action
        # Both have setpoints (or both release_hold); pick deepest.
        existing_cool = existing.cool_setpoint if existing.cool_setpoint is not None else 999
        new_cool = action.cool_setpoint if action.cool_setpoint is not None else 999
        if new_cool < existing_cool:
            by_time[key] = action
    return sorted(by_time.values(), key=lambda a: (a.hour, a.minute))


def precool_window_action(window: dict[str, int]) -> ScheduleAction:
    """Synthesize a ScheduleAction at the §7 cheap-window's start hour
    with the depth_f the decision rule selected. fan_mode left None so
    the action doesn't override the base schedule's fan setting (the
    ECM blower's circulate cycle stays in place during cheap-window
    pre-cool the same way it does during HOT_PRE_COOL)."""
    return ScheduleAction(
        hour=int(window["hour_ct"]),
        minute=0,
        label="PRICE_AWARE_PRECOOL",
        cool_setpoint=int(window["depth_f"]),
        heat_setpoint=HEAT_SETPOINT_FLOOR,
        fan_mode=None,
    )


def write_5cp_state(
    write_api: Any, bucket: str,
    *, scope: str,
    is_active: bool,
    current_load_mw: float,
    season_5th_highest_mw: float,
    load_derivative_mw_per_hour: float,
    forecast_peak_today_mw: float,
    zone: str,
) -> None:
    """Write one ``hvac.5cp_state`` row per scheduler tick per scope so
    the detector's decisions are auditable. Tagged by ``scope``
    (``comed_zone`` | ``rto``), ``zone`` (``CE`` | ``RTO``), and
    ``is_active``. Up to two rows per audit interval (the caller
    skips a scope whose data_status != "ok", so a transient PJM
    inst_load gap for one scope still records the other rather than
    fabricating audit rows from absent inputs)."""
    ratio = current_load_mw / season_5th_highest_mw if season_5th_highest_mw > 0 else 0.0
    write_point(
        write_api, bucket, "hvac.5cp_state",
        tags={
            "scope": scope,
            "zone": zone,
            "is_active": "true" if is_active else "false",
        },
        fields={
            "current_load_mw": float(current_load_mw),
            "season_5th_highest_mw": float(season_5th_highest_mw),
            "load_ratio": float(ratio),
            "load_derivative_mw_per_hour": float(load_derivative_mw_per_hour),
            "forecast_peak_today_mw": float(forecast_peak_today_mw),
        },
    )


def write_price_overlay_transition(
    write_api: Any, bucket: str,
    *, prev_tier: str, new_tier: str,
    current_price_cents: float,
    schedule_cool_f: float, effective_cool_f: float,
    triggered_at_utc: datetime | None,
) -> None:
    """Write one ``hvac.price_overlay`` row when the price-overlay tier
    changes between scheduler ticks. Skipped when the tier is unchanged
    so dashboards aren't drowned in no-op rows."""
    write_point(
        write_api, bucket, "hvac.price_overlay",
        tags={
            "prev_tier": prev_tier,
            "new_tier": new_tier,
        },
        fields={
            "current_price_cents": float(current_price_cents),
            "schedule_cool_f": float(schedule_cool_f),
            "effective_cool_f": float(effective_cool_f),
            "triggered_at_utc":
                triggered_at_utc.isoformat() if triggered_at_utc else "",
        },
    )


def write_action(write_api: Any, bucket: str, day_type: str, action: ScheduleAction,
                 cool_applied_f: float, heat_applied_f: float,
                 fan_mode_applied: str | None,
                 setpoint_reason: str, dry_run: bool, applied: bool,
                 thermostat_state_before: dict[str, Any], error: str | None = None,
                 layer_resolution: LayerResolution | None = None) -> None:
    tags: dict[str, str] = {
        "day_type": day_type,
        "action_label": action.label,
        "dry_run": "true" if dry_run else "false",
    }
    fields: dict[str, float | int | bool | str] = {
        "cool_setpoint_f": float(cool_applied_f),
        "heat_setpoint_f": float(heat_applied_f),
        "fan_mode": fan_mode_applied or "",
        "setpoint_reason": setpoint_reason,
        "cool_setpoint_proposed_f": float(action.cool_setpoint or 0),
        "heat_setpoint_proposed_f": float(action.heat_setpoint),
        "applied": int(applied),
        "error": error or "",
        "hvac_mode_before": str(thermostat_state_before.get("hvac_mode") or ""),
        "indoor_temp_before_f": float(thermostat_state_before.get("indoor_temp_f") or 0),
        "cool_setpoint_before_f": float(thermostat_state_before.get("cool_setpoint_f") or 0),
        "heat_setpoint_before_f": float(thermostat_state_before.get("heat_setpoint_f") or 0),
        "indoor_humidity_before_pct": float(thermostat_state_before.get("humidity") or 0),
    }
    if layer_resolution is not None:
        # Layer-priority audit fields (§4). Always emitted when the
        # resolution is computed so dashboards can answer "why was the
        # effective setpoint different from the schedule baseline?"
        tags["price_overlay_tier"] = layer_resolution.price_overlay_tier
        tags["fivecp_active"] = "true" if layer_resolution.fivecp_active else "false"
        fields["schedule_cool_f"] = float(layer_resolution.schedule_cool)
        fields["price_cool_f"] = float(layer_resolution.price_cool)
        fields["fivecp_cool_f"] = float(layer_resolution.fivecp_cool)
        fields["effective_cool_f"] = float(layer_resolution.effective_cool)
    write_point(write_api, bucket, "hvac.actions", tags=tags, fields=fields)


async def read_thermostat_snapshot(c4: C4Client) -> dict[str, Any]:
    climate = await c4.get_climate()
    snapshot = {}
    try:
        snapshot["indoor_temp_f"] = await c4.call_with_reauth(climate.get_current_temperature_f)
        snapshot["cool_setpoint_f"] = await c4.call_with_reauth(climate.get_cool_setpoint_f)
        snapshot["heat_setpoint_f"] = await c4.call_with_reauth(climate.get_heat_setpoint_f)
        snapshot["hvac_mode"] = await c4.call_with_reauth(climate.get_hvac_mode)
        snapshot["hvac_state"] = await c4.call_with_reauth(climate.get_hvac_state)
        snapshot["fan_mode"] = await c4.call_with_reauth(climate.get_fan_mode)
        snapshot["hold_mode"] = await c4.call_with_reauth(climate.get_hold_mode)
        snapshot["humidity"] = await c4.call_with_reauth(climate.get_humidity)
    except Exception as exc:
        log("warn", "thermostat_read_failed", error=str(exc), error_type=type(exc).__name__)
    return snapshot


# Pre-registered capacity-risk operating window per spec §5.1. Outside
# this window PJM capacity-risk inputs are not required for B-active
# classification (the controller's capacity-risk overlay layer is
# inactive by design). Inclusive of 2026-06-01 through 2026-09-30.
CAPACITY_RISK_WINDOW_START_CT = datetime(2026, 6, 1, 0, 0)
CAPACITY_RISK_WINDOW_END_CT = datetime(2026, 10, 1, 0, 0)  # exclusive


# Feeds required by each controller mode. The reactive warm-only overlay
# (always enabled) consumes the live ComEd price feed only. No enabled
# mode consumes weather, day-ahead forecasts, or PJM capacity-risk, so
# none of those are required for B-active classification. Adding a mode
# that needs a new feed extends this map.
_FEED_REQUIREMENTS_BY_MODE: dict[str, tuple[str, ...]] = {
    "price_overlay": ("price",),
}

# Modes enabled in the commissioning controller. The price overlay is the
# entire controller (spec "Reactive core"); it is unconditionally enabled.
_ENABLED_MODES: tuple[str, ...] = ("price_overlay",)


def required_feeds_for_arm_mode(*, when_ct: datetime, price_feed_healthy: bool,
                                  weather_ok: bool,
                                  pjm_capacity_risk_ok: bool) -> dict[str, bool]:
    """Return the dict of input-feed health flags REQUIRED for B-active
    classification, derived from the enabled-mode set (spec "Telemetry":
    required_feeds_for_arm_mode is derived from the enabled-mode set, not a
    hardcoded dict).

    Only feeds consumed by an enabled mode are required. The reactive
    warm-only overlay is the sole enabled mode and consumes the live price
    feed only, so the required set is ``{"price": ...}``. ``weather`` is
    dropped: no enabled mode consumes it (day-types / forecasts / deep
    precool are gone). PJM capacity-risk is likewise not required (5CP is
    planning/telemetry only).

    The full feed-health audit (every feed, including weather and PJM) is
    written separately by ``write_input_feed_health`` so the operator still
    sees their status in telemetry. ``weather_ok`` / ``pjm_capacity_risk_ok``
    / ``when_ct`` are accepted for caller-signature stability and are
    intentionally unused here.
    """
    _ = when_ct  # reserved
    _ = weather_ok  # no enabled mode consumes weather
    _ = pjm_capacity_risk_ok  # 5CP is planning/telemetry only, not required

    health_by_feed = {"price": price_feed_healthy}
    required: set[str] = set()
    for mode in _ENABLED_MODES:
        required.update(_FEED_REQUIREMENTS_BY_MODE.get(mode, ()))
    return {feed: health_by_feed[feed] for feed in health_by_feed if feed in required}


def _planned_boundary_ts(when_naive: datetime) -> datetime | None:
    """Return the calendar's intended boundary timestamp covering
    ``when_naive``: the start_ct of the arm period containing it, or
    the experiment end if past the last arm.
    """
    for arm in ARM_CALENDAR:
        if arm.start_ct <= when_naive < arm.end_ct:
            return arm.start_ct
    if when_naive >= ARM_CALENDAR[-1].end_ct:
        return ARM_CALENDAR[-1].end_ct
    return None


def maybe_log_arm_switch(write_api: Any, bucket: str, last_arm: str | None,
                          *, arm_observed: bool,
                          when_ct: datetime) -> tuple[str | None, bool]:
    """Detect arm-boundary crossings (spec §11 #3) and write
    ``hvac.switch_event`` rows when the active arm differs from
    ``last_arm``. Returns ``(current_arm, arm_observed=True)`` so the
    caller can update its FiringState.

    ``arm_observed`` is the cold-start guard. False on first call after
    process boot: the function seeds ``last_observed_arm`` without
    logging (a mid-arm controller restart is not a calendar boundary).
    True on every subsequent call: real changes between ``last_arm``
    and ``current_arm`` ARE boundaries and ARE logged — including the
    None -> A transition at experiment start (2026-06-01 00:00 CT).
    """
    when_naive = when_ct.replace(tzinfo=None) if when_ct.tzinfo else when_ct
    current_arm = current_arm_at(when_naive)
    if not arm_observed:
        # Cold start: seed FiringState, no log.
        return current_arm, True
    if current_arm == last_arm:
        return current_arm, True

    planned_ts = _planned_boundary_ts(when_naive)
    write_point(
        write_api, bucket, "hvac.switch_event",
        tags={},
        fields={
            "from_arm": last_arm or "",
            "to_arm": current_arm or "",
            "boundary_planned_ts": planned_ts.isoformat() if planned_ts else "",
            "boundary_actual_ts": when_naive.isoformat(),
        },
        time=when_ct,
    )
    return current_arm, True


def write_input_feed_health(write_api: Any, bucket: str, when_ct: datetime,
                              feeds: dict[str, bool]) -> None:
    """Write one ``hvac.input_feed_health`` row per feed (spec §11 #4).

    ``feeds`` is the FULL feed-health dict (every feed, regardless of
    whether it is required for the current hour's B-active
    classification). Per spec §5.1, PJM capacity-risk health is
    logged here even outside the capacity-risk operating window so
    reviewers can audit feed availability across the whole experiment;
    the B-active classification (``write_arm_mode``) uses a separately
    filtered ``required_feeds`` dict.
    """
    for feed_name, healthy in feeds.items():
        write_point(
            write_api, bucket, "hvac.input_feed_health",
            tags={"feed": feed_name},
            fields={"healthy": bool(healthy)},
            time=when_ct,
        )


def write_arm_mode(write_api: Any, bucket: str, when_ct: datetime,
                    required_feeds: dict[str, bool], controller_alive: bool) -> None:
    """Write one ``hvac.arm_mode`` row classifying the current cycle.

    Per spec §11 #2 + §5: in-window classification is one of A-active
    / B-active / B-fallback / B-down (carried in ``mode_actual`` with
    ``arm`` tag) — but ONLY when ``SCHEDULER_MODE=experiment`` (the
    spec §3 mandated mode for the locked window). If the operator
    leaves the scheduler in shadow or switches to production during
    the experiment window, the spec §5 four-mode classification does
    NOT apply: shadow means no thermostat writes (B-active would
    falsely claim the smart controller delivered treatment when it
    didn't), production is explicitly off-protocol (excluded from
    analysis per spec §3). For those cases emit
    ``mode_actual="off-protocol-shadow"`` / ``"off-protocol-production"``
    so the analysis pipeline can EXCLUDE those hours from the primary
    outcome rather than mis-attribute exposure.

    Outside the locked window the controller is still alive and ticking;
    we emit a liveness-only row with ``mode_actual="outside-window"``
    so the watchdog (spec §11 #5, queries ``hvac.arm_mode``) doesn't
    fire false ``controller_alive=false`` during shadow weeks.

    Every emitted row carries a ``scheduler_mode`` tag so the analysis
    pipeline can join arm-mode rows to the operator's mode setting
    without re-deriving it.

    ``required_feeds`` is the dict of input-feed health flags that the
    caller has already filtered to the feeds REQUIRED for this hour
    (per spec §5.1, PJM capacity-risk inputs are only required during
    the capacity-risk operating window). All-true = healthy. The
    full feed-health audit (all feeds, regardless of required-status)
    is written separately by ``write_input_feed_health`` so reviewers
    can see staleness on optional feeds too.

    ``controller_alive`` is normally True for in-process writes; the
    out-of-band watchdog (Task 1.6) writes ``hvac.heartbeat`` rows
    independently.
    """
    when_naive = when_ct.replace(tzinfo=None) if when_ct.tzinfo else when_ct
    arm = current_arm_at(when_naive)
    scheduler_mode = os.environ.get("SCHEDULER_MODE", SCHEDULER_MODE)
    if arm is None:
        write_point(
            write_api, bucket, "hvac.arm_mode",
            tags={"scheduler_mode": scheduler_mode},
            fields={"mode_actual": "outside-window"},
            time=when_ct,
        )
        return
    if scheduler_mode != "experiment":
        # In-window protocol deviation: the spec §5 four-mode
        # classification only applies when SCHEDULER_MODE=experiment.
        write_point(
            write_api, bucket, "hvac.arm_mode",
            tags={"scheduler_mode": scheduler_mode, "arm": arm},
            fields={"mode_actual": f"off-protocol-{scheduler_mode}"},
            time=when_ct,
        )
        return
    if arm == "A":
        mode_actual = "A-active"
    elif not controller_alive:
        mode_actual = "B-down"
    elif not all(required_feeds.values()):
        mode_actual = "B-fallback"
    else:
        mode_actual = "B-active"
    write_point(
        write_api, bucket, "hvac.arm_mode",
        tags={"scheduler_mode": scheduler_mode, "arm": arm},
        fields={"mode_actual": mode_actual},
        time=when_ct,
    )


async def execute_action(c4: C4Client, action: ScheduleAction,
                          cool_setpoint_to_apply: float,
                          heat_setpoint_to_apply: float,
                          state: dict[str, Any], dry_run: bool,
                          when_ct: datetime | None = None,
                          ) -> tuple[bool, str | None]:
    """Apply the action to the thermostat. Returns (applied, error).

    Both setpoints are passed in explicitly (rather than read from
    `action.heat_setpoint` / etc.) so the caller controls what is applied
    (e.g. the floor-clamped effective from ``_push_baseline_if_changed``).

    Two execution paths:
      * release_hold action: clears the Permanent hold so the thermostat's
        baseline schedule resumes. Idempotent; runs regardless of hvac_mode
        (set_hold_mode("Schedule") is safe even in Heat/Off — it just becomes
        a no-op when no hold is active).
      * Setpoint action: applies heat + cool setpoints, optional fan mode,
        then HOLD_MODE='Permanent' to pin the override against the
        thermostat's own schedule. Skipped when hvac_mode is not Cool/Auto
        (so we don't accidentally fight a heating-season furnace).

    Two write-gates (defense in depth):
      1. SCHEDULER_MODE gate (spec §3, this top-level check) — blocks
         shadow mode, blocks experiment mode outside Arm B periods,
         blocks experiment mode outside the locked window.
      2. Legacy ``dry_run`` parameter — kept for the comprehensive
         dry-run audit (plan Task 1.7).
    """
    if when_ct is None:
        when_ct = datetime.now()

    if not _writes_allowed(when_ct):
        return False, None

    if dry_run:
        return False, None  # logged as not-applied with no error

    if action.release_hold:
        try:
            climate = await c4.get_climate()
            await c4.call_with_reauth(lambda: climate.set_hold_mode("Schedule"))
            return True, None
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    hvac_mode = state.get("hvac_mode") or ""
    if hvac_mode not in ("Cool", "Auto"):
        return False, f"hvac_mode_not_cooling ({hvac_mode!r})"
    try:
        climate = await c4.get_climate()
        # Always set both heat and cool — protects against narrow-deadband
        # auto-widening when in Auto mode (Honeywell ISU 300 enforces deadband).
        #
        # **Heat first, then cool** (P1.3 adversarial-review fix). When
        # transitioning down to a low cool target (e.g., HOT_PRE_COOL=68F
        # or HOT_STREAK_DAY1=66F) while the existing heat setpoint is
        # higher than (target_cool - deadband), sending cool first can
        # be auto-adjusted by the thermostat before heat moves into
        # range. Setting heat first pins the floor at 65F so the
        # subsequent cool push lands at any locked value down to 68F
        # without the deadband fighting it. Symmetric for cool-going-up
        # transitions (no change in behaviour). Defensive ordering.
        await c4.call_with_reauth(lambda: climate.set_heat_setpoint_f(heat_setpoint_to_apply))
        await asyncio.sleep(1)
        await c4.call_with_reauth(lambda: climate.set_cool_setpoint_f(cool_setpoint_to_apply))
        await asyncio.sleep(1)
        # Apply fan mode if specified for this period (e.g., Circulate during coast)
        if action.fan_mode:
            fan_mode = action.fan_mode  # bind locally so the lambda closure carries the narrowed str type
            await c4.call_with_reauth(lambda: climate.set_fan_mode(fan_mode))
            await asyncio.sleep(1)
        # Pin the override so thermostat baseline doesn't override our setpoint
        await c4.call_with_reauth(lambda: climate.set_hold_mode("Permanent"))
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


@dataclass(frozen=True)
class LayerInputs:
    """Per-tick output of `_evaluate_layer_inputs`. Captures everything
    needed to call `resolve_layer_priority` plus the audit context for
    `hvac.price_overlay` and `hvac.5cp_state` writes.

    ``fivecp_active`` is the OR across both detector scopes (ComEd zone
    and PJM RTO). ``fivecp_scopes_fired`` lists the scope names that
    contributed -- ("comed_zone",), ("rto",), or both. Empty tuple
    means no scope triggered. Downstream logs and audit rows use the
    detail for attribution.

    ``fivecp_load_mw`` / ``fivecp_derivative`` reflect the COMED scope
    inputs for backward-compat with existing single-scope dashboards;
    per-scope detail is in ``hvac.5cp_state`` rows tagged by scope.
    """
    price_tier_name: str
    price_offset_f: float
    price_override_f: float | None
    price_prev_tier: str
    current_price_cents: float | None
    fivecp_active: bool
    fivecp_scopes_fired: tuple[str, ...]
    fivecp_load_mw: float
    fivecp_derivative: float
    fivecp_forecast_peak: float
    fivecp_season_5th_mw: float
    fivecp_data_available: bool


_FIVECP_AUDIT_INTERVAL = timedelta(minutes=5)
# Same 5-min cadence for arm-mode / feed-health / switch-event telemetry
# (spec §11 #2-4) so analysis sees a uniform 288-rows/day trace.
_ARM_MODE_AUDIT_INTERVAL = timedelta(minutes=5)

# P2.2 reviewer-flagged 2026-05-11: a carried-forward price-overlay
# tier (preserved across a brief feed gap per PR #60's P2.A fix) must
# not hold indefinitely. If the ComEd RTP feed has been unavailable
# for longer than this threshold, the overlay releases back to NORMAL
# tier. 30 minutes mirrors the minimum-hold window for tier
# transitions (price_overlay.DEFAULT_MINIMUM_HOLD_MINUTES) -- if a
# tier event can resolve in 30 min on healthy data, an outage of
# similar length is plausibly a real release rather than a brief blip.
PRICE_FEED_STALE_THRESHOLD = timedelta(minutes=30)


def derive_price_feed_healthy(firing: "FiringState", now_utc: datetime) -> bool:
    """Broad feed-health verdict per spec §3.6.

    Returns True iff the controller has observed a fresh ComEd bucket
    within the last PRICE_FEED_STALE_THRESHOLD (30 min wall-clock).

    Used by:
      * The hvac.input_feed_health audit row (run_schedule_check).
      * required_feeds_for_arm_mode for B-active classification.

    This is DISTINCT from per-tick downgrade actionability
    (sample.freshness == "fresh", 7-min threshold). The named-split in
    spec §3.6 prevents implementation-time conflation: an implementer
    cannot accidentally write
        return sample.freshness == "fresh"
    because the function reads firing state, not the per-tick sample.

    The safety-release timer (firing.nonfresh_after_hold_started_at_utc,
    spec §3.5) is yet a third concept -- uses controller-observation wall
    clock, not data-source. All three are independent.
    """
    last_fresh = firing.last_fresh_bucket_source_ts
    if last_fresh is None:
        return False
    return (now_utc - last_fresh) <= PRICE_FEED_STALE_THRESHOLD


# Action make-up window: a scheduled action that didn't successfully
# apply on its exact-minute tick (Control4 transient error, network
# blip, partial snapshot) can be retried on the next N ticks until
# either (a) it succeeds and gets marked done, or (b) the window
# elapses. Without this window, the exact-minute match in the
# action-fire loop would never re-fire after a transient failure
# and a failed 19:00 HOT_RECOVER (for example) would silently
# leave the thermostat in the prior schedule's setpoint until
# 21:00 SLEEP. Five minutes is short enough that an action's
# intent stays close to its scheduled time but long enough to
# absorb typical transient C4 / network blips.
ACTION_MAKEUP_WINDOW_MIN = 5


def _evaluate_layer_inputs(query_api: Any, write_api: Any, cfg: Config,
                            firing: FiringState, now_local: datetime,
                            *, tick_id: str | None = None) -> LayerInputs:
    """Per-tick evaluation of the §2 price overlay and §3 5CP detector,
    independent of whether a scheduled action is firing this minute.

    Side effects:
      * Updates ``firing.price_overlay_state`` and both
        ``firing.fivecp_state_comed`` / ``firing.fivecp_state_rto``.
      * Writes ``hvac.price_overlay`` on tier transitions only.
      * Writes ``hvac.5cp_state`` at most once every 5 min (throttled via
        ``firing.last_5cp_audit_at_utc``) so dashboards see the ~288
        rows/day cadence the validation procedure asserts.

    ``now_utc`` is derived from ``now_local`` rather than read from
    wall-clock so tests can drive the throttle window and so audit
    timestamps stay consistent with the rest of the scheduler tick.

    Per EXPERIMENT_DESIGN §3 item 5: "Continuous overlay on the active
    scheduled setpoint, evaluated each scheduler tick" — and §3 item 6
    similarly for 5CP. Pre-§Critical#2 this code lived inside the action-
    fire loop body and only ran 4-6 times/day; mid-window price spikes
    fell through unobserved.
    """
    now_utc = now_local.astimezone(timezone.utc)
    # tick_id is the JSON FIELD correlation id shared across every
    # decision-trace line emitted within one scheduler tick. Phase 2
    # lifts the generation to `run_schedule_check` so layer-resolution
    # traces share it with the price-overlay trace; this function
    # generates a fresh one when called without one (test compatibility
    # + defense in depth for any path that calls _evaluate_layer_inputs
    # outside the main tick loop). Must NOT be promoted to a Loki label
    # (cardinality, see decision-trace plan locked decisions).
    if tick_id is None:
        tick_id = uuid.uuid4().hex

    # ---- Price overlay (§2) ----
    # Read the latest ComEd bucket with now_utc for freshness classification.
    sample = fetch_latest_comed(query_api, cfg.influx_bucket, now_utc=now_utc)
    current_price_cents = sample.cents_per_kwh if sample is not None else None
    prev_tier = firing.price_overlay_state.current_tier

    # Update last-fresh field on fresh reads (independent of timer; used by
    # audit telemetry's broad-feed-health derivation, §3.6).
    if sample is not None and sample.freshness == "fresh":
        firing.last_fresh_bucket_source_ts = sample.source_ts

    # Safety-release TIMER update (spec §3.5, controller-observation wall-clock).
    # IMPORTANT: this is the CONTROLLER-OBSERVATION clock. Do NOT use
    # sample.source_ts or last_fresh_bucket_source_ts for this timer --
    # those are data-source timestamps; spec §3.5 explicitly forbids it.
    sample_is_fresh = sample is not None and sample.freshness == "fresh"
    min_hold_is_elapsed = hold_elapsed(
        firing.price_overlay_state, now_utc, DEFAULT_MINIMUM_HOLD_MINUTES,
    )

    if prev_tier == NORMAL_TIER_NAME or not min_hold_is_elapsed:
        firing.nonfresh_after_hold_started_at_utc = None
    elif sample_is_fresh:
        firing.nonfresh_after_hold_started_at_utc = None
    elif firing.nonfresh_after_hold_started_at_utc is None:
        firing.nonfresh_after_hold_started_at_utc = now_utc
    # else: timer was already set on a prior tick; leave it alone.

    # Initialize trace-field defaults BEFORE the release/gate branches (spec §3.5 P2).
    safety_release_fired = False
    release_reason = None
    downgrade_gate_held = False
    active_tier = None
    price_offset_f = 0.0
    price_override_f = None
    price_tier_name = prev_tier  # default; branches below override.

    # Safety release check -- uses ONLY the controller-observation timer.
    if (firing.nonfresh_after_hold_started_at_utc is not None
            and (now_utc - firing.nonfresh_after_hold_started_at_utc)
                >= PRICE_FEED_STALE_THRESHOLD
            and prev_tier != NORMAL_TIER_NAME):
        # Forensic split: which kind of failure accumulated to 30 wall-clock min?
        release_reason = (
            PriceOverlayCode.RELEASED_NO_DATA if sample is None
            else PriceOverlayCode.RELEASED_PERSISTENT_STALE
        )
        log("warn", "price_feed_stale_tier_released",
            reason=release_reason.value,
            timer_started_at=firing.nonfresh_after_hold_started_at_utc.isoformat(),
            wall_clock_elapsed_sec=(now_utc - firing.nonfresh_after_hold_started_at_utc).total_seconds())
        firing.price_overlay_state = PriceOverlayState(
            current_tier=NORMAL_TIER_NAME,
            triggered_at_utc=None,
        )
        firing.nonfresh_after_hold_started_at_utc = None  # clear after release
        safety_release_fired = True
        # Explicit normal outputs -- do NOT inherit prev_tier's offset/override.
        price_tier_name = NORMAL_TIER_NAME
        price_offset_f = 0.0
        price_override_f = None
        active_tier = None

    elif sample is not None:
        # State machine + caller-side gate (T12 logic).
        # current_price_cents is sample.cents_per_kwh in this branch (line
        # above), so the `sample is not None` guard implies non-None price.
        assert current_price_cents is not None
        proposed_tier, proposed_state = evaluate_price_overlay(
            current_price_cents, firing.price_overlay_state, now_utc,
        )
        proposed_name = proposed_tier.name if proposed_tier else NORMAL_TIER_NAME
        is_downgrade = tier_priority(proposed_name) < tier_priority(prev_tier)

        if is_downgrade and not sample_is_fresh:
            # Recency gate refuses downgrade. Hold prev_tier.
            downgrade_gate_held = True
            price_offset_f, price_override_f = offset_and_override_for_tier(prev_tier)
            price_tier_name = prev_tier
        else:
            # Detect protective upgrade and clear the safety-release timer so
            # the new tier gets its own observation window. Without this clear,
            # a delayed-next-tick after a non-fresh upgrade could fire release
            # against the old tier's accumulated non-fresh time. See Codex
            # Checkpoint-3 finding. Unconditional clear: no-op during the
            # previous tier's min-hold (timer was already None per the reset
            # rules above), necessary post-min-hold.
            is_upgrade = tier_priority(proposed_name) > tier_priority(prev_tier)
            if is_upgrade:
                firing.nonfresh_after_hold_started_at_utc = None
            # Apply state machine proposal.
            firing.price_overlay_state = proposed_state
            active_tier = proposed_tier
            if active_tier is None:
                price_offset_f = 0.0
                price_override_f = None
                price_tier_name = NORMAL_TIER_NAME
            else:
                price_offset_f = active_tier.cool_setpoint_offset
                price_override_f = active_tier.cool_setpoint_override
                price_tier_name = active_tier.name

    else:
        # sample is None, timer not yet at 30-min threshold: carry-forward.
        # Preserve prev_tier's offset/override.
        price_offset_f, price_override_f = offset_and_override_for_tier(prev_tier)
        price_tier_name = prev_tier

    # ---- Phase 1 decision-trace: price overlay per-eval ---------------
    # One trace line per `_evaluate_layer_inputs` call. Classifies the
    # outcome from caller-observable state (prev_tier, new_tier,
    # current_price, safety_release_fired) — never re-implements the
    # internal state machine. Held outcomes go at `debug` level (gated
    # on `SCHEDULER_DECISION_TRACE_VERBOSE`); transitions and releases at
    # `info`. See `docs/plans/archive/decision-trace-plan.md` Phase 1.
    new_tier = price_tier_name
    if downgrade_gate_held:
        po_outcome = "held"
        po_reason = PriceOverlayCode.HELD_DOWNGRADE_BUCKET_AGE
        po_level = "info"
    elif safety_release_fired:
        po_outcome = "released"
        # safety_release_fired implies the release branch above set
        # release_reason to RELEASED_NO_DATA or RELEASED_PERSISTENT_STALE.
        assert release_reason is not None
        po_reason = release_reason
        po_level = "warn"  # warn level — real degraded state
    elif current_price_cents is None:
        po_outcome = "held"
        po_reason = PriceOverlayCode.FEED_UNAVAILABLE_TIER_PRESERVED
        po_level = "debug"
    elif prev_tier == new_tier:
        po_outcome = "held"
        po_reason = (
            PriceOverlayCode.NORMAL_BELOW_TRIGGER
            if new_tier == NORMAL_TIER_NAME
            else PriceOverlayCode.HELD_IN_TIER
        )
        po_level = "debug"
    elif new_tier == "scarcity":
        po_outcome = "upgraded"
        po_reason = PriceOverlayCode.UPGRADED_TO_SCARCITY
        po_level = "info"
    elif new_tier == "elevated":
        if prev_tier == NORMAL_TIER_NAME:
            po_outcome = "upgraded"
            po_reason = PriceOverlayCode.UPGRADED_TO_ELEVATED
        else:  # scarcity -> elevated
            po_outcome = "downgraded"
            po_reason = PriceOverlayCode.DOWNGRADED_TO_ELEVATED
        po_level = "info"
    else:  # new_tier == NORMAL_TIER_NAME, prev_tier != NORMAL_TIER_NAME
        po_outcome = "released"
        po_reason = PriceOverlayCode.RELEASED_TO_NORMAL
        po_level = "info"

    bucket_age_sec = (
        (now_utc - sample.source_ts).total_seconds()
        if sample is not None
        else None
    )
    _trace(
        "decision_trace.price_overlay_eval",
        level=po_level,
        tick_id=tick_id,
        now_ct=now_local,
        price_cents=current_price_cents,
        price_feed_unavailable=(current_price_cents is None),
        bucket_age_sec=bucket_age_sec,
        prev_tier=prev_tier,
        new_tier=new_tier,
        outcome=po_outcome,
        reason_code=po_reason.value,
        hold_minutes_remaining=_price_overlay_hold_minutes_remaining(
            firing.price_overlay_state, now_utc,
        ),
    )

    # ---- 5CP detection (§3) ----
    # Two detectors run in parallel: ComEd-zone (catches ComEd 5CPs)
    # and PJM RTO (catches PJM 5CPs). Both contribute to the next-year
    # residential capacity charge per HVAC_LOGIC.md. is_5cp_risk is the
    # OR; structured-log payload records per-scope inputs so a
    # scale-mismatch regression (RTO fallback on ComEd path or vice
    # versa) shows up immediately in logs, not silently in behavior.
    #
    # Cooling-season window (PJM Manual 19 / ComEd Att. M-2: Jun 1 -
    # Sep 30) is the same for both scopes; off-season the detector
    # short-circuits inside evaluate_for_scope and no Flux is issued
    # for the season-5th. Forecast peaks are scope-specific:
    #   * COMED uses pjm.load_forecast{forecast_area=COMED} max-for-today
    #   * RTO   uses pjm.peak_forecast_rto{area="PJM RTO"} latest scalar
    # Sharing one forecast across scopes (the original P1.1 mis-wire)
    # silently disabled the RTO scope because a ComEd-scale forecast
    # (~10-22 GW) never exceeds RTO season-5th (~150 GW).
    season_start_utc, season_end_utc = cooling_season_window_utc(now_local)
    capped_end_utc = (
        min(season_end_utc, now_utc) if in_cooling_season(now_local)
        else season_end_utc
    )
    comed_forecast_peak = fetch_forecast_peak_today(
        query_api, cfg.influx_bucket, tz=ZoneInfo(cfg.tz_name),
    )
    rto_forecast_peak = fetch_rto_peak_forecast_today(
        query_api, cfg.influx_bucket,
    )
    comed_eval = evaluate_for_scope(
        COMED_SCOPE, query_api, cfg.influx_bucket,
        season_start_utc, capped_end_utc,
        comed_forecast_peak, firing.fivecp_state_comed, now_utc,
    )
    rto_eval = evaluate_for_scope(
        RTO_SCOPE, query_api, cfg.influx_bucket,
        season_start_utc, capped_end_utc,
        rto_forecast_peak, firing.fivecp_state_rto, now_utc,
    )
    firing.fivecp_state_comed = comed_eval.new_state
    firing.fivecp_state_rto = rto_eval.new_state

    fivecp_active = comed_eval.is_active or rto_eval.is_active
    fivecp_scopes_fired = tuple(
        name for name, ev in (("comed_zone", comed_eval), ("rto", rto_eval))
        if ev.is_active
    )
    fivecp_data_available = (
        comed_eval.log_fields.get("data_status") == "ok"
        or rto_eval.log_fields.get("data_status") == "ok"
    )

    log("info", "fivecp_eval", comed=comed_eval.log_fields,
        rto=rto_eval.log_fields, is_active=fivecp_active,
        scopes_fired=list(fivecp_scopes_fired))

    # Backward-compat fields for LayerInputs / existing dashboards: use
    # the ComEd-scope snapshot when available, else zeros. Per-scope
    # detail is preserved in hvac.5cp_state rows tagged by scope.
    fivecp_load_mw = (
        comed_eval.snapshot.current_mw if comed_eval.snapshot is not None else 0.0
    )
    fivecp_derivative = (
        comed_eval.snapshot.derivative_mw_per_hour
        if comed_eval.snapshot is not None else 0.0
    )
    fivecp_forecast_peak = comed_forecast_peak if comed_forecast_peak is not None else 0.0
    # comed_eval.season_5th_mw can be None when current-season official
    # metered-load history is insufficient (binding spec §11 #14). The
    # LayerInputs field is `float`; default to 0.0 so dashboards / dry-run
    # paths get a stable value. `fivecp_data_available` is the right gate
    # for "is the 5CP baseline real?" downstream.
    season_5th_mw = comed_eval.season_5th_mw if comed_eval.season_5th_mw is not None else 0.0

    # ---- Audit writes ----
    new_tier = firing.price_overlay_state.current_tier
    if new_tier != prev_tier and current_price_cents is not None:
        # Effective cool isn't fully resolved here (depends on schedule
        # baseline); supply a sentinel and let the action/mid-period
        # caller fill in the audit context if needed. The price-overlay
        # transition row is primarily a tier-history record.
        write_price_overlay_transition(
            write_api, cfg.influx_bucket,
            prev_tier=prev_tier, new_tier=new_tier,
            current_price_cents=current_price_cents,
            schedule_cool_f=firing.last_schedule_cool or 0,
            effective_cool_f=0,  # filled in by mid-period push if it runs
            triggered_at_utc=firing.price_overlay_state.triggered_at_utc,
        )

    if fivecp_data_available and (
        firing.last_5cp_audit_at_utc is None
        or now_utc - firing.last_5cp_audit_at_utc >= _FIVECP_AUDIT_INTERVAL
    ):
        # Per-scope forecast peaks must be passed individually so the
        # audit row records the actual value the detector saw. Sharing
        # one forecast across scopes was the P1.1-post-merge bug: an
        # RTO audit row tagged with a ComEd-scale forecast peak hid
        # the cross-scale gate failure.
        scope_forecast: dict[str, float | None] = {
            COMED_SCOPE.name: comed_forecast_peak,
            RTO_SCOPE.name:   rto_forecast_peak,
        }
        for scope, ev in (
            (COMED_SCOPE, comed_eval), (RTO_SCOPE, rto_eval),
        ):
            if ev.log_fields.get("data_status") != "ok" or ev.season_5th_mw is None:
                continue
            write_5cp_state(
                write_api, cfg.influx_bucket,
                scope=scope.name,
                zone=scope.metered_load_zone,
                is_active=ev.is_active,
                current_load_mw=ev.snapshot.current_mw if ev.snapshot else 0.0,
                season_5th_highest_mw=ev.season_5th_mw,
                load_derivative_mw_per_hour=(
                    ev.snapshot.derivative_mw_per_hour if ev.snapshot else 0.0
                ),
                forecast_peak_today_mw=scope_forecast[scope.name] or 0.0,
            )
        firing.last_5cp_audit_at_utc = now_utc

    return LayerInputs(
        price_tier_name=price_tier_name,
        price_offset_f=price_offset_f,
        price_override_f=price_override_f,
        price_prev_tier=prev_tier,
        current_price_cents=current_price_cents,
        fivecp_active=fivecp_active,
        fivecp_scopes_fired=fivecp_scopes_fired,
        fivecp_load_mw=fivecp_load_mw,
        fivecp_derivative=fivecp_derivative,
        fivecp_forecast_peak=fivecp_forecast_peak,
        fivecp_season_5th_mw=season_5th_mw,
        fivecp_data_available=fivecp_data_available,
    )


async def _push_baseline_if_changed(
    cfg: Config, c4: C4Client, write_api: Any,
    firing: FiringState, now_local: datetime,
    *, tick_id: str | None = None,
) -> None:
    """Push the floor-clamped comfort baseline when it differs from the
    last value pushed.

    The effective cool setpoint is the comfort baseline, floor-clamped so
    it is never below the baseline (``effective = max(effective, baseline)``).
    With no price offset yet (Slice B) the effective equals the baseline,
    but the clamp is applied in code as the load-bearing floor invariant —
    the only clamp in the controller (safety is device-owned; there is no
    software supervisor and no ceiling yet).

    Re-push only when the effective differs from the last value pushed
    (``firing.last_pushed_effective_cool``). Runs in shadow when
    ``SCHEDULER_MODE`` gates writes off; ``execute_action`` is still called
    so the audit row records what WOULD have been pushed.
    """
    if firing.last_schedule_cool is None:
        return  # no baseline computed yet

    if tick_id is None:
        tick_id = uuid.uuid4().hex

    baseline = firing.last_schedule_cool
    assert cfg.controller_config is not None  # caller guarantees config-present path
    heat_floor = cfg.controller_config.heat_floor  # native temp_scale; no conversion

    # Floor clamp — the load-bearing invariant. Effective is never below the
    # baseline. In Slice A there is no price offset so effective_cool == baseline
    # after the clamp (the clamp is defensive/no-op this slice). Slice B sets
    # effective_cool = baseline + price_offset before this line and inherits a
    # working floor invariant.
    # NOTE: a clamp test with teeth (effective < baseline) comes in Slice B.
    effective_cool = baseline  # Slice A: no offset yet
    effective_cool = max(effective_cool, baseline)  # floor invariant

    # No-push short-circuit: nothing to do when the effective is unchanged.
    if effective_cool == firing.last_pushed_effective_cool:
        return

    snapshot = await read_thermostat_snapshot(c4)

    action = ScheduleAction(
        hour=now_local.hour, minute=now_local.minute,
        label="BASELINE",
        cool_setpoint=baseline,
        heat_setpoint=heat_floor,
        fan_mode=None,
    )

    applied, error = await execute_action(
        c4, action, effective_cool, heat_floor, snapshot, cfg.dry_run,
        when_ct=now_local,
    )
    write_action(
        write_api, cfg.influx_bucket, "B", action,
        effective_cool, heat_floor, None, "comfort_baseline",
        cfg.dry_run, applied, snapshot, error,
    )
    log("info", "baseline_push",
        label=action.label,
        cool_setpoint_f=effective_cool,
        baseline_cool_f=baseline,
        prior_effective_cool_f=firing.last_pushed_effective_cool,
        dry_run=cfg.dry_run, applied=applied, error=error)

    # Guard update gated on (dry_run or error is None): a failed live push
    # must NOT update the guard, otherwise a later push would think the
    # value was already on the thermostat.
    if cfg.dry_run or error is None:
        firing.last_pushed_effective_cool = effective_cool


async def run_schedule_check(cfg: Config, c4: C4Client, query_api: Any, write_api: Any,
                              tz: ZoneInfo, now_local: datetime,
                              firing: FiringState) -> None:
    """Compute the comfort baseline for this minute and push it (in shadow)
    when it differs from the last value pushed.

    Single-path commissioning controller (spec "Architecture": in-place,
    single-path rewrite):
      1. Comfort baseline from ``comfort_baseline_cool`` (config), every tick.
      2. Floor clamp — the effective cool setpoint is never below the current
         baseline (``effective = max(effective, baseline)``). This is the ONLY
         clamp: there is no software safety supervisor (device-owned safety)
         and no ceiling yet (Slice C). No price offset yet (Slice B), so the
         effective equals the baseline here.
      3. Push on change — re-push only when the effective differs from the
         last pushed value, via ``_push_baseline_if_changed``.

    Removed vs the old day-type controller: day-type resolution
    (``fetch_today_decision`` / ``schedule_for``), overrides / vacation
    schedules, day-ahead precool injection, the per-action fire loop, the
    startup baseline reconstruction, and the safety supervisor.
    """
    # Generate one decision-trace tick_id per scheduler tick. Every
    # `decision_trace.*` log line emitted from this call shares this id so
    # downstream Loki / LogQL queries can correlate the price-overlay eval
    # and the would-push of a single tick. JSON FIELD only — not promoted
    # to a Loki label (cardinality).
    tick_id = uuid.uuid4().hex

    # Pull today's forecast for the full feed-health audit (weather is no
    # longer required for B-active classification but is still recorded).
    today_forecast = fetch_latest_forecast(query_api, cfg.influx_bucket, "today")

    # ---- Per-tick comfort baseline ----
    # Recompute last_schedule_cool every tick from the comfort_program. This
    # survives a mid-block restart without any startup reconstruction (the
    # value is derived from the clock, not from stored day-type state).
    assert cfg.controller_config is not None, (
        "controller_config is required: the commissioning controller has a "
        "single config-driven path"
    )
    firing.last_schedule_cool = comfort_baseline_cool(
        cfg.controller_config.comfort_program, now_local,
    )
    firing.baseline_initialized = True

    # ---- Per-tick layer evaluation ----
    # Evaluate price overlay + 5CP and write their audit rows every tick.
    # (Slice B re-introduces the price offset into resolve; here the
    # overlay is telemetry only — resolve is the bare comfort baseline.)
    layer_inputs = _evaluate_layer_inputs(
        query_api, write_api, cfg, firing, now_local, tick_id=tick_id,
    )

    # ---- Per-cycle arm-mode + switch-event + feed-health telemetry ----
    # (spec §11 #2-4)
    #
    # arm_mode and input_feed_health share the 5-min cadence of
    # hvac.5cp_state so analysis sees a uniform 288-rows/day trace.
    # Outside the locked experiment window the arm_mode write is a
    # no-op inside ``write_arm_mode``; input_feed_health still fires so
    # feed-availability is audited across the whole observation period.
    #
    # ``maybe_log_arm_switch`` runs every tick (NOT throttled) so a
    # boundary crossing is captured at minute resolution. The function
    # is a no-op when no transition occurred.
    now_utc_for_audit = now_local.astimezone(timezone.utc)
    firing.last_observed_arm, firing.arm_observed = maybe_log_arm_switch(
        write_api, cfg.influx_bucket, firing.last_observed_arm,
        arm_observed=firing.arm_observed, when_ct=now_local,
    )
    if (firing.last_arm_mode_audit_at_utc is None
            or now_utc_for_audit - firing.last_arm_mode_audit_at_utc
            >= _ARM_MODE_AUDIT_INTERVAL):
        # Single source of truth -- same helper the tests in
        # test_derive_price_feed_healthy_* assert against. See spec §3.6
        # named-split rationale: this is the 30-min broad-health verdict,
        # distinct from per-tick downgrade actionability (7-min) and the
        # safety-release timer (controller-observation wall clock).
        price_feed_healthy = derive_price_feed_healthy(firing, now_utc_for_audit)
        weather_ok = today_forecast is not None
        pjm_ok = layer_inputs.fivecp_data_available
        # FULL feed-health dict, written for audit regardless of
        # required-status (spec §5.1).
        all_feeds = {
            "price": price_feed_healthy,
            "weather": weather_ok,
            "pjm_capacity_risk": pjm_ok,
        }
        write_input_feed_health(
            write_api, cfg.influx_bucket, now_local, all_feeds,
        )
        # FILTERED dict for B-active classification (spec §5).
        required_feeds = required_feeds_for_arm_mode(
            when_ct=now_local,
            price_feed_healthy=price_feed_healthy,
            weather_ok=weather_ok,
            pjm_capacity_risk_ok=pjm_ok,
        )
        write_arm_mode(
            write_api, cfg.influx_bucket, now_local, required_feeds,
            controller_alive=True,
        )
        firing.last_arm_mode_audit_at_utc = now_utc_for_audit

    # ---- Push the comfort baseline when it changed ----
    # Single push path: the effective cool setpoint is the floor-clamped
    # comfort baseline (no price offset yet). Re-push only when it differs
    # from the last value pushed.
    await _push_baseline_if_changed(
        cfg, c4, write_api, firing, now_local, tick_id=tick_id,
    )


# ---- Main loop -------------------------------------------------------------

# Wall-clock second-of-minute at which the scheduler tick fires. Chosen
# to fall after the comed-poller's wall-clock :00 poll-write so the
# scheduler reads the same minute's freshest ComEd bucket rather than
# the previous minute's. 10s gives ~9s of headroom for poll fetch+write
# work (typically 1-3s, occasional spikes to 5s). Bumping later is
# trivial if empirical cycle_elapsed proves it's needed.
SCHEDULER_TICK_SECOND = 10


async def main_async(cfg: Config) -> int:
    tz = ZoneInfo(cfg.tz_name)
    log("info", "startup",
        controller_ip=cfg.controller_ip,
        thermostat_id=cfg.thermostat_id,
        dry_run=cfg.dry_run,
        decision_hour=cfg.decision_hour,
        tz=cfg.tz_name)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    query_api = influx.query_api()
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    c4 = C4Client(cfg)

    # Sanity check at startup: prove we can talk to Director
    try:
        snapshot = await read_thermostat_snapshot(c4)
        log("info", "startup_thermostat_snapshot", **snapshot)
    except Exception as exc:
        log("error", "startup_thermostat_unreachable", error=str(exc))
        # Don't exit -- keep retrying on schedule

    # Log any active overrides so they're visible at startup
    today_iso = datetime.now(tz).date().isoformat()
    overrides = load_overrides(cfg.overrides_file)
    log("info", "overrides_loaded",
        path=str(cfg.overrides_file),
        count=len(overrides),
        active_today=bool(find_active_override(overrides, today_iso)),
        all_windows=[(o.from_date, o.to_date, o.day_type, o.cool_setpoint_f, o.note)
                      for o in overrides])

    firing = FiringState()
    stop = asyncio.Event()

    def handle_stop(signum: int, _frame: Any) -> None:
        log("info", "signal_received", signum=signum)
        stop.set()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    health_marker = Path("/tmp/last_tick_ok")
    while not stop.is_set():
        # Wall-clock phase alignment: each tick fires at the next
        # XX:XX:SCHEDULER_TICK_SECOND boundary. Deterministic across
        # restarts (container boot no longer dictates tick phase).
        # `asyncio.wait_for(stop.wait(), timeout=sleep_for)` drops
        # promptly on SIGTERM since stop.set() resolves the future.
        now = datetime.now(tz)
        target = now.replace(second=SCHEDULER_TICK_SECOND, microsecond=0)
        if target <= now:
            target += timedelta(minutes=1)
        sleep_for = (target - now).total_seconds()
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break

        now_local = datetime.now(tz)

        # The day-type 21:00 decision cycle and intra-day forecast
        # revisits are removed: the commissioning controller computes the
        # comfort baseline per tick (no day-type resolution, no precool
        # window to persist the night before).

        # Schedule actions. P2.3 (reviewer-flagged 2026-05-11): the
        # health marker MUST be gated on tick success. Pre-fix the
        # marker was touched unconditionally, so a repeated
        # ``schedule_check_failed`` was visible in logs but
        # invisible to Docker's HEALTHCHECK + any deadman alert
        # built on top of it -- the container stayed "healthy"
        # while the control loop was broken (e.g., the 2026-05-11
        # incident).
        tick_ok = True
        try:
            await run_schedule_check(cfg, c4, query_api, write_api, tz, now_local, firing)
        except Exception as exc:
            tick_ok = False
            log("error", "schedule_check_failed",
                error=str(exc), error_type=type(exc).__name__)

        # Heartbeat for Docker healthcheck. Touched ONLY when the
        # schedule_check completed without raising; a sustained
        # failure age past the HEALTHCHECK staleness window
        # (5 min) flips the container unhealthy and triggers
        # whatever restart / alert path the operator has wired.
        if tick_ok:
            try:
                health_marker.touch()
            except Exception:
                pass

    log("info", "shutdown")
    influx.close()  # type: ignore[no-untyped-call]  # influxdb_client.InfluxDBClient.close lacks stubs
    return 0


def main() -> int:
    cfg = Config.from_env()
    return asyncio.run(main_async(cfg))


if __name__ == "__main__":
    sys.exit(main())
