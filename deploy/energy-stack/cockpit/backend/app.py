"""FastAPI cockpit backend.

Endpoints:
- GET /api/snapshot — returns the cockpit Snapshot dict
- GET /api/health   — liveness ping for the frontend polling hook

Modes (via COCKPIT_BACKEND_MODE env var):
- "canned" (default) — returns a hand-rolled Python dict mirroring the
  summer_normal TS fixture. Used during initial Phase 3 rollout and as
  a fallback when Influx/Loki credentials are not present.
- "live" — assembles the Snapshot from live Influx + Loki query results
  using snapshot.build_snapshot_live. Requires:
    INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET
    LOKI_URL, LOKI_CONTAINER (default: hvac-scheduler)
  Deployed usage: runs as the `cockpit` compose service on Pi-lab
  (../Dockerfile); compose supplies the env vars and the image also
  serves the built frontend (see static mount at the bottom).
  Workstation dev: start via ../start-cockpit.ps1 which sources
  ../.env.local for these values.

Run (from the cockpit directory, deploy/energy-stack/cockpit/):
    uvicorn backend.app:app --reload --port 8765
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .day_ahead import (
    build_day_ahead_canned,
    build_day_ahead_live,
)
from .day_at_a_glance import (
    build_day_at_a_glance_canned,
    build_day_at_a_glance_live,
)
from .snapshot import (
    build_snapshot_canned,
    build_snapshot_live,
)
from .today_actions import (
    build_today_actions_canned,
    build_today_actions_live,
)


app = FastAPI(
    title="Controller Cockpit Backend",
    description=(
        "Workstation-local read-only proxy for the HVAC controller. "
        "Returns the cockpit Snapshot dict."
    ),
    version="0.1.0",
)

# CORS for the Vite dev server. Production runs through the Vite proxy
# (same-origin), so this is dev-only / belt-and-suspenders.
_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness check for the polling hook."""
    return {
        "ok": True,
        "backend": "cockpit",
        "version": app.version,
        "mode": _mode(),
    }


@app.get("/api/snapshot")
def snapshot() -> dict[str, Any]:
    """Return the latest cockpit snapshot.

    Mode-dispatched via COCKPIT_BACKEND_MODE env var.
    """
    mode = _mode()
    if mode == "canned":
        return build_snapshot_canned()
    if mode == "live":
        return _live_snapshot()
    raise HTTPException(
        status_code=503,
        detail=(
            f"COCKPIT_BACKEND_MODE={mode!r} is not recognized. "
            "Valid: 'canned' (default), 'live'."
        ),
    )


@app.get("/api/day_at_a_glance")
def day_at_a_glance() -> dict[str, Any]:
    """Return today's day-at-a-glance payload for the narrative cockpit
    chart: 24 hourly price bars (PJM DA forecast + ComEd realized
    overlay), indoor + setpoint history, and the planned future
    ScheduleAction list for today's day_type."""
    mode = _mode()
    if mode == "canned":
        return build_day_at_a_glance_canned()
    if mode == "live":
        return _live_day_at_a_glance()
    raise HTTPException(
        status_code=503,
        detail=(
            f"COCKPIT_BACKEND_MODE={mode!r} is not recognized. "
            "Valid: 'canned' (default), 'live'."
        ),
    )


def _mode() -> str:
    return os.environ.get("COCKPIT_BACKEND_MODE", "canned").lower()


def _live_snapshot() -> dict[str, Any]:
    """Query Influx + Loki and assemble a live Snapshot.

    Lazy imports so the canned mode (default) doesn't require
    influxdb-client / httpx availability for every dev to spin up.
    """
    try:
        from influxdb_client import InfluxDBClient  # type: ignore[attr-defined]  # noqa: PLC0415  # stubs lack __all__
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "live mode requires `influxdb-client` — install via "
                "`pip install -r deploy/energy-stack/cockpit/backend/requirements.txt`"
            ),
        ) from e

    from . import influx, loki  # noqa: PLC0415

    url = _require_env("INFLUXDB_URL")
    token = _require_env("INFLUXDB_TOKEN")
    org = _require_env("INFLUXDB_ORG")
    bucket = _require_env("INFLUXDB_BUCKET")
    loki_url = _require_env("LOKI_URL")
    container = os.environ.get("LOKI_CONTAINER", "hvac-scheduler")

    influx_client = InfluxDBClient(url=url, token=token, org=org)
    query_api = influx_client.query_api()
    loki_client = _HttpxLokiClient(loki_url)

    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 1_000_000_000)

    thermostat = influx.query_latest_thermostat(query_api, bucket=bucket)
    arm_mode = influx.query_latest_arm_mode(query_api, bucket=bucket)
    price = influx.query_latest_price(query_api, bucket=bucket)
    heartbeat = influx.query_latest_heartbeat(query_api, bucket=bucket)
    last_action = influx.query_latest_action(query_api, bucket=bucket)
    weather = influx.query_today_forecast(query_api, bucket=bucket)
    outdoor = influx.query_outdoor_now(query_api, bucket=bucket)

    feed_health = _build_feed_health(query_api, bucket, now)
    traces = loki.fetch_latest_tick_traces(
        loki_client, container=container, now_ns=now_ns
    )

    scheduler_mode = (arm_mode or {}).get("scheduler_mode", "shadow")
    return build_snapshot_live(
        thermostat=thermostat,
        arm_mode=arm_mode,
        price=price,
        heartbeat=heartbeat,
        feed_health=feed_health,
        traces=traces,
        last_action=last_action,
        weather=weather,
        outdoor=outdoor,
        scheduler_mode=scheduler_mode,
        now=now,
    )


