"""Tests for the PJM Data Miner 2 poller's pure-logic transforms.

Covers EPT->UTC conversion, point-construction shapes for all 5 feeds,
schedule-table correctness (including weekday/month/day-of-month
restrictions), and the multi-feed dispatch logic. HTTP and Influx
are mocked.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app import (
    COMED_FORECAST_AREA,
    COMED_INST_AREA,
    COMED_METERED_ZONE,
    COMED_NSPL_ZONE,
    COMED_PNODE_ID,
    FEED_DISPATCHERS,
    FEED_SCHEDULE,
    RTO_INST_AREA,
    RTO_METERED_ZONE,
    Config,
    Schedule,
    _check_rto_metered_load_rows_per_hour,
    _parse_ept,
    build_da_lmp_points,
    build_inst_load_points,
    build_load_forecast_points,
    build_metered_load_points,
    build_nspl_points,
    build_peak_forecast_points,
    build_rt_lmp_points,
    fetch_da_lmp_for_tomorrow,
    fetch_inst_load_recent,
    fetch_inst_load_recent_rto,
    fetch_metered_load_recent,
    fetch_metered_load_recent_rto,
    fetch_peak_forecast_rto,
    RT_LMP_RECENT_LOOKBACK_DAYS,
    fetch_rt_lmp_for_date,
    fetch_rt_lmp_recent,
    poll_once,
    seconds_to_next_aligned_tick,
)

CHICAGO = ZoneInfo("America/Chicago")


# =========================================================================
# Constants — pinned ComEd zone codes per feed
# =========================================================================


def test_comed_pnode_id_pinned():
    assert COMED_PNODE_ID == 33092371


def test_comed_zone_codes_differ_by_feed():
    """The PJM API does NOT have a single canonical ComEd zone code per
    the official DM2 OpenAPI spec; the convention varies by feed.

    - hrl_load_metered: zone="CE" (PJM transmission-zone code list)
    - inst_load:        area="COMED" (PJM area-code list; same data,
                                      different filter param + value)
    - annual_zonal_nspl: zone="COMED" (full name, on a third list)
    - load_frcstd_7_day: forecast_area="COMED"

    These constants pin the correct value per feed; if any drifts from
    the spec the poller writes zero rows for that feed."""
    assert COMED_METERED_ZONE == "CE"
    assert COMED_INST_AREA == "COMED"
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


def test_da_lmp_fetcher_queries_tomorrow_not_today():
    """The 17:00 CT da_hrl_lmps run is for *tomorrow's* day-ahead prices,
    which PJM posts after the day-ahead market clears at ~16:00 ET. A
    2026-07-10T17:00 fetch must request datetime_beginning_ept=2026-07-11.
    Querying today produces already-past prices that are useless for
    forecast-bias modeling and scheduler planning."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["feed"] = feed
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 10, 17, 0, tzinfo=CHICAGO)

    import asyncio
    asyncio.run(fetch_da_lmp_for_tomorrow(FakeClient(), cfg, now_local))

    assert captured["feed"] == "da_hrl_lmps"
    assert captured["params"]["datetime_beginning_ept"] == "2026-07-11T00:00:00.0"
    assert captured["params"]["pnode_id"] == COMED_PNODE_ID
    assert captured["params"]["row_is_current"] == "true"


def test_da_lmp_dispatcher_points_at_tomorrow_fetcher():
    """FEED_DISPATCHERS['da_hrl_lmps'] must reference the tomorrow fetcher,
    not the historical 'for_today' name. Belt-and-braces pin that the
    rename in app.py also propagated to the dispatch table."""
    assert FEED_DISPATCHERS["da_hrl_lmps"] is fetch_da_lmp_for_tomorrow


# =========================================================================
# rt_hrl_lmps (Phase 2 SCED rebaseline; spec §8) — settled hourly LMP
# =========================================================================
#
# Polls PJM's rt_hrl_lmps endpoint daily after the 11am-12pm ET publish
# window for the COMED zone (pnode_id 33092371). Bill-canonical supply
# price for ComEd Rate BESH — what the HVAC$ outcome rolls up against.


def test_rt_lmp_fires_at_noon_ct():
    """Schedule fires at 12:00 CT = 13:00 ET, ~1h after PJM's typical
    11am-12pm ET publish window for settled data. The 1h margin
    absorbs PJM's normal posting jitter without delaying the cycle
    too long."""
    assert FEED_SCHEDULE["rt_hrl_lmps"] == Schedule(hours=(12,))


def test_rt_lmp_dispatcher_points_at_recent_fetcher():
    """FEED_DISPATCHERS['rt_hrl_lmps'] orchestrates a multi-day
    'recent' fetch on each scheduled run; settled data is T+1 with
    documented T+2 worst-case latency (spec §8)."""
    assert FEED_DISPATCHERS["rt_hrl_lmps"] is fetch_rt_lmp_recent


def test_rt_lmp_recent_lookback_days_pinned_to_3():
    """3 days = T+2 worst-case latency + 1 day margin. Locks the
    constant against drift; widening to 5+ days starts hitting
    rate-limit pressure when other 12:00 CT feeds also fire."""
    assert RT_LMP_RECENT_LOOKBACK_DAYS == 3


def test_rt_lmp_recent_uses_3_day_range_window_in_ept():
    """The 12:00 CT rt_hrl_lmps run pulls the last 3 days as a single
    range query: yesterday (T-1) back through T-3 in EPT. A
    2026-07-11T12:00 CT fetch must request
    datetime_beginning_ept=2026-07-08T00:00:00.0to2026-07-10T23:59:59.0
    so PJM's documented T+2 worst-case latency self-heals on the next
    cycle without operator backfill."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["feed"] = feed
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 11, 12, 0, tzinfo=CHICAGO)

    asyncio.run(fetch_rt_lmp_recent(FakeClient(), cfg, now_local))

    assert captured["feed"] == "rt_hrl_lmps"
    assert (
        captured["params"]["datetime_beginning_ept"]
        == "2026-07-08T00:00:00.0to2026-07-10T23:59:59.0"
    )
    assert captured["params"]["pnode_id"] == COMED_PNODE_ID


def test_rt_lmp_recent_filters_to_current_revisions_only():
    """row_is_current=true filters out PJM's superseded revisions.
    RT LMP is bill-canonical; non-deterministic upserts on revision
    rows would compromise the OSF-pre-registered HVAC$ outcome."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 11, 12, 0, tzinfo=CHICAGO)
    asyncio.run(fetch_rt_lmp_recent(FakeClient(), cfg, now_local))
    assert captured["params"]["row_is_current"] == "true"


