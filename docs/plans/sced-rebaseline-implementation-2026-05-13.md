# SCED Rebaseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship all pre-OSF dependencies for the rebaselined SCED experiment by 2026-05-30, enabling the 24-week summer-2026 single-household HVAC controller study to start 2026-06-01 with a complete telemetry, pricing, analysis, and documentation stack.

**Architecture:** Six phases. Phase 1 lands controller-side telemetry + arm-mode gating (foundation for everything). Phase 2 adds bill-canonical pricing infrastructure. Phase 3 rewrites the analysis pipeline around arm-period units with cost-matched exclusion + single validity gate. Phase 4 pulls the historical weather baseline for z-score scaling. Phase 5 freezes documentation. Phase 6 runs end-to-end shadow validation. Phase 2 + Phase 4 can run in parallel with Phase 3.

**Tech Stack:** Python 3.11 + pytest, InfluxDB 2 + Flux, Docker Compose on Pi-lab, ComEd parse_comed_bill + PJM DataMiner2 + Ecowitt + Refoss EM16P + Eagle-3 HAN. Existing tooling in `tools/analysis/`, `deploy/energy-stack/hvac-scheduler/`, `deploy/energy-stack/pjm-dm2-poller/`.

**Spec source of truth:** [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](sced-rebaseline-spec-2026-05-13.md). Every task in this plan cites the spec section it implements.

**OSF target:** 2026-05-30. **Experiment start:** 2026-06-01. **Experiment end:** 2026-11-16.

---

## File Structure

### New files to create

| Path | Responsibility |
|---|---|
| `tools/analysis/arm_calendar.py` | Locked 12-arm calendar (Section 2 of spec) + hour-index ↔ datetime conversion |
| `deploy/energy-stack/hvac-scheduler/arm_calendar.py` | Byte-identical copy of above (CI hash-sync check per M5) — controller container needs local copy, no shared PYTHONPATH |
| `tools/analysis/mode_classification.py` | 4-mode per-hour classification (Section 5 of spec) |
| `tools/analysis/arm_period_pipeline.py` | Arm-period-shaped pipeline (replaces weekly Stage 3/5 framing) |
| `tools/analysis/cost_matched_exclusion.py` | Greedy cost-matched symmetric exclusion (Section 5 of spec) |
| `tools/analysis/weather_vector.py` | 4-component weather vector + within-sample z-scoring (Section 6 of spec) |
| `tools/analysis/queries/rt_hrl_lmps.flux` | Settled hourly LMP Flux query |
| `tools/analysis/queries/hvac_arm_mode.flux` | Controller mode telemetry query |
| `docs/THERMOSTAT_ARM_A_SCHEDULE.md` | CTK04AE programmed schedule (referenced from OSF spec) |
| `docs/replay-validation/2026-05-2X-shadow/findings.md` | Shadow validation report |

### Existing files to modify

| Path | Change scope |
|---|---|
| `deploy/energy-stack/hvac-scheduler/app.py` | Add arm-calendar reading, mode-gating around `execute_action`, mode/switch/feed-health telemetry writes |
| `deploy/energy-stack/hvac-scheduler/precool.py` | Optional pre-freeze: upgrade DTOD base→resultant rates (controller-side, NOT mandated for OSF) |
| `deploy/energy-stack/pjm-dm2-poller/app.py` | Add `rt_hrl_lmps` feed alongside existing `da_hrl_lmps` |
| `tools/analysis/pipeline.py` | Major: remove `$/CDD` outcomes, weekly Stage 3/5 framing, bootstrap/SCED randomization. Wire to new arm-period pipeline. |
| `tools/analysis/replay/manifest.py` | Add `rt_hrl_lmps` to KNOWN_MEASUREMENTS |
| `docs/HVAC_LOGIC.md` | Verify day-type schedule completeness; patch gaps |

### Files to delete (post-pipeline-rewrite)

| Path | Reason |
|---|---|
| Selected sections of `pipeline.py` related to `weekly_*` outcomes, `$/CDD`, bootstrap, SCED randomization | Superseded by arm-period framing per spec §12 |

---

## Standing rules for every phase

These apply to every task and PR in this plan. Read once; enforced throughout.

