# Monday arm-transition procedure

The residential HVAC controls SCED study alternates Arm A (consumer-grade
programmable + smart recovery) and Arm B (full forecast-and-price-aware
reactive controller) in 2-week runs within randomized 4-week blocks. Each
arm transition happens at Monday 00:00 CT and requires three coordinated
changes:

1. **CTK04 ISU 4090 ("AIR" / Adaptive Intelligent Recovery)** — flipped to
   match the active arm. ON during Arm A (thermostat learns recovery
   timing, matches consumer Nest/Ecobee behaviour); OFF during Arm B
   (Pi setpoint pushes are honored at the scheduled minute, not
   pre-emptively reinterpreted).
2. **Pi scheduler dry-run mode** — Arm A runs the scheduler in
   `SCHEDULER_DRY_RUN=true` (intended actions logged but no setpoints
   pushed); Arm B sets it to `false`.
3. **Audit row** in `hvac.arm_transitions` so the experimental record
   reflects which arm was live at each transition.

Per [`EXPERIMENT_DESIGN.md§5`](EXPERIMENT_DESIGN.md#5-randomization-and-assignment).

---

## v1: manual procedure (ships with OSF filing)

The TCC web UI is the only confirmed write path for ISU 4090. Cinegration's
Control4 driver may expose ISU 4090 as a writable parameter (v2; pending
investigation), but until that's verified the toggle is manual.

### Each Monday at 00:00 CT (or shortly after the previous arm-week ends)

1. Read this week's arm from
   [`docs/experiment-assignments-summer-2026.csv`](experiment-assignments-summer-2026.csv).
2. **Toggle AIR**: open <https://mytotalconnectcomfort.com/>, navigate to
   the CTK04AE installer menu, set ISU 4090 = ON (Arm A) or OFF (Arm B).
   Verify the change took effect by reading back the value from the menu
   on the device itself.
3. **Toggle Pi dry-run**: SSH to pi-lab and edit
   `~/energy-stack/.env`:
   - Arm A: `SCHEDULER_DRY_RUN=true`
   - Arm B: `SCHEDULER_DRY_RUN=false`
   Then restart the scheduler:
   ```bash
   cd ~/energy-stack
   docker compose restart hvac-scheduler
   ```
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
5. **Log the transition**: from inside the project's deploy directory,
   run the audit script:
   ```bash
   # Arm A -> Arm B (entering Arm B):
   python scripts/log_arm_transition.py --from A --to B --air off

   # Arm B -> Arm A (entering Arm A):
   python scripts/log_arm_transition.py --from B --to A --air on
   ```

### If the assignment shows the same arm continuing for a second week

No action needed mid-block; the AIR setting and dry-run mode persist from
the prior Monday's transition.

### Validation criterion

For at least the first three Monday transitions of summer 2026:

- AIR is ON when starting an Arm A week (verify via the device's
  installer menu directly, not just TCC's cached view).
- AIR is OFF when starting an Arm B week.
- The transition was logged. Confirm with:
  ```bash
  ssh chris@192.168.20.10 'docker exec influxdb influx query \
    "from(bucket:\"energy\") |> range(start: -7d) |> \
     filter(fn:(r) => r._measurement == \"hvac.arm_transitions\")"'
  ```

---

## v2: automated via Control4 driver (post-OSF, optional)

If Cinegration's C4 driver exposes ISU 4090 as a writable parameter, the
toggle can move into `execute_action` at the Monday 00:00 transition (the
scheduler already runs every minute and reads the assignment CSV). v1
remains the documented fallback whenever the C4 path errors.

The investigation is tracked as a follow-up; the manual procedure ships
with the OSF pre-registration filing regardless.

---

## InfluxDB measurement schema

```
measurement: hvac.arm_transitions
tags:
  from_arm:        "A" | "B"
  to_arm:          "A" | "B"
  manual_or_auto:  "manual" | "auto"
fields:
  air_setting:  "on" | "off"
  pi_dry_run:   bool   (true when entering Arm A, false when entering Arm B)
  note:         free-form string (default empty)
timestamp:    transition wall-clock (UTC)
```

Tagged by `from_arm` + `to_arm` so dashboards can filter cleanly (e.g.,
"every B->A transition this season"). `manual_or_auto` is a tag so the
reporting cut survives once v2 lands.
