---
date: 2026-05-12
owner: chris
status: draft-pending-review-rev2
role-label: chris
---

# Actual-dollar outcomes migration plan

## Why

Per audit `docs/plans/pipeline-recalibration-audit-2026-05-12.md` + Chris locks 2026-05-12.

This experiment is about whether a smart RTP/DTOD/5CP-aware HVAC controller saves **actual money** compared with a standard programmable/set-it-and-forget-it thermostat in this house, under comparable weather/conditions. The old analysis design centered `$/CDD`, which is misaligned with that question. Separately, the spec and code also had gaps — washout specified but missing, CDD gate specified but missing. This recalibration removes the CDD gate from the spec instead of implementing it, drops `$/CDD` entirely from the analysis, and replaces it with actual-dollar primary outcomes.

CDD remains only as one component of the Stage 4 weather-matching vector and as part of the existing baseline covariance calibration. It does NOT appear as an outcome denominator, eligibility gate, or standalone reader-facing diagnostic.

## Locked outcome hierarchy

### Primary (matched-pair median Δ Arm B − Arm A with bootstrap 95% CI, plus % relative to Arm A side-by-side)

1. **Weekly HVAC actual dollars** — Refoss `em:2 + em:8 + em:9`, `Σ_h hvac_kwh_h × (supply_¢_h + dtod_¢_h) / 100`.
2. **Weekly whole-home actual dollars** — Eagle canonical, Refoss mains backup, drift check.
3. **O2 Layer 1 capacity-charge $ delta** — current Stage 6 output, unchanged.

### Secondary (mechanism)

4. **Weekly HVAC actual kWh** — Refoss HVAC channel sum.
5. **Weekly whole-home actual kWh** — Eagle canonical, Refoss mains backup.
6. **Weekly peak HVAC kW** — current O3, unchanged.

### O6 (redefined, diagnostic)

All-day load-shift / rebound diagnostic, NOT efficiency / CDD / temp-delta / evening-only:
- HVAC kWh and HVAC $ by price tier (`< 10¢` normal / `≥ 10¢` elevated / `≥ 20¢` scarcity — controller-locked thresholds)
- HVAC kWh and HVAC $ during 5CP-active windows
- Post-event rebound kWh / $ after price-release or 5CP-release
- (Optional) pre-cool kWh / $ before forecasted hot or expensive windows

### Headline reporting

Actual $ delta is the primary headline. Percent relative to Arm A matched cost is reported side-by-side for reader intuition. Percent is NOT the sole headline — denominator matters.

## Locked decisions

