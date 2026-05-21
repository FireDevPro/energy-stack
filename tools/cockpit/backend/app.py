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
  Workstation usage: start via ../start-cockpit.ps1 which sources
  tools/cockpit/.env.local for these values.

Run (only supported invocation form, from repo root):
    PYTHONPATH=. uvicorn tools.cockpit.backend.app:app --reload --port 8000
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from tools.cockpit.backend.snapshot import (
    build_snapshot_canned,
    build_snapshot_live,
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
                "`pip install -r tools/cockpit/backend/requirements.txt`"
            ),
        ) from e

    from tools.cockpit.backend import influx, loki  # noqa: PLC0415

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


def _build_feed_health(
    query_api: Any, bucket: str, now: datetime
) -> list[dict[str, Any]]:
    from tools.cockpit.backend import influx  # noqa: PLC0415
    from tools.cockpit.backend.freshness import classify  # noqa: PLC0415

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
