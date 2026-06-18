---
date: 2026-06-18
owner: chris
status: draft — dual-reviewed 2026-06-18
role-label: code-team
---

# MILD-as-full-controller — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Pi `hvac-scheduler` the full Arm B controller on **every** day-type, including MILD, so the real-time price overlay actuates on MILD days instead of being silently dormant.

**Architecture:** Today MILD is a single `MILD_RELEASE_HOLD` action that hands the day to the CTK04 thermostat's own program; the resulting `last_schedule_cool_f is None` short-circuits the price-overlay re-push (`app.py:2971`), so a ≥20¢ scarcity spike never pushes 85°F. The fix replaces `MILD_SCHEDULE` with a real Pi-owned setpoint schedule (no pre-cool + the shared coast/recover/sleep baseline, Permanent holds — identical mechanism to NORMAL/HOT). With a non-None baseline present, the **existing** layer-resolution + mid-period re-push machinery actuates the overlay on MILD automatically. No new hold mechanism, no dead-man, no watchdog changes.

**Tech Stack:** Python 3.11, pytest, InfluxDB client, pyControl4 (Control4 thermostat), Docker Compose on Pi-lab.

## Global Constraints

- **Day-type's *scheduled* schedule owns ONLY the pre-cool.** Day-type decides pre-cool depth/timing (the MILD day-type schedule has **no** pre-cool action; NORMAL/HOT/STREAK = progressively earlier+deeper). Day-type does NOT gate price-reactivity. The back half of the day (coast → recover → sleep) is a shared comfort baseline the Pi owns on every day-type. (Verified: evening recover=75 / sleep=73 are already identical across all active day-types.) **Caveat (review):** the §7 day-ahead price-aware pre-cool injection (`read_precool_window_for_date` → `merge_same_hour_actions_deepest_wins`, `app.py:3128`) is NOT day-type-gated, so a grid-event night can inject a pre-cool action into MILD too. Now that MILD is Pi-owned that's desirable (price-aware pre-cool on a cool grid-event day) — but do not state "MILD never pre-cools" as an invariant.
- **Holds stay Permanent.** No timed "Hold Until," no dead-man automation. While the Pi is **alive**, the supervisor caps overshoot (indoor ≥86°F → 74°F, runs in-process every tick). On a **Pi-down** event the supervisor is gone with the process; protection is then: (a) the thermostat holds its last Pi-pushed setpoint, which on a scarcity hold is ≤85°F — warm but safe; (b) the external off-box host-liveness monitor alerts → manual hold-release; (c) once the hold clears, the CTK04 program reclaims (cooler comfort). See [docs/SERVICES.md] and [docs/HVAC_LOGIC.md].
- **5CP unchanged.** Stays day-ahead/telemetry-only per binding spec §11 #14. Out of scope.
- **Experiment is terminated (2026-06-18).** The OSF pre-registration freeze is no longer binding, so the binding-spec §11 #12 "MILD has no setpoint table" completeness lock and the `B-fallback` enum semantics are no longer protocol constraints — they become ordinary doc-truth updates (Phase 3). See memory `project-experiment-killed-2026-06-18`.
- **Cockpit is a separate PR.** Per memory `feedback_cockpit_scope_boundary`, cockpit changes touch `deploy/energy-stack/cockpit/` only and ship as their own PR (Phase 2).
- Tests: run per-service — `cd deploy/energy-stack/hvac_scheduler && python -m pytest .` (never from repo root, per [AGENTS.md]).

---

## DECISION TO CONFIRM (before Phase 1)

The one unspecified parameter: **the exact MILD setpoints.** Chris's framing was "mimic what the thermostat would be doing." The proposal below mirrors the CTK04 program the house *already* experiences on mild days (so thermal behavior is unchanged — the only difference is the Pi owns it, enabling the overlay):

| Time (CT) | Label | Cool °F | Fan | Mirrors CTK04 |
|---|---|---|---|---|
| 06:00 | `MILD_MORNING` | 73 | Auto | Wake 73 |
| 13:00 | `MILD_DAY` | 78 | Circulate | Leave 78 |
| 19:00 | `MILD_RECOVER` | 75 | Auto | Return 75 |
| 21:00 | `SLEEP` | 73 | Auto | Sleep 73 |

Heat is paired at 65°F on every push (deadband safety, unchanged from other schedules). No pre-cool action (correct — MILD = forecast <75°F). **Confirm these values/times or adjust before Phase 1 Task 1.** Everything downstream keys off this table.

---

## File structure / blast radius