def test_rt_lmp_for_date_constructs_explicit_target():
    """The for_date helper used by the backfill script accepts any
    EPT date and queries that date specifically. Independent of
    'recent' wall-clock semantics."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["params"] = params
            return []

    cfg = MagicMock()
    target_date = datetime(2026, 1, 5, 0, 0)  # naive; treated as EPT date
    asyncio.run(fetch_rt_lmp_for_date(FakeClient(), cfg, target_date))

    assert captured["params"]["datetime_beginning_ept"] == "2026-01-05T00:00:00.0"
    assert captured["params"]["pnode_id"] == COMED_PNODE_ID


def test_rt_lmp_for_date_includes_row_is_current_filter():
    """Backfill must also filter to current revisions only — same
    determinism rationale as the live fetcher."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["params"] = params
            return []

    cfg = MagicMock()
    asyncio.run(fetch_rt_lmp_for_date(FakeClient(), cfg, datetime(2026, 1, 5, 0, 0)))
    assert captured["params"]["row_is_current"] == "true"


# ---- build_rt_lmp_points ------------------------------------------------


def _rt_item(hour: int, lmp: float = 30.0) -> dict:
    return {
        "datetime_beginning_ept": f"2026-07-15T{hour:02d}:00:00",
        "pnode_id": COMED_PNODE_ID,
        "pnode_name": "COMED",
        "zone": None,
        "type": "ZONE",
        "total_lmp_rt": lmp,
        "system_energy_price_rt": lmp - 1.0,
        "congestion_price_rt": 0.5,
        "marginal_loss_price_rt": 0.5,
    }


def test_rt_lmp_points_count_matches_input():
    items = [_rt_item(h, 25 + h) for h in range(24)]
    assert len(build_rt_lmp_points(items)) == 24


def test_rt_lmp_writes_to_pjm_lmp_rt_hourly_measurement():
    [pt] = build_rt_lmp_points([_rt_item(10)])
    line = pt.to_line_protocol()
    assert line.startswith("pjm.lmp_rt_hourly,")


def test_rt_lmp_carries_pnode_id_and_zone_tags():
    [pt] = build_rt_lmp_points([_rt_item(10)])
    line = pt.to_line_protocol()
    assert f"pnode_id={COMED_PNODE_ID}" in line
    # zone falls back to pnode_name when item['zone'] is None — same
    # pattern as da_hrl_lmps
    assert "zone=COMED" in line


def test_rt_lmp_carries_all_four_price_fields():
    [pt] = build_rt_lmp_points([_rt_item(10, lmp=42.5)])
    line = pt.to_line_protocol()
    assert "total_lmp_rt=42.5" in line
    assert "system_energy_price_rt=41.5" in line
    assert "congestion_price_rt=0.5" in line
    assert "marginal_loss_price_rt=0.5" in line


def test_rt_lmp_handles_missing_optional_fields():
    item = _rt_item(10)
    item["congestion_price_rt"] = None
    item["marginal_loss_price_rt"] = None
    [pt] = build_rt_lmp_points([item])
    line = pt.to_line_protocol()
    assert "congestion_price_rt=0" in line
    assert "marginal_loss_price_rt=0" in line


def test_rt_lmp_timestamp_is_ept_converted_to_utc():
    """Summer EPT = EDT = UTC-4. 13:00 EDT = 17:00 UTC.
    Expected UTC value hand-pinned (not derived from _parse_ept) so a
    TZ bug in the parser would surface as a test failure rather than
    self-confirming."""
    [pt] = build_rt_lmp_points([_rt_item(13)])
    line = pt.to_line_protocol()
    # 2026-07-15 13:00 EDT == 2026-07-15 17:00 UTC == 1784134800 epoch
    expected_ns = 1784134800 * 1_000_000_000
    assert line.endswith(f" {expected_ns}")


# =========================================================================
# rt_hrl_lmps backfill script (Phase 2 Task 2.3)
# =========================================================================
#
# One-shot fill of pjm.lmp_rt_hourly for the 2026-01-01..yesterday window.
# Run inside the pjm-dm2-poller container so it shares the live poller's
# env (PJM_DM2_API_KEY, INFLUXDB_*).


def test_iter_target_dates_inclusive_range():
    from datetime import date
    from backfill_rt_hrl_lmps import iter_target_dates
    out = list(iter_target_dates(date(2026, 1, 1), date(2026, 1, 3)))
    assert out == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]


def test_iter_target_dates_single_day():
    from datetime import date
    from backfill_rt_hrl_lmps import iter_target_dates
    out = list(iter_target_dates(date(2026, 5, 15), date(2026, 5, 15)))
    assert out == [date(2026, 5, 15)]


def test_iter_target_dates_empty_when_start_after_end():
    from datetime import date
    from backfill_rt_hrl_lmps import iter_target_dates
    out = list(iter_target_dates(date(2026, 5, 15), date(2026, 5, 14)))
    assert out == []


def test_default_start_date_is_spec_locked():
    """Spec §8 + plan Task 2.3: backfill from 2026-01-01 (minimum;
    24-month retention is aspirational)."""
    from datetime import date
    from backfill_rt_hrl_lmps import DEFAULT_START_DATE
    assert DEFAULT_START_DATE == date(2026, 1, 1)


def test_default_end_date_is_yesterday():
    """Settled data is T+1; the live poller covers yesterday onward.
    The backfill default ends at yesterday so the two together cover
    everything without a gap or double-write."""
    from datetime import date, timedelta
    from backfill_rt_hrl_lmps import default_end_date
    today = date.today()
    assert default_end_date(today=today) == today - timedelta(days=1)


def test_backfill_range_calls_fetcher_once_per_date(monkeypatch):
    """Per-date fetcher call. Each date's points get written before the
    next iteration to keep memory bounded over a multi-month range."""
    from datetime import date
    import backfill_rt_hrl_lmps as bf

    fetched_dates: list[date] = []
    write_calls: list[int] = []

    async def fake_fetch(client, cfg, target_date_ept):
        fetched_dates.append(target_date_ept.date())
        return [MagicMock(), MagicMock()]  # 2 fake points per date

    write_api = MagicMock()
    write_api.write.side_effect = lambda **kw: write_calls.append(len(kw["record"]))

    monkeypatch.setattr(bf, "fetch_rt_lmp_for_date", fake_fetch)

    sleeps: list[float] = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(bf.asyncio, "sleep", fake_sleep)

    cfg = MagicMock()
    cfg.influx_bucket = "energy"
    client = MagicMock()

    result = asyncio.run(bf.backfill_range(
        client, write_api, cfg,
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 3),
        sleep_s=5.0,
    ))

    assert fetched_dates == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    assert result.dates_attempted == 3
    assert result.total_points == 6
    assert result.empty_dates == []
    assert result.failed_dates == []
    assert write_calls == [2, 2, 2]
    # Sleep between dates (n-1 sleeps for n dates)
    assert sleeps == [5.0, 5.0]


