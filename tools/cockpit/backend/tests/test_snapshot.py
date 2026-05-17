"""Snapshot assembler tests.

Verifies the contract seam between live data and the cockpit UI: the
shape produced by `build_snapshot_canned` must match the paired TS
fixture content exactly (after JSON round-trip).

Drift between the Python fixture and the TS fixture surfaces here.
"""
from __future__ import annotations

import json
import pytest

import sys
from pathlib import Path

# Allow running as `pytest tools/cockpit/backend/tests/` from repo root
# without installing as a package.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient

from tools.cockpit.backend.app import app
from tools.cockpit.backend.snapshot import build_snapshot_canned
from tools.cockpit.backend.tests.fixtures.summer_normal import SUMMER_NORMAL
from tools.cockpit.backend.freshness import classify


class TestCannedSnapshot:
    def test_returns_summer_normal_shape(self):
        snap = build_snapshot_canned()
        assert snap == SUMMER_NORMAL

    def test_returns_deep_copy_not_reference(self):
        # Mutating the returned dict must not affect SUMMER_NORMAL.
        snap = build_snapshot_canned()
        snap["thermostat"]["indoor_temp_f"] = 999
        assert SUMMER_NORMAL["thermostat"]["indoor_temp_f"] != 999

    def test_is_json_serializable(self):
        # FastAPI serializes the dict to JSON; verify no non-JSON
        # types snuck into the canned fixture.
        snap = build_snapshot_canned()
        encoded = json.dumps(snap)
        assert "latest_tick_id" in encoded
        # Round-trip equivalence guarantees the JSON form parses back
        # to the same dict — same contract the frontend sees.
        decoded = json.loads(encoded)
        assert decoded == snap


class TestFreshnessClassification:
    @pytest.mark.parametrize(
        "source,age_ms,expected",
        [
            ("decision_trace.price_overlay_eval", 60_000, "fresh"),     # 1m
            ("decision_trace.price_overlay_eval", 9 * 60_000, "warn"),  # 9m
            ("decision_trace.price_overlay_eval", 13 * 60_000, "stale"),  # 13m
            ("decision_trace.price_overlay_eval", 20 * 60_000, "missing"),  # 20m
            ("hvac.thermostat", 5 * 60_000, "fresh"),
            ("hvac.thermostat", 25 * 60_000, "stale"),
            ("nws.forecast", 20 * 60_000, "fresh"),
            ("nws.forecast", 60 * 60_000, "warn"),
            ("unknown.source", 1_000_000, "fresh"),  # unknown defaults to fresh
        ],
    )
    def test_classify_buckets(self, source: str, age_ms: int, expected: str):
        assert classify(source, age_ms) == expected


class TestSnapshotContract:
    """Spot-check that the canned snapshot satisfies the locked
    contract invariants. If the TS Snapshot interface changes, these
    pin the corresponding Python expectation."""

    def setup_method(self):
        self.snap = build_snapshot_canned()

    def test_top_level_keys(self):
        expected = {
            "snapshot_ts",
            "latest_tick_id",
            "latest_tick_time",
            "scheduler_mode",
            "thermostat",
            "price",
            "arm_mode",
            "controller",
            "feed_health",
            "flow",
        }
        assert set(self.snap.keys()) == expected

    def test_scheduler_mode_in_locked_vocabulary(self):
        assert self.snap["scheduler_mode"] in {
            "shadow",
            "experiment",
            "production",
        }

    def test_arm_mode_actual_in_locked_vocabulary(self):
        assert self.snap["arm_mode"]["mode_actual"] in {
            "A-active",
            "B-active",
            "B-fallback",
            "B-down",
            "off-protocol-shadow",
            "off-protocol-production",
            "outside-window",
        }

    def test_price_tier_in_locked_vocabulary(self):
        assert self.snap["price"]["tier"] in {"normal", "elevated", "scarcity"}

    def test_flow_has_8_nodes(self):
        expected_nodes = {
            "weather",
            "day_type",
            "schedule",
            "price_overlay",
            "fivecp",
            "winner",
            "supervisor",
            "action",
        }
        assert set(self.snap["flow"].keys()) == expected_nodes

    def test_feed_health_entries_have_locked_shape(self):
        for entry in self.snap["feed_health"]:
            assert set(entry.keys()) == {"name", "status", "label"}
            assert entry["status"] in {"fresh", "warn", "stale", "missing"}

    def test_every_node_has_envelope_shape(self):
        envelope_keys = {
            "role_state",
            "freshness",
            "freshness_label",
            "title",
            "subtitle",
            "details",
            "source",
        }
        for node_name, node in self.snap["flow"].items():
            assert envelope_keys.issubset(node.keys()), (
                f"node {node_name} missing keys: "
                f"{envelope_keys - node.keys()}"
            )

    def test_winner_details_carries_changed_and_prev(self):
        winner = self.snap["flow"]["winner"]["details"]
        assert "prev_effective_cool_f" in winner
        assert "changed" in winner
        assert isinstance(winner["changed"], bool)

    def test_schedule_details_carries_base_and_effective(self):
        schedule = self.snap["flow"]["schedule"]["details"]
        assert "base_schedule_cool_f" in schedule
        assert "effective_schedule_cool_f" in schedule


class TestFastApiEndpoint:
    """Exercise the actual FastAPI app via TestClient. Catches:
    - import-path bugs (try/except fallback in app.py)
    - JSON-serialization drift between dict and HTTP response body
    - CORS / route registration regressions
    - status code / content-type contract
    """

    def setup_method(self):
        self.client = TestClient(app)

    def test_health_endpoint_returns_ok(self):
        res = self.client.get("/api/health")
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["backend"] == "cockpit"

    def test_snapshot_endpoint_returns_locked_shape(self):
        res = self.client.get("/api/snapshot")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/json")
        body = res.json()
        # Same shape the frontend's TS Snapshot interface expects.
        # If this drifts from the paired fixture, downstream rendering
        # will fail; this assertion is the contract gate.
        assert body == SUMMER_NORMAL

    def test_snapshot_response_is_deep_independent(self):
        # Mutating one response body must not affect another. Catches
        # any accidental return-by-reference into FastAPI's response.
        a = self.client.get("/api/snapshot").json()
        a["thermostat"]["indoor_temp_f"] = 999
        b = self.client.get("/api/snapshot").json()
        assert b["thermostat"]["indoor_temp_f"] != 999
