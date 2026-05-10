# Arm B Implementation Specification

**Status**: Working spec (2026-05-09). Implementation gated on this spec being approved before code changes land.
**Owner**: Chris dePaola
**Companion docs**: [`EXPERIMENT_DESIGN.md`](EXPERIMENT_DESIGN.md) (research framing, locked threshold values in Appendix A), [`HVAC_LOGIC.md`](HVAC_LOGIC.md) (current scheduler logic, schedules, fallback)

---

## Overview

Arm B requires four new capabilities on top of the currently deployed scheduler ([`deploy/energy-stack/hvac-scheduler/app.py`](../deploy/energy-stack/hvac-scheduler/app.py)):

1. **Recalibrated day-type classifier** (HOT at ≥85°F max OR apparent ≥90°F)
2. **Real-time RTP price-spike reactivity** (3-tier with hysteresis)
3. **PJM 5CP-eligibility detection** (live load + season-to-date tracking)
4. **Layer priority resolution** in `execute_action` (warmer-wins over schedule, safety supervisor floor)

Plus two operational procedures:

5. **AIR toggling** (CTK04 ISU 4090) for arm transitions on Mondays
6. **Dry-run mode validation** for Arm A weeks

This document specifies the code-level changes, integration points, new env vars, new InfluxDB measurements, and validation criteria for each.

---

## 0. Resolved dependencies

Two upstream feeds need work before the Arm B logic itself can land. Neither was on the original critical path; both became blockers once the locked thresholds were chosen against real data.

### 0a. NWS poller migration to `forecastGridData`

**Current:** [`deploy/energy-stack/nws-poller/app.py`](../deploy/energy-stack/nws-poller/app.py) uses the `forecastHourly` endpoint, which returns hourly period objects with `temperature` and `dewpoint` but does NOT include `apparentTemperature`.

**Required change:** migrate to the `forecastGridData` endpoint, which returns gridded forecasts with apparent temperature as a first-class field plus several other useful variables.

**Endpoint resolution:** the gridpoint metadata at `https://api.weather.gov/points/{lat},{lon}` returns a `properties.forecastGridData` URL. The current poller resolves `properties.forecastHourly`; this becomes a one-line swap, then the response parser changes substantially.

**Response format differences:**

`forecastGridData` returns a structured grid where each variable is a top-level key with a `values` list. Each entry is `{validTime, value}` where `validTime` is an ISO-8601 interval like `"2026-05-09T18:00:00+00:00/PT3H"` (start time + ISO-8601 period). Granularity varies:
- 1-hour for the first ~36 hours
- 3-hour for hours 36-72
- 6-hour beyond

Variables of interest:
- `temperature` (degC) — convert to F
- `apparentTemperature` (degC) — NEW, convert to F
- `dewpoint` (degC) — convert to F
- `relativeHumidity` (percent) — NEW, useful for sensitivity checks
- `skyCover` (percent) — NEW, cross-validates against Ecowitt pyranometer
- `windSpeed` (km/h) — convert to mph
- `windGust` (km/h) — NEW, convert to mph
- `probabilityOfPrecipitation` (percent) — NEW, useful for storm-event awareness

**Parser changes required:**

1. **Time-grid expansion.** Each `{validTime, value}` entry covers a duration; expand each into hourly slots within that duration. Library helpers: `isodate.parse_duration()` for the duration component.
2. **Cross-variable alignment.** Expand all variables onto a common 1-hour grid before aggregating. Use the union of expansion windows.
3. **Daily roll-up.** The existing `summarize_for_date()` logic iterates hourly periods and aggregates by date; the new version operates on the expanded common grid and computes:
   - Existing fields: `high_f`, `low_f`, `max_dewpoint_f`, `max_wind_mph`
   - **New fields:** `apparent_max_f`, `apparent_min_f`, `apparent_avg_f`, `rh_max_pct`, `rh_avg_pct`, `sky_cover_avg_pct`, `wind_gust_max_mph`, `precip_prob_max_pct`
4. **InfluxDB write schema.** Keep all existing fields on `nws.forecast` for backwards compatibility. Add the new fields. Existing dashboard queries continue to work; new queries (especially day-type classifier) read the new `apparent_max_f` field.

