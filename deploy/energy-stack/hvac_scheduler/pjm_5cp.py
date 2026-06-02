"""PJM 5CP-eligibility detector (Arm B planning/telemetry, post-§11 #14).

The residential capacity-charge dollar exposure depends on TWO separate
sets of 5 coincident peak hours per cooling season (see HVAC_LOGIC.md
"Capacity peak context"):

  * **PJM 5CP**   -- 5 highest RTO-wide hourly loads.
  * **ComEd 5CP** -- 5 highest ComEd-zone hourly loads.

ComEd's zone peaks can land earlier in the afternoon than the RTO peaks
because metro-Chicago load shape differs from the broader RTO. Each kW
shaved during *any* 5CP hour saves roughly the same dollar amount.

**Authority (binding spec §11 #14):** this module is PLANNING + TELEMETRY,
not live setpoint authority. ``fivecp_active=True`` does NOT raise the
effective cool setpoint — it is preserved on ``LayerResolution`` /
``hvac.5cp_state`` / ``fivecp_eval`` for post-hoc analysis of when 5CP
WOULD have fired, and consumed by the §7 day-ahead pre-cool deepening
decision in ``decide_day_type`` (via ``precool.should_deepen_precool``)
when sufficient current-season official metered-load history exists.
Live shutoff (cool=85F) is driven exclusively by the §2 price overlay's
scarcity tier (>= 20c/kWh override). The scheduler runs this detector
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
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .influx_adapter import project_record


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

# Pre-season cold-start fallback for the RTO 5CP detector. Source: latest
# published actual PJM RTO 5CP 5th-peak (2025) per PJM's annual 5CP
# publication: 151,524.7 MW. Rounded up to 151,525 to be slightly
# conservative (a real RTO hot day with current load >= 143,949 MW = 95%
# of 151,525 trips the detector pre-data; the 2025 actual peak was
# >161 GW so this is reachable on a hot weekday afternoon).
RTO_PRE_SEASON_FALLBACK_5TH_MW = 151525.0

MIN_OBSERVATIONS_FOR_5TH = 5

# Minimum distinct current-season official hourly observations required
# before the season-5th-highest baseline is considered usable for
# planning/control. Below this threshold the baseline is unavailable
# and callers receive None — there is NO prior-year fallback for control
# or planning decisions (binding spec §11 #14).
#
# Pinned at 168 hours (= 7 full days × 24 hours). Rationale:
#   - One week of current-season official metered-load data is the
#     minimum defensible window for an in-season baseline.
#   - Weather, demand growth, and grid behavior change year to year;
#     "require one week of current-season data" is more defensible
#     than anchoring to last year's distribution.
#   - PJM publishes hrl_load_metered with ~1-2 day lag, so 168 distinct
#     hours typically lands in current-year Influx by June 8-9.
#   - Before this threshold is reached, 5CP planning logic records
#     `insufficient_current_season_history` in reasoning rather than
#     pretending the prior year is authoritative.
MIN_DISTINCT_HOURS_FOR_BASELINE = 168

CHICAGO = ZoneInfo("America/Chicago")


# ---- Cooling-season window (PJM Manual 19 / ComEd Att. M-2) ---------------
#
# The 5CP summer period is **June 1 - September 30** per PJM Manual 19
# and ComEd Attachment M-2. The hvac-scheduler runs year-round, so all
# detector inputs must be clipped to this window:
#
#   * season_5th_highest must be computed from rows whose CT-local
#     timestamp falls inside Jun 1 - Sep 30. Including May or October
#     rows would let off-season low loads infiltrate the baseline.
#     This was the 2026-05-11 production incident: a fresh RTO ingest
#     of sparse May rows produced a season_5th_mw of 90,244 MW, which
#     a real RTO load of 85,410 MW already cleared (ratio 0.946,
#     dangerously close to the locked 0.95 trigger). PJM Manual 19's
#     definition keeps this out.
#
#   * The detector itself must not fire outside Jun-Sep -- a 5CP can
#     only happen during the official summer window by definition. An
#     active state crossing Sep 30 -> Oct 1 is reset to inactive.
#
# Off-season ticks (Oct-May) still need a season_5th reference for
# logging and for §7 night-before deepening decisions. They use the
# last completed cooling season's window:
#
#   * Jun-Sep    -> current year, Jun 1 to today (season-to-date)
#   * Oct-Dec    -> current year, Jun 1 to Sep 30 (just completed)
#   * Jan-May    -> previous year, Jun 1 to Sep 30 (last completed)
COOLING_SEASON_START_MONTH = 6
COOLING_SEASON_END_MONTH = 9


def in_cooling_season(now_local: datetime) -> bool:
    """True when ``now_local`` falls inside the PJM 5CP eligibility
    window (Jun 1 through Sep 30, CT local). The §3 detector skips its
    trigger evaluation outside this window because a 5CP can only
    happen during the official cooling season.
    """
    return COOLING_SEASON_START_MONTH <= now_local.month <= COOLING_SEASON_END_MONTH


def cooling_season_window_utc(
    now_local: datetime, *, tz: ZoneInfo = CHICAGO,
) -> tuple[datetime, datetime]:
    """Return ``(start_utc, end_utc)`` of the cooling-season window
    relevant to ``now_local``.

    Determined per the table in the module-level comment:
      - In-season (Jun-Sep): this year's Jun 1 -> Sep 30 (clipped to
        now if before Sep 30 -- callers should cap end at now_utc).
      - Off-season Oct-Dec:  this year's Jun 1 -> Sep 30 (just
        completed).
      - Off-season Jan-May:  previous year's Jun 1 -> Sep 30 (last
        completed).

    The returned values are timezone-aware UTC datetimes. Callers
    passing these into a Flux ``range(start, stop)`` should still cap
    ``stop`` at the current ``now_utc`` to avoid querying future
    timestamps when in-season.
    """
    year = now_local.year
    if now_local.month < COOLING_SEASON_START_MONTH:
        season_year = year - 1
    else:
        season_year = year
    start_local = datetime(season_year, COOLING_SEASON_START_MONTH, 1,
                            0, 0, 0, tzinfo=tz)
    end_local = datetime(season_year, COOLING_SEASON_END_MONTH, 30,
                          23, 59, 59, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


# ---- Detector scope (P1.1) -------------------------------------------------


@dataclass(frozen=True)
class DetectorScope:
    """Per-detector configuration. The §3 5CP detector runs once per
    scope. Two scopes are wired in production:

      * ``COMED_SCOPE`` -- catches ComEd 5CPs (ComEd-zone hours that
        contribute to ComEd's capacity-allocation step).
      * ``RTO_SCOPE``   -- catches PJM 5CPs (RTO-wide hours that
        determine ComEd's PLC, which then flows to the residential
        capacity-charge rate).

    Both scopes contribute dollars to the household's next-year
    residential capacity charge (per HVAC_LOGIC.md "Capacity peak
    context"), so the scheduler ORs their triggers.

    Carrying the scope explicitly through fetch / state-update /
    decision is what prevents a regression like the 2026-05 ComEd
    fallback bug (130 GW RTO-scale value misapplied to the ComEd-zone
    path). A function that takes a scope cannot silently mix scales.
    """
    name: str                          # log identifier: "comed_zone" | "rto"
    inst_load_area: str                # pjm.inst_load tag value: "COMED" | "PJM RTO"
    metered_load_zone: str             # pjm.metered_load tag value: "CE" | "RTO"
    pre_season_fallback_5th_mw: float  # 20375.0 | 151525.0


COMED_SCOPE = DetectorScope(
    name="comed_zone",
    inst_load_area="COMED",
    metered_load_zone="CE",
    pre_season_fallback_5th_mw=COMED_PRE_SEASON_FALLBACK_5TH_MW,
)

RTO_SCOPE = DetectorScope(
    name="rto",
    inst_load_area="PJM RTO",
    metered_load_zone="RTO",
    pre_season_fallback_5th_mw=RTO_PRE_SEASON_FALLBACK_5TH_MW,
)


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
    """Decide whether the 5CP risk-active state is set this tick.

    Returns ``(is_active, new_state)``. ``is_active=True`` is recorded on
    ``LayerResolution.fivecp_active`` and emitted in ``hvac.5cp_state`` /
    ``fivecp_eval`` telemetry. Post-binding-spec §11 #14, the §4 layer
    resolver does NOT use this signal to raise ``effective_cool_f`` —
    live shutoff comes from the §2 price overlay scarcity tier. The
    state-machine semantics are preserved unchanged so the telemetry
    accurately reflects when 5CP WOULD have fired under the previous
    live-authority design (useful for post-hoc detector-accuracy
    analysis per O2_CAPACITY_RECONSTRUCTION.md).

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


def season_5th_highest_from_loads(
    loads_mw: list[float],
    *,
    min_distinct_hours: int = MIN_DISTINCT_HOURS_FOR_BASELINE,
) -> Optional[float]:
    """Return the current-season 5th-highest hourly load, or None when
    insufficient current-season official metered-load history exists.

    Per binding spec §11 #14: NO prior-year fallback for control or
    planning decisions. Callers receive None and record
    `insufficient_current_season_history` in reasoning/logs.

    Sufficient-data path:
      - ``len(loads_mw) >= min_distinct_hours``: return the actual
        current-season 5th-highest from the data.

    Insufficient-data path:
      - ``len(loads_mw) < min_distinct_hours``: return None. The
        detector adapts to current-year conditions or is unavailable;
        it does not anchor to last year's hot days.

    ``loads_mw`` MUST be the deduplicated set of distinct hourly
    observations within the current cooling-season window for one scope
    (ComEd zone or PJM RTO). The caller is responsible for dedup
    (the Flux query in ``update_season_5th_highest`` uses
    ``aggregateWindow(every: 1h, fn: max)`` for this).
    """
    if len(loads_mw) < min_distinct_hours:
        return None
    sorted_desc = sorted(loads_mw, reverse=True)
    return float(sorted_desc[MIN_OBSERVATIONS_FOR_5TH - 1])


def update_season_5th_highest(query_api: Any, bucket: str,
                              season_start_utc: datetime,
                              season_end_utc: datetime,
                              *, zone: str = "CE",
                              ) -> Optional[float]:
    """Query InfluxDB for distinct hourly ``pjm.metered_load`` values
    inside the half-open window ``[season_start_utc, season_end_utc)``
    and return the current-season 5th-highest, or None when fewer than
    ``MIN_DISTINCT_HOURS_FOR_BASELINE`` (168 = 7 days) distinct hourly
    observations exist.

    Returning None signals "insufficient current-season official
    metered-load history" — callers MUST treat this as
    `5CP planning/control unavailable` per binding spec §11 #14. There
    is NO prior-year fallback for control or planning decisions; the
    function intentionally provides no `fallback_mw` parameter.

    The window MUST be a cooling-season window (Jun 1 - Sep 30 per
    PJM Manual 19 / ComEd Attachment M-2). Including off-season rows
    is what produced the 2026-05-11 incident: sparse RTO ingest of
    May rows yielded a 90,244 MW "season 5th" that real RTO load
    almost cleared. Callers should compute the window via
    ``cooling_season_window_utc(now_local)`` and cap the end at
    ``now_utc`` when in-season.

    Hour-dedup invariant: ``pjm.metered_load`` carries ``is_verified``
    as a tag (set by the poller). When PJM corrects a row from
    unverified -> verified, Influx stores it as a separate series at
    the same timestamp. The ``group()`` flatten pulls all is_verified
    series for a given hour into one table; ``aggregateWindow(every: 1h,
    fn: max)`` then yields one row per distinct hour. Distinct-hour
    count drives the threshold check, not raw-row count.

    Aggregation choice: ``fn: max`` (not ``mean``) so the corrected
    (verified) value is preferred over the initial estimate. PJM's
    initial unverified publish is typically conservative; the
    subsequent verified value adjusts slightly upward as corrections
    are applied. Taking ``max`` always selects the corrected value
    when both exist, while still returning the unverified value when
    PJM hasn't shipped a correction yet. ``mean`` averaged the two
    and produced a value lower than the verified actual, biasing the
    season-5th baseline downward for hours that had been corrected.

    Prior-bug context (binding spec §11 #14): an earlier version of
    this query terminated the pipeline with ``|> limit(n: 5)`` and
    then required >= 200 rows before trusting the result. The query
    could return at most 5 rows so the row-count check ALWAYS failed
    and the function effectively returned the prior-year fallback
    forever. The fix removes the Flux ``limit`` and changes the
    threshold check to distinct-hour count, with no fallback for
    control.

    Empty-window guard: when ``season_end_utc <= season_start_utc`` the
    window has zero (or negative) width. This happens on the first
    calendar day of the cooling season, where callers cap the end at the
    target date's midnight and that equals ``season_start_utc`` (both =
    Jun 1 00:00 local). A Flux ``range(start: X, stop: X)`` is rejected by
    InfluxDB with HTTP 400 "cannot query an empty range". A zero-width
    window also holds zero distinct hours -- insufficient history by
    definition -- so None is the same answer the query would return if it
    could run. Short-circuit before issuing the degenerate query.
    """
    if season_end_utc <= season_start_utc:
        return None
    flux = f"""
        from(bucket: "{bucket}")
          |> range(start: {season_start_utc.isoformat()},
                   stop: {season_end_utc.isoformat()})
          |> filter(fn: (r) => r._measurement == "pjm.metered_load"
                                and r.zone == "{zone}"
                                and r._field == "mw")
          |> group()
          |> aggregateWindow(every: 1h, fn: max, createEmpty: false)
    """
    loads: list[float] = []
    for table in query_api.query(flux):
        for record in table.records:
            try:
                rec = project_record(record)
            except ValueError:
                continue
            loads.append(rec.value)
    return season_5th_highest_from_loads(loads)


# ---- Live load + derivative ------------------------------------------------


@dataclass(frozen=True)
class ZoneLoadSnapshot:
    """Most recent hourly load + a simple derivative computed against the
    immediately prior hourly observation."""
    current_mw: float
    derivative_mw_per_hour: float
    observed_at_utc: datetime


def fetch_zone_live(query_api: Any, bucket: str, *, area: str = "COMED") -> Optional[ZoneLoadSnapshot]:
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
            try:
                rec = project_record(record)
            except ValueError:
                continue
            rows.append((rec.time_utc, rec.value))
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


# ---- Scope-aware top-level evaluation (P1.1) ------------------------------


@dataclass(frozen=True)
class ScopeEvaluation:
    """Per-tick result of ``evaluate_for_scope``. Bundles the binding
    decision (``is_active``) with the structured-log payload so callers
    can emit one log line covering all the inputs the detector saw.

    ``log_fields`` always includes ``scope``, ``area``, ``zone``, and
    ``fallback_5th_mw`` so a scale-mismatch regression (RTO fallback
    misapplied to ComEd path, or vice versa) shows up in logs the same
    tick it happens, not silently in behavior.
    """
    is_active: bool
    new_state: FiveCPState
    season_5th_mw: Optional[float]   # None = insufficient current-season official metered-load history
    snapshot: Optional[ZoneLoadSnapshot]
    log_fields: dict[str, Any]


def evaluate_for_scope(
    scope: DetectorScope,
    query_api: Any,
    bucket: str,
    season_start_utc: datetime,
    season_end_utc: datetime,
    forecast_peak_today_mw: Optional[float],
    state: FiveCPState,
    now_utc: datetime,
    *,
    tz: ZoneInfo = CHICAGO,
) -> ScopeEvaluation:
    """Top-level per-scope 5CP detector evaluation.

    Fetches inputs from InfluxDB (current load via ``pjm.inst_load``
    tagged with ``scope.inst_load_area``, season-to-date 5th-highest
    via ``pjm.metered_load`` tagged with ``scope.metered_load_zone``,
    bracketed by the cooling-season window), runs the state machine,
    and returns the binding decision plus the log payload describing
    every input.

    Off-season skip (PJM Manual 19 / ComEd Attachment M-2): a 5CP can
    only happen Jun 1 - Sep 30 by definition. Outside that window the
    function short-circuits to ``is_active=False`` with a fresh state
    (a hold that would have crossed Sep 30 -> Oct 1 is released).
    ``log_fields["data_status"]`` is ``"off_season"`` for these ticks.

    Missing-data semantics (in-season only):

      * If ``state.is_active`` and the hold-end has NOT elapsed
        (``now_utc < hold_end_time(triggered_at_utc)``): carry state.
        Within the hold window, the locked rule says stay active
        regardless of load conditions, so missing data doesn't change
        anything.

      * If ``state.is_active`` and the hold-end HAS elapsed: force
        release. Past hold-end the active state would normally be
        held only while release conditions (low ratio AND descending
        load) are unmet; without data we can't observe those
        conditions, so the safest move is to release rather than pin
        the shutoff layer indefinitely. The detector can re-trigger
        on the next tick if data returns and conditions warrant.
        Pre-2026-05 this path carried state.is_active unconditionally,
        which could pin HVAC at 85F shutoff for the rest of the day
        on a sustained feed/Influx outage after a trigger.

      * If ``not state.is_active``: stay inactive (default state).
        Missing data can never start a new active state.
    """
    now_local = now_utc.astimezone(tz)

    log_fields: dict[str, Any] = {
        "scope": scope.name,
        "area": scope.inst_load_area,
        "zone": scope.metered_load_zone,
        "fallback_5th_mw": scope.pre_season_fallback_5th_mw,
    }

    if not in_cooling_season(now_local):
        log_fields["data_status"] = "off_season"
        log_fields["season_5th_mw"] = None
        return ScopeEvaluation(
            is_active=False,
            new_state=FiveCPState(),
            season_5th_mw=None,
            snapshot=None,
            log_fields=log_fields,
        )

    season_5th_mw = update_season_5th_highest(
        query_api, bucket, season_start_utc, season_end_utc,
        zone=scope.metered_load_zone,
    )
    snapshot = fetch_zone_live(query_api, bucket, area=scope.inst_load_area)

    log_fields["season_5th_mw"] = season_5th_mw

    # Insufficient current-season history (binding spec §11 #14): no
    # prior-year fallback for control/planning. Detector cannot evaluate
    # this tick; record reason and stay inactive.
    if season_5th_mw is None:
        log_fields["data_status"] = "insufficient_current_season_history"
        return ScopeEvaluation(
            is_active=False,
            new_state=FiveCPState(),
            season_5th_mw=None,
            snapshot=snapshot,
            log_fields=log_fields,
        )

    if snapshot is None or forecast_peak_today_mw is None:
        log_fields["data_status"] = (
            "no_snapshot" if snapshot is None else "no_forecast_peak"
        )
        # Missing-data path: carry state during the hold window, force
        # release once hold-end has elapsed. See docstring for the
        # rationale of why missing data past hold-end forces release
        # rather than pinning the shutoff layer indefinitely.
        if state.is_active and state.triggered_at_utc is not None:
            hold_end = hold_end_time(state.triggered_at_utc)
            if now_utc >= hold_end:
                log_fields["forced_release"] = "hold_elapsed_without_data"
                return ScopeEvaluation(
                    is_active=False,
                    new_state=FiveCPState(),
                    season_5th_mw=season_5th_mw,
                    snapshot=snapshot,
                    log_fields=log_fields,
                )
        return ScopeEvaluation(
            is_active=state.is_active,
            new_state=state,
            season_5th_mw=season_5th_mw,
            snapshot=snapshot,
            log_fields=log_fields,
        )

    ratio = (snapshot.current_mw / season_5th_mw) if season_5th_mw > 0 else 0.0
    log_fields.update({
        "current_mw": snapshot.current_mw,
        "derivative_mw_per_hour": snapshot.derivative_mw_per_hour,
        "load_ratio": ratio,
        "forecast_peak_today_mw": forecast_peak_today_mw,
        "data_status": "ok",
    })

    is_active, new_state = evaluate_5cp_risk(
        current_load_mw=snapshot.current_mw,
        season_5th_highest_mw=season_5th_mw,
        load_derivative_mw_per_hour=snapshot.derivative_mw_per_hour,
        forecast_peak_today_mw=forecast_peak_today_mw,
        now_utc=now_utc,
        state=state,
    )
    return ScopeEvaluation(
        is_active=is_active,
        new_state=new_state,
        season_5th_mw=season_5th_mw,
        snapshot=snapshot,
        log_fields=log_fields,
    )


def _latest_forecast_revision_tag(query_api: Any, bucket: str,
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
    # NB: this query uses `schema.tagValues(...)` which returns rows
    # with only a string `_value` column (the tag value) — no `_time`,
    # `_measurement`, or `_field`. The influx_adapter.project_record
    # projection requires all four to be present and casts `_value` to
    # float, so the typed adapter is shape-incompatible here. Direct
    # `record.get_value()` access is retained for this single
    # tag-metadata query; spec §5.5's import-linter contract permits an
    # explicit exemption for this case if needed.
    for table in query_api.query(flux):
        for record in table.records:
            v = record.get_value()
            if v is not None:
                return str(v)
    return None


def fetch_forecast_peak_today(query_api: Any, bucket: str,
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


def fetch_forecast_peak_for_date(query_api: Any, bucket: str, target_date_iso: str,
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


def _max_forecast_in_window(query_api: Any, bucket: str, forecast_area: str,
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
            try:
                rec = project_record(record)
            except ValueError:
                continue
            return rec.value
    return None
