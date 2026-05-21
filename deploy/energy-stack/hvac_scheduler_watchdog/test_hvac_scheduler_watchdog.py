"""Unit tests for the hvac-scheduler-watchdog check.

The watchdog reads hvac.arm_mode rows from InfluxDB and writes
hvac.heartbeat controller_alive=false when the scheduler hasn't
emitted in the configured window (default 10 min). Per spec §11 #5,
this distinguishes B-down (controller not running) from missing-data
(other failure modes).

Real-shape integration testing against an ephemeral InfluxDB is the
plan's preferred L4 verification (Task 1.6 step 1) and is documented
as a known gap in the Phase 1 PR; on pi-lab the watchdog runs against
the live InfluxDB so behavior is validated end-to-end.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from . import check


def _mock_query_api(arm_mode_count: int) -> Any:
    """Build a query_api whose query() returns one table containing one
    record with the given count value (matching the Flux ``|> count()``
    output shape).
    """
    api = MagicMock()
    if arm_mode_count == 0:
        api.query.return_value = []
    else:
        record = MagicMock()
        record.get_value.return_value = arm_mode_count
        table = MagicMock()
        table.records = [record]
        api.query.return_value = [table]
    return api


def test_check_returns_true_and_does_not_write_when_recent_rows_present():
    query_api = _mock_query_api(arm_mode_count=5)
    write_api = MagicMock()
    alive = check.check_controller_alive(
        query_api, write_api, bucket="energy", threshold_minutes=10,
    )
    assert alive is True
    write_api.write.assert_not_called()


def test_check_returns_false_and_writes_heartbeat_when_no_recent_rows():
    query_api = _mock_query_api(arm_mode_count=0)
    write_api = MagicMock()
    alive = check.check_controller_alive(
        query_api, write_api, bucket="energy", threshold_minutes=10,
    )
    assert alive is False
    write_api.write.assert_called_once()
    record = write_api.write.call_args.kwargs.get("record")
    line = record.to_line_protocol()
    assert line.startswith("hvac.heartbeat ")
    assert "controller_alive=false" in line


def test_check_handles_count_zero_record_as_no_recent_rows():
    """Flux ``|> count()`` returns a row with value 0 when no source
    rows matched. Watchdog must treat that as 'no recent rows'."""
    query_api = _mock_query_api(arm_mode_count=0)
    # Override to return a table with an explicit zero-count record
    record = MagicMock()
    record.get_value.return_value = 0
    table = MagicMock()
    table.records = [record]
    query_api.query.return_value = [table]
    write_api = MagicMock()
    alive = check.check_controller_alive(
        query_api, write_api, bucket="energy", threshold_minutes=10,
    )
    assert alive is False
    write_api.write.assert_called_once()


def test_check_uses_threshold_minutes_in_query():
    query_api = _mock_query_api(arm_mode_count=5)
    write_api = MagicMock()
    check.check_controller_alive(
        query_api, write_api, bucket="energy", threshold_minutes=15,
    )
    flux = query_api.query.call_args[0][0]
    assert "-15m" in flux
    assert 'r._measurement == "hvac.arm_mode"' in flux
    assert 'bucket: "energy"' in flux


def test_check_propagates_query_exception_to_caller():
    """When InfluxDB itself is down, query() raises. The check function
    should propagate (not swallow) so the main loop can log the error
    via its except handler. Swallowing here would silently mark the
    controller as alive when in fact the watchdog can't see anything.
    """
    query_api = MagicMock()
    query_api.query.side_effect = ConnectionError("influx unreachable")
    write_api = MagicMock()
    with pytest.raises(ConnectionError):
        check.check_controller_alive(
            query_api, write_api, bucket="energy", threshold_minutes=10,
        )
    # No spurious heartbeat false on a query error.
    write_api.write.assert_not_called()
