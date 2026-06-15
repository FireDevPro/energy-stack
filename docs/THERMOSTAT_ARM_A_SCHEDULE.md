---
date: 2026-05-26
owner: chris
status: locked
role-label: spec
name: thermostat-arm-a-schedule
effective_at_osf_commit: pending-osf-filing
---

# Arm A thermostat schedule (CTK04AE programmed)

**OSF pre-registration freeze:** this document binds the CTK04AE thermostat's autonomous programmed schedule that runs during every Arm A period of the Summer 2026 experiment. Spec section 3 of `docs/plans/sced-rebaseline-spec-2026-05-13.md` references this file as the source of truth for Arm A setpoint behavior. The schedule is frozen at the OSF-filing commit hash. Any change to the thermostat's TCC-programmed weekly schedule after that commit is a protocol deviation requiring an OSF amendment.

The schedule documented here is the same 4-event daily program applied to all seven days of the week (Monday through Sunday identical). There is no weekday/weekend distinction in the programmed schedule, and there is no per-arm-period variation — the same fixed schedule runs autonomously across all six Arm A periods regardless of weather, calendar date, or PJM signals.

## Daily schedule (applies Monday through Sunday)

| Period | Time (CT) | Heat setpoint °F | Cool setpoint °F | Fan mode | Deadband |
|---|---|---|---|---|---|
| Wake | 5:00 AM | 68 | 73 | Automatic | 5°F |
| Leave | 1:00 PM | 66 | 78 | Circulate | 12°F |
| Return | 7:00 PM | 68 | 75 | Automatic | 7°F |
| Sleep | 10:00 PM | 65 | 73 | Automatic | 9°F |

**Deadband check.** The CTK04AE enforces a minimum 5°F separation between heat and cool setpoints in Auto Changeover mode (CTK04 ISU 3000). Every entry in the schedule above satisfies that constraint; Wake is at the floor at exactly 5°F.

**Fan mode terminology.** The TCC web UI labels the standard Auto setting as "Automatic" and the continuous circulation setting as "Circulate." The hvac-scheduler codebase and `docs/HVAC_LOGIC.md` use the shorter labels "Auto" and "Circulate." These are the same TCC settings — the abbreviated form is project-internal vocabulary, not a different control mode. The continuous-fan circulate duty cycle is set on the thermostat at 33 percent (about 20 minutes per hour) per the equipment-settings table in `docs/HVAC_LOGIC.md`.

## Equipment-level settings that bind together with the schedule

This document lists the equipment-side settings that are most directly relevant to Arm A behavior. **CTK04 ISU 4090 (AIR) = OFF** in both arms; all rows below are unchanged across arms and are reproduced here as a self-contained reference. The full installer-menu enumeration — including outdoor-equipment-type detection, IFC airflow trim / ramping profile / on-off delays, and the captured-state IFC user-menu rows — lives in the equipment-settings table at `docs/HVAC_LOGIC.md` and is the authoritative full list.

| Source | Code / Label | Setting | Arm A value | Why it matters |
|---|---|---|---|---|
| CTK04 ISU | 3000 | Auto Changeover Deadband | 3-5°F (verified at CTK04AE installer menu; matches HVAC_LOGIC.md equipment table) | Minimum separation between heat and cool setpoints in Auto mode. Every schedule period above satisfies the configured value (Wake at 5°F is the floor). |
| CTK04 ISU | 4090 | Adaptive Intelligent Recovery (AIR) | **OFF** | OFF in both arms. With AIR on, the thermostat starts cooling 30 to 60 minutes before a scheduled setpoint change, which pulls runtime into peak pricing and makes the Pi's setpoint pushes unpredictable; OFF means a scheduled setpoint applies exactly at its scheduled time. Matches the authoritative `docs/HVAC_LOGIC.md` row. |
| CTK04 ISU | 3020 | Finish With High Cool Stage | OFF (finish on LOW) | End-of-cycle stage management — better dehumidification and efficiency at end of cool calls. |
| CTK04 ISU | 3030 | Staging Control - Cool Differentials | Default (~2°F to call stage 2) | Stage 1 handles most loads; stage 2 only on overshoot. |
| CTK04 ISU | 3140 | Cool / Compressor Cycles Per Hour | Default | Compressor cycle protection. |
| CTK04 ISU | 3240 | Minimum Compressor Off Time | 5 min | Compressor protection. |
| CTK04 ISU | 9000-9080 | Dehumidification Equipment / Control | DEHUM equipment ON, paired with humidity setpoint | IFC drops blower speed during combined cool+DH calls, lengthening runtime and improving latent removal. |
| CTK04 home screen | Fan mode | Schedule (per-period overrides apply) | "Schedule" mode at the home screen so the per-period Auto / Circulate setting in the table above takes effect | The thermostat home-screen fan setting must be on "Schedule" (not "On" or "Auto") for the programmed schedule's per-period fan mode to apply. |
| CTK04 home screen | Fan | Continuous fan circulate duty cycle | 33 percent (default; about 20 minutes per hour) | Drives the "Circulate" setting during the Leave period. |
| IFC user-menu | DEHUM | Dehumidification active flag | ON | Equipment-side flag that enables the IFC blower-slowdown behavior during cool+DH calls. |
| IFC user-menu | CL OFF | Cool blower-off delay | 60 seconds (OEM default) | Blower runs 60 seconds after compressor cutoff, pulling residual latent cooling off the wet coil. |