**Test fixtures:**

- Real `forecastGridData` response sample committed to `nws-poller/tests/fixtures/`.
- Expected daily aggregates for that sample (computed by hand, committed alongside).
- Unit tests for: time-grid expansion, cross-variable alignment, daily roll-up, unit conversions.

**Validation criterion:** Run new poller in parallel with old poller for 48 hours. Compare existing fields (`high_f`, `low_f`, `max_dewpoint_f`) — they should match within rounding. Verify new fields populate correctly. After parallel-run validation, retire the old `forecastHourly` path.

**Estimated work:** half day to full day of focused implementation.

**Target:** completed and validated by 2026-05-15.

### 0b. PJM hourly metered load polling cadence

**Current:** [`deploy/energy-stack/pjm-dm2-poller/app.py:103`](../deploy/energy-stack/pjm-dm2-poller/app.py) polls the `hrl_load_metered` feed only on Sundays at 02:00 CT, pulling the last 7 days of zone="CE" hourly load.

**Required change:** poll hourly instead of weekly, pulling only the last 2-3 hours of data per call to keep payloads small. InfluxDB deduplicates on identical timestamps, so re-pulling overlapping windows is safe.

**Implementation:**

1. Edit `FEED_SCHEDULE` entry for `hrl_load_metered`:
   ```python
   "hrl_load_metered": Schedule(hours=tuple(range(0, 24))),  # every hour
   ```
2. Edit `fetch_metered_load_last_week` (rename to `fetch_metered_load_recent`) to use a 3-hour lookback window instead of 7-day:
   ```python
   start_dt = (now_local - timedelta(hours=3)).strftime(...)
   end_dt = now_local.strftime(...)
   ```
3. Add a separate weekly job (or keep the Sunday 02:00 trigger as a backfill) that pulls 7 days for any historical-completeness gaps.

**Caveat:** PJM's `rt_hrl_load_metered` (the actual feed name for real-time hourly metered) typically has ~1 hour data lag from PJM. Acceptable for our use case because:
- 5CP eligibility is determined post-hoc anyway
- Arm B's 5CP detection logic predicts upcoming 5CP-eligible hours from load trajectory + forecast peak; it does not need to react inside the hour
- Season-to-date 5th-highest computation works on completed hourly observations

**Test:**
- Unit test: `fetch_metered_load_recent` with mocked PJM client returning 3 hours of data, verify Influx points generated correctly.
- Integration test: run the modified poller for 4-6 hours, verify Influx receives hourly updates to `pjm.metered_load` measurement with zone=CE tag.

**Validation criterion:** `pjm.metered_load` has a fresh row every hour for at least 24 consecutive hours during pre-flight testing. Season-to-date 5th-highest computation (used by the 5CP detector in §3) returns sensible values.

**Estimated work:** 1-2 hours.

**Target:** completed and validated by 2026-05-12.

---

## 1. Day-type classifier recalibration

**Location:** [`deploy/energy-stack/hvac-scheduler/app.py:396`](../deploy/energy-stack/hvac-scheduler/app.py) (`_classify_one_day()`)

**Current thresholds (from HVAC_LOGIC.md§82):**
- MILD if forecast high < 82°F
- NORMAL if 82-94°F
- HOT if ≥95°F or heat advisory

**New thresholds (locked per EXPERIMENT_DESIGN Appendix A):**
- MILD if forecast high < 75°F
- NORMAL if 75-85°F max
- HOT if ≥85°F max OR forecast apparent ≥90°F OR heat advisory

**Implementation notes:**

- Apparent temperature comes from the NWS `forecastGridData` endpoint as a separate field. The existing `nws-poller` uses `forecastHourly` which does not return apparent temperature. The poller is being migrated to `forecastGridData` (see §1a below) before the day-type classifier change lands. Once the poller writes `apparent_max_f` to `nws.forecast` daily roll-ups, `_classify_one_day()` reads that field directly. No local Rothfusz derivation; the NWS-authoritative value is used so the methods section can cite "NWS gridpoint API `apparentTemperature` field" without ambiguity.
- Heat advisory check stays as a separate trigger condition (keeps current behavior intact).
- Return value enum is unchanged (`MILD`, `NORMAL`, `HOT`, `HOT_STREAK_DAY1`).

