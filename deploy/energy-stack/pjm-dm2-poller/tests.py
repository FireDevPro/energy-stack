"""Tests for the PJM Data Miner 2 poller's pure-logic transforms.

Covers the EPT->UTC conversion, point-construction shapes, schedule-table
correctness, and the multi-revision forecast key (forecast_area, evaluated_at_iso, target).
HTTP and Influx are mocked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app import (
    COMED_FORECAST_AREA,
    COMED_PNODE_ID,
    FEED_SCHEDULE,
    Config,
    PJMClient,
    _parse_ept,
    build_da_lmp_points,
    build_load_forecast_points,
    poll_once,
)

CHICAGO = ZoneInfo("America/Chicago")


# ---- _parse_ept ------------------------------------------------------------


def test_parse_ept_attaches_local_tz_then_converts():
    """EPT timestamps come in as naive ISO strings. We tag them with the
    configured tz, then convert to UTC for Influx."""
    dt = _parse_ept("2026-07-15T13:00:00", CHICAGO)
    assert dt.tzinfo is not None
    # 13:00 Chicago in mid-July (CDT, UTC-5) = 18:00 UTC
    assert dt.astimezone(timezone.utc).hour == 18


def test_parse_ept_handles_dst_boundary():
    """November DST end (fall back) is the case where Chicago has two
    01:00s. PJM uses EPT (Eastern Prevailing Time) which follows DST too;
    this test pins the conversion behavior at the boundary."""
    # 2026-11-01 02:30 Chicago is unambiguously after the fall-back.
    dt = _parse_ept("2026-11-01T02:30:00", CHICAGO)
    assert dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") == "2026-11-01 08:30"


# ---- build_da_lmp_points ---------------------------------------------------


def _da_item(hour: int, lmp: float = 30.0) -> dict:
    return {
        "datetime_beginning_ept": f"2026-07-15T{hour:02d}:00:00",
        "datetime_beginning_utc": f"2026-07-15T{hour + 5:02d}:00:00",  # CDT offset
        "pnode_id": COMED_PNODE_ID,
        "pnode_name": "COMED",
        "zone": None,
        "type": "ZONE",
        "total_lmp_da": lmp,
        "system_energy_price_da": lmp - 1.0,
        "congestion_price_da": 0.5,
        "marginal_loss_price_da": 0.5,
    }


def test_da_lmp_points_count_matches_input():
    items = [_da_item(h, 25 + h) for h in range(24)]
    points = build_da_lmp_points(items, CHICAGO)
    assert len(points) == 24


def test_da_lmp_point_carries_expected_tags_and_fields():
    items = [_da_item(13, 42.5)]
    [point] = build_da_lmp_points(items, CHICAGO)
    line = point.to_line_protocol()
    assert "pjm.lmp_da_hourly" in line
    assert f"pnode_id={COMED_PNODE_ID}" in line
    assert "pnode_name=COMED" in line
    assert "total_lmp_da=42.5" in line


def test_da_lmp_zone_tag_falls_back_to_pnode_name_when_zone_null():
    """For ZONE-type pnodes, PJM returns zone=null and the zone name is in
    pnode_name. We synthesize the zone tag so downstream queries can still
    filter by zone."""
    items = [_da_item(0)]
    [point] = build_da_lmp_points(items, CHICAGO)
    line = point.to_line_protocol()
    assert "zone=COMED" in line


def test_da_lmp_handles_missing_optional_fields():
    """Defensive: if PJM ever drops a price component, we write 0 rather
    than crash. Real responses always carry all four."""
    item = _da_item(10)
    item["congestion_price_da"] = None
    item["marginal_loss_price_da"] = None
    [point] = build_da_lmp_points([item], CHICAGO)
    line = point.to_line_protocol()
    assert "congestion_price_da=0" in line
    assert "marginal_loss_price_da=0" in line


# ---- build_load_forecast_points -------------------------------------------


def _forecast_item(target_hour: int, eval_hour: int, mw: float = 10000.0) -> dict:
    return {
        "evaluated_at_datetime_ept": f"2026-07-15T{eval_hour:02d}:00:00",
        "evaluated_at_datetime_utc": f"2026-07-15T{eval_hour + 5:02d}:00:00",
        "forecast_area": COMED_FORECAST_AREA,
        "forecast_datetime_beginning_ept": f"2026-07-16T{target_hour:02d}:00:00",
        "forecast_datetime_beginning_utc": f"2026-07-16T{target_hour + 5:02d}:00:00",
        "forecast_datetime_ending_ept": f"2026-07-16T{target_hour + 1:02d}:00:00",
        "forecast_datetime_ending_utc": f"2026-07-16T{target_hour + 6:02d}:00:00",
        "forecast_load_mw": mw,
    }


def test_forecast_points_carry_evaluated_at_tag_for_revision_dedup():
    """Same target hour evaluated twice yields TWO Influx points with
    distinct `evaluated_at_iso` tags. This is how the design tracks
    forecast revisions instead of overwriting them."""
    items = [
        _forecast_item(target_hour=15, eval_hour=6, mw=11000),
        _forecast_item(target_hour=15, eval_hour=13, mw=11500),
    ]
    a, b = build_load_forecast_points(items, CHICAGO)
    line_a = a.to_line_protocol()
    line_b = b.to_line_protocol()
    assert "evaluated_at_iso" in line_a
    assert "evaluated_at_iso" in line_b
    # Different evaluated_at -> different tag values
    eval_a = [t for t in line_a.split(",") if "evaluated_at_iso" in t][0]
    eval_b = [t for t in line_b.split(",") if "evaluated_at_iso" in t][0]
    assert eval_a != eval_b


def test_forecast_horizon_field_correct():
    # Forecast made at 06:00 CT for target 15:00 CT next day = 33-hour horizon
    items = [_forecast_item(target_hour=15, eval_hour=6)]
    [point] = build_load_forecast_points(items, CHICAGO)
    line = point.to_line_protocol()
    assert "horizon_hours=33i" in line


def test_forecast_area_tag_set():
    items = [_forecast_item(target_hour=15, eval_hour=6)]
    [point] = build_load_forecast_points(items, CHICAGO)
    line = point.to_line_protocol()
    assert "forecast_area=COMED" in line


# ---- FEED_SCHEDULE pinning -------------------------------------------------


def test_da_lmp_fires_at_17_only():
    """Day-Ahead market clears ~16:00 CT; we poll at 17:00 to ensure
    tomorrow's prices are posted. Single fire-time per day."""
    assert FEED_SCHEDULE["da_hrl_lmps"] == (17,)


