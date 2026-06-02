---
date: 2026-06-02
owner: chris
status: draft
role-label: code-team
---

# Restart baseline reconstruction — design

> Revised after a P1/P2 design review (four findings, all valid): 5CP wording
> corrected, `baseline_initialized` marker added, yesterday-resolution routed
> through the override order read-only, and the hook moved ahead of the layer
> evaluation. See the per-section notes.

## Problem

On any restart (redeploy, crash, power event) that lands between schedule
boundaries, `FiringState.last_schedule_cool_f` starts at `None` and is only
re-established when the next scheduled action fires within its 5-minute
make-up window. While it is `None`, `_push_layer_change_mid_period`
short-circuits at the top (`app.py:2897`) — *before* the thermostat read and
supervisor invocation at 2922 — so a single guard silences, until the next
action fires (up to ~7h on a HOT-day schedule):

- **§2 price-overlay mid-period re-push** (control)
- **P1.2 safety-supervisor indoor-overheat override** (safety)
- **the per-tick layer-resolution / 5CP telemetry traces** (observability)

5CP is **not** a live control layer — it is telemetry-only per binding spec
§11 #14 (`LayerResolution` docstring `app.py:1217-1220`: "does not
independently force live setpoint changes"; `HVAC_LOGIC.md:351`). So no 5CP
*control* is lost during the gap; what is lost is the 5CP telemetry trace.
(`EXPERIMENT_CHANGE_LOG.md` entry #2 currently uses the stale "§3 5CP shutoff"
wording — correct it when updating that entry.)

Gated (no writes) under Arm A; a real control + safety gap under Arm B after
any restart that lands between boundaries. Observed in the 2026-05-31
decision-trace report (4.5h of absent `layer_resolution` / `supervisor` traces
after the 14:23 CT shadow→experiment restart).

Logged as `docs/EXPERIMENT_CHANGE_LOG.md` entry #2 (Category A, apply before
2026-06-15).

## Why this is a reliability fix, not a logic change

Arm B's decision *settings and logic* (setpoints, schedules, thresholds,
arbitration, supervisor limits) are protected at all cost — they are the
conceptual mechanism under test. This change adds **zero** new decision
logic: it re-derives the *same* baseline the un-restarted controller would
already hold, using the existing locked code (`schedule_for`,
`resolve_cool_setpoint`, the override resolution). It protects the settings by
*reproducing* them, and protects the outcome data by closing the restart gap —
downtime and data holes nullify the experiment, whose value is the outcomes,
which require the software to actually run.

The price-overlay and 5CP state are intentionally NOT reconstructed: they
self-converge after a restart (price overlay re-evaluates within its 30-min
minimum-hold; 5CP is telemetry that re-derives from live load). The schedule
baseline is the one piece of state that does *not* self-heal — it stays `None`
until the next action — which is why it alone needs reconstruction.

## Scope: full 24/7 parity

Reconstruction restores the correct baseline after a restart at *any* hour, so
the controller self-heals. Rationale: this is a long-lived home-control
system, not a single-summer artifact; over years of operation overnight power
events will happen, and a silently-unarmed safety supervisor is an
unacceptable latent gap.

Two facts make full parity faithful and simple:

- SLEEP cool = **73°F**, identical across NORMAL / HOT / HOT_STREAK_DAY1.
  Reconstruction never invents a setpoint.
- MILD = a single 00:05 `release_hold` (no cooling baseline). A MILD day's
  baseline is correctly `None`.

## Design

### Placement and the one-shot marker (P2-B, P1-A)

Hook the reconstruction **immediately after the schedule is resolved
(~`app.py:3062`), before `_evaluate_layer_inputs` (3066)** — not on the later
`not fired_anything` path. Two reasons:

- The layer-eval audit write `write_price_overlay_transition` stamps
  `schedule_cool_f = firing.last_schedule_cool_f or 0` (`app.py:2813`). Placing
  reconstruction *before* it means the first post-restart tick logs the real
  baseline, not `0`.
- It arms the supervisor one tick earlier.

Guard with a process-life one-shot marker, new field
`FiringState.baseline_initialized: bool = False`:

```
if not firing.baseline_initialized and firing.last_schedule_cool_f is None:
    reconstruct_baseline(...)
firing.baseline_initialized = True
```

Why a marker is required: reconstruction legitimately yields `None` (MILD,
released hold, missing-yesterday default), so a bare `is None` guard would
re-run every tick — re-reading yesterday's decision from InfluxDB every minute
overnight. `baseline_initialized` makes reconstruction a **startup one-shot**:
on tick 1 the baseline is always `None` (fresh state) so it runs once and the
flag flips; thereafter the normal action-fire / release-hold flow owns the
baseline, including its legitimate `None`s, which must *not* be reconstructed.

### Deriving the baseline (reuses locked logic only)

