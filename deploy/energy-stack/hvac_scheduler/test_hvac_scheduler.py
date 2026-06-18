"""Tests for the HVAC scheduler — focused on pure-logic functions and the
release_hold action plumbing.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from . import app


@pytest.fixture(autouse=True)
def _stub_pjm_inputs(monkeypatch):
    """Default §7 PJM input fetch to (None, 130000.0) so the
    decide_day_type callers don't try to query a MagicMock InfluxDB
    every test. Tests that exercise the §7 escalation path override
    this with explicit values."""
    monkeypatch.setattr(
        app, "_fetch_pjm_inputs_for_target_date",
        lambda query_api, bucket, target_date_iso, tz: (None, 130000.0),
    )
from .app import (
    COOL_SHUTOFF_F,
    DAYTYPE_HOT,
    DAYTYPE_MILD,
    DAYTYPE_NORMAL,
    FiringState,
    LayerResolution,
    MILD_SCHEDULE,
    NORMAL_SCHEDULE,
    HOT_SCHEDULE,
    HOT_STREAK_DAY1_SCHEDULE,
    ScheduleAction,
    _classify_one_day,
    _evaluate_layer_inputs,
    _push_layer_change_mid_period,
    decide_day_type,
    execute_action,
    fetch_day_ahead_prices_for_date,
    fetch_today_decision,
    action_in_effect_at,
    merge_same_hour_actions_deepest_wins,
    precool_window_action,
    resolve_cool_setpoint,
    resolve_layer_priority,
    run_decision_revisit,
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


# ---- Layer priority resolution (§4) ---------------------------------------


def test_resolve_layer_priority_no_overlay_no_5cp_returns_schedule_unchanged():
    """The default passthrough: nothing fires, schedule baseline wins.
    Same shape that runs while §2 (price overlay) and §3 (5CP) modules
    haven't wired in yet."""
    r = resolve_layer_priority(schedule_cool_f=79)
    assert r.effective_cool_f == 79
    assert r.schedule_cool_f == 79
    assert r.price_cool_f == 79
    assert r.fivecp_cool_f == 79
    assert r.price_overlay_tier == "normal"
    assert r.fivecp_active is False


def test_resolve_layer_priority_elevated_offset_pulls_setpoint_warmer():
    """Spec §4 case 2: schedule 79°F, elevated price (+3°F offset)
    -> effective 82°F."""
    r = resolve_layer_priority(
        schedule_cool_f=79,
        price_overlay_tier="elevated",
        price_offset_f=3,
    )
    assert r.effective_cool_f == 82
    assert r.price_cool_f == 82


def test_resolve_layer_priority_scarcity_override_replaces_schedule():
    """Spec §4 case 3: schedule 73°F (sleep), scarcity tier (override
    to 85°F) -> effective 85°F (override wins, far warmer than 73°F+offset)."""
    r = resolve_layer_priority(
        schedule_cool_f=73,
        price_overlay_tier="scarcity",
        price_override_f=85,
    )
    assert r.effective_cool_f == 85
    assert r.price_cool_f == 85


def test_fivecp_active_does_not_change_effective_cool_f():
    """Binding spec §11 #14: 5CP is demoted from live-setpoint authority
    to planning/telemetry only. A bare ``fivecp_active=True`` with normal
    prices does NOT raise the effective cool setpoint above the schedule
    baseline — that's the core behavior change. Telemetry fields
    (``fivecp_active``, ``fivecp_cool_f``) are preserved on the
    LayerResolution so post-hoc analysis can reconstruct when 5CP would
    have fired."""
    r = resolve_layer_priority(
        schedule_cool_f=80,
        fivecp_active=True,
    )
    # Effective stays at schedule baseline; 5CP does NOT push to 85F.
    assert r.effective_cool_f == 80
    # Telemetry preserved: fivecp_active is still True, fivecp_cool_f
    # records what 5CP WOULD have proposed (85F) — but it didn't win.
    assert r.fivecp_active is True
    assert r.fivecp_cool_f == COOL_SHUTOFF_F


def test_scarcity_still_overrides_when_fivecp_active():
    """Severe price events still drive shutoff after the §11 #14 demotion:
    scarcity tier (>= 20¢/kWh) overrides to 85°F via the price overlay,
    independent of 5CP. With both scarcity and 5CP active, effective is
    85°F from the PRICE overlay (the only live-authority layer at the
    warm end); 5CP does not contribute."""
    r = resolve_layer_priority(
        schedule_cool_f=80,
        price_overlay_tier="scarcity",
        price_override_f=85,
        fivecp_active=True,
    )
    assert r.effective_cool_f == 85
    assert r.price_cool_f == 85  # price overlay is the one that pushed it


def test_resolve_layer_priority_precool_with_elevated_pushes_to_71_not_85():
    """Spec §4 case 6: schedule 68°F (HOT pre-cool), elevated price at 4am
    (+3°F offset) -> effective 71°F. Importantly, elevated tier does NOT
    blow pre-cool to 85°F; that requires the scarcity override or 5CP."""
    r = resolve_layer_priority(
        schedule_cool_f=68,
        price_overlay_tier="elevated",
        price_offset_f=3,
    )
    assert r.effective_cool_f == 71


def test_resolve_layer_priority_warmer_wins_when_offset_below_baseline():
    """Defensive: a buggy negative offset must not make the house cooler
    than the schedule intended. ``effective = max(schedule, price, ...)``
    enforces 'warmer wins' even if the price layer mis-computes."""
    r = resolve_layer_priority(
        schedule_cool_f=79,
        price_overlay_tier="elevated",
        price_offset_f=-5,
    )
    assert r.effective_cool_f == 79  # schedule baseline still wins


def test_resolve_layer_priority_returns_layer_resolution_dataclass():
    """Caller-facing contract: the return value is a LayerResolution with
    fields that map 1:1 onto the new hvac.actions audit fields."""
    r = resolve_layer_priority(schedule_cool_f=78)
    assert isinstance(r, LayerResolution)


# ---- Day-type classifier (recalibrated thresholds, EXPERIMENT_DESIGN App. A)


def test_classify_high_88_apparent_88_is_hot():
    """Pre-§1 this would be NORMAL (82-94F band); under the recalibrated
    thresholds 88F crosses the HOT >=85F line and triggers HOT."""
    assert _classify_one_day({"high_f": 88.0, "apparent_max_f": 88.0,
                              "is_heat_advisory": 0}) == DAYTYPE_HOT


def test_classify_high_82_apparent_92_is_hot():
    """Apparent-temperature path: dry-bulb is below the 85F floor but
    apparent crosses 90F (humidity-driven), so HOT triggers regardless."""
    assert _classify_one_day({"high_f": 82.0, "apparent_max_f": 92.0,
                              "is_heat_advisory": 0}) == DAYTYPE_HOT


def test_classify_high_84_no_advisory_no_apparent_is_normal():
    """Edge case: 84F is on the inside of NORMAL (75-85F band); 85F would
    flip to HOT. Without apparent_max_f or heat advisory, stays NORMAL."""
    assert _classify_one_day({"high_f": 84.0,
                              "is_heat_advisory": 0}) == DAYTYPE_NORMAL


def test_classify_high_84_with_apparent_88_stays_normal():
    """Apparent below the 90F threshold doesn't bump 84F dry-bulb to HOT;
    the apparent path requires apparent_max_f >= 90F."""
    assert _classify_one_day({"high_f": 84.0, "apparent_max_f": 88.0,
                              "is_heat_advisory": 0}) == DAYTYPE_NORMAL


def test_classify_high_76_apparent_88_is_normal():
    """76F dry-bulb stays in the 75-85F NORMAL band; apparent 88F is below
    the 90F apparent threshold so no HOT trigger."""
    assert _classify_one_day({"high_f": 76.0, "apparent_max_f": 88.0,
                              "is_heat_advisory": 0}) == DAYTYPE_NORMAL


def test_classify_high_70_is_mild():
    """Below the 75F NORMAL threshold -> MILD."""
    assert _classify_one_day({"high_f": 70.0,
                              "is_heat_advisory": 0}) == DAYTYPE_MILD


def test_classify_heat_advisory_overrides_temp():
    """Even at 70F, an active heat advisory (sustained high-humidity event)
    is treated as HOT for safety. Behaviour preserved from pre-§1."""
    assert _classify_one_day({"high_f": 70.0, "is_heat_advisory": 1}) == DAYTYPE_HOT


def test_classify_apparent_alone_can_trigger_hot():
    """If high_f is missing entirely (degraded fixture) but apparent_max_f
    is >= 90F, HOT still triggers — graceful behaviour when one variable
    drops out of the upstream forecast."""
    assert _classify_one_day({"apparent_max_f": 91.0,
                              "is_heat_advisory": 0}) == DAYTYPE_HOT


def test_classify_partial_forecast_no_temps_falls_back_to_normal(capsys):
    """P2.7 regression: a forecast row that's present but missing both
    high_f and apparent_max_f (degraded NWS parse / API failure)
    previously fell through to DAYTYPE_MILD, whose schedule has no
    pre-cool. That under-protects an actually-hot day. Now falls back
    to DAYTYPE_NORMAL with a warn log so the standard pre-cool/coast
    schedule still runs."""
    forecast = {
        "period_date": "2026-07-15",
        "is_heat_advisory": 0,
        "alert_summary": "",
        # No high_f, no apparent_max_f — degraded parse path
    }
    assert _classify_one_day(forecast) == DAYTYPE_NORMAL
    captured = capsys.readouterr().out
    assert "forecast_no_temperature_fields_falling_back_to_normal" in captured


def test_classify_empty_forecast_dict_is_normal_not_mild():
    """An empty dict (vs None) is treated like None: missing forecast,
    NORMAL fallback. Tests the dict-empty edge case along with the
    None case both falling into the safe NORMAL bucket."""
    # Empty dict is falsy in Python so the `if not forecast` short-circuit
    # handles it the same as None.
    assert _classify_one_day({}) == DAYTYPE_NORMAL


def test_decide_day_type_carries_apparent_in_reasons():
    """Per §1 the reasons dict surfaces apparent_max_f for audit so the
    hvac.decisions InfluxDB row records which threshold fired."""
    day_type, reasons = decide_day_type(
        {"high_f": 82.0, "apparent_max_f": 92.0, "is_heat_advisory": 0}
    )
    assert day_type == DAYTYPE_HOT
    assert reasons["apparent_max_f"] == 92.0
    assert reasons["reason"].startswith("apparent_ge_")


def test_decide_day_type_escalates_to_streak_on_single_day_5cp_risk():
    """§7 single-day path: tomorrow HOT (95F), tomorrow's PJM peak forecast
    >5% above season-to-date 5th highest. Even without a multi-day heat
    streak, escalate to HOT_STREAK_DAY1 to bank deeper thermal mass before
    the grid-stress hour."""
    from .app import DAYTYPE_HOT_STREAK_DAY1
    day_type, reasons = decide_day_type(
        {"high_f": 95.0, "is_heat_advisory": 0},
        day2_forecast={"high_f": 80.0, "is_heat_advisory": 0},
        tomorrow_peak_load_mw=145000,
        season_5th_highest_mw=130000,
    )
    assert day_type == DAYTYPE_HOT_STREAK_DAY1
    assert reasons["reason"] == "forecast_5cp_risk_single_day"


def test_decide_day_type_no_streak_when_pjm_inputs_absent():
    """§7 escalation requires both PJM inputs. If either is None (e.g.,
    pre-season tick), fall back to plain HOT classification."""
    day_type, _ = decide_day_type(
        {"high_f": 95.0, "is_heat_advisory": 0},
        day2_forecast={"high_f": 80.0, "is_heat_advisory": 0},
        tomorrow_peak_load_mw=None,
        season_5th_highest_mw=130000,
    )
    assert day_type == DAYTYPE_HOT


def test_decide_day_type_tape_distinguishes_insufficient_baseline_vs_missing_forecast():
    """Binding spec §11 #14: when the §7 forecast 5CP-risk path falls
    back, the evaluation_tape must record WHY — distinguishing the
    'insufficient_current_season_history' case from 'missing_pjm_forecast'
    so the audit trail explains the non-fire reason without spelunking."""
    # season_5th=None (baseline insufficient) -> status records that
    _, reasons = decide_day_type(
        {"high_f": 95.0, "is_heat_advisory": 0},
        day2_forecast={"high_f": 80.0, "is_heat_advisory": 0},
        tomorrow_peak_load_mw=145000,
        season_5th_highest_mw=None,
    )
    risk_entry = next(
        e for e in reasons["evaluation_tape"] if e["rule"] == "streak_5cp_risk"
    )
    assert risk_entry["fired"] is False
    assert risk_entry["status"] == "insufficient_current_season_history"

    # tomorrow_peak=None (forecast missing) -> different status
    _, reasons = decide_day_type(
        {"high_f": 95.0, "is_heat_advisory": 0},
        day2_forecast={"high_f": 80.0, "is_heat_advisory": 0},
        tomorrow_peak_load_mw=None,
        season_5th_highest_mw=130000,
    )
    risk_entry = next(
        e for e in reasons["evaluation_tape"] if e["rule"] == "streak_5cp_risk"
    )
    assert risk_entry["fired"] is False
    assert risk_entry["status"] == "missing_pjm_forecast"

    # Both inputs present -> status="ok". HOT base_type so the §7
    # branch actually runs and produces a streak_5cp_risk tape entry.
    _, reasons = decide_day_type(
        {"high_f": 95.0, "is_heat_advisory": 0},
        day2_forecast={"high_f": 80.0, "is_heat_advisory": 0},
        tomorrow_peak_load_mw=145000,
        season_5th_highest_mw=130000,
    )
    risk_entry = next(
        e for e in reasons["evaluation_tape"] if e["rule"] == "streak_5cp_risk"
    )
    assert risk_entry["status"] == "ok"


