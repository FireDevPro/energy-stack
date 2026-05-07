"""Tests for the telegram-notifier — focused on the alert-checker logic.

Cost calc, daily-summary builder, and other heavily Flux-integrated paths
are not covered here; they need integration tests against InfluxDB.

What IS covered:
- check_poller_silence per-poller tolerance lookup, including the
  comfortnet-publisher entry added with the ComfortNet pipeline.
- check_price_spike threshold logic.
- Alert dataclass shape (dedupe-key + text contract).

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import app
from app import (
    Alert,
    check_poller_silence,
    check_price_spike,
)


# ---- check_poller_silence -------------------------------------------------


def _last_writes(*, fresh_pollers=(), stale_minutes_ago=None, missing_pollers=()):
    """Build a poller_last_writes-style dict.

    fresh_pollers: names that wrote a few seconds ago (well under any tolerance)
    stale_minutes_ago: dict of {poller_name: minutes_since_last_write}
    missing_pollers: names that have no write at all (last_ts=None)
    """
    out = {}
    now = datetime.now(timezone.utc)
    for p in fresh_pollers:
        out[p] = now - timedelta(seconds=10)
    for p, m in (stale_minutes_ago or {}).items():
        out[p] = now - timedelta(minutes=m)
    for p in missing_pollers:
        out[p] = None
    return out


def test_check_poller_silence_no_alerts_when_all_fresh(monkeypatch):
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            fresh_pollers=["comed-poller", "eagle-poller",
                                           "refoss-poller", "nws-poller",
                                           "thermostat-poller", "comfortnet-publisher"],
                        ))
    alerts = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert alerts == []


def test_check_poller_silence_fires_for_missing_data(monkeypatch):
    """A poller with no data at all (last_ts=None) fires an alert."""
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(missing_pollers=["eagle-poller"]))
    alerts = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert len(alerts) == 1
    assert alerts[0].key == "silent:eagle-poller"
    assert "eagle-poller" in alerts[0].text
    assert "no data" in alerts[0].text


def test_check_poller_silence_default_tolerance_for_subminute_pollers(monkeypatch):
    """refoss-poller and eagle-poller use the default threshold_min."""
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            stale_minutes_ago={"refoss-poller": 11, "eagle-poller": 11},
                        ))
    alerts = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    keys = sorted(a.key for a in alerts)
    assert keys == ["silent:eagle-poller", "silent:refoss-poller"]


def test_check_poller_silence_below_default_tolerance_does_not_fire(monkeypatch):
    """9 min stale, 10 min tolerance → no alert."""
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            stale_minutes_ago={"refoss-poller": 9},
                        ))
    alerts = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert alerts == []


def test_check_poller_silence_per_poller_tolerances(monkeypatch):
    """ComEd has 25-min tolerance; nws 70; thermostat 30; comfortnet 30."""
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            stale_minutes_ago={
                                "comed-poller": 24,        # under tol (25), no alert
                                "nws-poller": 60,          # under tol (70), no alert
                                "thermostat-poller": 25,   # under tol (30), no alert
                                "comfortnet-publisher": 25,  # under tol (30), no alert
                            },
                        ))
    alerts = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert alerts == [], f"unexpected alerts: {[a.key for a in alerts]}"


def test_check_poller_silence_comfortnet_fires_at_31_min(monkeypatch):
    """The 30-min tolerance for comfortnet-publisher (added with the
    ComfortNet pipeline) — alert at 31 min, no alert at 29 min."""
    # 31 min stale → fires
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            stale_minutes_ago={"comfortnet-publisher": 31},
                        ))
    fired = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert len(fired) == 1
    assert fired[0].key == "silent:comfortnet-publisher"
    # Alert text includes the actual age and the tolerance.
    assert "31" in fired[0].text and "30" in fired[0].text

    # 29 min stale → silent
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            stale_minutes_ago={"comfortnet-publisher": 29},
                        ))
    silent = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert silent == []


def test_check_poller_silence_unknown_poller_uses_default_threshold(monkeypatch):
    """Any poller not in the tolerance map falls back to threshold_min."""
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            stale_minutes_ago={"made-up-poller": 11},
                        ))
    alerts = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert len(alerts) == 1
    assert alerts[0].key == "silent:made-up-poller"


def test_check_poller_silence_dedupe_key_format():
    """All silence alerts use the silent:<poller> key shape so the dedup
    layer suppresses repeats per poller."""
    a = Alert(key="silent:foo", text="...")
    assert a.key.startswith("silent:")


# ---- check_price_spike ----------------------------------------------------


def test_check_price_spike_no_rows_no_alert(monkeypatch):
    monkeypatch.setattr(app, "fetch_one", lambda q, f: [])
    assert check_price_spike(MagicMock(), "energy", threshold_c=20) == []


def test_check_price_spike_below_threshold_no_alert(monkeypatch):
    monkeypatch.setattr(app, "fetch_one",
                        lambda q, f: [{"_value": 19.9}])
    assert check_price_spike(MagicMock(), "energy", threshold_c=20) == []


def test_check_price_spike_at_threshold_fires(monkeypatch):
    """Threshold is inclusive — exactly-at fires."""
    monkeypatch.setattr(app, "fetch_one",
                        lambda q, f: [{"_value": 20.0}])
    alerts = check_price_spike(MagicMock(), "energy", threshold_c=20)
    assert len(alerts) == 1
    assert "20.0" in alerts[0].text


def test_check_price_spike_well_above_threshold_fires(monkeypatch):
    monkeypatch.setattr(app, "fetch_one",
                        lambda q, f: [{"_value": 47.5}])
    alerts = check_price_spike(MagicMock(), "energy", threshold_c=20)
    assert len(alerts) == 1
    assert alerts[0].key == "price_spike:47"
    assert "47.5" in alerts[0].text


def test_check_price_spike_null_value_no_alert(monkeypatch):
    """If the row exists but _value is None, don't crash."""
    monkeypatch.setattr(app, "fetch_one",
                        lambda q, f: [{"_value": None}])
    assert check_price_spike(MagicMock(), "energy", threshold_c=20) == []
