"""Shared fixtures for decision_trace_report tests."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

CT = ZoneInfo("America/Chicago")


@pytest.fixture
def target_date_iso() -> str:
    """Default target date for tests (CT calendar day)."""
    return "2026-05-15"


@pytest.fixture
def now_ct() -> datetime:
    """Frozen 'now' for deterministic feed-health tests."""
    return datetime(2026, 5, 16, 8, 0, tzinfo=CT)


@pytest.fixture
def loki_url() -> str:
    return "http://loki.test:3100"


@pytest.fixture
def influx_url() -> str:
    return "http://influx.test:8086"


def make_loki_stream(values: list[tuple[str, str]]) -> dict:
    """Build a Loki query_range response stream wrapper.

    `values` is a list of (epoch_ns_str, log_line) tuples — matches
    Loki's wire format.
    """
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {"stream": {"container": "hvac-scheduler"}, "values": list(values)},
            ],
        },
    }


def make_empty_loki_response() -> dict:
    return {
        "status": "success",
        "data": {"resultType": "streams", "result": []},
    }
