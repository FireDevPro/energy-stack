"""Ecowitt GW1200 → InfluxDB push receiver.

The GW1200 gateway POSTs sensor readings to a "Customized Server" endpoint on a
fixed cadence (default 60s). This service is that endpoint. It parses the
form-encoded payload, maps Ecowitt protocol fields to the project's canonical
``ecowitt.weather`` schema, computes outdoor dewpoint from the shaded WN32
temp/RH via the Magnus formula, and writes to InfluxDB.

Hardware in this deployment:
    GW1200B v1.4.7    gateway, indoor (basement / utility room). Source of
                      ``baromrelin``, ``tempinf``, ``humidityin``.
    WN32              shaded outdoor temp/RH on North/East wall under UV
                      shield. Pairs as a WH26 / WH32 "outdoor" slot — single
                      slot, no channel — and OVERRIDES the WS90's onboard
                      temp/RH in the Ecowitt push (``tempf``/``humidity``).
                      That override is the documented design intent: see
                      shop.ecowitt.com/products/wn32-outdoor. Canonical
                      outdoor temp/RH source.
    WS90              7-in-1 (wind, solar, rain, UV, lightning, temp, RH)
                      on pergola roof, South/East. With the WN32 paired,
                      the WS90's onboard temp/RH is not separately reported
                      in the push; we keep wind, solar, rain, UV.
    WN31 (optional)   8-channel multi-temp/RH sensor (dip-switch channel
                      1-8). When paired the parser emits ``ch{N}_temp_f``
                      / ``ch{N}_rh_pct`` / ``ch{N}_dewpoint_f`` per active
                      channel; no env var needed.

Gateway-side config (WSView app → Weather Services → Customized):
    Protocol:  Ecowitt
    Server:    <pi-lab LAN IP>
    Path:      /data/report/
    Port:      8088
    Upload:    60 seconds

Schema written (single measurement ``ecowitt.weather``):
    tag    gateway          GW1200 PASSKEY (stable per-device id)

    field  outdoor_temp_f         WN32 (canonical, via tempf)
    field  outdoor_rh_pct         WN32 (canonical, via humidity)
    field  outdoor_dewpoint_f     computed from WN32 via Magnus
    field  wind_mph               WS90
    field  solar_wm2              WS90
    field  pressure_inhg          GW1200 relative baro

    field  wind_gust_mph          WS90
    field  wind_dir_deg           WS90
    field  wind_gust_max_daily_mph  WS90
    field  uv_index               WS90
    field  rain_rate_inhr         WS90 piezo
    field  rain_event_in          WS90 piezo
    field  rain_daily_in          WS90 piezo
    field  rain_state             WS90 piezo (0/1)
    field  indoor_temp_f          GW1200 internal
    field  indoor_rh_pct          GW1200 internal
    field  baro_abs_inhg          GW1200 absolute baro

    field  ch{N}_temp_f           WH31/WN31 channel N, N in 1..8 (when paired)
    field  ch{N}_rh_pct           WH31/WN31 channel N, N in 1..8 (when paired)
    field  ch{N}_dewpoint_f       computed via Magnus, N in 1..8 (when paired)

Environment variables:
    ECOWITT_LISTEN_PORT     TCP port to bind (default 8088)
    INFLUXDB_URL            default http://influxdb:8086
    INFLUXDB_TOKEN          required
    INFLUXDB_ORG            required
    INFLUXDB_BUCKET         required
"""
from __future__ import annotations

import json
import math
import os
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

HEALTH_MARKER = Path("/tmp/last_push_ok")
MEASUREMENT = "ecowitt.weather"


def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


@dataclass
class Config:
    listen_port: int
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
            listen_port=int(os.environ.get("ECOWITT_LISTEN_PORT", "8088")),
            influx_url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
            influx_token=required("INFLUXDB_TOKEN"),
            influx_org=required("INFLUXDB_ORG"),
            influx_bucket=required("INFLUXDB_BUCKET"),
        )


# Magnus formula coefficients (WMO / Alduchov-Eskridge 1996). Inputs in °C, RH in %.
# Returns dewpoint in °C. Valid for 0 < RH <= 100 and reasonable air temps.
_MAGNUS_A = 17.625
_MAGNUS_B = 243.04


def compute_dewpoint_f(temp_f: float | None, rh_pct: float | None) -> float | None:
    if temp_f is None or rh_pct is None:
        return None
    if not 0 < rh_pct <= 100:
        return None
    t_c = (temp_f - 32.0) * 5.0 / 9.0
    gamma = math.log(rh_pct / 100.0) + (_MAGNUS_A * t_c) / (_MAGNUS_B + t_c)
    dp_c = (_MAGNUS_B * gamma) / (_MAGNUS_A - gamma)
    return dp_c * 9.0 / 5.0 + 32.0