@app.get("/api/day_ahead")
def day_ahead() -> dict[str, Any]:
    """Return tomorrow's day-type decision (from hvac.decisions, written
    at 21:00 the night before) + §7 pre-cool window if selected (from
    hvac.precool_window). Powers the narrative cockpit Day-Ahead +
    Pre-Cool cards."""
    mode = _mode()
    if mode == "canned":
        return build_day_ahead_canned()
    if mode == "live":
        return _live_day_ahead()
    raise HTTPException(
        status_code=503,
        detail=(
            f"COCKPIT_BACKEND_MODE={mode!r} is not recognized. "
            "Valid: 'canned' (default), 'live'."
        ),
    )


def _live_day_ahead() -> dict[str, Any]:
    try:
        from influxdb_client import InfluxDBClient  # type: ignore[attr-defined]  # noqa: PLC0415  # stubs lack __all__
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "live mode requires `influxdb-client` — install via "
                "`pip install -r deploy/energy-stack/cockpit/backend/requirements.txt`"
            ),
        ) from e

    from zoneinfo import ZoneInfo  # noqa: PLC0415

    from . import influx  # noqa: PLC0415

    url = _require_env("INFLUXDB_URL")
    token = _require_env("INFLUXDB_TOKEN")
    org = _require_env("INFLUXDB_ORG")
    bucket = _require_env("INFLUXDB_BUCKET")

    influx_client = InfluxDBClient(url=url, token=token, org=org)
    query_api = influx_client.query_api()

    ct = ZoneInfo("America/Chicago")
    now = datetime.now(timezone.utc)
    tomorrow_ct = (now.astimezone(ct) + timedelta(days=1)).date().isoformat()

    day_type_row = influx.query_day_type_decision(
        query_api, bucket=bucket, target_date_iso=tomorrow_ct
    )
    precool_row = influx.query_precool_window(
        query_api, bucket=bucket, target_date_iso=tomorrow_ct
    )

    return build_day_ahead_live(
        now=now, day_type_row=day_type_row, precool_row=precool_row
    )


@app.get("/api/today_actions")
def today_actions() -> dict[str, Any]:
    """Return today's planned ScheduleActions for the current day_type
    with per-item status (past / current / future). Powers the
    narrative cockpit ActionLog component."""
    mode = _mode()
    if mode == "canned":
        return build_today_actions_canned()
    if mode == "live":
        return _live_today_actions()
    raise HTTPException(
        status_code=503,
        detail=(
            f"COCKPIT_BACKEND_MODE={mode!r} is not recognized. "
            "Valid: 'canned' (default), 'live'."
        ),
    )


def _live_today_actions() -> dict[str, Any]:
    """Resolve today's day_type from the latest scheduler trace and
    build the action list. Reuses the day_at_a_glance loki-fetch
    pattern; day_type lookup is the only external dep."""
    from . import loki  # noqa: PLC0415

    loki_url = _require_env("LOKI_URL")
    container = os.environ.get("LOKI_CONTAINER", "hvac-scheduler")
    loki_client = _HttpxLokiClient(loki_url)

    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 1_000_000_000)
    traces = loki.fetch_latest_tick_traces(
        loki_client, container=container, now_ns=now_ns
    )
    day_type = (traces.get("day_type_decision") or {}).get("winning_day_type")
    return build_today_actions_live(now=now, day_type=day_type)


