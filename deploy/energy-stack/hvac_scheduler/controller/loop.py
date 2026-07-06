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
        # Device interaction (spike path + cleanup) lands in Task 11.
