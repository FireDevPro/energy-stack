"""Decision-trace acceptance tests.

Outside-in feature test for `docs/plans/decision-trace-plan.md`. Asserts
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
from unittest.mock import MagicMock

import pytest
from zoneinfo import ZoneInfo

import app
from app import FiringState, _evaluate_layer_inputs

# Reuse the existing _evaluate_layer_inputs test scaffolding so the trace
# tests drive the same caller surface the existing layer-input tests do.
from test_hvac_scheduler import (  # noqa: E402
    _make_schedule_check_cfg,
    _mock_c4_client,
    _stub_layer_eval_io,
)

PRICE_OVERLAY_EVENT = "decision_trace.price_overlay_eval"
LAYER_RESOLUTION_EVENT = "decision_trace.layer_resolution"
SUPERVISOR_EVENT = "decision_trace.supervisor"
PRECOOL_EVENT = "decision_trace.precool_decision"
DAY_TYPE_EVENT = "decision_trace.day_type_decision"


def _parse_trace_lines(stdout: str, msg_filter: str | None = None) -> list[dict]:
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
        assert t0["price_is_stale"] is False

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


# ---- Phase 2 — layer resolution per tick ---------------------------------


class TestPhase2LayerResolution:
    """Trace fires once per `resolve_layer_priority` call. Three scenarios
    cover the three winning layers (schedule / price overlay / 5cp);
    failure-isolation test parallels Phase 1."""

    @pytest.mark.asyncio
    async def test_layer_resolution_eval_emits_every_tick(self, capsys, monkeypatch):
        """Three calls to `_push_layer_change_mid_period` with distinct
        layer inputs produce three trace lines with the right
        `winning_layer` + `reason_code`:

          1. price=normal, 5cp=inactive -> schedule wins
          2. price=elevated tier (+3F), 5cp=inactive -> price overlay wins
          3. price=normal, 5cp=active (85F shutoff) -> 5cp wins
        """
        from app import FiringState, LayerInputs, _push_layer_change_mid_period
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        cfg = _make_schedule_check_cfg()
        c4, _ = _mock_c4_client()
        write_api = MagicMock()
        firing = FiringState(
            last_schedule_cool_f=75,  # baseline; needed so the mid-period path runs
            last_action_label="COAST",
            last_pushed_effective_cool_f=None,
        )
        now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

        # 1: schedule wins (price normal, 5cp inactive)
        li_schedule = LayerInputs(
            price_tier_name="normal", price_offset_f=0, price_override_f=None,
            price_prev_tier="normal", current_price_cents=5.0,
            fivecp_active=False, fivecp_scopes_fired=(),
            fivecp_load_mw=0.0, fivecp_derivative=0.0,
            fivecp_forecast_peak=0.0, fivecp_season_5th_mw=20375.0,
            fivecp_data_available=True,
        )
        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, "NORMAL", li_schedule,
            today_dewpoint_f=60.0, override_note="",
            now_local=now_local, tick_id="tick_1",
        )

        # 2: price overlay wins (elevated tier, offset +3 -> 78F)
        li_price = LayerInputs(
            price_tier_name="elevated", price_offset_f=3, price_override_f=None,
            price_prev_tier="normal", current_price_cents=12.0,
            fivecp_active=False, fivecp_scopes_fired=(),
            fivecp_load_mw=0.0, fivecp_derivative=0.0,
            fivecp_forecast_peak=0.0, fivecp_season_5th_mw=20375.0,
            fivecp_data_available=True,
        )
        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, "NORMAL", li_price,
            today_dewpoint_f=60.0, override_note="",
            now_local=now_local + timedelta(minutes=1), tick_id="tick_2",
        )

        # 3: 5cp wins (active, 85F shutoff override)
        li_5cp = LayerInputs(
            price_tier_name="normal", price_offset_f=0, price_override_f=None,
            price_prev_tier="normal", current_price_cents=5.0,
            fivecp_active=True, fivecp_scopes_fired=("comed_zone",),
            fivecp_load_mw=20000.0, fivecp_derivative=0.5,
            fivecp_forecast_peak=21000.0, fivecp_season_5th_mw=20375.0,
            fivecp_data_available=True,
        )
        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, "NORMAL", li_5cp,
            today_dewpoint_f=60.0, override_note="",
            now_local=now_local + timedelta(minutes=2), tick_id="tick_3",
        )

        traces = _parse_trace_lines(capsys.readouterr().out, LAYER_RESOLUTION_EVENT)
        assert len(traces) == 3, f"expected 3 traces, got {len(traces)}: {traces}"
        t1, t2, t3 = traces

        # Schedule wins
        assert t1["winning_layer"] == "schedule"
        assert t1["reason_code"] == "LAYER_RESOLUTION_SCHEDULE_WINS"
        assert t1["schedule_cool_f"] == 75
        assert t1["effective_cool_f"] == 75
        assert t1["fivecp_active"] is False
        assert t1["tick_id"] == "tick_1"
        # First trace: prev_eff is None, new is 75 -> info level
        assert t1["level"] == "info"

        # Price overlay wins
        assert t2["winning_layer"] == "price_overlay"
        assert t2["reason_code"] == "LAYER_RESOLUTION_PRICE_OVERLAY_WINS"
        assert t2["price_overlay_tier"] == "elevated"
        assert t2["price_cool_f"] == 78
        assert t2["effective_cool_f"] == 78
        assert t2["tick_id"] == "tick_2"
        # Effective changed 75 -> 78 -> info
        assert t2["level"] == "info"
        assert t2["prev_effective_cool_f"] == 75

        # 5CP wins
        assert t3["winning_layer"] == "5cp"
        assert t3["reason_code"] == "LAYER_RESOLUTION_5CP_WINS"
        assert t3["fivecp_active"] is True
        assert t3["fivecp_cool_f"] == 85
        assert t3["effective_cool_f"] == 85
        assert t3["fivecp_scopes_fired"] == ["comed_zone"]
        assert t3["tick_id"] == "tick_3"
        assert t3["level"] == "info"

    @pytest.mark.asyncio
    async def test_layer_resolution_tie_warmer_wins(self, capsys, monkeypatch):
        """When both the price overlay (scarcity-tier override 85F) AND
        5CP (active, 85F shutoff) propose the same effective setpoint,
        `winning_layer` must be `"tie"` and `reason_code` must be
        `LAYER_RESOLUTION_TIE_WARMER_WINS`. Preserves the forensic
        distinction between "schedule won alone" and "schedule matched
        by a non-schedule layer at the warmest." """
        from app import FiringState, LayerInputs, _push_layer_change_mid_period
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        cfg = _make_schedule_check_cfg()
        c4, _ = _mock_c4_client()
        write_api = MagicMock()
        firing = FiringState(
            last_schedule_cool_f=75,
            last_action_label="COAST",
            last_pushed_effective_cool_f=None,
        )
        # Scarcity tier (override=85F) + 5CP active (85F shutoff). Both
        # contribute at 85F effective.
        layer_inputs = LayerInputs(
            price_tier_name="scarcity", price_offset_f=0, price_override_f=85,
            price_prev_tier="scarcity", current_price_cents=22.0,
            fivecp_active=True, fivecp_scopes_fired=("comed_zone", "rto"),
            fivecp_load_mw=20000.0, fivecp_derivative=0.5,
            fivecp_forecast_peak=21000.0, fivecp_season_5th_mw=20375.0,
            fivecp_data_available=True,
        )
        now_local = datetime(2026, 7, 15, 17, 0, tzinfo=ZoneInfo("America/Chicago"))

        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, "HOT_5CP_RISK", layer_inputs,
            today_dewpoint_f=70.0, override_note="",
            now_local=now_local, tick_id="tick_tie",
        )

        traces = _parse_trace_lines(capsys.readouterr().out, LAYER_RESOLUTION_EVENT)
        assert len(traces) == 1, f"expected 1 trace, got {len(traces)}: {traces}"
        t = traces[0]
        assert t["winning_layer"] == "tie"
        assert t["reason_code"] == "LAYER_RESOLUTION_TIE_WARMER_WINS"
        assert t["effective_cool_f"] == 85
        assert t["price_cool_f"] == 85
        assert t["fivecp_cool_f"] == 85
        assert t["fivecp_active"] is True
        # schedule_cool_f stays 75 (the baseline); only the warmer layers tied.
        assert t["schedule_cool_f"] == 75
        # Both scopes contributed to fivecp_active in this fixture.
        assert set(t["fivecp_scopes_fired"]) == {"comed_zone", "rto"}

    @pytest.mark.asyncio
    async def test_layer_resolution_trace_is_failure_isolated(self, capsys, monkeypatch):
        """Patching `app.log` to raise on `decision_trace.layer_resolution`
        events must NOT propagate into `_push_layer_change_mid_period`'s
        caller path. The mid-period push behavior continues normally."""
        from app import FiringState, LayerInputs, _push_layer_change_mid_period
        import app as app_mod
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")

        original_log = app_mod.log
        def _maybe_raise(level, msg, **fields):
            if isinstance(msg, str) and msg == LAYER_RESOLUTION_EVENT:
                raise RuntimeError("synthetic layer_resolution trace failure")
            return original_log(level, msg, **fields)
        monkeypatch.setattr(app_mod, "log", _maybe_raise)

        cfg = _make_schedule_check_cfg()
        c4, _ = _mock_c4_client()
        write_api = MagicMock()
        firing = FiringState(
            last_schedule_cool_f=75,
            last_action_label="COAST",
            last_pushed_effective_cool_f=None,
        )
        layer_inputs = LayerInputs(
            price_tier_name="elevated", price_offset_f=3, price_override_f=None,
            price_prev_tier="normal", current_price_cents=12.0,
            fivecp_active=False, fivecp_scopes_fired=(),
            fivecp_load_mw=0.0, fivecp_derivative=0.0,
            fivecp_forecast_peak=0.0, fivecp_season_5th_mw=20375.0,
            fivecp_data_available=True,
        )
        now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

        # Must not raise. `_trace`'s internal try/except swallows the
        # synthetic failure, so `_trace_layer_resolution` returns
        # normally and the mid-period push continues.
        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, "NORMAL", layer_inputs,
            today_dewpoint_f=60.0, override_note="",
            now_local=now_local, tick_id="tick_iso",
        )

        # Stronger assertion: the existing thermostat-write path still
        # executed under the trace fault. read_thermostat_snapshot,
        # validate_setpoints, execute_action, and write_action must all
        # run as if the trace was healthy.
        # read_thermostat_snapshot called c4.get_climate at least once.
        assert c4.get_climate.await_count >= 1, (
            "read_thermostat_snapshot must still run under trace fault"
        )
        # write_action emitted at least one hvac.actions row for the
        # mid-period repush event (effective changed schedule=75 ->
        # elevated overlay 78F).
        action_rows = [
            c for c in write_api.write.call_args_list
            if "hvac.actions" in c.kwargs.get("record").to_line_protocol()
        ]
        assert len(action_rows) >= 1, (
            "hvac.actions write must still happen under trace fault"
        )
        # Guard updated to the new effective cool — proves the supervisor
        # + push branch executed cleanly through to the end.
        assert firing.last_pushed_effective_cool_f == 78


