---
date: 2026-06-02
owner: chris
status: draft
role-label: code-team
---

# Restart baseline reconstruction — design

## Problem

On any restart (redeploy, crash, power event) that lands between schedule
boundaries, `FiringState.last_schedule_cool_f` starts at `None` and is only
re-established when the next scheduled action fires within its 5-minute
make-up window. While it is `None`, `_push_layer_change_mid_period`
short-circuits at the top (`app.py:2897`) — *before* the thermostat read and
supervisor invocation at 2922 — so a single guard silences three things at
once until the next action fires (up to ~7h on a HOT-day schedule):

- §2 price-overlay mid-period re-push
- §3 5CP shutoff
- P1.2 safety-supervisor indoor-overheat override

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
`resolve_cool_setpoint`, the override resolution already in scope). It
protects the settings by *reproducing* them, and protects the outcome data by
closing the restart gap — downtime and data holes nullify the experiment,
whose value is the outcomes, which require the software to actually run.

The price-overlay and 5CP hold state are intentionally NOT reconstructed: they
self-converge after a restart (price overlay re-evaluates within its 30-min
minimum-hold; 5CP defaults inactive and re-triggers from live load). The
schedule baseline is the one piece of state that does *not* self-heal — it
stays `None` until the next action — which is why it alone needs
reconstruction.

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

Hook: in `run_schedule_check`, on the `not fired_anything` path, immediately
before `_push_layer_change_mid_period`, only when `last_schedule_cool_f is
None`. Reconstruct the baseline:

1. **Most recent action in today's resolved `schedule` with time ≤ now:**
   - non-`release_hold` → `last_schedule_cool_f =
     resolve_cool_setpoint(action, today_dewpoint_f)`; set `last_action_label`.
     (Covers every daytime restart — the high-value case: a 14:00 restart
     finds COAST@13:00 and re-arms in one tick.)
   - `release_hold` → leave `None` (hold released; correct for MILD-today and
     any released hold).
2. **No action ≤ now today (overnight, before the first pre-cool):** source
   the carried-over baseline from *yesterday's* last action — read yesterday's
   day-type from `hvac.decisions` (the same source the controller used to run
   yesterday) → cooling day = SLEEP 73; MILD = `None`.

The reconstruction reuses existing functions only and computes "what baseline
would be in effect now" deterministically. It does not touch
`last_decision_date`, `last_observed_arm`, the price-overlay state, or the 5CP
state.

### Decision 1 — yesterday's decision row missing (rare)

Default to `None`. Asserting 73 on unknown history would be a control decision
the un-restarted controller might not have made; protecting Arm B's logic
means not inventing one. The residual — supervisor unarmed overnight only when
yesterday's decision is missing AND the restart is overnight (a double-rare
case) — is disclosed in the change log as a Category C limitation.

### Decision 2 — first reconstruction tick: re-assert

Leave `last_pushed_effective_cool_f = None` so the first mid-period tick runs
the supervisor *and* pushes the intended effective setpoint once. This re-arms
safety and guarantees the thermostat matches the controller's intended state
after a restart. The pushed value is the locked-logic effective, not invented.
One push per restart, not per tick. (Under Arm A the push is dry-run/gated.)

## Out of scope / explicitly not doing

- No reconstruction of price-overlay or 5CP hold timers (they self-heal).
- No change to any setpoint, schedule, threshold, or arbitration rule.
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
- Idempotence: once reconstructed, subsequent ticks do not re-reconstruct.

Behavior (the actual bug-repro signal):

- After reconstruction, `_push_layer_change_mid_period` reaches the thermostat
  read + supervisor on the first post-restart tick instead of short-circuiting
  at the `None` guard.

## Documentation impact

- Update `docs/EXPERIMENT_CHANGE_LOG.md` entry #2: status pending →
  implemented, add PR link; add a Category C note for the
  missing-yesterday-decision overnight residual.
- This design doc archives to `docs/plans/archive/` on merge.
