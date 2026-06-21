"""Unit tests for the TCC adapter (TCCClient / TCCClimate).

The aiosomecomfort library is mocked — no network, no real creds. Covers:
  * read normalization to the existing hvac.thermostat strings (Title-case
    for mode/state/fan, the StatusCool->hold index map, dropped finer °C);
  * write mapping (right aiosomecomfort method, right arg, right hold call);
  * reauth-on-AuthError (re-login + retry once);
  * get_climate wraps its own refresh() in call_with_reauth.

Runs under asyncio_mode=auto (deploy/energy-stack/pytest.ini): tests are
`async def test_*` and `await` directly.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosomecomfort import SessionTimedOut

from .tcc_client import TCCClient, TCCClimate


def _fake_device(**overrides: object) -> MagicMock:
    """A fake aiosomecomfort Device with lowercase/raw values."""
    d = MagicMock()
    d.current_temperature = overrides.get("current_temperature", 74.0)
    d.current_humidity = overrides.get("current_humidity", 43.0)
    d.setpoint_cool = overrides.get("setpoint_cool", 70.0)
    d.setpoint_heat = overrides.get("setpoint_heat", 67.0)
    d.system_mode = overrides.get("system_mode", "auto")
    d.equipment_output_status = overrides.get("equipment_output_status", "cool")
    d.fan_mode = overrides.get("fan_mode", "circulate")
    # Library hold field is StatusCool (HOLD_TYPES index), NOT CurrentSetpointStatus.
    d.raw_ui_data = overrides.get("raw_ui_data", {"StatusCool": 2})
    d.refresh = AsyncMock()
    return d


def _climate_over(dev: MagicMock) -> TCCClimate:
    """A TCCClimate whose client.device is the given fake device."""
    return TCCClimate(MagicMock(device=dev))


async def test_read_normalization_matches_existing_strings() -> None:
    dev = _fake_device()
    c = _climate_over(dev)
    assert await c.get_current_temperature_f() == 74.0
    assert await c.get_cool_setpoint_f() == 70.0
    assert await c.get_heat_setpoint_f() == 67.0
    assert await c.get_hvac_mode() == "Auto"                  # Title-cased
    assert await c.get_hvac_state() == "Cool"                 # from equipment_output_status
    assert await c.get_fan_mode() == "Circulate"
    assert await c.get_humidity() == 43.0


async def test_hvac_state_fan_titlecases() -> None:
    """equipment_output_status='fan' (fan-only, no compressor) -> 'Fan',
    a value outside the historical {Cool,Heat,Off} set (documented)."""
    dev = _fake_device(equipment_output_status="fan")
    c = _climate_over(dev)
    assert await c.get_hvac_state() == "Fan"


@pytest.mark.parametrize("status,expected", [(0, "Off"), (1, "Hold Until"), (2, "Permanent")])
async def test_hold_mode_maps_from_status_index(status: int, expected: str) -> None:
    dev = _fake_device(raw_ui_data={"StatusCool": status})
    c = _climate_over(dev)
    assert await c.get_hold_mode() == expected


async def test_hold_mode_unknown_status_defaults_off() -> None:
    dev = _fake_device(raw_ui_data={"StatusCool": 99})
    c = _climate_over(dev)
    assert await c.get_hold_mode() == "Off"


async def test_write_mapping_calls_aiosomecomfort() -> None:
    dev = _fake_device()
    dev.set_setpoint_cool = AsyncMock()
    dev.set_setpoint_heat = AsyncMock()
    dev.set_fan_mode = AsyncMock()
    dev.set_hold_cool = AsyncMock()
    dev.set_hold_heat = AsyncMock()
    c = _climate_over(dev)

    await c.set_cool_setpoint_f(73)
    dev.set_setpoint_cool.assert_awaited_once_with(73)

    await c.set_heat_setpoint_f(65)
    dev.set_setpoint_heat.assert_awaited_once_with(65)

    await c.set_fan_mode("Circulate")
    dev.set_fan_mode.assert_awaited_once_with("circulate")    # lower-cased for TCC

    await c.set_hold_mode("Permanent")
    dev.set_hold_cool.assert_awaited_once_with(True)
    dev.set_hold_heat.assert_not_awaited()                    # set_hold_cool writes both Status fields

    await c.set_hold_mode("Schedule")                         # release_hold
    dev.set_hold_cool.assert_awaited_with(False)


async def test_call_with_reauth_relogins_and_retries() -> None:
    client = TCCClient("u", "p", 4750378)
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise SessionTimedOut("expired")
        return "ok"

    async def fake_login() -> None:
        pass

    client._login = fake_login  # type: ignore[method-assign]
    assert await client.call_with_reauth(flaky) == "ok"
    assert calls["n"] == 2


async def test_get_climate_refreshes_inside_reauth() -> None:
    """get_climate must run device.refresh() through call_with_reauth so a
    dead session at the network read re-logs in (the call sites invoke
    get_climate OUTSIDE their own reauth try)."""
    client = TCCClient("u", "p", 4750378)
    # Pretend already-logged-in so get_climate skips the initial _login.
    client._client = MagicMock()
    first_dev = _fake_device()
    first_dev.refresh = AsyncMock(side_effect=SessionTimedOut("expired"))
    second_dev = _fake_device()
    second_dev.refresh = AsyncMock()
    client.device = first_dev

    login_calls = {"n": 0}

    async def fake_login() -> None:
        login_calls["n"] += 1
        client.device = second_dev      # reauth swaps the device ref

    client._login = fake_login  # type: ignore[method-assign]

    climate = await client.get_climate()
    # First device's refresh raised -> reauth -> second device's refresh ran.
    first_dev.refresh.assert_awaited_once()
    second_dev.refresh.assert_awaited_once()
    assert login_calls["n"] == 1
    # The returned climate reads through the post-reauth device ref.
    assert climate._dev is second_dev


async def test_get_climate_logs_in_when_no_session() -> None:
    """First get_climate with no client/device performs a login."""
    client = TCCClient("u", "p", 4750378)
    dev = _fake_device()

    async def fake_login() -> None:
        client._client = MagicMock()
        client.device = dev

    client._login = fake_login  # type: ignore[method-assign]
    climate = await client.get_climate()
    dev.refresh.assert_awaited_once()
    assert climate._dev is dev