1. **Most recent action in today's resolved `schedule` with time ≤ now:**
   - non-`release_hold` → `last_schedule_cool_f =
     resolve_cool_setpoint(action, today_dewpoint_f)`; set `last_action_label`.
     (Covers every daytime restart — a 14:00 restart finds COAST@13:00 and
     re-arms in one tick.)
   - `release_hold` → leave `None` (hold released; correct for MILD-today and
     any released hold).
2. **No action ≤ now today (overnight, before the first pre-cool):** source
   the carried-over baseline from *yesterday's* last action, resolving
   yesterday through the **same override order** the live path uses, but
   **read-only** (P1-B, option 2):
   - `find_active_override(load_overrides(...), yesterday_iso)` → vacation
     (`vacation_schedule`) / day_type override (`schedule_for(override type)`)
     / else `_read_stored_decision(query_api, bucket, yesterday_iso)` →
     `schedule_for(stored)`.
   - Take that schedule's last action → cooling day = SLEEP 73; MILD /
     released = `None`.

   **Do not call `fetch_today_decision` for yesterday.** It is today-coupled
   (hard-codes `"today"`/`"tomorrow"` forecasts, `app.py:2375/2383`) and
   write-capable (persists a recomputed decision on a miss, 2353-2354) — aimed
   at yesterday it would write a *fabricated* decision into the
   `hvac.decisions` history the experiment analyzes. Yesterday's read must be
   the read-only primitive `_read_stored_decision`.

   This is a **localized mirror** of the override order, not a shared helper:
   the live resolution path (`app.py:3030-3041`) is left untouched (zero blast
   radius on the locked Arm B path). Order parity with the live path is pinned
   by a test (below), not by shared code.

The reconstruction does not touch `last_decision_date`, `last_observed_arm`,
the price-overlay state, or the 5CP state.

### Decision 1 — yesterday's decision missing (rare)

`_read_stored_decision(yesterday)` returns `None` and no override is active →
default baseline to `None`. Asserting 73 on unknown history would be a control
decision the un-restarted controller might not have made; protecting Arm B's
logic means not inventing one. The residual — supervisor unarmed overnight
only when yesterday's decision is missing AND the restart is overnight (a
double-rare case) — is disclosed in the change log as a Category C limitation.

### Decision 2 — first reconstruction tick: re-assert

Leave `last_pushed_effective_cool_f = None` so the first mid-period tick runs
the supervisor *and* pushes the intended effective setpoint once. This re-arms
safety and guarantees the thermostat matches the controller's intended state
after a restart. The pushed value is the locked-logic effective, not invented.
One push per restart, not per tick. (Under Arm A the push is dry-run/gated.)

## Out of scope / explicitly not doing

- No reconstruction of price-overlay or 5CP state (they self-heal).
- No change to any setpoint, schedule, threshold, or arbitration rule.
- No refactor of the live override-resolution path (3030-3041); reconstruction
  mirrors the order locally, read-only.
- No prior-day lookback beyond a single day (the only cross-boundary case is
  the SLEEP carry-over).

## Comment drift to correct (same change)

`FiringState.last_schedule_cool_f`'s docstring says it resets "on day
boundaries." There is no midnight reset in the code — the baseline persists
across midnight in normal operation (which is exactly why a restart is the
only source of a mid-stream `None`). Correct the comment to match.

## Testing

Unit tests (reconstruction helper, kept pure where possible):

- Daytime restart mid-COAST → baseline = COAST setpoint, label set.
- Overnight restart, yesterday a cooling day → baseline 73.
- Overnight restart, yesterday MILD → baseline `None`.
- Restart during a MILD day (today MILD, after the 00:05 release) → `None`.
- Most-recent action is a `release_hold` → `None`.
- Yesterday's decision missing → `None` (Decision 1).
- **Override-order parity:** reconstruction resolves a vacation date, a
  day_type-override date, and a plain-decision date the same way the live order
  does.
- **Read-only:** the yesterday path issues no write to `hvac.decisions`.
- **Idempotence:** with `baseline_initialized` set, a tick whose baseline is
  legitimately `None` (MILD / released) does not re-reconstruct.

Behavior (the actual bug-repro signal):

- After reconstruction, `_push_layer_change_mid_period` reaches the thermostat
  read + supervisor on the first post-restart tick instead of short-circuiting
  at the `None` guard.
- A price-overlay tier transition on the first post-restart tick logs the
  reconstructed `schedule_cool_f`, not `0`.

## Documentation impact

- Update `docs/EXPERIMENT_CHANGE_LOG.md` entry #2: status pending →
  implemented, add PR link; **correct the stale "§3 5CP shutoff" wording**
  (5CP is telemetry-only); add a Category C note for the
  missing-yesterday-decision overnight residual.
- This design doc archives to `docs/plans/archive/` on merge.