def _f(form: dict[str, str], key: str) -> float | None:
    v = form.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _i(form: dict[str, str], key: str) -> int | None:
    v = form.get(key)
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def parse_dateutc(value: str | None) -> datetime:
    """Ecowitt sends ``dateutc`` as ``YYYY-MM-DD+HH:MM:SS`` (URL-decoded form
    has the '+' as a space). Fall back to receive-time if absent/malformed.
    """
    if not value:
        return datetime.now(timezone.utc)
    try:
        s = value.replace("+", " ")
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def build_point(form: dict[str, str]) -> Point | None:
    """Map an Ecowitt POST payload to an InfluxDB point.

    Returns None if the payload contains no usable fields (e.g. handshake or
    misconfigured gateway sending only PASSKEY).
    """
    passkey = form.get("PASSKEY", "unknown")
    ts = parse_dateutc(form.get("dateutc"))

    # WN32 occupies the outdoor (WH26/WH32) slot, so its readings arrive on
    # the singular ``tempf``/``humidity`` fields. When WN32 is paired with a
    # WS90 array the WN32's reading wins by design (see module docstring).
    outdoor_temp_f = _f(form, "tempf")
    outdoor_rh_pct = _f(form, "humidity")

    fields: dict[str, float | int] = {}

    def put(name: str, value: float | int | None) -> None:
        if value is not None:
            fields[name] = value

    # Canonical outdoor (WN32, shaded). Project queries depend on these names.
    put("outdoor_temp_f", outdoor_temp_f)
    put("outdoor_rh_pct", outdoor_rh_pct)
    put("outdoor_dewpoint_f", compute_dewpoint_f(outdoor_temp_f, outdoor_rh_pct))

    # WS90 wind, solar, plus GW1200 relative baro.
    put("wind_mph", _f(form, "windspeedmph"))
    put("solar_wm2", _f(form, "solarradiation"))
    put("pressure_inhg", _f(form, "baromrelin"))

    # WS90 supplemental.
    put("wind_gust_mph", _f(form, "windgustmph"))
    put("wind_dir_deg", _f(form, "winddir"))
    put("wind_gust_max_daily_mph", _f(form, "maxdailygust"))
    put("uv_index", _f(form, "uv"))

    # Piezo rain (WS90).
    put("rain_rate_inhr", _f(form, "rrain_piezo"))
    put("rain_event_in", _f(form, "erain_piezo"))
    put("rain_daily_in", _f(form, "drain_piezo"))
    put("rain_state", _i(form, "srain_piezo"))

    # GW1200 internal.
    put("indoor_temp_f", _f(form, "tempinf"))
    put("indoor_rh_pct", _f(form, "humidityin"))
    put("baro_abs_inhg", _f(form, "baromabsin"))

    # WH31 / WN31 multi-channel temp/RH (channels 1-8). Each channel only
    # appears when a sensor is paired to that slot — missing fields are
    # silently dropped. Sensor placement is decided at the sensor's dip
    # switch, not here.
    for ch in range(1, 9):
        t = _f(form, f"temp{ch}f")
        rh = _f(form, f"humidity{ch}")
        put(f"ch{ch}_temp_f", t)
        put(f"ch{ch}_rh_pct", rh)
        put(f"ch{ch}_dewpoint_f", compute_dewpoint_f(t, rh))

    if not fields:
        return None

    p = Point(MEASUREMENT).tag("gateway", passkey).time(ts, WritePrecision.S)
    for name, value in fields.items():
        # Force float for measurements (avoids InfluxDB type-collision errors
        # if the same field is later written by a different writer as int).
        # Exception: rain_state is a 0/1 status flag, keep as int.
        if name == "rain_state":
            p = p.field(name, int(value))
        else:
            p = p.field(name, float(value))
    return p


class _Handler(BaseHTTPRequestHandler):
    # Class attrs set in make_handler closure.
    write_api = None  # type: ignore[assignment]
    bucket: str = ""
    org: str = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default stderr noise
        return

    def _ok(self, body: bytes = b"OK\n") -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bad(self, code: int, msg: str) -> None:
        body = msg.encode("utf-8") + b"\n"
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        # The gateway never GETs, but a /health endpoint is handy for compose.
        if self.path.startswith("/health"):
            self._ok(b"ok\n")
            return
        self._bad(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if not (self.path.startswith("/data/report") or self.path == "/"):
            self._bad(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b""
            form_multi = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
            form = {k: v[0] for k, v in form_multi.items()}

            point = build_point(form)
            if point is None:
                log("warning", "empty_payload", path=self.path, keys=list(form.keys()))
                self._ok()
                return

            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            HEALTH_MARKER.touch()
            log(
                "info",
                "push_written",
                gateway=form.get("PASSKEY", "unknown"),
                station=form.get("stationtype", ""),
                model=form.get("model", ""),
            )
            self._ok()
        except Exception as exc:
            log("error", "push_failed", error=str(exc), error_type=type(exc).__name__)
            self._bad(500, "ingest error")


def make_handler(write_api, bucket: str, org: str):
    class Bound(_Handler):
        pass

    Bound.write_api = write_api
    Bound.bucket = bucket
    Bound.org = org
    return Bound


def main() -> None:
    cfg = Config.from_env()
    log(
        "info",
        "starting",
        port=cfg.listen_port,
        bucket=cfg.influx_bucket,
    )

    client = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    handler = make_handler(write_api, cfg.influx_bucket, cfg.influx_org)
    server = ThreadingHTTPServer(("0.0.0.0", cfg.listen_port), handler)

    stop_event = threading.Event()

    def _shutdown(signum: int, _frame: Any) -> None:
        log("info", "shutdown_signal", signum=signum)
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log("info", "listening", addr=f"0.0.0.0:{cfg.listen_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        write_api.close()
        client.close()
        log("info", "stopped")


if __name__ == "__main__":
    main()
