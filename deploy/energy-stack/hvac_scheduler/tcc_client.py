"""Direct Total Connect Comfort thermostat adapter (aiosomecomfort-backed).

``TCCClient`` / ``TCCClimate`` fill the ``ThermostatClient`` / ``ClimateDevice``
seam that ``read_thermostat_snapshot`` / ``read_snapshot`` / ``execute_action``
talk to, replacing the demolished Control4 (``pyControl4``) client. They are
duplicated per-service (this file is copied verbatim into both
``hvac_scheduler`` and ``thermostat_poller``), mirroring how the old C4 client
was duplicated. The call sites are unchanged: same method surface.

Temperature units / scale:
    The seam method names keep the legacy ``_f`` suffix for a behavior-
    identical swap (do NOT rename the Protocol here — that is a separate
    cleanup). They do NOT necessarily carry Fahrenheit. ``aiosomecomfort``
    returns whatever display unit the device is configured for, and the
    commissioning controller is ``temp_scale``-native (the device is being
    set to °C). So these methods are pass-through: the value read from the
    device flows out, and the value written to ``set_*`` flows straight to
    the library. No unit conversion happens here. Setpoint values are already
    on the device's grid (config-enforced 0.5°C), so writes are format-only.

Normalization:
    String fields (``hvac_mode`` / ``hvac_state`` / ``fan_mode`` / ``hold_mode``)
    are normalized to the exact strings the existing ``hvac.thermostat`` data
    uses — Title-case for mode/state/fan, an index map for hold — verified
    against the live data and against ``aiosomecomfort-0.0.36/device.py``.
"""
from __future__ import annotations

import json
from datetime import datetime, time as dtime, timezone
from typing import Any, Awaitable, Callable

import aiohttp
from aiosomecomfort import (
    AIOSomeComfort,
    AuthError,
    SessionTimedOut,
    UnauthorizedError,
)

# Raw StatusCool index (HOLD_TYPES = ["schedule","temporary","permanent"]) ->
# the exact hold_mode strings the Control4 path wrote. Verified against
# aiosomecomfort-0.0.36 device.py:223 (_get_hold reads uiData["Status{Cool,Heat}"]
# indexed into HOLD_TYPES). CurrentSetpointStatus is NOT the library's hold
# field — do not use it.
_HOLD_BY_STATUS = {0: "Off", 1: "Hold Until", 2: "Permanent"}


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


def _title(v: Any) -> str:
    """Title-case a lowercase TCC enum to the existing string format.

    Returns "" when the device value is missing — matches the ClimateDevice
    seam's ``-> str`` contract and the downstream ``str(... or "")`` handling
    (a missing mode/state/fan was never a real value in the historical data).
    """
    if v is None:
        return ""
    return str(v).title()


