"""Haven IAQ CSV → InfluxDB ingester.

Watches /inbox/haven/ for HAVEN Pro / homeowner-portal CSV exports
(filename pattern: CAM_<device-id>_<start>_to_<end>.csv) and writes the
contents into InfluxDB. Idempotent on timestamp — re-importing the same
week is a no-op.

Runs an initial scan of /inbox/haven/ on startup (catches anything
dropped while the container was down), then watches the directory for
new files using polling (1-min interval — no inotify dependency, works
across Docker volume mount semantics).

After successful ingestion, files move to /inbox/haven/processed/<filename>.
On parse error, they move to /inbox/haven/failed/<filename> with a sidecar
.error file containing the error message.

Measurement layout:

  haven.airquality
    Tags:   device_id (from filename, e.g. "0000-6267")
    Fields: temp_f (float), temp_c (float), humidity_pct (float),
            tvoc_ppb (int), pm25_ugm3 (float, may be missing),
            airflow_cfm (float, may be missing — non-zero only when blower running),
            tvoc_status (str: "good"/"fair"/"poor"),
            pm25_status (str), combined_status (str)

CSV format observed (Haven Pro export, May 2026):
  Header line + 4 comment lines starting with '#' + data rows
  Columns: Timestamp,Date,Time,PM2.5 (µg/m³),PM2.5 Status,tVOC (ppb),
           tVOC Status,Temperature (°C),Temperature (°F),
           Relative Humidity (%),Combined Status,Airflow (CFM)
  PM2.5 and Airflow are sparse (only populated when blower running).

Environment variables:
    HAVEN_INBOX_DIR             Directory to watch (default /inbox/haven)
    HAVEN_PROCESSED_SUBDIR      Where to move OK files (default processed)
    HAVEN_FAILED_SUBDIR         Where to move broken files (default failed)
    HAVEN_SCAN_INTERVAL         Seconds between directory scans (default 60)
    INFLUXDB_URL                Default http://influxdb:8086
    INFLUXDB_TOKEN              Admin or write token
    INFLUXDB_ORG                InfluxDB organization
    INFLUXDB_BUCKET             Target bucket
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

HEALTH_MARKER = Path("/tmp/last_scan_ok")

# Filename pattern: CAM_<device-id>_<start>_to_<end>.csv
# device-id is something like "0000-6267" (matches the HAVEN-CAM-XXXX-YYYY hostname suffix)
FILENAME_RE = re.compile(r"^CAM_(?P<device_id>[0-9a-fA-F-]+)_.*\.csv$", re.IGNORECASE)


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


@dataclass(frozen=True)
class Config:
    inbox_dir: Path
    processed_subdir: str
    failed_subdir: str
    scan_interval: float
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str

    @staticmethod
    def from_env() -> "Config":
        def required(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                log("error", "missing_env", var=name)
                sys.exit(2)
            return v
        return Config(
            inbox_dir=Path(os.environ.get("HAVEN_INBOX_DIR", "/inbox/haven")),
            processed_subdir=os.environ.get("HAVEN_PROCESSED_SUBDIR", "processed"),
            failed_subdir=os.environ.get("HAVEN_FAILED_SUBDIR", "failed"),
            scan_interval=float(os.environ.get("HAVEN_SCAN_INTERVAL", "60")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


def parse_haven_csv(path: Path) -> Iterator[dict]:
    """Yield one parsed row per data line. Strips the # comment lines.
    Raises ValueError if the file doesn't have the expected header."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        lines = [ln for ln in f if not ln.startswith("#") and ln.strip()]
    if not lines:
        raise ValueError("empty_csv")
    reader = csv.DictReader(lines)
    expected = {"Timestamp", "Temperature (°F)", "Relative Humidity (%)", "tVOC (ppb)"}
    actual_cols = {c.strip() for c in (reader.fieldnames or [])}
    missing = expected - actual_cols
    if missing:
        raise ValueError(f"missing_columns: {sorted(missing)}; actual={sorted(actual_cols)}")
    for row in reader:
        yield row


def _maybe_float(s: str | None) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _maybe_int(s: str | None) -> int | None:
    f = _maybe_float(s)
    return int(f) if f is not None else None


