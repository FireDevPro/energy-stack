"""FastAPI cockpit backend.

Single endpoint `/api/snapshot` returns the cockpit Snapshot dict.

Phase 3 initial landing: serves a canned summer_normal snapshot.
Follow-on PR wires Influx + Loki queries via influx.py + loki.py +
snapshot.build_snapshot_live.

Run:
    uvicorn tools.cockpit.backend.app:app --reload --port 8000

Or from tools/cockpit/backend/:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    # Package import (when run as `tools.cockpit.backend.app:app`).
    from tools.cockpit.backend.snapshot import build_snapshot_canned
except ImportError:
    # Local import (when run from tools/cockpit/backend/ as `app:app`).
    from snapshot import build_snapshot_canned  # type: ignore[no-redef]


app = FastAPI(
    title="Controller Cockpit Backend",
    description=(
        "Workstation-local read-only proxy for the HVAC controller. "
        "Returns the cockpit Snapshot dict."
    ),
    version="0.1.0",
)

# Vite dev server runs on :5173 by default. Allow CORS from that origin
# so the frontend can fetch /api/snapshot during dev. Production build
# would proxy /api/* through the same origin; CORS is dev-only.
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
    return {"ok": True, "backend": "cockpit", "version": app.version}


@app.get("/api/snapshot")
def snapshot() -> dict[str, Any]:
    """Return the latest cockpit snapshot.

    Phase 3 initial: canned summer_normal data.
    Phase 3 follow-on: live data assembled from Influx + Loki via
    snapshot.build_snapshot_live.
    """
    backend_mode = os.environ.get("COCKPIT_BACKEND_MODE", "canned").lower()
    if backend_mode != "canned":
        # Live wiring is a follow-on; until it's implemented, error
        # explicitly rather than silently returning canned data when
        # someone expects live.
        raise NotImplementedError(
            f"COCKPIT_BACKEND_MODE={backend_mode} is not yet supported. "
            "Live Influx/Loki assembly lands in the Phase 3 follow-on."
        )
    return build_snapshot_canned()
