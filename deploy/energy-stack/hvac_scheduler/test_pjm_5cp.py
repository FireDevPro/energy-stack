"""Tests for the PJM 5CP-eligibility detector (§3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from .pjm_5cp import (
    COMED_PRE_SEASON_FALLBACK_5TH_MW,
    COMED_SCOPE,
    RTO_PRE_SEASON_FALLBACK_5TH_MW,
    RTO_SCOPE,
    COOL_SHUTOFF_F,
    LOAD_RATIO_RELEASE,
    LOAD_RATIO_TRIGGER,
    MIN_OBSERVATIONS_FOR_5TH,
    SPARSE_DATA_THRESHOLD,
    DetectorScope,
    FiveCPState,
    ScopeEvaluation,
    ZoneLoadSnapshot,
    cooling_season_window_utc,
    evaluate_5cp_risk,
    evaluate_for_scope,
    fetch_forecast_peak_for_date,
    fetch_zone_live,
    hold_end_time,
    in_cooling_season,
    season_5th_highest_from_loads,
    update_season_5th_highest,
)


CHICAGO = ZoneInfo("America/Chicago")
# 2026-07-15 14:30 CT == 19:30 UTC. Inside the 13-20 CT window.
T_AT_1430_CT = datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)


# ---- Locked thresholds ----------------------------------------------------


def test_locked_thresholds():
    """Pre-committed before OSF; frozen at OSF commit hash."""
    assert LOAD_RATIO_TRIGGER == 0.95
    assert LOAD_RATIO_RELEASE == 0.90
    assert COOL_SHUTOFF_F == 85


def test_comed_pre_season_fallback_is_zone_scaled():
    """ComEd-zone cold-start fallback must be at ComEd-zone scale (~20 GW),
    not RTO scale (~150 GW). The prior 130,000 MW value was RTO-scale
    misapplied to the zone path, leaving the detector inert pre-season.

    Source: 2025 ComEd-zone 5th-highest hourly metered load = 20,375.4 MW
    (empirically pulled from pjm.metered_load{zone=CE} on 2026-05-10)."""
    assert COMED_PRE_SEASON_FALLBACK_5TH_MW == 20375.0
    # Sanity-check the scale is plausible for ComEd zone (15-25 GW range).
    assert 15000 <= COMED_PRE_SEASON_FALLBACK_5TH_MW <= 25000


# ---- season_5th_highest_from_loads ---------------------------------------


def test_sparse_threshold_locked_at_200_hours():
    """Sparse-data threshold pins the cold-start window to ~8 days of
    cooling-season coverage. See pjm_5cp.SPARSE_DATA_THRESHOLD comment
    for the sizing rationale. Pinned here so a quiet bump can't drift
    the cold-start behaviour without an explicit test update."""
    assert SPARSE_DATA_THRESHOLD == 200


def test_sparse_data_returns_fallback_under_threshold():
    """Below the sparse threshold the detector falls back to the
    scope's prior-year value rather than trusting an unreliable
    handful of observations. Covers the first ~8 days of season as
    well as multi-day publish gaps later in the season."""
    assert season_5th_highest_from_loads(
        [], fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
    ) == COMED_PRE_SEASON_FALLBACK_5TH_MW
    # Just below the threshold: still fall back.
    almost_enough = [20000.0] * (SPARSE_DATA_THRESHOLD - 1)
    assert season_5th_highest_from_loads(
        almost_enough, fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
    ) == COMED_PRE_SEASON_FALLBACK_5TH_MW


def test_sparse_data_fallback_respects_caller_scope():
    """An RTO-scoped caller passing a 151,525 MW fallback gets it back
    when data is sparse, not the ComEd 20,375. Prevents the 2026-05
    scale-confusion regression."""
    assert season_5th_highest_from_loads([], fallback_mw=151525.0) == 151525.0
    assert season_5th_highest_from_loads(
        [10.0, 20.0, 30.0], fallback_mw=151525.0,
    ) == 151525.0


def test_sufficient_data_uses_actual_5th_above_fallback():
    """At or above the sparse threshold, return the actual 5th-highest
    from the data. When the data's 5th exceeds the fallback (a normal
    or hot summer), the fallback is inert."""
    # 200 values, mostly at 18,000 MW, with 6 hotter hours: 25,000,
    # 24,500, 23,800, 23,200, 22,900 (top 5), and 22,000 (6th).
    # 5th-highest = 22,900.
    loads = [18000.0] * 194 + [22000.0, 22900.0, 23200.0, 23800.0, 24500.0, 25000.0]
    assert len(loads) == SPARSE_DATA_THRESHOLD
    assert season_5th_highest_from_loads(
        loads, fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
    ) == 22900.0


def test_sufficient_data_uses_actual_5th_even_when_below_fallback():
    """The critical invariant that distinguishes this design from a
    permanent floor: when the current-year 5th-highest legitimately
    lands BELOW the prior-year fallback (a cooler-than-prior summer),
    the function returns the lower current-year value, NOT the
    fallback. This lets the detector adapt to current conditions.

    Concretely: 2026 cool-summer scenario with real ComEd-zone 5th of
    18,500 MW vs 2025 fallback of 20,375 MW. A permanent floor would
    suppress real 2026 5CP detection (ratio against 20,375 stays below
    0.95 for real loads of 17,800+); the sparse-threshold design
    returns 18,500 and lets the detector trigger correctly."""
    # 200 values: 195 baseline at 16,000 + 5 hotter values whose smallest
    # is 18,500. The 5th-highest is the 5th-from-top = 18,500.
    loads = [16000.0] * 195 + [18500.0, 18800.0, 19000.0, 19200.0, 19500.0]
    assert len(loads) == SPARSE_DATA_THRESHOLD
    result = season_5th_highest_from_loads(
        loads, fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
    )
    assert result == 18500.0
    assert result < COMED_PRE_SEASON_FALLBACK_5TH_MW


def test_sufficient_data_picks_correct_5th_in_unsorted_input():
    """Sort happens internally; caller doesn't need to sort first.
    Sized at the sparse threshold so the data path is exercised."""
    # 200 values: 195 baseline + 5 distinct top values in random order.
    loads = [18000.0] * 195 + [24500.0, 22200.0, 24000.0, 23800.0, 24300.0]
    # Top 5 (desc): 24500, 24300, 24000, 23800, 22200 -> 5th = 22200
    assert season_5th_highest_from_loads(
        loads, fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
    ) == 22200.0


def test_sparse_threshold_override_for_testing():
    """``sparse_threshold`` is a kwarg with a default; tests that need
    to exercise the sufficient-data path with small fixtures can
    override it. Production code should always use the locked default."""
    # 5 observations below the production threshold but above the
    # override -- exercise the actual-data path with a small fixture.
    assert season_5th_highest_from_loads(
        [21000.0, 22000.0, 23000.0, 24000.0, 25000.0],
        fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
        sparse_threshold=5,
    ) == 21000.0


# ---- hold_end_time --------------------------------------------------------


def test_hold_end_time_rounds_to_end_of_hour_plus_30min():
    """Trigger at 13:42 -> hold ends 14:30 (end of 13:00 hour + 30min)."""
    triggered = datetime(2026, 7, 15, 18, 42, 11, tzinfo=timezone.utc)
    end = hold_end_time(triggered)
    assert end == datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)


def test_hold_end_time_handles_top_of_hour():
    """A trigger at exactly HH:00 still rounds to (HH+1):30, not HH:30."""
    triggered = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
    end = hold_end_time(triggered)
    assert end == datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)


# ---- evaluate_5cp_risk: trigger conditions -------------------------------


def _all_conditions_met_args(now_utc=T_AT_1430_CT):
    """Helper: a kwargs dict where all four trigger conditions hold."""
    return dict(
        current_load_mw=18500.0,                    # ratio 18500/18000 = 1.027 > 0.95
        season_5th_highest_mw=18000.0,
        load_derivative_mw_per_hour=200.0,          # rising
        forecast_peak_today_mw=19500.0,             # > season_5th
        now_utc=now_utc,
        state=FiveCPState(),
    )


def test_all_conditions_met_triggers_active():
    is_active, new_state = evaluate_5cp_risk(**_all_conditions_met_args())
    assert is_active is True
    assert new_state.is_active is True
    assert new_state.triggered_at_utc == T_AT_1430_CT
    # Triggered hour is 14 in CT (19 UTC -> 14 CDT -> still inside the
    # 13-20 CT window).
    assert new_state.triggered_hour_ct == 14


def test_load_ratio_below_trigger_does_not_fire():
    """ratio = 17100/18000 = 0.95 exactly -- the locked rule is `> 0.95`,
    so 0.95 itself is NOT enough."""
    args = _all_conditions_met_args()
    args["current_load_mw"] = 17100.0
    is_active, _ = evaluate_5cp_risk(**args)
    assert is_active is False


def test_outside_13_20_ct_window_does_not_fire():
    """At 21:30 CT (02:30 UTC next day) all the load conditions could be
    met but we're past the broadened window. No new 5CP would land here."""
    args = _all_conditions_met_args(
        now_utc=datetime(2026, 7, 16, 2, 30, tzinfo=timezone.utc)
    )
    is_active, _ = evaluate_5cp_risk(**args)
    assert is_active is False


