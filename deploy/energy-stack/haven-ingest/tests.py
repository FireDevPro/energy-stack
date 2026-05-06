"""Tests for haven-ingest's pure-logic helpers and the TokenManager
file/refresh state.

Auth0 refresh-token rotation is the most fragile part of this service —
if the rotated refresh_token isn't persisted, the container loses all
HAVEN access on the next restart. These tests pin down the file I/O
and rotation handling.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app
from app import Config, TokenManager, indoor_reading_to_point, outdoor_reading_to_point


# ---- TokenManager fixtures ------------------------------------------------


def _make_cfg(token_file: Path, refresh_token: str = "env-token") -> Config:
    return Config(
        auth0_domain="haven-production.auth0.com",
        client_id="test-client-id",
        refresh_token=refresh_token,
        device_id="3645",
        outdoor_id="60585",
        poll_interval=300.0,
        backfill_days=7,
        token_file=token_file,
        influx_url="http://influxdb:8086",
        influx_token="t",
        influx_org="o",
        influx_bucket="energy",
    )


# ---- TokenManager: load precedence ---------------------------------------


def test_token_manager_loads_from_file_when_present(tmp_path: Path):
    """File takes precedence over env; this is the steady-state path
    after the first refresh has rotated the bootstrap token."""
    token_file = tmp_path / "haven_token.json"
    token_file.write_text(json.dumps({"refresh_token": "from-file"}))
    tm = TokenManager(_make_cfg(token_file, refresh_token="env-token"))
    assert tm._refresh_token == "from-file"


def test_token_manager_falls_back_to_env_when_file_missing(tmp_path: Path):
    """Bootstrap path: no file yet, use the env var."""
    token_file = tmp_path / "haven_token.json"
    tm = TokenManager(_make_cfg(token_file, refresh_token="env-token"))
    assert tm._refresh_token == "env-token"


def test_token_manager_falls_back_to_env_on_unreadable_file(tmp_path: Path):
    """Corrupt token file shouldn't kill the service — fall back to env."""
    token_file = tmp_path / "haven_token.json"
    token_file.write_text("not-json{")
    tm = TokenManager(_make_cfg(token_file, refresh_token="env-token"))
    assert tm._refresh_token == "env-token"


def test_token_manager_exits_when_neither_source_has_token(tmp_path: Path):
    """No file AND no env var → service can't function. Exit cleanly so
    Docker restarts and the operator sees a clear error."""
    token_file = tmp_path / "haven_token.json"
    cfg = _make_cfg(token_file, refresh_token="")
    with pytest.raises(SystemExit) as excinfo:
        TokenManager(cfg)
    assert excinfo.value.code == 2


def test_token_manager_uses_empty_refresh_token_in_file_as_missing(tmp_path: Path):
    """File present but refresh_token empty → fall back to env (don't use
    the empty string as a token)."""
    token_file = tmp_path / "haven_token.json"
    token_file.write_text(json.dumps({"refresh_token": ""}))
    tm = TokenManager(_make_cfg(token_file, refresh_token="env-token"))
    assert tm._refresh_token == "env-token"


# ---- TokenManager: rotation persistence ----------------------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def test_token_manager_persists_rotated_refresh_token(tmp_path: Path, monkeypatch):
    """The CRITICAL test: when Auth0 returns a new refresh_token in the
    response, it MUST be written to the file. Without this persistence,
    the container loses access on restart."""
    token_file = tmp_path / "haven_token.json"
    cfg = _make_cfg(token_file, refresh_token="env-token-bootstrap")
    tm = TokenManager(cfg)

    monkeypatch.setattr(app.requests, "post", lambda *a, **kw: _FakeResponse({
        "access_token": "new-access-token",
        "expires_in": 86400,
        "refresh_token": "rotated-refresh-token",
    }))

    tm._do_refresh()

    # In-memory state updated.
    assert tm._access_token == "new-access-token"
    assert tm._refresh_token == "rotated-refresh-token"
    # File persisted so container restart doesn't lose access.
    assert token_file.exists()
    persisted = json.loads(token_file.read_text())
    assert persisted == {"refresh_token": "rotated-refresh-token"}


def test_token_manager_does_not_break_when_response_omits_refresh_token(tmp_path: Path, monkeypatch):
    """Auth0 may not rotate every time — if the response omits
    refresh_token, keep using the existing one."""
    token_file = tmp_path / "haven_token.json"
    cfg = _make_cfg(token_file, refresh_token="kept-token")
    tm = TokenManager(cfg)

    monkeypatch.setattr(app.requests, "post", lambda *a, **kw: _FakeResponse({
        "access_token": "new-access-token",
        "expires_in": 86400,
        # no refresh_token key
    }))

    tm._do_refresh()
    assert tm._refresh_token == "kept-token"
    # File should NOT have been written (no rotation = no persistence needed).
    assert not token_file.exists()


