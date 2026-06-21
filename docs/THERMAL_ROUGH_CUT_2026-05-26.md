---
date: 2026-05-26
owner: chris
status: pre-OSF rough cut
role-label: analysis
name: thermal-rough-cut-2026-05-26
---

# Thermal Rough-Cut Pre-OSF Analysis (2026-05-26)

## Purpose

Empirical first-order fit of house thermal response from existing telemetry, intended as a **pre-OSF sanity check** of the hand-tuned scheduler constants in `deploy/energy-stack/hvac_scheduler/app.py`. Not an optimizer; not a replacement for the planned post-experiment RC envelope fit. The lock policy is: **don't tune any constant unless a check fails AND the confidence is high.**

## Source data

- InfluxDB on pi-lab, bucket `energy`, org `depaola-home`. Pull via SSH + `docker exec influxdb influx query`. The pull/fit/project scripts were run from a one-off `tools/thermal_rough_cut/` directory and deleted after this report was written — the artifact of value is this report + the high-res field addition it triggered, not the throwaway analysis scripts. Influx still has the source telemetry; any future refit re-derives from there.
- Aligned window: 2026-05-12 to 2026-05-26 (~14 days, bound by `ecowitt.weather` start date).
- 4313 5-min rows after alignment + outer-join + 10-min forward-fill tolerance.
- 29.2% of samples excluded due to setpoint changes (±30 min) or `hvac.actions` supervisor/humid pushes (±30 min).

## Models fit

Two models per sensor:

```
AC-off:   dT/dt = a · (T_out − T_in) + b · solar_wm2 + c
AC-on:    dT/dt = a · (T_out − T_in) + b · solar_wm2 − d   (per stage)
```

**Sensors:** RedLINK primary BR (control sensor); ecowitt ch2 (pre-move hallway location); ecowitt ch3 (pre-move S-BR location); ecowitt gateway (1F office).

**Note on ch2/ch3 location boundary:** sensors physically moved at ~02:25 CDT on 2026-05-26 (memory `ecowitt-move-event-2026-05-25` records 5pm CDT 2026-05-25 but actual move was later, verified via the 81.68°F body-heat spike in `ch2_temp_f` at 07:25 UTC 2026-05-26). Pre-move ch2/ch3 fits apply to their prior physical locations, NOT to the current 2F-hallway / 2F-S-BR placement. Post-move data (~30h) is too sparse for an independent fit.

## Fit results

### AC-off drift fits (the headline)

| Sensor | n | R² | RMSE °F/h | `a` (per °F ΔT) ± SE | `b` (per W/m²) ± SE | intercept ± SE |
|---|---|---|---|---|---|---|
| RedLINK primary BR | 64 | 0.47 | 0.23 | **0.0246 ± 0.0042** | -0.00010 ± 0.00020 (n.s.) | 0.387 ± 0.074 |
| ch2 (pre-move) | 20 | 0.73 | 0.22 | **0.0215 ± 0.0119** | **0.00084 ± 0.00043** | 0.154 ± 0.135 |
| ch3 (pre-move) | 20 | 0.28 | 0.34 | 0.0190 ± 0.0170 | 0.00022 ± 0.00062 (n.s.) | 0.288 ± 0.199 |
| gateway 1F office | 64 | 0.22 | 0.78 | 0.0333 ± 0.0165 | 0.00084 ± 0.00067 | 0.491 ± 0.252 |

Cross-check against Codex's empirical pull:
- Hallway drift at ΔT=7°F, solar=600 W/m² (typical afternoon) using ch2 fit: `0.022·7 + 0.00084·600 + 0.15 = 0.81°F/h`. Codex measured 0.58-0.91°F/h. Consistent.
- RedLINK at ΔT=8°F, solar=0 (cloudy): `0.025·8 + 0.39 = 0.59°F/h`. Today's observed 13:30-16:00 drift: 73°F → 75°F over 2.5h = 0.8°F/h at ΔT≈12°F. RedLINK fit predicts `0.025·12 + 0.39 = 0.69°F/h`. Within RMSE.

**Solar coefficient notes:** RedLINK and ch3 have non-significant solar coefficients (SE ≥ |coefficient|). RedLINK is in a N-shaded room — direct solar contribution is small and lagged through wall conduction. ch3 pre-move location had high solar exposure but sparse data; the post-move location (S-BR, no shade) is expected to have much higher `b` once data accumulates.

### AC-on stage 1/2 fits (degenerate; not usable)

