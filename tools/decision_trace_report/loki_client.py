"""Loki HTTP API wrapper for decision-trace report queries.

No live Pi/LAN calls in tests; mocks handle the requests layer.
"""
from typing import Any

import requests


class LokiClient:
    """Thin wrapper around Loki's `/loki/api/v1/query_range` endpoint."""

    def __init__(self, base_url: str, timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def query_range(
        self,
        query: str,
        start: str,
        end: str,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Run a LogQL query over a time range. Returns the parsed JSON response."""
        response = requests.get(
            f"{self.base_url}/loki/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "limit": limit},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def parse_trace_lines(response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract JSON-payload dicts from a Loki query_range response.

        Each Loki value is a (ns_timestamp, log_line) tuple. We parse the
        log_line as JSON and inject `_loki_ts_ns` so downstream sorters
        can use either the parsed event's own `ts` or Loki's ingest time.
        Malformed JSON is silently skipped — one bad line must not crash
        the report.
        """
        import json
        out: list[dict[str, Any]] = []
        for stream in response.get("data", {}).get("result", []):
            for ts_ns, line in stream.get("values", []):
                try:
                    parsed = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                parsed["_loki_ts_ns"] = int(ts_ns)
                out.append(parsed)
        return out

    def fetch_decision_traces(
        self,
        event_name: str,
        start: str,
        end: str,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Pull decision_trace.<event_name> log lines from Loki over a
        time range. Returns the JSON-parsed events sorted by `ts`.

        `event_name` is the suffix after `decision_trace.` (e.g.,
        `"day_type_decision"`, `"price_overlay_eval"`).
        """
        full_event = f"decision_trace.{event_name}"
        query = f'{{container="hvac-scheduler"}} |= "{full_event}"'
        raw = self.query_range(query, start, end, limit=limit)
        events = self.parse_trace_lines(raw)
        # Sort by trace's own `ts` if present, else Loki ingest time.
        events.sort(key=lambda e: e.get("ts", "") or e["_loki_ts_ns"])
        return events
