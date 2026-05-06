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
  * At each schedule transition time (e.g., 06:00, 13:00, 19:00, 22:00),
    looks up the day-type for TODAY and pushes the corresponding
    COOL_SETPOINT_F + HOLD_MODE='Permanent' to the thermostat.
  * Every action also writes to `hvac.actions` for audit.

Safety nets:
  * DRY_RUN env var (default true) -- logs commands without pushing.
  * Skips setpoint changes when thermostat HVAC_MODE != Cool/Auto
    (i.e., heating-season no-op).
  * Director token persisted to /data/director_token.json -- one cloud
    auth at startup OR on 401, then pure LAN.
  * Pi failure mode: thermostat keeps last-set setpoint; not a safety
    issue, just degraded scheduling.

Day-type rules:
  * MILD             -- forecast high < 82F            -- no actions
  * NORMAL           -- 82 <= forecast high < 95F      -- standard schedule
  * HOT_5CP_RISK     -- forecast high >= 95F OR active heat advisory
                                                       -- aggressive schedule

Environment variables:
    CONTROL4_EMAIL              Control4 account email
    CONTROL4_PASSWORD           Control4 account password
    CONTROL4_CONTROLLER_IP      Director IP (default 192.168.1.30)
    CONTROL4_THERMOSTAT_ID      C4 item id (default 3231)
    SCHEDULER_DRY_RUN           "true"|"false" (default "true")
    SCHEDULER_DECISION_HOUR     Hour-of-day to decide tomorrow (default 21)
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
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from pyControl4.account import C4Account
from pyControl4.director import C4Director
from pyControl4.climate import C4Climate


# ---- Config ----------------------------------------------------------------

DAYTYPE_MILD = "MILD"
DAYTYPE_NORMAL = "NORMAL"
DAYTYPE_HOT = "HOT_5CP_RISK"
DAYTYPE_HOT_STREAK_DAY1 = "HOT_STREAK_DAY1"  # tomorrow + day-after both HOT -> extra mass build

# Heat setpoint floor for Auto mode. 65F is a comfortable winter "don't freeze"
# target that gives a 15F deadband against typical cool setpoints (70-80F),
# well above the ASHRAE 90.1 5F minimum (and safely above the VisionPRO 8000
# ISU 300 default of 3F which is below code).
HEAT_SETPOINT_FLOOR_F = 65

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
    # cool_setpoint_f is None when release_hold=True; the action only flips
    # the thermostat back to schedule mode without changing setpoints.
    cool_setpoint_f: int | None = None
    heat_setpoint_f: int = HEAT_SETPOINT_FLOOR_F
    fan_mode: str | None = None  # 'Auto' | 'On' | 'Circulate' | None=don't touch
    cool_setpoint_humid_f: int | None = None  # used if today's max dewpoint > HUMID_DEWPOINT_F
    # When True: clear the thermostat's Permanent hold so the device's own
    # baseline schedule resumes. Used by MILD_SCHEDULE to release a hold left
    # over from yesterday's NORMAL/HOT cycle. Skips setpoint and fan_mode
    # writes; only calls set_hold_mode("Schedule").
    release_hold: bool = False


# NORMAL day: 82-94F forecast. Pre-cool, coast, recover, sleep.
# Sleep at 21:00 captures full DTOD overnight cheap window (9 PM-6 AM, 2.984c/kWh).
# Coast humid override drops 79->75 when dewpoint >65F to keep low-stage AC running
# for latent removal (per PNNL-26478, ASHRAE 55 humidity comfort).
NORMAL_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(6,  0, "PRE_COOL", cool_setpoint_f=70),
    ScheduleAction(13, 0, "COAST",    cool_setpoint_f=79, fan_mode="Circulate",
                   cool_setpoint_humid_f=75),
    ScheduleAction(19, 0, "RECOVER",  cool_setpoint_f=75, fan_mode="Auto"),
    ScheduleAction(21, 0, "SLEEP",    cool_setpoint_f=73),
]