def test_just_before_13_ct_does_not_fire():
    """12:59 CT == 17:59 UTC: window starts at 13:00 CT inclusive."""
    args = _all_conditions_met_args(
        now_utc=datetime(2026, 7, 15, 17, 59, tzinfo=timezone.utc)
    )
    is_active, _ = evaluate_5cp_risk(**args)
    assert is_active is False


def test_descending_load_does_not_fire():
    """Load already past peak isn't going to set a new 5CP. Detector
    requires positive derivative to enter active state."""
    args = _all_conditions_met_args()
    args["load_derivative_mw_per_hour"] = -50.0
    is_active, _ = evaluate_5cp_risk(**args)
    assert is_active is False


def test_forecast_peak_below_season_5th_does_not_fire():
    """If today's forecast peak isn't projected to exceed the season-to-date
    5th-highest, no 5CP risk."""
    args = _all_conditions_met_args()
    args["forecast_peak_today_mw"] = 17000.0
    is_active, _ = evaluate_5cp_risk(**args)
    assert is_active is False


# ---- evaluate_5cp_risk: hold semantics -----------------------------------


def test_active_state_holds_through_end_of_hour_even_if_load_drops():
    """Triggered at 14:30 UTC. At 14:50 UTC (still inside the same hour),
    even with a load drop and descending derivative, we stay active."""
    triggered_at = datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)
    state = FiveCPState(is_active=True, triggered_at_utc=triggered_at,
                         triggered_hour_ct=14)
    is_active, new_state = evaluate_5cp_risk(
        current_load_mw=15000.0,            # ratio = 0.83, below release
        season_5th_highest_mw=18000.0,
        load_derivative_mw_per_hour=-100.0, # descending
        forecast_peak_today_mw=19500.0,
        now_utc=datetime(2026, 7, 15, 19, 50, tzinfo=timezone.utc),  # 14:50 CT
        state=state,
    )
    assert is_active is True
    assert new_state == state  # state unchanged inside hold window


def test_hold_continues_30_min_past_end_of_hour():
    """Triggered at 14:30 CT -> hold ends 15:30 CT. At 15:20 CT, still
    inside hold even though we're past the trigger hour."""
    triggered_at = datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)
    state = FiveCPState(is_active=True, triggered_at_utc=triggered_at,
                         triggered_hour_ct=14)
    is_active, _ = evaluate_5cp_risk(
        current_load_mw=15000.0,
        season_5th_highest_mw=18000.0,
        load_derivative_mw_per_hour=-100.0,
        forecast_peak_today_mw=19500.0,
        now_utc=datetime(2026, 7, 15, 20, 20, tzinfo=timezone.utc),  # 15:20 CT
        state=state,
    )
    assert is_active is True


