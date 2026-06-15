---
date: 2026-05-18
owner: chris
status: active
role-label: code-team
---

# Monday arm-transition procedure

> [!NOTE]
> **Calendar framing updated 2026-05-18**: the experiment now uses **deterministic 14-day alternation** (12 arms total, 2026-06-01 → 2026-11-16) per [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](plans/sced-rebaseline-spec-2026-05-13.md) §2 — not "randomized 4-week blocks." The arm-transition operational procedure below remains valid; only the framing of which arm runs when has changed (now deterministic, no PRNG seed, canonical calendar in `tools/analysis/arm_calendar.py` and `deploy/energy-stack/hvac_scheduler/arm_calendar.py`). Also: `SCHEDULER_DRY_RUN` env var was retired in Phase 1 #112 and replaced with `SCHEDULER_MODE` (required, no default; values: `shadow`, `experiment`, `production` per binding spec §3 — set `SCHEDULER_MODE=experiment` during the study window for automatic per-arm-period A/B gating; no per-Monday env flip needed). Tracked since [PR #137 F3 deferral](https://github.com/FireDevPro/energy-stack/pull/137).

The residential HVAC controls SCED study alternates Arm A (consumer-grade
programmable, fixed schedule) and Arm B (full forecast-and-price-aware
reactive controller) in **deterministic 14-day arms** (12 arms total, 6 Arm A + 6 Arm B, alternating). Each
arm transition happens at Monday 00:00 CT. Both standing settings are now
fixed or automatic, so the only per-Monday action is the Arm B→A hold-clear:

1. **CTK04 ISU 4090 ("AIR" / Adaptive Intelligent Recovery)** — fixed
   **OFF in both arms** (not toggled per arm). With AIR on, the thermostat
   starts cooling 30-60 min before a scheduled setpoint change, which pulls
   runtime into peak pricing and pre-empts the schedule; OFF keeps scheduled
   setpoints landing at their scheduled minute. See `docs/HVAC_LOGIC.md`.
2. **Pi scheduler mode gating** — `SCHEDULER_MODE=experiment` set ONCE before the experiment starts; the scheduler reads the locked arm calendar and automatically blocks writes during Arm A periods and writes during Arm B periods. No per-Monday env-var flip needed. (Pre-rebaseline this step manually flipped `SCHEDULER_DRY_RUN=true|false` weekly; that env var is retired per binding spec §3 + Phase 1 #112.)

Arm membership per hour is derived deterministically from the calendar
(`arm_calendar.py`); there is no manual transition logging.

Per [`plans/sced-rebaseline-spec-2026-05-13.md` §2 (calendar)](plans/sced-rebaseline-spec-2026-05-13.md).

---

## v1: manual procedure (ships with OSF filing)

ISU 4090 (AIR) is set **OFF** once and left OFF in both arms — there is no
per-arm toggle. (The TCC web UI is the write path if it ever needs changing.)

### Each Monday at 00:00 CT (or shortly after the previous arm-week ends)

1. Read this week's arm from the canonical calendar at [`tools/analysis/arm_calendar.py`](../tools/analysis/arm_calendar.py) (mirror at [`deploy/energy-stack/hvac_scheduler/arm_calendar.py`](../deploy/energy-stack/hvac_scheduler/arm_calendar.py)). The retired `docs/experiment-assignments-summer-2026.csv` is preserved as a pre-rebaseline historical artifact only — do NOT read it.
2. **AIR**: no per-Monday action — ISU 4090 is fixed **OFF** in both arms.
   (Confirm once via the CTK04AE installer menu that it reads OFF.)
3. **Pi scheduler mode**: no per-Monday change. `SCHEDULER_MODE=experiment` set once at experiment start handles arm-period gating automatically (writes blocked during Arm A windows, active during Arm B windows). This step exists only as a pre-rebaseline historical reference; the per-Monday env-var flip + `docker compose restart hvac-scheduler` is retired.
4. **Clear any Permanent hold on the thermostat (Arm B → Arm A
   transitions only).** When leaving Arm B for Arm A, on the
   thermostat home screen confirm there is no Permanent hold left
   over from a Pi-pushed Arm B action. If "HOLD" is displayed,
   press CANCEL HOLD so the autonomous schedule resumes. The Pi
   scheduler does NOT clear holds during Arm A (it runs in shadow
   mode with writes blocked), so this is the sole guarantee that
   Arm A starts with a clean schedule run. No action needed on
   Arm A → Arm B transitions because the Pi scheduler re-pushes
   Permanent holds at its next scheduled action anyway.
(No transition-logging step. Arm membership per hour is derived from the
deterministic calendar; the former manual `log_arm_transition.py` audit logger
was removed 2026-06-15 — see `docs/EXPERIMENT_CHANGE_LOG.md`.)

### If the assignment shows the same arm continuing for a second week

No action needed mid-block; AIR stays OFF and the scheduler mode persists from
the prior Monday's transition.

### Validation criterion

For at least the first three Monday transitions of summer 2026:

- AIR reads OFF (verify via the device's installer menu directly, not just
  TCC's cached view) — it is OFF in both arm types, not toggled.

---

## AIR is not toggled

AIR (ISU 4090) is fixed OFF in both arms, so there is no per-arm toggle to
perform or automate. (Earlier drafts proposed automating an AIR flip via the
Control4 driver; that is moot — AIR pre-empts the price-aware schedule and is
simply left OFF. See `docs/HVAC_LOGIC.md`.)

---

## `hvac.arm_transitions` measurement (removed)

The manual `log_arm_transition.py` audit logger was removed 2026-06-15. It was
never run (the measurement has zero rows), and arm membership is derived
deterministically from the calendar (`arm_calendar.py`), so the logger was
redundant. The measurement name is still listed in the analysis replay manifest
as a reason-coded empty series; reconciling that is a flagged follow-up. See
`docs/EXPERIMENT_CHANGE_LOG.md`.
