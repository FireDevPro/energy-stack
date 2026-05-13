---
date: 2026-05-12
owner: chris
status: audit-complete-locks-applied-rev2
role-label: chris
---

# Pipeline recalibration audit (2026-05-12)

**Revision history:**
- Initial audit 2026-05-12 morning.
- Rev1 2026-05-12 mid-day: Chris design locks applied (drop $/CDD entirely; no cooling-relevance subset).
- Rev2 2026-05-12 evening: Chris core lock applied + framing rewrite ("old design centered $/CDD which is misaligned with the actual RTP/DTOD/5CP savings question"); CDD reader-facing diagnostic role removed; O6 redefined as load-shift/rebound diagnostic; kWh secondaries locked; Eagle vs Refoss whole-home source locked; §13 single-commit amendment.

Migration plan filed at `docs/plans/actual-dollar-outcomes-migration-plan.md`.

## 1. Executive summary

This experiment is about whether a smart RTP/DTOD/5CP-aware HVAC controller saves **actual money** compared with a standard programmable/set-it-and-forget-it thermostat in this house, under comparable weather/conditions.

The old analysis design centered `$/CDD`, which is misaligned with the actual RTP/DTOD/5CP savings question. Separately, the spec and code also had gaps — washout specified but missing, CDD gate specified but missing. This recalibration removes the CDD gate from the spec instead of implementing it, drops `$/CDD` entirely from the analysis, and replaces it with actual-dollar primary outcomes.

CDD remains only as one component of the Stage 4 weather-matching vector and as part of the existing baseline covariance calibration (unless separately revisited). CDD does NOT appear as a standalone reader-facing diagnostic, eligibility gate, outcome denominator, or interpretive metric. It is disclosed inside the 6-component matching/balance vector — that is how readers learn the weeks were matched on it.

**Status of dependent plans:**
- `docs/plans/cooling-relevance-primary-gate-plan.md` — **superseded.** The gate is being removed from the spec. The file is marked superseded; source verification it contains is preserved as evidence.
- `docs/plans/washout-primary-exclusion-plan.md` — **still needed** as a validity/out-of-sample exclusion. Mechanics amendments from prior review still apply. Re-sequence: washout PR after the actual-dollar migration lands so the washout filter operates against the new schema.
- Future Stage 9 plan — still paused. Sensitivities #2 (include_washout) and #4 (five_min_pricing) keep meaning under actual-$ primary; outcomes update accordingly.

## 2. Stage-by-stage audit

