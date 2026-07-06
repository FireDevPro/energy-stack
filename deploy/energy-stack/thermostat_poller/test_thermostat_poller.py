"""Tests for the thermostat poller.

Override detection was retired under the rev 4 spike-only controller
(plan 2026-07-05 Task 14): manual holds are first-class operator action,
and the `hvac.actions` row the detector compared against is days old or
absent under spike-only. These tests pin the removal:

- The module exposes no override machinery (classify_override,
  detect_and_write_override, fetch_last_action).
- Config carries no override_grace_min.
- A full poll cycle writes exactly one `hvac.thermostat` row (never
  `hvac.overrides`) and logs a `poll_ok` line with no override fields.

Run from this directory:
    python -m pytest . -q
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import signal
from pathlib import Path
from typing import Any

import pytest

from . import poller


# ---- Module surface: override machinery is gone ---------------------------


def test_override_machinery_removed() -> None:
    """Rev 4 retired override detection — none of its functions survive."""
    for name in ("classify_override", "detect_and_write_override", "fetch_last_action"):
        assert not hasattr(poller, name), f"{name} should have been removed"


def test_config_carries_no_override_grace() -> None:
    """OVERRIDE_GRACE_MIN config was retired with the detector."""
    field_names = {f.name for f in dataclasses.fields(poller.Config)}
    assert "override_grace_min" not in field_names


# ---- Poll cycle: hvac.thermostat only, no override fields ------------------


def _cfg(poll_interval: float = 3600.0) -> poller.Config:
    return poller.Config(
        device_id=4750378,
        email=None,
        password=None,
        poll_interval=poll_interval,
        influx_url="http://influx.test:8086",
        influx_token="tok",
        influx_org="org",
        influx_bucket="energy",
    )


class _FakeWriteApi:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def write(self, bucket: str, record: Any) -> None:
        self.records.append(record)


class _FakeInflux:
    """Stands in for InfluxDBClient inside main_async."""

    def __init__(self) -> None:
        self.write_api_obj = _FakeWriteApi()

    def write_api(self, write_options: Any = None) -> _FakeWriteApi:
        return self.write_api_obj

    def close(self) -> None:
        pass


async def test_poll_cycle_writes_thermostat_row_without_override_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One poll cycle end-to-end (fakes at the Influx and device-read seams):
    exactly one hvac.thermostat write — no hvac.overrides row — and a
    poll_ok log line carrying no override_detected/override keys."""
    cfg = _cfg()
    fake_influx = _FakeInflux()
    monkeypatch.setattr(poller, "InfluxDBClient", lambda **_: fake_influx)
    monkeypatch.setattr(poller, "HEALTH_MARKER", tmp_path / "last_poll_ok")
    # poller's `import signal` binds the same module object — patching the
    # stdlib module keeps main_async from clobbering pytest's handlers.
    monkeypatch.setattr(signal, "signal", lambda *_: None)

    async def fake_read(c4: Any) -> dict[str, Any]:
        return {
            "indoor_temp_f": 74.0,
            "cool_setpoint_f": 71.0,
            "heat_setpoint_f": 67.0,
            "hvac_mode": "Cool",
            "hvac_state": "Off",
            "fan_mode": "Auto",
            "hold_mode": "TemporaryHold",
            "humidity_pct": 45.0,
        }

    monkeypatch.setattr(poller, "read_snapshot", fake_read)

    task = asyncio.ensure_future(poller.main_async(cfg))
    for _ in range(500):
        if fake_influx.write_api_obj.records:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    lines = [r.to_line_protocol() for r in fake_influx.write_api_obj.records]
    assert len(lines) == 1, lines
    assert lines[0].startswith("hvac.thermostat,")
    assert "override" not in lines[0]

    out = capsys.readouterr().out
    log_records = [json.loads(line) for line in out.splitlines() if line.strip()]
    poll_ok = [rec for rec in log_records if rec.get("msg") == "poll_ok"]
    assert poll_ok, log_records
    for rec in poll_ok:
        assert "override_detected" not in rec
        assert "override" not in rec
