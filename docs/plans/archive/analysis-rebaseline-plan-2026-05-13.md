---
date: 2026-05-13
owner: chris
status: active-pending-review
role-label: chris
---

# Analysis rebaseline plan (2026-05-13)

## 0. Executive summary

This plan replaces the patch-era recalibration plans (now archived at `docs/plans/archive/`). Trigger: a 2-day intense recalibration sprint produced PR #108 (spec amendment, merged) and PR #109 (Phase 1 implementation, paused). Patches accumulated faster than the design contract was locked, creating legitimate concern that we were patching toward a contract we hadn't yet stated cleanly. This rebaseline states the corrected contract first, audits the current pipeline against it, and recommends one of four migration paths.

**Bottom line (preview; full justification in §9 + §10):**

- **Recommended: Option B — rewrite Stage 3 and Stage 5 cleanly from main, cherry-pick the new design surface from PR #109, close PR #109 without merge.** Estimated 8–14 focused hours. Fits the 2026-05-30 OSF target.
- **NOT recommended: Option C (full pipeline rewrite).** Stages 1, 4, 6, 7, 8 are architecturally correct as-is — 5 of 9 stages. Stage 2's 10 quality rules are all validity-quality and correct except for the never-implemented washout (which would be new code in any path). The drift was contained to two surfaces (Stage 3 columns, Stage 5 outcome tuple) + the new Eagle / missing-data design surface that any implementation would face.
- **PR #109 disposition: close without merge, keep branch as evidence.** Cherry-pick the new design (Eagle helpers, query, manifest entries, Phase 1.0 findings doc, Phase 2 Eagle-allocation plan). Replay the Stage 3/5 columns cleanly without `$/CDD` scaffolding.

## 1. Research question and non-negotiable locks (input)

### Research question

> Does a smart RTP/DTOD/5CP-aware HVAC controller save **actual money** compared with a standard programmable/set-it-and-forget-it thermostat in this house, under comparable weather/conditions?

### Non-negotiable locks (carried from Chris 2026-05-13)

1. **Actual dollars** answer the primary question.
2. **kWh secondaries** explain mechanism.
3. **CDD** is only part of the weather-matching vector and the historical baseline covariance calibration — nothing else.
4. **No `$/CDD`** outcome anywhere.
5. **No CDD eligibility gate.**
6. **Stage 3 computes facts only.** Stage 4 is the single weather-comparability decision point.
7. **No silent data-source substitution.**
8. **No silent Eagle gap smearing.**
9. **Eagle is canonical** whole-home meter source where usable.
10. **Refoss HVAC channels** are canonical HVAC circuit source.
11. **Refoss mains** are a sanity check / timing-shape backup only if explicitly designed and provenance-logged.
12. **Missing/gappy data** must either be validly imputed under a named rule or reason-coded as omitted. Never silent.

## 2. Re-stated intended pipeline architecture

Each stage has a single responsibility. Decisions belong to one stage; the others compute facts or apply pre-committed inference machinery.

