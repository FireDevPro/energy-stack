"""PJM 5CP-eligibility detector (Arm B layer 3).

The residential capacity-charge dollar exposure depends on TWO separate
sets of 5 coincident peak hours per cooling season (see HVAC_LOGIC.md
"Capacity peak context"):

  * **PJM 5CP**   -- 5 highest RTO-wide hourly loads.
  * **ComEd 5CP** -- 5 highest ComEd-zone hourly loads.

ComEd's zone peaks can land earlier in the afternoon than the RTO peaks
because metro-Chicago load shape differs from the broader RTO. Each kW
shaved during *any* 5CP hour saves roughly the same dollar amount, so
the detector triggers aggressive shutoff (cool setpoint = 85F) on
likely-eligible hours from EITHER set. The scheduler runs the detector
twice (once per scope) and ORs the triggers; this module is the
per-scope state machine.

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

Two-feed data lineage (refined post-deploy when PJM's official OpenAPI
spec confirmed the metered feed has multi-day publish lag):

  * ``pjm.inst_load`` (area depends on scope, ~5-min cadence,
    "approximate, NOT official PJM Loads") feeds ``current_load_mw``
    and the load derivative -- the real-time directional signal.
  * ``pjm.metered_load`` (zone depends on scope, daily publish with up
    to 90-day correction window, official metered values) feeds the
    season-to-date 5th-highest baseline -- the values that ultimately
    determine 5CP rank.

Both feeds are needed per scope; one without the other doesn't deliver
the detector's locked rule (current/baseline > 0.95).
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

# Pre-season cold-start fallback for the season-to-date 5th-highest hourly
# load. Used only when fewer than ``MIN_OBSERVATIONS_FOR_5TH`` hourly
# observations exist in InfluxDB for the current cooling season. Once the
# metered feed publishes enough hours (typically 3-5 days into June given
# the 1-2 day publish lag), this value is unused.
#
# Value source: 2025 ComEd-zone 5th-highest hourly metered load.
# Empirically pulled 2026-05-10 from ``pjm.metered_load{zone=CE}`` over the
# 2025 cooling season (Jun-Sep): 5th-highest = 20,375.4 MW (2025-07-23
# 18:00 CT). Rounded down to 20,375 so a real ComEd-zone hot day with
# current load >= 19,357 MW (95% of 20,375) trips the detector pre-data.
#
# Prior value of 130,000 MW was RTO-scale (130 GW) misapplied to the
# ComEd-zone path, leaving the detector inert pre-season because ComEd's
# annual peak (20.7 GW) couldn't reach 95% of a 130 GW baseline. Fixed
# 2026-05 ahead of randomization start.
COMED_PRE_SEASON_FALLBACK_5TH_MW = 20375.0
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


def season_5th_highest_from_loads(loads_mw: list[float], *,
                                   fallback_mw: float) -> float:
    """Pure function: given a list of hourly average loads (any order),
    return the 5th-highest value. Falls back to ``fallback_mw`` when
    fewer than ``MIN_OBSERVATIONS_FOR_5TH`` observations are supplied.

    ``fallback_mw`` must be scoped to the detector (ComEd-zone vs
    PJM RTO) -- mixing scales here is the bug that left the ComEd
    detector inert pre-season before 2026-05.
    """
    if len(loads_mw) < MIN_OBSERVATIONS_FOR_5TH:
        return fallback_mw
    sorted_desc = sorted(loads_mw, reverse=True)
    return float(sorted_desc[MIN_OBSERVATIONS_FOR_5TH - 1])


def update_season_5th_highest(query_api, bucket: str,
                              season_start_utc: datetime,
                              *, zone: str = "CE",
                              fallback_mw: float = COMED_PRE_SEASON_FALLBACK_5TH_MW
                              ) -> float:
    """Query InfluxDB for hourly-average ``pjm.metered_load`` values since
    ``season_start_utc`` and return the 5th-highest. Falls back to
    ``fallback_mw`` (scoped to the requested ``zone``) when fewer than
    ``MIN_OBSERVATIONS_FOR_5TH`` hourly observations exist (pre-season
    cold start)."""
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
    return season_5th_highest_from_loads(loads, fallback_mw=fallback_mw)


# ---- Live load + derivative ------------------------------------------------


@dataclass(frozen=True)
class ZoneLoadSnapshot:
    """Most recent hourly load + a simple derivative computed against the
    immediately prior hourly observation."""
    current_mw: float
    derivative_mw_per_hour: float
    observed_at_utc: datetime


def fetch_zone_live(query_api, bucket: str, *, area: str = "COMED") -> Optional[ZoneLoadSnapshot]:
    """Pull the two most recent ``pjm.inst_load`` observations for the
    given ComEd area and compute a simple discrete derivative (MW/hour).

    Reads ``pjm.inst_load`` (PJM "Instantaneous Load" feed, ~5-min
    cadence, area=COMED) rather than ``pjm.metered_load`` (which is
    daily-published with multi-day lag and is not suitable for the §3
    5CP detector's current-load comparison).

    Per the PJM DM2 OpenAPI spec, ``inst_load`` is "approximate, NOT
    official PJM Loads" but "frequently updated throughout the operating
    day" — the right tradeoff for a real-time directional signal of
    "is current load climbing toward season-to-date 5th-highest right
    now?". The season-to-date baseline still comes from the official
    metered feed via ``update_season_5th_highest``; the two feeds
    cooperate by purpose (inst_load = real-time current, metered_load =
    historical baseline).

    Note the parameter is ``area``, not ``zone`` — PJM's inst_load filter
    list uses ``area=COMED`` while hrl_load_metered uses ``zone=CE``.

    Returns None when fewer than two observations exist (e.g., container
    just started and the inst_load poller hasn't caught up); caller
    skips the 5CP check rather than treat absence as zero load."""
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: -1h)
          |> filter(fn: (r) => r._measurement == "pjm.inst_load"
                                and r.area == "{area}"
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


def _latest_forecast_revision_tag(query_api, bucket: str,
                                   forecast_area: str,
                                   *, search_window: str = "-36h") -> Optional[str]:
    """Return the most recent ``evaluated_at_iso`` tag value for
    pjm.load_forecast rows in the given forecast area, or None if no
    forecast has been published in the search window.

    PJM publishes ``load_frcstd_7_day`` two or more times per day. Each
    publication writes a full 7-day forecast tagged with its
    ``evaluated_at_iso`` value. Taking ``max()`` across all revisions
    can return a stale revision's number; we want the latest revision's
    forecast, which means we have to identify the newest tag value
    first, then filter to it.
    """
    flux = f"""
        import "influxdata/influxdb/schema"
        schema.tagValues(
            bucket: "{bucket}",
            tag: "evaluated_at_iso",
            predicate: (r) => r._measurement == "pjm.load_forecast"
                                and r.forecast_area == "{forecast_area}",
            start: {search_window},
        )
        |> sort(columns: ["_value"], desc: true)
        |> limit(n: 1)
    """
    for table in query_api.query(flux):
        for record in table.records:
            v = record.get_value()
            if v is not None:
                return str(v)
    return None


def fetch_forecast_peak_today(query_api, bucket: str,
                               *, forecast_area: str = "COMED",
                               tz: ZoneInfo = CHICAGO) -> Optional[float]:
    """Pull the maximum hourly ``forecast_load_mw`` for today (local tz)
    from the most recently published forecast revision.

    Two bug fixes vs. the May 2026 version:

    1. **Latest revision only.** PJM publishes multiple forecast
       revisions per day, each tagged with its own ``evaluated_at_iso``
       value. The earlier query took ``max()`` across all revisions,
       which could return a stale revision's peak if PJM revised
       downward. We now identify the latest revision first and filter
       to it.

    2. **Local-tz day boundary.** The earlier query used Flux ``today()``
       which is UTC midnight start of today, not Chicago. Evening CT
       queries (after 19:00 CT in CDT, the boundary crossed into
       UTC-tomorrow) would query an empty range. Now uses the
       caller-supplied tz to bound the day window correctly.
    """
    latest_rev = _latest_forecast_revision_tag(query_api, bucket, forecast_area)
    if latest_rev is None:
        return None
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return _max_forecast_in_window(
        query_api, bucket, forecast_area, latest_rev,
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def fetch_forecast_peak_for_date(query_api, bucket: str, target_date_iso: str,
                                  *, forecast_area: str = "COMED",
                                  tz: ZoneInfo = CHICAGO) -> Optional[float]:
    """Pull the maximum hourly ``forecast_load_mw`` for a specific target
    date (CT-local) from the most recently published forecast revision.

    Used by the §7 pre-cool deepening trigger which evaluates "tomorrow's
    peak forecast" at 21:00 the night before. Returns None when no
    forecast rows for the target date exist yet (e.g., a 21:00 decision
    that beat PJM's tomorrow-forecast publication, or the 7-day forecast
    horizon doesn't cover the date).

    Latest-revision selection: see ``_latest_forecast_revision_tag``.
    Pre-fix this function took ``max()`` across all revisions, which
    could pick a stale revision's number.
    """
    latest_rev = _latest_forecast_revision_tag(query_api, bucket, forecast_area)
    if latest_rev is None:
        return None
    target_date = datetime.fromisoformat(target_date_iso).replace(tzinfo=tz)
    start_local = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return _max_forecast_in_window(
        query_api, bucket, forecast_area, latest_rev,
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def _max_forecast_in_window(query_api, bucket: str, forecast_area: str,
                             revision_tag: str,
                             start_utc: datetime, end_utc: datetime) -> Optional[float]:
    """Filter pjm.load_forecast to a single revision and a UTC time
    window, return the max forecast_load_mw."""
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: {start_utc.isoformat()}, stop: {end_utc.isoformat()})
          |> filter(fn: (r) => r._measurement == "pjm.load_forecast"
                                and r.forecast_area == "{forecast_area}"
                                and r.evaluated_at_iso == "{revision_tag}"
                                and r._field == "forecast_load_mw")
          |> max()
    """
    for table in query_api.query(flux):
        for record in table.records:
            v = record.get_value()
            if v is not None:
                return float(v)
    return None