# NB: P1 fix (cooling-season gate on _fetch_pjm_inputs_for_target_date)
# is not unit-tested here because the module-level autouse fixture
# `_stub_pjm_inputs` replaces that function for every test in this file,
# making direct calls return the stub's fixed value rather than exercising
# the real gate. `in_cooling_season()` already has full unit-test coverage
# in test_pjm_5cp.py, and the in-place gate is a one-liner visible in the
# diff. Adding a class-scoped fixture override here for one test would be
# more ceremony than the change warrants.


# ---- Wall-clock tick-phase alignment (main_async loop) -------------------


def test_scheduler_tick_target_lands_on_next_wall_clock_second():
    """Phase-alignment invariant (main_async loop): for any wall-clock
    ``now``, ``target = now.replace(second=SCHEDULER_TICK_SECOND,
    microsecond=0); if target <= now: target += 1 minute`` always
    lands on the next XX:XX:SCHEDULER_TICK_SECOND boundary in the
    future. Pins the math against accidental edits and against
    accidental drift of SCHEDULER_TICK_SECOND vs the poller's wall-
    clock :00 alignment."""
    from .app import SCHEDULER_TICK_SECOND
    tz = ZoneInfo("America/Chicago")
    base = datetime(2026, 7, 15, 14, 30, tzinfo=tz)
    # Same-minute "now" values spanning all 60 seconds + sub-second cases.
    test_seconds = [0, 1, 5, 9, SCHEDULER_TICK_SECOND - 1, SCHEDULER_TICK_SECOND,
                    SCHEDULER_TICK_SECOND + 1, 30, 50, 59]
    for sec in test_seconds:
        now = base.replace(second=sec)
        target = now.replace(second=SCHEDULER_TICK_SECOND, microsecond=0)
        if target <= now:
            target += timedelta(minutes=1)
        # Target lands on SCHEDULER_TICK_SECOND
        assert target.second == SCHEDULER_TICK_SECOND
        assert target.microsecond == 0
        # And is strictly in the future
        assert target > now
        # And within 60s of now (one cycle max)
        delta = (target - now).total_seconds()
        assert 0 < delta <= 60.0, f"sec={sec}: delta={delta}"

    # Microsecond-precision now: target must still be strictly after now.
    now = base.replace(second=SCHEDULER_TICK_SECOND, microsecond=1)
    target = now.replace(second=SCHEDULER_TICK_SECOND, microsecond=0)
    if target <= now:
        target += timedelta(minutes=1)
    assert target > now
    assert (target - now).total_seconds() <= 60.0


def test_scheduler_tick_second_is_after_poller_zero():
    """The whole point of the wall-clock phase alignment: scheduler
    ticks must fall AFTER the poller's :00 boundary so the scheduler
    reads the same minute's freshest ComEd bucket rather than the
    previous minute's. Pin SCHEDULER_TICK_SECOND > 0."""
    from .app import SCHEDULER_TICK_SECOND
    assert SCHEDULER_TICK_SECOND > 0
    # And below 60 (must be a valid second-of-minute).
    assert SCHEDULER_TICK_SECOND < 60


def test_decision_and_revisit_guards_dont_require_minute_zero():
    """Regression guard for the startup-after-HH:00:SCHEDULER_TICK_SECOND
    edge case: with the wall-clock :10 tick phase, a container starting
    at 21:00:30 has its first tick at 21:01:10. The decision_hour /
    revisit_hours guards MUST NOT require `now_local.minute == 0` —
    otherwise the day-ahead decision would be silently lost whenever
    the container starts between :10 and :59 of the decision hour.

    Source-inspection test (matches the existing pattern at
    test_health_marker_only_touched_when_tick_succeeds): asserts the
    `minute == 0` substring is absent from the decision/revisit
    branches AND that the guards themselves (`last_decision_date`,
    `fired_revisits`) are still referenced so once-per-day semantics
    remain intact."""
    import inspect
    main_src = inspect.getsource(app.main_async)
    # The decision branch must reference decision_hour and the
    # last_decision_date guard, but NOT the strict-minute check.
    decision_idx = main_src.index("cfg.decision_hour")
    decision_block_end = main_src.index("decision_failed")
    decision_branch = main_src[decision_idx:decision_block_end]
    assert "now_local.minute == 0" not in decision_branch
    assert "last_decision_date" in decision_branch

    # Same for the revisit branch.
    revisit_idx = main_src.index("cfg.revisit_hours")
    revisit_block_end = main_src.index("revisit_failed")
    revisit_branch = main_src[revisit_idx:revisit_block_end]
    assert "now_local.minute == 0" not in revisit_branch
    assert "fired_revisits" in revisit_branch


def test_decide_day_type_multi_day_streak_path_still_works():
    """Existing multi-day path (§7 added an alternative escalation path,
    didn't replace this one). When BOTH days HOT, still escalates to
    HOT_STREAK_DAY1 with the older reason string for backwards-compat."""
    from .app import DAYTYPE_HOT_STREAK_DAY1
    day_type, reasons = decide_day_type(
        {"high_f": 96.0, "is_heat_advisory": 0},
        day2_forecast={"high_f": 97.0, "is_heat_advisory": 0},
    )
    assert day_type == DAYTYPE_HOT_STREAK_DAY1
    assert reasons["reason"] == "hot_streak_starting"


# ---- ScheduleAction & schedules -------------------------------------------


def test_release_hold_action_has_no_setpoint():
    """release_hold actions don't carry a cool_setpoint_f — verifies the
    optional default."""
    a = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)
    assert a.cool_setpoint_f is None
    assert a.release_hold is True
    assert a.fan_mode is None


def test_mild_schedule_is_pi_owned():
    """MILD is now a real Pi-owned schedule (no release_hold) so the price
    overlay can actuate on mild days — this is the mild-full-controller fix."""
    assert [a.label for a in MILD_SCHEDULE] == [
        "MILD_MORNING", "MILD_DAY", "MILD_RECOVER", "SLEEP"]
    assert all(a.cool_setpoint_f is not None for a in MILD_SCHEDULE)
    assert all(a.release_hold is False for a in MILD_SCHEDULE)


def test_existing_schedules_still_have_setpoints():
    """Sanity check: the optional cool_setpoint_f default doesn't accidentally
    leave NORMAL/HOT actions setpoint-less."""
    for action in NORMAL_SCHEDULE + HOT_SCHEDULE:
        assert action.release_hold is False
        assert action.cool_setpoint_f is not None
        assert action.cool_setpoint_f > 0


def test_hot_schedules_do_not_carry_fixed_5cp_shutoff_window():
    """Prereg compliance (EXPERIMENT_DESIGN.md §3): the fixed 14:00-
    18:00 CT shutoff window is dropped from Arm B. The 85°F shutoff
    timing comes from dynamic layers (§2 price-scarcity tier + §3
    dual-scope 5CP detector), NOT from schedule entries.

    This test pins the schedule so a future regression (someone
    re-adding the hard 14:00 / 18:00 hardcoded actions) fails
    loudly. The dynamic layers still produce 85°F effective cool
    via 'warmer wins' priority when real conditions warrant -- the
    layer evaluation is exercised separately by the dual-scope and
    price-overlay test families."""
    dropped_labels = {"HOT_5CP_SHUTOFF", "HOT_RECOVER_LOW"}
    hot_labels = {a.label for a in HOT_SCHEDULE}
    streak_labels = {a.label for a in HOT_STREAK_DAY1_SCHEDULE}
    assert not (hot_labels & dropped_labels), (
        f"HOT_SCHEDULE still carries fixed-window actions: "
        f"{hot_labels & dropped_labels}"
    )
    assert not (streak_labels & dropped_labels), (
        f"HOT_STREAK_DAY1_SCHEDULE still carries fixed-window actions: "
        f"{streak_labels & dropped_labels}"
    )
    # No schedule entry should pin cool >= 85 on a HOT day -- those
    # are dynamic-layer territory now.
    for a in HOT_SCHEDULE + HOT_STREAK_DAY1_SCHEDULE:
        if a.cool_setpoint_f is not None:
            assert a.cool_setpoint_f < 85, (
                f"HOT-day schedule action {a.label} pins cool="
                f"{a.cool_setpoint_f}; that's the dynamic-layer "
                f"shutoff range and should not appear in the locked "
                f"schedule baseline."
            )


# ---- resolve_cool_setpoint ------------------------------------------------


def test_resolve_cool_setpoint_release_hold_returns_sentinel():
    a = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)
    setpoint, reason = resolve_cool_setpoint(a, today_dewpoint_f=60.0)
    assert setpoint == 0
    assert reason == "release_hold"


def test_resolve_cool_setpoint_humid_override_unchanged_for_setpoint_actions():
    """The humid-override path must still work for non-release actions."""
    a = ScheduleAction(13, 0, "COAST", cool_setpoint_f=79, cool_setpoint_humid_f=75)
    setpoint, reason = resolve_cool_setpoint(a, today_dewpoint_f=70.0)
    assert setpoint == 75
    assert "humid_override" in reason


def test_resolve_cool_setpoint_standard_path_unchanged():
    a = ScheduleAction(13, 0, "COAST", cool_setpoint_f=79)
    setpoint, reason = resolve_cool_setpoint(a, today_dewpoint_f=60.0)
    assert setpoint == 79
    assert reason == "standard"


# ---- execute_action: release-hold path ------------------------------------


def _mock_c4_client() -> tuple[MagicMock, MagicMock]:
    """Build a C4Client mock whose call_with_reauth simply awaits the supplied
    callable. Captures the chain so the test can assert what was called."""
    c4 = MagicMock()

    climate = MagicMock()
    climate.set_hold_mode = AsyncMock()
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


async def test_execute_setpoint_action_still_pins_permanent_hold():
    """The original behavior — set setpoints + Permanent hold — must remain
    intact for non-release actions."""
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint_f=79,
                             fan_mode="Circulate")

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=79, heat_setpoint_to_apply=60,
                                           state={"hvac_mode": "Cool"}, dry_run=False)
    assert applied is True
    assert error is None
    climate.set_cool_setpoint_f.assert_awaited_once_with(79)
    climate.set_heat_setpoint_f.assert_awaited_once()
    climate.set_fan_mode.assert_awaited_once_with("Circulate")
    climate.set_hold_mode.assert_awaited_once_with("Permanent")


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
    parent.attach_mock(climate.set_hold_mode, "set_hold_mode")

    # HOT_PRE_COOL action: cool=68 from prior schedule state where heat
    # might have been higher than 65.
    action = ScheduleAction(4, 0, "HOT_PRE_COOL", cool_setpoint_f=68,
                             fan_mode="Auto")
    applied, error = await execute_action(
        c4, action, cool_setpoint_to_apply=68, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False,
    )
    assert applied is True
    assert error is None

    # Pin the order: heat first, cool second, then fan, then hold.
    expected = [
        call.set_heat_setpoint_f(65),
        call.set_cool_setpoint_f(68),
        call.set_fan_mode("Auto"),
        call.set_hold_mode("Permanent"),
    ]
    assert parent.mock_calls == expected, (
        f"Pre-P1.3 order was cool->heat. mock_calls: {parent.mock_calls}"
    )


async def test_execute_setpoint_action_skipped_when_in_heat_mode():
    """Setpoint actions still no-op in Heat mode (no fighting the furnace)."""
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint_f=79)

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
    action = ScheduleAction(13, 0, "COAST", cool_setpoint_f=79,
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
    action = ScheduleAction(4, 0, "HOT_PRE_COOL", cool_setpoint_f=68)
    applied, error = await execute_action(
        c4, action, cool_setpoint_to_apply=85, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=True,
    )
    assert applied is False
    assert error is None
    climate.set_cool_setpoint_f.assert_not_awaited()


# ---- fetch_today_decision: lazy recompute on missing/stale decision -------


def test_fetch_today_decision_returns_stored_value(monkeypatch):
    """Happy path: decision was written at 21:00 yesterday, present today."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_HOT)
    # Recompute path must NOT be touched.
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("forecast should not be queried when stored")))
    write_api = MagicMock()

    result = fetch_today_decision(MagicMock(), write_api, "energy", "2026-07-15")

    assert result == DAYTYPE_HOT
    write_api.write.assert_not_called()  # no recompute, no write


def test_fetch_today_decision_recomputes_when_stored_missing(monkeypatch):
    """If the 21:00 decision didn't run, today's first schedule check
    pulls today's live forecast and decides — the actual P2 fix."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: None)
    today_forecast = {"high_f": 97.0, "max_dewpoint_f": 70.0, "is_heat_advisory": 0}

    def _forecast(query_api, bucket, period):
        # Tomorrow NORMAL (80F) so today is plain HOT, not HOT_STREAK.
        return today_forecast if period == "today" else {"high_f": 80.0}

    monkeypatch.setattr(app, "fetch_latest_forecast", _forecast)
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(4.5, now_utc=now_utc))
    write_api = MagicMock()

    result = fetch_today_decision(MagicMock(), write_api, "energy", "2026-07-15")

    # Forecast high 97 → HOT_5CP_RISK.
    assert result == DAYTYPE_HOT
    # Recomputed decision was persisted so subsequent calls find it.
    write_api.write.assert_called_once()


