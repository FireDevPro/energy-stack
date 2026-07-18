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
        async def get_climate(self, refresh=True): return self.clim
        async def call_with_reauth(self, fn): return await fn()

    a = TccClimateAdapter(FakeClient())
    s = asyncio.run(a.snapshot())
    assert s == ControlSnapshot(25.5, 27.0, 18.5, True, 1290, 25.0, 52.0)
    asyncio.run(a.push(29.5, 18.5, 14 * 60 + 30))
    assert ("cool", 29.5) in FakeClim.pushed and ("until", 870) in FakeClim.pushed
    asyncio.run(a.release())
    assert ("mode", "Schedule") in FakeClim.pushed


def test_push_and_release_route_writes_through_reauth():
    import asyncio
    from .controller.device import TccClimateAdapter

    class FakeClim:
        def __init__(self): self.calls = []
        async def set_heat_setpoint_f(self, v): self.calls.append(("heat", v))
        async def set_cool_setpoint_f(self, v): self.calls.append(("cool", v))
        async def set_hold_until(self, t): self.calls.append(("until", t.hour * 60 + t.minute))
        async def set_hold_mode(self, m): self.calls.append(("mode", m))

    class FakeClient:
        def __init__(self):
            self.clim = FakeClim(); self.reauth_count = 0; self.refresh_requested = None
        async def get_climate(self, refresh=True):
            self.refresh_requested = refresh; return self.clim
        async def call_with_reauth(self, fn):
            self.reauth_count += 1; return await fn()

    c = FakeClient(); a = TccClimateAdapter(c)
    asyncio.run(a.push(29.5, 18.5, 870))
    assert c.refresh_requested is False          # no redundant refresh
    assert c.reauth_count == 3                    # each write wrapped
    assert ("cool", 29.5) in c.clim.calls and ("until", 870) in c.clim.calls
    asyncio.run(a.release())
    assert ("mode", "Schedule") in c.clim.calls
    assert c.reauth_count == 4                     # release write also wrapped


@dataclass
class Dev:
    """Recording device fake mirroring the acceptance test's FakeClimate."""
    schedule_cool: float = 25.5
    cool_setpoint: float = 25.5
    heat_setpoint: float = 18.5
    hold_active: bool = False
    hold_until_minutes: int | None = None
    indoor_temp: float = 25.0
    humidity: float = 45.0
    pushes: list = field(default_factory=list)
    releases: int = 0

    async def snapshot(self):
        from .controller.device import ControlSnapshot
        return ControlSnapshot(
            schedule_cool=self.schedule_cool,
            cool_setpoint=self.cool_setpoint,
            heat_setpoint=self.heat_setpoint,
            hold_active=self.hold_active,
            hold_until_minutes=self.hold_until_minutes,
            indoor_temp=self.indoor_temp,
            humidity=self.humidity,
        )

    async def push(self, cool, heat, until_minutes):
        self.pushes.append((cool, heat, until_minutes))
        self.cool_setpoint = cool
        self.hold_active = True
        self.hold_until_minutes = until_minutes

    async def release(self):
        self.releases += 1
        self.hold_active = False
        self.hold_until_minutes = None
        self.cool_setpoint = self.schedule_cool


def _wired(tmp_path, feed, dev, mode="production"):
    p = tmp_path / "c.yaml"; p.write_text(CFG_YAML, encoding="utf-8")
    cfg = load_config(str(p), temp_scale_env="C")
    return ControllerLoop(cfg=cfg, price_source=feed, climate=dev,
                          telemetry=Tel(), mode=mode, tz_name="America/Chicago",
                          data_dir=str(tmp_path))


def test_humidity_hysteresis_blocks_then_clears(tmp_path):
    from .controller.ownhold import load_record
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    feed = Feed(out=(12.8, now - timedelta(seconds=400), 400.0))
    dev = Dev(humidity=61.0)
    loop = _wired(tmp_path, feed, dev)
    asyncio.run(loop.tick(now))              # RH 61 >= rh_max 61 -> blocked
    assert loop.humidity_blocked and dev.pushes == []
    dev.humidity = 59.0                      # inside the band: stays blocked
    asyncio.run(loop.tick(now + timedelta(minutes=1)))
    assert loop.humidity_blocked and dev.pushes == []
    dev.humidity = 57.9                      # < rh_clear 58 -> clears, engage fires
    asyncio.run(loop.tick(now + timedelta(minutes=2)))
    assert not loop.humidity_blocked
    assert len(dev.pushes) == 1 and dev.pushes[0][0] == 27.0
    # RH crosses rh_max MID-HOLD -> active release write (rev 4.1), record cleared
    dev.humidity = 61.0
    asyncio.run(loop.tick(now + timedelta(minutes=3)))
    assert loop.humidity_blocked
    assert dev.releases == 1 and not dev.hold_active
    row = loop.telemetry.actions[-1]
    assert row["action_label"] == "RELEASE"
    assert row["setpoint_reason"] == "REV4_HUMIDITY_RELEASE"
    assert row["applied"] and row["dry_run"] is False
    assert load_record(str(tmp_path)) is None


