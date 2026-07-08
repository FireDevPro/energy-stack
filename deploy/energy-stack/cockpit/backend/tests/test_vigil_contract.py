"""Contract tests: the assembled endpoint dicts match what vigil.html reads.

A fake QueryApi returns scenario rows per Flux query; we assert shape + the
resting/engaged/release logic, then drive the real FastAPI route to confirm
the payload is JSON-serializable (catches stray datetimes)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from .. import app as app_module
from ..vigil_config import VigilConfig
from ..vigil_events import assemble_events
from ..vigil_now import assemble_now
from ..vigil_timeline import assemble_timeline

UTC = timezone.utc
NOW = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
CFG = VigilConfig(10.0, 20.0, 2.0, 61.0, 58.0, 30)


class _Rec:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get_value(self) -> Any:
        return self.values.get("_value")

    def get_time(self) -> Any:
        return self.values.get("_time")


class _Tbl:
    def __init__(self, recs: list[_Rec]) -> None:
        self.records = recs


class FakeQuery:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows

    def query(self, flux: str, org: str | None = None) -> list[_Tbl]:
        return [_Tbl([_Rec(r) for r in self._match(flux)])]

    def _match(self, flux: str) -> list[dict[str, Any]]:
        if "hvac.thermostat" in flux:
            return self.rows.get("thermostat", [])
        if "hvac.actions" in flux:
            return self.rows.get("action", [])
        if "hvac.arm_mode" in flux:
            return self.rows.get("arm", [])
        if "hvac.heartbeat" in flux:
            return self.rows.get("heartbeat", [])
        if "ecowitt.weather" in flux:
            return self.rows.get("outdoor", [])
        if "hvac.price_overlay" in flux:
            return self.rows.get("transitions", [])
        if "hourly_avg" in flux:
            return self.rows.get("hourly_avg", [])
        if "aggregateWindow" in flux:
            return self.rows.get("price_series", [])
        if 'period_type == "5min"' in flux and "last()" in flux:
            return self.rows.get("price_5min", [])
        if 'period_type == "5min"' in flux:
            return self.rows.get("price_raw", [])
        return []


def _resting() -> FakeQuery:
    return FakeQuery({
        "price_5min": [{"_value": 3.4, "_time": NOW - timedelta(minutes=5)}],
        "hourly_avg": [{"_value": 6.1, "_time": NOW - timedelta(minutes=30)}],
        "thermostat": [{"_time": NOW - timedelta(minutes=3), "indoor_temp_f_hires": 74.2,
                        "cool_setpoint_f": 73.4, "hold_mode": "Off", "hvac_state": "Off",
                        "fan_mode": "Follow Schedule", "humidity_pct": 44.0}],
        "action": [],
        "arm": [{"_time": NOW - timedelta(seconds=14), "scheduler_mode": "production"}],
        "heartbeat": [],
        "outdoor": [{"_time": NOW, "ch1_temp_f": 88.5, "ch1_rh_pct": 39.0, "ch1_dewpoint_f": 60.1}],
        "transitions": [],
        "price_raw": [], "price_series": [],
    })


def _engaged() -> FakeQuery:
    return FakeQuery({
        "price_5min": [{"_value": 24.8, "_time": NOW - timedelta(minutes=2)}],
        "hourly_avg": [{"_value": 18.2, "_time": NOW - timedelta(minutes=30)}],
        "thermostat": [{"_time": NOW - timedelta(minutes=2), "indoor_temp_f_hires": 77.1,
                        "cool_setpoint_f": 85.1, "hold_mode": "Hold Until", "hvac_state": "Off",
                        "fan_mode": "Follow Schedule", "humidity_pct": 47.0}],
        "action": [{"_time": NOW - timedelta(minutes=22), "tier": "scarcity",
                    "commanded_cool": 29.5, "schedule_cool": 25.5,
                    "hold_expires_at": (NOW + timedelta(minutes=8)).isoformat(),
                    "humidity_gated": 0, "applied": 1}],
        "arm": [{"_time": NOW - timedelta(seconds=12), "scheduler_mode": "production"}],
        "heartbeat": [],
        "outdoor": [{"_time": NOW, "ch1_temp_f": 96.2, "ch1_rh_pct": 34.0, "ch1_dewpoint_f": 63.0}],
        "transitions": [
            {"_time": NOW - timedelta(minutes=40), "new_tier": "elevated", "_value": 12.0},
            {"_time": NOW - timedelta(minutes=29), "new_tier": "scarcity", "_value": 24.8},
        ],
        "price_raw": [{"_time": NOW - timedelta(minutes=20), "_value": 26.1}],
        "price_series": [{"_time": NOW - timedelta(minutes=30), "_value": 24.8}],
    })


_NOW_KEYS = {"as_of", "why", "posture", "price", "tier", "liveness", "thermostat",
             "humidity_guard", "outdoor", "hold", "this_spike", "controller"}


def test_resting_shape() -> None:
    d = assemble_now(_resting(), bucket="b", config=CFG, now=NOW)
    assert _NOW_KEYS <= set(d)
    assert d["posture"] == "resting"
    assert d["hold"] is None and d["this_spike"] is None
    assert d["tier"]["current"] == "normal"
    assert d["why"] == "Cheap power, 3.4¢ — thermostat's running its own schedule."
    # resting tiles fully populated (no dead nulls)
    assert d["thermostat"]["indoor_temp_f"] == 74.2
    assert d["thermostat"]["cool_setpoint_f"] == 73.4
    assert d["thermostat"]["compressor_on"] is False
    assert d["outdoor"]["temp_f"] == 88.5
    assert d["humidity_guard"]["indoor_rh_pct"] == 44.0
    assert d["humidity_guard"]["rh_max_pct"] == 61.0
    assert d["liveness"]["alive"] is True
    assert d["price"]["fresh"] is True


def test_engaged_shape() -> None:
    d = assemble_now(_engaged(), bucket="b", config=CFG, now=NOW)
    assert d["posture"] == "engaged"
    assert d["tier"]["current"] == "scarcity"
    assert d["hold"] is not None
    assert d["hold"]["commanded_cool_f"] == 85.1
    assert d["hold"]["schedule_cool_f"] == 77.9
    assert d["hold"]["minutes_to_expiry"] == 8
    assert d["hold"]["coasting"] is True
    assert d["this_spike"] is not None and d["this_spike"]["ended"] is False
    assert d["this_spike"]["tiers_walked"][0]["tier"] == "elevated"
    assert d["why"].startswith("SCARCITY, 24.8¢ — holding 85°")


def test_timeline_shape() -> None:
    d = assemble_timeline(_engaged(), bucket="b", config=CFG, hours=24, now=NOW)
    assert d["thresholds"] == {"elevated_at": 10.0, "scarcity_at": 20.0}
    assert isinstance(d["price_series"], list)
    assert d["holds"] and d["holds"][0]["tier"] == "scarcity"


def test_events_shape() -> None:
    d = assemble_events(_engaged(), bucket="b", config=CFG, limit=10, now=NOW)
    assert d["events"]
    ev = d["events"][0]
    assert ev["tiers_walked"] == ["elevated", "scarcity"]  # strings, not objects
    assert ev["resolution"] == "ongoing"
    assert "started_at_ct" in ev


def test_route_is_json_serializable(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_get_query_api", lambda: _engaged())
    monkeypatch.setattr(app_module, "_get_config", lambda: CFG)
    monkeypatch.setenv("INFLUXDB_BUCKET", "b")
    client = TestClient(app_module.app)
    for path in ("/api/vigil/now", "/api/vigil/timeline", "/api/vigil/events"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
        assert isinstance(r.json(), dict)
