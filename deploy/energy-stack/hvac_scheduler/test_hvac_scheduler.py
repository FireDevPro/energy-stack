"""Tests for the HVAC scheduler — focused on pure-logic functions and the
release_hold action plumbing.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

import os
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from . import app


from .app import (
    FiringState,
    ScheduleAction,
    _evaluate_layer_inputs,
    execute_action,
    action_in_effect_at,
    resolve_cool_setpoint,
)


# ---- Test helper for the ComEd freshness PR (spec §8.7) ----
# Lives in this module (not conftest) because plain helper functions in
# conftest are not auto-imported into test-module globals — only
# @pytest.fixture functions are. This is module-local on purpose.


def _fresh_sample(cents: float, *, now_utc: datetime,
                  age_min: float = 1.0) -> Any:
    """Default-fresh PriceSample for tests not specifically exercising
    the freshness gate. `now_utc` is REQUIRED (no fallback to wall-clock
    — tests must drive time deterministically; see spec §8.7).
    `age_min` defaults to 1 min (well under the 7-min fresh threshold).

    PriceSample import is deferred until call time so this helper can
    land in the branch before Task 6 adds the dataclass.
    """
    from .app import PriceSample  # local import: deferred until first call
    return PriceSample(
        cents_per_kwh=cents,
        source_ts=now_utc - timedelta(minutes=age_min),
        freshness="fresh",
    )


# ---- ScheduleAction & schedules -------------------------------------------


def test_release_hold_action_has_no_setpoint():
    """release_hold actions don't carry a cool_setpoint_f — verifies the
    optional default."""
    a = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)
    assert a.cool_setpoint is None
    assert a.release_hold is True
    assert a.fan_mode is None


# ---- resolve_cool_setpoint ------------------------------------------------


def test_resolve_cool_setpoint_release_hold_returns_sentinel():
    a = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)
    setpoint, reason = resolve_cool_setpoint(a, today_dewpoint_f=60.0)
    assert setpoint == 0
    assert reason == "release_hold"


def test_resolve_cool_setpoint_humid_override_unchanged_for_setpoint_actions():
    """The humid-override path must still work for non-release actions."""
    a = ScheduleAction(13, 0, "COAST", cool_setpoint=79, cool_setpoint_humid=75)
    setpoint, reason = resolve_cool_setpoint(a, today_dewpoint_f=70.0)
    assert setpoint == 75
    assert "humid_override" in reason


def test_resolve_cool_setpoint_standard_path_unchanged():
    a = ScheduleAction(13, 0, "COAST", cool_setpoint=79)
    setpoint, reason = resolve_cool_setpoint(a, today_dewpoint_f=60.0)
    assert setpoint == 79
    assert reason == "standard"


# ---- execute_action: release-hold path ------------------------------------


def _mock_c4_client() -> tuple[MagicMock, MagicMock]:
    """Build a ThermostatClient mock whose call_with_reauth simply awaits the
    supplied callable. Captures the chain so the test can assert what was called."""
    c4 = MagicMock()

    climate = MagicMock()
    climate.set_hold_mode = AsyncMock()
    climate.set_hold_until = AsyncMock()
    climate.set_cool_setpoint_f = AsyncMock()
    climate.set_heat_setpoint_f = AsyncMock()
    climate.set_fan_mode = AsyncMock()

    c4.get_climate = AsyncMock(return_value=climate)

    async def _call(fn):
        return await fn()
    c4.call_with_reauth = AsyncMock(side_effect=_call)

    return c4, climate


async def test_execute_release_hold_calls_set_hold_mode_schedule():
    """release_hold action must call set_hold_mode("Schedule") — and only that.
    No setpoint or fan-mode writes."""
    c4, climate = _mock_c4_client()
    action = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=0, heat_setpoint_to_apply=60,
                                           state={"hvac_mode": "Cool"}, dry_run=False)

    assert applied is True
    assert error is None
    climate.set_hold_mode.assert_awaited_once_with("Schedule")
    climate.set_cool_setpoint_f.assert_not_awaited()
    climate.set_heat_setpoint_f.assert_not_awaited()
    climate.set_fan_mode.assert_not_awaited()


async def test_execute_release_hold_runs_regardless_of_hvac_mode():
    """A Permanent hold left over from yesterday's Cool day should still be
    cleared even if the user has since switched the thermostat to Heat or
    Off — set_hold_mode("Schedule") is idempotent and safe."""
    c4, climate = _mock_c4_client()
    action = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)

    applied, _err = await execute_action(c4, action, cool_setpoint_to_apply=0, heat_setpoint_to_apply=60,
                                          state={"hvac_mode": "Heat"}, dry_run=False)
    assert applied is True
    climate.set_hold_mode.assert_awaited_once_with("Schedule")


async def test_execute_release_hold_dry_run_does_not_call_thermostat():
    c4, climate = _mock_c4_client()
    action = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=0, heat_setpoint_to_apply=60,
                                           state={"hvac_mode": "Cool"}, dry_run=True)
    assert applied is False
    assert error is None
    climate.set_hold_mode.assert_not_awaited()


# ---- execute_action: regression on existing setpoint path -----------------


async def test_execute_setpoint_action_sets_timed_hold_never_permanent():
    """Spec Safety #2: holds are timed, never Permanent. A setpoint action
    pins its override with set_hold_until(expiry) so a dead controller's
    hold lapses on the device and the onboard schedule resumes."""
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=79,
                             fan_mode="Circulate")

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=79, heat_setpoint_to_apply=60,
                                           state={"hvac_mode": "Cool"}, dry_run=False,
                                           hold_until=dtime(14, 0))
    assert applied is True
    assert error is None
    climate.set_cool_setpoint_f.assert_awaited_once_with(79)
    climate.set_heat_setpoint_f.assert_awaited_once()
    climate.set_fan_mode.assert_awaited_once_with("Circulate")
    climate.set_hold_until.assert_awaited_once_with(dtime(14, 0))
    climate.set_hold_mode.assert_not_awaited()


async def test_execute_setpoint_action_without_hold_until_refuses_to_write():
    """No silent Permanent fallback: a setpoint action with no hold_until
    is refused before any device write (fail-loud; the only production
    caller always supplies one)."""
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=79)

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=79, heat_setpoint_to_apply=60,
                                           state={"hvac_mode": "Cool"}, dry_run=False)
    assert applied is False
    assert error and "hold_until" in error
    climate.set_cool_setpoint_f.assert_not_awaited()
    climate.set_heat_setpoint_f.assert_not_awaited()
    climate.set_hold_mode.assert_not_awaited()
    climate.set_hold_until.assert_not_awaited()


async def test_execute_setpoint_action_sets_heat_before_cool():
    """P1.3 ordering: when transitioning to a low cool target (e.g.,
    HOT_PRE_COOL=68F or HOT_STREAK_DAY1=66F) while the existing heat
    setpoint is high enough that the deadband would be violated, the
    cool push can be auto-adjusted by the thermostat before heat moves
    into range. Set heat first to pin the floor at 65F before cool
    moves. Defensive against asymmetric CTK04AE deadband behaviour."""
    from unittest.mock import call

    c4, climate = _mock_c4_client()
    # attach the AsyncMocks to a parent so we can read mock_calls in order
    parent = MagicMock()
    parent.attach_mock(climate.set_heat_setpoint_f, "set_heat_setpoint_f")
    parent.attach_mock(climate.set_cool_setpoint_f, "set_cool_setpoint_f")
    parent.attach_mock(climate.set_fan_mode, "set_fan_mode")
    parent.attach_mock(climate.set_hold_until, "set_hold_until")

    # HOT_PRE_COOL action: cool=68 from prior schedule state where heat
    # might have been higher than 65.
    action = ScheduleAction(4, 0, "HOT_PRE_COOL", cool_setpoint=68,
                             fan_mode="Auto")
    applied, error = await execute_action(
        c4, action, cool_setpoint_to_apply=68, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, hold_until=dtime(5, 0),
    )
    assert applied is True
    assert error is None

    # Pin the order: heat first, cool second, then fan, then hold.
    expected = [
        call.set_heat_setpoint_f(65),
        call.set_cool_setpoint_f(68),
        call.set_fan_mode("Auto"),
        call.set_hold_until(dtime(5, 0)),
    ]
    assert parent.mock_calls == expected, (
        f"Pre-P1.3 order was cool->heat. mock_calls: {parent.mock_calls}"
    )


async def test_execute_setpoint_action_skipped_when_in_heat_mode():
    """Setpoint actions still no-op in Heat mode (no fighting the furnace)."""
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=79)

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=79, heat_setpoint_to_apply=60,
                                           state={"hvac_mode": "Heat"}, dry_run=False)
    assert applied is False
    assert error and "hvac_mode_not_cooling" in error
    climate.set_hold_mode.assert_not_awaited()


# ---- §6 dry-run validation ------------------------------------------------


async def test_execute_setpoint_action_dry_run_pushes_nothing():
    """Dry-run mode (Arm A weeks) must NOT push any setpoints to the
    thermostat even on a fully-valid setpoint action with hvac_mode=Cool.
    The Pi only logs intended actions; CTK04AE's programmed schedule
    runs unobstructed. This is the binding contract validated by the
    24-hour pre-flight test before randomization begins."""
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=79,
                             fan_mode="Circulate")

    applied, error = await execute_action(
        c4, action, cool_setpoint_to_apply=79, heat_setpoint_to_apply=60,
        state={"hvac_mode": "Cool"}, dry_run=True,
    )
    assert applied is False
    assert error is None
    climate.set_cool_setpoint_f.assert_not_awaited()
    climate.set_heat_setpoint_f.assert_not_awaited()
    climate.set_fan_mode.assert_not_awaited()
    climate.set_hold_mode.assert_not_awaited()


async def test_execute_setpoint_action_dry_run_skips_even_when_layer_resolution_changes_setpoint():
    """If the layer-priority resolver computed a different effective
    setpoint (e.g., scarcity tier override of 85F), dry-run still pushes
    nothing. The 'don't push when dry_run' check sits above all upstream
    layer logic so no controller bug can leak through."""
    c4, climate = _mock_c4_client()
    # An action that would normally pre-cool to 68F; layer resolver bumped
    # it to 85F via scarcity-tier override. Even with that aggressive
    # resolution, dry-run pushes nothing.
    action = ScheduleAction(4, 0, "HOT_PRE_COOL", cool_setpoint=68)
    applied, error = await execute_action(
        c4, action, cool_setpoint_to_apply=85, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=True,
    )
    assert applied is False
    assert error is None
    climate.set_cool_setpoint_f.assert_not_awaited()


# ---- §Critical #2: per-tick layer evaluation ------------------------------


def _make_schedule_check_cfg(bucket: str = "energy",
                              tz_name: str = "America/Chicago",
                              dry_run: bool = True) -> MagicMock:
    cfg = MagicMock()
    cfg.influx_bucket = bucket
    cfg.tz_name = tz_name
    cfg.dry_run = dry_run
    # Explicitly None so the existing day-type reconstruction path runs
    # (controller_config=None is the pre-A2 default).  A2 tests that need the
    # config-gated path use _make_cfg_with_controller_config() instead.
    cfg.controller_config = None
    return cfg


def _stub_layer_eval_io(monkeypatch: Any, *,
                         price_cents: float | None = 5.0) -> None:
    """Stub the InfluxDB IO that _evaluate_layer_inputs makes. Lets tests
    drive the ComEd price input without spinning up Flux. (The 5CP detector
    was removed; the price overlay is the only IO this function makes.)
    """
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: (
                            None if price_cents is None
                            else _fresh_sample(price_cents, now_utc=now_utc)))


def test_evaluate_layer_inputs_runs_without_action_firing(monkeypatch):
    """The Critical #2 fix: layer eval is independent of action firing.
    A call at any minute populates a LayerInputs return value with the
    current price tier."""
    _stub_layer_eval_io(monkeypatch, price_cents=12.0)  # elevated tier
    cfg = _make_cfg_with_controller_config()
    firing = FiringState()
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 23,  # arbitrary non-action minute
                          tzinfo=ZoneInfo("America/Chicago"))

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs.price_tier_name == "elevated"


def test_evaluate_layer_inputs_carries_overlay_state_across_calls(monkeypatch):
    """Two consecutive ticks: first triggers elevated, second stays
    elevated due to the 30-min hold even with a brief price dip."""
    _stub_layer_eval_io(monkeypatch, price_cents=12.0)
    cfg = _make_cfg_with_controller_config()
    firing = FiringState()
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

    inputs1 = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs1.price_tier_name == "elevated"

    # Price drops below 8c release; overlay state machine sees prices but
    # the 30-min hold keeps us in elevated.
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(7.0, now_utc=now_utc))
    inputs2 = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing,
                                       now_local + timedelta(minutes=10))
    assert inputs2.price_tier_name == "elevated"  # hold still active


def test_evaluate_layer_inputs_writes_price_overlay_on_tier_transition(monkeypatch):
    """A price-tier transition writes a hvac.price_overlay row. Same-tier
    ticks don't (the measurement is event-driven)."""
    _stub_layer_eval_io(monkeypatch, price_cents=5.0)  # normal initially
    cfg = _make_cfg_with_controller_config()
    firing = FiringState()
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

    _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    transition_count = sum(
        1 for c in write_api.write.call_args_list
        if "hvac.price_overlay" in c.kwargs.get("record").to_line_protocol()
    )
    assert transition_count == 0  # normal-stays-normal: no transition

    # Crossing 10c triggers elevated -> one transition row written.
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(12.0, now_utc=now_utc))
    _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing,
                            now_local + timedelta(minutes=1))
    transition_count = sum(
        1 for c in write_api.write.call_args_list
        if "hvac.price_overlay" in c.kwargs.get("record").to_line_protocol()
    )
    assert transition_count == 1


# ---- P2: price tier preservation across feed gap --------------------------


def test_price_tier_carries_label_when_feed_drops(monkeypatch):
    """P2 review fix: when ``fetch_latest_comed`` returns None mid-tick,
    the active price-overlay tier label is carried forward (so the pinned
    formula re-derives the same warm effective setpoint next push) rather
    than collapsing to baseline. Slice B1: the effective setpoint is now
    derived from this tier label via ``effective_cool_for_tier``, so a
    preserved label is the whole carry-forward contract."""
    from .price_overlay import PriceOverlayState
    _stub_layer_eval_io(monkeypatch, price_cents=None)
    cfg = _make_cfg_with_controller_config()
    now_local = datetime(2026, 7, 15, 14, 30,
                          tzinfo=ZoneInfo("America/Chicago"))
    now_utc = now_local.astimezone(timezone.utc)
    # Simulate a prior tick where scarcity was active and the feed was
    # OK 2 min ago (well within the P2.2 stale threshold).
    firing = FiringState(
        price_overlay_state=PriceOverlayState(
            current_tier="scarcity",
            triggered_at_utc=now_utc - timedelta(minutes=10),
        ),
        last_fresh_bucket_source_ts=now_utc - timedelta(minutes=2),
    )
    write_api = MagicMock()

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    # Tier label preserved across the gap.
    assert inputs.price_tier_name == "scarcity"


