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


def test_fetch_decision_traces_warns_on_limit_hit(monkeypatch, caplog, loki_url):
    """Codex P3 regression: a multi-day --from/--to range can exceed
    the default limit=5000 for chatty events (price_overlay_eval at
    ~1/minute = 1440/day). If the response saturates, the silently-
    truncated result would corrupt downstream stats. The client must
    log a warning so the operator sees it."""
    import logging

    def fake_get(url, params=None, timeout=None, **kwargs):
        # Return a stream that exactly hits the requested limit so the
        # client cannot distinguish "happened to fit" from "truncated".
        n = params["limit"]
        values = [
            (str(1779328800000000000 + i), '{"msg": "decision_trace.x"}')
            for i in range(n)
        ]
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [{"stream": {}, "values": values}],
            },
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)
    client = LokiClient(loki_url)

    with caplog.at_level(logging.WARNING, logger="tools.decision_trace_report.loki_client"):
        events = client.fetch_decision_traces(
            event_name="price_overlay_eval",
            start="2026-05-10T00:00:00Z",
            end="2026-05-17T00:00:00Z",
            limit=5000,
        )

    assert len(events) == 5000
    assert any(
        "fetch_decision_traces" in rec.message and "limit" in rec.message
        for rec in caplog.records
    ), "expected truncation warning from fetch_decision_traces"


def test_count_reason_codes_aggregates_across_chunks(monkeypatch, loki_url):
    """count_reason_codes chunks by day and accumulates. A 7-day query
    must issue 7 separate query_range calls (one per day) and sum the
    per-chunk results."""
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        calls.append({"params": dict(params)})
        # Return 2 PRICE + 1 SUPERVISOR per chunk
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
                             '{"msg": "decision_trace.price_overlay_eval", '
                             '"reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"}'),
                            ("1779328860000000000",
                             '{"msg": "decision_trace.price_overlay_eval", '
                             '"reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"}'),
                            ("1779328920000000000",
                             '{"msg": "decision_trace.supervisor", '
                             '"reason_code": "SUPERVISOR_APPROVED"}'),
                        ],
                    },
                ],
            },
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)
    client = LokiClient(loki_url)
    counts = client.count_reason_codes(
        start="2026-05-08T00:00:00Z",
        end="2026-05-15T00:00:00Z",
    )

    # 7-day window with 24h chunks -> 7 separate Loki queries
    assert len(calls) == 7
    # Query matches `decision_trace.` events
    assert "decision_trace" in calls[0]["params"]["query"]
    # Each chunk's 2 PRICE + 1 SUPERVISOR rows accumulated across 7 chunks
    assert counts["PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"] == 14
    assert counts["SUPERVISOR_APPROVED"] == 7


def test_count_reason_codes_tolerates_per_chunk_errors(monkeypatch, caplog, loki_url):
    """Live-verification regression: when a cumulative window walks past
    Loki retention (typical 30d cumulative vs ~14d retention), the
    too-old chunks return 400 Bad Request. Previously this raised and
    killed the entire §5 call. The aggregator must catch per-chunk
    HTTP errors, log a warning, and continue accumulating over the
    chunks that DID succeed.
    """
    import logging
    import requests

    def fake_get(url, params=None, timeout=None, **kwargs):
        response = MagicMock()
        # First two chunks: 400 (out of retention). Remaining chunks:
        # one PRICE row each so we can assert partial aggregation.
        if params["start"] < "2026-05-10T00:00:00Z":
            response.status_code = 400
            response.json.return_value = {"status": "fail", "data": "out of retention"}
            err = requests.HTTPError("400 Bad Request")
            response.raise_for_status = MagicMock(side_effect=err)
            return response
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
                             '{"msg": "decision_trace.price_overlay_eval", '
                             '"reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"}'),
                        ],
                    },
                ],
            },
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)
    client = LokiClient(loki_url)

    with caplog.at_level(logging.WARNING, logger="tools.decision_trace_report.loki_client"):
        counts = client.count_reason_codes(
            start="2026-05-08T00:00:00Z",
            end="2026-05-15T00:00:00Z",
        )

    # 7 chunks attempted; first 2 (May 8 + May 9) failed; 5 succeeded.
    # 5 successful chunks × 1 PRICE row each = 5
    assert counts.get("PRICE_OVERLAY_NORMAL_BELOW_TRIGGER") == 5
    # Warning logged for failed chunks
    assert any(
        "count_reason_codes" in rec.message
        and ("chunk failed" in rec.message or "retention" in rec.message.lower())
        for rec in caplog.records
    ), "expected per-chunk failure warning"


def test_count_reason_codes_handles_partial_day_at_edges(monkeypatch, loki_url):
    """A non-day-aligned range still works — final chunk is sub-day,
    no extra calls past `end`."""
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        calls.append({"params": dict(params)})
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
    # 1.5-day window -> 2 chunks (24h + 12h)
    client.count_reason_codes(
        start="2026-05-08T00:00:00Z",
        end="2026-05-09T12:00:00Z",
    )
    assert len(calls) == 2
    # First chunk full day, second chunk 12h
    assert calls[0]["params"]["start"] == "2026-05-08T00:00:00Z"
    assert calls[0]["params"]["end"] == "2026-05-09T00:00:00Z"
    assert calls[1]["params"]["start"] == "2026-05-09T00:00:00Z"
    assert calls[1]["params"]["end"] == "2026-05-09T12:00:00Z"


def test_count_reason_codes_ignores_events_without_reason_code(monkeypatch, loki_url):
    """Some trace lines may not carry reason_code (defensive); skip them
    rather than counting blank as a 'code'."""

    def fake_get(url, params=None, timeout=None, **kwargs):
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
                             '{"msg": "decision_trace.startup"}'),  # no reason_code
                            ("1779328860000000000",
                             '{"msg": "decision_trace.supervisor", '
                             '"reason_code": "SUPERVISOR_APPROVED"}'),
                        ],
                    },
                ],
            },
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)
    client = LokiClient(loki_url)
    counts = client.count_reason_codes(
        # Single-day window so we still get exactly 1 query
        start="2026-05-14T00:00:00Z",
        end="2026-05-15T00:00:00Z",
    )
    assert counts == {"SUPERVISOR_APPROVED": 1}


def test_count_reason_codes_warns_on_chunk_at_limit(monkeypatch, loki_url, caplog):
    """If a single-day chunk returns exactly `per_chunk_limit` events,
    the function logs a warning so the operator knows the count for
    that day may be partial."""
    def fake_get(url, params=None, timeout=None, **kwargs):
        response = MagicMock()
        response.status_code = 200
        # Build per_chunk_limit synthetic events
        n = 5  # use a tiny limit to keep the fixture small
        values = [
            (str(1779328800000000000 + i),
             '{"msg": "decision_trace.x", "reason_code": "FAKE_CODE"}')
            for i in range(n)
        ]
        response.json.return_value = {
            "status": "success",
            "data": {"resultType": "streams",
                      "result": [{"stream": {}, "values": values}]},
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)
    client = LokiClient(loki_url)
    import logging
    with caplog.at_level(logging.WARNING):
        client.count_reason_codes(
            start="2026-05-14T00:00:00Z",
            end="2026-05-15T00:00:00Z",
            per_chunk_limit=5,
        )
    # Limit was hit -> warning emitted
    assert any("hit limit" in rec.message for rec in caplog.records)
