"""FastAPI cockpit backend — "The Vigil" (rev 4.1).

Read-only wall board for the spike-only HVAC controller. Serves three live
polls from InfluxDB (rev-4 measurements only — no Loki) and the single-file
frontend same-origin on :8765.

Endpoints:
- GET /api/health                     — liveness ping
- GET /api/vigil/now                  — live-state poll (client ~15s)
- GET /api/vigil/timeline?hours=24    — day ribbon (client ~2min)
- GET /api/vigil/events?limit=10      — recent spike episodes (client ~2min)

Env (live queries): INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET.
Tier thresholds come from the read-only config mount (see vigil_config); on the
Pi that is the SAME commissioning-controller.yaml the controller reads.

Run (from deploy/energy-stack/cockpit/):
    uvicorn backend.app:app --reload --port 8765
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .vigil_config import VigilConfig, load_config
from .vigil_events import assemble_events
from .vigil_now import assemble_now
from .vigil_timeline import assemble_timeline

app = FastAPI(
    title="Controller Cockpit — The Vigil",
    description="Read-only rev-4.1 wall board. Live state from InfluxDB.",
    version="4.1.0",
)

_config: VigilConfig | None = None
_query_api: Any = None


def _get_config() -> VigilConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise HTTPException(status_code=503, detail=f"missing env var {name}")
    return v


def _get_query_api() -> Any:
    """Lazily build and cache a single InfluxDB query client."""
    global _query_api
    if _query_api is not None:
        return _query_api
    try:
        from influxdb_client import InfluxDBClient  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "influxdb-client not installed — "
                "pip install -r backend/requirements.txt"
            ),
        ) from e
    client = InfluxDBClient(
        url=_require_env("INFLUXDB_URL"),
        token=_require_env("INFLUXDB_TOKEN"),
        org=_require_env("INFLUXDB_ORG"),
    )
    _query_api = client.query_api()
    return _query_api


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "backend": "vigil", "version": app.version}


@app.get("/api/vigil/now")
def vigil_now() -> dict[str, Any]:
    bucket = _require_env("INFLUXDB_BUCKET")
    return assemble_now(_get_query_api(), bucket=bucket, config=_get_config())


@app.get("/api/vigil/timeline")
def vigil_timeline(hours: int = 24) -> dict[str, Any]:
    bucket = _require_env("INFLUXDB_BUCKET")
    return assemble_timeline(
        _get_query_api(), bucket=bucket, config=_get_config(), hours=hours
    )


@app.get("/api/vigil/events")
def vigil_events(limit: int = 10) -> dict[str, Any]:
    bucket = _require_env("INFLUXDB_BUCKET")
    return assemble_events(
        _get_query_api(), bucket=bucket, config=_get_config(), limit=limit
    )


# Serve the single-file board same-origin. Mounted after routes so /api/* wins.
# The image copies frontend/index.html (the delivered vigil.html); dev serves
# the same file straight from the repo.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend"
if (_STATIC_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