class TCCClimate:
    """Exposes the ClimateDevice method surface over an aiosomecomfort device.

    Reads come from the device's cached attributes — the single network
    refresh happens in ``TCCClient.get_climate``, so a full snapshot is ONE
    TCC round-trip. The climate holds the ``TCCClient`` (not the raw device)
    so reads/writes always see the current device ref after a reauth swap.
    """

    def __init__(self, client: "TCCClient") -> None:
        self._client = client

    @property
    def _dev(self) -> Any:
        return self._client.device

    # ---- getters (cache reads; no network) ----
    async def get_current_temperature_f(self) -> Any:
        # Pass-through: the device's display unit (°C when set to Celsius).
        # TCC exposes only this single whole-display-unit DispTemperature; no
        # separate finer value (the old C4 get_current_temperature_c is gone —
        # indoor_temp_f_hires now collapses to this whole reading).
        return self._dev.current_temperature

    async def get_cool_setpoint_f(self) -> Any:
        return self._dev.setpoint_cool

    async def get_heat_setpoint_f(self) -> Any:
        return self._dev.setpoint_heat

    async def get_hvac_mode(self) -> str:
        return _title(self._dev.system_mode)

    async def get_hvac_state(self) -> str:
        return _title(self._dev.equipment_output_status)

    async def get_fan_mode(self) -> str:
        return _title(self._dev.fan_mode)

    async def get_hold_mode(self) -> str:
        status: Any = (self._dev.raw_ui_data or {}).get("StatusCool")
        if status in _HOLD_BY_STATUS:
            return _HOLD_BY_STATUS[status]
        return "Off"

    async def get_schedule_cool_f(self) -> Any:
        # Rev 4: the device's current program cool value — the drift anchor and
        # warm-only floor. Present in every uiData read, including mid-hold
        # (verified live 2026-07-03). Pass-through, display unit, no conversion.
        return (self._dev.raw_ui_data or {}).get("ScheduleCoolSp")

    async def get_hold_until_minutes(self) -> Any:
        # Rev 4: the device's dateless hold-until (minutes since midnight).
        # Used ONLY for own-hold record matching (zombie cleanup) — never as a
        # clock (no date; see spec Safety #3).
        return (self._dev.raw_ui_data or {}).get("TemporaryHoldUntilTime")

    async def get_humidity(self) -> Any:
        return self._dev.current_humidity

    # ---- setters (network writes) ----
    async def set_cool_setpoint_f(self, value: float) -> None:
        await self._dev.set_setpoint_cool(value)

    async def set_heat_setpoint_f(self, value: float) -> None:
        await self._dev.set_setpoint_heat(value)

    async def set_fan_mode(self, mode: str) -> None:
        await self._dev.set_fan_mode(str(mode).lower())

    async def set_hold_mode(self, mode: str) -> None:
        # set_hold_cool already writes BOTH StatusCool and StatusHeat in one
        # payload (device.py:_set_hold), so a set_hold_heat companion would be
        # a redundant duplicate round-trip. One call suffices.
        #   "Permanent" -> set_hold_cool(True)   (pins the override)
        #   anything else ("Schedule"/release)   -> set_hold_cool(False)
        permanent = mode == "Permanent"
        await self._dev.set_hold_cool(permanent)

    async def set_hold_until(self, until: dtime) -> None:
        # Timed hold (spec Safety #2: never Permanent). set_hold_cool with a
        # datetime.time writes StatusCool/StatusHeat = "Hold Until" plus BOTH
        # NextPeriod quarter-hour slots in one payload (device.py:_set_hold),
        # so one call covers heat and cool. The library raises on times off
        # the quarter-hour grid — callers floor via controller_core.hold_expiry.
        await self._dev.set_hold_cool(until)


class TCCClient:
    """Session-managed aiosomecomfort access mirroring the ThermostatClient seam.

    Logs in via TCC creds, persists/reuses the aiohttp session, and mirrors
    the old C4Client.call_with_reauth: catch AuthError / SessionTimedOut /
    UnauthorizedError, re-login, retry once. Transient errors (APIRateLimited,
    5xx ConnectionError) propagate to the call site's existing except, get
    logged, and self-heal on the next tick. No backoff loop.
    """

    def __init__(self, email: str, password: str, device_id: int) -> None:
        self._email = email
        self._password = password
        # devices_by_id is keyed on the raw API DeviceID (an int).
        self._device_id = int(device_id)
        self._session: aiohttp.ClientSession | None = None
        self._client: AIOSomeComfort | None = None
        self.device: Any = None

    async def _login(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        self._client = AIOSomeComfort(
            self._email, self._password, session=self._session
        )
        await self._client.login()
        await self._client.discover()
        self.device = self._find_device()
        log("info", "tcc_login_ok", device_id=self._device_id)

    def _find_device(self) -> Any:
        assert self._client is not None
        for loc in self._client.locations_by_id.values():
            if self._device_id in loc.devices_by_id:
                return loc.devices_by_id[self._device_id]
        raise RuntimeError(f"tcc device {self._device_id} not found")

    async def get_climate(self, refresh: bool = True) -> TCCClimate:
        if self._client is None or self.device is None:
            await self._login()
        # refresh() IS the network read for TCC — wrap it in reauth. (Unlike
        # the old C4 path, where get_climate returned a cache and the getters
        # carried the network call; here read_thermostat_snapshot /
        # read_snapshot call get_climate OUTSIDE their try/reauth block, so a
        # dead session at refresh would never re-login otherwise.) The lambda
        # re-reads self.device so a reauth-swapped ref is used on retry.
        # Callers that already refreshed this tick (push()/release() run only
        # after snapshot() per loop.py's `if snap is not None` guard) pass
        # refresh=False to skip the redundant round-trip; the setters read
        # only cached device._data.
        if refresh:
            await self.call_with_reauth(lambda: self.device.refresh())
        return TCCClimate(self)

    async def call_with_reauth(self, coro_fn: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await coro_fn()
        except (AuthError, SessionTimedOut, UnauthorizedError) as exc:
            log("warn", "tcc_session_invalid_reauth", error=str(exc))
            await self._login()
            return await coro_fn()
