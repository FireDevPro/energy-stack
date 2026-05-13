---
date: 2026-05-12
owner: chris
status: superseded-by-rebaseline-2026-05-13
role-label: chris
superseded-by: docs/plans/analysis-rebaseline-plan-2026-05-13.md
superseded-by-historical: docs/plans/archive/pipeline-recalibration-audit-2026-05-12.md, docs/plans/archive/actual-dollar-outcomes-migration-plan.md
---

> **SUPERSEDED 2026-05-12 (initially) and again 2026-05-13 (rebaseline).** Preserved as evidence. The CDD ≥ 5 gate was first ruled out by the recalibration audit (drop the gate from the spec rather than implement it); now the entire migration approach that decision was part of is being rebaselined. Source verification recorded below remains correct evidence; the recommended action was twice-superseded.

# Cooling-relevance primary-gate fix — execution plan [SUPERSEDED]

## Why this exists

`docs/EXPERIMENT_DESIGN.md:146` defines the cooling-relevance criterion (`weekly CDD ≥ 5`) as the FIRST gate for formal Stage 4/5 inference, applied in series with the §4 data-quality rules. `docs/EXPERIMENT_DESIGN.md:461` lists it as pre-reg commitment #4 — binding once filed to OSF. Verified absent from the primary pipeline. The misleading docstring at `tools/analysis/pipeline.py:330-334` caused prior reading to falsely conclude the gate was enforced upstream.

Discovered during washout-plan verification on 2026-05-12. Sequenced before the washout fix because cooling-relevance defines which weeks are eligible for formal Stage 4/5/8 analysis at all; washout is a within-eligible-week filter.

This PR closes the cooling-relevance gap. Washout and Stage 9 stay paused behind it.

## Spec anchors

- `docs/EXPERIMENT_DESIGN.md:146` — locked definition: weekly CDD (base 65°F) ≥ 5 → formal analysis; below → descriptive only. "Threshold is pre-committed before OSF and not adjusted post-hoc."
- `docs/EXPERIMENT_DESIGN.md:152` — "A week qualifies ... if it passes the cooling-relevance criterion above AND every gate below" — cooling-relevance precedes Rules 1-10.
- `docs/EXPERIMENT_DESIGN.md:271` — bootstrap CI computed over "qualifying weeks the cooling-relevance criterion and §4 data-quality gates produce" — distinct concepts, both required.
- `docs/EXPERIMENT_DESIGN.md:293` — primary matched-pair input set is "after washout exclusion and cooling-relevance filtering."
- `docs/EXPERIMENT_DESIGN.md:461` — pre-reg commitment #4 (binding).
- `docs/OSF_FILING.md:295-296` — filing claim mirrors the spec wording.

## Source verification (recorded 2026-05-12)

- `tools/analysis/pipeline.py:291-298` (`weekly_cdd`): computes the sum. No threshold.
- `tools/analysis/pipeline.py:330-334` (`weekly_dollars_per_cdd` docstring): **CONTAINS A FALSE CLAIM** that Stage 2 gates cooling-relevance. Stale comment; must be corrected.
- `tools/analysis/pipeline.py:583-587` (`QUALIFYING_WEEKS_LOCKED_COLUMNS`): no `cooling_relevant` column. The `qualifies` column carries data-quality semantics only.
- `tools/analysis/pipeline.py:1622-1659` (`rule8_pi_apply`): enforces ≥5 qualifying DAYS — completely different gate.
- `tools/analysis/pipeline.py:2382-2451` (`stage4_matching`): filters on `row["qualifies"].lower() in ("true", "1")` only. No CDD check.
- `tools/analysis/pipeline.py:2400-2402` (`unmatched_weeks.csv` writer): already has `reason` column with values like `"unmatched_size"`. New `"not_cooling_relevant"` slots in cleanly.
- `tools/analysis/pipeline.py:2461-2493` (`_compute_pair_diffs`): reads `matched_pairs.csv`. Stage 5 inherits Stage 4's filter via this path.
- `tools/analysis/pipeline.py:3611-3616` (`_load_qualifying_days_from_stage2_stage3`): Stage 8 qualifying-weeks set built from `qualifies==True` rows. **No `weekly_cdd` check.**
- `tools/analysis/baseline_distribution.py:163-227`: `min_cdd: float = 5.0` filter exists, but ONLY for ERA5 baseline-covariance computation. Not primary.
- Repo-wide grep `MIN_CDD|min_cdd|cdd_threshold|is_cooling_relevant` in primary pipeline: zero matches.

### Test-coverage verification (recorded 2026-05-12)

