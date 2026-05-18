---
name: sced-rebaseline-spec
date: 2026-05-13
owner: chris
status: draft
role-label: spec
supersedes: docs/plans/archive/analysis-rebaseline-plan-2026-05-13.md, docs/EXPERIMENT_DESIGN.md (analysis portions)
osf_filing_target: 2026-05-30
experiment_start: 2026-06-01
experiment_end: 2026-11-16
---

# SCED Rebaseline Spec — HVAC Controller Effectiveness, Summer 2026

## 0. Source of truth declaration

This spec is the binding source of truth for the OSF pre-registration filed on or before 2026-05-30. Where this spec conflicts with prior documents (`docs/EXPERIMENT_DESIGN.md`, archived `docs/plans/` files, PR #109), this spec wins. Prior `$/CDD` framing, weekly fact-table architecture, formal SCED randomization-test machinery, and bootstrap CI framing are explicitly superseded.

Post-OSF logic changes to controller behavior, analysis thresholds, or outcome definitions are protocol deviations unless explicitly amended through OSF's amendment process.

## 1. Research question and framing

**Primary question:**
Does a smart RTP/DTOD/5CP-aware HVAC controller, **when operating as intended**, reduce HVAC/furnace energy cost compared with a standard programmable thermostat schedule, under matched cost and weather conditions, in this single household?

**Research positioning:** Single-household exploratory case study. The question being asked is whether this particular control idea produces a measurable signal in this house under matched conditions. The primary estimand is per-protocol algorithm efficacy: comparison of Arm A and Arm B during hours where each arm operated in its intended treatment mode. Hours where the smart controller fell back to the thermostat schedule (B-fallback/B-down) are excluded from Arm B's aggregate, with matched-cost hours symmetrically excluded from Arm A. Whether a hypothetical reliability-hardened version of this controller would save money in real-world use is a different question that this study does not address.

**Framing:** Transparent single-household n-of-1 matched-pair case study. NOT a generalizability claim. NOT a power-driven formal SCED study. The value is real-world measurement of an actual controller under actual ComEd Rate BESH (Hourly Pricing) + DTOD (Distribution Time-of-Day) tariff conditions with all signal sources (live 5-min ComEd, settled PJM hourly LMP, NWS forecast, PJM system-load and capacity-risk inputs) connected.

**Arm B and 5CP — what's actually happening:** Arm B does NOT know or predict the official 5CP hours in real time. Official 5CP hours are determined after the summer from PJM metered-load data and published post-season. Arm B uses PJM system-load and forecast inputs to identify high-load capacity-risk windows where reduced household HVAC demand may lower the next year's capacity-charge exposure IF those hours later coincide with official peak events. The controller layer is a capacity-risk *detector*, not a 5CP feed or 5CP prediction. O2 outcomes use the official post-season 5CP list; the detector's hit/miss behavior versus that list is reported only as descriptive process telemetry.

**What this is NOT:**
- A reliability/uptime evaluation of the controller (reliability reported as provenance only).
- A whole-home savings claim (whole-home is reconciliation/sanity only).
- A retrospective historical comparison against 2024/2025 (instrumentation begins prospectively from 2026-04-29 forward).
- An intent-to-treat or real-world-deployment evaluation (controller-failure hours are excluded from the primary outcome per the per-protocol estimand above).

## 2. Experiment calendar

**Window:** 2026-06-01 (Monday 00:00 CT) through 2026-11-16 (Monday 00:00 CT). 24 weeks = 168 days = 12 arm periods.

**Arm sequence (deterministic):** A, B, A, B, A, B, A, B, A, B, A, B — first arm = A (status-quo control), strict alternation. 6 A periods + 6 B periods.

**Arm period structure:** 14 days each, switched at Sunday → Monday 00:00 CT.

**Washout:** First 48 hours after each arm switch are excluded from analysis aggregation. 12 days remain as the post-washout analysis window per arm period.

**Hour-index reference (per arm period):**
- Hour-index 0 = Wednesday 00:00 CT (immediately after 48h washout ends)
- Hour-index 287 = end of 288 elapsed UTC hours after hour-index 0
- Total = **288 elapsed UTC hours** per arm (uniform across all 12 arms)
- For arms 1-10 and 12 (no DST crossing): hour-index 287 corresponds to wall-clock Sun 23:00 CT
- **For arm 11 (spans 2026-11-01 DST fall-back): hour-index 287 corresponds to wall-clock Sun 11-01 22:00 CST.** The 288-elapsed-UTC-hour window ends at 11-01 23:00 CST, exactly 1 wall-clock hour before arm 12 begins at Mon 11-02 00:00 CST.

**DST equalization exclusion (arm 11 only):** Because the experiment crosses the 2026 fall DST transition, one repeated local wall-clock hour exists in arm 11. The analysis intentionally uses a fixed 288 elapsed-hour window per arm; therefore one real recorded hour in arm 11 is excluded as a planned DST equalization exclusion. This is not missing data and not a protocol failure. The 1-hour gap between end of arm 11's analysis window and arm 12's calendar start is treated as additional washout before arm 12's own 48h washout begins.

**Calendar (CT-local Monday 00:00 switches):**

| # | Arm | Start | End | Washout ends | Hour-index 0 |
|---|---|---|---|---|---|
| 1 | A | 2026-06-01 | 2026-06-15 | 2026-06-03 00:00 | 2026-06-03 00:00 (Wed) |
| 2 | B | 2026-06-15 | 2026-06-29 | 2026-06-17 00:00 | 2026-06-17 00:00 (Wed) |
| 3 | A | 2026-06-29 | 2026-07-13 | 2026-07-01 00:00 | 2026-07-01 00:00 (Wed) |
| 4 | B | 2026-07-13 | 2026-07-27 | 2026-07-15 00:00 | 2026-07-15 00:00 (Wed) |
| 5 | A | 2026-07-27 | 2026-08-10 | 2026-07-29 00:00 | 2026-07-29 00:00 (Wed) |
| 6 | B | 2026-08-10 | 2026-08-24 | 2026-08-12 00:00 | 2026-08-12 00:00 (Wed) |
| 7 | A | 2026-08-24 | 2026-09-07 | 2026-08-26 00:00 | 2026-08-26 00:00 (Wed) |
| 8 | B | 2026-09-07 | 2026-09-21 | 2026-09-09 00:00 | 2026-09-09 00:00 (Wed) |
| 9 | A | 2026-09-21 | 2026-10-05 | 2026-09-23 00:00 | 2026-09-23 00:00 (Wed) |
| 10 | B | 2026-10-05 | 2026-10-19 | 2026-10-07 00:00 | 2026-10-07 00:00 (Wed) |
| 11 | A | 2026-10-19 | 2026-11-02 | 2026-10-21 00:00 | 2026-10-21 00:00 (Wed) |
| 12 | B | 2026-11-02 | 2026-11-16 | 2026-11-04 00:00 | 2026-11-04 00:00 (Wed) |

## 3. Arm definitions

**Asymmetry is intentional. It IS the treatment.**

| Arm | Controller behavior | Setpoint source | Failure mode |
|---|---|---|---|
| A | hvac-scheduler runs in dry-run/shadow/observation mode only. Does NOT push setpoints. | CTK04AE thermostat's internal programmed schedule runs autonomously. | N/A (thermostat is the floor.) |
| B | hvac-scheduler active. Day-type classification + price overlays + capacity-risk overlays + precool deepening + safety supervisor. Pushes setpoints to CTK04AE. | Scheduler commands → CTK04AE. | If scheduler dies → CTK04AE program resumes (= effectively-A behavior during that window). Per Section 5: hour classified as B-down, EXCLUDED from primary. |

**Arm A schedule (CTK04AE programmed):** Documented in `docs/THERMOSTAT_ARM_A_SCHEDULE.md` (pre-OSF deliverable). Frozen at OSF-filing commit. Changes to thermostat program post-OSF = protocol deviation.

**Arm B controller spec:** Frozen at hvac-scheduler `main` commit hash at OSF filing time. Pre-freeze fix list (Section 11) lands before freeze. Post-freeze logic changes = protocol deviation.

**Switching mechanism:** Scheduler operates in one of three explicit top-level modes, controlled by `SCHEDULER_MODE` (env var, no default — must be set explicitly). The mode gates the `execute_action` setpoint-write path. Container runs continuously across all states for telemetry observation.

| Mode | Behavior |
|---|---|
| `shadow` | Never writes thermostat setpoints. Logs decisions/telemetry only. Safe default before experiment start. |
| `experiment` | Uses locked A/B experiment calendar (§2). Arm A periods = shadow/no-write. Arm B periods = active/write. **Outside the locked experiment window (before 2026-06-01 or after 2026-11-16) = shadow/no-write.** |
| `production` | Runs the smart controller normally, IGNORING the A/B experiment calendar. Used only for deliberate non-study operation after the experiment or operator-approved outside-study use. Operation in this mode is excluded from the experiment analysis dataset. |

**Required locks:**

- Pre-experiment-start (before 2026-06-01): mode = `shadow`.
- During the experiment (2026-06-01 → 2026-11-16): mode = `experiment`.
- Post-experiment (after 2026-11-16): mode either stays `shadow` or switches deliberately to `production`. Production-mode operation is outside the study and excluded from analysis.
- **Active control outside the experiment window requires explicit `production` mode.** No implicit "preserve pre-experiment behavior" fallback.
- **Unknown or invalid `SCHEDULER_MODE` value:** fail closed (no writes) AND log loudly on startup so the misconfiguration is visible before any write path can run. Preferred: refuse to start.

## 4. Primary outcome

**Outcome variable:** HVAC/furnace actual dollars over each arm period's valid post-washout analysis window, after symmetric exclusion (Section 5).

**Computation per hour (HVAC$_hour):**

```
HVAC$_hour = HVAC_kWh_hour × (
    rt_hrl_lmps[hour]/10              # PJM settled hourly LMP, ¢/kWh (bill-canonical)
  + PEA_per_kwh                       # Purchased Electricity Adjustment, monthly snapshot
  + transmission_per_kwh              # Transmission Services Charge, monthly snapshot
  + misc_procurement_per_kwh          # Misc Procurement Components Charge
  + dtod_resultant_per_kwh[band(hour)] # DTOD Distribution Facilities, ¢/kWh, 4 hour-bands (Section 8)
  + iedt_per_kwh                      # 0.126¢/kWh flat
  + variable_riders_per_kwh           # Σ per-kWh TAXES_FEES_CREDITS (excluding flat/% items)
)
```

Where `HVAC_kWh_hour` = sum across Refoss channels {em:2, em:8, em:9} of (mean `power_w` in the hour × 1h), per Section 7.

**Rate snapshot:** Frozen at OSF-filing snapshot (April 2026 DTOD worksheet + April 2026 bill ingestion). Rate-in-effect-by-service-period applied during post-experiment analysis if reliably ingested. Bill reconciliation (Section 10) catches divergence.

**Aggregation:** Sum HVAC$_hour over the 288 hours of the post-washout window MINUS hours excluded by Section 5's symmetric-exclusion rule.

**Secondary outcome:** HVAC/furnace kWh over the same valid hours, summed.

**Excluded from HVAC$:**
- Capacity Charge — kW-based, not per-kWh; handled separately under O2 Layer 1 framing in EXPERIMENT_DESIGN.md.
- Customer Charge ($15.35/mo flat) — invariant to HVAC.
- Standard Metering Charge ($3.83/mo flat) — invariant to HVAC.
- Percentage taxes (Municipal Tax, State Tax, Franchise Cost) — applied to bill total; not directly per-kWh-attributable.

## 5. Exclusion rule and validity gate

**Hour-level classification** (4 modes, one per hour per arm):

| Mode | Definition |
|---|---|
| A-active | Arm A period, CTK04AE running, telemetry valid |
| B-active | Arm B period, controller running, HVAC telemetry valid, required control inputs healthy. **Required inputs are price and weather/forecast for ALL Arm B hours, plus PJM capacity-risk inputs ONLY during the pre-registered capacity-risk operating window** (see §5.1 below). Outside that window, missing PJM capacity-risk inputs are logged in feed-health provenance but do NOT convert an hour to B-fallback. |
| B-fallback | Arm B period, controller process alive, but one or more required-for-that-hour control inputs are stale or missing, causing degraded/fallback controller behavior. |
| B-down | Arm B period, controller process not writing → CTK04AE program took over (effectively-A behavior). |
| telemetry-invalid | HVAC measurement fails Section 7 validity (<110 samples per channel OR >2min intra-hour gap OR ANY HVAC channel down). |

### 5.1 Capacity-risk operating window

The PJM 5 Coincident Peak hours typically occur during the highest-load summer afternoons. Outside that period, the capacity-risk detector layer is inactive by design — the controller does not consult PJM capacity-risk inputs at all. Therefore, PJM capacity-risk input staleness outside this window is irrelevant to Arm B's intended behavior and should not down-classify B-active to B-fallback.

**Pre-registered capacity-risk operating window:** 2026-06-01 00:00 CT through 2026-09-30 23:59 CT. This is the regulatory 5CP eligibility window per PJM Manual 19 and ComEd Attachment M-2 — not an empirical choice. See `docs/EXPERIMENT_DESIGN.md` line 569 for the audit-pinning row and `deploy/energy-stack/hvac-scheduler/pjm_5cp.py:140` for the in-code citation. Three years of ComEd RTP data (2023-2025) independently confirm zero ≥10¢/kWh stress events in October at this load center, consistent with the regulatory boundary. Outside this window:
- PJM capacity-risk inputs are not required for B-active classification.
- Their health is still logged in feed-health provenance.
- The controller's capacity-risk overlay logic is inactive by design.

Manual-override is NOT a tracked mode. Per operator-stated protocol commitment: no manual thermostat overrides will occur during the experiment. If an override does occur, it is treated as a protocol deviation requiring OSF amendment, not silently absorbed into the analysis.

**Fully-valid hour definition** (used by the single validity gate):

An hour-index `k` is *fully-valid for an arm* if both:
- Telemetry is valid at `k` (passes Section 7 sample/gap rules), AND
- The arm was in its intended treatment mode at `k`: `A-active` for Arm A periods, `B-active` for Arm B periods.

Hours where the arm was in `B-fallback` or `B-down` are NOT fully-valid (failed exposure to intended treatment, even if telemetry was fine).

**Single validity gate (pre-matching, per arm):**

An arm enters the matching pool only if `fully_valid_hours_count ≥ 259` (= ≥90% of 288 post-washout hours).

Arms that fail this gate are dropped from the matching pool entirely and reported descriptively (no primary contribution).

**Mathematical guarantee:** Any two arms each having ≥259 fully-valid hours produce a pair with `|A_valid ∩ B_valid| ≥ 2 × 259 − 288 = 230` time-aligned fully-valid hour-indices. No separate post-matching pair-level threshold is needed.

**Cost-matched exclusion within each pair (replaces same-indices):**

After matching, each pair has some hour-indices where arm A is fully-valid but arm B is not (or vice versa). These asymmetric-invalid hours need symmetric removal so the comparison summary stats are over equal-sized samples.

Rule:

1. Identify each excluded hour `k` in either arm (i.e., hour-indices where one arm was fully-valid and the other was not).
2. For each such `k`, compute `hourly_rate[k]` = `rt_hrl_lmps[k]/10 + DTOD[band(k)] + iedt + variable_riders` (¢/kWh, from Section 8).
3. Find the closest unmatched fully-valid hour `h` in the OTHER arm minimizing `|hourly_rate_other[h] − hourly_rate[k]|`.
4. Tie-break: pick the chronologically earlier `h`.
5. Mark `h` as cost-matched excluded.
6. Repeat 1-5 until every excluded hour in one arm has a cost-matched counterpart in the other arm. Greedy 1:1.

**Result:** equal counts of valid hours in both arms, with the dropped hours from each side conditioned on similar cost conditions. Defensible for a price-aware controller — the estimand becomes "HVAC$ savings under matched-cost conditions."

**Why cost-matched over same-indices:** for a controller whose treatment IS responding to price, cost-matched conditioning is more aligned with the treatment mechanism than time-of-day conditioning. Same-indices was the earlier proposal; cost-matched supersedes it. Rate data is available for ALL hours (including excluded ones) since rt_hrl_lmps and DTOD are independent of HVAC measurement.

**Provenance per pair:** report:
- `excluded_hours_a_count`, `excluded_hours_b_count`
- `excluded_hours_breakdown_a` (counts of telemetry-invalid in Arm A)
- `excluded_hours_breakdown_b` (counts of B-fallback / B-down / telemetry-invalid in Arm B)
- `cost_match_quality_median_diff_c_per_kwh` (median `|hourly_rate_a[h] − hourly_rate_b[k]|` across cost-matched pairs)
- `excluded_hourly_rate_distribution` (mean, p5, p50, p95 of `hourly_rate[k]` for excluded hours)
- Overlap of excluded hours with: elevated-price-tier windows, scarcity-price-tier windows, 5CP-active windows, high-temperature windows (≥85°F outdoor)
- B-active-percentage, B-fallback-percentage, B-down-percentage per arm

The overlap provenance lets reviewers detect whether exclusion silently concentrated on easy/hard hours, biasing the comparison.

## 6. Weather matching

**Hard invariant:** Weather is used ONLY for analysis-side comparability matching and transparent match-quality reporting. NOT an outcome denominator. NOT an eligibility gate. NOT a regression adjustment. NOT a post-hoc normalization. NOT an extra primary metric.

**Methodology context:** This uses the matching framework from observational-causal-inference literature: pair A and B blocks on multivariate physical covariates so cost/kWh differences are reported under comparable exposure conditions. The approach is methodologically mainstream and aligns with building-informatics weather-feature matching literature. Note: matching is used here for comparability presentation, not for inference. The study itself is descriptive (no hypothesis test, no verdict — see §9.5).

**Weather data source (mixed, with provenance):**
- **Primary:** Ecowitt local station (microclimate-faithful to actual house conditions). Canonical channels: `ch1_temp_f` (outdoor shaded) and `ch1_dewpoint_f`. `ws90_*` (outdoor unshaded) and `outdoor_*` (gateway alias) are descriptive only, not used in the vector.
- **Fallback:** NOAA-archived automated airport weather station **KJOT (Joliet Regional Airport, AWOS-3)** for any Ecowitt-gap hours. Selected at audit phase per `docs/replay-validation/2026-05-18-noaa-fallback-station-selection/findings.md` (6.9 mi from Plainfield, 100% temp + dew-point hourly completeness over a 7-day July 2024 sample including the 2024-07-15 derecho). The audit confirms AWOS-3 and ASOS METAR observations are functionally equivalent for the temperature and dew-point fields the spec §6 weather vector consumes — both flow through the same NWS METAR feed and the same NCEI archive — and proximity criterion favours KJOT over the nearest ASOS-program station (KMDW at 26.3 mi). Used ONLY to fill missing hours within an arm period; does NOT invalidate the arm period itself.
- **Provenance per arm:** report `pct_hours_ecowitt`, `pct_hours_noaa_fallback`, and fallback-source identity. No silent substitution.

**Weather vector (4 components, arm-period aggregation over post-washout 12-day window):**

| Component | Aggregation | Source |
|---|---|---|
| `cdd_total` | Sum of CDD over 12 days, base 65°F | Hourly outdoor temp |
| `mean_daily_max_temp_f` | Mean of each day's max temperature, over 12 days | Hourly outdoor temp |
| `mean_nocturnal_min_temp_f` | Mean of each day's nocturnal (22:00-06:00 CT) min temperature, over 12 days | Hourly outdoor temp |
| `mean_dewpoint_f` | Mean over all valid hours | Hourly outdoor dewpoint |

**Rationale:** mean-of-daily-stats (max and nocturnal min) is more stable than period-max stats, and aligns with building-science cooling-load matching practice. CDD captures integrated load. Mean dew point captures latent load. Nocturnal min captures cooling duration and pre-cool opportunity.

**Nocturnal-min night-count clarification:** A "night N" runs 22:00 CT of date N through 05:59 CT of date N+1, spanning the midnight boundary. The post-washout analysis window is midnight-aligned and 12 calendar days long (Wed 00:00 CT through the following Mon 00:00 CT). That window contains 11 *complete* overnight windows -- the first calendar day's post-midnight hours (00-05) belong to the previous night (which has no in-window pre-midnight half) and the last calendar day's pre-midnight hours (22-23) belong to a night that has no in-window post-midnight half. Both partial nights are excluded to avoid biasing the nightly-min estimator with under-sampled tails. `mean_nocturnal_min_temp_f` therefore averages 11 nightly minima per arm, not 12. This is consistent with the "mean of each day's nocturnal min" language in the table above when "day D's nocturnal" is interpreted as "the night beginning at day D's 22:00" (the only physically coherent interpretation, since the alternative would conflate two distinct nights' data within one calendar day).

**Dropped from vector:** solar irradiance and wind speed. Both have weaker direct relationship to HVAC cooling load than the four locked components, and adding them dilutes the matching axes. Could be re-added as sensitivity-only components if needed.

**Standardization:** Each component is z-scored (mean 0, SD 1) across all 12 experiment blocks (within-sample standardization). No external historical baseline. This avoids cross-source z-scoring concerns; means and stds are computed from the same data source as the vectors themselves.

**Distance metric:** Euclidean distance on the 4-component z-scored vector. Equal weights (primary). Sensitivity: re-run with `w_CDD = w_peak = 1.5`, others = 1.0.

**Matching algorithm:** Hungarian algorithm (optimal 1:1 bipartite matching). Constructs a 12×12 cost matrix (6 A-blocks × 6 B-blocks if all arms pass the validity gate), assigns to minimize total weather distance. If unequal counts (some arms dropped by validity gate), rectangular Hungarian over min(n_A_valid, n_B_valid).

**Poor-weather-match flag (NOT an exclusion):** Matched pairs whose weather distance exceeds the 90th percentile of the full A-B distance distribution (all possible A-B pairings, not just matched) are flagged as `poor_weather_match_flag = True`, but are **NOT excluded** from the primary per-pair table or aggregate summaries. The final report includes weather distance and per-component differences for every pair. A sensitivity summary may optionally show results excluding flagged poor-weather-match pairs, but the primary descriptive result includes all valid matched pairs. This preserves the discovery-framing principle (no hidden filters; reader sees everything and judges).

**Temporal gap:** Reported as `temporal_gap_days` per pair (descriptive). NOT a filter.

**Audit-phase items for §6:**
1. Confirm NOAA-archived automated airport weather station selection (KJOT Joliet, KARR Aurora, KMDW Midway, or KORD O'Hare) for fallback. Criteria: proximity to Plainfield IL + completeness of hourly temp + dewpoint. (Note: the candidate pool spans both FAA programs — ASOS and AWOS-3 — that flow through the same NCEI archive. The selected station KJOT is verified AWOS-3; program assignment for the three non-winning candidates is not separately verified in this audit. See `docs/replay-validation/2026-05-18-noaa-fallback-station-selection/findings.md` for details.)
2. Implement nocturnal-min aggregation (22:00-06:00 CT window) including DST-fold handling on arms 11-12.
3. Caliper computation: lock the "full A-B distance distribution" as N_A_valid × N_B_valid pairwise distances; 90th percentile thresholded.

## 7. HVAC measurement layer

**Channels (Refoss EM16P):**
- HVAC: `{em:2, em:8, em:9}` (em:2 + em:8 = AC compressor legs; em:9 = furnace blower / air handler)
- Mains-sanity: `{em:1, em:7}` (whole-home mains, sanity check only)

Channel mapping verified via 13.7-day audit (2026-04-29 → 2026-05-13): em:2/em:8 active-hour Pearson r = 0.9999, median ratio 1.013, no exact duplicates → distinct compressor legs. **AC never active without blower** (AC-only hours = 0; em:2+em:8 > 100W always coincides with em:9 > 50W) → AC requires blower as expected mechanically. Blower-only hours DO exist (~32 hours over the audit window) = fan-only mode or gas-heat blower; these are expected and not an anomaly.

**em:9 idle baseline (~28W):** Included in HVAC$. Not subtracted. Rationale: real HVAC-system electric consumption; controller cannot affect parasitic draw; subtracting would be a post-hoc adjustment harder to defend than reporting actual circuit electricity.

**Energy formula:** `hour_kWh[channel] = mean(power_w[channel] in hour) × 1h`. Summed across the 3 HVAC channels per hour.

**Per-hour validity rule (Q6 lock):**
- Min samples per HVAC channel per hour: **≥110** (= 92% of nominal 120 samples at 30s cadence)
- Max single-channel intra-hour gap: **≤120 seconds (2 min)**
- Channel-failure rule: if ANY of {em:2, em:8, em:9} fails either threshold, hour = `telemetry-invalid` → excluded per Section 5

Justification: 13.7-day production audit (2026-04-29 → 2026-05-13) showed p05 sample count = 120 (median = 120, max gap = 43s, ZERO gaps >2min over the full available data window). Thresholds permit small poller blips without flagging healthy hours.

**Per-arm-period validity gate (single gate, pre-matching):**

Per Section 5, an arm enters the matching pool only if `fully_valid_hours_count ≥ 259` (= ≥90% of 288 post-washout hours), where a fully-valid hour requires BOTH telemetry-valid AND in-intended-treatment-mode (A-active for Arm A, B-active for Arm B).

Continuous-invalid cap: no >24h contiguous run of telemetry-invalid OR exposure-invalid hours within the arm.

Arms failing the gate are dropped from the matching pool entirely and reported descriptively. The math `2 × 259 − 288 = 230` guarantees that any pair of passing arms produces ≥230 time-aligned fully-valid hour-indices, so no separate post-matching pair-level threshold is needed.

**Pipeline order of operations:**
1. Per-hour mode classification (4 modes: A-active / B-active / B-fallback / B-down / telemetry-invalid)
2. Per-arm fully-valid hour count + single validity gate → arms passing enter matching pool
3. Weather-matched pairing via rectangular Hungarian (Section 6)
4. Cost-matched symmetric exclusion within each pair (Section 5)
5. Per-pair HVAC$ + HVAC kWh aggregation + provenance reporting (Section 9)

**Refoss audit data window (validity-threshold basis):** 2026-04-29 → 2026-05-13, 13.7 days raw history. Documented pre-cooling-window limitation; mid-experiment data showing materially different telemetry behavior is a protocol deviation/amendment, NOT silent re-locking.

**Limited history note:** Refoss instrumentation begins prospectively 2026-04-29. No retrospective 2024/2025 baseline. Same for Eagle. This is not a defect; instrumentation starts when it starts.

## 8. Pricing layer

**Bill-canonical supply price (settled hourly LMP):** PJM DataMiner2 `rt_hrl_lmps` for pnode_id=33092371 (COMED zonal aggregate). Bill-canonical because ComEd Rate BESH bills on PJM RT settled hourly LMP for COMED zone with no markup.

**Latency:** T+2 worst-case (settled hourly data posts daily 11am-12pm ET on business days). Retrospective analysis runs after each arm period closes; latency does not affect outcome computation.

**rt_hrl_lmps poller backfill scope:** Pre-OSF requirement. Backfill from 2026-01-01 onward; ideally full 24-month available retention.

**Live 5-min stream (ComEd `comed.prices`):** Retained for controller decisions and as diagnostic/provenance source (signal the controller saw). NOT used for primary HVAC$ outcome (3.46¢ aggregate divergence over 24h vs published settled, max 0.79¢/hour — not bill-canonical).

**Eagle `zigbee:Price`:** Daily PTC (Price-to-Compare) value, NOT hourly RTP. Diagnostic-only.

**DTOD delivery rate components (Q5 Path C, primary primitives):**

| Period | CT hours | Distribution Facilities resultant (¢/kWh) | IEDT (¢/kWh) | Total delivery (¢/kWh) |
|---|---|---|---|---|
| Morning | 06:00-13:00 | 4.428 | 0.126 | 4.554 |
| Mid-Day Peak | 13:00-19:00 | 11.727 | 0.126 | 11.853 |
| Evening | 19:00-21:00 | 4.142 | 0.126 | 4.268 |
| Overnight | 21:00-06:00 (wrap) | 3.311 | 0.126 | 3.437 |

Source: ComEd April 2026 Tariff Worksheet (Residential Single Family Without Electric Space Heat, DTOD class). Resultant rates include all riders (IDUF, RBAF, TPAF, DGRA, DSPR). IEDT is a separate flat per-kWh line on the bill.

**DTOD class verification:** Customer elected DTOD at project start. April 2026 bill is the last pre-DTOD bill (flat $0.06267/kWh distribution). First DTOD bill expected 2026-05-24; validates DTOD line items appear correctly.

**Supply non-LMP components (per-kWh, monthly snapshot):**
- Purchased Electricity Adjustment (~$0.01773/kWh April 2026)
- Transmission Services Charge (~$0.01083/kWh April 2026)
- Misc Procurement Components Charge (~$0.00062/kWh)

**Variable per-kWh riders:** Energy Efficiency Programs + Renewable Portfolio Standard + Zero Emission Standard + Energy Transition Assistance + Environmental Cost Recovery + Coal to Solar/Energy Storage (≈ +1.16¢/kWh aggregate April 2026).

**Carbon-Free Energy Resource Adjustment:** Real per-kWh credit (-3.186¢/kWh April 2026). Applied as credit in HVAC$ formula. Magnitude varies monthly; not a fixed coincidence with Capacity Charge.

**Capacity Charge:** Bill line item ($54.64 April 2026 = 6.56 kW × $8.32925/kW-month MCC for Jan-May 2026; jumps to $10.13567/kW-month from June 2026 onward). Bill-ingested. NOT folded into hourly HVAC$ (kW-based, not kWh-based). Handled separately under O2 Layer 1 capacity-avoidance framing — see `docs/EXPERIMENT_DESIGN.md` § O2 and `tools/o2_capacity_reconstruction/tariff_snapshot.md`.

## 9. Reporting

**Primary deliverable:** Per-pair table (one row per matched pair).

**Required columns:**

| Column | Description |
|---|---|
| `pair_id` | 1..min(n_A_valid, n_B_valid) |
| `arm_a_id` | Arm-period identifier (e.g., A1) |
| `arm_b_id` | Arm-period identifier (e.g., B2) |
| `arm_a_dates` | Calendar start-end CT |
| `arm_b_dates` | Calendar start-end CT |
| `temporal_gap_days` | Days between mid-points (descriptive) |
| `weather_distance_zscore` | Euclidean on 4-component z-scored vector (cdd_total / mean_daily_max_temp_f / mean_nocturnal_min_temp_f / mean_dewpoint_f) |
| `weather_vector_a` | 4-tuple (raw component values, not z-scored) |
| `weather_vector_b` | 4-tuple |
| `weather_component_diffs_raw` | per-component (B − A) raw differences in physical units |
| `weather_component_diffs_zscored` | per-component (B − A) standardized differences |
| `poor_weather_match_flag` | True if `weather_distance_zscore` > 90th percentile of full A-B distance distribution. **NOT excluded from primary.** Optional sensitivity may rerun excluding flagged pairs (see §12). |
| `valid_pair_hours` | After cost-matched exclusion |
| `excluded_hours_count` | Total excluded |
| `excluded_hours_breakdown_a` | Counts of telemetry-invalid hours in Arm A |
| `excluded_hours_breakdown_b` | Counts of B-fallback / B-down / telemetry-invalid in Arm B |
| `cost_match_quality_median_diff_c_per_kwh` | Median \|rate_a − rate_b\| across cost-matched exclusion pairs |
| `excluded_hourly_rate_p50_c_per_kwh` | Median hourly rate of excluded hours |
| `excluded_overlap_elevated_price` | Hours overlapping elevated-tier price windows |
| `excluded_overlap_scarcity_price` | Hours overlapping scarcity-tier windows |
| `excluded_overlap_5cp_active` | Hours overlapping 5CP-active windows |
| `excluded_overlap_high_temp` | Hours overlapping ≥85°F outdoor |
| `cooling_active_hours_a` | Hours where em:2+em:8 > 100W |
| `cooling_active_hours_b` | Hours where em:2+em:8 > 100W |
| `low_cooling_exposure_flag` | True if cooling_active_hours < 6 over 12-day window (either arm) — descriptive only |
| `hvac_dollars_a` | $ over valid pair hours |
| `hvac_dollars_b` | $ over valid pair hours |
| `diff_dollars_b_minus_a` | Arm B − Arm A |
| `percent_diff_dollars` | (B − A) / A × 100 |
| `hvac_kwh_a` | kWh over valid pair hours |
| `hvac_kwh_b` | kWh |
| `diff_kwh_b_minus_a` | B − A |
| `weather_source_pct_ecowitt_a` | Provenance: % Ecowitt-sourced |
| `weather_source_pct_ecowitt_b` | Provenance |

**Aggregate summary line (across all valid pairs):**
- Mean, median, min, max, range of `diff_dollars_b_minus_a`
- Mean, median, min, max, range of `percent_diff_dollars`
- Mean, median, min, max, range of `diff_kwh_b_minus_a`
- (Removed: previously listed sign-flip p-value as "optional descriptive." Dropped per §9.5 discovery framing — no p-values, no test statistics, no inference.)

**Explicitly NOT reported as primary:** bootstrap confidence intervals, SCED randomization-test p-values, $/CDD outcomes, kWh/CDD outcomes, cooling-relevance subset analyses.

### 9.5 Pre-registered summary structure (discovery framing)

**This is a discovery study, not an inference study.** There is no hypothesis under test, no binary verdict ("B saves" vs "A saves" vs "inconclusive"), and no statistical decision rule. The deliverable is descriptive: the per-pair table (§9) plus a fixed, pre-registered set of summary breakdowns. Readers draw their own conclusions from the data.

**Pre-registered summary breakdown buckets:**

Each bucket reports the following statistics, computed over the pairs meeting its defining condition:
- Mean, median, min, max, range of `diff_dollars_b_minus_a`
- Mean, median, min, max, range of `diff_kwh_b_minus_a`
- Count of pairs in bucket

| Bucket | Defining condition |
|---|---|
| `all_valid_pairs` | All matched pairs (including poor-weather-match-flagged ones; nothing is filtered by weather distance) |
| `high_cooling_pairs` | `hvac_dollars_a + hvac_dollars_b ≥ $50` |
| `medium_cooling_pairs` | `hvac_dollars_a + hvac_dollars_b` in [$5, $50) |
| `low_cooling_pairs` (shoulder) | `hvac_dollars_a + hvac_dollars_b < $5` |
| `scarcity_exposed_pairs` | ≥1 hour at price-tier `scarcity` in either arm of the pair |
| `5cp_exposed_pairs` | ≥1 hour during a 5CP-active window in either arm of the pair |
| `high_temp_exposed_pairs` | ≥1 hour with outdoor temp ≥ 85°F in either arm |

Some buckets may have N=0 (e.g., no scarcity events occurred during the experiment). That is itself a reported finding ("no scarcity-exposed pairs in this cooling season"), not a failure mode.

**Display rule for `percent_diff_dollars` (small denominator):** when `hvac_dollars_a < $5`, the `percent_diff_dollars` column for that pair is reported as "N/A — denominator too small" to prevent inflated ratios from cluttering the table. The absolute `diff_dollars_b_minus_a` is still reported and counts in all bucket statistics.

**What the report does NOT do:**
- Does not declare which arm "won"
- Does not produce a p-value or confidence interval
- Does not aggregate pairs into a single headline number
- Does not infer anything about other households, other climates, other seasons, or productized versions of this controller

**What the report DOES do:**
- Shows per-pair measurements transparently
- Shows breakdowns by pre-registered conditions
- Lets readers see whether savings (if any) were concentrated in particular conditions (e.g., heat events vs shoulder days)
- Provides a measurement basis for future analyses, including those that may reweight or recombine the data under different price regimes (energy prices may rise; what is "trivial dollar" today may be material in future)
- Summarizes any notable cost-matching anomalies: large matched-rate differences (using `cost_match_quality_median_diff_c_per_kwh` from §5 provenance), or concentration of excluded hours during scarcity / 5CP-active / high-temperature windows (using the `excluded_overlap_*` provenance columns). This surfaces structural patterns in WHICH hours got excluded — important for reader interpretation even though the study makes no inferential claim about those patterns.

## 10. Sanity / reconciliation

**Bill reconciliation — descriptive sanity check (NOT a gate):**

Purpose: verify that our Refoss/Eagle measurements + pricing primitives produce believable household-level bill totals. Not a primary outcome, not gating any drops.

- Cadence: monthly, aligned with ComEd bill periods
- Method: reconstruct household variable bill from analysis primitives (whole-home kWh × hourly bill rates + monthly rider snapshots), compare to actual parsed bill line-item totals
- **Whole-home kWh source: Eagle smart-meter delivered-kWh is canonical when available**, because it is the closest available feed to the ComEd revenue meter used for billing. Refoss mains (em:1 + em:7) is used as a fallback when Eagle coverage is insufficient and as a sanity check against Eagle during overlapping periods. Any fallback use is reported with source-availability percentages and drift metrics. **Refoss mains is not silently averaged with Eagle.**
- Divergence threshold: flag if reconstructed monthly variable charges differ from bill variable charges by **>5% OR >$10** (whichever is larger)
- Action: provenance flag + investigation note. **NEVER silently rescale HVAC outcomes to force agreement.**
- Provenance per bill period: `pct_hours_eagle`, `pct_hours_refoss_fallback`, `eagle_refoss_drift_during_overlap` (mean abs difference where both were present)

**Refoss-mains-vs-HVAC sanity (per Q6):**
- Per hour: `(em:2 + em:8 + em:9) ≤ (em:1 + em:7) × 1.10`
- Violation → flag in provenance, NOT drop. Catches channel-mapping errors and CT-calibration drift.

**Refoss-mains-vs-Eagle sanity:** Refoss mains (em:1 + em:7) and Eagle whole-home should track within a tolerance band TBD at audit phase based on observed agreement (production data so far shows them closely aligned). Flag-not-drop.

**Arm 11 fall-back hour (documented non-material edge case):** Arm 11's analysis window contains the 2026-11-01 DST fall-back. The two UTC hours `2026-11-01T06:00Z` (CT 01:00 CDT) and `2026-11-01T07:00Z` (CT 01:00 CST) share the wall-clock label "2026-11-01 01:00" but represent distinct physical hours. Bill-reconciliation lookups and primary HVAC$ computation MUST key on UTC instant or arm-relative hour-index to keep them distinct. Reports and provenance fields that display CT labels for this interval should include the UTC instant or hour-index alongside the label so readers can disambiguate. Materiality: even if a display layer visually conflated the two labels, the maximum monthly bill-reconciliation residual is analytically below $0.01 under realistic overnight load/rate assumptions (overnight DTOD band, ~0.4 kWh whole-home at 01:00 CT) and cannot move the >5%/$10 sanity threshold. No reconciliation-side tolerance machinery is required; this is a display-layer disambiguation note, not a computation defect.

**Carbon-Free Energy Resource Adjustment per-arm-period tracking (L1):** the CFE credit is a per-kWh adjustment that varies monthly (April 2026: −3.186¢/kWh ≈ 40% of typical per-kWh rate magnitude). For each arm period, record the CFE rate in effect during that bill cycle to per-pair provenance. If two paired arms fall in different bill cycles with CFE rates differing by >0.5¢/kWh, flag the pair's `cfe_shift_flag` in provenance. Symmetric application means CFE doesn't change B − A direction, but it does affect total $ magnitude readability across pairs. The per-pair table includes a `cfe_c_per_kwh_a` and `cfe_c_per_kwh_b` column.

## 11. Pre-OSF dependencies and audit-phase fix list

**Critical-path deliverables before 2026-05-30 OSF filing:**

1. **Arm calendar + mode gating in hvac-scheduler**: read locked A/B calendar; gate `execute_action` setpoint-write path; Arm A dry-run/shadow only; Arm B active.
2. **Mode telemetry**: write `hvac.arm_mode` with values `A-active` / `B-active` / `B-fallback` / `B-down` every 5-min decision cycle. (Manual-override is NOT tracked per operator commitment; protocol deviations handled via amendment.)
3. **Switch-event logging**: write `hvac.switch_event` row at each boundary with `from_arm`, `to_arm`, `boundary_planned_ts`, `boundary_actual_ts`.
4. **Input-feed health telemetry**: per cycle, log health of price feed, weather/forecast feed, PJM capacity-risk inputs. B-active classification uses the conditional rule from §5: PJM capacity-risk feeds are only "required for B-active" inside the §5.1 capacity-risk operating window.
5. **Controller heartbeat / liveness**: out-of-band watchdog (systemd timer or external cron) writes `controller_alive=false` if no `hvac.arm_mode` row in last 10 min. Distinguishes B-down from missing-data.
6. **rt_hrl_lmps poller**: add `rt_hrl_lmps` feed to `pjm-dm2-poller`. Backfill from 2026-01-01 (minimum) or 24-month retention (ideal).
7. **DTOD analysis-rate table**: load resultant rates (Section 8 table) into analysis pipeline. Controller continues using base rates unless explicitly changed pre-freeze.
8. **CTK04AE Arm A schedule documentation**: lock `docs/HVAC_LOGIC.md` plus a referenced `docs/THERMOSTAT_ARM_A_SCHEDULE.md` (if not already present) with weekday/weekend schedule, setpoints, time boundaries, AIR/recovery settings, effective date. Cite OSF commit hash. **Status (2026-05-18 Phase 5):** schedule frozen at `docs/THERMOSTAT_ARM_A_SCHEDULE.md` with TCC web UI screenshot evidence at `docs/THERMOSTAT_ARM_A_TCC_SCREENSHOT_2026-05-18.png`. Two cells of the prior HVAC_LOGIC.md transcription (Wake cool, Leave heat) were corrected against the screenshot. OSF commit hash remains `pending-osf-filing` until OSF deposit; the YAML header in the new doc carries that placeholder explicitly.
9. **Dry-run guard audit**: verify NO Control4 setpoint-write path is invoked during dry-run. Comprehensive audit of all `execute_action` branches. Test coverage proving non-touch behavior.
10. **Analysis pipeline rewrite**: Stage 3 / Stage 5 reframed around arm-period unit; remove $/CDD scaffolding; remove weekly aggregation. Implement single pre-matching gate (Section 5) + cost-matched exclusion (Section 5). Cherry-pick Eagle manifest/query work + actual-dollar helpers from PR #109. PR #109 closed as superseded after spec lands.
11. **NOAA-archived automated airport weather station fallback selection**: lock station ID for Ecowitt-gap fallback (candidates KJOT Joliet, KARR Aurora, KMDW Midway, KORD O'Hare; mix of FAA AWOS-3 and ASOS, all in the same NCEI archive). Criteria: proximity to Plainfield IL + completeness of hourly temp + dewpoint. No historical pull needed — within-sample standardization makes ERA5 unnecessary.
12. **Day-type schedule completeness**: verify `docs/HVAC_LOGIC.md` enumerates every Arm B day-type schedule (MILD / NORMAL / HOT / HOT_STREAK_DAY1 / etc.) with hour-by-hour setpoints. Patch any gaps before OSF. **Status (2026-05-18 Phase 5):** verified against `deploy/energy-stack/hvac-scheduler/app.py` — the four day types defined in code (MILD, NORMAL, HOT_5CP_RISK, HOT_STREAK_DAY1) are each documented with hour-by-hour setpoint tables in HVAC_LOGIC.md "Day types" and "Schedules" sections. The HOT_STREAK_DAY1 trigger description was patched in this PR to document both escalation paths (multi-day heat AND single-day forecast 5CP-risk per §7), matching `decide_day_type` in `app.py`. No day-type / schedule gaps remain.
13. **Shadow validation run**: full dry-run on pre-experiment shadow data, exercising pipeline through Stage 5 outcome table. Validates ingestion, pricing, Refoss HVAC, Refoss-mains/Eagle reconciliation, weather-vector construction, arm calendar logic, no-write Arm A behavior. Pass/fail report artifact (NOT outcome evidence). **Scarcity-divergence audit (M3):** for shadow-period hours where `comed.prices` 5-min average exceeded its 95th percentile, compute abs diff vs `rt_hrl_lmps` settled. Report `max`, `p95`, `n_hours_diverging_>2c`. This characterizes how much the live-vs-settled split matters at the hours where controller decisions matter most.

## 12. Sensitivities (Q9 #3 lock)

**Kept:**

| Sensitivity | What it tests |
|---|---|
| `include_washout` | Re-run primary including the first 48h post-switch in aggregation. Descriptive: does the washout exclusion change the conclusion? |
| `weighted_matching` | Re-run Hungarian matching with weights `w_CDD = w_mean_daily_max_temp = 1.5`, others = 1.0. Tests sensitivity to component weighting. |
| `live_price_vs_settled_price` | Re-compute HVAC$ supply component using 5-min `comed.prices` hourly average instead of `rt_hrl_lmps`. Descriptive: how much does the bill-canonical choice matter? |
| `exclude_poor_weather_match_pairs` | Re-run aggregate summary EXCLUDING pairs flagged `poor_weather_match_flag = True` (>90th-percentile weather distance). Descriptive: how much do poorly-matched pairs influence the summary? Note: primary INCLUDES these pairs by default; this sensitivity flips that to provide the "if we had excluded" view. |

**Dropped:** $/CDD, kWh/CDD, cooling-relevance gates, `five_min_pricing` as outcome sensitivity, `em2_em8_only` as major sensitivity (at most a minor diagnostic), `include_fallback_as_arm_b` (old Q4 framing — NOT added; this study isn't about reliability), `mahalanobis_matching` (within-sample standardization makes Euclidean fully defensible; Mahalanobis on N=12 has unstable covariance).

**Reporting:** Each sensitivity appears alongside primary in a sensitivity table. Differences from primary surface as `Δ_sensitivity` columns. NO p-values on sensitivities (descriptive only).

## 13. PR #109 disposition

**Do not merge.** Leave open as draft until this spec (and the cherry-pick PRs that salvage parts of #109) are merged to main. **Close PR #109 as superseded immediately after the cherry-pick PRs land** (mid-Phase 3, not end-of-Phase-6). Earlier closure removes a stale draft from review queue and prevents accidental late merges. (L2 resolution: spec and plan agree on closure timing.)

**Salvageable via cherry-pick or manual port:**
- Eagle manifest entries + `eagle.meter.flux` query
- `weekly_actual_dollars` helper concept (re-implemented as `arm_period_actual_dollars`)
- Eagle Refoss-mains drift test logic (re-implemented per Section 10 reconciliation)
- Eagle coverage helper (re-implemented per Section 10)

**Discard:**
- Weekly Stage 3/5 shape (replace with arm-period-shape)
- `$/CDD` scaffolding (gone)
- Whole-home-primary framing (Eagle is reconciliation, not co-primary)
- Smoke-test defaults in `_load_week_inputs_from_stage1` (per audit P0)
- Silent gap smearing (per audit P0)

## 14. Limitations (for OSF transparency)

This is a **single-household n-of-1 case study**. Results are not generalizable. Specifically:

- **Estimand is per-protocol, not intent-to-treat.** The primary outcome answers "when the smart controller operated as intended, did its strategy reduce cost vs the standard schedule at matched cost conditions?" It does NOT answer "would this controller, including its reliability failures, save money under continuous real-world operation." This study explicitly excludes controller-failure hours from the primary aggregate. Whether the algorithm plus a reliability layer would produce real-world savings is a separate question not addressed here.
- **N = 6 matched pairs at best** (fewer if the per-arm validity gate drops any arms). The per-pair table is the deliverable, not aggregate inference. This is a discovery study (§9.5) — no statistical claim is made about effect size, significance, or direction. Poor-weather-match pairs are flagged but kept in primary; an optional sensitivity (§12) shows the alternative.
- **Single household**, single climate (Chicago, IL), single HVAC system (gas furnace + central AC), single occupant pattern.
- **Single cooling season** (Summer 2026). No multi-year replication.
- **No retrospective baseline**: instrumentation begins 2026-04-29 (Refoss) and 2026-05-11 (Ecowitt). No 2024/2025 pre-experiment history.
- **Threshold calibration window**: HVAC validity thresholds derived from 13.7 days of pre-cooling-season data. Mid-experiment data showing materially different telemetry behavior is treated as a protocol-deviation/amendment, NOT silent re-tuning.
- **Weather-only matching** can pair calendar-distant arms (e.g., June with October) if weather vectors are similar. Seasonal effects on sun angle, occupant routine, HVAC degradation not captured. `temporal_gap_days` reported as provenance.
- **Bill ingestion partial coverage**: first DTOD bill arrives 2026-05-24; pre-DTOD bills used flat rates. Rate-in-effect-by-service-period applied where reliably ingested; OSF snapshot otherwise.
- **Mixed weather source per arm (Ecowitt primary, NOAA fallback)**: when Ecowitt has gaps, the locked NOAA-archived station (KJOT, AWOS-3) fills missing hours within the same arm's vector. Provenance reports the per-arm Ecowitt/NOAA percentage split. Standardization is computed within-sample (N=12), so no cross-source z-score concerns.
- **Low-cooling-runtime arms NOT excluded from primary aggregation.** Per operator's stated framing (descriptive transparency over hidden filters), pairs with low `cooling_active_hours` are flagged via `low_cooling_exposure_flag` but kept in the per-pair table and decision rule. `percent_diff_dollars` is reported as "N/A — denominator too small" when `hvac_dollars_a < $5` to prevent inflated ratios from skewing summary stats. Absolute `diff_dollars_b_minus_a` is still reported and counts in the decision rule.
- **Manual overrides not tracked in telemetry.** Per operator commitment, no manual thermostat overrides will occur during the experiment. If overrides do occur (e.g., guest, equipment failure, accidental adjustment), they are protocol deviations reported in the final analysis narrative as deviations, not absorbed silently into the primary outcome. There is no detection mechanism in the telemetry; this is a known limitation.
- **Carbon-Free Energy Resource Adjustment** is monthly-variable; the April 2026 -3.186¢/kWh credit may not repeat. Affects total $ magnitude but not B − A comparison (applied symmetrically to both arms).

## 15. Open follow-ups (spec-implementation phase)

These items need concrete numbers/specs in the audit/tasks phase, not blocking OSF:

- Refoss-mains vs Eagle whole-home discrepancy tolerance for bill-reconciliation provenance: TBD per audit (production data shows close tracking; set tolerance based on observed agreement)
- ~~NOAA ASOS fallback station selection (KJOT Joliet / KARR Aurora / KMDW Midway / KORD O'Hare): TBD per audit (criteria: proximity to Plainfield IL + hourly temp + dewpoint completeness)~~ **Resolved 2026-05-18:** locked to KJOT Joliet (AWOS-3) per `docs/replay-validation/2026-05-18-noaa-fallback-station-selection/findings.md`. See §6 for the data-source paragraph.
- ~~Day-type schedule completeness audit: verify every Arm B day-type schedule (MILD / NORMAL / HOT / HOT_STREAK_DAY1 / etc.) is enumerated in `docs/HVAC_LOGIC.md` at the OSF-freeze commit; patch any gaps~~ **Resolved 2026-05-18:** verified and patched per §11 #12 status note. HOT_STREAK_DAY1 trigger description in HVAC_LOGIC.md expanded to document both escalation paths (multi-day heat AND single-day forecast 5CP-risk per §7).
- DST-fold handling in arms 11-12 (2026-11-01 02:00 → 01:00 CT): nocturnal-min aggregation window (22:00-06:00 CT) needs `zoneinfo` not hardcoded offset
- Sensitivity table format and report layout: spec/implementation phase
- ~~Capacity-risk operating window precise dates (§5.1): currently locked as 2026-06-01 → 2026-09-30. Confirm against PJM historical 5CP-distribution data before OSF freeze. PJM 5CP hours over recent summers (2020-2024) almost exclusively fall June-August; September is borderline. Audit data informs whether end-of-window should be 09-15, 09-30, or 10-15.~~ **Resolved 2026-05-18:** 09-30 is the regulatory 5CP eligibility window endpoint per PJM Manual 19 and ComEd Attachment M-2 (already cited in `docs/EXPERIMENT_DESIGN.md` line 569 and `deploy/energy-stack/hvac-scheduler/pjm_5cp.py:140`). Not an empirical choice — fixed by tariff. The empirical confirm question was framed wrong in the original §15 wording: the audit-grade question is "does PJM Manual 19's definition match what we wrote?" (yes, 09-30), not "what date does the data suggest?" Three years of ComEd RTP price data (2023-2025) independently confirm zero ≥10¢/kWh stress events in October at this load center, which is consistent with the regulatory boundary but not the source of truth for it. §5.1 updated with the regulatory citation co-located with the date.
- Broader repo/docs audit for "PJM/5CP feed" and "5CP prediction" wording: review `docs/HVAC_LOGIC.md`, code comments, and `deploy/energy-stack/hvac-scheduler/pjm_5cp.py` for instances where the distinction between (a) live capacity-risk detector and (b) official post-season 5CP list matters. Replace only where misleading. Do NOT rename historical artifacts like `pjm.coincident_peak` InfluxDB measurement or the `pjm_5cp.py` module filename — those remain valid as post-season truth-source terminology.