def test_load_forecast_fires_at_06_and_13():
    """Twice daily picks up the morning revision and the late-morning
    update. Same cadence the hvac-scheduler uses for nws.forecast revisits."""
    assert FEED_SCHEDULE["load_frcstd_7_day"] == (6, 13)


# ---- poll_once dispatching -------------------------------------------------


@pytest.mark.asyncio
async def test_poll_once_skips_feeds_outside_their_window(monkeypatch):
    """At hour 03:00 local, no feed should fire. Verifies the schedule
    table is the gate, not always-on."""
    cfg = _test_config(monkeypatch, hour=3)

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        return []

    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert fetched == []
    write_api.write.assert_not_called()


@pytest.mark.asyncio
async def test_poll_once_fires_da_lmp_at_hour_17(monkeypatch):
    cfg = _test_config(monkeypatch, hour=17)

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        return [_da_item(0, 30.0)]  # one row to write

    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert "da_hrl_lmps" in fetched
    assert "load_frcstd_7_day" not in fetched
    assert write_api.write.called


@pytest.mark.asyncio
async def test_poll_once_fires_forecast_at_hour_13(monkeypatch):
    cfg = _test_config(monkeypatch, hour=13)

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        return [_forecast_item(15, 13)]

    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert "load_frcstd_7_day" in fetched
    assert "da_hrl_lmps" not in fetched


@pytest.mark.asyncio
async def test_poll_once_continues_when_feed_fails(monkeypatch):
    """A 500 from PJM on one feed must not abort the cycle. We log and
    move on."""
    cfg = _test_config(monkeypatch, hour=17)

    client = MagicMock()

    async def failing_fetch(feed, params):
        raise RuntimeError("PJM da_hrl_lmps HTTP 500")

    client.fetch = failing_fetch
    write_api = MagicMock()

    # Should not raise.
    await poll_once(client, write_api, cfg)
    write_api.write.assert_not_called()


# ---- helpers ---------------------------------------------------------------


def _test_config(monkeypatch, hour: int) -> Config:
    """Build a Config and stub `datetime.now(tz)` to a known local hour."""
    cfg = Config(
        api_key="fake-key-for-tests",
        poll_interval_s=3600,
        influx_url="http://test",
        influx_token="t",
        influx_org="o",
        influx_bucket="b",
        tz=CHICAGO,
    )
    fake_now = datetime(2026, 7, 15, hour, 0, 0, tzinfo=CHICAGO)

    class _StubDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is not None else fake_now.replace(tzinfo=None)

    monkeypatch.setattr("app.datetime", _StubDatetime)
    return cfg