# HOT/5CP-risk day: forecast >=95F or active heat advisory.
# Aggressive pre-cool at 04:00, coast starts at 12:00, hard-shutoff 14:00-18:00
# covers the empirical PJM 5CP window per 2025 data (4 of 5 RTO peaks landed in
# the 4-5 PM CDT clock hour, but 6/25/2025 hit 1-2 PM, so 14:00 start is needed).
# Each kW shaved from 5CP saves ~$240-480/yr in next-year capacity charges.
HOT_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(4,  0, "HOT_PRE_COOL",     cool_setpoint_f=68),
    ScheduleAction(12, 0, "HOT_COAST",        cool_setpoint_f=80, fan_mode="Circulate",
                   cool_setpoint_humid_f=76),
    ScheduleAction(14, 0, "HOT_5CP_SHUTOFF",  cool_setpoint_f=85),  # PJM 5CP avoidance
    ScheduleAction(18, 0, "HOT_RECOVER_LOW",  cool_setpoint_f=78, fan_mode="Auto"),
    ScheduleAction(19, 0, "HOT_RECOVER",      cool_setpoint_f=75),
    ScheduleAction(21, 0, "SLEEP",            cool_setpoint_f=73),
]

# MILD day: forecast <82F. No active scheduling; thermostat baseline handles
# it — but a single 00:05 release-hold action clears any Permanent hold left
# over from yesterday (e.g. SLEEP=73 from a NORMAL day's last action). Without
# this, the thermostat would stay pinned to the previous day's last setpoint
# instead of returning to its own baseline schedule.
MILD_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True),
]

# HOT STREAK DAY 1: tomorrow AND day-after both forecast HOT. Heat wave starting.
# Pre-cool starts an HOUR earlier and goes 2 degrees DEEPER than a one-day HOT
# event, to build extra thermal mass that day 2 can coast on. Day 2 of the
# streak just runs the regular HOT_SCHEDULE since the mass is already there.
HOT_STREAK_DAY1_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(3,  0, "STREAK_PRE_COOL_EARLY", cool_setpoint_f=66),
    ScheduleAction(12, 0, "HOT_COAST",             cool_setpoint_f=80, fan_mode="Circulate",
                   cool_setpoint_humid_f=76),
    ScheduleAction(14, 0, "HOT_5CP_SHUTOFF",       cool_setpoint_f=85),
    ScheduleAction(18, 0, "HOT_RECOVER_LOW",       cool_setpoint_f=78, fan_mode="Auto"),
    ScheduleAction(19, 0, "HOT_RECOVER",           cool_setpoint_f=75),
    ScheduleAction(21, 0, "SLEEP",                 cool_setpoint_f=73),
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


