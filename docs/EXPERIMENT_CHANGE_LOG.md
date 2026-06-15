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

**Category:** A (defect repair, applied 2026-06-02)

**Symptom.** Observed in the 2026-05-31 decision-trace report: after the
shadow→experiment restart at 14:23 CT, `layer_resolution` and
`supervisor` traces were absent for 4.5 hours (until the next scheduled
action at 19:00 CT).

**Root cause.** On any restart, in-memory `FiringState.last_schedule_cool_f`
resets to `None` and is only re-established when a scheduled action fires
within its 5-minute make-up window. While it is `None`,
`_push_layer_change_mid_period` short-circuits — which suppresses the §2
price-overlay re-push (control) and the P1.2 safety-supervisor
indoor-overheat override (safety), plus the per-tick layer-resolution /
5CP telemetry traces (observability), until the next schedule boundary.
(5CP is telemetry-only per binding spec §11 #14 — not a live shutoff layer —
so no 5CP *control* is lost; the earlier "§3 5CP shutoff" phrasing here was
inaccurate.) Harmless under Arm A (writes gated), but a real control and
safety gap under Arm B after any restart (redeploy, crash, power event)
that lands between boundaries — up to ~7 h on a HOT-day schedule.

**Fix implemented.** One-shot startup baseline reconstruction (full 24/7
parity). On the first tick after a restart, if the baseline is `None`, derive
it from the locked schedule logic: today's most-recent action ≤ now, or — when
no action has fired yet today (overnight) — yesterday's last action resolved
**read-only** through the same override order (vacation → day_type override →
stored decision via `_read_stored_decision`, never the write-capable
`fetch_today_decision`). A `baseline_initialized` one-shot guard ensures it
runs only at startup; thereafter the normal action-fire / release-hold flow
owns the baseline (including legitimate `None`s). The first reconstruction tick
leaves `last_pushed_effective_cool_f = None` so the supervisor runs and the
intended setpoint is re-asserted once. Adds zero new decision logic and leaves
the live override-resolution path untouched. Fuller state-machine
reconciliation (price-overlay / 5CP hold timers) was considered and
**declined**: those self-converge after a restart; only the schedule baseline
does not, so only it is reconstructed.

**Residual (Category C, disclosed).** After an overnight restart in the rare
case where yesterday's decision row is *also* missing, the baseline cannot be
sourced and the overheat supervisor stays unarmed until today's first action.
The §2 price overlay and 5CP telemetry are inert in the overnight off-peak
window, so control is unaffected — only the safety supervisor is briefly
unarmed, and only in this double-rare case.

**Links:** design `docs/plans/restart-baseline-reconstruction-design.md`; plan
`docs/plans/restart-baseline-reconstruction-plan.md`; branch
`fix/restart-baseline-reconstruction`.

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

---

## 2026-06-03 — Extended Stage-2 cooling on mild evenings (CTK04 staging + 0.5 °C sensor quantization)

**Category:** C (known limitation, disclosed), with a Category-B deferred-improvement note.

**Symptom.** On the mild evening of 2026-06-02 (Arm A washout), the AC ran
continuous Stage 2 ~19:05–00:10 CT (~5 h) while the wall thermostat moved only
~2 °F — prompting a "why 5 h of Stage 2 to drop 2 °F" review.

**Root cause (not a defect).** The CTK04 is behaving as-built. Three benign,
interacting causes: (1) the Arm A program steps the cool setpoint down in
2–3 °F jumps through the evening (78→75→73); each step's instantaneous error
trips the cool staging differential (ISU 3030, ~2 °F) into Stage 2. (2) The
CTK04 senses/controls in 0.5 °C (~0.9 °F) buckets, so °F setpoints sit between
buckets — the call cannot register "satisfied" until the next-lower bucket,
adding ~0.9 °F overcool and holding Stage 2 against a residual it cannot see
closing. (3) The house actually cooled normally (~1 °F/h, ~5 °F over the run —
confirmed by the co-located Ecowitt ch2 and the Haven return-air sensor); the
"2 °F" was the quantized wall reading, not the real drop. **Not**
dehumidification (ComfortNet `dehumidify_demand`=0; indoor RH ~38 %, outdoor
dewpoint ~37 °F), **not** low capacity (serviced ~May 2026; all room sensors
cooled together), **not** sensor placement (ch2 beside the control sensor
cooled fine). Season-wide the system runs Stage 1 *more* than Stage 2 (68.6 h
vs 57.0 h, 2026-05-16→06-03) — a mild-evening interaction, not a chronic
Stage-2 lock.

**Impact on the experiment.** None to comparability. This is the Arm A
apparatus behaving as preregistered (CTK04 schedule running autonomously), and
the behavior is **common-mode to both arms** (Arm B inherits the same
equipment, sensor quantization, and staging on any thermostat-schedule
fallback). It does not bias the A-vs-B comparison. No mid-experiment change
applied.

**Deferred improvements (Category B — post-experiment, after 2026-11-16).**

