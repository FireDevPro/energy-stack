# Restart Baseline Reconstruction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a restart that lands between schedule boundaries, re-derive `FiringState.last_schedule_cool_f` once at startup so the §2 price-overlay re-push and the P1.2 overheat supervisor stop being silenced by the `None`-baseline guard.

**Architecture:** A pure lookup (`action_in_effect_at`) plus a read-only yesterday-resolver (`resolve_schedule_for_date_readonly`) feed a one-shot orchestrator (`reconstruct_startup_baseline`) wired into `run_schedule_check` after schedule resolution and before `_evaluate_layer_inputs`. Guarded by a new `baseline_initialized` flag so it runs exactly once per process. Reuses locked schedule/setpoint logic; the live override-resolution path is untouched.

**Tech Stack:** Python 3, pytest. Single service: `deploy/energy-stack/hvac_scheduler/`.

**Spec:** `docs/plans/restart-baseline-reconstruction-design.md`

**Test commands (run from the service dir):**
```
cd deploy/energy-stack/hvac_scheduler
python -m pytest test_hvac_scheduler.py::<name> -v
```
Full service suite before PR: `python -m pytest .` from the service dir (NOT from repo root — see `pytest.ini`).

---

## File structure

- **Modify** `deploy/energy-stack/hvac_scheduler/app.py`
  - `FiringState`: add `baseline_initialized: bool = False`; fix the `last_schedule_cool_f` "resets on day boundaries" comment.
  - New `action_in_effect_at(schedule, minutes_since_midnight)` — pure lookup.
  - New `resolve_schedule_for_date_readonly(date_iso, query_api, bucket, overrides)` — read-only override-order resolution.
  - New `reconstruct_startup_baseline(firing, today_schedule, now_local, today_dewpoint_f, query_api, bucket, overrides)` — orchestrator.
  - `run_schedule_check`: add the one-shot hook after the §7 precool merge (~line 3062), before `_evaluate_layer_inputs`.
- **Modify** `deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py` — unit + behavior tests.
- **Modify** `docs/EXPERIMENT_CHANGE_LOG.md` — entry #2 status + 5CP wording + Category C residual.

---

## Task 1: `baseline_initialized` field + comment fix

**Files:** Modify `app.py` (`FiringState`, ~lines 1415-1422)

- [ ] **Step 1: Add the field and fix the comment.** In `FiringState`, update the `last_schedule_cool_f` comment and add the flag:

```python
    # Mid-period re-push tracking (§4 / Critical #2). The most recently
    # fired non-release-hold action's schedule-baseline setpoint and the
    # last effective cool setpoint pushed to the thermostat. Reset to None
    # on release_hold actions. (It persists across midnight in normal
    # operation -- there is no day-boundary reset -- so a restart is the
    # only source of a mid-stream None; startup reconstruction repairs it.)
    last_schedule_cool_f: int | None = None
    last_action_label: str = ""
    last_pushed_effective_cool_f: int | None = None
    # One-shot guard for startup baseline reconstruction. False only on a
    # fresh process. Flipped True on the first run_schedule_check tick; after
    # that the normal action-fire / release-hold flow owns the baseline
    # (including its legitimate Nones, which must NOT be reconstructed).
    baseline_initialized: bool = False
```

- [ ] **Step 2: Verify import/parse.** Run: `python -m pytest test_hvac_scheduler.py -q` — Expected: existing suite still PASSES (no behavior change yet).

- [ ] **Step 3: Commit.**

```bash
git add deploy/energy-stack/hvac_scheduler/app.py
git commit -m "feat(hvac-scheduler): add baseline_initialized flag; fix stale FiringState comment"
```

---

## Task 2: `action_in_effect_at` pure lookup

**Files:** Modify `app.py` (new function near `schedule_for`, ~line 1197); Test `test_hvac_scheduler.py`

- [ ] **Step 1: Write the failing tests.**

```python
from app import action_in_effect_at, NORMAL_SCHEDULE, MILD_SCHEDULE

def test_action_in_effect_returns_latest_action_at_or_before_minute():
    # NORMAL: PRE_COOL 06:00, COAST 13:00, RECOVER 19:00, SLEEP 21:00
    assert action_in_effect_at(NORMAL_SCHEDULE, 14 * 60).label == "COAST"

def test_action_in_effect_none_before_first_action():
    assert action_in_effect_at(NORMAL_SCHEDULE, 3 * 60) is None  # 03:00, first is 06:00

def test_action_in_effect_returns_last_action_at_end_of_day():
    assert action_in_effect_at(NORMAL_SCHEDULE, 24 * 60 - 1).label == "SLEEP"

def test_action_in_effect_returns_release_hold_when_it_is_latest():
    # MILD: only MILD_RELEASE_HOLD at 00:05
    act = action_in_effect_at(MILD_SCHEDULE, 12 * 60)
    assert act.label == "MILD_RELEASE_HOLD" and act.release_hold is True
```

