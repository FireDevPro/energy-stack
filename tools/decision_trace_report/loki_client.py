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

        Logs a warning if the response saturates `limit` — a chatty
        event like `price_overlay_eval` over a multi-day `--from`/`--to`
        range can exceed 5000 lines (1/min ≈ 1440/day), and a silent
        truncation would skew downstream stats. Operator should re-run
        with a higher `limit` or narrower window when the warning fires.
        """
        import logging
        log = logging.getLogger(__name__)

        full_event = f"decision_trace.{event_name}"
        query = f'{{container="hvac-scheduler"}} |= "{full_event}"'
        raw = self.query_range(query, start, end, limit=limit)
        events = self.parse_trace_lines(raw)
        if len(events) >= limit:
            log.warning(
                "fetch_decision_traces: %s %s..%s saturated limit=%d — "
                "results may be truncated; widen limit or narrow range",
                full_event, start, end, limit,
            )
        # Sort by trace's own `ts` if present, else Loki ingest time.
        events.sort(key=lambda e: e.get("ts", "") or e["_loki_ts_ns"])
        return events

    def count_reason_codes(
        self,
        start: str,
        end: str,
        per_chunk_limit: int = 5000,
    ) -> dict[str, int]:
        """Count occurrences of each `reason_code` value across
        `decision_trace.*` events in `[start, end]`.

        Returns `{reason_code: count}`. Events without a `reason_code`
        field are ignored (some decision_trace.* events may not carry
        one — defensive). Used by §5 coverage scorecard.

        **Chunked by day** to avoid silent truncation. Verbose
        commissioning emits ~3500 lines/day; a single 30-day query
        would silently truncate. We walk `[start, end]` one CT day
        at a time and accumulate.

        `per_chunk_limit` defaults to **5000** — Loki's
        `max_entries_limit_per_query` defaults to 5000 server-side and
        rejects larger requests with HTTP 400. ~3500 lines/day fits
        with headroom. If a chunk hits the limit a warning is logged
        so partial single-day counts are loud; the cumulative number
        still avoids the cliff a single oversized query would produce.

        Per-chunk HTTP errors (typically Loki retention exceeded for
        old chunks in a 30-day cumulative window) are caught + logged,
        not raised — partial-but-loud beats no-§5-at-all.

        `start` and `end` must be RFC3339 UTC timestamps (e.g.,
        `2026-05-08T00:00:00Z`). The chunking step is 24 hours; partial
        days at the edges are queried with their actual sub-day spans.
        """
        from datetime import datetime, timedelta
        import logging
        log = logging.getLogger(__name__)

        def _parse(ts: str) -> datetime:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))

        def _fmt(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        query = '{container="hvac-scheduler"} |= "decision_trace"'
        start_dt = _parse(start)
        end_dt = _parse(end)

        counts: dict[str, int] = {}
        cur = start_dt
        while cur < end_dt:
            nxt = min(cur + timedelta(days=1), end_dt)
            # Per-chunk errors are tolerated, not fatal. The cumulative
            # 30-day window routinely walks past Loki retention; the
            # too-old chunks return 400 Bad Request. We want partial-
            # but-loud: log the failed chunk and keep aggregating.
            try:
                raw = self.query_range(query, _fmt(cur), _fmt(nxt), limit=per_chunk_limit)
            except Exception as exc:
                log.warning(
                    "count_reason_codes: chunk %s..%s failed (likely "
                    "Loki retention exceeded): %s",
                    _fmt(cur), _fmt(nxt), exc,
                )
                cur = nxt
                continue
            events = self.parse_trace_lines(raw)
            if len(events) >= per_chunk_limit:
                log.warning(
                    "count_reason_codes: chunk %s..%s hit limit %d — "
                    "single-day count may be partial",
                    _fmt(cur), _fmt(nxt), per_chunk_limit,
                )
            for event in events:
                code = event.get("reason_code")
                if not isinstance(code, str):
                    continue
                counts[code] = counts.get(code, 0) + 1
            cur = nxt
        return counts
