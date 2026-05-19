---
name: CONTROLLER_CONSTANTS
date: 2026-05-18
owner: chris
status: active
role-label: binding-reference
extracted_from: archive/EXPERIMENT_DESIGN.md (Appendix A)
extraction_pr: docs/plans/pre-osf-doc-audit-execution-2026-05-18.md PR6
related:
  - plans/sced-rebaseline-spec-2026-05-13.md
  - HVAC_LOGIC.md
---

# Locked controller constants (Arm B)

Threshold values for the Arm B logic, locked to 2025 ComEd RTP and Plainfield-IL weather data analysis (May-September 2025, n=3,663 hourly observations across the full cooling season, n=122 summer days for the Jun-Sep day-level statistics). The full distribution analysis, hour-of-day patterns, threshold-frequency tables, scarcity-event days, and weather correlation results are reproduced by the frozen analysis bundle at [`tools/comed_2025_analysis/`](../tools/comed_2025_analysis/) (committed alongside the OSF filing; see [`tools/comed_2025_analysis/README.md`](../tools/comed_2025_analysis/README.md) for a claim-by-script mapping and [`tools/comed_2025_analysis/expected_output.md`](../tools/comed_2025_analysis/expected_output.md) for frozen expected output).

This page extracts what was originally `EXPERIMENT_DESIGN.md` Appendix A so that the binding controller constants live as a current top-level reference rather than buried at the bottom of a superseded doc. The values here are verbatim from Appendix A and verified against deployed code per [`docs/plans/pre-osf-doc-audit-truth-tables-2026-05-18.md`](plans/pre-osf-doc-audit-truth-tables-2026-05-18.md) (17/17 quantitative claims match production code as of 2026-05-18).

Headline observations driving these thresholds:

- **Distributional percentiles** over the May-Sep 2025 cooling-season hourly distribution (n=3,663): P95 = 9.53¢/kWh, P99 = 20.47¢/kWh. (The narrower Jun-Sep subset yields P95 = 10.16¢/kWh, P99 = 23.48¢/kWh; the locked 10¢/20¢ trigger pair sits between these two framings.)
- **Hour-of-day pattern** over Jun-Sep 2025: 18:00 CT is the highest-mean hour (mean 11.03¢/kWh) and the highest-frequency spike hour (23.8% of its hours ≥10¢, the highest fraction of any hour). The overall hourly max in the dataset was 161.29¢/kWh at 17:00 CT on 2025-06-24; 18:00 CT's own max was 146.29¢/kWh on the same day. Both 17:00 and 18:00 fall outside the original 14-18 CT scheduler shutoff window's effective center.
- **Day-level forecast tractability:** 8 of 17 scarcity days had max temp <87°F (motivating real-time price reactivity rather than pure forecast-driven control).

All values pre-committed before OSF filing and frozen at the OSF commit hash.

## Day-type classification (recalibrated)

| Day-type | Trigger | Notes |
|---|---|---|
| MILD | forecast max < 75°F | No active scheduling |
| NORMAL | 75-85°F max | Standard pre-cool / coast / recover / sleep |
| HOT | ≥85°F max OR apparent ≥90°F | Aggressive pre-cool, shutoff during scarcity-risk hours |
| HOT_STREAK_DAY1 | HOT today AND HOT tomorrow | Deeper / earlier pre-cool to bank multi-day mass |

Recalibration captures 54% of historical price-spike days and 71% of scarcity days. The remaining 46% of spike days are grid-event-driven and addressed by real-time price reactivity. (Both figures reproduced by [`tools/comed_2025_analysis/verify_appendix_a.py`](../tools/comed_2025_analysis/verify_appendix_a.py).)

**Naming note:** the spec-canonical enum value `HOT` is implemented in code as the constant `DAYTYPE_HOT = "HOT_5CP_RISK"` (see [`deploy/energy-stack/hvac-scheduler/app.py`](../deploy/energy-stack/hvac-scheduler/app.py)). Both names refer to the same enum value with the same trigger rule. [`docs/HVAC_LOGIC.md`](HVAC_LOGIC.md) uses the code-canonical `HOT_5CP_RISK` string in its day-types table.

## Real-time RTP price-spike reactivity

**Threshold derivation vs application.** The 10¢ and 20¢ trigger values are anchored on the P95 and P99 of the 2025 ComEd **hourly**-price distribution (9.53¢ and 20.47¢ respectively) as economically interpretable signal levels for "elevated" and "scarcity" regimes. They are **applied to the latest 5-minute ComEd RTP print**, not the hourly average, so the controller reacts inside the hour rather than waiting for the hourly average to settle. Applying hourly-derived thresholds to 5-minute prints means the controller fires on a wider fraction of 5-minute intervals than the P95-of-hourly framing implies — by design. The goal is to catch hourly spike regimes early, before they fully resolve into the hourly average. The 30-minute minimum hold and 2¢ hysteresis prevent oscillation from single-tick spikes. The thresholds are **frozen early-action signals, not pure percentiles of any one series**.