@dataclass(frozen=True)
class Config:
    email: str
    password: str
    controller_ip: str
    thermostat_id: int
    dry_run: bool
    decision_hour: int
    tz_name: str
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    token_file: Path
    overrides_file: Path

    @staticmethod
    def from_env() -> "Config":
        def required(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                log("error", "missing_env", var=name)
                sys.exit(2)
            return v
        return Config(
            email=required("CONTROL4_EMAIL"),
            password=required("CONTROL4_PASSWORD"),
            controller_ip=os.environ.get("CONTROL4_CONTROLLER_IP", "192.168.1.30"),
            thermostat_id=int(os.environ.get("CONTROL4_THERMOSTAT_ID", "3231")),
            dry_run=os.environ.get("SCHEDULER_DRY_RUN", "true").lower() in ("1", "true", "yes"),
            decision_hour=int(os.environ.get("SCHEDULER_DECISION_HOUR", "21")),
            tz_name=os.environ.get("SCHEDULER_TZ", "America/Chicago"),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
            token_file=Path(os.environ.get("DIRECTOR_TOKEN_FILE", "/data/director_token.json")),
            overrides_file=Path(os.environ.get("OVERRIDES_FILE", OVERRIDES_FILE_DEFAULT)),
        )


# ---- Influx queries --------------------------------------------------------

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


def fetch_latest_forecast(query_api, bucket: str, for_period: str) -> dict | None:
    rows = []
    for table in query_api.query(fq_latest_forecast(bucket, for_period)):
        for record in table.records:
            rows.append(record.values)
    if not rows:
        return None
    # After pivot we get one row with all fields as columns
    return rows[0]


def fetch_latest_comed(query_api, bucket: str) -> float | None:
    for table in query_api.query(fq_latest_comed_5min(bucket)):
        for record in table.records:
            v = record.get_value()
            if v is not None:
                return float(v)
    return None


# ---- Day-type decision -----------------------------------------------------

def _classify_one_day(forecast: dict | None) -> str:
    """Single-day classification helper without the full reasons dict."""
    if not forecast:
        return DAYTYPE_NORMAL
    high_f = forecast.get("high_f")
    is_heat_adv = bool(forecast.get("is_heat_advisory", 0))
    if is_heat_adv or (high_f is not None and high_f >= 95):
        return DAYTYPE_HOT
    if high_f is not None and high_f >= 82:
        return DAYTYPE_NORMAL
    return DAYTYPE_MILD


def decide_day_type(forecast: dict | None,
                    day2_forecast: dict | None = None) -> tuple[str, dict]:
    """Return (day_type, reasoning_dict).

    If `day2_forecast` is provided AND tomorrow is HOT AND day-after is also
    HOT, escalates to HOT_STREAK_DAY1 (extra-aggressive pre-cool to build
    thermal mass for the multi-day heat event).
    """
    if not forecast:
        return DAYTYPE_NORMAL, {"reason": "no_forecast_available", "fallback": True}
    high_f = forecast.get("high_f")
    is_heat_adv = bool(forecast.get("is_heat_advisory", 0))
    dewpoint_f = forecast.get("max_dewpoint_f")

    reasons = {
        "high_f": high_f,
        "is_heat_advisory": is_heat_adv,
        "max_dewpoint_f": dewpoint_f,
        "alert_summary": forecast.get("alert_summary", ""),
    }

    base_type = _classify_one_day(forecast)
    if base_type == DAYTYPE_HOT:
        # Lookahead: if day-after is ALSO HOT, escalate to streak
        day2_type = _classify_one_day(day2_forecast) if day2_forecast else None
        if day2_type == DAYTYPE_HOT:
            reasons["reason"] = "hot_streak_starting"
            reasons["day2_high_f"] = (day2_forecast or {}).get("high_f")
            reasons["day2_is_heat_advisory"] = bool((day2_forecast or {}).get("is_heat_advisory", 0))
            return DAYTYPE_HOT_STREAK_DAY1, reasons
        reasons["reason"] = "heat_advisory" if is_heat_adv else "high_ge_95"
        return DAYTYPE_HOT, reasons
    if base_type == DAYTYPE_NORMAL:
        reasons["reason"] = "high_82_to_94"
        return DAYTYPE_NORMAL, reasons
    reasons["reason"] = "high_lt_82"
    return DAYTYPE_MILD, reasons


def schedule_for(day_type: str) -> list[ScheduleAction]:
    return {
        DAYTYPE_HOT_STREAK_DAY1: HOT_STREAK_DAY1_SCHEDULE,
        DAYTYPE_HOT:             HOT_SCHEDULE,
        DAYTYPE_NORMAL:          NORMAL_SCHEDULE,
        DAYTYPE_MILD:            MILD_SCHEDULE,
    }.get(day_type, NORMAL_SCHEDULE)


def resolve_cool_setpoint(action: ScheduleAction, today_dewpoint_f: float | None) -> tuple[int, str]:
    """Return (setpoint_to_apply, reason) — picks the humid override if dewpoint
    is high enough and an override is defined for this action.

    For release_hold actions there is no setpoint to apply; returns (0,
    "release_hold") so callers can record a sentinel without dispatching a
    setpoint write.
    """
    if action.release_hold:
        return 0, "release_hold"
    if (action.cool_setpoint_humid_f is not None
            and today_dewpoint_f is not None
            and today_dewpoint_f > HUMID_DEWPOINT_F):
        return action.cool_setpoint_humid_f, f"humid_override (dewpoint {today_dewpoint_f:.1f}F > {HUMID_DEWPOINT_F}F)"
    return action.cool_setpoint_f, "standard"


# ---- Control4 client wrapper ----------------------------------------------

class C4Client:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._account: C4Account | None = None
        self._director: C4Director | None = None
        self._climate: C4Climate | None = None
        self._token: str | None = None
        self._common_name: str | None = None

    def _load_token(self) -> dict | None:
        if not self.cfg.token_file.exists():
            return None
        try:
            return json.loads(self.cfg.token_file.read_text())
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
        self._director = C4Director(self.cfg.controller_ip, self._token)
        self._climate = C4Climate(self._director, self.cfg.thermostat_id)
        return self._director

    async def get_climate(self) -> C4Climate:
        await self.ensure_director()
        assert self._climate is not None
        return self._climate

    async def call_with_reauth(self, coro_fn):
        """Run a director call; on 401, re-auth and retry once."""
        try:
            return await coro_fn()
        except Exception as exc:
            txt = str(exc).lower()
            if "401" in txt or "unauthorized" in txt or "forbidden" in txt:
                log("warn", "director_token_invalid_reauth", error=str(exc))
                await self._cloud_auth()
                self._director = C4Director(self.cfg.controller_ip, self._token)
                self._climate = C4Climate(self._director, self.cfg.thermostat_id)
                return await coro_fn()
            raise


# ---- Scheduler core --------------------------------------------------------

@dataclass
class FiringState:
    """Track what's already fired today so we don't double-execute."""
    last_decision_date: str = ""
    fired_actions: set[tuple[str, int, int]] = field(default_factory=set)  # (date, hour, minute)


def write_decision(write_api, bucket: str, decision_for_date: str,
                   day_type: str, reasons: dict, comed_price: float | None) -> None:
    p = (Point("hvac.decisions")
         .tag("decision_for_date", decision_for_date)
         .tag("day_type", day_type)
         .field("high_f", float(reasons.get("high_f") or 0))
         .field("max_dewpoint_f", float(reasons.get("max_dewpoint_f") or 0))
         .field("is_heat_advisory", int(reasons.get("is_heat_advisory", False)))
         .field("alert_summary", reasons.get("alert_summary") or "")
         .field("reason", reasons.get("reason") or "")
         .field("comed_price_at_decision", float(comed_price or 0))
         )
    write_api.write(bucket=bucket, record=p)


def write_action(write_api, bucket: str, day_type: str, action: ScheduleAction,
                 cool_applied_f: int, fan_mode_applied: str | None,
                 setpoint_reason: str, dry_run: bool, applied: bool,
                 thermostat_state_before: dict, error: str | None = None) -> None:
    p = (Point("hvac.actions")
         .tag("day_type", day_type)
         .tag("action_label", action.label)
         .tag("dry_run", "true" if dry_run else "false")
         .field("cool_setpoint_f", float(cool_applied_f))
         .field("heat_setpoint_f", float(action.heat_setpoint_f))
         .field("fan_mode", fan_mode_applied or "")
         .field("setpoint_reason", setpoint_reason)
         .field("applied", int(applied))
         .field("error", error or "")
         .field("hvac_mode_before", str(thermostat_state_before.get("hvac_mode") or ""))
         .field("indoor_temp_before_f", float(thermostat_state_before.get("indoor_temp_f") or 0))
         .field("cool_setpoint_before_f", float(thermostat_state_before.get("cool_setpoint_f") or 0))
         .field("heat_setpoint_before_f", float(thermostat_state_before.get("heat_setpoint_f") or 0))
         .field("indoor_humidity_before_pct", float(thermostat_state_before.get("humidity") or 0))
         )
    write_api.write(bucket=bucket, record=p)


async def read_thermostat_snapshot(c4: C4Client) -> dict:
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


async def execute_action(c4: C4Client, action: ScheduleAction,
                          cool_setpoint_to_apply: int,
                          state: dict, dry_run: bool) -> tuple[bool, str | None]:
    """Apply the action to the thermostat. Returns (applied, error).

    Two execution paths:
      * release_hold action: clears the Permanent hold so the thermostat's
        baseline schedule resumes. Idempotent; runs regardless of hvac_mode
        (set_hold_mode("Schedule") is safe even in Heat/Off — it just becomes
        a no-op when no hold is active).
      * Setpoint action: applies heat + cool setpoints, optional fan mode,
        then HOLD_MODE='Permanent' to pin the override against the
        thermostat's own schedule. Skipped when hvac_mode is not Cool/Auto
        (so we don't accidentally fight a heating-season furnace).
    """
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
        await c4.call_with_reauth(lambda: climate.set_cool_setpoint_f(cool_setpoint_to_apply))
        await asyncio.sleep(1)
        await c4.call_with_reauth(lambda: climate.set_heat_setpoint_f(action.heat_setpoint_f))
        await asyncio.sleep(1)
        # Apply fan mode if specified for this period (e.g., Circulate during coast)
        if action.fan_mode:
            await c4.call_with_reauth(lambda: climate.set_fan_mode(action.fan_mode))
            await asyncio.sleep(1)
        # Pin the override so thermostat baseline doesn't override our setpoint
        await c4.call_with_reauth(lambda: climate.set_hold_mode("Permanent"))
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def run_decision(cfg: Config, c4: C4Client, query_api, write_api, tz: ZoneInfo,
                        firing: FiringState) -> None:
    """Read tomorrow's forecast (with day-after lookahead), decide day-type, log."""
    forecast = fetch_latest_forecast(query_api, cfg.influx_bucket, "tomorrow")
    day2 = fetch_latest_forecast(query_api, cfg.influx_bucket, "day2")
    comed_price = fetch_latest_comed(query_api, cfg.influx_bucket)
    day_type, reasons = decide_day_type(forecast, day2_forecast=day2)
    decision_date = (datetime.now(tz).date() + timedelta(days=1)).isoformat()
    write_decision(write_api, cfg.influx_bucket, decision_date, day_type, reasons, comed_price)
    firing.last_decision_date = decision_date
    log("info", "decision_made",
        for_date=decision_date,
        day_type=day_type,
        reason=reasons.get("reason"),
        high_f=reasons.get("high_f"),
        max_dewpoint_f=reasons.get("max_dewpoint_f"),
        is_heat_advisory=reasons.get("is_heat_advisory"),
        day2_high_f=reasons.get("day2_high_f"),
        comed_price_now=comed_price,
        dry_run=cfg.dry_run)


def _read_stored_decision(query_api, bucket: str, decision_for_date: str) -> str | None:
    """Return the persisted day-type for ``decision_for_date``, or None if
    no decision was ever written."""
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -36h)
  |> filter(fn: (r) => r._measurement == "hvac.decisions"
                    and r.decision_for_date == "{decision_for_date}")
  |> last()
  |> keep(columns: ["day_type"])