**Phase 1 — scheduler (one PR):**
- Modify: `deploy/energy-stack/hvac_scheduler/app.py:222-229` — replace `MILD_SCHEDULE`.
- Test: `deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py` — add outside-in acceptance test; rewrite the 4 MILD-assumes-release_hold tests + retarget the 6 release_hold-mechanism tests.
- No change needed to: `resolve_layer_priority`, `_push_layer_change_mid_period`, `_evaluate_layer_inputs`, `reconstruct_startup_baseline`, `execute_action`, `price_overlay.py`, `safety_supervisor.py` — they already handle a real baseline. `release_hold` the primitive stays (now unused by any schedule; retiring it fully is optional follow-up, not in scope).

**Phase 2 — cockpit (separate PR):**
- Modify: `deploy/energy-stack/cockpit/backend/schedules.py:55-57` (MILD mirror), `backend/snapshot.py:273-412` (the "stale holding" render path now means only controller-down, not normal-MILD).
- Modify: `cockpit/frontend/src/fixtures/mild_day.ts`, `frontend/src/narrative/components/ActionLog.tsx`, `DayAtAGlance.tsx` (null-setpoint MILD handling).
- Test: `cockpit/backend/tests/test_live_assembler.py`, `test_snapshot.py`.

**Phase 3 — docs + memory (separate PR):**
- Modify: `docs/HVAC_LOGIC.md` (MILD table + fallback section), `docs/SCHEDULER_TIMING.md`, `docs/CONTROLLER_CONSTANTS.md`, `docs/DRY_RUN_VALIDATION.md`, `docs/THERMOSTAT_ARM_A_SCHEDULE.md`, `docs/SERVICES.md`, `README.md`, `deploy/energy-stack/README.md`.
- Update memory: `project_mild_day_trace_pattern` (no longer "5 traces then silence"), `project-experiment-killed-2026-06-18` (mark fix shipped).

---

## Phase 1 — Scheduler fix + acceptance test

**This phase is the whole money-fix as one vertical slice** (data → layer logic → thermostat push). The acceptance test is the north star and stays `xfail(strict=True)` until the schedule change makes it pass with zero scaffolding.

### Task 1: Outside-in acceptance test (north star)

**Files:**
- Test: `deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py`

**Interfaces:**
- Consumes: `_drive_run_schedule_check(...)` (existing harness, line 1734) — EXTENDED with a `price_cents` passthrough in Step 1a; `_stub_layer_eval_io(monkeypatch, *, price_cents=5.0, ...)` (line 1334 — already accepts `price_cents`; it stubs `fetch_latest_comed`); `FiringState`; `app.execute_action` (mocked by the harness as `AsyncMock`).
- Produces: `_drive_run_schedule_check`'s new `price_cents` param.

**Why this shape (review-hardened — dual review 2026-06-18):** drive scarcity through the REAL price path — `_stub_layer_eval_io`'s `price_cents`, which is what `_evaluate_layer_inputs` actually reads. Do **not** add a separate `monkeypatch.setattr(app, "fetch_latest_comed", ...)`: `_drive_run_schedule_check` calls `_stub_layer_eval_io` internally (line 1749) and would clobber it (both reviewers caught this). Fire at a **non-action minute (14:00)** so the test exercises `_push_layer_change_mid_period` — the actual dormancy gate (`app.py:2971`, `last_schedule_cool_f is None`) — not the action-fire path. Start from a fresh `FiringState` (tier `normal`) so the 46.4¢ price *itself* must upgrade the overlay to scarcity (a scarcity upgrade is immediate — no min-hold), proving price → actuation end-to-end rather than proving a pre-seeded state survives a tick.

- [ ] **Step 1a: Add a `price_cents` passthrough to the harness**

`_drive_run_schedule_check` calls `_stub_layer_eval_io` without a `price_cents`, so it defaults to 5¢. Thread it through:

```python
def _drive_run_schedule_check(
    monkeypatch, *, now_local, firing,
    execute_result=(True, None), day_type="NORMAL", dry_run=False,
    price_cents=5.0,                                   # NEW
):
    ...
    _stub_layer_eval_io(monkeypatch, price_cents=price_cents,   # forward it
                        zone_load=14000.0, derivative=0.0,
                        forecast_peak=17000.0, season_5th=20375.0)
    ...
```

- [ ] **Step 1b: Write the failing acceptance test, marked xfail-strict**

