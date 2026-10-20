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
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from . import app
from .app import (
    Alert,
    Config,
    PJM_FEED_SLAS,
    PriceSpikeTracker,
    post_intake,
    check_controller_down,
    check_device_status_failures,
    check_pjm_feed_failures,
    check_pjm_feed_freshness,
    check_poller_silence,
    check_price_spike,
    filter_muted,
    poller_last_writes,
)


# ---- check_poller_silence -------------------------------------------------


def _last_writes(
    *,
    fresh_pollers: tuple[str, ...] | list[str] = (),
    stale_minutes_ago: dict[str, int] | None = None,
    missing_pollers: tuple[str, ...] | list[str] = (),
) -> dict[str, datetime | None]:
    """Build a poller_last_writes-style dict.

    fresh_pollers: names that wrote a few seconds ago (well under any tolerance)
    stale_minutes_ago: dict of {poller_name: minutes_since_last_write}
    missing_pollers: names that have no write at all (last_ts=None)
    """
    out: dict[str, datetime | None] = {}
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
                                           "thermostat-poller", "comfortnet-publisher",
                                           "pjm-dm2-poller"],
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


def test_check_poller_silence_pjm_dm2_poller_tolerance(monkeypatch):
    """pjm-dm2-poller writes a heartbeat every loop cycle (hourly).
    Tolerance is 130 min — one missed cycle is fine, two consecutive
    misses fire. 131 min stale → alert; 129 min stale → silent."""
    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            stale_minutes_ago={"pjm-dm2-poller": 131},
                        ))
    fired = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert len(fired) == 1
    assert fired[0].key == "silent:pjm-dm2-poller"
    assert "131" in fired[0].text and "130" in fired[0].text

    monkeypatch.setattr(app, "poller_last_writes",
                        lambda q, b: _last_writes(
                            stale_minutes_ago={"pjm-dm2-poller": 129},
                        ))
    silent = check_poller_silence(MagicMock(), "energy", threshold_min=10)
    assert silent == []


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


# ---- filter_muted ---------------------------------------------------------


def test_filter_muted_drops_alerts_whose_key_starts_with_a_prefix():
    alerts = [
        Alert(key="silent:eagle-poller", text="a"),
        Alert(key="silent:comed-poller", text="b"),
        Alert(key="pjm_feed_stale:da_hrl_lmps", text="c"),
        Alert(key="pjm_feed_failed:inst_load", text="d"),
        Alert(key="price_spike", text="e"),
    ]
    kept = filter_muted(alerts, ["silent:eagle-poller", "pjm_feed_"])
    assert [a.key for a in kept] == ["silent:comed-poller", "price_spike"]


def test_filter_muted_no_prefixes_is_passthrough():
    alerts = [Alert(key="silent:eagle-poller", text="a")]
    assert filter_muted(alerts, []) == alerts


def test_config_parses_mute_prefixes_from_env(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c",
                 "INFLUXDB_TOKEN": "x", "INFLUXDB_ORG": "o", "INFLUXDB_BUCKET": "b",
                 "ALERT_MUTE_PREFIXES": " silent:eagle-poller, pjm_feed_ ,,"}.items():
        monkeypatch.setenv(k, v)
    assert app.Config.from_env().alert_mute_prefixes == ("silent:eagle-poller", "pjm_feed_")


