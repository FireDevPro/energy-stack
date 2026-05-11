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
  * DRY_RUN env var (default true) -- logs commands without pushing.
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
    SCHEDULER_DRY_RUN           "true"|"false" (default "true")
    SCHEDULER_DECISION_HOUR     Hour-of-day to decide tomorrow (default 21)
    SCHEDULER_REVISIT_HOURS     Comma-separated local hours at which to re-poll
                                today's forecast and re-classify if it shifted
                                (default "6,11"; empty disables)
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

from pjm_5cp import (
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
from precool import (
    dtod_delivery_rates_24h,
    should_add_price_aware_precool,
    should_deepen_precool,
)
from price_overlay import (
    NORMAL_TIER_NAME,
    PriceOverlayState,
    evaluate_price_overlay,
)
from safety_supervisor import validate_setpoints


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
    ScheduleAction(4,  0, "HOT_PRE_COOL",     cool_setpoint_f=68),
    ScheduleAction(12, 0, "HOT_COAST",        cool_setpoint_f=80, fan_mode="Circulate",
                   cool_setpoint_humid_f=76),
    ScheduleAction(19, 0, "HOT_RECOVER",      cool_setpoint_f=75, fan_mode="Auto"),
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
    ScheduleAction(19, 0, "HOT_RECOVER",           cool_setpoint_f=75, fan_mode="Auto"),
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
    revisit_hours: tuple[int, ...]

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
            # Local hours at which to re-poll today's NWS forecast and re-classify
            # the day-type if it shifted enough to change the schedule. Default
            # 06:00 + 11:00 catches the morning forecast refresh AND the late-
            # morning update before the noon coast transition. NWS day-1 max-T
            # forecasts mis-classify ~1 in 3 marginal Midwest summer days
            # (per NSSL/Brooks public-forecast verification); the day-ahead-only
            # commitment leaves that error in place all day. Empty = disabled.
            revisit_hours=revisit_hours,
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


def fetch_rto_peak_forecast_today(query_api, bucket: str) -> float | None:
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
            v = record.get_value()
            if v is not None:
                return float(v)
    return None


# ---- Day-type decision -----------------------------------------------------

# Day-type thresholds (locked per EXPERIMENT_DESIGN.md Appendix A; recalibrated
# May 2026 against the 2025 ComEd RTP price-spike distribution). The earlier
# >=95F HOT threshold reflected absolute heat severity; the new threshold is
# tuned to capture price-spike risk: ~52% of 2025 spike days had max temp
# >=85F or apparent >=90F. The remaining ~40% of spikes are grid-event-driven
# (forecast-mild but PJM-stressed) and are addressed by the price-overlay
# layer (§2) and 5CP detector (§3), not by day-type classification.
HOT_TEMP_THRESHOLD_F = 85
HOT_APPARENT_THRESHOLD_F = 90
NORMAL_TEMP_THRESHOLD_F = 75


def _classify_one_day(forecast: dict | None) -> str:
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


def decide_day_type(forecast: dict | None,
                    day2_forecast: dict | None = None,
                    *,
                    tomorrow_peak_load_mw: float | None = None,
                    season_5th_highest_mw: float | None = None
                    ) -> tuple[str, dict]:
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
        return DAYTYPE_NORMAL, {"reason": "no_forecast_available", "fallback": True}
    high_f = forecast.get("high_f")
    apparent_max_f = forecast.get("apparent_max_f")
    is_heat_adv = bool(forecast.get("is_heat_advisory", 0))
    dewpoint_f = forecast.get("max_dewpoint_f")

    reasons = {
        "high_f": high_f,
        "apparent_max_f": apparent_max_f,
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
            reasons["day2_apparent_max_f"] = (day2_forecast or {}).get("apparent_max_f")
            reasons["day2_is_heat_advisory"] = bool((day2_forecast or {}).get("is_heat_advisory", 0))
            return DAYTYPE_HOT_STREAK_DAY1, reasons
        # §7 single-day forecast 5CP-risk path: deepen pre-cool when PJM
        # peak forecast clearly exceeds the season-to-date 5th highest
        # AND tomorrow's high reaches 90F.
        if (tomorrow_peak_load_mw is not None
                and season_5th_highest_mw is not None
                and should_deepen_precool(
                    {"max_temp_f": high_f, "peak_load_mw": tomorrow_peak_load_mw},
                    season_5th_highest_mw,
                )):
            reasons["reason"] = "forecast_5cp_risk_single_day"
            reasons["tomorrow_peak_load_mw"] = tomorrow_peak_load_mw
            reasons["season_5th_highest_mw"] = season_5th_highest_mw
            return DAYTYPE_HOT_STREAK_DAY1, reasons
        if is_heat_adv:
            reasons["reason"] = "heat_advisory"
        elif high_f is not None and high_f >= HOT_TEMP_THRESHOLD_F:
            reasons["reason"] = f"high_ge_{HOT_TEMP_THRESHOLD_F}"
        else:
            reasons["reason"] = f"apparent_ge_{HOT_APPARENT_THRESHOLD_F}"
        return DAYTYPE_HOT, reasons
    if base_type == DAYTYPE_NORMAL:
        reasons["reason"] = f"high_{NORMAL_TEMP_THRESHOLD_F}_to_{HOT_TEMP_THRESHOLD_F - 1}"
        return DAYTYPE_NORMAL, reasons
    reasons["reason"] = f"high_lt_{NORMAL_TEMP_THRESHOLD_F}"
    return DAYTYPE_MILD, reasons


def schedule_for(day_type: str) -> list[ScheduleAction]:
    return {
        DAYTYPE_HOT_STREAK_DAY1: HOT_STREAK_DAY1_SCHEDULE,
        DAYTYPE_HOT:             HOT_SCHEDULE,
        DAYTYPE_NORMAL:          NORMAL_SCHEDULE,
        DAYTYPE_MILD:            MILD_SCHEDULE,
    }.get(day_type, NORMAL_SCHEDULE)


# Locked per EXPERIMENT_DESIGN.md Appendix A. Effective cool setpoint applied
# during a 5CP-eligibility window or scarcity-tier price spike; 85F is high
# enough to functionally shut the AC off while staying inside the safety
# supervisor's [65, 86]F clamp.
COOL_SHUTOFF_F = 85


@dataclass(frozen=True)
class LayerResolution:
    """Audit-grade record of the layer-priority resolution applied to one
    scheduler tick. Fields populate hvac.actions so the operator can replay
    why the effective setpoint differs from the schedule baseline.

    The resolution rule is "warmer wins": the schedule baseline, the
    price-overlay layer, and the 5CP-shutoff layer each propose a cool
    setpoint, and the effective setpoint is the max of those proposals
    (capped later by the safety supervisor's 86F upper bound).
    """
    schedule_cool_f: int
    price_overlay_tier: str           # "normal" | "elevated" | "scarcity"
    price_cool_f: int                 # Schedule baseline if no tier active
    fivecp_active: bool
    fivecp_cool_f: int                # COOL_SHUTOFF_F if active else price_cool_f
    effective_cool_f: int             # max(schedule, price, fivecp)


def resolve_layer_priority(
    schedule_cool_f: int,
    *,
    price_overlay_tier: str = "normal",
    price_offset_f: int = 0,
    price_override_f: int | None = None,
    fivecp_active: bool = False,
    fivecp_shutoff_f: int = COOL_SHUTOFF_F,
) -> LayerResolution:
    """Resolve the effective cool setpoint across schedule / price / 5CP layers.

    Layers (warmer-wins; safety supervisor enforces the 65-86F floor/ceiling
    after this function returns):

      1. **Schedule baseline** -- the day-type schedule's cool_setpoint_f
         after `resolve_cool_setpoint` applies humid-override logic.
      2. **Price overlay** (§2) -- elevated tier adds ``price_offset_f`` to
         the schedule baseline; scarcity tier replaces it with
         ``price_override_f``. ``price_overlay_tier="normal"`` means no
         overlay is active.
      3. **5CP shutoff** (§3) -- when ``fivecp_active`` the 5CP layer
         proposes ``fivecp_shutoff_f`` (default 85F).

    The function is a pure transform; it doesn't read any global state. §2
    and §3 evaluate their respective conditions and pass the resulting
    arguments in.
    """
    if price_override_f is not None:
        price_cool_f = price_override_f
    else:
        price_cool_f = schedule_cool_f + price_offset_f

    fivecp_cool_f = fivecp_shutoff_f if fivecp_active else price_cool_f
    effective_cool_f = max(schedule_cool_f, price_cool_f, fivecp_cool_f)

    return LayerResolution(
        schedule_cool_f=schedule_cool_f,
        price_overlay_tier=price_overlay_tier,
        price_cool_f=price_cool_f,
        fivecp_active=fivecp_active,
        fivecp_cool_f=fivecp_cool_f,
        effective_cool_f=effective_cool_f,
    )


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
    # last effective cool setpoint pushed to the thermostat. When a per-
    # tick layer evaluation produces a different effective cool setpoint,
    # run_schedule_check re-pushes mid-period without waiting for the
    # next scheduled action. Reset to None on release_hold actions and on
    # day boundaries.
    last_schedule_cool_f: int | None = None
    last_action_label: str = ""
    last_pushed_effective_cool_f: int | None = None
    # Throttle for hvac.5cp_state audit writes. Spec calls for ~every-5-min
    # cadence (288 rows/day) so dashboards can plot the ratio + derivative
    # trace without flooding the bucket at the 1-min scheduler tick rate.
    last_5cp_audit_at_utc: datetime | None = None


def fetch_day_ahead_prices_for_date(
    query_api, bucket: str, target_date_iso: str, tz: ZoneInfo,
) -> list[float] | None:
    """Pull the 24 hourly day-ahead LMPs for ``target_date_iso`` from
    ``pjm.lmp_da_hourly`` and convert them to cents/kWh (the unit the
    §2 price overlay tier thresholds are measured in).

    PJM's day-ahead market clears around 16:00 ET and the poller runs
    at 17:00 CT, so tomorrow's 24-hour vector is available by 18:00 CT
    every day. Returns None when fewer than 24 hourly observations
    exist for the target date (e.g., a 21:00 decision that beat the
    DA-LMP write, or a market-cancelled day).

    Conversion: ``$/MWh ÷ 10 = ¢/kWh``. PJM publishes ``total_lmp_da``
    in $/MWh; the price overlay's locked thresholds (10c, 20c) and the
    §7 cheap/spike thresholds (3c, 10c) are all cents/kWh.
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
    prices_per_mwh: list[float] = []
    for table in query_api.query(flux):
        for record in table.records:
            v = record.get_value()
            if v is not None:
                prices_per_mwh.append(float(v))
    if len(prices_per_mwh) < 24:
        return None
    # $/MWh -> cents/kWh: ÷ 10. (e.g., $50/MWh = $0.05/kWh = 5c/kWh)
    return [p / 10.0 for p in prices_per_mwh[:24]]


def compute_price_aware_precool_window(
    query_api, bucket: str, target_date_iso: str, tz: ZoneInfo,
    *, forecast_period: str = "tomorrow",
) -> dict | None:
    """Resolve the §7 day-ahead price-aware pre-cool window for the
    target date. Composes fetch_day_ahead_prices_for_date,
    fetch_latest_forecast, and the pure ``should_add_price_aware_precool``
    decision rule.

    ``forecast_period`` selects which ``nws.forecast`` row the function
    reads ("tomorrow" at 21:00 the night before; "today" for runtime
    re-evaluation in run_schedule_check). Returns None when either
    input is unavailable or the decision rule says no window applies.

    The ComEd Delivery TOD rate schedule (P2.6) is always layered on
    top of the supply prices for cheap-window *ranking*. Chris is
    enrolled in DTOD; the schedule is fixed year-round and identical
    every day, so we build the delivery vector from the static table
    rather than from InfluxDB.
    """
    prices = fetch_day_ahead_prices_for_date(query_api, bucket, target_date_iso, tz)
    if prices is None:
        return None
    forecast = fetch_latest_forecast(query_api, bucket, forecast_period)
    if forecast is None:
        return None
    return should_add_price_aware_precool(
        prices, forecast, delivery_rates_cents=dtod_delivery_rates_24h(),
    )


def write_precool_window(
    write_api, bucket: str, target_date_iso: str, window: dict,
) -> None:
    """Persist a §7 price-aware pre-cool window to InfluxDB so the
    schedule-check tick can read it back the next day. ``hvac.precool_window``
    is event-sourced (one row per decision); the schedule check looks up
    the latest row matching today's target_date tag."""
    p = (Point("hvac.precool_window")
         .tag("target_date", target_date_iso)
         .tag("source", "decision")  # vs "schedule_check_recompute"
         .field("hour_ct", int(window["hour_ct"]))
         .field("depth_f", int(window["depth_f"])))
    write_api.write(bucket=bucket, record=p)


def read_precool_window_for_date(
    query_api, bucket: str, target_date_iso: str,
) -> dict | None:
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
        existing_cool = existing.cool_setpoint_f if existing.cool_setpoint_f is not None else 999
        new_cool = action.cool_setpoint_f if action.cool_setpoint_f is not None else 999
        if new_cool < existing_cool:
            by_time[key] = action
    return sorted(by_time.values(), key=lambda a: (a.hour, a.minute))


def precool_window_action(window: dict) -> ScheduleAction:
    """Synthesize a ScheduleAction at the §7 cheap-window's start hour
    with the depth_f the decision rule selected. fan_mode left None so
    the action doesn't override the base schedule's fan setting (the
    ECM blower's circulate cycle stays in place during cheap-window
    pre-cool the same way it does during HOT_PRE_COOL)."""
    return ScheduleAction(
        hour=int(window["hour_ct"]),
        minute=0,
        label="PRICE_AWARE_PRECOOL",
        cool_setpoint_f=int(window["depth_f"]),
        heat_setpoint_f=HEAT_SETPOINT_FLOOR_F,
        fan_mode=None,
    )


def _fetch_pjm_inputs_for_target_date(
    query_api, bucket: str, target_date_iso: str, tz: ZoneInfo,
) -> tuple[float | None, float]:
    """Fetch ``(target_date_peak_load_mw, season_5th_highest_mw)`` so the
    §7 forecast 5CP-risk pre-cool deepening trigger can fire at 21:00 the
    night before (or at the 06:00/11:00 revisit). The function exists so
    all three production decide_day_type callers (run_decision,
    run_decision_revisit, fetch_today_decision) populate the §7 inputs
    consistently.

    ``target_date_iso`` is the date being classified — for run_decision
    that's tomorrow; for revisit/lazy-recompute paths that's today. The
    season-to-date 5th-highest is independent of the target date, but
    PJM's forecast peak is per-date so it's queried via
    fetch_forecast_peak_for_date.

    Returns ``(None, season_5th_mw)`` when PJM's forecast for the target
    date hasn't published yet (e.g., 21:00 fired before tomorrow's load
    forecast was posted). decide_day_type's §7 path gates on both inputs
    being non-None, so a None peak silently falls back to the multi-day
    HOT_STREAK_DAY1 path.
    """
    # §7 pre-cool deepening uses ComEd-zone forecast peak vs ComEd-zone
    # season-5th; consistent scale. The dual-scope OR (§3) lives in the
    # live detector at the peak hour, not the night-before pre-cool
    # decision. Explicit scope arg keeps it impossible to silently mix
    # RTO/ComEd scales here (which was the latent bug fixed in the
    # prior commit).
    target_dt = datetime.fromisoformat(target_date_iso).replace(tzinfo=tz)
    season_start_utc, season_end_utc = cooling_season_window_utc(target_dt, tz=tz)
    # Cap end at the target date itself so "season-to-date" semantics
    # are honored for in-season targets. Off-season targets get the
    # last completed season's full Jun-Sep window.
    target_utc = target_dt.astimezone(timezone.utc)
    capped_end_utc = min(season_end_utc, target_utc) if in_cooling_season(target_dt) else season_end_utc
    season_5th_mw = update_season_5th_highest(
        query_api, bucket, season_start_utc, capped_end_utc,
        zone=COMED_SCOPE.metered_load_zone,
        fallback_mw=COMED_SCOPE.pre_season_fallback_5th_mw,
    )
    target_peak_mw = fetch_forecast_peak_for_date(
        query_api, bucket, target_date_iso, tz=tz,
    )
    return target_peak_mw, season_5th_mw


def write_5cp_state(
    write_api, bucket: str,
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
    p = (Point("hvac.5cp_state")
         .tag("scope", scope)
         .tag("zone", zone)
         .tag("is_active", "true" if is_active else "false")
         .field("current_load_mw", float(current_load_mw))
         .field("season_5th_highest_mw", float(season_5th_highest_mw))
         .field("load_ratio", float(ratio))
         .field("load_derivative_mw_per_hour", float(load_derivative_mw_per_hour))
         .field("forecast_peak_today_mw", float(forecast_peak_today_mw))
         )
    write_api.write(bucket=bucket, record=p)


def write_price_overlay_transition(
    write_api, bucket: str,
    *, prev_tier: str, new_tier: str,
    current_price_cents: float,
    schedule_cool_f: int, effective_cool_f: int,
    triggered_at_utc: datetime | None,
) -> None:
    """Write one ``hvac.price_overlay`` row when the price-overlay tier
    changes between scheduler ticks. Skipped when the tier is unchanged
    so dashboards aren't drowned in no-op rows."""
    p = (Point("hvac.price_overlay")
         .tag("prev_tier", prev_tier)
         .tag("new_tier", new_tier)
         .field("current_price_cents", float(current_price_cents))
         .field("schedule_cool_f", float(schedule_cool_f))
         .field("effective_cool_f", float(effective_cool_f))
         .field("triggered_at_utc",
                triggered_at_utc.isoformat() if triggered_at_utc else "")
         )
    write_api.write(bucket=bucket, record=p)


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
                 cool_applied_f: int, heat_applied_f: int,
                 fan_mode_applied: str | None,
                 setpoint_reason: str, dry_run: bool, applied: bool,
                 thermostat_state_before: dict, error: str | None = None,
                 supervisor_decision: str = "approved",
                 supervisor_reason: str | None = None,
                 layer_resolution: LayerResolution | None = None) -> None:
    p = (Point("hvac.actions")
         .tag("day_type", day_type)
         .tag("action_label", action.label)
         .tag("dry_run", "true" if dry_run else "false")
         .tag("supervisor_decision", supervisor_decision)
         .field("cool_setpoint_f", float(cool_applied_f))
         .field("heat_setpoint_f", float(heat_applied_f))
         .field("fan_mode", fan_mode_applied or "")
         .field("setpoint_reason", setpoint_reason)
         .field("supervisor_reason", supervisor_reason or "")
         .field("cool_setpoint_proposed_f", float(action.cool_setpoint_f or 0))
         .field("heat_setpoint_proposed_f", float(action.heat_setpoint_f))
         .field("applied", int(applied))
         .field("error", error or "")
         .field("hvac_mode_before", str(thermostat_state_before.get("hvac_mode") or ""))
         .field("indoor_temp_before_f", float(thermostat_state_before.get("indoor_temp_f") or 0))
         .field("cool_setpoint_before_f", float(thermostat_state_before.get("cool_setpoint_f") or 0))
         .field("heat_setpoint_before_f", float(thermostat_state_before.get("heat_setpoint_f") or 0))
         .field("indoor_humidity_before_pct", float(thermostat_state_before.get("humidity") or 0))
         )
    if layer_resolution is not None:
        # Layer-priority audit fields (§4). Always emitted when the
        # resolution is computed so dashboards can answer "why was the
        # effective setpoint different from the schedule baseline?"
        p = (p
             .tag("price_overlay_tier", layer_resolution.price_overlay_tier)
             .tag("fivecp_active", "true" if layer_resolution.fivecp_active else "false")
             .field("schedule_cool_f", float(layer_resolution.schedule_cool_f))
             .field("price_cool_f", float(layer_resolution.price_cool_f))
             .field("fivecp_cool_f", float(layer_resolution.fivecp_cool_f))
             .field("effective_cool_f", float(layer_resolution.effective_cool_f))
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
                          heat_setpoint_to_apply: int,
                          state: dict, dry_run: bool) -> tuple[bool, str | None]:
    """Apply the action to the thermostat. Returns (applied, error).

    Both setpoints are passed in explicitly (rather than read from
    `action.heat_setpoint_f` / etc.) because the safety supervisor may
    have clamped or overridden them before this call.

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
            await c4.call_with_reauth(lambda: climate.set_fan_mode(action.fan_mode))
            await asyncio.sleep(1)
        # Pin the override so thermostat baseline doesn't override our setpoint
        await c4.call_with_reauth(lambda: climate.set_hold_mode("Permanent"))
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run_decision_revisit(cfg: Config, query_api, write_api, today_iso: str) -> None:
    """Re-evaluate today's day-type against the latest NWS forecast.

    Runs at each ``cfg.revisit_hours`` to catch forecast-bust days where
    the 21:00-yesterday commitment turned out wrong (NWS day-1 max-T
    forecasts mis-classify ~1 in 3 marginal Midwest summer days per
    NSSL/Brooks public-forecast verification). If the live forecast
    classifies today differently than the stored decision, overwrite it;
    the next ``run_schedule_check`` tick uses the new day-type
    automatically. Already-fired actions stay fired (no retroactive
    catch-up); future actions in the new schedule will fire at their
    scheduled times.

    Logs the comparison either way so operator can audit.
    """
    stored = _read_stored_decision(query_api, cfg.influx_bucket, today_iso)
    today_forecast = fetch_latest_forecast(query_api, cfg.influx_bucket, "today")
    if today_forecast is None:
        log("warn", "revisit_no_forecast", today=today_iso, stored=stored)
        return

    tomorrow_forecast = fetch_latest_forecast(query_api, cfg.influx_bucket, "tomorrow")
    comed_price = fetch_latest_comed(query_api, cfg.influx_bucket)
    tz = ZoneInfo(cfg.tz_name)
    target_peak_mw, season_5th_mw = _fetch_pjm_inputs_for_target_date(
        query_api, cfg.influx_bucket, today_iso, tz,
    )
    new_day_type, reasons = decide_day_type(
        today_forecast, day2_forecast=tomorrow_forecast,
        tomorrow_peak_load_mw=target_peak_mw,
        season_5th_highest_mw=season_5th_mw,
    )

    if stored == new_day_type:
        log("info", "revisit_no_change",
            today=today_iso,
            day_type=stored,
            forecast_high_f=today_forecast.get("high_f"),
            forecast_dewpoint_f=today_forecast.get("max_dewpoint_f"))
        return

    log("info", "revisit_reclassified",
        today=today_iso,
        old_day_type=stored,
        new_day_type=new_day_type,
        forecast_high_f=today_forecast.get("high_f"),
        is_heat_advisory=reasons.get("is_heat_advisory"),
        reason=reasons.get("reason"))
    write_decision(write_api, cfg.influx_bucket, today_iso, new_day_type, reasons, comed_price)


async def run_decision(cfg: Config, c4: C4Client, query_api, write_api, tz: ZoneInfo,
                        firing: FiringState) -> None:
    """Read tomorrow's forecast (with day-after lookahead), decide day-type, log."""
    forecast = fetch_latest_forecast(query_api, cfg.influx_bucket, "tomorrow")
    day2 = fetch_latest_forecast(query_api, cfg.influx_bucket, "day2")
    comed_price = fetch_latest_comed(query_api, cfg.influx_bucket)
    decision_date = (datetime.now(tz).date() + timedelta(days=1)).isoformat()
    target_peak_mw, season_5th_mw = _fetch_pjm_inputs_for_target_date(
        query_api, cfg.influx_bucket, decision_date, tz,
    )
    day_type, reasons = decide_day_type(
        forecast, day2_forecast=day2,
        tomorrow_peak_load_mw=target_peak_mw,
        season_5th_highest_mw=season_5th_mw,
    )
    write_decision(write_api, cfg.influx_bucket, decision_date, day_type, reasons, comed_price)

    # §7 day-ahead price-aware pre-cool window. Computed at 21:00 the
    # night before per ARM_B_IMPLEMENTATION; if a qualifying cheap+spike
    # pattern exists, persist a hvac.precool_window row so run_schedule_check
    # can inject the synthetic ScheduleAction tomorrow.
    precool_window = compute_price_aware_precool_window(
        query_api, cfg.influx_bucket, decision_date, tz,
        forecast_period="tomorrow",
    )
    if precool_window is not None:
        write_precool_window(write_api, cfg.influx_bucket, decision_date, precool_window)

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

    # §7 forecast 5CP-risk inputs. fetch_today_decision doesn't carry a tz
    # in its signature; resolve it from the SCHEDULER_TZ env var the same
    # way Config.from_env() does so the helper sees the same wall-clock
    # day boundary.
    tz = ZoneInfo(os.environ.get("SCHEDULER_TZ", "America/Chicago"))
    target_peak_mw, season_5th_mw = _fetch_pjm_inputs_for_target_date(
        query_api, bucket, today_iso, tz,
    )
    day_type, reasons = decide_day_type(
        today_forecast, day2_forecast=tomorrow_forecast,
        tomorrow_peak_load_mw=target_peak_mw,
        season_5th_highest_mw=season_5th_mw,
    )

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
    price_offset_f: int
    price_override_f: int | None
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


def _evaluate_layer_inputs(query_api, write_api, cfg: Config,
                            firing: FiringState, now_local: datetime) -> LayerInputs:
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

    # ---- Price overlay (§2) ----
    current_price_cents = fetch_latest_comed(query_api, cfg.influx_bucket)
    prev_tier = firing.price_overlay_state.current_tier
    if current_price_cents is None:
        # Price feed unavailable: leave overlay state untouched. The
        # current tier (whichever we last entered) continues to apply.
        active_tier = None
        price_offset_f = 0
        price_override_f = None
        price_tier_name = prev_tier
    else:
        active_tier, firing.price_overlay_state = evaluate_price_overlay(
            current_price_cents, firing.price_overlay_state, now_utc,
        )
        if active_tier is None:
            price_offset_f = 0
            price_override_f = None
            price_tier_name = NORMAL_TIER_NAME
        else:
            price_offset_f = active_tier.cool_setpoint_offset_f
            price_override_f = active_tier.cool_setpoint_override_f
            price_tier_name = active_tier.name

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
    season_5th_mw = comed_eval.season_5th_mw

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
            schedule_cool_f=firing.last_schedule_cool_f or 0,
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
            if ev.log_fields.get("data_status") != "ok":
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


async def _push_layer_change_mid_period(
    cfg: Config, c4: C4Client, write_api,
    firing: FiringState, day_type: str, layer_inputs: LayerInputs,
    today_dewpoint_f: float | None, override_note: str,
    now_local: datetime,
) -> None:
    """When the per-tick layer evaluation produces a different effective
    cool setpoint than the last value pushed, re-push without waiting for
    the next scheduled action. Triggered when a price tier transitions or
    5CP active state crosses inside an action period.

    Skipped silently when no schedule baseline has been established yet
    today (e.g., before the first non-release-hold action fires) since
    there's no schedule baseline to layer on top of.
    """
    if firing.last_schedule_cool_f is None:
        return  # no baseline to layer on top of

    schedule_cool = firing.last_schedule_cool_f
    layer_resolution = resolve_layer_priority(
        schedule_cool,
        price_overlay_tier=layer_inputs.price_tier_name,
        price_offset_f=layer_inputs.price_offset_f,
        price_override_f=layer_inputs.price_override_f,
        fivecp_active=layer_inputs.fivecp_active,
    )
    if layer_resolution.effective_cool_f == firing.last_pushed_effective_cool_f:
        return  # nothing changed; skip the push

    # Construct a synthetic action for execute_action / write_action / log.
    synthetic_action = ScheduleAction(
        hour=now_local.hour, minute=now_local.minute,
        label=f"MID_PERIOD_REPUSH:{firing.last_action_label}",
        cool_setpoint_f=schedule_cool,
        heat_setpoint_f=HEAT_SETPOINT_FLOOR_F,
        fan_mode=None,  # leave fan mode alone mid-period
    )

    snapshot = await read_thermostat_snapshot(c4)
    decision = validate_setpoints(
        layer_resolution.effective_cool_f, HEAT_SETPOINT_FLOOR_F, snapshot,
    )
    sup_cool = decision.cool_setpoint_f
    sup_heat = decision.heat_setpoint_f
    sup_decision = decision.decision
    sup_reason = decision.reason
    if decision.needs_alert:
        level = "error" if decision.decision == "emergency" else "warn"
        log(level, "supervisor_intervention",
            day_type=day_type, label=synthetic_action.label,
            decision=decision.decision, reason=decision.reason,
            cool_proposed=layer_resolution.effective_cool_f,
            cool_applied=sup_cool,
            heat_proposed=HEAT_SETPOINT_FLOOR_F,
            heat_applied=sup_heat,
            indoor_temp_f=snapshot.get("indoor_temp_f"))

    applied, error = await execute_action(
        c4, synthetic_action, sup_cool, sup_heat, snapshot, cfg.dry_run,
    )
    write_action(
        write_api, cfg.influx_bucket, day_type, synthetic_action,
        sup_cool, sup_heat, None, "mid_period_layer_change",
        cfg.dry_run, applied, snapshot, error,
        supervisor_decision=sup_decision, supervisor_reason=sup_reason,
        layer_resolution=layer_resolution,
    )
    log("info", "mid_period_repush",
        day_type=day_type, label=synthetic_action.label,
        cool_setpoint_f=sup_cool,
        prior_effective_cool_f=firing.last_pushed_effective_cool_f,
        new_effective_cool_f=layer_resolution.effective_cool_f,
        price_overlay_tier=layer_inputs.price_tier_name,
        fivecp_active=layer_inputs.fivecp_active,
        override_note=override_note,
        dry_run=cfg.dry_run, applied=applied, error=error)

    # Update the mid-period tracking variable regardless of dry_run.
    # This is the GUARD value the next tick uses to decide whether to
    # re-push; gating it on `not cfg.dry_run` left it None forever in
    # Arm A weeks and caused phantom MID_PERIOD_REPUSH audit rows on
    # every subsequent tick (effective != None evaluates True even
    # when nothing actually changed).
    firing.last_pushed_effective_cool_f = layer_resolution.effective_cool_f


async def run_schedule_check(cfg: Config, c4: C4Client, query_api, write_api,
                              tz: ZoneInfo, now_local: datetime,
                              firing: FiringState) -> None:
    """Check if any schedule action fires at the current local minute.

    Override resolution order (first match wins):
      1. Active vacation override (flat setpoint all day) -> synthetic schedule
      2. Active day_type override -> use override's day_type schedule
      3. Forecast-derived day_type from latest hvac.decisions -> normal schedule

    Layer evaluation runs every tick (per Critical #2 fix): the §2 price
    overlay and §3 5CP detector are evaluated, audit rows are written,
    and a mid-period re-push fires if the resulting effective cool
    setpoint differs from the last value pushed. The action-fire path is
    unchanged; it consumes the per-tick layer inputs.
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

    # §7 day-ahead price-aware pre-cool: read the window persisted at
    # 21:00 last night and inject a synthetic ScheduleAction. Skipped on
    # vacation/override schedules — the homeowner's vacation setpoint
    # supersedes the price-aware layer. ``merge_same_hour_actions_deepest_wins``
    # resolves conflicts when the synthetic action's hour matches a base
    # schedule action.
    if not active_override:
        precool_window = read_precool_window_for_date(
            query_api, cfg.influx_bucket, today_iso,
        )
        if precool_window is not None:
            schedule = merge_same_hour_actions_deepest_wins(
                schedule + [precool_window_action(precool_window)]
            )

    # ---- Per-tick layer evaluation (Critical #2 fix) ----
    # Always evaluate price overlay + 5CP, write audit rows, regardless of
    # whether a scheduled action fires this minute.
    layer_inputs = _evaluate_layer_inputs(query_api, write_api, cfg, firing, now_local)

    fired_anything = False
    for action in schedule:
        if now_local.hour != action.hour or now_local.minute != action.minute:
            continue
        key = (today_iso, action.hour, action.minute)
        if key in firing.fired_actions:
            continue
        firing.fired_actions.add(key)

        schedule_cool, setpoint_reason = resolve_cool_setpoint(action, today_dewpoint_f)
        snapshot = await read_thermostat_snapshot(c4)

        if action.release_hold:
            # Release-hold actions don't carry setpoints; skip layer
            # resolution and supervisor entirely. Reset the mid-period
            # baseline so a stale value doesn't trigger a phantom re-push.
            cool_to_apply = schedule_cool
            layer_resolution = None
            sup_cool = schedule_cool
            sup_heat = action.heat_setpoint_f
            sup_decision = "approved"
            sup_reason = None
            firing.last_schedule_cool_f = None
            firing.last_action_label = action.label
            firing.last_pushed_effective_cool_f = None
        else:
            layer_resolution = resolve_layer_priority(
                schedule_cool,
                price_overlay_tier=layer_inputs.price_tier_name,
                price_offset_f=layer_inputs.price_offset_f,
                price_override_f=layer_inputs.price_override_f,
                fivecp_active=layer_inputs.fivecp_active,
            )
            cool_to_apply = layer_resolution.effective_cool_f

            decision = validate_setpoints(cool_to_apply, action.heat_setpoint_f, snapshot)
            sup_cool = decision.cool_setpoint_f
            sup_heat = decision.heat_setpoint_f
            sup_decision = decision.decision
            sup_reason = decision.reason
            if decision.needs_alert:
                level = "error" if decision.decision == "emergency" else "warn"
                log(level, "supervisor_intervention",
                    day_type=day_type,
                    label=action.label,
                    decision=decision.decision,
                    reason=decision.reason,
                    cool_proposed=cool_to_apply,
                    cool_applied=decision.cool_setpoint_f,
                    heat_proposed=action.heat_setpoint_f,
                    heat_applied=decision.heat_setpoint_f,
                    indoor_temp_f=snapshot.get("indoor_temp_f"))

            firing.last_schedule_cool_f = schedule_cool
            firing.last_action_label = action.label

        applied, error = await execute_action(c4, action, sup_cool, sup_heat,
                                               snapshot, cfg.dry_run)
        write_action(write_api, cfg.influx_bucket, day_type, action,
                      sup_cool, sup_heat, action.fan_mode, setpoint_reason,
                      cfg.dry_run, applied, snapshot, error,
                      supervisor_decision=sup_decision,
                      supervisor_reason=sup_reason,
                      layer_resolution=layer_resolution)
        log("info", "action_fired",
            day_type=day_type,
            label=action.label,
            cool_setpoint_f=sup_cool,
            heat_setpoint_f=sup_heat,
            cool_setpoint_proposed_f=cool_to_apply,
            heat_setpoint_proposed_f=action.heat_setpoint_f,
            fan_mode=action.fan_mode,
            setpoint_reason=setpoint_reason,
            supervisor_decision=sup_decision,
            supervisor_reason=sup_reason,
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
        if not action.release_hold:
            # Track the "would-have-pushed" effective cool setpoint
            # regardless of dry_run state. last_pushed_effective_cool_f
            # is the GUARD value used by the mid-period re-push path
            # to detect a real change in effective cool. Gating it on
            # `not cfg.dry_run` left it None across dry-run weeks (Arm A),
            # which made the mid-period guard ``effective == None`` always
            # False — every minute wrote a phantom MID_PERIOD_REPUSH audit
            # row even though nothing changed.
            firing.last_pushed_effective_cool_f = sup_cool
        fired_anything = True

    # ---- Mid-period re-push (Critical #2 fix) ----
    # No new action fired this tick, but the per-tick layer evaluation
    # may have changed the effective cool setpoint inside an active
    # period (e.g., price tier crossed mid-COAST). Re-push if the new
    # effective differs from the last value sent.
    if not fired_anything:
        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, day_type, layer_inputs,
            today_dewpoint_f, override_note, now_local,
        )

    if fired_anything:
        # Prune fired_actions to today only (keep memory bounded)
        firing.fired_actions = {k for k in firing.fired_actions if k[0] == today_iso}
        # Same prune for revisits — keys are (date_iso, hour); date drift would
        # otherwise grow the set monotonically.
        firing.fired_revisits = {k for k in firing.fired_revisits if k[0] == today_iso}


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

            # Intra-day forecast revisit at each cfg.revisit_hours[*]:00
            today_iso = now_local.date().isoformat()
            revisit_key = (today_iso, now_local.hour)
            if (now_local.hour in cfg.revisit_hours and now_local.minute == 0
                    and revisit_key not in firing.fired_revisits):
                firing.fired_revisits.add(revisit_key)
                try:
                    run_decision_revisit(cfg, query_api, write_api, today_iso)
                except Exception as exc:
                    log("error", "revisit_failed", error=str(exc), error_type=type(exc).__name__)

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
