"""Loki query helpers for `decision_trace.*` events.

Strategy: query for the latest decision_trace.layer_resolution row
(fires every 5-min tick), extract its tick_id, then fetch each other
event filtered by that tick_id. day_type_decision and precool_decision
don't share the 5-min tick — fetch the most recent regardless.

Live smoke against Pi-lab Loki is the operator's manual step. Mocked
tests exercise the parse + assembly path.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Protocol


class LokiClient(Protocol):
    """HTTP wrapper around Loki's /loki/api/v1/query_range. Tests use
    a Mock that returns canned response shapes; production wraps httpx
    or the energy-stack's existing LokiClient (Phase 1 report-tool)."""

    def query_range(
        self,
        query: str,
        *,
        start_ns: int,
        end_ns: int,
        limit: int = 100,
    ) -> Any: ...


def _decode_streams(response: Any) -> list[dict[str, Any]]:
    """Decode Loki streams response into a flat list of {ts_ns, line, payload}
    dicts. The `payload` is the parsed JSON object that the scheduler
    emitted via `log()`. Non-JSON lines are skipped."""
    out: list[dict[str, Any]] = []
    streams = (
        response.get("data", {}).get("result", [])
        if isinstance(response, dict)
        else []
    )
    for stream in streams:
        for entry in stream.get("values", []):
            if len(entry) < 2:
                continue
            ts_ns_str, line = entry[0], entry[1]
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(
                {"ts_ns": int(ts_ns_str), "line": line, "payload": payload}
            )
    return out


def _latest(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    latest_event: dict[str, Any] | None = None
    for ev in events:
        if latest_event is None or ev["ts_ns"] > latest_event["ts_ns"]:
            latest_event = ev
    return latest_event


def fetch_latest_tick_traces(
    client: LokiClient,
    *,
    container: str,
    now_ns: int,
    lookback_ns: int = 30 * 60 * 1_000_000_000,  # 30 minutes
) -> dict[str, Any]:
    """Fetch the most recent decision_trace.* events.

    Returns a dict keyed by event-name suffix:
        {
            "layer_resolution":   {...} | None,
            "price_overlay_eval": {...} | None,
            "supervisor":         {...} | None,  # per-invocation only
            "day_type_decision":  {...} | None,
            "precool_decision":   {...} | None,
            "tick_id":            str  | None,
        }

    Strategy: pull the latest layer_resolution to lock tick_id; pull
    price_overlay_eval + supervisor by that tick_id; pull day_type /
    precool by recency (their tick_ids are distinct per cadence).
    """
    start_ns = now_ns - lookback_ns
    base = f'{{container="{container}"}}'

    def _query(event: str, tick_id: str | None = None) -> dict[str, Any] | None:
        # Loki's |= '"event":"..."' substring match is cheap; the JSON
        # parse step filters to valid payloads.
        filter_clause = f'|= `"event":"{event}"`'
        if tick_id is not None:
            filter_clause += f' |= `"tick_id":"{tick_id}"`'
        q = f"{base} {filter_clause}"
        resp = client.query_range(
            q, start_ns=start_ns, end_ns=now_ns, limit=100
        )
        decoded = _decode_streams(resp)
        ev = _latest(decoded)
        return ev["payload"] if ev else None

    layer_resolution = _query("decision_trace.layer_resolution")
    tick_id = (
        layer_resolution.get("tick_id") if isinstance(layer_resolution, dict) else None
    )

    return {
        "tick_id": tick_id,
        "layer_resolution": layer_resolution,
        "price_overlay_eval": (
            _query("decision_trace.price_overlay_eval", tick_id)
            if tick_id
            else None
        ),
        "supervisor": (
            _query("decision_trace.supervisor", tick_id) if tick_id else None
        ),
        "day_type_decision": _query("decision_trace.day_type_decision"),
        "precool_decision": _query("decision_trace.precool_decision"),
    }
