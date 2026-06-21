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
    fetch_forecast_peak_today,
    in_cooling_season,
    update_season_5th_highest,
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
    """Per-tick output of `_evaluate_layer_inputs`. Captures the resolved
    price-overlay tier/offset (re-consumed by the Slice B price path) plus
    the audit context for `hvac.price_overlay` and `hvac.5cp_state` writes.

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