**Test:**

Add unit tests to [`test_hvac_scheduler.py`](../deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py) covering:

- 88°F max + apparent 88°F → HOT (currently NORMAL)
- 82°F max + apparent 92°F → HOT (currently NORMAL)
- 84°F max + no advisory → HOT (currently NORMAL)
- 76°F max + apparent 88°F → NORMAL
- 70°F max → MILD

**Validation criterion:** unit tests pass, schedule action firing during dry-run on existing logged forecast data shows expected HOT-day count for the 2025-equivalent forecast set.

---

## 2. Real-time RTP price-spike reactivity

**New module:** `deploy/energy-stack/hvac-scheduler/price_overlay.py`

**Integration point:** Called from `execute_action()` (line 628) before the safety supervisor.

### Module structure

```python
# price_overlay.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class PriceTier:
    name: str
    trigger_price_cents_per_kwh: float
    release_price_cents_per_kwh: float
    cool_setpoint_offset_f: int  # positive = warmer
    cool_setpoint_override_f: Optional[int]  # if set, replaces the schedule setpoint instead of offsetting

PRICE_TIERS = [
    PriceTier("scarcity", 20.0, 18.0, cool_setpoint_offset_f=0,
              cool_setpoint_override_f=85),
    PriceTier("elevated", 10.0, 8.0, cool_setpoint_offset_f=3,
              cool_setpoint_override_f=None),
]

@dataclass
class PriceOverlayState:
    current_tier: str  # "normal", "elevated", "scarcity"
    triggered_at_utc: Optional[datetime]  # when current tier started

def evaluate_price_overlay(
    current_price_cents: float,
    state: PriceOverlayState,
    now_utc: datetime,
    minimum_hold_minutes: int = 30,
) -> tuple[Optional[PriceTier], PriceOverlayState]:
    """Return (active_tier_or_None, new_state).

    Decision rule:
    - For each tier in priority order (scarcity > elevated):
      - If currently in this tier or higher, check release condition + min hold.
      - If currently below this tier, check trigger condition.
    - Tier transitions logged to InfluxDB.
    """
    ...
```

### Integration in `execute_action()`

```python
# After determining schedule_setpoint and humid_override_setpoint:
current_price = fetch_latest_comed_hourly_avg(query_api, cfg.influxdb_bucket)

active_tier, new_state = evaluate_price_overlay(
    current_price, price_overlay_state, datetime.now(timezone.utc)
)

# Apply tier (warmer-wins semantics)
schedule_cool, _ = resolve_cool_setpoint(action, today_dewpoint_f)
if active_tier:
    if active_tier.cool_setpoint_override_f is not None:
        price_setpoint = active_tier.cool_setpoint_override_f
    else:
        price_setpoint = schedule_cool + active_tier.cool_setpoint_offset_f
    effective_cool = max(schedule_cool, price_setpoint)
else:
    effective_cool = schedule_cool

# (5CP check applied next, see §3)
# Safety supervisor clamps last
```

### State persistence

`PriceOverlayState` survives across scheduler ticks. Two options:

- **A: in-memory only.** Lose state across container restarts. Cold-start re-evaluates from current price (acceptable; 30-min hold is short).
- **B: InfluxDB-persisted.** Write `hvac.price_overlay_state` on every tier transition; read on startup.

**Recommendation: A for v1.** Simpler. Container restarts are rare and re-evaluation from current price gives correct behavior within 5 minutes.

### New InfluxDB measurement

Write `hvac.price_overlay` on every tier transition:

```
measurement: hvac.price_overlay
tags: arm (A or B)
fields:
  tier: "normal" | "elevated" | "scarcity"
  current_price_cents: float
  schedule_cool_f: int
  effective_cool_f: int
  triggered_at_utc: timestamp
```

### Testing

