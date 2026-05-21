"""Ecowitt GW1200 → InfluxDB push receiver.

The GW1200 gateway POSTs sensor readings to a "Customized Server" endpoint on a
fixed cadence (default 60s). This service is that endpoint. It parses the
form-encoded payload, maps Ecowitt protocol fields to the project's canonical
``ecowitt.weather`` schema, computes dewpoints via the Magnus formula, and
writes to InfluxDB.

Two-stream design (shaded canonical + sun comparator)
------------------------------------------------------
The WS90 ships with an onboard temp/RH sensor on the same pole as the wind /
solar / rain hardware -- which means it sits in direct sun on the pergola.
That reading is useful as a sun-exposure comparator but it is NOT a valid
"outdoor air temperature" for meteorological work (forecast bias correction,
HVAC pre-cool decisions, dewpoint-driven comfort modeling all assume a shaded
sensor).

A standalone WN31 on a WH31 channel -- mounted in a UV-shielded enclosure on
the shaded N/E wall -- gives us the canonical shaded reading on a separate
multi-channel slot. The WH26 outdoor slot is left empty intentionally; with
no WH26-class sensor present the WS90 fills ``tempf``/``humidity`` from its
onboard sensor and we capture that as the sun comparator. Both streams land
in the same measurement, distinguished by field name.

Hardware in this deployment:
    GW1200B v1.4.7    gateway, indoor (basement / utility room). Source of
                      ``baromrelin``, ``tempinf``, ``humidityin``.
    WS90              7-in-1 on pergola roof, South/East. Wind, solar, UV,
                      piezo rain, lightning, AND its onboard temp/RH (sun-
                      exposed comparator at ``tempf``/``humidity``).
    WN31              Multi-channel temp/RH sensor on the shaded N/E wall
                      under a UV shield. Dip switches 1-3 set channel 1-8
                      at the sensor; ``ECOWITT_SHADED_CHANNEL`` env var
                      tells the parser which channel is the canonical
                      shaded reference. Source of ``outdoor_temp_f``,
                      ``outdoor_rh_pct``, ``outdoor_dewpoint_f``.

Gateway-side config (WSView app → Weather Services → Customized):
    Protocol:  Ecowitt
    Server:    <pi-lab LAN IP>
    Path:      /data/report/
    Port:      8088
    Upload:    60 seconds

Schema written (single measurement ``ecowitt.weather``):
    tag    gateway          GW1200 PASSKEY (stable per-device id)

    Canonical shaded outdoor (WN31 on ECOWITT_SHADED_CHANNEL):
    field  outdoor_temp_f         shaded WN31
    field  outdoor_rh_pct         shaded WN31
    field  outdoor_dewpoint_f     computed from WN31 via Magnus
                                  Not written if ECOWITT_SHADED_CHANNEL is
                                  unset or the channel is silent -- fail loud
                                  rather than silently substituting sun data.

    Sun-exposed comparator (WS90 onboard temp/RH on tempf/humidity):
    field  ws90_temp_f            WS90 onboard
    field  ws90_rh_pct            WS90 onboard
    field  ws90_dewpoint_f        computed from WS90 via Magnus

    WS90 wind / solar / rain / UV:
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

    GW1200 internal:
    field  indoor_temp_f          GW1200 internal
    field  indoor_rh_pct          GW1200 internal
    field  baro_abs_inhg          GW1200 absolute baro

    Other paired WH31 channels (any channel != ECOWITT_SHADED_CHANNEL):
    field  ch{N}_temp_f           WH31/WN31 channel N (when paired)
    field  ch{N}_rh_pct           WH31/WN31 channel N (when paired)
    field  ch{N}_dewpoint_f       computed via Magnus (when paired)

Environment variables:
    ECOWITT_LISTEN_PORT     TCP port to bind (default 8088)
    ECOWITT_SHADED_CHANNEL  WH31 channel (1-8) hosting the shaded reference
                            sensor. When set, outdoor_temp_f/rh_pct/dewpoint_f
                            are sourced from that channel. When unset, those
                            fields are not written -- forcing downstream
                            consumers to surface the gap rather than silently
                            using the WS90 sun reading as canonical.
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
from typing import Any, cast
from urllib.parse import parse_qs

from influxdb_client import InfluxDBClient, Point, WritePrecision  # type: ignore[attr-defined]  # stubs lack __all__
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
    shaded_channel: int | None
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

        raw_ch = os.environ.get("ECOWITT_SHADED_CHANNEL", "").strip()
        shaded_channel: int | None
        if raw_ch == "":
            shaded_channel = None
        else:
            try:
                shaded_channel = int(raw_ch)
            except ValueError:
                log("error", "invalid_shaded_channel", value=raw_ch)
                sys.exit(2)
            if not 1 <= shaded_channel <= 8:
                log("error", "shaded_channel_out_of_range", value=shaded_channel)
                sys.exit(2)

        return Config(
            listen_port=int(os.environ.get("ECOWITT_LISTEN_PORT", "8088")),
            shaded_channel=shaded_channel,
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


def build_point(form: dict[str, str], shaded_channel: int | None = None) -> Point | None:
    """Map an Ecowitt POST payload to an InfluxDB point.

    Returns None if the payload contains no usable fields (e.g. handshake or
    misconfigured gateway sending only PASSKEY).

    ``shaded_channel`` selects which WH31 channel hosts the canonical shaded
    reference. When None, ``outdoor_*`` fields are NOT written -- analytical
    consumers see a gap instead of a silently sun-biased substitute.
    """
    passkey = form.get("PASSKEY", "unknown")
    ts = parse_dateutc(form.get("dateutc"))

    fields: dict[str, float | int] = {}

    def put(name: str, value: float | int | None) -> None:
        if value is not None:
            fields[name] = value

    # Canonical shaded outdoor (WN31 on shaded_channel). Project queries depend
    # on these names. Written only if a channel is configured AND that channel
    # is actually transmitting in this push.
    if shaded_channel is not None:
        shaded_temp_f = _f(form, f"temp{shaded_channel}f")
        shaded_rh_pct = _f(form, f"humidity{shaded_channel}")
        put("outdoor_temp_f", shaded_temp_f)
        put("outdoor_rh_pct", shaded_rh_pct)
        put("outdoor_dewpoint_f", compute_dewpoint_f(shaded_temp_f, shaded_rh_pct))

    # WS90 onboard temp/RH arrives on the singular tempf/humidity fields
    # whenever the WH26/WH32 outdoor slot is empty (intentional in this
    # deployment). Sun-exposed comparator, NOT the canonical outdoor.
    ws90_temp_f = _f(form, "tempf")
    ws90_rh_pct = _f(form, "humidity")
    put("ws90_temp_f", ws90_temp_f)
    put("ws90_rh_pct", ws90_rh_pct)
    put("ws90_dewpoint_f", compute_dewpoint_f(ws90_temp_f, ws90_rh_pct))

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

    # Other WH31 / WN31 channels (1-8) excluding the shaded canonical -- those
    # already populate outdoor_* above and we do not duplicate them. Each
    # channel only appears when a sensor is paired to that slot.
    for ch in range(1, 9):
        if ch == shaded_channel:
            continue
        t = _f(form, f"temp{ch}f")
        rh = _f(form, f"humidity{ch}")
        put(f"ch{ch}_temp_f", t)
        put(f"ch{ch}_rh_pct", rh)
        put(f"ch{ch}_dewpoint_f", compute_dewpoint_f(t, rh))

    if not fields:
        return None

    # `p: Any` lets the Point()/.tag()/.time()/.field() chain (untyped in
    # influxdb_client stubs) flow through without per-call ignores.
    # cast() at return restores the declared Point return type.
    p: Any = (
        Point(MEASUREMENT)  # type: ignore[no-untyped-call]  # influxdb_client stubs lack Point/.tag/.time annotations
        .tag("gateway", passkey)
        .time(ts, WritePrecision.S)
    )
    for name, value in fields.items():
        # Force float for measurements (avoids InfluxDB type-collision errors
        # if the same field is later written by a different writer as int).
        # Exception: rain_state is a 0/1 status flag, keep as int.
        if name == "rain_state":
            p = p.field(name, int(value))
        else:
            p = p.field(name, float(value))
    return cast(Point, p)


class _Handler(BaseHTTPRequestHandler):
    # Class attrs set in make_handler closure. write_api is annotated Any to
    # accept the None sentinel default AND the closure-bound real instance;
    # influxdb_client's write_api type is untyped in stubs regardless.
    write_api: Any = None
    bucket: str = ""
    org: str = ""
    shaded_channel: int | None = None

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

            point = build_point(form, self.shaded_channel)
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


def make_handler(
    write_api: Any, bucket: str, org: str, shaded_channel: int | None
) -> type[BaseHTTPRequestHandler]:
    class Bound(_Handler):
        pass

    Bound.write_api = write_api
    Bound.bucket = bucket
    Bound.org = org
    Bound.shaded_channel = shaded_channel
    return Bound


def main() -> None:
    cfg = Config.from_env()
    log(
        "info",
        "starting",
        port=cfg.listen_port,
        shaded_channel=cfg.shaded_channel,
        bucket=cfg.influx_bucket,
    )
    if cfg.shaded_channel is None:
        log(
            "warning",
            "no_shaded_channel_configured",
            detail="outdoor_temp_f / outdoor_rh_pct / outdoor_dewpoint_f will NOT be "
                   "written. Set ECOWITT_SHADED_CHANNEL once the shaded sensor is paired.",
        )

    client = InfluxDBClient(url=cfg.influx_url, token=cfg.influx_token, org=cfg.influx_org)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    handler = make_handler(write_api, cfg.influx_bucket, cfg.influx_org, cfg.shaded_channel)
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
        write_api.close()  # type: ignore[no-untyped-call]  # influxdb_client stubs lack close annotation
        client.close()  # type: ignore[no-untyped-call]  # influxdb_client stubs lack close annotation
        log("info", "stopped")


if __name__ == "__main__":
    main()