def test_release_only_when_ratio_below_0_90_and_derivative_negative():
    """Hold has elapsed (16:00 CT, well past 15:30 CT release time). Both
    conditions must hold simultaneously: ratio < 0.90 AND derivative < 0."""
    triggered_at = datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)
    state = FiveCPState(is_active=True, triggered_at_utc=triggered_at,
                         triggered_hour_ct=14)
    later = datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc)  # 16:00 CT

    # ratio 0.85, derivative -100: release.
    is_active, new_state = evaluate_5cp_risk(
        current_load_mw=15300.0,
        season_5th_highest_mw=18000.0,
        load_derivative_mw_per_hour=-100.0,
        forecast_peak_today_mw=19500.0,
        now_utc=later,
        state=state,
    )
    assert is_active is False
    assert new_state.is_active is False


def test_no_release_when_ratio_below_0_90_but_derivative_positive():
    """Even after hold elapsed, a positive derivative with a low ratio
    means we're ramping up again -- stay active."""
    triggered_at = datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)
    state = FiveCPState(is_active=True, triggered_at_utc=triggered_at,
                         triggered_hour_ct=14)
    later = datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc)
    is_active, _ = evaluate_5cp_risk(
        current_load_mw=15000.0,
        season_5th_highest_mw=18000.0,
        load_derivative_mw_per_hour=+100.0,  # rising again
        forecast_peak_today_mw=19500.0,
        now_utc=later,
        state=state,
    )
    assert is_active is True


def test_no_release_when_derivative_negative_but_ratio_above_release():
    """Symmetric: descending but still high (ratio 0.92) -> stay active."""
    triggered_at = datetime(2026, 7, 15, 19, 30, tzinfo=timezone.utc)
    state = FiveCPState(is_active=True, triggered_at_utc=triggered_at,
                         triggered_hour_ct=14)
    later = datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc)
    is_active, _ = evaluate_5cp_risk(
        current_load_mw=16560.0,             # ratio 0.92
        season_5th_highest_mw=18000.0,
        load_derivative_mw_per_hour=-50.0,
        forecast_peak_today_mw=19500.0,
        now_utc=later,
        state=state,
    )
    assert is_active is True


# ---- fetch_zone_live (real-time current-load via pjm.inst_load) -----------


def test_fetch_zone_live_reads_inst_load_with_area_filter():
    """Post-architecture-fix the live-current-load reader queries
    pjm.inst_load (real-time, ~5-min cadence, area=COMED) rather than
    the daily-published-with-multi-day-lag pjm.metered_load. Pin the
    Flux query so the regression that motivated this refactor doesn't
    sneak back in."""
    api = MagicMock()
    api.query.return_value = []
    fetch_zone_live(api, "energy")
    flux = api.query.call_args[0][0]
    assert 'r._measurement == "pjm.inst_load"' in flux
    assert 'r.area == "COMED"' in flux
    # NOT the metered feed:
    assert 'pjm.metered_load' not in flux
    assert 'r.zone' not in flux


def test_fetch_zone_live_returns_none_when_under_two_observations():
    """The detector requires two observations to compute a derivative;
    if the inst_load poller hasn't caught up yet, return None so the
    caller skips the 5CP layer for this tick rather than treating
    absence as zero load."""
    api = MagicMock()
    table = MagicMock()
    rec = MagicMock()
    rec.get_value.return_value = 14000.0
    rec.get_time.return_value = datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc)
    table.records = [rec]  # only one observation
    api.query.return_value = [table]
    out = fetch_zone_live(api, "energy")
    assert out is None


def test_fetch_zone_live_computes_per_hour_derivative():
    """Two observations 5 minutes apart with a 100 MW rise = +1200 MW/hr
    derivative. This is the directional signal the 5CP detector uses
    (positive derivative = load is climbing, eligible to trigger)."""
    api = MagicMock()
    table = MagicMock()
    later = MagicMock()
    later.get_value.return_value = 14100.0
    later.get_time.return_value = datetime(2026, 7, 15, 19, 5, tzinfo=timezone.utc)
    earlier = MagicMock()
    earlier.get_value.return_value = 14000.0
    earlier.get_time.return_value = datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc)
    table.records = [later, earlier]
    api.query.return_value = [table]
    out = fetch_zone_live(api, "energy")
    assert out is not None
    assert out.current_mw == 14100.0
    # 100 MW / (5/60) hour = 1200 MW/hr
    assert round(out.derivative_mw_per_hour) == 1200


# ---- fetch_forecast_peak_for_date (§7 wire-up) ---------------------------


def _fake_query_api_with_responses(*responses):
    """Stub query_api that returns ``responses[i]`` from the i-th call to
    ``.query()``. Each response is a list-of-floats (each float becomes
    one record's _value). ``get_time`` is stubbed to a fixed real UTC
    datetime so the influx_adapter.project_record projection (which now
    fronts the value-query path) does not fail the
    isinstance(time_raw, datetime) check. The revision-tag query
    (_latest_forecast_revision_tag) reads only get_value() so the time
    stub is inert for that call."""
    api = MagicMock()
    side_effect_tables: list = []
    stub_time = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)
    for r in responses:
        if not r:
            side_effect_tables.append([])
            continue
        table = MagicMock()
        records = []
        for v in r:
            rec = MagicMock()
            rec.get_value.return_value = v
            rec.get_time.return_value = stub_time
            records.append(rec)
        table.records = records
        side_effect_tables.append([table])
    api.query.side_effect = side_effect_tables
    return api


def test_fetch_forecast_peak_for_date_returns_max_from_latest_revision():
    """Two-query path (post-P2.5 fix): first query returns the latest
    evaluated_at_iso tag, second query returns the max forecast value
    for that revision."""
    api = _fake_query_api_with_responses(
        ["2026-07-15T13:00:00+00:00"],  # latest revision tag
        [14000.0],                       # max forecast for that revision
    )
    out = fetch_forecast_peak_for_date(api, "energy", "2026-07-15")
    assert out == 14000.0