def test_fetch_today_decision_falls_back_to_normal_when_no_forecast(monkeypatch):
    """When BOTH the stored decision AND today's forecast are missing,
    return NORMAL but do NOT persist (avoid writing a junk sentinel)."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: None)
    monkeypatch.setattr(app, "fetch_latest_forecast", lambda *a, **kw: None)
    write_api = MagicMock()

    result = fetch_today_decision(MagicMock(), write_api, "energy", "2026-07-15")

    assert result == DAYTYPE_NORMAL
    # Critical: don't persist a fallback decision — it'd hide the real
    # issue (missing forecast) on every subsequent check.
    write_api.write.assert_not_called()


def test_fetch_today_decision_passes_day2_forecast_for_streak_detection(monkeypatch):
    """Recompute must include the day-after forecast so today can be
    correctly classified as HOT_STREAK_DAY1 when tomorrow is also HOT."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: None)

    captured: dict[str, list[str]] = {}

    def _forecast(query_api: Any, bucket: str, period: str) -> Any:
        captured.setdefault("queried", []).append(period)
        if period == "today":
            return {"high_f": 96.0}
        if period == "tomorrow":
            return {"high_f": 97.0}
        return None

    monkeypatch.setattr(app, "fetch_latest_forecast", _forecast)
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: None)
    write_api = MagicMock()

    fetch_today_decision(MagicMock(), write_api, "energy", "2026-07-15")

    # Both today AND tomorrow must be queried so streak detection works.
    assert "today" in captured["queried"]
    assert "tomorrow" in captured["queried"]


# ---- run_decision_revisit: intra-day forecast re-evaluation ---------------


def _make_revisit_cfg(bucket: str = "energy", tz_name: str = "America/Chicago") -> MagicMock:
    """Build a Config-shaped object with just what run_decision_revisit reads.
    Avoids constructing the full Config (which would need every env-var
    field). app's frozen=True keeps mutability honest; using a Mock for the
    attributes we need."""
    cfg = MagicMock()
    cfg.influx_bucket = bucket
    cfg.tz_name = tz_name
    return cfg


def test_revisit_no_change_does_not_overwrite(monkeypatch):
    """When the live forecast still classifies as the stored day_type,
    revisit must NOT write a new decision (no-op log only)."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_NORMAL)
    # 80F max -- inside the 75-85F NORMAL band under the recalibrated
    # thresholds, so the live forecast still classifies as NORMAL.
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, p: {"high_f": 80.0,
                                          "max_dewpoint_f": 60.0,
                                          "is_heat_advisory": 0})
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(4.0, now_utc=now_utc))
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    write_api.write.assert_not_called()


def test_revisit_escalates_normal_to_hot_when_forecast_busts_up(monkeypatch):
    """The bug-the-research-flagged scenario: 21:00 yesterday committed
    NORMAL based on 88°F forecast; morning forecast now says 96°F. Revisit
    must overwrite the stored decision so the noon coast and 14:00 shutoff
    fire under HOT_SCHEDULE."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_NORMAL)
    # Today HOT (96F), tomorrow NORMAL (80F under the recalibrated 75-85F
    # NORMAL band) -- plain HOT, not streak.
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, period: (
                            {"high_f": 96.0, "max_dewpoint_f": 70.0,
                             "is_heat_advisory": 0}
                            if period == "today"
                            else {"high_f": 80.0, "max_dewpoint_f": 60.0,
                                  "is_heat_advisory": 0}
                        ))
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(4.0, now_utc=now_utc))
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    write_api.write.assert_called_once()
    # The Point passed to write should carry the new HOT day_type as a tag.
    point = write_api.write.call_args.kwargs.get("record")
    assert point is not None
    assert dict(point._tags).get("day_type") == DAYTYPE_HOT


def test_revisit_de_escalates_hot_to_normal_when_forecast_cools(monkeypatch):
    """Symmetric: forecast yesterday said 96 (HOT), this morning's update
    says 80 (NORMAL under the recalibrated 75-85F band). Revisit overwrites
    so we don't unnecessarily run the aggressive HOT shutoff."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_HOT)
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, p: {"high_f": 80.0,
                                          "max_dewpoint_f": 60.0,
                                          "is_heat_advisory": 0})
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(4.0, now_utc=now_utc))
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    write_api.write.assert_called_once()
    point = write_api.write.call_args.kwargs.get("record")
    assert dict(point._tags).get("day_type") == DAYTYPE_NORMAL


def test_revisit_no_forecast_does_not_overwrite(monkeypatch):
    """If today's forecast can't be read (NWS poller down, fresh deploy),
    revisit logs a warning and does NOT touch the stored decision —
    the 21:00 commitment stands."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_NORMAL)
    monkeypatch.setattr(app, "fetch_latest_forecast", lambda *a, **kw: None)
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    write_api.write.assert_not_called()


def test_revisit_promotes_to_hot_streak_when_tomorrow_also_hot(monkeypatch):
    """Streak detection must work in the revisit path too — if today
    becomes HOT and tomorrow's forecast is also HOT, today should
    re-classify as HOT_STREAK_DAY1, not just HOT, so we get the deeper
    pre-cool tomorrow morning."""
    from .app import DAYTYPE_HOT_STREAK_DAY1

    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_NORMAL)

    def _forecast(q, b, period):
        if period == "today":
            return {"high_f": 96.0, "max_dewpoint_f": 70.0, "is_heat_advisory": 0}
        if period == "tomorrow":
            return {"high_f": 97.0, "max_dewpoint_f": 71.0, "is_heat_advisory": 0}
        return None

    monkeypatch.setattr(app, "fetch_latest_forecast", _forecast)
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(4.0, now_utc=now_utc))
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    write_api.write.assert_called_once()
    point = write_api.write.call_args.kwargs.get("record")
    assert dict(point._tags).get("day_type") == DAYTYPE_HOT_STREAK_DAY1


def test_revisit_promotes_to_hot_streak_when_pjm_forecast_5cp_risk(monkeypatch):
    """§7 single-day forecast 5CP-risk path through run_decision_revisit:
    today is HOT (95F), tomorrow is NORMAL (so the multi-day path
    doesn't fire), and PJM's published forecast peak for today exceeds
    the season-to-date 5th highest by >5%. Revisit must escalate to
    HOT_STREAK_DAY1 with reason='forecast_5cp_risk_single_day'.

    This is the wire-up test: it exercises the production caller
    feeding the §7 inputs into decide_day_type, not just the function's
    kwargs in isolation."""
    from .app import DAYTYPE_HOT_STREAK_DAY1

    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_HOT)

    def _forecast(q, b, period):
        if period == "today":
            return {"high_f": 95.0, "max_dewpoint_f": 70.0, "is_heat_advisory": 0}
        if period == "tomorrow":
            return {"high_f": 80.0, "max_dewpoint_f": 60.0, "is_heat_advisory": 0}
        return None

    monkeypatch.setattr(app, "fetch_latest_forecast", _forecast)
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(4.0, now_utc=now_utc))
    # Override the autouse fixture: today's PJM peak forecast 145000 MW,
    # season-to-date 5th 130000 MW -- ratio 1.115 > 1.05, so §7 fires.
    monkeypatch.setattr(
        app, "_fetch_pjm_inputs_for_target_date",
        lambda q, b, d, tz: (145000.0, 130000.0),
    )
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    write_api.write.assert_called_once()
    point = write_api.write.call_args.kwargs.get("record")
    assert dict(point._tags).get("day_type") == DAYTYPE_HOT_STREAK_DAY1
    line = point.to_line_protocol()
    assert "forecast_5cp_risk_single_day" in line


def test_revisit_does_not_escalate_when_pjm_inputs_unavailable(monkeypatch):
    """§7 graceful degradation: when PJM forecast peak is None (e.g.,
    21:00 ran before tomorrow's load forecast was posted), the revisit
    falls back to plain HOT, not HOT_STREAK_DAY1."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_NORMAL)
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, p: {"high_f": 95.0, "max_dewpoint_f": 70.0,
                                          "is_heat_advisory": 0}
                        if p == "today" else
                        {"high_f": 80.0, "max_dewpoint_f": 60.0,
                         "is_heat_advisory": 0})
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(4.0, now_utc=now_utc))
    # PJM forecast unavailable; helper returns (None, season_5th).
    monkeypatch.setattr(
        app, "_fetch_pjm_inputs_for_target_date",
        lambda q, b, d, tz: (None, 130000.0),
    )
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    write_api.write.assert_called_once()
    point = write_api.write.call_args.kwargs.get("record")
    assert dict(point._tags).get("day_type") == DAYTYPE_HOT  # plain HOT, not streak


def test_revisit_handles_no_stored_decision_yet(monkeypatch):
    """First-run case: no decision was ever written, but a forecast
    arrived. Revisit treats stored=None as 'differs from new', writes
    the decision."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: None)
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, p: {"high_f": 90.0,
                                          "max_dewpoint_f": 65.0,
                                          "is_heat_advisory": 0})
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: _fresh_sample(4.0, now_utc=now_utc))
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    # Wrote the freshly-classified decision.
    write_api.write.assert_called_once()



# ---- safety supervisor ----------------------------------------------------

from .safety_supervisor import (  # noqa: E402
    DECISION_APPROVED,
    DECISION_CLAMPED,
    DECISION_EMERGENCY,
    EMERGENCY_COOL_TARGET_F,
    EMERGENCY_INDOOR_F,
    SAFE_COOL_MAX_F,
    SAFE_COOL_MIN_F,
    SupervisorDecision,
    validate_setpoints,
)


def test_supervisor_approves_in_range_setpoints():
    """Happy path: setpoints within bounds + indoor temp comfortable."""
    d = validate_setpoints(75, 60, snapshot={"indoor_temp_f": 73.5})
    assert d.decision == DECISION_APPROVED
    assert d.cool_setpoint_f == 75
    assert d.heat_setpoint_f == 60
    assert d.reason is None
    assert not d.needs_alert


def test_supervisor_clamps_cool_setpoint_too_low():
    """A controller bug producing cool=55 must not reach the thermostat."""
    d = validate_setpoints(55, 60, snapshot={"indoor_temp_f": 73.0})
    assert d.decision == DECISION_CLAMPED
    assert d.cool_setpoint_f == SAFE_COOL_MIN_F
    assert "cool_55_to_65" in (d.reason or "")
    assert d.needs_alert


def test_supervisor_clamps_cool_setpoint_too_high():
    """cool=95 would leave AC sitting idle; clamp to safe upper bound."""
    d = validate_setpoints(95, 60, snapshot={"indoor_temp_f": 73.0})
    assert d.decision == DECISION_CLAMPED
    assert d.cool_setpoint_f == SAFE_COOL_MAX_F
    assert "cool_95_to_86" in (d.reason or "")


def test_supervisor_clamps_heat_setpoint():
    """Heat-side bounds are also enforced."""
    d = validate_setpoints(75, 80, snapshot={"indoor_temp_f": 73.0})
    assert d.decision == DECISION_CLAMPED
    assert d.heat_setpoint_f == 75  # SAFE_HEAT_MAX_F


def test_supervisor_emergency_overrides_when_indoor_too_hot():
    """Even if a layer (price scarcity tier or 5CP detector) pushes
    cool=85, if indoor temp is already 87°F, force the AC to engage at
    the emergency target."""
    d = validate_setpoints(85, 60, snapshot={"indoor_temp_f": 87.0})
    assert d.decision == DECISION_EMERGENCY
    assert d.cool_setpoint_f == EMERGENCY_COOL_TARGET_F
    assert "indoor_87.0F" in (d.reason or "")
    assert d.needs_alert


def test_supervisor_emergency_threshold_inclusive():
    """Boundary check: indoor exactly at the emergency threshold triggers."""
    d = validate_setpoints(80, 60, snapshot={"indoor_temp_f": EMERGENCY_INDOOR_F})
    assert d.decision == DECISION_EMERGENCY


def test_supervisor_no_emergency_below_threshold():
    """Just below the emergency threshold means clamping/approving normally."""
    d = validate_setpoints(80, 60, snapshot={"indoor_temp_f": EMERGENCY_INDOOR_F - 0.1})
    assert d.decision == DECISION_APPROVED


def test_supervisor_handles_missing_indoor_temp():
    """If the C4 read failed and indoor_temp_f is None, we can't fire the
    emergency rule but we still validate range bounds."""
    d = validate_setpoints(75, 60, snapshot={})
    assert d.decision == DECISION_APPROVED
    d2 = validate_setpoints(55, 60, snapshot={"indoor_temp_f": None})
    assert d2.decision == DECISION_CLAMPED


def test_supervisor_emergency_takes_precedence_over_clamp():
    """If both rules would apply (out-of-range cool setpoint AND indoor too
    hot), emergency wins so the AC actually engages aggressively."""
    d = validate_setpoints(95, 60, snapshot={"indoor_temp_f": 88.0})
    assert d.decision == DECISION_EMERGENCY
    assert d.cool_setpoint_f == EMERGENCY_COOL_TARGET_F


def test_supervisor_decision_is_immutable():
    """SupervisorDecision is frozen; downstream code can't accidentally mutate
    it after the fact."""
    d = validate_setpoints(75, 60, snapshot={"indoor_temp_f": 73.0})
    with pytest.raises((AttributeError, Exception)):
        d.cool_setpoint_f = 99   # type: ignore[misc]


# ---- §7 price-aware pre-cool wire-up -------------------------------------


def test_merge_same_hour_actions_deepest_wins_picks_lower_setpoint():
    """When the §7 price-aware-precool action lands on the same hour as
    a base-schedule action, deepest setpoint wins per the spec."""
    base = ScheduleAction(12, 0, "HOT_COAST", cool_setpoint_f=80,
                           fan_mode="Circulate")
    price_aware = ScheduleAction(12, 0, "PRICE_AWARE_PRECOOL",
                                  cool_setpoint_f=66)
    merged = merge_same_hour_actions_deepest_wins([base, price_aware])
    assert len(merged) == 1
    assert merged[0].cool_setpoint_f == 66
    assert merged[0].label == "PRICE_AWARE_PRECOOL"


