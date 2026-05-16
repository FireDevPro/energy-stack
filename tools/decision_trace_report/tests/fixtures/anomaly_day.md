# Decision-trace commissioning report — 2026-05-15

_Rendered at 2026-05-16T08:00:00+00:00_

## Table of contents

- [Anomaly summary](#anomaly-summary)
- [§1 Night-before decision audit](#1-night-before-decision-audit)
- [§2 Live day-of decision audit](#2-live-day-of-decision-audit)
- [§3 Price spike reaction audit](#3-price-spike-reaction-audit)
- [§4 Feed + telemetry health](#4-feed--telemetry-health)
- [§5 Coverage scorecard](#5-coverage-scorecard)

## Anomaly summary

| Type | Count |
|---|---:|
| Unexpected reason codes | 1 |
| Supervisor non-approved decisions | 1 |
| Stale feeds | 1 |
| Trace-vs-Influx discrepancies | 1 |
| Unexplained price spikes | 1 |
| Query errors | 0 |

**Status: open the report**

## §1 Night-before decision audit — 2026-05-15

### Day-type decision

⚠️ No decision_trace.day_type_decision events found for this date.

`hvac.decisions` row present: `NORMAL` — possible trace/Influx disagreement (no trace to compare).
### §7 Precool decision

- selected: **False**, reason_code: `PRECOOL_REJECTED_NO_CHEAP_WINDOW`


## §2 Live day-of decision audit — 2026-05-15

| time | tick_id | event | winning_layer | schedule_cool_f | price_cool_f | fivecp_cool_f | effective_cool_f | sup_decision | sup_reason |
|---|---|---|---|---|---|---|---|---|---|
| 14:00:00-05:00 | `tick_bbb` | layer | price_overlay | 79 | 75 | 79 | 75 |  |  |
| 14:00:00-05:00 | `tick_bbb` | sup |  |  |  |  |  | clamped | `SUPERVISOR_CLAMPED_COOL_FLOOR` |


## §3 Price spike reaction audit — 2026-05-15

| spike time | price ¢/kWh | nearest trace | tier | reason_code | explained |
|---|---:|---|---|---|---|
| 2026-05-15T14:00:00-05:00 | 14.25 | 14:00:00-05:00 | `normal` | `PRICE_OVERLAY_NORMAL_BELOW_TRIGGER` | ❌ |


## §4 Feed + telemetry health

| Feed | Kind | Last write | Age / status | Verdict |
|---|---|---|---|---|
| `comed.prices` | continuous | 2026-05-16T07:59:00+00:00 | 1m | ✅ fresh |
| `nws.forecast` | continuous | 2026-05-16T07:59:00+00:00 | 1m | ✅ fresh |
| `refoss.channel` | continuous | 2026-05-16T07:59:00+00:00 | 1m | ✅ fresh |
| `hvac.thermostat` | continuous | 2026-05-16T07:59:00+00:00 | 1m | ✅ fresh |
| `haven.indoor` | continuous | 2026-05-16T05:00:00+00:00 | 3h0m | 🔴 stale |
| `pjm.inst_load` | continuous | 2026-05-16T07:59:00+00:00 | 1m | ✅ fresh |
| `pjm.lmp_da_hourly` | event | 2026-05-15T22:00:00+00:00 | caught up through 2026-05-15T22:00:00+00:00 | ✅ fresh |
| `pjm.metered_load` | event | 2026-05-10T07:00:00+00:00 | caught up through 2026-05-10T07:00:00+00:00 | ✅ fresh |


## §5 Coverage scorecard

### ⚠️ Unexpected reason codes (in trace but NOT in any enum)

- `MYSTERY_UNDOCUMENTED_CODE` — cumulative: 2, last 7d: 2

### DayTypeCode

| Code | Status | Cumulative | Last 7 days |
|---|---|---:|---:|
| `DAY_TYPE_HOT_HEAT_ADVISORY` | ⚪ not observed live | 0 | 0 |
| `DAY_TYPE_HOT_HIGH_GE_85` | ⚪ not observed live | 0 | 0 |
| `DAY_TYPE_HOT_APPARENT_GE_90` | ⚪ not observed live | 0 | 0 |
| `DAY_TYPE_HOT_STREAK_MULTI_DAY` | ⚪ not observed live | 0 | 0 |
| `DAY_TYPE_HOT_STREAK_5CP_RISK` | ⚪ not observed live | 0 | 0 |
| `DAY_TYPE_NORMAL_HIGH_75_TO_84` | ⚪ not observed live | 0 | 0 |
| `DAY_TYPE_NORMAL_MISSING_TEMPS_FALLBACK` | ⚪ not observed live | 0 | 0 |
| `DAY_TYPE_NORMAL_NO_FORECAST_FALLBACK` | ⚪ not observed live | 0 | 0 |
| `DAY_TYPE_MILD_HIGH_LT_75` | ⚪ not observed live | 0 | 0 |

### LayerResolutionCode

| Code | Status | Cumulative | Last 7 days |
|---|---|---:|---:|
| `LAYER_RESOLUTION_SCHEDULE_WINS` | ⚪ not observed live | 0 | 0 |
| `LAYER_RESOLUTION_PRICE_OVERLAY_WINS` | ✅ observed live | 1 | 1 |
| `LAYER_RESOLUTION_5CP_WINS` | ⚪ not observed live | 0 | 0 |
| `LAYER_RESOLUTION_TIE_WARMER_WINS` | ⚪ not observed live | 0 | 0 |

### PrecoolCode

| Code | Status | Cumulative | Last 7 days |
|---|---|---:|---:|
| `PRECOOL_SELECTED` | ⚪ not observed live | 0 | 0 |
| `PRECOOL_REJECTED_NO_DA_LMP_DATA` | ⚪ not observed live | 0 | 0 |
| `PRECOOL_REJECTED_NO_FORECAST` | ⚪ not observed live | 0 | 0 |
| `PRECOOL_REJECTED_DA_LMP_INCOMPLETE` | ⚪ not observed live | 0 | 0 |
| `PRECOOL_REJECTED_NO_CHEAP_WINDOW` | ✅ observed live | 3 | 3 |
| `PRECOOL_REJECTED_NO_SPIKE_WINDOW_AFTER_GAP` | ⚪ not observed live | 0 | 0 |

### PriceOverlayCode

| Code | Status | Cumulative | Last 7 days |
|---|---|---:|---:|
| `PRICE_OVERLAY_NORMAL_BELOW_TRIGGER` | ✅ observed live | 10 | 10 |
| `PRICE_OVERLAY_HELD_IN_TIER` | ⚪ not observed live | 0 | 0 |
| `PRICE_OVERLAY_UPGRADED_TO_ELEVATED` | ⚪ not observed live | 0 | 0 |
| `PRICE_OVERLAY_UPGRADED_TO_SCARCITY` | ⚪ not observed live | 0 | 0 |
| `PRICE_OVERLAY_DOWNGRADED_TO_ELEVATED` | ⚪ not observed live | 0 | 0 |
| `PRICE_OVERLAY_RELEASED_TO_NORMAL` | ⚪ not observed live | 0 | 0 |
| `PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED` | ⚪ not observed live | 0 | 0 |
| `PRICE_OVERLAY_STALE_FEED_RELEASED` | ⚪ not observed live | 0 | 0 |

### SupervisorCode

| Code | Status | Cumulative | Last 7 days |
|---|---|---:|---:|
| `SUPERVISOR_APPROVED` | ⚪ not observed live | 0 | 0 |
| `SUPERVISOR_CLAMPED_COOL_FLOOR` | ✅ observed live | 1 | 1 |
| `SUPERVISOR_CLAMPED_COOL_CEILING` | ⚪ not observed live | 0 | 0 |
| `SUPERVISOR_CLAMPED_HEAT_FLOOR` | ⚪ not observed live | 0 | 0 |
| `SUPERVISOR_CLAMPED_HEAT_CEILING` | ⚪ not observed live | 0 | 0 |
| `SUPERVISOR_CLAMPED_MULTIPLE` | ⚪ not observed live | 0 | 0 |
| `SUPERVISOR_EMERGENCY_OVERHEAT` | ⚪ not observed live | 0 | 0 |

