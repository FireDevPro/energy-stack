---
date: 2026-05-12
owner: chris
status: superseded-by-rebaseline-2026-05-13
role-label: chris
superseded-by: docs/plans/analysis-rebaseline-plan-2026-05-13.md
---

> **SUPERSEDED 2026-05-13.** Stashed locally during the recalibration sprint, never merged; surfaced and archived as evidence during the analysis rebaseline. The 48h post-switch washout exclusion remains a valid design commitment (per `docs/EXPERIMENT_DESIGN.md:222`) — the rebaseline will decide whether it lives as a Stage 2 rule, a Stage 3 filter, or is integrated differently. The mechanics amendments flagged during prior review (signature widening, qualifying_days.csv coverage, no-Pi-outage-conflation, public-output assertions) are still valid concerns to address in any future washout implementation.

# Pre-Stage-9 washout primary-exclusion fix — execution plan [SUPERSEDED]

## Why this exists

`docs/EXPERIMENT_DESIGN.md:222` commits a 48h post-switch washout exclusion as part of the primary matched-pair analysis. Verified absent from current code: Stage 3 aggregates full 168h weeks; no rule, no helper, no filter. Stage 9 sensitivity `include_washout` is impossible to define honestly until this closes — the sensitivity contrast does not exist if primary already includes everything.

This PR closes the washout gap. Stage 9 stays paused until this lands.

## Spec anchors

- `docs/EXPERIMENT_DESIGN.md:222` — 48h locked default; conditional `W = ceil(2τ/12h)` clamp `[24h, 72h]` only activates with ratified τ (none pre-OSF).
- `docs/EXPERIMENT_DESIGN.md:224` — `include_washout` is the sensitivity contrast.
- `docs/EXPERIMENT_DESIGN.md:293` — primary aggregates "after washout exclusion and cooling-relevance filtering."
- `docs/EXPERIMENT_DESIGN.md:464` — pre-reg commitment item #7 binds W, formula, and clamp.
- `docs/ANALYSIS_PIPELINE.md` Stage 2 / Stage 3 — to be amended naming washout and noting variable-length aggregation.

## Source verification (recorded 2026-05-12)

- `tools/analysis/pipeline.py:1622-1659` (`rule8_pi_apply`): day-set union of rule1_tier4 ∪ rule7_outage ∪ rule9_vacation. No washout set. **Misleadingly named** — actually enforces the generic <5-qualifying-days rule, not Pi-specific logic.
- `tools/analysis/pipeline.py:2107-2164` (`_stage3_hourly_refoss_kwh`): hardcodes 168 hourly buckets, no day or hour filter.
- `tools/analysis/pipeline.py:2270-2274` (`_load_stage3_inputs_for_week`): signature is `(stage1_dir, week_start_ct, arm)`. **No `stage2_dir` parameter — cannot read `qualifying_days.csv` as currently shaped.**
- `tools/analysis/pipeline.py:2001-2068` (`_compute_weekly_row`): consumes 168 hourly records + 7 daily temps unconditionally. Math itself is length-agnostic.
- `tools/analysis/pipeline.py:820-826` (`stage2_quality`): **only writes `qualifying_days.csv` rows for weeks where `result.row.get("qualifying")` is True.** Non-qualifying weeks contribute no day rows. Stage 3 enumerates ALL weeks (qualifying or not), so it cannot rely on `qualifying_days.csv` for filter info.
- `tools/analysis/pipeline.py:1349-1361` (`_read_assignment_csv`): returns per-Monday rows with `iso_week`, `monday_date`, `arm`.
- `tools/analysis/pipeline.py:3583-…` (`_load_qualifying_days_from_stage2_stage3`): Stage 8 reads `qualifying_days.csv`. Inherits washout for free (Stage 8 only operates on qualifying weeks, where day rows exist).
- Repo-wide grep for `washout|first_48|hours=48` outside Stage 9 stub docstrings: no functional hits.

### Second gap discovered (verification flagged, NOT in this PR scope)