```python
@pytest.mark.xfail(strict=True, reason="MILD is release-hold only; the mid-period re-push short-circuits on last_schedule_cool_f is None, so the overlay can't actuate on MILD until MILD has a real schedule (mild-full-controller Phase 1)")
def test_mild_day_scarcity_spike_pushes_85(monkeypatch):
    """NORTH STAR: a >=20c ComEd 5-min print on a MILD day must drive the
    thermostat to the 85F scarcity setpoint via the MID-PERIOD re-push (the
    real dormancy gate). Fresh state, no pre-seeded tier -- the 46.4c price
    itself upgrades the overlay to scarcity (immediate, no min-hold) and the
    mid-period push must fire. 14:00 is a NON-action minute, so this exercises
    _push_layer_change_mid_period, not the action-fire path. Today MILD's only
    action is the 00:05 release_hold, so reconstruct_startup_baseline sets
    last_schedule_cool_f=None and the mid-period push short-circuits -> no 85
    -> strict-xfail. After MILD gets a real schedule, 13:00 MILD_DAY is in
    effect (baseline 78), the push fires, warmer-wins gives 85."""
    firing = FiringState()  # price_overlay_state defaults to "normal"
    now_local = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("America/Chicago"))
    _drive_run_schedule_check(
        monkeypatch, now_local=now_local, firing=firing,
        price_cents=46.4, day_type="MILD",
        execute_result=(True, None), dry_run=False,
    )
    execute_mock = app.execute_action
    assert isinstance(execute_mock, AsyncMock)  # patched by the harness
    pushed_cools = [c.args[2] for c in execute_mock.await_args_list]
    assert 85 in pushed_cools, f"expected an 85F mid-period push, got {pushed_cools}"
```
(The `isinstance(..., AsyncMock)` narrow keeps mypy happy — `app.execute_action` is typed as the real Callable, not the mock.)

- [ ] **Step 2: Run it to confirm it xfails (not errors)**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_hvac_scheduler.py::test_mild_day_scarcity_spike_pushes_85 -v`
Expected: `XFAIL` (strict) — the tick runs to completion; today at 14:00 MILD's in-effect action is the 00:05 release_hold, so `reconstruct_startup_baseline` sets `last_schedule_cool_f=None`, the mid-period re-push short-circuits, no 85 is pushed, and the assertion fails under xfail. If it ERRORS (not xfails), the harness wiring is wrong — fix before proceeding.

- [ ] **Step 3: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py
git commit -m "test(hvac): add xfail north-star — MILD scarcity spike must push 85 mid-period"
```

### Task 2: Give MILD a real Pi-owned schedule

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/app.py:222-229`

**Interfaces:**
- Consumes: `ScheduleAction(hour, minute, label, cool_setpoint_f=..., fan_mode=...)` (existing dataclass, line 147).
- Produces: `MILD_SCHEDULE: list[ScheduleAction]` consumed by `schedule_for` (line 1195).

- [ ] **Step 1: Replace `MILD_SCHEDULE`** (values per the confirmed DECISION table)

```python
# MILD day: forecast high < 75F. No pre-cool needed (it's cool out), but the
# Pi still OWNS the day so the price overlay can actuate (the whole point of
# the mild-full-controller fix). Setpoints mirror the CTK04 comfort program
# the house already ran on mild days, now Pi-pushed with Permanent holds.
MILD_SCHEDULE: list[ScheduleAction] = [
    ScheduleAction(6,  0, "MILD_MORNING", cool_setpoint_f=73, fan_mode="Auto"),
    ScheduleAction(13, 0, "MILD_DAY",     cool_setpoint_f=78, fan_mode="Circulate"),
    ScheduleAction(19, 0, "MILD_RECOVER", cool_setpoint_f=75, fan_mode="Auto"),
    ScheduleAction(21, 0, "SLEEP",        cool_setpoint_f=73, fan_mode="Auto"),
]
```

- [ ] **Step 2: Remove the xfail marker from Task 1's test and run it**

Delete the `@pytest.mark.xfail(...)` line from `test_mild_day_scarcity_spike_pushes_85`.
Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_hvac_scheduler.py::test_mild_day_scarcity_spike_pushes_85 -v`
Expected: PASS — at 14:00 `reconstruct_startup_baseline` finds `MILD_DAY` (13:00) in effect so `last_schedule_cool_f=78`; the 46.4¢ price upgrades the overlay to scarcity; the mid-period re-push runs `resolve_layer_priority(78, override=85)=85` and `execute_action` is called with 85.

