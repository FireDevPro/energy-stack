"""hvac-scheduler-watchdog — out-of-band controller liveness check.

Per spec §11 #5: writes ``hvac.heartbeat controller_alive=false`` when
no ``hvac.arm_mode`` row exists in the last ``WATCHDOG_THRESHOLD_MINUTES``
(default 10). The point distinguishes B-down (controller process not
running / not writing) from missing-data (other failure modes such as
InfluxDB outages).

Runs as a separate Docker container (deploy/energy-stack/
hvac-scheduler-watchdog) so it cannot fail-with-the-controller. The
watchdog itself is single-purpose and stateless; if it crashes,
docker compose ``restart: unless-stopped`` brings it back.

Note: the watchdog only writes when DOWN. The absence of recent
``hvac.heartbeat`` rows tells you nothing about controller liveness on
its own; the canonical liveness signal is recent ``hvac.arm_mode``
rows from the scheduler itself. The heartbeat is purely a "DOWN
beacon" written by an out-of-band observer.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

from influxdb_client import InfluxDBClient, Point  # type: ignore[attr-defined]  # stubs lack __all__
from influxdb_client.client.write_api import SYNCHRONOUS


def check_controller_alive(query_api: Any, write_api: Any, bucket: str,
                            threshold_minutes: int = 10) -> bool:
    """Return True if at least one ``hvac.arm_mode`` row exists in the
    last ``threshold_minutes``. If False, also write
    ``hvac.heartbeat controller_alive=false`` to ``bucket``.
    """
    flux = (
        f'from(bucket: "{bucket}")\n'
        f'  |> range(start: -{threshold_minutes}m)\n'
        f'  |> filter(fn: (r) => r._measurement == "hvac.arm_mode")\n'
        f'  |> count()\n'
    )
    has_recent = False
    for table in query_api.query(flux):
        for record in table.records:
            value = record.get_value()
            if value is not None and value > 0:
                has_recent = True
                break
        if has_recent:
            break

    if not has_recent:
        p = Point("hvac.heartbeat").field("controller_alive", False)  # type: ignore[no-untyped-call]
        write_api.write(bucket=bucket, record=p)
        return False
    return True


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"watchdog_missing_env: {name}", flush=True)
        sys.exit(2)
    return value


def main() -> None:
    bucket = _required_env("INFLUXDB_BUCKET")
    url = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
    token = _required_env("INFLUXDB_TOKEN")
    org = _required_env("INFLUXDB_ORG")
    interval = int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "60"))
    threshold = int(os.environ.get("WATCHDOG_THRESHOLD_MINUTES", "10"))

    with InfluxDBClient(url=url, token=token, org=org) as client:
        query_api = client.query_api()
        write_api = client.write_api(write_options=SYNCHRONOUS)
        while True:
            try:
                check_controller_alive(query_api, write_api, bucket, threshold)
            except Exception as exc:
                print(f"watchdog_error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(interval)


if __name__ == "__main__":
    main()