- Unit tests in `test_price_overlay.py`:
  - Below all tiers: returns None
  - Crosses elevated threshold: triggers elevated, holds for 30 min minimum
  - Price drops to release within hold: stays elevated until hold expires
  - Crosses scarcity threshold from elevated: upgrades immediately (no hold required for upgrades)
  - Drops from scarcity to elevated after release: downgrades after 30 min in scarcity
  - Hysteresis works correctly (10¢ trigger, 8¢ release; 20¢ trigger, 18¢ release)
- Integration test: scheduler tick with mocked ComEd price → verify expected setpoint output.

### Env vars

None needed. Tier thresholds are pre-committed and frozen at the OSF commit hash, hardcoded in `PRICE_TIERS`.

### Validation criterion

Run scheduler in dry-run against May 2025 logged ComEd hourly prices. Verify the price-overlay state machine produces the expected tier transitions matching the historical 5-month data: ~157 hours in elevated tier, ~38 hours in scarcity tier.

---

## 3. PJM 5CP-eligibility detection

**New module:** `deploy/energy-stack/hvac-scheduler/pjm_5cp.py`

**Integration point:** Called from `execute_action()` after price overlay, before safety supervisor.

### Required PJM data

The 5CP detector consumes:

- **`pjm.metered_load`** with `zone="CE"` tag — hourly metered load. Poller cadence migrated from weekly to hourly per §0b. ~1 hour data lag from PJM (acceptable per the §0b caveat).
- **`pjm.load_forecast`** — already polled twice-daily, provides forecast peak for today and 7-day-ahead.

The existing weekly Sunday backfill is preserved as a backstop in case the hourly cadence misses any windows. No new feed is added; the existing `hrl_load_metered` feed becomes hourly.

The detector queries `pjm.metered_load` for season-to-date hourly observations, computes the running 5th-highest, compares against current-hour load and the day's forecast peak.

### Module structure

```python
# pjm_5cp.py
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Optional

CT = ZoneInfo("America/Chicago")

# Locked per EXPERIMENT_DESIGN Appendix A
LOAD_RATIO_TRIGGER = 0.95
LOAD_RATIO_RELEASE = 0.90
WINDOW_START_CT = time(13, 0)
WINDOW_END_CT = time(20, 0)
COOL_SHUTOFF_F = 85

@dataclass
class FiveCPState:
    is_active: bool
    triggered_at_utc: Optional[datetime]
    triggered_hour_ct: Optional[int]

def evaluate_5cp_risk(
    current_load_mw: float,
    season_5th_highest_mw: float,
    load_derivative_mw_per_15min: float,
    forecast_peak_today_mw: float,
    now_utc: datetime,
    state: FiveCPState,
) -> tuple[bool, FiveCPState]:
    """Return (is_5cp_risk_active, new_state).

    Trigger when:
    - current_load_mw / season_5th_highest_mw > 0.95
    - load_derivative > 0 (load rising)
    - hour_of_day in [13:00, 20:00] CT
    - forecast_peak_today_mw > season_5th_highest_mw

    Hold until end-of-current-hour + 30 min after first trigger.
    Release only when load_ratio drops below 0.90 AND derivative < 0
    AND hold period elapsed.
    """
    ...

def update_season_5th_highest(
    query_api, bucket: str, current_season_start: datetime
) -> float:
    """Compute season-to-date 5th-highest hourly average load.

    Query InfluxDB for hourly averages of pjm.metered_load (zone="CE")
    since season_start. Sort descending, return value at index 4 (0-indexed).
    Falls back to a hardcoded 130,000 MW (rough ComEd zone summer-peak threshold)
    if fewer than 5 hourly observations exist.
    """
    ...
```

### Integration in `execute_action()`

Continuing from price-overlay output:

```python
# After price overlay, evaluate 5CP risk
season_5th = update_season_5th_highest(query_api, cfg.influxdb_bucket, season_start_utc)
load_data = fetch_pjm_zone_live(query_api, cfg.influxdb_bucket)
forecast_peak = fetch_pjm_forecast_peak_today(query_api, cfg.influxdb_bucket)

is_5cp_active, new_5cp_state = evaluate_5cp_risk(
    load_data.current_mw,
    season_5th,
    load_data.derivative_mw_per_15min,
    forecast_peak,
    now_utc,
    fivecp_state,
)

if is_5cp_active:
    fivecp_setpoint = COOL_SHUTOFF_F
    effective_cool = max(effective_cool, fivecp_setpoint)
```