def test_price_tier_elevated_label_preserved_across_feed_gap(monkeypatch):
    """Symmetric for the elevated tier."""
    from .price_overlay import PriceOverlayState
    _stub_layer_eval_io(monkeypatch, price_cents=None)
    cfg = _make_cfg_with_controller_config()
    now_local = datetime(2026, 7, 15, 14, 30,
                          tzinfo=ZoneInfo("America/Chicago"))
    now_utc = now_local.astimezone(timezone.utc)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(
            current_tier="elevated",
            triggered_at_utc=now_utc - timedelta(minutes=10),
        ),
        last_fresh_bucket_source_ts=now_utc - timedelta(minutes=2),
    )
    write_api = MagicMock()

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs.price_tier_name == "elevated"


def test_normal_tier_unaffected_by_feed_gap(monkeypatch):
    """Normal tier stays normal regardless of feed availability -- the
    carry-forward must not introduce a phantom active tier when there was
    no active tier to begin with."""
    _stub_layer_eval_io(monkeypatch, price_cents=None)
    cfg = _make_cfg_with_controller_config()
    firing = FiringState()   # default state: tier=normal
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs.price_tier_name == "normal"


# ---- P2.3 (reviewer-flagged 2026-05-11): health marker gating -------------


def test_health_marker_gating_pattern_in_main_loop():
    """Regression guard for the 2026-05-11 outage: when
    ``run_schedule_check`` raises, the main loop MUST NOT touch the
    ``/tmp/last_tick_ok`` health marker. Pre-fix the marker was touched
    unconditionally, so a repeated schedule_check_failed was visible
    in logs but invisible to Docker's HEALTHCHECK -- the container
    stayed 'healthy' while the control loop was broken (the actual
    P1.3 schema-collision incident).

    The marker now updates only when the tick completes without
    raising. This test inspects the main-loop source to confirm the
    gating is present; a runtime test would require spinning up
    asyncio and mocking the whole main() entrypoint, which is more
    fragile than the source assertion for a small structural fix."""
    import inspect
    main_src = inspect.getsource(app.main_async)
    # The marker touch must be inside an ``if tick_ok:`` block, and
    # ``tick_ok`` must be set False on the schedule_check exception path.
    assert "tick_ok = False" in main_src
    assert "if tick_ok:" in main_src
    sched_check_idx = main_src.index("schedule_check_failed")
    health_marker_idx = main_src.index("health_marker.touch")
    tick_ok_false_idx = main_src.index("tick_ok = False")
    # tick_ok = False must appear before the gated touch (so a failed
    # tick prevents the touch from firing later in the same loop iter).
    assert tick_ok_false_idx < health_marker_idx
    # And it must appear AFTER the schedule_check_failed line (i.e.
    # inside its except handler, not before).
    assert tick_ok_false_idx > sched_check_idx


# ---- SCHEDULER_MODE arm-mode gating (spec §3) -----------------------------
#
# Spec §3 controls whether the setpoint-write path can run via three
# explicit top-level modes set by SCHEDULER_MODE:
#   - shadow     : never writes (logs only)
#   - experiment : writes ONLY during Arm B periods inside the locked
#                  2026-06-01..2026-11-16 calendar; outside the window =
#                  no writes (no implicit "preserve pre-experiment" fallback)
#   - production : writes always; ignores A/B calendar (excluded from study)
# Unknown / missing values: refuse to start (sys.exit(2)).
#
# conftest.py sets SCHEDULER_MODE=production as the default so existing
# tests' dry_run-only assertions pass through the gate; per-mode tests
# below override via monkeypatch.setenv.


def _reload_app_with_mode(mode: str | None) -> None:
    """Reload the app module with SCHEDULER_MODE set to ``mode`` (or
    deleted when ``mode`` is None). Used for startup-validation tests.
    """
    import importlib
    if mode is None:
        os.environ.pop("SCHEDULER_MODE", None)
    else:
        os.environ["SCHEDULER_MODE"] = mode
    importlib.reload(app)


@pytest.fixture
def restore_app_after_reload():
    """Ensure subsequent tests see a healthy app module. Use on tests
    that call importlib.reload(app) with a deliberately-bad
    SCHEDULER_MODE — the module ends up partially loaded after sys.exit
    propagates out of pytest.raises.
    """
    yield
    _reload_app_with_mode("production")


async def test_shadow_mode_never_writes_even_with_dry_run_false(monkeypatch):
    """Spec §3 shadow: top-level mode gate blocks writes regardless of
    the dry_run parameter. Defense in depth."""
    monkeypatch.setenv("SCHEDULER_MODE", "shadow")
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=78,
                             fan_mode="Circulate")
    when_ct = datetime(2026, 6, 20, 13, 0)  # mid-Arm-2 (Arm B) — irrelevant in shadow

    applied, error = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
    )
    assert applied is False
    assert error is None
    climate.set_cool_setpoint_f.assert_not_awaited()
    climate.set_heat_setpoint_f.assert_not_awaited()
    climate.set_hold_mode.assert_not_awaited()


async def test_experiment_mode_arm_a_does_not_write(monkeypatch):
    """Spec §3 experiment: Arm A periods = scheduler in passive/no-write
    mode. CTK04AE thermostat program runs autonomously."""
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=78)
    when_ct = datetime(2026, 6, 5, 13, 0)  # mid-Arm-1 (Arm A)

    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
    )
    assert applied is False
    climate.set_cool_setpoint_f.assert_not_awaited()


async def test_experiment_mode_arm_b_writes(monkeypatch):
    """Spec §3 experiment: Arm B periods = scheduler active, writes pushed."""
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=78)
    when_ct = datetime(2026, 6, 20, 13, 0)  # mid-Arm-2 (Arm B)

    applied, error = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
        hold_until=dtime(14, 0),
    )
    assert applied is True
    assert error is None
    climate.set_cool_setpoint_f.assert_awaited_once_with(78)


async def test_experiment_mode_outside_window_does_not_write(monkeypatch):
    """Spec §3 experiment outside the 2026-06-01..2026-11-16 window:
    no writes. No implicit "preserve pre-experiment" fallback."""
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=78)

    # Before experiment start
    pre_when = datetime(2026, 5, 25, 13, 0)
    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=pre_when,
    )
    assert applied is False
    climate.set_cool_setpoint_f.assert_not_awaited()

    # After experiment end (2026-11-16 00:00 exclusive)
    post_when = datetime(2026, 11, 25, 13, 0)
    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=post_when,
    )
    assert applied is False
    climate.set_cool_setpoint_f.assert_not_awaited()


async def test_production_mode_writes_regardless_of_calendar(monkeypatch):
    """Spec §3 production: ignores A/B calendar entirely. Used for
    deliberate non-study operation. Excluded from analysis dataset."""
    monkeypatch.setenv("SCHEDULER_MODE", "production")
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint=78)
    # During what would be Arm A in experiment mode, production still writes.
    when_ct = datetime(2026, 6, 5, 13, 0)

    applied, error = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
        hold_until=dtime(14, 0),
    )
    assert applied is True
    assert error is None
    climate.set_cool_setpoint_f.assert_awaited_once_with(78)


def test_invalid_scheduler_mode_fails_startup(restore_app_after_reload):
    """Spec §3: unknown SCHEDULER_MODE = refuse to start (sys.exit(2))."""
    with pytest.raises(SystemExit) as exc_info:
        _reload_app_with_mode("bogus")
    assert exc_info.value.code == 2


def test_missing_scheduler_mode_fails_startup(restore_app_after_reload):
    """Spec §3: no default. SCHEDULER_MODE must be set explicitly."""
    with pytest.raises(SystemExit) as exc_info:
        _reload_app_with_mode(None)
    assert exc_info.value.code == 2


def test_scheduler_dry_run_env_is_ignored_with_warning(monkeypatch, capsys):
    """Plan standing rule: SCHEDULER_DRY_RUN is retired; if both env
    vars are present, SCHEDULER_DRY_RUN is ignored with a warning."""
    monkeypatch.setenv("SCHEDULER_MODE", "production")
    monkeypatch.setenv("SCHEDULER_DRY_RUN", "true")
    monkeypatch.setenv("INFLUXDB_TOKEN", "x")
    monkeypatch.setenv("INFLUXDB_ORG", "x")
    monkeypatch.setenv("INFLUXDB_BUCKET", "x")

    cfg = app.Config.from_env()
    captured = capsys.readouterr().out
    assert "scheduler_dry_run_ignored" in captured
    # Production mode -> dry_run derived as False (writes allowed by the gate).
    assert cfg.dry_run is False
    assert cfg.mode == "production"


def test_config_dry_run_derived_from_shadow_mode(monkeypatch):
    """In shadow mode, cfg.dry_run is True (the existing dry_run check
    inside execute_action acts as defense in depth alongside the
    SCHEDULER_MODE gate)."""
    monkeypatch.setenv("SCHEDULER_MODE", "shadow")
    monkeypatch.setenv("INFLUXDB_TOKEN", "x")
    monkeypatch.setenv("INFLUXDB_ORG", "x")
    monkeypatch.setenv("INFLUXDB_BUCKET", "x")
    monkeypatch.delenv("SCHEDULER_DRY_RUN", raising=False)

    cfg = app.Config.from_env()
    assert cfg.dry_run is True
    assert cfg.mode == "shadow"


def test_writes_allowed_handles_tz_aware_datetime(monkeypatch):
    """The main loop computes ``now_local = datetime.now(tz)`` (tz-aware).
    The gate must accept that without raising; arm_calendar uses
    naive CT-local datetimes."""
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    tz = ZoneInfo("America/Chicago")
    when_ct = datetime(2026, 6, 20, 13, 0, tzinfo=tz)  # tz-aware Arm B
    assert app._writes_allowed(when_ct) is True

    when_ct_arm_a = datetime(2026, 6, 5, 13, 0, tzinfo=tz)
    assert app._writes_allowed(when_ct_arm_a) is False


def test_writes_allowed_fails_closed_on_runtime_invalid_mode(monkeypatch):
    """Defense in depth: if SCHEDULER_MODE is mutated to an invalid
    value at runtime (after the import-time validation passed), the
    gate must fail closed (return False) rather than fall through to
    the experiment branch and consult the calendar. Spec §3 fail-
    closed lock applies at all times, not just startup."""
    monkeypatch.setenv("SCHEDULER_MODE", "bogus_runtime_value")
    when_ct = datetime(2026, 6, 20, 13, 0)  # Arm B - would write under experiment
    assert app._writes_allowed(when_ct) is False


# ---- hvac.arm_mode telemetry (spec §11 #2) --------------------------------


def _line_protocol(write_api: MagicMock) -> str:
    """Return the line-protocol body of the most recent write_api.write call."""
    record = write_api.write.call_args.kwargs.get("record")
    return str(record.to_line_protocol())


def test_write_arm_mode_writes_a_active_during_arm_a(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 5, 13, 0)  # Arm 1 (A)
    feeds = {"price": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    write_api.write.assert_called_once()
    line = _line_protocol(write_api)
    assert line.startswith("hvac.arm_mode,")
    assert "arm=A" in line
    assert "scheduler_mode=experiment" in line
    assert 'mode_actual="A-active"' in line


def test_write_arm_mode_writes_b_active_when_all_feeds_healthy(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 20, 13, 0)  # Arm 2 (B)
    feeds = {"price": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    line = _line_protocol(write_api)
    assert "arm=B" in line
    assert "scheduler_mode=experiment" in line
    assert 'mode_actual="B-active"' in line


def test_write_arm_mode_writes_b_fallback_when_feed_stale(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 20, 13, 0)  # Arm 2 (B)
    feeds = {"price": False}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    line = _line_protocol(write_api)
    assert "arm=B" in line
    assert 'mode_actual="B-fallback"' in line


def test_write_arm_mode_writes_b_down_when_controller_not_alive(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 20, 13, 0)
    feeds = {"price": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=False)
    line = _line_protocol(write_api)
    assert "arm=B" in line
    assert 'mode_actual="B-down"' in line


def test_write_arm_mode_in_window_shadow_emits_off_protocol_shadow(monkeypatch):
    """Spec §3 mandates SCHEDULER_MODE=experiment during the locked
    window. If the operator left the scheduler in shadow mode past
    2026-06-01 00:00 CT (no thermostat writes), the spec §5 four-mode
    classification does NOT apply: B-active would falsely claim the
    smart controller delivered treatment when it didn't. Emit
    mode_actual="off-protocol-shadow" so the analysis can EXCLUDE
    these hours from the primary outcome."""
    monkeypatch.setenv("SCHEDULER_MODE", "shadow")
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 20, 13, 0)  # Arm B period, but mode=shadow
    feeds = {"price": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    line = _line_protocol(write_api)
    assert 'mode_actual="off-protocol-shadow"' in line
    assert "scheduler_mode=shadow" in line


def test_write_arm_mode_in_window_production_emits_off_protocol_production(monkeypatch):
    """Spec §3: production mode is for deliberate non-study operation
    and is excluded from the analysis dataset. If active during the
    locked window it MUST NOT be classified as A-active or B-active."""
    monkeypatch.setenv("SCHEDULER_MODE", "production")
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 5, 13, 0)  # Arm A period, but mode=production
    feeds = {"price": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    line = _line_protocol(write_api)
    assert 'mode_actual="off-protocol-production"' in line
    assert "scheduler_mode=production" in line


def test_write_arm_mode_outside_experiment_window_emits_outside_window_row(monkeypatch):
    """Outside the locked window we emit a liveness row with
    mode_actual="outside-window" regardless of scheduler_mode so the
    watchdog (spec §11 #5) sees recent rows during shadow weeks."""
    monkeypatch.setenv("SCHEDULER_MODE", "shadow")
    write_api = MagicMock()
    when_ct = datetime(2026, 5, 25, 13, 0)  # before experiment start
    feeds = {"price": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    write_api.write.assert_called_once()
    line = _line_protocol(write_api)
    assert 'mode_actual="outside-window"' in line
    assert "scheduler_mode=shadow" in line


def test_write_arm_mode_outside_window_after_experiment_end(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "production")
    write_api = MagicMock()
    when_ct = datetime(2026, 11, 25, 13, 0)  # after experiment end
    feeds = {"price": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    write_api.write.assert_called_once()
    line = _line_protocol(write_api)
    assert 'mode_actual="outside-window"' in line


def test_write_arm_mode_accepts_tz_aware_datetime(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    write_api = MagicMock()
    tz = ZoneInfo("America/Chicago")
    when_ct = datetime(2026, 6, 5, 13, 0, tzinfo=tz)  # Arm 1 (A), tz-aware
    feeds = {"price": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    line = _line_protocol(write_api)
    assert "arm=A" in line
    assert 'mode_actual="A-active"' in line


# ---- Required-feeds-for-arm-mode helper (spec §5 + §5.1) ------------------


def test_required_feeds_never_includes_pjm_or_weather():
    """A4 (spec "Telemetry": required_feeds derived from the enabled-mode
    set): the reactive warm-only overlay is the sole enabled mode and
    consumes the live price feed only. PJM capacity-risk and weather are
    NOT required for B-active classification — at ANY date, inside or
    outside the (former) cooling-season window. PJM + weather health are
    still logged in the FULL feed-health audit (write_input_feed_health),
    just not used to down-classify B-active."""
    # Inside the (former) capacity-risk window: only price required.
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 7, 15, 13, 0),
        price_feed_healthy=True,
    )
    assert feeds == {"price": True}
    # Outside the window: only price required.
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 10, 15, 13, 0),
        price_feed_healthy=True,
    )
    assert feeds == {"price": True}
    # September 30 boundary: neither pjm nor weather required.
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 9, 30, 23, 59),
        price_feed_healthy=True,
    )
    assert "pjm_capacity_risk" not in feeds
    assert "weather" not in feeds
    # October 1 boundary: neither pjm nor weather required.
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 10, 1, 0, 0),
        price_feed_healthy=True,
    )
    assert "pjm_capacity_risk" not in feeds
    assert "weather" not in feeds


