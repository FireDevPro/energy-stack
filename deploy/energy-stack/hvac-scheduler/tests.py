"""Tests for the HVAC scheduler — focused on pure-logic functions and the
release_hold action plumbing.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import app
from app import (
    DAYTYPE_HOT,
    DAYTYPE_MILD,
    DAYTYPE_NORMAL,
    MILD_SCHEDULE,
    NORMAL_SCHEDULE,
    HOT_SCHEDULE,
    ScheduleAction,
    execute_action,
    fetch_today_decision,
    resolve_cool_setpoint,
    run_decision_revisit,
)


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

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=0,
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

    applied, _err = await execute_action(c4, action, cool_setpoint_to_apply=0,
                                          state={"hvac_mode": "Heat"}, dry_run=False)
    assert applied is True
    climate.set_hold_mode.assert_awaited_once_with("Schedule")


async def test_execute_release_hold_dry_run_does_not_call_thermostat():
    c4, climate = _mock_c4_client()
    action = ScheduleAction(0, 5, "MILD_RELEASE_HOLD", release_hold=True)

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=0,
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

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=79,
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

    applied, error = await execute_action(c4, action, cool_setpoint_to_apply=79,
                                           state={"hvac_mode": "Heat"}, dry_run=False)
    assert applied is False
    assert error and "hvac_mode_not_cooling" in error
    climate.set_hold_mode.assert_not_awaited()


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
        return today_forecast if period == "today" else {"high_f": 88.0}

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


def _make_revisit_cfg(bucket: str = "energy"):
    """Build a Config-shaped object with just what run_decision_revisit reads.
    Avoids constructing the full Config (which would need every env-var
    field). app's frozen=True keeps mutability honest; using a Mock for the
    one attribute we need."""
    cfg = MagicMock()
    cfg.influx_bucket = bucket
    return cfg


def test_revisit_no_change_does_not_overwrite(monkeypatch):
    """When the live forecast still classifies as the stored day_type,
    revisit must NOT write a new decision (no-op log only)."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_NORMAL)
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, p: {"high_f": 87.0,
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
    # Today HOT, tomorrow NORMAL — plain HOT, not streak.
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, period: (
                            {"high_f": 96.0, "max_dewpoint_f": 70.0,
                             "is_heat_advisory": 0}
                            if period == "today"
                            else {"high_f": 86.0, "max_dewpoint_f": 60.0,
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
    says 88 (NORMAL). Revisit overwrites so we don't unnecessarily run the
    aggressive HOT shutoff."""
    monkeypatch.setattr(app, "_read_stored_decision",
                        lambda q, b, d: DAYTYPE_HOT)
    monkeypatch.setattr(app, "fetch_latest_forecast",
                        lambda q, b, p: {"high_f": 88.0,
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

