"""Tests for the HVAC scheduler — focused on pure-logic functions and the
release_hold action plumbing.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app import (
    MILD_SCHEDULE,
    NORMAL_SCHEDULE,
    HOT_SCHEDULE,
    ScheduleAction,
    execute_action,
    resolve_cool_setpoint,
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