| Stage | Current role | Decisions made | Primary outputs | CDD role | What changes |
|---|---|---|---|---|---|
| **1 Extract** (`pipeline.stage1_extract`) | Pull every Influx measurement in window; write parquet per measurement | None (data plumbing) | `stage1/<measurement>.parquet` + manifest | none | **Add `eagle.meter` to extracted measurements.** Currently `eagle.meter` exists in production (writer at `deploy/energy-stack/eagle-poller/poller.py`, 30-second cadence, fields `demand_kw / delivered_kwh / received_kwh`) but is NOT in `docs/ANALYSIS_PIPELINE.md` §2.1 input list and NOT in Stage 1's extraction. Needed for the canonical whole-home source. |
| **2 Quality gates** (`pipeline.stage2_quality`, rules 1-10) | Apply ten data-quality rules; emit qualifying weeks + day-level inclusion | Per-week: qualifies/excluded by rule. Per-day: included/excluded with `exclusion_source` | `stage2/qualifying_weeks.csv` + `qualifying_days.csv` + `imputed_intervals.csv` + `outages.csv` | none directly; rule 5 ensures CDD-basis temp coverage but does NOT threshold CDD | **Validity rules retained.** Add washout (separate plan). New: Eagle coverage rule analogous to Rule 1's Refoss handling — define what "Eagle is missing for this hour/day/week" means for whole-home outcomes. |
| **3 Weekly aggregates** (`pipeline.stage3_weekly`) | Per-week per-arm compute outcomes + 6-component weather vector | None (Stage 3 propagates Stage 2's `qualifies` unchanged per `pipeline.py:2019-2020`) | `stage3/weekly.csv` with columns including `o1_dollars_per_cdd, o3_peak_hvac_kw, o4_dollars_per_cdd_whole_home` + weather vector | (a) outcome denominator for o1/o4 — **dropping**, (b) weather-vector component — **keeping** | **Schema and outcomes rewritten.** Add `weekly_hvac_dollars`, `weekly_whole_home_dollars`, `weekly_hvac_kwh`, `weekly_whole_home_kwh`. Whole-home columns sourced from Eagle (canonical), with Refoss-mains drift check + provenance flag. Remove `o1_dollars_per_cdd` and `o4_dollars_per_cdd_whole_home` in Phase 2 (after Phase 1 cross-validates). Keep `o3_peak_hvac_kw` unchanged. Keep weather vector components unchanged. |
| **4 Matching** (`pipeline.stage4_matching`) | Mahalanobis-Hungarian over 6-D weather vector; quality threshold T=2.5 | (a) filter `qualifies==True`, (b) globally-optimal pairing, (c) flag pairs with distance > 2.5 as poor-quality | `stage4/matched_pairs.csv` + `unmatched_weeks.csv` | CDD is component 1 of the 6-D matching vector (correct) | **No change.** Exactly the "match on comparable weather, then compare actual outcomes" structure. Outcome-agnostic; new outcomes flow through unchanged. |
| **5 Effects + CI** (`pipeline.stage5_effects`) | Per matched-pair difference per outcome; stationary bootstrap CI | `STAGE5_OUTCOMES` = three $/CDD-based names. Per outcome: median Δ + 95% CI | `stage5/effects.csv` (+ `pair_diffs.csv`) | inherited from Stage 3 outcome columns | **Outcome list rewritten.** New `STAGE5_OUTCOMES` = primary actual-$ + actual-kWh secondaries + peak kW. Bootstrap machinery unchanged. Add `percent_of_arm_a` column for the headline % framing alongside absolute $ delta. |
| **6 O2 capacity** (`pipeline.stage6_o2`) | Layer 1 `ACustCPL` $ delta + Layer 2 reconstruction + Layer 3 bill reconciliation + detector accuracy | (a) which 5CP hours fall in which arm-week, (b) compute kW → $ via tariff factor, (c) scenario reconstruction (3 portfolio-sum scenarios), (d) detector hit-rate | `stage6/o2_layer1.csv` + `o2_layer2.csv` + `o2_layer3.csv` + `detector_accuracy.csv` | none (operates on PJM 5CP hours, not weekly CDD) | **No change.** Already actual-capacity-dollars. Layer 1 is one of the three primary outcomes Chris named. |
| **7 SCED randomization** (`pipeline.stage7_sced`) | Sign-flip permutation test over Stage 5 pair diffs | Per outcome: 2^N (or 100k random) sign-flips, two-sided p-value | `stage7/sced_pvalues.csv` | none directly | **No code change.** Inherits new outcome list from Stage 5. |
| **8 Decomposition** (`pipeline.stage8_decomposition`) | Daily outcomes per included day; classify into forecast-correlated / grid-event / no-spike; arm-level category medians | (a) day-level filter via `qualifying_days.csv`, (b) per-day actual-$ and peak-kW, (c) per-day spike classification, (d) layer attribution | `stage8/decomposition.csv` + `layer_attribution.csv` + `provenance.json` + `reason_report.json` | **deliberately ignored** at day-outcome level (zero-CDD days included per design intent) | **No change.** Already on the actual-daily-$ pattern this audit endorses for Stage 3 too. Stage 8 is the existence proof. |
| **9 Sensitivities** (`pipeline.stage9_sensitivity`) | Six pre-committed reruns | Effects-like alternatives + DoW + threshold robustness | `stage9/<sensitivity_id>.csv` | sensitivity #1 (`euclidean_zscore`) operates on weather vector incl. CDD | **Outcome names update** when Stage 9 implementation resumes (paused until washout lands). Drop any $/CDD-as-sensitivity framing. |

### CDD role taxonomy summary (post-locks)

| Role | Verdict | Site |
|---|---|---|
| Stage 4 matching-vector component | **Keep** | `WEATHER_VECTOR_COMPONENTS` at `pipeline.py:62`; consumed by Stage 4 Mahalanobis |
| Baseline covariance calibration | **Keep unchanged** unless separately revisited | `tools/analysis/baseline_distribution.py:163-227`, `min_cdd=5.0` ERA5 filter |
| Disclosed in 6-component matching/balance vector for reader transparency | **Keep** | Per-pair vector dump in reporting |
| Formal-analysis eligibility gate | **Removed from spec** (was spec-locked, never implemented) | Delete `EXPERIMENT_DESIGN.md:146-148, 152, 461` |
| Outcome denominator | **Removed entirely** | Delete `weekly_dollars_per_cdd` helper at `pipeline.py:319-342`; drop `o1_dollars_per_cdd` / `o4_dollars_per_cdd_whole_home` columns |
| Standalone reader-facing diagnostic | **Removed** | Per-pair table reports the 6-component vector that includes CDD; CDD does NOT appear as its own reader-facing line item |
| Sensitivity analysis basis | **NOT added** (per Chris lock #2) | No `cooling_relevant_subset`, no `$/CDD as sensitivity` |

## 3. Revised outcome hierarchy (locked)

### Primary (matched-pair median Δ Arm B − Arm A with bootstrap 95% CI, plus % relative to Arm A side-by-side)

1. **Weekly HVAC actual dollars** (`em:2 + em:8 + em:9` from Refoss). `Σ_h hvac_kwh_h × (supply_¢_h + dtod_¢_h) / 100`.
2. **Weekly whole-home actual dollars** (Eagle canonical; Refoss mains backup with drift check). Same formula, different source.
3. **O2 Layer 1 capacity-charge $ delta** (current Stage 6 output, unchanged).

### Secondary (mechanism)

4. **Weekly HVAC actual kWh** (Refoss HVAC channel sum).
5. **Weekly whole-home actual kWh** (Eagle canonical; Refoss mains backup).
6. **Weekly peak HVAC kW** (current O3, unchanged).

Purpose of kWh secondaries: show mechanism. Readers need to distinguish "saved money by using less energy" from "saved money by shifting usage into cheaper / lower-risk hours." Two arms with identical actual kWh but different $ → savings came from timing. Different kWh → savings came from reduction.

### Whole-home source resolution (Eagle vs Refoss mains)

Eagle is the smart-meter HAN feed; its 30-second cadence supports per-hour RTP/DTOD cost calculation. Treat Eagle as the canonical whole-home energy source. Refoss mains (`em:1 + em:7`) remains the sanity cross-check / backup. Do NOT silently average the two.

Drift check: emit a provenance/reason output when Eagle and Refoss mains diverge beyond a threshold (TBD; recommend 5% on weekly kWh as a starting value, finalized during implementation). Drift triggers investigation of channel mapping, time alignment, packet gaps, CT calibration, or meter-feed semantics. Do NOT swallow drift silently.

### O6 — redefined as all-day load-shift / rebound diagnostic

The old O6 (kWh during 18:00-23:59 normalized by daily mean temp delta from 75°F) is wrong-framed for this experiment: it implies cooling efficiency, normalization by CDD, evening-only recovery. Arm B shifts load **all day** in response to prices, weather, and 5CP risk — not only in the evening recovery window.

Redefine O6 as load-shift / rebound diagnostic with these sub-components:

- **HVAC kWh and HVAC $ by price tier and window** — using the controller's locked price-overlay thresholds:
  - Normal: `< 10¢/kWh`
  - Elevated: `≥ 10¢/kWh, < 20¢/kWh`
  - Scarcity: `≥ 20¢/kWh`
- **HVAC kWh and HVAC $ during 5CP-active windows** (driven by `hvac.5cp_state.is_active`).
- **Post-event rebound kWh / $** — energy consumed in a defined window following price-release or 5CP-release transitions. Did the load actually move, or did it just delay and reappear?
- **(Optional) Pre-cool kWh / $ before forecasted hot or expensive windows** — energy consumed in advance of the trigger.

Purpose: explain whether Arm B actually moved HVAC load away from expensive / risky periods, AND whether that load reappeared later. O6 is diagnostic / context — not the primary outcome.

Price-tier thresholds match the controller's existing price-overlay rule values (10¢ elevated, 20¢ scarcity per `EXPERIMENT_DESIGN.md:99-102`). No new analysis-only cutoffs invented. Record the exact thresholds in provenance/spec so the diagnostic is tied to the controller rule being evaluated.

### Context / diagnostics (reported but not effect-size-tested)

- 6-component weather vector per pair (CDD disclosed inside this, not as standalone line item)
- Indoor temperature distribution per arm
- Within-day kWh / $ profile (current O5)

### Headline reporting structure

For each cooling season:
- **Primary headline:** matched-pair median Arm B − Arm A actual $ delta with bootstrap 95% CI, presented side-by-side with the percent-of-Arm-A figure for reader intuition.
- **Do not** present percent as the only headline — denominator matters; the absolute $ figure is the trustworthy primary.

### Sensitivities (carry §7 list with reinterpretation)

- `euclidean_zscore` — alternative matching metric. Unchanged.
- `include_washout` — keeps meaning under actual-$ primary.
- `em2_em8_only` — applies to actual-$ HVAC outcomes now.
- `five_min_pricing` — applies to actual-$ HVAC outcomes now.
- `day_of_week` — descriptive stratification. Reinterpret to use Stage 8 daily actual-$ outcomes.
- `threshold_robustness` — Stage 8 rerun. Unchanged.

Explicitly NOT added: `$/CDD as sensitivity`, `cooling_relevant_subset`, any new CDD-derived metric.

## 4. Minimal migration path

### Stays unchanged

- Stage 1 extract infrastructure (added measurement: `eagle.meter`).
- Stage 2 rules 1-10 (with washout added per separate plan; new Eagle coverage rule for whole-home).
- Stage 4 matching: 6-D weather vector + Mahalanobis-Hungarian + T=2.5 threshold + baseline covariance.
- Stage 5 bootstrap CI machinery + stationary bootstrap + PRNG seed.
- Stage 6 O2 reconstruction.
- Stage 7 SCED randomization machinery.
- Stage 8 decomposition (already on actual-daily-$ pattern).
- `baseline_cov.npz` covariance file.

### Schema changes

- `WEEKLY_CSV_LOCKED_COLUMNS` (`pipeline.py:1993-1998`):
  - **Add:** `weekly_hvac_dollars`, `weekly_whole_home_dollars`, `weekly_hvac_kwh`, `weekly_whole_home_kwh`.
  - **Remove (Phase 2):** `o1_dollars_per_cdd`, `o4_dollars_per_cdd_whole_home`.
  - **Keep:** `o3_peak_hvac_kw`, all 6 weather-vector components.
- `STAGE5_OUTCOMES` (`pipeline.py:2454-2458`) — replace with the new primary + secondary list. Effects.csv adds `percent_of_arm_a` column.

### Test changes

- ~20 sites grep-counted across `test_pipeline.py`, `test_pipeline_end_to_end.py`, `test_pipeline_realshape_e2e.py`, `test_stage8_loader_realshape.py` reference `o1_dollars_per_cdd` / `o4_dollars_per_cdd_whole_home`. Refresh in Phase 2.
- The misleading docstring at `pipeline.py:330-334` ("Stage 2 already gates ... cooling-relevance") disappears with the `weekly_dollars_per_cdd` helper deletion.

### Doc changes (single Phase 0 amendment commit per Chris lock)

- `docs/EXPERIMENT_DESIGN.md` §2 — rewrite O1, O4. Add kWh secondaries. Redefine O6 as load-shift / rebound diagnostic.
- `docs/EXPERIMENT_DESIGN.md` §4 lines 146-148 — delete cooling-relevance criterion paragraph.
- `docs/EXPERIMENT_DESIGN.md` §4 line 152 — drop "passes the cooling-relevance criterion above AND".
- `docs/EXPERIMENT_DESIGN.md` §6 line 230+ — rewrite "Primary metric" header and body for actual-$.
- `docs/EXPERIMENT_DESIGN.md` §7 line 271, 293 — strike cooling-relevance wording.
- `docs/EXPERIMENT_DESIGN.md` §8 line 362 — rewrite Headline bullet (actual-$ + % side-by-side).
- `docs/EXPERIMENT_DESIGN.md` §13 — delete commitment #4 (cooling-relevance); rewrite commitment #8 (drop $/CDD references); add commitment lines for the new actual-$ outcomes and Eagle canonical whole-home source. ONE amendment commit per Chris lock.
- `docs/ANALYSIS_PIPELINE.md` §2.1 — add `eagle.meter` to input measurements table.
- `docs/ANALYSIS_PIPELINE.md` Stage 1, Stage 3, Stage 5 descriptions.
- `docs/OSF_FILING.md:295-296` — rewrite cooling-relevance filter wording.

### Plans to retire / revise

- `docs/plans/cooling-relevance-primary-gate-plan.md` — **superseded.** Status updated, file retained as evidence.
- `docs/plans/washout-primary-exclusion-plan.md` — **retain and revise** post-migration. Mechanics amendments still apply.
- Future Stage 9 plan — restart post-washout from `~/.claude/projects/D--Projects-energy-proxy/memory/project-stage9-decision-locks-2026-05-12.md` + the recalibrated outcome list.

## 5. Design questions for Chris (post-rev2 locks)

Answered by 2026-05-12 locks (no further input needed):
- ~~Q1 Drop $/CDD entirely~~ → **YES, dropped everywhere.**
- ~~Q2 Keep cooling-relevance as sensitivity subset~~ → **NO.**
- ~~Q3 O1 = HVAC actual $, O4 = whole-home actual $~~ → **YES.**
- ~~Q4 Add actual-kWh as secondary outcomes~~ → **YES.**
- ~~Q5 O6 normalization~~ → **REDEFINED as load-shift/rebound diagnostic.**
- ~~Q6 §13 commitment-list rewrites staged or single~~ → **SINGLE Phase 0 amendment commit.**
- ~~Q7 OSF deadline~~ → **HELD at 2026-05-30; staffing = focused hours.**
- ~~Q8 Allcott % framing~~ → **YES, actual $ primary AND % relative to Arm A side-by-side.**

Still open (tactical, Phase 0 or Phase 1 implementation-time decisions, NOT pre-Phase-0 blockers):

1. **Eagle vs Refoss mains drift threshold.** Recommend start at 5% weekly kWh; finalize during Phase 1 after looking at a few weeks of real Eagle data to characterize typical noise.
2. **Eagle whole-home energy formula choice.** Two options:
   - (a) Mean(`demand_kw`) per hour × 1 hour = hourly kWh. Mirrors Refoss approach.
   - (b) Differential `delivered_kwh` per hour from the cumulative totalizer. Direct meter-true integration; reconciles best against the ComEd bill.
   Both supported by Eagle's 30-second cadence. Recommend (b) for bill reconciliation alignment.
3. **O6 sub-component scope for Phase 0/1.** Phase 0 spec defines all four sub-bullets. Phase 1 tracer implements which subset? Recommend: HVAC $ by price tier + HVAC $ during 5CP-active windows in Phase 1; rebound and pre-cool diagnostics in a follow-on if time allows.

## 6. Code vs controller separation

Nothing in this audit suggests the controller is broken.

- **Arm A** (`EXPERIMENT_DESIGN.md:64-83`) — no code change implied.
- **Arm B** (`EXPERIMENT_DESIGN.md:85-122`, implemented in `deploy/energy-stack/hvac-scheduler/`) — pre-cool logic, day-type classification, price-overlay tiers, 5CP detection, layer-priority resolution, safety supervisor all correct.
- The `pjm_5cp.py` verified-preferred fix from priority queue item #3 is a separate live-scheduler operational fix. Out of scope.
- Telemetry (refoss, comed.prices, hvac.*, nws.forecast, ecowitt.weather, pjm.*, **eagle.meter**, hvac.thermostat) — collected as designed. The only telemetry gap is that `eagle.meter` is not yet consumed by the analysis pipeline; the controller and meter feed are fine.

The recalibration is entirely in: (a) the spec's outcome definitions, (b) the analysis pipeline's outcome computation, (c) related tests and docs, (d) Stage 1's input measurement list (add Eagle). Controller and measurements are correct.

## Next-step decision points (not yet acted on)

1. Chris reviews this Rev2 audit + the revised migration plan.
2. Phase 0 begins (spec amendment as single commit).
3. Phase 1 tracer (add actual-$ + actual-kWh columns, Eagle wiring).
4. Phase 2 (drop $/CDD, test cascade refresh).
5. Phase 3 (replay re-run).
6. Then: washout PR (revised), then Stage 9.