def test_backfill_range_skips_write_on_empty_fetch(monkeypatch):
    """If PJM returns zero rows for a date (e.g., not yet posted),
    skip the influx write rather than calling write with an empty
    list. Track the date in empty_dates so the end-of-run summary
    can flag it for follow-up."""
    from datetime import date
    import backfill_rt_hrl_lmps as bf

    async def fake_fetch(client, cfg, target_date_ept):
        # Return [] on the middle day
        if target_date_ept.day == 2:
            return []
        return [MagicMock()]

    write_api = MagicMock()
    monkeypatch.setattr(bf, "fetch_rt_lmp_for_date", fake_fetch)
    async def no_sleep(s): pass
    monkeypatch.setattr(bf.asyncio, "sleep", no_sleep)

    result = asyncio.run(bf.backfill_range(
        MagicMock(), write_api, MagicMock(influx_bucket="energy"),
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 3),
        sleep_s=0.0,
    ))

    assert result.dates_attempted == 3
    assert result.total_points == 2  # day 1 + day 3
    assert result.empty_dates == [date(2026, 1, 2)]
    assert result.failed_dates == []
    assert write_api.write.call_count == 2  # day 2 skipped


def test_backfill_range_continues_after_transient_fetch_error(monkeypatch):
    """A PJM HTTP error on one date must not abort the whole backfill —
    log + record the date in failed_dates + continue. Otherwise a
    single 5xx mid-run leaves a partial backfill the operator has to
    manually compute the resume offset for. Spec §8 retention is
    bill-canonical input; resilience here matters."""
    from datetime import date
    import backfill_rt_hrl_lmps as bf

    async def fake_fetch(client, cfg, target_date_ept):
        if target_date_ept.day == 2:
            raise RuntimeError("PJM HTTP 503: temporarily unavailable")
        return [MagicMock()]

    write_api = MagicMock()
    monkeypatch.setattr(bf, "fetch_rt_lmp_for_date", fake_fetch)
    async def no_sleep(s): pass
    monkeypatch.setattr(bf.asyncio, "sleep", no_sleep)

    result = asyncio.run(bf.backfill_range(
        MagicMock(), write_api, MagicMock(influx_bucket="energy"),
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 3),
        sleep_s=0.0,
    ))

    assert result.dates_attempted == 3
    assert result.total_points == 2  # day 1 + day 3
    assert result.failed_dates == [date(2026, 1, 2)]
    assert result.empty_dates == []
    assert write_api.write.call_count == 2  # day 2 errored
    assert result.needs_followup() is True


def test_backfill_result_needs_followup_clean():
    from backfill_rt_hrl_lmps import BackfillResult
    r = BackfillResult()
    r.dates_attempted = 130
    r.total_points = 3120
    assert r.needs_followup() is False


def test_load_forecast_fires_twice_daily():
    assert FEED_SCHEDULE["load_frcstd_7_day"] == Schedule(hours=(6, 13))


def test_metered_load_fires_every_hour_at_top_of_hour():
    """Hourly cadence with 5-day rolling lookback. PJM publishes
    hrl_load_metered daily with multi-day publish lag (per official
    DM2 OpenAPI spec: "lag in updated data availability... data
    adjustments can occur up to 90 days after the actual date"). Hourly
    polling catches newly-posted observations within ~1h of when PJM
    ships them. Default minutes=(0,) means each hour fires once at
    :00 even though the wake loop ticks every 5 min."""
    s = FEED_SCHEDULE["hrl_load_metered"]
    assert s.hours == tuple(range(0, 24))
    assert s.minutes == (0,)
    assert s.weekdays is None
    assert s.months is None
    assert s.days is None


def test_seconds_to_next_aligned_tick_5min_grid():
    """Five-minute alignment: from any time, returns the seconds until
    the next :00, :05, :10, ..., :55 wall-clock minute. This is the
    Schedule.minutes contract for inst_load — without alignment, a
    container started at :49:29 would tick at :49, :54, :59, :04, :09,
    none of which match the schedule."""
    # Started at 12:49:29 -> next aligned tick is 12:50:00, 31 seconds away.
    started_at = datetime(2026, 5, 10, 12, 49, 29, tzinfo=CHICAGO)
    assert round(seconds_to_next_aligned_tick(started_at, 300.0)) == 31

    # Started at 12:50:00 (already aligned) -> returns interval (next
    # tick at 12:55:00), so the loop guarantees forward progress.
    on_boundary = datetime(2026, 5, 10, 12, 50, 0, tzinfo=CHICAGO)
    assert round(seconds_to_next_aligned_tick(on_boundary, 300.0)) == 300

    # Started 0.5s before a boundary -> next boundary is the imminent one.
    just_before = datetime(2026, 5, 10, 12, 49, 59, 500_000, tzinfo=CHICAGO)
    sleep_s = seconds_to_next_aligned_tick(just_before, 300.0)
    assert 0 < sleep_s <= 0.5


def test_seconds_to_next_aligned_tick_hourly_grid():
    """3600s interval still aligns on the top of the hour."""
    # 12:49:29 -> next :00:00 is 13:00:00, 630 seconds away.
    t = datetime(2026, 5, 10, 12, 49, 30, tzinfo=CHICAGO)
    assert round(seconds_to_next_aligned_tick(t, 3600.0)) == 630


def test_inst_load_fires_every_5_min():
    """inst_load (PJM's approximate, real-time area load) is polled every
    5 minutes per the locked architecture for §3 5CP detection.
    minutes=(0, 5, 10, ..., 55) gates every 5 min within each hour."""
    s = FEED_SCHEDULE["inst_load"]
    assert s.hours == tuple(range(0, 24))
    assert s.minutes == tuple(range(0, 60, 5))
    assert s.weekdays is None
    assert s.months is None
    assert s.days is None