def test_fetch_forecast_peak_for_date_returns_none_when_no_revisions_exist():
    """Pre-publication of any forecast: the revision-tag query returns
    nothing, so the function short-circuits to None without firing the
    value query."""
    api = _fake_query_api_with_responses([])  # empty revision list
    out = fetch_forecast_peak_for_date(api, "energy", "2026-07-15")
    assert out is None


def test_fetch_forecast_peak_for_date_returns_none_when_revision_has_no_data():
    """Latest revision exists but doesn't cover this target date
    (e.g., target date is beyond the 7-day forecast horizon)."""
    api = _fake_query_api_with_responses(
        ["2026-07-15T13:00:00+00:00"],  # revision exists
        [],                              # but no forecast values for target date
    )
    out = fetch_forecast_peak_for_date(api, "energy", "2026-07-15")
    assert out is None


def test_fetch_forecast_peak_for_date_window_uses_local_tz_day_boundary():
    """The value-query's start/stop bracket the local-tz day, not UTC.
    Pre-P2.5 the analogous fetch_forecast_peak_today used Flux today()
    which is UTC and could query an empty window in evening CT.
    fetch_forecast_peak_for_date always took target_date_iso so it had
    a different bug shape, but verify the tz-aware bounds anyway."""
    api = _fake_query_api_with_responses(
        ["2026-07-15T13:00:00+00:00"],
        [],
    )
    fetch_forecast_peak_for_date(api, "energy", "2026-07-15",
                                  tz=ZoneInfo("America/Chicago"))
    # First call is the revision-tag query, second is the value query.
    second_flux = api.query.call_args_list[1][0][0]
    assert "2026-07-15T05:00:00+00:00" in second_flux  # 00:00 CDT
    assert "2026-07-16T05:00:00+00:00" in second_flux  # 24h later CDT
    assert 'r.evaluated_at_iso == "2026-07-15T13:00:00+00:00"' in second_flux
    assert 'r._measurement == "pjm.load_forecast"' in second_flux


def test_fetch_forecast_peak_for_date_picks_latest_revision_not_max_across_revisions():
    """Regression for the P2.5 bug: pre-fix the query took ``|> max()``
    across all evaluated_at_iso revisions, returning the highest value
    ever published rather than the value from the latest revision. The
    latest-revision-tag selection step ensures stale revision peaks
    don't leak into the §7 trigger decision."""
    # Three revisions exist in the search window; the latest (most
    # recent timestamp string) is the 17:00 one. The 09:00 revision
    # had a higher peak (16000) because PJM later revised down.
    api = MagicMock()
    rev_table = MagicMock()
    rev_records = []
    rec_latest = MagicMock()
    rec_latest.get_value.return_value = "2026-07-15T17:00:00+00:00"
    rev_records.append(rec_latest)
    rev_table.records = rev_records
    value_table = MagicMock()
    rec_value = MagicMock()
    rec_value.get_value.return_value = 13500.0  # revised-down latest value
    # get_time stub for the influx_adapter.project_record projection that
    # now fronts _max_forecast_in_window.
    rec_value.get_time.return_value = datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc)
    value_table.records = [rec_value]
    api.query.side_effect = [[rev_table], [value_table]]
    out = fetch_forecast_peak_for_date(api, "energy", "2026-07-15")
    # Confirms the latest revision's value is returned, not 16000 (which
    # would have come from the older, since-revised revision).
    assert out == 13500.0
    # Sanity: the second query filtered to the latest revision tag.
    second_flux = api.query.call_args_list[1][0][0]
    assert 'r.evaluated_at_iso == "2026-07-15T17:00:00+00:00"' in second_flux


def test_fetch_forecast_peak_today_uses_local_tz_not_utc_today(monkeypatch):
    """P2.5 fix #2: the pre-fix version used Flux today() which is UTC
    midnight start, so evening CT (after 19:00 CDT = 00:00 UTC next day)
    queried tomorrow-UTC and missed today CT data. Verify the new
    implementation builds the window from Chicago today."""
    from .pjm_5cp import fetch_forecast_peak_today

    api = _fake_query_api_with_responses(
        ["2026-07-15T17:00:00+00:00"],
        [],
    )

    class _StubDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # 23:30 CT on 2026-07-15. In UTC that's 04:30 on 2026-07-16
            # (CDT is UTC-5). Pre-fix today() would have queried the
            # wrong day.
            return datetime(2026, 7, 16, 4, 30, tzinfo=timezone.utc).astimezone(tz) if tz else datetime(2026, 7, 15, 23, 30)

    monkeypatch.setattr("hvac_scheduler.pjm_5cp.datetime", _StubDatetime)
    fetch_forecast_peak_today(api, "energy", tz=ZoneInfo("America/Chicago"))
    # Second call is the value query (first is the revision query).
    second_flux = api.query.call_args_list[1][0][0]
    # Chicago-today bounds for 2026-07-15 CDT: 05:00 UTC (15th) to 05:00 UTC (16th).
    assert "2026-07-15T05:00:00+00:00" in second_flux
    assert "2026-07-16T05:00:00+00:00" in second_flux


# ---- Replay: June 24, 2025 ramp-up scenario ------------------------------


