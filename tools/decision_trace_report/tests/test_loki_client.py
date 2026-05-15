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


def test_parse_trace_lines_extracts_json_from_loki_response(loki_url):
    """parse_trace_lines pulls each log line's JSON payload out of Loki's
    streams/values wire format."""
    raw_response = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"container": "hvac-scheduler"},
                    "values": [
                        ("1779328800000000000",
                         '{"msg": "decision_trace.day_type_decision", '
                         '"decision_for_date": "2026-05-15", "winning_day_type": "NORMAL"}'),
                        ("1779328900000000000",
                         '{"msg": "decision_trace.day_type_decision", '
                         '"decision_for_date": "2026-05-15", "winning_day_type": "NORMAL"}'),
                    ],
                },
            ],
        },
    }

    client = LokiClient(loki_url)
    parsed = client.parse_trace_lines(raw_response)

    assert len(parsed) == 2
    assert parsed[0]["msg"] == "decision_trace.day_type_decision"
    assert parsed[0]["decision_for_date"] == "2026-05-15"
    assert parsed[0]["_loki_ts_ns"] == 1779328800000000000


def test_parse_trace_lines_skips_non_json(loki_url):
    """Malformed JSON lines are silently skipped — we don't want one bad
    line to crash the entire report rendering."""
    raw_response = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {},
                    "values": [
                        ("1779328800000000000", '{"msg": "ok"}'),
                        ("1779328900000000000", "not json at all"),
                        ("1779329000000000000", '{"msg": "also ok"}'),
                    ],
                },
            ],
        },
    }
    client = LokiClient(loki_url)
    parsed = client.parse_trace_lines(raw_response)
    assert len(parsed) == 2
    assert parsed[0]["msg"] == "ok"
    assert parsed[1]["msg"] == "also ok"


def test_fetch_decision_traces_filters_by_event_name(monkeypatch, loki_url):
    """fetch_decision_traces queries with the right LogQL filter for an
    event_name and time range, then parses the response."""
    captured = {}

    def fake_get(url, params=None, timeout=None, **kwargs):
        captured["params"] = params
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {},
                        "values": [
                            ("1779328800000000000",
                             '{"msg": "decision_trace.day_type_decision", '
                             '"decision_for_date": "2026-05-15"}'),
                        ],
                    },
                ],
            },
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)

    client = LokiClient(loki_url)
    events = client.fetch_decision_traces(
        event_name="day_type_decision",
        start="2026-05-15T00:00:00Z",
        end="2026-05-15T23:59:59Z",
    )

    assert 'decision_trace.day_type_decision' in captured["params"]["query"]
    assert '{container="hvac-scheduler"}' in captured["params"]["query"]
    assert len(events) == 1
    assert events[0]["decision_for_date"] == "2026-05-15"