def row_to_point(row: dict, device_id: str) -> Point | None:
    ts_raw = row.get("Timestamp", "").strip().strip('"')
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        return None

    temp_f = _maybe_float(row.get("Temperature (°F)"))
    temp_c = _maybe_float(row.get("Temperature (°C)"))
    rh = _maybe_float(row.get("Relative Humidity (%)"))
    tvoc = _maybe_int(row.get("tVOC (ppb)"))
    pm25 = _maybe_float(row.get("PM2.5 (µg/m³)"))
    airflow = _maybe_float(row.get("Airflow (CFM)"))

    pm_status = (row.get("PM2.5 Status") or "").strip().strip('"')
    tvoc_status = (row.get("tVOC Status") or "").strip().strip('"')
    combined_status = (row.get("Combined Status") or "").strip().strip('"')

    p = Point("haven.airquality").tag("device_id", device_id).time(ts, WritePrecision.S)
    if temp_f is not None:
        p = p.field("temp_f", temp_f)
    if temp_c is not None:
        p = p.field("temp_c", temp_c)
    if rh is not None:
        p = p.field("humidity_pct", rh)
    if tvoc is not None:
        p = p.field("tvoc_ppb", tvoc)
    if pm25 is not None:
        p = p.field("pm25_ugm3", pm25)
    if airflow is not None:
        p = p.field("airflow_cfm", airflow)
    if pm_status:
        p = p.field("pm25_status", pm_status)
    if tvoc_status:
        p = p.field("tvoc_status", tvoc_status)
    if combined_status:
        p = p.field("combined_status", combined_status)

    # Skip rows with no measured fields (pure status-only rows aren't useful)
    if temp_f is None and temp_c is None and rh is None and tvoc is None:
        return None
    return p


def ingest_file(path: Path, write_api, cfg: Config) -> tuple[int, int]:
    """Parse CSV and write to InfluxDB. Returns (rows_seen, rows_written)."""
    m = FILENAME_RE.match(path.name)
    device_id = m.group("device_id") if m else "unknown"
    rows_seen = 0
    points: list[Point] = []
    for row in parse_haven_csv(path):
        rows_seen += 1
        p = row_to_point(row, device_id)
        if p is not None:
            points.append(p)
    if points:
        # Batch in chunks to avoid huge single writes
        BATCH = 1000
        for i in range(0, len(points), BATCH):
            write_api.write(bucket=cfg.influx_bucket, record=points[i : i + BATCH])
    return rows_seen, len(points)


def scan_once(cfg: Config, write_api) -> int:
    """Scan the inbox once. Returns number of files processed (success or fail)."""
    if not cfg.inbox_dir.exists():
        cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
    processed = cfg.inbox_dir / cfg.processed_subdir
    failed = cfg.inbox_dir / cfg.failed_subdir
    processed.mkdir(parents=True, exist_ok=True)
    failed.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in cfg.inbox_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
    handled = 0
    for path in files:
        try:
            rows_seen, rows_written = ingest_file(path, write_api, cfg)
            dst = processed / path.name
            shutil.move(str(path), str(dst))
            log("info", "ingest_ok",
                file=path.name,
                rows_seen=rows_seen,
                rows_written=rows_written,
                moved_to=str(dst))
            handled += 1
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            dst = failed / path.name
            try:
                shutil.move(str(path), str(dst))
                (dst.with_suffix(dst.suffix + ".error")).write_text(err_msg)
            except Exception as move_exc:
                log("error", "ingest_failed_and_move_failed",
                    file=path.name,
                    error=err_msg,
                    move_error=str(move_exc))
                continue
            log("error", "ingest_failed",
                file=path.name,
                error=err_msg,
                moved_to=str(dst))
            handled += 1
    return handled


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup",
        inbox_dir=str(cfg.inbox_dir),
        scan_interval_s=cfg.scan_interval,
        bucket=cfg.influx_bucket)

    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    stop_requested = False

    def handle_stop(signum, _frame):
        nonlocal stop_requested
        log("info", "signal_received", signum=signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    try:
        while not stop_requested:
            try:
                handled = scan_once(cfg, write_api)
                log("info", "scan_ok", files_handled=handled)
                HEALTH_MARKER.touch()
            except Exception as exc:
                log("error", "scan_failed", error=str(exc), error_type=type(exc).__name__)

            deadline = time.monotonic() + cfg.scan_interval
            while not stop_requested and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
    finally:
        log("info", "shutdown")
        influx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
