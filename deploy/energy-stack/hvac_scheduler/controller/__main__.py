"""Entrypoint: python -m hvac_scheduler.controller."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

from influxdb_client import InfluxDBClient  # type: ignore[attr-defined]  # influxdb_client lacks __all__; same targeted ignore as influx_adapter.py
from influxdb_client.client.write_api import SYNCHRONOUS

from ..tcc_client import TCCClient
from .config import ConfigError, load_config
from .device import TccClimateAdapter
from .loop import ControllerLoop
from .pricing import fetch_price
from .telemetry import InfluxTelemetry


class InfluxPriceSource:
    def __init__(self, query_api: Any, bucket: str) -> None:
        self._api, self._bucket = query_api, bucket

    def latest(self, now_utc: datetime) -> tuple[float, datetime, float] | None:
        s = fetch_price(self._api, self._bucket, now_utc)
        return None if s is None else (s.cents, s.bucket_time_utc, s.age_sec)


def main() -> int:
    mode = os.environ.get("SCHEDULER_MODE", "shadow")
    if mode not in ("shadow", "production"):
        print(f"invalid SCHEDULER_MODE: {mode!r}", flush=True)
        return 2
    try:
        cfg = load_config(os.environ["CONTROLLER_CONFIG_FILE"],
                          temp_scale_env=os.environ.get("TEMP_SCALE", "C"))
    except (KeyError, ConfigError) as exc:
        print(f"config error: {exc}", flush=True)
        return 2

    influx = InfluxDBClient(url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
                            token=os.environ["INFLUXDB_TOKEN"],
                            org=os.environ["INFLUXDB_ORG"])
    bucket = os.environ["INFLUXDB_BUCKET"]
    tz_name = os.environ.get("SCHEDULER_TZ", "America/Chicago")

    loop = ControllerLoop(
        cfg=cfg,
        price_source=InfluxPriceSource(influx.query_api(), bucket),
        climate=TccClimateAdapter(TCCClient(
            os.environ["TCC_USERNAME"], os.environ["TCC_PASSWORD"],
            int(os.environ.get("TCC_DEVICE_ID", "4750378")))),
        telemetry=InfluxTelemetry(
            write_api=influx.write_api(write_options=SYNCHRONOUS),
            bucket=bucket, unit=cfg.temp_scale, config_id=cfg.config_id,
            tz_name=tz_name),
        mode=mode, tz_name=tz_name, data_dir="/data",
    )
    loop.run_forever()   # 60s ticks at second :10; touches /tmp/last_tick_ok
    return 0


if __name__ == "__main__":
    sys.exit(main())