def test_fetch_metered_load_recent_uses_5_day_window():
    """Per the official PJM DM2 OpenAPI spec for hrl_load_metered: the
    feed is published daily with multi-day publish lag. The 5-day
    lookback absorbs PJM's typical 2-3 day publish delay plus weekend
    gaps. Earlier 3h lookback (May 2026) was wrong against PJM's actual
    publishing cadence and produced 0-row writes post-deploy."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["feed"] = feed
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 15, 14, 17, 30, tzinfo=CHICAGO)

    import asyncio
    asyncio.run(fetch_metered_load_recent(FakeClient(), cfg, now_local))

    assert captured["feed"] == "hrl_load_metered"
    assert captured["params"]["zone"] == COMED_METERED_ZONE
    # 5 days back from 2026-07-15 14:17:30 CDT (= 15:17:30 EDT).
    # Filter is formatted in Eastern Prevailing Time per PJM's _ept
    # convention.
    assert (
        captured["params"]["datetime_beginning_ept"]
        == "2026-07-10T15:17:30.0to2026-07-15T15:17:30.0"
    )


def test_fetch_inst_load_recent_uses_30min_window_and_area_filter():
    """inst_load filters by ``area`` (not ``zone`` like hrl_load_metered)
    per the DM2 OpenAPI spec — and the ComEd code on inst_load's allowed
    list is "COMED" (not "CE" like hrl_load_metered's). 30-minute
    lookback catches stragglers if the 5-min poll missed a tick."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["feed"] = feed
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 15, 14, 17, 30, tzinfo=CHICAGO)

    import asyncio
    asyncio.run(fetch_inst_load_recent(FakeClient(), cfg, now_local))

    assert captured["feed"] == "inst_load"
    assert captured["params"]["area"] == COMED_INST_AREA
    assert "zone" not in captured["params"]
    # 14:17:30 CDT (UTC-5) == 15:17:30 EDT (UTC-4). The 30-min window
    # is built in Eastern Prevailing Time per PJM's _ept filter
    # convention. Pre-fix the poller used CDT for the filter string,
    # which asked PJM for an hour-stale window and silently degraded
    # the §3 5CP detector's live-load input. See PR #62.
    assert (
        captured["params"]["datetime_beginning_ept"]
        == "2026-07-15T14:47:30.0to2026-07-15T15:17:30.0"
    )


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


def test_parse_ept_attaches_eastern_prevailing_time():
    """PJM ``_ept`` fields are Eastern Prevailing Time, NOT the
    operator's local tz. Summer: 13:00 EDT (UTC-4) -> 17:00 UTC.
    Pre-fix the parser attached ``cfg.tz=America/Chicago``, so 13:00
    "EPT" was being read as 13:00 CDT -> 18:00 UTC (one hour late).
    This test pins the post-fix Eastern semantics."""
    dt = _parse_ept("2026-07-15T13:00:00")
    assert dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).hour == 17


def test_parse_ept_handles_dst_boundary():
    """DST ends 2026-11-01 02:00 EDT -> 01:00 EST. By 02:30 the
    fallback has already occurred (the ambiguous hour is 01:00-01:59),
    so 02:30 is unambiguously EST (UTC-5) -> 07:30 UTC. Pinning
    Eastern semantics; a future regression to ``cfg.tz=Chicago``
    would produce 08:30 UTC and fail loudly."""
    dt = _parse_ept("2026-11-01T02:30:00")
    assert dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") == "2026-11-01 07:30"


def test_summer_ept_produces_edt_offset_in_influx_point():
    """End-to-end pin: PJM publishes a June 23, 2025 17:00 EPT (= EDT in
    summer = UTC-4) peak; the Influx point's timestamp must be
    2025-06-23T21:00:00Z. Pre-fix this came out as 2025-06-23T22:00:00Z
    because the poller treated the EPT field as Chicago time (CDT,
    UTC-5). This test would have caught the 1-hour offset bug
    immediately if it had existed before the fix."""
    item = {
        "datetime_beginning_ept": "2025-06-23T17:00:00",
        "is_verified": True,
        "load_area": "CE",
        "mw": 20528.0,
        "zone": "CE",
    }
    [pt] = build_metered_load_points([item])
    line = pt.to_line_protocol()
    ts_ns = int(line.split()[-1])
    expected_utc = int(datetime(2025, 6, 23, 21, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)
    assert ts_ns == expected_utc


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
    assert len(build_da_lmp_points(items)) == 24


def test_da_lmp_zone_tag_falls_back_to_pnode_name():
    [pt] = build_da_lmp_points([_da_item(0)])
    assert "zone=COMED" in pt.to_line_protocol()


def test_da_lmp_handles_missing_optional_fields():
    item = _da_item(10)
    item["congestion_price_da"] = None
    item["marginal_loss_price_da"] = None
    [pt] = build_da_lmp_points([item])
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
    )
    eval_a = next(t for t in a.to_line_protocol().split(",") if "evaluated_at_iso" in t)
    eval_b = next(t for t in b.to_line_protocol().split(",") if "evaluated_at_iso" in t)
    assert eval_a != eval_b


def test_forecast_horizon_field():
    [pt] = build_load_forecast_points([_forecast_item(15, 6)])
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
    pts = build_metered_load_points([_metered_item(h) for h in range(24)])
    assert len(pts) == 24


def test_metered_load_carries_zone_and_verification_tags():
    [pt] = build_metered_load_points([_metered_item(13, 14500.5)])
    line = pt.to_line_protocol()
    assert "pjm.metered_load" in line
    assert "zone=CE" in line  # NOTE: not COMED — see COMED_METERED_ZONE constant
    assert "is_verified=true" in line
    assert "mw=14500.5" in line


def test_metered_load_unverified_rows_marked():
    [pt] = build_metered_load_points([_metered_item(0, verified=False)])
    assert "is_verified=false" in pt.to_line_protocol()


# =========================================================================
# build_inst_load_points (real-time current load for §3 5CP detector)
# =========================================================================


def _inst_load_item(hour: int, mw: float = 14000.0, minute: int = 0) -> dict:
    return {
        "datetime_beginning_ept": f"2026-07-15T{hour:02d}:{minute:02d}:00",
        "area": COMED_INST_AREA,
        "instantaneous_load": mw,
    }


def test_inst_load_points_count():
    """Each posted observation becomes one point. PJM publishes inst_load
    sub-hourly so a 30-min window may contain 6+ observations."""
    pts = build_inst_load_points([_inst_load_item(13, 14000.0 + h, minute=h*10)
                                   for h in range(3)])
    assert len(pts) == 3


