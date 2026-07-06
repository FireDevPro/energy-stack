"""Rev 4 controller loop. Spec: rev 4 §Runtime.

Injection seams (constructor) so the acceptance test drives the real loop:
  price_source.latest(now_utc) -> (cents, bucket_time_utc, age_sec) | None
  climate.snapshot() / .push(cool, heat, until_minutes) / .release()
  telemetry.trace(**kw) / .write_action(**kw) / .write_arm_mode(**kw)
Production wiring (Task 11) adapts the real Influx + TCC clients to these.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import ControllerConfig
from .pricing import PriceSample
from . import tiers


class ControllerLoop:
    def __init__(self, *, cfg: ControllerConfig, price_source: Any, climate: Any,
                 telemetry: Any, mode: str, tz_name: str, data_dir: str) -> None:
        if mode not in ("shadow", "production"):
            raise ValueError(f"SCHEDULER_MODE must be shadow|production, got {mode!r}")
        self.cfg = cfg
        self.price_source = price_source
        self.climate = climate
        self.telemetry = telemetry
        self.mode = mode
        self.tz = ZoneInfo(tz_name)
        self.data_dir = data_dir
        self.tier_state = tiers.TierState()
        self.humidity_blocked = False              # RH hysteresis (Task 11)
        self._last_arm_write: datetime | None = None  # arm-mode throttle (Task 11)

    async def tick(self, now_utc: datetime) -> None:
        tick_id = uuid.uuid4().hex
        raw = self.price_source.latest(now_utc)
        sample = None
        if raw is not None:
            cents, bucket_time, age_sec = raw
            sample = PriceSample(cents=cents, bucket_time_utc=bucket_time, age_sec=age_sec)

        prev = self.tier_state.tier
        self.tier_state, reason = tiers.evaluate_tier(
            self.tier_state, sample, self.cfg, now_utc)

        self.telemetry.trace(
            tick_id=tick_id,
            scheduler_mode=self.mode,
            price_cents=(sample.cents if sample else None),
            bucket_age_sec=(sample.age_sec if sample else None),
            prev_tier=prev,
            new_tier=self.tier_state.tier,
            reason_code=reason,
            config_id=self.cfg.config_id,
        )

        own = None
        snap = None
        from .ownhold import OwnHoldRecord, clear_record, load_record, save_record
        from . import holds
        own = load_record(self.data_dir)

        needs_device = self.tier_state.tier != tiers.NORMAL or own is not None
        if needs_device:
            snap = await self.climate.snapshot()
            self._update_humidity_gate(snap)

        overlay_commanded = snap.cool_setpoint if snap else 0.0

        if snap is not None:
            now_local = now_utc.astimezone(self.tz)
            kind, cool, until, dreason = holds.decide(
                self.tier_state.tier, snap, own, self.cfg,
                now_utc, now_local, self.humidity_blocked)
            if kind == "push":
                assert cool is not None and until is not None  # decide() contract; narrows for mypy
                applied, err = await self._apply_push(cool, until)
                overlay_commanded = cool
                if applied:
                    save_record(self.data_dir, OwnHoldRecord(
                        value=cool, until_minutes=until,
                        expiry_utc=self._slot_to_utc(until, now_local).isoformat()))
                self.telemetry.write_action(
                    tier=self.tier_state.tier, action_label="SPIKE",
                    dry_run=(self.mode == "shadow"), commanded_cool=cool,
                    commanded_heat=self.cfg.heat_floor,
                    schedule_cool=snap.schedule_cool or 0.0, applied=applied,
                    error=err, hold_expires_at=self._slot_to_utc(until, now_local).isoformat(),
                    snapshot_before=snap, setpoint_reason=dreason,
                    humidity_gated=self.humidity_blocked)
            elif kind == "release":
                applied, err = await self._apply_release()
                if applied:
                    clear_record(self.data_dir)
                self.telemetry.write_action(
                    tier=self.tier_state.tier, action_label="RELEASE",
                    dry_run=(self.mode == "shadow"),
                    commanded_cool=snap.schedule_cool or 0.0,
                    commanded_heat=self.cfg.heat_floor,
                    schedule_cool=snap.schedule_cool or 0.0, applied=applied,
                    error=err, hold_expires_at="", snapshot_before=snap,
                    setpoint_reason=dreason, humidity_gated=self.humidity_blocked)
            else:
                # record hygiene: normally-lapsed or foreign hold -> drop stale record
                if own is not None and not holds._matches_own(own, snap):
                    clear_record(self.data_dir)

        # AFTER decide/act so commanded reflects the NEW target, not the old
        # setpoint (rev 3 contract: prev/new tier tags + triggered_at_utc).
        if prev != self.tier_state.tier:
            self.telemetry.write_price_overlay(
                prev_tier=prev, new_tier=self.tier_state.tier,
                current_price_cents=(sample.cents if sample else 0.0),
                baseline_cool=(snap.schedule_cool if snap and snap.schedule_cool else 0.0),
                commanded_cool=overlay_commanded,
                triggered_at_utc=now_utc.isoformat())

        self._maybe_write_arm_mode(now_utc)

    async def _apply_push(self, cool: float, until: int) -> tuple[bool, str]:
        if self.mode == "shadow":
            return False, ""
        try:
            await self.climate.push(cool, self.cfg.heat_floor, until)
            return True, ""
        except Exception as exc:  # transient TCC errors self-heal next tick
            return False, f"{type(exc).__name__}: {exc}"

    async def _apply_release(self) -> tuple[bool, str]:
        if self.mode == "shadow":
            return False, ""
        try:
            await self.climate.release()
            return True, ""
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _update_humidity_gate(self, snap: Any) -> None:
        rh = snap.humidity
        if rh is None or rh >= self.cfg.rh_max_pct:
            self.humidity_blocked = True
        elif rh < self.cfg.rh_clear_pct:
            self.humidity_blocked = False

    def _slot_to_utc(self, until_minutes: int, now_local: datetime) -> datetime:
        from datetime import timedelta, timezone
        base = now_local.replace(hour=until_minutes // 60,
                                 minute=until_minutes % 60, second=0, microsecond=0)
        if base <= now_local:
            base += timedelta(days=1)
        return base.astimezone(timezone.utc)

    def _maybe_write_arm_mode(self, now_utc: datetime) -> None:
        if self._last_arm_write is not None and \
                (now_utc - self._last_arm_write).total_seconds() < 300:
            return
        self._last_arm_write = now_utc
        self.telemetry.write_arm_mode(now_ct=now_utc.astimezone(self.tz),
                                      scheduler_mode=self.mode)

    def run_forever(self) -> None:
        """Blocking entrypoint — ONE event loop for the process lifetime
        (async-seam global constraint). Per-tick error contract: a transient
        failure logs one line and the loop continues."""
        import asyncio
        import json as _json
        import pathlib
        import signal
        from datetime import timedelta, timezone

        async def _run() -> None:
            stop = asyncio.Event()
            aio = asyncio.get_running_loop()
            try:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    aio.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass  # non-POSIX dev boxes; container is Linux
            while not stop.is_set():
                now = datetime.now(timezone.utc)
                try:
                    await self.tick(now)
                    pathlib.Path("/tmp/last_tick_ok").touch()  # Dockerfile healthcheck
                except Exception as exc:
                    print(_json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "level": "error", "msg": "rev4_tick_failed",
                        "error_type": type(exc).__name__, "error": str(exc),
                    }), flush=True)
                nxt = now.replace(second=10, microsecond=0) + timedelta(minutes=1)
                delay = max(1.0, (nxt - datetime.now(timezone.utc)).total_seconds())
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except (asyncio.TimeoutError, TimeoutError):
                    pass

        asyncio.run(_run())