**Code-style note (#4 fix from adversarial review):** Python snippets in this plan are **illustrative**, not prescriptive. The hvac-scheduler uses synchronous Influx writes (`write_api.write(bucket=..., record=point)`); the analysis layer may use different patterns. When implementing each task, **inspect the existing code in that subsystem and follow its conventions**. Do not blindly copy snippet style. In particular: the `await influx.write(point=...)` and `point.tag().field()`-as-getter patterns in some snippets are pseudocode — the real implementation must match the actual InfluxDB client API and the project's existing patterns. If an existing pattern doesn't match the snippet, the existing pattern wins.

**SCHEDULER_DRY_RUN retirement (#5):** the existing `SCHEDULER_DRY_RUN=true` env var is **retired** when Phase 1 lands. It is replaced entirely by `SCHEDULER_MODE` (spec §3). Migration step in the Phase 1 deploy: on pi-lab, remove `SCHEDULER_DRY_RUN` and set `SCHEDULER_MODE=shadow` in the .env. No dual-control / overlap period. After Phase 1: if both env vars are present, `SCHEDULER_DRY_RUN` is ignored with a warning log.

**Per-phase review gates (#6):** Before opening a phase PR as ready-for-review:
1. All task-level tests pass (`bash deploy/energy-stack/run_tests.sh` green)
2. Run `/codex:adversarial-review` against the phase branch
3. Run `/superpowers:reviewer` or equivalent superpowers review (per existing project tooling)
4. Address every HIGH-severity finding inline before PR-ready (commit fixes)
5. Document any rejected findings in the PR body with rationale
6. **If review surfaces uncertainty that cannot be resolved in the phase, STOP and escalate to operator**, do not paper over

**PR body template (#7):** every phase PR body MUST include this status section:

```markdown
## Status

### Task status
- [x] Tasks completed in this PR: <list>
- Tests run: <command + green/red summary>

### Feature status (per AGENTS.md multi-phase workflow)
- Outside-in acceptance test (`test_rebaseline_end_to_end_acceptance`): [SKIPPED | XFAIL | PASS]
- Remaining phases: <list>
- This PR does NOT claim feature completion. The rebaseline is feature-complete only when the outside-in test passes with zero scaffolding.

### Reviews
- Codex adversarial review: <link to output, summary of findings addressed>
- Superpowers review: <link or note>
- Rejected findings: <list with rationale, or "none">

### Replay / shadow validation
- Status at this phase: <pending | partial | complete>
- Last shadow validation run: <link or "not yet run">
```

---

## Approved exception: horizontal phasing

AGENTS.md plan-authoring discipline says: *"Each phase = vertical slice (data → logic → UI / output). Phase 1 = tracer bullet."* This plan deviates from that pattern: phases 1-6 are organized by subsystem (telemetry, pricing infrastructure, analysis pipeline, weather fallback, documentation, validation) rather than by vertical slice through the whole pipeline.

**Why this deviation is approved here:**

1. The subsystems are genuinely sequential: rt_hrl_lmps poller (Phase 2) must be running and backfilled before the analysis pipeline (Phase 3) can use its data. Mode-classification telemetry (Phase 1) must exist before validity gates can be applied. Building a "thin vertical slice through everything" would still require all the upstream telemetry/data pieces to exist in some form, just with stub data — which is exactly what the outside-in acceptance test in Phase 0 provides.
2. **Phase 0 IS the vertical slice.** It exercises the entire pipeline against synthetic data with known answers. Phases 1-6 progressively replace its scaffolding with real implementations.
3. The infrastructure phases produce no user-visible output independently — they're prerequisites for the analysis to run at all.

**What the outside-in acceptance test compensates for:** the absence of true vertical phases. The test ensures coherence-of-the-whole even when phases are subsystem-organized. Feature-completion rule (per AGENTS.md outside-in TDD) explicitly requires that test to pass against the real implementation before claiming the rebaseline is done.

If during execution a phase reveals it cannot be completed independently (e.g., Phase 3 implementation hits an unanticipated need for a Phase 2 component), the plan should be revised in place rather than deferring the dependency.

---

## Phase 0: Outside-In Acceptance Oracle

**Goal:** Write a feature-level acceptance test that exercises the entire rebaseline pipeline end-to-end against synthetic data with known answers. Phases 1-6 progressively replace its scaffolding with real implementations. **Feature is NOT complete until this test passes using the real implementation with zero scaffolding.**

**Why first:** per AGENTS.md outside-in TDD rule: *"First step on any feature: write a feature-level acceptance test that exercises the full vertical slice end-to-end. Drive inward from there. Reason: prevents 'task done equals feature done' confusion. The project is the whole, not any given task. Intermediate stages stay xfail or skip until the slice ships."*

**Time:** 0.5-1 day.

---

### Task 0.1: Synthetic 12-arm experiment dataset

**Files:**
- Create: `tools/analysis/tests/fixtures/synth_rebaseline_dataset.py`

This fixture module builds a self-contained synthetic dataset that exercises every code path the real pipeline will touch. Known-answer construction lets the outside-in test assert exact expected outputs.

- [ ] **Step 1: Build the synthetic builder**

```python
"""Synthetic 12-arm experiment dataset for outside-in acceptance testing.

Constructs:
- 12 arm periods following spec §2 calendar (2026-06-01 → 2026-11-16)
- Refoss hourly power_w streams for em:1, em:2, em:7, em:8, em:9
- Eagle whole-home kWh
- Ecowitt weather (temp, dewpoint, wind, solar)
- rt_hrl_lmps settled prices
- ComEd 5-min live prices
- PJM capacity-risk inputs
- hvac.arm_mode telemetry (with known A-active / B-active / B-fallback / B-down / telemetry-invalid distribution)

Built with known properties so the acceptance test can assert exact answers:
- Arm A periods: HVAC kWh = baseline_a per hour during cooling, 0 otherwise
- Arm B periods: HVAC kWh = baseline_a × (1 - savings_pct) per hour during cooling
- Some hours injected as B-fallback (price feed stale)
- Some hours injected as telemetry-invalid (Refoss gap >2min)
- Weather vectors constructed so 5 of 6 expected pairs match within caliper, 1 pair above 90th-percentile distance (poor_weather_match_flag)
- Known cost-matched exclusion result: 12 hours excluded from each pair (4 in A, 4 in B asymmetric → 8 cost-matched symmetric)
"""
import datetime
from dataclasses import dataclass


@dataclass
class SynthDataset:
    refoss_df: "pd.DataFrame"
    eagle_df: "pd.DataFrame"
    ecowitt_df: "pd.DataFrame"
    rt_hrl_lmps_df: "pd.DataFrame"
    comed_prices_df: "pd.DataFrame"
    hvac_arm_mode_df: "pd.DataFrame"
    bills_df: "pd.DataFrame"
    expected_per_pair_table: "pd.DataFrame"
    expected_buckets: dict


SAVINGS_PCT = 0.15
BASELINE_HVAC_KW = 2.5
KNOWN_LMP_C_PER_KWH = 5.0


def build_synth_dataset() -> SynthDataset:
    """Construct the full synthetic dataset + expected outputs."""
    # ... full implementation builds dataframes + expected per-pair table
    # using known-answer construction:
    # - Pair 1: ARMs 1+2, mild weather, B saves 15%, all hours fully-valid
    # - Pair 2: ARMs 3+4, heat wave, B saves 15%, some B-fallback in Arm 4
    # - Pair 3: ARMs 5+6, weather outlier triggering poor_weather_match_flag
    # - Pair 4: ARMs 7+8, telemetry-invalid hours in both arms
    # - Pair 5: ARMs 9+10, shoulder season, low HVAC$ (tests denominator-small handling)
    # - Pair 6: ARMs 11+12, DST-crossing arm 11 + November cool weather
    ...
```

- [ ] **Step 2: Document the known-answer construction with INDEPENDENT expected values**

**Critical (oracle independence, #8 from adversarial review):** Expected values in `expected_per_pair_table` MUST be **hand-pinned** or **independently computed**. They MUST NOT be computed using the same helper logic as the implementation under test. If `build_synth_dataset()` generates a dataset and then calls `run_full_pipeline()` (or any of its components) to compute the expected output, the test becomes self-confirming and proves nothing.

**Required:** each expected value in `expected_per_pair_table` either:
- Is a literal constant hand-computed by the author (e.g., `diff_dollars_b_minus_a = -8.40 # 12 days × 24h × 2.5 kWh × 15% savings × 7.78 ¢/kWh / 100`)
- Is computed via simple arithmetic in the fixture module itself, using parameters constants (`SAVINGS_PCT`, `BASELINE_HVAC_KW`, `KNOWN_LMP_C_PER_KWH`) and the dataset's known properties, WITHOUT importing any function from `tools/analysis/`

The fixture is permitted to construct the INPUT dataframes using whatever logic is convenient, but the EXPECTED OUTPUT values must be derived independently. If a reviewer reads the fixture and finds expected values coming from `from tools.analysis.X import Y; expected = Y(...)`, that is a bug and must be fixed before this task is complete.

- [ ] **Step 3: Commit**

```bash
git checkout -b sced-rebaseline-phase0
git add tools/analysis/tests/fixtures/synth_rebaseline_dataset.py
git commit -m "feat(test-fixtures): synthetic 12-arm experiment dataset with known answers (phase 0)"
```

---

### Task 0.2: Outside-in acceptance test (initially scaffolded)

**Files:**
- Create: `tools/analysis/tests/test_rebaseline_end_to_end_acceptance.py`

The test runs the full pipeline end-to-end and asserts every key output matches expected. Initially marked `xfail` or `skip` until Phase 6 lands.

- [ ] **Step 1: Write the test**

```python
"""Outside-in acceptance test for the SCED rebaseline.

This is the FEATURE-LEVEL test. It exercises the full pipeline:
synthetic ingestion → mode classification → validity gate → weather
matching → cost-matched exclusion → per-pair table → aggregate buckets.

Per AGENTS.md outside-in TDD rule: this test stays xfail/skip until
real implementations land. The rebaseline is NOT feature-complete
until this test passes with zero scaffolding.
"""
import pytest

from tools.analysis.tests.fixtures.synth_rebaseline_dataset import (
    build_synth_dataset, SAVINGS_PCT,
)

# Pipeline entry point — will be implemented incrementally across Phases 1-6
try:
    from tools.analysis.arm_period_pipeline import run_full_pipeline
    PIPELINE_AVAILABLE = True
except ImportError:
    PIPELINE_AVAILABLE = False


@pytest.mark.skipif(not PIPELINE_AVAILABLE,
                    reason="Outside-in: implementation lands across Phases 1-6")
def test_rebaseline_end_to_end_acceptance():
    """The whole pipeline produces expected outputs on synthetic data.

    DO NOT modify this test to make it pass by mocking or replacing
    the pipeline. The rebaseline is feature-complete only when this
    test passes against the real implementation.
    """
    synth = build_synth_dataset()

    result = run_full_pipeline(
        refoss_df=synth.refoss_df,
        eagle_df=synth.eagle_df,
        ecowitt_df=synth.ecowitt_df,
        rt_hrl_lmps_df=synth.rt_hrl_lmps_df,
        comed_prices_df=synth.comed_prices_df,
        hvac_arm_mode_df=synth.hvac_arm_mode_df,
        bills_df=synth.bills_df,
    )

    # 1. Per-pair table shape and content
    actual = result.per_pair_table.sort_values("pair_id").reset_index(drop=True)
    expected = synth.expected_per_pair_table.sort_values("pair_id").reset_index(drop=True)
    assert len(actual) == len(expected)
    assert set(actual.columns) == set(expected.columns)
    for col in ("diff_dollars_b_minus_a", "diff_kwh_b_minus_a", "valid_pair_hours"):
        assert (abs(actual[col] - expected[col]) < 0.5).all(), f"mismatch in {col}"

    # 2. Per-pair savings match injected SAVINGS_PCT (within tolerance)
    cooling_pairs = actual[actual["hvac_dollars_a"] > 5.0]
    observed_savings_pct = (cooling_pairs["diff_dollars_b_minus_a"] /
                             cooling_pairs["hvac_dollars_a"])
    assert (abs(observed_savings_pct + SAVINGS_PCT) < 0.02).all(), \
        "Per-pair savings should be ~15% in cooling pairs"

    # 3. Per spec §9.5: pre-registered buckets all populated
    assert "all_valid_pairs" in result.bucket_summaries
    assert "high_cooling_pairs" in result.bucket_summaries
    assert "medium_cooling_pairs" in result.bucket_summaries
    assert "low_cooling_pairs" in result.bucket_summaries
    assert "scarcity_exposed_pairs" in result.bucket_summaries
    assert "5cp_exposed_pairs" in result.bucket_summaries
    assert "high_temp_exposed_pairs" in result.bucket_summaries

    # 4. Mode classification correctly labels the scenarios the fixture injects.
    # Only assert modes the fixture actually constructs; do NOT require all 4
    # modes to be present as a general expectation. The fixture's docstring
    # lists which scenarios it injects; this assertion mirrors that list.
    for injected_mode in synth.injected_modes:  # e.g. {"A-active", "B-active", "B-fallback"}
        assert result.mode_distribution.get(injected_mode, 0) > 0, \
            f"Expected mode {injected_mode} (injected by fixture) not observed"

    # 5. Validity gate drops expected arms (per known-answer construction)
    assert result.arms_passed_validity == synth.expected_arms_passed_validity

    # 6. Caliper flag triggers on the constructed poor-match pair
    assert (actual["poor_weather_match_flag"] == True).sum() == 1, \
        "Exactly one pair should hit the >90th-percentile poor-match flag"

    # 7. Cost-matched exclusion preserved equal counts in both arms of every pair
    assert (actual["valid_pair_hours_a"] == actual["valid_pair_hours_b"]).all()

    # 8. Per-pair table has all spec §9 required columns
    required_columns = {
        "pair_id", "arm_a_id", "arm_b_id", "arm_a_dates", "arm_b_dates",
        "temporal_gap_days", "weather_distance_zscore",
        "weather_vector_a", "weather_vector_b",
        "weather_component_diffs_raw", "weather_component_diffs_zscored",
        "poor_weather_match_flag",
        "valid_pair_hours", "excluded_hours_count",
        "excluded_hours_breakdown_a", "excluded_hours_breakdown_b",
        "cost_match_quality_median_diff_c_per_kwh",
        "cfe_c_per_kwh_a", "cfe_c_per_kwh_b",
        "cooling_active_hours_a", "cooling_active_hours_b",
        "low_cooling_exposure_flag",
        "hvac_dollars_a", "hvac_dollars_b",
        "diff_dollars_b_minus_a", "percent_diff_dollars",
        "hvac_kwh_a", "hvac_kwh_b", "diff_kwh_b_minus_a",
        "weather_source_pct_ecowitt_a", "weather_source_pct_ecowitt_b",
    }
    assert required_columns <= set(actual.columns), \
        f"Missing required columns: {required_columns - set(actual.columns)}"
```

- [ ] **Step 2: Verify test runs (as skipped initially)**

Run: `python -m pytest tools/analysis/tests/test_rebaseline_end_to_end_acceptance.py -v`
Expected: SKIPPED — `run_full_pipeline` not yet implemented.

- [ ] **Step 3: Commit**

```bash
git add tools/analysis/tests/test_rebaseline_end_to_end_acceptance.py
git commit -m "feat(test-acceptance): outside-in feature-level acceptance test (phase 0)"
```

---

### Task 0.3: Plan completion criteria

**Files:**
- Modify: `docs/plans/sced-rebaseline-implementation-2026-05-13.md` (this file's self-review section)

- [ ] **Step 1: Add explicit feature-completion rule**

Add to the Self-review section (bottom of this plan):

> **Feature-completion rule (per AGENTS.md outside-in TDD + multi-phase feature workflow):** the SCED rebaseline is NOT feature-complete until `tools/analysis/tests/test_rebaseline_end_to_end_acceptance.py::test_rebaseline_end_to_end_acceptance` passes against the real implementation with zero scaffolding. Specifically: the test must NOT be made to pass by mocking, replacing, or substituting any component of `run_full_pipeline` or its dependencies. Individual task-level tests in Phases 1-6 are necessary but not sufficient.

- [ ] **Step 2: Open Phase 0 PR + merge**

```bash
gh pr create --base main --title "Phase 0: SCED rebaseline outside-in acceptance oracle" --body "..."
```

Stop here for user review + merge before starting Phase 1.

---

## Phase 1: Telemetry foundation + arm-mode gating

**Goal:** Scheduler runs in continuous loop, reads locked arm calendar, gates setpoint writes by current arm, writes mode/switch/feed-health/heartbeat telemetry. Pre-flight validation that dry-run mode never touches Control4.

**Days 1-3.**

---

### Task 1.1: Create arm calendar config module

**Files:**
- Create: `tools/analysis/arm_calendar.py`
- Test: `tools/analysis/tests/test_arm_calendar.py`

Implements spec §2. Pure-Python data + utility module. No I/O.

- [ ] **Step 1: Write the failing test**

```python
# tools/analysis/tests/test_arm_calendar.py
import datetime
import pytest
from tools.analysis.arm_calendar import (
    ARM_CALENDAR, current_arm_at, hour_index_to_datetime,
    datetime_to_hour_index, post_washout_start,
)


def test_calendar_has_12_arms_alternating():
    assert len(ARM_CALENDAR) == 12
    assert [a.arm for a in ARM_CALENDAR] == ["A", "B"] * 6


def test_first_arm_starts_2026_06_01():
    assert ARM_CALENDAR[0].start_ct == datetime.datetime(2026, 6, 1, 0, 0)
    assert ARM_CALENDAR[0].arm == "A"


def test_last_arm_ends_2026_11_16():
    assert ARM_CALENDAR[-1].end_ct == datetime.datetime(2026, 11, 16, 0, 0)
    assert ARM_CALENDAR[-1].arm == "B"


def test_arm_periods_are_14_days():
    for arm in ARM_CALENDAR:
        assert (arm.end_ct - arm.start_ct).days == 14


def test_current_arm_at_first_arm():
    assert current_arm_at(datetime.datetime(2026, 6, 5, 14, 0)) == "A"


def test_current_arm_at_second_arm():
    assert current_arm_at(datetime.datetime(2026, 6, 20, 14, 0)) == "B"


def test_current_arm_at_before_window_is_none():
    assert current_arm_at(datetime.datetime(2026, 5, 31, 23, 59)) is None


def test_current_arm_at_after_window_is_none():
    assert current_arm_at(datetime.datetime(2026, 11, 17, 0, 0)) is None


def test_post_washout_start_is_wed_00():
    # Arm 1 starts Mon 2026-06-01; washout ends Wed 2026-06-03 00:00
    assert post_washout_start(ARM_CALENDAR[0]) == datetime.datetime(2026, 6, 3, 0, 0)


def test_hour_index_0_is_post_washout_start():
    arm = ARM_CALENDAR[0]
    assert hour_index_to_datetime(arm, 0) == datetime.datetime(2026, 6, 3, 0, 0)


def test_hour_index_287_is_sunday_23():
    arm = ARM_CALENDAR[0]
    # Wed 06-03 00:00 + 287 hours = Sun 06-14 23:00
    assert hour_index_to_datetime(arm, 287) == datetime.datetime(2026, 6, 14, 23, 0)


def test_datetime_to_hour_index_roundtrip():
    arm = ARM_CALENDAR[0]
    for k in (0, 12, 100, 287):
        dt = hour_index_to_datetime(arm, k)
        assert datetime_to_hour_index(arm, dt) == k


def test_datetime_to_hour_index_outside_window_is_none():
    arm = ARM_CALENDAR[0]
    # In washout (Mon-Tue) → not in post-washout window
    assert datetime_to_hour_index(arm, datetime.datetime(2026, 6, 2, 12, 0)) is None
    # After arm-end → not in post-washout window
    assert datetime_to_hour_index(arm, datetime.datetime(2026, 6, 15, 0, 0)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\Projects\energy-proxy && python -m pytest tools/analysis/tests/test_arm_calendar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.analysis.arm_calendar'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/analysis/arm_calendar.py
"""Locked SCED arm calendar per docs/plans/sced-rebaseline-spec-2026-05-13.md §2.

12 arm periods, A/B alternating, 14 days each, Sun→Mon 00:00 CT switches.
First arm = A on 2026-06-01, last arm ends 2026-11-16.
"""
from __future__ import annotations
import datetime
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class ArmPeriod:
    index: int                # 1..12
    arm: Literal["A", "B"]
    start_ct: datetime.datetime  # CT-local, naive
    end_ct: datetime.datetime    # exclusive


def _build_calendar() -> tuple[ArmPeriod, ...]:
    start = datetime.datetime(2026, 6, 1, 0, 0)  # Monday
    return tuple(
        ArmPeriod(
            index=i + 1,
            arm="A" if i % 2 == 0 else "B",
            start_ct=start + datetime.timedelta(days=14 * i),
            end_ct=start + datetime.timedelta(days=14 * (i + 1)),
        )
        for i in range(12)
    )


ARM_CALENDAR: tuple[ArmPeriod, ...] = _build_calendar()
WASHOUT_HOURS = 48
POST_WASHOUT_DAYS = 12
HOURS_PER_ARM = POST_WASHOUT_DAYS * 24  # 288


def current_arm_at(when_ct: datetime.datetime) -> Optional[Literal["A", "B"]]:
    """Return current arm letter, or None if outside experiment window."""
    for arm in ARM_CALENDAR:
        if arm.start_ct <= when_ct < arm.end_ct:
            return arm.arm
    return None


def post_washout_start(arm: ArmPeriod) -> datetime.datetime:
    """Hour-index 0 is Wed 00:00 CT, immediately after the 48h washout."""
    return arm.start_ct + datetime.timedelta(hours=WASHOUT_HOURS)


def hour_index_to_datetime(arm: ArmPeriod, k: int) -> datetime.datetime:
    if not 0 <= k < HOURS_PER_ARM:
        raise ValueError(f"hour_index {k} out of range [0, {HOURS_PER_ARM})")
    return post_washout_start(arm) + datetime.timedelta(hours=k)


def datetime_to_hour_index(arm: ArmPeriod, when_ct: datetime.datetime) -> Optional[int]:
    start = post_washout_start(arm)
    if not start <= when_ct < arm.end_ct:
        return None
    delta_hours = int((when_ct - start).total_seconds() // 3600)
    if not 0 <= delta_hours < HOURS_PER_ARM:
        return None
    return delta_hours
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tools/analysis/tests/test_arm_calendar.py -v`
Expected: PASS — all 12 tests green.

- [ ] **Step 5: Commit**

```bash
git checkout -b sced-rebaseline-phase1
git add tools/analysis/arm_calendar.py tools/analysis/tests/test_arm_calendar.py
git commit -m "feat(analysis): add locked SCED arm calendar (spec §2)"
```

---

### Task 1.2: Add arm-mode gating to scheduler

**Files:**
- Modify: `deploy/energy-stack/hvac-scheduler/app.py` (add arm-calendar read + gate around `execute_action`)
- Test: `deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py`

Implements spec §3 (Arm A = scheduler in passive mode, Arm B = active).

- [ ] **Step 1: Write the failing test**

```python
# deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py (add to existing file)
import datetime
import pytest


@pytest.mark.asyncio
async def test_shadow_mode_never_writes(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "shadow")
    import importlib, app; importlib.reload(app)
    c4, climate = _mock_c4_client()
    action = app.ScheduleAction(13, 0, "LEAVE", cool_setpoint_f=78, fan_mode="Circulate")
    when_ct = datetime.datetime(2026, 6, 20, 13, 0)  # mid-Arm-2 (Arm B period — irrelevant in shadow)

    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
    )
    assert applied is False
    climate.set_cool_setpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_experiment_mode_arm_a_does_not_write(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    import importlib, app; importlib.reload(app)
    c4, climate = _mock_c4_client()
    action = app.ScheduleAction(13, 0, "LEAVE", cool_setpoint_f=78, fan_mode="Circulate")
    when_ct = datetime.datetime(2026, 6, 5, 13, 0)  # mid-Arm-1 (Arm A)

    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
    )
    assert applied is False
    climate.set_cool_setpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_experiment_mode_arm_b_writes(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    import importlib, app; importlib.reload(app)
    c4, climate = _mock_c4_client()
    action = app.ScheduleAction(13, 0, "LEAVE", cool_setpoint_f=78, fan_mode="Circulate")
    when_ct = datetime.datetime(2026, 6, 20, 13, 0)  # mid-Arm-2 (Arm B)

    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
    )
    assert applied is True
    climate.set_cool_setpoint.assert_awaited_once_with(78)


@pytest.mark.asyncio
async def test_experiment_mode_outside_window_does_not_write(monkeypatch):
    """Outside the experiment window (before 2026-06-01 or after 2026-11-16),
    experiment mode must default to no-write. No 'preserve pre-experiment'
    fallback. Per spec §3 lock."""
    monkeypatch.setenv("SCHEDULER_MODE", "experiment")
    import importlib, app; importlib.reload(app)
    c4, climate = _mock_c4_client()
    action = app.ScheduleAction(13, 0, "LEAVE", cool_setpoint_f=78, fan_mode="Circulate")
    # Before experiment start
    pre_when = datetime.datetime(2026, 5, 25, 13, 0)
    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=pre_when,
    )
    assert applied is False
    climate.set_cool_setpoint.assert_not_awaited()

    # After experiment end
    post_when = datetime.datetime(2026, 11, 25, 13, 0)
    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=post_when,
    )
    assert applied is False


@pytest.mark.asyncio
async def test_production_mode_writes_regardless_of_calendar(monkeypatch):
    """Production mode ignores A/B calendar entirely. Used for non-study operation."""
    monkeypatch.setenv("SCHEDULER_MODE", "production")
    import importlib, app; importlib.reload(app)
    c4, climate = _mock_c4_client()
    action = app.ScheduleAction(13, 0, "LEAVE", cool_setpoint_f=78, fan_mode="Circulate")
    # Even during what would be Arm A in experiment mode, production writes
    when_ct = datetime.datetime(2026, 6, 5, 13, 0)
    applied, _ = await app.execute_action(
        c4, action, cool_setpoint_to_apply=78, heat_setpoint_to_apply=65,
        state={"hvac_mode": "Cool"}, dry_run=False, when_ct=when_ct,
    )
    assert applied is True


def test_invalid_scheduler_mode_fails_startup(monkeypatch):
    """Unknown or invalid SCHEDULER_MODE: refuse to start (sys.exit), per spec §3."""
    monkeypatch.setenv("SCHEDULER_MODE", "bogus")
    import importlib
    with pytest.raises(SystemExit) as exc_info:
        import app; importlib.reload(app)
    assert exc_info.value.code == 2


def test_missing_scheduler_mode_fails_startup(monkeypatch):
    """No default. SCHEDULER_MODE must be set explicitly."""
    monkeypatch.delenv("SCHEDULER_MODE", raising=False)
    import importlib
    with pytest.raises(SystemExit) as exc_info:
        import app; importlib.reload(app)
    assert exc_info.value.code == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd deploy/energy-stack/hvac-scheduler && python -m pytest test_hvac_scheduler.py -k "scheduler_mode or experiment_mode or shadow_mode or production_mode" -v`
Expected: FAIL — `SCHEDULER_MODE` env handling and `_writes_allowed` gate not yet implemented.

- [ ] **Step 3: Write minimal implementation**

**Import-path note (M5 resolution):** the hvac-scheduler container does NOT have `tools/analysis/` on its PYTHONPATH (per existing service-isolation pattern). Solution: duplicate `arm_calendar.py` into `deploy/energy-stack/hvac-scheduler/arm_calendar.py` and add a CI hash-sync check at every PR that asserts the two files are byte-identical. This respects the existing per-service Dockerfile pattern and avoids cross-service Python-path coupling.

Add `deploy/energy-stack/hvac-scheduler/arm_calendar.py` as a copy of `tools/analysis/arm_calendar.py`. Add to CI: `python -c "import hashlib, pathlib; a = hashlib.sha256(pathlib.Path('tools/analysis/arm_calendar.py').read_bytes()).hexdigest(); b = hashlib.sha256(pathlib.Path('deploy/energy-stack/hvac-scheduler/arm_calendar.py').read_bytes()).hexdigest(); assert a == b, f'arm_calendar.py copies out of sync ({a} != {b})'"`.

In `deploy/energy-stack/hvac-scheduler/app.py`:

```python
import os
import sys
from arm_calendar import current_arm_at  # local copy, hash-sync-checked in CI

VALID_MODES = ("shadow", "experiment", "production")


def _get_scheduler_mode() -> str:
    """Read SCHEDULER_MODE env var. No default — must be set explicitly.
    Fail closed (refuse to start) on missing or invalid mode per spec §3.
    """
    mode = os.environ.get("SCHEDULER_MODE")
    if mode not in VALID_MODES:
        log("error", "scheduler_mode_invalid",
            value=mode, valid=VALID_MODES,
            message="SCHEDULER_MODE must be set to one of: shadow, experiment, production. Refusing to start.")
        sys.exit(2)
    log("info", "scheduler_mode_active", mode=mode)
    return mode


SCHEDULER_MODE = _get_scheduler_mode()


def _writes_allowed(when_ct: datetime.datetime) -> bool:
    """Per spec §3 SCHEDULER_MODE gating.

    - shadow: never writes
    - experiment: writes ONLY during Arm B periods inside the locked window
    - production: writes always (ignores A/B calendar)
    """
    if SCHEDULER_MODE == "shadow":
        return False
    if SCHEDULER_MODE == "production":
        return True
    # SCHEDULER_MODE == "experiment"
    current_arm = current_arm_at(when_ct)
    # current_arm is None when outside the experiment window → no writes
    return current_arm == "B"


async def execute_action(
    c4, action, cool_setpoint_to_apply, heat_setpoint_to_apply,
    state, dry_run, when_ct=None,
):
    if when_ct is None:
        when_ct = datetime.datetime.now()

    # Top-level mode gate (spec §3): explicit SCHEDULER_MODE controls
    # whether the setpoint-write path can run at all.
    if not _writes_allowed(when_ct):
        return False, None

    # existing execute_action body unchanged from here
    ...
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest test_hvac_scheduler.py -v`
Expected: PASS — both new tests + all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac-scheduler/app.py deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac-scheduler): add arm-mode gating around execute_action (spec §3)"
```

---

### Task 1.3: Add hvac.arm_mode telemetry

**Files:**
- Modify: `deploy/energy-stack/hvac-scheduler/app.py` (write `hvac.arm_mode` per decision cycle)
- Test: `deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py`

Implements spec §11 fix-list item #2.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_write_arm_mode_writes_a_active_during_arm_a():
    from app import write_arm_mode_telemetry
    influx = _mock_influx_writer()
    when_ct = datetime.datetime(2026, 6, 5, 13, 0)
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
    await write_arm_mode_telemetry(influx, when_ct, feeds, controller_alive=True)
    influx.write.assert_called_once()
    point = influx.write.call_args.kwargs["point"]
    assert point.tag("arm") == "A"
    assert point.field("mode_actual") == "A-active"


@pytest.mark.asyncio
async def test_write_arm_mode_writes_b_fallback_when_feed_stale():
    from app import write_arm_mode_telemetry
    influx = _mock_influx_writer()
    when_ct = datetime.datetime(2026, 6, 20, 13, 0)
    feeds = {"price": True, "weather": False, "pjm_capacity_risk": True}  # weather stale
    await write_arm_mode_telemetry(influx, when_ct, feeds, controller_alive=True)
    point = influx.write.call_args.kwargs["point"]
    assert point.tag("arm") == "B"
    assert point.field("mode_actual") == "B-fallback"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `write_arm_mode_telemetry` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
async def write_arm_mode_telemetry(influx, when_ct, feeds, controller_alive):
    arm = current_arm_at(when_ct)
    if arm == "A":
        mode = "A-active"
    elif arm == "B":
        if not controller_alive:
            mode = "B-down"
        elif not all(feeds.values()):
            mode = "B-fallback"
        else:
            mode = "B-active"
    else:
        return  # outside experiment window
    point = (Point("hvac.arm_mode")
             .time(when_ct)
             .tag("arm", arm)
             .field("mode_actual", mode))
    await influx.write(bucket=BUCKET, point=point)
```

Wire into the 5-min decision cycle. Pass `feeds` dict from existing input-fetch logic; pass `controller_alive=True` (always — the watchdog handles the alternative).

- [ ] **Step 4: Run tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac-scheduler/app.py deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac-scheduler): add hvac.arm_mode telemetry per cycle (spec §11 #2)"
```

---

### Task 1.4: Add hvac.switch_event logging at boundaries

**Files:**
- Modify: `deploy/energy-stack/hvac-scheduler/app.py`
- Test: existing test file

Implements spec §11 fix-list item #3.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_switch_event_logged_at_arm_boundary():
    from app import maybe_log_arm_switch
    influx = _mock_influx_writer()
    # At 00:00 Mon 2026-06-15 we cross from Arm A (period 1) to Arm B (period 2)
    last_arm = "A"
    when_ct = datetime.datetime(2026, 6, 15, 0, 0)
    new_arm = await maybe_log_arm_switch(influx, last_arm, when_ct)
    assert new_arm == "B"
    influx.write.assert_called_once()
    point = influx.write.call_args.kwargs["point"]
    assert point.field("from_arm") == "A"
    assert point.field("to_arm") == "B"
```

- [ ] **Step 2: Run test, fails**

- [ ] **Step 3: Write implementation**

```python
async def maybe_log_arm_switch(influx, last_arm, when_ct):
    current = current_arm_at(when_ct)
    if current != last_arm:
        point = (Point("hvac.switch_event")
                 .time(when_ct)
                 .field("from_arm", last_arm or "")
                 .field("to_arm", current or "")
                 .field("boundary_planned_ts", when_ct.replace(minute=0, second=0, microsecond=0).isoformat())
                 .field("boundary_actual_ts", when_ct.isoformat()))
        await influx.write(bucket=BUCKET, point=point)
    return current
```

Wire into main loop: track `last_arm` across cycles, call on each cycle.

- [ ] **Step 4: Run tests, PASS**

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac-scheduler/app.py deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac-scheduler): log arm-switch events at boundaries (spec §11 #3)"
```

---

### Task 1.5: Add input-feed health telemetry

**Files:**
- Modify: `deploy/energy-stack/hvac-scheduler/app.py`
- Test: existing test file

Implements spec §11 fix-list item #4. Reuse the `feeds` dict from Task 1.3.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_input_feed_health_writes_3_rows():
    from app import write_input_feed_health
    influx = _mock_influx_writer()
    when_ct = datetime.datetime(2026, 6, 20, 13, 0)
    feeds = {"price": True, "weather": False, "pjm_capacity_risk": True}
    await write_input_feed_health(influx, when_ct, feeds)
    assert influx.write.call_count == 3
    points = [c.kwargs["point"] for c in influx.write.call_args_list]
    health_map = {p.tag("feed"): p.field("healthy") for p in points}
    assert health_map == {"price": True, "weather": False, "pjm_capacity_risk": True}
```

- [ ] **Step 2: Run test, fails**

- [ ] **Step 3: Implementation**

```python
async def write_input_feed_health(influx, when_ct, feeds):
    for feed_name, healthy in feeds.items():
        point = (Point("hvac.input_feed_health")
                 .time(when_ct)
                 .tag("feed", feed_name)
                 .field("healthy", healthy))
        await influx.write(bucket=BUCKET, point=point)
```

Wire into decision cycle alongside arm_mode write.

- [ ] **Step 4: Run tests, PASS**

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac-scheduler/app.py deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac-scheduler): add input-feed health telemetry (spec §11 #4)"
```

---

### Task 1.6: Add controller heartbeat watchdog

**Files:**
- Create: `deploy/energy-stack/hvac-scheduler-watchdog/check.sh`
- Create: `deploy/energy-stack/hvac-scheduler-watchdog/Dockerfile` (or extend systemd unit)
- Modify: `deploy/energy-stack/docker-compose.yml` (add watchdog service or cron)
- Test: `deploy/energy-stack/hvac-scheduler-watchdog/test_check.py`

Implements spec §11 fix-list item #5.

- [ ] **Step 1: Write the integration test (L4 resolution)**

The watchdog is load-bearing for B-down detection. Manual-verify-after-deploy is insufficient. Use docker-compose integration test that brings up an ephemeral InfluxDB, simulates a scheduler crash (no recent hvac.arm_mode rows), runs the watchdog, and asserts `controller_alive=false` was written.

```python
# test_check_integration.py
import subprocess
import time
import pytest
from pathlib import Path

COMPOSE = Path(__file__).parent / "docker-compose-test.yml"


@pytest.fixture(scope="module")
def ephemeral_influx():
    """Bring up an ephemeral influx via docker-compose-test.yml."""
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), "up", "-d"], check=True)
    time.sleep(5)  # influx warmup
    yield
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), "down", "-v"], check=True)