### State persistence

Same trade-off as price overlay. Recommendation: in-memory v1.

### New InfluxDB measurements

```
measurement: hvac.5cp_state
tags: arm (A or B), zone (ComEd)
fields:
  is_active: bool (0 or 1)
  current_load_mw: float
  season_5th_highest_mw: float
  load_ratio: float
  load_derivative_mw: float
  forecast_peak_today_mw: float
```

Written every scheduler tick (every 5 min) so state is auditable.

```
measurement: pjm.metered_load (zone="CE")
tags: zone (ComEd)
fields:
  mw: float
```

Written by extended `pjm-dm2-poller` if not already covered.

### Testing

- Unit tests in `test_pjm_5cp.py`:
  - All conditions met → is_active=True
  - Load ratio 0.94 → not active
  - Outside 13-20 CT → not active
  - Derivative negative → not active
  - Active state holds through end-of-hour even if load drops
  - Hold continues for 30 min past end-of-hour
  - Release only when load ratio < 0.90 AND derivative < 0 AND hold elapsed
  - Season 5th-highest computation correct on synthetic data
  - Pre-season fallback (< 5 observations) uses 130,000 MW
- Integration test: replay June 24, 2025 PJM zone load history (the 161¢/kWh day) through the detector. Should trigger 5CP risk during the 14-19 CT window.

### Validation criterion

Run detector against historical PJM data for 2024 and 2025 cooling seasons. Verify it would have triggered during the published PJM 5CPs (PJM publishes the actual 5CP timestamps after season close). Hit rate target: ≥ 4 of 5 actual 5CPs detected (with possible false positives at the 0.95 ratio threshold).

---

## 4. Layer priority resolution

**Location:** [`execute_action()`](../deploy/energy-stack/hvac-scheduler/app.py) line 628

The resolution order, applied in `execute_action()` before the safety supervisor:

```python
async def execute_action(c4, action, ...):
    # 1. Schedule baseline
    schedule_cool, schedule_reason = resolve_cool_setpoint(action, today_dewpoint_f)

    # 2. Price overlay (warmer wins)
    active_tier, _ = evaluate_price_overlay(current_price, price_state, now_utc)
    if active_tier:
        if active_tier.cool_setpoint_override_f is not None:
            price_cool = active_tier.cool_setpoint_override_f
        else:
            price_cool = schedule_cool + active_tier.cool_setpoint_offset_f
    else:
        price_cool = schedule_cool

    # 3. 5CP shutoff (warmer wins)
    is_5cp_active, _ = evaluate_5cp_risk(...)
    if is_5cp_active:
        fivecp_cool = COOL_SHUTOFF_F
    else:
        fivecp_cool = price_cool

    # 4. Final effective setpoint (warmer wins across all layers)
    effective_cool = max(schedule_cool, price_cool, fivecp_cool)

    # 5. Safety supervisor (clamps to [65, 86])
    decision = safety_supervisor.validate_setpoints(
        effective_cool, heat_setpoint=65, snapshot=...
    )
    actual_cool = decision.cool_setpoint_f
    
    # 6. Push to thermostat
    await c4.set_cool_setpoint_f(actual_cool)
    await c4.set_heat_setpoint_f(65)
    ...
    
    # 7. Log all layers to hvac.actions for audit
    write_action(write_api, ..., 
                 schedule_cool_f=schedule_cool,
                 price_overlay_tier=active_tier.name if active_tier else "normal",
                 price_cool_f=price_cool,
                 fivecp_active=is_5cp_active,
                 fivecp_cool_f=fivecp_cool,
                 effective_cool_f=effective_cool,
                 actual_cool_f=actual_cool,
                 supervisor_decision=decision.decision)
```

### Test

Unit tests covering layer interaction:

- Schedule says 79°F, no price spike, no 5CP → effective 79°F
- Schedule says 79°F, elevated price → effective 82°F (79+3)
- Schedule says 73°F (sleep), scarcity price → effective 85°F (override wins)
- Schedule says 80°F, 5CP active → effective 85°F
- Schedule says 80°F, scarcity price AND 5CP active → effective 85°F (both want 85, no double-up)
- Schedule says 68°F (pre-cool), elevated price at 4am → effective 71°F (push to 71, not 85)

---

## 5. AIR toggling for arm transitions

**CTK04 ISU 4090** (Adaptive Intelligent Recovery) needs to be:
- **ON during Arm A weeks** (so the thermostat learns recovery timing, matching consumer-grade behavior)
- **OFF during Arm B weeks** (so Pi setpoint pushes are honored exactly when fired, not pre-emptively interpreted)

### Manual procedure (v1)

Every Monday at 00:00 CT (the arm-transition boundary):

1. Read assignment CSV: `docs/experiment-assignments-summer-2026.csv` for this week's arm.
2. If transitioning A → B: open TCC web UI (`mytotalconnectcomfort.com`), navigate to the CTK04AE installer menu, set ISU 4090 = OFF.
3. If transitioning B → A: same path, set ISU 4090 = ON.
4. If staying on the same arm (within a 2-week run): no action needed.
5. Log the action to `hvac.arm_transitions` measurement with timestamp and outcome.

### Automation (v2, if feasible)

Investigate whether the Cinegration C4 driver exposes ISU 4090 as a writable parameter. If yes, automate the toggle from `execute_action()` at the Monday 00:00 transition. If no, manual procedure stays as v1.

### New InfluxDB measurement

```
measurement: hvac.arm_transitions
tags: from_arm (A or B), to_arm (A or B)
fields:
  air_setting: "on" | "off"
  manual_or_auto: "manual" | "auto"
  pi_dry_run: bool
```

### Validation criterion

For at least the first three Monday transitions of summer 2026, verify (via thermostat readout in TCC):
- AIR is ON when starting an Arm A week
- AIR is OFF when starting an Arm B week
- The transition was logged

---

## 6. Dry-run mode validation

The Pi scheduler already supports a dry-run flag (need to verify the exact env var). The mode must:

1. Continue all classification, schedule firing, price-overlay evaluation, and 5CP detection
2. Log every intended action to `hvac.actions` with a `dry_run=true` field
3. **NOT push setpoints to Control4 / TCC**
4. Allow the CTK04AE-programmed fallback schedule to run unobstructed

### Verification procedure

Before randomization begins:

1. Run scheduler in dry-run for 24 hours during a NORMAL forecast day.
2. Confirm via Control4 logs and TCC history that no setpoint pushes occurred from the Pi.
3. Confirm `hvac.actions` shows expected scheduled actions with `dry_run=true`.
4. Confirm CTK04AE-programmed schedule executed its 4 daily comfort settings (Wake / Leave / Return / Sleep) per the fallback documented in HVAC_LOGIC.md.

### Env var

Verify the existing scheduler exposes a config like `HVAC_SCHEDULER_DRY_RUN=true|false`. If not, add it. The arm-transition procedure (Mondays) sets this env var alongside the AIR toggle.

### Validation criterion

The 24-hour dry-run produces:
- Zero Pi-originated setpoint pushes (verified via Control4 hvac.thermostat measurement showing only TCC-originated setpoint changes)
- Complete `hvac.actions` log of intended actions
- Intact CTK04AE-programmed schedule execution

---

## 7. Pre-cool deepening (forecast-driven)

Already partially implemented as `HOT_STREAK_DAY1` schedule (multi-day heat trigger). Extend to also trigger on forecast 5CP risk:

**Location:** [`decide_day_type()`](../deploy/energy-stack/hvac-scheduler/app.py) line 409, evaluated at 21:00 the night before.

**Architecture (locked):** overnight pre-cool stays primary in both NORMAL and HOT schedules. The day-ahead ComEd price forecast adds a secondary depth/timing modulation layer on top of the temperature-driven base decision. This handles the case observed in 2025-2026 data where shoulder-season days with weather-mild forecasts (no day-type-driven pre-cool trigger) had negative-price afternoon windows followed by evening spikes — those windows are now capturable via the price-aware modulation layer without restructuring the temperature-driven pre-cool decision for hot summer days.

