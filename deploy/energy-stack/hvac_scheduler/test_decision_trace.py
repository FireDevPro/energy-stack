"""Decision-trace acceptance tests.

Outside-in feature test for `docs/plans/archive/decision-trace-plan.md`. Asserts
every silent decision-trace gap is closed. Per-phase tests use
`@pytest.mark.xfail(strict=True)` until the phase's PR lands; markers are
removed in the same PR that wires the phase's emission. The final chain
test stays `xfail(strict=True)` until end of Phase 5 (the only definition
of feature-complete per AGENTS.md outside-in TDD rule + memory
feedback-outside-in-xfail-not-skip).

Run from this directory:
    python -m pytest test_decision_trace.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from zoneinfo import ZoneInfo

from . import app
from .app import FiringState, _evaluate_layer_inputs

# Reuse the existing _evaluate_layer_inputs test scaffolding so the trace
# tests drive the same caller surface the existing layer-input tests do.
from .test_hvac_scheduler import (  # noqa: E402
    _make_schedule_check_cfg,
    _mock_c4_client,
    _stub_layer_eval_io,
)

PRICE_OVERLAY_EVENT = "decision_trace.price_overlay_eval"
LAYER_RESOLUTION_EVENT = "decision_trace.layer_resolution"


def _parse_trace_lines(stdout: str, msg_filter: str | None = None) -> list[dict[str, Any]]:
    """Parse JSON log lines from captured stdout; optionally filter by msg."""
    out = []
    for line in stdout.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg_filter is None or rec.get("msg") == msg_filter:
            out.append(rec)
    return out


# ---- Phase 1 — price overlay per-eval log ---------------------------------


class TestPhase1PriceOverlay:
    """Trace fires once per `_evaluate_layer_inputs` invocation with
    enough context to reconstruct the price-overlay decision externally."""

    def test_price_overlay_eval_emits_every_call(self, capsys, monkeypatch):
        """Three calls produce three trace lines. Outcomes and reason
        codes match the price-overlay state machine's behavior.

        Sequence:
          1. price=5.0c, prev=normal -> held normal (price below trigger)
          2. price=12.0c -> upgrade to elevated (>= 10c trigger)
          3. price=22.0c -> upgrade to scarcity (>= 20c trigger)
        """
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        cfg = _make_schedule_check_cfg()
        firing = FiringState()
        write_api = MagicMock()
        now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

        # 1: held normal
        _stub_layer_eval_io(monkeypatch, price_cents=5.0)
        _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)

        # 2: upgrade to elevated
        _stub_layer_eval_io(monkeypatch, price_cents=12.0)
        _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing,
                                now_local + timedelta(minutes=1))

        # 3: upgrade to scarcity
        _stub_layer_eval_io(monkeypatch, price_cents=22.0)
        _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing,
                                now_local + timedelta(minutes=2))

        traces = _parse_trace_lines(capsys.readouterr().out, PRICE_OVERLAY_EVENT)
        assert len(traces) == 3, f"expected 3 trace lines, got {len(traces)}: {traces}"

        t0, t1, t2 = traces
        assert t0["outcome"] == "held"
        assert t0["reason_code"] == "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"
        assert t0["prev_tier"] == "normal"
        assert t0["new_tier"] == "normal"
        assert t0["price_cents"] == 5.0
        assert t0["price_feed_unavailable"] is False

        assert t1["outcome"] == "upgraded"
        assert t1["reason_code"] == "PRICE_OVERLAY_UPGRADED_TO_ELEVATED"
        assert t1["prev_tier"] == "normal"
        assert t1["new_tier"] == "elevated"
        assert t1["price_cents"] == 12.0

        assert t2["outcome"] == "upgraded"
        assert t2["reason_code"] == "PRICE_OVERLAY_UPGRADED_TO_SCARCITY"
        assert t2["new_tier"] == "scarcity"

        # tick_id is present on every line, distinct per call.
        assert {t["tick_id"] for t in traces} == set(t["tick_id"] for t in traces)
        assert len({t["tick_id"] for t in traces}) == 3
        # scheduler_mode is always emitted.
        for t in traces:
            assert t["scheduler_mode"] in ("shadow", "experiment", "production")

    def test_price_overlay_trace_is_failure_isolated(self, capsys, monkeypatch):
        """Trace failure (Loki down / stdout broken / bad field type)
        must not propagate. `_evaluate_layer_inputs` still returns a
        valid LayerInputs, the price-overlay state still updates, and
        the existing hvac.price_overlay write still happens.

        Fault-injection target is `app.log` (the actual outermost call
        inside `_trace`), not `_trace` itself. `_trace` has an internal
        try/except that swallows; replacing `_trace` directly tests a
        scenario where the safety wrapper itself is gone — unrealistic.
        Patching `log` exercises the realistic failure mode the wrapper
        is designed to absorb."""
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        # log() is also used for non-trace lines (warn / startup / etc.).
        # Only raise when the caller is the trace helper — identified by
        # the event-name prefix the trace emits.
        original_log = app.log
        def _maybe_raise(level, msg, **fields):
            if isinstance(msg, str) and msg.startswith("decision_trace."):
                raise RuntimeError("synthetic trace failure")
            return original_log(level, msg, **fields)
        monkeypatch.setattr(app, "log", _maybe_raise)

        cfg = _make_schedule_check_cfg()
        firing = FiringState()
        write_api = MagicMock()
        now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))
        _stub_layer_eval_io(monkeypatch, price_cents=12.0)

        # Must not raise.
        inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)

        # Price-overlay state machine still ran correctly.
        assert inputs.price_tier_name == "elevated"
        assert firing.price_overlay_state.current_tier == "elevated"

        # Existing hvac.price_overlay transition write still happened
        # (normal -> elevated is a tier change).
        wrote_price_overlay = any(
            "hvac.price_overlay" in c.kwargs.get("record").to_line_protocol()
            for c in write_api.write.call_args_list
        )
        assert wrote_price_overlay, "hvac.price_overlay transition write must still happen"

    def test_tick_id_not_in_loki_labels(self):
        """promtail config does NOT promote tick_id to a Loki label.
        Promoting it would explode series cardinality (~525K/year). This
        test is the regression guard.

        Passes today (tick_id isn't in the config yet); stays as a pin
        forward."""
        promtail_config = Path(__file__).parent.parent / "promtail" / "promtail-config.yml"
        assert promtail_config.exists(), f"promtail config missing at {promtail_config}"
        content = promtail_config.read_text()
        # Loose check: tick_id must not appear anywhere in the promtail
        # config (no label-assignment, no pipeline-stage extraction).
        assert "tick_id" not in content, (
            "tick_id appears in promtail/promtail-config.yml — must not be lifted "
            "to a Loki label (cardinality explosion). See decision-trace plan "
            "locked decisions."
        )

    def test_arm_field_present_only_inside_calendar_window(self, capsys, monkeypatch):
        """`arm` field emitted when current_arm_at(now_ct) returns A/B
        (i.e., inside the locked 2026-06-01..2026-11-16 calendar window);
        absent when current_arm_at returns None.

        scheduler_mode is asserted in test_price_overlay_eval_emits_every_call
        regardless of calendar position — this test isolates the
        arm-field semantics from mode."""
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        cfg = _make_schedule_check_cfg()
        firing = FiringState()
        write_api = MagicMock()
        _stub_layer_eval_io(monkeypatch, price_cents=5.0)

        # Inside calendar: 2026-07-04 12:00 CT falls inside arm 3 (A).
        now_inside = datetime(2026, 7, 4, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
        _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_inside)

        # Outside calendar: 2026-05-14 12:00 CT (pre-experiment).
        now_outside = datetime(2026, 5, 14, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
        _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_outside)

        traces = _parse_trace_lines(capsys.readouterr().out, PRICE_OVERLAY_EVENT)
        assert len(traces) == 2, f"expected 2 traces, got {len(traces)}"

        t_inside, t_outside = traces
        assert t_inside.get("arm") == "A", f"inside-calendar arm=A expected, got {t_inside.get('arm')!r}"
        assert "arm" not in t_outside, f"outside-calendar arm must be absent, got {t_outside.get('arm')!r}"

