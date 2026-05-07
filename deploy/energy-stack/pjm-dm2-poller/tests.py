"""Tests for the PJM Data Miner 2 poller's pure-logic transforms.

Covers EPT->UTC conversion, point-construction shapes for all 5 feeds,
schedule-table correctness (including weekday/month/day-of-month
restrictions), and the multi-feed dispatch logic. HTTP and Influx
are mocked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app import (
    COMED_FORECAST_AREA,
    COMED_METERED_ZONE,
    COMED_NSPL_ZONE,
    COMED_PNODE_ID,
    FEED_DISPATCHERS,
    FEED_SCHEDULE,
    Config,
    Schedule,
    _parse_ept,
    build_da_lmp_points,
    build_load_forecast_points,
    build_metered_load_points,
    build_nspl_points,
    build_peak_forecast_points,
    poll_once,
)

CHICAGO = ZoneInfo("America/Chicago")


# =========================================================================
# Constants — pinned ComEd zone codes per feed
# =========================================================================


def test_comed_pnode_id_pinned():
    assert COMED_PNODE_ID == 33092371


def test_comed_zone_codes_differ_by_feed():
    """The PJM API does NOT have a single canonical ComEd zone code.
    hrl_load_metered uses 'CE'; annual_zonal_nspl uses 'COMED'. These
    constants pin the correct value per feed; if either changes the
    poller writes zero rows."""
    assert COMED_METERED_ZONE == "CE"
    assert COMED_NSPL_ZONE == "COMED"
    assert COMED_FORECAST_AREA == "COMED"


# =========================================================================
# Schedule
# =========================================================================


def test_schedule_fires_on_listed_hour():
    s = Schedule(hours=(6, 13))
    assert s.should_fire(datetime(2026, 7, 15, 6, tzinfo=CHICAGO))
    assert s.should_fire(datetime(2026, 7, 15, 13, tzinfo=CHICAGO))


def test_schedule_skips_unlisted_hour():
    s = Schedule(hours=(6, 13))
    assert not s.should_fire(datetime(2026, 7, 15, 7, tzinfo=CHICAGO))


def test_schedule_weekday_restriction():
    """Sunday-only fires only on Sunday (weekday=6)."""
    s = Schedule(hours=(2,), weekdays=(6,))
    assert s.should_fire(datetime(2026, 7, 12, 2, tzinfo=CHICAGO))   # Sunday
    assert not s.should_fire(datetime(2026, 7, 13, 2, tzinfo=CHICAGO))  # Monday


def test_schedule_month_restriction():
    """Cooling season fires Jun-Sep only."""
    s = Schedule(hours=(13,), months=(6, 7, 8, 9))
    assert s.should_fire(datetime(2026, 7, 15, 13, tzinfo=CHICAGO))  # July OK
    assert not s.should_fire(datetime(2026, 5, 15, 13, tzinfo=CHICAGO))  # May skipped
    assert not s.should_fire(datetime(2026, 10, 15, 13, tzinfo=CHICAGO))  # October skipped


def test_schedule_day_of_month_restriction():
    """Annual NSPL fires only on Dec 1."""
    s = Schedule(hours=(3,), months=(12,), days=(1,))
    assert s.should_fire(datetime(2026, 12, 1, 3, tzinfo=CHICAGO))
    assert not s.should_fire(datetime(2026, 12, 2, 3, tzinfo=CHICAGO))   # Dec 2 skipped
    assert not s.should_fire(datetime(2026, 11, 1, 3, tzinfo=CHICAGO))   # Nov skipped
    assert not s.should_fire(datetime(2026, 12, 1, 4, tzinfo=CHICAGO))   # Wrong hour


# =========================================================================
# FEED_SCHEDULE pinning — the binding scheduling contract
# =========================================================================


def test_da_lmp_fires_at_17_only():
    assert FEED_SCHEDULE["da_hrl_lmps"] == Schedule(hours=(17,))


def test_load_forecast_fires_twice_daily():
    assert FEED_SCHEDULE["load_frcstd_7_day"] == Schedule(hours=(6, 13))


def test_metered_load_fires_sunday_02_only():
    s = FEED_SCHEDULE["hrl_load_metered"]
    assert s.hours == (2,)
    assert s.weekdays == (6,)


def test_peak_forecast_cooling_season_only():
    s = FEED_SCHEDULE["ops_sum_frcst_peak_rto"]
    assert s.hours == (6, 13)
    assert s.months == (6, 7, 8, 9)


def test_nspl_dec_1_only():
    s = FEED_SCHEDULE["annual_zonal_nspl"]
    assert s.hours == (3,)
    assert s.months == (12,)
    assert s.days == (1,)


# =========================================================================
# _parse_ept (timestamp conversion)
# =========================================================================


def test_parse_ept_attaches_local_tz_then_converts():
    dt = _parse_ept("2026-07-15T13:00:00", CHICAGO)
    assert dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).hour == 18


def test_parse_ept_handles_dst_boundary():
    dt = _parse_ept("2026-11-01T02:30:00", CHICAGO)
    assert dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") == "2026-11-01 08:30"


# =========================================================================
# build_da_lmp_points (phase 1 feed)
# =========================================================================


def _da_item(hour: int, lmp: float = 30.0) -> dict:
    return {
        "datetime_beginning_ept": f"2026-07-15T{hour:02d}:00:00",
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
    assert len(build_da_lmp_points(items, CHICAGO)) == 24


def test_da_lmp_zone_tag_falls_back_to_pnode_name():
    [pt] = build_da_lmp_points([_da_item(0)], CHICAGO)
    assert "zone=COMED" in pt.to_line_protocol()


def test_da_lmp_handles_missing_optional_fields():
    item = _da_item(10)
    item["congestion_price_da"] = None
    item["marginal_loss_price_da"] = None
    [pt] = build_da_lmp_points([item], CHICAGO)
    line = pt.to_line_protocol()
    assert "congestion_price_da=0" in line
    assert "marginal_loss_price_da=0" in line


# =========================================================================
# build_load_forecast_points (phase 1 feed)
# =========================================================================


def _forecast_item(target_hour: int, eval_hour: int, mw: float = 10000.0) -> dict:
    return {
        "evaluated_at_datetime_ept": f"2026-07-15T{eval_hour:02d}:00:00",
        "forecast_area": COMED_FORECAST_AREA,
        "forecast_datetime_beginning_ept": f"2026-07-16T{target_hour:02d}:00:00",
        "forecast_load_mw": mw,
    }


def test_forecast_points_carry_evaluated_at_tag():
    [a, b] = build_load_forecast_points(
        [_forecast_item(15, 6, 11000), _forecast_item(15, 13, 11500)],
        CHICAGO,
    )
    eval_a = next(t for t in a.to_line_protocol().split(",") if "evaluated_at_iso" in t)
    eval_b = next(t for t in b.to_line_protocol().split(",") if "evaluated_at_iso" in t)
    assert eval_a != eval_b


def test_forecast_horizon_field():
    [pt] = build_load_forecast_points([_forecast_item(15, 6)], CHICAGO)
    assert "horizon_hours=33i" in pt.to_line_protocol()


# =========================================================================
# build_metered_load_points (phase 2 feed)
# =========================================================================


def _metered_item(hour: int, mw: float = 12000.0, verified: bool = True) -> dict:
    return {
        "datetime_beginning_ept": f"2026-07-15T{hour:02d}:00:00",
        "is_verified": verified,
        "load_area": "CE",
        "mw": mw,
        "zone": COMED_METERED_ZONE,
    }


def test_metered_load_points_count():
    pts = build_metered_load_points([_metered_item(h) for h in range(24)], CHICAGO)
    assert len(pts) == 24


def test_metered_load_carries_zone_and_verification_tags():
    [pt] = build_metered_load_points([_metered_item(13, 14500.5)], CHICAGO)
    line = pt.to_line_protocol()
    assert "pjm.metered_load" in line
    assert "zone=CE" in line  # NOTE: not COMED — see COMED_METERED_ZONE constant
    assert "is_verified=true" in line
    assert "mw=14500.5" in line


def test_metered_load_unverified_rows_marked():
    [pt] = build_metered_load_points([_metered_item(0, verified=False)], CHICAGO)
    assert "is_verified=false" in pt.to_line_protocol()


# =========================================================================
# build_peak_forecast_points (phase 2 feed)
# =========================================================================


def _peak_item(hour_generated: int = 8, peak_hour: int = 16) -> dict:
    return {
        "generated_at_ept": f"2026-07-15T{hour_generated:02d}:30:00",
        "projected_peak_datetime_ept": f"2026-07-15T{peak_hour:02d}:00:00",
        "area": "PJM RTO",
        "internal_scheduled_capacity": 148690.00,
        "scheduled_tie_flow_total": -2209.00,
        "capacity_adjustments": 0.00,
        "total_scheduled_capacity": 146481.00,
        "load_forecast": 124761.00,
        "operating_reserve": 21720.00,
        "unscheduled_steam_capacity": 16718.00,
    }


def test_peak_forecast_points_carry_load_forecast():
    [pt] = build_peak_forecast_points([_peak_item()], CHICAGO)
    line = pt.to_line_protocol()
    assert "pjm.peak_forecast_rto" in line
    assert "area=PJM" in line  # Influx tag escapes spaces; see line for actual form
    assert "load_forecast_mw=124761" in line


def test_peak_forecast_includes_projected_peak_string():
    """The projected-peak datetime is stored as a string field for
    downstream parsing rather than being collapsed into the timestamp."""
    [pt] = build_peak_forecast_points([_peak_item(peak_hour=17)], CHICAGO)
    assert 'projected_peak_datetime_ept="2026-07-15T17:00:00"' in pt.to_line_protocol()


def test_peak_forecast_uses_generated_at_as_timestamp():
    """A single day can have multiple revisions; using generated_at_ept as
    the timestamp keeps each revision distinct."""
    [a, b] = build_peak_forecast_points(
        [_peak_item(hour_generated=8), _peak_item(hour_generated=14)],
        CHICAGO,
    )
    # Different generated_at -> different Influx timestamps
    assert a.to_line_protocol().split()[-1] != b.to_line_protocol().split()[-1]


# =========================================================================
# build_nspl_points (phase 2 feed)
# =========================================================================


def _nspl_item(year: int = 2026, peak_dt: str = "2025-06-23T17:00:00", mw: float = 20713.7) -> dict:
    return {
        "year": year,
        "datetime_beginning_ept": peak_dt,
        "zone": COMED_NSPL_ZONE,
        "nspl_mw": mw,
    }


def test_nspl_points_carry_year_tag():
    [pt] = build_nspl_points([_nspl_item(year=2026)], CHICAGO)
    line = pt.to_line_protocol()
    assert "pjm.nspl_zonal" in line
    assert "year=2026" in line
    assert "zone=COMED" in line  # NOTE: this feed uses "COMED", not "CE"
    assert "nspl_mw=20713.7" in line


def test_nspl_timestamp_is_underlying_peak_hour():
    """The NSPL effective for billing year N is determined by the peak
    hour of summer N-1. We timestamp the Influx point at that peak hour
    so it lines up with pjm.coincident_peak rank-1 for the same summer."""
    [pt] = build_nspl_points([_nspl_item(peak_dt="2025-06-23T17:00:00")], CHICAGO)
    # 17:00 CDT = 22:00 UTC
    line = pt.to_line_protocol()
    # Final field of line protocol is the unix-ns timestamp
    ts_ns = int(line.split()[-1])
    # 2025-06-23T22:00:00 UTC
    expected = int(datetime(2025, 6, 23, 22, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)
    assert ts_ns == expected


# =========================================================================
# Dispatch table
# =========================================================================


def test_every_scheduled_feed_has_a_dispatcher():
    """Schedule-without-dispatcher means a feed silently doesn't run.
    Dispatcher-without-schedule means dead code. Both directions verified."""
    assert set(FEED_SCHEDULE.keys()) == set(FEED_DISPATCHERS.keys())


# =========================================================================
# poll_once dispatch
# =========================================================================


@pytest.mark.asyncio
async def test_poll_once_skips_all_outside_their_windows(monkeypatch):
    """At 03:00 on a Tuesday in May, no feed should fire (NSPL only fires
    on Dec 1, metered_load only Sunday 02:00, etc.)."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 5, 12, 3))  # Tue 2026-05-12 03:00 CT

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        return []
    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert fetched == []