def test_config_mute_prefixes_default_empty(monkeypatch):
    for k, v in {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c",
                 "INFLUXDB_TOKEN": "x", "INFLUXDB_ORG": "o", "INFLUXDB_BUCKET": "b"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ALERT_MUTE_PREFIXES", raising=False)
    assert app.Config.from_env().alert_mute_prefixes == ()


def test_daily_summary_loop_exits_immediately_when_summary_hour_negative():
    import asyncio
    cfg = MagicMock(summary_hour=-1)
    stop = asyncio.Event()
    # Never-set stop event: without the disable guard this would sleep until
    # the next summary hour. It must return at once with no query/send.
    asyncio.run(asyncio.wait_for(
        app.daily_summary_loop(cfg, None, ZoneInfo("America/Chicago"), stop), timeout=2))


# ---- check_price_spike ----------------------------------------------------



# ---- price spikes: episodes, not readings ----------------------------------
#
# Chris's ruling (2026-10-20): the spike alerts stay (he sheds load on them), but
# one per episode event, not one per 5-minute price. Start when the price
# crosses the threshold, one more when it climbs through a band, one when it
# has been under the threshold for two consecutive readings. All pass through
# to Chris (lane "chris"); the Chief does not reason over them.

T0 = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


def T() -> PriceSpikeTracker:
    return PriceSpikeTracker(threshold_c=20, bands_c=(30, 40), end_readings=2)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_tracker_start_once_then_silent():
    t = T()
    a = t.update(24.0, at(0))
    assert [x.lane for x in a] == ["chris"]
    assert a[0].key.startswith("price_spike:start:")
    assert "24.0" in a[0].title
    assert t.update(27.0, at(5)) == []          # a wandering price is the same episode
    assert t.update(21.3, at(10)) == []


def test_tracker_bands_fire_once_each_in_order():
    t = T()
    t.update(24.0, at(0))
    a = t.update(45.0, at(5))
    assert [x.key.split(":")[2] for x in a] == ["30", "40"]
    assert all(x.lane == "chris" for x in a)
    assert t.update(46.0, at(10)) == []


def test_tracker_one_reading_dip_is_not_an_end():
    t = T()
    t.update(24.0, at(0))
    assert t.update(18.0, at(5)) == []
    assert t.update(25.0, at(10)) == []         # still the same episode, no new start


def test_tracker_end_after_two_below_with_duration_and_peak():
    t = T()
    t.update(24.0, at(0))
    t.update(34.0, at(5))
    assert t.update(18.0, at(95)) == []
    a = t.update(8.1, at(100))
    assert len(a) == 1
    assert a[0].severity == "ok"
    assert "1h40m" in a[0].text and "34.0" in a[0].text
    assert "8.1" in a[0].title
    assert a[0].key.startswith("price_spike:end:")
    b = t.update(24.0, at(105))
    assert b[0].key.startswith("price_spike:start:")   # a new episode


def test_tracker_none_is_ignored():
    assert T().update(None, at(0)) == []


def test_fresh_tracker_above_threshold_starts():
    # a restart mid-episode loses state and emits one more start; never a silent miss
    assert len(T().update(21.0, at(0))) == 1


def test_tracker_threshold_is_inclusive():
    assert len(T().update(20.0, at(0))) == 1
    assert T().update(19.9, at(0)) == []


def test_check_price_spike_feeds_tracker(monkeypatch):
    monkeypatch.setattr(app, "fetch_one", lambda q, f: [{"_value": 20.0}])
    t = T()
    assert len(check_price_spike(MagicMock(), "energy", t, at(0))) == 1
    monkeypatch.setattr(app, "fetch_one", lambda q, f: [])
    assert check_price_spike(MagicMock(), "energy", t, at(5)) == []
    monkeypatch.setattr(app, "fetch_one", lambda q, f: [{"_value": None}])
    assert check_price_spike(MagicMock(), "energy", t, at(10)) == []


# ---- the intake is the only destination ------------------------------------

def _cfg(**over: Any) -> Config:
    base = dict(intake_url="http://intake.test/alert", summary_hour=8, alert_check_interval_s=300,
                poller_silent_min=10, price_spike_threshold_c=20.0, price_spike_bands_c=(30.0, 40.0),
                price_spike_end_readings=2, alert_dedupe_min=30, alert_mute_prefixes=(),
                tz_name="America/Chicago", influx_url="http://influx", influx_token="t",
                influx_org="o", influx_bucket="energy")
    base.update(over)
    return Config(**base)


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status

    async def text(self) -> str:
        return ""

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None


class _FakeSession:
    posted: list[tuple[str, dict[str, Any]]] = []
    status = 200

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def post(self, url: str, json: dict[str, Any], timeout: Any = None) -> _FakeResp:
        _FakeSession.posted.append((url, json))
        return _FakeResp(_FakeSession.status)


async def test_post_intake_payload(monkeypatch):
    _FakeSession.posted.clear()
    _FakeSession.status = 200
    monkeypatch.setattr(app.aiohttp, "ClientSession", _FakeSession)
    ok = await post_intake(_cfg(), Alert(key="price_spike:start:x", text="Defer big loads if possible.",
                                         title="ComEd price spike: 24.0¢/kWh", lane="chris", severity="warn"))
    assert ok is True
    url, body = _FakeSession.posted[0]
    assert url == "http://intake.test/alert"
    assert body["source"] == "energy" and body["lane_hint"] == "chris" and body["severity"] == "warn"
    assert body["title"] == "ComEd price spike: 24.0¢/kWh" and body["body"] == "Defer big loads if possible."
    assert body["dedupe_key"] == "price_spike:start:x" and body["html"] is False and body["kind"] == "alert"


async def test_post_intake_defaults_title_to_first_line_and_reports_failure(monkeypatch):
    _FakeSession.posted.clear()
    _FakeSession.status = 500
    monkeypatch.setattr(app.aiohttp, "ClientSession", _FakeSession)
    ok = await post_intake(_cfg(), Alert(key="silent:eagle-poller", text="eagle-poller silent 12 min\nlast write 14:02"))
    assert ok is False
    _, body = _FakeSession.posted[0]
    assert body["title"] == "eagle-poller silent 12 min" and body["lane_hint"] == "chief"


async def test_alert_loop_posts_by_lane_and_keeps_failed_keys(monkeypatch):
    import asyncio
    posted: list[dict[str, Any]] = []
    fail_first = {"n": 0}

    async def fake_post(cfg: Config, alert: Alert) -> bool:
        posted.append({"key": alert.key, "lane": alert.lane})
        fail_first["n"] += 1
        return fail_first["n"] != 1          # the first post fails, the rest succeed

    monkeypatch.setattr(app, "post_intake", fake_post)
    for name in ("check_poller_silence", "check_device_status_failures", "check_controller_down",
                 "check_fridge_anomalies", "check_pjm_feed_freshness", "check_pjm_feed_failures"):
        monkeypatch.setattr(app, name, lambda *a, **k: [])
    monkeypatch.setattr(app, "check_poller_silence", lambda *a, **k: [Alert(key="silent:x", text="x silent")])
    monkeypatch.setattr(app, "check_price_spike",
                        lambda q, b, tracker, now: [Alert(key="price_spike:start:s", text="d", title="t", lane="chris")])
    stop = asyncio.Event()
    cfg = _cfg(alert_check_interval_s=1)

    async def run_two_ticks() -> None:
        task = asyncio.create_task(app.alert_loop(cfg, MagicMock(), stop))
        await asyncio.sleep(1.5)
        stop.set()
        await task

    await run_two_ticks()
    lanes = [(p["key"], p["lane"]) for p in posted]
    assert ("silent:x", "chief") in lanes and ("price_spike:start:s", "chris") in lanes
    # the first post failed, so that key was posted again on the second tick
    assert lanes.count(lanes[0]) == 2


# ---- poller_last_writes ---------------------------------------------------


def test_poller_last_writes_picks_max_when_stale_row_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESSION: Flux `|> last()` returns ONE row per series, so a
    multi-series measurement like `comed.prices` (period_type=5min AND
    period_type=hourly_avg) yields multiple rows. Picking `rows[0]`
    arbitrarily can land on the stale hourly_avg series (written once
    per hour) and produce a false "comed-poller silent for 27 min"
    alert when the 5min series is fresh.

    The function MUST take the MAX `_time` across all rows so a poller
    counts as "writing" if ANY of its series is recent.

    Worst-case ordering: stale row first, fresh row second. Under the
    buggy `rows[0]` code this test FAILS (returns the stale time).
    Under the max-across-rows fix it returns the fresh time.
    """
    fresh = datetime(2026, 5, 20, 14, 30, 0, tzinfo=timezone.utc)
    stale = datetime(2026, 5, 20, 14,  3, 0, tzinfo=timezone.utc)  # 27 min earlier

    def _multi_row_fetch(query_api: Any, flux: str) -> list[dict[str, Any]]:
        # Simulate Flux returning the stale series first
        return [
            {"_time": stale, "result": "_result", "table": 0},
            {"_time": fresh, "result": "_result", "table": 1},
        ]

    monkeypatch.setattr(app, "fetch_one", _multi_row_fetch)
    result = poller_last_writes(MagicMock(), "energy")

    # Every poller in the measurement list should resolve to the FRESH time.
    for poller, ts in result.items():
        assert ts == fresh, f"{poller} got {ts}, expected fresh {fresh}"


def test_poller_last_writes_picks_max_when_fresh_row_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive: ordering doesn't matter. With fresh first the buggy
    `rows[0]` code happens to be correct; the fix must not regress this
    case."""
    fresh = datetime(2026, 5, 20, 14, 30, 0, tzinfo=timezone.utc)
    stale = datetime(2026, 5, 20, 14,  3, 0, tzinfo=timezone.utc)

    def _multi_row_fetch(query_api: Any, flux: str) -> list[dict[str, Any]]:
        return [
            {"_time": fresh, "result": "_result", "table": 0},
            {"_time": stale, "result": "_result", "table": 1},
        ]

    monkeypatch.setattr(app, "fetch_one", _multi_row_fetch)
    result = poller_last_writes(MagicMock(), "energy")
    for poller, ts in result.items():
        assert ts == fresh, f"{poller} got {ts}, expected fresh {fresh}"


def test_poller_last_writes_returns_none_when_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A poller with no rows in the lookback window resolves to None."""
    monkeypatch.setattr(app, "fetch_one", lambda q, f: [])
    result = poller_last_writes(MagicMock(), "energy")
    for poller, ts in result.items():
        assert ts is None, f"{poller} got {ts}, expected None"


# ---- check_pjm_feed_freshness ---------------------------------------------
#
# Per-feed deadman on pjm.feed_status. Each PJM feed has its own cadence:
# DA LMP daily, NSPL annually, RTO peak forecast cooling-season-only.
# Tests pin both the per-feed tolerance numbers and the in-season gating
# semantics so a refactor doesn't silently weaken the alerting.


CHICAGO = ZoneInfo("America/Chicago")


def _setup_pjm_clock(
    monkeypatch: pytest.MonkeyPatch,
    when_local: datetime,
    **feeds_to_age_hours: float | None,
) -> None:
    """Pin app.datetime.now() to `when_local` AND stub
    latest_pjm_feed_successes() to return success timestamps that are
    `hours_ago` behind that same pinned now. Combining both stubs in one
    helper guarantees the function-under-test and the fixture data agree
    on what 'now' means — easy to get wrong if these are stubbed in
    separate calls.

    Use feed_name absent (or None) to model "no success row ever seen"."""
    when_utc = when_local.astimezone(timezone.utc)

    class _StubDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return when_utc.astimezone(tz)
            return when_utc.replace(tzinfo=None)
    monkeypatch.setattr(app, "datetime", _StubDatetime)

    out = {feed: when_utc - timedelta(hours=hrs)
           for feed, hrs in feeds_to_age_hours.items() if hrs is not None}
    monkeypatch.setattr(app, "latest_pjm_feed_successes",
                        lambda q, b: out)


def test_pjm_feed_slas_cover_every_scheduled_feed():
    """Pin: every PJM feed in the poller's FEED_SCHEDULE should have a
    matching SLA here. Drops or adds in the poller without an SLA update
    should fail this test."""
    sla_feeds = {sla.feed for sla in PJM_FEED_SLAS}
    expected = {
        "da_hrl_lmps",
        "load_frcstd_7_day",
        "hrl_load_metered",
        "ops_sum_frcst_peak_rto",
        "annual_zonal_nspl",
    }
    assert sla_feeds == expected


def test_pjm_freshness_no_alerts_when_all_feeds_fresh(monkeypatch):
    """In-season for all feeds, every feed reported success well within
    its tolerance. No alerts."""
    _setup_pjm_clock(
        monkeypatch,
        datetime(2026, 7, 15, 10, tzinfo=CHICAGO),  # cooling season
        da_hrl_lmps=18,           # 18h < 25h tolerance
        load_frcstd_7_day=4,      # 4h < 14h
        hrl_load_metered=72,      # 72h < 192h (8 days)
        ops_sum_frcst_peak_rto=4, # cooling season, 4h < 14h
    )
    alerts = check_pjm_feed_freshness(MagicMock(), "energy", CHICAGO)
    assert alerts == [], f"unexpected: {[a.key for a in alerts]}"


def test_pjm_freshness_da_lmp_stale_fires(monkeypatch):
    """DA LMP fires daily; 26h since last success exceeds 25h tolerance."""
    _setup_pjm_clock(
        monkeypatch,
        datetime(2026, 7, 15, 18, tzinfo=CHICAGO),
        da_hrl_lmps=26,
        load_frcstd_7_day=4,
        hrl_load_metered=72,
        ops_sum_frcst_peak_rto=4,
    )
    alerts = check_pjm_feed_freshness(MagicMock(), "energy", CHICAGO)
    keys = [a.key for a in alerts]
    assert keys == ["pjm_feed_stale:da_hrl_lmps"]
    assert "Day-ahead LMP" in alerts[0].text
    assert "26.0" in alerts[0].text and "25" in alerts[0].text


def test_pjm_freshness_never_seen_success_does_not_fire(monkeypatch):
    """A feed with no success rows is silent in the freshness check.

    This branch used to fire 'never reported success', but that conflated
    two distinct states the system actually distinguishes:
    (1) feed never attempted (cold-start: no rows of any kind), and
    (2) feed attempted, only failures (rows with success=false exist).
    State (2) is now covered by check_pjm_feed_failures, which fires
    immediately on the actual failure row with the real error context.
    State (1) is correctly silent — there is nothing to alert on.

    Pinning this absence keeps a future change from re-introducing the
    deploy-day flood that this commit was the response to."""
    _setup_pjm_clock(
        monkeypatch,
        datetime(2026, 7, 15, 18, tzinfo=CHICAGO),
        # da_hrl_lmps intentionally absent (no success row)
        load_frcstd_7_day=4,
        hrl_load_metered=72,
        ops_sum_frcst_peak_rto=4,
    )
    alerts = check_pjm_feed_freshness(MagicMock(), "energy", CHICAGO)
    keys = [a.key for a in alerts]
    assert "pjm_feed_stale:da_hrl_lmps" not in keys


def test_pjm_freshness_cooling_season_feed_suppressed_off_season(monkeypatch):
    """ops_sum_frcst_peak_rto fires Jun-Sep only. In May the feed will
    have a ~9-month-old success row (or none), but we don't alert
    because the feed is legitimately not running."""
    _setup_pjm_clock(
        monkeypatch,
        datetime(2026, 5, 15, 18, tzinfo=CHICAGO),
        da_hrl_lmps=18,
        load_frcstd_7_day=4,
        hrl_load_metered=72,
        # ops_sum_frcst_peak_rto: no row at all (truly off-season silence)
    )
    alerts = check_pjm_feed_freshness(MagicMock(), "energy", CHICAGO)
    keys = [a.key for a in alerts]
    assert "pjm_feed_stale:ops_sum_frcst_peak_rto" not in keys


def test_pjm_freshness_cooling_season_feed_fires_in_season(monkeypatch):
    """Same feed in July with a stale success → alert."""
    _setup_pjm_clock(
        monkeypatch,
        datetime(2026, 7, 15, 18, tzinfo=CHICAGO),
        da_hrl_lmps=18,
        load_frcstd_7_day=4,
        hrl_load_metered=72,
        ops_sum_frcst_peak_rto=20,  # 20h > 14h tolerance
    )
    alerts = check_pjm_feed_freshness(MagicMock(), "energy", CHICAGO)
    keys = [a.key for a in alerts]
    assert "pjm_feed_stale:ops_sum_frcst_peak_rto" in keys


def test_pjm_freshness_annual_nspl_suppressed_outside_dec_jan(monkeypatch):
    """Annual NSPL is checked only Dec 1 – Jan 31. In June a 12-month-old
    last-success is the normal state; alerting on it would be noise."""
    _setup_pjm_clock(
        monkeypatch,
        datetime(2026, 6, 15, 18, tzinfo=CHICAGO),
        da_hrl_lmps=18,
        load_frcstd_7_day=4,
        hrl_load_metered=72,
        ops_sum_frcst_peak_rto=4,
        annual_zonal_nspl=24 * 200,  # 200 days old, normal mid-year state
    )
    alerts = check_pjm_feed_freshness(MagicMock(), "energy", CHICAGO)
    keys = [a.key for a in alerts]
    assert "pjm_feed_stale:annual_zonal_nspl" not in keys


def test_pjm_freshness_annual_nspl_fires_in_december_window(monkeypatch):
    """In December if NSPL didn't successfully fire (last success is
    11 months old, well above the 168h = 7d tolerance), alert."""
    _setup_pjm_clock(
        monkeypatch,
        datetime(2026, 12, 5, 10, tzinfo=CHICAGO),
        da_hrl_lmps=18,
        load_frcstd_7_day=4,
        hrl_load_metered=72,
        annual_zonal_nspl=24 * 330,  # 11 months ago
    )
    alerts = check_pjm_feed_freshness(MagicMock(), "energy", CHICAGO)
    keys = [a.key for a in alerts]
    assert "pjm_feed_stale:annual_zonal_nspl" in keys


def test_pjm_freshness_alert_keys_use_pjm_feed_stale_prefix():
    """All PJM-freshness alert keys share a common prefix so the dedup
    layer (last_sent dict in alert_loop) handles them per-feed cleanly."""
    a = Alert(key="pjm_feed_stale:da_hrl_lmps", text="...")
    assert a.key.startswith("pjm_feed_stale:")


def test_pjm_freshness_query_failure_returns_no_alerts(monkeypatch):
    """Transient Influx query errors must NOT fire 'never reported success'
    alerts for every feed at once — that would flood the channel and the
    dedup layer would suppress real alerts when they recur shortly after.
    A query failure logs warn and returns []."""
    when_local = datetime(2026, 7, 15, 18, tzinfo=CHICAGO)
    when_utc = when_local.astimezone(timezone.utc)

    class _StubDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return when_utc.astimezone(tz)
            return when_utc.replace(tzinfo=None)
    monkeypatch.setattr(app, "datetime", _StubDatetime)

    def _fail(_q, _b):
        raise RuntimeError("InfluxDB connection refused")
    monkeypatch.setattr(app, "latest_pjm_feed_successes", _fail)

    alerts = check_pjm_feed_freshness(MagicMock(), "energy", CHICAGO)
    assert alerts == []


# ---- check_pjm_feed_failures ----------------------------------------------
#
# Direct alert on `pjm.feed_status` rows with success=false. Replaces the
# old "never reported success" branch of check_pjm_feed_freshness with a
# real-time signal that includes the actual exception type and message
# the poller wrote, instead of a generic "check the logs" message.


def test_pjm_failures_no_rows_no_alerts(monkeypatch):
    """No failure rows in the lookback window → no alerts. This is the
    common-case path that prevents cold-start spam: a freshly-deployed
    poller that hasn't yet attempted any feed has zero failure rows and
    so produces zero alerts here."""
    monkeypatch.setattr(app, "latest_pjm_feed_failures", lambda q, b, m: [])
    alerts = check_pjm_feed_failures(MagicMock(), "energy")
    assert alerts == []


def test_pjm_failures_single_failure_fires_with_error_context(monkeypatch):
    """A single failure row produces one alert that includes the feed
    name, the exception type, and (truncated) error message — the
    actionable context lives in the alert itself, not in 'check the logs'."""
    monkeypatch.setattr(app, "latest_pjm_feed_failures",
                        lambda q, b, m: [{
                            "feed": "da_hrl_lmps",
                            "error_type": "RuntimeError",
                            "error_msg": "PJM HTTP 401: unauthorized",
                            "_time": datetime(2026, 5, 7, 22, tzinfo=timezone.utc),
                        }])
    alerts = check_pjm_feed_failures(MagicMock(), "energy")
    assert len(alerts) == 1
    a = alerts[0]
    assert a.key == "pjm_feed_failed:da_hrl_lmps"
    assert "da_hrl_lmps" in a.text
    assert "RuntimeError" in a.text
    assert "PJM HTTP 401" in a.text


def test_pjm_failures_multiple_feeds_produce_independent_alerts(monkeypatch):
    """Two feeds failing in the same lookback window produce two alerts
    with distinct dedup keys — a recurring failure on one feed does not
    suppress a new failure on another."""
    monkeypatch.setattr(app, "latest_pjm_feed_failures",
                        lambda q, b, m: [
                            {"feed": "da_hrl_lmps", "error_type": "RuntimeError",
                             "error_msg": "401", "_time": None},
                            {"feed": "load_frcstd_7_day", "error_type": "TimeoutError",
                             "error_msg": "deadline exceeded", "_time": None},
                        ])
    alerts = check_pjm_feed_failures(MagicMock(), "energy")
    keys = sorted(a.key for a in alerts)
    assert keys == ["pjm_feed_failed:da_hrl_lmps", "pjm_feed_failed:load_frcstd_7_day"]


def test_pjm_failures_query_failure_returns_no_alerts(monkeypatch):
    """Transient Influx query errors must not fire phantom failure alerts
    for every feed. Same defensive contract as check_pjm_feed_freshness:
    log warn, return [], let the next cycle retry."""
    def _fail(_q, _b, _m):
        raise RuntimeError("InfluxDB connection refused")
    monkeypatch.setattr(app, "latest_pjm_feed_failures", _fail)
    alerts = check_pjm_feed_failures(MagicMock(), "energy")
    assert alerts == []


def test_pjm_failures_truncates_long_error_messages(monkeypatch):
    """A pathologically long error_msg (e.g., a full HTML error page from
    PJM) gets capped at 200 chars in the alert text. The poller already
    truncates at 200 when writing, but the alerter belt-and-suspenders
    truncates again so a misconfigured upstream can't bloat Telegram
    messages past their 4096-char limit."""
    long_msg = "X" * 500
    monkeypatch.setattr(app, "latest_pjm_feed_failures",
                        lambda q, b, m: [{
                            "feed": "da_hrl_lmps",
                            "error_type": "RuntimeError",
                            "error_msg": long_msg,
                            "_time": None,
                        }])
    alerts = check_pjm_feed_failures(MagicMock(), "energy")
    assert len(alerts) == 1
    assert "X" * 200 in alerts[0].text
    assert "X" * 201 not in alerts[0].text


def test_pjm_failures_alert_key_prefix():
    """Pinning the dedup-key shape so future refactors don't accidentally
    collide with check_pjm_feed_freshness's `pjm_feed_stale:` keys."""
    a = Alert(key="pjm_feed_failed:da_hrl_lmps", text="...")
    assert a.key.startswith("pjm_feed_failed:")