def test_replay_ramp_up_into_late_afternoon_peak():
    """Loose replay of a 2025-style spike day: load ramps from 92% of
    season-5th at 12:00 CT through 100% at 17:00, then dips. The detector
    should fire somewhere in the 13-20 CT window when the ratio crosses
    0.95 with positive derivative."""
    season_5th = 18000.0
    forecast_peak = 19000.0
    # Simulate hourly observations 12:00 - 18:00 CT
    hourly_loads = [
        (12, 16400.0,  150.0),  # ratio 0.91 -- below trigger
        (13, 16900.0,  500.0),  # ratio 0.94 -- still below
        (14, 17400.0,  500.0),  # ratio 0.97 -- triggers
        (15, 17850.0,  450.0),
        (16, 18020.0,  170.0),  # ratio 1.001
        (17, 17600.0, -420.0),
        (18, 16500.0, -1100.0),
    ]
    state = FiveCPState()
    fired_at_hour = None
    for hour_ct, load, deriv in hourly_loads:
        # CDT in July: UTC = CT + 5
        now_utc = datetime(2026, 7, 15, hour_ct + 5, 0, tzinfo=timezone.utc)
        is_active, state = evaluate_5cp_risk(
            current_load_mw=load,
            season_5th_highest_mw=season_5th,
            load_derivative_mw_per_hour=deriv,
            forecast_peak_today_mw=forecast_peak,
            now_utc=now_utc,
            state=state,
        )
        if is_active and fired_at_hour is None:
            fired_at_hour = hour_ct
    # Expect the detector to engage at 14:00 CT when ratio first exceeds 0.95
    assert fired_at_hour == 14
    # And to still be active at the 18:00 CT tick because the descent
    # hasn't satisfied BOTH release conditions simultaneously by then
    # (derivative is negative but ratio at 16:00 is still > 0.90).
    assert state.is_active is True


# ---- DetectorScope (P1.1) --------------------------------------------------


def test_comed_scope_constants_are_zone_scaled():
    """COMED_SCOPE must point at ComEd-zone tag values + ComEd-scale
    fallback. This is the invariant that prevents the 2026-05 RTO/ComEd
    scale-confusion bug from sneaking back in."""
    assert COMED_SCOPE.name == "comed_zone"
    assert COMED_SCOPE.inst_load_area == "COMED"
    assert COMED_SCOPE.metered_load_zone == "CE"
    assert COMED_SCOPE.pre_season_fallback_5th_mw == COMED_PRE_SEASON_FALLBACK_5TH_MW
    assert 15000 <= COMED_SCOPE.pre_season_fallback_5th_mw <= 25000


def test_rto_scope_constants_are_rto_scaled():
    """RTO_SCOPE must point at RTO tag values + RTO-scale fallback."""
    assert RTO_SCOPE.name == "rto"
    assert RTO_SCOPE.inst_load_area == "PJM RTO"
    assert RTO_SCOPE.metered_load_zone == "RTO"
    assert RTO_SCOPE.pre_season_fallback_5th_mw == RTO_PRE_SEASON_FALLBACK_5TH_MW
    # RTO summer peak is ~145-165 GW; the fallback should sit inside
    # that band, not at ComEd scale (~20 GW).
    assert 140000 <= RTO_SCOPE.pre_season_fallback_5th_mw <= 170000


def test_rto_pre_season_fallback_value_matches_published_5cp():
    """Locked value: 151,524.7 MW (PJM Summer 2025 5CPs, 5th-highest
    RTO-wide hourly demand). Rounded to 151,525 for one-decimal
    cleanliness."""
    assert RTO_PRE_SEASON_FALLBACK_5TH_MW == 151525.0


def test_scope_dataclass_is_frozen():
    """Scope objects flow through the scheduler; an accidental in-place
    mutation could quietly change detector behavior mid-tick. Frozen
    dataclass makes that a type error at the boundary."""
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        COMED_SCOPE.name = "tampered"  # type: ignore[misc]


# ---- evaluate_for_scope (P1.1) --------------------------------------------


def _scope_evaluation_fixture(*,
                               snapshot: ZoneLoadSnapshot | None,
                               season_5th_mw: float,
                               forecast_peak: float | None,
                               state: FiveCPState = FiveCPState(),
                               now_utc: datetime = T_AT_1430_CT,
                               scope: DetectorScope = COMED_SCOPE,
                               monkeypatch=None) -> ScopeEvaluation:
    """Common harness for evaluate_for_scope tests. Stubs the two IO
    helpers inside pjm_5cp so the scope-aware code path is exercised
    end-to-end without spinning up Flux."""
    from . import pjm_5cp
    monkeypatch.setattr(pjm_5cp, "fetch_zone_live",
                        lambda q, b, *, area: snapshot)
    monkeypatch.setattr(pjm_5cp, "update_season_5th_highest",
                        lambda q, b, s, e, *, zone, fallback_mw: season_5th_mw)
    season_start = datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc)
    season_end = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)
    return evaluate_for_scope(
        scope, MagicMock(), "energy",
        season_start, season_end,
        forecast_peak, state, now_utc,
    )


def test_evaluate_for_scope_log_fields_always_include_scope_metadata(monkeypatch):
    """Every evaluation logs scope+area+zone+fallback so a regression on
    scale mixing (RTO fallback misapplied to ComEd path or vice versa)
    shows up immediately in logs, not silently in behavior. This is the
    structured-log tripwire Chris asked for in the P1.1 sign-off."""
    ev = _scope_evaluation_fixture(
        snapshot=ZoneLoadSnapshot(15000.0, 100.0, T_AT_1430_CT),
        season_5th_mw=20375.0, forecast_peak=22000.0,
        scope=COMED_SCOPE, monkeypatch=monkeypatch,
    )
    assert ev.log_fields["scope"] == "comed_zone"
    assert ev.log_fields["area"] == "COMED"
    assert ev.log_fields["zone"] == "CE"
    assert ev.log_fields["fallback_5th_mw"] == COMED_PRE_SEASON_FALLBACK_5TH_MW
    assert ev.log_fields["season_5th_mw"] == 20375.0
    assert ev.log_fields["data_status"] == "ok"


def test_evaluate_for_scope_rto_log_fields_carry_rto_metadata(monkeypatch):
    """Same test for RTO_SCOPE -- confirms the scope is genuinely the
    thing driving log emission, not a hardcoded ComEd shape."""
    ev = _scope_evaluation_fixture(
        snapshot=ZoneLoadSnapshot(145000.0, 500.0, T_AT_1430_CT),
        season_5th_mw=151525.0, forecast_peak=155000.0,
        scope=RTO_SCOPE, monkeypatch=monkeypatch,
    )
    assert ev.log_fields["scope"] == "rto"
    assert ev.log_fields["area"] == "PJM RTO"
    assert ev.log_fields["zone"] == "RTO"
    assert ev.log_fields["fallback_5th_mw"] == RTO_PRE_SEASON_FALLBACK_5TH_MW


