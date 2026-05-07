# Thermal Model — Step 1 Design (Affine Fit)

**Status**: Design proposed (2026-05-05), implementation gated on Ecowitt deployment + ~30 days of paired observation history
**Owner**: Chris dePaola
**Depends on**: existing `energy-stack` (InfluxDB `hvac.thermostat`, `hvac.comfortnet`, `nws.forecast`, `refoss.channel`) plus `weather.ecowitt` once that station is online
**Methodological anchor**: Bacher & Madsen 2011, *Identifying suitable models for the heat dynamics of buildings*, Energy and Buildings 43(7), [doi:10.1016/j.enbuild.2011.02.005](https://doi.org/10.1016/j.enbuild.2011.02.005)

---

## Problem

The `hvac-scheduler` today is house-agnostic. Day-type setbacks, pre-cool depths, COAST shutoff lead times, and the HOT-window pre-cool target are constants in `app.py`. They were chosen by hand to be "safe" for an unknown thermal envelope. That leaves money on the table on both sides of the trade:

- **Over-cooling on mild days**: pre-cool runs deeper than the envelope requires, which is wasted kWh and a comfort penalty (cold house at noon, then drifts back up by evening).
- **Under-cooling on hot streaks**: the COAST window assumes the house drifts up at an unknown rate, so the conservative shutoff lead time is short. If the actual time constant `τ` is longer than assumed, the scheduler is leaving free coast time on the table during 5CP-risk hours.

Field deployments of MPC schedulers for residential HVAC report median cost savings around **16%** (range ~10–25% in the more reliable studies; Khabbazi et al. 2025 meta-review of 24 residential + 80 commercial trials), with peak-demand reductions of **22–46%** under ToD pricing (ORNL residential MPC). Whole-building energy reductions of ~19% (CI 13–24%) are reported in the Kuznik 2023 Indiana single-family field test, and ~17% primary energy in the Sturzenegger 2016 7-month Swiss office trial. Numbers above ~30% on energy/cost in the literature almost always come from simulation, lab buildings, or specific sub-system metrics (e.g. backup-resistance frequency), not whole-building totals.

The contribution of **operational-data parameter identification specifically** (vs the MPC controller layer) is *not* isolated in the published literature — we found no study that cleanly decomposes (a) MPC + generic prototype RC vs (b) MPC + house-fitted RC vs (c) fixed-rule baseline. The 2025 meta-review explicitly flags this gap. Treat the parameter-ID contribution here as a hypothesis grounded in Bacher & Madsen's methodological argument that fit quality drives prediction skill, not a cited result. The cited methodology in every controller study above is some flavor of RC-network parameter identification fed by indoor temperature, outdoor temperature, equipment state, and solar irradiance.

We already log everything that methodology needs. The Ecowitt station fills the last gap (direct outdoor air temperature and solar W/m² at the house, instead of NWS gridpoint forecast).

## Goals

1. Identify a small set of house-specific thermal parameters from existing telemetry:
   - **`τ`** (thermal time constant, hours), the e-folding time for indoor air temperature to relax toward outdoor when no equipment is running.
   - **`P_cool_stage1`**, **`P_cool_stage2_delta`** (cooling rates, °F/hour at standard ΔT), the marginal cooling power of the AMVM971005CN + ASXC160481BE in stage 1 alone vs. stage 2.
   - **`Q_solar(hour)`** (a coarse hour-of-day solar gain function), absorbing solar load until the Ecowitt provides direct W/m².
2. Wire those parameters back into the scheduler in three concrete places:
   - **Pre-cool depth**: replace fixed setbacks with an integration of the envelope ODE that lands the house at the target indoor temperature exactly at the start of the HOT window.
   - **COAST shutoff lead time**: compute the latest shutoff that still leaves indoor at or below the comfort ceiling at end of window, given current outdoor and `τ`.
   - **Stage-1 vs stage-2 trade-off**: quantify the kW saved by holding stage 1 against the heat load it can actually carry, so the scheduler can refuse stage 2 during 5CP hours when stage 1 is sufficient.
3. Validate the fit against a holdout window before any setpoint logic depends on it.

## Non-goals

- **Manual J / Wrightsoft / EnergyPlus calibration**. Building-drawing-driven models are out of scope. The whole point of the operational-data approach is that we don't need them.
- **Component-level disaggregation**. We are fitting the lumped envelope, not separating wall/window/roof contributions or air-infiltration vs conduction.
- **Occupancy / internal-gain modeling**. Single occupant, one HVAC zone, no roommates. Internal gains roll into the residual.
- **Step 2 (2R2C grey-box)**. That is the next phase. Step 1 is gated only on having enough operational data; Step 2 is gated on whether Step 1 leaves visible structural residuals.

## Methodology

### The model

Continuous-time lumped capacitance:

```
C * dT_in/dt = (T_out - T_in) / R + Q_solar(t) + Q_hvac(t) + ε
```

where `C` is effective thermal mass, `R` is effective envelope resistance, `Q_solar` is solar gain, `Q_hvac` is equipment heat injection (negative when cooling), and `ε` is unmodeled internal gains. We don't separate `R` and `C` in Step 1; we fit the time constant `τ = R*C` directly.

Discretized at a fixed Δt (5 minutes is the design cadence — coarser than the 600 s VisionPRO poll interval would mean fewer samples; finer would mean fitting noise):

```
ΔT_in / Δt  =  (1/τ) * (T_out - T_in)                     [envelope]
            +  c_stage1 * I[stage1_active]                 [HVAC stage 1]
            +  c_stage2_delta * I[stage2_active]           [HVAC stage 2 marginal]
            +  Σ_k  α_k * φ_k(hour_of_day)                 [solar proxy]
            +  ε
```

`I[·]` is the indicator function. `φ_k` is a small basis (Step 1 plan: 6 piecewise-linear hat functions covering 0500–2000 local time, zero outside that range). Once Ecowitt is producing solar W/m², the solar term collapses to a single `α_solar * irradiance` and the basis is retired.

This is **linear in coefficients** (`1/τ`, `c_stage1`, `c_stage2_delta`, `α_k`). Step 1 is therefore an OLS fit, with diagnostics. No Kalman filter, no nonlinear optimizer, no ARMAX — those belong to Step 2.

### Sample filtering

The published Bacher–Madsen methodology and follow-ups (Aalborg/DTU group, NREL ResStock-Alfa) all apply pre-filtering before the regression. For Step 1 we apply:

- **Stage-transition mask**: drop the 5 minutes immediately after any HVAC stage change. Coil thermal mass means apparent cooling rate during the first cycle minute is not a steady-state estimate.
- **Setpoint-step mask**: drop the 30 minutes after any setpoint change. The thermostat aggressively races toward the new setpoint and produces non-steady samples.
- **Door/window events**: not currently logged. Flagged in the open-questions section. For now we accept the residual contamination; with a single-occupant household this is sparse.
- **Fan-only periods**: when `hvac_state == "fan"` (recirculate, no cooling), `Q_hvac = 0` and the sample is valid envelope data — keep it. These are the cleanest envelope-relaxation samples and matter most for `τ` identification.
- **Defrost / aux**: not relevant in cooling season. Flagged for the heating-season variant.

### Coefficient interpretation

- `1/τ` (units 1/hour). Reported as `τ_hours = 1 / (1/τ)`. Bacher-Madsen reported `τ ∈ [20, 200]` hours across their Danish residential sample. A modern US single-family with average insulation typically lands `τ ∈ [4, 20]` hours; we have no prior on this house specifically — that's the point of fitting it.
- `c_stage1`, `c_stage1 + c_stage2_delta` (units °F/hour at ΔT=0). Reported as `P_cool_stage1_F_per_hr` and `P_cool_stage2_F_per_hr`. The 2-stage 4-ton ASXC16 has a published nominal capacity of 48,000 BTU/hr at AHRI conditions; the fit gives the in-situ effective rate, which is what the scheduler should use rather than the nameplate.
- `Q_solar(hour)` is reported as a 6-element vector for visualization but isn't used directly by the scheduler — it gets absorbed into `Q_hvac` calibration since the scheduler decisions are made on day-aggregate quantities.

### Validation

Train/test split on the fit window:

- **Train**: oldest 80% of valid samples in the fit window.
- **Test**: newest 20%.
- **Metric**: one-step-ahead RMSE on `T_in[t+Δ] - T_in[t]` predictions, compared against a persistence baseline (`T_in[t+Δ] = T_in[t]`).
- **Skill score**: `S = 1 - RMSE_model / RMSE_persistence`. Bacher-Madsen reported `S ∈ [0.7, 0.85]` on 2-minute cadence. Our 5-minute cadence is coarser; **acceptance threshold for Step 1 to be wired into the scheduler is `S ≥ 0.5`**. Below that, the fit is rejected and the scheduler falls back to its current fixed-rule constants while we investigate.

A second sanity check: **physical plausibility gates**. `τ_hours ∈ [2, 48]` and `P_cool_stage2_F_per_hr ∈ [1.5, 8.0]`. A fit outside those bounds is rejected with a Telegram alert, regardless of what the skill score says. Catches catastrophic data issues (clock skew, wrong measurement, bad mask) before they reach setpoint logic.

## Data sources

All sources already write to InfluxDB except Ecowitt, which arrives in May 2026 and gets its own poller in a parallel PR.

| Source | Measurement | Fields used | Cadence today | Cadence assumed by Step 1 fit |
|---|---|---|---|---|
| `thermostat-poller` | `hvac.thermostat` | `indoor_temp_f`, `cool_setpoint_f`, `heat_setpoint_f`, `hvac_mode`, `hvac_state` | **600 s** (TCC rate floor) | 5 min — gap, see "Cadence reality check" below |
| `comfortnet` (CT-485 sniffer → MQTT → Telegraf) | `hvac.comfortnet` | `cl_act` (cool stage actual %), `hd_act`, `fan_act` | not yet flowing (publisher pending) | sub-minute |
| `refoss-poller` | `refoss.channel` | `power_w` on the HVAC circuit channels | 30 s | 30 s (independent kW witness, no gap) |
| `nws-poller` | `nws.forecast` | `high_f`, `low_f`, `max_dewpoint_f` | **daily rollup only** (today/tomorrow/day2) | hourly outdoor temp series — gap |
| Ecowitt (planned) | `weather.ecowitt` | `outdoor_temp_f`, `solar_radiation_w_m2`, `outdoor_humidity_pct` | not yet installed | 30 s |
| `hvac-scheduler` | `hvac.actions` | `cool_setpoint_f`, `applied`, `dry_run` | event-driven | event-driven (no gap) |

### Cadence reality check (CodeX 2026-05-07)

The fit methodology in the §Methodology section was specified at 5-minute cadence. The currently-shipped data sources can't supply that:

- **`hvac.thermostat` is 10-minute, not 5-minute** (Honeywell TCC rate-limit floor; not negotiable until ComfortNet provides sub-minute indoor signal).
- **`nws-poller` writes daily rollups only** (`for_period` ∈ {today, tomorrow, day2}). The hourly forecast endpoint is fetched internally but not retained as hourly points. Step 1's forward integration against hourly `T_out(t)` cannot run on the existing NWS measurement.
- **`hvac.comfortnet`** is the authoritative stage signal but is not yet flowing (the broker is up under compose profile `mqtt`, but the Pi-3B publisher hasn't shipped).

**Two paths to unblock Step 1**, in order of preference:

1. **Wait for Ecowitt + ComfortNet** (current plan). When both are live, indoor cadence comes from ComfortNet at sub-minute and outdoor from Ecowitt at 30 s. Fit is as designed.
2. **Resample-and-bound fallback**: rewrite the fit at **15-minute cadence** (LCM of 5 / 10 / 30 min), using `hvac.thermostat` directly and adding a new `for_period=hourly` write path to `nws-poller` (or pulling `forecastGridData` instead of `forecastHourly`). This loosens the Bacher–Madsen skill-score expectation (their `S ∈ [0.7, 0.85]` was at 2-minute cadence; at 15-minute we expect roughly `S ∈ [0.4, 0.65]`). Acceptance threshold drops to `S ≥ 0.35` for the fallback fit.

**Step 1 is therefore blocked on at least one of**: (a) ComfortNet publisher landing, (b) Ecowitt installation + ≥ 14 days of paired observations, OR (c) NWS-poller writing hourly forecast points + acceptance of the looser fallback skill threshold. The implementation PR can still land behind a `THERMAL_MODEL_ENABLED=false` flag, but the fit job won't ratify a model until one of those gates clears.

### Why Ecowitt is on the critical path

NWS gridpoint forecasts smooth across a 2.5 km grid cell and lag actual conditions at the house by 30–90 minutes during hot ramp-up. Fitting `1/τ` against a smoothed, lagged outdoor signal biases the time constant. The Ecowitt sensor co-located on the house gives the actual `T_out` driving the envelope, plus solar irradiance, plus humidity (which lets us do a saturation-enthalpy correction in Step 2 if needed). Per ASHRAE Handbook of Fundamentals (2021) Ch. 18, envelope identification with non-co-located weather is widely understood to inflate residuals; the gain from a $200 PWS is well-documented across the residential RC literature.

We do not block other thermal-model work on Ecowitt — Step 1 implementation can land before the station is online and run in "preliminary" mode against the fallback path above, with results stored to InfluxDB but explicitly marked unratified.

## Outputs

A single fit run writes one point to InfluxDB and one JSON file to `/data` on `pi-lab`.

### `hvac.thermal_model` (InfluxDB, `energy-longterm` bucket)

One point per fit run. Tagged `model_version = "step1.affine.v1"`.

| Field | Type | Notes |
|---|---|---|
| `tau_hours` | float | Thermal time constant. |
| `p_cool_stage1_f_per_hr` | float | Stage-1 cooling rate at ΔT=0. |
| `p_cool_stage2_f_per_hr` | float | Stage-2 (full capacity) cooling rate at ΔT=0. Reported as `c_stage1 + c_stage2_delta`, not just the marginal coefficient. |
| `solar_amplitude_f_per_hr` | float | Peak of `Q_solar(hour)` basis. Replaced by `solar_coupling_f_per_hr_per_w_m2` once Ecowitt is in. |
| `skill_score` | float | Hold-out skill vs persistence. |
| `train_rmse_f_per_5min` | float | Diagnostic. |
| `test_rmse_f_per_5min` | float | Diagnostic. |
| `sample_count_train` | int | |
| `sample_count_test` | int | |
| `fit_window_start` | string (ISO) | Oldest sample timestamp. |
| `fit_window_end` | string (ISO) | Newest sample timestamp. |
| `outdoor_source` | string | `"nws"` or `"ecowitt"`. Drives ratification. |
| `ratified` | int (0/1) | 1 if `skill_score ≥ 0.5` AND `outdoor_source == "ecowitt"` AND physical-plausibility gates pass. Scheduler reads only ratified rows. |

### `/data/thermal_model.json` (on `pi-lab`)

Latest ratified fit, materialized to disk so the scheduler doesn't query Influx on every decision tick. Same schema as the fields above. Pattern mirrors `/data/haven_token.json` (rotating refresh token persistence). The fit script writes both the Influx point and the JSON file atomically; the scheduler reads only the JSON file.

If the JSON file is missing or its `fit_window_end` is older than 60 days, the scheduler logs a warning and falls back to the existing fixed-rule constants. No setpoint logic ever runs against a stale fit silently.

## Scheduler integration

Three call sites in `deploy/energy-stack/hvac-scheduler/app.py`. All three are gated on the model being loaded and ratified; if not, the existing constants apply unchanged.

1. **Pre-cool depth (HOT_5CP_RISK / HOT_STREAK_DAY1)**. Today: fixed setpoint setback applied at start of pre-cool window. Replacement: integrate the envelope ODE forward from `now` to start of HOT window using forecast `T_out(t)` and the model parameters, solving for the pre-cool setpoint that brings indoor to the HOT-window target at HOT-window start with a configurable safety margin. The safety margin is the only remaining tunable.

2. **COAST window length**. Today: a fixed minutes-before-window-end shutoff. Replacement: solve for the latest shutoff time `t_off` such that the integrated envelope drift between `t_off` and end-of-window leaves indoor ≤ ceiling. With `τ` known this is a closed-form computation, no iteration needed.

3. **Stage-2 admission during 5CP candidate hours**. Today: the scheduler doesn't gate stage selection — that's the thermostat's call. Addition: when CT-485 reports stage 2 active during a 5CP candidate hour, log a `hvac.actions` advisory row noting the kW gap (`P_cool_stage2 - P_cool_stage1`) and indoor-rise estimate if held to stage 1. This is a **read-only advisory in Step 1**; gating logic that actually limits the thermostat to stage 1 is deferred until we have a clean control path (Step 3, separate design).

The integration touches roughly 80 lines of `app.py`, mostly in `decide_action()` and a new `thermal_model.py` helper module. Existing tests stay green; new tests cover the ratified-vs-unratified branch, the stale-file fallback, and the envelope integration math against synthetic fixtures.

## Code layout

```
deploy/energy-stack/
├── scripts/
│   └── fit_thermal_model.py        # standalone fit job, run nightly via cron on pi-lab
├── hvac-scheduler/
│   ├── app.py                      # +pre-cool / +COAST integration call sites
│   ├── thermal_model.py            # NEW: load JSON, integrate envelope ODE, expose helpers
│   └── test_hvac_scheduler.py      # +tests for ratified/unratified branches
└── (no new container — fit_thermal_model.py is a script, like parse_comed_bill.py)
```

The fit job is a script, not a service. It runs once per night via a cron entry on `pi-lab` (`0 2 * * *`), reads the prior 30 days from InfluxDB, runs the OLS fit + validation, writes the Influx point and `/data/thermal_model.json`. ~5 seconds per run. Lives in the repo for the same reason `parse_comed_bill.py` does — future-Claude shouldn't have to recreate the masking rules from scratch.

A workstation alias `thermal-fit` invokes it on demand: `ssh pi-lab "cd ~/energy-stack && python scripts/fit_thermal_model.py [--window-days N] [--write|--dry-run]"`. Dry-run prints the fit + skill score without writing.

## Sequencing

| Date (target) | Milestone |
|---|---|
| 2026-05-05 | Design doc landed (this PR). |
| ~2026-05-08 | Ecowitt station installed, `weather.ecowitt` measurement in InfluxDB, paired observation history begins. |
| ~2026-06-07 | 30 days of paired Ecowitt + indoor data accumulated. First `fit_thermal_model.py` run produces a candidate ratifiable fit. |
| 2026-06-15 to 2026-06-30 | Implementation PR: `fit_thermal_model.py` + `thermal_model.py` + scheduler integration call sites + tests. Lands behind a `THERMAL_MODEL_ENABLED=false` env flag. |
| 2026-07 (first heat wave) | Validation in production. `THERMAL_MODEL_ENABLED=true` flipped after one heat-wave cycle confirms the integrated envelope predictions match observed indoor traces within the holdout RMSE bound. |
| 2026-Q3 | Step 1 retrospective: how much of the comfort/cost loss against the fixed-rule baseline did Step 1 close? If residuals show structural pattern (e.g. systematic miss on humid afternoons), proceed to Step 2 (2R2C grey-box per Bacher-Madsen Model 4 / 5). If residuals are well-mixed, declare Step 1 sufficient and stop. |

The implementation PR can land before Ecowitt has 30 days of data; the gate is "ratified fit available", which is a runtime check, not a code-merge check.

## Risks and open questions

- **Door/window events not logged.** Single-occupant household, sparse contamination, but worth a future Reed-switch / aqara sensor PR. Until then, residual variance is inflated and Step 2 is the first place that bias would compound.
- **Cooling-season-only.** This design is the cooling-mode fit. The heating-mode fit (modulating gas furnace, distinct nonlinearity from the modulation curve) needs a separate variant with a per-modulation-bin coefficient. Scoped for fall 2026.
- **`hvac.comfortnet` stage signal authoritativeness.** We assume `cl_act` percentage cleanly maps to {off / stage 1 / stage 2}. The decoder docs in `comfortnet` repo say it does, but we should cross-validate against `refoss.channel` kW step the first week of paired data — if the kW signature disagrees with the CT-485 stage tag, the fit falls back to using `refoss.channel` thresholds for stage detection.
- **Ecowitt outage.** If the station drops out mid-fit-window, the fit job degrades to NWS for the gap and emits an unratified fit. The scheduler keeps running on the last ratified fit until the next ratifiable window. This is the same fallback model the existing pollers use; no new runtime tolerance is being introduced.
- **Forecast bias coupling.** The scheduler uses NWS forecast `T_out` for the *forward* integration (predicting indoor at HOT-window start), even after the model is fit on Ecowitt observations. Forecast bias will leak into setpoint decisions. The forecast-bias correction PR (queued ~14 days after Ecowitt has paired observation history) closes this loop. Step 1's quality bound assumes forecast bias is < 2°F at horizons ≤ 12 hours, which is consistent with public-forecast verification at this region; we'll re-check against on-site Ecowitt observations once paired data exists rather than assuming a specific upstream model's verification stats.

## References

- Bacher, P., & Madsen, H. (2011). *Identifying suitable models for the heat dynamics of buildings*. Energy and Buildings, 43(7), 1511–1522. [doi:10.1016/j.enbuild.2011.02.005](https://doi.org/10.1016/j.enbuild.2011.02.005). Methodological anchor for Step 1 and Step 2.
- Khabbazi, A., et al. (2025). *Model predictive control for HVAC systems: a review of field demonstrations*. [arXiv:2503.05022](https://arxiv.org/abs/2503.05022) / [Applied Energy](https://www.sciencedirect.com/science/article/abs/pii/S0306261925011894). Meta-review of 24 residential + 80 commercial MPC field trials. Source of the ~16% residential / 13% commercial cost-savings figure; flags that 87% of papers omit ablations and deployment-cost reporting.
- Kuznik, F., et al. (2023). *Field assessment of MPC for residential HVAC*, Energy and Buildings. [doi pending / ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0378778823002566). Indiana single-family field test, 19% (CI 13–24%) whole-building heating-energy reduction; 38–83% on backup-resistance stages.
- Sturzenegger, D., Gyalistras, D., Morari, M., & Smith, R. S. (2016). *Model predictive climate control of a Swiss office building: implementation, results, and cost-benefit analysis*. IEEE Transactions on Control Systems Technology. [IEEE](https://ieeexplore.ieee.org/document/7087366/). 17% primary-energy reduction over 7-month field trial vs incumbent rule-based control. Anchor for "MPC field trials produce ~15–20% on real buildings."
- Oldewurtel, F., et al. (2012). *Use of model predictive control and weather forecasts for energy efficient building climate control*. Energy and Buildings, 45, 15–27. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0378778811004105). The OptiControl simulation study (1228 cases) reporting ~20% typical control-energy savings over RBC.
- ORNL. *Model-based and data-driven HVAC control strategies for residential demand response*. [ornl.gov publication](https://www.ornl.gov/publication/model-based-and-data-driven-hvac-control-strategies-residential-demand-response). 10.6% avg cost (3.2–23.3% range), 22.5% peak vs PBC, 46% peak vs baseline. Source of the peak-demand reduction figures cited in §Problem.
- Christensen, D. et al. (NREL, 2017–2024). ResStock + Alfa parameter-identification stack. [resstock.nrel.gov](https://resstock.nrel.gov/). Reference implementation of nightly RC fits at scale.
- ASHRAE Handbook of Fundamentals (2021), Ch. 18 *Nonresidential Cooling and Heating Load Calculations* and Ch. 19 *Energy Estimating and Modeling Methods*. Background on lumped-capacitance modeling assumptions and the role of co-located weather observations.
- DOE (2021). *A National Roadmap for Grid-Interactive Efficient Buildings*. [www.energy.gov/eere/buildings/grid-interactive-efficient-buildings](https://www.energy.gov/eere/buildings/grid-interactive-efficient-buildings). Framing for why house-specific identification matters at the grid-services level (5CP-style avoided-cost mechanisms).
- NOAA / NSSL. Public-forecast verification statistics for the Chicago/Romeoville WFO domain ([NSSL](https://www.nssl.noaa.gov/users/brooks/public_html/media/okcmed.html); [UW Atmos MOS verification](https://atmos.uw.edu/~jbaars/mvn_paper/mvn_extended.htm) for methodology). Cited under Risks for the forecast-bias bound; on-site Ecowitt verification will replace this proxy once paired observations accumulate.