- *Current system (CTK04):* enable Advanced installer mode (ISU 3010); widen
  the cool staging differential (ISU 3030) and/or lower Cool CPH (ISU 3140) to
  hold Stage 1 longer on mild loads; choose setpoints that land on 0.5 °C
  buckets (72.5 / 73.4 / 74.3 / 75.2 °F) to remove the ~0.9 °F overcool; soften
  the Arm A evening setpoint steps. (DEHUM is currently inert — no humidity
  setpoint configured — so it has no effect unless one is set.)
- *Planned hardware path:* replace CTK04 + ComfortNet with a Venstar ColorTouch
  T7900 + ComfortBridge board, wired conventional 2-stage (ODS=2AC, Y1/Y2) so
  the **thermostat owns staging** (deadband/timer/turnoff — true down-staging,
  real-error, °F-native, deterministic), with the Venstar **local API** driving
  setpoints (dropping Control4/TCC). Board-side CFS (1–5 / Target-Runtime
  default 30 min) was considered and rejected in favor of thermostat-side
  staging.

**Open items to verify before the hardware change.** ComfortBridge "cannot
down-stage within a call" (forum-sourced, not OEM-verified); ODS=2AC routing to
the ASXC160481BE; furnace heat modulation preserved in conventional wiring;
ComfortNet / CTK04 official end-of-life (unconfirmed — no dated OEM source
found).

**Links.** Diagnosis from live InfluxDB analysis (this session): Refoss
`em:2`/`em:8`/`em:9`, `hvac.comfortnet`, `hvac.thermostat`, `ecowitt.weather`
ch2, `haven.indoor`. CTK04 staging settings per `docs/HVAC_LOGIC.md`. No code
change (Category C / deferred B).

---

## 2026-06-15 — AIR (ISU 4090) documentation contradiction corrected; dead arm-transition logger removed

**Category:** Documentation erratum (no apparatus change).

**What was wrong.** The frozen pre-registration contained an internal
contradiction about CTK04 ISU 4090 (Adaptive Intelligent Recovery, "AIR"):

- `docs/HVAC_LOGIC.md` (authoritative equipment table; present since the
  initial commit) correctly stated **AIR = OFF** — "critical for this
  scheduler," because AIR pre-empts scheduled setpoints 30–60 min early,
  pulling runtime into peak pricing.
- `docs/THERMOSTAT_ARM_A_SCHEDULE.md`, `docs/ARM_TRANSITIONS.md`, and
  `docs/ARM_B_IMPLEMENTATION.md` §5 (introduced 2026-05-10) incorrectly
  described **AIR = ON during Arm A**, toggled per arm each Monday, with a
  manual audit logger (`scripts/log_arm_transition.py`) and a speculative
  Control4-driver automation. This per-arm AIR toggle was never the intended
  design and was never operationalized.

**As-run state (evidence).** AIR ran **OFF in both arms** for the entire study
to date. Thermostat + co-located Ecowitt ch2 telemetry over 2026-06-08→06-14
show cooling beginning **at** each scheduled setpoint drop (Return 19:00, Sleep
22:00) with the house never pre-cooled to the new setpoint — the AIR-off
signature — including on hot nights where AIR-on would have pre-cooled hardest.
The `hvac.arm_transitions` measurement the logger would have written has **zero
rows** (logger never run).

**Why it does not affect the result.** AIR was identical (OFF) in both arms — a
common-mode apparatus setting, not a treatment variable — so it cannot bias the
Arm A vs Arm B matched-pair comparison. The binding spec
(`sced-rebaseline-spec-2026-05-13.md`) defines the arms by control authority
(thermostat program vs active Pi) and characterizes Arm A as "a standard
programmable thermostat schedule"; it does not itself assert AIR = ON (it
delegates the value to the equipment docs).

**Correction applied (docs only).** Set AIR = OFF consistently in
`THERMOSTAT_ARM_A_SCHEDULE.md`, `ARM_TRANSITIONS.md`, and
`ARM_B_IMPLEMENTATION.md`; removed the per-arm-toggle / Monday-flip framing.
Deleted the dead, never-run `scripts/log_arm_transition.py` and its test. No
code path ever touched ISU 4090 (verified: the scheduler has zero references to
4090 / AIR), so there is no behavioral change.

**Flagged follow-up (not done here).** The `hvac.arm_transitions` measurement
remains listed in the analysis replay manifest
(`tools/analysis/replay/manifest.py`) as a reason-coded empty series. Removing
it touches the frozen replay-validation baseline and is deferred for a separate,
deliberate decision.

**OSF action (operator).** The contradiction is in the OSF-frozen deposit
(`osf-prereg-2026-05-31`); this correction should be noted as an erratum on the
OSF record — the frozen docs disagreed on a common-mode setting, the apparatus
ran OFF (the authoritative value), and the record is corrected to OFF.

**Links.** Diagnosis from live InfluxDB + git history (this session). Drift
introduced 2026-05-10 (`2ce7716`, `4eb9914`); not caught by the 2026-05-18
pre-OSF doc audit.