def test_inst_load_carries_area_tag_and_mw_field():
    """``area`` is the tag (matches the PJM filter param); ``mw`` is the
    field name (matches pjm.metered_load's `mw` field so the §3 detector
    can swap feeds without renaming downstream queries)."""
    [pt] = build_inst_load_points([_inst_load_item(13, 14250.5)])
    line = pt.to_line_protocol()
    assert line.startswith("pjm.inst_load")
    assert "area=COMED" in line
    assert "mw=14250.5" in line


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
    [pt] = build_peak_forecast_points([_peak_item()])
    line = pt.to_line_protocol()
    assert "pjm.peak_forecast_rto" in line
    assert "area=PJM" in line  # Influx tag escapes spaces; see line for actual form
    assert "load_forecast_mw=124761" in line


def test_peak_forecast_includes_projected_peak_string():
    """The projected-peak datetime is stored as a string field for
    downstream parsing rather than being collapsed into the timestamp."""
    [pt] = build_peak_forecast_points([_peak_item(peak_hour=17)])
    assert 'projected_peak_datetime_ept="2026-07-15T17:00:00"' in pt.to_line_protocol()


def test_peak_forecast_uses_generated_at_as_timestamp():
    """A single day can have multiple revisions; using generated_at_ept as
    the timestamp keeps each revision distinct."""
    [a, b] = build_peak_forecast_points(
        [_peak_item(hour_generated=8), _peak_item(hour_generated=14)],
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
    [pt] = build_nspl_points([_nspl_item(year=2026)])
    line = pt.to_line_protocol()
    assert "pjm.nspl_zonal" in line
    assert "year=2026" in line
    assert "zone=COMED" in line  # NOTE: this feed uses "COMED", not "CE"
    assert "nspl_mw=20713.7" in line


def test_nspl_timestamp_is_underlying_peak_hour():
    """The NSPL effective for billing year N is determined by the peak
    hour of summer N-1. We timestamp the Influx point at that peak hour
    so it lines up with pjm.coincident_peak rank-1 for the same summer.

    Time conversion: PJM ``_ept`` is Eastern Prevailing Time. June 23
    is EDT (UTC-4), so 17:00 EDT = 21:00 UTC. Pre-2026-05 the parser
    used the operator's ``cfg.tz=America/Chicago`` and produced 22:00
    UTC -- one hour late."""
    [pt] = build_nspl_points([_nspl_item(peak_dt="2025-06-23T17:00:00")])
    line = pt.to_line_protocol()
    # Final field of line protocol is the unix-ns timestamp
    ts_ns = int(line.split()[-1])
    # 17:00 EDT == 21:00 UTC
    expected = int(datetime(2025, 6, 23, 21, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)
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
async def test_poll_once_skips_restricted_feeds_outside_their_windows(monkeypatch):
    """At 03:00 on a Tuesday in May, every restricted-schedule feed stays
    quiet (NSPL is Dec 1 only, peak_forecast is cooling-season only, DA
    LMP is 17:00 only, load_frcstd_7_day is 06/13 only). hrl_load_metered
    fires hourly per §0b, so it's excluded from this restriction check
    and verified separately in test_poll_once_fires_metered_load_at_arbitrary_hour."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 5, 12, 3))  # Tue 2026-05-12 03:00 CT

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        return []
    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert "da_hrl_lmps" not in fetched
    assert "load_frcstd_7_day" not in fetched
    assert "ops_sum_frcst_peak_rto" not in fetched
    assert "annual_zonal_nspl" not in fetched


@pytest.mark.asyncio
async def test_poll_once_fires_metered_load_at_arbitrary_hour(monkeypatch):
    """Hourly metered_load cadence (per ARM_B_IMPLEMENTATION §0b) fires
    at 04:00 CT on a Tuesday in May the same way it fires at any other
    hour. Other feeds whose schedules don't cover this hour stay quiet."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 5, 12, 4))  # Tue 04:00 CT

    client = MagicMock()
    fetched: list[str] = []

    async def fake_fetch(feed, params):
        fetched.append(feed)
        return [_metered_item(0, 11000.0)]
    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert "hrl_load_metered" in fetched
    # 04:00 falls outside the other feeds' scheduled hours
    assert "da_hrl_lmps" not in fetched
    assert "load_frcstd_7_day" not in fetched
    assert "ops_sum_frcst_peak_rto" not in fetched


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
async def test_poll_once_continues_when_one_feed_fails(monkeypatch, tmp_path):
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))
    _stub_health_marker(monkeypatch, tmp_path)

    client = MagicMock()
    calls: list[tuple[str, dict]] = []

    async def maybe_fail(feed, params):
        calls.append((feed, params))
        if feed == "load_frcstd_7_day":
            raise RuntimeError("PJM HTTP 500")
        if feed == "hrl_load_metered":
            return [_metered_item(13, 14000.0)]
        if feed == "inst_load":
            return [_inst_load_item(13, 14250.0)]
        return [_peak_item()]  # peak forecast succeeds
    client.fetch = maybe_fail
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    # All due feeds were attempted; the failure of one didn't abort the cycle.
    # P1.1: hrl_load_metered and inst_load each call the API twice -- once
    # per scope (ComEd zone + RTO aggregate) -- distinguished by the
    # zone / area filter param.
    feed_names = [c[0] for c in calls]
    assert "load_frcstd_7_day" in feed_names
    assert "ops_sum_frcst_peak_rto" in feed_names
    metered_calls = [params for f, params in calls if f == "hrl_load_metered"]
    inst_calls = [params for f, params in calls if f == "inst_load"]
    assert {p["zone"] for p in metered_calls} == {"CE", "RTO"}  # P1.1
    assert {p["area"] for p in inst_calls} == {"COMED", "PJM RTO"}  # P1.1

    # 13:00 (minute 0) in summer fires six FEED_SCHEDULE entries; each
    # writes a pjm.feed_status row. The two zonal pairs (metered_load
    # and inst_load) each write 2 measurement rows (CE+RTO / COMED+PJM RTO).
    measurements = _measurements_written(write_api)
    assert measurements.count("pjm.peak_forecast_rto") == 1
    assert measurements.count("pjm.metered_load") == 2   # CE + RTO
    assert measurements.count("pjm.inst_load") == 2      # COMED + PJM RTO
    assert measurements.count("pjm.feed_status") == 6


# =========================================================================
# Health-marker gating + per-feed status (CodeX 2026-05-07 P2)
# =========================================================================