| Sensor | Stage | n windows | R² | Verdict |
|---|---|---|---|---|
| RedLINK | 1 | 21 | n.a. (zero variance) | All windows have dT/dt = 0 exactly — thermostat reports indoor in whole °F, so within any short stable AC-on window the measured slope is mechanically zero |
| RedLINK | 2 | 2 | n.a. | insufficient data |
| ch2 | 1 | 3 | n.a. | insufficient data |
| ch3 | 1 | 3 | n.a. | insufficient data |
| ch2/ch3 | 2 | 2 | n.a. | insufficient data |
| gateway | 1 | 21 | 0.07 | Coefficients in noise; not usable |
| gateway | 2 | 2 | n.a. | insufficient data |

**Conclusion:** the linear-regression-on-dT/dt approach for AC-on windows does not work *with the current `indoor_temp_f` field* due to (a) 1°F quantization in the field's source + control-loop pinning of indoor to setpoint and (b) sparse stage 2 events.

For recovery-cost estimates in this rough cut, the projection step uses hand-anchored capacities (stage 1: 1.6 °F/h cooling effect / 1.0 kW power; stage 2: 3.6 °F/h / 2.1 kW) derived from manufacturer nameplate + Codex's empirical power readings + observed today-evening recovery rate. **These are order-of-magnitude estimates, not fit-supported values.** Any threshold check that depends on them carries LOW confidence.

### Director-probe finding: fractional indoor temp IS available

The 1°F resolution limit on `indoor_temp_f` is NOT a Control4 driver limitation — it's a wrong-variable choice. The `thermostat_poller` reads via `pyControl4.climate.C4Climate.get_current_temperature_f()`, which returns the Director's `TEMPERATURE_F` variable (whole-degree). The Director ALSO exposes `TEMPERATURE_C` with 0.1°C resolution (≈0.18°F effective), and pyControl4 has a matching `get_current_temperature_c()` method.

Verified on pi-lab 2026-05-26 by probing `director.get_item_variables(thermostat_id)` directly:

| Director variable | Value | Resolution | Implied °F |
|---|---|---|---|
| `TEMPERATURE_F` (currently polled) | 78 | 1°F | 78.0 |
| `TEMPERATURE_C` | 25.5 | 0.1°C | **77.9** |
| `COOL_SETPOINT_C` | 25.5 | 0.1°C | 77.9 |
| `HEAT_SETPOINT_C` | 19 | 0.1°C | 66.2 |

**Disposition: add a derived `indoor_temp_f_hires` field to `thermostat_poller` in the same PR as this report.** Pure additive change (~5 lines in `deploy/energy-stack/thermostat_poller/poller.py`: read `TEMPERATURE_C`, convert to °F, write alongside existing `indoor_temp_f`). Doesn't change the existing field, the scheduler's behavior, or any locked parameter. The experiment compares aggregate Arm A vs Arm B energy/cost over weeks; telemetry resolution doesn't enter the comparison.

The high-res field starts collecting from deployment moment onward, so the full 24-week experiment has it. Post-experiment thermal characterization (planned per `docs/archive/THERMAL_MODEL_DESIGN.md`) can refit stage-1/2 cooling rates with usable variance instead of the mechanical zeros that defeated this rough cut.

## Archetype projections

Four archetype days (`spring_mild_today`, `summer_hot_dry`, `summer_hot_humid`, `summer_extreme`) projected against three schedules (`arm_a`, `arm_b_hot`, `arm_b_streak_day1`). The `_humid` variants of Arm B HOT schedules activate when archetype dewpoint > 65°F (matches the `HUMID_DEWPOINT_F=65` constant in scheduler code).

Selected highlights from the projections:

### Sanity check: spring_mild_today (matches actual 2026-05-26 conditions)

| Sensor | Arm | Peak indoor | 17:00 CT | 19:00 CT |
|---|---|---|---|---|
| RedLINK | A (78°F coast) | 77.8°F | 76.9°F | 77.8°F |
| ch2 hallway | A | 78.9°F | 78.0°F | 78.9°F |

Actual observation today at 16:00 CT: RedLINK 75°F, ch2 76.3°F. Projection runs a bit warmer than observed (likely because the model doesn't fully capture the residual cooling from the deep precool to 73°F + the AC's stage-1 cycling during the recovery from any drift). Order-of-magnitude consistent.

### Extreme day (100°F, dewpoint 70°F, poor overnight relief)

| Sensor | Arm | Peak indoor | 17:00 CT | Stage 2 hours |
|---|---|---|---|---|
| RedLINK | A (fixed 73→78→75→73) | 79.0°F | 77.5°F | 1 |
| RedLINK | B HOT humid (precool 68, coast 76, humid override active) | 76.1°F | 74.3°F | 1 |
| RedLINK | B STREAK humid (precool 66) | 76.5°F | 73.1°F | 1 |
| ch2 hallway | A | 79.3°F | 79.3°F | 1 |
| ch2 hallway | B HOT humid | 77.5°F | 77.5°F | 0 |
| ch3 S-BR | A | 79.5°F | 78.2°F | 1 |

