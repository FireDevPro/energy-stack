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
from .errors import InfrastructureError
from .pricing import PriceSample
from . import tiers


def _tick_failure_log(exc: Exception, now_utc: datetime) -> dict[str, Any]:
    # TCC 30s request timeouts are transient and self-heal on the next tick
    # (the device holds via its 30-min TTL meanwhile). No alarm keys on this
    # log at any level, so warn-vs-error changes no alarm coverage: SUSTAINED
    # outages surface out-of-band (watchdog arm_mode staleness + thermostat-
    # poller silence); the residual intermittent-during-spike gap is PR 2's
    # device-stall alarm. So a lone TimeoutError is warn (log legibility) —
    # genuine faults (any other exception type) stay at error.
    transient = isinstance(exc, TimeoutError)
    return {
        "ts": now_utc.isoformat(),
        "level": "warn" if transient else "error",
        "msg": "rev4_tick_failed",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _infra(op: str, fn: Any, *args: Any) -> Any:
    """Run a substrate operation, tagging any failure as infrastructure.

    Local-disk hold-record I/O is the machine, not the controller's job. Spec
    §Telemetry: infrastructure is not a domain error class, so these must not
    reach the top-level handler as an unclassified exception and be recorded
    as `crash`.
    """
    try:
        return fn(*args)
    except OSError as exc:
        raise InfrastructureError(f"{op}: {exc}") from exc


def _tick_warn(msg: str, exc: Exception) -> None:
    """Loki line for a RECORDED domain failure. The Influx row is the alerting
    signal; this is for human log-reading only."""
    import json as _json
    from datetime import timezone as _tz
    print(_json.dumps({
        "ts": datetime.now(_tz.utc).isoformat(),
        "level": "warn", "msg": msg,
        "error_type": type(exc).__name__, "error": str(exc),
    }), flush=True)


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
        try:
            raw = self.price_source.latest(now_utc)
        except Exception as exc:
            # The price query runs against Influx — substrate, not a domain
            # error (spec §Telemetry).
            raise InfrastructureError(
                f"price query: {type(exc).__name__}: {exc}") from exc
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
            try:
                snap = await self.climate.snapshot()
            except Exception as exc:
                # Domain error, class `read` (spec §Telemetry). Recorded as an
                # attempt row, then the tick ends: with no snapshot there is
                # nothing to decide and nothing to act on. Deliberately NOT
                # re-raised — the top-level handler records `crash`, and a TCC
                # timeout is not a crash.
                self.telemetry.write_device_status(
                    op="read", success=False, tier=self.tier_state.tier,
                    dry_run=(self.mode == "shadow"),
                    error_type=type(exc).__name__, error_msg=str(exc))
                _tick_warn("device_read_failed", exc)
                return
            self.telemetry.write_device_status(
                op="read", success=True, tier=self.tier_state.tier,
                dry_run=(self.mode == "shadow"))
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
                    _infra("save_record", save_record, self.data_dir, OwnHoldRecord(
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
                    _infra("clear_record", clear_record, self.data_dir)
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
                    _infra("clear_record", clear_record, self.data_dir)

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
        # Shadow returns before touching the device: there is no attempt to
        # record. Every real attempt writes one op="write" row (spec §Telemetry).
        if self.mode == "shadow":
            return False, ""
        try:
            await self.climate.push(cool, self.cfg.heat_floor, until)
        except Exception as exc:  # transient TCC errors self-heal next tick
            self._record_write(success=False, exc=exc)
            return False, f"{type(exc).__name__}: {exc}"
        self._record_write(success=True)
        return True, ""

    async def _apply_release(self) -> tuple[bool, str]:
        if self.mode == "shadow":
            return False, ""
        try:
            await self.climate.release()
        except Exception as exc:
            self._record_write(success=False, exc=exc)
            return False, f"{type(exc).__name__}: {exc}"
        self._record_write(success=True)
        return True, ""

    def _record_write(self, *, success: bool, exc: Exception | None = None) -> None:
        self.telemetry.write_device_status(
            op="write", success=success, tier=self.tier_state.tier, dry_run=False,
            error_type=("" if exc is None else type(exc).__name__),
            error_msg=("" if exc is None else str(exc)))

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

    def _handle_tick_exception(self, exc: Exception) -> None:
        """Top-level per-tick error contract (spec §Telemetry).

        `InfrastructureError` is the substrate failing, NOT a domain error: an
        Influx outage gaps every measurement at once, so the record already
        shows it, and total loss is the off-box dead-man's job. Anything else
        reaching here is the controller's own decision/act logic raising —
        class `crash`, which is why crash alerts on the first occurrence.

        Device read and write failures never reach here: they are recorded and
        handled at their own seams, so nothing is counted twice.
        """
        import json as _json
        from datetime import timezone as _tz
        if not isinstance(exc, InfrastructureError):
            self.telemetry.write_device_status(
                op="crash", success=False, tier=self.tier_state.tier,
                dry_run=(self.mode == "shadow"),
                error_type=type(exc).__name__, error_msg=str(exc))
        print(_json.dumps(_tick_failure_log(
            exc, datetime.now(_tz.utc))), flush=True)

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
                    self._handle_tick_exception(exc)
                nxt = now.replace(second=10, microsecond=0) + timedelta(minutes=1)
                delay = max(1.0, (nxt - datetime.now(timezone.utc)).total_seconds())
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except (asyncio.TimeoutError, TimeoutError):
                    pass

        asyncio.run(_run())