def test_watchdog_writes_alive_false_when_no_recent_arm_mode(ephemeral_influx):
    # Write a stale arm_mode row (15 min ago)
    subprocess.run([
        "docker", "compose", "-f", str(COMPOSE), "exec", "-T", "influxdb-test",
        "influx", "write", "-b", "energy-test",
        f"hvac.arm_mode,arm=A mode_actual=\"A-active\" {int((time.time()-900)*1e9)}"
    ], check=True)

    # Run the watchdog
    result = subprocess.run(["bash", "check.sh"], capture_output=True, text=True,
                            env={"INFLUX_BUCKET": "energy-test"})
    assert result.returncode == 0

    # Verify a controller_alive=false row was written
    query_result = subprocess.run([
        "docker", "compose", "-f", str(COMPOSE), "exec", "-T", "influxdb-test",
        "influx", "query", "--raw",
        'from(bucket:"energy-test") |> range(start: -1m) |> filter(fn: (r) => r._measurement == "hvac.heartbeat")'
    ], capture_output=True, text=True, check=True)
    assert "controller_alive" in query_result.stdout
    assert "false" in query_result.stdout


def test_watchdog_does_not_fire_when_recent_arm_mode_present(ephemeral_influx):
    # Write a fresh arm_mode row
    subprocess.run([
        "docker", "compose", "-f", str(COMPOSE), "exec", "-T", "influxdb-test",
        "influx", "write", "-b", "energy-test",
        f"hvac.arm_mode,arm=B mode_actual=\"B-active\" {int(time.time()*1e9)}"
    ], check=True)

    result = subprocess.run(["bash", "check.sh"], capture_output=True, text=True,
                            env={"INFLUX_BUCKET": "energy-test"})
    assert result.returncode == 0

    # No controller_alive=false should have been written in this run
    # (would need to filter heartbeat rows by recent timestamp)
    ...
```

Plus `docker-compose-test.yml` with a single `influxdb-test` service on an ephemeral volume. Adds ~2 hours of test-infra work but eliminates manual-verification risk on the load-bearing primitive.

- [ ] **Step 2-4**

Implementation:

```bash
#!/usr/bin/env bash
# check.sh — runs every 5 min via cron or systemd timer
set -euo pipefail

QUERY='from(bucket: "energy")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "hvac.arm_mode")
  |> count()
  |> findRecord(fn: (key) => true, idx: 0)'

ROWS=$(docker exec influxdb influx query --raw "$QUERY" | grep -c '^,,' || true)

if [ "$ROWS" -eq 0 ]; then
  # No arm_mode row in last 10 min → controller is down
  echo "no recent arm_mode rows; writing controller_alive=false"
  docker exec influxdb influx write \
    -b energy \
    "hvac.heartbeat controller_alive=false"
fi
```

Wire as cron entry on pi-lab: `*/5 * * * * /home/chris/energy-stack/scripts/check.sh`.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac-scheduler-watchdog/
git commit -m "feat(watchdog): controller heartbeat detection (spec §11 #5)"
```

---

### Task 1.7: Dry-run guard comprehensive audit

**Files:**
- Add tests: `deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py`

Implements spec §11 fix-list item #9. Verify NO branch of `execute_action` calls Control4 setpoint-write methods when `dry_run=True`.

- [ ] **Step 1: Enumerate every execute_action branch**

Read current `execute_action` in `app.py`. Identify all action types: setpoint push, hold mode, fan mode, release-hold, mid-period repush, vacation override. Make a list.

- [ ] **Step 2: Write a parametrized test covering every branch**

```python
@pytest.mark.parametrize("action_label,kwargs", [
    ("AWAKE", {"cool_setpoint_to_apply": 73, "fan_mode": "Auto"}),
    ("LEAVE", {"cool_setpoint_to_apply": 78, "fan_mode": "Circulate"}),
    ("RETURN", {"cool_setpoint_to_apply": 75}),
    ("SLEEP", {"cool_setpoint_to_apply": 76}),
    ("MILD_RELEASE_HOLD", {"release_hold": True}),
    ("MID_PERIOD_REPUSH:PRE_COOL", {"cool_setpoint_to_apply": 68}),
    # ... every action label
])
@pytest.mark.asyncio
async def test_dry_run_never_calls_control4_for_any_action(action_label, kwargs):
    c4, climate = _mock_c4_client()
    action = ScheduleAction(13, 0, action_label, **{k: v for k, v in kwargs.items() if not callable(v)})
    await execute_action(c4, action, dry_run=True, **kwargs)
    climate.set_cool_setpoint.assert_not_awaited()
    climate.set_heat_setpoint.assert_not_awaited()
    climate.set_fan_mode.assert_not_awaited()
    climate.set_hold_mode.assert_not_awaited()
```

- [ ] **Step 3: Run, identify any branch that DOES call Control4 on dry_run=True**

Fix any branch that fails the test.

- [ ] **Step 4: Re-run, PASS**

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py
git commit -m "test(hvac-scheduler): comprehensive dry-run guard audit (spec §11 #9)"
```

---

### Task 1.8: Phase 1 PR + merge

- [ ] **Step 1: Run full test suite**

```bash
bash deploy/energy-stack/run_tests.sh
```

- [ ] **Step 2: Push branch, open PR**

```bash
git push -u origin sced-rebaseline-phase1
gh pr create --base main --title "Phase 1: SCED rebaseline telemetry foundation" --body "$(cat <<'EOF'
## Summary
- Locked arm calendar (12 arms, 2026-06-01 → 2026-11-16)
- Arm-mode gating around execute_action (Arm A = no-op, Arm B = active)
- hvac.arm_mode / hvac.switch_event / hvac.input_feed_health / hvac.heartbeat telemetry
- Dry-run guard comprehensive test coverage