def test_evaluate_for_scope_carries_state_on_missing_snapshot(monkeypatch):
    """When pjm.inst_load returns no rows (cold-start container or feed
    outage) AND the active hold is still inside its 30-min tail window,
    the evaluator carries the prior state.is_active rather than treating
    missing data as zero load (which would falsely release a triggered
    scope mid-event). The trigger was at T-10min; hold_end =
    end-of-hour + 30min, so we're well inside the hold window."""
    prior_state = FiveCPState(
        is_active=True,
        triggered_at_utc=T_AT_1430_CT - timedelta(minutes=10),
        triggered_hour_ct=14,
    )
    ev = _scope_evaluation_fixture(
        snapshot=None,
        season_5th_mw=20375.0, forecast_peak=22000.0,
        state=prior_state, monkeypatch=monkeypatch,
    )
    assert ev.is_active is True
    assert ev.new_state == prior_state
    assert ev.log_fields["data_status"] == "no_snapshot"


def test_evaluate_for_scope_forces_release_when_hold_elapsed_without_data(monkeypatch):
    """P1.4 (reviewer-flagged 2026-05-11): if data has disappeared AND
    the hold-end is already in the past, force release rather than
    pin the shutoff layer indefinitely.

    Without this, a feed/Influx outage that begins shortly after a
    trigger would hold the §3 layer at its 85F shutoff setpoint for
    the rest of the day -- the carry-state branch had no exit. With
    the force-release: past hold-end, missing data releases; the
    detector will re-trigger on the next tick if data returns and
    conditions warrant.

    Setup: trigger fired at 13:00 CT, hold_end = 14:30 UTC (end of
    13:00 EPT hour, which is 18:00 UTC, +30min = 18:30 UTC). 'Now' is
    20:00 UTC (15:00 CT, 90+ min past hold-end). Data is missing.
    Expected: forced release."""
    trigger_at = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)  # 13:00 CT
    prior_state = FiveCPState(
        is_active=True,
        triggered_at_utc=trigger_at,
        triggered_hour_ct=13,
    )
    well_past_hold_end = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)  # 15:00 CT
    ev = _scope_evaluation_fixture(
        snapshot=None,  # data missing
        season_5th_mw=20375.0, forecast_peak=22000.0,
        state=prior_state, now_utc=well_past_hold_end,
        monkeypatch=monkeypatch,
    )
    assert ev.is_active is False
    assert ev.new_state == FiveCPState()   # reset, not the prior active hold
    assert ev.log_fields["data_status"] == "no_snapshot"
    assert ev.log_fields.get("forced_release") == "hold_elapsed_without_data"


def test_evaluate_for_scope_forces_release_on_missing_forecast_past_hold_end(monkeypatch):
    """Symmetric for the missing-forecast-peak case. Same release rule
    applies regardless of which data side dropped out."""
    trigger_at = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
    prior_state = FiveCPState(
        is_active=True,
        triggered_at_utc=trigger_at,
        triggered_hour_ct=13,
    )
    well_past_hold_end = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
    ev = _scope_evaluation_fixture(
        snapshot=ZoneLoadSnapshot(15000.0, 100.0, well_past_hold_end),
        season_5th_mw=20375.0,
        forecast_peak=None,  # forecast missing
        state=prior_state, now_utc=well_past_hold_end,
        monkeypatch=monkeypatch,
    )
    assert ev.is_active is False
    assert ev.log_fields["data_status"] == "no_forecast_peak"
    assert ev.log_fields.get("forced_release") == "hold_elapsed_without_data"


def test_evaluate_for_scope_does_not_force_release_inside_hold_window(monkeypatch):
    """The force-release only kicks in PAST hold-end. Inside the hold
    window, missing data still carries the prior active state -- the
    detector's design intent is that within hold the layer stays
    active regardless of load conditions, and that semantic must
    survive a brief data gap."""
    trigger_at = T_AT_1430_CT - timedelta(minutes=5)  # 14:25 UTC
    prior_state = FiveCPState(
        is_active=True,
        triggered_at_utc=trigger_at,
        triggered_hour_ct=14,
    )
    # T_AT_1430_CT = 14:30 UTC; hold_end = 15:30 UTC; we're at 14:30 UTC,
    # well inside the hold window.
    ev = _scope_evaluation_fixture(
        snapshot=None,
        season_5th_mw=20375.0, forecast_peak=22000.0,
        state=prior_state, monkeypatch=monkeypatch,
    )
    assert ev.is_active is True
    assert ev.new_state == prior_state
    assert "forced_release" not in ev.log_fields


def test_evaluate_for_scope_missing_data_does_not_force_release_when_inactive(monkeypatch):
    """If the prior state is inactive, missing data is a no-op: the
    evaluator stays inactive. The force-release only applies to an
    is_active state past its hold-end."""
    inactive_state = FiveCPState()
    ev = _scope_evaluation_fixture(
        snapshot=None,
        season_5th_mw=20375.0, forecast_peak=22000.0,
        state=inactive_state, monkeypatch=monkeypatch,
    )
    assert ev.is_active is False
    assert "forced_release" not in ev.log_fields


def test_evaluate_for_scope_carries_state_on_missing_forecast_peak(monkeypatch):
    """Same carry-state behavior when the forecast peak is None (PJM's
    forecast revision for today hasn't published yet)."""
    prior_state = FiveCPState(
        is_active=True,
        triggered_at_utc=T_AT_1430_CT - timedelta(minutes=10),
        triggered_hour_ct=14,
    )
    ev = _scope_evaluation_fixture(
        snapshot=ZoneLoadSnapshot(15000.0, 100.0, T_AT_1430_CT),
        season_5th_mw=20375.0, forecast_peak=None,
        state=prior_state, monkeypatch=monkeypatch,
    )
    assert ev.is_active is True
    assert ev.log_fields["data_status"] == "no_forecast_peak"