| Question | Decision | Rationale / source |
|---|---|---|
| $/CDD as any outcome | **Dropped entirely.** No primary, secondary, descriptive, sensitivity. | Chris core lock 2026-05-12; literature audit found 0 of 58 cited sources use $/CDD. |
| `weekly_dollars_per_cdd` helper | **Deleted** (and the misleading docstring at `pipeline.py:330-334` goes with it). | Helper has no remaining call site after Phase 2. |
| Cooling-relevance eligibility gate | **Removed from spec.** Not implemented in code; never will be. | Low CDD ≠ invalid data. Stage 4 matching on the full 6-D vector handles comparability. |
| Cooling-relevance subset sensitivity | **Not added.** | Chris lock #2 — no new CDD-based descriptor or subset. |
| CDD in matching vector | **Kept.** | Component 1 of the 6-D weather vector. Disclosed in matching/balance reporting only. |
| CDD as standalone reader-facing diagnostic | **Removed.** | Per Chris: CDD shown inside the 6-D vector explanation of matching, not as its own line item. |
| Baseline covariance calibration | **Unchanged.** `min_cdd=5.0` filter on ERA5 stays unless separately revisited. | Calibration-set choice, not primary-analysis filter. Locked at OSF lock. |
| Actual-kWh secondaries (HVAC + whole-home) | **Yes, secondary.** | Show mechanism — "used less" vs "smarter timing". |
| Whole-home canonical source | **Eagle** (HAN smart meter). Refoss mains = sanity cross-check / backup. No silent averaging. | Eagle is the meter; bills reconcile against it. Refoss mains for drift detection. |
| Eagle vs Refoss drift handling | **Provenance/reason emission + investigate** when drift exceeds threshold. Don't swallow drift silently. **Threshold locked at ≥10% weekly kWh delta** per Phase 1.0 verification at `docs/replay-validation/2026-05-12-eagle-shape-verification/findings.md` (weekly drift in the 7-day spring window: 0.193%; daily range 0.21%-2.77%; the 10% threshold is ~50× the observed weekly noise floor). drift_pct = `abs(refoss_mains_kwh - eagle_kwh) / eagle_kwh × 100`; Eagle as denominator because Eagle is canonical. |
| O3 (weekly peak HVAC kW) | **Unchanged.** | Already actual physical quantity. |
| O6 | **Redefined** as all-day load-shift / rebound diagnostic. NOT efficiency / CDD / temp-delta / evening-only. | Arm B shifts load all day; O6 must measure that, not evening recovery. |
| O6 price-tier thresholds | **10¢ elevated / 20¢ scarcity** — match the controller's locked overlay rule. No new analysis cutoffs. | Diagnostic is tied to the controller rule being evaluated. |
| Stage 8 outcome names | **Unchanged.** Already actual-daily-$. | Naming continuity. |
| §13 commitment-list rewrites | **Single Phase 0 amendment commit.** | One conceptual recalibration; not a drip series. |
| Headline reporting | **Actual $ delta primary; % relative to Arm A side-by-side. NOT percent-only.** | Denominator matters. |
| OSF deadline | **2026-05-30 held.** Staffing = focused hours. But do not trade correctness for speed if migration exposes another foundation issue. | Chris lock 2026-05-12 (memory `feedback-hold-deadlines-invest-hours`). |

## Spec sites to amend (Phase 0)

- `docs/EXPERIMENT_DESIGN.md:40` — O1 redefinition (actual HVAC $).
- `docs/EXPERIMENT_DESIGN.md:48` — O4 redefinition (actual whole-home $).
- `docs/EXPERIMENT_DESIGN.md:50` — O5 wording sweep ("cooling-relevant weeks" framing).
- `docs/EXPERIMENT_DESIGN.md:52` — O6 redefinition.
- `docs/EXPERIMENT_DESIGN.md:142, 144, 146-148` — cooling-relevance criterion deletion.
- `docs/EXPERIMENT_DESIGN.md:152` — strike "passes the cooling-relevance criterion above AND".
- `docs/EXPERIMENT_DESIGN.md:230-235` — "Primary metric: O1" header + body rewrite.
- `docs/EXPERIMENT_DESIGN.md:271` — strike cooling-relevance wording in bootstrap-CI description.
- `docs/EXPERIMENT_DESIGN.md:277` — "constructed analogously to O1" wording sweep.
- `docs/EXPERIMENT_DESIGN.md:279` — O5 cooling-relevant-week wording.
- `docs/EXPERIMENT_DESIGN.md:281` — O6 redefinition.
- `docs/EXPERIMENT_DESIGN.md:293` — "after washout exclusion and cooling-relevance filtering" rewrite.
- `docs/EXPERIMENT_DESIGN.md:326` — "cooling-relevant day" wording sweep.
- `docs/EXPERIMENT_DESIGN.md:362` — Headline reporting bullet (actual $ + % side-by-side).
- `docs/EXPERIMENT_DESIGN.md:366` — O5 framing.
- `docs/EXPERIMENT_DESIGN.md:461` — DELETE pre-reg commitment #4 (cooling-relevance criterion).
- `docs/EXPERIMENT_DESIGN.md:465` — REWRITE pre-reg commitment #8 (drop $/CDD parts; add actual-$ + Eagle canonical).
- `docs/ANALYSIS_PIPELINE.md` §2.1 measurements table — add `eagle.meter` row.
- `docs/ANALYSIS_PIPELINE.md:147-155` — Stage 3 outcome construction rewrite.
- `docs/ANALYSIS_PIPELINE.md:184-190` — Stage 5 outcome list rewrite.
- `docs/OSF_FILING.md:295-296` — strike cooling-relevance filter wording.

