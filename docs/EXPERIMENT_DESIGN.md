# Experiment Design — Residential HVAC Controls Field Study (N=1)

**Status**: Pre-registration draft (2026-05-06). Binding once filed to OSF. No data unblinding before pre-registration is filed.
**Owner**: Chris dePaola (owner-as-investigator, owner-as-only-subject)
**Ethics framing**: unaffiliated owner self-experimentation with no recruited participants. No institutional IRB is currently engaged. If the work is later submitted through an institution, a formal IRB / NHSR determination will be requested at that time. The investigator's working interpretation of the regulatory landscape, and limitations of that interpretation, are documented in §11.
**Companion docs**: [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md) (controller methodology for Arm B), [`HVAC_LOGIC.md`](HVAC_LOGIC.md) (current scheduler day-type logic for Arm A), [`INFLUXDB_RETENTION.md`](INFLUXDB_RETENTION.md) (data persistence guarantees).

---

## 1. Background and rationale

The Khabbazi et al. 2025 meta-review of residential HVAC MPC field demonstrations ([arXiv 2503.05022](https://arxiv.org/abs/2503.05022), [Applied Energy](https://www.sciencedirect.com/science/article/abs/pii/S0306261925011894)) classified only 29% of 24 reviewed residential studies as methodologically reliable. The 71% flagged for confounding share a common design flaw: **sequential pre/post comparison** (one period of incumbent control followed by one period of treatment control), which lets weather, occupancy, equipment aging, and tariff drift contaminate the savings estimate. Khabbazi explicitly identifies, as the field's most consistent unmet need: long-duration, well-instrumented residential field comparisons with **within-subject randomized alternation between control modes**, explicit deployment cost reporting, and peak-shaving / wholesale-price-arbitrage objectives.

This study fills that gap for a single instrumented home. It compares two controllers (defined in §3) under randomized week-level alternation across one or more cooling seasons starting summer 2026, with full telemetry, ComEd bill ingest, and pre-committed analysis plan.

The study's contribution is **methodological as much as substantive**: no published residential HVAC study cites the single-case experimental design (SCED) literature, despite SCED being the right statistical framework for randomized within-subject comparisons. We adopt SCED randomization-test methodology ([Heyvaert & Onghena 2014, *Journal of Contextual Behavioral Science* 3(1):51–64](https://doi.org/10.1016/j.jcbs.2013.10.002)) explicitly.

## 2. Hypotheses (pre-committed, primary and secondary)

All hypotheses are pre-committed before any data unblinding.

### Primary hypothesis (H1)

> **Mean weekly within-summer electricity cost** (supply + delivery, normalized to weekly cooling-degree-days) is lower under Arm B than Arm A by at least 5%, on a per-week paired-randomization-test basis.

**Direction**: one-sided (Arm B < Arm A).
**Effect-size threshold**: 5% relative reduction at the median.
**Stat threshold**: randomization test p ≤ 0.05 against the null of no difference, AND bootstrap 95% CI lower bound > 0%.

### Secondary hypotheses (H2–H4)

H2 (peak kW, within-summer): mean weekly **maximum 1-hour HVAC kW draw** is lower under Arm B than Arm A by at least 10%.

H3 (comfort, within-summer): mean weekly **degree-hours of indoor temperature exceedance above the comfort ceiling** (78°F default; per-day-type ceilings per the existing scheduler) is **not worse** under Arm B than Arm A. Pre-committed non-inferiority margin: **the larger of +20% relative or +4 °F·hr/week absolute**. The absolute fallback prevents the relative margin from becoming meaningless on cool weeks where baseline exceedance is near zero (a common SCED pre-registration pitfall flagged by CodeX 2026-05-07).

H4 (capacity-charge, cross-summer): the **summer-2027 ComEd capacity charge $** (locked from summer-2026 PJM 5CP coincident-peak readings) is lower than the equivalent supply-and-delivery-share-implied prior-baseline projection. Single-shot cross-summer comparison; reported with a 95% CI from the summer-2026 weekly-peak distribution.

H4 is the only hypothesis that requires waiting until ComEd posts the next year's locked $/kW (typically June 2027). H1–H3 are answered when summer 2026 closes (October 2026 after final bill).

### Stop-loss (null-direction safety)

> If at week 4 of alternation **either** trigger fires, halt alternation, return scheduler to Arm A only, and investigate:
> - **Relative trigger**: Arm B's weekly comfort exceedance hours exceed Arm A's by more than 2× the baseline week's value, **AND** the baseline week's value is ≥ 2 °F·hr (so the multiplier is meaningful).
> - **Absolute trigger**: Arm B's weekly comfort exceedance exceeds Arm A's by more than 8 °F·hr/week regardless of relative ratio.

This is a safety override, not a hypothesis. Pre-committed so we cannot retroactively rationalize a comfort failure as acceptable noise. The absolute trigger covers the case where baseline exceedance is near zero and a 2× ratio would either fire on noise or never fire at all — a SCED preregistration pitfall flagged by the CodeX 2026-05-07 review.

## 3. Arms / conditions

### Arm A — Baseline RBC (control)

The `hvac-scheduler` as it exists at the start of the alternation period: day-type classifier (`MILD` / `NORMAL` / `HOT_5CP_RISK` / `HOT_STREAK_DAY1`) with hand-tuned setpoint schedules per day-type, intra-day forecast revisit at hours `[6, 11]`, MILD release of yesterday's permanent hold, and the existing capacity-peak window definitions. No thermal-model-driven optimization. Frozen at the commit hash committed to OSF pre-registration.

### Arm B — RBC + Step 1 model-informed

Arm A's day-type classifier and overall structure, **plus** the three Step 1 integration points from [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md):

1. Pre-cool depth set by envelope-ODE integration using the ratified per-house thermal model (`τ`, cooling capacities, solar proxy).
2. COAST shutoff lead time computed in closed form from the same model.
3. Stage-2-during-5CP-hours advisory log entries (read-only in Step 1).

Both arms run the same intra-day forecast revisit, same MILD-release logic, same scheduler safety supervisor (when implemented), same observability. The only difference is the substitution of three model-driven calculations for three hand-tuned constants. Frozen at the same commit hash committed to OSF pre-registration.

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

**Burn-in**: a 7-day burn-in period at the start of any new arm where data are flagged but **not excluded** from the primary analysis. Sensitivity analysis pre-committed: re-run primary analysis excluding burn-in days. If sensitivity changes the H1 conclusion, both results reported.

## 6. Metrics

All metrics defined precisely so post-hoc redefinition is impossible.

### Primary

**`weekly_cost_per_cdd_$/CDD`**: sum of (`hourly_kWh × hourly_supply_price`) + (`hourly_kWh × delivery_rate`) across all hours in the calendar week, divided by sum of `cooling_degree_days_base65F` for the week from NWS reanalysis. CDD computed as `max(0, daily_mean_F - 65)`.

### Secondary

- **`weekly_peak_kw_1hr`**: maximum of the 1-hour rolling mean of `refoss.channel.power_w` summed across the HVAC circuit channels.
- **`weekly_comfort_exceedance_F_hr`**: integrated (over the week) of `max(0, indoor_temp_f - comfort_ceiling)`, where the ceiling is 78°F by default and follows day-type if the scheduler defines a different ceiling for that day-type.
- **`weekly_hvac_kwh_per_cdd`**: total HVAC-circuit kWh / weekly CDD.
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

**Primary test (H1)**: paired randomization test ([Heyvaert & Onghena 2014, *Journal of Contextual Behavioral Science* 3(1):51–64](https://doi.org/10.1016/j.jcbs.2013.10.002)) on the per-week metric difference `(weekly_cost_per_cdd_A - weekly_cost_per_cdd_B)`. Test statistic: median of paired differences within blocks (each block contains one A and one B week). Reference distribution: all possible randomization assignments of the observed weeks under the block constraint. p-value: fraction of randomizations giving a test statistic ≥ observed. Effect-size estimate: median paired difference ± bootstrap 95% CI (B = 10,000 resamples).

**Companion model (parallel report)**: per CodeX 2026-05-07 review, alongside the per-CDD ratio above, fit a weather-normalized regression of weekly HVAC kWh against weekly CDD with an arm dummy: `kWh_week ~ β₀ + β₁·CDD_week + β₂·arm + ε`. Coefficient β₂ is the arm effect with all weather variation absorbed; report point estimate + bootstrap CI. The randomization test on `weekly_cost_per_cdd` is the pre-committed primary; the regression is a sensitivity check that doesn't depend on the per-CDD denominator behaving well at low load. If the two methods disagree on H1, both are reported and the disagreement is itself the conclusion.

**Secondary tests (H2, H3)**: same randomization-test framework on each secondary metric. Multiple-comparison correction: **Bonferroni at α = 0.05 / 3 = 0.0167** for the three within-summer secondary hypotheses (H2, H3, H4 deferred to next summer). Pre-committed: report uncorrected p as well, but conclusions reference the corrected threshold.

**Cross-summer test (H4)**: descriptive only with 95% CI; not powered for hypothesis testing at N=1 cross-summer comparison. Result interpreted in context, not as a formal accept/reject.

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

Stopping criteria for the broader study: minimum one cooling season of valid data (≥ 12 weeks of paired observations after exclusions). Maximum study window: 3 cooling seasons (through 2028). After 3 seasons, declare and publish, regardless of outcome. Negative or null results are publishable contributions.

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
4. The metric definitions in §6.
5. The analysis pipeline at the commit hash referenced in OSF.
6. The decision rules in §8.
7. The stop-loss criterion in §2.

Changes after pre-registration require an amendment posted to OSF with explicit justification, and will be reported as deviations in any published methods section.

## 14. References

- Khabbazi, A., et al. (2025). *Lessons learned from MPC field demonstrations for HVAC*. [arXiv:2503.05022](https://arxiv.org/abs/2503.05022) / [Applied Energy 387 (2025) 126459](https://www.sciencedirect.com/science/article/abs/pii/S0306261925011894). Source of the methodological-gap framing.
- Heyvaert, M., & Onghena, P. (2014). *Randomization tests for single-case experiments: state of the art, state of the science, and state of the application*. Journal of Contextual Behavioral Science, 3(1), 51–64. [doi:10.1016/j.jcbs.2013.10.002](https://doi.org/10.1016/j.jcbs.2013.10.002). Statistical method for the primary test.
- ASHRAE (2023). *Guideline 14-2023: Measurement of Energy, Demand, and Water Savings*. [ANSI store](https://webstore.ansi.org/standards/ashrae/ashraeguideline142023). M&V framework.
- EVO (2022). *International Performance Measurement and Verification Protocol (IPMVP) Core Concepts*. [evo-world.org](https://evo-world.org/en/products-services-mainmenu-en/protocols/ipmvp). Whole-facility energy reconciliation framework.
- ASHRAE (2020). *Standard 55-2020: Thermal Environmental Conditions for Human Occupancy*. Comfort metric definitions.
- Bacher, P., & Madsen, H. (2011). *Identifying suitable models for the heat dynamics of buildings*. Energy and Buildings 43(7), 1511–1522. [doi:10.1016/j.enbuild.2011.02.005](https://doi.org/10.1016/j.enbuild.2011.02.005). Methodological anchor for the Step 1 controller (Arm B).
- Brick Consortium. *Brick Schema*. [brickschema.org](https://brickschema.org/). Metadata vocabulary for building telemetry.
- IEA EBC Annex 81 (2024). *Data-Driven Smart Buildings*. [annex81.iea-ebc.org](https://annex81.iea-ebc.org/). Buildings-data-platform reference.
- 45 CFR 46.102 (2018). *Definitions for purposes of this policy*. [HHS](https://www.hhs.gov/ohrp/regulations-and-policy/regulations/45-cfr-46/index.html). NHSR determination basis.