def test_humidity_release_shadow_gate_dry_run(tmp_path):
    """Shadow mode: the mid-hold humidity release is decided and traced as a
    dry_run action row but never touches the device or the record."""
    from .controller.ownhold import OwnHoldRecord, load_record, save_record
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    save_record(str(tmp_path), OwnHoldRecord(
        value=27.0, until_minutes=870,
        expiry_utc=(now + timedelta(minutes=25)).isoformat()))
    feed = Feed(out=(12.8, now - timedelta(seconds=400), 400.0))
    dev = Dev(hold_active=True, hold_until_minutes=870, cool_setpoint=27.0,
              humidity=61.0)
    loop = _wired(tmp_path, feed, dev, mode="shadow")
    asyncio.run(loop.tick(now))
    assert dev.releases == 0 and dev.pushes == []
    row = loop.telemetry.actions[-1]
    assert row["dry_run"] is True and not row["applied"]
    assert row["setpoint_reason"] == "REV4_HUMIDITY_RELEASE"
    assert load_record(str(tmp_path)) is not None  # only cleared on a real write


def test_shadow_gate_never_writes_device(tmp_path):
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    feed = Feed(out=(12.8, now - timedelta(seconds=400), 400.0))
    dev = Dev()
    loop = _wired(tmp_path, feed, dev, mode="shadow")
    asyncio.run(loop.tick(now))
    assert dev.pushes == [] and dev.releases == 0
    row = loop.telemetry.actions[-1]
    assert row["dry_run"] is True and not row["applied"]
    assert not (tmp_path / "own_hold.json").exists()  # shadow never saves a record


def test_record_hygiene_clears_stale_record(tmp_path):
    from .controller.ownhold import OwnHoldRecord, load_record, save_record
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    save_record(str(tmp_path), OwnHoldRecord(
        value=27.0, until_minutes=870,
        expiry_utc=(now + timedelta(minutes=25)).isoformat()))
    feed = Feed(out=(4.0, now - timedelta(seconds=400), 400.0))
    dev = Dev(hold_active=False)             # device already lapsed the hold
    loop = _wired(tmp_path, feed, dev)
    asyncio.run(loop.tick(now))
    assert load_record(str(tmp_path)) is None
    assert dev.pushes == [] and dev.releases == 0


def test_arm_mode_throttled(tmp_path):
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    feed = Feed(out=(4.2, now - timedelta(seconds=400), 400.0))
    loop = _loop(tmp_path, feed)
    asyncio.run(loop.tick(now))
    asyncio.run(loop.tick(now + timedelta(minutes=1)))  # < 300s -> throttled
    assert len(loop.telemetry.arm_rows) == 1
    asyncio.run(loop.tick(now + timedelta(minutes=6)))  # >= 300s -> writes again
    assert len(loop.telemetry.arm_rows) == 2


def test_influx_telemetry_action_row_contract():
    from .controller.telemetry import InfluxTelemetry
    from .controller.device import ControlSnapshot

    class Cap:
        lines: list[str] = []
        def write(self, bucket, record): Cap.lines.append(record.to_line_protocol())

    tel = InfluxTelemetry(write_api=Cap(), bucket="energy", unit="C",
                          config_id="abc123", tz_name="America/Chicago")
    snap = ControlSnapshot(25.5, 27.0, 18.5, True, 870, 25.0, 52.0)
    tel.write_action(tier="elevated", action_label="SPIKE", dry_run=False,
                     commanded_cool=27.0, commanded_heat=18.5, schedule_cool=25.5,
                     applied=True, error="", hold_expires_at="2026-07-10T19:30:00+00:00",
                     snapshot_before=snap, setpoint_reason="REV4_ENGAGED",
                     humidity_gated=False)
    lp = Cap.lines[-1]
    assert lp.startswith("hvac.actions,")
    for token in ("commanded_cool=27", "baseline_cool=25.5", "schedule_cool=25.5",
                  "drift=1.5", "applied=1i", 'error=""', "config_id="):
        assert token in lp, token
    assert "dry_run=false" in lp  # tag


def test_arm_mode_row_production_branch():
    from .controller.telemetry import InfluxTelemetry
    from datetime import datetime
    from zoneinfo import ZoneInfo

    class Cap:
        lines: list[str] = []
        def write(self, bucket, record): Cap.lines.append(record.to_line_protocol())

    tel = InfluxTelemetry(write_api=Cap(), bucket="energy", unit="C",
                          config_id="abc", tz_name="America/Chicago")
    tel.write_arm_mode(datetime(2026, 7, 10, 14, 0, tzinfo=ZoneInfo("America/Chicago")),
                       scheduler_mode="production")
    assert Cap.lines[-1].startswith("hvac.arm_mode,")
    assert "mode_actual=" in Cap.lines[-1]


def test_tick_failure_log_downgrades_timeout():
    from .controller.loop import _tick_failure_log
    t = datetime(2026, 7, 11, tzinfo=UTC)
    assert _tick_failure_log(TimeoutError(), t)["level"] == "warn"
    assert _tick_failure_log(ValueError("boom"), t)["level"] == "error"
    rec = _tick_failure_log(TimeoutError(), t)
    assert rec["msg"] == "rev4_tick_failed" and rec["error_type"] == "TimeoutError"
