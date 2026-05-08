# Experiment Design — Residential HVAC Controls Field Study (N=1)

**Status**: Pre-registration draft (revised 2026-05-07). Binding once filed to OSF. No data unblinding before pre-registration is filed.
**Owner**: Chris dePaola (owner-as-investigator, owner-as-only-subject)
**Ethics framing**: unaffiliated owner self-experimentation with no recruited participants. No institutional IRB is currently engaged. If the work is later submitted through an institution, a formal IRB / NHSR determination will be requested at that time. The investigator's working interpretation of the regulatory landscape, and limitations of that interpretation, are documented in §11.
**Companion docs**: [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md) (controller methodology for Arm B), [`HVAC_LOGIC.md`](HVAC_LOGIC.md) (current scheduler day-type logic for Arm A), [`INFLUXDB_RETENTION.md`](INFLUXDB_RETENTION.md) (data persistence guarantees).

---

## 1. Background and rationale

The Khabbazi et al. 2025 meta-review of residential HVAC MPC field demonstrations ([arXiv 2503.05022](https://arxiv.org/abs/2503.05022), [Applied Energy](https://www.sciencedirect.com/science/article/abs/pii/S0306261925011894)) classified only 29% of 24 reviewed residential studies as methodologically reliable. The 71% flagged for confounding share a common design flaw: **sequential pre/post comparison** (one period of incumbent control followed by one period of treatment control), which lets weather, occupancy, equipment aging, and tariff drift contaminate the savings estimate. Khabbazi explicitly identifies, as the field's most consistent unmet need: long-duration, well-instrumented residential field comparisons with **within-subject randomized alternation between control modes**, explicit deployment cost reporting, and peak-shaving / wholesale-price-arbitrage objectives.

This study fills that gap for a single instrumented home. It compares two controllers (defined in §3) under randomized week-level alternation across one or more cooling seasons starting summer 2026, with full telemetry, ComEd bill ingest, and pre-committed analysis plan.

Saloux et al. (2025), reviewing 91 MPC field implementations across 19 years of literature, taxonomize four post-implementation performance-evaluation methods (their §2.4): Option #1 "Twin buildings" (parallel between-building), Option #2 "Before and after" (within-subject sequential or alternating), Option #3 "Model for BAU" (modeled baseline + measured MPC), Option #4 "Models for both arms." Saloux explicitly recognize alternation as a within-Option-#2 variant: *"One can also alternate reference operation and MPC every day or every few days during the implementation period."* This study is an **Option #2 implementation with randomized within-block alternation and pre-committed analysis** — directly addressing a gap Saloux identifies in §5.2.2: *"reported savings do not have the same significance... it is challenging to compare control strategies against each other fairly. We recommend the development of a methodology that would standardize the savings assessment following MPC implementation."* The field's average test duration across Saloux's 91-paper corpus is **41 days**, with 29% of papers under one week; this study's 18-week target is ~3× the average and well beyond Saloux's recommended ≥1-month minimum.

Saloux also flag survival bias in §5.1.1 — *"only success stories are generally published in academic journals... and there might have been initiatives over the years when MPC was eventually ineffective"* — which §8 of this design directly mitigates by pre-committing to publish negative or null results regardless of outcome.

The study's contribution is **methodological as much as substantive**: no published residential HVAC study cites the single-case experimental design (SCED) literature, despite SCED being the right statistical framework for randomized within-subject comparisons. We adopt SCED randomization-test methodology ([Heyvaert & Onghena 2014, *Journal of Contextual Behavioral Science* 3(1):51–64](https://doi.org/10.1016/j.jcbs.2013.10.002)) explicitly. The closest published residential alternation precedent is Lindelöf et al. (2015), which used **deterministic** ≥2-week alternation in 10 Swiss dwellings; this design extends Lindelöf with **block-randomized** allocation, paired randomization-test inference, and pre-committed identical setpoint schedules across arms.

## 2. Hypotheses (pre-committed, primary and secondary)

All hypotheses are pre-committed before any data unblinding.

### Primary hypothesis (H1)

> **Mean weekly HVAC-circuit electricity cost** (supply + delivery on the dedicated HVAC Refoss channels, normalized to weekly cooling-degree-days) is lower under Arm B than Arm A by at least 5%, on a per-week paired-randomization-test basis.

**Direction**: one-sided (Arm B < Arm A).
**Effect-size threshold**: 5% relative reduction at the median.
**Stat threshold**: randomization test p ≤ 0.05 against the null of no difference, AND bootstrap 95% CI lower bound > 0%.

**Why HVAC-circuit cost rather than whole-home cost as primary** (revised 2026-05-07): the experiment tests a controller, and the controller's authority extends only to the HVAC equipment. Whole-home cost includes uncontrolled loads (cooking, laundry, IT, dehumidifier) that add noise without measuring the scientific claim. The whole-home metric is retained as a household-level reconciliation outcome (§6) for ASHRAE G14 / IPMVP comparability.

### Secondary hypotheses (H2–H4)

H2 (peak kW, within-summer): mean weekly **maximum 1-hour HVAC kW draw** is lower under Arm B than Arm A by at least 10%.

H3 (comfort, within-summer): mean weekly **degree-hours of indoor temperature exceedance above the comfort ceiling** (78°F default; per-day-type ceilings per the existing scheduler) is **not worse** under Arm B than Arm A. Pre-committed non-inferiority margin: **the larger of +20% relative or +4 °F·hr/week absolute**. The absolute fallback prevents the relative margin from becoming meaningless on cool weeks where baseline exceedance is near zero (a common SCED pre-registration pitfall flagged by CodeX 2026-05-07).

H4 (capacity-charge, cross-summer, descriptive): the **summer-2027 ComEd capacity-charge line item** (determined by summer-2026 PJM 5CP coincident-peak readings) compared to the prior-year capacity-charge line item. Reported descriptively only as a YoY $ delta with 95% CI from the summer-2026 weekly-peak distribution; no directional accept/reject. Descriptive because the YoY comparison mixes controller-driven coincident-load changes with year-to-year changes in PJM/ComEd capacity prices, and disentangling those at N=1 is not credible.

H4 is the only hypothesis that requires waiting until ComEd posts the next year's locked $/kW (typically June 2027). H1–H3 are answered when summer 2026 closes (October 2026 after final bill).

### Stop-loss (null-direction safety)

> If at week 4 of alternation **either** trigger fires, halt alternation, return scheduler to Arm A only, and investigate:
> - **Relative trigger**: Arm B's weekly comfort exceedance hours exceed Arm A's by more than 2× the baseline week's value, **AND** the baseline week's value is ≥ 2 °F·hr (so the multiplier is meaningful).
> - **Absolute trigger**: Arm B's weekly comfort exceedance exceeds Arm A's by more than 8 °F·hr/week regardless of relative ratio.

This is a safety override, not a hypothesis. Pre-committed so we cannot retroactively rationalize a comfort failure as acceptable noise. The absolute trigger covers the case where baseline exceedance is near zero and a 2× ratio would either fire on noise or never fire at all — a SCED preregistration pitfall flagged by the CodeX 2026-05-07 review.

## 3. Arms / conditions

### Arm A — Baseline RBC (control)

The `hvac-scheduler` as it exists at the start of the alternation period: day-type classifier (`MILD` / `NORMAL` / `HOT_5CP_RISK` / `HOT_STREAK_DAY1`) with hand-tuned setpoint schedules per day-type, intra-day forecast revisit at hours `[6, 11]`, MILD release of yesterday's permanent hold, and the existing capacity-peak window definitions. No thermal-model-driven optimization. Full schedules and decision logic in [`HVAC_LOGIC.md`](HVAC_LOGIC.md). Frozen at the commit hash committed to OSF pre-registration.

**Known flaws in Arm A** (per Saloux et al. 2025 Appendix D, "current control flaws" reporting): (a) pre-cool depths are set as hand-tuned constants per day-type without per-house thermal model fit, so they're necessarily conservative or aggressive relative to the actual envelope; (b) COAST shutoff timing is fixed at 14:00 CT for HOT days regardless of indoor-temperature trajectory; (c) the dewpoint-65°F humid override threshold is hand-tuned, not derived from a saturation-enthalpy comfort calculation; (d) day-type classification is binary (MILD / NORMAL / HOT / HOT_STREAK) with no continuous interpolation, so a 94°F NORMAL day and a 95°F HOT day produce qualitatively different schedules with a 1°F sensitivity on forecast bust. These are exactly the flaws Arm B's three model-informed substitutions are designed to address.

### Arm B — RBC + Step 1 model-informed

Arm A's day-type classifier and overall structure, **plus** the three Step 1 integration points from [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md):

1. Pre-cool depth set by envelope-ODE integration using the ratified per-house thermal model (`τ`, cooling capacities, solar proxy).
2. COAST shutoff lead time computed in closed form from the same model.
3. Stage-2-during-5CP-hours advisory log entries (read-only in Step 1).

**Architecture clarification** (per Saloux 2025 Appendix D, "MPC application" categorization and "optimization type"): **Arm B is not a horizon-based MPC.** It is rule-based control with three model-derived substitutions for hand-tuned constants. There is no rolling-horizon optimization, no objective function solved at each timestep, no prediction horizon to tune. The envelope-ODE integration that sets pre-cool depth uses a fixed forward-integration window matching the schedule's pre-cool-to-coast transition (8 hours; specified in `THERMAL_MODEL_DESIGN.md`). The closed-form COAST shutoff lead time is a single algebraic expression evaluated once per cycle, not iteratively optimized. Per Saloux's §2.2 application taxonomy, Arm B is most accurately classified as **"setpoint optimization"** (substitute three setpoint-determining constants for model-derived equivalents) rather than full MPC. This scoping is intentional: it's the smallest model-informed change that can be specified, fit, validated, and pre-registered for an N=1 study within one cooling season; full rolling-horizon MPC is the Step 2 / Step 3 plan in [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md), out of scope for the present pre-registration.

Both arms run the same intra-day forecast revisit, same MILD-release logic, same scheduler safety supervisor (when implemented), same observability. **Critically, both arms use the same thermostat setpoint schedule structure** (same time-of-day breakpoints, same humid-override threshold, same comfort ceilings) — Arm B differs only in the three model-derived values that fill in three otherwise-hand-tuned constants. Pre-committed: any operator-initiated mid-trial schedule change to either arm flags the affected week as excluded from primary analysis (§6 week-validity rules). Frozen at the same commit hash committed to OSF pre-registration.

## 4. Study design

**Type**: Randomized alternating-treatments single-case experimental design (SCED), week-level alternation.
**Duration**: One full cooling season minimum (target: June 1 to September 30, 2026). Continuation criteria in §8.
**Subject**: One single-family owner-occupied residence in IECC climate zone 5A. Single-zone HVAC (one Amana CTK04AE thermostat — Honeywell-OEM whitelabel with native RedLINK Wi-Fi + CT-485 communicating bus). Equipment: Amana AMVM971005CN modulating gas furnace + Amana ASXC160481BE 2-stage AC.
**Investigator**: Owner; no recruited subjects.
**Blinding**: Investigator is necessarily unblinded (controls the system). Mitigated by pre-registered analysis plan and frozen analysis code (§7).

## 5. Randomization

**Unit of randomization**: calendar week (Monday 00:00 CT through Sunday 23:59 CT).
**Block design**: blocks of 2 weeks, each containing one Arm A week and one Arm B week, order randomized within block. Guarantees within-month balance and prevents long single-arm runs.
**Seed**: `20260601` (the date of pre-registration commitment, deterministic).
**Method**: assignment list generated by [`deploy/energy-stack/scripts/randomize_arms.py`](../deploy/energy-stack/scripts/randomize_arms.py) from the seed, committed to the repo and OSF before week 1. The assignment list is part of the pre-registration record. Output: [`docs/experiment-assignments-summer-2026.csv`](experiment-assignments-summer-2026.csv) — a CSV of `(iso_week, monday_date, arm)` triples covering 2026-06-01 through 2026-09-28 (18 weeks: 9 Arm A, 9 Arm B). The script's pinned-snapshot test (`test_pre_registered_seed_first_assignment_is_pinned`) fails loud if the algorithm or seed ever drifts.

**Switch mechanism**: `hvac-scheduler` reads the assignment list at startup of each calendar week and selects Arm A or Arm B logic. Switch is automatic; no investigator action between weeks. Switch logged to `hvac.actions` with explicit `arm` tag.

**Post-switch washout** (revised 2026-05-07): in an alternating-treatments SCED, transition effects between arms are recurring carryover, not a one-time burn-in. The first **W hours** after each Monday 00:00 CT arm switch are flagged as post-switch washout and **excluded from the primary analysis**, applied identically to both arms.

`W` is set as follows:

- If a ratified pre-study thermal model (per [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md)) exists before alternation begins: `W = ceil(2τ / 12h) × 12h`, where τ is the fitted thermal time constant. `W` is clamped to `[24h, 72h]` so a tight envelope can't shrink the washout below physical-air-rebalance time and an unrealistically large τ can't eat more than ~43% of any arm-week.
- If no ratified τ exists before alternation begins: `W = 48h`. Defended by the upper bound of typical residential cooling envelope τ (10–30h for stick-frame, 30–60h for masonry), which 48h covers comfortably.

The selected `W` is frozen and committed to OSF before any outcome unblinding. The same `W` applies to both arms; asymmetric washouts would bias the comparison.

**Sensitivity analysis** (pre-committed): re-run primary analysis with washout hours **included**. If the sign or significance of H1 differs between primary (washout-excluded) and sensitivity (washout-included), both results are reported and the disagreement is itself the conclusion. Same disagreement-is-the-conclusion discipline as the regression-vs-randomization-test companion analysis in §7.

## 6. Metrics

All metrics defined precisely so post-hoc redefinition is impossible.

### HVAC channel set (pre-committed)

For all metrics that reference "HVAC-circuit", the set of `refoss.channel` channels is pinned as:

- `em:2` and `em:8` — the two AC compressor legs of the Amana ASXC160481BE 2-stage AC.
- `em:9` — the Amana AMVM971005CN furnace blower (which moves cool air during cooling cycles as well; cooling-season usage of this channel is dominated by AC duty cycle).

Whole-home electricity is tracked separately on the split-phase mains `em:1 + em:7`. The HVAC channel set is part of the pre-registration record and frozen at the `randomize_arms.py` commit hash.

### Primary

**`weekly_hvac_cost_per_cdd_$/CDD`** (revised 2026-05-07, promoted from secondary): sum across all hours in the calendar week of (`hvac_circuit_hourly_kWh × hourly_supply_price`) + (`hvac_circuit_hourly_kWh × delivery_rate`), divided by sum of `cooling_degree_days_base65F` for the week from NWS reanalysis. `hvac_circuit_hourly_kWh` is the hourly integral of `refoss.channel.power_w` summed across the HVAC channel set. CDD computed as `max(0, daily_mean_F - 65)`. Capacity-charge components are explicitly excluded — capacity is billed monthly off coincident-peak monthly kW and cannot be allocated cleanly to a single circuit hour-by-hour. Capacity-side effects are addressed in H4.

**Pricing granularity**: hourly. ComEd's per-hour supply price (`comed.prices` `period_type=hourly_avg`) is used rather than the 5-min real-time price so the metric reconciles cleanly to the bill (which uses hourly average for variable-rate residential customers). A sensitivity using 5-min pricing is pre-committed under §7 if the hourly approximation is challenged.

### Household reconciliation (formerly primary, now secondary)

**`weekly_cost_per_cdd_$/CDD`** (revised 2026-05-07, demoted from primary): sum of (`hourly_kWh × hourly_supply_price`) + (`hourly_kWh × delivery_rate`) across all hours in the calendar week, divided by sum of `cooling_degree_days_base65F` for the week. Whole-home `hourly_kWh` from EAGLE meter (or `em:1 + em:7` Refoss as the cross-validation source). Retained as a reported secondary outcome for ASHRAE G14 / IPMVP whole-facility comparability and for end-to-end ComEd-bill reconciliation. Not the primary statistical test because it includes uncontrolled household loads outside the HVAC controller's authority.

### Secondary (formal tests)

- **`weekly_peak_kw_1hr`**: maximum of the 1-hour rolling mean of `refoss.channel.power_w` summed across the HVAC channel set.
- **`weekly_comfort_exceedance_F_hr`**: integrated (over the week) of `max(0, indoor_temp_f - comfort_ceiling)`, where the ceiling is 78°F by default and follows day-type if the scheduler defines a different ceiling for that day-type.

### Secondary (descriptive)

- **`weekly_hvac_kwh_per_cdd`**: total HVAC-circuit kWh / weekly CDD. Reported alongside the primary as the energy-only counterpart of the cost-based primary.
- **`weekly_compressor_cycles`**: count of stage-1-to-on and stage-2-to-on transitions in `hvac.comfortnet.cl_act`, per week.
- **`weekly_dehumidify_kwh_per_cdd`**: HVAC kWh attributable to humidity-driven cycles (identified via `hvac.comfortnet` dehumidify flag), normalized by CDD.

### Cross-summer (H4 only)

- **`capacity_charge_yoy_$`**: ComEd capacity-charge line item from the post-summer bill, vs prior summer's same line item.

### Week-validity rules (pre-committed exclusions)

A calendar week is included in primary analysis if and only if:

1. **CDD floor**: `weekly_cdd_base65F ≥ 14 °F·day` (≈ 2 °F·day average). Below this the cooling load is too low to differentiate scheduler behavior; per-CDD ratios become numerically unstable. Weeks below the floor are reported in a sensitivity-analysis subset, not the primary.
2. **Telemetry completeness**: ≥ 90% of expected EAGLE samples present (≥ 18,144 of 20,160 expected at 30 s cadence over 7 days), AND ≥ 90% of expected `hvac.thermostat` samples present.
3. **No mid-week arm switch**: the assignment list switches arms only at calendar-week boundaries. Any operator-initiated mid-week override (vacation hold, manual day-type force) flags the week as excluded from primary; reported in sensitivity.
4. **No equipment fault**: no `hvac.comfortnet.events.fault` of severity ≥ "critical" during the week. Faulted weeks reported separately.

**Missing CDD denominator handling**: if all weeks in a comparison block (one A + one B) fall below the CDD floor, the block is dropped from the randomization test entirely (not imputed). Pre-committed so a cool summer cannot be salvaged with weak data.

**Minimum analyzable sample**: if fewer than 6 valid blocks (12 weeks) survive these exclusions by end of summer 2026, the primary test is reported with a flag noting reduced power, and the study continues into summer 2027 per §8.

These rules are pre-committed per the CodeX 2026-05-07 review recommendation that "a cool week, a mild summer, or a baseline week with zero exceedance can dominate the denominator and make the analysis fragile after the fact."

## 7. M&V protocol and statistical analysis plan

### M&V framing

Primary M&V framework: [ASHRAE Guideline 14-2023](https://webstore.ansi.org/standards/ashrae/ashraeguideline142023) §5 (Whole-Building Approach), with the specific deviation that we use within-subject randomized alternation rather than baseline-then-retrofit. [IPMVP Option C](https://evo-world.org/en/products-services-mainmenu-en/protocols/ipmvp) (Whole Facility) for energy-cost reconciliation against actual ComEd bills.

**Weather normalization**: cooling-degree-day denominator on all energy and cost metrics. Sensitivity analysis with change-point regression (G14 Annex D method) using daily mean outdoor temperature from `weather.ecowitt` (post-installation; `nws.forecast` historical for pre-installation periods) as the independent variable.

**Comfort assessment**: ASHRAE 55-2020 PMV/PPD computed at hourly granularity using `hvac.thermostat.indoor_temp_f`, `hvac.thermostat.humidity_pct`, default clothing (0.5 clo summer / 1.0 clo winter), default metabolic rate (1.0 met seated), and assumed air velocity 0.1 m/s. Reported alongside the simpler exceedance-hours metric.

### Statistical analysis plan

**Primary test (H1)**: paired randomization test ([Heyvaert & Onghena 2014, *Journal of Contextual Behavioral Science* 3(1):51–64](https://doi.org/10.1016/j.jcbs.2013.10.002)) on the per-week metric difference `(weekly_hvac_cost_per_cdd_A − weekly_hvac_cost_per_cdd_B)`. Test statistic: median of paired differences within blocks (each block contains one A and one B week). Reference distribution: all possible randomization assignments of the observed weeks under the block constraint. p-value: fraction of randomizations giving a test statistic ≥ observed. Effect-size estimate: median paired difference ± bootstrap 95% CI (B = 10,000 resamples).

**Companion model (parallel report)**: per CodeX 2026-05-07 review, alongside the per-CDD ratio above, fit a weather-normalized regression of weekly HVAC kWh against weekly CDD with an arm dummy: `kWh_week ~ β₀ + β₁·CDD_week + β₂·arm + ε`. Coefficient β₂ is the arm effect with all weather variation absorbed; report point estimate + bootstrap CI. The randomization test on `weekly_hvac_cost_per_cdd` is the pre-committed primary; the regression is a sensitivity check that doesn't depend on the per-CDD denominator behaving well at low load. If the two methods disagree on H1, both are reported and the disagreement is itself the conclusion.

**Secondary tests (H2, H3)** — revised 2026-05-07 to **Holm-Bonferroni**: same randomization-test framework on each secondary metric. Multiple-comparison correction across the **two formal-test secondary hypotheses** (H2, H3) by Holm-Bonferroni at family-wise α = 0.05.

Procedure on the two ordered p-values `p(1) ≤ p(2)`:

1. Reject `H(1)` if `p(1) ≤ 0.05 / 2 = 0.025`.
2. If `H(1)` rejected, additionally reject `H(2)` if `p(2) ≤ 0.05`.
3. If `H(1)` not rejected, do not reject `H(2)` regardless of its p-value.

Holm-Bonferroni (rather than plain Bonferroni at α/2 for both) is more powerful at the small-family limit while maintaining family-wise error control, and is pre-registration-friendly because the procedure is fully deterministic given the observed p-values. Pre-committed: report uncorrected p-values alongside, but conclusions reference the Holm-corrected thresholds.

**H4 is excluded from the multiple-comparison family** (revised 2026-05-07): H4 is descriptive only — it has no significance threshold to correct against. Including it in the Bonferroni denominator was inconsistent with its descriptive framing and was costing power on H2 and H3 without buying anything.

**Cross-summer test (H4)**: descriptive only — reported as a YoY $ delta with 95% CI from the summer-2026 weekly-peak distribution. Not a hypothesis test; no accept/reject decision attached. Result interpreted in narrative context.

**Pre-committed analyses NOT in primary plan**: time-of-day decomposition, heat-wave-only subset, day-type-stratified comparison. Any of these, if reported, are flagged as exploratory.

**Frozen analysis code**: `scripts/analyze_experiment.py` committed to repo and tagged at the commit hash referenced in the OSF pre-registration. Re-run on locked data after summer closes; output files (CSV results + plots) committed to repo as a release tagged `experiment-summer-2026-results`.

## 8. Decision rules and stopping criteria

Pre-committed decisions based on summer-2026 outcomes:

| Outcome on H1 | Decision |
|---|---|
| H1 confirmed (CI lower bound > 0% AND median ≥ 5%) | Step 1 declared effective. Proceed with Step 1 as default for summer 2027. Plan Step 2 (2R2C grey-box) only if specific residuals motivate it (per [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md)). |
| Inconclusive (CI spans 0%, \|median\| < 5%) | Continue alternation through summer 2027 with same controllers and seed (`20260602`). Re-analyze with combined two-summer dataset. |
| H1 disconfirmed (CI upper bound < 0%) | Step 1 is **worse** than baseline. Halt Step 1 deployment. Investigate (model-fit quality, integration-point bugs, comfort-loss-driven aggressive cooling). Submit findings as a negative result — these are valuable to the field per Khabbazi's call for honest reporting. |
| Stop-loss triggered (§2) | Halt alternation immediately, return to Arm A. Document and submit as a "controller failure mode" report. |

**Minimum analyzable sample for confirmatory interpretation** (revised 2026-05-07):

- **≥ 8 valid blocks** (16 paired weeks) after §6 exclusions: H1, H2, H3 reported as confirmatory under the §7 thresholds. With 8 blocks the paired-randomization-test reference distribution has 2⁸ = 256 arrangements, giving a minimum achievable p ≈ 0.004 — comfortable resolution against the Holm-corrected α = 0.025 floor for the lead secondary.
- **6–7 valid blocks**: H1 effect estimate and 95% CI reported as **exploratory**; confirmatory interpretation deferred to summer 2027. Resolution at 6 blocks is min p ≈ 0.016, just barely below 0.05 and right at the Holm-corrected 0.025 threshold for the lead secondary, which is too thin to distinguish a true null from a power-limited null.
- **< 6 valid blocks**: study reported as underpowered; no confirmatory or exploratory effect estimates published from summer 2026 alone. Continue to summer 2027 with the same controllers and seed (`20260602`), re-analyze with combined two-summer dataset.

Stopping criteria for the broader study: minimum one cooling season meeting the ≥ 8-valid-block threshold above, **or** combined two-summer dataset meeting it. Maximum study window: 3 cooling seasons (through 2028). After 3 seasons, declare and publish, regardless of outcome. Negative or null results are publishable contributions.

## 9. Anonymization plan

- **Geographic identifiers**: redacted to IECC climate zone 5A and ComEd service territory. ZIP-3 acceptable as secondary tag if requested by reviewers.
- **Account/utility identifiers**: ComEd account number, EAGLE cloud-id, EAGLE install code, Control4 IP and credentials redacted before publication. The `secrets/` SOPS-encrypted layer never leaves the local repo.
- **Equipment**: make/model retained (Amana AMVM971005CN, Amana ASXC160481BE, Amana CTK04AE, Rainforest EAGLE-3, Refoss EM16P). These are useful to other researchers and don't identify the household.
- **Address, name, occupant household composition**: redacted. Investigator name and ORCID retained as standard scholarly authorship.

## 10. Open-data plan

- **Telemetry**: full sub-minute multi-year dataset published as Apache Parquet (one file per measurement per year) on Zenodo with DOI. License: CC BY 4.0.
- **Metadata**: [Brick Schema](https://brickschema.org/) JSON-LD turtle file describing every published point, its unit, sampling cadence, and provenance. One-time tagging effort against the existing InfluxDB measurements (`hvac.thermostat`, `hvac.comfortnet`, `refoss.channel`, `nws.forecast`, `weather.ecowitt`, `comed.bill`, `comed.prices`, `hvac.actions`, `hvac.thermal_model`, `pjm.lmp_da_hourly`, `pjm.metered_load`, `pjm.coincident_peak`).
- **Code**: this repository tagged at the commit hash referenced in the OSF pre-registration. Zenodo automatically generates a citable DOI from the tagged GitHub release.
- **Bills**: parsed bill data (cost line items, peak kW, kWh) included; original PDFs withheld due to embedded account info.
- **Pre-registration**: OSF project URL referenced in published methods section. OSF page is public from the moment it's filed.
- **Embargo**: none. Data and code released alongside the paper submission.
- **DOI structure**: separate DOIs for (a) code at submission time, (b) dataset at submission time, (c) revised dataset if reviewers request additions. All cross-referenced.

## 11. Ethical conduct and ethics framing

This study is conducted as **unaffiliated owner self-experimentation** with no recruited participants. No institutional IRB is currently engaged. If the work is later submitted through an institution, a formal IRB / Non-Human Subjects Research (NHSR) determination will be requested at that time and any required modifications applied.

**The investigator's working interpretation, and its limits.** The investigator is also the only subject. Under 45 CFR 46.102, a "human subject" is "a living individual about whom an investigator … obtains information or biospecimens through intervention or interaction with the individual," and "intervention" explicitly includes "manipulations of the subject … or the subject's environment that are performed for research purposes" ([Cornell LII / 45 CFR 46.102](https://www.law.cornell.edu/cfr/text/45/46.102)). Setpoint changes during a research arm meet the regulatory definition of intervention, and the investigator's residence is the manipulated environment. The Common Rule does not contain a categorical exemption for self-experimentation; whether a determination of NHSR or exempt status applies in any specific institutional context is for that institution's IRB to decide. Per CodeX 2026-05-07 review feedback, this draft no longer asserts a categorical NHSR determination — that determination is for an IRB, not the investigator, to issue.

**Conduct commitments** (binding regardless of regulatory framing):

- Subject is the investigator only, who is also the owner-occupant of the residence under study. No recruitment of additional human subjects is planned for the duration of the pre-registered study.
- Data published from the study includes building telemetry, equipment behavior, and utility-bill line items. The anonymization plan in §9 governs what's released; the investigator's name and ORCID are retained as standard scholarly authorship, but address, account numbers, and household-level identifiers are redacted.
- The investigator has the unilateral right to terminate any controller arm at any time for any reason (comfort, equipment safety, household event, ethical concern), in which case the deviation is logged and reported transparently in any published methods section.
- Household members and guests, if any, are not enrolled subjects. Indoor temperature during arms is held within the comfort ceiling envelope (78 °F primary, with the §6 stop-loss triggers as backstops). If a household member or guest objects to a controller arm at any point, the arm is paused for the duration of their presence and the deviation is logged.
- Animal occupants (companion dogs) are not subjects of the study. Indoor temperature is held within the comfort-ceiling envelope governing human comfort, which is consistent with [AVMA companion-animal welfare guidance](https://www.avma.org/resources-tools/animal-health-and-welfare/animal-welfare-changing-environment) (sustained indoor temperature should not exceed 80 °F).
- Self-experimentation has standing precedent in the scientific literature ([Forssmann 1929 cardiac catheterization](https://doi.org/10.1007/BF01884716), [Marshall & Warren 1984 *H. pylori*](https://doi.org/10.1016/S0140-6736%2884%2991816-6), the broader N=1 / quantified-self lineage); the precedent is offered as context for editors and reviewers, not as a regulatory exemption.

## 12. Limitations

- **N=1**. All generalization claims explicitly bounded to "this house, this climate, this equipment." Multi-home generalization requires future expansion.
- **Single climate zone (IECC 5A)**. Results may not transfer to hot-dry, hot-humid, or marine climates.
- **Single equipment family** (Amana modulating + 2-stage). Not directly applicable to single-stage AC, heat pumps, or radiant systems. Step 2 / heating-season variants partially address.
- **Investigator-as-occupant unblinding**. Documented, mitigated by frozen analysis code and pre-committed metrics, but not fully eliminable.
- **Tariff structure dependence**. ComEd hourly pricing + PJM 5CP capacity charges are specific to this utility/RTO. Transferability to flat-rate, non-coincident-peak, or wholesale-pass-through markets is partial.
- **Step 1 controller scope**. Step 1 does not include true rolling-horizon MPC (see [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md) §rationale). Comparisons to MPC require Step 2/3 or a future arm.

## 13. Pre-registration commitment

The following are binding once this document is filed to OSF and the pre-registration URL is committed to this repo:

1. The hypotheses in §2, with their direction, effect-size thresholds, and statistical thresholds.
2. The arm definitions in §3, including the commit hash for both controllers.
3. The randomization seed (`20260601`) and the `randomize_arms.py` script that derives the assignment list.
4. The metric definitions in §6, including the HVAC channel set (`em:2 + em:8 + em:9`) and the explicit primary-vs-reconciliation split.
5. The post-switch washout duration `W` (§5), including the formula `W = ceil(2τ / 12h) × 12h` if a ratified pre-study τ exists, the `[24h, 72h]` clamp, and the `W = 48h` fallback when no ratified τ exists. The selected `W` is frozen and committed to OSF before any outcome unblinding and applied symmetrically to both arms.
6. The analysis pipeline at the commit hash referenced in OSF, including the Holm-Bonferroni procedure for H2 and H3 (§7) and H4's exclusion from the multiple-comparison family.
7. The decision rules in §8, including the ≥ 8 valid-block threshold for confirmatory interpretation and the 6-7 block exploratory window.
8. The stop-loss criterion in §2.

Changes after pre-registration require an amendment posted to OSF with explicit justification, and will be reported as deviations in any published methods section.

## 14. References

### Methodological framing and field-study meta-reviews

- Khabbazi, A. J., Pergantis, E. N., Reyes Premer, L. D., Papageorgiou, P., Lee, A. H., Braun, J. E., Henze, G. P., & Kircher, K. J. (2025). *Lessons learned from field demonstrations of model predictive control and reinforcement learning for residential and commercial HVAC: A review*. Applied Energy 399, 126459. [doi:10.1016/j.apenergy.2025.126459](https://doi.org/10.1016/j.apenergy.2025.126459) / [arXiv:2503.05022](https://arxiv.org/abs/2503.05022). Source of the methodological-gap framing.
- Saloux, E., Candanedo, J. A., Vallianos, C., Morovat, N., & Zhang, K. (2025). *From theory to practice: A critical review of model predictive control field implementations in the built environment*. Applied Energy 393, 126091. [doi:10.1016/j.apenergy.2025.126091](https://doi.org/10.1016/j.apenergy.2025.126091). Critical-review companion to Khabbazi: 91 implementations over 19 years, 41-day average test duration, four performance-evaluation methods, Appendix D "cheat sheet" for standardized reporting.
- Drgoňa, J., Arroyo, J., Cupeiro Figueroa, I., Blum, D., Arendt, K., Kim, D., Ollé, E. P., Oravec, J., Wetter, M., Vrabie, D. L., & Helsen, L. (2020). *All you need to know about model predictive control for buildings*. Annual Reviews in Control 50, 190–232. [doi:10.1016/j.arcontrol.2020.09.001](https://doi.org/10.1016/j.arcontrol.2020.09.001). Comprehensive MPC-for-buildings methodology review.
- Dhaliwal, G., Gunay, B., & Beausoleil-Morrison, I. (2026). *Parametric analysis of model predictive control for residential HVAC systems*. Energy and Buildings 351, 116662. [doi:10.1016/j.enbuild.2025.116662](https://doi.org/10.1016/j.enbuild.2025.116662). Multi-baseline simulation comparison (MPC vs reactive controllers with varying pre-conditioning); cooling savings range 1.6–11.2% bracketing this study's 5% H1 threshold.

### Statistical method

- Heyvaert, M., & Onghena, P. (2014). *Randomization tests for single-case experiments: state of the art, state of the science, and state of the application*. Journal of Contextual Behavioral Science 3(1), 51–64. [doi:10.1016/j.jcbs.2013.10.002](https://doi.org/10.1016/j.jcbs.2013.10.002). Statistical method for the primary test.
- Holm, S. (1979). *A Simple Sequentially Rejective Multiple Test Procedure*. Scandinavian Journal of Statistics 6(2), 65–70. Holm-Bonferroni multiple-comparison correction (§7).

### Residential MPC field-study precedents

- Brown, S., & Beausoleil-Morrison, I. (2023). *Investigation of a model predictive controller for use in a highly glazed house with hydronic floor heating and cooling*. Science and Technology for the Built Environment 29(4), 347–365. [doi:10.1080/23744731.2023.2196910](https://doi.org/10.1080/23744731.2023.2196910). PPC vs RC sequential pre/post (26 days unoccupied test house) + MATLAB MPC simulation.
- Brown, S., & Beausoleil-Morrison, I. (2023). *Long-term implementation of a model predictive controller for a hydronic floor heating and cooling system in a highly glazed house in Canada*. Applied Energy 349, 121677. [doi:10.1016/j.apenergy.2023.121677](https://doi.org/10.1016/j.apenergy.2023.121677). 182-day continuous MPC at the same CHEeR test house, no comparator.
- Pergantis, E. N., Priyadarshan, Al Theeb, N., Dhillon, P., Ore, J. P., Ziviani, D., Groll, E. A., & Kircher, K. J. (2024). *Field demonstration of predictive heating control for an all-electric house in a cold climate*. Applied Energy 360, 122820. [doi:10.1016/j.apenergy.2024.122820](https://doi.org/10.1016/j.apenergy.2024.122820) / [arXiv:2402.07032](https://arxiv.org/abs/2402.07032). Closest single-house cold-climate field-study precedent (IECC Zone 5A, 33 MPC days, deterministic 5-day stretches, regression-slope inference). 19% (95% CI 13–24%) daily heating-energy savings; 27% (22–32%) annualized cost.
- Pergantis, E. N., Dhillon, P., Reyes Premer, L. D., Lee, A. H., Ziviani, D., & Kircher, K. J. (2024). *Humidity-aware model predictive control for residential air conditioning: A field study*. Building and Environment 266, 112093. [doi:10.1016/j.buildenv.2024.112093](https://doi.org/10.1016/j.buildenv.2024.112093) / [arXiv:2407.01707](https://arxiv.org/abs/2407.01707). Cooling-season analog at the same Zone 5A house. Sensible vs latent humidity model: equivalent for cost reduction (2.32 vs 2.34 kWh/°C); latent reduces peak-power constraint violations by 80% — central to H2 (peak kW) risk discussion.
- Lindelöf, D., Afshari, H., Alisafaee, M., Biswas, J., Caban, M., Mocellin, X., & Viaene, J. (2015). *Field tests of an adaptive, model-predictive heating controller for residential buildings*. Energy and Buildings 99, 292–302. [doi:10.1016/j.enbuild.2015.04.029](https://doi.org/10.1016/j.enbuild.2015.04.029). Closest precedent for residential within-subject alternation: 8 SFH + 2 apartments in Switzerland with deterministic ≥2-week alternation between MPC and reference controller, energy-signature regression, 28% ± 4% savings. This study's randomized block-alternation + paired randomization-test inference is the methodological extension.
- Lindelöf, D. (2016). *Bayesian estimation of a building's base temperature for the calculation of heating degree-days*. Energy and Buildings 134, 154–161. [doi:10.1016/j.enbuild.2016.10.038](https://doi.org/10.1016/j.enbuild.2016.10.038). Bayesian variable-base degree-day methodology referenced for the §7 fitted-balance-point sensitivity.
- Lindelöf, D., Pomerleau, A., Mounier, A., Schaller, M., Vermeulen, R., Pittet, F., Henrici, J. R., Rumley, A., Faraj, A., & Riederer, P. (2017). *Bayesian evaluation of energy conservation measures: a case study with a model-predictive controller for space heating on a commercial building*. Energy Procedia 122, 235–240. [doi:10.1016/j.egypro.2017.07.351](https://doi.org/10.1016/j.egypro.2017.07.351). Worked-example application of the 2016 method using the `homeR` R package.
- Wang, D., Chen, Y., Wang, W., Gao, C., & Wang, Z. (2023). *Field test of Model Predictive Control in residential buildings for utility cost savings*. Energy and Buildings 288, 113026. [doi:10.1016/j.enbuild.2023.113026](https://doi.org/10.1016/j.enbuild.2023.113026). Residential MPC field test using utility-cost savings as primary outcome.
- Knudsen, M. D., Georges, L., Skeie, K. S., & Petersen, S. (2021). *Experimental test of a black-box economic model predictive control for residential space heating*. Applied Energy 298, 117227. [doi:10.1016/j.apenergy.2021.117227](https://doi.org/10.1016/j.apenergy.2021.117227). Norway residential field; minimal-sensing economic MPC.
- Langner, F., Kovačević, J., Spatafora, L., Dietze, S., Waczowicz, S., Çakmak, H. K., Matthes, J., & Hagenmeyer, V. (2025). *Experimental evaluation of model predictive control and fuzzy logic control for demand response in buildings*. Applied Energy 401, 126666. [doi:10.1016/j.apenergy.2025.126666](https://doi.org/10.1016/j.apenergy.2025.126666). Three-identical-buildings parallel between-building comparison; methodological alternative to within-subject alternation.

### M&V, comfort, methodology references

- ASHRAE (2023). *Guideline 14-2023: Measurement of Energy, Demand, and Water Savings*. [ANSI store](https://webstore.ansi.org/standards/ashrae/ashraeguideline142023). M&V framework.
- EVO (2022). *International Performance Measurement and Verification Protocol (IPMVP) Core Concepts*. [evo-world.org](https://evo-world.org/en/products-services-mainmenu-en/protocols/ipmvp). Whole-facility energy reconciliation framework.
- ASHRAE (2020). *Standard 55-2020: Thermal Environmental Conditions for Human Occupancy*. Comfort metric definitions.
- Carlucci, S., & Pagliano, L. (2012). *A review of indices for the long-term evaluation of the general thermal comfort conditions in buildings*. Energy and Buildings 53, 194–205. [doi:10.1016/j.enbuild.2012.06.015](https://doi.org/10.1016/j.enbuild.2012.06.015). Degree-hours-of-discomfort (DDH) framing for the H3 secondary outcome.
- Paulus, M. T., Claridge, D. E., & Culp, C. (2015). *Algorithm for automating the selection of a temperature dependent change point model*. Energy and Buildings 87, 95–104. [doi:10.1016/j.enbuild.2014.11.033](https://doi.org/10.1016/j.enbuild.2014.11.033). Change-point regression methodology for the §7 fitted-balance-point sensitivity (alongside Lindelöf 2016 Bayesian variant).
- Bacher, P., & Madsen, H. (2011). *Identifying suitable models for the heat dynamics of buildings*. Energy and Buildings 43(7), 1511–1522. [doi:10.1016/j.enbuild.2011.02.005](https://doi.org/10.1016/j.enbuild.2011.02.005). Methodological anchor for the Step 1 controller (Arm B).
- Allcott, H., & Rogers, T. (2014). *The Short-Run and Long-Run Effects of Behavioral Interventions: Experimental Evidence from Energy Conservation*. American Economic Review 104(10), 3003–3037. [doi:10.1257/aer.104.10.3003](https://doi.org/10.1257/aer.104.10.3003). Precedent for pre-registered RCT in residential energy.

### Open-data and metadata precedents

- Brick Consortium. *Brick Schema*. [brickschema.org](https://brickschema.org/). Metadata vocabulary for building telemetry.
- Balaji, B., Bhattacharya, A., Fierro, G., et al. (2018). *Brick: Metadata schema for portable smart building applications*. Applied Energy 226, 1273–1292. [doi:10.1016/j.apenergy.2018.02.091](https://doi.org/10.1016/j.apenergy.2018.02.091). Brick reference paper for the open-data publication plan.
- IEA EBC Annex 81 (2024). *Data-Driven Smart Buildings*. [annex81.iea-ebc.org](https://annex81.iea-ebc.org/). Buildings-data-platform reference.
- Saldanha, N., & Beausoleil-Morrison, I. (2012). *Measured end-use electric load profiles for 12 Canadian houses at high temporal resolution*. Energy and Buildings 49, 519–530. [doi:10.1016/j.enbuild.2012.02.050](https://doi.org/10.1016/j.enbuild.2012.02.050). Closest published precedent for the per-circuit submetering this study uses (Refoss EM16P).
- Makonin, S., Ellert, B., Bajić, I. V., & Popowich, F. (2016). *Electricity, water, and natural gas consumption of a residential house in Canada from 2012 to 2014*. Scientific Data 3, 160037. [doi:10.1038/sdata.2016.37](https://doi.org/10.1038/sdata.2016.37). AMPds2 dataset; open-data publication-format precedent.
- Pullinger, M., Kilgour, J., Goddard, N., et al. (2021). *The IDEAL household energy dataset, electricity, gas, contextual sensor data and survey data for 255 UK homes*. Scientific Data 8, 146. [doi:10.1038/s41597-021-00921-y](https://doi.org/10.1038/s41597-021-00921-y). IDEAL dataset; open-data publication-format precedent.

### Standards and regulation

- 45 CFR 46.102 (2018). *Definitions for purposes of this policy*. [HHS](https://www.hhs.gov/ohrp/regulations-and-policy/regulations/45-cfr-46/index.html). NHSR determination basis.
