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
SUPERVISOR_EVENT = "decision_trace.supervisor"
PRECOOL_EVENT = "decision_trace.precool_decision"
DAY_TYPE_EVENT = "decision_trace.day_type_decision"


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
          3. price=elevated tier (+3F), 5cp=active -> price overlay STILL
             wins (5CP demoted per binding spec §11 #14; it does not
             contribute to effective_cool_f, but its telemetry fields
             are preserved on the trace line).
        """
        from .app import FiringState, LayerInputs, _push_layer_change_mid_period
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

        # 3: price overlay still wins; 5cp active does NOT raise effective
        # (binding spec §11 #14 — 5CP is telemetry-only, never controls).
        # Keeping price_overlay_tier=elevated so a transition occurs and
        # a trace is emitted; the assertion is that 5CP being newly
        # active doesn't change winning_layer or effective_cool_f.
        li_5cp = LayerInputs(
            price_tier_name="elevated", price_offset_f=3, price_override_f=None,
            price_prev_tier="elevated", current_price_cents=12.0,
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
        # All three calls emit a layer-resolution trace (trace is
        # written BEFORE the no-push short-circuit). Tick 3 emits a
        # trace but does NOT call execute_action — verified below via
        # firing.last_pushed_effective_cool_f staying at 78.
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

        # Tick 3: 5CP became active, but price overlay STILL wins.
        # 5CP demoted per binding spec §11 #14 — it never appears in
        # winning_layer. Telemetry fields preserved on the trace.
        assert t3["winning_layer"] == "price_overlay"
        assert t3["reason_code"] == "LAYER_RESOLUTION_PRICE_OVERLAY_WINS"
        assert t3["effective_cool_f"] == 78    # unchanged from t2
        assert t3["fivecp_active"] is True      # telemetry preserved
        assert t3["fivecp_cool_f"] == 85        # what 5CP WOULD have proposed
        assert t3["fivecp_scopes_fired"] == ["comed_zone"]
        assert t3["tick_id"] == "tick_3"

        # ACCEPTANCE: 5CP-only transition does not call execute_action.
        # last_pushed_effective_cool_f is updated only by a successful
        # push; it stayed at 78 from tick 2, so no mid-period push fired
        # for tick 3 (binding spec §11 #14).
        assert firing.last_pushed_effective_cool_f == 78

    @pytest.mark.asyncio
    async def test_scarcity_with_fivecp_active_price_overlay_wins(self, capsys, monkeypatch):
        """Binding spec §11 #14: when scarcity tier (price overlay
        override to 85F) AND 5CP are both active, the price overlay
        wins outright — 5CP is no longer a live-control layer and
        cannot tie. ``winning_layer`` MUST be ``"price_overlay"``,
        never ``"tie"`` or ``"5cp"``. The 5CP telemetry fields
        (``fivecp_active``, ``fivecp_cool_f``) are preserved on the
        trace so post-hoc analysis can reconstruct when 5CP would have
        fired."""
        from .app import FiringState, LayerInputs, _push_layer_change_mid_period
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        cfg = _make_schedule_check_cfg()
        c4, _ = _mock_c4_client()
        write_api = MagicMock()
        firing = FiringState(
            last_schedule_cool_f=75,
            last_action_label="COAST",
            last_pushed_effective_cool_f=None,
        )
        # Scarcity tier (override=85F) + 5CP active. Pre-§11 #14 these
        # would have tied at 85F; post-fix only the price overlay
        # contributes to effective_cool_f.
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
            now_local=now_local, tick_id="tick_price_alone",
        )

        traces = _parse_trace_lines(capsys.readouterr().out, LAYER_RESOLUTION_EVENT)
        assert len(traces) == 1, f"expected 1 trace, got {len(traces)}: {traces}"
        t = traces[0]
        # Price overlay alone wins; no tie, no 5cp.
        assert t["winning_layer"] == "price_overlay"
        assert t["reason_code"] == "LAYER_RESOLUTION_PRICE_OVERLAY_WINS"
        assert t["effective_cool_f"] == 85
        assert t["price_cool_f"] == 85
        # Telemetry preserved: 5CP would have proposed 85F if it were
        # a live layer, but it isn't.
        assert t["fivecp_cool_f"] == 85
        assert t["fivecp_active"] is True
        # schedule_cool_f stays 75 (the baseline).
        assert t["schedule_cool_f"] == 75
        # Both scopes contributed to fivecp_active in this fixture.
        assert set(t["fivecp_scopes_fired"]) == {"comed_zone", "rto"}

    @pytest.mark.asyncio
    async def test_layer_resolution_trace_is_failure_isolated(self, capsys, monkeypatch):
        """Patching `app.log` to raise on `decision_trace.layer_resolution`
        events must NOT propagate into `_push_layer_change_mid_period`'s
        caller path. The mid-period push behavior continues normally."""
        from .app import FiringState, LayerInputs, _push_layer_change_mid_period
        from . import app as app_mod
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
    """Trace fires once per `validate_setpoints` invocation (NOT per
    scheduler tick; supervisor only runs when a layer resolution
    proposes a setpoint). All 7 reason codes covered by directly driving
    `_trace_supervisor` with the supervisor's own outputs; one
    integration test via `_push_layer_change_mid_period` confirms the
    call-site wire-up; failure-isolation parallels Phase 1/2."""

    @pytest.mark.parametrize(
        "scenario_id,proposed_cool,proposed_heat,snapshot,"
        "expected_reason,expected_decision,expected_level,expected_indoor_avail",
        [
            # Approved: in-range setpoints, indoor temp present.
            ("approved",
             78, 68, {"indoor_temp_f": 72.0},
             "SUPERVISOR_APPROVED", "approved", "debug", True),
            # Approved, no indoor temp signal: supervisor falls through
            # to clamp check (all in-range) -> approved. indoor_temp_available
            # surfaces the diagnostic.
            ("approved_no_indoor_temp",
             78, 68, {},
             "SUPERVISOR_APPROVED", "approved", "debug", False),
            # Cool clamped UP from below floor (60 -> 65).
            ("clamped_cool_floor",
             60, 68, {"indoor_temp_f": 72.0},
             "SUPERVISOR_CLAMPED_COOL_FLOOR", "clamped", "info", True),
            # Cool clamped DOWN from above ceiling (90 -> 86).
            ("clamped_cool_ceiling",
             90, 68, {"indoor_temp_f": 72.0},
             "SUPERVISOR_CLAMPED_COOL_CEILING", "clamped", "info", True),
            # Heat clamped UP from below floor (50 -> 55).
            ("clamped_heat_floor",
             78, 50, {"indoor_temp_f": 72.0},
             "SUPERVISOR_CLAMPED_HEAT_FLOOR", "clamped", "info", True),
            # Heat clamped DOWN from above ceiling (80 -> 75).
            ("clamped_heat_ceiling",
             78, 80, {"indoor_temp_f": 72.0},
             "SUPERVISOR_CLAMPED_HEAT_CEILING", "clamped", "info", True),
            # Both axes clamped simultaneously.
            ("clamped_multiple",
             90, 50, {"indoor_temp_f": 72.0},
             "SUPERVISOR_CLAMPED_MULTIPLE", "clamped", "info", True),
            # Emergency overheat: indoor >= 86 trumps everything.
            ("emergency_overheat",
             85, 68, {"indoor_temp_f": 87.0},
             "SUPERVISOR_EMERGENCY_OVERHEAT", "emergency", "info", True),
        ],
    )
    def test_supervisor_eval_emits_every_invocation(
        self, capsys, monkeypatch,
        scenario_id, proposed_cool, proposed_heat, snapshot,
        expected_reason, expected_decision, expected_level, expected_indoor_avail,
    ):
        """Drive validate_setpoints + _trace_supervisor for each of the
        7 reason codes (8 scenarios including approved-no-indoor-temp).
        Asserts reason_code, decision, level, and indoor_temp_available
        field present on every trace line."""
        from .app import _trace_supervisor
        from .safety_supervisor import validate_setpoints
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")

        now_ct = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))
        decision = validate_setpoints(proposed_cool, proposed_heat, snapshot)
        _trace_supervisor(
            tick_id=f"tick_{scenario_id}", now_ct=now_ct,
            proposed_cool_f=proposed_cool, proposed_heat_f=proposed_heat,
            snapshot=snapshot, decision=decision,
        )

        traces = _parse_trace_lines(capsys.readouterr().out, SUPERVISOR_EVENT)
        assert len(traces) == 1, f"{scenario_id}: expected 1 trace, got {len(traces)}"
        t = traces[0]
        assert t["reason_code"] == expected_reason, scenario_id
        assert t["decision"] == expected_decision, scenario_id
        assert t["level"] == expected_level, scenario_id
        assert t["indoor_temp_available"] is expected_indoor_avail, scenario_id
        assert t["tick_id"] == f"tick_{scenario_id}", scenario_id
        # Proposed values always preserved on the trace.
        assert t["proposed_cool_f"] == proposed_cool
        assert t["proposed_heat_f"] == proposed_heat
        # Final values match the supervisor decision.
        assert t["final_cool_f"] == decision.cool_setpoint_f
        assert t["final_heat_f"] == decision.heat_setpoint_f

    @pytest.mark.asyncio
    async def test_supervisor_trace_fires_from_mid_period_repush(self, capsys, monkeypatch):
        """Integration: confirm the call-site wire-up actually emits a
        supervisor trace when `_push_layer_change_mid_period` runs, and
        that the trace shares `tick_id` with the layer_resolution trace
        from the same tick."""
        from .app import FiringState, LayerInputs, _push_layer_change_mid_period
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        cfg = _make_schedule_check_cfg()
        c4, _ = _mock_c4_client()
        write_api = MagicMock()
        firing = FiringState(
            last_schedule_cool_f=78,
            last_action_label="COAST",
            last_pushed_effective_cool_f=None,
        )
        layer_inputs = LayerInputs(
            price_tier_name="normal", price_offset_f=0, price_override_f=None,
            price_prev_tier="normal", current_price_cents=5.0,
            fivecp_active=False, fivecp_scopes_fired=(),
            fivecp_load_mw=0.0, fivecp_derivative=0.0,
            fivecp_forecast_peak=0.0, fivecp_season_5th_mw=20375.0,
            fivecp_data_available=True,
        )
        now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, "NORMAL", layer_inputs,
            today_dewpoint_f=60.0, override_note="",
            now_local=now_local, tick_id="tick_integration",
        )

        out = capsys.readouterr().out
        sup_traces = _parse_trace_lines(out, SUPERVISOR_EVENT)
        layer_traces = _parse_trace_lines(out, LAYER_RESOLUTION_EVENT)

        assert len(sup_traces) == 1, "supervisor trace must fire on mid-period repush"
        assert len(layer_traces) == 1, "layer_resolution trace must also fire (Phase 2)"
        assert sup_traces[0]["tick_id"] == "tick_integration"
        assert layer_traces[0]["tick_id"] == "tick_integration"
        # The mid-period repush proposes layer_resolution.effective_cool_f
        # (78 = schedule, no overlay) with HEAT_SETPOINT_FLOOR_F=65 heat.
        assert sup_traces[0]["proposed_cool_f"] == 78
        assert sup_traces[0]["proposed_heat_f"] == 65

    @pytest.mark.asyncio
    async def test_supervisor_trace_is_failure_isolated(self, capsys, monkeypatch):
        """Patching `app.log` to raise on `decision_trace.supervisor`
        events must NOT propagate into the caller. The mid-period push
        + thermostat-write path continues to execute."""
        from .app import FiringState, LayerInputs, _push_layer_change_mid_period
        from . import app as app_mod
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")

        original_log = app_mod.log
        def _maybe_raise(level, msg, **fields):
            if isinstance(msg, str) and msg == SUPERVISOR_EVENT:
                raise RuntimeError("synthetic supervisor trace failure")
            return original_log(level, msg, **fields)
        monkeypatch.setattr(app_mod, "log", _maybe_raise)

        cfg = _make_schedule_check_cfg()
        c4, _ = _mock_c4_client()
        write_api = MagicMock()
        firing = FiringState(
            last_schedule_cool_f=78,
            last_action_label="COAST",
            last_pushed_effective_cool_f=None,
        )
        layer_inputs = LayerInputs(
            price_tier_name="normal", price_offset_f=0, price_override_f=None,
            price_prev_tier="normal", current_price_cents=5.0,
            fivecp_active=False, fivecp_scopes_fired=(),
            fivecp_load_mw=0.0, fivecp_derivative=0.0,
            fivecp_forecast_peak=0.0, fivecp_season_5th_mw=20375.0,
            fivecp_data_available=True,
        )
        now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

        # Must not raise.
        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, "NORMAL", layer_inputs,
            today_dewpoint_f=60.0, override_note="",
            now_local=now_local, tick_id="tick_sup_iso",
        )

        # Write path completed despite trace fault.
        assert c4.get_climate.await_count >= 1, (
            "read_thermostat_snapshot must still run under trace fault"
        )
        action_rows = [
            c for c in write_api.write.call_args_list
            if "hvac.actions" in c.kwargs.get("record").to_line_protocol()
        ]
        assert len(action_rows) >= 1, (
            "hvac.actions write must still happen under trace fault"
        )
        assert firing.last_pushed_effective_cool_f == 78


# ---- Phase 4 — §7 precool rejection reason -------------------------------


class TestPhase4PrecoolRejection:
    """Trace fires once per `compute_price_aware_precool_window` call —
    one row per night at 21:00. All 6 PrecoolCode outcomes covered by
    driving the wrapper with mocked fetch helpers + the trace_reason
    out-param. The trace_reason mutation pattern matches Phase 5's
    decide_day_type[evaluation_tape] approach — single optional keyword
    arg, no return-shape change, no rule-tree re-implementation."""

    @pytest.mark.parametrize(
        "scenario_id,prices,forecast,expected_reason,expected_selected",
        [
            # Rejection: day-ahead LMP fetch returns None.
            ("no_da_lmp_data", None, None,
             "PRECOOL_REJECTED_NO_DA_LMP_DATA", False),
            # Rejection: prices present but forecast missing.
            ("no_forecast", [3.0] * 24, None,
             "PRECOOL_REJECTED_NO_FORECAST", False),
            # Rejection: DA vector incomplete (< 24 hours).
            ("da_lmp_incomplete", [3.0] * 20, {"high_f": 80.0},
             "PRECOOL_REJECTED_DA_LMP_INCOMPLETE", False),
            # Rejection: no consecutive cheap-hour window. All hours
            # above CHEAP_PRICE_THRESHOLD_C (= 4¢ supply baseline).
            ("no_cheap_window", [9.0] * 24, {"high_f": 80.0},
             "PRECOOL_REJECTED_NO_CHEAP_WINDOW", False),
            # Rejection: cheap window present (hours 6-12 at 2¢), but no
            # spike window after the minimum gap.
            ("no_spike_window_after_gap",
             [9.0] * 6 + [2.0] * 6 + [5.0] * 12,
             {"high_f": 80.0},
             "PRECOOL_REJECTED_NO_SPIKE_WINDOW_AFTER_GAP", False),
            # Happy path: cheap window 06:00-12:00 at 2¢, evening spike
            # 17:00-20:00 at 15¢. Selected.
            ("selected",
             [9.0] * 6 + [2.0] * 6 + [5.0] * 5 + [15.0] * 4 + [5.0] * 3,
             {"high_f": 92.0},
             "PRECOOL_SELECTED", True),
        ],
    )
    def test_precool_emits_with_reason(
        self, monkeypatch,
        scenario_id, prices, forecast, expected_reason, expected_selected,
    ):
        """Drive compute_price_aware_precool_window with mocked fetch
        helpers; capture trace_reason; assert the wrapper's out-list
        contains exactly one entry matching the expected PrecoolCode."""
        from . import app
        from zoneinfo import ZoneInfo as _ZoneInfo
        monkeypatch.setattr(app, "fetch_day_ahead_prices_for_date",
                            lambda q, b, d, tz: prices)
        monkeypatch.setattr(app, "fetch_latest_forecast",
                            lambda q, b, period: forecast)

        trace: list[str] = []
        result = app.compute_price_aware_precool_window(
            MagicMock(), "energy", "2026-07-15", _ZoneInfo("America/Chicago"),
            forecast_period="tomorrow", trace_reason=trace,
        )
        assert len(trace) == 1, f"{scenario_id}: expected 1 trace entry, got {trace}"
        assert trace[0] == expected_reason, scenario_id
        assert (result is not None) is expected_selected, (
            f"{scenario_id}: expected selected={expected_selected}, got result={result}"
        )

    def test_trace_precool_emits_trace_line(self, capsys, monkeypatch):
        """Confirm the _trace_precool helper emits a well-formed
        decision_trace.precool_decision line with expected fields for
        both happy-path and rejection inputs."""
        from .app import _trace_precool
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        now_ct = datetime(2026, 7, 14, 21, 0, tzinfo=ZoneInfo("America/Chicago"))

        # Selected.
        _trace_precool(
            tick_id="tick_selected", now_ct=now_ct,
            decision_for_date="2026-07-15", day_type="HOT_5CP_RISK",
            window={"hour_ct": 6, "depth_f": 67},
            reason_code="PRECOOL_SELECTED",
        )
        # Rejection.
        _trace_precool(
            tick_id="tick_rejected", now_ct=now_ct,
            decision_for_date="2026-07-15", day_type="NORMAL",
            window=None,
            reason_code="PRECOOL_REJECTED_NO_CHEAP_WINDOW",
        )

        traces = _parse_trace_lines(capsys.readouterr().out, PRECOOL_EVENT)
        assert len(traces) == 2
        t_sel, t_rej = traces
        assert t_sel["tick_id"] == "tick_selected"
        assert t_sel["selected"] is True
        assert t_sel["hour_ct"] == 6
        assert t_sel["depth_f"] == 67
        assert t_sel["day_type"] == "HOT_5CP_RISK"
        assert t_sel["reason_code"] == "PRECOOL_SELECTED"
        assert t_sel["level"] == "info"

        assert t_rej["selected"] is False
        assert t_rej["hour_ct"] is None
        assert t_rej["depth_f"] is None
        assert t_rej["reason_code"] == "PRECOOL_REJECTED_NO_CHEAP_WINDOW"
        assert t_rej["level"] == "info"

    def test_precool_trace_is_failure_isolated(self, capsys, monkeypatch):
        """Patching `app.log` to raise on `decision_trace.precool_decision`
        events must NOT propagate. _trace_precool returns normally."""
        from .app import _trace_precool
        from . import app as app_mod
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        original_log = app_mod.log
        def _maybe_raise(level, msg, **fields):
            if isinstance(msg, str) and msg == PRECOOL_EVENT:
                raise RuntimeError("synthetic precool trace failure")
            return original_log(level, msg, **fields)
        monkeypatch.setattr(app_mod, "log", _maybe_raise)

        now_ct = datetime(2026, 7, 14, 21, 0, tzinfo=ZoneInfo("America/Chicago"))
        # Must not raise.
        _trace_precool(
            tick_id="tick_iso", now_ct=now_ct,
            decision_for_date="2026-07-15", day_type="NORMAL",
            window=None, reason_code="PRECOOL_REJECTED_NO_DA_LMP_DATA",
        )


# ---- Phase 5 — day-type negative branches --------------------------------


class TestPhase5DayTypeTape:
    """`decide_day_type` mutates its existing reasons dict to add an
    `evaluation_tape` list — one entry per rule branch evaluated, with
    threshold/actual/fired/reason_code. The trace at `run_decision` and
    `run_decision_revisit` inlines the tape into the
    `decision_trace.day_type_decision` line.

    Return shape of `decide_day_type` is unchanged: the existing
    `reasons` keys (high_f, apparent_max_f, is_heat_advisory,
    max_dewpoint_f, alert_summary, reason) all stay present with the
    same types. Existing callers unaffected — regression-guarded by
    test_existing_decide_day_type_callers_unchanged."""

    def test_day_type_negative_branches_in_trace(self, monkeypatch):
        """A NORMAL day close to the HOT threshold (high_f=84,
        apparent_max_f=88) records HOT branches evaluated and rejected
        in the tape, then the NORMAL branch fired."""
        from .app import decide_day_type
        forecast = {
            "high_f": 84.0, "apparent_max_f": 88.0,
            "is_heat_advisory": 0, "max_dewpoint_f": 60.0,
            "alert_summary": "",
        }
        day_type, reasons = decide_day_type(forecast)
        assert day_type == "NORMAL"
        tape = reasons["evaluation_tape"]

        by_code = {entry["reason_code"]: entry for entry in tape}
        # HOT_HEAT_ADVISORY rule was evaluated and rejected (False).
        assert by_code["DAY_TYPE_HOT_HEAT_ADVISORY"]["fired"] is False
        # HOT_HIGH_GE_85 rule was evaluated and rejected (84 < 85).
        e = by_code["DAY_TYPE_HOT_HIGH_GE_85"]
        assert e["fired"] is False
        assert e["threshold"] == 85
        assert e["actual"] == 84.0
        # HOT_APPARENT_GE_90 rule was evaluated and rejected (88 < 90).
        e = by_code["DAY_TYPE_HOT_APPARENT_GE_90"]
        assert e["fired"] is False
        assert e["threshold"] == 90
        assert e["actual"] == 88.0
        # NORMAL_HIGH_75_TO_84 fired (84 >= 75).
        e = by_code["DAY_TYPE_NORMAL_HIGH_75_TO_84"]
        assert e["fired"] is True
        assert e["threshold"] == 75
        assert e["actual"] == 84.0

    def test_day_type_streak_branches_in_tape(self, monkeypatch):
        """A HOT day with day2=HOT records both streak rules: the
        multi-day path fires (day2 also HOT), and the 5cp-risk rule was
        also evaluated. The winning path is HOT_STREAK_MULTI_DAY."""
        from .app import decide_day_type
        forecast = {
            "high_f": 92.0, "apparent_max_f": 95.0,
            "is_heat_advisory": 0, "max_dewpoint_f": 70.0,
            "alert_summary": "",
        }
        day2 = {"high_f": 88.0, "apparent_max_f": 90.0,
                "is_heat_advisory": 0, "max_dewpoint_f": 68.0}
        day_type, reasons = decide_day_type(forecast, day2_forecast=day2)
        assert day_type == "HOT_STREAK_DAY1"
        tape = reasons["evaluation_tape"]
        by_code = {entry["reason_code"]: entry for entry in tape}
        # Base HOT_HIGH_GE_85 fired (92 >= 85).
        assert by_code["DAY_TYPE_HOT_HIGH_GE_85"]["fired"] is True
        # Streak multi-day fired (day2 is HOT).
        assert by_code["DAY_TYPE_HOT_STREAK_MULTI_DAY"]["fired"] is True
        # Streak 5cp-risk rule NOT in tape — multi-day fired first and
        # short-circuited the streak-eval chain. Test expresses that
        # short-circuit explicitly.
        assert "DAY_TYPE_HOT_STREAK_5CP_RISK" not in by_code

    def test_day_type_missing_temps_fallback_preserves_warn_and_reason(self, capsys):
        """Two regressions caught on PR review:

        1. The P2.7 warn log `forecast_no_temperature_fields_falling_back_
           to_normal` must still emit when decide_day_type sees a
           degraded forecast (high_f + apparent_max_f both None). Plan
           rule: "no removal of existing log lines."

        2. reasons["reason"] must NOT report "high_75_to_84" when the
           missing-temps fallback is the rule that actually fired —
           that lies about the rule and would make the trace's
           `winning_reason` field contradict `evaluation_tape`'s
           reason_code.
        """
        from .app import decide_day_type
        forecast = {
            # No high_f, no apparent_max_f — the degraded path.
            "is_heat_advisory": 0, "max_dewpoint_f": 60.0,
            "alert_summary": "",
        }
        day_type, reasons = decide_day_type(forecast)
        assert day_type == "NORMAL"
        # (1) Warn log preserved.
        captured = capsys.readouterr().out
        assert "forecast_no_temperature_fields_falling_back_to_normal" in captured
        # (2) Reason string distinguishes this path.
        assert reasons["reason"] == "missing_temps_fallback"
        # Tape confirms the same rule fired.
        last_fired = next(e for e in reversed(reasons["evaluation_tape"]) if e["fired"])
        assert last_fired["reason_code"] == "DAY_TYPE_NORMAL_MISSING_TEMPS_FALLBACK"

    def test_day_type_no_forecast_fallback_tape(self):
        """No-forecast input produces a single-entry tape with
        NORMAL_NO_FORECAST_FALLBACK fired."""
        from .app import decide_day_type
        day_type, reasons = decide_day_type(None)
        assert day_type == "NORMAL"
        tape = reasons["evaluation_tape"]
        assert len(tape) == 1
        assert tape[0]["reason_code"] == "DAY_TYPE_NORMAL_NO_FORECAST_FALLBACK"
        assert tape[0]["fired"] is True
        # Existing reasons keys still present.
        assert reasons["reason"] == "no_forecast_available"
        assert reasons["fallback"] is True

    def test_existing_decide_day_type_callers_unchanged(self):
        """Regression guard: every existing reasons-dict key continues
        to appear with the same type. Phase 5 only ADDS the
        evaluation_tape key; it must never remove or retype any
        existing key. Test pins every key the production callers
        consume (high_f, apparent_max_f, is_heat_advisory,
        max_dewpoint_f, alert_summary, reason) plus the conditional
        streak fields when they fire."""
        from .app import decide_day_type
        forecast = {
            "high_f": 80.0, "apparent_max_f": 82.0,
            "is_heat_advisory": 0, "max_dewpoint_f": 60.0,
            "alert_summary": "",
        }
        _, reasons = decide_day_type(forecast)
        assert reasons["high_f"] == 80.0
        assert reasons["apparent_max_f"] == 82.0
        assert reasons["is_heat_advisory"] is False
        assert reasons["max_dewpoint_f"] == 60.0
        assert reasons["alert_summary"] == ""
        assert isinstance(reasons["reason"], str)
        # New key present.
        assert isinstance(reasons["evaluation_tape"], list)

    def test_trace_day_type_emits_trace_line(self, capsys, monkeypatch):
        """Confirm _trace_day_type emits decision_trace.day_type_decision
        with the evaluation_tape inlined and the expected scalar
        fields."""
        from .app import _trace_day_type
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        now_ct = datetime(2026, 7, 14, 21, 0, tzinfo=ZoneInfo("America/Chicago"))
        reasons = {
            "high_f": 84.0, "apparent_max_f": 88.0,
            "is_heat_advisory": False, "max_dewpoint_f": 60.0,
            "alert_summary": "",
            "reason": "high_75_to_84",
            "evaluation_tape": [
                {"rule": "high_ge_normal", "threshold": 75,
                 "actual": 84.0, "fired": True,
                 "reason_code": "DAY_TYPE_NORMAL_HIGH_75_TO_84"},
            ],
        }
        _trace_day_type(
            tick_id="tick_dt", now_ct=now_ct,
            decision_for_date="2026-07-15", winning_day_type="NORMAL",
            reasons=reasons,
        )
        traces = _parse_trace_lines(capsys.readouterr().out, DAY_TYPE_EVENT)
        assert len(traces) == 1
        t = traces[0]
        assert t["tick_id"] == "tick_dt"
        assert t["winning_day_type"] == "NORMAL"
        assert t["decision_for_date"] == "2026-07-15"
        assert t["high_f"] == 84.0
        assert t["apparent_max_f"] == 88.0
        assert t["winning_reason"] == "high_75_to_84"
        assert t["level"] == "info"
        assert len(t["evaluation_tape"]) == 1
        assert t["evaluation_tape"][0]["reason_code"] == "DAY_TYPE_NORMAL_HIGH_75_TO_84"

    def test_day_type_trace_is_failure_isolated(self, monkeypatch):
        """Patching app.log to raise on day_type events must not
        propagate. _trace_day_type returns normally."""
        from .app import _trace_day_type
        from . import app as app_mod
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")
        original_log = app_mod.log
        def _maybe_raise(level, msg, **fields):
            if isinstance(msg, str) and msg == DAY_TYPE_EVENT:
                raise RuntimeError("synthetic day_type trace failure")
            return original_log(level, msg, **fields)
        monkeypatch.setattr(app_mod, "log", _maybe_raise)
        now_ct = datetime(2026, 7, 14, 21, 0, tzinfo=ZoneInfo("America/Chicago"))
        # Must not raise.
        _trace_day_type(
            tick_id="tick_iso", now_ct=now_ct,
            decision_for_date="2026-07-15", winning_day_type="MILD",
            reasons={"reason": "high_lt_75", "evaluation_tape": []},
        )


# ---- Feature-level chain test --------------------------------------------


class TestFeatureChain:
    """Outside-in feature-complete oracle. Stays xfail(strict=True) until
    Phase 5 lands. Marker is removed only in the PR that ships Phase 5 and
    only when the test passes against the real implementation with zero
    scaffolding (per AGENTS.md outside-in TDD rule + memory
    feedback-outside-in-xfail-not-skip)."""

    @pytest.mark.asyncio
    async def test_causal_chain_reconstructable_from_log(self, capsys, monkeypatch):
        """Outside-in feature-complete oracle. Drives ONE synthetic
        scheduler tick (the price-overlay eval + the mid-period layer
        re-push, both threading the SAME `tick_id`) and asserts every
        decision_trace.* line emitted within that tick shares that
        `tick_id`. Verifies the causal chain
        price_overlay_eval -> layer_resolution -> supervisor is
        reconstructable from a single LogQL filter.

        Note: day_type_decision and precool_decision do NOT fire during
        a scheduler tick — they fire from run_decision at 21:00 nightly.
        The chain test covers the per-tick chain inside
        run_schedule_check, which is the chain operators tail in real
        time during commissioning. day_type / precool correlation is
        covered by run_decision's own tick_id sharing (verified by
        Phase 4 + Phase 5 tests separately).

        With this test green and the per-phase tests green, the
        decision-trace feature is complete per AGENTS.md outside-in
        TDD rule."""
        from .app import (
            FiringState, LayerInputs,
            _evaluate_layer_inputs, _push_layer_change_mid_period,
        )
        monkeypatch.setenv("SCHEDULER_DECISION_TRACE_VERBOSE", "true")

        cfg = _make_schedule_check_cfg()
        c4, _ = _mock_c4_client()
        write_api = MagicMock()
        firing = FiringState(
            last_schedule_cool_f=78,  # baseline so mid-period repush runs
            last_action_label="COAST",
            last_pushed_effective_cool_f=None,
        )
        now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

        # Drive the price-overlay path with an elevated-tier scenario.
        _stub_layer_eval_io(monkeypatch, price_cents=12.0)

        # Generate one tick_id (as run_schedule_check does) and thread
        # it through both helpers.
        tick_id = "chain_tick_id"

        layer_inputs = _evaluate_layer_inputs(
            MagicMock(), write_api, cfg, firing, now_local, tick_id=tick_id,
        )
        await _push_layer_change_mid_period(
            cfg, c4, write_api, firing, "NORMAL", layer_inputs,
            today_dewpoint_f=60.0, override_note="",
            now_local=now_local, tick_id=tick_id,
        )

        out = capsys.readouterr().out

        # Every decision_trace.* line emitted in this synthetic tick
        # must carry the same tick_id.
        all_decision_traces = []
        for line in out.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("msg", "")
            if isinstance(msg, str) and msg.startswith("decision_trace."):
                all_decision_traces.append(rec)

        assert len(all_decision_traces) >= 3, (
            f"expected >=3 decision_trace.* lines (price_overlay + "
            f"layer_resolution + supervisor), got {len(all_decision_traces)}: "
            f"{[t.get('msg') for t in all_decision_traces]}"
        )

        # All tick_ids must match.
        observed_tick_ids = {t["tick_id"] for t in all_decision_traces}
        assert observed_tick_ids == {tick_id}, (
            f"tick_id correlation broken across phases: {observed_tick_ids}"
        )

        # All three event types must appear at least once.
        observed_events = {t["msg"] for t in all_decision_traces}
        assert PRICE_OVERLAY_EVENT in observed_events
        assert LAYER_RESOLUTION_EVENT in observed_events
        assert SUPERVISOR_EVENT in observed_events