Spec: docs/plans/sced-rebaseline-spec-2026-05-13.md §3, §11.

## Test plan
- [ ] Full pytest suite passes
- [ ] Deploy to pi-lab; verify hvac.arm_mode rows appear in InfluxDB
- [ ] Verify no thermostat writes under SCHEDULER_MODE=shadow (and under SCHEDULER_MODE=experiment during Arm A periods)
EOF
)"
```

Stop here for user review + merge.

---

## Phase 2: Pricing infrastructure

**Goal:** Add settled hourly LMP (`rt_hrl_lmps`) to InfluxDB. Backfill 2026-01-01 → present. Add DTOD analysis-rate table for use in HVAC$ formula.

**Days 4-5.**

---

### Task 2.1: Add rt_hrl_lmps Flux query

**Files:**
- Create: `tools/analysis/queries/rt_hrl_lmps.flux`

Implements spec §8 (pricing layer).

- [ ] **Step 1: Write the query**

```flux
// Per-hour PJM settled real-time LMP for COMED zone, written by
// pjm-dm2-poller. Bill-canonical supply price for Rate BESH.
// pnode_id=33092371 (COMED zonal aggregate).
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "pjm.lmp_rt_hourly")
  |> filter(fn: (r) => r._field == "total_lmp_rt" or r._field == "system_energy_price_rt" or r._field == "congestion_price_rt" or r._field == "marginal_loss_price_rt")
  |> filter(fn: (r) => r.pnode_id == "33092371")
  |> yield(name: "rt_hrl_lmps")
```

- [ ] **Step 2: Add to manifest KNOWN_MEASUREMENTS**

In `tools/analysis/replay/manifest.py`, add `"pjm.lmp_rt_hourly"` to KNOWN_MEASUREMENTS and POST_2025_MEASUREMENTS sets.

- [ ] **Step 3: Commit**

```bash
git checkout main && git pull --ff-only && git checkout -b sced-rebaseline-phase2
git add tools/analysis/queries/rt_hrl_lmps.flux tools/analysis/replay/manifest.py
git commit -m "feat(analysis): add rt_hrl_lmps Flux query + manifest entry (spec §8)"
```

---

### Task 2.2: Extend pjm-dm2-poller for rt_hrl_lmps

**Files:**
- Modify: `deploy/energy-stack/pjm-dm2-poller/app.py`
- Test: `deploy/energy-stack/pjm-dm2-poller/test_app.py`

- [ ] **Step 1: Write the failing test**

```python
# test_app.py
import pytest
from app import poll_rt_hrl_lmps


@pytest.mark.asyncio
async def test_poll_rt_hrl_lmps_includes_startrow_and_datetime_filter(mock_pjm_api):
    await poll_rt_hrl_lmps(target_date="2026-05-11")
    call_url = mock_pjm_api.last_request_url()
    assert "startRow=1" in call_url
    assert "datetime_beginning_ept=2026-05-11" in call_url
    assert "pnode_id=33092371" in call_url


@pytest.mark.asyncio
async def test_poll_rt_hrl_lmps_writes_24_hourly_points(mock_pjm_api_24_rows, mock_influx):
    await poll_rt_hrl_lmps(target_date="2026-05-11")
    assert mock_influx.write_count == 24
```

- [ ] **Step 2: Run, fails — poll_rt_hrl_lmps not defined**

- [ ] **Step 3: Implement**

```python
async def poll_rt_hrl_lmps(target_date: str):
    """Pull 24 hourly settled LMP rows for COMED zone for a date.
    target_date format: 'YYYY-MM-DD' in EPT.
    """
    url = (
        f"https://api.pjm.com/api/v1/rt_hrl_lmps"
        f"?rowCount=24&startRow=1"
        f"&datetime_beginning_ept={target_date}"
        f"&pnode_id={COMED_PNODE_ID}"
        f"&order=Asc&sort=datetime_beginning_ept"
    )
    response = await fetch_with_key(url)
    items = response.get("items", [])
    for row in items:
        point = (Point("pjm.lmp_rt_hourly")
                 .time(row["datetime_beginning_utc"])
                 .tag("pnode_id", str(row["pnode_id"]))
                 .tag("zone", row.get("zone") or "")
                 .field("total_lmp_rt", float(row["total_lmp_rt"]))
                 .field("system_energy_price_rt", float(row["system_energy_price_rt"]))
                 .field("congestion_price_rt", float(row.get("congestion_price_rt", 0)))
                 .field("marginal_loss_price_rt", float(row.get("marginal_loss_price_rt", 0))))
        await influx.write(bucket=BUCKET, point=point)
```

Schedule `poll_rt_hrl_lmps(yesterday)` to run daily at 11:30am ET (after PJM posts).

- [ ] **Step 4: Run tests, PASS**

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/pjm-dm2-poller/app.py deploy/energy-stack/pjm-dm2-poller/test_app.py
git commit -m "feat(pjm-dm2-poller): add rt_hrl_lmps daily polling (spec §11 #6)"
```

---

### Task 2.3: Backfill rt_hrl_lmps from 2026-01-01

**Files:**
- Create: `deploy/energy-stack/pjm-dm2-poller/backfill_rt_hrl_lmps.py`

- [ ] **Step 1: Write backfill script**