- **CDD ≥ 5 cooling-relevance threshold is NOT implemented in the pipeline.** `EXPERIMENT_DESIGN.md:293` says primary filters by cooling relevance; only appearance of the threshold is `tools/analysis/baseline_distribution.py:196` (ERA5 covariance filter, NOT primary). No Stage 2 rule, no Stage 3/4 gate. The `weekly_dollars_per_cdd` zero-CDD guard at `pipeline.py:334` is a div-zero defense, not a relevance filter. **Treat as a separate pre-OSF gap. Do NOT claim "unchanged" in this plan. Out of scope here unless verified one-line.**

## Locked decisions

| Question | Decision |
|---|---|
| W (duration) | **48h.** Locked default. τ-based formula does not activate without ratified τ. |
| Helper ownership | **Shared analysis-window helper.** `washout_days_for_week(week_start_ct, arm, assignments) -> set[date]` is pure, owned by neither stage. Stage 2 calls it to populate `qualifying_days.csv`. Stage 3 calls it directly to filter aggregation inputs. Same source of truth, no cross-stage CSV coupling. |
| Granularity | **Day.** 48h = exactly Mon 00:00 CT — Wed 00:00 CT = Mon + Tue. No partial-day. |
| Stage 2 day-row coverage | **Write `qualifying_days.csv` rows for ALL enumerated weeks**, not only those that pass overall qualification. Reason: orthogonal audit trail; future loaders shouldn't have to guess why a non-qualifying week has no day rows. Schema unchanged. |
| Stage 3 filter mechanism | `_load_stage3_inputs_for_week` signature widens to accept `excluded_days: set[date]`. Orchestrator computes `washout_days_for_week(...)` and passes it in. **No CSV read from Stage 3 into Stage 2's outputs.** |
| Arm-period-start detection | `assignments[i]` is a switch week iff `i == 0` **OR** `assignments[i-1].arm != assignments[i].arm`. **The first row IS a switch** (W23 loses Mon + Tue). Conservative; matches the "2-week arms lose 2/14" rationale at `docs/EXPERIMENT_DESIGN.md:208`. |
| Combo with other rules | A day that's also rule9_vacation writes `exclusion_source="rule9_vacation;washout_48h"` — semicolon-joined, alphabetical. Matches Stage 8 provenance contract from `docs/plans/archive/stage8-loader-plan.md`. |
| Rule 8 day-count threshold | **Unchanged at 5.** Switch weeks have 5 candidate days post-washout (Wed-Sun); all 5 must pass. Stricter than non-switch (which can lose up to 2 of 7). Spec doesn't pro-rate. No 4-of-5 invention. |
| Rule 8 naming | **Rename `rule8_pi_apply` → `rule8_min_days_apply`.** The function is already a generic day-count helper, not Pi-specific. New signature accepts a unified `excluded_days: set[date]` (or multiple kwargs incl. `washout_days`). Mechanical rename: 1 caller (`pipeline.py:651`) + 6 tests in `test_pipeline.py:514-566`. Threshold constant stays. |
| New `ReasonCode` enum entry | **None.** Use only the string literal `"washout_48h"` in `qualifying_days.csv`'s `exclusion_source` column. Per Chris's decision 2026-05-12. |
| CDD ≥ 5 cooling-relevance | **Not in scope.** Verified absent from pipeline; flagged as a separate pre-OSF gap. Will be addressed in its own plan (or as a follow-on one-liner if verified trivial). |
| `include_washout` Stage 9 sensitivity | Out of scope. Schema is locked here (qualifying_days.csv carries the tag); sensitivity execution lives in the Stage 9 plan. |

## Phases

This is one PR. Focused single slice (one new shared helper, one Stage 2 day-set extension, one Stage 3 filter point, one rule rename).

### Phase 0 — RED feature acceptance test

Outside-in. Drives every subsequent decision.

**Fixture:** 4-week synthetic bundle covering W23 A (switch), W24 A (cont), W25 B (switch), W26 B (cont). Synthetic shapes tailored to each oracle below — no other exclusions, no Tier 4 gaps, no vacations.

**Five required oracle tests** (binding acceptance set; every test proves the filter happens *before* aggregation, not as a post-hoc subtraction):

