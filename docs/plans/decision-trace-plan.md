---
date: 2026-05-14
owner: chris
status: draft
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
| Failure isolation | Every trace `log()` wrapped in try/except that swallows. Trace never raises into the control path. Tested per phase BOTH at the trace-helper level AND at the caller level (`run_per_tick_overlays`, `run_decision`, supervisor call site, etc.) — the guarantee that matters is "trace failure cannot interrupt the calling control path." |
| Rule-code touch | No return-shape changes. Two additive touches only:<br>1. `decide_day_type` mutates its existing `reasons` dict to add `reasons["evaluation_tape"] = [...]`. Return signature unchanged.<br>2. New wrapper `compute_price_aware_precool_window_with_trace(...) -> (dict \| None, reason_code)` lives alongside the original pure function. Original function and existing callers untouched.<br>Both made once before OSF filing and pinned. |
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

- New `deploy/energy-stack/hvac-scheduler/decision_codes.py`. Initial enum:
  - `PRICE_OVERLAY_HELD_NORMAL_BELOW_TRIGGER`
  - `PRICE_OVERLAY_HELD_ELEVATED_HOLD_ACTIVE`
  - `PRICE_OVERLAY_HELD_ELEVATED_PRICE_ABOVE_RELEASE`
  - `PRICE_OVERLAY_HELD_SCARCITY_HOLD_ACTIVE`
  - `PRICE_OVERLAY_HELD_SCARCITY_PRICE_ABOVE_RELEASE`
  - `PRICE_OVERLAY_UPGRADED_TO_ELEVATED`
  - `PRICE_OVERLAY_UPGRADED_TO_SCARCITY`
  - `PRICE_OVERLAY_DOWNGRADED_TO_ELEVATED`
  - `PRICE_OVERLAY_RELEASED_TO_NORMAL`
  - `PRICE_OVERLAY_STALE_FEED_RELEASED` (covers the existing `price_feed_stale_tier_released` log path)
- New trace helper `_trace(event, level, verbose, **fields)` in `app.py` (one place; try/except wraps `log()`; honours `SCHEDULER_DECISION_TRACE_VERBOSE`; auto-inlines `tick_id`, `scheduler_mode`, and `arm` (when `current_arm_at(now_ct)` returns A/B) into every emitted line).
- New env `SCHEDULER_DECISION_TRACE_VERBOSE` (default `false`). Wire through `Config.decision_trace_verbose`. Compose env passthrough `${SCHEDULER_DECISION_TRACE_VERBOSE:-false}`. Pi `.env` sets `true` for commissioning.
- New log emission at the caller side in `app.py:run_per_tick_overlays`, immediately after `evaluate_price_overlay` returns. `price_overlay.py` stays pure (zero `log()` calls — the module already has that property, worth preserving). Emit one line per call with:
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

- `test_price_overlay_eval_emits_every_call` — drive `run_per_tick_overlays` (the caller, not the pure function) through three input scenarios producing held / upgrade / downgrade outcomes; capture stdout; assert exactly three trace lines with the expected `outcome` + `reason_code`. `evaluate_price_overlay` is exercised transitively but is not the unit under test for the trace assertions.
- `test_price_overlay_trace_is_failure_isolated` — two tiers:
  1. Monkeypatch the trace helper's underlying `log()` to raise; assert `run_per_tick_overlays` still completes, `evaluate_price_overlay` still returns the correct `(tier, state)` tuple, and the existing `hvac.price_overlay` transition write still happens.
  2. Same fault injection; assert `run_per_tick_overlays` does not propagate the exception to its caller (`run_schedule_check`-side path).
- `test_tick_id_not_in_loki_labels` — read `deploy/energy-stack/promtail/config.yml`; assert no pipeline stage promotes `tick_id` to a Loki label.
- `test_arm_field_present_only_inside_calendar_window` — drive `run_per_tick_overlays` with two synthetic `now_ct` values: one inside the locked calendar window (e.g., 2026-07-04 12:00 CT, where `current_arm_at` returns `"A"` or `"B"`) and one outside it (e.g., 2026-05-14 12:00 CT pre-experiment, where `current_arm_at` returns `None`). Assert `arm` present (with the correct A/B value) in the first case, absent in the second. `scheduler_mode` is independently asserted in a separate test (varied across `shadow`/`experiment`/`production` regardless of calendar position).
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
- Fields: `tick_id`, `scheduler_mode`, `arm` (when `current_arm_at(now_ct)` returns A/B), `schedule_cool_f`, `price_overlay_tier`, `price_cool_f`, `fivecp_active` (bool), `fivecp_scopes_fired` (list), `fivecp_cool_f`, `effective_cool_f`, `winning_layer` ∈ `{schedule, price_overlay, 5cp}`, `reason_code`.
- Level: `info` when `effective_cool_f` differs from previous tick; `debug` when identical (gated on verbose).

