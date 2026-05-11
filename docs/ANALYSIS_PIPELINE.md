# Analysis pipeline (pre-registered, executable)

The binding analysis pipeline that produces every reported outcome in
[`EXPERIMENT_DESIGN.md`](EXPERIMENT_DESIGN.md). Locked at the OSF
commit hash. Re-running this pipeline at any later date against the
same input data must produce identical outputs (up to floating-point
non-determinism documented per stage).

Two pre-commitments live in this document and nowhere else:

1. **The exact computation chain** from raw InfluxDB measurements
   through to the headline-table numbers. Each stage names the script
   that executes it, the inputs it reads, and the artifacts it writes.
2. **The frozen environment** — Python version, dependency pins, and
   the locked PRNG seed — that the analysis runs under.

Spec text in `EXPERIMENT_DESIGN.md` defines *what* is being measured
and why. This document defines *how* the measurement is computed, in
enough detail that someone holding only the raw data exports and this
repository can reproduce every cited number.

---

## 1. Frozen environment

| Item | Value |
|---|---|
| Python version | `3.13` ([`.python-version`](../.python-version); matches the `python:3.13-slim` base image used by all production services) |
| Loose pins (input) | [`tools/analysis/requirements.in`](../tools/analysis/requirements.in) |
| Locked dependencies (hash-pinned) | [`tools/analysis/requirements.txt`](../tools/analysis/requirements.txt), generated via `pip-compile --generate-hashes tools/analysis/requirements.in -o tools/analysis/requirements.txt` |
| PRNG seed (matching analyses + bootstrap) | `20260601` |
| Timezone for all human-readable timestamps | `America/Chicago` (CT) |
| Numeric timestamps | UTC (Unix epoch seconds) |