@pytest.mark.asyncio
async def test_poll_once_fires_metered_load_on_sunday_02(monkeypatch):
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 12, 2))  # Sunday 02:00 CT

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        return [_metered_item(0, 11000.0)]
    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert "hrl_load_metered" in fetched
    # No other feeds should fire at Sunday 02:00 CT
    assert "da_hrl_lmps" not in fetched
    assert "load_frcstd_7_day" not in fetched


@pytest.mark.asyncio
async def test_poll_once_fires_peak_and_load_forecast_in_july_at_13(monkeypatch):
    """In cooling season at 13:00 local: load_frcstd_7_day AND
    ops_sum_frcst_peak_rto both fire (they share that hour)."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        if feed == "load_frcstd_7_day":
            return [_forecast_item(15, 13)]
        if feed == "ops_sum_frcst_peak_rto":
            return [_peak_item()]
        return []
    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert "load_frcstd_7_day" in fetched
    assert "ops_sum_frcst_peak_rto" in fetched


@pytest.mark.asyncio
async def test_poll_once_skips_peak_forecast_in_off_season(monkeypatch):
    """13:00 in May: load_frcstd_7_day fires (year-round) but
    ops_sum_frcst_peak_rto does NOT (cooling season only)."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 5, 12, 13))

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        return [_forecast_item(15, 13)]
    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert "load_frcstd_7_day" in fetched
    assert "ops_sum_frcst_peak_rto" not in fetched