@pytest.mark.asyncio
async def test_health_marker_touched_when_due_feeds_return_empty(monkeypatch, tmp_path):
    """A cycle where every due feed returns no data still touches the
    marker. Loop liveness semantics: as long as the cycle ran to
    completion, the loop is healthy. (Pre-§0b this was an "idle cycle"
    test, but hourly metered_load means there's always at least one
    due feed; the same semantic is preserved by having that feed
    return empty.)"""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 5, 12, 3))  # Tue 03:00 CT
    marker = _stub_health_marker(monkeypatch, tmp_path)

    async def empty_fetch(feed, params):
        return []
    client = MagicMock()
    client.fetch = empty_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)

    assert marker.exists(), "marker must be touched on every clean loop pass"


@pytest.mark.asyncio
async def test_health_marker_touched_when_at_least_one_feed_succeeds(monkeypatch, tmp_path):
    """Mixed-outcome cycle: one due feed succeeds, one fails. Marker is
    touched because the loop completed normally — feed-level health is
    tracked via pjm.feed_status, not the container marker."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))  # cooling season 13:00
    marker = _stub_health_marker(monkeypatch, tmp_path)

    async def maybe_fail(feed, params):
        if feed == "load_frcstd_7_day":
            raise RuntimeError("PJM HTTP 500")
        return [_peak_item()]
    client = MagicMock()
    client.fetch = maybe_fail
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert marker.exists(), "marker must be touched when ANY due feed succeeds"


@pytest.mark.asyncio
async def test_health_marker_touched_even_when_all_due_feeds_fail(monkeypatch, tmp_path):
    """Loop-liveness semantics (CodeX pass 2 walked back the earlier
    feed-success-gating attempt): the marker is touched even when every
    due feed fails. The loop ran cleanly; restarting the container would
    not fix an API outage. Per-feed failures are observable through
    `pjm.feed_status` rows, which downstream alerting can deadman-check."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))  # 13:00 in cooling season
    marker = _stub_health_marker(monkeypatch, tmp_path)

    async def always_fail(feed, params):
        raise RuntimeError("PJM HTTP 401")
    client = MagicMock()
    client.fetch = always_fail
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert marker.exists(), \
        "marker must be touched on every clean loop pass; per-feed failure surface is pjm.feed_status"

    # Sanity-check that the failure DID get recorded in the proper place
    status_lines = _status_rows(write_api)
    assert len(status_lines) >= 1
    assert all("success=false" in line for line in status_lines)


@pytest.mark.asyncio
async def test_feed_status_row_written_on_success(monkeypatch, tmp_path):
    """Every successful feed attempt writes one pjm.feed_status row tagged
    success=true with points_written field. Independent of whether the
    feed itself wrote anything (an empty NSPL response is still success)."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))
    _stub_health_marker(monkeypatch, tmp_path)

    async def fake_fetch(feed, params):
        if feed == "load_frcstd_7_day":
            return [_forecast_item(15, 13)]
        if feed == "ops_sum_frcst_peak_rto":
            return [_peak_item()]
        return []
    client = MagicMock()
    client.fetch = fake_fetch
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)

    status_lines = _status_rows(write_api)
    # 13:00 (minute 0) in summer fires six feeds: load_frcstd_7_day,
    # hrl_load_metered (zone=CE) + hrl_load_metered_rto (zone=RTO),
    # inst_load (area=COMED) + inst_load_rto (area=PJM RTO),
    # ops_sum_frcst_peak_rto.
    assert len(status_lines) == 6
    for line in status_lines:
        assert "pjm.feed_status" in line
        assert "success=true" in line


@pytest.mark.asyncio
async def test_feed_status_row_written_on_failure(monkeypatch, tmp_path):
    """A failing feed writes a pjm.feed_status row tagged success=false
    with the exception type as a field. This is the surface that
    telegram-notifier and Grafana alerting query for per-feed health,
    independent of the container healthcheck."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))
    _stub_health_marker(monkeypatch, tmp_path)

    async def all_fail(feed, params):
        raise RuntimeError("PJM HTTP 401: unauthorized")
    client = MagicMock()
    client.fetch = all_fail
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)

    status_lines = _status_rows(write_api)
    # 13:00 (minute 0) in summer fires six feeds: load_frcstd_7_day,
    # hrl_load_metered (zone=CE) + hrl_load_metered_rto (zone=RTO),
    # inst_load (area=COMED) + inst_load_rto (area=PJM RTO),
    # ops_sum_frcst_peak_rto.
    assert len(status_lines) == 6
    for line in status_lines:
        assert "pjm.feed_status" in line
        assert "success=false" in line
        assert "RuntimeError" in line  # error_type field carries exception class


@pytest.mark.asyncio
async def test_feed_status_failure_to_write_does_not_break_cycle(monkeypatch, tmp_path):
    """If the pjm.feed_status write itself errors (transient Influx
    blip), the cycle must NOT raise and the marker logic must still
    apply. Status writes are observability, not control."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))
    marker = _stub_health_marker(monkeypatch, tmp_path)

    write_api = MagicMock()

    async def fake_fetch(feed, params):
        if feed == "load_frcstd_7_day":
            return [_forecast_item(15, 13)]
        if feed == "ops_sum_frcst_peak_rto":
            return [_peak_item()]
        return []

    # Make the *status* write fail (the second arg is a list of one Point;
    # data writes pass a list with multiple points). A simple proxy: any
    # write whose record list has exactly one point is treated as a
    # status write and fails.
    def selective_write(*, bucket, record):
        if len(record) == 1 and "pjm.feed_status" in record[0].to_line_protocol():
            raise RuntimeError("Influx transient error")
        return None
    write_api.write = MagicMock(side_effect=selective_write)

    client = MagicMock()
    client.fetch = fake_fetch

    # Must not raise
    await poll_once(client, write_api, cfg)
    # And the cycle still counts as healthy (data writes succeeded)
    assert marker.exists()


# =========================================================================
# Liveness heartbeat (consumed by telegram-notifier check_poller_silence)
# =========================================================================


@pytest.mark.asyncio
async def test_heartbeat_written_when_due_feeds_return_empty(monkeypatch, tmp_path):
    """A cycle where every due feed returns no data still writes a
    `pjm.poller_heartbeat` row. This is the signal telegram-notifier's
    check_poller_silence consumes — without it, sustained empty cycles
    (e.g., off-season hours when metered_load returns nothing) would
    look like the poller died."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 5, 12, 3))  # Tue 03:00 CT
    _stub_health_marker(monkeypatch, tmp_path)

    async def empty_fetch(feed, params):
        return []
    client = MagicMock()
    client.fetch = empty_fetch
    write_api = MagicMock()
    await poll_once(client, write_api, cfg)

    measurements = _measurements_written(write_api)
    assert "pjm.poller_heartbeat" in measurements