Bootstrap acceptance test (run from a clean checkout in an isolated venv):

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r tools/analysis/requirements.txt
.venv/Scripts/python.exe -m pytest tools/analysis/tests/
```

Replace `.venv/Scripts/python.exe` with `.venv/bin/python` on POSIX. Verified 2026-05-11 on Windows / Python 3.13.13 with 51 tests passing.

---

## 2. Input data

### 2.1 InfluxDB measurements

All raw data lives in the `energy` bucket of the on-prem InfluxDB 2 instance described in [`SERVICES.md`](SERVICES.md). The analysis reads the following measurements (reconciled 2026-05-11 against production schema; container heartbeat is filesystem-based per the P2.3 hardening, not an Influx measurement — see Stage 2 Rule 7 for how outages are detected):

| Measurement | Source service / script | Fields used | Cadence |
|---|---|---|---|
| `comed.prices` | `comed-poller` | `price_cents` | 5-min |
| `refoss.channel` | `refoss-poller` | `power_w`, `energy_wh`, per-channel tag `channel` (`em:1`..`em:9`) | 1-min |
| `refoss.system` | `refoss-poller` | system-level health and totals | 1-min |
| `hvac.thermostat` | `thermostat-poller` | `indoor_temp_f`, `cool_setpoint_f`, `heat_setpoint_f`, `running`, `hvac_mode` | 1-min |
| `hvac.comfortnet` | `thermostat-poller` (CT-485 ingestion) | `cool_actual_pct`, `heat_actual_pct`, `blower_cfm` | 1-min |
| `hvac.overrides` | `thermostat-poller` (occupant-driven detection) and [`tools/log_override.py`](../tools/log_override.py) (operator annotation) | `category`, `start_ts`, `end_ts`, `setpoint_f`, `note` | event |
| `hvac.actions` | `hvac-scheduler` | `action`, `arm`, `cool_setpoint_f`, `source`, `override_category`, `applied`, `dry_run` | per-tick (every minute when actionable) |
| `hvac.decisions` | `hvac-scheduler` | per-tick day-type / schedule decision audit row | per-tick |
| `hvac.5cp_state` | `hvac-scheduler` | `state`, `scope`, `ratio` | ~2.5-min (matches PJM `inst_load` cadence) |
| `hvac.price_overlay` | `hvac-scheduler` | `tier`, `effective_setpoint_f` | ~30-min (price-overlay evaluation cadence) |
| `hvac.precool_window` | `hvac-scheduler` | `hour_ct`, `depth_f`, `target_date` | event (one per night's pre-cool decision) |
| `hvac.arm_transitions` | [`scripts/log_arm_transition.py`](../deploy/energy-stack/scripts/log_arm_transition.py) | `from_arm`, `to_arm`, `air_setting`, `pi_dry_run` | event (Monday switches) |
| `nws.forecast` | `nws-poller` | `max_temp_f`, `apparent_max_f`, `min_temp_f`, `rh_max_pct`, `sky_cover_avg_pct`, `wind_gust_max_mph` | 6-hourly issuance |
| `nws.alerts` | `nws-poller` | active NWS alert ingest (heat advisories etc.) | event |
| `ecowitt.weather` | `ecowitt` integration | `outdoor_temp_f`, `outdoor_dewpoint_f`, `outdoor_rh_pct`, `wind_mph`, `solar_wm2`, `pressure_inhg` | 5-min |
| `pjm.inst_load` | `pjm-dm2-poller` | `mw`, `area` tag | ~5-min |
| `pjm.metered_load` | `pjm-dm2-poller` | `mw`, `zone` tag | hourly |
| `pjm.peak_forecast_rto` | `pjm-dm2-poller` | `peak_load_mw`, `peak_hour_ept` | daily 06:00 / 13:00 |
| `pjm.lmp_da_hourly` | `pjm-dm2-poller` | `total_lmp_da`, `pnode_name` | daily 17:00 (24 rows for next day) |
| `pjm.load_forecast` | `pjm-dm2-poller` | 7-day load forecast | daily |
| `pjm.nspl_zonal` | `pjm-dm2-poller` | `nspl_mw`, `zone` | annual |
| `pjm.feed_status` | `pjm-dm2-poller` | per-feed last-successful-fetch timestamp | per-feed |
| `pjm.poller_heartbeat` | `pjm-dm2-poller` | `tick_ok` | per minute |
| `pjm.coincident_peak` | [`scripts/scrape_pjm_5cp_pdf.py`](../deploy/energy-stack/scripts/scrape_pjm_5cp_pdf.py) | `peak_load_mw`, `comed_zone_load_mw`, tags `summer_year` + `peak_rank` | yearly, 5 rows |
| `comed.bill` | [`scripts/parse_comed_bill.py`](../deploy/energy-stack/scripts/parse_comed_bill.py) | `capacity_charge_dollars`, `supply_kwh`, `delivery_dollars` | monthly |

The HVAC energy channels referenced as `em_2_kwh + em_8_kwh + em_9_kwh` in §3 Stage 3 are derived at analysis time from the per-channel rows of `refoss.channel` (filter `channel == em:2`, etc.) — the production schema is long-format (one row per channel per tick), not wide-format. The pipeline pivots at Stage 1 before passing to Stage 3.

### 2.2 External data (not in Influx)

| Source | Acquisition | Used for |
|---|---|---|
| PJM 5CP PDF | [`scripts/scrape_pjm_5cp_pdf.py --year YYYY`](../deploy/energy-stack/scripts/scrape_pjm_5cp_pdf.py) | O2 truth-source 5CP hour list |
| ComEd bill PDFs | [`scripts/parse_comed_bill.py`](../deploy/energy-stack/scripts/parse_comed_bill.py) — monthly ingestion | O4 (whole-home), O2 Layer 3 (bill reconciliation) |
| ComEd tariff Schedule of Rates | committed snapshot at [`tools/o2_capacity_reconstruction/tariff_snapshot.md`](../tools/o2_capacity_reconstruction/tariff_snapshot.md) | O2 Layer 2 stipulated portfolio constant |
| Historical PJM day-ahead LMP (COMED zone, summers 2023-2025) | [`tools/comed_price_imputation/fetch_lmp.py`](../tools/comed_price_imputation/fetch_lmp.py) — runs once at OSF lock | Rule 3 (price feed imputation) spread constant |
| 2020-2025 ERA5 reanalysis at KORD coordinates (41.9786°N, 87.9047°W), cooling-relevant weeks (CDD ≥ 5) | [`tools/analysis/fetch_kord_era5.py`](../tools/analysis/fetch_kord_era5.py) → [`tools/analysis/baseline_distribution.py`](../tools/analysis/baseline_distribution.py) → `data/baseline_cov.npz` | Mahalanobis Σ (matched-pair distance) |

---

## 3. Pipeline stages

Stages run in order, all implemented as functions in [`tools/analysis/pipeline.py`](../tools/analysis/pipeline.py) and invokable individually via `python -m tools.analysis.pipeline --stage N`. Each stage writes its outputs to `analysis/out/<run_ts>/stage<N>/`. A run is identified by its UTC start timestamp.

### Stage 1 — Extract

**Function:** `pipeline.stage1_extract(start, end, out_dir)`

**Inputs:** InfluxDB measurements per §2.1, time-window `start` and `end` (CT).

**Logic:** Pull every measurement listed in §2.1 within the window. One Flux query per measurement; queries are stable text in [`tools/analysis/queries/`](../tools/analysis/queries/). Write each measurement to a parquet file. Snapshot is read-only after Stage 1.

**Output:** `out/<run>/stage1/<measurement>.parquet`

**Determinism:** Influx queries are deterministic against a snapshot. The pipeline acceptance test (§7) re-extracts twice from a frozen DB dump and asserts byte-identical parquet output.

### Stage 2 — Quality gates

**Function:** `pipeline.stage2_quality(stage1_dir, out_dir)`

**Inputs:** Stage 1 output.

**Logic:** Apply each of the ten data-quality rules in [`EXPERIMENT_DESIGN.md §4`](EXPERIMENT_DESIGN.md#data-quality-rules-and-missing-data-handling) in order. Each rule is a function `pipeline.rule<N>_<name>(...)`. Outputs:

| File | Schema |
|---|---|
| `qualifying_weeks.csv` | `(week_start_ct, arm, qualifying:bool, exclusion_reason:str\|null, imputed_hvac_kwh_pct:float, imputed_price_hours_pct:float, override_operational_count:int, override_vacation_days:int)` |
| `imputed_intervals.csv` | per-Tier 1-3 imputed interval log |
| `outages.csv` | scheduler and Refoss outage spans with overlap flags |

**Per-rule notes:**

- **Rule 1 (Refoss 4-tier)** — `pipeline.rule1_refoss`. Tier classification by gap length, mains-scaled historical-median imputation for tier 2, ComfortNet-derived imputation for tier 3. ComfortNet `kW = cool_actual_pct × cool_nameplate_kW + heat_actual_pct × furnace_nameplate_kW + blower_cfm_to_kW(cfm)` with `cool_nameplate_kW = 4.6` (Amana ASXC160481BE 4-ton @ 1.15 kW/ton SEER-adjusted; locked in [`pipeline.NAMEPLATE`](../tools/analysis/pipeline.py)), `furnace_nameplate_kW = 0.06` (electrical, blower-only since heat months excluded), and `blower_cfm_to_kW = cfm / 4500 × 0.6` (ECM motor curve). Imputation cap: ≥10% weekly HVAC kWh → week excluded.
- **Rule 2 (ComfortNet)** — `pipeline.rule2_comfortnet`. Flags O6-ineligible days.
- **Rule 3 (RTP price feed)** — `pipeline.rule3_price`. Uses the locked spread constant from [`tools/comed_price_imputation/spread_constants.json`](../tools/comed_price_imputation/spread_constants.json).
- **Rule 4 (NWS forecast)** — `pipeline.rule4_forecast`. Flags substitutions; does not gate week eligibility.
- **Rule 5 (Ecowitt CDD)** — `pipeline.rule5_ecowitt`. NWS gridpoint fallback then daily `(Tmax+Tmin)/2−65` estimator.
- **Rule 6 (PJM `inst_load`)** — `pipeline.rule6_pjm`. Logs detector-accuracy descriptive stats; does not gate.
- **Rule 7 (scheduler outages)** — `pipeline.rule7_scheduler`. Three OR gates per §4 Rule 7. Outage detection runs against the Influx schema directly: a contiguous gap of ≥5 min where `hvac.5cp_state` AND `hvac.actions` both have zero writes is treated as scheduler-down. (The container-health filesystem heartbeat — Docker `HEALTHCHECK` on `/tmp/last_tick_ok` — is the runtime alert path, not the post-hoc analysis signal.)
- **Rule 8 (Pi outages)** — `pipeline.rule8_pi`. Combines rule 1 + 7 effects per affected day.
- **Rule 9 (manual overrides)** — `pipeline.rule9_overrides`. Reads `hvac.overrides`; classifies operational vs vacation; vacation excludes affected days; <5 qualifying days excludes week.
- **Rule 10 (arm-transition verification)** — `pipeline.rule10_transition`. First-control-relevant-window-OR-6h check; failure excludes the week.

**Order matters.** Rule 1 imputation runs before rule 9 (operational overrides keep their hours in the week but with a flag; the Refoss imputation cap then applies to the full week). Rule 10 runs last and is the only rule that excludes a week purely on metadata.

### Stage 3 — Weekly aggregates

**Function:** `pipeline.stage3_weekly(stage1_dir, stage2_dir, out_dir)`

**Inputs:** Stage 1 + Stage 2 outputs.

**Logic:** For each qualifying week × arm, compute the per-outcome inputs:

- **O1 numerator (`$_hvac`):** `Σ_h (em_2_kwh + em_8_kwh + em_9_kwh)_h × (comed_hourly_supply_c + delivery_c)` over the week. The hourly supply price uses `comed.prices period_type=hourly_avg` after Rule 3 imputation. The delivery rate is the ComEd Delivery TOD-rate for the time-of-day bucket the hour falls into; the locked rate table (Morning / Mid-Day Peak / Evening / Overnight) lives in [`deploy/energy-stack/hvac-scheduler/precool.py`](../deploy/energy-stack/hvac-scheduler/precool.py) (`DTOD_PERIODS_CT` constant, sourced from the CUB March-2026 fact sheet for the Single-Family Non-Electric Heat delivery class).
- **O1 denominator (`CDD`):** `Σ_d max(T_avg_d − 65, 0)` over the 7 days of the week. T_avg per day from Ecowitt (Rule 5 fallback handling already applied in Stage 2).
- **O3:** `max_h (em_2_kwh + em_8_kwh + em_9_kwh)_h` — peak hourly HVAC kW of the week (kWh-per-hour = kW for hourly-summed data).
- **O4 numerator:** same construction as O1 but on `em_1 + em_7` (mains).
- **O5 raw:** per-hour `(em_2+em_8+em_9)` kWh and supply-price-multiplied $, retained for matched-pair aggregation.
- **O6 raw:** per-HOT-day 18:00-23:59 CT `(em_2+em_8+em_9)` kWh, retained for matched-pair aggregation.
- **Weather summary vector (§7 components 1-6 of EXPERIMENT_DESIGN):** CDD, mean enthalpy, total solar, mean wind, max temp, max dewpoint. Enthalpy computed via ASHRAE Handbook of Fundamentals psychrometric formulas given pressure/temp/dewpoint, in BTU/lb dry air.

**Output:** `out/<run>/stage3/weekly.csv` with one row per `(week_start_ct, arm)` and all of the above fields plus a `qualifies` boolean from Stage 2.

### Stage 4 — Matched-pair construction

**Function:** `pipeline.stage4_matching(...)`

**Inputs:** Stage 3 `weekly.csv` (qualifying rows only); baseline covariance `data/baseline_cov.npz` from `tools/analysis/baseline_distribution.py`.

**Logic:**

1. Restrict to `qualifies == True` rows.
2. Compute pairwise Mahalanobis distance between every Arm A week and every Arm B week using the 6-component weather summary vector and the locked Σ.
3. Solve the Hungarian assignment (`scipy.optimize.linear_sum_assignment`) over the rectangular cost matrix, minimizing total distance. If `n_A != n_B`, the smaller side defines the number of pairs; remaining weeks of the larger arm enter the "unmatched" report.
4. For each pair, record the pair Mahalanobis distance.
5. Flag pairs with distance > 2.5 Mahalanobis units as poor-quality (excluded from primary effect-size; included in sensitivity).
6. Flag individual weekly summary vectors with point-to-distribution Mahalanobis > 3.5 against the baseline as anomalous.

**Output:**
- `out/<run>/stage4/matched_pairs.csv`: `(pair_id, week_a, week_b, distance, quality:{primary, poor, anomalous})`
- `out/<run>/stage4/unmatched_weeks.csv`

### Stage 5 — Effect sizes + bootstrap CI

**Function:** `pipeline.stage5_effects(...)`

**Inputs:** Stage 3 outcomes per week + Stage 4 pairings.

**Logic, per outcome (O1, O3, O4):**

1. For each primary-quality pair, compute the difference `Δ = arm_B − arm_A` of the outcome.
2. Effect size: matched-pair median of Δ.
3. Stationary bootstrap CI: 10,000 resamples with mean block length 2 pairs (≈ √N for typical N=8-12). Percentile method. Seeded from `PRNG_SEED + outcome_index` so each outcome is independent and reproducible.

**Output:** `out/<run>/stage5/effects.csv`: `(outcome, n_pairs, median_diff, ci_low_95, ci_high_95, bootstrap_samples_npz)`

### Stage 6 — O2 layer reconstructions

**Function:** `pipeline.stage6_o2(...)`

**Inputs:** Stage 1 `hvac.energy`, `pjm.coincident_peak`, `comed.bill`; the stipulated portfolio constant from [`tools/o2_capacity_reconstruction/tariff_constants.json`](../tools/o2_capacity_reconstruction/tariff_constants.json).

**Logic:**

1. Pull the 5 PJM Five Peak hours and the 5 ComEd Five Peak hours for summer Y from `pjm.coincident_peak`.
2. Compute the household's hour-beginning kWh at each of those 10 hours, summed across `em_1 + em_7` (mains). Convert kWh → kW (1-hour granularity is identity).
3. **Layer 1 — `ACustCPL` difference:**
   - Group the 10 peak hours by which arm was active that week.
   - `ACustCPL_Y(A)` = mean kW across PJM Five Peaks falling in Arm A weeks; same for Arm B.
   - Report Layer 1 as `ACustCPL_Y(B) − ACustCPL_Y(A)` in kW, converted to $ via the locked tariff factor.
   - Bootstrap CI: resample over the (typically 1-3) PJM Five Peak hours falling in each arm; if an arm has fewer than 2 peaks, report point estimate without CI and flag.
4. **Layer 2 — full CPLC reconstruction:**
   - Compute both Att. M-2 branches with `ACustPL_Y` (ComEd Five Peaks) and `ACustCPL_Y` (PJM Five Peaks).
   - Use stipulated portfolio constant `Σ_5Pc(ACustPL − ACustCPL)` from `tariff_constants.json`. Report point estimate plus ±10% sensitivity.
5. **Layer 3 — bill reconciliation:**
   - Sum `comed.bill capacity_charge_dollars` for the Y+1 May-Sep months.
   - Report ratio `(Layer 2 reconstructed $) / (Layer 3 observed $)` as tariff-reconstruction fidelity.
6. **Detector accuracy report (process metric):**
   - For each PJM-published 5CP hour in summer Y, check whether `hvac.5cp_state.state == "holding"` during that hour. Compute TP, FP, FN, TN against all summer hours. Report rates.

**Output:**
- `out/<run>/stage6/o2_layer1.csv`
- `out/<run>/stage6/o2_layer2.csv`
- `out/<run>/stage6/o2_layer3.csv`
- `out/<run>/stage6/detector_accuracy.csv`

### Stage 7 — SCED randomization test

**Function:** `pipeline.stage7_sced(...)`

**Inputs:** Stage 5 pair differences.

**Logic:** For each outcome, exhaustively enumerate all 2^N sign-flip permutations of the matched-pair differences. The fraction of permutations with absolute median ≥ |observed median| is the two-sided p-value. For N > 20, switch to 100,000 random sign flips (still reproducible from `PRNG_SEED + outcome_index + 1`).

**Output:** `out/<run>/stage7/sced_pvalues.csv`

### Stage 8 — Forecast-correlated vs grid-event decomposition

**Function:** `pipeline.stage8_decomposition(...)`

**Inputs:** Stage 3 weekly outcomes; daily classification using `nws.forecast` (21:00-prior issuance) and `comed.prices` hourly average.

**Logic:** Classify each day in each qualifying week into `forecast_correlated_spike`, `grid_event_spike`, or `no_spike` per the EXPERIMENT_DESIGN §7 definitions. Decompose the matched-pair median Δ for each category; report magnitude attribution.

Layer-attribution side-table for grid-event days: for each grid-event day in Arm B, log which Arm B layer triggered (`hvac.price_overlay.tier`, `hvac.5cp_state.state`) and the timing.

**Output:** `out/<run>/stage8/decomposition.csv` + `out/<run>/stage8/layer_attribution.csv`

### Stage 9 — Sensitivity analyses

**Function:** `pipeline.stage9_sensitivity(...)`

Re-runs Stages 4-5 (or 8 where relevant) with one alteration per sensitivity, as enumerated in EXPERIMENT_DESIGN §7:

1. Euclidean on z-scored vector instead of Mahalanobis.
2. Washout hours included.
3. O1 with `em:2 + em:8` only.
4. O1 with 5-minute pricing rather than hourly average.
5. Day-of-week stratification (descriptive split, no statistical re-test).
6. Price-tier threshold robustness: re-run Stage 8 with `{8¢/15¢, 10¢/20¢, 12¢/25¢}`.

**Output:** `out/<run>/stage9/<sensitivity_id>.csv` per sensitivity.

---

## 4. Output artifacts (frozen schema)

The pipeline's "headline" output is a single tarball produced by `tools/analysis/make_filing_bundle.py`:

```
filing_bundle_<commit>.tar.gz
├── effects.csv               (Stage 5)
├── effects_with_pi.md         (human-readable headline table)
├── o2_layer1.csv             (Stage 6)
├── o2_layer2.csv             (Stage 6)
├── o2_layer3.csv             (Stage 6)
├── detector_accuracy.csv     (Stage 6)
├── sced_pvalues.csv          (Stage 7)
├── decomposition.csv         (Stage 8)
├── layer_attribution.csv     (Stage 8)
├── sensitivity/              (Stage 9 individual files)
├── qualifying_weeks.csv      (Stage 2)
├── matched_pairs.csv         (Stage 4)
├── data_quality_summary.md   (counts per §4 exclusion rule)
└── commit_info.txt           (git rev, run_ts, dep hashes)
```

This bundle is deposited on OSF alongside the paper. Anyone with the bundle and the locked InfluxDB dump can re-derive the paper.

---

## 5. Reproducibility checklist

A pipeline run is reproducible if:

- [ ] `git rev-parse HEAD` matches the OSF commit hash
- [ ] `pip install -r tools/analysis/requirements.txt --require-hashes` succeeds
- [ ] `pytest tools/analysis/tests/` is green
- [ ] `tools/analysis/spread_constants.json`, `tariff_constants.json`, `baseline_cov.npz` are present and their content hash matches the OSF-recorded values
- [ ] Re-extracting Stage 1 from the deposited InfluxDB dump produces byte-identical parquet outputs
- [ ] `python tools/analysis/pipeline.py --start 2026-06-01 --end 2026-09-30` produces a `filing_bundle_<commit>.tar.gz` whose CSVs are byte-identical to the deposited bundle

---

## 6. Pre-OSF acceptance criteria (specific to this document)

Before OSF filing (target 2026-05-30):

- [x] All Stage scripts present and runnable on a 2025 replay dump
- [x] `pytest tools/analysis/tests/` green against synthetic fixtures
- [x] `spread_constants.json` locked (`PLACEHOLDER: false`, computed 2026-05-11 from 2024-2025 RTP+LMP data spanning 5,840 hours)
- [x] `tariff_constants.json` locked (`PLACEHOLDER: false`, locked 2026-05-11; ComEdNPL, AComEdCPL, capacity rate from primary sources; portfolio_sum reported across three pre-registered named scenarios with FERC ER22-1520-001 anchor — see [`tools/o2_capacity_reconstruction/tariff_snapshot.md`](../tools/o2_capacity_reconstruction/tariff_snapshot.md))
- [x] `baseline_cov.npz` produced from 2020-2025 ERA5 reanalysis at KORD coords, cooling-relevant weeks (CDD ≥ 5)

[`tools/analysis/check_constants_locked.py`](../tools/analysis/check_constants_locked.py) is the pre-filing gate that refuses to bless the OSF commit while any placeholder remains. Currently returns 0 (all three constants files are locked).

---

## 7. Tools added by this PR

| Path | Purpose |
|---|---|
| [`tools/log_override.py`](../tools/log_override.py) | CLI to annotate manual setpoint overrides into `hvac.overrides`. Operational vs vacation classification per [`EXPERIMENT_DESIGN.md §4 Rule 9`](EXPERIMENT_DESIGN.md#data-quality-rules-and-missing-data-handling). |
| [`tools/comed_price_imputation/`](../tools/comed_price_imputation/) | RTP-vs-day-ahead-LMP spread computation per §4 Rule 3. Locked spread constants in `spread_constants.json` (median spread cents/kWh by summer month, computed from 2024-2025 RTP+LMP data). |
| [`tools/o2_capacity_reconstruction/`](../tools/o2_capacity_reconstruction/) | Layer 2 scenario reconstruction and Att. M-2 branch computation. Locked tariff constants (ComEdNPL, AComEdCPL, capacity rate) plus three pre-registered portfolio-sum scenarios (`low` / `anchor_2021` / `high`). |
| [`tools/analysis/`](../tools/analysis/) | The Stage 1-9 pipeline modules, the orchestrator (`pipeline.py`), the requirements file, and the test suite. |
