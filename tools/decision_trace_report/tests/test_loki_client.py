"""Tests for LokiClient — pure HTTP wrapper, no live calls."""
from unittest.mock import MagicMock

import pytest

from tools.decision_trace_report.loki_client import LokiClient


def test_query_range_builds_correct_url_and_params(monkeypatch, loki_url):
    """query_range hits /loki/api/v1/query_range with query, start, end, limit."""
    captured = {}

    def fake_get(url, params=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["params"] = params
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)

    client = LokiClient(loki_url)
    client.query_range(
        '{container="hvac-scheduler"}',
        "2026-05-15T00:00:00Z",
        "2026-05-15T23:59:59Z",
        limit=500,
    )

    assert captured["url"] == f"{loki_url}/loki/api/v1/query_range"
    assert captured["params"]["query"] == '{container="hvac-scheduler"}'
    assert captured["params"]["start"] == "2026-05-15T00:00:00Z"
    assert captured["params"]["end"] == "2026-05-15T23:59:59Z"
    assert captured["params"]["limit"] == 500
