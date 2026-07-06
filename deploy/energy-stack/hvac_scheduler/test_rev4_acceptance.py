"""Rev 4 outside-in acceptance: the whole spike-only story in one scenario.

Drives the real ControllerLoop through fake seams (price feed + device).
xfail(strict=True) until the implementation is complete — see plan Task 11.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.xfail(
    strict=True, reason="rev 4 controller not complete yet (plan Tasks 2-11)"
)

UTC = timezone.utc
CT = "America/Chicago"


# ---- Fakes at the two external seams --------------------------------------

@dataclass
class FakePriceFeed:
    """Scripted 5-min buckets: list of (bucket_time_utc, cents). fetch() returns
    the newest bucket at-or-before now, mimicking pricing.fetch_price."""
    buckets: list[tuple[datetime, float]] = field(default_factory=list)

    def latest(self, now_utc: datetime):
        past = [(t, v) for t, v in self.buckets if t <= now_utc]
        if not past:
            return None
        t, v = max(past, key=lambda x: x[0])
        return (v, t, (now_utc - t).total_seconds())


@dataclass
class FakeClimate:
    """Device state machine: program value + one temporary hold slot.
    Mirrors the ControlSnapshot fields device.read_control_snapshot returns."""
    schedule_cool: float = 25.5
    cool_setpoint: float = 25.5
    heat_setpoint: float = 18.5
    hold_active: bool = False
    hold_until_minutes: int | None = None
    indoor_temp: float = 25.0
    humidity: float = 45.0
    read_count: int = 0
    pushes: list[tuple[float, float, int]] = field(default_factory=list)  # (cool, heat, until_min)
    releases: int = 0

    async def snapshot(self):
        self.read_count += 1
        from hvac_scheduler.controller.device import ControlSnapshot
        return ControlSnapshot(
            schedule_cool=self.schedule_cool,
            cool_setpoint=self.cool_setpoint,
            heat_setpoint=self.heat_setpoint,
            hold_active=self.hold_active,
            hold_until_minutes=self.hold_until_minutes,
            indoor_temp=self.indoor_temp,
            humidity=self.humidity,
        )

    async def push(self, cool: float, heat: float, until_minutes: int):
        self.pushes.append((cool, heat, until_minutes))
        self.cool_setpoint = cool
        self.hold_active = True
        self.hold_until_minutes = until_minutes

    async def release(self):
        self.releases += 1
        self.hold_active = False
        self.hold_until_minutes = None
        self.cool_setpoint = self.schedule_cool

    def lapse_if_due(self, now_local_minutes: int):
        """The device's own TTL behavior (edge-triggered, like the real CTK04).
        Mutates state directly — a device lapse is NOT a controller release."""
        if self.hold_active and self.hold_until_minutes is not None \
                and now_local_minutes == self.hold_until_minutes:
            self.hold_active = False
            self.hold_until_minutes = None
            self.cool_setpoint = self.schedule_cool


@dataclass
class TelemetryRecorder:
    actions: list[dict] = field(default_factory=list)
    arm_rows: list[dict] = field(default_factory=list)
    overlay_rows: list[dict] = field(default_factory=list)
    traces: list[dict] = field(default_factory=list)

    def write_action(self, **kw): self.actions.append(kw)
    def write_arm_mode(self, **kw): self.arm_rows.append(kw)
    def write_price_overlay(self, **kw): self.overlay_rows.append(kw)
    def trace(self, **kw): self.traces.append(kw)


def make_cfg(tmp_path):
    from hvac_scheduler.controller.config import load_config
    y = tmp_path / "c.yaml"
    y.write_text(
        "temp_scale: C\n"
        "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
        "elevated_offset: 1.5\n"
        "scarcity_absolute: 29.5\n"
        "heat_floor: 18.5\n"
        "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
        "hold_ttl_minutes: 30\n"
        "release_confirm_buckets: 2\n"
        "stale_release_minutes: 30\n",
        encoding="utf-8",
    )
    return load_config(str(y), temp_scale_env="C")


# ---- The scenario -----------------------------------------------------------

def test_full_spike_story(tmp_path):
    from hvac_scheduler.controller.loop import ControllerLoop

    cfg = make_cfg(tmp_path)
    feed = FakePriceFeed()
    dev = FakeClimate(schedule_cool=25.5, cool_setpoint=25.5)
    tel = TelemetryRecorder()
    loop = ControllerLoop(
        cfg=cfg, price_source=feed, climate=dev, telemetry=tel,
        mode="production", tz_name=CT, data_dir=str(tmp_path),
    )

    # t0 = 2026-07-10 19:00Z = 14:00 CT (midday block on the real device)
    t0 = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)

    def run_tick(now):
        asyncio.run(loop.tick(now))

    # -- 1. Normal tier: cheap fresh price -> ZERO device interaction
    feed.buckets.append((t0 - timedelta(seconds=400), 4.2))
    run_tick(t0)
    assert dev.read_count == 0 and dev.pushes == [] and dev.releases == 0
    assert tel.traces[-1]["new_tier"] == "normal"

    # -- 2. Spike engages on ONE fresh bucket >= 10c: hold at program + 1.5
    t1 = t0 + timedelta(minutes=5)
    feed.buckets.append((t1 - timedelta(seconds=400), 12.8))
    run_tick(t1)
    assert len(dev.pushes) == 1
    cool, heat, until = dev.pushes[-1]
    assert cool == 27.0            # 25.5 + 1.5
    assert heat == 18.5            # heat pinned on every push
    assert until % 15 == 0         # quarter-hour slot
    assert tel.traces[-1]["new_tier"] == "elevated"

    # -- 3. Escalation to scarcity: absolute 29.5
    t2 = t1 + timedelta(minutes=5)
    feed.buckets.append((t2 - timedelta(seconds=400), 40.6))
    run_tick(t2)
    assert dev.pushes[-1][0] == 29.5
    assert tel.traces[-1]["new_tier"] == "scarcity"

    # -- 4. Program block change mid-hold: corrected on the NEXT tick, not at expiry
    dev.schedule_cool = 23.0       # device program steps down (evening block)
    t3 = t2 + timedelta(minutes=1)
    run_tick(t3)                   # scarcity target still 29.5 -> unchanged, no extra push
    n_before = len(dev.pushes)
    # now drop tier to elevated via two confirming buckets below 18c (scarcity release = 20-2)
    feed.buckets.append((t3 + timedelta(minutes=4, seconds=20), 15.0))
    run_tick(t3 + timedelta(minutes=5))
    feed.buckets.append((t3 + timedelta(minutes=9, seconds=20), 15.5))
    run_tick(t3 + timedelta(minutes=10))
    # two consecutive fresh buckets below scarcity-release but above elevated trigger:
    # tier downgrades to elevated, target re-anchors to program(23.0) + 1.5 = 24.5
    assert tel.traces[-1]["new_tier"] == "elevated"
    assert dev.pushes[-1][0] == 24.5
    assert len(dev.pushes) == n_before + 1

    # -- 5. Collapse: two fresh buckets below elevated-release (8c) -> release to normal,
    #       NO device release write (lapse-only): hold left to expire on the device.
    t4 = t3 + timedelta(minutes=15)
    feed.buckets.append((t4 - timedelta(seconds=100), 4.0))  # must be NEWER than step 4's last bucket
    run_tick(t4)
    feed.buckets.append((t4 + timedelta(minutes=4, seconds=20), 3.5))
    run_tick(t4 + timedelta(minutes=5))
    assert tel.traces[-1]["new_tier"] == "normal"
    assert dev.releases == 0               # lapse-only: no release write on spike end
    pushes_at_release = len(dev.pushes)

    # -- 6. Normal ticks after release: no further extension, and the DEVICE
    #       lapses the hold on its own — lapse-home proven, not just no-push.
    run_tick(t4 + timedelta(minutes=6))
    assert len(dev.pushes) == pushes_at_release
    assert dev.hold_active and dev.hold_until_minutes is not None
    dev.lapse_if_due(dev.hold_until_minutes)   # the device's TTL edge fires
    assert not dev.hold_active and dev.cool_setpoint == dev.schedule_cool

    # -- 7. Zombie cleanup: simulate power-cycle-stuck hold (expired, still active),
    #       with the controller's own record persisted from step 4's push.
    rec_path = tmp_path / "own_hold.json"
    assert rec_path.exists()
    rec = json.loads(rec_path.read_text())
    dev.hold_active = True
    dev.hold_until_minutes = rec["until_minutes"]
    dev.cool_setpoint = rec["value"]
    t5 = t4 + timedelta(hours=2)           # long past expiry + grace
    feed.buckets.append((t5 - timedelta(seconds=400), 3.0))
    run_tick(t5)
    assert dev.releases == 1               # released our zombie, once
    assert not json.loads(rec_path.read_text() or "null")  # record cleared
    run_tick(t5 + timedelta(minutes=1))
    assert dev.releases == 1               # never touches the device again

    # -- 8. Manual hold respected: foreign hold warmer than tier target survives
    dev.hold_active = True
    dev.hold_until_minutes = 999           # not ours (no record)
    dev.cool_setpoint = 30.0               # manually warmer than scarcity_absolute
    t6 = t5 + timedelta(minutes=10)
    feed.buckets.append((t6 - timedelta(seconds=400), 45.0))
    run_tick(t6)
    assert dev.pushes[-1][0] != 30.0 or len(dev.pushes) == pushes_at_release + 0
    # precise assertion: no push occurred (target 29.5 is NOT warmer than held 30.0)
    assert len(dev.pushes) == pushes_at_release