def test_evaluate_for_scope_triggers_when_all_inputs_say_go(monkeypatch):
    """Happy path: hot afternoon, current load 96% of season-5th,
    derivative positive, forecast > season-5th, inside 13-20 CT window.
    Detector enters active state."""
    ev = _scope_evaluation_fixture(
        # 96% of 20375 = 19560; pick 19600 to clear the trigger band
        snapshot=ZoneLoadSnapshot(19600.0, 200.0, T_AT_1430_CT),
        season_5th_mw=20375.0, forecast_peak=21000.0,
        scope=COMED_SCOPE, monkeypatch=monkeypatch,
    )
    assert ev.is_active is True
    assert ev.new_state.is_active is True
    assert ev.log_fields["load_ratio"] > LOAD_RATIO_TRIGGER


# ---- Cooling-season window (PJM Manual 19 / ComEd Att. M-2) --------------


@pytest.mark.parametrize("month,day,expected_in_season", [
    (5, 31, False),  # day before season starts
    (6,  1, True),   # season begins
    (6, 15, True),   # mid-June
    (7,  4, True),   # mid-summer
    (8, 31, True),   # peak summer
    (9, 30, True),   # last day of season
    (10, 1, False),  # day after season ends
    (11, 15, False), # late fall
    (1,  1, False),  # winter
    (3, 15, False),  # spring
])
def test_in_cooling_season_boundaries(month, day, expected_in_season):
    """Per PJM Manual 19 / ComEd Att. M-2: cooling season is
    inclusive Jun 1 - Sep 30. Boundary days must be classified
    correctly so the detector doesn't trigger on May 31 or Oct 1."""
    now_local = datetime(2026, month, day, 14, 0, tzinfo=CHICAGO)
    assert in_cooling_season(now_local) is expected_in_season


def test_cooling_season_window_in_season_uses_current_year():
    """In June-September, the relevant window is the current year's
    Jun 1 00:00 CT -> Sep 30 23:59 CT."""
    now = datetime(2026, 7, 15, 14, 0, tzinfo=CHICAGO)
    start, end = cooling_season_window_utc(now)
    start_local = start.astimezone(CHICAGO)
    end_local = end.astimezone(CHICAGO)
    assert (start_local.year, start_local.month, start_local.day) == (2026, 6, 1)
    assert (end_local.year, end_local.month, end_local.day) == (2026, 9, 30)


def test_cooling_season_window_off_season_october_to_december_uses_current_year():
    """Oct-Dec: the season just completed; window is current year's
    Jun 1 -> Sep 30. This is what off-season §7 deepening references
    when looking up the historical 5th-highest baseline."""
    now = datetime(2026, 11, 15, 14, 0, tzinfo=CHICAGO)
    start, end = cooling_season_window_utc(now)
    start_local = start.astimezone(CHICAGO)
    end_local = end.astimezone(CHICAGO)
    assert (start_local.year, start_local.month, start_local.day) == (2026, 6, 1)
    assert (end_local.year, end_local.month, end_local.day) == (2026, 9, 30)


def test_cooling_season_window_off_season_january_to_may_uses_previous_year():
    """Jan-May: the current year's season hasn't started yet. The
    window must point at the *previous* year's Jun-Sep. This is the
    fix for the 2026-05-11 production incident: sparse RTO ingest of
    May 2026 rows produced a 90,244 MW "season 5th" that real RTO
    load almost cleared (ratio 0.946). Forcing the window to 2025
    Jun-Sep keeps off-season rows out of the baseline."""
    now = datetime(2026, 5, 11, 14, 0, tzinfo=CHICAGO)
    start, end = cooling_season_window_utc(now)
    start_local = start.astimezone(CHICAGO)
    end_local = end.astimezone(CHICAGO)
    assert (start_local.year, start_local.month, start_local.day) == (2025, 6, 1)
    assert (end_local.year, end_local.month, end_local.day) == (2025, 9, 30)


def test_evaluate_for_scope_skips_off_season(monkeypatch):
    """Off-season ticks short-circuit: no Flux is issued, the state
    is reset (a hold can't cross Sep 30 -> Oct 1), and is_active
    returns False. log_fields["data_status"] surfaces the skip so
    the audit can confirm the detector intentionally stayed inert.

    This is the safety guard against the 2026-05-11 incident: even
    if InfluxDB had bogus May 2026 RTO rows, the detector cannot
    fire during off-season."""
    from . import pjm_5cp

    # Spies: we should NEVER reach the Flux helpers during off-season.
    called: dict[str, bool] = {"fetch_zone_live": False, "update_season": False}
    def _spy_fetch_zone_live(q, b, *, area):
        called["fetch_zone_live"] = True
        return ZoneLoadSnapshot(99999.0, 0.0, T_AT_1430_CT)
    def _spy_update_season(q, b, s, e, *, zone, fallback_mw):
        called["update_season"] = True
        return 90244.0  # the bogus 2026-05-11 value
    monkeypatch.setattr(pjm_5cp, "fetch_zone_live", _spy_fetch_zone_live)
    monkeypatch.setattr(pjm_5cp, "update_season_5th_highest", _spy_update_season)

    # 2026-05-11 14:30 CT = May, off-season. Even if a prior-state
    # active hold existed, it must reset on off-season transition.
    may_now_utc = datetime(2026, 5, 11, 19, 30, tzinfo=timezone.utc)
    prior_state = FiveCPState(
        is_active=True,
        triggered_at_utc=datetime(2026, 5, 11, 19, 20, tzinfo=timezone.utc),
        triggered_hour_ct=14,
    )
    ev = evaluate_for_scope(
        RTO_SCOPE, MagicMock(), "energy",
        datetime(2025, 6, 1, 5, 0, tzinfo=timezone.utc),
        datetime(2025, 10, 1, 5, 0, tzinfo=timezone.utc),
        160000.0,  # would normally trigger
        prior_state,
        may_now_utc,
    )
    assert ev.is_active is False
    assert ev.new_state == FiveCPState()  # reset, not the prior active hold
    assert ev.log_fields["data_status"] == "off_season"
    assert ev.log_fields["season_5th_mw"] == RTO_PRE_SEASON_FALLBACK_5TH_MW
    assert called["fetch_zone_live"] is False  # no Flux issued
    assert called["update_season"] is False    # no Flux issued