## Code sites to change

- `tools/analysis/pipeline.py:1993-1998` — `WEEKLY_CSV_LOCKED_COLUMNS`. Phase 1 adds new columns; Phase 2 removes `o1_dollars_per_cdd` + `o4_dollars_per_cdd_whole_home`.
- `tools/analysis/pipeline.py:2001-2068` — `_compute_weekly_row`. Phase 1 adds new computations; Phase 2 removes $/CDD ones.
- `tools/analysis/pipeline.py:319-342` — `weekly_dollars_per_cdd` helper. Phase 2 deletes (misleading docstring at lines 330-334 goes with it).
- `tools/analysis/pipeline.py:2454-2458` — `STAGE5_OUTCOMES`. Phase 1 appends; Phase 2 removes $/CDD entries.
- `tools/analysis/pipeline.py:2496-2520` — `stage5_effects` adds `percent_of_arm_a` column.
- `tools/analysis/pipeline.py` — new Eagle extraction in Stage 1; new Eagle-derived whole-home aggregation helper; new drift-check helper.
- `tools/analysis/pipeline.py:1622-1659` — `rule8_pi_apply` extension for Eagle coverage (if Eagle absence excludes whole-home outcomes for the week — TBD Phase 0 spec call).
- `tools/analysis/pipeline.py:4548-4630` — Stage 9 outcome names (paused; updates when Stage 9 resumes).

## Phases

### Phase 0 — Spec amendment (single commit, docs only)

PR `feature/spec-recalibration-actual-dollars`, base `main`. One commit per Chris lock on §13 amendments.

Edits per the "Spec sites to amend" list above. The Phase 0 spec ALREADY treats $/CDD as removed and the cooling-relevance gate as deleted. The migration's additive Phase 1 retains $/CDD code columns ONLY as temporary cross-validation scaffolding — the spec never re-endorses them.

Acceptance: doc-level review. No test/code changes. Phase 1 begins after merge.

Estimated workload: 1-2 focused hours.

### Phase 1 — Tracer: add actual-$ + actual-kWh outcomes flowing through Stage 3 → Stage 5; wire Eagle

Outside-in RED tests written first.

**Acceptance tests:**

1. **Weekly HVAC actual $ oracle.** Synthetic week with known per-hour kWh × per-hour supply_¢ × DTOD rate → hand-computed actual HVAC $ within ¢ tolerance. Stage 3 weekly.csv has `weekly_hvac_dollars` matching the oracle. Stage 5 effects.csv emits row with `outcome="weekly_hvac_dollars"`.
2. **Weekly whole-home actual $ oracle (Eagle).** Synthetic Eagle `delivered_kwh` totalizer values → hand-computed hourly differentials → hand-computed actual whole-home $. Stage 3 weekly.csv has `weekly_whole_home_dollars`. Stage 5 effects.csv emits row.
3. **Eagle vs Refoss mains drift detection.** Synthetic week where Eagle and Refoss mains diverge by X%. Verify drift flag fires in provenance output. Verify primary outcome uses Eagle (not silently averaged).
4. **kWh secondaries.** Stage 3 weekly.csv has `weekly_hvac_kwh` and `weekly_whole_home_kwh` columns with hand-computed oracle values. Stage 5 reports them.
5. **`percent_of_arm_a` column.** Stage 5 effects.csv has a `percent_of_arm_a` column populated for O1 and O4 dollar outcomes only; O2 reports absolute dollar delta only outside Stage 5 (O2 lives in Stage 6, with bootstrap over PJM 5CP hours under each arm rather than matched weekly pairs, so the O1/O4 denominator does not transfer).