1. **O1 ignores Mon+Tue HVAC kWh.** Fixture: Mon+Tue have huge HVAC kWh (e.g., 10 kWh/h × 48h); Wed-Sun low (0.1 kWh/h). Oracle: W23 `o1_dollars_per_cdd` derives from Wed-Sun only. Hand-computed within ¢ tolerance. Regression caught: washout applied via post-hoc subtraction rather than upstream filter.
2. **O3 ignores Mon+Tue HVAC peak.** Fixture: Mon+Tue contain the weekly HVAC peak (e.g., 8 kW); Wed-Sun lower (e.g., 2 kW). Oracle: W23 `o3_peak_hvac_kw` = 2 kW, NOT 8 kW. Regression caught: `max(hourly_kwh)` over an unfiltered 168-list still sees the peak.
3. **Weather vector ignores Mon+Tue.** Fixture: Mon+Tue are the hottest (max temp 100°F, max dewpoint 80°F); Wed-Sun mild (max temp 75°F). Oracle: W23 `max_temp_f` = 75, `max_dewpoint_f` reflects Wed-Sun only, `weekly_cdd` sums Wed-Sun only (5 days). Regression caught: weather records bypass the day filter.
4. **`qualifying_days.csv` schema content.** Oracle: every enumerated week has 7 rows (qualifying OR not). W23 + W25 rows for Mon + Tue carry `included=false`, `exclusion_source="washout_48h"`. W24 + W26 rows for Mon + Tue carry `included=true`, `exclusion_source=""`. Wed-Sun rows for all weeks `included=true`. Bit-exact CSV content.
5. **Stage 8 outputs reflect washout (assert outputs, NOT internals).** Run `stage8_decomposition` on the same fixture (or a Stage 8-extension fixture that puts qualifying days into a no-spike category). Oracle assertions:
   - `decomposition.csv` row for `(o1_daily_hvac_dollars, no_spike)` has `arm_a_n_days == 5` (= 1 switch week × 5 days post-washout from W23) and `arm_b_n_days == 5` (= W25). If the fixture also has W24 + W26 as qualifying no-spike weeks, expand accordingly: `arm_a_n_days == 12` (5 + 7), `arm_b_n_days == 12`.
   - `provenance.json` `day_exclusions_summary` includes a count for `"washout_48h"` matching the number of washout days in qualifying weeks.

These five are the binding acceptance set.

### Phase 1 — implementation (sequential commits, one PR)

