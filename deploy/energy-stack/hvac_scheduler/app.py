"""Arm B commissioning controller — price-aware comfort scheduler.

Arm B runs the same comfort program as Arm A and adds price awareness on
top. It holds a config-driven comfort baseline (the comfort_program +
ceiling from the controller config) and only ever drifts the cool
setpoint *warmer* in response to live ComEd RTP price — never below the
baseline. The warm-only floor is enforced in code as
``effective = max(effective, baseline)``. (The price offset itself lands
in Slice B; until then the effective setpoint equals the baseline and the
clamp is the load-bearing invariant.)

Safety is device-owned: the thermostat enforces its own min/max bounds
and timed holds. There is no software safety supervisor in this service.

Design:
  * One persistent process, asyncio main loop, ticks every ~10s.
  * On each tick, evaluates the reactive price overlay against the live
    ComEd price and, when the floor-clamped comfort baseline changes,
    pushes COOL_SETPOINT + HOLD_MODE to the thermostat.
  * Skips setpoint writes when thermostat HVAC_MODE != Cool/Auto
    (heating-season no-op).
  * Every action writes to `hvac.actions` for audit.

Run mode (write-gating):
  * SCHEDULER_MODE env var (REQUIRED, no default): shadow = never writes
    (logs the proposed action only); production = writes always.
    Promotion runs shadow -> production. Module refuses to start
    (sys.exit(2)) on a missing or invalid value.

Actuation: COOL_SETPOINT + HOLD_MODE='Permanent' are pushed to the
Honeywell thermostat via a ``ThermostatClient`` device seam. The concrete
device client (Control4 -> TCC swap) is deferred: ``StubThermostatClient``
raises ``NotImplementedError`` for every method until the TCC
(aiosomecomfort) implementation is wired at go-active. Pi failure mode:
the thermostat keeps its last-set setpoint (degraded scheduling, not a
safety issue).

Environment variables:
    CONTROL4_THERMOSTAT_ID      Thermostat device id (default 3231). The
                                Control4-era value (a C4 item id) is dead;
                                the TCC (aiosomecomfort) client will reuse
                                this field with a Honeywell device id and a
                                renamed env var at go-active.
    SCHEDULER_MODE              "shadow" | "production" (REQUIRED; no default).
                                shadow = never writes; production = writes
                                always. Module refuses to start (sys.exit(2))
                                on a missing or invalid value.
    SCHEDULER_DRY_RUN           Retired by SCHEDULER_MODE. If still set in the
                                env, it is logged-and-ignored at Config load.
    CONTROLLER_CONFIG_FILE      Path to the controller config YAML (comfort
                                program, heat floor, ceiling). When unset, the
                                control loop does not consume a config.
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
from typing import Any, Awaitable, Callable, Protocol
from zoneinfo import ZoneInfo

from influxdb_client import InfluxDBClient  # type: ignore[attr-defined]  # influxdb_client lacks __all__/stubs; main() owns this single import for client wiring
from influxdb_client.client.write_api import SYNCHRONOUS

from .arm_calendar import ARM_CALENDAR, current_arm_at  # local copy, hash-sync-checked in CI
from .controller_config import ControllerConfig, load_controller_config
from .controller_core import comfort_baseline_cool
from .price_overlay import (
    NORMAL_TIER_NAME,
    PriceOverlayState,
    PriceTier,
    build_price_tiers,
    effective_cool_for_tier,
    evaluate_price_overlay,
    hold_elapsed,
    tier_priority,
)
from .decision_codes import (
    PriceOverlayCode,
)


# ---- Config ----------------------------------------------------------------

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
    # baseline schedule resumes. Skips setpoint and fan_mode writes; only
    # calls set_hold_mode("Schedule").
    release_hold: bool = False


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
    state: PriceOverlayState, now_utc: datetime, minimum_hold_minutes: int,
) -> float | None:
    """Minutes left on the price-overlay minimum-hold window, or None
    when in NORMAL tier / no triggered_at timestamp. Surfaces internal
    state-machine timing to the trace caller without re-implementing
    the state machine. ``minimum_hold_minutes`` is config-driven
    (``ControllerConfig.hold_ttl_minutes``)."""
    if state.triggered_at_utc is None:
        return None
    elapsed = (now_utc - state.triggered_at_utc).total_seconds() / 60.0
    remaining = minimum_hold_minutes - elapsed
    return max(0.0, remaining)


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
    # Thermostat device id. Kept for the Control4 -> TCC swap: the TCC
    # client will reuse this field (with a Honeywell device id) once wired.
    thermostat_id: int
    dry_run: bool
    mode: str
    temp_scale: str
    decision_trace_verbose: bool
    tz_name: str
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    token_file: Path
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
            tz_name=os.environ.get("SCHEDULER_TZ", "America/Chicago"),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
            token_file=Path(os.environ.get("DIRECTOR_TOKEN_FILE", "/data/director_token.json")),
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


def fq_latest_comed_5min(bucket: str) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "5min")
  |> last()
'''


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


# ---- Thermostat device seam -----------------------------------------------
#
# The concrete device I/O (the prior Control4 Director client) is
# demolished. ``ThermostatClient`` is the thin interface the controller
# talks to; ``StubThermostatClient`` is the not-yet-wired implementation
# that raises ``NotImplementedError`` for every method. The Control4 ->
# TCC swap fills this seam with an aiosomecomfort-backed client at
# rebuild / go-active. ``read_thermostat_snapshot`` and ``execute_action``
# are typed to the interface and their bodies are unchanged.


class ClimateDevice(Protocol):
    """The per-thermostat read/write surface the controller invokes via
    ``ThermostatClient.call_with_reauth``. Exactly the methods
    ``read_thermostat_snapshot`` and ``execute_action`` call — nothing
    more. All methods are async; setpoint values are in the controller's
    temp_scale (default °F)."""

    # Read path (read_thermostat_snapshot)
    async def get_current_temperature_f(self) -> float: ...
    async def get_cool_setpoint_f(self) -> float: ...
    async def get_heat_setpoint_f(self) -> float: ...
    async def get_hvac_mode(self) -> str: ...
    async def get_hvac_state(self) -> str: ...
    async def get_fan_mode(self) -> str: ...
    async def get_hold_mode(self) -> str: ...
    async def get_humidity(self) -> float: ...

    # Write path (execute_action)
    async def set_cool_setpoint_f(self, value: float) -> None: ...
    async def set_heat_setpoint_f(self, value: float) -> None: ...
    async def set_fan_mode(self, mode: str) -> None: ...
    async def set_hold_mode(self, mode: str) -> None: ...


class ThermostatClient(Protocol):
    """The device-client seam the controller talks to. Concrete impls
    (Control4 -> TCC swap) own auth/token lifecycle; the controller only
    needs to fetch the climate device and run a call with reauth-on-401."""

    async def get_climate(self) -> ClimateDevice: ...

    async def call_with_reauth(
        self, coro_fn: Callable[[], Awaitable[Any]]
    ) -> Any: ...


_SWAP_PENDING = "Control4->TCC swap pending: no device client wired"


class StubThermostatClient:
    """Not-yet-wired ``ThermostatClient``. Every method raises
    ``NotImplementedError`` — this is the seam the aiosomecomfort/TCC
    client fills at rebuild / go-active. The control loop still
    constructs and threads this client so the wiring is exercised; it
    only raises when a real device call is attempted (startup snapshot
    or a live write), which the surrounding try/except logs and
    survives (supervisor-continuity)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def get_climate(self) -> ClimateDevice:
        raise NotImplementedError(_SWAP_PENDING)

    async def call_with_reauth(
        self, coro_fn: Callable[[], Awaitable[Any]]
    ) -> Any:
        raise NotImplementedError(_SWAP_PENDING)