def test_merge_same_hour_actions_keeps_distinct_hours():
    """Actions at different hours don't merge — both fire at their
    scheduled times."""
    a = ScheduleAction(12, 0, "PRICE_AWARE_PRECOOL", cool_setpoint_f=66)
    # Synthetic fixture: just a different-hour action with any label.
    # HOT_5CP_SHUTOFF was the pre-prereg fixed-window action; kept here
    # as a label string only because the test logic is opaque to it.
    b = ScheduleAction(14, 0, "SYNTHETIC_AFTERNOON", cool_setpoint_f=85)
    merged = merge_same_hour_actions_deepest_wins([a, b])
    assert len(merged) == 2
    assert sorted(m.hour for m in merged) == [12, 14]


def test_merge_same_hour_setpoint_action_wins_over_release_hold():
    """A release_hold + setpoint conflict at the same hour resolves in
    favour of the setpoint (running a setpoint is more conservative
    than clearing the hold). The MILD schedule's 00:05 release_hold
    plus a hypothetical 00:05 setpoint action gives the setpoint."""
    rh = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)
    setpoint = ScheduleAction(0, 5, "PRICE_AWARE_PRECOOL", cool_setpoint_f=66)
    merged = merge_same_hour_actions_deepest_wins([rh, setpoint])
    assert len(merged) == 1
    assert merged[0].label == "PRICE_AWARE_PRECOOL"


def test_precool_window_action_synthesizes_correct_shape():
    """The synthetic action injected by run_schedule_check uses the
    window's hour_ct + depth_f, leaves fan_mode None, and clamps the
    heat setpoint to the floor."""
    a = precool_window_action({"hour_ct": 12, "depth_f": 67})
    assert a.hour == 12
    assert a.minute == 0
    assert a.label == "PRICE_AWARE_PRECOOL"
    assert a.cool_setpoint_f == 67
    assert a.fan_mode is None


def _build_da_lmp_query_result(
    hour_to_price_per_mwh: dict[int, float],
    target_date_iso: str = "2026-07-15",
    tz_name: str = "America/Chicago",
) -> list[MagicMock]:
    """Build a MagicMock query_api result list mirroring the InfluxDB
    Flux response for ``pjm.lmp_da_hourly`` rows. Each record carries an
    explicit ``get_time()`` returning the UTC instant corresponding to
    the requested CT hour, so the function under test can map the row
    back to its CT-hour-of-day for the EPT-vs-CT boundary logic."""
    tz = ZoneInfo(tz_name)
    table = MagicMock()
    table.records = []
    target_local = datetime.fromisoformat(target_date_iso).replace(tzinfo=tz)
    for hour in sorted(hour_to_price_per_mwh):
        ct_time = target_local.replace(hour=hour, minute=0, second=0, microsecond=0)
        utc_time = ct_time.astimezone(timezone.utc)
        record = MagicMock()
        record.get_value.return_value = hour_to_price_per_mwh[hour]
        record.get_time.return_value = utc_time
        table.records.append(record)
    return [table]


def test_fetch_day_ahead_prices_converts_dollars_per_mwh_to_cents_per_kwh(monkeypatch):
    """The poller stores total_lmp_da in $/MWh; the §7 decision rule
    needs cents/kWh (the unit the locked tier thresholds use). $50/MWh
    must come out as 5c/kWh."""
    api = MagicMock()
    api.query.return_value = _build_da_lmp_query_result({h: 50.0 for h in range(24)})
    out = fetch_day_ahead_prices_for_date(api, "energy", "2026-07-15",
                                           tz=ZoneInfo("America/Chicago"))
    assert out is not None
    assert len(out) == 24
    assert all(p == 5.0 for p in out)


def test_fetch_day_ahead_prices_accepts_ept_ct_boundary_only_hour_23_missing(monkeypatch):
    """EPT-vs-CT day-boundary case (NOT DST-specific — EPT is 1 hour
    ahead of CT year-round). At a 21:00 CT day-D decision for tomorrow,
    PJM's "tomorrow EPT day" publish covers CT hours 0-22 of CT-tomorrow
    but not CT hour 23 (which belongs to "EPT day D+2", unpublished
    until 17:00 CT day D+1). Function pads hour 23 with hour 22's value
    so the precool decision can fire on the boundary case. Hours 6-14
    (cheap-window search) and 10-22 (typical spike search) — the
    operationally-required range — are real PJM data."""
    api = MagicMock()
    # 23 hours present, hour 23 missing. Distinct value at hour 22 so
    # we can verify padding.
    hours = {h: (50.0 if h != 22 else 75.0) for h in range(23)}
    api.query.return_value = _build_da_lmp_query_result(hours)
    out = fetch_day_ahead_prices_for_date(api, "energy", "2026-07-15",
                                           tz=ZoneInfo("America/Chicago"))
    assert out is not None
    assert len(out) == 24
    # Hour 22 in cents/kWh: $75/MWh = 7.5c/kWh
    assert out[22] == 7.5
    # Hour 23 padded with hour 22's value -> same 7.5c/kWh
    assert out[23] == 7.5


def test_fetch_day_ahead_prices_rejects_interior_gap(monkeypatch):
    """Genuine insufficient coverage — an interior hour missing is NOT
    the structural EPT-vs-CT boundary case. The function must reject
    rather than pad, so the §7 decision short-circuits."""
    api = MagicMock()
    # 23 hours, but hour 7 (interior, not hour 23) is missing.
    hours = {h: 50.0 for h in range(24) if h != 7}
    api.query.return_value = _build_da_lmp_query_result(hours)
    out = fetch_day_ahead_prices_for_date(api, "energy", "2026-07-15",
                                           tz=ZoneInfo("America/Chicago"))
    assert out is None


def test_fetch_day_ahead_prices_rejects_partial_coverage(monkeypatch):
    """Substantial coverage gap (e.g., only the first 18 hours posted)
    must reject. Catches missed polls, market-cancelled days, and
    queries-before-publish."""
    api = MagicMock()
    hours = {h: 50.0 for h in range(18)}  # hours 18-23 all missing
    api.query.return_value = _build_da_lmp_query_result(hours)
    out = fetch_day_ahead_prices_for_date(api, "energy", "2026-07-15",
                                           tz=ZoneInfo("America/Chicago"))
    assert out is None


def test_fetch_day_ahead_prices_rejects_empty_result(monkeypatch):
    """No DA LMP rows at all -> None. (Pre-publish query, market-
    cancelled day, etc.)"""
    api = MagicMock()
    api.query.return_value = _build_da_lmp_query_result({})
    out = fetch_day_ahead_prices_for_date(api, "energy", "2026-07-15",
                                           tz=ZoneInfo("America/Chicago"))
    assert out is None


def test_fetch_day_ahead_prices_skips_rows_from_other_ct_dates(monkeypatch):
    """Rows that land in the requested UTC range but actually belong to
    a different CT calendar date (e.g., PJM-EPT-day-X's hour 00:00 EPT =
    CT 23:00 day-before) must be filtered out — they're for a different
    precool decision, not this one. Without the filter, a row at CT
    23:00 day-before could occupy the dict's hour-23 slot and mask the
    structural boundary case."""
    api = MagicMock()
    # All 23 hours of the target CT date + one stray hour 23 of the
    # previous CT date (CT 23:00 2026-07-14 = the EPT 00:00 boundary
    # of EPT day 2026-07-15). The function should ignore the stray and
    # treat the result as the boundary case (pad hour 23 with hour 22).
    tz = ZoneInfo("America/Chicago")
    table = MagicMock()
    table.records = []
    for hour in range(23):
        ct_time = datetime.fromisoformat("2026-07-15").replace(tzinfo=tz, hour=hour)
        rec = MagicMock()
        rec.get_value.return_value = 50.0
        rec.get_time.return_value = ct_time.astimezone(timezone.utc)
        table.records.append(rec)
    # Stray record from the previous CT date (different precool decision).
    stray_ct = datetime.fromisoformat("2026-07-14").replace(tzinfo=tz, hour=23)
    stray = MagicMock()
    stray.get_value.return_value = 999.0
    stray.get_time.return_value = stray_ct.astimezone(timezone.utc)
    table.records.append(stray)
    api = MagicMock()
    api.query.return_value = [table]
    out = fetch_day_ahead_prices_for_date(api, "energy", "2026-07-15", tz=tz)
    assert out is not None
    assert len(out) == 24
    # The 999 value MUST NOT appear; hour 23 is padded with hour 22 (50).
    assert 99.9 not in out
    assert out[23] == 5.0


# ---- §Critical #2: per-tick layer evaluation ------------------------------


def _make_schedule_check_cfg(bucket: str = "energy",
                              tz_name: str = "America/Chicago",
                              dry_run: bool = True) -> MagicMock:
    cfg = MagicMock()
    cfg.influx_bucket = bucket
    cfg.tz_name = tz_name
    cfg.dry_run = dry_run
    return cfg


def _stub_layer_eval_io(monkeypatch: Any, *,
                         price_cents: float | None = 5.0,
                         zone_load: float | None = 14000.0,
                         derivative: float = 0.0,
                         forecast_peak: float | None = 17000.0,
                         season_5th: float = 20375.0,
                         rto_zone_load: float | None = None,
                         rto_derivative: float = 0.0,
                         rto_season_5th: float = 151525.0,
                         rto_forecast_peak: float | None = None) -> None:
    """Stub the InfluxDB IO that _evaluate_layer_inputs makes. Lets tests
    drive the price/load/forecast inputs without spinning up Flux.

    Dual-scope after P1.1: the §3 detector runs once per scope
    (comed_zone + rto). The stubs respect the area/zone tag passed by
    ``evaluate_for_scope`` so the COMED snapshot doesn't pretend to be
    RTO load and vice versa. Pass ``rto_zone_load=None`` (default) to
    simulate no RTO data (poller hasn't backfilled yet); pass a value
    to drive RTO detector inputs explicitly.

    Per-scope forecast peaks (P1.1 post-merge fix): the ComEd scope
    reads ``pjm.load_forecast{forecast_area=COMED}`` and the RTO
    scope reads ``pjm.peak_forecast_rto{area="PJM RTO"}``. The two
    feeds are independent in production. Pass ``rto_forecast_peak``
    explicitly when driving RTO triggers in tests.
    """
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b, *, now_utc: (
                            None if price_cents is None
                            else _fresh_sample(price_cents, now_utc=now_utc)))

    from .pjm_5cp import ZoneLoadSnapshot
    from . import pjm_5cp

    def _snap(mw: float, deriv: float) -> Any:
        return ZoneLoadSnapshot(
            current_mw=mw,
            derivative_mw_per_hour=deriv,
            observed_at_utc=datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc),
        )

    snapshots = {
        "COMED":   _snap(zone_load, derivative) if zone_load is not None else None,
        "PJM RTO": _snap(rto_zone_load, rto_derivative) if rto_zone_load is not None else None,
    }
    fallback_seasons = {"CE": season_5th, "RTO": rto_season_5th}

    def _fetch_zone_live_stub(q, b, *, area="COMED"):
        return snapshots[area]

    def _update_season_5th_highest_stub(q, b, s, e, *, zone="CE"):
        return fallback_seasons[zone]

    # Patch in pjm_5cp so evaluate_for_scope picks up the stubs; also
    # patch app.update_season_5th_highest so compute_5cp_inputs_for_date
    # (the §7 pre-cool deepening night-before caller in app.py) sees the
    # stub. fetch_zone_live is no longer imported in app.py since the
    # scope-aware refactor; only patch it in pjm_5cp.
    monkeypatch.setattr(pjm_5cp, "fetch_zone_live", _fetch_zone_live_stub)
    monkeypatch.setattr(pjm_5cp, "update_season_5th_highest",
                        _update_season_5th_highest_stub)
    monkeypatch.setattr(app, "update_season_5th_highest",
                        _update_season_5th_highest_stub)
    # fetch_forecast_peak_today now takes a kwarg-only `tz` param (P2.5)
    monkeypatch.setattr(app, "fetch_forecast_peak_today",
                        lambda q, b, *, tz=None: forecast_peak)
    monkeypatch.setattr(app, "fetch_rto_peak_forecast_today",
                        lambda q, b: rto_forecast_peak)


def test_evaluate_layer_inputs_runs_without_action_firing(monkeypatch):
    """The Critical #2 fix: layer eval is independent of action firing.
    A call at any minute populates a LayerInputs return value with the
    current price tier and 5CP state."""
    _stub_layer_eval_io(monkeypatch, price_cents=12.0,  # elevated tier
                        zone_load=15000.0, derivative=200.0,
                        forecast_peak=17000.0, season_5th=14000.0)
    cfg = _make_schedule_check_cfg()
    firing = FiringState()
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 23,  # arbitrary non-action minute
                          tzinfo=ZoneInfo("America/Chicago"))

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs.price_tier_name == "elevated"
    assert inputs.price_offset_f == 3
    assert inputs.fivecp_data_available is True


def test_evaluate_layer_inputs_carries_overlay_state_across_calls(monkeypatch):
    """Two consecutive ticks: first triggers elevated, second stays
    elevated due to the 30-min hold even with a brief price dip."""
    _stub_layer_eval_io(monkeypatch, price_cents=12.0,
                        zone_load=10000.0, derivative=0.0,
                        forecast_peak=11000.0, season_5th=14000.0)
    cfg = _make_schedule_check_cfg()
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