- `tools/analysis/tests/test_pipeline.py:1244-1262`: arithmetic-only `weekly_cdd` tests. No threshold assertion.
- `tools/analysis/tests/test_pipeline.py:1394-1409`: tests Stage 3's "never re-derives quality" boundary invariant. **Currently prohibits Stage 3 from overriding `qualifies` based on CDD** — locks Option C out.
- `tools/analysis/tests/test_pipeline_end_to_end.py:356` + `test_pipeline_realshape_e2e.py:163`: `weekly_cdd > 0` checks. Non-zero, not ≥5.
- `tools/analysis/tests/test_stage8_loader_realshape.py:110-112, 199-201`: **Stage 8 fixture pins `weekly_cdd="0"` AND `qualifies="True"`.** Test comment: "Stage 3 weekly.csv: same week qualifies, weekly_cdd=0 so the zero-CDD-still-counted property holds (Stage 8 ignores CDD)." **The test was written assuming the gap.** Will break under the fix. Must refresh.
- `tools/analysis/tests/test_baseline_distribution.py:70`: baseline-side only.
- No test asserts `weekly_cdd < 5` week is excluded from Stage 4 / Stage 5 / Stage 8 formal inputs. Exhaustively confirmed.

## Locked decisions

| Question | Decision |
|---|---|
| Threshold value | **`MIN_WEEKLY_CDD = 5.0`.** Per `docs/EXPERIMENT_DESIGN.md:146` and pre-reg commitment #4. Inclusive (`>=`); 4.99 excluded, 5.00 included. |
| Where the gate lives | **Stage 4 + Stage 8 each filter at their qualifying-weeks loading step.** Stage 5 + Stage 7 + Stage 9 inherit via matched_pairs.csv / weekly.csv chains. Stage 3 boundary rule preserved (weekly.csv keeps the row for descriptive output). |
| Why not Stage 2 | Stage 2 doesn't compute weekly CDD; adding it duplicates Stage 3. Awkward layering. |
| Why not Stage 3 (set `qualifies=False`) | Violates the documented boundary rule at `tools/analysis/pipeline.py:2019-2020` + the test at `test_pipeline.py:1394-1409`. Conflates two distinct exclusion reasons into one column. |
| Why not new column in weekly.csv | Schema change to locked CSV is heavier than necessary given current PR scope and OSF deadline. |
| Reason taxonomy | Two distinct artifacts:<br>• **Stage 2 data-quality failure** → `qualifies=False` in weekly.csv with `exclusion_reason` populated (existing).<br>• **Cooling-irrelevant week** → `qualifies=True`, `weekly_cdd < 5.0`, EXCLUDED at Stage 4 with `reason="not_cooling_relevant"` in `unmatched_weeks.csv`; EXCLUDED at Stage 8 with a new entry in `provenance.json`'s `cooling_irrelevant_weeks` list and a `reason_report.json` entry. |
| New `ReasonCode` enum entry | **Add `NOT_COOLING_RELEVANT`** to `tools/analysis/replay/reason_codes.py` for use by Stage 4 + Stage 8 reason reports. (Distinct from the washout decision because cooling-relevance triggers stage-level empty-output reporting paths the way other formal-gate failures do.) Use the string `"not_cooling_relevant"` for `unmatched_weeks.csv reason` column where the existing convention is bare strings. |
| Helper centralization | Single `is_cooling_relevant(weekly_cdd_value: float) -> bool` helper in `pipeline.py`, plus the `MIN_WEEKLY_CDD = 5.0` constant. Both Stage 4 and Stage 8 call the helper. Single source of truth. |
| Stage 3 boundary | Unchanged. weekly.csv carries every Stage-2-qualifying row regardless of CDD. Cooling-irrelevant weeks are visible for descriptive purposes (the same "reported descriptively only" the spec mandates). |
| Stage 8 zero-CDD-day property | **Preserved.** The spec's "daily DOLLARS, NOT $/CDD, so zero-CDD grid-event days remain in" (`docs/EXPERIMENT_DESIGN.md:334`) applies to DAYS within a COOLING-RELEVANT WEEK. The new gate operates at WEEK level. A cooling-relevant week (CDD ≥ 5) may still contain individual zero-CDD-temp days — those days enter Stage 8 decomposition. A cooling-irrelevant week (CDD < 5) is excluded entirely. |
| Stage 8 test fixture pinning `weekly_cdd=0` | **Refresh.** The current fixture at `test_stage8_loader_realshape.py:199-201` is spec-incorrect; weeks with `weekly_cdd=0` should never reach Stage 8. Rebuild the fixture with `weekly_cdd >= 5` and exercise the day-level zero-CDD property within that. |
| Stale docstring at `pipeline.py:330-334` | **Correct in this PR.** Replace "Stage 2 already gates ... via the cooling-relevance criterion" with the truth: the gate lives at Stage 4 and Stage 8 boundaries. |
| Stage 9 inheritance | Stage 9 sensitivities derive from Stage 4 matched_pairs.csv (#1, #2 reusing primary matches) and Stage 8 (#6 threshold_robustness). Both inherit the gate. No Stage 9 code change required. |

## Phases

One PR. Surgical: 2 filter sites, 1 helper, 1 enum entry, 1 docstring correction, ~5 tests.

### Phase 0 — RED feature acceptance tests

Outside-in. The five acceptance tests Chris specified, written first.

1. **Boundary at 4.99 vs 5.00.** Two-fixture pair: weekly fixtures `(A, weekly_cdd=4.99, qualifies=True)` and `(B, weekly_cdd=5.00, qualifies=True)`.
   - `stage4_matching` output: `matched_pairs.csv` does NOT contain a pair using the 4.99 week. `unmatched_weeks.csv` contains the 4.99 week with `reason="not_cooling_relevant"`. The 5.00 week IS matchable.
2. **Stage 3 still records the row.** `stage3/weekly.csv` contains both the 4.99 and the 5.00 weeks with their full schema columns. `qualifies` is True for both (Stage 2 passed them on data-quality grounds; cooling-relevance is enforced downstream).
3. **Reason taxonomy distinguishes.** Fixture has three weeks:
   - W1: Stage 2 failed (`qualifies=False, exclusion_reason="refoss_imputation_too_high"`).
   - W2: Stage 2 passed, `weekly_cdd=4.0` (cooling-irrelevant).
   - W3: Stage 2 passed, `weekly_cdd=10.0` (cooling-relevant).
   Oracle: weekly.csv row W1 has `qualifies=False`. Row W2 has `qualifies=True, weekly_cdd=4.0`. Row W3 has `qualifies=True, weekly_cdd=10.0`. Stage 4 `unmatched_weeks.csv` contains W2 with `reason="not_cooling_relevant"`. Stage 4 reason_report.json (or matched_pairs result) does NOT list W1 — W1 was already filtered by the `qualifies==True` gate before cooling-relevance was even checked.
4. **Stage 5 excludes cooling-irrelevant.** Effects.csv computed only over pairs that survive Stage 4. The 4.99 week never contributes to a pair_diff.
5. **Stage 8 excludes cooling-irrelevant weeks.** Fixture: one cooling-relevant week (CDD=10) with mixed day-CDD values (some days 0, some days hot) → Stage 8 decomposition emits day-level rows for all qualifying days in that week. Add a parallel cooling-irrelevant week (CDD=4) → Stage 8 emits ZERO rows for that week. Stage 8 `provenance.json` `cooling_irrelevant_weeks` lists it. Stage 8 `reason_report.json` includes a `NOT_COOLING_RELEVANT` entry.

These five are binding.

### Phase 1 — implementation (sequential commits, one PR)

1. **Add `MIN_WEEKLY_CDD = 5.0` constant + `is_cooling_relevant(value)` helper** in `tools/analysis/pipeline.py` near `CDD_BASE_F = 65.0` (line 288). Unit-tested boundary: `is_cooling_relevant(4.99) == False`, `is_cooling_relevant(5.0) == True`, `is_cooling_relevant(5.00001) == True`.
2. **Add `ReasonCode.NOT_COOLING_RELEVANT`** to `tools/analysis/replay/reason_codes.py`. String value `"not_cooling_relevant"`.
3. **Stage 4 wiring.** In `stage4_matching`:
   - After the `qualifies==True` filter, add a `is_cooling_relevant(float(row["weekly_cdd"]))` filter.
   - Weeks failing the cooling-relevance check are written to `unmatched_weeks.csv` with `reason="not_cooling_relevant"`.
   - If after BOTH filters one arm is empty, emit `ReasonCode.NOT_COOLING_RELEVANT` (or, if combined with insufficient-pairs, the existing `SINGLE_ARM_IN_WINDOW`/`INSUFFICIENT_QUALIFYING_WEEKS_PER_ARM` — pick the most specific reason; document the precedence in code).
4. **Stage 8 wiring.** In `_load_qualifying_days_from_stage2_stage3` at `pipeline.py:3611-3616`, extend the qualifying-weeks gate to AND in `is_cooling_relevant(float(row["weekly_cdd"]))`. Track excluded weeks for `provenance.json` (new section `cooling_irrelevant_weeks`). Emit reason_report.json entry where appropriate.
5. **Correct the misleading docstring** at `tools/analysis/pipeline.py:330-334`. Replace with accurate wording: "The zero-CDD guard prevents division by zero. Cooling-relevance (weekly CDD ≥ 5) is enforced at Stage 4 and Stage 8 boundaries, not in this helper."
6. **Refresh `test_stage8_loader_realshape.py`** fixture at lines 110-112, 199-201. Replace the spec-incorrect `weekly_cdd=0, qualifies=True` setup with `weekly_cdd >= 5` and exercise the day-level zero-CDD property correctly (e.g., a cooling-relevant week with a single zero-CDD grid-event day).
7. **Unit + integration tests:**
   - `is_cooling_relevant` boundary unit test (case 4.99, 5.0, 5.0001).
   - Stage 4 unit test: cooling-irrelevant week is filtered, written to `unmatched_weeks.csv` with correct reason.
   - Stage 8 unit test: cooling-irrelevant week absent from qualifying_days set; provenance lists it.
   - Phase 0 oracle tests (all 5).
8. **Realshape e2e refresh.** `test_pipeline_realshape_e2e.py` switch numerics may shift; refresh assertions if needed. The pre-randomization replay bundle likely produces zero qualifying weeks anyway, so the gate is exercised at unit + integration level not realshape.
9. **Docs.**
   - `docs/ANALYSIS_PIPELINE.md` Stage 4: name `weekly_cdd >= 5.0` as a filter alongside `qualifies==True`; document `"not_cooling_relevant"` reason in `unmatched_weeks.csv`.
   - `docs/ANALYSIS_PIPELINE.md` Stage 8: note cooling-relevance filter on qualifying weeks; provenance section.
   - `docs/EXPERIMENT_DESIGN.md` unchanged (it already commits the design).

## Acceptance criteria (binding)

The five Phase 0 oracles must pass. Specifically (per Chris's list):

1. ✓ Week with `weekly_cdd=4.99` excluded from Stage 4 matching, Stage 5 effects.
2. ✓ Week with `weekly_cdd=5.00` included.
3. ✓ Stage 3 still records the weekly row for audit/descriptive.
4. ✓ Reason/reporting distinguishes Stage 2 data-quality failure from cooling-irrelevant.
5. ✓ Stage 8/9 downstream behavior explicit: cooling-irrelevant weeks excluded; provenance lists them; no descriptive output claims otherwise.

## Out of scope

- **Washout primary-exclusion fix.** Next sequenced PR, plan at `docs/plans/washout-primary-exclusion-plan.md`. Will be revised to address mechanics issues (A/B/D from prior review) once cooling-relevance lands.
- Stage 9 sensitivity loader. Stays paused.
- Changing the threshold value (locked at 5.0 by pre-reg commitment #4).
- Adding `cooling_relevant: bool` column to weekly.csv (schema change rejected; the filter at downstream boundaries is sufficient).
- Removing the conflated semantics of `qualifies` (it stays data-quality-only by docstring + downstream gate ordering).

## Risks

- **OSF clock.** This is the FIRST of two primary-eligibility gaps to close pre-OSF. Filing is 2026-05-30 (18 days). Estimated 1 day incl. test refresh.
- **Stage 8 fixture refresh.** `test_stage8_loader_realshape.py` has ~2400 lines including the `weekly_cdd=0` fixture pattern; the refresh may touch multiple test methods that build on it. Estimate scope after Phase 0 lands.
- **Stage 8 PR #102-#107 oracle ripple.** All Stage 8 phase tests are now in main. The fixture refresh may surface additional places where `weekly_cdd=0` is assumed; address in this PR's Phase 1 step 6.
- **No realshape numeric proof pre-OSF.** Pre-randomization bundle has no qualifying weeks, so the realshape pipeline currently produces reason-coded empties for Stage 4/5. The gate is unit-test-proven, not realshape-proven, until summer 2026 data lands.

## Tracking

- Single PR `feature/cooling-relevance-primary-gate`, base `main`. No stacking.
- Sequential commits per implementation step.
- Draft PR opened after Phase 0 RED tests land. Ready-for-review after Phase 1 step 9.
- Archive plan to `docs/plans/archive/cooling-relevance-primary-gate-plan.md` in closing commit.

## After this lands

1. Revise `docs/plans/washout-primary-exclusion-plan.md` per the four mechanics amendments (A/B/D from prior review, plus the test-against-public-outputs amendment).
2. Run the washout fix PR.
3. After washout lands, resume Stage 9 planning with the locks in `~/.claude/projects/D--Projects-energy-proxy/memory/project-stage9-decision-locks-2026-05-12.md`.
