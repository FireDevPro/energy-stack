"""Mocked tests for the live snapshot assembler.

Exercises build_snapshot_live against synthetic Influx + Loki query
results. No live HTTP — the production query functions are I/O; the
assembler is a pure function, fully testable in isolation.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Allow running as `pytest tools/cockpit/backend/tests/` from repo root.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.cockpit.backend.snapshot import build_snapshot_live


_NOW = datetime(2026, 7, 14, 18, 0, 30, tzinfo=timezone.utc)


def _fresh_ts() -> str:
    """ISO timestamp 30 seconds before _NOW."""
    return (_NOW - timedelta(seconds=30)).isoformat()


def _thermostat() -> dict[str, Any]:
    return {
        "indoor_temp_f": 74.8,
        "indoor_humidity_pct": 51,
        "cool_setpoint_f": 76,
        "heat_setpoint_f": 65,
        "hvac_mode": "Cool",
        "fan_mode": "Auto",
        "source_ts": _fresh_ts(),
    }


def _arm_mode() -> dict[str, Any]:
    return {
        "mode_actual": "B-active",
        "arm": "B",
        "scheduler_mode": "experiment",
        "source_ts": _fresh_ts(),
    }


def _price(cents: float = 8.4) -> dict[str, Any]:
    return {"current_cents_per_kwh": cents, "source_ts": _fresh_ts()}


def _layer_resolution(
    winning_layer: str = "schedule",
    effective: int = 76,
    prev: int = 78,
    fivecp_active: bool = False,
    fivecp_scopes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "tick_id": "a1b2c3d4",
        "now_ct": _fresh_ts(),
        "schedule_cool_f": 78,
        "fivecp_active": fivecp_active,
        "fivecp_scopes_fired": fivecp_scopes or [],
        "fivecp_cool_f": 85 if fivecp_active else None,
        "effective_cool_f": effective,
        "prev_effective_cool_f": prev,
        "winning_layer": winning_layer,
        "reason_code": f"LAYER_RESOLUTION_{winning_layer.upper()}_WINS",
    }


def _price_overlay_eval(tier: str = "normal", price_cents: float = 8.4) -> dict[str, Any]:
    # `ts` is the trace's UTC emit time, set by the scheduler's `log()`
    # helper on every decision_trace.* line. Cockpit's snapshot assembler
    # uses it for the price_overlay node's freshness classification.
    return {
        "ts": _fresh_ts(),
        "tick_id": "a1b2c3d4",
        "now_ct": _fresh_ts(),
        "price_cents": price_cents,
        "price_feed_unavailable": False,
        "prev_tier": tier,
        "new_tier": tier,
        "outcome": "held",
        "reason_code": f"PRICE_OVERLAY_{tier.upper()}_BELOW_TRIGGER",
        "hold_minutes_remaining": 0,
    }


def _day_type(
    winning: str = "NORMAL", high: int = 84, apparent: int = 88, dewpoint: int = 64
) -> dict[str, Any]:
    return {
        "tick_id": "x9y8z7w6",
        "now_ct": (_NOW - timedelta(hours=21)).isoformat(),
        "winning_day_type": winning,
        "decision_for_date": "2026-07-14",
        "reason_code": f"DAY_TYPE_{winning}_HIGH_75_TO_84"
        if winning == "NORMAL"
        else f"DAY_TYPE_{winning}_HIGH_GE_85",
        "evaluation_tape": [
            {
                "code": "DAY_TYPE_HOT_HIGH_GE_85",
                "fired": False,
                "actual": high,
                "threshold": 85,
            }
        ],
        "high_f": high,
        "apparent_max_f": apparent,
        "max_dewpoint_f": dewpoint,
        "is_heat_advisory": False,
    }


def _supervisor(
    decision: str = "approved",
    proposed_cool: int = 76,
    final_cool: int = 76,
) -> dict[str, Any]:
    return {
        "tick_id": "a1b2c3d4",
        "now_ct": _fresh_ts(),
        "decision": decision,
        "proposed_cool_f": proposed_cool,
        "proposed_heat_f": 65,
        "final_cool_f": final_cool,
        "final_heat_f": 65,
        "supervisor_reason": None,
        "reason_code": "SUPERVISOR_APPROVED"
        if decision == "approved"
        else f"SUPERVISOR_CLAMPED_COOL_CEILING",
    }


def _feed_health_fresh() -> list[dict[str, Any]]:
    return [
        {"name": "ComEd", "status": "fresh", "label": "30s ago"},
        {"name": "NWS", "status": "fresh", "label": "1m ago"},
        {"name": "PJM forecast", "status": "fresh", "label": "0m ago"},
        {"name": "PJM RT LMP", "status": "fresh", "label": "1h ago"},
        {"name": "Refoss", "status": "fresh", "label": "15s ago"},
        {"name": "EAGLE", "status": "fresh", "label": "15s ago"},
        {"name": "Thermostat", "status": "fresh", "label": "6m ago"},
    ]


class TestBuildSnapshotLive:
    def test_happy_path_schedule_winning(self):
        snap = build_snapshot_live(
            thermostat=_thermostat(),
            arm_mode=_arm_mode(),
            price=_price(),
            heartbeat=None,
            feed_health=_feed_health_fresh(),
            traces={
                "tick_id": "a1b2c3d4",
                "layer_resolution": _layer_resolution(),
                "price_overlay_eval": _price_overlay_eval(),
                "supervisor": _supervisor(),
                "day_type_decision": _day_type(),
                "precool_decision": None,
            },
            scheduler_mode="experiment",
            now=_NOW,
        )
        assert snap["scheduler_mode"] == "experiment"
        assert snap["latest_tick_id"] == "a1b2c3d4"
        assert snap["arm_mode"]["mode_actual"] == "B-active"
        assert snap["arm_mode"]["arm"] == "B"
        assert snap["thermostat"]["indoor_temp_f"] == 74.8
        assert snap["thermostat"]["cool_setpoint_f"] == 76
        assert snap["thermostat"]["hvac_mode"] == "cool"
        assert snap["thermostat"]["fan_mode"] == "auto"
        assert snap["price"]["current_cents_per_kwh"] == 8.4
        assert snap["price"]["tier"] == "normal"
        assert snap["controller"]["alive"] is True

        flow = snap["flow"]
        assert flow["schedule"]["role_state"] == "winning"
        assert flow["price_overlay"]["role_state"] == "dimmed"
        assert flow["fivecp"]["role_state"] == "dimmed"
        assert flow["winner"]["details"]["winning_layer"] == "schedule"
        assert flow["winner"]["details"]["changed"] is True
        assert flow["supervisor"]["role_state"] == "winning"
        assert flow["supervisor"]["details"]["decision"] == "approved"
        assert flow["day_type"]["details"]["winning_day_type"] == "NORMAL"

    def test_price_overlay_wins_with_scarcity_tier(self):
        snap = build_snapshot_live(
            thermostat=_thermostat(),
            arm_mode=_arm_mode(),
            price=_price(53.4),
            heartbeat=None,
            feed_health=_feed_health_fresh(),
            traces={
                "tick_id": "b2c3d4e5",
                "layer_resolution": _layer_resolution(
                    winning_layer="price_overlay", effective=85, prev=76
                ),
                "price_overlay_eval": _price_overlay_eval(
                    tier="scarcity", price_cents=53.4
                ),
                "supervisor": _supervisor(proposed_cool=85, final_cool=85),
                "day_type_decision": _day_type(),
                "precool_decision": None,
            },
            scheduler_mode="experiment",
            now=_NOW,
        )
        assert snap["price"]["tier"] == "scarcity"
        flow = snap["flow"]
        assert flow["price_overlay"]["role_state"] == "winning"
        assert flow["winner"]["details"]["winning_layer"] == "price_overlay"
        assert flow["winner"]["details"]["effective_cool_f"] == 85

    def test_fivecp_wins(self):
        snap = build_snapshot_live(
            thermostat=_thermostat(),
            arm_mode=_arm_mode(),
            price=_price(),
            heartbeat=None,
            feed_health=_feed_health_fresh(),
            traces={
                "tick_id": "c3d4e5f6",
                "layer_resolution": _layer_resolution(
                    winning_layer="fivecp",
                    effective=85,
                    prev=76,
                    fivecp_active=True,
                    fivecp_scopes=["COMED"],
                ),
                "price_overlay_eval": _price_overlay_eval(),
                "supervisor": _supervisor(proposed_cool=85, final_cool=85),
                "day_type_decision": _day_type(winning="HOT_5CP_RISK", high=92),
                "precool_decision": None,
            },
            scheduler_mode="experiment",
            now=_NOW,
        )
        flow = snap["flow"]
        assert flow["fivecp"]["details"]["fivecp_active"] is True
        assert flow["fivecp"]["details"]["fivecp_scopes_fired"] == ["COMED"]
        assert flow["winner"]["details"]["winning_layer"] == "fivecp"

    def test_supervisor_clamped_changes_role_to_clamped(self):
        snap = build_snapshot_live(
            thermostat=_thermostat(),
            arm_mode=_arm_mode(),
            price=_price(),
            heartbeat=None,
            feed_health=_feed_health_fresh(),
            traces={
                "tick_id": "d4e5f6a7",
                "layer_resolution": _layer_resolution(effective=92),
                "price_overlay_eval": _price_overlay_eval(),
                "supervisor": _supervisor(
                    decision="clamped", proposed_cool=92, final_cool=86
                ),
                "day_type_decision": _day_type(),
                "precool_decision": None,
            },
            scheduler_mode="experiment",
            now=_NOW,
        )
        flow = snap["flow"]
        assert flow["supervisor"]["role_state"] == "clamped"
        assert flow["supervisor"]["details"]["proposed_cool_f"] == 92
        assert flow["supervisor"]["details"]["final_cool_f"] == 86

    def test_heartbeat_false_renders_controller_down(self):
        snap = build_snapshot_live(
            thermostat=_thermostat(),
            arm_mode=_arm_mode(),
            price=_price(),
            heartbeat={"controller_alive": False, "source_ts": _fresh_ts()},
            feed_health=_feed_health_fresh(),
            traces={
                "tick_id": None,
                "layer_resolution": None,
                "price_overlay_eval": None,
                "supervisor": None,
                "day_type_decision": _day_type(),
                "precool_decision": None,
            },
            scheduler_mode="experiment",
            now=_NOW,
        )
        assert snap["controller"]["alive"] is False
        # Supervisor not invoked → not_applicable
        assert snap["flow"]["supervisor"]["role_state"] == "not_applicable"

    def test_locked_vocabulary_satisfied(self):
        snap = build_snapshot_live(
            thermostat=_thermostat(),
            arm_mode=_arm_mode(),
            price=_price(),
            heartbeat=None,
            feed_health=_feed_health_fresh(),
            traces={
                "tick_id": "a1b2c3d4",
                "layer_resolution": _layer_resolution(),
                "price_overlay_eval": _price_overlay_eval(),
                "supervisor": _supervisor(),
                "day_type_decision": _day_type(),
                "precool_decision": None,
            },
            scheduler_mode="experiment",
            now=_NOW,
        )
        assert snap["scheduler_mode"] in {"shadow", "experiment", "production"}
        assert snap["arm_mode"]["mode_actual"] in {
            "A-active",
            "B-active",
            "B-fallback",
            "B-down",
            "off-protocol-shadow",
            "off-protocol-production",
            "outside-window",
        }
        assert snap["price"]["tier"] in {"normal", "elevated", "scarcity"}
        assert snap["flow"]["winner"]["details"]["winning_layer"] in {
            "schedule",
            "price_overlay",
            "fivecp",
            "tie",
        }
        if snap["flow"]["supervisor"]["details"]["decision"] is not None:
            assert snap["flow"]["supervisor"]["details"]["decision"] in {
                "approved",
                "clamped",
                "emergency",
            }