- [ ] **Step 2: Run, verify fail.** Run: `python -m pytest test_hvac_scheduler.py -k action_in_effect -v` — Expected: FAIL (`cannot import name 'action_in_effect_at'`).

- [ ] **Step 3: Implement.** Add to `app.py`:

```python
def action_in_effect_at(
    schedule: list[ScheduleAction], minutes_since_midnight: int
) -> ScheduleAction | None:
    """The schedule action in effect at the given minute-of-day: the latest
    action whose start (hour*60+minute) is <= minutes_since_midnight. None if
    no action starts at or before that minute. The caller derives the baseline
    (release_hold -> None; otherwise resolve_cool_setpoint)."""
    in_effect: ScheduleAction | None = None
    for a in schedule:
        start = a.hour * 60 + a.minute
        if start <= minutes_since_midnight and (
            in_effect is None or start > in_effect.hour * 60 + in_effect.minute
        ):
            in_effect = a
    return in_effect
```

- [ ] **Step 4: Run, verify pass.** Run: `python -m pytest test_hvac_scheduler.py -k action_in_effect -v` — Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add deploy/energy-stack/hvac_scheduler/app.py deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac-scheduler): add action_in_effect_at lookup"
```

---

## Task 3: `resolve_schedule_for_date_readonly` (read-only override-order mirror)

**Files:** Modify `app.py` (new function after `fetch_today_decision`, ~line 2400); Test `test_hvac_scheduler.py`

- [ ] **Step 1: Write the failing tests.** Uses a fake query_api returning a stored decision, and Override fixtures.

```python
from app import (
    resolve_schedule_for_date_readonly, schedule_for, vacation_schedule,
    Override, DAYTYPE_HOT, HOT_SCHEDULE,
)

class _FakeQ:  # _read_stored_decision reads via query_api; stub its result
    def __init__(self, day_type): self._dt = day_type
    # match whatever _read_stored_decision expects; see existing tests for the
    # canonical stub pattern in test_hvac_scheduler.py
    ...

def test_resolve_yesterday_uses_stored_decision_when_no_override():
    q = _FakeQ("HOT_5CP_RISK")
    assert resolve_schedule_for_date_readonly("2026-07-01", q, "b", overrides=[]) == HOT_SCHEDULE

def test_resolve_yesterday_vacation_override_wins():
    ov = Override(...)  # a vacation override covering 2026-07-01
    out = resolve_schedule_for_date_readonly("2026-07-01", _FakeQ("HOT_5CP_RISK"), "b", [ov])
    assert out == vacation_schedule(ov)

def test_resolve_yesterday_empty_when_no_decision_no_override():
    assert resolve_schedule_for_date_readonly("2026-07-01", _FakeQ(None), "b", []) == []
```

> NOTE to implementer: copy the canonical `_read_stored_decision` stub from the existing tests in `test_hvac_scheduler.py` rather than guessing the query_api shape.

- [ ] **Step 2: Run, verify fail.** Run: `python -m pytest test_hvac_scheduler.py -k resolve_yesterday -v` — Expected: FAIL (import error).

- [ ] **Step 3: Implement.** Mirrors the live order at `app.py:3030-3041` but read-only (no recompute/persist):

```python
def resolve_schedule_for_date_readonly(
    date_iso: str, query_api: Any, bucket: str, overrides: list[Override]
) -> list[ScheduleAction]:
    """Resolve the schedule that governed `date_iso`, mirroring the live
    override order (vacation -> day_type override -> stored decision) but
    READ-ONLY: uses _read_stored_decision, never fetch_today_decision (which is
    today-coupled and persists a recompute on a miss). Returns [] when no
    override is active and no decision was stored. Used to source the overnight
    carry-over baseline from yesterday's last action."""
    override = find_active_override(overrides, date_iso)
    if override and override.is_vacation():
        return vacation_schedule(override)
    if override and override.is_day_type_override():
        return schedule_for(override.day_type or DAYTYPE_NORMAL)
    stored = _read_stored_decision(query_api, bucket, date_iso)
    if stored is None:
        return []
    return schedule_for(stored)
```

- [ ] **Step 4: Run, verify pass.** Run: `python -m pytest test_hvac_scheduler.py -k resolve_yesterday -v` — Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add deploy/energy-stack/hvac_scheduler/app.py deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac-scheduler): read-only schedule resolution for a past date"
```

---

## Task 4: `reconstruct_startup_baseline` orchestrator

