"""Tests for the HVAC scheduler — focused on pure-logic functions and the
release_hold action plumbing.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

import app


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
from app import (
    COOL_SHUTOFF_F,
    DAYTYPE_HOT,
    DAYTYPE_MILD,
    DAYTYPE_NORMAL,
    FiringState,
    LayerResolution,
    MILD_SCHEDULE,
    NORMAL_SCHEDULE,
    HOT_SCHEDULE,
    ScheduleAction,
    _classify_one_day,
    _evaluate_layer_inputs,
    decide_day_type,
    execute_action,
    fetch_today_decision,
    resolve_cool_setpoint,
    resolve_layer_priority,
    run_decision_revisit,
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


def test_resolve_layer_priority_5cp_active_uses_shutoff_setpoint():
    """Spec §4 case 4: schedule 80°F, 5CP active -> effective 85°F."""
    r = resolve_layer_priority(
        schedule_cool_f=80,
        fivecp_active=True,
    )
    assert r.effective_cool_f == COOL_SHUTOFF_F
    assert r.fivecp_cool_f == COOL_SHUTOFF_F


def test_resolve_layer_priority_5cp_and_scarcity_overlap_no_double_up():
    """Spec §4 case 5: schedule 80°F, scarcity AND 5CP both active --
    both want 85°F; effective is still 85°F, not 90°F or any double-up."""
    r = resolve_layer_priority(
        schedule_cool_f=80,
        price_overlay_tier="scarcity",
        price_override_f=85,
        fivecp_active=True,
    )
    assert r.effective_cool_f == 85


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
    """Below the 75F NORMAL threshold -> MILD (no active scheduling)."""
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
    from app import DAYTYPE_HOT_STREAK_DAY1
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


def test_decide_day_type_multi_day_streak_path_still_works():
    """Existing multi-day path (§7 added an alternative escalation path,
    didn't replace this one). When BOTH days HOT, still escalates to
    HOT_STREAK_DAY1 with the older reason string for backwards-compat."""
    from app import DAYTYPE_HOT_STREAK_DAY1
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


def test_mild_schedule_releases_hold_at_start_of_day():
    """MILD must have an early action that clears the previous day's
    Permanent hold — this is the bug fix."""
    assert len(MILD_SCHEDULE) == 1
    a = MILD_SCHEDULE[0]
    assert a.release_hold is True
    # Fires shortly after midnight so the thermostat baseline takes over for
    # the rest of the MILD day.
    assert (a.hour, a.minute) == (0, 5)


def test_existing_schedules_still_have_setpoints():
    """Sanity check: the optional cool_setpoint_f default doesn't accidentally
    leave NORMAL/HOT actions setpoint-less."""
    for action in NORMAL_SCHEDULE + HOT_SCHEDULE:
        assert action.release_hold is False
        assert action.cool_setpoint_f is not None
        assert action.cool_setpoint_f > 0


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


def _mock_c4_client():
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
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.5)
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

    captured = {}

    def _forecast(query_api, bucket, period):
        captured.setdefault("queried", []).append(period)
        if period == "today":
            return {"high_f": 96.0}
        if period == "tomorrow":
            return {"high_f": 97.0}
        return None

    monkeypatch.setattr(app, "fetch_latest_forecast", _forecast)
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: None)
    write_api = MagicMock()

    fetch_today_decision(MagicMock(), write_api, "energy", "2026-07-15")

    # Both today AND tomorrow must be queried so streak detection works.
    assert "today" in captured["queried"]
    assert "tomorrow" in captured["queried"]


# ---- run_decision_revisit: intra-day forecast re-evaluation ---------------


def _make_revisit_cfg(bucket: str = "energy", tz_name: str = "America/Chicago"):
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
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.0)
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
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.0)
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
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.0)
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
    from app import DAYTYPE_HOT_STREAK_DAY1

    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_NORMAL)

    def _forecast(q, b, period):
        if period == "today":
            return {"high_f": 96.0, "max_dewpoint_f": 70.0, "is_heat_advisory": 0}
        if period == "tomorrow":
            return {"high_f": 97.0, "max_dewpoint_f": 71.0, "is_heat_advisory": 0}
        return None

    monkeypatch.setattr(app, "fetch_latest_forecast", _forecast)
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.0)
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
    from app import DAYTYPE_HOT_STREAK_DAY1

    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_HOT)

    def _forecast(q, b, period):
        if period == "today":
            return {"high_f": 95.0, "max_dewpoint_f": 70.0, "is_heat_advisory": 0}
        if period == "tomorrow":
            return {"high_f": 80.0, "max_dewpoint_f": 60.0, "is_heat_advisory": 0}
        return None

    monkeypatch.setattr(app, "fetch_latest_forecast", _forecast)
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.0)
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
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.0)
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
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.0)
    write_api = MagicMock()

    run_decision_revisit(_make_revisit_cfg(), MagicMock(), write_api, "2026-07-15")

    # Wrote the freshly-classified decision.
    write_api.write.assert_called_once()



# ---- safety supervisor ----------------------------------------------------

from safety_supervisor import (  # noqa: E402
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
    """Even if the schedule says cool=85 (HOT_5CP_SHUTOFF), if indoor
    temp is already 87°F, force the AC to engage at the emergency target."""
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


# ---- §Critical #2: per-tick layer evaluation ------------------------------


def _make_schedule_check_cfg(bucket: str = "energy",
                              tz_name: str = "America/Chicago",
                              dry_run: bool = True):
    cfg = MagicMock()
    cfg.influx_bucket = bucket
    cfg.tz_name = tz_name
    cfg.dry_run = dry_run
    return cfg


def _stub_layer_eval_io(monkeypatch, *,
                         price_cents: float | None = 5.0,
                         zone_load: float | None = 14000.0,
                         derivative: float = 0.0,
                         forecast_peak: float | None = 17000.0,
                         season_5th: float = 130000.0):
    """Stub the InfluxDB IO that _evaluate_layer_inputs makes. Lets tests
    drive the price/load/forecast inputs without spinning up Flux."""
    monkeypatch.setattr(app, "fetch_latest_comed",
                        lambda q, b: price_cents)

    if zone_load is not None:
        from pjm_5cp import ZoneLoadSnapshot
        snapshot = ZoneLoadSnapshot(
            current_mw=zone_load,
            derivative_mw_per_hour=derivative,
            observed_at_utc=datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc),
        )
    else:
        snapshot = None
    monkeypatch.setattr(app, "fetch_zone_live", lambda q, b: snapshot)
    monkeypatch.setattr(app, "fetch_forecast_peak_today",
                        lambda q, b: forecast_peak)
    monkeypatch.setattr(app, "update_season_5th_highest",
                        lambda q, b, s: season_5th)


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
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 7.0)
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
    monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 12.0)
    _evaluate_layer_inputs(MagicMock(), write_api, cfg, firing,
                            now_local + timedelta(minutes=1))
    transition_count = sum(
        1 for c in write_api.write.call_args_list
        if "hvac.price_overlay" in c.kwargs.get("record").to_line_protocol()
    )
    assert transition_count == 1
