from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .controller.config import load_config
from .controller.loop import ControllerLoop

UTC = timezone.utc

CFG_YAML = (
    "temp_scale: C\n"
    "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
    "elevated_offset: 1.5\nscarcity_absolute: 29.5\nheat_floor: 18.5\n"
    "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
    "hold_ttl_minutes: 30\nrelease_confirm_buckets: 2\nstale_release_minutes: 30\n"
)


@dataclass
class Feed:
    out: tuple | None = None
    def latest(self, now_utc): return self.out


@dataclass
class NeverClimate:
    """Blows up on any access — normal tier must never touch the device."""
    async def snapshot(self): raise AssertionError("device read in normal tier")
    async def push(self, *a): raise AssertionError("device write in normal tier")
    async def release(self): raise AssertionError("device release in normal tier")


@dataclass
class Tel:
    traces: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    arm_rows: list = field(default_factory=list)
    overlay_rows: list = field(default_factory=list)
    def trace(self, **kw): self.traces.append(kw)
    def write_action(self, **kw): self.actions.append(kw)
    def write_arm_mode(self, **kw): self.arm_rows.append(kw)
    def write_price_overlay(self, **kw): self.overlay_rows.append(kw)


def _loop(tmp_path, feed):
    p = tmp_path / "c.yaml"; p.write_text(CFG_YAML, encoding="utf-8")
    cfg = load_config(str(p), temp_scale_env="C")
    return ControllerLoop(cfg=cfg, price_source=feed, climate=NeverClimate(),
                          telemetry=Tel(), mode="shadow", tz_name="America/Chicago",
                          data_dir=str(tmp_path))


def test_normal_tick_traces_and_never_touches_device(tmp_path):
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    feed = Feed(out=(4.2, now - timedelta(seconds=400), 400.0))
    loop = _loop(tmp_path, feed)
    asyncio.run(loop.tick(now))
    t = loop.telemetry.traces[-1]
    assert t["new_tier"] == "normal" and t["price_cents"] == 4.2
    assert t["scheduler_mode"] == "shadow"


def test_missing_feed_is_a_traced_noop(tmp_path):
    loop = _loop(tmp_path, Feed(out=None))
    asyncio.run(loop.tick(datetime(2026, 7, 10, 12, 0, tzinfo=UTC)))
    assert loop.telemetry.traces[-1]["reason_code"] == "REV4_FEED_MISSING"


def test_adapter_maps_seam_to_snapshot():
    import asyncio
    from .controller.device import TccClimateAdapter, ControlSnapshot

    class FakeClim:
        async def get_schedule_cool_f(self): return 25.5
        async def get_cool_setpoint_f(self): return 27.0
        async def get_heat_setpoint_f(self): return 18.5
        async def get_hold_mode(self): return "Hold Until"
        async def get_hold_until_minutes(self): return 1290
        async def get_current_temperature_f(self): return 25.0
        async def get_humidity(self): return 52.0
        pushed = []
        async def set_cool_setpoint_f(self, v): self.pushed.append(("cool", v))
        async def set_heat_setpoint_f(self, v): self.pushed.append(("heat", v))
        async def set_hold_until(self, t): self.pushed.append(("until", t.hour * 60 + t.minute))
        async def set_hold_mode(self, m): self.pushed.append(("mode", m))

    class FakeClient:
        def __init__(self): self.clim = FakeClim()
        async def get_climate(self): return self.clim

    a = TccClimateAdapter(FakeClient())
    s = asyncio.run(a.snapshot())
    assert s == ControlSnapshot(25.5, 27.0, 18.5, True, 1290, 25.0, 52.0)
    asyncio.run(a.push(29.5, 18.5, 14 * 60 + 30))
    assert ("cool", 29.5) in FakeClim.pushed and ("until", 870) in FakeClim.pushed
    asyncio.run(a.release())
    assert ("mode", "Schedule") in FakeClim.pushed
