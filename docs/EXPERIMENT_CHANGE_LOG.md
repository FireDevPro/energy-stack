# Experiment change log

Apparatus change register for the preregistered HVAC control experiment
(OSF: osf.io/w52kq). One entry per finding that touches the deployed
control system during the experiment window. The git history and the
linked PRs carry the technical detail; this log is the experiment-facing
record of *what changed in the apparatus, when, and why* — so a reviewer
can see that mid-experiment changes were legitimate defect repairs that
restore the preregistered behavior, not behavioral changes that would
compromise comparability.

Each entry is classified:

- **A — Defect repair (applied):** the apparatus was not behaving as the
  preregistration specified; the fix *restores* intended behavior.
  Legitimate to apply mid-experiment.
- **B — Deferred change (post-experiment):** the apparatus is functioning
  but as-built differs from as-intended, or the change is an improvement.
  Changing it mid-run would alter the apparatus and break comparability,
  so it is logged and deferred to after the experiment window.
- **C — Known limitation (disclosed, no change):** a by-design constraint
  recorded for transparency in the writeup.

Entries are append-only and dated in America/Chicago.

---

## 2026-06-01 — Empty-range crash on the first day of cooling season

**Category:** A (defect repair, applied)

**Symptom.** The 2026-05-31 21:00 CT nightly decision (classifying
2026-06-01, the first day of Arm A control) failed every minute for the
full decision hour — 60 consecutive `HTTP 400` errors from InfluxDB,
`"cannot query an empty range"`. `last_decision_date` was never written.
The same fault recurs all day 2026-06-01 in the two intraday revisits
(06:00, 11:00 CT) and in every schedule-check tick (via the lazy
`fetch_today_decision` recompute), because all three call
`_fetch_pjm_inputs_for_target_date("2026-06-01")`. The schedule-check
crash is upstream of the `hvac.arm_mode` write, so arm-mode telemetry
goes silent and the watchdog flags `controller_alive=false`.

**Root cause.** Not mode-related (the decision/query path is identical in
shadow/experiment/production). It is cooling-season onset. On the first
calendar day of the season the season-to-date window collapses to zero
width: `_fetch_pjm_inputs_for_target_date` caps the window end at the
target date's midnight (`min(season_end_utc, target_utc)`), and on June 1
that equals `season_start_utc` (both = 2026-06-01T05:00:00Z in CDT). The
resulting `update_season_5th_highest` Flux `range(start: X, stop: X)` is
rejected by InfluxDB. Before June 1 the `in_cooling_season` guard
short-circuited this path, so it had never executed in-season before.

**Fix implemented.** Guard in `update_season_5th_highest`: return `None`
when `season_end_utc <= season_start_utc`, before issuing any Flux.
Behavior-preserving — a zero-width window holds zero distinct hours, far
under the 168-hour baseline floor, so `None` is the same value the query
would return if it could run; `decide_day_type` already treats `None` as
`insufficient_current_season_history`. Protects all three callers and the
live 5CP detector, and prevents the annual June 1 recurrence.

**Impact.** 2026-06-01 is in the 48-hour washout, so the lost decision
records are not counted in the analysis. The bug self-heals at
2026-06-02 00:00 CT (target June 2 → full 24 h window). The fix removes
the recurrence and the watchdog false alarm.

**Links:** branch `fix/season-onset-empty-range`; tests
`test_update_season_5th_returns_none_on_zero_width_window_without_querying`,
`test_update_season_5th_returns_none_on_negative_width_window_without_querying`.

---

## 2026-06-01 — Restart leaves the controller able to sense but unable to act

**Category:** A (defect repair, to apply before 2026-06-15)

**Symptom.** Observed in the 2026-05-31 decision-trace report: after the
shadow→experiment restart at 14:23 CT, `layer_resolution` and
`supervisor` traces were absent for 4.5 hours (until the next scheduled
action at 19:00 CT).

**Root cause.** On any restart, in-memory `FiringState.last_schedule_cool_f`
resets to `None` and is only re-established when a scheduled action fires
within its 5-minute make-up window. While it is `None`,
`_push_layer_change_mid_period` short-circuits — which suppresses not just
tracing but the §2 price-overlay re-push, the §3 5CP shutoff, and the
P1.2 safety-supervisor indoor-overheat override, until the next schedule
boundary. Harmless under Arm A (writes gated), but a real control and
safety gap under Arm B after any restart (redeploy, crash, power event)
that lands between boundaries — up to ~7 h on a HOT-day schedule.

**Fix implemented.** Tier 1 startup baseline reconstruction — on a tick
where the baseline is `None`, derive it deterministically from today's
day-type schedule and the current time (no re-firing of past actions),
re-arming mid-period reactivity and the supervisor immediately. Fuller
state-machine reconciliation (price-overlay / 5CP hold timers from
telemetry) was considered and **declined**: it duplicates a documented
"cold-start re-converges from current" design and adds reconstruction
risk for modest benefit. Design plan and PR: pending (target before
Arm B opens, 2026-06-15).

**Links:** plan and PR pending.

---

## 2026-06-01 — Season-to-date 5CP baseline unavailable for the first ~7 days

**Category:** C (known limitation, disclosed)

**Description.** `update_season_5th_highest` requires 168 distinct hourly
`pjm.metered_load` observations before returning a value (binding spec
§11 #14; no prior-year fallback for control/planning). So
`season_5th_mw` is `None` from 2026-06-01 until ~2026-06-08. During that
window the single-day forecast-5CP-risk pre-cool escalation
(HOT → HOT_STREAK_DAY1 via `should_deepen_precool`) cannot fire; only the
multi-day-heat-streak escalation path is available. By design.

**Action.** None. Disclosed here and to be noted in the experiment writeup.