def test_required_feeds_propagates_unhealthy_flags():
    """Unhealthy price flag propagates through to the feed dict used for
    B-active classification (weather + PJM no longer in the dict — A4
    derives required feeds from the enabled-mode set)."""
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 7, 15, 13, 0),
        price_feed_healthy=False,
    )
    assert feeds == {"price": False}


# ---- hvac.switch_event boundary logging (spec §11 #3) ---------------------


def test_switch_event_logged_at_a_to_b_boundary():
    write_api = MagicMock()
    # 2026-06-15 00:00 CT is the Arm 1 (A) -> Arm 2 (B) boundary
    when_ct = datetime(2026, 6, 15, 0, 0)
    new_arm, observed = app.maybe_log_arm_switch(
        write_api, "energy", "A", arm_observed=True, when_ct=when_ct,
    )
    assert new_arm == "B"
    assert observed is True
    write_api.write.assert_called_once()
    line = _line_protocol(write_api)
    assert line.startswith("hvac.switch_event ")
    assert 'from_arm="A"' in line
    assert 'to_arm="B"' in line
    assert 'boundary_planned_ts="2026-06-15T00:00:00"' in line


def test_switch_event_not_logged_within_arm():
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 5, 14, 0)  # mid-Arm-1
    new_arm, observed = app.maybe_log_arm_switch(
        write_api, "energy", "A", arm_observed=True, when_ct=when_ct,
    )
    assert new_arm == "A"
    assert observed is True
    write_api.write.assert_not_called()


def test_switch_event_cold_start_does_not_log():
    """Cold start (arm_observed=False): the function seeds FiringState
    by returning the current arm + arm_observed=True but does NOT
    write a switch row. Mid-arm controller restart should not produce
    a phantom "boundary" event."""
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 5, 14, 0)
    new_arm, observed = app.maybe_log_arm_switch(
        write_api, "energy", None, arm_observed=False, when_ct=when_ct,
    )
    assert new_arm == "A"
    assert observed is True
    write_api.write.assert_not_called()


def test_switch_event_logs_experiment_start_boundary_from_observed_none():
    """Once arm_observed=True (post-cold-start), a transition from
    None (previously observed = outside window) to a real arm IS a
    spec §11 #3 boundary and MUST log. Without this, the controller's
    first observation of June 1 00:00 CT is silently swallowed."""
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 1, 0, 0)  # experiment start
    new_arm, observed = app.maybe_log_arm_switch(
        write_api, "energy", None, arm_observed=True, when_ct=when_ct,
    )
    assert new_arm == "A"
    assert observed is True
    line = _line_protocol(write_api)
    assert 'from_arm=""' in line
    assert 'to_arm="A"' in line
    assert 'boundary_planned_ts="2026-06-01T00:00:00"' in line


def test_switch_event_logged_at_b_to_a_boundary():
    write_api = MagicMock()
    # 2026-06-29 00:00 CT is the Arm 2 (B) -> Arm 3 (A) boundary
    when_ct = datetime(2026, 6, 29, 0, 0)
    new_arm, observed = app.maybe_log_arm_switch(
        write_api, "energy", "B", arm_observed=True, when_ct=when_ct,
    )
    assert new_arm == "A"
    assert observed is True
    line = _line_protocol(write_api)
    assert 'from_arm="B"' in line
    assert 'to_arm="A"' in line
    assert 'boundary_planned_ts="2026-06-29T00:00:00"' in line


def test_switch_event_logged_at_experiment_end():
    """End of experiment window: B -> None. Logged as a boundary with
    empty to_arm and the experiment-end timestamp."""
    write_api = MagicMock()
    when_ct = datetime(2026, 11, 16, 0, 0)  # experiment end (exclusive)
    new_arm, observed = app.maybe_log_arm_switch(
        write_api, "energy", "B", arm_observed=True, when_ct=when_ct,
    )
    assert new_arm is None
    assert observed is True
    line = _line_protocol(write_api)
    assert 'from_arm="B"' in line
    assert 'to_arm=""' in line
    assert 'boundary_planned_ts="2026-11-16T00:00:00"' in line


def test_switch_event_includes_actual_timestamp():
    write_api = MagicMock()
    # Observation slightly after the boundary (e.g., next 1-min tick)
    when_ct = datetime(2026, 6, 15, 0, 1)
    new_arm, observed = app.maybe_log_arm_switch(
        write_api, "energy", "A", arm_observed=True, when_ct=when_ct,
    )
    assert new_arm == "B"
    assert observed is True
    line = _line_protocol(write_api)
    # Planned ts is the calendar boundary (00:00); actual ts is the observation (00:01)
    assert 'boundary_planned_ts="2026-06-15T00:00:00"' in line
    assert 'boundary_actual_ts="2026-06-15T00:01:00"' in line


# ---- hvac.input_feed_health telemetry (spec §11 #4) -----------------------


def test_write_input_feed_health_writes_one_row_per_feed():
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 20, 13, 0)
    # The reactive price overlay is the only enabled mode, so price is the
    # only feed the controller produces. write_input_feed_health writes one
    # row per feed in the dict it is handed.
    feeds = {"price": False}
    app.write_input_feed_health(write_api, "energy", when_ct, feeds)
    assert write_api.write.call_count == 1

    line = _line_protocol(write_api)
    assert line.startswith("hvac.input_feed_health,feed=price")
    assert "healthy=false" in line


def test_write_input_feed_health_empty_dict_is_noop():
    write_api = MagicMock()
    app.write_input_feed_health(write_api, "energy", datetime(2026, 6, 20, 13, 0), {})
    write_api.write.assert_not_called()


# ---- Comprehensive dry-run guard audit (spec §11 #9) ----------------------
#
# Every execute_action branch MUST short-circuit before any Control4
# write when dry_run=True, regardless of action label, hvac_mode, or
# release-hold flag. This parametrized test enumerates a representative
# set of ScheduleAction shapes and asserts no Control4 mutator was
# awaited.
#
# Tests run with SCHEDULER_MODE=production so the top-level mode gate
# (Task 1.2) doesn't pre-empt the audit; the dry_run gate is what we
# are stress-testing here.


def _all_schedule_actions() -> list[Any]:
    """A representative set of ScheduleAction shapes the dry-run gate must
    block. Each entry is (label, action, cool_setpoint_to_apply, hvac_mode).
    Synthetic actions stand in for the (now-removed) day-type schedules; the
    dry-run gate is shape-agnostic so a handful of representative setpoint
    actions exercise the same code path.
    """
    out = []
    for label, cool, fan in (
        ("PRE_COOL", 70, None),
        ("COAST", 79, "Circulate"),
        ("BASELINE", 78, None),
        ("HOT_PRE_COOL", 68, None),
        ("SLEEP", 73, None),
    ):
        action = app.ScheduleAction(13, 0, label, cool_setpoint=cool, fan_mode=fan)
        out.append((label, action, cool, "Cool"))
    # Synthetic mid-period repush action label (covers the write-gate path
    # for a non-schedule synthetic action).
    repush = app.ScheduleAction(13, 0, "MID_PERIOD_REPUSH:COAST",
                                  cool_setpoint=82, fan_mode=None)
    out.append(("MID_PERIOD_REPUSH:COAST", repush, 82, "Cool"))
    # Auto mode hits the same setpoint branch
    auto_action = app.ScheduleAction(13, 0, "COAST", cool_setpoint=79)
    out.append(("COAST_AUTO_MODE", auto_action, 79, "Auto"))
    # Heating/Off mode short-circuits ("hvac_mode_not_cooling") - dry_run
    # gate must still pre-empt that path.
    heat_action = app.ScheduleAction(13, 0, "COAST", cool_setpoint=79)
    out.append(("COAST_HEAT_MODE", heat_action, 79, "Heat"))
    return out


@pytest.mark.parametrize(
    "label,action,cool_to_apply,hvac_mode",
    _all_schedule_actions(),
    ids=lambda v: v if isinstance(v, str) else "",
)
async def test_dry_run_never_calls_control4_for_any_action(
    monkeypatch, label, action, cool_to_apply, hvac_mode,
):
    monkeypatch.setenv("SCHEDULER_MODE", "production")
    c4, climate = _mock_c4_client()
    when_ct = datetime(2026, 6, 20, 13, 0)  # mid-Arm-2 (irrelevant in production)

    applied, error = await app.execute_action(
        c4, action,
        cool_setpoint_to_apply=cool_to_apply,
        heat_setpoint_to_apply=app.HEAT_SETPOINT_FLOOR,
        state={"hvac_mode": hvac_mode},
        dry_run=True,
        when_ct=when_ct,
    )

    assert applied is False, (
        f"{label}: dry_run=True must return applied=False, got applied={applied}"
    )
    assert error is None, (
        f"{label}: dry_run=True must return error=None, got error={error!r}"
    )
    climate.set_cool_setpoint_f.assert_not_awaited()
    climate.set_heat_setpoint_f.assert_not_awaited()
    climate.set_fan_mode.assert_not_awaited()
    climate.set_hold_mode.assert_not_awaited()


async def test_dry_run_blocks_even_when_mode_gate_would_allow(monkeypatch):
    """Production mode + dry_run=True: mode gate would allow but the
    dry_run gate must still pre-empt. Defense in depth."""
    monkeypatch.setenv("SCHEDULER_MODE", "production")
    c4, climate = _mock_c4_client()
    action = app.ScheduleAction(13, 0, "COAST", cool_setpoint=79)
    when_ct = datetime(2026, 6, 20, 13, 0)
    applied, error = await app.execute_action(
        c4, action, cool_setpoint_to_apply=79, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=True, when_ct=when_ct,
    )
    assert applied is False
    assert error is None
    climate.set_cool_setpoint_f.assert_not_awaited()


# ---- Outside-in acceptance test (north star per AGENTS.md outside-in TDD) ----
# Replays the 2026-05-19 19:18Z bug from STALE_DATA_HANDOFF.md. Initially
# xfail(strict=True) until the recency gate lands; marker comes off in the
# same commit that finishes the implementation.

def test_19_18z_downgrade_refused_on_stale_bucket(monkeypatch):
    """At 19:18Z on 2026-05-19, scheduler was in elevated tier with min-hold
    elapsed. Latest bucket was [19:05Z, 19:10Z] (price 2.5¢, age 8 min).
    Pre-fix: scheduler downgraded based on the stale 2.5¢. Two minutes later
    ComEd shed a 30.1¢ price.

    Post-fix expected behavior:
    - Tier remains 'elevated' (gate refuses the downgrade)
    - decision_trace.price_overlay_eval has reason_code
      PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE, bucket_age_sec ~480,
      price_feed_unavailable=false
    """
    from .app import (
        PriceSample, FiringState, _evaluate_layer_inputs,
    )
    from .price_overlay import PriceOverlayState

    now_utc = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    source_ts = datetime(2026, 5, 19, 19, 10, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=2.5,
        source_ts=source_ts,
        freshness="warn",  # 8 min > 7-min fresh threshold
    )

    captured_traces: list[dict[str, Any]] = []

    def _capture_trace(event_name: str, **fields: Any) -> None:
        if event_name == "decision_trace.price_overlay_eval":
            captured_traces.append(fields)

    monkeypatch.setattr("hvac_scheduler.app.fetch_latest_comed",
                        lambda q, b, *, now_utc: sample)
    monkeypatch.setattr("hvac_scheduler.app._trace", _capture_trace)

    cfg = MagicMock(influx_bucket="energy", tz_name="America/Chicago")
    cfg.controller_config = _make_controller_config_stub()  # config-driven overlay (B1)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(
            current_tier="elevated",
            triggered_at_utc=now_utc - timedelta(minutes=30, seconds=1),
        ),
        last_fresh_bucket_source_ts=source_ts,
    )

    _evaluate_layer_inputs(
        query_api=MagicMock(),
        write_api=MagicMock(),
        cfg=cfg,
        firing=firing,
        now_local=now_utc,  # treat now_utc as the local clock for this test
    )

    # Tier preserved.
    assert firing.price_overlay_state.current_tier == "elevated", (
        f"Tier should remain 'elevated', got {firing.price_overlay_state.current_tier!r}"
    )

    # Trace classifier output.
    price_overlay_traces = [t for t in captured_traces]
    assert price_overlay_traces, "Expected a decision_trace.price_overlay_eval emission"
    trace = price_overlay_traces[-1]
    assert trace.get("outcome") == "held", f"Got outcome={trace.get('outcome')!r}"
    assert trace.get("reason_code") == "PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE", (
        f"Got reason_code={trace.get('reason_code')!r}"
    )
    assert 470 <= trace.get("bucket_age_sec", -1) <= 490, (
        f"Expected bucket_age_sec ~480, got {trace.get('bucket_age_sec')!r}"
    )
    assert trace.get("price_feed_unavailable") is False

    # NOTE: hvac.input_feed_health audit-row assertion is OUT OF SCOPE for
    # the north-star test. That audit write happens in run_schedule_check
    # (app.py:2856), one layer above _evaluate_layer_inputs. The audit
    # derivation gets its own dedicated test in Task 11 against a real
    # production helper. Keep this acceptance test focused on the gate.


# ---- fetch_latest_comed new shape tests (spec §3.3, §8.3) ----

import pytest


def _mock_query_api_returning(records: list[Any]) -> MagicMock:
    """Build a query_api mock whose .query() returns one table with the
    given records (each a dict-like with `get_value()` and `get_time()` callables)."""
    from unittest.mock import MagicMock
    api = MagicMock()
    table = MagicMock()
    table.records = records
    api.query = MagicMock(return_value=[table])
    return api


def _record(value: Any, time: Any) -> MagicMock:
    """Minimal Influx record stub matching influxdb-client's interface."""
    from unittest.mock import MagicMock
    rec = MagicMock()
    rec.get_value = MagicMock(return_value=value)
    rec.get_time = MagicMock(return_value=time)
    return rec


def test_fetch_latest_comed_returns_PriceSample_when_row_exists():
    from .app import PriceSample, fetch_latest_comed
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    source_ts = now - timedelta(minutes=2)
    api = _mock_query_api_returning([_record(5.25, source_ts)])
    result = fetch_latest_comed(api, "energy", now_utc=now)
    assert isinstance(result, PriceSample)
    assert result.cents_per_kwh == 5.25
    assert result.source_ts == source_ts
    assert result.freshness == "fresh"


def test_fetch_latest_comed_returns_None_when_no_row():
    from .app import fetch_latest_comed
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    api = _mock_query_api_returning([])
    assert fetch_latest_comed(api, "energy", now_utc=now) is None


