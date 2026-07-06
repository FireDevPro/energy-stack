"""Rev 4 telemetry: hvac.actions rows (field contract preserved for live
consumers: telegram-notifier filters `error`, thermostat-poller filtered
`dry_run` pre-retirement), hvac.arm_mode liveness rows (watchdog contract),
and decision-trace JSON lines.
Whitelisted imports only: influx_adapter.write_point, arm_calendar.current_arm_at.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..arm_calendar import current_arm_at
from ..influx_adapter import write_point

_TRANSITION_REASONS = {
    "REV4_UPGRADED_TO_ELEVATED", "REV4_UPGRADED_TO_SCARCITY",
    "REV4_DOWNGRADED_TO_ELEVATED", "REV4_RELEASED_TO_NORMAL",
    "REV4_RELEASED_STALE_BACKSTOP", "REV4_ZOMBIE_RELEASED",
    "REV4_ENGAGED", "REV4_ENGAGED_OVER_MANUAL", "REV4_CORRECTED",
    "REV4_WARM_ONLY_RELEASE",
}


def _log(level: str, msg: str, **fields: Any) -> None:
    rec: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(),
                           "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


class InfluxTelemetry:
    def __init__(self, *, write_api: Any, bucket: str, unit: str, config_id: str,
                 tz_name: str) -> None:
        self.write_api = write_api
        self.bucket = bucket
        self.unit = unit
        self.config_id = config_id
        self.tz = ZoneInfo(tz_name)

    def trace(self, **kw: Any) -> None:
        # ALWAYS emits (spec §Telemetry: the per-tick trace continues 24/7 —
        # a silent healthy controller is indistinguishable from a hung one).
        # Transitions at info, holds at debug; nothing is suppressed.
        reason = kw.get("reason_code", "")
        level = "info" if reason in _TRANSITION_REASONS else "debug"
        _log(level, "decision_trace.rev4_tick", **kw)

    def write_action(self, *, tier: str, action_label: str, dry_run: bool,
                     commanded_cool: float, commanded_heat: float,
                     schedule_cool: float, applied: bool, error: str,
                     hold_expires_at: str, snapshot_before: Any,
                     setpoint_reason: str, humidity_gated: bool) -> None:
        tags = {"unit": self.unit, "tier": tier, "action_label": action_label,
                "dry_run": "true" if dry_run else "false"}
        fields: dict[str, float | int | bool | str] = {
            "commanded_cool": float(commanded_cool),
            "commanded_heat": float(commanded_heat),
            "baseline_cool": float(schedule_cool),
            "schedule_cool": float(schedule_cool),
            "drift": float(commanded_cool) - float(schedule_cool),
            "humidity_gated": int(humidity_gated),
            "setpoint_reason": setpoint_reason,
            "applied": int(applied),
            "error": error or "",
            "config_id": self.config_id,
            "hold_expires_at": hold_expires_at or "",
            "actual_indoor_temp": float(snapshot_before.indoor_temp or 0),
            "actual_cool_before": float(snapshot_before.cool_setpoint),
            "actual_heat_before": float(snapshot_before.heat_setpoint),
            "actual_humidity": float(snapshot_before.humidity or 0),
        }
        write_point(self.write_api, self.bucket, "hvac.actions",
                    tags=tags, fields=fields)

    def write_price_overlay(self, *, prev_tier: str, new_tier: str,
                            current_price_cents: float, baseline_cool: float,
                            commanded_cool: float, triggered_at_utc: str) -> None:
        # Tier transitions only (rev 3 field contract preserved incl.
        # triggered_at_utc — see app.py's writer + SERVICES.md).
        write_point(self.write_api, self.bucket, "hvac.price_overlay",
                    tags={"prev_tier": prev_tier, "new_tier": new_tier,
                          "unit": self.unit},
                    fields={"current_price_cents": float(current_price_cents),
                            "baseline_cool": float(baseline_cool),
                            "commanded_cool": float(commanded_cool),
                            "triggered_at_utc": triggered_at_utc})

    def write_arm_mode(self, now_ct: datetime, scheduler_mode: str) -> None:
        # current_arm_at requires NAIVE CT (rev 3: app.py stripped tzinfo too).
        arm = current_arm_at(now_ct.replace(tzinfo=None))
        if arm is None:
            write_point(self.write_api, self.bucket, "hvac.arm_mode",
                        tags={"scheduler_mode": scheduler_mode},
                        fields={"mode_actual": "outside-window"}, time=now_ct)
            return
        # `experiment` mode (arm-gated A/B) is the retained 2027 layer; rev 4
        # runs shadow|production only, so every in-window row is off-protocol.
        write_point(self.write_api, self.bucket, "hvac.arm_mode",
                    tags={"scheduler_mode": scheduler_mode, "arm": arm},
                    fields={"mode_actual": f"off-protocol-{scheduler_mode}"},
                    time=now_ct)