| Stage | Single responsibility | What it decides | What it does NOT decide |
|---|---|---|---|
| **1 Extract** | Pull raw measurements from Influx, write parquet per measurement + manifest. | Nothing analytical — data plumbing only. | Quality, comparability, outcomes. |
| **2 Validity / exclusions** | Apply data-quality rules. Decide which weeks + which days are valid for formal analysis. | Per-week qualifies bool. Per-day included bool + exclusion source. | Comparability between arms. Outcome values. |
| **3 Weekly facts** | Per-(week, arm), compute outcome values + weather summary vector. | Nothing — propagates Stage 2's qualifies unchanged. Computes facts only. | Whether the week qualifies (Stage 2). Whether weeks are comparable (Stage 4). |
| **4 Weather matching** | Match qualifying Arm A weeks with qualifying Arm B weeks on the 6-component weather vector via Mahalanobis-Hungarian. | Pair assignments. Pair quality (primary vs poor). Outlier flagging. | Which outcomes to compute. How to report. |
| **5 Effects + CI** | Per outcome (from Stage 3) over primary pairs (from Stage 4): matched-pair median Δ + stationary bootstrap 95% CI. | Statistical summary per outcome. | Statistical significance interpretation (no accept/reject). |
| **6 O2 capacity** | Reconstruct annual capacity-charge avoidance (Layer 1 ACustCPL $ delta, Layer 2 reconstructed, Layer 3 bill reconciliation). | Per-layer dollar values per arm. Detector accuracy (process metric). | Whether the detector was "good" (descriptive only). |
| **7 SCED randomization** | Sign-flip permutation test over matched-pair differences as a secondary inference channel. | Per-outcome two-sided p-value. | Whether to publish based on p-value. |
| **8 Decomposition** | Per qualifying day, classify into spike categories; report arm-level category-median $ delta per (outcome × category). Layer-attribution side table for grid-event days. | Per-day classification. Layer-triggered attribution. | Matched-pair primary inference (that's Stage 5). |
| **9 Sensitivities** | Re-run Stages 4-5 (or 8) with one pre-committed alteration per sensitivity; report effects-like rows per sensitivity. | Per-sensitivity alternative effect estimates. | Whether sensitivity differs "enough" (descriptive). |

### Architectural invariants

- **Stage 3 boundary rule:** Stage 3 propagates Stage 2's `qualifies` unchanged. Stage 3 never re-derives quality logic. Currently locked at `tools/analysis/pipeline.py:2019-2020` (preserved in this rebaseline).
- **Stage 4 single decision point:** all weather-comparability decisions live here. Stage 2 decides validity (data-quality), Stage 4 decides comparability (weather pairing). No CDD-eligibility gate, no per-outcome filtering at Stage 3.
- **Outcome agnosticism downstream of Stage 5:** Stages 5/7/9 consume `STAGE5_OUTCOMES` programmatically. Adding or removing an outcome should require one tuple edit + tests, not pipeline surgery.
- **Provenance everywhere:** every drop, every imputation, every coverage gap → a record in `<stage>/provenance.json` or `<stage>/reason_report.json`. No silent behavior.

## 3. Audit of current pipeline against the intended architecture

Source-grounded audit. Citations are file:line on `main` (post-PR-#108) unless noted as "PR #109 branch."

| Stage | Code site | Current state | Verdict |
|---|---|---|---|
| **1 Extract** | `pipeline.py:514-577` `stage1_extract` | Pulls every `*.flux` query in `tools/analysis/queries/`, writes parquet per measurement + manifest. No semantic logic. PR #109 added `eagle.meter.flux` (new measurement; not patching). | **Keep unchanged.** Cherry-pick `eagle.meter.flux` + `KNOWN_MEASUREMENTS` extension from PR #109. |
| **2 Validity** | `pipeline.py:580-1990` (Rules 1-10 + orchestrator) | 10 quality rules, all data-validity. `rule8_pi_apply` is a misleadingly-named generic min-days rule. Washout was spec'd but never implemented. CDD-eligibility gate was spec'd (now removed from spec, never implemented in code). | **Keep with small edits.** (a) Rename `rule8_pi_apply` to `rule8_min_days_apply` and broaden to accept any day-set. (b) Add `washout_apply` rule + integrate into the min-days union. (c) Add Eagle coverage rule (drops O4/O8 on gap > 300s; locked in PR #109 work). (d) Eventual whole-house power-outage rule (Phase 2; see Phase 2 plan in §5 below). |
| **3 Weekly facts** | `pipeline.py:1993-2379` + `WEEKLY_CSV_LOCKED_COLUMNS` | Schema currently bakes `o1_dollars_per_cdd`, `o4_dollars_per_cdd_whole_home` as primary columns. PR #109 added actual-$ + actual-kWh columns additively; `$/CDD` columns retained as cross-validation scaffolding. `_compute_weekly_row` still computes `$/CDD` even after PR #109's actual-$ work lands. | **Partial rewrite.** (a) Replace `WEEKLY_CSV_LOCKED_COLUMNS` with the locked schema: `(week_start_ct, arm, qualifies, weekly_hvac_dollars, weekly_whole_home_dollars, weekly_hvac_kwh, weekly_whole_home_kwh, o3_peak_hvac_kw, *WEATHER_VECTOR_COMPONENTS)`. No `$/CDD` entries. (b) Delete `weekly_dollars_per_cdd` helper (was Phase 1 scaffolding on the branch; ship without). (c) `_compute_weekly_row` returns only the locked columns. (d) `_empty_weekly_row` writes `""` (empty cells), not `0.0` — placeholder zeros are banned by the no-silent-zero policy. (e) Cherry-pick `weekly_actual_dollars`, `eagle_hourly_kwh_from_delivered`, `eagle_refoss_mains_drift`, `eagle_coverage` from PR #109 (these are clean new helpers, not patches). |
| **4 Matching** | `pipeline.py:2382-2451` `stage4_matching` | Mahalanobis-Hungarian on 6-component weather vector, T=2.5 quality threshold. Outcome-agnostic. Already exactly the architecture this rebaseline endorses. | **Keep unchanged.** |
| **5 Effects + CI** | `pipeline.py:2454-2520` `stage5_effects` + `STAGE5_OUTCOMES` | Bootstrap CI machinery correct. `STAGE5_OUTCOMES` tuple currently contains the `$/CDD` outcomes as primary. PR #109 added new outcomes additively (with scaffolding retained). No `unit` column on main; PR #109 added it. No `percent_of_arm_a` column on main; PR #109 added it. | **Partial rewrite.** (a) Replace `STAGE5_OUTCOMES = ("weekly_hvac_dollars", "weekly_whole_home_dollars", "weekly_hvac_kwh", "weekly_whole_home_kwh", "o3_peak_hvac_kw")`. No `$/CDD` entries. (b) Cherry-pick `STAGE5_DOLLAR_OUTCOMES` constant + `STAGE5_OUTCOME_UNITS` dict + `unit` column + `percent_of_arm_a` column from PR #109. (c) `_compute_pair_diffs` returns `(pair_id, diff, arm_a_value)` triples (cherry-picked from PR #109). (d) `pair_diffs.csv` schema unchanged (Stage 7 unaffected). |
| **6 O2 capacity** | `pipeline.py:2773-...` `stage6_o2` | Already actual capacity dollars. Layer 1 ACustCPL $ delta is one of the co-primary outcomes per the locked outcome contract. No drift. | **Keep unchanged.** |
| **7 SCED randomization** | `pipeline.py:~3479-3496` `stage7_sced` | Outcome-agnostic. Iterates `STAGE5_OUTCOMES`. Inherits whatever Stage 5 produces. | **Keep unchanged** (will pick up the new outcome list automatically). |
| **8 Decomposition** | `pipeline.py:~3536-4540` `stage8_decomposition` | Uses its own daily outcome names (`o1_daily_hvac_dollars`, `o3_daily_peak_hvac_kw`, `o4_daily_mains_dollars`) — actual daily $, not $/CDD. The existence proof for the actual-$ worldview in this repo. | **Keep unchanged.** Note: Stage 8 currently reads `weekly_cdd` from `stage3/weekly.csv` to check qualifying weeks — that column is kept by this rebaseline (as a weather-vector component), so Stage 8 wiring is unaffected. |
| **9 Sensitivities** | `pipeline.py:4548-4674` `stage9_sensitivity` (stubbed) | Stub loader; locked output schemas. Phase 9 design locks (Mahalanobis primary, day_of_week as Stage 8 derivative, em2_em8_only emits O1 only, etc.) recorded in `~/.claude/projects/D--Projects-energy-proxy/memory/project-stage9-decision-locks-2026-05-12.md`. | **Re-plan after rebaseline.** Reuse Stage 9 locks; implement in a separate post-rebaseline PR. |

### Stage classification summary

| Verdict | Count | Stages |
|---|---|---|
| Keep unchanged | 5 | 1, 4, 6, 7, 8 |
| Keep with small edits | 1 | 2 (rule rename + washout + Eagle coverage rule + future power-outage rule) |
| Partial rewrite | 2 | 3 (column list, helpers, no-placeholder-zero), 5 (outcome tuple, unit column, percent column) |
| Full rewrite | 0 | — |
| Retire | 0 | — |
| Plan separately | 1 | 9 (post-rebaseline) |

**5 of 9 stages keep unchanged. 0 of 9 stages need full rewrite.** That distribution is what makes Option B (rewrite Stage 3/5 cleanly) the right scope.

## 4. Complete-rewrite question — the explicit answer

**Should this be a complete pipeline rewrite? No.** Evidence:

1. **Architecture is right.** Stages 1, 4, 6, 7, 8 match the rebaseline locks exactly. Stage 4's Mahalanobis-Hungarian on the 6-D weather vector is the single weather-comparability decision point Chris's locks require. Stage 6 has been actual-capacity-$ from inception. Stage 8 has been actual-daily-$ from inception.
2. **Stage 2's quality rules are sound.** All 10 rules are validity/data-quality, not weather-comparability. The CDD-eligibility gate was spec'd-but-not-implemented — the spec amendment correctly dropped it; no rewrite needed. Washout would have been new code in any path.
3. **Drift was localized.** Concentrated in two surfaces: Stage 3 outcome columns (`$/CDD` baked in) and Stage 5 `STAGE5_OUTCOMES` tuple. Each is < 100 lines of code change.
4. **PR #109's "patches" included new design, not patches.** Eagle wiring, drift detection, coverage gate — all NEW design surfaces. There was no Eagle behavior to patch; we were building Eagle support from scratch. The pattern that *felt* like patching was actually:
   - Day 1: Audit found spec/RQ misalignment → spec amendment (PR #108, merged cleanly).
   - Day 1-2: Phase 1 implementation surfaced 2 real design questions (silent Refoss substitution; gap smearing). Both were new-design decisions any implementation would have faced, not pipeline patches.

5. **Sunk-cost framing rejected.** Chris explicitly said this isn't a "throw away months of work" decision — it's a "lock the contract before continuing" decision. A full rewrite isn't justified by the evidence above, and the audit isn't biased by the prior investment.

A full rewrite would be appropriate if:
- The architecture itself was wrong (it isn't).
- Multiple stages had drift (only 2 do).
- The drift was structural rather than column-name-level (it's column-name-level).

None of those conditions hold. Option B is the right scope.

## 5. Hidden old-world assumptions still embedded

Audit of remaining drift on `main` (post-PR-#108 spec amendment) + PR #109 branch:

| Assumption | Location | Drift class | Disposition |
|---|---|---|---|
| `o1_dollars_per_cdd` column name | `pipeline.py:1995` `WEEKLY_CSV_LOCKED_COLUMNS` (main) | $/CDD | **Remove** in Option B rewrite. |
| `o4_dollars_per_cdd_whole_home` column name | same | $/CDD | **Remove.** |
| `weekly_dollars_per_cdd` helper function | `pipeline.py:319-342` (main) | $/CDD | **Remove.** Misleading docstring at lines 330-334 about Stage 2 enforcing cooling-relevance is also stale. |
| `STAGE5_OUTCOMES` containing `$/CDD` entries | `pipeline.py:2454-2458` (main) | $/CDD | **Replace** with actual-$/kWh + peak kW list. |
| `_empty_weekly_row` writes `0.0` for outcomes | `pipeline.py:2056-2068` (main) | Placeholder zero | **Replace** with `""` empty cells. Aligns with Stage 8's no-placeholder-zero policy (`docs/ANALYSIS_PIPELINE.md` Stage 8 section). |
| `$/CDD` columns retained as cross-validation scaffolding | PR #109 `WEEKLY_CSV_LOCKED_COLUMNS` | Scaffolding | **Do not bring forward.** Option B rewrites without scaffolding. |
| Silent Refoss substitution when Eagle missing | Was on `main` Phase 1 commit `830d021`; fixed at `ec7369c` (PR #109 branch) | Silent fallback | **Cherry-pick the fix.** No silent substitution; O4/O8 drop with reason. |
| Silent gap smearing in `eagle_hourly_kwh_from_delivered` | PR #109 commits `830d021`-`ec7369c`; fixed at `59f3520` (PR #109 branch) | Silent fallback / source mixing concern | **Cherry-pick the fix.** Coverage gate + drop on max_gap > 300s. |
| Eagle vs Refoss "averaging" — explicitly prohibited but no enforcement test before PR #109 | Implicit drift | Source mixing | **Cherry-pick** the real-loader test from PR #109 that proves Eagle is canonical (not averaged). |
| Stage 3 carrying any quality decision logic | Searched: none on main; the boundary rule at `pipeline.py:2019-2020` is correctly enforced via `qualifies` propagation only. | Stage 3 deciding Stage 4 things | **Confirmed clean.** |
| Cooling-relevance gate in code | None; was spec-only, removed by PR #108 amendment. | Cooling relevance | **Already resolved.** |

Searches that found nothing concerning:
- `grep -r "cooling.relevant\|cooling-relevance" tools/analysis/` returns one match in `baseline_distribution.py` for the historical ERA5 calibration filter (CDD ≥ 5 for the baseline covariance computation) — this is intentional per lock #3 and stays.
- `grep -r "$/CDD" tools/analysis/` outside the `$/CDD` columns themselves — none.

## 6. Missing-data policy matrix

Locks the per-source behavior. Each case below has a single decision: validly impute, reason-code drop, or treat as outage exclusion. No silent zeros.

| # | Case | Decision | Rule / mechanism | Reason code | Notes |
|---|---|---|---|---|---|
| 1 | Refoss HVAC channels missing/gappy | Validly impute per Tier 1-3; drop week at Tier 4 | Stage 2 Rule 1 (unchanged) | `rule1_imputation_too_high` (existing) | Up to 10% imputed; > 10% → exclude week. |
| 2 | Eagle missing entire week | Drop O4 + O8 for week; week itself stays in analysis if Refoss HVAC + weather are present | Stage 3 coverage gate via `eagle_coverage` | `no_eagle_meter_data_in_window` | O1, O3, O7 (Refoss-derived HVAC outcomes) unaffected. |
| 3 | Eagle partial gap > 5min | Drop O4 + O8 for week (Phase 1) | Stage 3 coverage gate (`max_gap_seconds >= 300`) | `eagle_meter_gap_exceeds_threshold` | Phase 2 plan deferred: shape-allocated imputation using Refoss-mains; see archived plan. |
| 4 | Eagle present + Refoss-mains drift > 10% weekly | Flag in `stage3/provenance.json`, NO drop | Sanity-check provenance entry | (drift flag, not a reason code) | Eagle remains canonical; investigation triggered for the Refoss instrumentation. |
| 5 | Eagle + Refoss both missing (no overlap with scheduler outage) | Drop O4 + O8; O1/O3/O7 also affected if Refoss HVAC channels missing | Stage 3 coverage gate fires for Eagle; Rule 1 fires for Refoss HVAC; the week may also fail Rule 1's 10% imputation cap | Multiple reason codes per affected outcome | If Eagle + Refoss + scheduler all simultaneously silent → see #9 (power outage). |
| 6 | ComEd price missing | Imputation from day-ahead LMP + locked spread constant; > 20% imputed → exclude week | Stage 2 Rule 3 (unchanged) | `rule3_imputation_too_high` (existing) | |
| 7 | Ecowitt/weather missing | NWS gridpoint fallback then `(Tmax+Tmin)/2 − 65` daily estimator; > 6h both-missing in a day → drop day from CDD | Stage 2 Rule 5 (unchanged) | `rule5_weather_gap` (existing) | |
| 8 | Scheduler outage | Excluded per Rule 7 thresholds (> 1% week downtime OR any single > 60min OR overlaps control window) | Stage 2 Rule 7 (unchanged) | `scheduler_outage_*` (existing) | |
| 9 | Whole-house / rack power outage (Eagle + Refoss + scheduler simultaneously silent ≥ 5min) | **New rule needed (Phase 2-ish).** Mark span as power-outage; exclude affected days; O1/O3/O4/O7/O8 drop for those days at Stage 8 + their weeks may fail Rule 1's 10% cap at Stage 3. | New Stage 2 Rule 11 OR extension of Rule 7 (design call) | New: `whole_house_power_outage` | Currently NOT in any active plan; flagged in archived Eagle Phase 2 plan. Rebaseline keeps as deferred but explicit. |

## 7. Outcome contract

Locked. Source-of-truth columns + units + missing behavior.

| Outcome | Type | Data source | Stage emitting | Unit | Missing-data drop behavior |
|---|---|---|---|---|---|
| **O1** weekly HVAC actual cost | co-primary | Refoss `em:2 + em:8 + em:9` × supply_¢ × DTOD | Stage 3 col `weekly_hvac_dollars` → Stage 5 effects | dollars | Drops with `rule1_imputation_too_high` (Refoss > 10% imputed). Other rules also apply. |
| **O2 Layer 1** capacity-charge $ delta | co-primary | PJM 5CP hours × mains kW × tariff factor | Stage 6 `o2_layer1.csv` | dollars | Drops with `no_pjm_5cp_hours_in_window` or single-arm-coverage codes. |
| **O4** weekly whole-home actual cost | co-primary | Eagle `delivered_kwh` differential × supply_¢ × DTOD | Stage 3 col `weekly_whole_home_dollars` → Stage 5 | dollars | Drops with `no_eagle_meter_data_in_window` or `eagle_meter_gap_exceeds_threshold`. Refoss NOT substituted. |
| **O3** weekly peak HVAC kW (1h rolling mean) | secondary | Refoss HVAC channels max hourly kWh | Stage 3 col `o3_peak_hvac_kw` → Stage 5 | kw | Drops on Refoss Rule 1 fail. |
| **O7** weekly HVAC actual energy | secondary | Refoss `em:2 + em:8 + em:9` sum | Stage 3 col `weekly_hvac_kwh` → Stage 5 | kwh | Drops on Refoss Rule 1 fail. |
| **O8** weekly whole-home actual energy | secondary | Eagle `delivered_kwh` totalizer endpoint differential | Stage 3 col `weekly_whole_home_kwh` → Stage 5 | kwh | Currently drops same as O4 (Phase 1). Phase 2 may relax: O8 survives partial gap if endpoints OK. |
| **O5** within-day kWh + $ profile | descriptive | Refoss HVAC hourly | Stage 3 / reporting | varies (kwh, dollars) | Best-effort across qualifying weeks; per-hour cells may be sparse. |
| **O6** all-day HVAC load-shift / rebound diagnostic | descriptive | Refoss HVAC + `hvac.5cp_state` + `hvac.price_overlay` (price tier from controller's locked overlay rule) | Stage 3 raw → reporting panel | per-panel (dollars, kwh) | Per-panel best-effort; controller-locked thresholds (Normal `<10¢`, Elevated `≥10¢ <20¢`, Scarcity `≥20¢`). |

Stage 5 emits a `unit` column + a `percent_of_arm_a` column populated only for **O1 and O4** (dollar outcomes). O2 reports absolute $ delta only (Stage 6 bootstrap denominator differs).

## 8. Test strategy

Five layers; every primary outcome touches all five.

| Layer | Purpose | Scope |
|---|---|---|
| **Outside-in feature tests** | Acceptance tests per locked primary outcome. End-to-end via the real loader path. | One test per primary outcome (O1, O4, O2 Layer 1) + secondary (O3, O7, O8). Synthetic real-shape Stage 1 export, runs Stages 1-5 (or 1-6 for O2), asserts the headline row in `effects.csv` (or `o2_layer1.csv`). |
| **Numeric oracle tests** | Per-helper, hand-computed values with ¢/kWh tolerance. | Per pure helper (`weekly_actual_dollars`, `eagle_hourly_kwh_from_delivered`, `eagle_refoss_mains_drift`, `eagle_coverage`, `weekly_cdd`, etc.). |
| **Shape tests** | Schema, column order, presence of unit/percent columns, presence of provenance keys. | `WEEKLY_CSV_LOCKED_COLUMNS`, `STAGE5_OUTCOME_UNITS`, `STAGE5_DOLLAR_OUTCOMES`, manifest schema, provenance.json schemas. |
| **Replay validation** | Run full pipeline against real Stage 1 export (current week from Pi-lab Influx). Verify every populated stage has correct row counts; every empty stage has reason-coded cause. | Done end-of-phase per migration plan. 7-day + 90-day windows on Pi-lab. |
| **Drift-detection rule** | When a new outcome / column / reason code is added, a check enforces it appears in every relevant test layer. | Compile-time enforcement via `set(STAGE5_OUTCOMES)` against the existence of a per-outcome test. Failure: explicit message naming the missing layer. |

### Automatic gap-closer rule

When the rebaseline implementation adds a new outcome or reason code:

1. Outcome must appear in `STAGE5_OUTCOMES` AND `STAGE5_OUTCOME_UNITS`.
2. If a dollar outcome, must appear in `STAGE5_DOLLAR_OUTCOMES`.
3. Must have a per-outcome numeric oracle test.
4. Must have a missing-data-drop test (which reason code fires when its primary input is absent).
5. Must appear in the outcome-contract table in `docs/EXPERIMENT_DESIGN.md` §2 with locked unit + locked drop behavior.

A meta-test (`tools/analysis/tests/test_outcome_contract.py` — new) walks the outcome set and asserts each layer. Failure mode: "outcome X declared in STAGE5_OUTCOMES has no entry in STAGE5_OUTCOME_UNITS" — caught at PR time, not at OSF filing time.

## 9. Migration options

Four options. Each is graded on blast radius, risk, effort, OSF impact, and PR #109 disposition.

### Option A — Salvage incrementally (continue PR #109 path)

| Dimension | Assessment |
|---|---|
| **What** | Continue from PR #109's current state. Phase 1 (additive with $/CDD scaffolding) → Phase 2 (subtractive). Eagle + drift + coverage already landed on the branch. |
| **Blast radius** | Small (Stage 3 columns, Stage 5 outcome list, tests; all already done additively). |
| **Risk** | Medium. Patch pattern that triggered the rebaseline continues; each new design question surfaces as another patch. Phase 2's removal of the scaffolding is mechanical but error-prone (~20 test sites to refresh). |
| **Effort** | ~6-10 focused hours (finish Phase 2, replay re-run). |
| **OSF impact** | Fits 2026-05-30 comfortably. |
| **PR #109 disposition** | Continues as the active implementation. Marked ready-for-review post-rebaseline-approval. |

### Option B — Rewrite Stage 3/5 cleanly from main (cherry-pick the new design surface) ← **RECOMMENDED**

| Dimension | Assessment |
|---|---|
| **What** | Close PR #109 without merge (keep branch as evidence). Open new `docs/analysis-rebaseline-implementation` branch off main. Reimplement Stage 3 columns + Stage 5 outcome list cleanly (no $/CDD scaffolding). Cherry-pick the genuinely-new design surface from PR #109: Eagle helpers, `eagle.meter.flux`, `KNOWN_MEASUREMENTS` extension, Phase 1.0 findings doc, Phase 2 Eagle-allocation plan, real-loader integration test, drift/coverage logic + tests. |
| **Blast radius** | Medium. Stage 3 column list, Stage 5 outcome tuple, `_compute_weekly_row`, `_empty_weekly_row`, ~20 test sites (column-name-pinned tests). |
| **Risk** | Medium. Cherry-pick discipline required to avoid bringing scaffolding forward. Mitigated by writing the new branch fresh and copy-pasting verified helpers + tests by hand rather than `git cherry-pick`. |
| **Effort** | ~8-14 focused hours (rewrite Stage 3/5, port verified helpers, refresh tests, run full pipeline against real Stage 1 once). |
| **OSF impact** | Fits 2026-05-30. Tight if scope drifts; the rebaseline plan provides the discipline. |
| **PR #109 disposition** | Close without merge. Keep branch as evidence (don't delete). Reference in archive notes. The 6 commits remain available for inspection / future re-use. |

### Option C — Full pipeline rewrite

| Dimension | Assessment |
|---|---|
| **What** | Rewrite Stages 2-5, Stage 9 from contract forward. Keep Stages 1, 6, 7, 8 (which are correct). |
| **Blast radius** | Large. ~3000 lines of code in Stages 2-5 would be rewritten. ~1400 lines of test code refreshed. |
| **Risk** | High. Stage 2's 10 quality rules have months of evidence backing them. Rewriting risks introducing subtle bugs in already-tested code. Rule 1 4-tier imputation is intricate; Rule 3 spread imputation depends on a locked constants file. |
| **Effort** | ~40-80 focused hours. |
| **OSF impact** | Almost certainly slips. The 2026-05-30 target would move to mid-late June at earliest. |
| **PR #109 disposition** | Close without merge. Possibly discard the entire branch. |
| **Verdict** | **Not justified by the evidence.** 5 of 9 stages are correct as-is. The drift was concentrated in 2 surfaces, both surgical. A full rewrite would discard months of tested Stage 2 logic to fix Stage 3 column names. |

### Option D — Simplify out the formal pipeline

| Dimension | Assessment |
|---|---|
| **What** | Drop the Mahalanobis-Hungarian matched-pair framework. Replace with simpler arm-vs-arm weekly totals + descriptive weather-similarity reporting. No SCED inference. |
| **Blast radius** | Massive. Affects `EXPERIMENT_DESIGN.md` §7 (entire statistical analysis plan), pre-reg commitment #8, the OSF pre-reg already informally circulated. |
| **Risk** | Very high. The matched-pair design is the methodological contribution of this paper (per `EXPERIMENT_DESIGN.md` §1). Dropping it undermines the paper's framing entirely. |
| **Effort** | Hard to bound. Spec rewrite alone is several days. Implementation changes depend on what replaces the matched-pair design. |
| **OSF impact** | Significant slip. Pre-reg amendment required. |
| **PR #109 disposition** | Discarded. Most of the pipeline work to date is discarded. |
| **Verdict** | **Not appropriate.** The locks Chris committed to (actual $, kWh secondaries, no $/CDD, no CDD gate, Stage 4 single comparability decision) all assume the matched-pair architecture exists. Dropping it would be a different study. |

### Summary table

| Option | Blast radius | Risk | Effort | OSF impact | PR #109 |
|---|---|---|---|---|---|
| A — Salvage incrementally | Small | Medium | 6-10h | Fits | Continue |
| **B — Rewrite Stage 3/5 cleanly** | **Medium** | **Medium** | **8-14h** | **Fits** | **Close, evidence** |
| C — Full rewrite | Large | High | 40-80h | Slips | Close, evidence |
| D — Simplify out | Massive | Very high | Days+ | Slips significantly | Discard |

## 10. Recommendation

**Option B.** Rewrite Stage 3 + Stage 5 cleanly from main; cherry-pick the new-design surface from PR #109 (Eagle wiring, drift, coverage, manifest entries, findings doc, Phase 2 plan, real-loader test); close PR #109 without merge; keep the branch as evidence.

### Justification

1. **The drift was localized.** 2 of 9 stages have $/CDD baked in. The other 7 are either correct or need only small edits (Stage 2 small rule additions; Stage 9 separately planned).
2. **PR #109's helpers and Eagle wiring are correct.** They're new design, not patches. They belong in the rebaseline. Verified by 373 passing tests including 3 oracles for the Eagle gap behavior.
3. **PR #109's scaffolding pattern is the concern.** "Keep $/CDD columns as cross-validation scaffolding, Phase 2 removes them" was the architectural compromise that produced the patch feel. Skipping the scaffolding is a clean reset.
4. **Option A's patches keep accumulating mental load.** Even if the final state is correct, the audit trail looks like patching. Reviewers and the OSF filing reader will see Phase 1 + Phase 2 + amendments, not a clean implementation.
5. **Option C is overkill.** Rewriting Stage 2's 10 quality rules introduces risk to working code to fix a Stage 3 column issue. Not justified.
6. **Option D changes the study.** The locks assume matched-pair design. Different conversation.

### Risks of Option B (called out explicitly)

- **Cherry-pick discipline.** Easy to accidentally bring $/CDD scaffolding forward. Mitigation: write the new branch from scratch; copy verified helpers + tests by hand (not `git cherry-pick`); the rebaseline plan's outcome contract + locks list serve as the checklist.
- **Test refresh scope.** ~20 sites pin `o1_dollars_per_cdd` / `o4_dollars_per_cdd_whole_home`. Mostly mechanical; estimated ~3-4 hours of grep + replace + verify.
- **Eagle Phase 2 plan is on PR #109's branch.** When PR #109 closes, that plan file would disappear. Mitigation: copy `docs/plans/eagle-gap-allocation-phase2-plan.md` to main in the rebaseline-implementation PR (it's a deferred plan, not Phase-1 code; it belongs on main).
- **Replay re-run cost.** Final replay on real Pi-lab Influx adds ~30-60 minutes of operator time. Already in the budget for any option.

### PR #109 disposition

- **Close without merge.** Marked draft now; close after this rebaseline plan is reviewed and Option B accepted.
- **Keep branch.** Don't delete. Cherry-pick reference for future inspection.
- **Banner already in PR body** documenting the pause + pointing here. Will be updated to "closed; superseded by [new implementation PR]" once the rebaseline-implementation PR opens.

### What happens to the existing memory entries

The two memory entries from the recalibration sprint stay valid:
- `feedback-5min-cost-integration-math.md` — locked math correction. Still applies.
- `project-stage9-decision-locks-2026-05-12.md` — Stage 9 sensitivity loader design locks. Still apply; Stage 9 is planned separately post-rebaseline.
- `feedback-hold-deadlines-invest-hours.md` — still applies.
- `feedback-small-can-be-load-bearing.md` — still applies and is reinforced by this rebaseline.

## Tracking

- New branch off main: `docs/analysis-rebaseline` (this plan only, doc-only PR).
- After this plan is reviewed + Option B accepted: separate implementation branch `docs/analysis-rebaseline-implementation` off main.
- PR #109 closed-without-merge after Option B is accepted.
- Archive this plan to `docs/plans/archive/analysis-rebaseline-plan-2026-05-13.md` when the rebaseline-implementation PR merges.

## Open questions for Chris before implementation begins

1. **Confirm Option B.** Acceptable, or push back?
2. **Eagle Phase 2 plan disposition.** Bring `eagle-gap-allocation-phase2-plan.md` to main (as a deferred plan file), or leave on the closed PR #109 branch as evidence and re-create later when Phase 2 actually starts? Recommend bring to main.
3. **Whole-house power outage rule.** Phase 2 work per §6 case 9. Defer entirely until summer 2026 evidence? Or scope a small Rule 11 now? Recommend defer.
4. **Stage 2 rule rename (`rule8_pi_apply` → `rule8_min_days_apply`).** Land in the rebaseline-implementation PR or as its own small PR? Recommend rebaseline-implementation (no point in stacking).
5. **Washout primary-exclusion.** Pre-reg commitment #7 in `EXPERIMENT_DESIGN.md:464` says washout is part of primary. Code never implemented it. The rebaseline endorses re-implementing it. Land in the rebaseline-implementation PR or in a separate follow-on? Recommend rebaseline-implementation (it touches Stage 2 + Stage 3 the same way the column rewrite does).

Stop here. No implementation code until this plan is reviewed.