def test_fetch_latest_comed_returns_None_and_logs_error_when_time_missing(caplog):
    """Per spec §7: missing _time is malformed Influx state; log error,
    return None (do NOT raise — supervisor-continuity invariant)."""
    from .app import fetch_latest_comed
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    api = _mock_query_api_returning([_record(5.25, None)])
    result = fetch_latest_comed(api, "energy", now_utc=now)
    assert result is None


def test_fetch_latest_comed_classifies_warn_age():
    from .app import fetch_latest_comed
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    source_ts = now - timedelta(minutes=10)  # >7 fresh, <16 warn
    api = _mock_query_api_returning([_record(8.0, source_ts)])
    result = fetch_latest_comed(api, "energy", now_utc=now)
    assert result is not None
    assert result.freshness == "warn"


def test_fetch_latest_comed_classifies_stale_age():
    from .app import fetch_latest_comed
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    source_ts = now - timedelta(minutes=20)  # >16 warn, <30 stale
    api = _mock_query_api_returning([_record(8.0, source_ts)])
    result = fetch_latest_comed(api, "energy", now_utc=now)
    assert result is not None
    assert result.freshness == "stale"


# ---- FiringState last_fresh_bucket_source_ts semantic (spec §3.6, §8.4) ----

def test_last_fresh_bucket_source_ts_updates_on_fresh_read(monkeypatch):
    """Per spec §3.3 + §3.5: field is set to sample.source_ts (NOT now_utc)
    when sample.freshness == 'fresh'. Captures the corrected semantic."""
    from .app import FiringState, PriceSample, _evaluate_layer_inputs
    from .price_overlay import PriceOverlayState
    from unittest.mock import MagicMock

    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    source_ts = now - timedelta(minutes=3)  # fresh
    sample = PriceSample(
        cents_per_kwh=5.0, source_ts=source_ts, freshness="fresh",
    )
    monkeypatch.setattr("hvac_scheduler.app.fetch_latest_comed",
                        lambda q, b, *, now_utc: sample)
    monkeypatch.setattr("hvac_scheduler.app._trace", lambda *a, **k: None)
    monkeypatch.setattr("hvac_scheduler.app.write_input_feed_health", lambda *a, **k: None)

    cfg = MagicMock(influx_bucket="energy", tz_name="America/Chicago")
    cfg.controller_config = _make_controller_config_stub()  # config-driven overlay (B1)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(current_tier="normal"),
    )
    _evaluate_layer_inputs(MagicMock(), MagicMock(), cfg, firing, now_local=now)

    assert firing.last_fresh_bucket_source_ts == source_ts, (
        f"Field must be set to sample.source_ts ({source_ts}), "
        f"got {firing.last_fresh_bucket_source_ts}. "
        f"DO NOT use now_utc — see spec §3.5 'data-source vs controller-observation' guard."
    )


def test_last_fresh_bucket_source_ts_NOT_updated_on_warn_read(monkeypatch):
    """Per spec §3.6: only fresh reads update the field. Warn/stale/None reads
    leave it alone — this is the corrected semantic from the pre-fix bug
    (where the field updated on every non-None read)."""
    from .app import FiringState, PriceSample, _evaluate_layer_inputs
    from .price_overlay import PriceOverlayState
    from unittest.mock import MagicMock

    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=10),  # warn
        freshness="warn",
    )
    monkeypatch.setattr("hvac_scheduler.app.fetch_latest_comed",
                        lambda q, b, *, now_utc: sample)
    monkeypatch.setattr("hvac_scheduler.app._trace", lambda *a, **k: None)
    monkeypatch.setattr("hvac_scheduler.app.write_input_feed_health", lambda *a, **k: None)

    cfg = MagicMock(influx_bucket="energy", tz_name="America/Chicago")
    cfg.controller_config = _make_controller_config_stub()  # config-driven overlay (B1)
    seeded = now - timedelta(hours=1)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(current_tier="normal"),
        last_fresh_bucket_source_ts=seeded,
    )
    _evaluate_layer_inputs(MagicMock(), MagicMock(), cfg, firing, now_local=now)

    assert firing.last_fresh_bucket_source_ts == seeded, (
        "Warn read must NOT update the field (only fresh reads update it)."
    )


# ---- Audit telemetry derivation (spec §3.6, §8.5) ----

def test_derive_price_feed_healthy_within_30_min_returns_true():
    """Anti-regression test for the named-split in spec §3.6:
    price_feed_healthy must use the 30-min wall-clock threshold on
    last_fresh_bucket_source_ts.

    Pass-1-of-spec-review had a bug where an implementer set
    price_ok = sample.freshness == 'fresh', which broke arm-mode
    classification because ~74% of normal cycles would have classified
    as B-fallback. This test pins the correct broad-health semantic
    by asserting on the production helper used at the audit-write site."""
    from .app import FiringState, derive_price_feed_healthy
    from .price_overlay import PriceOverlayState

    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(current_tier="normal"),
        last_fresh_bucket_source_ts=now - timedelta(minutes=5),
    )
    assert derive_price_feed_healthy(firing, now) is True, (
        "Last fresh bucket 5 min ago is well within the 30-min broad-health "
        "window. If False, the 7-min per-tick threshold leaked into this helper."
    )


def test_derive_price_feed_healthy_past_30_min_returns_false():
    from .app import FiringState, derive_price_feed_healthy
    from .price_overlay import PriceOverlayState

    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(current_tier="normal"),
        last_fresh_bucket_source_ts=now - timedelta(minutes=31),
    )
    assert derive_price_feed_healthy(firing, now) is False


def test_derive_price_feed_healthy_at_exactly_30_min_returns_true():
    """Boundary: `<=` is inclusive, so exactly 30 min counts as healthy."""
    from .app import FiringState, derive_price_feed_healthy
    from .price_overlay import PriceOverlayState

    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(current_tier="normal"),
        last_fresh_bucket_source_ts=now - timedelta(minutes=30),
    )
    assert derive_price_feed_healthy(firing, now) is True


def test_derive_price_feed_healthy_returns_false_when_field_is_none():
    """Cold-start case: no fresh bucket ever observed -> not healthy."""
    from .app import FiringState, derive_price_feed_healthy
    from .price_overlay import PriceOverlayState

    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(current_tier="normal"),
        last_fresh_bucket_source_ts=None,
    )
    assert derive_price_feed_healthy(firing, now) is False


def test_derive_price_feed_healthy_ignores_per_tick_freshness():
    """The regression-prevention test. price_feed_healthy must NOT depend
    on the current tick's sample.freshness. Even if sample is non-fresh
    or None, as long as last_fresh_bucket_source_ts is within 30 min,
    the feed is broadly healthy. This is what prevented the pass-1 bug
    where arm-mode classification became overly sensitive to per-tick
    freshness jitter."""
    from .app import FiringState, derive_price_feed_healthy
    from .price_overlay import PriceOverlayState

    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(current_tier="elevated"),
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
    )
    # The current tick's sample is irrelevant to this derivation.
    # If a future implementer adds a `sample` parameter and uses
    # sample.freshness, this test catches it.
    assert derive_price_feed_healthy(firing, now) is True


# ---- Recency gate tests (spec §3.4, §8.2) ----

def _run_evaluate_with(monkeypatch: Any, sample: Any, *, current_tier: Any, triggered_at_utc: Any,
                       last_fresh_bucket_source_ts: Any, now_utc: datetime,
                       nonfresh_after_hold_started_at_utc: datetime | None = None) -> Any:
    """Helper: invoke _evaluate_layer_inputs under fully-mocked conditions.

    `nonfresh_after_hold_started_at_utc` (added in Task 19) lets tests
    SEED the safety-release timer before the evaluation. Default None
    preserves Task 12 gate-test behavior."""
    from .app import FiringState, _evaluate_layer_inputs
    from .price_overlay import PriceOverlayState
    from unittest.mock import MagicMock

    captured_traces: list[dict[str, Any]] = []
    def _capture_trace(event_name: str, **fields: Any) -> None:
        captured_traces.append({"_event": event_name, **fields})

    monkeypatch.setattr("hvac_scheduler.app.fetch_latest_comed",
                        lambda q, b, *, now_utc: sample)
    monkeypatch.setattr("hvac_scheduler.app._trace", _capture_trace)
    monkeypatch.setattr("hvac_scheduler.app.write_input_feed_health", lambda *a, **k: None)

    cfg = MagicMock(influx_bucket="energy", tz_name="America/Chicago")
    cfg.controller_config = _make_controller_config_stub()  # config-driven overlay (B1)
    firing = FiringState(
        price_overlay_state=PriceOverlayState(
            current_tier=current_tier,
            triggered_at_utc=triggered_at_utc,
        ),
        last_fresh_bucket_source_ts=last_fresh_bucket_source_ts,
        nonfresh_after_hold_started_at_utc=nonfresh_after_hold_started_at_utc,
    )
    _evaluate_layer_inputs(MagicMock(), MagicMock(), cfg, firing, now_local=now_utc)
    return firing, captured_traces


def test_gate_refuses_downgrade_when_sample_is_warn(monkeypatch):
    """Gate refuses downgrades when sample.freshness != 'fresh'. The
    19:18Z bug class: bucket age 8 min, price 2.5¢ (below 8¢ release),
    min-hold elapsed → pre-fix would downgrade; post-fix must hold."""
    from .app import PriceSample
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=2.5,
        source_ts=now - timedelta(minutes=8),
        freshness="warn",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=30, seconds=1),
        last_fresh_bucket_source_ts=now - timedelta(minutes=8),
        now_utc=now,
    )
    assert firing.price_overlay_state.current_tier == "elevated"
    po_traces = [t for t in traces if t.get("_event") == "decision_trace.price_overlay_eval"]
    assert po_traces
    assert po_traces[-1]["reason_code"] == "PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"


def test_gate_allows_downgrade_when_sample_is_fresh(monkeypatch):
    """With fresh data, the gate doesn't refuse; state machine fires the downgrade."""
    from .app import PriceSample
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=2.5,
        source_ts=now - timedelta(minutes=2),  # fresh
        freshness="fresh",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=30, seconds=1),
        last_fresh_bucket_source_ts=now - timedelta(minutes=2),
        now_utc=now,
    )
    assert firing.price_overlay_state.current_tier == "normal"  # downgraded to normal


def test_gate_does_not_affect_upgrade(monkeypatch):
    """Upgrades fire regardless of staleness — adding protection is safe."""
    from .app import PriceSample
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=22.0,  # >= 20¢ scarcity trigger
        source_ts=now - timedelta(minutes=10),  # warn
        freshness="warn",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=30, seconds=1),
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
    )
    assert firing.price_overlay_state.current_tier == "scarcity"


def test_gate_does_not_affect_hold_within_tier(monkeypatch):
    """If price is still above release, the state machine proposes hold,
    not downgrade. The gate has nothing to refuse."""
    from .app import PriceSample
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=15.0,  # >= 8¢ elevated release threshold
        source_ts=now - timedelta(minutes=10),  # warn
        freshness="warn",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=30, seconds=1),
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
    )
    assert firing.price_overlay_state.current_tier == "elevated"
    po_traces = [t for t in traces if t.get("_event") == "decision_trace.price_overlay_eval"]
    # Not gate-held; state machine naturally held.
    assert po_traces[-1]["reason_code"] != "PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"


def test_gate_boundary_at_exact_seven_min(monkeypatch):
    """Age == 7 min exactly → classifies as fresh (boundary inclusive)
    → gate does NOT refuse → downgrade fires."""
    from .app import PriceSample
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=2.5,
        source_ts=now - timedelta(minutes=7),  # exactly at fresh boundary
        freshness="fresh",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=30, seconds=1),
        last_fresh_bucket_source_ts=now - timedelta(minutes=7),
        now_utc=now,
    )
    assert firing.price_overlay_state.current_tier == "normal"  # downgraded


def test_gate_boundary_at_seven_min_plus_one_second(monkeypatch):
    """Age 7 min + 1 sec → warn → gate refuses."""
    from .app import PriceSample
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=2.5,
        source_ts=now - timedelta(minutes=7, seconds=1),
        freshness="warn",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=30, seconds=1),
        last_fresh_bucket_source_ts=now - timedelta(minutes=7, seconds=1),
        now_utc=now,
    )
    assert firing.price_overlay_state.current_tier == "elevated"


def test_gate_treats_future_dated_bucket_as_fresh_and_allows_downgrade(monkeypatch):
    """Per spec §7: clock-skew / negative-age treated as fresh.
    Anti-regression for a hypothetical sign-flip bug."""
    from .app import PriceSample
    now = datetime(2026, 5, 19, 19, 18, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=2.5,
        source_ts=now + timedelta(minutes=5),  # future-dated
        freshness="fresh",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=30, seconds=1),
        last_fresh_bucket_source_ts=now,
        now_utc=now,
    )
    assert firing.price_overlay_state.current_tier == "normal"  # downgraded


# ---- Safety release timer tests (spec §3.5, §8.6) ----

def test_timer_does_not_set_during_min_hold(monkeypatch):
    """During min-hold, no release possible — timer stays None even on stale data."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=10),
        freshness="warn",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=15),  # min-hold NOT elapsed (15 < 30)
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
    )
    assert firing.nonfresh_after_hold_started_at_utc is None


def test_timer_does_not_set_at_normal_tier(monkeypatch):
    """At normal tier, no release possible — timer stays None."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=10),
        freshness="warn",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="normal",
        triggered_at_utc=None,
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
    )
    assert firing.nonfresh_after_hold_started_at_utc is None


def test_timer_sets_on_first_post_hold_nonfresh_with_stale_sample(monkeypatch):
    """First post-hold non-fresh observation -> timer = now_utc (NOT source_ts)."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=10),  # warn
        freshness="warn",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=31),  # min-hold elapsed
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
    )
    assert firing.nonfresh_after_hold_started_at_utc == now, (
        f"Timer must be set to now_utc ({now}), got "
        f"{firing.nonfresh_after_hold_started_at_utc}. If this equals "
        f"sample.source_ts, the implementation is using the data-source clock."
    )


def test_timer_sets_on_first_post_hold_nonfresh_with_none_sample(monkeypatch):
    """First post-hold no-data observation -> timer = now_utc."""
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    firing, _ = _run_evaluate_with(
        monkeypatch, sample=None,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=31),
        last_fresh_bucket_source_ts=now - timedelta(minutes=5),
        now_utc=now,
    )
    assert firing.nonfresh_after_hold_started_at_utc == now


def test_timer_clears_on_fresh_sample_when_seeded(monkeypatch):
    """SEED timer to non-None first, then verify fresh sample clears it.
    Without seeding, this test would pass trivially (timer starts None).
    Per spec §3.5 reset rule #1."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=15.0,  # would-hold-anyway; fresh-clear is independent
        source_ts=now - timedelta(minutes=2),
        freshness="fresh",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=31),
        last_fresh_bucket_source_ts=now - timedelta(minutes=2),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=now - timedelta(minutes=15),  # SEED
    )
    assert firing.nonfresh_after_hold_started_at_utc is None, (
        "Fresh sample must clear the timer per spec §3.5 reset rule #1."
    )