```python
# backfill_rt_hrl_lmps.py
"""One-shot backfill for rt_hrl_lmps. Pulls 2026-01-01 through yesterday.
Respects PJM rate limits with 5s sleep between calls.
"""
import asyncio
import datetime
from app import poll_rt_hrl_lmps


async def main():
    start = datetime.date(2026, 1, 1)
    end = datetime.date.today() - datetime.timedelta(days=1)
    current = start
    while current <= end:
        print(f"polling {current.isoformat()}")
        await poll_rt_hrl_lmps(target_date=current.isoformat())
        await asyncio.sleep(5)  # rate-limit margin
        current += datetime.timedelta(days=1)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run once, verify InfluxDB has full coverage**

```bash
ssh pi-lab 'docker exec pjm-dm2-poller python backfill_rt_hrl_lmps.py'
ssh pi-lab 'docker exec influxdb influx query --raw "
from(bucket: \"energy\")
  |> range(start: 2026-01-01)
  |> filter(fn: (r) => r._measurement == \"pjm.lmp_rt_hourly\")
  |> count()
"'
```

Expected: ~3000+ rows (≈130 days × 24 hours).

- [ ] **Step 3: Commit**

```bash
git add deploy/energy-stack/pjm-dm2-poller/backfill_rt_hrl_lmps.py
git commit -m "chore(pjm-dm2-poller): rt_hrl_lmps backfill script (spec §8)"
```

---

### Task 2.4: DTOD analysis-rate table module

**Files:**
- Create: `tools/analysis/dtod_rates.py`
- Test: `tools/analysis/tests/test_dtod_rates.py`

Implements spec §8 DTOD table.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from tools.analysis.dtod_rates import dtod_resultant_c_per_kwh, dtod_total_delivery_c_per_kwh


def test_morning_resultant():
    assert dtod_resultant_c_per_kwh(hour_ct=10) == pytest.approx(4.428)


def test_mid_day_peak_resultant():
    assert dtod_resultant_c_per_kwh(hour_ct=15) == pytest.approx(11.727)


def test_evening_resultant():
    assert dtod_resultant_c_per_kwh(hour_ct=20) == pytest.approx(4.142)


def test_overnight_resultant_late():
    assert dtod_resultant_c_per_kwh(hour_ct=22) == pytest.approx(3.311)


def test_overnight_resultant_early():
    assert dtod_resultant_c_per_kwh(hour_ct=3) == pytest.approx(3.311)


def test_total_delivery_includes_iedt():
    # Morning resultant 4.428 + IEDT 0.126 = 4.554
    assert dtod_total_delivery_c_per_kwh(hour_ct=10) == pytest.approx(4.554)


def test_out_of_range_raises():
    with pytest.raises(ValueError):
        dtod_resultant_c_per_kwh(hour_ct=24)
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""DTOD Distribution Time-of-Day resultant rates per docs/plans/
sced-rebaseline-spec-2026-05-13.md §8.

Source: ComEd April 2026 Tariff Worksheet, Residential Single Family
Without Electric Space Heat (DTOD class). Resultant rates include all
riders (IDUF, RBAF, TPAF, DGRA, DSPR). IEDT is a separate flat per-kWh
charge that applies regardless.

This module is FROZEN at OSF filing. Rate changes mid-experiment are
protocol deviations requiring amendment.
"""
from __future__ import annotations

IEDT_C_PER_KWH = 0.126  # Illinois Electricity Distribution Tax, per-kWh

# (start_hour_inclusive, end_hour_exclusive, resultant_c_per_kwh)
DTOD_PERIODS_RESULTANT_C: tuple[tuple[int, int, float], ...] = (
    ( 6, 13,  4.428),  # Morning
    (13, 19, 11.727),  # Mid-Day Peak
    (19, 21,  4.142),  # Evening
    (21, 24,  3.311),  # Overnight (late)
    ( 0,  6,  3.311),  # Overnight (early)
)

TARIFF_SOURCE = "ComEd April 2026 Tariff Worksheet, Residential SF Without Electric Heat DTOD"
TARIFF_EFFECTIVE_DATE = "2026-04-25"


def dtod_resultant_c_per_kwh(hour_ct: int) -> float:
    if not 0 <= hour_ct <= 23:
        raise ValueError(f"hour_ct must be in [0, 23], got {hour_ct}")
    for start, end, rate in DTOD_PERIODS_RESULTANT_C:
        if start <= hour_ct < end:
            return rate
    raise RuntimeError(f"DTOD schedule does not cover hour_ct={hour_ct}")


def dtod_total_delivery_c_per_kwh(hour_ct: int) -> float:
    """Total per-kWh delivery rate = DTOD resultant + IEDT."""
    return dtod_resultant_c_per_kwh(hour_ct) + IEDT_C_PER_KWH
```

- [ ] **Step 4: Run tests, PASS**

- [ ] **Step 5: Commit**

```bash
git add tools/analysis/dtod_rates.py tools/analysis/tests/test_dtod_rates.py
git commit -m "feat(analysis): DTOD resultant rate table for analysis (spec §8)"
```

---

### Task 2.5: Phase 2 PR + merge

Same flow as Task 1.8. Open PR `Phase 2: SCED rebaseline pricing infrastructure`.

---

## Phase 3: Analysis pipeline rewrite

**Goal:** Replace weekly Stage 3/5 with arm-period-shaped pipeline. Implement single validity gate, cost-matched exclusion, Euclidean Hungarian matching, per-pair table.

**Days 6-10.**

---

### Task 3.1: Mode classification module

**Files:**
- Create: `tools/analysis/mode_classification.py`
- Test: `tools/analysis/tests/test_mode_classification.py`

Implements spec §5 4-mode classification.

- [ ] **Step 1: Write the failing test**

```python
import datetime
from tools.analysis.mode_classification import classify_hour, HourMode

# In-capacity-risk-window timestamp (June)
TS_IN_WINDOW = datetime.datetime(2026, 7, 15, 14, 0)
# Out-of-capacity-risk-window timestamp (October)
TS_OUT_WINDOW = datetime.datetime(2026, 10, 20, 14, 0)


def test_arm_a_telemetry_valid_is_a_active():
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
    assert classify_hour(arm="A", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.A_ACTIVE


def test_arm_a_telemetry_invalid_is_invalid():
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
    assert classify_hour(arm="A", when_ct=TS_IN_WINDOW, telemetry_valid=False,
                         controller_alive=True, feeds=feeds) == HourMode.TELEMETRY_INVALID


def test_arm_b_all_required_healthy_in_window_is_b_active():
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_ACTIVE


def test_arm_b_controller_dead_is_b_down():
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": True}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=False, feeds=feeds) == HourMode.B_DOWN


def test_arm_b_price_stale_in_window_is_b_fallback():
    feeds = {"price": False, "weather": True, "pjm_capacity_risk": True}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_FALLBACK


def test_arm_b_weather_stale_in_window_is_b_fallback():
    feeds = {"price": True, "weather": False, "pjm_capacity_risk": True}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_FALLBACK


def test_arm_b_pjm_capacity_stale_INSIDE_window_is_b_fallback():
    """Inside capacity-risk operating window, PJM capacity-risk inputs ARE required."""
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": False}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_FALLBACK


def test_arm_b_pjm_capacity_stale_OUTSIDE_window_is_b_active():
    """Outside capacity-risk operating window, PJM capacity-risk inputs are NOT required."""
    feeds = {"price": True, "weather": True, "pjm_capacity_risk": False}
    assert classify_hour(arm="B", when_ct=TS_OUT_WINDOW, telemetry_valid=True,
                         controller_alive=True, feeds=feeds) == HourMode.B_ACTIVE


def test_arm_b_telemetry_invalid_dominates():
    """Telemetry-invalid wins over treatment-mode status."""
    feeds = {"price": False, "weather": False, "pjm_capacity_risk": False}
    assert classify_hour(arm="B", when_ct=TS_IN_WINDOW, telemetry_valid=False,
                         controller_alive=False, feeds=feeds) == HourMode.TELEMETRY_INVALID
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""Hour-level mode classification per spec §5 + §5.1.

4 active modes + telemetry-invalid:
- A_ACTIVE: Arm A, all systems good
- B_ACTIVE: Arm B, controller healthy, all REQUIRED-for-that-hour feeds healthy
  - price + weather required ALL Arm B hours
  - pjm_capacity_risk required ONLY during capacity-risk operating window
- B_FALLBACK: Arm B, controller alive, ≥1 required-for-that-hour feed stale
- B_DOWN: Arm B, controller process not writing
- TELEMETRY_INVALID: HVAC measurement insufficient regardless of arm
"""
from __future__ import annotations
import datetime
from enum import Enum
from typing import Literal

# Pre-registered capacity-risk operating window per spec §5.1
CAPACITY_RISK_WINDOW_START = datetime.datetime(2026, 6, 1, 0, 0)
CAPACITY_RISK_WINDOW_END = datetime.datetime(2026, 10, 1, 0, 0)  # exclusive


class HourMode(Enum):
    A_ACTIVE = "A-active"
    B_ACTIVE = "B-active"
    B_FALLBACK = "B-fallback"
    B_DOWN = "B-down"
    TELEMETRY_INVALID = "telemetry-invalid"


def in_capacity_risk_window(when_ct: datetime.datetime) -> bool:
    return CAPACITY_RISK_WINDOW_START <= when_ct < CAPACITY_RISK_WINDOW_END


def required_feeds_at(when_ct: datetime.datetime) -> set[str]:
    """Per spec §5: price + weather always; pjm_capacity_risk only in-window."""
    required = {"price", "weather"}
    if in_capacity_risk_window(when_ct):
        required.add("pjm_capacity_risk")
    return required


def classify_hour(
    *,
    arm: Literal["A", "B"],
    when_ct: datetime.datetime,
    telemetry_valid: bool,
    controller_alive: bool,
    feeds: dict[str, bool],
) -> HourMode:
    if not telemetry_valid:
        return HourMode.TELEMETRY_INVALID
    if arm == "A":
        return HourMode.A_ACTIVE
    # arm == "B"
    if not controller_alive:
        return HourMode.B_DOWN
    # Check only the required-for-this-hour feeds
    required = required_feeds_at(when_ct)
    for feed_name in required:
        if not feeds.get(feed_name, False):
            return HourMode.B_FALLBACK
    return HourMode.B_ACTIVE


def is_fully_valid(mode: HourMode) -> bool:
    """Per spec §5: fully-valid = telemetry-valid AND in intended treatment mode."""
    return mode in (HourMode.A_ACTIVE, HourMode.B_ACTIVE)
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git checkout main && git pull --ff-only && git checkout -b sced-rebaseline-phase3
git add tools/analysis/mode_classification.py tools/analysis/tests/test_mode_classification.py
git commit -m "feat(analysis): 4-mode hour classification (spec §5)"
```

---

### Task 3.2: HVAC telemetry validity check per hour

**Files:**
- Create: `tools/analysis/hvac_telemetry_validity.py`
- Test: `tools/analysis/tests/test_hvac_telemetry_validity.py`

Implements spec §7 per-hour validity rule.

- [ ] **Step 1: Write the failing test**

```python
import datetime
import pandas as pd
from tools.analysis.hvac_telemetry_validity import hour_is_telemetry_valid


def _samples(start: datetime.datetime, count: int, gap_seconds: int = 30):
    return pd.DataFrame({
        "_time": [start + datetime.timedelta(seconds=gap_seconds * i) for i in range(count)],
        "_value": [100.0] * count,
        "channel": ["em:2"] * count,
    })


def test_120_samples_no_gap_is_valid():
    start = datetime.datetime(2026, 6, 5, 14, 0)
    df = pd.concat([_samples(start, 120, 30).assign(channel=ch) for ch in ("em:2", "em:8", "em:9")])
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is True


def test_below_110_samples_is_invalid():
    start = datetime.datetime(2026, 6, 5, 14, 0)
    df = pd.concat([_samples(start, 105, 30).assign(channel=ch) for ch in ("em:2", "em:8", "em:9")])
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is False


def test_gap_over_120s_is_invalid():
    start = datetime.datetime(2026, 6, 5, 14, 0)
    # 60 samples at 30s + 121s gap + 60 samples at 30s
    df1 = _samples(start, 60, 30).assign(channel="em:2")
    df2 = _samples(start + datetime.timedelta(seconds=60 * 30 + 121), 60, 30).assign(channel="em:2")
    df = pd.concat([df1, df2, _samples(start, 120, 30).assign(channel="em:8"),
                    _samples(start, 120, 30).assign(channel="em:9")])
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is False


def test_any_channel_failure_kills_hour():
    """em:9 has only 100 samples; em:2/em:8 are fine. Hour fails."""
    start = datetime.datetime(2026, 6, 5, 14, 0)
    df = pd.concat([
        _samples(start, 120, 30).assign(channel="em:2"),
        _samples(start, 120, 30).assign(channel="em:8"),
        _samples(start, 100, 30).assign(channel="em:9"),
    ])
    assert hour_is_telemetry_valid(df, hour_start_utc=start) is False
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""Per-hour HVAC telemetry validity per spec §7.

Rules:
- ≥110 samples per HVAC channel in the hour (= 92% of nominal 120 at 30s cadence)
- No single-channel intra-hour gap >120 seconds (2 min)
- ANY-channel-failure rule: if ANY of {em:2, em:8, em:9} fails either threshold,
  the hour is telemetry-invalid.
"""
from __future__ import annotations
import datetime
import pandas as pd

MIN_SAMPLES_PER_CHANNEL = 110
MAX_INTRA_HOUR_GAP_SECONDS = 120
HVAC_CHANNELS = ("em:2", "em:8", "em:9")


def hour_is_telemetry_valid(
    refoss_df: pd.DataFrame,
    hour_start_utc: datetime.datetime,
) -> bool:
    """refoss_df: DataFrame with columns _time, _value, channel.
    hour_start_utc: hour boundary (UTC).
    """
    hour_end = hour_start_utc + datetime.timedelta(hours=1)
    hour_df = refoss_df[(refoss_df["_time"] >= hour_start_utc) &
                       (refoss_df["_time"] < hour_end)]
    for ch in HVAC_CHANNELS:
        ch_df = hour_df[hour_df["channel"] == ch].sort_values("_time")
        if len(ch_df) < MIN_SAMPLES_PER_CHANNEL:
            return False
        if len(ch_df) >= 2:
            gaps = ch_df["_time"].diff().dropna().dt.total_seconds()
            if (gaps > MAX_INTRA_HOUR_GAP_SECONDS).any():
                return False
    return True
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add tools/analysis/hvac_telemetry_validity.py tools/analysis/tests/test_hvac_telemetry_validity.py
git commit -m "feat(analysis): per-hour HVAC telemetry validity (spec §7)"
```

---

### Task 3.3: Arm-period validity gate (single gate)

**Files:**
- Create: `tools/analysis/validity_gate.py`
- Test: `tools/analysis/tests/test_validity_gate.py`

Implements spec §5 / §7 single pre-matching gate.

- [ ] **Step 1: Write the failing test**

```python
from tools.analysis.validity_gate import arm_passes_validity_gate
from tools.analysis.mode_classification import HourMode


def test_259_fully_valid_passes():
    modes = [HourMode.A_ACTIVE] * 259 + [HourMode.TELEMETRY_INVALID] * 29
    assert arm_passes_validity_gate(modes) is True


def test_258_fully_valid_fails():
    modes = [HourMode.A_ACTIVE] * 258 + [HourMode.TELEMETRY_INVALID] * 30
    assert arm_passes_validity_gate(modes) is False


def test_continuous_invalid_run_over_24h_fails():
    # 270 valid hours but 25 contiguous invalid → fails the continuous-cap rule
    modes = ([HourMode.A_ACTIVE] * 100
             + [HourMode.TELEMETRY_INVALID] * 25
             + [HourMode.A_ACTIVE] * 163)
    assert arm_passes_validity_gate(modes) is False


def test_b_fallback_counts_as_invalid_for_gate():
    """B-fallback is NOT fully-valid even though telemetry is fine."""
    modes = [HourMode.B_ACTIVE] * 258 + [HourMode.B_FALLBACK] * 30
    assert arm_passes_validity_gate(modes) is False
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""Single pre-matching arm-period validity gate per spec §5 / §7.

Rules:
- ≥259 of 288 fully-valid hours (= ≥90%)
- No contiguous invalid run >24 hours

Fully-valid hour = A_ACTIVE (for Arm A) or B_ACTIVE (for Arm B).
"""
from __future__ import annotations
from tools.analysis.mode_classification import HourMode, is_fully_valid

MIN_FULLY_VALID_HOURS = 259
MAX_CONTINUOUS_INVALID_HOURS = 24


def arm_passes_validity_gate(modes: list[HourMode]) -> bool:
    if len(modes) != 288:
        raise ValueError(f"expected 288 modes, got {len(modes)}")
    fully_valid_count = sum(1 for m in modes if is_fully_valid(m))
    if fully_valid_count < MIN_FULLY_VALID_HOURS:
        return False
    # Check continuous invalid run
    current_run = 0
    for m in modes:
        if is_fully_valid(m):
            current_run = 0
        else:
            current_run += 1
            if current_run > MAX_CONTINUOUS_INVALID_HOURS:
                return False
    return True
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add tools/analysis/validity_gate.py tools/analysis/tests/test_validity_gate.py
git commit -m "feat(analysis): single pre-matching validity gate (spec §5)"
```

---

### Task 3.4: Per-hour HVAC$ computation

**Files:**
- Create: `tools/analysis/hvac_dollars.py`
- Test: `tools/analysis/tests/test_hvac_dollars.py`

Implements spec §4 HVAC$ formula.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from tools.analysis.hvac_dollars import hvac_dollars_for_hour, HourlyRateInputs


def test_hvac_dollars_basic_computation():
    inputs = HourlyRateInputs(
        rt_hrl_lmps_per_mwh=25.32,    # 2.532 ¢/kWh
        pea_c_per_kwh=1.773,           # April 2026 snapshot
        transmission_c_per_kwh=1.083,
        misc_procurement_c_per_kwh=0.062,
        dtod_total_delivery_c_per_kwh=4.554,   # Morning band
        variable_riders_c_per_kwh=1.16,        # Σ per-kWh riders
        carbon_free_credit_c_per_kwh=-3.186,   # April 2026 credit
    )
    hvac_kwh = 1.5
    cents = hvac_dollars_for_hour(hvac_kwh, inputs)
    # 1.5 × (2.532 + 1.773 + 1.083 + 0.062 + 4.554 + 1.16 - 3.186)
    # = 1.5 × 7.978 = 11.967 cents
    assert cents == pytest.approx(11.967)


def test_zero_kwh_yields_zero_cost():
    inputs = HourlyRateInputs(
        rt_hrl_lmps_per_mwh=50, pea_c_per_kwh=1, transmission_c_per_kwh=1,
        misc_procurement_c_per_kwh=0, dtod_total_delivery_c_per_kwh=5,
        variable_riders_c_per_kwh=1, carbon_free_credit_c_per_kwh=0,
    )
    assert hvac_dollars_for_hour(0.0, inputs) == 0.0


def test_negative_lmp_handled():
    """During negative-LMP hours, supply is a credit. Math should still work."""
    inputs = HourlyRateInputs(
        rt_hrl_lmps_per_mwh=-2.94,  # -0.294 ¢/kWh
        pea_c_per_kwh=1.773, transmission_c_per_kwh=1.083,
        misc_procurement_c_per_kwh=0.062, dtod_total_delivery_c_per_kwh=3.437,
        variable_riders_c_per_kwh=1.16, carbon_free_credit_c_per_kwh=-3.186,
    )
    hvac_kwh = 1.0
    # 1.0 × (-0.294 + 1.773 + 1.083 + 0.062 + 3.437 + 1.16 - 3.186) = 4.035
    assert hvac_dollars_for_hour(hvac_kwh, inputs) == pytest.approx(4.035)
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""Per-hour HVAC$ computation per spec §4."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class HourlyRateInputs:
    rt_hrl_lmps_per_mwh: float        # PJM settled hourly LMP, $/MWh
    pea_c_per_kwh: float              # Purchased Electricity Adjustment, ¢/kWh
    transmission_c_per_kwh: float     # Transmission Services Charge, ¢/kWh
    misc_procurement_c_per_kwh: float
    dtod_total_delivery_c_per_kwh: float  # DTOD resultant + IEDT
    variable_riders_c_per_kwh: float  # Σ per-kWh riders (RPS, EE, ZES, etc.)
    carbon_free_credit_c_per_kwh: float  # negative = credit


def hourly_rate_c_per_kwh(inputs: HourlyRateInputs) -> float:
    """Bill-canonical per-kWh rate. ¢/kWh."""
    supply_lmp_c = inputs.rt_hrl_lmps_per_mwh / 10.0  # $/MWh → ¢/kWh
    return (
        supply_lmp_c
        + inputs.pea_c_per_kwh
        + inputs.transmission_c_per_kwh
        + inputs.misc_procurement_c_per_kwh
        + inputs.dtod_total_delivery_c_per_kwh
        + inputs.variable_riders_c_per_kwh
        + inputs.carbon_free_credit_c_per_kwh
    )


def hvac_dollars_for_hour(hvac_kwh: float, inputs: HourlyRateInputs) -> float:
    """Returns cost in cents."""
    return hvac_kwh * hourly_rate_c_per_kwh(inputs)
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add tools/analysis/hvac_dollars.py tools/analysis/tests/test_hvac_dollars.py
git commit -m "feat(analysis): HVAC dollar computation per hour (spec §4)"
```

---

### Task 3.5: Weather vector construction per arm period

**Files:**
- Create: `tools/analysis/weather_vector.py`
- Test: `tools/analysis/tests/test_weather_vector.py`

Implements spec §6 weather vector.

- [ ] **Step 1: Write the failing test**

```python
import datetime
import pandas as pd
import pytest
from tools.analysis.weather_vector import build_weather_vector, WeatherVector
from tools.analysis.arm_calendar import ARM_CALENDAR


def _ecowitt_df(start_utc, hours=288, temp_f=80, dewpoint_f=60):
    return pd.DataFrame({
        "_time": [start_utc + datetime.timedelta(hours=i) for i in range(hours)],
        "ch1_temp_f": [temp_f] * hours,
        "ch1_dewpoint_f": [dewpoint_f] * hours,
    })


def test_weather_vector_has_4_components():
    arm = ARM_CALENDAR[0]
    df = _ecowitt_df(start_utc=datetime.datetime(2026, 6, 3, 5, 0))
    vec = build_weather_vector(arm, df)
    assert isinstance(vec, WeatherVector)
    assert vec.cdd_total > 0
    assert vec.mean_daily_max_temp_f == pytest.approx(80.0)
    assert vec.mean_nocturnal_min_temp_f == pytest.approx(80.0)  # constant temp test
    assert vec.mean_dewpoint_f == pytest.approx(60.0)


def test_cdd_total_sum_over_12_days():
    arm = ARM_CALENDAR[0]
    # 12 days × 24 hours × (80 - 65)°F / 24 = 12 × 15 = 180 CDD
    df = _ecowitt_df(start_utc=datetime.datetime(2026, 6, 3, 5, 0), temp_f=80)
    vec = build_weather_vector(arm, df)
    assert vec.cdd_total == pytest.approx(180.0)


def test_nocturnal_min_uses_22_06_window():
    """Nocturnal min averages each day's MIN temp over 22:00-06:00 CT only."""
    arm = ARM_CALENDAR[0]
    # Construct: 95°F daytime, 70°F nocturnal (22:00-06:00 CT)
    # Then mean_daily_max_temp ≈ 95, mean_nocturnal_min ≈ 70
    df = ...  # construct with diurnal pattern
    vec = build_weather_vector(arm, df)
    assert vec.mean_daily_max_temp_f == pytest.approx(95.0, abs=1.0)
    assert vec.mean_nocturnal_min_temp_f == pytest.approx(70.0, abs=1.0)
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""4-component arm-period weather vector per spec §6.

Components:
- cdd_total: sum of hourly CDD (base 65°F) over 12 days
- mean_daily_max_temp_f: mean of each day's max temp over 12 days
- mean_nocturnal_min_temp_f: mean of each day's nocturnal (22:00-06:00 CT) min temp over 12 days
- mean_dewpoint_f: mean over all valid hours

Solar and wind dropped per spec §6 ("dropped from vector").
"""
from __future__ import annotations
import datetime
from dataclasses import dataclass
import pandas as pd
from zoneinfo import ZoneInfo
from tools.analysis.arm_calendar import ArmPeriod, post_washout_start, HOURS_PER_ARM

CDD_BASE_F = 65.0
CT = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class WeatherVector:
    cdd_total: float
    mean_daily_max_temp_f: float
    mean_nocturnal_min_temp_f: float
    mean_dewpoint_f: float

    def as_array(self):
        import numpy as np
        return np.array([
            self.cdd_total,
            self.mean_daily_max_temp_f,
            self.mean_nocturnal_min_temp_f,
            self.mean_dewpoint_f,
        ], dtype=float)


def build_weather_vector(arm: ArmPeriod, ecowitt_df: pd.DataFrame) -> WeatherVector:
    """Aggregate Ecowitt data over arm-period post-washout window.
    Uses ch1_temp_f (shaded) and ch1_dewpoint_f as canonical outdoor.
    """
    # CT-local → UTC via zoneinfo (L3 DST resolution)
    start_local = post_washout_start(arm).replace(tzinfo=CT)
    start_utc = start_local.astimezone(UTC).replace(tzinfo=None)
    end_utc = start_utc + datetime.timedelta(hours=HOURS_PER_ARM)
    df = ecowitt_df[(ecowitt_df["_time"] >= start_utc) & (ecowitt_df["_time"] < end_utc)].copy()

    # Convert times to CT for daily / nocturnal aggregation
    df["_time_ct"] = df["_time"].dt.tz_localize(UTC).dt.tz_convert(CT)
    df["date_ct"] = df["_time_ct"].dt.date
    df["hour_ct"] = df["_time_ct"].dt.hour

    # cdd_total
    hourly_temp = df["ch1_temp_f"]
    cdd_hourly = (hourly_temp - CDD_BASE_F).clip(lower=0) / 24.0
    cdd_total = cdd_hourly.sum()

    # mean_daily_max_temp_f
    daily_max = df.groupby("date_ct")["ch1_temp_f"].max()
    mean_daily_max = daily_max.mean()

    # mean_nocturnal_min_temp_f: filter to 22:00-06:00 CT, group by date, take min, mean across days
    # Note: 22:00-23:00 of date D belongs with 00:00-05:00 of date D+1 conceptually.
    # We define "nocturnal night N" = 22:00-23:59 of date N + 00:00-05:59 of date N+1.
    # Simpler: just filter hour_ct in [22,23,0,1,2,3,4,5], group by which night each hour belongs to.
    nocturnal_mask = df["hour_ct"].isin([22, 23, 0, 1, 2, 3, 4, 5])
    nocturnal_df = df[nocturnal_mask].copy()
    # Night identifier: hours 22-23 belong to the night starting that calendar date; hours 0-5 belong to the previous night.
    nocturnal_df["night"] = nocturnal_df.apply(
        lambda r: r["date_ct"] if r["hour_ct"] >= 22 else r["date_ct"] - datetime.timedelta(days=1),
        axis=1,
    )
    nightly_min = nocturnal_df.groupby("night")["ch1_temp_f"].min()
    mean_nocturnal_min = nightly_min.mean()

    return WeatherVector(
        cdd_total=float(cdd_total),
        mean_daily_max_temp_f=float(mean_daily_max),
        mean_nocturnal_min_temp_f=float(mean_nocturnal_min),
        mean_dewpoint_f=float(df["ch1_dewpoint_f"].mean()),
    )
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add tools/analysis/weather_vector.py tools/analysis/tests/test_weather_vector.py
git commit -m "feat(analysis): 4-component arm-period weather vector (spec §6)"
```

---

### Task 3.6: Euclidean z-score Hungarian matching

**Files:**
- Create: `tools/analysis/matching.py`
- Test: `tools/analysis/tests/test_matching.py`

Implements spec §6 matching.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pytest
from tools.analysis.matching import zscore_vectors, hungarian_match
from tools.analysis.weather_vector import WeatherVector


def test_zscore_vectors_uses_baseline_means_stds():
    vecs = [WeatherVector(100, 1000, 5, 80, 60), WeatherVector(150, 1500, 6, 85, 65)]
    baseline_means = np.array([100, 1000, 5, 80, 60])
    baseline_stds = np.array([50, 500, 1, 10, 5])
    z = zscore_vectors(vecs, baseline_means, baseline_stds)
    assert z.shape == (2, 5)
    assert z[0].tolist() == pytest.approx([0.0, 0.0, 0.0, 0.0, 0.0])
    assert z[1].tolist() == pytest.approx([1.0, 1.0, 1.0, 0.5, 1.0])


def test_hungarian_match_3x3_returns_optimal_pairs():
    """3 Arm A vs 3 Arm B with known optimal assignment."""
    arm_a_z = np.array([[0, 0, 0, 0, 0], [1, 1, 1, 1, 1], [2, 2, 2, 2, 2]])
    arm_b_z = np.array([[0.1, 0.1, 0.1, 0.1, 0.1],
                        [1.1, 1.1, 1.1, 1.1, 1.1],
                        [2.1, 2.1, 2.1, 2.1, 2.1]])
    pairs = hungarian_match(arm_a_z, arm_b_z)
    # Each A_i should pair with B_i (closest)
    assert pairs == [(0, 0), (1, 1), (2, 2)]


def test_rectangular_hungarian_n_a_less_than_n_b():
    """2 Arm A vs 3 Arm B → 2 pairs, 1 unmatched B."""
    arm_a_z = np.array([[0, 0, 0, 0, 0], [1, 1, 1, 1, 1]])
    arm_b_z = np.array([[0.5, 0.5, 0.5, 0.5, 0.5],
                        [1.5, 1.5, 1.5, 1.5, 1.5],
                        [10, 10, 10, 10, 10]])
    pairs = hungarian_match(arm_a_z, arm_b_z)
    assert len(pairs) == 2
    # B index 2 (far away) should NOT be in the pairs
    matched_b = {b for _, b in pairs}
    assert 2 not in matched_b
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""Weather matching per spec §6: Euclidean z-score distance + rectangular
Hungarian (optimal bipartite). No pre-dropping of arms."""
from __future__ import annotations
import numpy as np
from scipy.optimize import linear_sum_assignment
from tools.analysis.weather_vector import WeatherVector


def zscore_vectors(
    vecs: list[WeatherVector],
    baseline_means: np.ndarray,
    baseline_stds: np.ndarray,
) -> np.ndarray:
    """Returns N×5 z-scored matrix."""
    arr = np.stack([v.as_array() for v in vecs])
    return (arr - baseline_means) / baseline_stds


def hungarian_match(arm_a_z: np.ndarray, arm_b_z: np.ndarray) -> list[tuple[int, int]]:
    """Rectangular Hungarian on Euclidean distance.
    Returns list of (a_idx, b_idx) pairs at min(n_A, n_B) length.
    Unmatched arms in the larger pool are not returned (caller reports descriptively).
    """
    n_a = arm_a_z.shape[0]
    n_b = arm_b_z.shape[0]
    # Cost matrix: n_a × n_b, Euclidean distance per cell
    cost = np.linalg.norm(arm_a_z[:, np.newaxis, :] - arm_b_z[np.newaxis, :, :], axis=2)
    row_ind, col_ind = linear_sum_assignment(cost)
    return list(zip(row_ind.tolist(), col_ind.tolist()))
```

Add `scipy>=1.10` to `requirements.txt`.

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add tools/analysis/matching.py tools/analysis/tests/test_matching.py
git commit -m "feat(analysis): Euclidean z-score Hungarian matching (spec §6)"
```

---

### Task 3.7: Cost-matched symmetric exclusion

**Files:**
- Create: `tools/analysis/cost_matched_exclusion.py`
- Test: `tools/analysis/tests/test_cost_matched_exclusion.py`

Implements spec §5 cost-matched exclusion rule.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from tools.analysis.cost_matched_exclusion import cost_matched_exclude


def test_no_exclusion_when_both_arms_fully_valid():
    rates_a = [10.0] * 288
    rates_b = [10.0] * 288
    valid_a = [True] * 288
    valid_b = [True] * 288
    valid_a_out, valid_b_out = cost_matched_exclude(rates_a, rates_b, valid_a, valid_b)
    assert sum(valid_a_out) == 288
    assert sum(valid_b_out) == 288


def test_b_invalid_2_hours_drops_2_matched_a_hours():
    rates_a = [10.0] * 288
    rates_b = [15.0] * 288
    valid_a = [True] * 288
    valid_b = [True] * 288
    # Drop indices 100 and 200 in B
    valid_b[100] = False
    valid_b[200] = False
    valid_a_out, valid_b_out = cost_matched_exclude(rates_a, rates_b, valid_a, valid_b)
    assert sum(valid_a_out) == 286
    assert sum(valid_b_out) == 286


def test_cost_matching_picks_closest_rate():
    rates_a = [5.0, 10.0, 15.0, 20.0]
    rates_b = [12.0, 12.0, 12.0, 12.0]
    valid_a = [True, True, True, True]
    valid_b = [True, True, True, True]
    # Drop B[0] (rate 12) → closest A rate is 10 (a_idx=1) or 15 (a_idx=2),
    # |12-10|=2 vs |12-15|=3 → pick a_idx=1
    valid_b[0] = False
    va_out, vb_out = cost_matched_exclude(rates_a, rates_b, valid_a, valid_b)
    assert vb_out == [False, True, True, True]
    assert va_out == [True, False, True, True]


def test_tie_break_chronologically_earlier():
    rates_a = [10.0, 10.0, 10.0, 10.0]
    rates_b = [10.0, 10.0, 10.0, 10.0]
    valid_a = [True, True, True, True]
    valid_b = [True, True, True, True]
    valid_b[2] = False
    # All A hours have rate 10, exact tie. Tie-break: earliest = a_idx=0
    va_out, vb_out = cost_matched_exclude(rates_a, rates_b, valid_a, valid_b)
    assert va_out == [False, True, True, True]
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""Cost-matched symmetric exclusion per spec §5.

For each hour where one arm is fully-valid but the other is not, exclude
the closest-by-cost unmatched hour from the fully-valid arm. Greedy 1:1.
"""
from __future__ import annotations


def cost_matched_exclude(
    rates_a: list[float],
    rates_b: list[float],
    valid_a: list[bool],
    valid_b: list[bool],
) -> tuple[list[bool], list[bool]]:
    """Returns (updated valid_a, updated valid_b) with symmetric exclusion applied.
    Inputs are 288-element lists indexed by hour-index 0..287.
    """
    if not (len(rates_a) == len(rates_b) == len(valid_a) == len(valid_b) == 288):
        raise ValueError("all inputs must be length 288")

    va = list(valid_a)
    vb = list(valid_b)

    # Find asymmetric-invalid hours (one arm valid, the other not)
    # Case 1: A valid, B invalid → drop a cost-matched A hour
    # Case 2: A invalid, B valid → drop a cost-matched B hour
    a_to_drop_for_b = [(k, rates_b[k]) for k in range(288) if va[k] and not vb[k]]
    b_to_drop_for_a = [(k, rates_a[k]) for k in range(288) if not va[k] and vb[k]]

    # Process A-side drops driven by B-invalid hours
    for _, target_rate in a_to_drop_for_b:
        candidates = [(k, abs(rates_a[k] - target_rate)) for k in range(288)
                      if va[k] and vb[k]]  # both currently valid
        if not candidates:
            break
        # Sort by (rate-diff, hour-index) — chronological tie-break
        candidates.sort(key=lambda x: (x[1], x[0]))
        chosen = candidates[0][0]
        va[chosen] = False

    # Process B-side drops driven by A-invalid hours
    for _, target_rate in b_to_drop_for_a:
        candidates = [(k, abs(rates_b[k] - target_rate)) for k in range(288)
                      if va[k] and vb[k]]
        if not candidates:
            break
        candidates.sort(key=lambda x: (x[1], x[0]))
        chosen = candidates[0][0]
        vb[chosen] = False

    return va, vb
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add tools/analysis/cost_matched_exclusion.py tools/analysis/tests/test_cost_matched_exclusion.py
git commit -m "feat(analysis): cost-matched symmetric exclusion (spec §5)"
```

---

### Task 3.8: Arm-period pipeline orchestrator

**Files:**
- Create: `tools/analysis/arm_period_pipeline.py`
- Test: `tools/analysis/tests/test_arm_period_pipeline.py`

Implements spec §4 outcome + §5 exclusion + §7 validity end-to-end per arm-pair.

- [ ] **Step 1: Write the failing test (integration smoke)**

```python
import datetime
import numpy as np
import pandas as pd
from tools.analysis.arm_period_pipeline import compute_pair_outcome
from tools.analysis.arm_calendar import ARM_CALENDAR


def test_compute_pair_outcome_returns_per_pair_table_row(synth_pair_inputs):
    arm_a, arm_b, refoss_df, prices_df, ecowitt_df, mode_classifications_a, mode_classifications_b = synth_pair_inputs
    row = compute_pair_outcome(
        arm_a=arm_a, arm_b=arm_b,
        refoss_df=refoss_df, prices_df=prices_df, ecowitt_df=ecowitt_df,
        modes_a=mode_classifications_a, modes_b=mode_classifications_b,
        baseline_means=np.array([100, 1000, 5, 80, 60]),
        baseline_stds=np.array([50, 500, 1, 10, 5]),
    )
    assert "hvac_dollars_a" in row
    assert "hvac_dollars_b" in row
    assert "diff_dollars_b_minus_a" in row
    assert "valid_pair_hours" in row
    assert row["valid_pair_hours"] >= 230  # passes implicit guarantee
```

- [ ] **Step 2: Run, fails**

- [ ] **Step 3: Implement**

```python
"""Arm-period pipeline orchestrator per spec §4-§7.

Per matched pair:
1. Per-hour mode classification (already done upstream)
2. Single validity gate per arm (already filtered upstream)
3. Per-hour HVAC kWh + hourly rate
4. Cost-matched symmetric exclusion
5. Summed HVAC$ + HVAC kWh per side
6. Pair row with provenance
"""
from __future__ import annotations
import datetime
import numpy as np
import pandas as pd
from tools.analysis.arm_calendar import ArmPeriod, post_washout_start, HOURS_PER_ARM
from tools.analysis.mode_classification import HourMode, is_fully_valid
from tools.analysis.weather_vector import build_weather_vector
from tools.analysis.matching import zscore_vectors
from tools.analysis.cost_matched_exclusion import cost_matched_exclude
from tools.analysis.hvac_dollars import HourlyRateInputs, hourly_rate_c_per_kwh
from tools.analysis.dtod_rates import dtod_total_delivery_c_per_kwh


def compute_pair_outcome(*, arm_a, arm_b, refoss_df, prices_df, ecowitt_df,
                         modes_a, modes_b, baseline_means, baseline_stds,
                         monthly_rate_snapshot):
    """Returns dict per-pair row per spec §9."""
    # Build per-hour series for both arms
    rates_a = _hourly_rates(arm_a, prices_df, monthly_rate_snapshot)
    rates_b = _hourly_rates(arm_b, prices_df, monthly_rate_snapshot)
    kwh_a = _hourly_hvac_kwh(arm_a, refoss_df)
    kwh_b = _hourly_hvac_kwh(arm_b, refoss_df)

    # Initial validity from modes
    valid_a = [is_fully_valid(m) for m in modes_a]
    valid_b = [is_fully_valid(m) for m in modes_b]

    # Cost-matched symmetric exclusion
    valid_a, valid_b = cost_matched_exclude(rates_a, rates_b, valid_a, valid_b)

    # Sum HVAC$ and kWh over valid hours
    hvac_dollars_a = sum(kwh_a[k] * rates_a[k] / 100.0
                          for k in range(HOURS_PER_ARM) if valid_a[k])
    hvac_dollars_b = sum(kwh_b[k] * rates_b[k] / 100.0
                          for k in range(HOURS_PER_ARM) if valid_b[k])
    hvac_kwh_a = sum(kwh_a[k] for k in range(HOURS_PER_ARM) if valid_a[k])
    hvac_kwh_b = sum(kwh_b[k] for k in range(HOURS_PER_ARM) if valid_b[k])

    # Weather vectors + z-scored distance
    vec_a = build_weather_vector(arm_a, ecowitt_df)
    vec_b = build_weather_vector(arm_b, ecowitt_df)
    z = zscore_vectors([vec_a, vec_b], baseline_means, baseline_stds)
    weather_distance = float(np.linalg.norm(z[0] - z[1]))

    return {
        "arm_a_id": f"A{arm_a.index}",
        "arm_b_id": f"B{arm_b.index}",
        "arm_a_dates": f"{arm_a.start_ct.date()}/{arm_a.end_ct.date()}",
        "arm_b_dates": f"{arm_b.start_ct.date()}/{arm_b.end_ct.date()}",
        "temporal_gap_days": abs((arm_b.start_ct - arm_a.start_ct).days),
        "weather_distance_zscore": weather_distance,
        "valid_pair_hours": sum(1 for k in range(HOURS_PER_ARM)
                                if valid_a[k] and valid_b[k]),
        "hvac_dollars_a": hvac_dollars_a,
        "hvac_dollars_b": hvac_dollars_b,
        "diff_dollars_b_minus_a": hvac_dollars_b - hvac_dollars_a,
        "percent_diff_dollars": ((hvac_dollars_b - hvac_dollars_a) / hvac_dollars_a * 100
                                  if hvac_dollars_a else 0.0),
        "hvac_kwh_a": hvac_kwh_a,
        "hvac_kwh_b": hvac_kwh_b,
        "diff_kwh_b_minus_a": hvac_kwh_b - hvac_kwh_a,
    }


def _hourly_hvac_kwh(arm: ArmPeriod, refoss_df: pd.DataFrame) -> list[float]:
    """Per spec §7: hour_kWh = mean(power_w) × 1h, summed across em:2/em:8/em:9."""
    # CT-local → UTC via zoneinfo to handle DST-fold correctly (L3 resolution)
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    utc = ZoneInfo("UTC")
    start_local = post_washout_start(arm).replace(tzinfo=ct)
    start = start_local.astimezone(utc).replace(tzinfo=None)
    out = []
    for k in range(HOURS_PER_ARM):
        h_start = start + datetime.timedelta(hours=k)
        h_end = h_start + datetime.timedelta(hours=1)
        h_df = refoss_df[(refoss_df["_time"] >= h_start) & (refoss_df["_time"] < h_end)]
        per_channel_mean_w = h_df.groupby("channel")["_value"].mean()
        hvac_w = per_channel_mean_w.reindex(["em:2", "em:8", "em:9"], fill_value=0).sum()
        out.append(hvac_w / 1000.0)  # W → kWh over 1 hour
    return out


def _hourly_rates(arm: ArmPeriod, prices_df: pd.DataFrame,
                  monthly_rate_snapshot: dict) -> list[float]:
    """Per spec §4 hourly rate formula. DST-aware via zoneinfo."""
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    utc = ZoneInfo("UTC")
    start_local = post_washout_start(arm).replace(tzinfo=ct)
    start = start_local.astimezone(utc).replace(tzinfo=None)
    out = []
    for k in range(HOURS_PER_ARM):
        h_start_utc = start + datetime.timedelta(hours=k)
        # CT hour-of-day for DTOD band lookup
        h_start_local = h_start_utc.replace(tzinfo=utc).astimezone(ct)
        hour_ct = h_start_local.hour
        # Look up rt_hrl_lmps for this hour
        match = prices_df[prices_df["_time"] == h_start_utc]
        lmp_per_mwh = float(match["total_lmp_rt"].iloc[0]) if len(match) else 0.0
        inputs = HourlyRateInputs(
            rt_hrl_lmps_per_mwh=lmp_per_mwh,
            pea_c_per_kwh=monthly_rate_snapshot["pea_c_per_kwh"],
            transmission_c_per_kwh=monthly_rate_snapshot["transmission_c_per_kwh"],
            misc_procurement_c_per_kwh=monthly_rate_snapshot["misc_procurement_c_per_kwh"],
            dtod_total_delivery_c_per_kwh=dtod_total_delivery_c_per_kwh(hour_ct),
            variable_riders_c_per_kwh=monthly_rate_snapshot["variable_riders_c_per_kwh"],
            carbon_free_credit_c_per_kwh=monthly_rate_snapshot["carbon_free_credit_c_per_kwh"],
        )
        out.append(hourly_rate_c_per_kwh(inputs))
    return out
```

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add tools/analysis/arm_period_pipeline.py tools/analysis/tests/test_arm_period_pipeline.py
git commit -m "feat(analysis): arm-period pipeline orchestrator (spec §4-§7)"
```

---

### Task 3.9: End-to-end test on synthetic data

**Files:**
- Test: `tools/analysis/tests/test_pipeline_end_to_end_arm_period.py`

- [ ] **Step 1: Build a synthetic 12-arm experiment with known answer**

Generate fake Refoss/Ecowitt/prices data spanning 2026-06-01 → 2026-11-16. Inject a known 10% HVAC$ savings between arms (Arm B uses 10% less HVAC kWh).

- [ ] **Step 2: Run pipeline end-to-end**

Verify the aggregate diff (mean B − A across pairs) is within tolerance of the injected 10%.

- [ ] **Step 3: Commit**

```bash
git add tools/analysis/tests/test_pipeline_end_to_end_arm_period.py
git commit -m "test(analysis): end-to-end synthetic experiment validates pipeline"
```

---

### Task 3.10: Remove obsolete code from pipeline.py

**Files:**
- Modify: `tools/analysis/pipeline.py` (substantial deletions)

Per spec §13 PR #109 disposition + §12 dropped sensitivities.

- [ ] **Step 1: Inventory removable code**

```bash
grep -nE "weekly_dollars_per_cdd|kwh_per_cdd|bootstrap|sced_random|MAHALANOBIS_PAIR_FLAG|MAHALANOBIS_OUTLIER_FLAG|BOOTSTRAP_N|SCED_EXACT_MAX" tools/analysis/pipeline.py
```

Catalogue functions/constants to remove. Examples:
- `weekly_dollars_per_cdd`, `weekly_kwh_per_cdd`, `_compute_weekly_row` $/CDD branches
- `BOOTSTRAP_N_RESAMPLES`, `BOOTSTRAP_BLOCK_LENGTH`, `_stationary_bootstrap`
- `SCED_EXACT_MAX_N`, `SCED_RANDOM_RESAMPLES`, `_sced_randomization_test`

- [ ] **Step 2: Delete obsolete functions one at a time, run tests after each**

Each deletion is a commit. Run `bash deploy/energy-stack/run_tests.sh` after each.

- [ ] **Step 3: Final commit**

```bash
git commit -m "refactor(analysis): remove obsolete weekly/CDD/SCED machinery (spec §12, §13)"
```

---

### Task 3.11: Eagle whole-home Flux query + manifest entry

**Files:**
- Create: `tools/analysis/queries/eagle.meter.flux`
- Modify: `tools/analysis/replay/manifest.py` (add `eagle.meter` to KNOWN_MEASUREMENTS)

Per spec §10: Eagle smart-meter delivered-kWh is canonical for bill reconciliation. This task wires the Flux query + manifest support so the analysis layer can read Eagle data.

- [ ] **Step 1: Write the Flux query**

Inspect Eagle's existing InfluxDB schema first (`docker exec -i influxdb influx query --raw` with a probe query) to confirm measurement/field names. Then create the query targeting whatever Eagle measurement is actually written by `eagle-poller`. Likely shape:

```flux
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "eagle.meter")
  |> filter(fn: (r) => r._field == "delivered_kwh")
  |> yield(name: "eagle_meter")
```

(Adjust based on actual schema.)

- [ ] **Step 2: Add to manifest**

In `tools/analysis/replay/manifest.py`, add `"eagle.meter"` to `KNOWN_MEASUREMENTS` and `POST_2025_MEASUREMENTS`. Cherry-pick the equivalent change from PR #109 if it has a usable version.

- [ ] **Step 3: Commit**

```bash
git add tools/analysis/queries/eagle.meter.flux tools/analysis/replay/manifest.py
git commit -m "feat(analysis): Eagle meter Flux query + manifest entry (spec §10)"
```

---

### Task 3.12: Eagle coverage helper

**Files:**
- Create: `tools/analysis/eagle_coverage.py`
- Test: `tools/analysis/tests/test_eagle_coverage.py`

Computes per-bill-period Eagle coverage % so reconciliation provenance can report it.

- [ ] **Step 1: Failing test**

```python
def test_eagle_coverage_full_bill_period_returns_100():
    # 30 days × 24 hours = 720 hours; Eagle present every hour
    ...

def test_eagle_coverage_partial_returns_correct_pct():
    # 720 hours, Eagle present 540 hours → 75%
    ...

def test_eagle_coverage_with_short_gaps_still_counts():
    # Eagle reports every ~hour; tolerate up to 5min gap before counting hour as missing
    ...
```

- [ ] **Step 2-3: Implement + pass**

```python
"""Eagle coverage helper per spec §10."""
def eagle_coverage_pct(eagle_df, start_utc, end_utc) -> float:
    """Returns % of hours in [start_utc, end_utc) where Eagle has ≥1 reading."""
    ...
```

- [ ] **Step 4: Commit**

```bash
git add tools/analysis/eagle_coverage.py tools/analysis/tests/test_eagle_coverage.py
git commit -m "feat(analysis): Eagle coverage helper (spec §10)"
```

---

### Task 3.13: Bill reconciliation reconstruction

**Files:**
- Create: `tools/analysis/bill_reconciliation.py`
- Test: `tools/analysis/tests/test_bill_reconciliation.py`

Implements spec §10 bill reconciliation:
- Source priority: Eagle primary, Refoss mains (em:1 + em:7) fallback
- Method: reconstruct household variable bill from hourly kWh × rates + monthly riders
- Output: reconstructed totals + divergence vs actual bill + provenance fields

- [ ] **Step 1: Failing tests**

```python
def test_reconstruct_uses_eagle_when_available():
    """All hours have Eagle data; Refoss is not consulted."""
    ...

def test_reconstruct_falls_back_to_refoss_on_eagle_gap():
    """Half the hours have Eagle gaps; Refoss fills."""
    ...

def test_provenance_reports_pct_eagle_vs_refoss():
    ...

def test_divergence_flag_triggers_at_5_pct_or_dollar_10():
    """Whichever is larger per spec §10."""
    # 5% of $200 = $10 → flag
    # 5% of $300 = $15, but diff is $9 → no flag
    # 5% of $50 = $2.50, but diff is $11 → flag ($10 floor)
    ...

def test_eagle_refoss_drift_reported_during_overlap():
    """Where both have data, report mean abs diff in provenance."""
    ...

def test_never_silently_rescales_hvac_outcomes():
    """Spec §10 hard rule: divergence is reported, not corrected."""
    ...
```

- [ ] **Step 2-3: Implement + pass**

```python
"""Bill reconciliation per spec §10.

Eagle smart-meter delivered-kWh is canonical. Refoss mains fills gaps.
Reconstruct variable bill from kWh × rates; compare to actual bill.
Flag divergence > max(5%, $10). Never rescale HVAC outcomes.
"""
from dataclasses import dataclass


@dataclass
class BillReconciliation:
    reconstructed_supply_dollars: float
    reconstructed_delivery_dollars: float
    reconstructed_riders_dollars: float
    reconstructed_total_variable_dollars: float
    actual_bill_variable_dollars: float
    divergence_dollars: float
    divergence_pct: float
    divergence_flagged: bool
    pct_hours_eagle: float
    pct_hours_refoss_fallback: float
    eagle_refoss_drift_during_overlap_kwh: float  # mean abs diff


def reconcile_bill_period(*, eagle_df, refoss_df, prices_df, rate_snapshot,
                          actual_bill_lineitems) -> BillReconciliation:
    ...
```

- [ ] **Step 4: Commit**

```bash
git add tools/analysis/bill_reconciliation.py tools/analysis/tests/test_bill_reconciliation.py
git commit -m "feat(analysis): bill reconciliation reconstruction (spec §10)"
```

---

### Task 3.14: Wire Eagle/bill reconciliation into pipeline orchestrator

**Files:**
- Modify: `tools/analysis/arm_period_pipeline.py`

Add a `reconcile_against_bills()` step to the pipeline that:
- Pulls the bills covering the experiment window
- For each bill period, calls `reconcile_bill_period()`
- Attaches reconciliation results to the pipeline output for provenance reporting
- Does NOT affect HVAC$ outcomes (sanity-only)

- [ ] **Step 1: Failing test**

```python
def test_pipeline_includes_bill_reconciliation_in_output():
    """Pipeline output has a `bill_reconciliations` field with one entry per bill period."""
    ...
```

- [ ] **Step 2-3: Wire it in.**

- [ ] **Step 4: Commit**

```bash
git add tools/analysis/arm_period_pipeline.py tools/analysis/tests/test_arm_period_pipeline.py
git commit -m "feat(analysis): wire bill reconciliation into pipeline output (spec §10)"
```

---

### Task 3.15: Phase 3 PR + merge

Open PR `Phase 3: SCED rebaseline analysis pipeline`. Substantial diff; expect extensive review. Include PR body template per "Standing rules for every phase" above.

---

## Phase 4: NOAA fallback station selection

**Goal:** Lock the NOAA ASOS fallback station for Ecowitt-gap hours. NO historical baseline pull (within-sample standardization per spec §6 makes ERA5/2020-2025 historical data unnecessary).

**Time:** ~0.5 days. Independent of Phase 3.

**Note:** This phase was originally 11-12 days (ERA5 reanalysis + 6-year pull + z-score parameter JSON freeze). The H2 adversarial finding led to a revised spec §6 that uses within-sample standardization, eliminating the need for historical baseline parameters. Phase 4 reduces to a single audit task: pick which NOAA station serves as Ecowitt-gap fallback.

---

### Task 4.1: Evaluate NOAA ASOS station candidates

**Files:**
- Create: `docs/replay-validation/2026-05-XX-noaa-fallback-station-selection/findings.md`

- [ ] **Step 1: Pull 7 days of sample data from each candidate station**

Use NOAA NCEI API for KJOT (Joliet, ~10 mi from Plainfield), KARR (Aurora, ~15 mi), KMDW (Chicago Midway, ~30 mi), KORD (Chicago O'Hare, ~35 mi). Sample a summer 2024 week.

- [ ] **Step 2: Tabulate completeness**

For each station, check hourly availability of:
- Dry-bulb temperature
- Dew point temperature

These are the only two components needed for spec §6's 4-component vector (CDD derived from temp; mean_daily_max from temp; mean_nocturnal_min from temp; mean_dewpoint from dewpoint).

| Station | Distance to Plainfield | Hourly temp % present | Hourly dewpoint % present | Recommended? |
|---|---|---|---|---|
| KJOT | ~10 mi | ? | ? | likely yes (closest) |
| KARR | ~15 mi | ? | ? | |
| KMDW | ~30 mi | ? | ? | |
| KORD | ~35 mi | ? | ? | |

- [ ] **Step 3: Pick winner based on (a) proximity, (b) completeness. Document decision.**

- [ ] **Step 4: Commit findings doc**

```bash
git checkout main && git pull && git checkout -b sced-rebaseline-phase4
git add docs/replay-validation/2026-05-XX-noaa-fallback-station-selection/findings.md
git commit -m "docs(weather): lock NOAA ASOS fallback station (spec §11 #11)"
```

---

### Task 4.2: Phase 4 PR + merge

Single-task phase. Open PR `Phase 4: SCED rebaseline NOAA fallback station selection`.

---

## Phase 5: Documentation

**Goal:** Freeze CTK04AE Arm A schedule and HVAC_LOGIC.md day-types. Lock OSF references.

**Day 13.**

---

### Task 5.1: Document CTK04AE Arm A schedule

**Files:**
- Create: `docs/THERMOSTAT_ARM_A_SCHEDULE.md`

- [ ] **Step 1: Pull schedule from CTK04AE via Control4 API or manual transcription**

```bash
# If Control4 exposes the schedule read-only:
ssh pi-lab 'python3 /home/chris/scripts/dump_ctk04ae_schedule.py'

# Otherwise: transcribe from thermostat UI
```

- [ ] **Step 2: Write doc**

```markdown
---
name: thermostat-arm-a-schedule
date: 2026-05-XX
owner: chris
status: locked
role-label: spec
effective_at_osf_commit: <hash>
---

# CTK04AE Arm A Baseline Schedule

This document captures the CTK04AE thermostat's programmed schedule
that runs autonomously during Arm A periods of the SCED experiment
(per docs/plans/sced-rebaseline-spec-2026-05-13.md §3).

## Weekday schedule
| Time (CT) | Cool setpoint | Heat setpoint | Fan |
|---|---|---|---|
| 06:00 | 73°F | 65°F | Auto |
| 09:00 | 78°F | 60°F | Auto |
| ... | | | |

## Weekend schedule
| Time (CT) | Cool setpoint | Heat setpoint | Fan |
|---|---|---|---|
| ... | | | |

## Settings
- Adaptive Intelligent Recovery (AIR): OFF
- Auto Changeover Deadband: 3°F (minimum)
- Dehumidification: ON
- ...

## Provenance
Pulled from CTK04AE on 2026-05-XX. Frozen at OSF filing commit.
```

- [ ] **Step 3: Commit**

```bash
git checkout main && git pull && git checkout -b sced-rebaseline-phase5
git add docs/THERMOSTAT_ARM_A_SCHEDULE.md
git commit -m "docs(arm-a): freeze CTK04AE thermostat schedule (spec §3, §11 #8)"
```

---

### Task 5.2: HVAC_LOGIC.md day-type completeness audit

**Files:**
- Modify: `docs/HVAC_LOGIC.md` (patch any gaps)

- [ ] **Step 1: Enumerate day-types from code**

```bash
grep -nE "day_type|DayType" deploy/energy-stack/hvac-scheduler/app.py | head -30
```

List all day-types: MILD, NORMAL, HOT, HOT_STREAK_DAY1, HOT_STREAK_DAY2, ...

- [ ] **Step 2: Check each is documented in HVAC_LOGIC.md with full schedule**

- [ ] **Step 3: Patch any missing day-type schedule tables**

- [ ] **Step 4: Commit**

```bash
git add docs/HVAC_LOGIC.md
git commit -m "docs(hvac-logic): complete day-type schedule coverage (spec §11 #12)"
```

---

### Task 5.3: Phase 5 PR + merge

Open PR `Phase 5: SCED rebaseline documentation`.

---

## Phase 6: Shadow validation

**Goal:** Run analysis pipeline on shadow-mode data (2026-04-29 → present), produce pass/fail validation report.

**Days 14-15.**

---

### Task 6.1: Shadow data pull + pipeline run

**Files:**
- Create: `tools/analysis/run_shadow_validation.py`

- [ ] **Step 1: Build the runner**

```python
"""Pre-experiment shadow validation per spec §11 #13.

Runs full analysis pipeline against pre-experiment shadow-mode data
(2026-04-29 → today). Produces validation artifacts demonstrating:
- Ingestion works (Refoss, Ecowitt, prices, comed bill, PJM capacity-risk inputs)
- Pricing reconstructs sensibly
- Refoss HVAC channels compute valid hourly kWh
- Refoss-mains vs Eagle reconciliation produces expected ~0 violations
- Weather vector construction succeeds with provenance
- Arm calendar logic (note: experiment window starts 2026-06-01, so
  pre-experiment shadow data is NOT inside any arm; this validates
  pipeline shape only, not arm-period outcome)
- No-write Arm A shadow behavior

Outputs: pass/fail JSON + per-stage findings markdown.
"""
```

- [ ] **Step 2: Run**

```bash
ssh pi-lab 'cd /home/chris/energy-stack && python tools/analysis/run_shadow_validation.py'
```

- [ ] **Step 3: Generate findings.md from results**

```markdown
# Shadow Validation Findings — 2026-05-XX

Per docs/plans/sced-rebaseline-spec-2026-05-13.md §11 #13.

## Inputs window
2026-04-29 → 2026-05-XX (~XX days).

## Pipeline-stage results
| Stage | Status | Notes |
|---|---|---|
| Refoss ingestion | PASS | 5 channels, atomic writes |
| Ecowitt ingestion | PASS | ch1 + ws90 sensors active |
| rt_hrl_lmps poller + backfill | PASS | XXX hours from 2026-01-01 |
| Bill ingestion | PASS | Last bill 2026-04-24, DTOD election active |
| Mode classification | PASS | All shadow hours classified as A-shadow (controller not pushing) |
| Validity gate | N/A | Pre-experiment, no arm-period |
| HVAC$ formula | PASS | Test inputs produce expected values |
| Reconciliation | PASS | Refoss mains vs Eagle within tolerance |

## Open issues
- (any flagged)

## Sign-off
✅ Pipeline-shape validation passes. Ready for 2026-06-01 experiment start.
```

- [ ] **Step 4: Commit**

```bash
git checkout main && git pull && git checkout -b sced-rebaseline-phase6
git add tools/analysis/run_shadow_validation.py docs/replay-validation/2026-05-XX-shadow/
git commit -m "feat(validation): shadow validation runner + findings (spec §11 #13)"
```

---

### Task 6.2: PR #109 closure VERIFICATION (closure itself happens in Phase 3)

L2 resolution: PR #109 is closed mid-Phase 3 immediately after the cherry-pick PRs land (Eagle manifest, actual-dollar helper concept), NOT in Phase 6. This task verifies the closure happened and the cherry-picks are present in main.

- [ ] **Step 1: Verify #109 is closed**

```bash
gh pr view 109 --json state,closedAt
```

Expected: `state: CLOSED`, `closedAt` set to a date in Phase 3 window.

- [ ] **Step 2: Verify cherry-picked content is in main**

```bash
git log --oneline main -- tools/analysis/queries/eagle.meter.flux
```

Expected: at least one commit landed cherry-picking the Eagle work.

(Closure command, performed during Phase 3 after cherry-picks merge, for reference:)

```bash
gh pr close 109 --comment "Superseded by sced-rebaseline spec (docs/plans/sced-rebaseline-spec-2026-05-13.md). Salvageable bits cherry-picked per spec §13."
```

---

### Task 6.3: Phase 6 PR + merge

Final pre-OSF PR `Phase 6: SCED rebaseline shadow validation + PR #109 closure`.

---

### Task 6.4: M3 scarcity-divergence audit step

**Files:**
- Modify: `tools/analysis/run_shadow_validation.py`

Per spec §11 #13 (M3 resolution). Add to the shadow-validation runner an audit of live-vs-settled price divergence at scarcity hours.

- [ ] **Step 1: Add scarcity-divergence function**

```python
def audit_scarcity_divergence(start_ts, end_ts):
    """For hours where comed.prices 5-min avg exceeded p95, compute
    abs diff vs rt_hrl_lmps settled. Report max, p95, n_diverging_>2c."""
    # Query both feeds for the window
    # Compute hourly mean of 5-min prices per hour
    # Compare to rt_hrl_lmps settled for same hour
    # Filter to hours where 5-min hourly mean > p95 of all hours in window
    # Report distribution of |5-min_hourly_mean - rt_hrl_lmps|
    ...
    return {
        "n_scarcity_hours": ...,
        "max_diff_c_per_kwh": ...,
        "p95_diff_c_per_kwh": ...,
        "n_diverging_over_2c": ...,
    }
```

- [ ] **Step 2: Include results in findings.md**

Section: "Live-vs-settled scarcity divergence (M3)". If `n_diverging_over_2c > 0`, flag in OSF appendix as a real risk that controller-observed signals deviate from bill-canonical prices at the moments controller decisions matter most.

- [ ] **Step 3: Commit**

```bash
git add tools/analysis/run_shadow_validation.py docs/replay-validation/2026-05-XX-shadow/findings.md
git commit -m "feat(validation): add scarcity-divergence audit step (spec §11 #13 M3)"
```

---

## Phase 7: Post-experiment-start operational checkpoint

**Goal:** Catch failures that Phase 6 cannot surface because pre-experiment shadow data lacks cooling-active hours, B-active mode, and summer weather distributions. Per adversarial review M6.

**Time:** ~0.5 days work; calendar timing is ~2026-06-15 (15 days after experiment start).

**Note:** This is an OPERATIONAL checkpoint, not an analysis checkpoint. It is pre-registered in OSF as a routine sanity check. Failures discovered here are reported as protocol deviations in the final analysis; they do NOT change the locked spec.

---

### Task 7.1: First-arm-transition operational checkpoint

**Files:**
- Create: `docs/replay-validation/2026-06-15-first-arm-transition/findings.md`

After the first Arm A → Arm B switch on 2026-06-15 00:00 CT, the system has generated its first real B-active classification data + first real cooling-active hours under summer conditions.

- [ ] **Step 1: Verify arm-mode classification is producing real B-active rows**

```bash
ssh pi-lab 'docker exec -i influxdb influx query --raw << "FLUX"
from(bucket: "energy")
  |> range(start: 2026-06-15T05:00:00Z, stop: 2026-06-16T05:00:00Z)
  |> filter(fn: (r) => r._measurement == "hvac.arm_mode")
  |> filter(fn: (r) => r._field == "mode_actual")
  |> group(columns: ["_value"])
  |> count()
FLUX
'
```

Expected: `B-active` rows present in non-trivial count. If only `B-fallback` or `B-down` appearing, controller has a deploy issue.

- [ ] **Step 2: Verify cooling-active hours are being captured**

Query HVAC kWh for first 24h of Arm 2 (Arm B):

```bash
ssh pi-lab 'docker exec -i influxdb influx query --raw << "FLUX"
from(bucket: "energy")
  |> range(start: 2026-06-15T05:00:00Z, stop: 2026-06-16T05:00:00Z)
  |> filter(fn: (r) => r._measurement == "refoss.channel")
  |> filter(fn: (r) => r.channel == "em:2" or r.channel == "em:8")
  |> filter(fn: (r) => r._field == "power_w")
  |> aggregateWindow(every: 1h, fn: mean)
  |> filter(fn: (r) => r._value > 100)
  |> count()
FLUX
'
```

Expected: at least 1 cooling-active hour. If zero, EITHER AC didn't run during this 24h period (acceptable if cool weather) OR Refoss is missing/miscalibrated (NOT acceptable).

- [ ] **Step 3: Run the rewritten analysis pipeline against the first ~12 days of data**

This is NOT outcome evidence — it's pipeline-shape validation under real summer-cooling conditions. Verify:
- Per-hour mode classification populates each emitted mode value cleanly (no crashes, no nulls); coverage of specific modes depends on what actually happened in the window and is reported descriptively, not as a pass/fail expectation
- Single validity gate behaves sanely on real telemetry
- Weather vector construction succeeds
- Cost-matched exclusion produces equal-count valid hours per side
- No crashes, no schema mismatches, no DST-fold bugs

- [ ] **Step 4: Write findings.md**

```markdown
# First Arm Transition Operational Checkpoint — 2026-06-15

Per docs/plans/sced-rebaseline-spec-2026-05-13.md §11 (M6 audit-phase item).
Pre-registered in OSF as operational, NOT analysis.

## Arm-mode classification health
- B-active hours: ?
- B-fallback hours: ?
- B-down hours: ?

## Cooling-active hour confirmation
- First 24h of Arm 2 (Arm B): cooling-active hours = ?
- HVAC$ ($) over first 24h: ?

## Pipeline shape validation under real conditions
- Per-hour modes: all 4 represented?
- Weather vector built successfully?
- Cost-matched exclusion: ?
- DST-fold (arms 11-12) — not yet relevant at this checkpoint, will revisit if needed

## Open issues (none expected)
- [if any]

## Sign-off
✅ Operational checkpoint passes. Experiment continues.
OR
⚠️ Issues found. Listed above. Protocol deviation logged.
```

- [ ] **Step 5: Commit**

```bash
git checkout main && git pull && git checkout -b sced-rebaseline-phase7
git add docs/replay-validation/2026-06-15-first-arm-transition/
git commit -m "ops(experiment): first-arm-transition operational checkpoint (spec §11 M6)"
```

---

## OSF prereg filing (2026-05-30)

After Phases 1-6 merged (Phase 7 happens post-experiment-start; not pre-OSF), file OSF prereg referencing:
- `docs/plans/sced-rebaseline-spec-2026-05-13.md` — frozen at the OSF-filing commit hash
- `docs/THERMOSTAT_ARM_A_SCHEDULE.md` — frozen
- `docs/HVAC_LOGIC.md` — frozen (Arm B controller spec)
- `docs/replay-validation/2026-05-XX-shadow/findings.md` — shadow validation evidence (incl. M3 scarcity-divergence audit)
- `docs/replay-validation/2026-05-XX-noaa-fallback-station-selection/findings.md` — NOAA fallback station lock

Phase 7 (post-experiment-start checkpoint) is referenced in OSF as a future operational checkpoint to be run on 2026-06-15.

---

## Self-review (updated post-adversarial-review)

**Spec coverage check:** every section of `docs/plans/sced-rebaseline-spec-2026-05-13.md` traced to at least one task in this plan. §0 source-of-truth declaration is procedural. §14 limitations are documentation-only.

**Placeholder scan:** No "TBD" inside steps. Task 4.1 has audit task with table headers awaiting data — that's the audit's purpose. Task 5.1 has `<hash>` placeholder for OSF commit hash — intentionally unknowable until OSF filing day. Task 7.1 dates reference 2026-06-15 calendar event.

**Type consistency:** `HourMode` enum used consistently across modules. `ArmPeriod` dataclass used consistently. `HourlyRateInputs` dataclass referenced same way across pipeline tasks.

**Revised critical-path estimates (post-H3 + post-H2 resolution):**

| Phase | Estimate |
|---|---|
| 1 Telemetry foundation | 1-2 days |
| 2 Pricing infrastructure | 1-2 days |
| 3 Analysis pipeline rewrite | 2-4 days (per operator velocity input) |
| 4 NOAA fallback station selection | 0.5 days |
| 5 Documentation | 0.5 days |
| 6 Shadow validation + M3 audit | 1-2 days |
| OSF doc writing | 1-2 days |
| **Total before OSF (May 30)** | **7-12 days** |
| 7 First-arm-transition checkpoint | 0.5 days (post-OSF, 2026-06-15) |

vs. 17 days available May 13 → May 30. Comfortable buffer.

**Critical-path residual risks:**
1. Phase 3 pipeline rewrite may surface unanticipated Influx schema or import path issues — bounded by Day 3 of work
2. CTK04AE schedule pull (Task 5.1) may require manual transcription if Control4 API doesn't expose schedule read-side — half-day fallback
3. NOAA NCEI API access for Task 4.1 — uses public-no-auth bulk download, low risk

**Feature-completion rule (per AGENTS.md outside-in TDD + multi-phase feature workflow):**

The SCED rebaseline is NOT feature-complete until `tools/analysis/tests/test_rebaseline_end_to_end_acceptance.py::test_rebaseline_end_to_end_acceptance` passes against the real implementation with **zero scaffolding**.

Specifically: the test MUST NOT be made to pass by mocking, replacing, or substituting any component of `run_full_pipeline` or its dependencies. Individual task-level tests in Phases 1-6 are necessary but not sufficient. The Phase 0 acceptance test is the binding feature-level criterion.

This rule prevents "task done = feature done" confusion. Every phase PR may merge independently, but the rebaseline as a whole is not shipped until the outside-in test passes end-to-end. Per AGENTS.md: *"The project is the whole, not any given task. Intermediate stages stay xfail or skip until the slice ships."*