Acceptance:

- `test_layer_resolution_eval_emits_every_tick` — drive three input combinations (schedule-only, price-elevated, 5CP-active); assert three trace lines with correct `winning_layer`.
- Failure-isolation test parallel to Phase 1.

### Phase 3 — supervisor per invocation

Today the supervisor log line is implicit (its decision rides inside `action_fired` and `mid_period_repush`). Phase 3 surfaces it on its own.

Cadence note: the supervisor is invoked when a layer resolution proposes a setpoint that needs gating — not on every scheduler tick. Trace fires **once per supervisor call**, not once per tick. A tick where no layer change occurs and supervisor is not invoked produces no supervisor trace line. This is intentional: tracing absence-of-invocation would imply an evaluation that didn't happen.

Changes:

- `decision_codes.py` extended:
  - `SUPERVISOR_APPROVED`
  - `SUPERVISOR_CLAMPED_COOL_FLOOR`
  - `SUPERVISOR_CLAMPED_COOL_CEILING`
  - `SUPERVISOR_CLAMPED_HEAT_FLOOR`
  - `SUPERVISOR_CLAMPED_HEAT_CEILING`
  - `SUPERVISOR_EMERGENCY_OVERHEAT`
  - `SUPERVISOR_EMERGENCY_NO_INDOOR_TEMP` (snapshot missing)
- New trace at every `safety_supervisor.evaluate(...)` call site. Fields: `tick_id`, `scheduler_mode`, `arm` (when `current_arm_at(now_ct)` returns A/B), `proposed_cool_f`, `proposed_heat_f`, `indoor_temp_f`, `indoor_temp_available` (bool), `decision` ∈ `{approved, clamped, emergency}`, `reason_code`, `final_cool_f`, `final_heat_f`.
- Level: `info` for any non-approved decision; `debug` for approved (gated on verbose).

Acceptance:

- `test_supervisor_eval_emits_every_invocation` — drive approved / clamped-floor / clamped-ceiling / emergency-overheat / emergency-no-temp paths; assert correct `reason_code` on each.
- Failure-isolation test.

### Phase 4 — §7 precool rejection reason

Today: window selected → row written. Window rejected → silent.

Changes:

- `decision_codes.py` extended:
  - `PRECOOL_SELECTED`
  - `PRECOOL_REJECTED_NO_DA_LMP_DATA`
  - `PRECOOL_REJECTED_NO_CHEAP_WINDOW`
  - `PRECOOL_REJECTED_NO_EVENING_SPIKE`
  - `PRECOOL_REJECTED_GAP_TOO_SHORT`
  - `PRECOOL_REJECTED_DAY_TYPE_NOT_ELIGIBLE`
  - `PRECOOL_REJECTED_ALREADY_DEEPER_VIA_HOT_STREAK`
- New wrapper `compute_price_aware_precool_window_with_trace(...) -> (dict | None, reason_code)` in `app.py` (or a sibling module). The original `compute_price_aware_precool_window` keeps its `dict | None` signature and its existing call sites are untouched. The wrapper re-implements the rejection decision tree as a thin shell around the pure function, returning the appropriate `reason_code` for each None-yielding branch.
- `app.py:run_decision` switches from calling `compute_price_aware_precool_window` directly to calling the new wrapper, so the trace line carries the reason code.
- New trace at the call site in `app.py:run_decision`. Always fires once at 21:00; always logs `info` level (one row / night is not noisy).
- Fields: `tick_id`, `scheduler_mode`, `arm` (when `current_arm_at(now_ct)` returns A/B), `decision_for_date`, `day_type`, `selected` (bool), `hour_ct`, `depth_f`, `reason_code`.

