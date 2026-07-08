"""Assemble ``GET /api/vigil/timeline?hours=24`` — the day ribbon (~2 min)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import vigil_queries as q
from .vigil_config import VigilConfig
from .vigil_derive import build_episodes


def _peak_tier(strs: list[str]) -> str:
    return "scarcity" if "scarcity" in strs else "elevated"


def assemble_timeline(
    query_api: Any, *, bucket: str, config: VigilConfig, hours: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    price_series = q.query_price_series(query_api, bucket=bucket, hours=hours)
    transitions = q.query_price_overlay_transitions(query_api, bucket=bucket, hours=hours)
    episodes = build_episodes(transitions, now=now)

    holds = [
        {
            "from": ep["started_at"].isoformat(),
            "to": (ep["ended_at"] or now).isoformat(),
            "tier": _peak_tier(ep["tiers_walked_strs"]),
        }
        for ep in episodes
        if ep["tiers_walked_strs"]
    ]

    return {
        "hours": hours,
        "thresholds": {"elevated_at": config.elevated_at, "scarcity_at": config.scarcity_at},
        "price_series": price_series,
        "holds": holds,
    }