@pytest.mark.asyncio
async def test_heartbeat_written_when_all_feeds_fail(monkeypatch, tmp_path):
    """A cycle where every due feed fails still writes a heartbeat — the
    POLLER is alive even when PJM is returning errors. Otherwise a sustained
    PJM outage would silence the heartbeat and false-fire the silence
    deadman."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 7, 15, 13))  # cooling 13:00
    _stub_health_marker(monkeypatch, tmp_path)

    async def always_fail(feed, params):
        raise RuntimeError("PJM HTTP 500")
    client = MagicMock()
    client.fetch = always_fail
    write_api = MagicMock()

    await poll_once(client, write_api, cfg)
    assert "pjm.poller_heartbeat" in _measurements_written(write_api)


@pytest.mark.asyncio
async def test_heartbeat_write_failure_does_not_break_cycle(monkeypatch, tmp_path):
    """A failed heartbeat write (Influx blip) must not raise — the cycle
    must complete cleanly so the next cycle's heartbeat can land. Same
    swallow-and-log contract as `_write_feed_status`."""
    cfg = _stub_config_at(monkeypatch, datetime(2026, 5, 12, 3))  # Tue 03:00 CT (empty fetch)
    marker = _stub_health_marker(monkeypatch, tmp_path)

    write_api = MagicMock()

    def selective_write(*, bucket, record):
        for pt in record:
            if "pjm.poller_heartbeat" in pt.to_line_protocol():
                raise RuntimeError("Influx transient error")
        return None
    write_api.write = MagicMock(side_effect=selective_write)

    async def empty_fetch(feed, params):
        return []
    client = MagicMock()
    client.fetch = empty_fetch
    # Must not raise
    await poll_once(client, write_api, cfg)
    # And the marker still gets touched (heartbeat failure is observability,
    # not control — same posture as feed_status write failures)
    assert marker.exists()


# =========================================================================
# helpers
# =========================================================================


def _measurements_written(write_api: MagicMock) -> list[str]:
    """Extract the measurement name from each Point passed to write_api.write."""
    out: list[str] = []
    for call in write_api.write.call_args_list:
        record = call.kwargs.get("record") or (call.args[1] if len(call.args) > 1 else [])
        for pt in record:
            line = pt.to_line_protocol()
            out.append(line.split(",", 1)[0].split(" ", 1)[0])
    return out


def _status_rows(write_api: MagicMock) -> list[str]:
    """Line-protocol strings for every pjm.feed_status point written."""
    out: list[str] = []
    for call in write_api.write.call_args_list:
        record = call.kwargs.get("record") or (call.args[1] if len(call.args) > 1 else [])
        for pt in record:
            line = pt.to_line_protocol()
            if line.startswith("pjm.feed_status"):
                out.append(line)
    return out


def _stub_health_marker(monkeypatch, tmp_path):
    """Redirect HEALTH_MARKER to a temp path so tests can assert touch state
    without touching /tmp."""
    from pathlib import Path
    marker = Path(tmp_path) / "last_poll_ok"
    monkeypatch.setattr("app.HEALTH_MARKER", marker)
    return marker


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


# =========================================================================
# P1.1: RTO 5CP detection ingestion
# =========================================================================


def test_rto_constants_match_pjm_dm2_spec():
    """PJM DM2 OpenAPI spec values for RTO-wide aggregation. These string
    constants flow to the API as filter values; getting them wrong
    silently returns 0 rows instead of the RTO aggregate."""
    assert RTO_INST_AREA == "PJM RTO"
    assert RTO_METERED_ZONE == "RTO"


def test_rto_feed_schedule_entries_registered():
    """FEED_SCHEDULE must list both RTO variants so the per-feed
    dispatch loop fires them. Without the schedule entry, the fetcher
    is dead code regardless of being defined."""
    assert "hrl_load_metered_rto" in FEED_SCHEDULE
    assert "inst_load_rto" in FEED_SCHEDULE
    # Both should run on the same cadence as their ComEd counterparts
    # (hourly for metered, every 5 min for inst).
    assert FEED_SCHEDULE["hrl_load_metered_rto"].hours == FEED_SCHEDULE["hrl_load_metered"].hours
    assert FEED_SCHEDULE["inst_load_rto"].minutes == FEED_SCHEDULE["inst_load"].minutes


def test_rto_feed_dispatchers_registered():
    """FEED_DISPATCHERS must contain entries for both RTO schedule keys
    so poll_once can find a fetcher when the schedule fires."""
    assert FEED_DISPATCHERS["hrl_load_metered_rto"] is fetch_metered_load_recent_rto
    assert FEED_DISPATCHERS["inst_load_rto"] is fetch_inst_load_recent_rto


def test_fetch_inst_load_recent_rto_uses_pjm_rto_area():
    """The RTO inst_load fetcher hits the same endpoint as ComEd's but
    with area=PJM RTO. HTTP-client-side URL encoding turns the space
    into %20 -- verify the raw param string here, not the encoded form."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["feed"] = feed
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 15, 14, 17, 30, tzinfo=CHICAGO)

    asyncio.run(fetch_inst_load_recent_rto(FakeClient(), cfg, now_local))

    assert captured["feed"] == "inst_load"
    assert captured["params"]["area"] == "PJM RTO"
    assert captured["params"]["area"] != COMED_INST_AREA  # not the ComEd code


def test_fetch_metered_load_recent_rto_uses_rto_zone():
    """RTO metered uses zone=RTO. Same endpoint as ComEd's, different filter."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["feed"] = feed
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=CHICAGO)

    asyncio.run(fetch_metered_load_recent_rto(FakeClient(), cfg, now_local))

    assert captured["feed"] == "hrl_load_metered"
    assert captured["params"]["zone"] == "RTO"
    assert captured["params"]["zone"] != COMED_METERED_ZONE  # not "CE"


def test_rto_metered_rows_per_hour_tripwire_silent_on_one_row_per_hour(capsys):
    """The happy path: PJM returns 1 aggregate row per hour for
    zone=RTO. The tripwire emits nothing."""
    items = [
        {"datetime_beginning_ept": "2026-07-15T13:00:00", "mw": 145000.0},
        {"datetime_beginning_ept": "2026-07-15T14:00:00", "mw": 152000.0},
        {"datetime_beginning_ept": "2026-07-15T15:00:00", "mw": 158000.0},
    ]
    _check_rto_metered_load_rows_per_hour(items)
    captured = capsys.readouterr()
    assert "rto_metered_load_unexpected_rows_per_hour" not in captured.out


def test_rto_metered_rows_per_hour_tripwire_fires_on_duplicate_hour(capsys):
    """The degenerate case the tripwire is built for: PJM returns N
    rows per hour (e.g., one per zone, all labeled zone=RTO). The warn
    log surfaces the issue immediately so downstream queries summing
    `mw` don't silently over-count."""
    items = [
        {"datetime_beginning_ept": "2026-07-15T13:00:00", "mw": 25000.0},
        {"datetime_beginning_ept": "2026-07-15T13:00:00", "mw": 18000.0},  # duplicate hour
        {"datetime_beginning_ept": "2026-07-15T13:00:00", "mw": 22000.0},  # duplicate hour
        {"datetime_beginning_ept": "2026-07-15T14:00:00", "mw": 28000.0},
    ]
    _check_rto_metered_load_rows_per_hour(items)
    captured = capsys.readouterr()
    assert "rto_metered_load_unexpected_rows_per_hour" in captured.out
    assert "warn" in captured.out