# ---- check_controller_down --------------------------------------------------
#
# Rev 4 watchdog beacon: the watchdog writes hvac.heartbeat with
# controller_alive=false when the controller dies (absence = healthy).
# This check alerts on the beacon within the last 10 min.


def test_check_controller_down_fires_on_beacon(monkeypatch):
    """A controller_alive=false beacon row in the window fires exactly one
    alert with the fixed dedup key; no beacon rows → silent."""
    monkeypatch.setattr(app, "fetch_one", lambda q, f: [{"_value": False}])
    alerts = check_controller_down(MagicMock(), "energy")
    assert len(alerts) == 1 and alerts[0].key == "controller_down"

    monkeypatch.setattr(app, "fetch_one", lambda q, f: [])
    assert check_controller_down(MagicMock(), "energy") == []


# check_hvac_action_errors was retired with hvac.actions.error (spec
# §Telemetry). Its replacement, check_device_status_failures, is tested below.


# ---- check_device_status_failures -------------------------------------------
#
# Consecutive-of-a-kind. Measured over 12.3 days of production: 109 failed
# ticks, none reaching 3 consecutive, while the old any-class rule would have
# fired ~1x/day on blips that self-healed on the very next tick.


def test_device_status_alerts_on_three_consecutive_read_failures(monkeypatch):
    rows = [{"success": "false", "op": "read", "error_type": "TimeoutError",
             "error_msg": ""} for _ in range(3)]
    monkeypatch.setattr(app, "fetch_one", lambda q, f: rows if 'r.op == "read"' in f else [])
    alerts = check_device_status_failures(MagicMock(), "energy")
    assert len(alerts) == 1
    assert alerts[0].key == "hvac_device:read"
    assert "TimeoutError" in alerts[0].text