# ---- Phase 3 — supervisor per invocation ---------------------------------


class TestPhase3Supervisor:
    @pytest.mark.xfail(strict=True, reason="Phase 3 not yet implemented")
    def test_supervisor_eval_emits_every_invocation(self):
        pytest.fail("Phase 3 — supervisor trace emission not yet wired")


# ---- Phase 4 — §7 precool rejection reason -------------------------------


class TestPhase4PrecoolRejection:
    @pytest.mark.xfail(strict=True, reason="Phase 4 not yet implemented")
    def test_precool_rejection_emits_with_reason(self):
        pytest.fail("Phase 4 — precool-rejection trace emission not yet wired")


# ---- Phase 5 — day-type negative branches --------------------------------


class TestPhase5DayTypeTape:
    @pytest.mark.xfail(strict=True, reason="Phase 5 not yet implemented")
    def test_day_type_negative_branches_in_trace(self):
        pytest.fail("Phase 5 — day-type negative-branch trace emission not yet wired")


# ---- Feature-level chain test --------------------------------------------


class TestFeatureChain:
    """Outside-in feature-complete oracle. Stays xfail(strict=True) until
    Phase 5 lands. Marker is removed only in the PR that ships Phase 5 and
    only when the test passes against the real implementation with zero
    scaffolding (per AGENTS.md outside-in TDD rule + memory
    feedback-outside-in-xfail-not-skip)."""

    @pytest.mark.xfail(strict=True, reason="Feature chain green only after Phase 5 ships")
    def test_causal_chain_reconstructable_from_log(self):
        pytest.fail(
            "Chain test: one synthetic full-tick run produces a connected "
            "chain of decision_trace.* lines sharing a tick_id, covering "
            "price overlay -> layer resolution -> supervisor -> would-push. "
            "Pending Phase 5."
        )
