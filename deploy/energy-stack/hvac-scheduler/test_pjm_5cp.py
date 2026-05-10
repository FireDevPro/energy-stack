"""Tests for the PJM 5CP-eligibility detector (§3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from pjm_5cp import (
    COOL_SHUTOFF_F,
    LOAD_RATIO_RELEASE,
    LOAD_RATIO_TRIGGER,
    MIN_OBSERVATIONS_FOR_5TH,
    PRE_SEASON_FALLBACK_5TH_MW,
    FiveCPState,
    evaluate_5cp_risk,
    fetch_forecast_peak_for_date,
    fetch_zone_live,
    hold_end_time,
    season_5th_highest_from_loads,
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
    assert PRE_SEASON_FALLBACK_5TH_MW == 130000.0


# ---- season_5th_highest_from_loads ---------------------------------------


def test_pre_season_fallback_when_under_5_observations():
    """First 4 hourly observations of the season -- the detector still
    needs a comparison value, so we fall back to a hardcoded zone-summer
    threshold rather than letting current_load / 0 explode."""
    assert season_5th_highest_from_loads([]) == PRE_SEASON_FALLBACK_5TH_MW
    assert season_5th_highest_from_loads([100, 200, 300, 400]) == PRE_SEASON_FALLBACK_5TH_MW


def test_5th_highest_from_exactly_5_observations():
    """At exactly 5 observations the smallest one is the 5th highest."""
    assert season_5th_highest_from_loads([100, 200, 300, 400, 500]) == 100


def test_5th_highest_picks_correct_index_in_unsorted_input():
    """Sort happens internally; caller doesn't need to sort first."""
    loads = [12000, 14500, 11000, 13200, 14000, 13800, 14300]  # 7 hours
    # Sorted desc: 14500, 14300, 14000, 13800, 13200, 12000, 11000
    # 5th highest (index 4) = 13200
    assert season_5th_highest_from_loads(loads) == 13200


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


def _fake_query_api(records: list[float]):
    """Minimal Flux query_api stub: returns one table whose records each
    yield one of the supplied float values from .get_value()."""
    api = MagicMock()
    table = MagicMock()
    table.records = []
    for v in records:
        record = MagicMock()
        record.get_value.return_value = v
        table.records.append(record)
    api.query.return_value = [table] if records else []
    return api


def test_fetch_forecast_peak_for_date_returns_max_value():
    """When the Flux query returns rows, pick the max — the Flux query
    has |> max() so practically there's one row, but the parser tolerates
    multiple."""
    api = _fake_query_api([14000.0])
    out = fetch_forecast_peak_for_date(api, "energy", "2026-07-15")
    assert out == 14000.0


def test_fetch_forecast_peak_for_date_returns_none_when_empty():
    """Pre-publication of tomorrow's forecast: query returns nothing."""
    api = _fake_query_api([])
    out = fetch_forecast_peak_for_date(api, "energy", "2026-07-15")
    assert out is None


def test_fetch_forecast_peak_for_date_window_uses_local_tz_day_boundary():
    """The Flux query's start/stop bracket the local-tz day. Verify by
    inspecting the rendered query text — start_utc should be the local
    midnight in UTC, stop_utc should be 24h later."""
    api = MagicMock()
    api.query.return_value = []
    fetch_forecast_peak_for_date(api, "energy", "2026-07-15",
                                  tz=ZoneInfo("America/Chicago"))
    flux = api.query.call_args[0][0]
    # 2026-07-15 00:00 CDT = 2026-07-15 05:00 UTC
    assert "2026-07-15T05:00:00+00:00" in flux
    # 2026-07-16 00:00 CDT = 2026-07-16 05:00 UTC
    assert "2026-07-16T05:00:00+00:00" in flux
    assert 'r._measurement == "pjm.load_forecast"' in flux
    assert 'r.forecast_area == "COMED"' in flux


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
