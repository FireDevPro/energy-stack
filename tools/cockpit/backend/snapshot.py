"""Snapshot assembler — combines Influx + Loki query results into the
Snapshot dict that the frontend consumes.

This module is the contract seam between live data and the cockpit UI.
The shape it produces MUST match the TS `Snapshot` interface defined
at tools/cockpit/frontend/src/types.ts. Drift surfaces in
test_snapshot.py via the paired fixture comparison.

Two assembly modes:
- `build_snapshot_canned()` — returns the paired Python copy of the
  summer_normal TS fixture. Used for the contract gate in tests and
  for the `/api/snapshot` happy-path when `COCKPIT_BACKEND_MODE=canned`.
- `build_snapshot_live(...)` — assembles a Snapshot dict from supplied
  Influx + Loki query results + the current wall-clock time. Pure
  function: I/O is the caller's job (app.py wires it to real query
  functions). Tests pass synthetic inputs and assert the assembled
  output.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.cockpit.backend.tests.fixtures.summer_normal import SUMMER_NORMAL
from tools.cockpit.backend.freshness import classify


def build_snapshot_canned() -> dict[str, Any]:
    """Return the canned summer_normal snapshot (deep-copied)."""
    return _deep_copy(SUMMER_NORMAL)


# ---- Live assembly --------------------------------------------------


def build_snapshot_live(
    *,
    thermostat: dict[str, Any] | None,
    arm_mode: dict[str, Any] | None,
    price: dict[str, Any] | None,
    heartbeat: dict[str, Any] | None,
    feed_health: list[dict[str, Any]],
    traces: dict[str, Any],
    scheduler_mode: str,
    now: datetime,
) -> dict[str, Any]:
    """Assemble the Snapshot dict from live query results.

    Pure function. Inputs are the dicts returned by `influx.query_*` and
    `loki.fetch_latest_tick_traces`. Output matches the TS `Snapshot`
    interface.

    Caller (app.py) is responsible for the I/O. Tests construct synthetic
    inputs and assert the assembled output.
    """
    layer = traces.get("layer_resolution") or {}
    po_eval = traces.get("price_overlay_eval") or {}
    sup = traces.get("supervisor")
    day_type = traces.get("day_type_decision") or {}
    tick_id = traces.get("tick_id") or _short_id(now)

    layer_ts = _parse_ts(layer.get("now_ct")) or now
    indoor_temp = (thermostat or {}).get("indoor_temp_f")
    indoor_temp_available = isinstance(indoor_temp, (int, float))

    tier = _classify_tier((price or {}).get("current_cents_per_kwh"))
    arm = (arm_mode or {}).get("arm")
    mode_actual = (arm_mode or {}).get("mode_actual") or "outside-window"
    controller_alive = (
        heartbeat is None or not heartbeat.get("controller_alive")
    )
    # heartbeat row is written ONLY when down — presence with True is
    # impossible; presence with False means watchdog detected silence.
    if heartbeat is not None:
        controller_alive = bool(heartbeat.get("controller_alive"))

    return {
        "snapshot_ts": _iso(now),
        "latest_tick_id": tick_id,
        "latest_tick_time": _iso(layer_ts),
        "scheduler_mode": scheduler_mode,
        "thermostat": _build_thermostat(thermostat, now),
        "price": _build_price(price, tier, now),
        "arm_mode": _build_arm_mode(arm_mode, mode_actual, arm, now),
        "controller": {
            "alive": controller_alive,
            "last_heartbeat_ts": (heartbeat or {}).get("source_ts"),
            "freshness": "fresh",
        },
        "feed_health": feed_health,
        "flow": {
            "weather": _build_weather_node(day_type, now),
            "day_type": _build_day_type_node(day_type, now),
            "schedule": _build_schedule_node(layer, now),
            "price_overlay": _build_price_overlay_node(po_eval, layer, now),
            "fivecp": _build_fivecp_node(layer, now),
            "winner": _build_winner_node(layer, now),
            "supervisor": _build_supervisor_node(sup, indoor_temp_available, now),
            "action": _build_action_node(layer, sup, now),
        },
    }


# ---- Node builders --------------------------------------------------


def _build_thermostat(t: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    if t is None:
        return _missing_thermostat()
    ts = _parse_ts(t.get("source_ts"))
    age = _age_ms(ts, now)
    return {
        "indoor_temp_f": _f(t.get("indoor_temp_f")),
        "indoor_humidity_pct": _i(t.get("indoor_humidity_pct")),
        "cool_setpoint_f": _i(t.get("cool_setpoint_f")),
        "heat_setpoint_f": _i(t.get("heat_setpoint_f")),
        "hvac_mode": (t.get("hvac_mode") or "cool").lower(),
        "fan_mode": (t.get("fan_mode") or "auto").lower(),
        "source_ts": t.get("source_ts"),
        "freshness": classify("hvac.thermostat", age),
        "freshness_label": _label_age(age),
    }


def _missing_thermostat() -> dict[str, Any]:
    return {
        "indoor_temp_f": 0.0,
        "indoor_humidity_pct": 0,
        "cool_setpoint_f": 0,
        "heat_setpoint_f": 0,
        "hvac_mode": "cool",
        "fan_mode": "auto",
        "source_ts": None,
        "freshness": "missing",
        "freshness_label": "no reading",
    }


def _build_price(p: dict[str, Any] | None, tier: str, now: datetime) -> dict[str, Any]:
    if p is None:
        return {
            "current_cents_per_kwh": 0.0,
            "tier": "normal",
            "source_ts": None,
            "freshness": "missing",
            "freshness_label": "no price",
        }
    ts = _parse_ts(p.get("source_ts"))
    age = _age_ms(ts, now)
    return {
        "current_cents_per_kwh": _f(p.get("current_cents_per_kwh")),
        "tier": tier,
        "source_ts": p.get("source_ts"),
        "freshness": classify("comed.prices", age),
        "freshness_label": _label_age(age),
    }


def _build_arm_mode(
    a: dict[str, Any] | None, mode_actual: str, arm: str | None, now: datetime
) -> dict[str, Any]:
    if a is None:
        return {
            "mode_actual": mode_actual,
            "arm": None,
            "source_ts": None,
            "freshness": "missing",
            "freshness_label": "no arm_mode row",
        }
    ts = _parse_ts(a.get("source_ts"))
    age = _age_ms(ts, now)
    return {
        "mode_actual": mode_actual,
        "arm": arm if arm in ("A", "B") else None,
        "source_ts": a.get("source_ts"),
        "freshness": classify("hvac.arm_mode", age),
        "freshness_label": _label_age(age),
    }


def _build_weather_node(day_type: dict[str, Any], now: datetime) -> dict[str, Any]:
    # Weather inputs surface inside the day_type trace; for live data
    # without a separate NWS query we fall back to "context" with
    # whatever the day_type tape recorded.
    high = day_type.get("high_f")
    apparent = day_type.get("apparent_max_f")
    dewpoint = day_type.get("max_dewpoint_f")
    return {
        "role_state": "context",
        "freshness": "fresh",
        "freshness_label": "from day-type decision",
        "title": "Weather",
        "subtitle": _weather_subtitle(high, dewpoint),
        "details": {
            "current_outdoor_f": _f(high) if high is not None else 0.0,
            "today_high_f": _i(high) if high is not None else 0,
            "apparent_max_f": _i(apparent) if apparent is not None else 0,
            "dewpoint_max_f": _i(dewpoint) if dewpoint is not None else 0,
            "heat_advisory": bool(day_type.get("is_heat_advisory")),
        },
        "source": {
            "event": "nws.forecast",
            "tick_id": None,
            "ts": day_type.get("now_ct") or _iso(now),
        },
    }


def _build_day_type_node(day_type: dict[str, Any], now: datetime) -> dict[str, Any]:
    winning = day_type.get("winning_day_type") or "NORMAL"
    tape = day_type.get("evaluation_tape") or []
    return {
        "role_state": "context",
        "freshness": "fresh",
        "freshness_label": "decided 21:00",
        "title": "Day Type",
        "subtitle": str(winning),
        "details": {
            "winning_day_type": str(winning),
            "decision_for_date": day_type.get("decision_for_date") or "",
            "reason_code": day_type.get("reason_code") or "",
            "evaluation_tape": tape,
        },
        "source": {
            "event": "decision_trace.day_type_decision",
            "tick_id": day_type.get("tick_id"),
            "ts": day_type.get("now_ct") or _iso(now),
        },
    }


def _build_schedule_node(layer: dict[str, Any], now: datetime) -> dict[str, Any]:
    base = _i(layer.get("schedule_cool_f")) or 78
    winning_layer = layer.get("winning_layer") or "schedule"
    role = "winning" if winning_layer == "schedule" else "dimmed"
    return {
        "role_state": role,
        "freshness": "fresh",
        "freshness_label": "this tick",
        "title": "Schedule",
        "subtitle": f"baseline: {base}°F",
        "details": {
            "action_label": "live",
            "base_schedule_cool_f": base,
            "effective_schedule_cool_f": base,
            "humid_override_active": False,
            "humid_override_setpoint_f": None,
            "precool_window": None,
        },
        "source": {
            "event": "decision_trace.layer_resolution",
            "tick_id": layer.get("tick_id"),
            "ts": layer.get("now_ct") or _iso(now),
        },
    }


def _build_price_overlay_node(
    po: dict[str, Any], layer: dict[str, Any], now: datetime
) -> dict[str, Any]:
    # role_state is driven by the layer-resolution arbitration outcome,
    # not by the overlay's own state. The overlay can be "elevated" tier
    # without being the winning layer (e.g., when 5CP also fired warmer).
    winning = layer.get("winning_layer") == "price_overlay"
    new_tier = po.get("new_tier") or "normal"
    return {
        "role_state": "winning" if winning else "dimmed",
        "freshness": "stale" if po.get("price_is_stale") else "fresh",
        "freshness_label": "this tick",
        "title": "Price Overlay",
        "subtitle": str(new_tier),
        "details": {
            "price_cents": _f(po.get("price_cents")),
            "prev_tier": po.get("prev_tier") or "normal",
            "new_tier": new_tier,
            "outcome": po.get("outcome") or "held",
            "reason_code": po.get("reason_code") or "",
            "hold_minutes_remaining": po.get("hold_minutes_remaining"),
        },
        "source": {
            "event": "decision_trace.price_overlay_eval",
            "tick_id": po.get("tick_id"),
            "ts": po.get("now_ct") or _iso(now),
        },
    }


def _build_fivecp_node(layer: dict[str, Any], now: datetime) -> dict[str, Any]:
    active = bool(layer.get("fivecp_active"))
    return {
        "role_state": "winning" if layer.get("winning_layer") == "fivecp" else (
            "dimmed" if active else "dimmed"
        ),
        "freshness": "fresh",
        "freshness_label": "this tick",
        "title": "5CP Risk",
        "subtitle": "active" if active else "no risk",
        "details": {
            "fivecp_active": active,
            "fivecp_scopes_fired": list(layer.get("fivecp_scopes_fired") or []),
            "fivecp_cool_f": _i(layer.get("fivecp_cool_f")) if active else None,
            "in_season": True,
        },
        "source": {
            "event": "decision_trace.layer_resolution",
            "tick_id": layer.get("tick_id"),
            "ts": layer.get("now_ct") or _iso(now),
        },
    }


def _build_winner_node(layer: dict[str, Any], now: datetime) -> dict[str, Any]:
    effective = _i(layer.get("effective_cool_f")) or 0
    prev = _i(layer.get("prev_effective_cool_f")) or effective
    winning_layer = layer.get("winning_layer") or "schedule"
    if winning_layer not in {"schedule", "price_overlay", "fivecp", "tie"}:
        winning_layer = "schedule"
    return {
        "role_state": "winning",
        "freshness": "fresh",
        "freshness_label": "this tick",
        "title": "Winner",
        "subtitle": winning_layer.replace("_", " ").title(),
        "details": {
            "winning_layer": winning_layer,
            "effective_cool_f": effective,
            "prev_effective_cool_f": prev,
            "changed": effective != prev,
            "reason_code": layer.get("reason_code") or "",
        },
        "source": {
            "event": "decision_trace.layer_resolution",
            "tick_id": layer.get("tick_id"),
            "ts": layer.get("now_ct") or _iso(now),
        },
    }


def _build_supervisor_node(
    sup: dict[str, Any] | None, indoor_temp_available: bool, now: datetime
) -> dict[str, Any]:
    if sup is None:
        return {
            "role_state": "not_applicable",
            "freshness": "fresh",
            "freshness_label": "not invoked this tick",
            "title": "Supervisor",
            "subtitle": "not invoked",
            "details": {
                "decision": None,
                "proposed_cool_f": None,
                "proposed_heat_f": None,
                "final_cool_f": None,
                "final_heat_f": None,
                "supervisor_reason": None,
                "reason_code": None,
                "indoor_temp_available": None,
            },
            "source": None,
        }
    decision = sup.get("decision") or "approved"
    role = (
        "emergency" if decision == "emergency"
        else "clamped" if decision == "clamped"
        else "winning"
    )
    return {
        "role_state": role,
        "freshness": "fresh",
        "freshness_label": "this tick",
        "title": "Supervisor",
        "subtitle": str(decision),
        "details": {
            "decision": decision,
            "proposed_cool_f": _i(sup.get("proposed_cool_f")),
            "proposed_heat_f": _i(sup.get("proposed_heat_f")),
            "final_cool_f": _i(sup.get("final_cool_f")),
            "final_heat_f": _i(sup.get("final_heat_f")),
            "supervisor_reason": sup.get("supervisor_reason"),
            "reason_code": sup.get("reason_code"),
            "indoor_temp_available": indoor_temp_available,
        },
        "source": {
            "event": "decision_trace.supervisor",
            "tick_id": sup.get("tick_id"),
            "ts": sup.get("now_ct") or _iso(now),
        },
    }


def _build_action_node(
    layer: dict[str, Any], sup: dict[str, Any] | None, now: datetime
) -> dict[str, Any]:
    # Layer resolution proposed an effective; supervisor (if invoked)
    # finalized it. Action node represents the "what would be written"
    # surface for the live snapshot — the actual hvac.actions row only
    # appears at scheduled action times.
    cool = (
        _i((sup or {}).get("final_cool_f"))
        if sup
        else _i(layer.get("effective_cool_f"))
    )
    return {
        "role_state": "context",
        "freshness": "fresh",
        "freshness_label": "this tick",
        "title": "Action",
        "subtitle": "live tick",
        "details": {
            "applied": False,
            "dry_run": True,
            "action_label": "live_tick",
            "cool_setpoint_f": cool,
            "heat_setpoint_f": _i((sup or {}).get("final_heat_f")),
            "fan_mode": "auto",
            "setpoint_reason": "live snapshot — last hvac.actions row not queried",
            "fire_ts": None,
            "error": None,
        },
        "source": None,
    }


# ---- Helpers --------------------------------------------------------


def _classify_tier(cents: Any) -> str:
    """Mirror of deploy/energy-stack/hvac-scheduler/price_overlay.py
    tier policy: normal < 18¢, elevated < 20¢, scarcity >= 20¢."""
    if cents is None:
        return "normal"
    try:
        c = float(cents)
    except (TypeError, ValueError):
        return "normal"
    if c >= 20.0:
        return "scarcity"
    if c >= 14.0:
        return "elevated"
    return "normal"


def _weather_subtitle(high: Any, dewpoint: Any) -> str:
    parts = []
    if high is not None:
        parts.append(f"high {int(high)}°F")
    if dewpoint is not None:
        parts.append(f"dewpoint {int(dewpoint)}°F")
    return ", ".join(parts) if parts else "no forecast"


def _short_id(now: datetime) -> str:
    return f"live-{int(now.timestamp())}"


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_ts(s: Any) -> datetime | None:
    if isinstance(s, datetime):
        return s
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_ms(ts: datetime | None, now: datetime) -> int:
    if ts is None:
        return 10**12  # treat as missing
    delta = now - ts
    return max(int(delta.total_seconds() * 1000), 0)


def _label_age(ms: int) -> str:
    s = ms // 1000
    if s < 60:
        return f"{s}s ago"
    m = s // 60
    if m < 60:
        return f"{m}m ago"
    h = m // 60
    return f"{h}h ago"


def _f(x: Any) -> float:
    try:
        return float(x) if x is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _i(x: Any) -> int:
    try:
        return int(x) if x is not None else 0
    except (TypeError, ValueError):
        return 0


def _deep_copy(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy(v) for v in obj]
    return obj