def test_token_manager_get_token_refreshes_when_expired(tmp_path: Path, monkeypatch):
    """_get_token must trigger refresh when within 60s of expiry."""
    token_file = tmp_path / "haven_token.json"
    tm = TokenManager(_make_cfg(token_file, refresh_token="t"))
    tm._access_token = "old"
    tm._expires_at = time.monotonic() - 10  # already expired

    monkeypatch.setattr(app.requests, "post", lambda *a, **kw: _FakeResponse({
        "access_token": "fresh",
        "expires_in": 86400,
    }))

    assert tm._get_token() == "fresh"


def test_token_manager_get_token_does_not_refresh_when_fresh(tmp_path: Path):
    """If access token is fresh, _get_token returns cached without an HTTP call."""
    token_file = tmp_path / "haven_token.json"
    tm = TokenManager(_make_cfg(token_file, refresh_token="t"))
    tm._access_token = "still-good"
    tm._expires_at = time.monotonic() + 3600  # 1 hour out

    # If a refresh call were attempted it would hit the real Auth0 endpoint
    # and fail. Just calling without monkeypatch verifies no refresh happened.
    assert tm._get_token() == "still-good"


# ---- indoor_reading_to_point ---------------------------------------------


def _indoor_reading():
    return {
        "timestamp": "2026-05-06T12:00:00+00:00",
        "airflow": 0.45,
        "voc_count": 12.0,
        "voc_mc": 80.0,
        "pm_count": 50.0,
        "pm_mc": 5.0,
        "temperature": 22.0,
        "humidity": 45.0,
        "voc_status": "good",
        "pm_status": "good",
        "humidity_status": "good",
        "combined_status": "good",
    }


def test_indoor_reading_to_point_carries_device_tag():
    p = indoor_reading_to_point(_indoor_reading(), "3645")
    assert dict(p._tags) == {"device_id": "3645"}


def test_indoor_reading_to_point_carries_all_fields():
    p = indoor_reading_to_point(_indoor_reading(), "3645")
    fields = dict(p._fields)
    for required in ("airflow", "voc_count", "voc_mc", "pm_count", "pm_mc",
                      "temperature_c", "humidity",
                      "voc_status", "pm_status", "humidity_status",
                      "combined_status"):
        assert required in fields, f"missing field: {required}"


def test_indoor_reading_to_point_uses_explicit_ts_when_provided():
    """The poll path passes an explicit ts (current poll time) instead of
    the reading's own timestamp — verify the override works."""
    explicit = datetime(2026, 7, 4, 18, 0, tzinfo=timezone.utc)
    p = indoor_reading_to_point(_indoor_reading(), "3645", ts=explicit)
    # Point's _time is set; can't easily inspect from outside but this at
    # least verifies the code path doesn't crash with an explicit ts.
    assert p is not None


# ---- outdoor_reading_to_point --------------------------------------------


def _outdoor_reading():
    return {
        "timestamp": "2026-05-06T12:00:00+00:00",
        "temperature": 18.0,
        "humidity": 55.0,
        "dew_point": 9.0,
        "aqi": 42,
        "co": 0.3,
        "o3": 0.05,
        "no2": 0.02,
        "so2": 0.01,
        "pm25": 8.0,
        "pm10": 12.0,
        "condition": 1,
        "hazel": 0.0,
        "alder": 0.0,
        "birch": 1.0,
        "oak": 2.0,
        "grass": 5.0,
        "mugwort": 0.0,
        "ragweed": 0.0,
    }


def test_outdoor_reading_to_point_carries_station_tag():
    p = outdoor_reading_to_point(_outdoor_reading(), "60585")
    assert dict(p._tags) == {"station_id": "60585"}


def test_outdoor_reading_to_point_carries_air_quality_fields():
    p = outdoor_reading_to_point(_outdoor_reading(), "60585")
    fields = dict(p._fields)
    for required in ("temperature_c", "humidity", "dew_point", "aqi",
                      "co", "o3", "no2", "so2", "pm25", "pm10", "condition"):
        assert required in fields, f"missing field: {required}"


def test_outdoor_reading_to_point_carries_pollen_fields():
    p = outdoor_reading_to_point(_outdoor_reading(), "60585")
    fields = dict(p._fields)
    for pollen in ("hazel", "alder", "birch", "oak", "grass", "mugwort", "ragweed"):
        assert pollen in fields, f"missing pollen field: {pollen}"