def test_5cp_audit_throttled_to_5min_intervals(monkeypatch):
    """hvac.5cp_state writes throttle to once per 5 min so dashboards see
    ~288 rows/day, not the 1440 rows/day a per-minute write would
    produce."""
    _stub_layer_eval_io(monkeypatch)
    cfg = _make_schedule_check_cfg()
    firing = FiringState()
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

    # First call writes the audit row.
    _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    first_count = sum(
        1 for c in write_api.write.call_args_list
        if "hvac.5cp_state" in c.kwargs.get("record").to_line_protocol()
    )
    assert first_count == 1

    # 1 minute later: throttle still in effect, no new audit row.
    _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing,
                            now_local + timedelta(minutes=1))
    second_count = sum(
        1 for c in write_api.write.call_args_list
        if "hvac.5cp_state" in c.kwargs.get("record").to_line_protocol()
    )
    assert second_count == 1  # unchanged

    # 5 minutes after first call: throttle elapsed, second audit row writes.
    _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing,
                            now_local + timedelta(minutes=5))
    third_count = sum(
        1 for c in write_api.write.call_args_list
        if "hvac.5cp_state" in c.kwargs.get("record").to_line_protocol()
    )
    assert third_count == 2


# ---- P1.1: dual-scope 5CP (ComEd OR RTO) ---------------------------------


def test_dual_scope_audit_writes_two_rows_when_both_scopes_have_data(monkeypatch):
    """With both detector scopes' inst_load feeds populated, each audit
    cycle writes TWO hvac.5cp_state rows -- one per scope -- so the
    dashboard can plot both ratios side-by-side. Tag-disambiguation is
    via the `scope` tag (`comed_zone` | `rto`) carried on the row."""
    _stub_layer_eval_io(monkeypatch,
                         zone_load=14000.0, rto_zone_load=140000.0,
                         season_5th=20375.0, rto_season_5th=151525.0,
                         forecast_peak=17000.0,  # ComEd-scale forecast
                         rto_forecast_peak=155000.0)  # RTO-scale forecast
    cfg = _make_schedule_check_cfg()
    firing = FiringState()
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))

    _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)

    audit_lines = [
        c.kwargs.get("record").to_line_protocol()
        for c in write_api.write.call_args_list
        if "hvac.5cp_state" in c.kwargs.get("record").to_line_protocol()
    ]
    assert len(audit_lines) == 2
    # Tag presence: each row must carry both scope and zone tags.
    assert any("scope=comed_zone" in line and "zone=CE" in line for line in audit_lines)
    assert any("scope=rto" in line and "zone=RTO" in line for line in audit_lines)


def test_dual_scope_or_fires_when_rto_alone_qualifies(monkeypatch):
    """The OR semantics: even if ComEd-zone load is well below trigger,
    an RTO-scale ramp-up alone is enough to push the §3 5CP layer to
    its 85°F shutoff setpoint. This is the coverage P1.1 adds -- the
    prior single-scope ComEd detector
    would miss PJM 5CP hours that don't also coincide with ComEd zone
    peaks (per HVAC_LOGIC.md, ComEd zone tends to peak earlier than
    RTO; a late-afternoon RTO ramp without ComEd-zone elevation is
    exactly the case the new detector catches)."""
    _stub_layer_eval_io(monkeypatch,
                         # ComEd well below trigger: 13k / 20.375k = 0.638
                         zone_load=13000.0, derivative=0.0,
                         season_5th=20375.0,
                         # ComEd-scale forecast (low; would never satisfy
                         # the cross-scale RTO gate in the pre-fix code)
                         forecast_peak=15000.0,
                         # RTO above trigger: 145k / 151.525k = 0.957 > 0.95
                         rto_zone_load=145000.0, rto_derivative=400.0,
                         rto_season_5th=151525.0,
                         # RTO-scale forecast > RTO season_5th: satisfies gate
                         rto_forecast_peak=160000.0)
    cfg = _make_schedule_check_cfg()
    firing = FiringState()
    write_api = MagicMock()
    # 14:30 CT == 19:30 UTC -- inside the 13:00-20:00 CT detector window.
    now_local = datetime(2026, 7, 15, 14, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs.fivecp_active is True
    assert "rto" in inputs.fivecp_scopes_fired
    assert "comed_zone" not in inputs.fivecp_scopes_fired


def test_dual_scope_or_fires_when_comed_alone_qualifies(monkeypatch):
    """Symmetric case: ComEd-zone scope qualifies, RTO doesn't. The OR
    still trips the active state. Confirms neither scope is being
    given precedence."""
    _stub_layer_eval_io(monkeypatch,
                         # ComEd above trigger: 19.6k / 20.375k = 0.962
                         zone_load=19600.0, derivative=300.0,
                         season_5th=20375.0,
                         forecast_peak=22000.0,  # ComEd-scale, > ComEd season_5th
                         # RTO well below trigger: 140k / 151.525k = 0.924
                         rto_zone_load=140000.0, rto_derivative=0.0,
                         rto_season_5th=151525.0,
                         # RTO forecast below season_5th: gate fails
                         rto_forecast_peak=148000.0)
    cfg = _make_schedule_check_cfg()
    firing = FiringState()
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs.fivecp_active is True
    assert "comed_zone" in inputs.fivecp_scopes_fired


def test_dual_scope_per_scope_state_is_independent(monkeypatch):
    """The two scopes carry independent FiveCPState. A ComEd-only
    trigger does NOT flip the RTO state machine (and vice versa) --
    important so a ComEd release doesn't prematurely exit an RTO hold."""
    _stub_layer_eval_io(monkeypatch,
                         zone_load=19600.0, derivative=300.0,
                         season_5th=20375.0,
                         forecast_peak=22000.0,
                         rto_zone_load=140000.0, rto_derivative=0.0,
                         rto_season_5th=151525.0,
                         rto_forecast_peak=148000.0)
    cfg = _make_schedule_check_cfg()
    firing = FiringState()
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert firing.fivecp_state_comed.is_active is True   # ComEd triggered
    assert firing.fivecp_state_rto.is_active is False    # RTO did not


def test_may_off_season_cannot_fire_against_bogus_low_baseline(monkeypatch):
    """Reproducer for the 2026-05-11 production incident: sparse RTO
    ingest of May 2026 rows produced an RTO 'season 5th' of 90,244 MW,
    against which a real-time RTO load of 85,410 MW reached ratio
    0.946 -- just below the 0.95 trigger. The summer-eligibility gate
    (PJM Manual 19 / ComEd Att. M-2: Jun 1 - Sep 30) makes the
    detector refuse to evaluate triggers outside cooling season, so
    even a malformed off-season baseline cannot fire the layer."""
    _stub_layer_eval_io(monkeypatch,
                         # Drive the exact production-incident values:
                         rto_zone_load=85410.0, rto_derivative=1294.0,
                         rto_season_5th=90244.0,  # bogus, off-season
                         rto_forecast_peak=156000.0,  # RTO-scale, would normally
                                                       # exceed bogus baseline
                         zone_load=14000.0, season_5th=20375.0,
                         forecast_peak=10000.0)
    cfg = _make_schedule_check_cfg()
    firing = FiringState()
    write_api = MagicMock()
    # 2026-05-11 14:30 CT -- May, off-season per Manual 19.
    now_local = datetime(2026, 5, 11, 14, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs.fivecp_active is False
    assert inputs.fivecp_scopes_fired == ()
    # State machines are reset across the off-season boundary (no
    # carried hold from a prior in-season trigger).
    assert firing.fivecp_state_comed.is_active is False
    assert firing.fivecp_state_rto.is_active is False


def test_evaluate_layer_inputs_writes_price_overlay_on_tier_transition(monkeypatch):
    """A price-tier transition writes a hvac.price_overlay row. Same-tier
    ticks don't (the measurement is event-driven)."""
    _stub_layer_eval_io(monkeypatch, price_cents=5.0)  # normal initially
    cfg = _make_schedule_check_cfg()
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


# ---- §P1.2 dry-run mid-period repush spam regression ----------------------


async def test_dry_run_mid_period_repush_writes_once_then_skips_when_layer_unchanged(monkeypatch):
    """P1.2 regression: in dry-run mode the mid-period re-push guard
    must update last_pushed_effective_cool_f even though execute_action
    skipped the real Control4 call. Otherwise the guard compares the
    new effective_cool_f to None forever and writes a phantom
    MID_PERIOD_REPUSH row every scheduler tick.

    Reproducer: two consecutive _push_layer_change_mid_period calls in
    dry-run with the same layer inputs. First call writes one
    hvac.actions row (effective changed from None). Second call must
    skip (effective unchanged) — pre-fix it wrote another row."""
    from .app import LayerInputs

    cfg = MagicMock()
    cfg.influx_bucket = "energy"
    cfg.dry_run = True

    c4, climate = _mock_c4_client()
    write_api = MagicMock()
    firing = FiringState(
        last_schedule_cool_f=79,           # COAST action fired earlier
        last_action_label="COAST",
        last_pushed_effective_cool_f=None,  # post-firing state in dry-run pre-fix
    )
    layer_inputs = LayerInputs(
        price_tier_name="normal",
        price_offset_f=0,
        price_override_f=None,
        price_prev_tier="normal",
        current_price_cents=5.0,
        fivecp_active=False,
        fivecp_scopes_fired=(),
        fivecp_load_mw=0.0,
        fivecp_derivative=0.0,
        fivecp_forecast_peak=0.0,
        fivecp_season_5th_mw=20375.0,
        fivecp_data_available=False,
    )
    now_local = datetime(2026, 7, 15, 13, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    # First call: effective_cool_f=79 != last_pushed=None -> writes one
    # hvac.actions audit row.
    await _push_layer_change_mid_period(
        cfg, c4, write_api, firing, "NORMAL",
        layer_inputs, today_dewpoint_f=60.0, override_note="",
        now_local=now_local,
    )
    first_rows = sum(
        1 for c in write_api.write.call_args_list
        if "hvac.actions" in c.kwargs.get("record").to_line_protocol()
    )
    assert first_rows == 1
    # After the first call, the guard variable should be updated.
    assert firing.last_pushed_effective_cool_f == 79

    # Second call, identical inputs, 1 minute later: must skip silently.
    await _push_layer_change_mid_period(
        cfg, c4, write_api, firing, "NORMAL",
        layer_inputs, today_dewpoint_f=60.0, override_note="",
        now_local=now_local + timedelta(minutes=1),
    )
    second_rows = sum(
        1 for c in write_api.write.call_args_list
        if "hvac.actions" in c.kwargs.get("record").to_line_protocol()
    )
    # Pre-fix: this asserted 2 (the spam bug). Post-fix: still 1.
    assert second_rows == 1, (
        "P1.2 regression: dry-run mid-period push wrote a phantom row "
        "on a tick where the effective cool setpoint did not change"
    )


# ---- P1.2: failed-action retry (run_schedule_check integration) ----------


def _drive_run_schedule_check(
    monkeypatch: Any,
    *,
    now_local: datetime,
    firing: FiringState,
    execute_result: tuple[bool, str | None] = (True, None),
    day_type: str = "NORMAL",
    dry_run: bool = False,
    price_cents: float | None = 5.0,
) -> tuple[Any, Any, Any]:
    """Drive ``app.run_schedule_check`` end-to-end with the IO stubbed.
    ``execute_result`` controls the (applied, error) tuple returned by
    the patched ``execute_action``. Returns the cfg/c4/write_api so
    callers can inspect side effects."""
    import asyncio

    _stub_layer_eval_io(monkeypatch, price_cents=price_cents,
                         zone_load=14000.0, derivative=0.0,
                         forecast_peak=17000.0, season_5th=20375.0)

    # The rest of the run_schedule_check dependency surface:
    monkeypatch.setattr(app, "fetch_today_decision",
                        lambda q, w, b, t: day_type)
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, p: None)  # no dewpoint -> no humid override
    monkeypatch.setattr(app, "read_precool_window_for_date",
                        lambda q, b, d: None)
    monkeypatch.setattr(app, "load_overrides", lambda path: [])
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 73.0,
                            "hvac_mode": "Cool",
                            "cool_setpoint_f": 78,
                            "heat_setpoint_f": 65,
                        }))
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=execute_result))
    monkeypatch.setattr(app, "write_action", MagicMock())

    cfg = _make_schedule_check_cfg(dry_run=dry_run)
    cfg.overrides_file = "/nonexistent"
    c4, _climate = _mock_c4_client()
    query_api = MagicMock()
    write_api = MagicMock()
    asyncio.run(app.run_schedule_check(
        cfg, c4, query_api, write_api,
        ZoneInfo(cfg.tz_name), now_local, firing,
    ))
    return cfg, c4, write_api


def test_mild_day_scarcity_spike_pushes_85(monkeypatch):
    """NORTH STAR: a >=20c ComEd 5-min print on a MILD day must drive the
    thermostat to the 85F scarcity setpoint via the MID-PERIOD re-push (the
    real dormancy gate). Fresh state, no pre-seeded tier -- the 46.4c price
    itself upgrades the overlay to scarcity (immediate, no min-hold) and the
    mid-period push must fire. 14:00 is a NON-action minute, so this exercises
    _push_layer_change_mid_period, not the action-fire path. Today MILD's only
    action is the 00:05 release_hold, so reconstruct_startup_baseline sets
    last_schedule_cool_f=None and the mid-period push short-circuits -> no 85
    -> strict-xfail. After MILD gets a real schedule, 13:00 MILD_DAY is in
    effect (baseline 78), the push fires, warmer-wins gives 85."""
    firing = FiringState()  # price_overlay_state defaults to "normal"
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing,
        price_cents=46.4, day_type="MILD",
        execute_result=(True, None), dry_run=False,
    )
    execute_mock = app.execute_action
    assert isinstance(execute_mock, AsyncMock)  # patched by the harness
    pushed_cools = [c.args[2] for c in execute_mock.await_args_list]
    assert 85 in pushed_cools, f"expected an 85F mid-period push, got {pushed_cools}"