def test_rto_tripwire_runs_inside_rto_fetcher(capsys):
    """End-to-end: fetch_metered_load_recent_rto runs the tripwire on
    the raw response, before items are turned into Points. Ensures the
    invariant is checked on every RTO call, not just when explicitly
    invoked by a test."""
    duplicate_response = [
        {"datetime_beginning_ept": "2026-07-15T13:00:00",
         "mw": 25000.0, "zone": "RTO"},
        {"datetime_beginning_ept": "2026-07-15T13:00:00",
         "mw": 18000.0, "zone": "RTO"},
    ]

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            return duplicate_response

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=CHICAGO)

    asyncio.run(fetch_metered_load_recent_rto(FakeClient(), cfg, now_local))
    captured = capsys.readouterr()
    assert "rto_metered_load_unexpected_rows_per_hour" in captured.out


def test_comed_metered_does_not_run_rto_tripwire(capsys):
    """Symmetric guard: the ComEd metered fetcher must NOT emit the
    RTO tripwire warn even when its response would also be "duplicate"
    by RTO standards. ComEd zone metered legitimately returns one row
    per hour per zone tag value, but `zone=CE` always returns one
    aggregate row per hour. The check only applies to RTO."""
    duplicate_response = [
        {"datetime_beginning_ept": "2026-07-15T13:00:00",
         "mw": 14000.0, "zone": "CE"},
        # Hypothetical duplicate (won't happen in real PJM data) -- still
        # the ComEd fetcher should not emit the RTO-specific warn.
        {"datetime_beginning_ept": "2026-07-15T13:00:00",
         "mw": 14100.0, "zone": "CE"},
    ]

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            return duplicate_response

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=CHICAGO)

    asyncio.run(fetch_metered_load_recent(FakeClient(), cfg, now_local))
    captured = capsys.readouterr()
    assert "rto_metered_load_unexpected_rows_per_hour" not in captured.out


# ---- P1.1 (reviewer-flagged 2026-05-11): EPT request windows --------------


def test_request_window_is_ept_not_chicago_for_inst_load():
    """Reviewer-flagged 2026-05-11: PJM's ``_ept`` filter expects
    Eastern Prevailing Time on REQUEST as well as RESPONSE. Pre-fix
    the poller formatted ``now_local`` (cfg.tz = Chicago) directly,
    asking PJM for a window 1 hour stale in summer.

    Cross-DST stability check: a Chicago-local time always resolves
    to the same Eastern wall-clock regardless of which DST band
    we're in. This test pins the EDT case (Jul = both CDT and EDT
    in summer)."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    # 12:00 CT = 13:00 ET in summer (cleanly hour-aligned for clarity).
    now_local = datetime(2026, 7, 15, 12, 0, 0, tzinfo=CHICAGO)
    asyncio.run(fetch_inst_load_recent(FakeClient(), cfg, now_local))

    # The window must end at 13:00 EDT, not 12:00 (which would be CDT
    # leaking through as if it were EDT).
    assert (
        captured["params"]["datetime_beginning_ept"]
        == "2026-07-15T12:30:00.0to2026-07-15T13:00:00.0"
    )


def test_request_window_is_ept_not_chicago_for_metered_load():
    """Symmetric for ``hrl_load_metered`` (5-day window). The window
    boundaries are in EPT regardless of cfg.tz."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 15, 12, 0, 0, tzinfo=CHICAGO)
    asyncio.run(fetch_metered_load_recent(FakeClient(), cfg, now_local))

    # End in EDT (13:00 ET), 5 days back from there.
    assert (
        captured["params"]["datetime_beginning_ept"]
        == "2026-07-10T13:00:00.0to2026-07-15T13:00:00.0"
    )


def test_request_window_is_ept_for_da_lmp_tomorrow():
    """The DA LMP fetcher asks for tomorrow's date at 00:00 EPT. The
    "tomorrow" boundary must be computed in Eastern -- a late-night
    Chicago poll could fall on a different EPT date."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    # Edge case: 23:30 CT = 00:30 EDT next day. EPT "tomorrow" is two
    # calendar days from now in Chicago terms.
    now_local = datetime(2026, 7, 15, 23, 30, 0, tzinfo=CHICAGO)
    asyncio.run(fetch_da_lmp_for_tomorrow(FakeClient(), cfg, now_local))

    # 23:30 CDT (Jul 15) = 00:30 EDT (Jul 16). Tomorrow in EPT = Jul 17.
    assert captured["params"]["datetime_beginning_ept"] == "2026-07-17T00:00:00.0"


def test_request_window_is_ept_for_peak_forecast_rto():
    """``ops_sum_frcst_peak_rto`` uses ``generated_at_ept`` which also
    needs to be EPT-formatted. The "today midnight" boundary is in
    Eastern."""
    captured: dict[str, object] = {}

    class FakeClient:
        async def fetch(self, feed: str, params: dict) -> list[dict]:
            captured["params"] = params
            return []

    cfg = MagicMock()
    cfg.tz = CHICAGO
    now_local = datetime(2026, 7, 15, 12, 0, 0, tzinfo=CHICAGO)
    asyncio.run(fetch_peak_forecast_rto(FakeClient(), cfg, now_local))

    # "Today midnight EPT" = 2026-07-15T00:00:00; today end = today
    # 23:59:59 in EPT.
    assert (
        captured["params"]["generated_at_ept"]
        == "2026-07-15T00:00:00.0to2026-07-15T23:59:59.0"
    )
