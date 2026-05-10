"""PJM 5CP-eligibility detector (Arm B layer 3).

The five highest hourly ComEd-zone loads of the cooling season set the
following year's residential capacity charge. The detector predicts when
the current hour is likely a 5CP-eligible hour and triggers an aggressive
shutoff (cool setpoint = 85F) for the duration of the elevated-load
window plus a 30-minute trailing hold.

Triggers (all must be true to enter the active state):

  * ``current_load_mw / season_to_date_5th_highest_mw > 0.95``
    Allows for prediction error and catches the load ramp-up on its way
    to a new 5CP-eligible peak. The 0.95 ratio comes from EXPERIMENT_DESIGN
    Appendix A and is locked.

  * ``load_derivative_mw_per_hour > 0``
    Load is still rising. A descending load that's already past its peak
    won't set a new 5CP, so the detector waits for the ramp-up edge.

  * ``13:00 CT <= hour_of_day < 20:00 CT``
    Broadened from the historical 14-18 CT window to capture 2025-style
    late-peak behaviour (the 2025 RTP peak hour was 18:00 CT, not 16:00).

  * ``forecast_peak_today_mw > season_to_date_5th_highest_mw``
    PJM's published peak forecast must indicate today's projected peak
    exceeds the season-to-date 5th highest. Without this, current load
    might be momentarily high but not on track to set a new 5CP.

Hold semantics:
  * After triggering, hold until end-of-current-hour + 30 minutes.
  * Once the hold elapses, release only when both
      ``current_load_mw / season_to_date_5th_highest_mw < 0.90`` AND
      ``load_derivative_mw_per_hour < 0``
    This prevents brief load dips during a sustained peak from exiting
    the active state prematurely.

Modeled on the joe248 AppDaemon 5CP-prediction implementation
(community.home-assistant.io/t/hacking-your-comed-electricity-bill/111494).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


# ---- Locked thresholds (EXPERIMENT_DESIGN Appendix A) ---------------------


LOAD_RATIO_TRIGGER = 0.95
LOAD_RATIO_RELEASE = 0.90
WINDOW_START_CT = dtime(13, 0)
WINDOW_END_CT = dtime(20, 0)
COOL_SHUTOFF_F = 85
HOLD_TAIL_MINUTES = 30
# Used when fewer than 5 hourly observations have accumulated in the season
# (pre-season cold start). 130,000 MW is the rough ComEd zone summer-peak
# threshold below which 5CP-eligibility is essentially impossible.
PRE_SEASON_FALLBACK_5TH_MW = 130000.0
MIN_OBSERVATIONS_FOR_5TH = 5

CHICAGO = ZoneInfo("America/Chicago")


# ---- State machine ---------------------------------------------------------


@dataclass(frozen=True)
class FiveCPState:
    """Immutable per-tick state for the 5CP detector."""
    is_active: bool = False
    triggered_at_utc: Optional[datetime] = None
    triggered_hour_ct: Optional[int] = None


def hold_end_time(triggered_at_utc: datetime) -> datetime:
    """Compute the earliest UTC moment at which the hold could be released.

    Defined as the end of the (CT-local) hour the trigger fired, plus 30
    minutes. This protects against brief load dips during a sustained peak
    pulling the AC back on inside the same hour as the trigger."""
    end_of_hour_utc = triggered_at_utc.replace(
        minute=0, second=0, microsecond=0
    ) + timedelta(hours=1)
    return end_of_hour_utc + timedelta(minutes=HOLD_TAIL_MINUTES)


def evaluate_5cp_risk(
    current_load_mw: float,
    season_5th_highest_mw: float,
    load_derivative_mw_per_hour: float,
    forecast_peak_today_mw: float,
    now_utc: datetime,
    state: FiveCPState,
    *,
    window_tz: ZoneInfo = CHICAGO,
    window_start_ct: dtime = WINDOW_START_CT,
    window_end_ct: dtime = WINDOW_END_CT,
    load_ratio_trigger: float = LOAD_RATIO_TRIGGER,
    load_ratio_release: float = LOAD_RATIO_RELEASE,
) -> tuple[bool, FiveCPState]:
    """Decide whether the 5CP-shutoff layer is active this tick.

    Returns ``(is_active, new_state)``. ``is_active=True`` is the signal
    the §4 layer-priority resolver consumes to clamp the effective cool
    setpoint at COOL_SHUTOFF_F.

    Implementation walks two paths:

      * **Already active**: stay active until both the hold has elapsed
        AND release conditions (low ratio AND descending load) hold
        simultaneously.

      * **Currently inactive**: trigger only when the four entry
        conditions in the module docstring are all true.
    """
    now_local = now_utc.astimezone(window_tz)

    if state.is_active:
        # Already active: the only question is whether to release.
        if state.triggered_at_utc is None:
            # Defensive: corrupted state. Treat as just-triggered to avoid
            # accidentally releasing too early.
            return True, state
        if now_utc < hold_end_time(state.triggered_at_utc):
            # Inside the hold window. Stay active regardless of price/load.
            return True, state
        # Hold elapsed: check release.
        ratio = (current_load_mw / season_5th_highest_mw) if season_5th_highest_mw > 0 else 0.0
        if ratio < load_ratio_release and load_derivative_mw_per_hour < 0:
            return False, FiveCPState()  # back to default inactive
        # Hold elapsed but conditions still elevated: stay active.
        return True, state

    # Currently inactive: evaluate trigger conditions.
    in_window = window_start_ct <= now_local.time() < window_end_ct
    if not in_window:
        return False, state
    if season_5th_highest_mw <= 0:
        return False, state

    ratio = current_load_mw / season_5th_highest_mw
    if ratio <= load_ratio_trigger:
        return False, state
    if load_derivative_mw_per_hour <= 0:
        return False, state
    if forecast_peak_today_mw <= season_5th_highest_mw:
        return False, state

    # All four triggers fired.
    new_state = FiveCPState(
        is_active=True,
        triggered_at_utc=now_utc,
        triggered_hour_ct=now_local.hour,
    )
    return True, new_state


# ---- Season 5th-highest computation ---------------------------------------


def season_5th_highest_from_loads(loads_mw: list[float]) -> float:
    """Pure function: given a list of hourly average loads (any order),
    return the 5th-highest value. Falls back to PRE_SEASON_FALLBACK_5TH_MW
    when fewer than MIN_OBSERVATIONS_FOR_5TH observations are supplied."""
    if len(loads_mw) < MIN_OBSERVATIONS_FOR_5TH:
        return PRE_SEASON_FALLBACK_5TH_MW
    sorted_desc = sorted(loads_mw, reverse=True)
    return float(sorted_desc[MIN_OBSERVATIONS_FOR_5TH - 1])


def update_season_5th_highest(query_api, bucket: str,
                              season_start_utc: datetime,
                              *, zone: str = "CE") -> float:
    """Query InfluxDB for hourly-average ``pjm.metered_load`` values since
    ``season_start_utc`` and return the 5th-highest. Falls back to
    PRE_SEASON_FALLBACK_5TH_MW when fewer than 5 hourly observations
    exist (pre-season cold start)."""
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: {season_start_utc.isoformat()})
          |> filter(fn: (r) => r._measurement == "pjm.metered_load"
                                and r.zone == "{zone}"
                                and r._field == "mw")
          |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
          |> sort(columns: ["_value"], desc: true)
          |> limit(n: {MIN_OBSERVATIONS_FOR_5TH})
    """
    loads: list[float] = []
    for table in query_api.query(flux):
        for record in table.records:
            v = record.get_value()
            if v is not None:
                loads.append(float(v))
    return season_5th_highest_from_loads(loads)


