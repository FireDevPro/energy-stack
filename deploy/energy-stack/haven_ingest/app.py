"""Haven IAQ API poller → InfluxDB.

Authenticates via Auth0 refresh token (no browser required after initial
setup), polls the HAVEN API for indoor telemetry and outdoor air quality,
and writes to InfluxDB.

On startup, backfills HAVEN_BACKFILL_DAYS of indoor + outdoor history via
the range endpoints, then switches to live polling every HAVEN_POLL_INTERVAL
seconds using telemetry_current.

Access tokens expire after 24 h. The refresh token is exchanged automatically.
If the refresh token itself expires (typically 30–90 days of disuse), set a
new HAVEN_REFRESH_TOKEN obtained from a fresh browser login session.

Measurements written:
  haven.indoor   tag: device_id
                 fields: airflow, voc_count, voc_mc, pm_count, pm_mc,
                         temperature_c, humidity, voc_status, pm_status,
                         humidity_status, combined_status

  haven.outdoor  tag: station_id
                 fields: temperature_c, humidity, dew_point, aqi, co, o3,
                         no2, so2, pm25, pm10, condition,
                         hazel, alder, birch, oak, grass, mugwort, ragweed

Environment variables:
    HAVEN_AUTH0_DOMAIN      Auth0 tenant (default haven-production.auth0.com)
    HAVEN_CLIENT_ID         Auth0 app client ID
    HAVEN_REFRESH_TOKEN     Bootstrap refresh token (captured once from browser login).
                            After the first successful exchange, the rotated token is
                            persisted to HAVEN_TOKEN_FILE and this env var is no longer
                            consulted.
    HAVEN_TOKEN_FILE        Path to persist the rotating refresh token across restarts
                            (default /data/haven_token.json). Mount a named volume at
                            /data so the file survives container recreation.
    HAVEN_DEVICE_ID         Indoor device numeric ID (e.g. 3645)
    HAVEN_OUTDOOR_ID        Outdoor station numeric ID (e.g. 60585)
    HAVEN_POLL_INTERVAL     Seconds between live polls (default 300)
    HAVEN_BACKFILL_DAYS     Days of history to pull on startup (default 7)
    INFLUXDB_URL            Default http://influxdb:8086
    INFLUXDB_TOKEN          InfluxDB write token
    INFLUXDB_ORG            InfluxDB organization
    INFLUXDB_BUCKET         Target bucket
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import FrameType
from typing import Any, cast

import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision  # type: ignore[attr-defined]  # stubs lack __all__
from influxdb_client.client.write_api import SYNCHRONOUS, WriteApi

HEALTH_MARKER = Path("/tmp/last_poll_ok")
HAVEN_API = "https://havenapi.tzoa.io"


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


@dataclass
class Config:
    auth0_domain: str
    client_id: str
    refresh_token: str
    token_file: Path
    device_id: str
    outdoor_id: str
    poll_interval: float
    backfill_days: int
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
            auth0_domain=os.environ.get("HAVEN_AUTH0_DOMAIN", "haven-production.auth0.com"),
            client_id=required("HAVEN_CLIENT_ID"),
            refresh_token=os.environ.get("HAVEN_REFRESH_TOKEN", ""),
            token_file=Path(os.environ.get("HAVEN_TOKEN_FILE", "/data/haven_token.json")),
            device_id=required("HAVEN_DEVICE_ID"),
            outdoor_id=required("HAVEN_OUTDOOR_ID"),
            poll_interval=float(os.environ.get("HAVEN_POLL_INTERVAL", "300")),
            backfill_days=int(os.environ.get("HAVEN_BACKFILL_DAYS", "7")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


class TokenManager:
    """Manages the Auth0 access token via refresh token exchange.

    Token precedence on startup:
      1. Token file (HAVEN_TOKEN_FILE) — persisted from previous run
      2. HAVEN_REFRESH_TOKEN env var — bootstrap only

    After each successful exchange the rotated refresh_token is written to the
    token file so container restarts are fully self-sustaining.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._refresh_token = self._load_token()

    def _load_token(self) -> str:
        tf = self._cfg.token_file
        if tf.exists():
            try:
                data = json.loads(tf.read_text())
                token = cast(str, data.get("refresh_token", ""))
                if token:
                    log("info", "token_loaded_from_file", path=str(tf))
                    return token
            except Exception as exc:
                log("warning", "token_file_unreadable", path=str(tf), error=str(exc))
        token = self._cfg.refresh_token
        if not token:
            log("error", "no_refresh_token",
                detail="Set HAVEN_REFRESH_TOKEN or provide a token file at HAVEN_TOKEN_FILE")
            sys.exit(2)
        log("info", "token_loaded_from_env")
        return token

    def _save_token(self, refresh_token: str) -> None:
        tf = self._cfg.token_file
        try:
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text(json.dumps({"refresh_token": refresh_token}))
        except Exception as exc:
            log("warning", "token_file_write_failed", path=str(tf), error=str(exc))

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Client-Application": "pro_portal",
        }

    def _get_token(self) -> str:
        if time.monotonic() >= self._expires_at - 60:
            self._do_refresh()
        return self._access_token  # type: ignore[return-value]

    def _do_refresh(self) -> None:
        url = f"https://{self._cfg.auth0_domain}/oauth/token"
        resp = requests.post(url, data={
            "grant_type": "refresh_token",
            "client_id": self._cfg.client_id,
            "refresh_token": self._refresh_token,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._expires_at = time.monotonic() + data.get("expires_in", 86400)
        if "refresh_token" in data:
            self._refresh_token = data["refresh_token"]
            self._save_token(self._refresh_token)
        log("info", "token_refreshed", expires_in=data.get("expires_in"))


class HavenClient:
    def __init__(self, tokens: TokenManager, cfg: Config) -> None:
        self._tokens = tokens
        self._cfg = cfg

    def telemetry_current(self) -> dict[str, Any]:
        url = f"{HAVEN_API}/device/{self._cfg.device_id}/telemetry_current"
        r = requests.get(url, headers=self._tokens.headers(), timeout=15)
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def telemetry_range(self, start: datetime, end: datetime, interval: str = "5m") -> list[dict[str, Any]]:
        url = f"{HAVEN_API}/device/{self._cfg.device_id}/telemetry_range"
        r = requests.get(url, headers=self._tokens.headers(), params={
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "interval": interval,
        }, timeout=60)
        r.raise_for_status()
        return cast(list[dict[str, Any]], r.json())

    def outdoor_range(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        url = f"{HAVEN_API}/outdoor_air/{self._cfg.outdoor_id}/range"
        r = requests.get(url, headers=self._tokens.headers(), params={
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        }, timeout=60)
        r.raise_for_status()
        return cast(list[dict[str, Any]], r.json())


def indoor_reading_to_point(r: dict[str, Any], device_id: str, ts: datetime | None = None) -> Point:
    # `point: Any` lets the Point()/.tag()/.field() chain (untyped in
    # influxdb_client stubs) flow through without per-call ignores.
    # cast() at return restores the declared Point return type.
    t = ts or datetime.fromisoformat(r["timestamp"])
    point: Any = (
        Point("haven.indoor")  # type: ignore[no-untyped-call]
        .tag("device_id", device_id)
        .field("airflow", float(r["airflow"]))
        .field("voc_count", float(r["voc_count"]))
        .field("voc_mc", float(r["voc_mc"]))
        .field("pm_count", float(r["pm_count"]))
        .field("pm_mc", float(r["pm_mc"]))
        .field("temperature_c", float(r["temperature"]))
        .field("humidity", float(r["humidity"]))
        .field("voc_status", r["voc_status"])
        .field("pm_status", r["pm_status"])
        .field("humidity_status", r["humidity_status"])
        .field("combined_status", r["combined_status"])
        .time(t, WritePrecision.S)
    )
    return cast(Point, point)


def outdoor_reading_to_point(r: dict[str, Any], station_id: str) -> Point:
    t = datetime.fromisoformat(r["timestamp"])
    point: Any = (
        Point("haven.outdoor")  # type: ignore[no-untyped-call]
        .tag("station_id", station_id)
        .field("temperature_c", float(r["temperature"]))
        .field("humidity", float(r["humidity"]))
        .field("dew_point", float(r["dew_point"]))
        .field("aqi", int(r["aqi"]))
        .field("co", float(r["co"]))
        .field("o3", float(r["o3"]))
        .field("no2", float(r["no2"]))
        .field("so2", float(r["so2"]))
        .field("pm25", float(r["pm25"]))
        .field("pm10", float(r["pm10"]))
        .field("condition", int(r["condition"]))
        .field("hazel", float(r["hazel"]))
        .field("alder", float(r["alder"]))
        .field("birch", float(r["birch"]))
        .field("oak", float(r["oak"]))
        .field("grass", float(r["grass"]))
        .field("mugwort", float(r["mugwort"]))
        .field("ragweed", float(r["ragweed"]))
        .time(t, WritePrecision.S)
    )
    return cast(Point, point)


def write_batch(write_api: WriteApi, bucket: str, points: list[Point], batch_size: int = 1000) -> int:
    for i in range(0, len(points), batch_size):
        write_api.write(bucket=bucket, record=points[i:i + batch_size])
    return len(points)


def main() -> int:
    cfg = Config.from_env()
    log("info", "startup",
        device_id=cfg.device_id,
        outdoor_id=cfg.outdoor_id,
        poll_interval=cfg.poll_interval,
        backfill_days=cfg.backfill_days)

    tokens = TokenManager(cfg)
    client = HavenClient(tokens, cfg)
    influx = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    stop_requested = False

    def handle_stop(signum: int, _frame: FrameType | None) -> None:
        nonlocal stop_requested
        log("info", "signal_received", signum=signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    # Backfill on startup
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=cfg.backfill_days)
    log("info", "backfill_start", start=start.isoformat(), end=now.isoformat())
    try:
        indoor = client.telemetry_range(start, now)
        n = write_batch(write_api, cfg.influx_bucket,
                        [indoor_reading_to_point(r, cfg.device_id) for r in indoor])
        log("info", "backfill_indoor_ok", points=n)
    except Exception as exc:
        log("error", "backfill_indoor_failed", error=str(exc), error_type=type(exc).__name__)

    try:
        outdoor = client.outdoor_range(start, now)
        n = write_batch(write_api, cfg.influx_bucket,
                        [outdoor_reading_to_point(r, cfg.outdoor_id) for r in outdoor])
        log("info", "backfill_outdoor_ok", points=n)
    except Exception as exc:
        log("error", "backfill_outdoor_failed", error=str(exc), error_type=type(exc).__name__)

    # Live poll loop
    try:
        while not stop_requested:
            try:
                data = client.telemetry_current()
                point = indoor_reading_to_point(data, cfg.device_id, ts=datetime.now(timezone.utc))
                write_api.write(bucket=cfg.influx_bucket, record=point)
                HEALTH_MARKER.touch()
                log("info", "poll_ok",
                    temp_c=data.get("temperature"),
                    humidity=data.get("humidity"),
                    voc_count=data.get("voc_count"),
                    combined_status=data.get("combined_status"))
            except Exception as exc:
                log("error", "poll_failed", error=str(exc), error_type=type(exc).__name__)

            deadline = time.monotonic() + cfg.poll_interval
            while not stop_requested and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
    finally:
        log("info", "shutdown")
        influx.close()  # type: ignore[no-untyped-call]  # influxdb_client stubs lack close annotation
    return 0


if __name__ == "__main__":
    sys.exit(main())
