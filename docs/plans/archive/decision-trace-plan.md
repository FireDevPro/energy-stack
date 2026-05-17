---
date: 2026-05-14
owner: chris
status: complete-pending-merge
role-label: chris
---

# Decision-trace logging — commissioning visibility plan

Complete the live decision-trace inside the existing `SCHEDULER_MODE=shadow` runtime mode, so `docker compose logs -f hvac-scheduler | jq` shows the full causal trace of every controller decision before June 1 commissioning. Controller authority is enabled only after commissioning review shows the observed decision paths are explainable, and unobserved paths are covered by tests or replay.

This plan does NOT introduce shadow mode — that already exists via `SCHEDULER_MODE` (spec §3, landed via SCED rebaseline Phase 1 / PR #112). This plan closes the silent gaps in the decision tracer that runs inside shadow.

## Spec anchors

- [sced-rebaseline-spec-2026-05-13.md §3](sced-rebaseline-spec-2026-05-13.md) — `SCHEDULER_MODE` (shadow / experiment / production); arm-calendar gating; locked freeze at OSF commit hash.
- [arm_calendar.py](../../deploy/energy-stack/hvac-scheduler/arm_calendar.py) — 12-period A/B calendar already wired. Trace rows source their `arm` field from `current_arm_at(now_ct)` — same call `write_arm_mode` makes to produce the canonical `hvac.arm_mode` rows. `arm` reflects calendar membership, not `SCHEDULER_MODE`.
- [HVAC_LOGIC.md](../HVAC_LOGIC.md) — schedule + supervisor + 5CP rule definitions (ground truth for the decisions being traced).
- This conversation (grilled, then descoped from "audit engine" → "complete the existing tracer log"). Locked decisions captured below.
- Existing log emission pattern: `log(level, event, **fields)` in [app.py](../../deploy/energy-stack/hvac-scheduler/app.py), JSON to stdout, scraped by `promtail` to `loki`. Grafana Explore `{container="energy-stack-hvac-scheduler-1"}` is the existing viewing surface.

## Locked decisions

| Question | Decision |
|---|---|
| Runtime mode the trace runs inside | `SCHEDULER_MODE=shadow` (already gates `execute_action`'s setpoint-write path). This plan does not change the mode definition or the gating logic. |
| Storage | **Loki only.** No new Influx measurement. No new service. Mirror to Influx only if a later phase needs aggregation. |
| Cadence | **Every evaluation**, not every transition or every action fire. Per-tick price overlay + per-tick 5CP + per-tick layer resolution + per-invocation supervisor (not per-tick). ~500-1500 log lines / day in commissioning. |
| Verbosity gate | `SCHEDULER_DECISION_TRACE_VERBOSE` env var (default `false`). Orthogonal to `SCHEDULER_MODE`. When `true`: per-evaluation "no-change" emissions land at `debug` level. Transitions, fires, decisions, rejections, errors always emit at `info`. Loki/LogQL filters by `level`. Commissioning runs with `true`; experiment defaults `false`. |
| Failure isolation | Every trace `log()` wrapped in try/except that swallows. Trace never raises into the control path. Tested per phase BOTH at the trace-helper level AND at the caller level (`_evaluate_layer_inputs`, `run_decision`, supervisor call site, etc.) — the guarantee that matters is "trace failure cannot interrupt the calling control path." |
| Rule-code touch | No return-shape changes. Two additive touches only:<br>1. `decide_day_type` mutates its existing `reasons` dict to add `reasons["evaluation_tape"] = [...]`. Return signature unchanged.<br>2. `should_add_price_aware_precool` and `compute_price_aware_precool_window` each gain one optional kwarg `trace_reason: list[str] \| None = None`. Default None means no overhead and no behaviour change; when provided, the function appends one `PrecoolCode` value on the way out. Same list-mutation pattern as touch #1 — chosen over a new wrapper that would have duplicated the rejection-tree logic.<br>Both made once before OSF filing and pinned. |
| Cross-correlation fields on every trace line | `tick_id` (UUID4 per scheduler tick, JSON FIELD only — **NOT a Loki label**, would explode cardinality). `scheduler_mode` always emitted as its own field (independent of arm context). `arm` emitted when `arm_calendar.current_arm_at(now_ct)` returns `"A"` or `"B"`; omitted when it returns `None` (outside the locked calendar window). This matches the semantics of `write_arm_mode` and the existing `hvac.arm_mode` rows — `arm` reflects calendar membership, not protocol mode. Off-protocol shadow/production operation inside the calendar window therefore still carries an `arm` field, which is correct: it documents which calendar period the tick fell in, regardless of whether the scheduler was acting on it. |
| reason_code taxonomy | Coded enums in `deploy/energy-stack/hvac-scheduler/decision_codes.py`. Append-only forever. Locked at OSF commit hash. Phase 1 establishes the file with the price-overlay subset; later phases extend it. |
| Coexistence | Trace is additive. Existing `hvac.decisions` / `hvac.actions` / `hvac.5cp_state` / `hvac.price_overlay` / `hvac.precool_window` measurements continue to drive Grafana dashboards. No measurement renamed, no field removed. |
| Out of Phase 1 scope | Report compiler, second rule engine, Influx mirror, new dashboards, parallel arm simulation, behavior changes, removal of any existing log line, change to `SCHEDULER_MODE` semantics. |

## Feature-level acceptance test (outside-in)

Single test file `tests/test_decision_trace.py` in the scheduler service. Asserts every silent gap is closed. Each per-phase test is marked `@pytest.mark.xfail(strict=True, reason="phase N not yet implemented")` (NEVER `skip` — per memory `feedback-outside-in-xfail-not-skip`, the feature test must produce a visible signal at every PR boundary). The `xfail` marker comes off in the PR that makes the test pass against the real implementation with zero scaffolding — that is the only definition of feature-complete for that gap. The chain test stays `xfail(strict=True)` until end of Phase 5.

```
class TestDecisionTrace:
    def test_price_overlay_eval_emits_every_call(...)        # Phase 1 — xfail until Phase 1 PR
    def test_price_overlay_trace_is_failure_isolated(...)    # Phase 1 — xfail until Phase 1 PR
    def test_layer_resolution_eval_emits_every_tick(...)     # Phase 2 — xfail until Phase 2 PR
    def test_supervisor_eval_emits_every_invocation(...)     # Phase 3 — xfail until Phase 3 PR
    def test_precool_rejection_emits_with_reason(...)        # Phase 4 — xfail until Phase 4 PR
    def test_day_type_negative_branches_in_trace(...)        # Phase 5 — xfail until Phase 5 PR
    def test_causal_chain_reconstructable_from_log(...)      # xfail until end of Phase 5
```

The chain test exercises one full tick: synthetic ComEd price arrival → price overlay eval logged → layer resolution logged → supervisor logged → would-push logged. Reads stdout JSON, walks the chain, asserts each link emits the same `tick_id` so downstream LogQL queries can correlate. Marker removed only after all five phases.

## Phases (vertical slices)

### Phase 1 — tracer bullet: price overlay per-eval log

Smallest end-to-end cut through every layer this feature touches (env var plumbing, decision_codes.py, log call, failure-isolation, acceptance test, Loki visibility, Grafana Explore demo).

Changes:

- New `deploy/energy-stack/hvac-scheduler/decision_codes.py`. Initial enum (caller-observable state only — no internal state-machine knowledge re-implemented; finer-grained "hold-active vs price-above-release" distinctions are NOT exposed as separate codes because doing so would require either re-implementing the state machine or changing `evaluate_price_overlay`'s return shape, neither of which is permitted):
  - `PRICE_OVERLAY_NORMAL_BELOW_TRIGGER` — tier unchanged at normal, price below all triggers
  - `PRICE_OVERLAY_HELD_IN_TIER` — tier unchanged at elevated/scarcity; reason (hold-active vs price-above-release) reconstructable from `hold_minutes_remaining` + `price_cents` fields on the trace line
  - `PRICE_OVERLAY_UPGRADED_TO_ELEVATED`
  - `PRICE_OVERLAY_UPGRADED_TO_SCARCITY`
  - `PRICE_OVERLAY_DOWNGRADED_TO_ELEVATED`
  - `PRICE_OVERLAY_RELEASED_TO_NORMAL`
  - `PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED` — price feed null, tier carried forward, stale-threshold NOT exceeded
  - `PRICE_OVERLAY_STALE_FEED_RELEASED` — covers the existing `price_feed_stale_tier_released` log path; tier forcibly released to normal after the stale-feed window
- New trace helper `_trace(event, level, verbose, **fields)` in `app.py` (one place; try/except wraps `log()`; honours `SCHEDULER_DECISION_TRACE_VERBOSE`; auto-inlines `tick_id`, `scheduler_mode`, and `arm` (when `current_arm_at(now_ct)` returns A/B) into every emitted line).
- New env `SCHEDULER_DECISION_TRACE_VERBOSE` (default `false`). Wire through `Config.decision_trace_verbose`. Compose env passthrough `${SCHEDULER_DECISION_TRACE_VERBOSE:-false}`. Pi `.env` sets `true` for commissioning.
- New log emission at the caller side in `app.py:_evaluate_layer_inputs`, immediately after `evaluate_price_overlay` returns. `price_overlay.py` stays pure (zero `log()` calls — the module already has that property, worth preserving). Emit one line per call with:
  - `event="decision_trace.price_overlay_eval"`
  - `tick_id` (one per scheduler tick; UUID4 generated at top of tick loop)
  - `scheduler_mode` (one of `shadow` / `experiment` / `production`; always emitted)
  - `arm` (from `arm_calendar.current_arm_at(now_ct)`; emitted only when it returns `"A"` or `"B"`; omitted when it returns `None` outside the calendar window)
  - `price_cents`, `price_is_stale` (bool)
  - `prev_tier`, `new_tier`
  - `outcome` ∈ `{held, upgraded, downgraded, released}`
  - `reason_code` (one of the enums above)
  - `hold_minutes_remaining` (None if normal or hold elapsed)
  - `level=info` for transitions / stale-release; `level=debug` for held outcomes (gated on verbose)

Acceptance:

- `test_price_overlay_eval_emits_every_call` — drive `_evaluate_layer_inputs` (the caller, not the pure function) through three input scenarios producing held / upgrade / downgrade outcomes; capture stdout; assert exactly three trace lines with the expected `outcome` + `reason_code`. `evaluate_price_overlay` is exercised transitively but is not the unit under test for the trace assertions.
- `test_price_overlay_trace_is_failure_isolated` — fault-injects `app.log` (the realistic outermost failure mode the `_trace` wrapper is designed to absorb: Loki down / stdout broken / bad field type). Asserts that with `log()` raising on every `decision_trace.*` event: `_evaluate_layer_inputs` still completes; the price-overlay state machine still updates correctly; the existing `hvac.price_overlay` transition write still happens; the exception does not propagate to `_evaluate_layer_inputs`'s caller. Patching `app._trace` directly tests an unrealistic scenario where the safety wrapper itself is replaced with a broken implementation, so we don't.
- `test_tick_id_not_in_loki_labels` — read `deploy/energy-stack/promtail/config.yml`; assert no pipeline stage promotes `tick_id` to a Loki label.
- `test_arm_field_present_only_inside_calendar_window` — drive `_evaluate_layer_inputs` with two synthetic `now_ct` values: one inside the locked calendar window (e.g., 2026-07-04 12:00 CT, where `current_arm_at` returns `"A"` or `"B"`) and one outside it (e.g., 2026-05-14 12:00 CT pre-experiment, where `current_arm_at` returns `None`). Assert `arm` present (with the correct A/B value) in the first case, absent in the second. `scheduler_mode` is independently asserted in a separate test (varied across `shadow`/`experiment`/`production` regardless of calendar position).
- Verify on Pi after merge: `docker compose logs --since 1h hvac-scheduler | jq 'select(.event=="decision_trace.price_overlay_eval")'` shows a line per scheduler tick.

Demoable: tail the log during a real ComEd 5-min arrival, see the eval roll past with current price + held/upgrade reason. That's the live tracer experience.

### Phase 2 — layer resolution per tick

Changes:

- `decision_codes.py` extended:
  - `LAYER_RESOLUTION_SCHEDULE_WINS`
  - `LAYER_RESOLUTION_PRICE_OVERLAY_WINS`
  - `LAYER_RESOLUTION_5CP_WINS`
  - `LAYER_RESOLUTION_TIE_WARMER_WINS` (when multiple layers propose the same setpoint)
- New trace at the call site of `resolve_layer_priority` in `app.py` (the function itself is pure; log at caller).
- Fields: `tick_id`, `scheduler_mode`, `arm` (when `current_arm_at(now_ct)` returns A/B), `schedule_cool_f`, `price_overlay_tier`, `price_cool_f`, `fivecp_active` (bool), `fivecp_scopes_fired` (list), `fivecp_cool_f`, `effective_cool_f`, `prev_effective_cool_f`, `winning_layer` ∈ `{schedule, price_overlay, 5cp, tie}`, `reason_code`. The `tie` value corresponds to `LAYER_RESOLUTION_TIE_WARMER_WINS` and is emitted when multiple non-schedule layers (5cp and price overlay) propose the same effective setpoint — preserves the forensic distinction between "schedule won alone" and "schedule was matched by a non-schedule layer at the warmest." Operator can inspect the per-layer fields on the trace to see which agreed.
- Level: `info` when `effective_cool_f` differs from previous tick; `debug` when identical (gated on verbose).

Acceptance:

- `test_layer_resolution_eval_emits_every_tick` — drive three input combinations (schedule-only, price-elevated, 5CP-active); assert three trace lines with correct `winning_layer` + `reason_code` + tick_id propagation.
- `test_layer_resolution_tie_warmer_wins` — drive an input combination where both the price overlay (scarcity-tier override 85F) and 5CP (active, 85F shutoff) propose the same effective setpoint; assert `winning_layer="tie"` + `reason_code="LAYER_RESOLUTION_TIE_WARMER_WINS"`.
- Failure-isolation test parallel to Phase 1: asserts BOTH that no exception propagates AND that the existing thermostat-write path (read_thermostat_snapshot + execute_action + write_action) still executes.

### Phase 3 — supervisor per invocation

Today the supervisor log line is implicit (its decision rides inside `action_fired` and `mid_period_repush`). Phase 3 surfaces it on its own.

Cadence note: the supervisor is invoked when a layer resolution proposes a setpoint that needs gating — not on every scheduler tick. Trace fires **once per supervisor call**, not once per tick. A tick where no layer change occurs and supervisor is not invoked produces no supervisor trace line. This is intentional: tracing absence-of-invocation would imply an evaluation that didn't happen.

Changes:

- `decision_codes.py` extended with `SupervisorCode` enum. The shipped enum is derived from caller-observable state (proposed setpoints + SupervisorDecision dataclass), NOT from internal supervisor knowledge:
  - `SUPERVISOR_APPROVED` — proposed values in range AND no emergency
  - `SUPERVISOR_CLAMPED_COOL_FLOOR` — cool clamped UP (proposed_cool < 65)
  - `SUPERVISOR_CLAMPED_COOL_CEILING` — cool clamped DOWN (proposed_cool > 86)
  - `SUPERVISOR_CLAMPED_HEAT_FLOOR` — heat clamped UP (proposed_heat < 55)
  - `SUPERVISOR_CLAMPED_HEAT_CEILING` — heat clamped DOWN (proposed_heat > 75)
  - `SUPERVISOR_CLAMPED_MULTIPLE` — both axes clamped in same call
  - `SUPERVISOR_EMERGENCY_OVERHEAT` — indoor >= 86°F triggers emergency override

  Plan-aspirational `SUPERVISOR_EMERGENCY_NO_INDOOR_TEMP` is **NOT in the shipped enum**: the production supervisor does not escalate to emergency when `indoor_temp_f` is missing — it falls through to the clamp check, which (with in-range setpoints) returns `approved`. The diagnostic is surfaced via the `indoor_temp_available: bool` field on the trace line, so an operator can filter `decision_trace.supervisor` by `indoor_temp_available=false` to see when the safety floor was running blind without inventing a fake "emergency" reason code.
- New trace at every `validate_setpoints(...)` call site. Fields: `tick_id`, `scheduler_mode`, `arm` (when `current_arm_at(now_ct)` returns A/B), `proposed_cool_f`, `proposed_heat_f`, `indoor_temp_f` (None when unavailable), `indoor_temp_available` (bool), `decision` ∈ `{approved, clamped, emergency}`, `reason_code`, `supervisor_reason` (the existing free-text reason from the SupervisorDecision), `final_cool_f`, `final_heat_f`.
- Level: `info` for any non-approved decision; `debug` for approved (gated on verbose).

Acceptance:

- `test_supervisor_eval_emits_every_invocation` — parametrized over 8 scenarios covering all 7 reason codes (approved + approved-no-indoor-temp + 4 single-axis clamps + clamped-multiple + emergency-overheat). Asserts `reason_code`, `decision`, `level`, `indoor_temp_available`, and `tick_id` correctness for each.
- `test_supervisor_trace_fires_from_mid_period_repush` — integration test confirming the call-site wire-up and that supervisor + layer_resolution traces share `tick_id`.
- `test_supervisor_trace_is_failure_isolated` — fault-injects `app.log` on supervisor events; asserts no exception propagates AND that the existing thermostat-write path (`read_thermostat_snapshot` + `execute_action` + `write_action`) still executes.

### Phase 4 — §7 precool rejection reason

Today: window selected → row written. Window rejected → silent.

Changes:

- `decision_codes.py` extended with `PrecoolCode` enum reflecting the **actual branches in production code**, not aspiration:
  - `PRECOOL_SELECTED`
  - `PRECOOL_REJECTED_NO_DA_LMP_DATA`
  - `PRECOOL_REJECTED_NO_FORECAST` (real branch in `compute_price_aware_precool_window`)
  - `PRECOOL_REJECTED_DA_LMP_INCOMPLETE` (real branch in `should_add_price_aware_precool` when `len(prices) < 24`)
  - `PRECOOL_REJECTED_NO_CHEAP_WINDOW`
  - `PRECOOL_REJECTED_NO_SPIKE_WINDOW_AFTER_GAP` (collapses planned NO_EVENING_SPIKE + GAP_TOO_SHORT — both produce the same observable rejection path)

  Plan-aspirational codes DROPPED (no matching branch in current production code):
  - `GAP_TOO_SHORT` — collapsed into `NO_SPIKE_WINDOW_AFTER_GAP`. The gap requirement is enforced by starting the spike search after `cheap_start + MIN_GAP_BETWEEN_CHEAP_AND_SPIKE_HOURS`; the only observable outcome is "no spike found beyond the gap."
  - `DAY_TYPE_NOT_ELIGIBLE` — no day-type gate exists in the function. `compute_price_aware_precool_window` is called at 21:00 regardless of day_type.
  - `ALREADY_DEEPER_VIA_HOT_STREAK` — that interaction is handled by `merge_same_hour_actions_deepest_wins` AFTER selection, not as a precool rejection inside the §7 path.

  Same reconciliation pattern as Phase 1's `HELD_IN_TIER` and Phase 3's `CLAMPED_MULTIPLE`. Codes are append-only / OSF-bound, so the enum must describe production behaviour.
- **Design pattern revision**: plan originally called for a NEW wrapper `compute_price_aware_precool_window_with_trace(...)` that re-implements the rejection decision tree as a thin shell. Shipped instead: an additive optional kwarg `trace_reason: list[str] | None = None` on the EXISTING `compute_price_aware_precool_window` and `should_add_price_aware_precool` functions. The functions append exactly one `PrecoolCode` value to the list on the way out. Default `None` means no overhead and no behaviour change — existing callers unaffected. This is the same dict/list-mutation pattern Phase 1 user-approved for `decide_day_type["evaluation_tape"]` (Phase 5). The change is minimal (one optional kwarg per function) and avoids the rejection-tree-duplication risk the wrapper approach carried.
- `app.py:run_decision` passes a fresh `trace_reason` list to `compute_price_aware_precool_window`, reads the appended code, emits the trace.
- New `_trace_precool` helper emission. Always fires once at 21:00 per night; always logs `info` level (one row / night is not noisy). `run_decision` generates a fresh `tick_id` since it runs outside the `run_schedule_check` tick loop and has no shared `tick_id`.
- Fields: `tick_id`, `scheduler_mode`, `arm` (when `current_arm_at(now_ct)` returns A/B), `decision_for_date`, `day_type`, `selected` (bool), `hour_ct` (int or null), `depth_f` (int or null), `reason_code`.

Acceptance:

- `test_precool_emits_with_reason` — parametrized over the 6 PrecoolCode outcomes by driving `compute_price_aware_precool_window` with mocked fetch helpers + capturing the `trace_reason` list. Asserts correct code for each branch.
- `test_trace_precool_emits_trace_line` — drives `_trace_precool` directly with both happy-path and rejection inputs; asserts well-formed `decision_trace.precool_decision` line with all expected fields.
- `test_precool_trace_is_failure_isolated` — fault-injects `app.log` on precool events; asserts no exception propagates.
- Existing `should_add_price_aware_precool` and `compute_price_aware_precool_window` tests unchanged (callers that don't pass `trace_reason` are unaffected).

### Phase 5 — day-type negative branches

Today: `decide_day_type` returns `(day_type, reasons)` with `reason: str` carrying only the winner's reason. The trace shows what fired, not what was considered.

Changes:

- `decision_codes.py` extended with `DayTypeCode` enum — 9 values reflecting the actual rule branches in `_classify_with_tape` (the Phase 5 helper) + the streak escalation rules in `decide_day_type`:
  - `HOT_HEAT_ADVISORY`, `HOT_HIGH_GE_85`, `HOT_APPARENT_GE_90` — the three single-day HOT triggers, in precedence order
  - `HOT_STREAK_MULTI_DAY`, `HOT_STREAK_5CP_RISK` — the two HOT_STREAK_DAY1 escalation paths
  - `NORMAL_HIGH_75_TO_84`, `NORMAL_MISSING_TEMPS_FALLBACK`, `NORMAL_NO_FORECAST_FALLBACK` — the three NORMAL paths (winning, P2.7 missing-temps safe default, no-forecast fallback)
  - `MILD_HIGH_LT_75` — the MILD default
- New helper `_classify_with_tape(forecast) -> (day_type, list[tape_entry])` in `app.py`. Same rule precedence as the existing `_classify_one_day`, but additionally records each evaluated rule as a tape entry with `{rule, threshold, actual, fired: bool, reason_code}`. `_classify_one_day` itself is unchanged (other callers — day2 classification — keep their existing path).
- `decide_day_type` keeps its `(day_type, reasons)` return signature. The function uses `_classify_with_tape` internally and appends streak-path entries (`streak_multi_day`, `streak_5cp_risk`) when the base classification is HOT. The final tape is stored in `reasons["evaluation_tape"]`. All existing keys (`high_f`, `apparent_max_f`, `is_heat_advisory`, `max_dewpoint_f`, `alert_summary`, `reason`, plus conditional streak fields) preserved with same types — verified by `test_existing_decide_day_type_callers_unchanged`.
- New `_trace_day_type(...)` emission helper. Wired at `app.py:run_decision` AND `app.py:run_decision_revisit`. Fields: `tick_id`, `scheduler_mode`, `arm` (when applicable), `decision_for_date`, `winning_day_type`, `evaluation_tape` (inlined), `winning_reason`, plus the scalar fields from the existing reasons dict for self-contained forensics. Level `info`. `run_decision` reuses the same `tick_id` as the §7 precool trace (Phase 4) so both 21:00 traces correlate; `run_decision_revisit` generates a fresh `tick_id` per revisit (06:00 + 11:00 CT).
- Third `decide_day_type` call site at `fetch_today_decision` (recovery / cold-start path) is NOT wired with a trace in Phase 5. The plan's "run_decision + run_decision_revisit" scope holds. Recovery path observability is a known gap; can be added in a follow-up if commissioning surfaces a need.

Acceptance:

- `test_day_type_negative_branches_in_trace` — input lands NORMAL but is close to HOT (high_f=84, apparent=88); asserts HOT branches evaluated and rejected in the tape with correct thresholds + actuals, and NORMAL branch fired.
- `test_day_type_streak_branches_in_tape` — HOT day + day2=HOT produces tape with both base HOT trigger fired AND streak_multi_day fired AND short-circuit: 5cp_risk rule NOT evaluated (multi-day path wins first).
- `test_day_type_no_forecast_fallback_tape` — None forecast yields single-entry tape with NORMAL_NO_FORECAST_FALLBACK fired; existing `reason` + `fallback` keys preserved.
- `test_existing_decide_day_type_callers_unchanged` — regression guard pinning every existing reasons-dict key with its expected type.
- `test_trace_day_type_emits_trace_line` — drives `_trace_day_type` directly; asserts well-formed `decision_trace.day_type_decision` line with inlined evaluation_tape + scalar fields.
- `test_day_type_trace_is_failure_isolated` — fault-injects `app.log` on day_type events; asserts no exception propagates.
- `test_causal_chain_reconstructable_from_log` — **xfail marker removed**. Drives one synthetic scheduler tick (`_evaluate_layer_inputs` then `_push_layer_change_mid_period`, both threaded with the same `tick_id`); asserts every emitted `decision_trace.*` line shares that `tick_id` and all three event types (price_overlay_eval, layer_resolution, supervisor) appear at least once.

**With this PR merged the feature is complete per AGENTS.md outside-in TDD rule + memory `feedback-outside-in-xfail-not-skip`**: the feature-level acceptance test passes against the real implementation with zero scaffolding, and zero xfail markers remain on `test_decision_trace.py`.

## Phase 6 (deferred — not part of this plan)

The daily Markdown / Telegram report compiler. Reads Loki via LogQL, renders yesterday's chain into a human-readable report, posts via existing `telegram-notifier`. Pure render. No rule evaluation, no second engine. Will get its own plan doc when Phase 1-5 are merged and the trace volume + shape are observed in practice.

## Risks

| Risk | Mitigation |
|---|---|
| Loki ingest pipeline can't handle 1500 lines/day from one container | Existing pipeline already handles `fivecp_eval` per-tick + all the existing scheduler logs; smoke-test on Pi after Phase 1 merge before fanning out. If problematic, drop per-tick to once-per-5-min by reusing `_FIVECP_AUDIT_INTERVAL`-style throttle. |
| Stdout JSON line gets truncated at high frequency | Existing log calls don't hit this; trace lines are similar size. Monitor after Phase 1. |
| `decide_day_type` evaluation-tape addition breaks an existing caller that does dict-key iteration | The change is dict-mutation (adds `reasons["evaluation_tape"]`); no return-shape change, no key removed, no existing key's type changed. Pin a test that verifies all existing `reasons` keys are still present and same-typed. |
| Verbose mode enabled in experiment by accident inflates Loki cardinality | Compose env defaults `false`; Pi `.env` only sets `true` for commissioning window; OSF-locked commit hash records the default and the operator-set state at experiment start. |
| Rule-code touch lands at OSF commit hash | Both touches (`decide_day_type` dict mutation in Phase 5; `should_add_price_aware_precool` + `compute_price_aware_precool_window` `trace_reason` kwarg in Phase 4) land before Phase 5 ships; both are additive and behaviour-identical when the new kwarg is not passed; OSF filing happens after this plan completes; the prereg's frozen commit hash is the post-merge commit. |
| `tick_id` accidentally promoted to a Loki label | Phase 1 acceptance includes an assertion against `promtail/config.yml` that no pipeline stage lifts `tick_id`. Re-check on subsequent phases when adding new fields. |
| `arm` field on trace lines diverges from the canonical `hvac.arm_mode` rows / `arm_calendar` semantics | Use `arm_calendar.current_arm_at(now_ct)` as the single source of truth — same call `write_arm_mode` makes. Literal `"A"` / `"B"` when inside the calendar window; field omitted when it returns `None`. Phase 1 acceptance test (`test_arm_field_present_only_inside_calendar_window`) pins the semantics against `current_arm_at` directly, not against `SCHEDULER_MODE`. |

## Non-goals (locked)

- No new Influx measurement.
- No new long-lived service / container.
- No report compiler in this plan.
- No second rule engine, no rule re-implementation, no independent verifier.
- No change to thermostat-writing logic.
- No change to `SCHEDULER_MODE` semantics or values.
- No change to `arm_calendar.py` or arm-calendar gating logic.
- No change to existing measurement schemas or Grafana dashboards.
- No new decision branches in the controller.
- No removal of existing log lines.

## Branching

One PR per phase. Each PR uses `--base main` per memory `feedback_stacked_pr_retargeting`; no stacking. Wait for the prior PR to merge before opening the next. PR #104 (this plan doc only) was force-pushed onto current `main` on 2026-05-14 to drop three stage8 Phase 2 commits (`2a3fa26`, `9f9547b`, `d2caf7b`) that had been inadvertently carried on the original feature branch and have since landed on `main` via the squash-merged PR #103. Subsequent phase branches cut fresh off `main` after PR #104 merges.

## Archive

Move to `docs/plans/archive/decision-trace-plan.md` in the commit that closes Phase 5.