def test_timer_does_NOT_clear_when_stale_would_hold(monkeypatch):
    """ANTI-REGRESSION (spec §3.5 + operator clarification):
    earlier pass-3 tick-counter draft reset on 'stale-would-hold' —
    the operator clarified that non-fresh is non-fresh regardless of
    what the stale price would propose. The timer must NOT reset.

    Scenario: timer was set 15 min ago. Current sample is stale but
    its price (18c) is above the elevated release threshold (8c) so
    the state machine would propose HOLD. Timer must STAY set."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    seeded_timer = now - timedelta(minutes=15)
    sample = PriceSample(
        cents_per_kwh=18.0,  # >= elevated release (8c) -> state machine HOLDs
        source_ts=now - timedelta(minutes=10),
        freshness="warn",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=45),
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=seeded_timer,
    )
    assert firing.nonfresh_after_hold_started_at_utc == seeded_timer, (
        f"Timer must STAY set when stale sample would-hold. "
        f"Expected {seeded_timer}, got {firing.nonfresh_after_hold_started_at_utc}. "
        f"If this is None, an implementer regressed to the pass-3 tick-counter "
        f"reset behavior — spec §3.5 forbids that."
    )


def test_timer_does_NOT_clear_when_stale_would_downgrade(monkeypatch):
    """ANTI-REGRESSION: timer must STAY set when sample is non-fresh and
    state machine would propose DOWNGRADE (the recency-gate scenario).
    The gate handles the per-tick refusal; the timer accumulates wall-clock."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    seeded_timer = now - timedelta(minutes=15)
    sample = PriceSample(
        cents_per_kwh=2.5,  # below release -> state machine proposes DOWNGRADE
        source_ts=now - timedelta(minutes=10),
        freshness="warn",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=45),
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=seeded_timer,
    )
    assert firing.nonfresh_after_hold_started_at_utc == seeded_timer
    # Gate also refuses the downgrade — tier remains elevated.
    assert firing.price_overlay_state.current_tier == "elevated"


def test_timer_does_NOT_clear_when_sample_remains_none(monkeypatch):
    """Anti-regression: timer must STAY set when sample stays None.
    The no-data case continues to accumulate."""
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    seeded_timer = now - timedelta(minutes=15)
    firing, _ = _run_evaluate_with(
        monkeypatch, sample=None,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=45),
        last_fresh_bucket_source_ts=now - timedelta(minutes=15),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=seeded_timer,
    )
    assert firing.nonfresh_after_hold_started_at_utc == seeded_timer


def test_timer_clears_on_return_to_normal(monkeypatch):
    """Reset rule #2: any tick where prev_tier == NORMAL clears the timer."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=10),
        freshness="warn",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="normal",
        triggered_at_utc=None,
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=now - timedelta(minutes=15),  # SEED
    )
    assert firing.nonfresh_after_hold_started_at_utc is None


def test_timer_clears_when_min_hold_restarts(monkeypatch):
    """Reset rule #3: min-hold not elapsed clears the timer."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=10),
        freshness="warn",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=5),  # min-hold NOT elapsed
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=now - timedelta(minutes=15),  # SEED
    )
    assert firing.nonfresh_after_hold_started_at_utc is None, (
        "Timer must clear when min-hold is not elapsed (covers tier upgrade)."
    )


def test_safety_release_at_29_min_59_sec_still_held(monkeypatch):
    """Timer set 29:59 ago, sample still non-fresh -> no release."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    timer_started = now - timedelta(minutes=29, seconds=59)
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=15),
        freshness="stale",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=45),
        last_fresh_bucket_source_ts=now - timedelta(minutes=15),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=timer_started,
    )
    assert firing.price_overlay_state.current_tier == "elevated"


def test_safety_release_at_30_min_exactly_fires(monkeypatch):
    """Timer set EXACTLY 30 min ago, sample stale -> release fires."""
    from .app import PriceSample, PRICE_FEED_STALE_THRESHOLD
    now = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    timer_started = now - PRICE_FEED_STALE_THRESHOLD
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=15),
        freshness="stale",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=60),
        last_fresh_bucket_source_ts=now - timedelta(minutes=35),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=timer_started,
    )
    assert firing.price_overlay_state.current_tier == "normal"
    assert firing.nonfresh_after_hold_started_at_utc is None  # cleared after release
    po_traces = [t for t in traces if t.get("_event") == "decision_trace.price_overlay_eval"]
    assert po_traces
    assert po_traces[-1]["reason_code"] == "PRICE_OVERLAY_RELEASED_PERSISTENT_STALE"


def test_safety_release_at_30_min_fires_no_data_reason(monkeypatch):
    """Timer set 30 min ago, sample is None -> release with RELEASED_NO_DATA."""
    from .app import PRICE_FEED_STALE_THRESHOLD
    now = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    timer_started = now - PRICE_FEED_STALE_THRESHOLD - timedelta(seconds=1)
    firing, traces = _run_evaluate_with(
        monkeypatch, sample=None,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=70),
        last_fresh_bucket_source_ts=now - timedelta(minutes=40),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=timer_started,
    )
    assert firing.price_overlay_state.current_tier == "normal"
    po_traces = [t for t in traces if t.get("_event") == "decision_trace.price_overlay_eval"]
    assert po_traces[-1]["reason_code"] == "PRICE_OVERLAY_RELEASED_NO_DATA"


def test_safety_release_does_not_use_data_source_clock(monkeypatch):
    """ANTI-REGRESSION TEST for the two-wall-clocks distinction (spec §3.5).
    Scenario: bucket source_ts is 45 min old (very stale by data-source
    clock) BUT the controller only just observed non-fresh post-hold 5 min
    ago. The timer's controller-observation clock reads 5 min — NOT 45 min.
    Release does NOT fire.

    This test would catch a regression where an implementer wires the
    safety release to `now_utc - firing.last_fresh_bucket_source_ts`."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=5.0,
        source_ts=now - timedelta(minutes=45),  # very stale by data-source clock
        freshness="stale",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=60),
        last_fresh_bucket_source_ts=now - timedelta(minutes=45),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=now - timedelta(minutes=5),  # only 5 min ago
    )
    # Release does NOT fire — controller-observation clock is 5 min, not 45 min.
    assert firing.price_overlay_state.current_tier == "elevated", (
        "Release fired prematurely. Implementation may be using sample.source_ts "
        "or last_fresh_bucket_source_ts as the safety-release clock instead of "
        "the controller-observation timer. See spec §3.5 guard."
    )


def test_timer_clears_when_protective_upgrade_fires_post_min_hold(monkeypatch):
    """ANTI-REGRESSION (Codex Checkpoint-3 finding): when stale data
    shows a price crossing a higher tier's trigger, the state machine
    upgrades. The safety-release timer accumulated under the previous
    tier MUST be cleared so the new tier gets its own observation
    window.

    Without this clear, under a delayed-next-tick after the upgrade,
    the next tick observes the new tier's min-hold as elapsed AND the
    old timer as still set → fires release against pre-upgrade
    accumulated non-fresh time → just-upgraded scarcity tier releases
    prematurely (the failure mode Codex described).

    Scenario: elevated tier post-min-hold (triggered_at_utc 45 min ago).
    Timer was set 10 min ago (10 min of post-hold non-fresh accumulated).
    New bucket arrives at age 8 min (warn) showing 22¢ — state machine
    proposes UPGRADE to scarcity.

    Post-fix expectations:
    - Tier upgrades to scarcity (state machine proposal applied)
    - Timer cleared to None (so new tier's observation window starts fresh)
    """
    from .app import PriceSample
    now = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    seeded_timer = now - timedelta(minutes=10)
    sample = PriceSample(
        cents_per_kwh=22.0,  # ≥ 20¢ scarcity trigger
        source_ts=now - timedelta(minutes=8),
        freshness="warn",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=45),  # post-min-hold
        last_fresh_bucket_source_ts=now - timedelta(minutes=8),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=seeded_timer,
    )
    assert firing.price_overlay_state.current_tier == "scarcity", (
        f"Upgrade should fire (price 22¢ ≥ 20¢ scarcity trigger). "
        f"Got tier={firing.price_overlay_state.current_tier!r}."
    )
    assert firing.nonfresh_after_hold_started_at_utc is None, (
        f"Timer must be cleared on protective upgrade so the new tier "
        f"gets its own observation window. Got "
        f"{firing.nonfresh_after_hold_started_at_utc}. If non-None, the "
        f"upgrade carried over the previous tier's non-fresh time — "
        f"would cause premature release on delayed next tick (Codex finding)."
    )


def test_timer_clear_on_upgrade_is_noop_during_min_hold(monkeypatch):
    """Companion test: upgrade fires DURING previous tier's min-hold.
    Timer was None per the min-hold-not-elapsed reset rule. Clear is
    a no-op. Confirms the unconditional clear doesn't break anything
    in the during-min-hold case."""
    from .app import PriceSample
    now = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=22.0,  # ≥ scarcity trigger
        source_ts=now - timedelta(minutes=2),  # fresh
        freshness="fresh",
    )
    firing, _ = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=15),  # IN min-hold (15 < 30)
        last_fresh_bucket_source_ts=now - timedelta(minutes=2),
        now_utc=now,
        nonfresh_after_hold_started_at_utc=None,  # was None during min-hold
    )
    assert firing.price_overlay_state.current_tier == "scarcity"
    assert firing.nonfresh_after_hold_started_at_utc is None


# ---- Slice B2: the four feed-gap behaviors under the 4-tier overlay ---------
# These prove the EXISTING feed-gap mechanism (freshness gate + safety-release
# timer in _evaluate_layer_inputs, reused as-is) still holds the four spec
# behaviors under B1's config-driven 4-tier overlay, including `extreme` and
# the held *effective setpoint* (re-derived from the held tier via the same
# `effective_cool_for_tier` formula the push path uses). Spec
# §"Feed-gap behavior — reuse as-is". No mechanism logic is changed here.

# Baseline used for the effective-setpoint assertions: the 13:00-19:00 midday
# block (78F) of _A2_PROGRAM_F, which _make_controller_config_stub() carries.
_B2_BASELINE_F = 78.0


def test_b2_brief_gap_holds_tier_and_effective_setpoint(monkeypatch):
    """Behavior 1: a brief feed gap (timer below PRICE_FEED_STALE_THRESHOLD)
    in an active tier holds BOTH the tier and the re-derived warm effective
    setpoint — it rides out a routine ~5-min publish gap, not collapses to
    baseline.

    Scarcity tier, min-hold elapsed, sample missing this tick, last fresh
    bucket only 2 min ago (no safety-release timer yet). The tier label is
    carried forward and `effective_cool_for_tier` re-derives the same warm
    setpoint (baseline + warm_band + spike_extra), NOT the comfort baseline.
    """
    from .app import _evaluate_layer_inputs, FiringState
    from .price_overlay import PriceOverlayState, effective_cool_for_tier

    now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("hvac_scheduler.app.fetch_latest_comed",
                        lambda q, b, *, now_utc: None)  # brief gap: no sample
    cfg = _make_cfg_with_controller_config()
    firing = FiringState(
        price_overlay_state=PriceOverlayState(
            current_tier="scarcity",
            triggered_at_utc=now - timedelta(minutes=31),  # min-hold elapsed
        ),
        last_fresh_bucket_source_ts=now - timedelta(minutes=2),  # brief gap only
    )

    inputs = _evaluate_layer_inputs(MagicMock(), MagicMock(), cfg, firing,
                                    now_local=now)

    # Tier held.
    assert inputs.price_tier_name == "scarcity"
    assert firing.price_overlay_state.current_tier == "scarcity"
    # Effective setpoint held (re-derived warm, not baseline). The held tier
    # would have started the safety-release timer (post-min-hold + non-fresh)
    # but it is nowhere near the 30-min threshold, so no release.
    eff = effective_cool_for_tier(inputs.price_tier_name, _B2_BASELINE_F,
                                  cfg.controller_config)
    assert eff == _B2_BASELINE_F + 2.0 + 2.0  # 78 + warm_band + spike_extra = 82
    assert eff > _B2_BASELINE_F, "must hold warm, not collapse to baseline"


def test_b2_stale_spike_upgrades_into_extreme(monkeypatch):
    """Behavior 2: a stale-but-present extreme-price sample drives a warm
    UPGRADE into the new top tier `extreme` despite staleness (upgrades are
    NOT freshness-gated; only downgrades are). The effective snaps to the
    comfort ceiling.

    Elevated tier, min-hold elapsed, sample at >= extreme_at (50c) with a
    `warn` (non-fresh) freshness label -> upgrades straight to `extreme`,
    effective = comfort_max.
    """
    from .app import PriceSample
    from .price_overlay import effective_cool_for_tier

    now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=55.0,            # >= 50c extreme trigger
        source_ts=now - timedelta(minutes=10),  # warn -> non-fresh
        freshness="warn",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="elevated",
        triggered_at_utc=now - timedelta(minutes=31),  # min-hold elapsed
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
    )
    # Upgrade fired despite staleness, all the way to extreme.
    assert firing.price_overlay_state.current_tier == "extreme"
    po_traces = [t for t in traces
                 if t.get("_event") == "decision_trace.price_overlay_eval"]
    assert po_traces[-1]["reason_code"] == "PRICE_OVERLAY_UPGRADED_TO_EXTREME"
    # Effective snaps to the comfort ceiling (comfort_max=85 in the stub).
    eff = effective_cool_for_tier("extreme", _B2_BASELINE_F,
                                  _make_controller_config_stub())
    assert eff == 85.0


def test_b2_stale_downgrade_refused_holds_scarcity_and_effective(monkeypatch):
    """Behavior 3: in an active tier, a stale lower-price sample does NOT
    downgrade — the recency gate holds the tier (downgrade_gate_held) and the
    warm effective setpoint stays held. Covered at the `scarcity` tier (the
    elevated case is already covered) to confirm the gate is tier-agnostic
    under the 4-tier overlay.

    Scarcity, min-hold elapsed, sample 2.5c (below scarcity release of 18c)
    with a `warn` (non-fresh) label -> the state machine would downgrade but
    the gate refuses; tier and effective held.
    """
    from .app import PriceSample
    from .price_overlay import effective_cool_for_tier

    now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
    sample = PriceSample(
        cents_per_kwh=2.5,             # below scarcity release (18c)
        source_ts=now - timedelta(minutes=10),  # warn -> non-fresh
        freshness="warn",
    )
    firing, traces = _run_evaluate_with(
        monkeypatch, sample,
        current_tier="scarcity",
        triggered_at_utc=now - timedelta(minutes=31),  # min-hold elapsed
        last_fresh_bucket_source_ts=now - timedelta(minutes=10),
        now_utc=now,
    )
    # Tier held by the recency gate.
    assert firing.price_overlay_state.current_tier == "scarcity"
    po_traces = [t for t in traces
                 if t.get("_event") == "decision_trace.price_overlay_eval"]
    assert po_traces[-1]["outcome"] == "held"
    assert po_traces[-1]["reason_code"] == "PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"
    # Effective setpoint still the held warm value, not baseline.
    eff = effective_cool_for_tier("scarcity", _B2_BASELINE_F,
                                  _make_controller_config_stub())
    assert eff == _B2_BASELINE_F + 2.0 + 2.0  # 82, held warm