# ---- Scheduler core --------------------------------------------------------

@dataclass
class FiringState:
    """Track what's already fired today so we don't double-execute, plus
    the persistent state for the price-overlay (§2) state machine that
    spans ticks."""
    last_decision_date: str = ""
    fired_actions: set[tuple[str, int, int]] = field(default_factory=set)  # (date, hour, minute)
    # Price-overlay state machine (§2). Survives across scheduler ticks but
    # not container restarts; cold-start re-evaluates from current price
    # within the 30-min minimum-hold window so behaviour stabilizes fast.
    price_overlay_state: PriceOverlayState = field(default_factory=PriceOverlayState)
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
    # Throttle for hvac.arm_mode + hvac.switch_event + hvac.input_feed_health
    # writes. ~5-min cadence so analysis sees a uniform 288-rows/day
    # arm-mode trace per spec §11 #2.
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
                 thermostat_state_before: dict[str, Any], error: str | None = None) -> None:
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
    write_point(write_api, bucket, "hvac.actions", tags=tags, fields=fields)


async def read_thermostat_snapshot(c4: ThermostatClient) -> dict[str, Any]:
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


def required_feeds_for_arm_mode(*, when_ct: datetime,
                                  price_feed_healthy: bool) -> dict[str, bool]:
    """Return the dict of input-feed health flags REQUIRED for B-active
    classification, derived from the enabled-mode set (spec "Telemetry":
    required_feeds_for_arm_mode is derived from the enabled-mode set, not a
    hardcoded dict).

    Only feeds consumed by an enabled mode are required. The reactive
    warm-only overlay is the sole enabled mode and consumes the live price
    feed only, so the required set is ``{"price": ...}``. Weather and PJM
    capacity-risk are not consumed by any enabled mode (day-types /
    forecasts / deep precool / 5CP are gone) so they are not required.

    ``when_ct`` is accepted for caller-signature stability and is
    intentionally unused here.
    """
    _ = when_ct  # reserved

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