def test_successful_action_marks_done(monkeypatch):
    """Happy path: live mode, execute_action succeeds, key IS added
    to firing.fired_actions so the action won't refire."""
    firing = FiringState()
    # NORMAL_SCHEDULE has PRE_COOL at 06:00.
    now_local = datetime(2026, 7, 15, 6, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing,
        execute_result=(True, None), dry_run=False,
    )
    assert ("2026-07-15", 6, 0) in firing.fired_actions


def test_failed_action_in_live_mode_does_not_mark_done(monkeypatch):
    """P1.2 invariant: when execute_action returns an error in live
    mode, the key is NOT added to fired_actions. The next tick within
    the make-up window must be able to retry the action."""
    firing = FiringState()
    now_local = datetime(2026, 7, 15, 6, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing,
        execute_result=(False, "Control4 timeout"), dry_run=False,
    )
    assert ("2026-07-15", 6, 0) not in firing.fired_actions


def test_dry_run_marks_done_regardless_of_apply_flag(monkeypatch):
    """Dry-run mode is an intentional no-push; the action is treated
    as 'handled' once the per-tick orchestration completes. Without
    this, every dry-run tick within the make-up window would replay
    the same action."""
    firing = FiringState()
    now_local = datetime(2026, 7, 15, 6, 0, tzinfo=ZoneInfo("America/Chicago"))
    # In dry-run, execute_action returns (False, None) by convention --
    # nothing was pushed but no error occurred.
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing,
        execute_result=(False, None), dry_run=True,
    )
    assert ("2026-07-15", 6, 0) in firing.fired_actions


def test_failed_action_retries_within_makeup_window(monkeypatch):
    """A failed action at hh:00 can refire at hh:01-hh:05. This is the
    behavior the make-up window enables. The pre-fix code would have
    permanently suppressed the action after the first failure because
    (a) the key was added before the I/O and (b) the time-match check
    only allowed firing on the exact-minute tick."""
    firing = FiringState()
    base = datetime(2026, 7, 15, 6, 0, tzinfo=ZoneInfo("America/Chicago"))

    # Tick 1: 06:00 fails.
    _drive_run_schedule_check(
        monkeypatch, now_local=base, firing=firing,
        execute_result=(False, "Control4 timeout"), dry_run=False,
    )
    assert ("2026-07-15", 6, 0) not in firing.fired_actions

    # Tick 2: 06:03, still inside make-up window, this time succeeds.
    _drive_run_schedule_check(
        monkeypatch, now_local=base + timedelta(minutes=3), firing=firing,
        execute_result=(True, None), dry_run=False,
    )
    assert ("2026-07-15", 6, 0) in firing.fired_actions


def test_failed_action_stops_retrying_after_makeup_window(monkeypatch):
    """Beyond the make-up window (5 min by default), the time-match
    check stops firing the action even if it never succeeded. This
    is the design choice that bounds the retry to "near scheduled
    time" rather than letting an action fire arbitrarily late."""
    firing = FiringState()
    base = datetime(2026, 7, 15, 6, 0, tzinfo=ZoneInfo("America/Chicago"))

    # Tick 1: 06:00 fails.
    _drive_run_schedule_check(
        monkeypatch, now_local=base, firing=firing,
        execute_result=(False, "Control4 timeout"), dry_run=False,
    )
    assert ("2026-07-15", 6, 0) not in firing.fired_actions

    # Tick 2: 06:06, BEYOND the 5-minute make-up window. Action no
    # longer matches the time check, so it cannot refire today even
    # if the underlying problem is now resolved. Operator must wait
    # for tomorrow's schedule (or manually re-fire).
    _drive_run_schedule_check(
        monkeypatch, now_local=base + timedelta(minutes=6), firing=firing,
        execute_result=(True, None), dry_run=False,
    )
    assert ("2026-07-15", 6, 0) not in firing.fired_actions


def test_already_fired_action_does_not_refire_within_window(monkeypatch):
    """Once an action has succeeded and been marked done, ticks within
    the make-up window must NOT refire it (the fired_actions set is
    the de-duplication guard)."""
    # baseline_initialized=True: models a mid-session tick (the 6:00 action
    # already fired in this session, so the startup hook already ran and the
    # mid-period guard was set by the prior tick's push).
    firing = FiringState(fired_actions={("2026-07-15", 6, 0)},
                         baseline_initialized=True,
                         last_schedule_cool_f=70,
                         last_pushed_effective_cool_f=70)
    base = datetime(2026, 7, 15, 6, 2, tzinfo=ZoneInfo("America/Chicago"))

    _, _, _ = _drive_run_schedule_check(
        monkeypatch, now_local=base, firing=firing,
        execute_result=(True, None), dry_run=False,
    )
    # execute_action should NOT have been awaited: the fired_actions
    # short-circuit kicks in BEFORE the action body runs.
    assert app.execute_action.await_count == 0  # type: ignore[attr-defined]


# ---- P1.3: _read_stored_decision multi-revision Flux semantics ------------


def _mock_query_api_for_decisions(records: list[dict[str, Any]]) -> MagicMock:
    """Build a query_api mock whose ``.query(flux)`` returns a list of
    tables. ``records`` is a flat list of dicts, each mapped to one
    Flux record's ``.values``. All records appear in a single table
    (matching what the fixed ``group() |> sort |> limit 1`` pipeline
    produces -- one flattened table with the chosen rows).

    Side effect: the mock stores the Flux query string at ``.last_flux``
    for regression assertions against the pipeline structure.
    """
    mock = MagicMock()
    mock.last_flux = None

    def _query(flux: str) -> list[Any]:
        mock.last_flux = flux

        class _Rec:
            def __init__(self, values: dict[str, Any]) -> None:
                self.values = values

        class _Table:
            def __init__(self, recs: list[dict[str, Any]]) -> None:
                self.records = [_Rec(r) for r in recs]

        return [_Table(records)]

    mock.query = _query
    return mock


def test_read_stored_decision_returns_day_type_when_present():
    """Happy path: one decision row exists, function returns its
    day_type tag value."""
    q = _mock_query_api_for_decisions([{"day_type": "HOT"}])
    assert app._read_stored_decision(q, "energy", "2026-07-15") == "HOT"


def test_read_stored_decision_returns_none_when_empty():
    """No decision rows in bucket -> None. fetch_today_decision's lazy
    recompute path depends on this being None, not raising."""
    q = _mock_query_api_for_decisions([])
    assert app._read_stored_decision(q, "energy", "2026-07-15") is None


def test_read_stored_decision_flux_query_flattens_series_with_group():
    """Regression guard: the Flux pipeline MUST include ``group()`` to
    flatten tag-keyed series before picking the latest. Without it,
    a NORMAL->HOT revisit creates two series (different day_type tag
    values), and ``last()`` per-series + Python iteration in
    unspecified order can return the older NORMAL day_type and silently
    defeat the 06:00/11:00 forecast-bust correction path."""
    q = _mock_query_api_for_decisions([{"day_type": "HOT"}])
    app._read_stored_decision(q, "energy", "2026-07-15")
    assert "|> group()" in q.last_flux


def test_read_stored_decision_flux_filters_by_single_field_before_group():
    """Regression guard for the 2026-05-11 production incident:
    ``group()`` without a prior ``_field`` filter triggers an Influx
    runtime error ``schema collision: cannot group string and float
    types together`` because ``hvac.decisions`` carries fields of
    mixed types (high_f float, dry_run string, etc.) and flattening
    collides them in the ``_value`` column.

    The Flux MUST filter to a single ``_field`` BEFORE ``group()`` so
    the flattened table has a homogeneous ``_value`` type. ``high_f``
    is the canonical choice because every ``write_decision`` call
    writes it -- one row per decision write, one per
    (decision_for_date, day_type) pair, which is exactly what the
    rank-by-time path needs."""
    q = _mock_query_api_for_decisions([{"day_type": "HOT"}])
    app._read_stored_decision(q, "energy", "2026-07-15")
    flux = q.last_flux
    assert 'r._field == "high_f"' in flux
    field_pos = flux.index('r._field == "high_f"')
    group_pos = flux.index("|> group()")
    assert field_pos < group_pos


def test_read_stored_decision_flux_query_picks_latest_by_time():
    """Regression guard: after ``group()``, the query MUST sort
    descending by ``_time`` and ``limit(n: 1)`` so the most recent
    decision wins. Returning an older row when a newer revisit
    exists is the P1.3 bug class."""
    q = _mock_query_api_for_decisions([{"day_type": "HOT"}])
    app._read_stored_decision(q, "energy", "2026-07-15")
    flux = q.last_flux
    assert "sort(columns: [\"_time\"], desc: true)" in flux
    assert "limit(n: 1)" in flux


def test_read_stored_decision_handles_record_without_day_type():
    """Defensive: if a row somehow lacks ``day_type`` (corrupt tag
    state, partial write), continue iterating rather than crash.
    Returns None when no row carries a non-empty day_type."""
    q = _mock_query_api_for_decisions([{"day_type": None}, {"day_type": ""}])
    assert app._read_stored_decision(q, "energy", "2026-07-15") is None


def test_read_stored_decision_targets_correct_decision_for_date():
    """The decision_for_date filter must appear verbatim in the Flux
    so a query for 2026-07-15 doesn't accidentally pull 2026-07-14's
    last decision."""
    q = _mock_query_api_for_decisions([{"day_type": "HOT"}])
    app._read_stored_decision(q, "energy", "2026-07-15")
    assert 'r.decision_for_date == "2026-07-15"' in q.last_flux


# ---- P2: price tier preservation across feed gap --------------------------


def test_price_tier_carries_effective_setpoint_when_feed_drops(monkeypatch):
    """P2 review fix: when ``fetch_latest_comed`` returns None mid-tick,
    the price-overlay tier label was carried forward but the
    setpoint contributions (offset/override) were silently zeroed.
    Result: logs labeled the tier as ``scarcity`` while the
    effective setpoint fell back to the schedule baseline.

    Post-fix: when the feed is unavailable, the active tier's
    locked offset/override are looked up from PRICE_TIERS and
    carried forward to the layer-priority resolver."""
    from .price_overlay import PriceOverlayState
    _stub_layer_eval_io(monkeypatch, price_cents=None)
    cfg = _make_schedule_check_cfg()
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
    # Label preserved (was already correct).
    assert inputs.price_tier_name == "scarcity"
    # Setpoint contribution preserved (the fix): scarcity tier has
    # cool_setpoint_override_f=85 in PRICE_TIERS.
    assert inputs.price_override_f == 85
    # Offset is 0 for scarcity (since it uses override, not offset).
    assert inputs.price_offset_f == 0


def test_price_tier_elevated_offset_preserved_across_feed_gap(monkeypatch):
    """Symmetric for the elevated tier (offset=+3, override=None)."""
    from .price_overlay import PriceOverlayState
    _stub_layer_eval_io(monkeypatch, price_cents=None)
    cfg = _make_schedule_check_cfg()
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
    assert inputs.price_offset_f == 3   # locked elevated offset
    assert inputs.price_override_f is None


def test_normal_tier_unaffected_by_feed_gap(monkeypatch):
    """Normal tier produces zero contribution regardless of feed
    availability -- the fix should not introduce a phantom offset
    when there was no active tier to begin with."""
    _stub_layer_eval_io(monkeypatch, price_cents=None)
    cfg = _make_schedule_check_cfg()
    firing = FiringState()   # default state: tier=normal
    write_api = MagicMock()
    now_local = datetime(2026, 7, 15, 14, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    inputs = _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing, now_local)
    assert inputs.price_tier_name == "normal"
    assert inputs.price_offset_f == 0
    assert inputs.price_override_f is None


# ---- P1.3 (reviewer-flagged 2026-05-11): failed-push guard gating ----------


def test_failed_action_does_not_update_pushed_guard(monkeypatch):
    """Reviewer-flagged 2026-05-11: when a live scheduled push fails,
    ``firing.last_pushed_effective_cool_f`` MUST NOT be updated to the
    target setpoint. Otherwise a later mid-period repush sees
    ``effective == last_pushed`` and silently skips, leaving the
    thermostat at whatever value WAS successfully pushed (which is
    the stale prior schedule action's setpoint).

    Reproducer: starting with last_pushed=78 (prior schedule action's
    successful push), the 19:00 HOT_RECOVER action targets 75 but
    ``execute_action`` returns ``(False, "C4 timeout")``. The guard
    must stay at 78, not move to 75."""
    firing = FiringState(
        last_pushed_effective_cool_f=78,
        last_schedule_cool_f=78,
        last_action_label="HOT_COAST",
    )
    now_local = datetime(2026, 7, 15, 19, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing,
        execute_result=(False, "Control4 timeout"), dry_run=False,
    )
    # Guard MUST stay at the last successfully-pushed value, not the
    # failed-target value.
    assert firing.last_pushed_effective_cool_f == 78