def test_evaluate_for_scope_rto_fires_from_rto_forecast_alone(monkeypatch):
    """Chris-requested invariant: RTO scope must be able to trigger
    on RTO inputs alone. Set up: RTO ratio 0.96 (above 0.95 trigger),
    RTO forecast 160 GW > RTO season-5th 151.525 GW, in the 13-20 CT
    window. ComEd has no data at all. RTO scope fires.

    This is the test that would have failed pre-fix because the RTO
    scope was receiving the ComEd forecast (~9 GW), failing the
    forecast > season-5th gate regardless of load ratio."""
    ev = _scope_evaluation_fixture(
        snapshot=ZoneLoadSnapshot(145500.0, 800.0, T_AT_1430_CT),
        season_5th_mw=151525.0,
        forecast_peak=160000.0,  # RTO-scale forecast (the fix)
        scope=RTO_SCOPE, monkeypatch=monkeypatch,
    )
    assert ev.is_active is True
    assert ev.new_state.is_active is True
    assert ev.log_fields["forecast_peak_today_mw"] == 160000.0


def test_evaluate_for_scope_rto_does_not_fire_on_comed_scale_forecast(monkeypatch):
    """Chris-requested invariant: a ComEd-scale forecast (~9 GW) MUST
    NOT satisfy the RTO scope's forecast > season-5th gate. This is
    the failure mode that the per-scope forecast wiring fixes -- the
    original P1.1 passed one shared ``forecast_peak`` to both scopes
    so the RTO scope was being handed a ComEd-scale number that could
    never exceed an RTO-scale baseline."""
    ev = _scope_evaluation_fixture(
        snapshot=ZoneLoadSnapshot(145500.0, 800.0, T_AT_1430_CT),  # RTO ratio 0.96
        season_5th_mw=151525.0,
        forecast_peak=9110.0,  # ComEd-scale "leakage" -- must not trigger RTO
        scope=RTO_SCOPE, monkeypatch=monkeypatch,
    )
    assert ev.is_active is False  # gate failure -- forecast_peak < season_5th
    assert ev.new_state.is_active is False




# ---- P2: metered-load season-5th must dedup by hour -----------------------


def _mock_metered_query_api(records: list[float]):
    """Build a query_api mock whose ``.query(flux)`` captures the Flux
    string and returns one table of ``mw`` records. The fixed
    ``|> group()`` pipeline produces a single flattened table after
    aggregateWindow + sort + limit; this mock matches that shape."""
    mock = MagicMock()
    mock.last_flux = None

    def _query(flux):
        mock.last_flux = flux

        class _Rec:
            def __init__(self, v):
                self._v = v

            def get_value(self):
                return self._v

            def get_time(self):
                return None

        class _Table:
            def __init__(self, recs):
                self.records = [_Rec(r) for r in recs]

        return [_Table(records)]

    mock.query = _query
    return mock


def test_update_season_5th_flux_query_flattens_is_verified_with_group():
    """P2 regression guard: ``pjm.metered_load`` carries ``is_verified``
    as a tag, so when PJM publishes a row (is_verified=false) and
    later corrects it (is_verified=true), the bucket holds two
    series at the same ``_time``. Without ``|> group()`` to flatten
    them, ``aggregateWindow`` and ``sort/limit`` operate per-series
    and a single physical hour can fill multiple top-5 slots, skewing
    the season-5th value upward.

    Post-fix: the Flux MUST contain ``|> group()`` to collapse the
    is_verified-keyed series before aggregating."""
    q = _mock_metered_query_api([21000.0, 22000.0, 23000.0, 24000.0, 25000.0])
    update_season_5th_highest(
        q, "energy",
        datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 1, 5, 0, tzinfo=timezone.utc),
        zone="CE",
        fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
    )
    assert "|> group()" in q.last_flux


def test_update_season_5th_flux_group_appears_before_aggregate_window():
    """Order matters: ``group()`` must appear BEFORE
    ``aggregateWindow`` so the flatten removes the is_verified tag
    from the group key. If group() came after, the per-series
    aggregation would already have produced the duplicate-hour rows
    and a late group() couldn't collapse them back."""
    q = _mock_metered_query_api([21000.0, 22000.0, 23000.0, 24000.0, 25000.0])
    update_season_5th_highest(
        q, "energy",
        datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 1, 5, 0, tzinfo=timezone.utc),
        zone="CE",
        fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
    )
    flux = q.last_flux
    group_pos = flux.index("|> group()")
    agg_pos = flux.index("|> aggregateWindow")
    assert group_pos < agg_pos


def test_update_season_5th_flux_uses_max_to_prefer_verified():
    """P2.1 regression guard: when PJM corrects a row (is_verified=
    false -> is_verified=true), the bucket holds two values at the
    same hour. ``aggregateWindow(fn: mean)`` averages them and
    produces a value lower than the verified actual, biasing the
    season-5th baseline downward.

    ``fn: max`` selects the higher of the two -- which is almost
    always the verified value (PJM's initial unverified publish is
    conservative, corrections adjust slightly upward). When only
    one row exists for an hour (unverified-only or verified-only),
    max returns that single value unchanged."""
    q = _mock_metered_query_api([21000.0, 22000.0, 23000.0, 24000.0, 25000.0])
    update_season_5th_highest(
        q, "energy",
        datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 1, 5, 0, tzinfo=timezone.utc),
        zone="CE",
        fallback_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
    )
    flux = q.last_flux
    assert "fn: max" in flux
    assert "fn: mean" not in flux