async def execute_action(c4: ThermostatClient, action: ScheduleAction,
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
    """Per-tick output of `_evaluate_layer_inputs`. Carries the post-gating
    active price-overlay tier name (consumed by `_push_baseline_if_changed`
    via `effective_cool_for_tier`, which derives the effective cool from the
    tier name + config) plus the audit context for `hvac.price_overlay`
    writes.
    """
    price_tier_name: str
    price_prev_tier: str
    current_price_cents: float | None


# 5-min cadence for arm-mode / feed-health / switch-event telemetry
# (spec §11 #2-4) so analysis sees a uniform 288-rows/day trace.
_ARM_MODE_AUDIT_INTERVAL = timedelta(minutes=5)

# P2.2 reviewer-flagged 2026-05-11: a carried-forward price-overlay
# tier (preserved across a brief feed gap per PR #60's P2.A fix) must
# not hold indefinitely. If the ComEd RTP feed has been unavailable
# for longer than this threshold, the overlay releases back to NORMAL
# tier. 30 minutes is a fixed feed-gap timer (the spec keeps it as-is) --
# if a tier event can typically resolve in tens of minutes on healthy
# data, an outage of similar length is plausibly a real release rather
# than a brief blip.
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


def _evaluate_layer_inputs(query_api: Any, write_api: Any, cfg: Config,
                            firing: FiringState, now_local: datetime,
                            *, tick_id: str | None = None) -> LayerInputs:
    """Per-tick evaluation of the §2 price overlay, independent of whether
    a scheduled action is firing this minute.

    Side effects:
      * Updates ``firing.price_overlay_state``.
      * Writes ``hvac.price_overlay`` on tier transitions only.

    ``now_utc`` is derived from ``now_local`` rather than read from
    wall-clock so tests can drive the throttle window and so audit
    timestamps stay consistent with the rest of the scheduler tick.

    Per EXPERIMENT_DESIGN §3 item 5: "Continuous overlay on the active
    scheduled setpoint, evaluated each scheduler tick". Pre-§Critical#2
    this code lived inside the action-fire loop body and only ran 4-6
    times/day; mid-window price spikes fell through unobserved.
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
    # Config-driven tiers + minimum-hold (no hardcoded numbers in the overlay).
    assert cfg.controller_config is not None, (
        "controller_config is required: the price overlay is config-driven"
    )
    tiers = build_price_tiers(cfg.controller_config)
    min_hold_minutes = cfg.controller_config.hold_ttl_minutes

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
        firing.price_overlay_state, now_utc, min_hold_minutes,
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
    active_tier: PriceTier | None = None
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
        # Explicit normal output -- do NOT inherit prev_tier.
        price_tier_name = NORMAL_TIER_NAME
        active_tier = None

    elif sample is not None:
        # State machine + caller-side gate (T12 logic).
        # current_price_cents is sample.cents_per_kwh in this branch (line
        # above), so the `sample is not None` guard implies non-None price.
        assert current_price_cents is not None
        proposed_tier, proposed_state = evaluate_price_overlay(
            current_price_cents, firing.price_overlay_state, now_utc,
            tiers, min_hold_minutes,
        )
        proposed_name = proposed_tier.name if proposed_tier else NORMAL_TIER_NAME
        is_downgrade = tier_priority(proposed_name, tiers) < tier_priority(prev_tier, tiers)

        if is_downgrade and not sample_is_fresh:
            # Recency gate refuses downgrade. Hold prev_tier.
            downgrade_gate_held = True
            price_tier_name = prev_tier
        else:
            # Detect protective upgrade and clear the safety-release timer so
            # the new tier gets its own observation window. Without this clear,
            # a delayed-next-tick after a non-fresh upgrade could fire release
            # against the old tier's accumulated non-fresh time. See Codex
            # Checkpoint-3 finding. Unconditional clear: no-op during the
            # previous tier's min-hold (timer was already None per the reset
            # rules above), necessary post-min-hold.
            is_upgrade = tier_priority(proposed_name, tiers) > tier_priority(prev_tier, tiers)
            if is_upgrade:
                firing.nonfresh_after_hold_started_at_utc = None
            # Apply state machine proposal.
            firing.price_overlay_state = proposed_state
            active_tier = proposed_tier
            price_tier_name = active_tier.name if active_tier is not None else NORMAL_TIER_NAME

    else:
        # sample is None, timer not yet at 30-min threshold: carry-forward
        # the prev_tier label (the effective setpoint is re-derived from it).
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
    else:
        # Real tier change. Direction is derived from a PRIORITY comparison
        # of prev vs new (not per-name branches) so the 4th `extreme` tier
        # is classified correctly — e.g. extreme->scarcity is a downgrade,
        # not a mislabelled release-to-normal.
        po_level = "info"
        if new_tier == NORMAL_TIER_NAME:
            po_outcome = "released"
            po_reason = PriceOverlayCode.RELEASED_TO_NORMAL
        elif tier_priority(new_tier, tiers) > tier_priority(prev_tier, tiers):
            po_outcome = "upgraded"
            po_reason = {
                "elevated": PriceOverlayCode.UPGRADED_TO_ELEVATED,
                "scarcity": PriceOverlayCode.UPGRADED_TO_SCARCITY,
                "extreme": PriceOverlayCode.UPGRADED_TO_EXTREME,
            }[new_tier]
        else:  # downgrade to a non-normal tier
            po_outcome = "downgraded"
            po_reason = {
                "elevated": PriceOverlayCode.DOWNGRADED_TO_ELEVATED,
                "scarcity": PriceOverlayCode.DOWNGRADED_TO_SCARCITY,
            }[new_tier]

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
            firing.price_overlay_state, now_utc, min_hold_minutes,
        ),
    )

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

    return LayerInputs(
        price_tier_name=price_tier_name,
        price_prev_tier=prev_tier,
        current_price_cents=current_price_cents,
    )


async def _push_baseline_if_changed(
    cfg: Config, c4: ThermostatClient, write_api: Any,
    firing: FiringState, now_local: datetime, active_tier_name: str,
    *, tick_id: str | None = None,
) -> None:
    """Push the warm-overlaid, floor-clamped effective cool setpoint when it
    differs from the last value pushed.

    The effective cool setpoint is the comfort baseline plus the active
    price tier's warm offset, resolved by the pinned formula
    (``effective_cool_for_tier``):

        effective_cool = clamp(baseline + offset,
                               floor = baseline, ceiling = comfort_max)

    Warm-only: the formula never drops below baseline (floor invariant) and
    never above the comfort ceiling. An additional ``max(effective, baseline)``
    is kept as the defensive load-bearing floor clamp (the only clamp the
    controller owns; safety is device-owned, no software supervisor).

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

    # Apply the active price tier's warm offset via the pinned formula
    # (config-driven; clamped to [baseline, comfort_max]).
    effective_cool = effective_cool_for_tier(
        active_tier_name, baseline, cfg.controller_config,
    )
    effective_cool = max(effective_cool, baseline)  # defensive floor invariant

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


async def run_schedule_check(cfg: Config, c4: ThermostatClient, query_api: Any, write_api: Any,
                              tz: ZoneInfo, now_local: datetime,
                              firing: FiringState) -> None:
    """Compute the comfort baseline for this minute, apply the price overlay,
    and push the resulting effective setpoint (in shadow) when it differs from
    the last value pushed.

    Single-path commissioning controller (spec "Architecture": in-place,
    single-path rewrite):
      1. Comfort baseline from ``comfort_baseline_cool`` (config), every tick.
      2. Price overlay — ``_evaluate_layer_inputs`` resolves the active warm
         tier; ``_push_baseline_if_changed`` turns it into the effective cool
         via the pinned formula, clamped to [baseline, comfort_max]. Warm-only:
         the floor clamp (``effective = max(effective, baseline)``) is the only
         clamp the controller owns (device-owned safety; no software supervisor).
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
    # Evaluate the price overlay and write its audit rows every tick. The
    # post-gating active tier is threaded into the push below, where the
    # pinned formula turns it into the warm-overlaid effective setpoint.
    layer_inputs = _evaluate_layer_inputs(
        query_api, write_api, cfg, firing, now_local, tick_id=tick_id,
    )

    # ---- Per-cycle arm-mode + switch-event + feed-health telemetry ----
    # (spec §11 #2-4)
    #
    # arm_mode and input_feed_health share a 5-min cadence so analysis
    # sees a uniform 288-rows/day trace. Outside the locked experiment
    # window the arm_mode write is a no-op inside ``write_arm_mode``;
    # input_feed_health still fires so feed-availability is audited
    # across the whole observation period.
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
        # FULL feed-health dict, written for audit. The reactive price
        # overlay is the only enabled mode, so price is the only feed.
        all_feeds = {
            "price": price_feed_healthy,
        }
        write_input_feed_health(
            write_api, cfg.influx_bucket, now_local, all_feeds,
        )
        # FILTERED dict for B-active classification (spec §5).
        required_feeds = required_feeds_for_arm_mode(
            when_ct=now_local,
            price_feed_healthy=price_feed_healthy,
        )
        write_arm_mode(
            write_api, cfg.influx_bucket, now_local, required_feeds,
            controller_alive=True,
        )
        firing.last_arm_mode_audit_at_utc = now_utc_for_audit

    # ---- Push the effective setpoint when it changed ----
    # Single push path: the effective cool setpoint is the comfort baseline
    # plus the active price tier's warm offset (pinned formula), floor-clamped.
    # Re-push only when it differs from the last value pushed.
    await _push_baseline_if_changed(
        cfg, c4, write_api, firing, now_local,
        layer_inputs.price_tier_name, tick_id=tick_id,
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
        thermostat_id=cfg.thermostat_id,
        dry_run=cfg.dry_run,
        tz=cfg.tz_name)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    query_api = influx.query_api()
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    c4 = StubThermostatClient(cfg)

    # Sanity check at startup: prove we can talk to Director
    try:
        snapshot = await read_thermostat_snapshot(c4)
        log("info", "startup_thermostat_snapshot", **snapshot)
    except Exception as exc:
        log("error", "startup_thermostat_unreachable", error=str(exc))
        # Don't exit -- keep retrying on schedule

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