Acceptance:

- `test_precool_rejection_emits_with_reason` — drive each rejection branch by calling the wrapper with synthetic inputs; assert correct `reason_code` on each.
- `test_precool_selection_emits` — drive the happy path via the wrapper; assert `PRECOOL_SELECTED` with hour and depth.
- Existing `compute_price_aware_precool_window` tests unchanged (function signature is unchanged).
- Caller-level failure-isolation test parallel to Phase 1.

### Phase 5 — day-type negative branches

Today: `decide_day_type` returns `(day_type, reasons)` with `reason: str` carrying only the winner's reason. The trace shows what fired, not what was considered.

Changes:

- `decision_codes.py` extended for each rule arm. `DAY_TYPE_HOT_high_f_ge_85`, `DAY_TYPE_HOT_apparent_max_ge_90`, `DAY_TYPE_HOT_STREAK_DAY1_day2_ge_85`, `DAY_TYPE_NORMAL_high_f_75_to_84`, `DAY_TYPE_MILD_high_f_lt_75`, etc. (Full list locked when writing the function.)
- `decide_day_type` keeps its `(day_type, reasons)` return signature. The function mutates the existing `reasons` dict to add `reasons["evaluation_tape"] = [...]` — a list of `{rule, threshold, actual, fired: bool, reason_code}` dicts, one per evaluated branch. Existing callers that consume `reasons["high_f"]`, `reasons["reason"]`, etc. are unaffected.
- New trace at the call site in `app.py:run_decision` and `app.py:run_decision_revisit`. Fields: `tick_id`, `scheduler_mode`, `arm` (when `current_arm_at(now_ct)` returns A/B), `decision_for_date`, `winning_day_type`, `evaluation_tape` (read from `reasons["evaluation_tape"]`, inlined into the log line). Level `info`.

Acceptance:

- `test_day_type_negative_branches_in_trace` — drive one input that lands NORMAL but is close to HOT (high_f=84, apparent=88); assert `reasons["evaluation_tape"]` contains the HOT branch evaluated and rejected with the correct threshold and actual value, and the trace line carries it.
- `test_existing_decide_day_type_callers_unchanged` — pin tests that read `reasons["high_f"]`, `reasons["reason"]`, etc. continue to pass without modification (regression guard against an accidental return-shape change).
- `test_causal_chain_reconstructable_from_log` (the chain test mentioned in the acceptance section) — xfail marker removed: one synthetic full-tick run produces a connected chain of decision_trace.* lines sharing a tick_id, covering price overlay → layer resolution → supervisor → would-push.

## Phase 6 (deferred — not part of this plan)

The daily Markdown / Telegram report compiler. Reads Loki via LogQL, renders yesterday's chain into a human-readable report, posts via existing `telegram-notifier`. Pure render. No rule evaluation, no second engine. Will get its own plan doc when Phase 1-5 are merged and the trace volume + shape are observed in practice.

## Risks

| Risk | Mitigation |
|---|---|
| Loki ingest pipeline can't handle 1500 lines/day from one container | Existing pipeline already handles `fivecp_eval` per-tick + all the existing scheduler logs; smoke-test on Pi after Phase 1 merge before fanning out. If problematic, drop per-tick to once-per-5-min by reusing `_FIVECP_AUDIT_INTERVAL`-style throttle. |
| Stdout JSON line gets truncated at high frequency | Existing log calls don't hit this; trace lines are similar size. Monitor after Phase 1. |
| `decide_day_type` evaluation-tape addition breaks an existing caller that does dict-key iteration | The change is dict-mutation (adds `reasons["evaluation_tape"]`); no return-shape change, no key removed, no existing key's type changed. Pin a test that verifies all existing `reasons` keys are still present and same-typed. |
| Verbose mode enabled in experiment by accident inflates Loki cardinality | Compose env defaults `false`; Pi `.env` only sets `true` for commissioning window; OSF-locked commit hash records the default and the operator-set state at experiment start. |
| Rule-code touch lands at OSF commit hash | Both touches (`decide_day_type` dict mutation, `compute_price_aware_precool_window_with_trace` wrapper) land before Phase 5 ships; original functions remain bit-identical; OSF filing happens after this plan completes; the prereg's frozen commit hash is the post-merge commit. |
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