| Parameter | Value | Basis |
|---|---|---|
| Elevated trigger | 10¢/kWh | Early-action threshold; ≈ P95 of 2025 hourly distribution (9.53¢/kWh). Applied to latest 5-min ComEd RTP print (see derivation-vs-application paragraph above). |
| Elevated release | 8¢/kWh | 2¢ hysteresis |
| Scarcity trigger | 20¢/kWh | Early-action threshold; ≈ P99 of 2025 hourly distribution (20.47¢/kWh). Same 5-min application semantics. |
| Scarcity release | 18¢/kWh | 2¢ hysteresis |
| Minimum hold | 30 min | Prevent thrashing on borderline prices / single-tick spikes |
| Elevated offset | +3°F to active cool setpoint | Meaningful pull-back without abandoning comfort |
| Scarcity setpoint | 85°F (effective shutoff) | Equipment-safe upper bound below the safety supervisor's 86°F ceiling; carries the shutoff role now that the fixed-window HOT_5CP_SHUTOFF schedule action is dropped |

## PJM 5CP-eligibility detection (dual-scope)

Two scopes run in parallel; effective shutoff trigger is the OR of their per-scope decisions. Each scope reads its own pair of PJM Data Miner 2 feeds; the locked decision rule (load ratio, derivative, time window, forecast gate, hold semantics) is identical per scope but parameterized over (`area`, `zone`, `pre_season_fallback_5th_mw`).

**Data lineage per scope** (per PJM DM2 OpenAPI spec):

| Scope | `current_load_mw` feed | `season_to_date_5th_highest_mw` feed | `forecast_peak_today_mw` feed |
|---|---|---|---|
| `comed_zone` | `inst_load?area=COMED` (~5-min cadence) | `hrl_load_metered?zone=CE` (hourly, 1-2 day publish lag, 90-day correction window) | `load_frcstd_7_day?forecast_area=COMED` max over today's 24 hours |
| `rto` | `inst_load?area=PJM RTO` (~5-min cadence) | `hrl_load_metered?zone=RTO` (hourly aggregate of the entire PJM footprint, same cadence/lag as zonal) | `ops_sum_frcst_peak_rto?area=PJM RTO` latest `load_forecast_mw` (scalar daily peak) |

The `inst_load` feed is described in the PJM spec as "approximate, NOT official PJM Loads" but "frequently updated throughout the operating day" — the right tradeoff for a real-time directional signal. The `hrl_load_metered` feed carries official metered values that determine the actual 5CP rank. **Per-scope forecast peak** is required because the gate condition (`forecast_peak > season_to_date_5th_highest`) is unsatisfiable cross-scale — a ComEd-area forecast (~10-22 GW) never exceeds an RTO-scale season 5th-highest (~150 GW), which silently disables the RTO scope if a shared forecast value is used. Six feeds total per tick.

| Parameter | Value | Basis |
|---|---|---|
| Load-ratio trigger | `current_load_mw(scope) / season_to_date_5th_highest_mw(scope) > 0.95` | Allows for prediction error, catches ramp-up. Per-scope; ComEd-zone ratio and RTO ratio evaluated independently. |
| Window | 13:00-20:00 CT (time-of-day) | Broadened from current 14-18 CT; 2025 RTO peak hour was 18:00 CT |
| Summer eligibility gate | June 1 - September 30 (date) | Per PJM Manual 19 and ComEd Attachment M-2, 5CP eligibility is restricted to this window. Detector short-circuits to inactive outside Jun-Sep (state machines reset across the boundary so a hold cannot cross Sep 30 -> Oct 1). Season-5th computation is bracketed to the same window so off-season rows in the bucket cannot infiltrate the baseline. |
| Hold | end-of-hour + 30 min, per scope | Independent state machines: a ComEd-zone release does not exit an RTO-scope hold or vice versa |
| ComEd-zone pre-season fallback | 20,375 MW | 2025 ComEd-zone 5th-highest hourly metered load (empirical, `pjm.metered_load{zone=CE}`). Replaces a prior 130,000 MW value that was RTO-scale misapplied to the zone path (left the ComEd detector inert pre-season). |
| RTO pre-season fallback | 151,525 MW | 2025 PJM RTO 5th-highest published 5CP (PJM Summer 2025 5CPs report) |
| Pre-cool deepen forecast trigger | tomorrow_peak > season_5th × 1.05 AND high ≥ 90°F | Forecast-confident heat day. Currently evaluated on ComEd-zone scope only (the §7 night-before pre-positioning); the live detector handles dual-scope at the actual peak hour. |
| Pre-cool deepen action | 03:00 start at 66°F (vs default 04:00 at 68°F) | Matches existing HOT_STREAK_DAY1 |

## Layer priority (warmer wins, safety supervisor floor)

```
effective = max(schedule + humid_override,
                schedule + price_overlay,
                5cp_shutoff_setpoint)
effective = clamp(effective, 65, 86)
```

## Day-of-week awareness

**None.** Reported descriptively in the paper. 2025 data shows Sat with zero scarcity hours and Mon-Tue with 25 combined; insufficient seasons of data to encode in the controller.