def _live_day_at_a_glance() -> dict[str, Any]:
    """Assemble today's day-at-a-glance payload from live Influx
    queries. Mirrors the lazy-import + env-var pattern from
    _live_snapshot()."""
    try:
        from influxdb_client import InfluxDBClient  # type: ignore[attr-defined]  # noqa: PLC0415  # stubs lack __all__
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "live mode requires `influxdb-client` — install via "
                "`pip install -r deploy/energy-stack/cockpit/backend/requirements.txt`"
            ),
        ) from e

    from zoneinfo import ZoneInfo  # noqa: PLC0415

    from . import influx, loki  # noqa: PLC0415

    url = _require_env("INFLUXDB_URL")
    token = _require_env("INFLUXDB_TOKEN")
    org = _require_env("INFLUXDB_ORG")
    bucket = _require_env("INFLUXDB_BUCKET")
    loki_url = _require_env("LOKI_URL")
    container = os.environ.get("LOKI_CONTAINER", "hvac-scheduler")

    influx_client = InfluxDBClient(url=url, token=token, org=org)
    query_api = influx_client.query_api()

    ct = ZoneInfo("America/Chicago")
    now = datetime.now(timezone.utc)
    now_ct = now.astimezone(ct)
    today_start_ct = now_ct.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end_ct = today_start_ct + timedelta(days=1)
    history_start_ct = today_start_ct - timedelta(hours=24)

    thermo_hist = influx.query_thermostat_history(
        query_api,
        bucket=bucket,
        start_utc=history_start_ct.astimezone(timezone.utc),
        end_utc=now.astimezone(timezone.utc),
    )
    realized = influx.query_hourly_avg_prices(
        query_api,
        bucket=bucket,
        start_utc=today_start_ct.astimezone(timezone.utc),
        end_utc=today_end_ct.astimezone(timezone.utc),
    )
    forecast = influx.query_da_lmp_forecast(
        query_api,
        bucket=bucket,
        start_utc=today_start_ct.astimezone(timezone.utc),
        end_utc=today_end_ct.astimezone(timezone.utc),
    )

    # day_type comes from the scheduler trace, same source the snapshot
    # endpoint uses. Reuse loki to fetch the latest tick's traces.
    loki_client = _HttpxLokiClient(loki_url)
    now_ns = int(now.timestamp() * 1_000_000_000)
    traces = loki.fetch_latest_tick_traces(
        loki_client, container=container, now_ns=now_ns
    )
    day_type = (traces.get("day_type_decision") or {}).get("winning_day_type")

    return build_day_at_a_glance_live(
        now=now,
        day_type=day_type,
        thermostat_history=thermo_hist,
        realized_hourly=realized,
        da_forecast=forecast,
    )


def _build_feed_health(
    query_api: Any, bucket: str, now: datetime
) -> list[dict[str, Any]]:
    from . import influx  # noqa: PLC0415
    from .freshness import classify  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    for display_name, measurement, freshness_key, tag_filter in influx.FEED_DEFINITIONS:
        ts = influx.query_feed_last_ts(
            query_api,
            bucket=bucket,
            measurement=measurement,
            tag_filter=tag_filter,
        )
        if ts is None:
            out.append(
                {"name": display_name, "status": "missing", "label": "no rows"}
            )
            continue
        age_ms = max(int((now - ts).total_seconds() * 1000), 0)
        out.append(
            {
                "name": display_name,
                "status": classify(freshness_key, age_ms),
                "label": _label_age(age_ms),
            }
        )
    return out


def _label_age(ms: int) -> str:
    s = ms // 1000
    if s < 60:
        return f"{s}s ago"
    m = s // 60
    if m < 60:
        return f"{m}m ago"
    h = m // 60
    return f"{h}h ago"


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise HTTPException(
            status_code=503,
            detail=f"live mode requires env var {name}",
        )
    return v


class _HttpxLokiClient:
    """Minimal Loki HTTP client. Wraps httpx.get against
    /loki/api/v1/query_range."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def query_range(
        self, query: str, *, start_ns: int, end_ns: int, limit: int = 100
    ) -> Any:
        import httpx  # lazy

        params = {
            "query": query,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(limit),
            "direction": "backward",
        }
        url = f"{self._base_url}/loki/api/v1/query_range"
        r = httpx.get(url, params=params, timeout=5.0)
        r.raise_for_status()
        return r.json()


# The Docker image copies the Vite production build to ../frontend/dist;
# when present, serve it same-origin so the container exposes the API and
# the UI on one port. Dev keeps using the Vite server (no dist, no mount).
# Mounted after all route definitions so /api/* always wins.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