def test_successful_action_updates_pushed_guard(monkeypatch):
    """Symmetric: a successful live scheduled push DOES update the
    guard to the supervisor-approved cool setpoint, so the next
    mid-period evaluation has the correct reference."""
    firing = FiringState(
        last_pushed_effective_cool_f=78,
        last_schedule_cool_f=78,
        last_action_label="HOT_COAST",
    )
    now_local = datetime(2026, 7, 15, 19, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing,
        execute_result=(True, None), dry_run=False,
    )
    # NORMAL_SCHEDULE at 19:00 has RECOVER 75. So the guard moves to 75.
    # But schedule_for here is determined by day_type stub (NORMAL),
    # which has RECOVER at 19:00 with cool=75 -- so sup_cool == 75.
    assert firing.last_pushed_effective_cool_f == 75


def test_dry_run_action_updates_pushed_guard(monkeypatch):
    """Dry-run mode is an intentional no-push; the guard MUST still
    update to keep the mid-period re-push path's Arm-A guard
    correctly populated. Pre-PR #50 the guard was gated on
    ``not cfg.dry_run`` and left None across dry-run weeks, producing
    phantom MID_PERIOD_REPUSH audit rows every tick. This test pins
    that dry-run still moves the guard."""
    firing = FiringState(
        last_pushed_effective_cool_f=None,
        last_action_label="",
    )
    now_local = datetime(2026, 7, 15, 19, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing,
        execute_result=(False, None),  # dry-run: no error, no apply
        dry_run=True,
    )
    assert firing.last_pushed_effective_cool_f == 75


# ---- P1.2 (reviewer-flagged 2026-05-11): per-tick supervisor continuity ---


async def test_emergency_supervisor_fires_during_sustained_hold(monkeypatch):
    """P1.2 (reviewer-flagged 2026-05-11): the safety supervisor's
    emergency rule (indoor >= 86F -> override cool to 74F) MUST fire
    even when the layer-resolved effective setpoint hasn't changed.

    Pre-fix the mid-period path skipped the thermostat read when
    ``effective == last_pushed``, so during a sustained 85F shutoff
    hold the supervisor never observed the indoor temp climbing past
    the emergency threshold. House could sit at 85F effective with
    indoor 87F+ for hours.

    Post-fix: snapshot read happens every tick. Supervisor runs every
    tick. If indoor >= 86F during a no-change tick, the supervisor
    escalates to emergency 74F and we push the override.
    """
    from .app import LayerInputs

    # Stub read_thermostat_snapshot to return indoor=87 (emergency
    # threshold = 86, so this triggers the override).
    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 87.0,
                            "hvac_mode": "Cool",
                            "cool_setpoint_f": 85,
                            "heat_setpoint_f": 65,
                        }))
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())

    cfg = MagicMock()
    cfg.influx_bucket = "energy"
    cfg.dry_run = False

    c4, _climate = _mock_c4_client()
    write_api = MagicMock()
    firing = FiringState(
        last_schedule_cool_f=80,           # HOT_COAST baseline
        last_action_label="HOT_COAST",
        last_pushed_effective_cool_f=85,    # scarcity tier active, last push was 85
    )
    # Scarcity tier still active -- layer-resolved effective stays at 85.
    layer_inputs = LayerInputs(
        price_tier_name="scarcity",
        price_offset_f=0,
        price_override_f=85,                # scarcity override
        price_prev_tier="scarcity",
        current_price_cents=25.0,
        fivecp_active=False,
        fivecp_scopes_fired=(),
        fivecp_load_mw=0.0,
        fivecp_derivative=0.0,
        fivecp_forecast_peak=0.0,
        fivecp_season_5th_mw=20375.0,
        fivecp_data_available=True,
    )
    now_local = datetime(2026, 7, 15, 16, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    await _push_layer_change_mid_period(
        cfg, c4, write_api, firing, "NORMAL",
        layer_inputs, today_dewpoint_f=60.0, override_note="",
        now_local=now_local,
    )

    # The push MUST have fired: supervisor escalated to emergency 74F,
    # which differs from the last_pushed guard (85), so the no-push
    # short-circuit doesn't apply.
    assert app.execute_action.await_count == 1   # type: ignore[attr-defined]
    # The pushed value was the supervisor's emergency cool=74, not the
    # raw layer-resolved 85.
    push_call = app.execute_action.await_args   # type: ignore[attr-defined]
    sup_cool_pushed = push_call.args[2]  # third positional arg
    assert sup_cool_pushed == 74
    # Guard tracks the supervisor's chosen value, not the raw effective.
    assert firing.last_pushed_effective_cool_f == 74


async def test_no_push_when_supervisor_approves_unchanged_layer(monkeypatch):
    """Symmetric guard: the no-push short-circuit DOES apply when the
    supervisor approves the layer-resolved effective AND it matches
    the last-pushed value. P1.2 fix must not over-eagerly push every
    tick during a normal sustained period."""
    from .app import LayerInputs

    monkeypatch.setattr(app, "read_thermostat_snapshot",
                        AsyncMock(return_value={
                            "indoor_temp_f": 73.0,   # comfortable
                            "hvac_mode": "Cool",
                            "cool_setpoint_f": 79,
                            "heat_setpoint_f": 65,
                        }))
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())

    cfg = MagicMock()
    cfg.influx_bucket = "energy"
    cfg.dry_run = False

    c4, _climate = _mock_c4_client()
    write_api = MagicMock()
    firing = FiringState(
        last_schedule_cool_f=79,
        last_action_label="COAST",
        last_pushed_effective_cool_f=79,
    )
    layer_inputs = LayerInputs(
        price_tier_name="normal",
        price_offset_f=0,
        price_override_f=None,
        price_prev_tier="normal",
        current_price_cents=5.0,
        fivecp_active=False,
        fivecp_scopes_fired=(),
        fivecp_load_mw=0.0,
        fivecp_derivative=0.0,
        fivecp_forecast_peak=0.0,
        fivecp_season_5th_mw=20375.0,
        fivecp_data_available=True,
    )
    now_local = datetime(2026, 7, 15, 13, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    await _push_layer_change_mid_period(
        cfg, c4, write_api, firing, "NORMAL",
        layer_inputs, today_dewpoint_f=60.0, override_note="",
        now_local=now_local,
    )

    # Supervisor approved 79 (same as last_pushed), no push fired.
    assert app.execute_action.await_count == 0   # type: ignore[attr-defined]
    # Guard stays at 79.
    assert firing.last_pushed_effective_cool_f == 79


async def test_supervisor_runs_thermostat_read_every_mid_period_tick(monkeypatch):
    """Belt-and-braces: the thermostat snapshot read MUST happen on
    every mid-period tick (after the baseline-exists check), even
    when the layer-resolved effective hasn't changed. Pre-fix the
    read was conditional on a setpoint change and the emergency rule
    could not observe indoor temperature during long shutoff holds."""
    from .app import LayerInputs

    read_mock = AsyncMock(return_value={
        "indoor_temp_f": 73.0,
        "hvac_mode": "Cool",
        "cool_setpoint_f": 79,
        "heat_setpoint_f": 65,
    })
    monkeypatch.setattr(app, "read_thermostat_snapshot", read_mock)
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())

    cfg = MagicMock()
    cfg.influx_bucket = "energy"
    cfg.dry_run = False

    c4, _climate = _mock_c4_client()
    write_api = MagicMock()
    firing = FiringState(
        last_schedule_cool_f=79,
        last_action_label="COAST",
        last_pushed_effective_cool_f=79,   # nothing will change this tick
    )
    layer_inputs = LayerInputs(
        price_tier_name="normal",
        price_offset_f=0,
        price_override_f=None,
        price_prev_tier="normal",
        current_price_cents=5.0,
        fivecp_active=False,
        fivecp_scopes_fired=(),
        fivecp_load_mw=0.0,
        fivecp_derivative=0.0,
        fivecp_forecast_peak=0.0,
        fivecp_season_5th_mw=20375.0,
        fivecp_data_available=True,
    )
    now_local = datetime(2026, 7, 15, 13, 30,
                          tzinfo=ZoneInfo("America/Chicago"))

    await _push_layer_change_mid_period(
        cfg, c4, write_api, firing, "NORMAL",
        layer_inputs, today_dewpoint_f=60.0, override_note="",
        now_local=now_local,
    )

    # Even though no push happened, the thermostat snapshot WAS read.
    # This is the supervisor-continuity invariant: the safety layer
    # MUST observe the thermostat every tick to catch emergencies.
    assert read_mock.await_count == 1


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
    action = ScheduleAction(13, 0, "COAST", cool_setpoint_f=78,
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
    action = ScheduleAction(13, 0, "COAST", cool_setpoint_f=78)
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
    action = ScheduleAction(13, 0, "COAST", cool_setpoint_f=78)
    when_ct = datetime(2026, 6, 20, 13, 0)  # mid-Arm-2 (Arm B)

    applied, error = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
    )
    assert applied is True
    assert error is None
    climate.set_cool_setpoint_f.assert_awaited_once_with(78)


async def test_experiment_mode_outside_window_does_not_write(monkeypatch):
    """Spec §3 experiment outside the 2026-06-01..2026-11-16 window:
    no writes. No implicit "preserve pre-experiment" fallback."""
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, "COAST", cool_setpoint_f=78)

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
    action = ScheduleAction(13, 0, "COAST", cool_setpoint_f=78)
    # During what would be Arm A in experiment mode, production still writes.
    when_ct = datetime(2026, 6, 5, 13, 0)

    applied, error = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
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
    monkeypatch.setenv("CONTROL4_EMAIL", "x@example.com")
    monkeypatch.setenv("CONTROL4_PASSWORD", "x")
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
    monkeypatch.setenv("CONTROL4_EMAIL", "x@example.com")
    monkeypatch.setenv("CONTROL4_PASSWORD", "x")
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
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
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
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    line = _line_protocol(write_api)
    assert "arm=B" in line
    assert "scheduler_mode=experiment" in line
    assert 'mode_actual="B-active"' in line


def test_write_arm_mode_writes_b_fallback_when_feed_stale(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 20, 13, 0)  # Arm 2 (B)
    feeds = {"price": True, "weather": False, "pjm_capacity_risk": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    line = _line_protocol(write_api)
    assert "arm=B" in line
    assert 'mode_actual="B-fallback"' in line


def test_write_arm_mode_writes_b_down_when_controller_not_alive(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    write_api = MagicMock()
    when_ct = datetime(2026, 6, 20, 13, 0)
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
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
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
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
    feeds = {"price": True, "weather": True}
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
    feeds = {"price": True, "weather": True}
    app.write_arm_mode(write_api, "energy", when_ct, feeds, controller_alive=True)
    line = _line_protocol(write_api)
    assert "arm=A" in line
    assert 'mode_actual="A-active"' in line


# ---- Required-feeds-for-arm-mode helper (spec §5 + §5.1) ------------------


def test_required_feeds_never_includes_pjm_after_5cp_demotion():
    """Binding spec §11 #14: 5CP demoted from live-setpoint authority,
    so PJM capacity-risk health is no longer a required input for B-active
    classification — at ANY date, inside or outside the cooling-season
    window. Live control depends on price + weather only. PJM health is
    still logged in the FULL feed-health audit (write_input_feed_health),
    just not used to down-classify B-active."""
    # Inside the (former) capacity-risk window: still not required.
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 7, 15, 13, 0),
        price_feed_healthy=True,
        weather_ok=True,
        pjm_capacity_risk_ok=True,
    )
    assert feeds == {"price": True, "weather": True}
    # Outside the window: still not required.
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 10, 15, 13, 0),
        price_feed_healthy=True,
        weather_ok=True,
        pjm_capacity_risk_ok=False,
    )
    assert feeds == {"price": True, "weather": True}
    # September 30 boundary: still not required.
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 9, 30, 23, 59),
        price_feed_healthy=True,
        weather_ok=True,
        pjm_capacity_risk_ok=False,
    )
    assert "pjm_capacity_risk" not in feeds
    # October 1 boundary: still not required.
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 10, 1, 0, 0),
        price_feed_healthy=True,
        weather_ok=True,
        pjm_capacity_risk_ok=False,
    )
    assert "pjm_capacity_risk" not in feeds


def test_required_feeds_propagates_unhealthy_flags():
    """Unhealthy price/weather flags propagate through to the feed dict
    used for B-active classification (PJM no longer in the dict per
    §11 #14)."""
    feeds = app.required_feeds_for_arm_mode(
        when_ct=datetime(2026, 7, 15, 13, 0),
        price_feed_healthy=False,
        weather_ok=True,
        pjm_capacity_risk_ok=True,
    )
    assert feeds == {"price": False, "weather": True}


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
    feeds = {"price": True, "weather": False, "pjm_capacity_risk": True}
    app.write_input_feed_health(write_api, "energy", when_ct, feeds)
    assert write_api.write.call_count == 3

    lines = [
        c.kwargs.get("record").to_line_protocol()
        for c in write_api.write.call_args_list
    ]
    health_by_feed = {}
    for line in lines:
        # parse "hvac.input_feed_health,feed=NAME healthy=true|false ts"
        assert line.startswith("hvac.input_feed_health,feed=")
        feed = line.split("feed=", 1)[1].split(" ", 1)[0]
        healthy = "healthy=true" in line
        health_by_feed[feed] = healthy
    assert health_by_feed == {"price": True, "weather": False, "pjm_capacity_risk": True}


def test_write_input_feed_health_logs_pjm_outside_operating_window_too():
    """Spec §5.1: PJM capacity-risk health is STILL logged in feed-health
    provenance even outside the capacity-risk operating window. The
    feed-health audit is independent of the B-active classification."""
    write_api = MagicMock()
    when_ct = datetime(2026, 10, 15, 13, 0)  # outside capacity-risk window
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": False}
    app.write_input_feed_health(write_api, "energy", when_ct, feeds)
    assert write_api.write.call_count == 3
    lines = [
        c.kwargs.get("record").to_line_protocol()
        for c in write_api.write.call_args_list
    ]
    pjm_line = next(line for line in lines if "feed=pjm_capacity_risk" in line)
    assert "healthy=false" in pjm_line