**Three pre-cool decisions, in order of trigger sensitivity:**

1. **Base pre-cool** (temperature-driven, schedule baseline). Already in the locked day-type tables. NORMAL pre-cools to 70°F at 06:00; HOT pre-cools to 68°F at 04:00.

2. **Pre-cool deepening** (forecast 5CP risk). Decision rule:

    ```python
    def should_deepen_precool(
        forecast_tomorrow: dict, season_5th_mw: float
    ) -> bool:
        return (
            forecast_tomorrow.get("peak_load_mw", 0) > season_5th_mw * 1.05
            and forecast_tomorrow.get("max_temp_f", 0) >= 90
        )
    ```
    If true, override the next day's HOT schedule to use the existing `HOT_STREAK_DAY1` schedule (03:00 start at 66°F). Triggers on single-day forecast 5CP risk, in addition to the existing multi-day-heat trigger.

3. **Day-ahead price-modulated pre-cool** (NEW). Decision rule:

    ```python
    def should_add_price_aware_precool(
        day_ahead_prices: list[float],  # 24 hourly forecast prices for tomorrow
        forecast_tomorrow: dict,
    ) -> Optional[dict]:
        """Returns {hour_ct, depth_f} if a price-aware pre-cool window is identified,
        else None.

        Triggers when day-ahead forecast shows BOTH:
        - At least one cheap window (>= 2 hours below 3 c/kWh) AND
        - At least one elevated window later in the day (>= 1 hour above 10 c/kWh
          per the locked elevated-tier threshold), where the elevated window starts
          at least 4 hours after the cheap window's start.

        Identifies the cheapest 2-hour window within forecast_tomorrow's 06:00-15:00
        CT range. Returns the start hour and depth scaled by the magnitude of the
        forecast spike (deeper for bigger spikes, capped at 66 F).
        """
        ...
    ```
    Adds a supplementary pre-cool action when the day-ahead price forecast warrants it AND the base decision would have either skipped pre-cool entirely or pre-cooled at a sub-optimal time. Does not override the base decision; layers on top.

**Layer interaction for pre-cool decisions:**

```
final_precool_actions = base_schedule_precool   # always (per day-type)
if should_deepen_precool(...):
    final_precool_actions = HOT_STREAK_DAY1_actions   # overrides base depth/timing
if (price_aware_window := should_add_price_aware_precool(...)):
    final_precool_actions += additional_action(price_aware_window)   # layered on top
```

If pre-cool would land on the same hour through multiple decisions, deepest setpoint wins.

### Test

Unit tests for `should_deepen_precool`:
- Tomorrow forecast 95°F + tomorrow PJM peak forecast 145,000 MW + season 5th = 130,000 MW → trigger
- Tomorrow forecast 95°F + tomorrow PJM peak forecast 130,000 MW → no trigger (peak ratio not exceeded)
- Tomorrow forecast 88°F + tomorrow PJM peak forecast 145,000 MW → no trigger (temp threshold not met)

Unit tests for `should_add_price_aware_precool`:
- Day-ahead has 12-15 CT at 1¢ and 18-19 CT at 12¢ → trigger (cheap+evening spike pattern)
- Day-ahead all hours between 3-9¢ → no trigger (no cheap window)
- Day-ahead all hours between 1-3¢ → no trigger (no spike to coast against)
- Day-ahead has 02-05 CT at -1¢ and 18-19 CT at 15¢ → trigger (negative night, evening spike)
- Day-ahead has 12-15 CT at 1¢ and 14-15 CT at 11¢ → no trigger (spike too close to cheap window, less than 4h gap)

---

## 8. Sequencing and dependencies

### Critical path to June 1