def test_b2_sustained_staleness_releases_after_min_hold_plus_threshold(monkeypatch):
    """Behavior 4: sustained staleness releases to baseline only after the
    minimum-hold window AND PRICE_FEED_STALE_THRESHOLD of sustained non-fresh
    observation — the EXISTING compound timing (~hold_ttl_minutes +
    PRICE_FEED_STALE_THRESHOLD), NOT a flat 30 min. This is the reused
    mechanism; the test asserts the compound contract as a tick-by-tick
    narrative (no mechanism change).

    With hold_ttl_minutes=30 and PRICE_FEED_STALE_THRESHOLD=30:
      * the stale-release timer does not even start until the 30-min min-hold
        has elapsed (it resets to None every tick before then), so
      * a flat-30-min release (T0 + 30) does NOT fire — the controller is
        still in tier, and
      * the release fires only ~T0 + 60 (30 min-hold + 30 stale threshold).
    """
    from .app import (
        _evaluate_layer_inputs, FiringState, PriceSample,
        PRICE_FEED_STALE_THRESHOLD,
    )
    from .price_overlay import PriceOverlayState

    assert PRICE_FEED_STALE_THRESHOLD == timedelta(minutes=30)  # contract anchor

    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg_with_controller_config()
    min_hold = cfg.controller_config.hold_ttl_minutes  # 30
    assert min_hold == 30

    monkeypatch.setattr("hvac_scheduler.app._trace", lambda *a, **k: None)
    monkeypatch.setattr("hvac_scheduler.app.write_input_feed_health",
                        lambda *a, **k: None)

    # Every tick from here on returns a stale (non-fresh) sample whose price
    # still sits above the scarcity release (so the only thing that can end the
    # tier is the safety-release timer, never a price-driven downgrade).
    def _stale_sample(q, b, *, now_utc):
        return PriceSample(
            cents_per_kwh=25.0,                      # above scarcity release (18c)
            source_ts=now_utc - timedelta(minutes=20),  # stale -> non-fresh
            freshness="stale",
        )
    monkeypatch.setattr("hvac_scheduler.app.fetch_latest_comed", _stale_sample)

    firing = FiringState(
        price_overlay_state=PriceOverlayState(
            current_tier="scarcity",
            triggered_at_utc=t0,                     # tier entered at t0
        ),
        last_fresh_bucket_source_ts=t0,
    )

    def _tick(minutes_after_t0: int) -> None:
        _evaluate_layer_inputs(MagicMock(), MagicMock(), cfg, firing,
                               now_local=t0 + timedelta(minutes=minutes_after_t0))

    # During min-hold: tier held, timer never starts (resets to None each tick).
    _tick(10)
    assert firing.price_overlay_state.current_tier == "scarcity"
    assert firing.nonfresh_after_hold_started_at_utc is None
    _tick(29)
    assert firing.price_overlay_state.current_tier == "scarcity"
    assert firing.nonfresh_after_hold_started_at_utc is None

    # First post-min-hold tick: NOW the stale-release timer starts (at t0+31),
    # NOT back-dated to the start of the gap.
    _tick(31)
    assert firing.price_overlay_state.current_tier == "scarcity"
    assert firing.nonfresh_after_hold_started_at_utc == t0 + timedelta(minutes=31)

    # A "flat 30 min" interpretation would release here (T0 + 30 already past).
    # The compound mechanism does NOT — only ~1 min of stale-release time has
    # accumulated since the timer started at t0+31.
    _tick(40)
    assert firing.price_overlay_state.current_tier == "scarcity", (
        "Released on flat ~30 min — the compound timing (min-hold + threshold) "
        "was mis-driven or the mechanism changed."
    )

    # Just before timer + PRICE_FEED_STALE_THRESHOLD (t0+31 + 30 = t0+61): held.
    _tick(60)
    assert firing.price_overlay_state.current_tier == "scarcity"

    # At timer + PRICE_FEED_STALE_THRESHOLD (t0+61): release fires to baseline.
    _tick(61)
    assert firing.price_overlay_state.current_tier == "normal", (
        "Release should fire at min-hold + PRICE_FEED_STALE_THRESHOLD "
        "(~t0+61), the existing compound timing."
    )
    assert firing.nonfresh_after_hold_started_at_utc is None  # cleared on release


# ---- B3: scale-neutral telemetry schema (hvac.actions / hvac.price_overlay) --
#
# Spec §"Telemetry" + §"Units": scale-neutral field names + a `unit` tag.
# hvac.actions: drop day_type; tags = unit/tier/action_label/dry_run; fields
# carry commanded_cool/commanded_heat/baseline_cool/drift/actual_indoor_temp/
# actual_humidity/actual_cool_before/actual_heat_before/config_id. Values are
# in native temp_scale (NO conversion); the `unit` tag records the scale.
# drift == commanded_cool - baseline_cool.


def _capture_write_point(monkeypatch: Any) -> list[dict[str, Any]]:
    """Patch app.write_point and return the list of captured calls as dicts
    {measurement, tags, fields} so a test can assert the schema directly."""
    captured: list[dict[str, Any]] = []

    def _wp(write_api: Any, bucket: str, measurement: str, *,
            tags: Any, fields: Any, **kwargs: Any) -> None:
        captured.append({"measurement": measurement,
                         "tags": dict(tags), "fields": dict(fields)})
    monkeypatch.setattr(app, "write_point", _wp)
    return captured


def test_b3_write_action_schema_normal_tick(monkeypatch):
    """hvac.actions carries the scale-neutral schema on a normal tick: a `unit`
    tag (no `day_type`), a `tier` tag, and commanded/baseline/drift/actual/
    config_id fields. drift == commanded - baseline (0 at normal tier)."""
    captured = _capture_write_point(monkeypatch)
    action = ScheduleAction(13, 30, "BASELINE", cool_setpoint=78.0,
                            heat_setpoint=65.0)
    snapshot = {
        "indoor_temp_f": 74.0, "hvac_mode": "Cool",
        "cool_setpoint_f": 75.0, "heat_setpoint_f": 65.0, "humidity": 51.0,
    }
    app.write_action(
        MagicMock(), "energy", action,
        78.0, 65.0, None, "comfort_baseline",
        True, False, snapshot,
        unit="F", tier="normal", baseline_cool=78.0, config_id="abc123",
    )

    row = captured[-1]
    assert row["measurement"] == "hvac.actions"
    # Tags: unit + tier present; day_type GONE.
    assert row["tags"]["unit"] == "F"
    assert row["tags"]["tier"] == "normal"
    assert row["tags"]["action_label"] == "BASELINE"
    assert row["tags"]["dry_run"] == "true"
    assert "day_type" not in row["tags"]
    # Fields: scale-neutral names; old *_f names GONE.
    f = row["fields"]
    assert f["commanded_cool"] == 78.0
    assert f["commanded_heat"] == 65.0
    assert f["baseline_cool"] == 78.0
    assert f["drift"] == 0.0  # normal tier: commanded == baseline
    assert f["actual_indoor_temp"] == 74.0
    assert f["actual_humidity"] == 51.0
    assert f["actual_cool_before"] == 75.0
    assert f["actual_heat_before"] == 65.0
    assert f["config_id"] == "abc123"
    for gone in ("cool_setpoint_f", "heat_setpoint_f", "indoor_temp_before_f",
                 "indoor_humidity_before_pct", "cool_setpoint_proposed_f",
                 "heat_setpoint_proposed_f", "cool_setpoint_before_f",
                 "heat_setpoint_before_f"):
        assert gone not in f


def test_b3_write_action_drift_on_spike_tick(monkeypatch):
    """A spike (scarcity) tick: commanded_cool > baseline_cool and
    drift == warm_band + spike_extra (the warm-drift magnitude). Native scale,
    no conversion."""
    captured = _capture_write_point(monkeypatch)
    action = ScheduleAction(13, 30, "BASELINE", cool_setpoint=78.0,
                            heat_setpoint=65.0)
    snapshot = {"indoor_temp_f": 79.0, "hvac_mode": "Cool",
                "cool_setpoint_f": 78.0, "heat_setpoint_f": 65.0,
                "humidity": 55.0}
    # scarcity effective = baseline + warm_band(2) + spike_extra(2) = 82.
    app.write_action(
        MagicMock(), "energy", action,
        82.0, 65.0, None, "comfort_baseline",
        True, False, snapshot,
        unit="F", tier="scarcity", baseline_cool=78.0, config_id="abc123",
    )

    f = captured[-1]["fields"]
    assert captured[-1]["tags"]["tier"] == "scarcity"
    assert f["commanded_cool"] == 82.0
    assert f["baseline_cool"] == 78.0
    assert f["commanded_cool"] > f["baseline_cool"]
    assert f["drift"] == 4.0  # warm_band + spike_extra


def test_b3_write_action_celsius_unit_no_conversion(monkeypatch):
    """Under a Celsius config the values are emitted native (no F conversion)
    and the unit tag reads "C"."""
    captured = _capture_write_point(monkeypatch)
    action = ScheduleAction(13, 30, "BASELINE", cool_setpoint=25.5,
                            heat_setpoint=18.5)
    snapshot = {"indoor_temp_f": 24.0, "cool_setpoint_f": 25.5,
                "heat_setpoint_f": 18.5, "humidity": 50.0, "hvac_mode": "Cool"}
    app.write_action(
        MagicMock(), "energy", action,
        26.5, 18.5, None, "comfort_baseline",
        True, False, snapshot,
        unit="C", tier="elevated", baseline_cool=25.5, config_id="def456",
    )
    row = captured[-1]
    assert row["tags"]["unit"] == "C"
    assert row["fields"]["commanded_cool"] == 26.5  # native C, not converted
    assert row["fields"]["baseline_cool"] == 25.5
    assert row["fields"]["drift"] == 1.0  # warm_band in C


def test_b3_price_overlay_transition_schema(monkeypatch):
    """hvac.price_overlay is scale-neutral: a `unit` tag plus baseline_cool /
    commanded_cool (renamed from schedule_cool_f / effective_cool_f). The
    prev_tier/new_tier tags (carrying `extreme`) and current_price_cents /
    triggered_at_utc are unchanged."""
    captured = _capture_write_point(monkeypatch)
    app.write_price_overlay_transition(
        MagicMock(), "energy",
        prev_tier="scarcity", new_tier="extreme", unit="F",
        current_price_cents=55.0,
        baseline_cool=78.0, commanded_cool=85.0,
        triggered_at_utc=datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc),
    )
    row = captured[-1]
    assert row["measurement"] == "hvac.price_overlay"
    assert row["tags"]["unit"] == "F"
    assert row["tags"]["prev_tier"] == "scarcity"
    assert row["tags"]["new_tier"] == "extreme"
    f = row["fields"]
    assert f["baseline_cool"] == 78.0
    assert f["commanded_cool"] == 85.0
    assert f["current_price_cents"] == 55.0
    assert f["triggered_at_utc"]  # non-empty iso string
    assert "schedule_cool_f" not in f
    assert "effective_cool_f" not in f


def test_b3_run_schedule_check_writes_config_id_and_unit(monkeypatch):
    """Integration: a spike tick driven through run_schedule_check emits an
    hvac.actions row whose config_id equals the loaded config's id, whose unit
    tag is the config temp_scale, and whose commanded_cool > baseline_cool with
    tier == the spike tier."""
    import asyncio

    captured = _capture_write_point(monkeypatch)
    _stub_layer_eval_io(monkeypatch, price_cents=25.0)  # scarcity (>= 20c)
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(False, None)))
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 79.0, "hvac_mode": "Cool",
                            "cool_setpoint_f": 78.0, "heat_setpoint_f": 65.0,
                            "humidity": 55.0,
                        }))

    cfg = _make_cfg_with_controller_config()
    c4, _ = _mock_c4_client()
    firing = FiringState()
    now_local = datetime(2026, 7, 15, 13, 30, tzinfo=ZoneInfo("America/Chicago"))
    asyncio.run(app.run_schedule_check(
        cfg, c4, MagicMock(), MagicMock(),
        ZoneInfo(cfg.tz_name), now_local, firing,
    ))

    action_rows = [r for r in captured if r["measurement"] == "hvac.actions"]
    assert action_rows, "expected at least one hvac.actions row"
    row = action_rows[-1]
    assert row["tags"]["unit"] == cfg.controller_config.temp_scale
    assert row["tags"]["tier"] == "scarcity"
    assert row["fields"]["config_id"] == _STUB_CONFIG_ID
    # Baseline 78 (midday block) + warm_band 2 + spike_extra 2 = 82 commanded.
    assert row["fields"]["baseline_cool"] == 78.0
    assert row["fields"]["commanded_cool"] == 82.0
    assert row["fields"]["commanded_cool"] > row["fields"]["baseline_cool"]
    assert row["fields"]["drift"] == 4.0


# ---- action_in_effect_at (Task 2: restart baseline reconstruction) ----------

# Synthetic schedule (the day-type schedules were removed): PRE_COOL 06:00,
# COAST 13:00, RECOVER 19:00, SLEEP 21:00.
_SAMPLE_SCHEDULE = [
    ScheduleAction(6, 0, "PRE_COOL", cool_setpoint=70),
    ScheduleAction(13, 0, "COAST", cool_setpoint=79),
    ScheduleAction(19, 0, "RECOVER", cool_setpoint=75),
    ScheduleAction(21, 0, "SLEEP", cool_setpoint=73),
]


def test_action_in_effect_returns_latest_action_at_or_before_minute():
    act = action_in_effect_at(_SAMPLE_SCHEDULE, 14 * 60)
    assert act is not None and act.label == "COAST"


def test_action_in_effect_none_before_first_action():
    assert action_in_effect_at(_SAMPLE_SCHEDULE, 3 * 60) is None  # 03:00, first is 06:00


def test_action_in_effect_returns_last_action_at_end_of_day():
    act = action_in_effect_at(_SAMPLE_SCHEDULE, 24 * 60 - 1)
    assert act is not None and act.label == "SLEEP"


def test_action_in_effect_returns_release_hold_when_it_is_latest():
    # release_hold is no longer used by any day-type schedule, but
    # action_in_effect_at must still surface it when it is the latest action.
    sched = [ScheduleAction(0, 5, "RELEASE_HOLD", release_hold=True)]
    act = action_in_effect_at(sched, 12 * 60)
    assert act is not None
    assert act.label == "RELEASE_HOLD" and act.release_hold is True


# ---- A2: per-tick comfort baseline sourced from controller_config ----------
#
# These tests cover the config-gated per-tick baseline path added in A2.
# When cfg.controller_config is present, last_schedule_cool is computed every
# tick via comfort_baseline_cool(config.comfort_program, now_local), replacing
# the startup-reconstruction dependency for that path.  When controller_config
# is None the existing day-type reconstruction path is unchanged.

_A2_PROGRAM_F = [
    {"from": "22:00", "to": "06:00", "cool": 73.0},
    {"from": "06:00", "to": "13:00", "cool": 75.0},
    {"from": "13:00", "to": "19:00", "cool": 78.0},
    {"from": "19:00", "to": "22:00", "cool": 75.0},
]

# Sentinel config_id for the A2/A4/B1 config-present test stub. The B3
# hvac.actions schema test asserts the written config_id equals this value.
_STUB_CONFIG_ID = "stub-config-sha"