def test_device_status_silent_when_a_success_interleaves(monkeypatch):
    """The whole point of attempt rows: one success breaks the run."""
    rows = [{"success": "false", "op": "read", "error_type": "TimeoutError", "error_msg": ""},
            {"success": "true",  "op": "read", "error_type": "", "error_msg": ""},
            {"success": "false", "op": "read", "error_type": "TimeoutError", "error_msg": ""}]
    monkeypatch.setattr(app, "fetch_one", lambda q, f: rows if 'r.op == "read"' in f else [])
    assert check_device_status_failures(MagicMock(), "energy") == []


def test_device_status_crash_alerts_on_one(monkeypatch):
    rows = [{"success": "false", "op": "crash", "error_type": "ValueError",
             "error_msg": "bad state"}]
    monkeypatch.setattr(app, "fetch_one", lambda q, f: rows if 'r.op == "crash"' in f else [])
    alerts = check_device_status_failures(MagicMock(), "energy")
    assert len(alerts) == 1 and alerts[0].key == "hvac_device:crash"
    assert "bad state" in alerts[0].text


def test_device_status_silent_with_too_few_rows(monkeypatch):
    rows = [{"success": "false", "op": "read", "error_type": "TimeoutError", "error_msg": ""}]
    monkeypatch.setattr(app, "fetch_one", lambda q, f: rows if 'r.op == "read"' in f else [])
    assert check_device_status_failures(MagicMock(), "energy") == []


def test_device_status_query_failure_declines_to_alert(monkeypatch):
    def boom(q, f): raise RuntimeError("influx down")
    monkeypatch.setattr(app, "fetch_one", boom)
    assert check_device_status_failures(MagicMock(), "energy") == []