| Item | Target | Blocker |
|---|---|---|
| §0b: PJM `hrl_load_metered` cadence migration (weekly → hourly) | 2026-05-12 | None (small change to existing poller) |
| §0a: NWS poller migration to `forecastGridData` | 2026-05-15 | None (parallel-run validation in window) |
| Day-type classifier recalibration | 2026-05-16 | §0a complete (reads `apparent_max_f`) |
| Layer priority resolution refactor | 2026-05-18 | None |
| Price overlay module + tests | 2026-05-20 | Layer priority refactor |
| PJM 5CP detection module + tests | 2026-05-22 | §0b complete (reads hourly `pjm.metered_load`) |
| AIR toggling procedure documented | 2026-05-22 | Test C4 driver capability |
| Pre-cool deepening forecast trigger | 2026-05-25 | None (small change) |
| Dry-run mode 24-hour validation | 2026-05-25 | All above |
| Full integration test (replay 2025 data) | 2026-05-28 | All above |
| OSF pre-registration filing | 2026-05-30 | Full integration test passing |
| Arm assignment list locked at OSF commit hash | 2026-05-30 | None |
| Randomization begins | 2026-06-01 | Everything above |

### Hard dependencies (resolved)

- **PJM hourly metered zone load:** resolved via §0b (modify existing `hrl_load_metered` feed schedule from weekly to hourly with shorter lookback). No new feed needed.
- **NWS apparent temperature:** resolved via §0a (migrate `nws-poller` from `forecastHourly` to `forecastGridData` endpoint, which provides `apparentTemperature` natively). No local Rothfusz derivation; uses NWS-authoritative value.
- **Control4 driver capability for ISU 4090.** Determines whether AIR toggling automates (v2) or stays manual (v1). Investigate during the AIR procedure documentation work; manual procedure documented as v1 regardless.

### Soft dependencies

- **Thermal model.** Optional Arm B component per EXPERIMENT_DESIGN. Not on critical path; if not ratified by June 1, Arm B ships without thermal-model integration and the limitation is reported.
- **Ecowitt deployment.** Affects thermal model timeline only.

---

## 9. Test plan summary

### Unit tests (must pass before deployment)

- Day-type classifier: 5 cases per locked threshold
- Price overlay: 8 cases covering tier transitions, hysteresis, hold time
- 5CP detection: 8 cases covering all triggers and release conditions
- Layer priority: 6 cases covering interactions
- Pre-cool deepening: 3 cases

### Integration tests

1. **Replay May 2025 ComEd prices through dry-run scheduler.** Verify price overlay tier counts match historical analysis (157 elevated hours, 38 scarcity hours over 5 months).
2. **Replay June 24, 2025 PJM zone load through 5CP detector.** Verify trigger during the 14-19 CT window.
3. **24-hour dry-run with current scheduler in dry-run mode.** Verify zero Pi-originated setpoint pushes; CTK04AE schedule runs intact.
4. **Full Arm B simulation against 2025 weather + price data.** End-to-end check that the layered logic produces sensible setpoints across diverse 2025 conditions.

### Field validation

- Two-week shakedown period in late May 2026 with Arm B running on the live system (no formal arm assignment yet). Watch for unexpected setpoint pushes, layer-resolution edge cases, or supervisor clamps. Address any issues before June 1.

---

## 10. Acceptance criteria for OSF filing

OSF filing proceeds only when ALL of the following are true:

1. ✅ All unit tests passing on `hvac-scheduler` repo
2. ✅ All integration tests passing on replay data
3. ✅ 24-hour dry-run validation completed successfully
4. ✅ AIR toggling procedure documented and tested at least once
5. ✅ All InfluxDB measurements writing correctly: new (`hvac.price_overlay`, `hvac.5cp_state`, `hvac.arm_transitions`) and updated (`pjm.metered_load` zone="CE" hourly cadence; `nws.forecast` with new `apparent_*`, `rh_*`, `sky_cover_avg_pct`, `wind_gust_max_mph`, `precip_prob_max_pct` fields)
6. ✅ Two-week shakedown period completed without unresolved issues
7. ✅ Assignment CSV ([`docs/experiment-assignments-summer-2026.csv`](experiment-assignments-summer-2026.csv)) regenerated with the locked seed and committed
8. ✅ EXPERIMENT_DESIGN.md frozen at the OSF-referenced commit hash

If any of these is incomplete by 2026-05-30, OSF filing slips and randomization start date moves accordingly.
