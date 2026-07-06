"""Rev 4 price input. fresh-strict = bucket age <= 720s (12 min), calibrated to
the measured ComEd publish-lag jitter (floor 370-430s, sawtooth ceiling ~11.2
min). Spec: rev 4 §Feed-gap. The freshness.py 7-min label is display-only and
must NOT gate control decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

FRESH_STRICT_MAX_AGE_SEC: float = 720.0


@dataclass(frozen=True)
class PriceSample:
    cents: float
    bucket_time_utc: datetime
    age_sec: float


def is_fresh_strict(s: PriceSample) -> bool:
    return s.age_sec <= FRESH_STRICT_MAX_AGE_SEC


def _flux_latest_5min(bucket: str) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices" and r.period_type == "5min")
  |> filter(fn: (r) => r._field == "price_cents_per_kwh")
  |> last()
'''


def fetch_price(query_api: Any, bucket: str, now_utc: datetime) -> PriceSample | None:
    for table in query_api.query(_flux_latest_5min(bucket)):
        for rec in table.records:
            t = rec.get_time()
            v = rec.get_value()
            if t is None or v is None:
                return None
            if t.tzinfo is None:
                # some influxdb_client versions return naive datetimes
                # (see influx_adapter.project_record's identical guard)
                t = t.replace(tzinfo=timezone.utc)
            return PriceSample(
                cents=float(v),
                bucket_time_utc=t,
                age_sec=(now_utc - t).total_seconds(),
            )
    return None