## 6-row "obviously bad constant" check

Pre-defined thresholds, evaluated against the archetype projections:

| # | Check | Observed | Threshold | Verdict | Confidence |
|---|---|---|---|---|---|
| 1 | Dry-hot hallway at 17:00 CT under Arm B HOT (coast=80°F) | 76.8°F | ≤82°F | **PASS** | medium |
| 2 | Humid-hot hallway at 17:00 CT under Arm B HOT humid override (coast=76°F) | 75.7°F | ≤79°F | **PASS** | medium |
| 3 | HOT_STREAK_DAY1 66°F precool achievable from 73°F start by 12:00 CT | 68.0°F | ≤67°F | **FAIL** | LOW |
| 4 | Dry-hot S-BR at 17:00 CT under Arm B HOT | 74.2°F | ≤85°F | **PASS** | LOW |
| 5 | Recovery uses stage 2 on dry-hot day under Arm B HOT | 1 h | ≤0.5 h | **FAIL** | LOW |
| 6 | Extreme-day RedLINK at 17:00 CT under Arm A (78°F coast) | 77.5°F | ≤80°F | **PASS** | medium |

### Disposition of the two FAILs

**Both are LOW confidence.** Per the pre-OSF lock policy ("don't tune any constant unless a check fails AND the confidence is high"), neither warrants a parameter change before filing. They become signals to watch in the experiment.

**Row #3 (precool 66°F achievability):** model projects indoor reaches 68°F by noon under STREAK precool target 66°F starting 73°F. Off by 2°F. LOW confidence because: (a) stage 1 cooling capacity is hand-anchored, not fit; (b) extrapolation from spring data; (c) the model's 1-hour AC engagement granularity over-counts kWh and under-counts cooling effect compared to real cycling. The real test is whether the scheduler can drive indoor to ~70°F or lower by noon on a real August streak-day-1; if it stalls at 73°F, the 66°F target is aspirational and the schedule's HOT_COAST 80°F target is overoptimistic in its assumed precool depth.

**Row #5 (stage 2 use during recovery):** model projects 1 hour of stage 2 during 19:00-20:00 CT Return-block recovery on a dry-hot day. LOW confidence for the same reasons — hand-anchored stage capacities. The real concern this is flagging: if recovery from 80°F coast to 75°F Return requires stage 2 on hot days, that's elevated kWh during early-evening price hours which can exceed 10-15¢/kWh. Worth watching in the actual experiment, not worth changing the schedule pre-OSF.

**Both flagged for the OSF deposit's "known limitations" section,** so post-experiment refit can revisit specifically.

## What this analysis does NOT establish

- **True envelope time constant τ.** The first-order linear OLS approximates τ ≈ 1/a, giving τ ≈ 40h for RedLINK, 47h for ch2. These values are high and probably overestimate τ because the linear-in-ΔT model loses fidelity at large ΔT.
- **Stage 2 cooling capacity.** Hand-anchored, not measured.
- **S-BR post-move drift.** Sensor was relocated to S-facing 2F bedroom at ~02:25 CDT 2026-05-26. Pre-move ch3 location was different; fit numbers above don't apply to current placement.
- **Humid-day behavior in the fit period.** No high-dewpoint days in the 14-day window (today's dewpoint 50°F). Humid-archetype projections rely on the dry-day fit + an assumed scheduler humid-override response, not on humid-day measurement.

## Decision for pre-OSF lock

**No parameter changes.** All four sensor placements remain candidates; the hallway-placement lean stands. The 6-row check yields:

- 4 PASSes (medium confidence)
- 2 FAILs (both low confidence, both flagged as season-1 watch-items)

Per memory `feedback_research_locks_parameters` and the lock policy, this is below the bar for pre-OSF parameter revision. The scheduler's hand-tuned constants survive the rough cut.

## Related

- `docs/archive/THERMAL_MODEL_DESIGN.md` — planned post-experiment Bacher-Madsen-anchored RC envelope fit. This rough cut is intentionally simpler.
- `tools/comed_2025_analysis/` — Codex's frozen 2025 ComEd price analysis. The maintenance-vs-drift strategy debate that motivated this rough cut.
- `docs/superpowers/specs/2026-06-20-commissioning-controller-design.md` and `deploy/energy-stack/hvac_scheduler/app.py` — the controller this analysis was sanity-checking. (The old hand-tuned day-type constants doc was removed in the demolition; the current controller is config-driven per the spec.)
- `memory/feedback-no-epa-78-claim.md` — separate retraction made during the same conversation about EPA 78°F citation.