1. **`washout_days_for_week(week_start_ct, arm, assignments)`** — pure shared helper in `tools/analysis/pipeline.py`. Returns `{Mon, Tue}` for switch weeks, `set()` otherwise. Switch defined per the locked rule. Owned by neither Stage 2 nor Stage 3. Unit tests: switch detection across the locked assignment CSV; first row IS switch; arm-flip is switch; same-arm-second-week is NOT switch.
2. **Rename `rule8_pi_apply` → `rule8_min_days_apply`.** Update signature: accept the existing `rule1_tier4_days / rule7_outage_days / rule9_vacation_days` AND a new `washout_days: set[date] = frozenset()` kwarg. Union all four sets, apply unchanged 5-day threshold. Update docstring to name the rule generically. Mechanical rename ripples: 1 caller at `pipeline.py:651` + 6 tests at `test_pipeline.py:514-566`. The original tests stay (they don't pass `washout_days`; default empty set preserves behavior).
3. **Stage 2 wiring.** In `stage2_quality`:
   - Compute `washout_days = washout_days_for_week(week, arm, assignments)` per enumerated week.
   - Pass `washout_days` into `rule8_min_days_apply`.
   - Per-week qualifying_days emission writes 7 rows for **every enumerated week** (qualifying or not). When the week's day is in `washout_days`, append `"washout_48h"` to its `exclusion_source` (alphabetical join with other rule contributions).
4. **Stage 3 wiring.** Widen `_load_stage3_inputs_for_week(stage1_dir, week_start_ct, arm, *, excluded_days: set[date] = frozenset())`. Default-empty preserves existing test fixtures that don't supply it. Inside, after building 168 hourly records and 7 daily temps, drop hours/days whose CT date is in `excluded_days`. `stage3_weekly` orchestrator reads assignments, computes washout via the shared helper, passes it into the loader.
5. **Unit tests:**
   - `washout_days_for_week` across the locked assignment CSV (multiple switch and non-switch cases; first-row-is-switch case).
   - `rule8_min_days_apply` with `washout_days` set: switch-week-no-other-exclusions qualifies (5 days); switch-week-with-Thursday-Tier-4 fails (4 days < 5).
   - `_load_stage3_inputs_for_week` with `excluded_days={Mon, Tue}` returns 120 hourly records (NOT 168) and 5 daily temps.
6. **Integration test (the 5 oracles in Phase 0).**
7. **Realshape e2e refresh.** `tools/analysis/tests/test_pipeline_realshape_e2e.py` switch-week numerics shift; refresh pinned values, document old → new in a single commit's test comments.
8. **Docs.**
   - `docs/ANALYSIS_PIPELINE.md` Stage 2: name `washout_48h` as exclusion source; note that `qualifying_days.csv` now covers all enumerated weeks.
   - `docs/ANALYSIS_PIPELINE.md` Stage 3: note variable-length aggregation for switch weeks (120h / 5 daily temps).
   - `docs/EXPERIMENT_DESIGN.md` unchanged (it already commits the design; this PR makes the code match).

### Phase 2 — replay-bundle re-run (modest scope, schema-drift catch only)

Synthetic fixtures in Phase 0 / 1 carry the binding correctness proof for washout. Replay bundles for the May 2026 pre-randomization window do NOT show shifted switch-week outcomes (pre-randomization = no arm assignments in window = reason-coded empty Stage 2 output). The replay re-run's value is **catching incidental schema-drift bugs** the same way Stage 8's close-out did (PRs #94-#97), not validating washout numerics.

- Re-run 7d + 90d replay bundles on Pi-lab.
- Expect mostly unchanged outputs (pre-randomization window).
- Watch for: `assignment_csv` consumption breakage if the bundle window starts mid-experiment; new `washout_48h` strings appearing in `exclusion_source`; any CSV-parse regressions downstream.
- Document deltas (if any) in `docs/replay-validation/` per Stage 8 close-out protocol.

## Out of scope

- **CDD ≥ 5 cooling-relevance threshold.** Second code-vs-spec gap discovered during washout verification. Separate PR (or one-liner if verified trivial).
- `include_washout` Stage 9 sensitivity rerun. Schema locked here; execution in the Stage 9 plan.
- Pro-rated CDD ≥ 5 or pro-rated qualifying-days thresholds for switch weeks.
- Imputing the dropped 48h. Spec says exclude.
- Rule 10 (6h verification window) interaction. Rule 10's window lives inside washout; verification failures still flag the week, but washout days drop regardless.
- Refactoring `_compute_weekly_row`'s length assumptions. Math already length-agnostic.
- Touching Stage 4/5/6/7 code. They consume Stage 3 weekly.csv and Stage 2 qualifying_days.csv passively.

## Risks

- **Downstream oracle test cascade.** Stage 4 pair_ids may rearrange because switch-week weather vectors are now 5-day means. Stage 5 / 7 / 8 oracles inherit. Mitigation: refresh assertions in a single batch (Phase 1 step 7), document deltas.
- **Switch-week asymmetry.** Same-controller, same-weather weeks produce structurally different outcomes between switch (5d) and non-switch (7d) positions. By design (matches `docs/EXPERIMENT_DESIGN.md:208` rationale), but worth naming in docs so readers don't read it as a controller artifact.
- **Stricter rule 8 for switch weeks.** A switch week with one Tier 4 day drops; the same pattern in a non-switch week qualifies. Higher switch-week drop rate. Locked at 5.
- **OSF clock.** Primary-path change touching pre-reg commitment #7. Must land before OSF filing (2026-05-30). Estimated 1-2 days incl. realshape refresh.
- **CDD ≥ 5 gap discovery.** Surfaced during this verification. Its own PR. If verified trivially small (e.g., a single Stage 2 rule + threshold), may merge into this PR; otherwise separate.

## Tracking

- Single PR `feature/washout-primary-exclusion`, base `main`. No stacking. Standalone — does not block on or get blocked by Stage 9.
- Sequential commits per step.
- Draft PR opened after Phase 0 RED test lands. Ready-for-review after Phase 1 step 8.
- Archive plan to `docs/plans/archive/washout-primary-exclusion-plan.md` in closing commit.
