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
