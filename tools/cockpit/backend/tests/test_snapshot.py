"""Snapshot assembler tests.

Verifies the contract seam between live data and the cockpit UI: the
shape produced by `build_snapshot_canned` must match the paired TS
fixture content exactly (after JSON round-trip).

Drift between the Python fixture and the TS fixture surfaces here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Allow running as `pytest tools/cockpit/backend/tests/` from repo root
# without installing as a package.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tools.cockpit.backend.app import app
from tools.cockpit.backend.snapshot import (
    _build_price_overlay_node,
    build_snapshot_canned,
)
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
    def test_classify_buckets(self, source: str, age_ms: int, expected: str) -> None:
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


class TestPriceOverlayFreshness:
    """Pin the price_overlay node's freshness to the shared classify()
    module against the trace's own emit timestamp.

    The price-overlay node's freshness MUST track the underlying ComEd
    price BUCKET age, NOT the trace's emit time. The scheduler logs
    decision_trace.price_overlay_eval every evaluation tick (~30s
    cadence), so po["ts"] is always recent. The bucket the scheduler is
    evaluating against may be many minutes old.

    Pre-fix history (PR 4 first attempt): snapshot.py read
    po.get('price_is_stale') — a field the scheduler stopped emitting in
    the freshness PR (renamed to price_feed_unavailable). The .get()
    always returned None → freshness was always 'fresh', silently
    breaking operator visibility.

    First-pass fix (rejected): used classify('decision_trace.price_overlay_eval', age_ms)
    against po['ts']. This also broke operator visibility: a fresh trace
    emitted against stale price data still showed 'fresh'.

    Final fix: classify('comed.prices', int(bucket_age_sec * 1000)).
    comed.prices thresholds (per freshness.py): fresh ≤ 7m / warn ≤ 16m
    / stale ≤ 30m / missing > 30m. Missing bucket_age_sec or
    price_feed_unavailable=True → 'missing'.
    """

    _NOW = datetime(2026, 7, 14, 18, 0, 30, tzinfo=timezone.utc)

    def _po(
        self,
        *,
        bucket_age_sec: float | None,
        price_feed_unavailable: bool = False,
        trace_age_sec: float = 30,
    ) -> dict[str, Any]:
        """Build a price_overlay_eval trace payload.

        trace_age_sec defaults to 30s ago — a recently-emitted trace.
        These tests deliberately use fresh trace ages to prove that
        trace freshness does NOT drive the node's freshness field.
        """
        po: dict[str, Any] = {
            "tick_id": "a1b2c3d4",
            "ts": (self._NOW - timedelta(seconds=trace_age_sec)).isoformat(),
            "price_cents": None if price_feed_unavailable else 8.4,
            "price_feed_unavailable": price_feed_unavailable,
            "prev_tier": "normal",
            "new_tier": "normal",
            "outcome": "held",
            "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
            "hold_minutes_remaining": 0,
        }
        if bucket_age_sec is not None:
            po["bucket_age_sec"] = bucket_age_sec
        return po

    def _layer(self) -> dict[str, Any]:
        return {"winning_layer": "schedule"}

    def test_freshness_fresh_when_bucket_under_7_min(self) -> None:
        # 1 minute < 7 min fresh threshold (comed.prices) → "fresh"
        node = _build_price_overlay_node(
            self._po(bucket_age_sec=60), self._layer(), self._NOW
        )
        assert node["freshness"] == "fresh"

    def test_freshness_warn_when_bucket_in_warn_band(self) -> None:
        # 10 min > 7 min fresh, < 16 min warn → "warn"
        node = _build_price_overlay_node(
            self._po(bucket_age_sec=10 * 60), self._layer(), self._NOW
        )
        assert node["freshness"] == "warn"

    def test_freshness_stale_when_bucket_in_stale_band(self) -> None:
        # 20 min > 16 min warn, < 30 min stale → "stale"
        node = _build_price_overlay_node(
            self._po(bucket_age_sec=20 * 60), self._layer(), self._NOW
        )
        assert node["freshness"] == "stale"

    def test_freshness_missing_when_no_bucket_age(self) -> None:
        # No bucket_age_sec on the trace payload → "missing"
        node = _build_price_overlay_node(
            self._po(bucket_age_sec=None), self._layer(), self._NOW
        )
        assert node["freshness"] == "missing"

    def test_freshness_missing_when_price_feed_unavailable(self) -> None:
        # price_feed_unavailable=True → "missing" regardless of any
        # bucket_age_sec value (current_price_cents is None on the
        # scheduler side; no real bucket to age).
        node = _build_price_overlay_node(
            self._po(bucket_age_sec=None, price_feed_unavailable=True),
            self._layer(),
            self._NOW,
        )
        assert node["freshness"] == "missing"

    def test_freshness_tracks_bucket_not_trace_ts(self) -> None:
        """REGRESSION: fresh trace ts must NOT mask stale bucket data.

        The scheduler emits decision_trace.price_overlay_eval on every
        evaluation tick. po['ts'] is therefore always recent. But the
        underlying ComEd price bucket may be 10+ minutes old (the
        scheduler still emits the trace at the regular cadence). The
        cockpit's freshness display MUST reflect the bucket age, not
        the trace emit time."""
        # trace emitted 30s ago (fresh) + bucket 12 min old (warn band)
        po = self._po(bucket_age_sec=12 * 60, trace_age_sec=30)
        node = _build_price_overlay_node(po, self._layer(), self._NOW)
        # Must be "warn" per comed.prices thresholds — not "fresh"
        # from trace ts (which would be wrong).
        assert node["freshness"] == "warn"

    def test_freshness_label_reflects_bucket_age_not_this_tick(self) -> None:
        """REGRESSION: freshness_label must reflect the bucket's age
        ('Nm ago'), not literal 'this tick' which would be misleading
        for stale price data."""
        # bucket 12 min old + fresh trace ts
        po = self._po(bucket_age_sec=12 * 60, trace_age_sec=30)
        node = _build_price_overlay_node(po, self._layer(), self._NOW)
        assert node["freshness_label"] == "12m ago"
        assert node["freshness_label"] != "this tick"

    def test_freshness_label_no_price_data_when_missing(self) -> None:
        node = _build_price_overlay_node(
            self._po(bucket_age_sec=None), self._layer(), self._NOW
        )
        assert node["freshness_label"] == "no price data"

    def test_freshness_ignores_orphan_price_is_stale_flag(self) -> None:
        # Even with the orphan flag set, freshness must be driven by the
        # bucket age — not by the field the scheduler stopped writing.
        po = self._po(bucket_age_sec=60)  # fresh bucket
        po["price_is_stale"] = True  # orphan: scheduler no longer emits this
        node = _build_price_overlay_node(po, self._layer(), self._NOW)
        assert node["freshness"] == "fresh"