# ---- Live load + derivative ------------------------------------------------


@dataclass(frozen=True)
class ZoneLoadSnapshot:
    """Most recent hourly load + a simple derivative computed against the
    immediately prior hourly observation."""
    current_mw: float
    derivative_mw_per_hour: float
    observed_at_utc: datetime


def fetch_zone_live(query_api, bucket: str, *, zone: str = "CE") -> Optional[ZoneLoadSnapshot]:
    """Pull the two most recent hourly metered-load points for the given
    zone and compute a simple discrete derivative.

    Returns None when fewer than two observations exist (so the caller can
    skip the 5CP check rather than treat the absence as zero load)."""
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: -6h)
          |> filter(fn: (r) => r._measurement == "pjm.metered_load"
                                and r.zone == "{zone}"
                                and r._field == "mw")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: 2)
    """
    rows: list[tuple[datetime, float]] = []
    for table in query_api.query(flux):
        for record in table.records:
            v = record.get_value()
            t = record.get_time()
            if v is not None and t is not None:
                rows.append((t, float(v)))
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r[0], reverse=True)
    (latest_t, latest_mw), (prev_t, prev_mw) = rows[0], rows[1]
    hours = (latest_t - prev_t).total_seconds() / 3600.0
    derivative = (latest_mw - prev_mw) / hours if hours > 0 else 0.0
    return ZoneLoadSnapshot(
        current_mw=latest_mw,
        derivative_mw_per_hour=derivative,
        observed_at_utc=latest_t,
    )


def fetch_forecast_peak_today(query_api, bucket: str,
                               *, forecast_area: str = "COMED") -> Optional[float]:
    """Pull the most recently published per-hour forecast for today and
    return the maximum forecast_load_mw. Returns None when no forecast
    rows for today exist yet."""
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: today())
          |> filter(fn: (r) => r._measurement == "pjm.load_forecast"
                                and r.forecast_area == "{forecast_area}"
                                and r._field == "forecast_load_mw")
          |> max()
    """
    for table in query_api.query(flux):
        for record in table.records:
            v = record.get_value()
            if v is not None:
                return float(v)
    return None