- [ ] **Step 3: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/app.py deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac): MILD becomes a Pi-owned schedule so the price overlay actuates"
```

### Task 3: Reconcile the MILD/release_hold tests

The schedule change breaks tests that asserted MILD = a single release_hold and that MILD reconstructs a None baseline. Update them to the new reality. The `release_hold` execute_action primitive still exists, so the 6 mechanism tests (467, 534, 579, 596, 609, 1164) keep passing **as long as** they construct their own `ScheduleAction(..., release_hold=True)` rather than asserting `MILD_SCHEDULE` contains one — verify and adjust only the ones that reference `MILD_SCHEDULE`.

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py`

- [ ] **Step 1: Fix the 4 hard-break tests**

- `test_mild_schedule_releases_hold_at_start_of_day` (~L476): now asserts the new shape. Replace its body's `assert len(MILD_SCHEDULE) == 1` and release_hold assertion with:
```python
    assert [a.label for a in MILD_SCHEDULE] == [
        "MILD_MORNING", "MILD_DAY", "MILD_RECOVER", "SLEEP"]
    assert all(a.cool_setpoint_f is not None for a in MILD_SCHEDULE)
    assert all(a.release_hold is False for a in MILD_SCHEDULE)
```
Rename it to `test_mild_schedule_is_pi_owned`.
- `test_action_in_effect_returns_release_hold_when_it_is_latest` (~L3940): MILD no longer has a release_hold; change the fixture to construct an explicit single-`release_hold` schedule (not `MILD_SCHEDULE`) so it still exercises `action_in_effect_at`'s release_hold path. Update the docstring's "MILD: only MILD_RELEASE_HOLD" comment.
- `test_reconstruct_startup_baseline_overnight_yesterday_mild` (~L4074) and `test_reconstruct_startup_baseline_mild_today_after_release` (~L4089): MILD now reconstructs a **real** baseline, not None. Update the expected `last_schedule_cool_f` to the in-effect MILD setpoint. NOTE the exact value per the test's wall-clock: the overnight-before-06:00 test carries yesterday's `SLEEP` 73; the **10:00 restart** test (`mild_today_after_release`) carries `MILD_MORNING` **73** — the 06:00 action is in effect, the 13:00 `MILD_DAY` (78) is still future (do NOT assert 78 there). Rename away from "after_release".

- [ ] **Step 1b: Confirm the dry-run audit parametrization (blast-radius site)**

`_all_schedule_actions()` (~L2954) iterates `app.MILD_SCHEDULE`; after the swap MILD contributes 4 setpoint actions instead of 1 release_hold, so the parametrized `test_dry_run_never_calls_control4_for_any_action` (~L2987) gets more cases. It should still pass (it handles `cool_setpoint_f is not None` generically), but run it explicitly to confirm: `python -m pytest test_hvac_scheduler.py -k dry_run_never_calls -v`. If it regresses, the generic `cool_setpoint_f` handling at ~L2967 needs a look.

- [ ] **Step 2: Update stale-rationale docstrings**

In `test_classify_high_70_is_mild` (~L227) and `test_classify_partial_forecast_no_temps_falls_back_to_normal` (~L247), drop any "no active scheduling on MILD" claim from the docstrings (classification is unchanged; only execution changed).

- [ ] **Step 3: Run the full scheduler suite**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest . -q`
Expected: all pass (including the de-xfailed north star).

- [ ] **Step 4: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py
git commit -m "test(hvac): reconcile MILD tests with Pi-owned schedule"
```

### Task 4: Full-stack regression + PR

- [ ] **Step 1: Run the canonical full-stack tests**

Run: `bash deploy/energy-stack/run_tests.sh`
Expected: green (each service in its own pytest process).

- [ ] **Step 2: Push and open the PR (stop here per branching policy)**

```bash
git push -u origin plan/mild-full-controller
gh pr create --base main --title "feat(hvac): Pi owns MILD days so the price overlay actuates" --body "Fixes the MILD price-overlay dormancy (memory project-experiment-killed-2026-06-18). MILD becomes a real Pi-owned schedule; existing layer machinery actuates scarcity/elevated on MILD. Holds stay Permanent; Pi-down fallback unchanged (external monitor + manual). Cockpit + docs follow as separate PRs."
```
Surface the PR URL. Do NOT merge — Chris reviews + merges.

---

## Phase 2 — Cockpit (separate PR, after Phase 1 merges)

Branch fresh from `main`. Scope: `deploy/energy-stack/cockpit/` only.