@pytest.mark.asyncio
async def test_poll_once_continues_when_one_feed_fails(monkeypatch):
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))

    client = MagicMock()
    calls: list[str] = []

    async def maybe_fail(feed, params):
        calls.append(feed)
        if feed == "load_frcstd_7_day":
            raise RuntimeError("PJM HTTP 500")
        return [_peak_item()]  # peak forecast succeeds
    client.fetch = maybe_fail
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    # Both feeds were attempted; the failure of one didn't abort the cycle
    assert "load_frcstd_7_day" in calls
    assert "ops_sum_frcst_peak_rto" in calls
    # Only the successful feed wrote
    write_api.write.assert_called_once()


# =========================================================================
# helpers
# =========================================================================


def _stub_config_at(monkeypatch, when: datetime) -> Config:
    """Build a Config and stub `datetime.now(tz)` to return `when` in CT."""
    cfg = Config(
        api_key="fake-key",
        poll_interval_s=3600,
        influx_url="http://test",
        influx_token="t",
        influx_org="o",
        influx_bucket="b",
        tz=CHICAGO,
    )
    fake_now = when.replace(tzinfo=CHICAGO)

    class _StubDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is not None else fake_now.replace(tzinfo=None)

    monkeypatch.setattr("app.datetime", _StubDatetime)
    return cfg