def test_write_input_feed_health_empty_dict_is_noop():
    write_api = MagicMock()
    app.write_input_feed_health(write_api, "energy", datetime(2026, 6, 20, 13, 0), {})
    write_api.write.assert_not_called()


# ---- Comprehensive dry-run guard audit (spec §11 #9) ----------------------
#
# Every execute_action branch MUST short-circuit before any Control4
# write when dry_run=True, regardless of action label, hvac_mode, or
# release-hold flag. This parametrized test enumerates every action
# in every schedule (NORMAL/HOT/MILD/HOT_STREAK_DAY1) plus
# synthetic mid-period-repush and vacation actions and asserts no
# Control4 mutator was awaited.
#
# Tests run with SCHEDULER_MODE=production so the top-level mode gate
# (Task 1.2) doesn't pre-empt the audit; the dry_run gate is what we
# are stress-testing here.


def _all_schedule_actions() -> list[Any]:
    """Every ScheduleAction across every locked schedule, plus
    synthetic actions used in mid-period repush and vacation paths.
    Each entry is (label, action, cool_setpoint_to_apply, hvac_mode).
    """
    out = []
    for sched in (
        app.NORMAL_SCHEDULE,
        app.HOT_SCHEDULE,
        app.MILD_SCHEDULE,
        app.HOT_STREAK_DAY1_SCHEDULE,
    ):
        for action in sched:
            cool = action.cool_setpoint_f if action.cool_setpoint_f is not None else 0
            out.append((action.label, action, cool, "Cool"))
    # Synthetic mid-period repush (constructed in
    # _push_layer_change_mid_period at line ~2002)
    repush = app.ScheduleAction(13, 0, "MID_PERIOD_REPUSH:COAST",
                                  cool_setpoint_f=82, fan_mode=None)
    out.append(("MID_PERIOD_REPUSH:COAST", repush, 82, "Cool"))
    # Synthetic vacation action (vacation_schedule helper)
    vac = app.ScheduleAction(0, 0, "VACATION_HOLD", cool_setpoint_f=80)
    out.append(("VACATION_HOLD", vac, 80, "Cool"))
    # Auto mode hits the same setpoint branch
    auto_action = app.ScheduleAction(13, 0, "COAST", cool_setpoint_f=79)
    out.append(("COAST_AUTO_MODE", auto_action, 79, "Auto"))
    # Heating/Off mode short-circuits ("hvac_mode_not_cooling") - dry_run
    # gate must still pre-empt that path.
    heat_action = app.ScheduleAction(13, 0, "COAST", cool_setpoint_f=79)
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
        heat_setpoint_to_apply=app.HEAT_SETPOINT_FLOOR_F,
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
    action = app.ScheduleAction(13, 0, "COAST", cool_setpoint_f=79)
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
    monkeypatch.setattr("hvac_scheduler.app.write_5cp_state", lambda *a, **k: None)
    monkeypatch.setattr("hvac_scheduler.app.evaluate_for_scope",
                        lambda *a, **k: MagicMock(
                            is_active=False, log_fields={"data_status": "none"},
                            snapshot=None, season_5th_mw=0.0, new_state=MagicMock()))

    cfg = MagicMock(influx_bucket="energy", tz_name="America/Chicago")
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
    monkeypatch.setattr("hvac_scheduler.app.write_5cp_state", lambda *a, **k: None)
    monkeypatch.setattr("hvac_scheduler.app.evaluate_for_scope",
                        lambda *a, **k: MagicMock(
                            is_active=False, log_fields={"data_status": "none"},
                            snapshot=None, season_5th_mw=0.0, new_state=MagicMock()))

    cfg = MagicMock(influx_bucket="energy", tz_name="America/Chicago")
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
    monkeypatch.setattr("hvac_scheduler.app.write_5cp_state", lambda *a, **k: None)
    monkeypatch.setattr("hvac_scheduler.app.evaluate_for_scope",
                        lambda *a, **k: MagicMock(
                            is_active=False, log_fields={"data_status": "none"},
                            snapshot=None, season_5th_mw=0.0, new_state=MagicMock()))

    cfg = MagicMock(influx_bucket="energy", tz_name="America/Chicago")
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


# ---- action_in_effect_at (Task 2: restart baseline reconstruction) ----------

def test_action_in_effect_returns_latest_action_at_or_before_minute():
    # NORMAL: PRE_COOL 06:00, COAST 13:00, RECOVER 19:00, SLEEP 21:00
    act = action_in_effect_at(NORMAL_SCHEDULE, 14 * 60)
    assert act is not None and act.label == "COAST"


def test_action_in_effect_none_before_first_action():
    assert action_in_effect_at(NORMAL_SCHEDULE, 3 * 60) is None  # 03:00, first is 06:00


def test_action_in_effect_returns_last_action_at_end_of_day():
    act = action_in_effect_at(NORMAL_SCHEDULE, 24 * 60 - 1)
    assert act is not None and act.label == "SLEEP"


def test_action_in_effect_returns_release_hold_when_it_is_latest():
    # release_hold is no longer used by any day-type schedule, but
    # action_in_effect_at must still surface it when it is the latest action.
    sched = [ScheduleAction(0, 5, "RELEASE_HOLD", release_hold=True)]
    act = action_in_effect_at(sched, 12 * 60)
    assert act is not None
    assert act.label == "RELEASE_HOLD" and act.release_hold is True


# ---- resolve_schedule_for_date_readonly (Task 3: restart baseline reconstruction) --


def test_resolve_schedule_for_date_readonly_uses_stored_decision(monkeypatch):
    """No active override -> falls through to _read_stored_decision; returns
    the schedule for the stored day-type."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_HOT)

    result = app.resolve_schedule_for_date_readonly(
        "2026-07-15", MagicMock(), "energy", []
    )

    assert result == app.HOT_SCHEDULE


def test_resolve_schedule_for_date_readonly_vacation_override_wins(monkeypatch):
    """A vacation override takes precedence over any stored decision."""
    # _read_stored_decision must NOT be called when vacation wins.
    monkeypatch.setattr(
        app, "_read_stored_decision",
        lambda q, b, d: (_ for _ in ()).throw(
            AssertionError("_read_stored_decision must not be called for vacation override"))
    )
    vac_override = app.Override(
        from_date="2026-07-14",
        to_date="2026-07-16",
        cool_setpoint_f=82,
        heat_setpoint_f=60,
    )

    result = app.resolve_schedule_for_date_readonly(
        "2026-07-15", MagicMock(), "energy", [vac_override]
    )

    # vacation_schedule returns ScheduleAction instances with VACATION_AFFIRM label
    assert len(result) > 0
    assert all(a.label == "VACATION_AFFIRM" for a in result)


def test_resolve_schedule_for_date_readonly_returns_empty_when_no_override_and_no_decision(
    monkeypatch,
):
    """No override AND no stored decision -> returns [] (caller treats as no baseline)."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: None)

    result = app.resolve_schedule_for_date_readonly(
        "2026-07-15", MagicMock(), "energy", []
    )

    assert result == []


def test_resolve_schedule_for_date_readonly_day_type_override_parity(monkeypatch):
    """A day_type override short-circuits to the matching schedule without
    touching _read_stored_decision."""
    monkeypatch.setattr(
        app, "_read_stored_decision",
        lambda q, b, d: (_ for _ in ()).throw(
            AssertionError("_read_stored_decision must not be called for day_type override"))
    )
    dt_override = app.Override(
        from_date="2026-07-14",
        to_date="2026-07-16",
        day_type=app.DAYTYPE_HOT,
        # cool_setpoint_f intentionally absent (None) so is_vacation() is False
    )

    result = app.resolve_schedule_for_date_readonly(
        "2026-07-15", MagicMock(), "energy", [dt_override]
    )

    assert result == app.HOT_SCHEDULE


def test_resolve_schedule_for_date_readonly_does_not_call_fetch_today_decision(monkeypatch):
    """Stored-decision branch must use _read_stored_decision, never the
    write-capable fetch_today_decision (P1-B read-only invariant)."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: app.DAYTYPE_NORMAL)
    monkeypatch.setattr(
        app, "fetch_today_decision",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("fetch_today_decision must not be called by the read-only path"))
    )

    result = app.resolve_schedule_for_date_readonly(
        "2026-07-15", MagicMock(), "energy", []
    )

    assert result == app.NORMAL_SCHEDULE


# ---- reconstruct_startup_baseline (Task 4) ------------------------------------

CT = ZoneInfo("America/Chicago")


def test_reconstruct_startup_baseline_daytime_coast_normal(monkeypatch):
    """Daytime restart mid-COAST on a NORMAL day -> baseline 79 (COAST), label COAST."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: DAYTYPE_NORMAL)
    firing = FiringState()
    now = datetime(2026, 7, 1, 14, 0, tzinfo=CT)

    app.reconstruct_startup_baseline(
        firing, NORMAL_SCHEDULE, now, None, MagicMock(), "energy", []
    )

    assert firing.last_schedule_cool_f == 79
    assert firing.last_action_label == "COAST"


def test_reconstruct_startup_baseline_overnight_yesterday_normal(monkeypatch):
    """Overnight (03:00), today NORMAL (no action <= 03:00), yesterday NORMAL ->
    yesterday's last action is SLEEP (73)."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: DAYTYPE_NORMAL)
    firing = FiringState()
    now = datetime(2026, 7, 1, 3, 0, tzinfo=CT)

    app.reconstruct_startup_baseline(
        firing, NORMAL_SCHEDULE, now, None, MagicMock(), "energy", []
    )

    # NORMAL_SCHEDULE's last action by minute is SLEEP at 21:00 -> 73
    assert firing.last_schedule_cool_f == 73
    assert firing.last_action_label == "SLEEP"


def test_reconstruct_startup_baseline_overnight_yesterday_mild(monkeypatch):
    """Overnight (03:00), yesterday MILD -> yesterday's last action is now
    SLEEP=73 (MILD is a real Pi-owned schedule), so the baseline carries 73."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: DAYTYPE_MILD)
    firing = FiringState()
    now = datetime(2026, 7, 1, 3, 0, tzinfo=CT)

    app.reconstruct_startup_baseline(
        firing, NORMAL_SCHEDULE, now, None, MagicMock(), "energy", []
    )

    assert firing.last_schedule_cool_f == 73
    assert firing.last_action_label == "SLEEP"


def test_reconstruct_startup_baseline_mild_today_at_1000(monkeypatch):
    """Restart during a MILD day at 10:00 -> MILD_MORNING (06:00) is in effect
    (MILD is now Pi-owned), so the baseline carries 73."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: DAYTYPE_MILD)
    firing = FiringState()
    now = datetime(2026, 7, 1, 10, 0, tzinfo=CT)

    app.reconstruct_startup_baseline(
        firing, MILD_SCHEDULE, now, None, MagicMock(), "energy", []
    )

    assert firing.last_schedule_cool_f == 73
    assert firing.last_action_label == "MILD_MORNING"


def test_reconstruct_startup_baseline_overnight_no_yesterday_decision(monkeypatch):
    """Overnight (03:00), yesterday has no stored decision -> baseline None."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: None)
    firing = FiringState()
    now = datetime(2026, 7, 1, 3, 0, tzinfo=CT)

    app.reconstruct_startup_baseline(
        firing, NORMAL_SCHEDULE, now, None, MagicMock(), "energy", []
    )

    assert firing.last_schedule_cool_f is None


def test_reconstruct_startup_baseline_leaves_last_pushed_untouched(monkeypatch):
    """After daytime reconstruction, last_pushed_effective_cool_f is still None."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: DAYTYPE_NORMAL)
    firing = FiringState()
    now = datetime(2026, 7, 1, 14, 0, tzinfo=CT)

    app.reconstruct_startup_baseline(
        firing, NORMAL_SCHEDULE, now, None, MagicMock(), "energy", []
    )

    assert firing.last_pushed_effective_cool_f is None


# ---- Task 5: one-shot hook wiring in run_schedule_check -------------------


def test_run_schedule_check_sets_baseline_initialized_and_reconstructs(monkeypatch):
    """First tick with fresh FiringState (baseline_initialized=False,
    last_schedule_cool_f=None) at a daytime hour where a NORMAL schedule
    action is in effect: hook runs, baseline_initialized becomes True,
    and last_schedule_cool_f is populated (not None)."""
    monkeypatch.setattr(app, "_read_stored_decision", lambda q, b, d: DAYTYPE_NORMAL)
    firing = FiringState()
    # 14:00 on a NORMAL day -> COAST action in effect (setpoint 79)
    now_local = datetime(2026, 7, 1, 14, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing, day_type="NORMAL",
    )
    assert firing.baseline_initialized is True
    assert firing.last_schedule_cool_f is not None


def test_run_schedule_check_one_shot_guard_skips_reconstruction_after_first_tick(monkeypatch):
    """Idempotence: with baseline_initialized=True, the hook must NOT
    call reconstruct_startup_baseline even when last_schedule_cool_f is
    None (e.g. after a release_hold). The None baseline must remain None."""
    reconstruct_called = []

    def _fake_reconstruct(*args, **kwargs):
        reconstruct_called.append(True)

    monkeypatch.setattr(app, "reconstruct_startup_baseline", _fake_reconstruct)
    firing = FiringState(baseline_initialized=True, last_schedule_cool_f=None)
    now_local = datetime(2026, 7, 1, 14, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing, day_type="NORMAL",
    )
    assert not reconstruct_called, "reconstruct must not run after baseline_initialized=True"