- [ ] **Step 1:** `backend/schedules.py:55-57` — replace the MILD mirror with the 4 Pi-owned actions (match `app.py` MILD_SCHEDULE exactly).
- [ ] **Step 2:** `backend/snapshot.py:273-412` — the "no live layer_resolution this tick → mark stale held value" branch now represents **only** a down controller, not a normal MILD day. Confirm a live MILD day produces a real `winning` node (it now emits layer_resolution traces every tick); verify the controller-down render still fires only on heartbeat-false.
- [ ] **Step 3:** `frontend/src/fixtures/mild_day.ts` — rebuild so MILD shows a Pi-owned stepped schedule (not "thermostat baseline / no active push"); `ActionLog.tsx` + `DayAtAGlance.tsx` — MILD actions now carry setpoints, so they render like NORMAL.
- [ ] **Step 4:** Update `backend/tests/test_live_assembler.py` + `test_snapshot.py` fixtures; manual Chrome smoke per `feedback_cockpit_no_test_investment` (no deep test investment).
- [ ] **Step 5:** Push + PR `--base main`.

## Phase 3 — Docs + memory (separate PR)

- [ ] **Step 1:** `docs/HVAC_LOGIC.md` — replace the MILD release-hold table (~L102, L113-117) with the new MILD setpoint table; update the "Thermostat fallback (when Pi is offline)" section (~L261-284) to state MILD is now Pi-owned and the CTK04 program is the Pi-**down** fallback (external monitor + manual release), not a normal-operation mild-day path.
- [ ] **Step 2:** `docs/SCHEDULER_TIMING.md` (Gantt MILD milestone L50, the "legitimate None: released hold or MILD" prose L160/L171), `docs/CONTROLLER_CONSTANTS.md` (L32 "MILD No active scheduling"), `docs/DRY_RUN_VALIDATION.md` (L76-78), `docs/THERMOSTAT_ARM_A_SCHEDULE.md` (L55/L57), `docs/SERVICES.md` (L348), `README.md` (L39-41), `deploy/energy-stack/README.md` (L13).
- [ ] **Step 3:** Update local memory: rewrite `project_mild_day_trace_pattern` (MILD now pushes setpoints + emits full traces; it is no longer "5 traces then silence"); add a "fix shipped" note to `project-experiment-killed-2026-06-18`.
- [ ] **Step 4:** Push + PR `--base main`.

---

## Self-review

**Spec coverage:** the resolved design points each map to a task — day-type=pre-cool / shared baseline (Task 2 schedule + Global Constraints), overlay actuates on MILD (Task 1+2, the north star), Permanent holds unchanged (no task needed — explicitly out of scope), 5CP untouched (out of scope), external-monitor fallback (no code — Global Constraints + Phase 3 docs), cockpit (Phase 2), docs/memory (Phase 3).

**Placeholder scan:** none — `MILD_SCHEDULE` code is complete; the acceptance test is concrete against the real harness. The one deliberately-open item is the MILD setpoint table, gated behind the explicit DECISION TO CONFIRM callout (Chris signs off before Task 1).

**Type consistency:** `MILD_SCHEDULE` is `list[ScheduleAction]` matching `schedule_for`'s return and the other schedules. The acceptance test drives the price through `_stub_layer_eval_io(price_cents=46.4)` via the new `_drive_run_schedule_check(price_cents=...)` passthrough — it does NOT stub `fetch_latest_comed` directly (the harness's internal `_stub_layer_eval_io` call would clobber that). `_stub_layer_eval_io` builds the sample with `_fresh_sample(price_cents, now_utc=...)`, so the `PriceSample` shape is the helper's concern, not the test's.

**Dual-reviewed 2026-06-18:** Codex + Claude both confirmed core correctness (the swap actuates the overlay) and the safety caps; their findings (test price-stub clobber + action-fire-vs-mid-period, the §7 pre-cool caveat, the Pi-down supervisor wording, the `_all_schedule_actions` blast-radius site, the 10:00=`MILD_MORNING` test value, and the 5-min cadence figure) are folded in above.

**Risk note (carry into execution):** the only non-obvious coupling is that Task 2 is a one-line schedule swap that *silently* turns the price overlay on for all of MILD (via the `last_schedule_cool_f is None` short-circuit ceasing to fire). That is intended, but the acceptance test is what makes it visible. Analysis pipeline mis-classification was checked and is NOT a risk: `hvac.arm_mode` is written on a ~5-min cadence (288 rows/day, `_ARM_MODE_AUDIT_INTERVAL`, `app.py:2532`) independent of whether a setpoint action fires, so live MILD hours keep emitting `mode_actual` and `arm_period_pipeline.py`'s hourly-majority aggregation won't mis-stamp them B-down.
