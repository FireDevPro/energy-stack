"""Assemble ``GET /api/vigil/events?limit=10`` — recent spike episodes (~2 min).

Each object is one engage→release episode, newest first. Note the contract
difference from ``/now``: here ``tiers_walked`` is a list of **strings**.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import vigil_queries as q
from .vigil_config import VigilConfig
from .vigil_derive import avoided_cost, build_episodes

_CT = ZoneInfo("America/Chicago")
_LOOKBACK_HOURS = 48


def assemble_events(
    query_api: Any, *, bucket: str, config: VigilConfig, limit: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    transitions = q.query_price_overlay_transitions(
        query_api, bucket=bucket, hours=_LOOKBACK_HOURS
    )
    price_pts = q.query_price_series_raw(query_api, bucket=bucket, hours=_LOOKBACK_HOURS)
    episodes = build_episodes(transitions, now=now, peak_lookup=price_pts)

    events = []
    for ep in reversed(episodes):  # newest first
        if not ep["tiers_walked_strs"]:
            continue
        events.append({
            "started_at": ep["started_at"].isoformat(),
            "started_at_ct": ep["started_at"].astimezone(_CT).isoformat(),
            "ended_at": ep["ended_at"].isoformat() if ep["ended_at"] else None,
            "duration_min": ep["duration_min"],
            "peak_cents": ep["peak_cents"],
            "tiers_walked": ep["tiers_walked_strs"],
            "resolution": ep["resolution"],
            "est_avoided_cost_usd": avoided_cost(ep["duration_min"], ep["representative_cents"]),
        })
        if len(events) >= limit:
            break
    return {"events": events}