**Files:** Modify `app.py` (new function after Task 3's, ~line 2430); Test `test_hvac_scheduler.py`

- [ ] **Step 1: Write the failing tests.** `firing` is a `FiringState`; `now` is a CT-aware datetime.

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from app import reconstruct_startup_baseline, FiringState
CT = ZoneInfo("America/Chicago")

def test_daytime_restart_reconstructs_coast():
    f = FiringState()
    now = datetime(2026, 7, 1, 14, 0, tzinfo=CT)  # mid-COAST on a NORMAL day
    reconstruct_startup_baseline(f, NORMAL_SCHEDULE, now, None,
                                 _FakeQ("NORMAL"), "b", [])
    assert f.last_schedule_cool_f == 79 and f.last_action_label == "COAST"

def test_overnight_restart_yesterday_cooling_reconstructs_sleep_73():
    f = FiringState()
    now = datetime(2026, 7, 2, 3, 0, tzinfo=CT)  # 03:00, today NORMAL, no action yet
    reconstruct_startup_baseline(f, NORMAL_SCHEDULE, now, None,
                                 _FakeQ("NORMAL"), "b", [])  # yesterday cooling
    assert f.last_schedule_cool_f == 73

def test_overnight_restart_yesterday_mild_reconstructs_none():
    f = FiringState()
    now = datetime(2026, 7, 2, 3, 0, tzinfo=CT)
    reconstruct_startup_baseline(f, NORMAL_SCHEDULE, now, None,
                                 _FakeQ("MILD"), "b", [])  # yesterday MILD -> released
    assert f.last_schedule_cool_f is None

def test_restart_during_mild_day_reconstructs_none():
    f = FiringState()
    now = datetime(2026, 7, 1, 10, 0, tzinfo=CT)  # today MILD, after 00:05 release
    reconstruct_startup_baseline(f, MILD_SCHEDULE, now, None, _FakeQ("MILD"), "b", [])
    assert f.last_schedule_cool_f is None

def test_missing_yesterday_decision_reconstructs_none():
    f = FiringState()
    now = datetime(2026, 7, 2, 3, 0, tzinfo=CT)
    reconstruct_startup_baseline(f, NORMAL_SCHEDULE, now, None, _FakeQ(None), "b", [])
    assert f.last_schedule_cool_f is None

def test_reconstruct_leaves_last_pushed_none_for_reassert():
    f = FiringState()
    now = datetime(2026, 7, 1, 14, 0, tzinfo=CT)
    reconstruct_startup_baseline(f, NORMAL_SCHEDULE, now, None, _FakeQ("NORMAL"), "b", [])
    assert f.last_pushed_effective_cool_f is None
```

- [ ] **Step 2: Run, verify fail.** Run: `python -m pytest test_hvac_scheduler.py -k reconstruct -v` — Expected: FAIL (import error).

- [ ] **Step 3: Implement.**

```python
def reconstruct_startup_baseline(
    firing: FiringState,
    today_schedule: list[ScheduleAction],
    now_local: datetime,
    today_dewpoint_f: float | None,
    query_api: Any,
    bucket: str,
    overrides: list[Override],
) -> None:
    """One-shot startup repair of last_schedule_cool_f after a restart between
    schedule boundaries. Sets the baseline the un-restarted controller would
    hold, reusing locked schedule/setpoint logic. Leaves
    last_pushed_effective_cool_f = None so the first mid-period tick re-asserts
    (runs the supervisor and pushes the intended effective once)."""
    now_min = now_local.hour * 60 + now_local.minute
    act = action_in_effect_at(today_schedule, now_min)
    if act is None:
        # Overnight, before today's first action: carry over yesterday's last.
        yesterday_iso = (now_local.date() - timedelta(days=1)).isoformat()
        y_schedule = resolve_schedule_for_date_readonly(
            yesterday_iso, query_api, bucket, overrides
        )
        act = action_in_effect_at(y_schedule, 24 * 60 - 1) if y_schedule else None
    if act is None or act.release_hold:
        firing.last_schedule_cool_f = None
        if act is not None:
            firing.last_action_label = act.label
        return
    cool, _reason = resolve_cool_setpoint(act, today_dewpoint_f)
    firing.last_schedule_cool_f = cool
    firing.last_action_label = act.label
```

- [ ] **Step 4: Run, verify pass.** Run: `python -m pytest test_hvac_scheduler.py -k reconstruct -v` — Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add deploy/energy-stack/hvac_scheduler/app.py deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac-scheduler): startup baseline reconstruction orchestrator"
```

---

## Task 5: Wire the one-shot hook into `run_schedule_check`

**Files:** Modify `app.py` (`run_schedule_check`, after the §7 precool merge ~line 3062, before `_evaluate_layer_inputs`); Test `test_hvac_scheduler.py`

- [ ] **Step 1: Add the hook.** Immediately after the precool-merge block (just before `# ---- Per-tick layer evaluation`):

```python
    # ---- Startup baseline reconstruction (one-shot) ----
    # A restart between schedule boundaries leaves last_schedule_cool_f None,
    # which makes _push_layer_change_mid_period short-circuit (silencing the
    # §2 re-push and the P1.2 supervisor) until the next action fires. Repair
    # it once, before the layer eval below, so the first post-restart audit row
    # carries the real baseline and the supervisor arms this tick.
    if not firing.baseline_initialized and firing.last_schedule_cool_f is None:
        reconstruct_startup_baseline(
            firing, schedule, now_local, today_dewpoint_f,
            query_api, cfg.influx_bucket, overrides,
        )
    firing.baseline_initialized = True
```

- [ ] **Step 2: Write the idempotence + one-shot tests.** Use the existing `run_schedule_check` harness in `test_hvac_scheduler.py` (copy the canonical fixture/mocks from a nearby test). Assert:

```python
# After a first tick with no action firing and a daytime now, the baseline is
# reconstructed and the flag is set:
#   assert firing.baseline_initialized is True
#   assert firing.last_schedule_cool_f is not None   # daytime
# After a release_hold has set the baseline to None mid-day, a later tick does
# NOT re-reconstruct (flag already True):
#   firing.baseline_initialized = True
#   firing.last_schedule_cool_f = None
#   <run a tick>  -> assert firing.last_schedule_cool_f is None  (untouched)
```

> NOTE to implementer: if a full `run_schedule_check` harness is heavier than the value here, assert the hook's effect by calling `reconstruct_startup_baseline` directly (covered in Task 4) and add one focused test that the flag short-circuits a second call. The load-bearing logic is unit-covered; this task's goal is the wiring + the flag's one-shot behavior.

- [ ] **Step 3: Run.** Run: `python -m pytest test_hvac_scheduler.py -v` — Expected: PASS (new + existing).

- [ ] **Step 4: Run the full service suite.** Run: `python -m pytest .` (from the service dir) — Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add deploy/energy-stack/hvac_scheduler/app.py deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py
git commit -m "feat(hvac-scheduler): wire one-shot startup baseline reconstruction"
```

---

## Task 6: Change-log update

**Files:** Modify `docs/EXPERIMENT_CHANGE_LOG.md` (entry #2)

- [ ] **Step 1: Update entry #2.** In the "Restart leaves the controller able to sense but unable to act" entry:
  - Correct the stale impact wording: it suppresses the **§2 price-overlay re-push** and the **P1.2 safety-supervisor override** (and the per-tick layer-resolution / 5CP **telemetry** traces) — remove "§3 5CP shutoff" (5CP is telemetry-only per binding spec §11 #14).
  - Change `**Fix implemented.**` from "Design plan and PR: pending" to a summary of the shipped fix (one-shot startup reconstruction; full 24/7 parity via read-only yesterday resolution).
  - Update `**Links:**` to the branch `fix/restart-baseline-reconstruction` and the PR number once opened.
  - Append a short **Category C** note: "After an overnight restart in the rare case where yesterday's decision row is also missing, the overheat supervisor stays unarmed until today's first action; price/5CP layers are inert in that window so control is unaffected."

- [ ] **Step 2: Commit.**

```bash
git add docs/EXPERIMENT_CHANGE_LOG.md
git commit -m "docs(change-log): record restart baseline reconstruction; correct 5CP wording"
```

---

## Self-review

- **Spec coverage:** placement before layer-eval (P2-B) → Task 5; `baseline_initialized` one-shot (P1-A) → Tasks 1, 5; read-only yesterday via override order (P1-B) → Task 3; 5CP wording (P2-A) → Task 6; Decision 1 (missing → None) → Task 4 test; Decision 2 (re-assert, `last_pushed=None`) → Task 4 test; comment fix → Task 1; full parity overnight → Task 4 tests. All covered.
- **Placeholders:** the two `NOTE to implementer` lines point at copying existing test fixtures (the query_api stub shape is codebase-specific and lives in `test_hvac_scheduler.py`); they are not logic placeholders. No "TODO/TBD/add error handling".
- **Type consistency:** `action_in_effect_at` / `resolve_schedule_for_date_readonly` / `reconstruct_startup_baseline` signatures match between definition and call sites (Task 5 hook passes `schedule, now_local, today_dewpoint_f, query_api, cfg.influx_bucket, overrides`).

## Out of scope (do not do)

- No edit to the live override-resolution block (`app.py:3030-3041`).
- No reconstruction of price-overlay or 5CP state.
- No setpoint / schedule / threshold / arbitration changes.