**Implementation:**

1. Add `eagle.meter` to `docs/ANALYSIS_PIPELINE.md §2.1` and to Stage 1 extraction. Verify it lands in `out/stage1/eagle.meter.parquet`.
2. New helper `weekly_actual_dollars(hourly_records)` (mirrors `weekly_dollars_per_cdd` shape, no CDD division).
3. New helper `eagle_hourly_kwh_from_delivered(eagle_df, week_start_ct)` — differential of cumulative `delivered_kwh` per hour over the week. Returns 168 hourly kWh values.
4. New helper `eagle_refoss_mains_drift(eagle_kwh_weekly, refoss_mains_kwh_weekly) -> dict` — returns `{"drift_pct": float, "exceeds_threshold": bool}`.
5. Extend `WEEKLY_CSV_LOCKED_COLUMNS` to add new columns (additive; $/CDD columns retained for cross-validation).
6. Extend `_compute_weekly_row` to populate new columns.
7. Append new outcomes to `STAGE5_OUTCOMES` (don't remove $/CDD ones yet).
8. Add `percent_of_arm_a` column to Stage 5 effects.csv emission.
9. Provenance sidecar at `stage3/provenance.json` (new) records Eagle-vs-Refoss drift per week.
10. Unit tests + 5 integration oracles.

**$/CDD columns in Phase 1 are scaffolding only.** They allow tests to assert "new HVAC $ × matched-pair median / week_cdd ≈ old $/CDD" as a cross-check. They are NOT a supported, reportable, or endorsed analysis outcome at any layer.

Estimated workload: 4-6 focused hours.

### Phase 2 — Drop $/CDD columns + test cascade refresh

After Phase 1 lands and tests are green.

1. Remove `o1_dollars_per_cdd` and `o4_dollars_per_cdd_whole_home` from `WEEKLY_CSV_LOCKED_COLUMNS`, `_compute_weekly_row`, `_empty_weekly_row`.
2. Remove same from `STAGE5_OUTCOMES`.
3. Delete `weekly_dollars_per_cdd` helper at `pipeline.py:319-342` (misleading docstring deletes with it).
4. Refresh every test that references the dropped columns. Grep estimate: ~20 test sites across `test_pipeline.py`, `test_pipeline_end_to_end.py`, `test_pipeline_realshape_e2e.py`, `test_stage8_loader_realshape.py`. Each gets updated to assert against actual-$ columns.
5. Stage 4 unchanged. Stage 6 unchanged. Stage 7 inherits Stage 5 outcome list automatically. Stage 8 unchanged (already actual-daily-$). Stage 9 stub unchanged this PR.
6. Sweep `docs/` and `tools/` for stragglers: `dollars_per_cdd`, `$/CDD`, `o1_dollars_per_cdd`, `o4_dollars_per_cdd`. Delete or rewrite each.

Estimated workload: 6-10 focused hours (test cascade is the variable).

### Phase 3 — Replay re-run + downstream verification

After Phase 2 lands.

- Replay 7d + 90d bundles on Pi-lab. Pre-randomization bundle still produces reason-coded empty Stage 4/5 outputs; the change is purely schema (column names) on synthetic-fixture e2e tests. Realshape numerics will shift to actual-$ for any weeks that DO qualify.
- Confirm `make_filing_bundle.py` tarball reflects new outcomes (Stage 5 effects.csv columns named correctly).
- Update `docs/replay-validation/` per Stage 8 close-out pattern.

Estimated workload: 1-2 focused hours.

**Expected 12-20 focused hours if Eagle schema and cadence match assumptions; keep 2026-05-30 as target, but do not trade correctness for speed if Phase 1 exposes a foundation issue.**

## Out of scope

- **Washout primary-exclusion.** Separate plan at `docs/plans/washout-primary-exclusion-plan.md`. Still needed as a validity exclusion. Re-sequence: washout PR AFTER Phase 2 lands so washout filter operates against the new schema. Mechanics amendments from prior review still apply.
- **CDD ≥ 5 cooling-relevance gate plan.** Superseded by this migration. Plan file marked superseded.
- **Stage 9 sensitivity loader implementation.** Paused; resumes after washout lands.
- **pjm_5cp.py verified-preferred fix.** Live-scheduler operational fix, unrelated.
- **O6 sub-component scope inside Phase 1.** Recommend HVAC $ by price tier + HVAC $ during 5CP windows in Phase 1; rebound and pre-cool diagnostics in a follow-on if time allows. Phase 0 spec still defines all four.

## Risks

- **OSF clock held at 2026-05-30** per Chris lock. Budget: 12-20 focused hours, conditional on Eagle schema + cadence matching assumptions. If Phase 1 surfaces another foundation issue, surface and address it; do not trade correctness for speed. Recent calibration: the washout/CDD-gate/$/CDD chain started as "small fixes" and turned out to be load-bearing on the whole analysis design. Stay alert for the same pattern.
- **Schema break.** `WEEKLY_CSV_LOCKED_COLUMNS` is declared locked schema; Phase 2 removes columns. No known external consumers pre-OSF.
- **Eagle data verification.** Eagle has been running in production but is not yet consumed by the analysis pipeline. Phase 1 must verify (a) `eagle.meter` is present in real bundles, (b) `delivered_kwh` is monotonic + accurate, (c) cadence supports per-hour differentials, (d) what Eagle-side gaps look like and how Rule-1-style coverage handling applies. If Eagle data is bad in real bundles, the whole-home outcomes are blocked.
- **Stage 4 outcome-independence claim.** Verified during audit: Stage 4 reads `qualifies==True` + 6-D weather vector + writes pair_id / week_a / week_b / distance / quality. Outcome columns never touched. Confirms Phase 2 has no Stage 4 impact.
- **Test cascade volume.** ~20 sites in Phase 2. Mostly mechanical column-name changes. Estimate stays at half a day if grep is exhaustive.

## Tracking

- 3 PRs sequenced, all `--base main`, no stacking.
- Phase 0 = `feature/spec-recalibration-actual-dollars` (docs only, single commit)
- Phase 1 = `feature/actual-dollar-outcomes-tracer` (additive code + Eagle wiring)
- Phase 2 = `feature/drop-dollars-per-cdd` (subtractive + test cascade)
- Phase 3 = replay re-run on Pi-lab, no PR (documented in `docs/replay-validation/`)
- Archive this plan to `docs/plans/archive/actual-dollar-outcomes-migration-plan.md` after Phase 2 merges.

## Open questions (tactical, NOT pre-Phase-0 blockers)

1. **Eagle whole-home energy formula.** Recommend (b) differential of `delivered_kwh` totalizer for bill-reconciliation alignment, but (a) mean(`demand_kw`) × hour mirrors Refoss pattern. Decide during Phase 1 after looking at Eagle data shape.
2. ~~Eagle-vs-Refoss drift threshold value.~~ **Locked at ≥10% weekly kWh delta** per Phase 1.0 verification (see `docs/replay-validation/2026-05-12-eagle-shape-verification/findings.md`).
3. **Eagle absence treatment.** If Eagle is missing for a week, do whole-home outcomes drop with a reason code (Stage 8-style per-output gating), or does the week fail Rule-8 qualifying-days entirely? Recommend per-output reason-code drop (don't fail the whole week for a whole-home-only gap).
4. **O6 sub-component Phase 1 scope.** Recommend HVAC $ by price tier + HVAC $ during 5CP windows first; rebound/pre-cool diagnostics second if time allows. Phase 0 spec still defines all four for completeness.
