"""Rev 4 outside-in acceptance: the whole spike-only story in one scenario.

Drives the real ControllerLoop through fake seams (price feed + device).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

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
    device_rows: list[dict] = field(default_factory=list)

    def write_action(self, **kw): self.actions.append(kw)
    def write_arm_mode(self, **kw): self.arm_rows.append(kw)
    def write_price_overlay(self, **kw): self.overlay_rows.append(kw)
    def trace(self, **kw): self.traces.append(kw)
    def write_device_status(self, **kw): self.device_rows.append(kw)


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

    # -- 5. Collapse: two fresh buckets below elevated-release (8c) -> release
    #       to normal, and the controller WRITES ONE ACTIVE RELEASE (rev 4.1):
    #       the device goes home immediately, no lapse tail.
    t4 = t3 + timedelta(minutes=15)
    feed.buckets.append((t4 - timedelta(seconds=100), 4.0))  # must be NEWER than step 4's last bucket
    run_tick(t4)
    assert dev.releases == 0               # first cheap bucket: still confirming
    feed.buckets.append((t4 + timedelta(minutes=4, seconds=20), 3.5))
    run_tick(t4 + timedelta(minutes=5))
    assert tel.traces[-1]["new_tier"] == "normal"
    assert dev.releases == 1               # ONE active release write on spike end
    assert not dev.hold_active and dev.cool_setpoint == dev.schedule_cool
    rec_path = tmp_path / "own_hold.json"
    assert not json.loads(rec_path.read_text() or "null")  # record cleared on success
    assert tel.actions[-1]["action_label"] == "RELEASE"
    assert tel.actions[-1]["setpoint_reason"] == "REV4_SPIKE_END_RELEASE"
    pushes_at_release = len(dev.pushes)

    # -- 6. Normal ticks after the release: nothing left to do — no pushes,
    #       no further releases, no device reads (record is gone).
    reads_after_release = dev.read_count
    run_tick(t4 + timedelta(minutes=6))
    assert len(dev.pushes) == pushes_at_release
    assert dev.releases == 1
    assert dev.read_count == reads_after_release

    # -- 7. Dead-controller safety net + zombie cleanup. The TTL lapse is
    #       SOLELY the deadman path (rev 4.1): simulate a controller that
    #       pushed, then died across expiry. A powered device lapses at its
    #       edge; a power cut across the edge leaves a ZOMBIE the restarted
    #       controller must actively release.
    from hvac_scheduler.controller.ownhold import OwnHoldRecord, save_record
    t5 = t4 + timedelta(hours=2)
    save_record(str(tmp_path), OwnHoldRecord(
        value=27.0, until_minutes=870,
        expiry_utc=(t5 - timedelta(hours=1)).isoformat()))  # long past expiry+grace
    dev.hold_active = True
    dev.hold_until_minutes = 870
    dev.cool_setpoint = 27.0
    dev.lapse_if_due(870)                  # powered device: TTL edge fires on its own
    assert not dev.hold_active and dev.cool_setpoint == dev.schedule_cool
    dev.hold_active = True                 # ...but a power cut MISSED the edge
    dev.hold_until_minutes = 870
    dev.cool_setpoint = 27.0
    feed.buckets.append((t5 - timedelta(seconds=400), 3.0))
    run_tick(t5)
    assert dev.releases == 2               # released our zombie, once
    assert not json.loads(rec_path.read_text() or "null")  # record cleared
    run_tick(t5 + timedelta(minutes=1))
    assert dev.releases == 2               # never touches the device again

    # -- 8. Manual hold respected: foreign hold warmer than tier target survives
    #       (own record is None after the release bookkeeping above).
    dev.hold_active = True
    dev.hold_until_minutes = 999           # not ours (no record)
    dev.cool_setpoint = 30.0               # manually warmer than scarcity_absolute
    t6 = t5 + timedelta(minutes=10)
    feed.buckets.append((t6 - timedelta(seconds=400), 45.0))
    run_tick(t6)
    # precise assertion: no push occurred (target 29.5 is NOT warmer than held 30.0)
    assert len(dev.pushes) == pushes_at_release
    assert dev.releases == 2               # and no release either: manual holds never touched


# ---- Feature-level north star: failure telemetry + liveness ----------------
#
# Spec §Telemetry -> Failure telemetry. Stays xfail(strict=True) across every
# PR boundary of the device_status feature; the marker comes off only when the
# liveness decouple lands (plan 2026-08-07, Task 7). Never `skip` -- a skip is
# silent across PRs and trains everyone to ignore the north star.


@pytest.mark.xfail(strict=True,
                   reason="feature-complete only at Task 7 (liveness decouple)")
def test_read_outage_records_attempts_and_preserves_liveness(tmp_path):
    """A sustained device-read outage must (a) record one read attempt row per
    tick, all failures, so a reader can count consecutive failures of a kind,
    and (b) leave the liveness beacon intact, so the watchdog never
    false-trips a live controller."""
    from hvac_scheduler.controller.loop import ControllerLoop

    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)

    class BoomClimate:
        async def snapshot(self):
            raise TimeoutError()

    # A fresh 12.8c bucket 60s before each tick -> elevated tier every tick, so
    # every tick genuinely attempts a device read.
    feed = FakePriceFeed(buckets=[
        (now + timedelta(minutes=i) - timedelta(seconds=60), 12.8) for i in range(3)
    ])
    tel = TelemetryRecorder()
    loop = ControllerLoop(cfg=make_cfg(tmp_path), price_source=feed,
                          climate=BoomClimate(), telemetry=tel,
                          mode="production", tz_name=CT, data_dir=str(tmp_path))

    for i in range(3):
        asyncio.run(loop.tick(now + timedelta(minutes=i)))

    reads = [r for r in tel.device_rows if r["op"] == "read"]
    assert len(reads) == 3, "one read attempt recorded per tick"
    assert all(r["success"] is False for r in reads)
    assert tel.arm_rows, "liveness must survive a device outage"
