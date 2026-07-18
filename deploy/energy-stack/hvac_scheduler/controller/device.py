"""Rev 4 device facade over the whitelisted TCCClimate seam.
Sync interface (the tick is synchronous logic); one network refresh per snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from typing import Any


@dataclass(frozen=True)
class ControlSnapshot:
    schedule_cool: float | None
    cool_setpoint: float
    heat_setpoint: float
    hold_active: bool
    hold_until_minutes: int | None
    indoor_temp: float | None
    humidity: float | None


class TccClimateAdapter:
    """Async end-to-end (Global Constraints: never asyncio.run inside —
    tick runs in an event loop, and TCCClient's aiohttp session binds to
    the first loop it sees)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def snapshot(self) -> ControlSnapshot:
        clim = await self._client.get_climate()
        sched = await clim.get_schedule_cool_f()
        return ControlSnapshot(
            schedule_cool=(float(sched) if sched is not None else None),
            cool_setpoint=float(await clim.get_cool_setpoint_f()),
            heat_setpoint=float(await clim.get_heat_setpoint_f()),
            # != "Off": both "Hold Until" AND "Permanent" are real holds — a
            # Permanent manual hold must hit the foreign-hold (respect) branch,
            # not the engage branch (spec §Manual holds).
            hold_active=(await clim.get_hold_mode()) != "Off",
            hold_until_minutes=await clim.get_hold_until_minutes(),
            indoor_temp=await clim.get_current_temperature_f(),
            humidity=await clim.get_humidity(),
        )

    async def push(self, cool: float, heat: float, until_minutes: int) -> None:
        # snapshot() refreshed the session this tick (loop.py runs push only
        # after a successful snapshot); reuse it — no redundant refresh. Wrap
        # the WHOLE write sequence in ONE reauth so a mid-sequence 401 re-runs
        # all three writes atomically (absolute, idempotent sets) — never
        # leaving a moved heat setpoint with no cool hold. (A non-auth transient
        # mid-sequence still self-heals on the next tick's REV4_ENGAGED_OVER_MANUAL
        # re-push; TCC offers no transactional write.)
        clim = await self._client.get_climate(refresh=False)

        async def _writes() -> None:
            await clim.set_heat_setpoint_f(heat)
            await clim.set_cool_setpoint_f(cool)
            await clim.set_hold_until(dtime(hour=until_minutes // 60,
                                            minute=until_minutes % 60))

        await self._client.call_with_reauth(_writes)

    async def release(self) -> None:
        clim = await self._client.get_climate(refresh=False)
        await self._client.call_with_reauth(lambda: clim.set_hold_mode("Schedule"))
