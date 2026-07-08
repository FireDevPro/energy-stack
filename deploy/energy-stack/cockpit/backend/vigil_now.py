"""Assemble ``GET /api/vigil/now`` — the live-state poll (client cadence ~15s).

One composite object recomputed per request from rev-4 Influx state only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import vigil_queries as q
from .vigil_config import VigilConfig
from .vigil_derive import (
    alive_from,
    build_episodes,
    build_hold,
    compose_why,
    tier_from_cents,
    this_spike_from_episodes,
    to_display_f,
)

_CT = ZoneInfo("America/Chicago")
_FRESH_STRICT_SEC = 720  # controller's fresh-strict horizon


def assemble_now(
    query_api: Any, *, bucket: str, config: VigilConfig, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)

    price = q.query_latest_price_5min(query_api, bucket=bucket)
    hourly_avg = q.query_latest_hourly_avg(query_api, bucket=bucket)
    thermo = q.query_latest_thermostat(query_api, bucket=bucket) or {}
    action = q.query_latest_action(query_api, bucket=bucket)
    arm = q.query_latest_arm_mode(query_api, bucket=bucket)
    watchdog_down = q.query_watchdog_down(query_api, bucket=bucket)
    outdoor = q.query_outdoor_now(query_api, bucket=bucket) or {}
    transitions = q.query_price_overlay_transitions(query_api, bucket=bucket, hours=24)
    price_pts = q.query_price_series_raw(query_api, bucket=bucket, hours=24)

    cents = price.get("cents") if price else None
    price_age = (now - price["time"]).total_seconds() if price and price.get("time") else None
    fresh = price_age is not None and price_age <= _FRESH_STRICT_SEC

    last_tick_age = (
        (now - arm["time"]).total_seconds() if arm and arm.get("time") else None
    )
    alive = alive_from(last_tick_age, watchdog_down)

    compressor_on = (thermo.get("hvac_state") == "Cool")
    hold_expires = action.get("hold_expires_at") if action else None
    hold_active = bool(
        action and action.get("applied") and hold_expires and hold_expires > now
    )

    if hold_active and action and action.get("tier") in ("elevated", "scarcity"):
        tier = action["tier"]
    else:
        tier = tier_from_cents(cents, config.elevated_at, config.scarcity_at)

    hold = (
        build_hold(action, now=now, compressor_on=compressor_on, hold_expires_at=hold_expires)
        if hold_active
        else None
    )

    episodes = build_episodes(transitions, now=now, peak_lookup=price_pts)
    this_spike = this_spike_from_episodes(
        episodes, now=now, tier_current=tier, hold_active=hold_active
    )
    releasing = bool(this_spike and this_spike.get("ended"))
    posture = "engaged" if (tier != "normal" or hold_active) else "resting"
    gated = bool(action.get("humidity_gated")) if action else False

    why = compose_why(
        tier=tier,
        cents=cents,
        commanded_cool_f=hold.get("commanded_cool_f") if hold else None,
        minutes_to_expiry=hold.get("minutes_to_expiry") if hold else None,
        gated=gated,
        fresh=fresh,
        alive=alive,
        releasing=releasing,
    )

    return {
        "as_of": {"utc": now.isoformat(), "ct": now.astimezone(_CT).isoformat()},
        "why": why,
        "posture": posture,
        "price": {
            "cents": cents,
            "hourly_avg_cents": hourly_avg,
            "fresh": fresh,
        },
        "tier": {
            "current": tier,
            "elevated_at": config.elevated_at,
            "scarcity_at": config.scarcity_at,
        },
        "liveness": {
            "alive": alive,
            "watchdog_down": watchdog_down,
            "last_tick_age_sec": int(last_tick_age) if last_tick_age is not None else None,
        },
        "thermostat": {
            "indoor_temp_f": to_display_f(thermo.get("indoor_temp_f")),
            "cool_setpoint_f": to_display_f(thermo.get("cool_setpoint_f")),
            "hold_mode": thermo.get("hold_mode"),
            "compressor_on": compressor_on,
            "fan_mode": thermo.get("fan_mode"),
        },
        "humidity_guard": {
            "indoor_rh_pct": thermo.get("humidity_pct"),
            "rh_max_pct": config.rh_max_pct,
            "gated": gated,
        },
        "outdoor": {
            "temp_f": outdoor.get("temp_f"),
            "rh_pct": outdoor.get("rh_pct"),
            "dewpoint_f": outdoor.get("dewpoint_f"),
        },
        "hold": hold,
        "this_spike": this_spike,
        "controller": {"mode": arm.get("scheduler_mode") if arm else None},
    }