CTK04 ISU 4090 is OFF and does not change across arms. All settings above are unchanged across arms. Arm A's equipment-side freeze is therefore whatever value each row in the HVAC_LOGIC.md authoritative table holds at the OSF-filing commit hash; changes after that commit are protocol deviations on the same footing as setpoint changes.

## Hold, vacation, and manual override assumptions

The Arm A schedule is supposed to run autonomously without intervention. The pre-registration commits to the following operator behavior:

- **No manual thermostat overrides during Arm A periods.** Per the spec's per-protocol estimand framing (section 1), the operator commits to not touching the thermostat during the 24-week experiment. If a manual override occurs (guest, equipment failure, accidental adjustment), it is a protocol deviation reported in the final analysis narrative, not silently absorbed into the per-pair table.
- **No vacation hold during Arm A periods.** A vacation hold would override the programmed schedule with a flat setpoint and would constitute a manual override at the protocol level.
- **No pending Permanent hold at the start of an Arm A period.** At every Arm B → Arm A transition the operator's checklist (`docs/ARM_TRANSITIONS.md`, "Each Monday at 00:00 CT") includes a Permanent-hold-clear step that confirms no Pi-pushed Permanent hold remains active on the thermostat from the prior Arm B period. The Pi scheduler does NOT clear holds during Arm A — it runs in shadow mode (writes blocked by `_writes_allowed()` in `app.py`), so its `MILD_RELEASE_HOLD` action (which fires only on MILD day types in any case) writes mode telemetry but never reaches the thermostat. The operator-side transition checklist is the sole guarantee that Arm A starts with no pending hold.

The scheduler does run in shadow mode during Arm A periods and writes mode telemetry and proposed-setpoint logs to InfluxDB, but it pushes no setpoints, hold-mode changes, or fan-mode changes to the thermostat. The thermostat's autonomous schedule is the sole driver of comfort during these periods. See `deploy/energy-stack/hvac_scheduler/app.py` and `docs/HVAC_LOGIC.md` "Thermostat fallback" section for the same schedule documented from the controller-side perspective.

## Provenance

- **Source:** TCC (Total Connect Comfort) web user interface, "THERMOSTAT Menu → SCHEDULE" tab. Account-protected, accessed via the operator's TCC login.
- **Pull date:** 2026-05-26 (re-pulled after pre-OSF Sleep cool refinement; see 2026-05-26 entry below).
- **Pull method:** Manual transcription from the TCC web UI. There is no read-only API path to the programmed schedule. The pyControl4 `C4Climate` driver used by `deploy/energy-stack/thermostat_poller/poller.py` exposes only the current-state fields (setpoints, mode, fan, hold) — not the embedded weekly schedule, which lives in the CTK04 thermostat's firmware and is reachable only through the TCC web UI or the on-device installer menu.
- **Evidence artifact:** `docs/THERMOSTAT_ARM_A_TCC_SCREENSHOT_2026-05-26.png` — the TCC web UI screenshot taken at the pull date, showing all seven days with identical 4-event schedules. The transcription in the daily-schedule table above matches the screenshot cell-for-cell.
- **Discrepancies surfaced and corrected:** the prior transcription embedded in `docs/HVAC_LOGIC.md` "Thermostat fallback" table and the cited Wake cool setpoint in `docs/EXPERIMENT_DESIGN.md` had two cells out of sync with the actual TCC schedule (Wake cool was documented at 74°F instead of 73°F; Leave heat was documented at 68°F instead of 66°F). Both are corrected in the same Phase 5 PR that introduces this document. The freeze cited in this document is the corrected, screenshot-verified version.
- **Pre-OSF design refinement (2026-05-26):** Sleep cool setpoint adjusted from 74°F to 73°F to match Arm B's Sleep cool temperature (`hvac_scheduler.NORMAL_SCHEDULE` SLEEP), eliminating a 1°F comfort confound between the two arms. Sleep time remains 22:00 CT — Arm B's 21:00 Sleep start is a separate intentional treatment lever (captures the first hour of the 21:00-06:00 DTOD off-peak window at ~$0.02984/kWh per `docs/HVAC_LOGIC.md`). Pre-OSF refinements are permitted under the freeze; post-OSF this would be a protocol deviation.
- **Verifier:** Chris (operator, sole household occupant).
- **OSF commit hash:** filled at OSF filing. Currently shown in the YAML frontmatter as `pending-osf-filing`; will be replaced with the commit SHA at the OSF deposit moment.

## Change-control

Post-OSF changes to any cell of the daily schedule table, to any value in the equipment-settings table, or to the hold-and-override commitments above are protocol deviations under the OSF pre-registration. The amendment procedure documented at the OSF project page applies. The amendment must cite this file's then-current commit hash as the pre-change baseline.

In-experiment programmed-schedule changes by the thermostat (firmware update changing defaults, TCC web UI auto-save corrupting a row) are also deviations and would be caught by the `hvac.thermostat` poller's `cool_setpoint_f` / `heat_setpoint_f` field readings drifting from the expected values for the current period.