def _make_controller_config_stub(heat_floor: float = 65.0) -> Any:
    """Real ControllerConfig for A2/A4/B1 tests (config-present path).

    F-scale, on-grid values. ``heat_floor`` defaults to 65.0 (F baseline);
    pass 18.5 for the Celsius-heat-floor case. The price tiers / flexibility
    / ceiling / hold are real so the config-driven overlay (Slice B1) can
    read them: warm_band=2, spike_extra=2, comfort_max=85 (above the 78
    midday baseline so an extreme spike rides to the ceiling), and
    hold_ttl_minutes=30 to preserve the gate tests' 30-min hold semantics.
    """
    from .controller_config import (
        Ceiling, ControllerConfig, Flexibility, HumidityGuard, Modes,
        PriceTiersCents, Stage1Ramp,
    )
    return ControllerConfig(
        temp_scale="F",
        comfort_program=tuple(_A2_PROGRAM_F),
        heat_floor=heat_floor,
        flexibility=Flexibility(warm_band=2.0, spike_extra=2.0),
        price_tiers_cents=PriceTiersCents(
            elevated_at=10.0, scarcity_at=20.0, extreme_at=50.0,
            hysteresis_cents=2.0,
        ),
        humidity_guard=HumidityGuard(rh_max_pct=65, rh_clear_pct=62),
        ceiling=Ceiling(comfort_max=85.0),
        hold_ttl_minutes=30,
        modes=Modes(stage1_ramp=Stage1Ramp(enabled=False)),
        config_id=_STUB_CONFIG_ID,
    )


def _make_cfg_with_controller_config(
    bucket: str = "energy",
    tz_name: str = "America/Chicago",
    dry_run: bool = True,
) -> MagicMock:
    cfg = _make_schedule_check_cfg(bucket=bucket, tz_name=tz_name, dry_run=dry_run)
    cfg.controller_config = _make_controller_config_stub()
    return cfg


def test_a2_block_boundary_baseline_matches_new_block(monkeypatch):
    """A2: just inside a new comfort block (13:30), with the 13:00 action
    already marked as fired so it won't re-fire, the per-tick baseline sourced
    from controller_config equals the new block's value (78) since no action
    overwrites it this tick."""
    import asyncio

    _stub_layer_eval_io(monkeypatch)
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 74.0,
                            "hvac_mode": "Cool",
                            "cool_setpoint_f": 75,
                            "heat_setpoint_f": 65,
                        }))
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())

    # Pre-seed fired_actions so the 13:00 COAST action does not fire this tick.
    # We're at 13:30 -- well past the 5-min makeup window -- so the action
    # won't re-fire regardless, but pre-seeding is explicit about intent.
    firing = FiringState()
    firing.fired_actions.add(("2026-07-15", 13, 0))
    # 13:30 is inside the 13:00-19:00 block (cool=78 in _A2_PROGRAM_F).
    # No NORMAL schedule action fires at 13:30 (next boundary is 19:00).
    now_local = datetime(2026, 7, 15, 13, 30, tzinfo=ZoneInfo("America/Chicago"))

    cfg = _make_cfg_with_controller_config()
    c4, _climate = _mock_c4_client()

    asyncio.run(app.run_schedule_check(
        cfg, c4, MagicMock(), MagicMock(),
        ZoneInfo(cfg.tz_name), now_local, firing,
    ))
    # The config-gated per-tick path must have set the baseline to 78
    # (the 13:00-19:00 block value in _A2_PROGRAM_F).
    assert firing.last_schedule_cool == 78.0, (
        f"expected 78.0 from config comfort_program, got {firing.last_schedule_cool}"
    )


def test_a2_mid_block_restart_recomputes_baseline_without_reconstruction(monkeypatch):
    """A2 KEY ASSERTION: restart mid-block with config present and no prior
    state (last_schedule_cool=None, baseline_initialized=False). The baseline
    must be recomputed from the comfort_program -- not from startup
    reconstruction -- so last_schedule_cool is not None on the first tick."""
    import asyncio

    _stub_layer_eval_io(monkeypatch)
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 74.0,
                            "hvac_mode": "Cool",
                            "cool_setpoint_f": 75,
                            "heat_setpoint_f": 65,
                        }))
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())

    # Cold start: no prior state
    firing = FiringState()
    assert firing.last_schedule_cool is None
    assert firing.baseline_initialized is False

    # Mid-block: 15:30, inside the 13:00-19:00 block (cool=78)
    now_local = datetime(2026, 7, 15, 15, 30, tzinfo=ZoneInfo("America/Chicago"))

    cfg = _make_cfg_with_controller_config()
    c4, _climate = _mock_c4_client()

    asyncio.run(app.run_schedule_check(
        cfg, c4, MagicMock(), MagicMock(),
        ZoneInfo(cfg.tz_name), now_local, firing,
    ))

    # Key assertion: baseline recomputed from config (the startup
    # reconstruction path no longer exists; the config path is the only path).
    assert firing.last_schedule_cool == 78.0, (
        f"mid-block restart should recompute 78.0 from config, got {firing.last_schedule_cool}"
    )


def test_a2_no_scheduled_actions_mid_period_still_gets_baseline(monkeypatch):
    """A2: mid-period path with config present and no day-type schedule actions
    for this minute still gets a valid baseline from the comfort_program.
    Uses a time with no matching schedule action (14:00 on MILD has no action
    in the existing day-type schedule, but the config provides the baseline)."""
    import asyncio

    _stub_layer_eval_io(monkeypatch)
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 74.0,
                            "hvac_mode": "Cool",
                            "cool_setpoint_f": 78,
                            "heat_setpoint_f": 65,
                        }))
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())

    firing = FiringState()
    # 14:00 on MILD: non-action minute (past the 13:00 MILD_DAY action).
    # With config present, the baseline comes from the 13:00-19:00 block (78).
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

    cfg = _make_cfg_with_controller_config()
    c4, _climate = _mock_c4_client()

    asyncio.run(app.run_schedule_check(
        cfg, c4, MagicMock(), MagicMock(),
        ZoneInfo(cfg.tz_name), now_local, firing,
    ))
    # Config-gated path must have populated the baseline (not None)
    assert firing.last_schedule_cool is not None, (
        "baseline must be set from config when no day-type action fires"
    )
    assert firing.last_schedule_cool == 78.0


# ---- A4: resolve = comfort baseline, floor-clamped; supervisor deleted ------
#
# The pivotal slice. With controller_config present (the single path now), the
# effective cool setpoint is the per-tick comfort baseline, clamped never below
# the baseline (the floor invariant). The software safety supervisor is gone:
# no validate_setpoints, no supervisor fields on hvac.actions. The loop no
# longer consults day-type resolution (fetch_today_decision / schedule_for) or
# the 21:00 decision cycle (run_decision).


def test_a4_effective_equals_baseline_floor_clamped_shadow(monkeypatch):
    """A4 acceptance: a config-present tick in shadow pushes the comfort
    baseline as the effective cool setpoint (floor-clamped, never below the
    baseline), writes a shadow hvac.actions row with NO supervisor fields,
    and consults neither the day-type resolution (fetch_today_decision /
    schedule_for) nor the safety supervisor (validate_setpoints)."""
    import asyncio

    # Trip-wires: these MUST NOT be called on the config path anymore.
    called: dict[str, int] = {
        "validate_setpoints": 0,
        "fetch_today_decision": 0,
        "schedule_for": 0,
    }

    def _trip(name: str, ret: Any) -> Any:
        def _stub(*a: Any, **k: Any) -> Any:
            called[name] += 1
            return ret
        return _stub

    # validate_setpoints was deleted with the supervisor; setattr only if a
    # regression re-introduces it (then the trip-wire catches the call).
    if hasattr(app, "validate_setpoints"):
        monkeypatch.setattr(app, "validate_setpoints",
                            _trip("validate_setpoints", None))
    if hasattr(app, "fetch_today_decision"):
        monkeypatch.setattr(app, "fetch_today_decision",
                            _trip("fetch_today_decision", "NORMAL"))
    if hasattr(app, "schedule_for"):
        monkeypatch.setattr(app, "schedule_for",
                            _trip("schedule_for", []))

    _stub_layer_eval_io(monkeypatch, price_cents=5.0)  # normal tier

    captured: dict[str, Any] = {}

    async def _exec(c4, action, cool, heat, snapshot, dry_run, *, when_ct=None, hold_until=None):
        captured["cool"] = cool
        captured["label"] = action.label
        return (False, None)  # shadow: not applied, no error
    monkeypatch.setattr(app, "execute_action", AsyncMock(side_effect=_exec))

    write_action_mock = MagicMock()
    monkeypatch.setattr(app, "write_action", write_action_mock)
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 74.0,
                            "hvac_mode": "Cool",
                            "cool_setpoint_f": 75,
                            "heat_setpoint_f": 65,
                        }))

    firing = FiringState()
    # 13:30 is inside the 13:00-19:00 block (cool=78 in _A2_PROGRAM_F).
    now_local = datetime(2026, 7, 15, 13, 30, tzinfo=ZoneInfo("America/Chicago"))

    cfg = _make_cfg_with_controller_config()
    c4, _climate = _mock_c4_client()

    asyncio.run(app.run_schedule_check(
        cfg, c4, MagicMock(), MagicMock(),
        ZoneInfo(cfg.tz_name), now_local, firing,
    ))

    # Baseline for 13:30 is 78 (the 13:00-19:00 block). Effective == baseline,
    # floor-clamped (never below baseline).
    assert firing.last_schedule_cool == 78.0
    assert captured.get("cool") == 78.0, (
        f"effective must equal the comfort baseline 78, got {captured.get('cool')}"
    )

    # A shadow hvac.actions row was written.
    assert write_action_mock.call_count >= 1

    # No supervisor fields in any write_action call.
    for c in write_action_mock.call_args_list:
        assert "supervisor_decision" not in c.kwargs
        assert "supervisor_reason" not in c.kwargs

    # Day-type resolution + supervisor were NOT consulted.
    assert called["validate_setpoints"] == 0, "validate_setpoints must not be called"
    assert called["fetch_today_decision"] == 0, "fetch_today_decision must not be called"
    assert called["schedule_for"] == 0, "schedule_for must not be called"


def test_a4_floor_clamp_holds_effective_at_or_above_baseline(monkeypatch):
    """A4 floor invariant: across the day's comfort blocks the effective cool
    setpoint is never below the active block's baseline."""
    import asyncio

    _stub_layer_eval_io(monkeypatch, price_cents=5.0)
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(False, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 74.0, "hvac_mode": "Cool",
                            "cool_setpoint_f": 75, "heat_setpoint_f": 65,
                        }))

    cfg = _make_cfg_with_controller_config()
    c4, _climate = _mock_c4_client()

    for hh, expected in [(2, 73.0), (10, 75.0), (15, 78.0), (20, 75.0)]:
        firing = FiringState()
        now_local = datetime(2026, 7, 15, hh, 30,
                             tzinfo=ZoneInfo("America/Chicago"))
        asyncio.run(app.run_schedule_check(
            cfg, c4, MagicMock(), MagicMock(),
            ZoneInfo(cfg.tz_name), now_local, firing,
        ))
        assert firing.last_schedule_cool == expected
        # Floor invariant: whatever the loop pushed, it is >= baseline.
        if firing.last_pushed_effective_cool is not None:
            assert firing.last_pushed_effective_cool >= expected


def test_a4_required_feeds_excludes_weather():
    """A4: required_feeds_for_arm_mode is derived from the enabled-mode set
    and no longer includes weather (no enabled mode consumes it)."""
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 7, 15, 13, 0),
        price_feed_healthy=True,
    )
    assert "weather" not in feeds
    assert feeds == {"price": True}


def test_a4_heat_setpoint_comes_from_config_heat_floor(monkeypatch):
    """A4 Fix 1: heat setpoint pushed to execute_action / write_action comes
    from cfg.controller_config.heat_floor, not the hardcoded HEAT_SETPOINT_FLOOR.

    Tests both the F case (heat_floor=65.0) and the C case (heat_floor=18.5).
    Under a Celsius config, pushing 65 would be a unit-correctness bug (65°C
    is above the cool setpoint); 18.5 is the correct value.
    """
    import asyncio

    def _run_one(heat_floor: float) -> dict[str, Any]:
        _stub_layer_eval_io(monkeypatch, price_cents=5.0)
        monkeypatch.setattr(app, "read_thermostat_snapshot",
                            AsyncMock(return_value={
                                "indoor_temp_f": 74.0, "hvac_mode": "Cool",
                                "cool_setpoint_f": 75, "heat_setpoint_f": 65,
                            }))

        captured: dict[str, Any] = {}

        async def _exec(c4, action, cool, heat, snapshot, dry_run, *, when_ct=None, hold_until=None):
            captured["cool"] = cool
            captured["heat"] = heat
            return (False, None)
        monkeypatch.setattr(app, "execute_action", AsyncMock(side_effect=_exec))

        write_calls: list[Any] = []

        def _write(*args: Any, **kwargs: Any) -> None:
            write_calls.append(args)
        monkeypatch.setattr(app, "write_action", _write)

        cfg = _make_cfg_with_controller_config()
        cfg.controller_config = _make_controller_config_stub(heat_floor=heat_floor)
        c4, _ = _mock_c4_client()

        firing = FiringState()
        now_local = datetime(2026, 7, 15, 13, 30, tzinfo=ZoneInfo("America/Chicago"))
        asyncio.run(app.run_schedule_check(
            cfg, c4, MagicMock(), MagicMock(),
            ZoneInfo(cfg.tz_name), now_local, firing,
        ))
        captured["write_calls"] = write_calls
        return captured

    # F case: heat_floor=65.0 → pushed heat must be 65.0
    result_f = _run_one(heat_floor=65.0)
    assert result_f.get("heat") == 65.0, (
        f"F-config: expected heat 65.0, got {result_f.get('heat')}"
    )

    # C case: heat_floor=18.5 → pushed heat must be 18.5, NOT 65
    result_c = _run_one(heat_floor=18.5)
    assert result_c.get("heat") == 18.5, (
        f"C-config: expected heat 18.5, got {result_c.get('heat')} "
        f"(65 would be a unit-correctness bug — 65°C above the cool setpoint)"
    )
    # Sanity: the cool (effective) is still the comfort baseline 78
    assert result_c.get("cool") == 78.0, (
        f"C-config: effective cool should be 78, got {result_c.get('cool')}"
    )


def test_a4_floor_clamp_is_max_effective_baseline(monkeypatch):
    """A4 Fix 2: the floor clamp is structurally max(effective_cool, baseline)
    rather than a self-comparison no-op.  In Slice A, effective_cool starts as
    the baseline, so the clamp is still baseline-valued — but the code path is
    correct and Slice B will inherit a working invariant.

    This test verifies: last_pushed_effective_cool == baseline (the clamp did
    not drop below baseline; in Slice A it equals baseline exactly).
    A clamp test with teeth (effective < baseline before clamping) comes in
    Slice B when the price offset is introduced.
    """
    import asyncio

    _stub_layer_eval_io(monkeypatch, price_cents=5.0)
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 74.0, "hvac_mode": "Cool",
                            "cool_setpoint_f": 75, "heat_setpoint_f": 65,
                        }))
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(False, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())

    cfg = _make_cfg_with_controller_config()
    c4, _ = _mock_c4_client()

    firing = FiringState()
    now_local = datetime(2026, 7, 15, 13, 30, tzinfo=ZoneInfo("America/Chicago"))
    asyncio.run(app.run_schedule_check(
        cfg, c4, MagicMock(), MagicMock(),
        ZoneInfo(cfg.tz_name), now_local, firing,
    ))

    baseline = 78.0  # 13:00-19:00 block in _A2_PROGRAM_F
    assert firing.last_schedule_cool == baseline
    # Slice A: effective == baseline (clamp is no-op with no offset).
    assert firing.last_pushed_effective_cool == baseline, (
        f"effective must equal baseline {baseline}, "
        f"got {firing.last_pushed_effective_cool}"
    )
    # Floor invariant holds: effective is never below baseline.
    assert firing.last_pushed_effective_cool >= baseline


