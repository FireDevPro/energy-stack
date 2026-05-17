"""Loki query helpers for `decision_trace.*` events.

Phase 3 follow-on: each function below issues a LogQL query and
returns decoded trace events.

For initial Phase 3 landing, stubs.
"""
from __future__ import annotations

from typing import Any, Protocol


class LokiClient(Protocol):
    """Loki HTTP client shape we depend on. Tests use a mock."""

    def query_range(
        self,
        query: str,
        *,
        start_ns: int,
        end_ns: int,
        limit: int = 100,
    ) -> Any: ...


def fetch_latest_tick_traces(
    client: LokiClient, *, container: str
) -> dict[str, Any]:
    """Fetch the most recent decision_trace.* events for a single tick.

    Strategy: query for the latest decision_trace.layer_resolution row
    (fires every 5-min tick), extract its tick_id, then fetch each
    other event filtered by that tick_id.

    decision_trace.day_type_decision and decision_trace.precool_decision
    don't share the 5-min tick_id; fetch the most recent regardless.

    Returns a dict keyed by event-name suffix:
        {
            "price_overlay_eval": {...},
            "layer_resolution": {...},
            "supervisor": {...} | None,
            "day_type_decision": {...},
            "precool_decision": {...} | None,
        }
    """
    raise NotImplementedError("Phase 3 follow-on")