'''
    for table in query_api.query(flux):
        for record in table.records:
            day_type = record.values.get("day_type")
            if day_type:
                return day_type
    return None


def fetch_today_decision(query_api, write_api, bucket: str, today_iso: str) -> str:
    """Look up day-type decision for today. If missing, recompute lazily
    from the live forecast and persist.

    Recovery mechanism for any reason today's decision wasn't written at
    yesterday's 21:00 (scheduler down, InfluxDB unreachable, NWS API
    failure, container restart mid-decision, clock skew, first run with
    no history). The first schedule check on a day with no stored
    decision pulls today's live forecast, runs the same classification
    logic, persists the result, and returns it. Subsequent checks find
    the stored value normally.

    Falls back to DAYTYPE_NORMAL only when the stored decision AND today's
    forecast are both missing — at which point there's nothing to
    recompute against, and we don't write the fallback to InfluxDB
    (avoids polluting the decision history with a sentinel).
    """
    stored = _read_stored_decision(query_api, bucket, today_iso)
    if stored is not None:
        return stored

    log("info", "today_decision_missing_recomputing", today=today_iso)

    today_forecast = fetch_latest_forecast(query_api, bucket, "today")
    if today_forecast is None:
        log("warn", "today_decision_no_forecast_falling_back",
            today=today_iso, day_type=DAYTYPE_NORMAL)
        return DAYTYPE_NORMAL

    # Day-after for streak detection — today might be HOT_STREAK_DAY1 if
    # tomorrow is also HOT.
    tomorrow_forecast = fetch_latest_forecast(query_api, bucket, "tomorrow")
    comed_price = fetch_latest_comed(query_api, bucket)

    day_type, reasons = decide_day_type(today_forecast, day2_forecast=tomorrow_forecast)

    log("info", "today_decision_recomputed",
        today=today_iso, day_type=day_type,
        reason=reasons.get("reason"),
        high_f=reasons.get("high_f"),
        comed_price_now=comed_price)

    write_decision(write_api, bucket, today_iso, day_type, reasons, comed_price)
    return day_type


def vacation_schedule(override: Override) -> list[ScheduleAction]:
    """Synthesize a schedule from a vacation override -- one re-affirm action
    every VACATION_PING_INTERVAL_HOURS to keep the setpoint pinned (in case
    something else briefly clears the Hold)."""
    cool = override.cool_setpoint_f or 80
    heat = override.heat_setpoint_f or HEAT_SETPOINT_FLOOR_F
    fan = override.fan_mode  # may be None
    actions = []
    for hr in range(0, 24, VACATION_PING_INTERVAL_HOURS):
        actions.append(ScheduleAction(
            hour=hr, minute=0, label="VACATION_AFFIRM",
            cool_setpoint_f=cool, heat_setpoint_f=heat, fan_mode=fan,
        ))
    return actions


async def run_schedule_check(cfg: Config, c4: C4Client, query_api, write_api,
                              tz: ZoneInfo, now_local: datetime,
                              firing: FiringState) -> None:
    """Check if any schedule action fires at the current local minute.

    Override resolution order (first match wins):
      1. Active vacation override (flat setpoint all day) -> synthetic schedule
      2. Active day_type override -> use override's day_type schedule
      3. Forecast-derived day_type from latest hvac.decisions -> normal schedule
    """
    today_iso = now_local.date().isoformat()
    overrides = load_overrides(cfg.overrides_file)
    active_override = find_active_override(overrides, today_iso)

    if active_override and active_override.is_vacation():
        day_type = "VACATION"
        schedule = vacation_schedule(active_override)
        override_note = active_override.note
    elif active_override and active_override.is_day_type_override():
        day_type = active_override.day_type or DAYTYPE_NORMAL
        schedule = schedule_for(day_type)
        override_note = active_override.note
    else:
        day_type = fetch_today_decision(query_api, write_api, cfg.influx_bucket, today_iso)
        schedule = schedule_for(day_type)
        override_note = ""

    # Pull today's forecast (for dewpoint humid override). Falls back gracefully
    # if forecast unavailable -- humid override just won't apply.
    today_forecast = fetch_latest_forecast(query_api, cfg.influx_bucket, "today")
    today_dewpoint_f = (today_forecast or {}).get("max_dewpoint_f")

    fired_anything = False
    for action in schedule:
        if now_local.hour != action.hour or now_local.minute != action.minute:
            continue
        key = (today_iso, action.hour, action.minute)
        if key in firing.fired_actions:
            continue
        firing.fired_actions.add(key)

        cool_to_apply, setpoint_reason = resolve_cool_setpoint(action, today_dewpoint_f)
        snapshot = await read_thermostat_snapshot(c4)
        applied, error = await execute_action(c4, action, cool_to_apply,
                                               snapshot, cfg.dry_run)
        write_action(write_api, cfg.influx_bucket, day_type, action,
                      cool_to_apply, action.fan_mode, setpoint_reason,
                      cfg.dry_run, applied, snapshot, error)
        log("info", "action_fired",
            day_type=day_type,
            label=action.label,
            cool_setpoint_f=cool_to_apply,
            heat_setpoint_f=action.heat_setpoint_f,
            fan_mode=action.fan_mode,
            setpoint_reason=setpoint_reason,
            today_dewpoint_f=today_dewpoint_f,
            override_active=bool(active_override),
            override_note=override_note,
            dry_run=cfg.dry_run,
            applied=applied,
            error=error,
            hvac_mode_before=snapshot.get("hvac_mode"),
            indoor_temp_before_f=snapshot.get("indoor_temp_f"),
            indoor_humidity_before_pct=snapshot.get("humidity"),
            cool_setpoint_before_f=snapshot.get("cool_setpoint_f"),
            heat_setpoint_before_f=snapshot.get("heat_setpoint_f"))
        fired_anything = True

    if fired_anything:
        # Prune fired_actions to today only (keep memory bounded)
        firing.fired_actions = {k for k in firing.fired_actions if k[0] == today_iso}


# ---- Main loop -------------------------------------------------------------

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

    def handle_stop(signum, _frame):
        log("info", "signal_received", signum=signum)
        stop.set()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    health_marker = Path("/tmp/last_tick_ok")
    last_minute_seen = -1
    while not stop.is_set():
        now_local = datetime.now(tz)
        # Tick once per minute boundary
        if now_local.minute != last_minute_seen:
            last_minute_seen = now_local.minute

            # Daily decision at decision_hour:00
            if (now_local.hour == cfg.decision_hour and now_local.minute == 0
                    and firing.last_decision_date != (now_local.date() + timedelta(days=1)).isoformat()):
                try:
                    await run_decision(cfg, c4, query_api, write_api, tz, firing)
                except Exception as exc:
                    log("error", "decision_failed", error=str(exc), error_type=type(exc).__name__)

            # Schedule actions
            try:
                await run_schedule_check(cfg, c4, query_api, write_api, tz, now_local, firing)
            except Exception as exc:
                log("error", "schedule_check_failed", error=str(exc), error_type=type(exc).__name__)

            # Heartbeat for Docker healthcheck (touch every minute regardless of
            # whether actions fired). If this stops, the container is wedged.
            try:
                health_marker.touch()
            except Exception:
                pass

        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass

    log("info", "shutdown")
    influx.close()
    return 0


def main() -> int:
    cfg = Config.from_env()
    return asyncio.run(main_async(cfg))


if __name__ == "__main__":
    sys.exit(main())