# ---- C1: humidity-release guard (spec §"Guards") ----------------------------
#
# The guard is an EFFECTIVE-layer override applied in _push_baseline_if_changed.
# When indoor RH crosses rh_max_pct (or is missing) the warm overlay is gated
# OFF — the effective falls back to the comfort baseline — overriding both the
# tier offset AND the spike-hold min-hold. Re-enables only below rh_clear_pct
# (hysteresis band holds the prior state). Never below baseline; no DEHUM. The
# price-overlay state machine is untouched (temp rides; humidity releases).
# Stub config: rh_max_pct=65, rh_clear_pct=62, warm_band=2, spike_extra=2,
# comfort_max=85; midday baseline 78.

_C1_MIDDAY = datetime(2026, 7, 15, 13, 30, tzinfo=ZoneInfo("America/Chicago"))


def _run_push_with_gate(
    monkeypatch: Any,
    *,
    tier: str,
    humidity: float | None,
    firing: FiringState | None = None,
) -> tuple[float | None, dict[str, Any], FiringState]:
    """Drive one _push_baseline_if_changed call at the given active tier with
    the given indoor humidity. Returns (pushed_effective, write_action_kwargs,
    firing). The snapshot's ``humidity`` key is omitted entirely when
    ``humidity is None`` (simulates a missing reading)."""
    import asyncio

    captured: dict[str, Any] = {}

    async def _exec(c4, action, cool, heat, snapshot, dry_run, *, when_ct=None, hold_until=None):
        captured["cool"] = cool
        return (False, None)  # shadow
    monkeypatch.setattr(app, "execute_action", AsyncMock(side_effect=_exec))

    wa_kwargs: dict[str, Any] = {}

    def _write(*args: Any, **kwargs: Any) -> None:
        wa_kwargs.update(kwargs)
        wa_kwargs["_cool_applied"] = args[3]  # cool_applied is positional arg 3
        wa_kwargs["_baseline_cool"] = kwargs["baseline_cool"]
    monkeypatch.setattr(app, "write_action", _write)

    snap: dict[str, Any] = {
        "indoor_temp_f": 74.0, "hvac_mode": "Cool",
        "cool_setpoint_f": 75, "heat_setpoint_f": 65,
    }
    if humidity is not None:
        snap["humidity"] = humidity
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value=snap))

    cfg = _make_cfg_with_controller_config()
    c4, _ = _mock_c4_client()
    if firing is None:
        firing = FiringState()
    # Pin the per-tick baseline (the push reads firing.last_schedule_cool).
    firing.last_schedule_cool = 78.0

    asyncio.run(app._push_baseline_if_changed(
        cfg, c4, MagicMock(), firing, _C1_MIDDAY, tier,
    ))
    return captured.get("cool"), wa_kwargs, firing


def test_c1_gate_engages_at_rh_max_overrides_active_tier(monkeypatch):
    """RH >= rh_max_pct (65) gates the overlay OFF even on an active extreme
    tier: the effective drops to the baseline (78), not the ceiling (85)."""
    cool, _wa, firing = _run_push_with_gate(
        monkeypatch, tier="extreme", humidity=65.0)
    assert cool == 78.0, f"gated effective must be baseline 78, got {cool}"
    assert firing.overlay_humidity_gated is True


def test_c1_gate_does_not_engage_in_dry_air(monkeypatch):
    """RH below rh_clear_pct: the overlay rides — extreme snaps to the ceiling
    (85), the gate stays disengaged."""
    cool, _wa, firing = _run_push_with_gate(
        monkeypatch, tier="extreme", humidity=45.0)
    assert cool == 85.0, f"dry air must ride the ceiling 85, got {cool}"
    assert firing.overlay_humidity_gated is False


def test_c1_hysteresis_band_holds_prior_state(monkeypatch):
    """A reading inside the band (rh_clear=62 <= RH < rh_max=65) keeps the
    PRIOR gate state — it neither engages nor releases on its own."""
    # Prior gated=True + a band reading -> still gated (effective stays baseline).
    f_gated = FiringState(overlay_humidity_gated=True)
    cool_g, _wa_g, fg = _run_push_with_gate(
        monkeypatch, tier="extreme", humidity=63.0, firing=f_gated)
    assert fg.overlay_humidity_gated is True
    assert cool_g == 78.0, "band reading must HOLD the prior gated state"

    # Prior gated=False + a band reading -> still not gated (effective rides).
    f_open = FiringState(overlay_humidity_gated=False)
    cool_o, _wa_o, fo = _run_push_with_gate(
        monkeypatch, tier="extreme", humidity=63.0, firing=f_open)
    assert fo.overlay_humidity_gated is False
    assert cool_o == 85.0, "band reading must HOLD the prior un-gated state"


def test_c1_reenables_only_below_rh_clear(monkeypatch):
    """A gated controller re-enables the overlay only when RH drops strictly
    below rh_clear_pct (62) — not merely below rh_max (65)."""
    # Was gated; RH now 61 (< 62 clear) -> release: rides to the ceiling again.
    f = FiringState(overlay_humidity_gated=True)
    cool, _wa, firing = _run_push_with_gate(
        monkeypatch, tier="extreme", humidity=61.0, firing=f)
    assert firing.overlay_humidity_gated is False
    assert cool == 85.0, f"below rh_clear must re-enable the overlay, got {cool}"


def test_c1_missing_humidity_is_conservative_gated(monkeypatch):
    """Missing humidity -> treat as humid -> gated (effective = baseline)."""
    cool, _wa, firing = _run_push_with_gate(
        monkeypatch, tier="extreme", humidity=None)
    assert firing.overlay_humidity_gated is True
    assert cool == 78.0, f"missing humidity must gate to baseline, got {cool}"


def test_c1_gate_overrides_spike_hold_min_hold(monkeypatch):
    """The humidity release is immediate: a gated tick mid-min-hold (the tier
    was triggered seconds ago, well inside the hold window) still drops the
    effective to baseline. The gate overrides the spike-hold min-hold."""
    from .price_overlay import PriceOverlayState
    f = FiringState(
        price_overlay_state=PriceOverlayState(
            current_tier="extreme",
            triggered_at_utc=_C1_MIDDAY.astimezone(timezone.utc),  # just fired
        ),
    )
    cool, _wa, firing = _run_push_with_gate(
        monkeypatch, tier="extreme", humidity=70.0, firing=f)
    assert cool == 78.0, "gate must override the min-hold and drop to baseline"
    # The price-overlay state machine is UNTOUCHED — the tier keeps tracking
    # price underneath (temp rides; humidity releases).
    assert firing.price_overlay_state.current_tier == "extreme"


def test_c1_never_below_baseline_when_gated(monkeypatch):
    """When gated the effective is exactly the baseline — never below it
    (warm-only floor invariant; no DEHUM)."""
    _cool, wa, _firing = _run_push_with_gate(
        monkeypatch, tier="scarcity", humidity=80.0)
    assert wa["_cool_applied"] == wa["_baseline_cool"]
    assert wa["_cool_applied"] >= wa["_baseline_cool"]


def test_c1_gated_action_row_telemetry(monkeypatch):
    """A gated tick's hvac.actions row shows humidity_gated=1, drift=0, and
    commanded_cool == baseline_cool (the gate is unambiguously observable)."""
    captured = _capture_write_point(monkeypatch)
    import asyncio

    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(False, None)))
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 79.0, "hvac_mode": "Cool",
                            "cool_setpoint_f": 78.0, "heat_setpoint_f": 65.0,
                            "humidity": 70.0,  # >= rh_max -> gated
                        }))
    cfg = _make_cfg_with_controller_config()
    c4, _ = _mock_c4_client()
    firing = FiringState()
    firing.last_schedule_cool = 78.0
    asyncio.run(app._push_baseline_if_changed(
        cfg, c4, MagicMock(), firing, _C1_MIDDAY, "extreme",
    ))

    rows = [r for r in captured if r["measurement"] == "hvac.actions"]
    assert rows, "expected an hvac.actions row"
    f = rows[-1]["fields"]
    assert f["humidity_gated"] == 1
    assert f["drift"] == 0.0
    assert f["commanded_cool"] == f["baseline_cool"] == 78.0


def test_c1_ungated_action_row_humidity_gated_zero(monkeypatch):
    """A normal (un-gated) spike tick's row carries humidity_gated=0."""
    captured = _capture_write_point(monkeypatch)
    import asyncio

    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(False, None)))
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 79.0, "hvac_mode": "Cool",
                            "cool_setpoint_f": 78.0, "heat_setpoint_f": 65.0,
                            "humidity": 45.0,  # dry -> not gated
                        }))
    cfg = _make_cfg_with_controller_config()
    c4, _ = _mock_c4_client()
    firing = FiringState()
    firing.last_schedule_cool = 78.0
    asyncio.run(app._push_baseline_if_changed(
        cfg, c4, MagicMock(), firing, _C1_MIDDAY, "scarcity",
    ))

    rows = [r for r in captured if r["measurement"] == "hvac.actions"]
    assert rows, "expected an hvac.actions row"
    f = rows[-1]["fields"]
    assert f["humidity_gated"] == 0


# ---------------------------------------------------------------------------
# Timed-hold refresh — _push_baseline_if_changed (spec Safety #2)
# ---------------------------------------------------------------------------
# An applied production push records the device-side hold expiry; while the
# effective is unchanged, the push re-fires only inside the refresh margin
# so an alive controller keeps its hold and a dead one lapses to the onboard
# schedule. Shadow never tracks expiry (behavior byte-identical to today).

_TZ_CT = ZoneInfo("America/Chicago")
_PUSH_MIDDAY = datetime(2026, 7, 15, 13, 30, tzinfo=_TZ_CT)  # midday block, baseline 78


def _push_rig(monkeypatch: Any, *, dry_run: bool,
              exec_result: tuple[bool, str | None] = (True, None)) -> tuple[Any, Any, FiringState, dict[str, Any]]:
    """Rig for driving _push_baseline_if_changed directly: production or
    shadow cfg, execute_action captured (never a real device), IO stubbed."""
    captured: dict[str, Any] = {"calls": 0}

    async def _exec(c4: Any, action: Any, cool: Any, heat: Any, snapshot: Any,
                    dry_run_flag: Any, *, when_ct: Any = None,
                    hold_until: Any = None) -> tuple[bool, str | None]:
        captured["calls"] += 1
        captured["cool"] = cool
        captured["hold_until"] = hold_until
        if dry_run_flag:
            return (False, None)
        return exec_result

    monkeypatch.setattr(app, "execute_action", AsyncMock(side_effect=_exec))
    monkeypatch.setattr(app, "write_action", MagicMock())
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 74.0, "humidity": 45.0,
                            "hvac_mode": "Cool",
                            "cool_setpoint_f": 75, "heat_setpoint_f": 65,
                        }))

    cfg = _make_cfg_with_controller_config(dry_run=dry_run)
    c4, _climate = _mock_c4_client()
    firing = FiringState()
    firing.last_schedule_cool = 78.0
    return cfg, c4, firing, captured


async def test_push_production_passes_hold_until_and_records_expiry(monkeypatch):
    """An applied production push sends a quarter-hour hold expiry to
    execute_action (13:30 + ttl 30 -> 14:00) and records it on FiringState."""
    cfg, c4, firing, cap = _push_rig(monkeypatch, dry_run=False)

    await app._push_baseline_if_changed(cfg, c4, MagicMock(), firing,
                                        _PUSH_MIDDAY, "extreme", tick_id="t1")

    assert cap["calls"] == 1
    assert cap["hold_until"] == dtime(14, 0)
    assert firing.hold_expires_at == _PUSH_MIDDAY.replace(minute=0, hour=14)


async def test_push_shadow_never_tracks_hold_expiry(monkeypatch):
    """Shadow pushes stay exactly as today: audit row + log, no expiry state,
    so the refresh rule can never fire in shadow."""
    cfg, c4, firing, cap = _push_rig(monkeypatch, dry_run=True)

    await app._push_baseline_if_changed(cfg, c4, MagicMock(), firing,
                                        _PUSH_MIDDAY, "extreme", tick_id="t1")

    assert cap["calls"] == 1  # the would-push is still audited
    assert firing.hold_expires_at is None


async def test_push_refreshes_near_expiry_without_effective_change(monkeypatch):
    """Unchanged effective + hold inside the refresh margin -> re-push (the
    alive-controller refresh that keeps the timed hold from lapsing)."""
    cfg, c4, firing, cap = _push_rig(monkeypatch, dry_run=False)
    await app._push_baseline_if_changed(cfg, c4, MagicMock(), firing,
                                        _PUSH_MIDDAY, "extreme", tick_id="t1")
    assert cap["calls"] == 1  # initial push at 13:30, expiry 14:00

    near_expiry = _PUSH_MIDDAY.replace(minute=58)  # 13:58, 2 min out
    await app._push_baseline_if_changed(cfg, c4, MagicMock(), firing,
                                        near_expiry, "extreme", tick_id="t2")

    assert cap["calls"] == 2
    assert cap["hold_until"] == dtime(14, 15)  # 13:58 + 30 -> floor 14:15


async def test_push_short_circuit_intact_far_from_expiry(monkeypatch):
    """Unchanged effective + hold far from expiry -> no re-push (the ride-the-
    ceiling short-circuit keeps its no-thrash behavior)."""
    cfg, c4, firing, cap = _push_rig(monkeypatch, dry_run=False)
    await app._push_baseline_if_changed(cfg, c4, MagicMock(), firing,
                                        _PUSH_MIDDAY, "extreme", tick_id="t1")
    assert cap["calls"] == 1

    next_tick = _PUSH_MIDDAY.replace(minute=35)  # 13:35, expiry 14:00 is 25 min out
    await app._push_baseline_if_changed(cfg, c4, MagicMock(), firing,
                                        next_tick, "extreme", tick_id="t2")

    assert cap["calls"] == 1  # short-circuited


async def test_push_failed_write_does_not_record_expiry(monkeypatch):
    """A failed live push must not record an expiry (mirrors the existing
    guard rule: a failed push must not pretend the device took the value)."""
    cfg, c4, firing, cap = _push_rig(monkeypatch, dry_run=False,
                                     exec_result=(False, "TimeoutError: boom"))

    await app._push_baseline_if_changed(cfg, c4, MagicMock(), firing,
                                        _PUSH_MIDDAY, "extreme", tick_id="t1")

    assert cap["calls"] == 1
    assert firing.hold_expires_at is None
