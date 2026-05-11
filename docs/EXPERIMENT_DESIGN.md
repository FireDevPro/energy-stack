# Experiment Design — Residential HVAC Controls Field Study (N=1)

**Status**: Pre-registration draft (revised 2026-05-09). Binding once filed to OSF. No data unblinding before pre-registration is filed.
**Owner**: Chris dePaola (owner-as-investigator)
**Ethics framing**: building-as-subject measurement study with the homeowner-investigator as sole occupant. See §11.
**Companion docs**: [`HVAC_LOGIC.md`](HVAC_LOGIC.md) (scheduler logic, including the thermostat fallback that defines Arm A), [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md) (thermal model used as one component of Arm B), [`INFLUXDB_RETENTION.md`](INFLUXDB_RETENTION.md) (data persistence guarantees).

---

## 1. Background and rationale

### The field gap

The Khabbazi et al. 2025 meta-review of residential HVAC MPC field demonstrations ([arXiv 2503.05022](https://arxiv.org/abs/2503.05022), [Applied Energy 387 (2025) 126459](https://www.sciencedirect.com/science/article/abs/pii/S0306261925011894)) classified 24 residential studies. Across 104 total residential and commercial papers, "real-time pricing" appears once and "5CP" or "coincident peak" appears zero times. Two structural gaps:

1. **No clean published field comparison of "fully forecast-and-price-aware reactive controller" vs "programmable thermostat with smart recovery" on residential real-time pricing.** Most cited residential MPC field studies use bang-bang or fixed-schedule baselines. Wang et al. 2023 ([doi:10.1016/j.enbuild.2023.113026](https://doi.org/10.1016/j.enbuild.2023.113026)) is one of the few field tests with an explicit rule-based-control comparator and tariff-aware optimization, reporting 22-27% cost savings vs RBC under TOU. Pergantis et al. 2024 cooling and heating field tests report 19% energy reduction vs measurements in occupied IECC zone 5A. Drgoňa et al. 2020 and Henze 2013 both note that against a well-tuned rule-based controller the realistic delta narrows to single-digit-to-low-double-digit percent. None of these compares against a programmable thermostat with smart recovery as the baseline.

2. **No formal Single-Case Experimental Design (SCED) methodology in residential HVAC field studies.** Heyvaert & Onghena 2014 ([JCBS 3(1):51-64](https://doi.org/10.1016/j.jcbs.2013.10.002)) is the methodological reference; the building-science literature has not adopted it.

Allcott 2011 ([Resource and Energy Economics 33(4):820-842](https://doi.org/10.1016/j.reseneeco.2011.06.003)) is the only published residential RTP analysis directly relevant to ComEd Hourly Pricing (Rate BESH). It reports ~10% bill savings from passive enrollment, predating modern smart thermostats. Blonz, Palmer, Wichman & Wietelman 2023 ([AEJ:Applied 17(1):1-37](https://doi.org/10.1257/app.20210618)) is the closest methodologically rigorous comparison of automated vs manual smart-thermostat operation under time-varying prices, but it tests Ontario TOU, not RTP, and tests vendor-default automation rather than a research-grade controller.

### The product gap

No consumer thermostat product in the ComEd service territory consumes the public hourly pricing API for continuous price-following control. The structural reasons (market size below ~38K residential RTP customers, vendor revenue dependent on utility DR programs rather than customer bill savings, equipment incompatibility between contactor-cycling DR and variable-capacity HVAC) are well-documented. The implication for this study is that the comparison cannot be against a vendor product; the consumer baseline is a programmable thermostat with smart recovery operating on the same RTP exposure, which is what every ComEd Hourly Pricing customer running a Nest, Ecobee, Honeywell, or CTK04AE actually has today.

### Contribution

This study fills both gaps with a single-house, multi-summer field measurement, using formal SCED methodology, comparing a consumer-grade programmable+smart-recovery thermostat (Arm A) against a fully forecast-and-price-aware reactive controller with PJM 5CP-eligibility detection (Arm B). The contribution is methodological (SCED in residential HVAC), substantive (closing the named field-gap with measured effect sizes), and practical (open data + open code for the existing DIY community on ComEd Hourly Pricing).

The study takes a **measurement-and-reporting stance, not an advocacy stance**. The hypothesis structure does not seek to prove one controller superior; it commits to reporting effect sizes with confidence intervals and stratified outputs regardless of direction.

---

## 2. Hypotheses and pre-committed outcomes

All outcomes are pre-committed before any data unblinding. Framing is descriptive measurement with effect-size estimation rather than confirmatory hypothesis testing. Bootstrap confidence intervals are the primary inference; randomization tests are reported as a secondary check on the same data.

### Co-primary outcomes

**O1 — Weekly HVAC-circuit cost per cooling-degree-day.** Sum across all hours in the calendar week of (`hvac_circuit_hourly_kWh × hourly_supply_price`) + (`hvac_circuit_hourly_kWh × delivery_rate`), divided by sum of cooling-degree-days base 65°F for the week. The HVAC channel set (`em:2 + em:8 + em:9` on the Refoss EM16P) is fixed at the OSF commit hash. Reported as the matched-pair median difference (Arm B minus Arm A) with bootstrap 95% CI.

**O2 — Annual capacity-charge avoidance (cross-summer; three measurement layers).** Difference in the following-year ComEd Capacity Charge attributable to the experimental period, anchored to PJM's final-published 5CP hour lists (released mid-October per [PJM Manual 19 §4.3](https://www.pjm.com/-/media/documents/manuals/m19.ashx)) rather than to the live detector. Reported at three layers: **Layer 1** (primary, fully observable) — `ACustCPL` difference at the five PJM Five Peak hours, the first-branch term of [PJM OATT Attachment M-2 (ComEd) §2](https://www.pjm.com/pjmfiles/directory/etariff/MasterTariffs/23TariffSections/18111.pdf); **Layer 2** (descriptive) — full `CPLC_(Y+1)` reconstruction including the second branch's portfolio term, sourced as a stipulated constant from ComEd's published tariff Schedule; **Layer 3** (descriptive, post-Y+1) — the actual ComEd Capacity Charge line item on the Y+1 May-Sep bills. Full mechanics in §6. With the 2025-2026 PJM Base Residual Auction increasing the residential capacity rate roughly tenfold, this outcome moves from secondary to co-primary in dollar terms. Layer 1 reported as the matched-pair $ delta with 95% CI bootstrapped from the qualifying-weeks distribution; Layers 2 and 3 descriptive. No directional reject/accept threshold.

### Secondary outcomes

**O3 — Weekly peak HVAC kW (1-hour rolling mean).** Maximum of the 1-hour rolling mean of `refoss.channel.power_w` summed across the HVAC channel set, per week. Matched-pair median difference with bootstrap 95% CI.

**O4 — Whole-home cost per CDD.** Same construction as O1 but computed on `em:1 + em:7` (split-phase mains) for whole-home reconciliation against the ComEd bill. Provided as ASHRAE G14 / IPMVP comparability.

**O5 — Within-day energy and cost profile by arm.** Hourly kWh and hourly cost on the HVAC channel set, averaged across cooling-relevant weeks within each arm. Descriptive; no formal test.

**O6 — Recovery-overhead ratio.** kWh consumed on the HVAC channel set during the 18:00-23:59 window on HOT-classified days, normalized by daily mean outdoor temp delta from 75°F. Tests whether the load-shifting strategy in Arm B incurs net energy cost during recovery that erodes the peak-window savings.

### Operational safety floor (not a measured outcome)

The CTK04AE installer-menu safety supervisor in Arm B clamps any pushed setpoint to `[65°F, 86°F]` cool. The thermostat-programmed fallback schedule used in Arm A is ASHRAE 55-compliant throughout (cool setpoints 74-78°F, see [HVAC_LOGIC.md§Thermostat fallback](HVAC_LOGIC.md#thermostat-fallback-when-pi-is-offline)). Indoor temperature is monitored continuously and reported as a descriptive accompaniment to outcomes; comfort is not a hypothesis or measured outcome of the study. If the household reports unacceptable conditions during any arm, the deviation is logged and reported transparently in the final paper.

---

## 3. Arms / conditions

Both arms run on identical equipment (Amana ASXC160481BE 2-stage AC + AMVM971005CN modulating gas furnace + ECM blower, Amana CTK04AE thermostat, Control4 EA-5 bridge), same household, same ComEd RRTP supply rate, same Refoss/EAGLE/Ecowitt/ComfortNet instrumentation. The arm difference is the active control logic.

### Arm A — Consumer-grade programmable with smart recovery

**Configuration:** CTK04AE running its programmed 4-event schedule natively. Pi-based scheduler in dry-run mode (logs intended actions to InfluxDB but pushes no setpoints). CTK04 ISU 4090 (Adaptive Intelligent Recovery) set ON, matching Ecobee Smart Recovery and Nest learned-recovery behavior.

**Schedule:** the CTK04AE-programmed fallback documented at [HVAC_LOGIC.md§Thermostat fallback](HVAC_LOGIC.md#thermostat-fallback-when-pi-is-offline). Cool setpoints 74°F / 78°F / 75°F / 74°F at 5am / 1pm / 7pm / 10pm. ASHRAE 55-compliant throughout.

**What Arm A does:**
- 4-event programmable schedule
- Adaptive recovery start (thermostat learns time-to-setpoint, starts cooling early)
- Auto deadband enforcement
- Stage-2 staging on temperature differential

**What Arm A does NOT do:**
- Day-ahead or intra-day forecast adaptation
- ComEd RTP price awareness
- PJM 5CP-eligibility detection
- Humid-day setpoint adjustment
- Day-type classification

This represents what a Chicago Hourly Pricing customer running a Nest, Ecobee, Honeywell, Sensi, or stock CTK04AE thermostat actually has today. No vendor product in the market does more than this for this tariff.

### Arm B — Full forecast-and-price-aware reactive controller

**Configuration:** Pi-based scheduler active. CTK04 ISU 4090 (AIR) set OFF (the Pi's explicit setpoint pushes would conflict with thermostat-internal pre-emptive starts).

**What Arm B does:**

1. **Forecast-driven day-type classification at 21:00 the night before.** NWS forecast → MILD / NORMAL / HOT / HOT_STREAK_DAY1, with day-after lookahead. Day-type thresholds (recalibrated against 2025 ComEd RTP price-spike distribution): **HOT** at forecast max ≥85°F OR forecast apparent temp ≥90°F; **NORMAL** at 75-85°F max; **MILD** below 75°F max. Captures 54% of historical price-spike days and 71% of scarcity days (reproduced by [`tools/comed_2025_analysis/verify_appendix_a.py`](../tools/comed_2025_analysis/verify_appendix_a.py)); the remaining 46% of spike days are grid-event-driven (not temperature-correlated) and are addressed by the price-reactivity layer below.
2. **Intra-day forecast revisit at 06:00 and 11:00.** Reclassifies if the forecast has shifted; future schedule actions execute against the revised classification.
3. **Day-type-specific schedules** with pre-cool / coast / recover / sleep periods. See [HVAC_LOGIC.md§Schedules](HVAC_LOGIC.md#schedules).
4. **Humid override.** When forecast dewpoint exceeds 65°F, alternate cool setpoint substitutes during COAST periods to maintain dehumidification duty.
5. **Real-time RTP price-spike reactivity (tiered, with hysteresis).** Continuous overlay on the active scheduled setpoint, evaluated each scheduler tick:

    | Tier | Trigger price | Release price | Action |
    |---|---|---|---|
    | Normal | < 10¢/kWh | n/a | No price-driven override |
    | Elevated | ≥ 10¢/kWh | < 8¢/kWh | +3°F to active cool setpoint |
    | Scarcity | ≥ 20¢/kWh | < 18¢/kWh | Cool setpoint = 85°F (effective shutoff) |

    Once a tier triggers, hold for 30 minutes minimum before considering downgrade. **Threshold derivation vs application:** the 10¢ and 20¢ values are anchored on P95 (9.53¢) and P99 (20.47¢) of the 2025 ComEd **hourly**-price distribution as economically interpretable signal levels for "elevated" and "scarcity" regimes. They are **applied to the latest 5-minute ComEd RTP print**, not the hourly average, so the controller reacts inside the hour rather than waiting for the hourly average to settle. Applying hourly-derived thresholds to 5-minute prints means the overlay will trigger on a wider set of 5-minute intervals than the P95 fraction implies for hourly intervals — by design, the goal is to catch hourly spike regimes before they fully resolve. The 30-minute minimum hold and 2¢ hysteresis prevent oscillation from single-tick spikes. Layer is additive over the schedule's setpoint with "warmer wins" semantics; never makes the house cooler than the schedule intended. New logic implemented before OSF filing.

6. **PJM 5CP-eligibility detection (dual-scope: ComEd zone + PJM RTO).** Live PJM load + season-to-date 5th-highest hourly peak comparison + load derivative monitoring to identify likely 5CP-eligible hours, triggering aggressive shutoff (cool setpoint = 85°F) during those windows. Residential capacity charges depend on the household's metered demand during **two separate sets** of coincident peak hours (per [`HVAC_LOGIC.md`](HVAC_LOGIC.md) "Capacity peak context"): the 5 highest RTO-wide hours (PJM 5CPs) and the 5 highest ComEd-zone hours (ComEd 5CPs). ComEd's zone peaks can land earlier than RTO peaks because metro-Chicago load shape differs from the broader RTO; under PJM OATT Attachment M-2 the next-year `CPLC` depends on the household's average load across BOTH peak sets (PJM-peak average plus a conditional ComEd-peak term, see O2), so the detector runs two parallel state machines and OR's their triggers to enable shedding on any plausibly-eligible hour of either set. The locked decision rule applies per scope: `is_5cp_risk(scope)` triggers when `current_load_mw(scope) / season_to_date_5th_highest_mw(scope) > 0.95` AND `load_derivative(scope) > 0` AND `hour_of_day is in 13:00-20:00 CT` (broadened from the historical 14:00-18:00 to capture 2025-style late-peak behavior, where the actual RTO peak hour was 18:00 CT) AND `forecast_peak_today_mw > season_to_date_5th_highest_mw(scope)`. Hold for end-of-hour + 30 min after trigger per scope; release per scope only when load drops below 90% of that scope's 5th-highest AND derivative goes negative. **Effective shutoff is `is_5cp_risk_comed OR is_5cp_risk_rto`**, so the broader of the two scopes wins. State machines are independent: a ComEd-zone release does not exit an RTO-scope hold or vice versa. Approach modeled on the joe248 AppDaemon 5CP-prediction implementation ([HA forum thread](https://community.home-assistant.io/t/hacking-your-comed-electricity-bill/111494)). **Four-feed data lineage** (per PJM DM2 OpenAPI spec): `current_load_mw(comed_zone)` and `load_derivative(comed_zone)` come from `inst_load?area=COMED`; `current_load_mw(rto)` and `load_derivative(rto)` from `inst_load?area=PJM RTO` — both ~5-minute cadence, described in the spec as "approximate, NOT official PJM Loads" but "frequently updated throughout the operating day" (the right tradeoff for a real-time directional signal). `season_to_date_5th_highest_mw(comed_zone)` comes from `hrl_load_metered?zone=CE` and `season_to_date_5th_highest_mw(rto)` from `hrl_load_metered?zone=RTO` — both daily publish with up to 90-day correction window per spec, official metered values that determine the actual 5CP rank. Pre-season cold-start fallbacks per scope (Appendix A) are calibrated to last year's actual 5th-highest at the matching scale. New logic implemented before OSF filing.

7. **Pre-cool deepening (forecast-driven, evaluated at 21:00 the night before).** If `forecast.tomorrow.peak_mw > season_to_date_5th_highest_mw × 1.05` AND `forecast.tomorrow.high_f ≥ 90`, override the HOT_PRE_COOL action to start at 03:00 instead of 04:00 with cool setpoint of 66°F instead of 68°F (matching the existing HOT_STREAK_DAY1 schedule). Triggered by forecast 5CP risk, not just multi-day heat.

8. **Layer priority resolution.** Multiple layers can fire simultaneously. Effective cool setpoint computed as:
   ```
   effective = max(schedule + humid_override,
                   schedule + price_overlay,
                   5cp_shutoff_setpoint)
   effective = clamp(effective, 65, 86)   # safety supervisor
   ```
   Safety supervisor always wins. 5CP shutoff wins over price-spike (5CP capacity-charge dollars > hourly arbitrage). Price-spike wins over schedule (real-time signal beats forecast). Humid override applies to schedule baseline only. "Warmer wins" prevents accidental over-cooling.

9. **Optional: thermal-model-informed pre-cool depth, coast lead time, and stage-2 advisory.** If the operational thermal model fit per [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md) ratifies (skill score ≥ 0.5 on holdout), these three setpoint-adjustment integrations are active. If the model fails ratification, Arm B operates with hand-tuned constants for these three parameters and the model dependency is reported as a limitation.

The thermal model is one component of Arm B, not the headline contribution. The headline contribution is "fully aware reactive controller vs consumer baseline" with everything bundled.

**The fixed 14:00-18:00 CT shutoff window from the original scheduler design is dropped in Arm B.** Real-time price-spike reactivity (item 5) and 5CP detection (item 6) drive shutoff timing dynamically. The fixed window assumed the historical PJM peak clustering (14-17 CT) which 2025 data shows shifted later (peak hour was 18:00 CT). The dynamic layers track actual conditions instead of historical assumptions. Optional fallback floor of 13:00-20:00 CT shutoff applies only if the price feed becomes unavailable.

### Boundary conditions of the experiment

These are inputs, fixed across both arms, not variables under test:

- Single-family detached residence, 3500 sqft, 2 above-ground floors plus basement
- No HVAC zoning, single thermostat
- Building envelope: well-insulated, black siding, black roof, southeast-facing AC condenser
- IECC climate zone 5A (Cook County, IL)
- Single occupant
- ComEd Rate BESH (Hourly Pricing) supply, ComEd delivery
- Variable-capacity HVAC (2-stage AC, modulating furnace, ECM blower)

Generalization claims in the final paper are bounded to similar households. Equipment-class scoping is explicit: single-stage HVAC cannot execute Arm B's deep pre-cool without short-cycling, so the result does not transfer to that equipment class.

---

## 4. Study design

**Type:** Year-round operation with formal analysis restricted to cooling-relevant weeks, single-case experimental design (SCED) with alternating-treatments week-level randomization within calendar-month blocks.

**Duration:** continuous from study start (target June 1, 2026), with formal cooling-relevant analysis on each cooling season. Multi-summer replication planned (paper 1 reports summer 2026 cooling, paper 2 reports combined 2026+2027 plus first H4-equivalent capacity-charge readout).

**Cooling-relevance criterion:** a calendar week is included in the formal O1, O3, O5, O6 analysis if its realized weekly cooling-degree-day count (base 65°F, derived from co-located weather observations) is ≥ 5. Weeks below this threshold are reported descriptively only and excluded from formal effect-size estimation. The threshold is pre-committed before OSF and not adjusted post-hoc.

This handles climate variability cleanly: spring and fall weeks with real cooling load qualify; deep-winter and dead-shoulder weeks where AC barely runs do not. Categorical "summer" boundaries are not used.

### Data quality rules and missing-data handling

A week qualifies for the formal O1 / O3 / O5 / O6 analysis if it passes the cooling-relevance criterion above AND every gate below. Weeks failing any gate are reported descriptively only and excluded from formal effect-size estimation. All gates pre-committed before OSF.

**1. Refoss EM16P HVAC-channel coverage (`em:2`, `em:8`, `em:9`).** Four-tier handling, applied per-interval in order:

| Tier | Gap length | Handling |
|---|---|---|
| 1 | < 5 min (single missed 1-min tick) | Linear interpolation across the gap. |
| 2 | 5-30 min | Same-day-type same-hour median from the prior 14 days, scaled by the within-hour ratio of mains (`em:1 + em:7`) to its own same-window median. |
| 3 | 30-180 min | ComfortNet CT-485 derived: `cool_actual_pct × cool_nameplate_kW + heat_actual_pct × furnace_nameplate_kW + blower_cfm_to_kW(cfm)`. |
| 4 | > 180 min OR ComfortNet also offline | Flag the day; no imputation. |

**Imputation cap:** if the imputed energy (Tiers 1-3 combined) ≥10% of total weekly HVAC kWh, the week is dropped from formal analysis. Per-tier imputed kWh is logged for reporting.

**2. CT-485 / ComfortNet HVAC-state.** Used in Refoss Tier 3 imputation and in O6 recovery-overhead analysis. Missing intervals are not imputed. O6 reports any day with >30 minutes of ComfortNet downtime as ineligible for the recovery-ratio computation; the week's O6 average uses the qualifying-day subset.

**3. ComEd RTP price feed.** Hourly average comes from `comed.prices period_type=hourly_avg`, computed within hour from 5-minute prints. An hour is considered observed if ≥6 of 12 5-minute prints are present. Hours below that are filled from the day-ahead PJM LMP at COMED, adjusted by the historical month-matched median (RTP − day-ahead LMP) spread computed once at OSF lock from public PJM data over summers **2024-2025** (Jun-Sep, n=5,840 hour-pairs; computation and locked values in [`tools/comed_price_imputation/`](../tools/comed_price_imputation/) — `compute_spread.py` against `data/rtp_<year>.txt` from the ComEd public Hourly Pricing endpoint and `data/lmp_<year>.csv` from PJM DM2 `da_hrl_lmps`). 2023 PJM DA-LMP is in PJM's archive tier (731-day cutoff) and is not currently included; the locked spread values shipped in this commit are 2024-2025 only. Weeks with >5% imputed hours are flagged but not excluded; weeks with >20% imputed hours are excluded.

**4. NWS forecast.** Used only by Arm B's day-type classifier. A missing 21:00 issuance is filled from the prior-day same-issuance forecast; if both are missing, the day is forced to NORMAL classification and the substitution flagged. Forecast availability does not affect week eligibility.

**5. Ecowitt outdoor temperature (CDD basis).** Missing Ecowitt readings filled from NWS gridpoint at the same hour. If both are missing for >2 hours in a day, that day's CDD contribution uses the `(Tmax + Tmin)/2 − 65` daily estimator on whatever station data is present; the substitution is flagged. If both sources are missing for >6 hours in a day, the day is dropped from the week's CDD numerator and denominator.

**6. PJM DM2 `inst_load` feed (5CP detector input).** Used live by the detector and post-hoc only to characterize detector behavior. Outages do not affect O2: O2 is anchored to PJM's final-published 5CP hour list (truth source per §6 O2), not to the live detector's flagging.

**7. Scheduler service outages.** A week is excluded from formal analysis if ANY of the following holds:
- Total scheduler downtime exceeds 1% of week-hours (≈ 100 min/week), OR
- Any single continuous outage exceeds 60 minutes, OR
- Any outage overlaps a control-relevant window: pre-cool, recover, or an active 5CP / scarcity hold.

Outage detection (post-hoc, from Influx): the scheduler writes one `hvac.5cp_state` row per ~2.5 min and at least one `hvac.actions` row per minute when alive; an outage is flagged when both have no writes for ≥ 5 minutes simultaneously. (The runtime container-health gate is filesystem-based — Docker `HEALTHCHECK` on `/tmp/last_tick_ok` per the P2.3 hardening in `hvac-scheduler/app.py` — and triggers operator alerts; the post-hoc Influx gap is the analysis-time signal.)

**8. Pi-lab / data-collection power outages.** Treated as scheduler-service outages (rule 7) AND as Refoss outages (rule 1) — the Pi hosts both the scheduler and the Refoss/ComfortNet loggers. If the resulting affected-day exclusions leave a week with <5 qualifying days, the week is excluded.

**9. Manual setpoint overrides.** Two categories with different handling:
- **Operational overrides** (occupant briefly bumps the setpoint for comfort, e.g., +1°F during work-from-home stretches): kept in formal analysis with an `override=operational` flag. Reported descriptively as count and total-degree-hours per week.
- **Vacation overrides** (occupant departs; thermostat held at 82°F absolute with no active scheduling): exclude the affected calendar-day(s). If the resulting week has <5 qualifying days, exclude the week.

Source: `hvac.actions` rows with `source != scheduler` (manual API calls or thermostat-local overrides) annotated by the occupant via `tools/log_override.py` (to be added under PR-C) at the time of the override. Annotation requirement holds for both categories; absence of annotation at week-close defaults the override to "operational" with a flag.

**10. Arm-transition verification.** After each Monday 00:00 CT arm switch, the new arm's scheduler must demonstrate it is in effect by the earlier of:
- the first control-relevant window of the new arm executing successfully (HOT_PRE_COOL on a HOT day or NORMAL_PRE_COOL on a NORMAL day, with the expected `arm` tag and non-dry-run execution where applicable), OR
- 6 hours past the switch time.

Detection: an `hvac.actions` row with `arm == intended_arm` matching the expected mode (dry-run for Arm A, active for Arm B) within the verification deadline. Failure to verify by the deadline marks the calendar-week as an arm-mis-assignment week and excludes it from formal analysis; the 48h washout exclusion still applies regardless.

Verification failures and the per-rule exclusion counts are reported in the OSF-deposited data-quality table.

**Subject:** one single-occupant residence as described in §3 boundary conditions. The investigator-occupant.

**Blinding:** investigator is necessarily unblinded (controls the system). Mitigated by pre-registered analysis plan with frozen code, frozen seed, and pre-committed metric definitions.

---

## 5. Randomization and assignment

**Unit of randomization:** calendar week, with arm-runs of 2 weeks. Each 4-week block contains 2 consecutive Arm A weeks and 2 consecutive Arm B weeks. Order within block (AABB or BBAA) is randomized.

**Why 2-week arms:** with a 24-72h post-switch washout (§Washout below), 1-week arms lose 2/7 of each arm-period to washout. 2-week arms lose 2/14 (14%), nearly halving the data-loss overhead and giving 12 analyzable days per arm-period instead of 5. Multi-day phenomena (heat streaks, recovery cycles) live inside the arm-period rather than splitting across the boundary.

**Why randomized order within block (rather than fixed AABB pattern):** randomization makes the SCED randomization-test reference distribution well-defined and pre-empts post-hoc selection concerns. The randomization is acknowledged as ceremonial rather than confound-controlling — the matched-pair weather analysis (§7) does the actual confound-control work — but it costs nothing operationally and preserves the formal SCED inference framework as a secondary check.

**Seed:** `20260601`. Deterministic from this seed.

**Method:** assignment generated by [`deploy/energy-stack/scripts/randomize_arms.py`](../deploy/energy-stack/scripts/randomize_arms.py) and committed to the repo and OSF before the first arm-week. Output: [`docs/experiment-assignments-summer-2026.csv`](experiment-assignments-summer-2026.csv) covering year-round 2026 weeks, with each 4-week block labeled.

**Switch mechanism:** at each Monday 00:00 CT arm transition:
- **Entering Arm A:** Pi scheduler set to dry-run mode (logs only). CTK04 ISU 4090 set ON. CTK04AE-programmed schedule resumes.
- **Entering Arm B:** Pi scheduler set to active. CTK04 ISU 4090 set OFF. Pi pushes setpoints per the live-aware logic.

The transition is logged to `hvac.actions` with explicit `arm` tag.

**Post-switch washout:** the first 48 hours after each Monday 00:00 CT arm switch are flagged as washout and excluded from the formal analysis. Applied identically to both arms. 48h is a conservative residential thermal envelope time-constant (typical residential τ is 10-30h; 48h covers transition-rebalance comfortably). If a ratified pre-study τ is available before OSF, washout becomes `W = ceil(2τ/12h) × 12h` clamped to [24h, 72h]; otherwise W=48h.

**Sensitivity analysis** (pre-committed): re-run primary analysis with washout hours included. If sign or significance of O1 differs, both results are reported and the disagreement is itself the conclusion.

---

## 6. Metrics and measurement

### Primary metric: O1 weekly HVAC-circuit $/CDD

For each cooling-relevant week:

- **Cost numerator:** sum across all hours of (`hvac_circuit_hourly_kWh × ComEd_hourly_supply_price + hvac_circuit_hourly_kWh × delivery_rate`). The HVAC channel set is `em:2 + em:8 + em:9` on the Refoss EM16P, fixed at the OSF commit hash. `em:2` and `em:8` are the two AC compressor legs; `em:9` is the furnace blower (which moves cool air during cooling cycles). Pricing granularity: hourly average from `comed.prices` `period_type=hourly_avg`, matching the ComEd bill calculation.
- **CDD denominator:** sum of `cooling_degree_days_base_65F` for the week, computed from co-located weather observations (Ecowitt when online, NWS gridpoint as fallback).

### O2 capacity-charge avoidance — three-layer measurement

**Truth source for "which hours count":** PJM's final-published 5CP hour lists for the RTO (5 PJM Five Peak hours) and the ComEd zone (5 ComEd Five Peak hours), released mid-October after the season closes per [PJM Manual 19 §4.3](https://www.pjm.com/-/media/documents/manuals/m19.ashx). The Arm B live 5CP detector's hour-by-hour accuracy is reported descriptively as a process metric (see Detector accuracy report below); it does NOT define O2. O2 is computed at the PJM-published peak hours regardless of which hours the live detector flagged.

The outcome is reported at three layers, each strictly more inclusive of stipulation than the prior:

**Layer 1 — Observed `ACustCPL` difference (primary, fully observable).**
Arm B minus Arm A difference in `ACustCPL_Y`, the household's average metered demand across the five PJM Five Peak hours of summer Y. Fully observable from the household's revenue meter (EAGLE feed for instantaneous; cross-checked against ComEd-bill kWh for accumulation). Maps to the first branch of PJM OATT Attachment M-2 (ComEd) §2:
- `CPLC_(Y+1) = ACustCPL_Y` when `ACustCPL_Y ≥ ACustPL_Y`.

This is the dominant case for most residential profiles. Reported as the primary O2 number with bootstrap 95% CI computed across the qualifying-weeks distribution under each arm. **Diluted-per-kW caveat:** a single-hour reduction at one of the five peaks shifts the average by roughly `kW / 5`, not full kW; the reported delta reflects the five-hour average.

**Layer 2 — Stipulated `CPLC_(Y+1)` reconstruction (descriptive, one stipulated input).**
Full `CPLC_(Y+1)` reconstruction using both Att. M-2 branches:
- Branch 1 as in Layer 1.
- Branch 2: `CPLC_(Y+1) = ACustCPL_Y + (ComEdNPL_Y − AComEdCPL_Y) × (ACustPL_Y − ACustCPL_Y) / Σ_5Pc(ACustPL − ACustCPL)` where `ACustPL_Y` is the household's average demand across the five ComEd Five Peak hours, `ComEdNPL_Y` is ComEd's weather-normalized peak load, `AComEdCPL_Y` is ComEd's average coincident peak at the PJM five peaks, and `Σ_5Pc(ACustPL − ACustCPL)` is the portfolio sum across all customers in branch 2.

The portfolio sum is unobservable from a single household. ComEd computes it internally each year from customer-register data across the positive-gap customer population defined by Att. M-2 §2, and does not publish current-year values. The Summer 2021 value for the Weather Sensitive Customer class (the relevant class for Rate BESH residential AC-driven load) was disclosed as **2,033.653 MW** in the FERC ER22-1520-001 deficiency response, Exhibits 1(b), 2(b)(i), and 2(b)(ii); see [`tools/o2_capacity_reconstruction/tariff_snapshot.md`](../tools/o2_capacity_reconstruction/tariff_snapshot.md) §4 for the source PDF, exhibit references, and verification math. The aggregation inequality `Σ max(ACustPL_i − ACustCPL_i, 0) ≠ max(Σ ACustPL_i − Σ ACustCPL_i, 0)` means no PJM Data Miner zonal feed reconstructs this denominator from public aggregates.

Layer 2 is descriptive only and is reported across three pre-registered named denominators rather than a single point estimate with a confidence band:

| Scenario | portfolio_sum_mw | Source |
|---|---|---|
| `low` | 1,500 MW | prior planning case |
| `anchor_2021` | 2,033.653 MW | FERC ER22-1520-001 disclosed Summer 2021 Weather Sensitive denominator |
| `high` | 3,000 MW | wide upper sensitivity near historical system-gap scale |

These are scenario analyses, not confidence intervals. The reported CPLC reconstruction is presented as a side-table with one row per scenario. If a future ICC e-Docket or ComEd workpaper publishes the current-year denominator, the locked scenarios will be replaced with the disclosed value and a tighter sensitivity under an OSF amendment.

**Layer 3 — Bill reconciliation (descriptive, post-Y+1).**
The actual ComEd Capacity Charge line item on the Y+1 bills (May-Sep months, the period over which `CPLC_(Y+1)` is applied per tariff) is recorded month-by-month and summed. The ratio (Layer 2 / Layer 3) is reported as a tariff-reconstruction fidelity number. Layer 3 has no within-house counterfactual — there is only one realized bill trajectory for the realized arm assignment in summer Y — so Layer 3 is descriptive only and does not enter any effect-size statement.

**Counterfactual scope.** Layers 1 and 2 are computed twice: once on the Arm A weeks' realized demand, once on the Arm B weeks' realized demand. The pair difference is what the cross-summer SCED randomization permits as inference. Layer 3 is anchored only to the realized assignment.

**Bootstrap CI** for Layer 1 (the primary statement) computed across whatever number of qualifying weeks the cooling-relevance criterion and §4 data-quality gates produce under each arm.

**Detector accuracy report (process metric, not an outcome).** Separately, the Arm B live 5CP detector's hour-by-hour decisions during summer Y are cross-referenced against PJM's October-published 5CP hour list. Reported: true-positive rate (detector held shutoff during a published 5CP hour), false-positive rate (detector held shutoff during a non-5CP hour), false-negative rate (detector did not hold during a published 5CP hour). This characterizes the live detector as an engineering subsystem; it is decoupled from O2's outcome statement.

### Secondary metrics

O3 (weekly peak HVAC kW) and O4 (whole-home $/CDD) constructed analogously to O1.

O5 (within-day profile) reported as hourly kWh and hourly cost averaged across each arm's cooling-relevant weeks, with continuous weather-condition descriptors per matched pair.

O6 (recovery-overhead ratio) integrated 18:00-23:59 on HOT-classified days; a positive ratio indicates the load-shifting strategy in Arm B incurs net evening kWh.

### Descriptive accompaniments

For each matched pair, the following are reported alongside the cost outcomes for reader context: max temperature, max dewpoint, mean temperature, mean dewpoint, total CDD, total solar irradiance, mean wind speed, mean outdoor enthalpy, indoor temperature distribution (median, 90th, 95th percentile during 13:00-20:00 CT 5CP-eligibility window (per §3 Appendix A) for both arms).

---

## 7. Statistical analysis plan

### Primary inference: matched-pair effect sizes with bootstrap 95% CI

Each Arm A week and each Arm B week (after washout exclusion and cooling-relevance filtering) is summarized to a weekly weather summary vector and an outcome value (O1 $/CDD primarily; same procedure applies to other metrics).

**Weather summary vector (6 components, ASHRAE Guideline 14-2023 multivariable regression aligned):**

1. Total weekly CDD (base 65°F)
2. Mean weekly outdoor air enthalpy (BTU/lb dry air, computed from temperature + dewpoint + atmospheric pressure via standard psychrometric formulas)
3. Total weekly solar irradiance (Wh/m² weekly sum)
4. Mean weekly wind speed (mph)
5. Max weekly outdoor temperature (°F)
6. Max weekly outdoor dewpoint (°F)

Items 1-4 are the ASHRAE G14 canonical set. Items 5-6 are extensions to capture peak-condition distribution shape that the cumulative metrics integrate over.

**Distance metric:** Mahalanobis distance (generalized point-to-point form) `d²(x,y) = (x-y)ᵀ Σ⁻¹ (x-y)` where Σ is the covariance matrix estimated from 2020-2025 **ERA5 reanalysis at KORD coordinates** (41.9786°N, 87.9047°W), cooling-relevant weeks (CDD ≥ 5). ERA5 is chosen over multi-source NOAA stitching because it provides all six required weather-summary components (CDD basis, dewpoint, wind, solar, enthalpy inputs) consistently at a single grid point; NOAA GHCND/LCD/NSRDB would require three separate ingest paths with different timing skew and would produce a functionally equivalent covariance matrix for the matched-pair distance application. Computation: [`tools/analysis/baseline_distribution.py`](../tools/analysis/baseline_distribution.py) → [`tools/analysis/data/baseline_cov.npz`](../tools/analysis/data/baseline_cov.npz), locked at OSF filing.

**Matching algorithm:** Hungarian optimal pairing without replacement, minimizing total Mahalanobis distance across all matched pairs.

**Match quality threshold:** pairs with distance > 2.5 Mahalanobis units flagged as poor-quality, reported separately, excluded from primary effect-size aggregation, included in sensitivity analysis. Threshold pre-committed before OSF.

**Outlier flagging:** weekly summary vectors with Mahalanobis distance > 3.5 from the baseline distribution mean (point-to-distribution form) flagged as anomalous and reported separately.

**Effect size:** matched-pair median difference in O1 (Arm B minus Arm A). Reported with stationary bootstrap 95% CI computed over 10,000 resamples using the percentile method.

### Secondary inference: SCED randomization test

Same matched pairs, same outcome. Under the null hypothesis of no controller difference, the sign of each pair's difference is exchangeable. Reference distribution: all 2^N sign-flips for N matched pairs. Reported as p-value alongside the bootstrap CI for completeness.

### Forecast-correlated vs grid-event decomposition (committed analysis)

2025 ComEd RTP data analysis (May-Sep 2025, 122 summer days, 54 spike days, 17 scarcity days) shows that 54% of price-spike days (≥10¢/kWh peak) and 71% of scarcity days (≥20¢/kWh peak) are temperature-correlated (max temp ≥85°F OR apparent temp ≥90°F), and 46% of spike days and 29% of scarcity days are grid-event-driven (occur on weather-mild days; September shoulder-season events span max temp 65-85°F, with one Sep 4 scarcity event at max temp 65°F). This split has structural implications: forecast-based pre-cool addresses only the temperature-correlated subset; real-time price reactivity is the only defense against the grid-event subset. (Numbers reproduced by [`tools/comed_2025_analysis/verify_appendix_a.py`](../tools/comed_2025_analysis/verify_appendix_a.py); full per-day weather × price table in [`tools/comed_2025_analysis/expected_output.md`](../tools/comed_2025_analysis/expected_output.md).)

**Committed decomposition output:**

For the experimental period, classify each cooling-relevant day into:

- **Forecast-correlated price-spike day:** any hour with hourly average ≥10¢/kWh AND that day's max forecast temp ≥85°F (or apparent ≥90°F) at 21:00-prior classification time
- **Grid-event price-spike day:** any hour with hourly average ≥10¢/kWh AND that day's max forecast temp <85°F (and apparent <90°F)
- **No-spike day:** no hour above 10¢/kWh

Report Arm B vs Arm A cost differences separately for each category. Specifically:

- **Headline split:** matched-pair median Arm B - Arm A cost difference, decomposed by day classification.
- **Magnitude attribution:** what fraction of total observed savings (or additional costs) came from forecast-correlated days vs grid-event days.
- **Layer attribution (descriptive):** for grid-event days specifically, log Arm B's response patterns — which layer triggered (price-spike reactivity vs 5CP detection vs neither), at what time, and what indoor temperature resulted.

This decomposition directly addresses an unmeasured question in the residential MPC literature: how much of the savings from a "fully aware" controller comes from forecast-driven control (which all published residential MPC field studies use) vs real-time price reactivity (which essentially none cleanly isolates). The 54/46 forecast-correlated vs grid-event split observed in 2025 ComEd data suggests this is a non-trivial structural feature of the load-shifting opportunity, not a refinement.

### Sensitivity analyses (all pre-committed)

1. **Euclidean on z-scored vector** as alternative distance metric.
2. **Washout hours included** in the cost/CDD computation.
3. **HVAC channel set sensitivity:** O1 recomputed with `em:2 + em:8` only (excluding furnace blower) to verify the channel-set choice doesn't drive the result.
4. **5-minute pricing instead of hourly average** for O1 supply-price calculation.
5. **Day-of-week stratification:** descriptive only. 2025 ComEd data showed Saturday with zero scarcity hours and Mon-Tue with 25 combined; 2026 data may or may not replicate this. Reported alongside primary outcomes; not used as a controller input.
6. **Price-tier threshold robustness:** re-run the forecast-correlated vs grid-event decomposition with three threshold pairs: `{8¢/15¢, 10¢/20¢ (committed), 12¢/25¢}`. Tests whether the conclusions are robust to threshold choice. Driven by 2025-2026 YoY tail growth (P99 went 13.87¢ → 22.44¢ in shoulder seasons): if 2026 conditions continue to spike, a future-season pre-reg amendment may tighten the operational thresholds, and this sensitivity analysis pre-establishes the analytical robustness of the locked values.
7. **DTOD delivery-rate basis (base vs billed):** primary O1 uses the **base Distribution Facilities Charge** values from the ComEd CUB DTOD fact sheet ([locked in `deploy/energy-stack/hvac-scheduler/precool.py`](../deploy/energy-stack/hvac-scheduler/precool.py); Single-Family Non-Electric Heat: Morning 4.009¢, Mid-Day Peak 10.712¢, Evening 3.747¢, Overnight 2.984¢ per kWh). The **billed** delivery rate is ~10% higher because of layered adjustment factors (IDUF, EDAF, DSPR, RBAF, TPAF, DGRA) that appear in the residential bill calculation — Morning 4.428¢, Mid-Day Peak 11.727¢, Evening 4.142¢, Overnight 3.311¢ per kWh per the [ComEd Retail Delivery Service Guide](../tools/o2_capacity_reconstruction/data/) effective April 2026. Sensitivity re-runs O1 with the billed rates substituted for base. Both arms see the same delivery-rate structure, so the matched-pair Δ_O1 only changes in absolute level (~10-15%), not in sign; the sign-and-significance conclusion is robust.

### Inference framing

The paper reports effect sizes with 95% CIs. It does not run a confirmatory hypothesis test with an accept/reject threshold. CIs that span zero are reported as inconclusive in magnitude rather than treated as failed hypotheses. CIs that exclude zero are reported as evidence of an effect of the estimated magnitude. Any direction is publishable.

---

## 8. Reporting structure and decision rules

### Reporting

For each cooling season:

1. **Headline:** matched-pair median O1 difference with bootstrap 95% CI, plus O2 Layer 1 (`ACustCPL` difference) $ delta with bootstrap 95% CI. O2 Layers 2 and 3 reported as descriptive side-tables.
2. **Forecast-correlated vs grid-event decomposition** (per §7): Arm B - Arm A cost difference reported separately for forecast-correlated price-spike days, grid-event price-spike days, and no-spike days. Layer attribution (which Arm B layer triggered) reported for grid-event days specifically.
3. **Continuous-axis scatter plots:** matched-pair O1 difference on the y-axis vs each weather summary vector component on the x-axis. Lets readers see whether the effect varies systematically with weather severity.
4. **Per-pair table:** every matched pair shown with its weather summary vector and outcome difference.
5. **Within-day hourly profile** by arm, on cooling-relevant weeks only, descriptive only.
6. **Indoor temperature distribution** (median, 90th, 95th percentile) by arm, descriptive only.
7. **Day-of-week distribution** of price-spike hours and scarcity hours (descriptive only).
8. **Boundary-conditions block** (the §3 list) prominently displayed so readers can assess transferability to their own situation.

Categorical day-type stratification (HOT/NORMAL/MILD weeks) is **not** used as a reporting cut. The continuous weather variables are already in the weather summary vector and the matched pairs; categorical bins would force continuous variables into bins that lose signal.

### Decision rules

The study does not commit to confirm/disconfirm decision rules. Outcomes are reported as effect sizes with CIs in any direction. Continuation criteria for multi-season analysis:

- **≥ 8 valid matched pairs from a cooling season:** report as standalone summer paper.
- **< 8 valid matched pairs:** report as exploratory, defer formal claim until combined with subsequent season.
- **Any season:** report regardless. Negative or null results are publishable contributions.

**Stop-loss (operational safety, not a measured outcome):** If the household reports unacceptable indoor conditions during any arm, the deviation is logged, the arm is paused if the household requires, and the deviation is reported transparently in the methods section. This is not a hypothesis-failure trigger; it's a real-world operational guardrail.

---

## 9. Anonymization plan

- **Geographic identifiers:** redacted to IECC climate zone 5A and ComEd service territory. ZIP-3 acceptable as secondary tag if requested.
- **Account/utility identifiers:** ComEd account number, EAGLE cloud-id, EAGLE install code, Control4 IP and credentials redacted. The `secrets/` SOPS-encrypted layer never leaves the local repo.
- **Equipment make/model retained** (Amana AMVM971005CN, Amana ASXC160481BE, Amana CTK04AE, Rainforest EAGLE-3, Refoss EM16P). Useful to other researchers, doesn't identify the household.
- **Address, name, household composition:** redacted. Investigator name and ORCID retained as standard scholarly authorship.

---

## 10. Open-data plan

- **Telemetry:** full sub-minute multi-year dataset published as Apache Parquet (one file per measurement per year) on Zenodo with citable DOI. License: CC BY 4.0.
- **Metadata:** [Brick Schema](https://brickschema.org/) JSON-LD turtle file describing every published point, its unit, sampling cadence, and provenance.
- **Code:** repository tagged at the commit hash referenced in the OSF pre-registration. Zenodo issues a citable code DOI from the tagged GitHub release.
- **Bills:** parsed bill data (cost line items, peak kW, kWh) included; original PDFs withheld due to embedded account info.
- **Pre-registration:** OSF project URL referenced in the published methods section. OSF page is public from filing.
- **Embargo:** none. Data and code released alongside paper submission.
- **DOI structure:** separate DOIs for code at submission, dataset at submission, and revised dataset if reviewers request additions. All cross-referenced.

---

## 11. Ethical conduct and ethics framing

This study is conducted as **building-as-subject measurement** by an unaffiliated owner-investigator who is also the sole occupant of the residence under study.

### Building-as-subject framing

The subject of measurement is the HVAC system and the building envelope. Measured outcomes are kWh, $, peak kW, and PJM 5CP coincident-peak demand. The setpoint envelope (cool setpoint range across both arms) is set by homeowner preference and is identical across arms by construction; it is an **input** to the experiment, not an outcome being optimized or measured. The experiment compares the cost of reaching the same homeowner-set envelope under two different control strategies.

The thermostat-programmed fallback schedule running in Arm A operates entirely within the ASHRAE 55-2020 summer comfort envelope (cool setpoints 74-78°F). Arm B's setpoints during pre-cool and 5CP-shutoff windows are deliberately outside ASHRAE 55, as a homeowner-set cost-optimization choice that predates this study. The smart-system safety supervisor enforces a hard `[65°F, 86°F]` cool setpoint clamp on every push, so no controller bug can drive the house outside engineering-safe operating bounds.

### No institutional affiliation

The investigator holds no Federalwide Assurance and no institutional research affiliation. 45 CFR 46 applies to research conducted by FWA-bound institutions; independent owner self-experimentation in one's own residence does not fall within its institutional enforcement scope.

### COPE proportionality

The Committee on Publication Ethics has acknowledged that requiring formal IRB review for independent self-experimenters with no third-party participants imposes a publication barrier disproportionate to the actual ethical risk. The study includes a Statement of Ethics in the eventual manuscript covering: sole participant, self-consent, no recruited or affected third parties, intervention limited to standard homeowner thermostat operation, and no risk beyond daily life.

### Companion-animal welfare

The household includes companion dogs. Indoor temperature is held within the comfort-ceiling envelope governing human comfort, which is consistent with [AVMA companion-animal welfare guidance](https://www.avma.org/resources-tools/animal-health-and-welfare/animal-welfare-changing-environment) (sustained indoor temperature should not exceed 80°F). Realized indoor temperature distributions during 13:00-20:00 CT 5CP-eligibility windows (per §3 Appendix A) are reported descriptively (median, 90th, 95th percentile) so any envelope excursions are auditable.

### Conduct commitments

- The investigator has unilateral authority to terminate any arm at any time for any reason (comfort, equipment safety, household event, ethical concern); the deviation is logged and reported transparently.
- Household guests, if any, are not enrolled subjects. If a guest objects to the operating envelope at any point, the arm is paused for the guest's stay and the deviation is logged.

### Self-experimentation precedent

Self-experimentation is offered as context, not as a regulatory exemption: [Forssmann 1929 cardiac catheterization](https://doi.org/10.1007/BF01884716), [Marshall & Warren 1984 *H. pylori*](https://doi.org/10.1016/S0140-6736(84)91816-6), and the broader N=1 / quantified-self lineage. The building-as-subject framing above is the primary defense; self-experimentation precedent is supplementary.

---

## 12. Limitations

- **N=1.** Generalization claims explicitly bounded to "this house, this climate, this equipment." Multi-home generalization requires future expansion. This is the residential MPC field-study norm (per Khabbazi 2025), not a unique limitation.
- **Single climate zone (IECC 5A, Chicago).** Hot-dry, hot-humid, marine, and mountain climates are out of scope.
- **Single equipment family** (Amana modulating + 2-stage AC + ECM blower). Single-stage AC cannot execute Arm B's deep pre-cool without short-cycling; the result does not transfer to that equipment class.
- **Single-occupant household.** Multi-occupant households introduce occupancy variability and potential controller overrides not present here.
- **Tariff-specific** (ComEd Rate BESH / Hourly Pricing on PJM RTO). Transferability to flat-rate, TOU, or ERCOT-type tariff structures is partial.
- **Investigator-as-occupant unblinding.** Mitigated by frozen analysis code and pre-committed metrics, not eliminated.
- **Forecast-skill dependence.** Both arms' performance depends on NWS forecast quality; degraded forecasts during the experimental period would affect Arm B more than Arm A but would also be a real-world operational reality, not a design flaw.
- **5CP attribution.** Arm B's 5CP-prediction logic is a heuristic, not a forecast. Hit rate on actual PJM 5CPs will reflect prediction skill, not theoretical maximum savings.
- **5CP day-of-week scope.** PJM Manual 19 §4.3 restricts the published PJM RTO 5CP set to non-holiday weekdays in Jun-Sep; ComEd's filed tariff (PJM OATT Attachment M-2 §2(e)) imposes only "different calendar days in a summer" with no day-of-week or holiday carve-out. Arm B's §3 detector applies a single Jun 1 - Sep 30 gate to both scopes (no weekday/holiday refinement) because (a) ComEd zone 5CPs can in principle land on weekends, (b) a federal-holiday calendar dependency would introduce drift risk that could silently invalidate weeks of SCED data, and (c) the cost of a false-positive shutoff on a weekend RTO ramp is small comfort cost with zero capacity benefit — Arm B's weekly $/CDD metric naturally captures this as a design cost. Reported as a measurement limitation rather than corrected in code.
- **Thermal model dependency.** If the operational thermal model fails ratification, Arm B operates with hand-tuned constants for pre-cool depth, coast lead time, and stage-2 advisory. The thermal-model component of the contribution claim is then dropped and reported as a limitation.

---

## 13. Pre-registration commitments

The following are binding once this document is filed to OSF and the pre-registration URL is committed to this repo:

1. The outcome definitions in §2 (O1-O6) including the HVAC channel set (`em:2 + em:8 + em:9`).
2. The arm definitions in §3, including the commit hash for both controllers and the boundary-conditions block.
3. **The locked Arm B threshold values in Appendix A**: day-type classification (HOT at ≥85°F max OR apparent ≥90°F), price-spike tier thresholds (10¢ elevated, 20¢ scarcity, 2¢ hysteresis, 30 min hold), 5CP detection rule (load ratio >0.95 in 13-20 CT window, summer eligibility gate Jun 1 - Sep 30 per PJM Manual 19, dual-scope ComEd-zone + RTO OR'd with per-scope forecast feeds, pre-season fallbacks 20,375 MW and 151,525 MW respectively), pre-cool deepening forecast trigger, and layer priority resolution.
4. The cooling-relevance criterion in §4 (weekly CDD ≥ 5).
5. The randomization seed (`20260601`) and the `randomize_arms.py` script that derives the assignment list, plus the year-round assignment CSV.
6. The 2-week-arm structure within 4-week blocks with randomized order.
7. The post-switch washout duration `W` (§5), formula and clamp.
8. The 6-component weather summary vector (§7), Hungarian matching algorithm, Mahalanobis distance (Use 2) primary metric, T=2.5 quality threshold, 10,000 stationary bootstrap resamples with percentile 95% CI, and the five pre-committed sensitivity analyses.
9. The forecast-correlated vs grid-event decomposition analysis (§7).
10. The reporting structure in §8 (decomposition, continuous-axis scatter, per-pair table, within-day hourly profile, indoor temperature distribution, day-of-week distribution, boundary-conditions block).
11. The §11 building-as-subject ethics framing.

Changes after pre-registration require an amendment posted to OSF with explicit justification and are reported as deviations in any published methods section.

---

## 14. References

- Allcott, H. (2011). Rethinking real-time electricity pricing. *Resource and Energy Economics* 33(4):820-842. [doi:10.1016/j.reseneeco.2011.06.003](https://doi.org/10.1016/j.reseneeco.2011.06.003). The only published residential RTP analysis directly relevant to ComEd Hourly Pricing.
- ASHRAE (2023). *Guideline 14-2023: Measurement of Energy, Demand, and Water Savings.* The published M&V standard for multivariable regression baseline; methodology source for the 6-component weather summary vector.
- Bacher, P., & Madsen, H. (2011). Identifying suitable models for the heat dynamics of buildings. *Energy and Buildings* 43(7):1511-1522. [doi:10.1016/j.enbuild.2011.02.005](https://doi.org/10.1016/j.enbuild.2011.02.005). Methodological anchor for the Arm B thermal model.
- Blonz, J., Palmer, K., Wichman, C. J., & Wietelman, D. C. (2023). Smart Thermostats, Automation, and Time-Varying Prices. *AEJ: Applied Economics* 17(1):1-37. [doi:10.1257/app.20210618](https://doi.org/10.1257/app.20210618). Closest methodologically rigorous comparison of automated vs manual smart-thermostat operation under time-varying prices; tests TOU not RTP.
- Drgoňa, J., et al. (2020). All you need to know about model predictive control for buildings. *Annual Reviews in Control* 50:190-232. [doi:10.1016/j.arcontrol.2020.09.001](https://doi.org/10.1016/j.arcontrol.2020.09.001). Demonstrates that the often-cited "20-30% MPC savings" claim is against bang-bang baselines; against optimized rule-based controllers the residential delta narrows to 5-15%.
- Henze, G. P. (2013). Model predictive control for buildings: a quantum leap? *Journal of Building Performance Simulation* 6(3):157-158. [doi:10.1080/19401493.2013.778519](https://doi.org/10.1080/19401493.2013.778519). Notes the headline-MPC-savings caveats explicitly.
- Heyvaert, M., & Onghena, P. (2014). Randomization tests for single-case experiments. *Journal of Contextual Behavioral Science* 3(1):51-64. [doi:10.1016/j.jcbs.2013.10.002](https://doi.org/10.1016/j.jcbs.2013.10.002). Statistical method for the secondary SCED randomization test.
- Wang, D., Chen, Y., Wang, W., Gao, C., & Wang, Z. (2023). Field test of Model Predictive Control in residential buildings for utility cost savings. *Energy and Buildings* 288:113026. [doi:10.1016/j.enbuild.2023.113026](https://doi.org/10.1016/j.enbuild.2023.113026). Reports 22.1% (bedroom) and 26.8% (living room) cooling cost savings vs rule-based control under Time-of-Use pricing; the only published residential MPC field test with an explicit RBC + ToU comparator.
- Khabbazi, A., Pergantis, E. N., et al. (2025). Lessons learned from field demonstrations of MPC and RL for residential and commercial HVAC: A review. *Applied Energy* 387 (2025) 126459. [arXiv:2503.05022](https://arxiv.org/abs/2503.05022). Source of the field-gap framing; documents that "real-time pricing" appears once across 104 papers reviewed.
- Oldewurtel, F., et al. (2012). Use of model predictive control and weather forecasts for energy efficient building climate control. *Energy and Buildings* 45:15-27. [doi:10.1016/j.enbuild.2011.09.022](https://doi.org/10.1016/j.enbuild.2011.09.022). Canonical reference for forecast-value isolation in building MPC.
- Pergantis, E. N., et al. (2024). Humidity-aware MPC for residential air conditioning: A field study. *Building and Environment* 266:112093. Closest published prior art for cooling-season residential MPC field study in a similar climate zone.
- Pergantis, E. N., et al. (2024). Field demonstration of predictive heating control for an all-electric house in a cold climate. *Applied Energy*. Companion to the above; same instrumented house in IECC 5A.
- Pritoni, M., et al. (2015). Energy efficiency and the misuse of programmable thermostats: The effectiveness of crowdsourcing for understanding household behavior. *Energy Research & Social Science* 8:190-197. [doi:10.1016/j.erss.2015.06.002](https://doi.org/10.1016/j.erss.2015.06.002). Documents real-world programmable-thermostat adoption and override patterns relevant to the Arm A baseline framing.
- PJM Interconnection (2025). 2026/27 Base Residual Auction Report. Source of the residential capacity-charge rate change context for O2.
- Saloux, E., Candanedo, J., et al. (2025). From theory to practice: A critical review of MPC field implementations in the built environment. Companion meta-review to Khabbazi 2025.

---

## Appendix A: locked threshold values (data-grounded)

Threshold values for the Arm B logic, locked to 2025 ComEd RTP and Plainfield-IL weather data analysis (May-September 2025, n=3,663 hourly observations across the full cooling season, n=122 summer days for the Jun-Sep day-level statistics). The full distribution analysis, hour-of-day patterns, threshold-frequency tables, scarcity-event days, and weather correlation results are reproduced by the frozen analysis bundle at [`tools/comed_2025_analysis/`](../tools/comed_2025_analysis/) (committed alongside the OSF filing; see [`tools/comed_2025_analysis/README.md`](../tools/comed_2025_analysis/README.md) for a claim-by-script mapping and [`tools/comed_2025_analysis/expected_output.md`](../tools/comed_2025_analysis/expected_output.md) for frozen expected output). Headline observations driving these thresholds:

- **Distributional percentiles** over the May-Sep 2025 cooling-season hourly distribution (n=3,663): P95 = 9.53¢/kWh, P99 = 20.47¢/kWh. (The narrower Jun-Sep subset yields P95 = 10.16¢/kWh, P99 = 23.48¢/kWh; the locked 10¢/20¢ trigger pair sits between these two framings.)
- **Hour-of-day pattern** over Jun-Sep 2025: 18:00 CT is the highest-mean hour (mean 11.03¢/kWh) and the highest-frequency spike hour (23.8% of its hours ≥10¢, the highest fraction of any hour). The overall hourly max in the dataset was 161.29¢/kWh at 17:00 CT on 2025-06-24; 18:00 CT's own max was 146.29¢/kWh on the same day. Both 17:00 and 18:00 fall outside the original 14-18 CT scheduler shutoff window's effective center.
- **Day-level forecast tractability:** 8 of 17 scarcity days had max temp <87°F (motivating real-time price reactivity rather than pure forecast-driven control).

All values pre-committed before OSF filing and frozen at the OSF commit hash.

### Day-type classification (recalibrated)

| Day-type | Trigger | Notes |
|---|---|---|
| MILD | forecast max < 75°F | No active scheduling |
| NORMAL | 75-85°F max | Standard pre-cool / coast / recover / sleep |
| HOT | ≥85°F max OR apparent ≥90°F | Aggressive pre-cool, shutoff during scarcity-risk hours |
| HOT_STREAK_DAY1 | HOT today AND HOT tomorrow | Deeper / earlier pre-cool to bank multi-day mass |

Recalibration captures 54% of historical price-spike days and 71% of scarcity days. The remaining 46% of spike days are grid-event-driven and addressed by real-time price reactivity. (Both figures reproduced by [`tools/comed_2025_analysis/verify_appendix_a.py`](../tools/comed_2025_analysis/verify_appendix_a.py).)

### Real-time RTP price-spike reactivity

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

### PJM 5CP-eligibility detection (dual-scope)

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

### Layer priority (warmer wins, safety supervisor floor)

```
effective = max(schedule + humid_override,
                schedule + price_overlay,
                5cp_shutoff_setpoint)
effective = clamp(effective, 65, 86)
```

### Day-of-week awareness

**None.** Reported descriptively in the paper. 2025 data shows Sat with zero scarcity hours and Mon-Tue with 25 combined; insufficient seasons of data to encode in the controller.

---

## Appendix B: implementation status and gating

This pre-registration is conditional on the following Arm B implementation work completing before randomization begins:

- **Real-time RTP price-spike reactivity** (3-tier logic with hysteresis per Appendix A): new code in `hvac-scheduler`. Target: completed and tested by 2026-05-25.
- **PJM 5CP-eligibility detection** (live PJM load + season-to-date 5th-highest peak comparison + load derivative + 13-20 CT window per Appendix A; dual-scope ComEd-zone + RTO with OR'd triggers): new code in `hvac-scheduler` (`pjm_5cp.py` parameterized by `DetectorScope`) and parallel data ingestion in `pjm-dm2-poller` (`inst_load?area=PJM RTO` and `hrl_load_metered?zone=RTO` alongside the existing ComEd-zone feeds). Modeled on the joe248 AppDaemon approach. Target: completed and tested by 2026-05-25.
- **Day-type classifier recalibration** (HOT at ≥85°F max OR apparent ≥90°F per Appendix A): edit existing day-type thresholds in `hvac-scheduler`. Target: completed and tested by 2026-05-15.
- **Layer priority resolution** (`max(schedule, price_overlay, 5cp_shutoff)` semantics): new code in `hvac-scheduler` `execute_action()`. Target: completed and tested by 2026-05-25.
- **AIR toggling procedure** for Monday arm transitions (ON for Arm A, OFF for Arm B): manual via TCC web UI initially, automated via Control4 driver if feasible. Target: documented procedure by 2026-05-25.
- **Dry-run mode confirmation:** verify Pi scheduler dry-run produces no setpoint pushes during Arm A weeks while continuing to log intended actions to InfluxDB. Target: validated by 2026-05-25.
- **Thermal model fit (Arm B optional component):** gated on Ecowitt deployment plus ≥14 days paired observations. If not ratified by experiment start, Arm B operates with hand-tuned constants for the three thermal-model integration points and the model dependency is reported as a limitation.

Detailed implementation specifications including code-level decision rules, integration points with the existing scheduler, and validation criteria will be drafted in `docs/ARM_B_IMPLEMENTATION.md` (in progress).

If any of the above is not completed and validated by 2026-05-31, OSF filing is delayed until completion. The pre-registration is binding only after OSF filing.
