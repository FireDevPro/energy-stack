---
date: 2026-05-18
owner: chris
status: active
role-label: code-team
---

# Dry-run mode validation procedure

> [!WARNING]
> **`SCHEDULER_DRY_RUN` was retired in Phase 1 #112 (2026-05-14)** and replaced with `SCHEDULER_MODE` (values: `shadow` for Arm A, `active` for Arm B) per the rebaseline impl plan standing rule #5. The validation procedure below describes the retired env var; the operational mechanics still apply but the env var name and the values are different. See [`docs/plans/sced-rebaseline-implementation-2026-05-13.md`](plans/sced-rebaseline-implementation-2026-05-13.md) standing-rule #5 + Phase 1 deploy notes for the migration. Also: "randomization begins" framing here predates the rebaseline; the experiment starts 2026-06-01 with **deterministic alternation** (no PRNG seed). Tracked since [PR #137 F3 deferral](https://github.com/Promithius-DR/energy-stack/pull/137).

The Pi-side `hvac-scheduler` runs in one of three modes, controlled by `SCHEDULER_MODE` (required env var, no default; container exits with code 2 on missing or invalid):

  * **`shadow`** -- never writes. Logs every decision/telemetry but pushes
    no setpoints. Safe pre-experiment default.
  * **`experiment`** -- reads the locked A/B calendar
    ([`tools/analysis/arm_calendar.py`](../tools/analysis/arm_calendar.py)).
    Arm A periods = no writes (Arm A is the CTK04AE programmed schedule);
    Arm B periods = active writes (cool setpoints + Permanent hold pushed
    to CTK04AE via Control4). Outside the 2026-06-01..2026-11-16 window =
    no writes.
  * **`production`** -- writes always, ignores A/B calendar. Used only for
    deliberate non-study operation. Excluded from analysis.

Per [binding spec §3](plans/sced-rebaseline-spec-2026-05-13.md). This document
specifies the binding pre-flight validation that must complete before
the experiment starts (2026-06-01).

---

## Env var contract

`SCHEDULER_MODE` (required, no default). Read at scheduler startup; container
refuses to start (`sys.exit(2)`) on missing or invalid value. The arm-transition
Monday procedure does NOT flip this env var — set it once before the experiment
starts; arm-period gating happens automatically via the locked calendar (see
[`ARM_TRANSITIONS.md`](ARM_TRANSITIONS.md)).

```bash
# In ~/energy-stack/.env on pi-lab:
SCHEDULER_MODE=shadow      # pre-experiment-start (before 2026-06-01)
SCHEDULER_MODE=experiment  # during the experiment window (2026-06-01..2026-11-16)
SCHEDULER_MODE=production  # post-experiment, deliberate non-study operation
```

A change requires `docker compose restart hvac-scheduler` to pick up. The retired `SCHEDULER_DRY_RUN` env var (replaced 2026-05-14 per Phase 1 #112) is ignored with a warning if still set.

---

## Unit-test contract (always true)

`execute_action()` returns `(applied=False, error=None)` immediately when
`dry_run=True`, before any Control4 call. The check sits at the top of
the function, above the release_hold and setpoint paths, so no controller
upstream of `execute_action` can leak a setpoint push when dry-run is
active. See [test_hvac_scheduler.py](../deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py)
tests:

  * `test_execute_release_hold_dry_run_does_not_call_thermostat`
  * `test_execute_setpoint_action_dry_run_pushes_nothing`
  * `test_execute_setpoint_action_dry_run_skips_even_when_layer_resolution_changes_setpoint`

These are run on every PR to the hvac-scheduler service.

---

## 24-hour pre-flight validation

Before randomization begins, run the scheduler in dry-run for a full
24-hour cycle on a NORMAL forecast day and confirm all four conditions
below. Any failure blocks randomization start.

### Setup

1. Pick a forecast-NORMAL day (high 75-85F, no heat advisory). Skip MILD
   days because the only scheduled action is `MILD_RELEASE_HOLD` at 00:05
   and there's nothing meaningful to validate.
2. SSH to pi-lab and confirm scheduler mode:
   ```bash
   ssh chris@192.168.20.10 \
     'grep ^SCHEDULER_MODE= ~/energy-stack/.env && \
      docker exec hvac-scheduler env | grep ^SCHEDULER_MODE='
   ```
   Both must report `SCHEDULER_MODE=shadow` (pre-experiment) or `SCHEDULER_MODE=experiment` (during the study, in which case the validation window must be inside an Arm A period for no-write expectations to hold).
3. Note the start time (UTC). The validation window is `[start, start+24h]`.

### Pass conditions

**1. Zero Pi-originated setpoint pushes during the window.**

Query the `hvac.thermostat` measurement (Control4 driver mirror of the
thermostat state). Every setpoint change in the window must be tagged
`source!="pi"` (i.e., TCC-originated, matching CTK04AE's programmed
schedule).

```flux
from(bucket: "energy")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "hvac.thermostat"
                        and r._field == "cool_setpoint_f")
  |> difference()                       // setpoint change events
  |> filter(fn: (r) => r._value != 0.0) // ignore no-ops
```

Any row with the Pi as origin is a failure.

**2. `hvac.actions` records the day's intended schedule.**

NORMAL day expected actions: `PRE_COOL @ 06:00`, `COAST @ 13:00`,
`RECOVER @ 19:00`, `SLEEP @ 21:00`. All four rows must exist with
`dry_run=true`.

```flux
from(bucket: "energy")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "hvac.actions")
  |> filter(fn: (r) => r.dry_run == "true")
  |> filter(fn: (r) => r._field == "cool_setpoint_f")
  |> count()
```

`_value >= 4` is the bar. Fewer means the scheduler missed firings;
investigate before promoting to Arm B.

**3. CTK04AE-programmed schedule executed its 4 fallback transitions.**

Verify the thermostat carried out its native `Wake / Leave / Return /
Sleep` schedule (per [HVAC_LOGIC.md](HVAC_LOGIC.md#thermostat-fallback-when-pi-is-offline)).
This shows up in `hvac.thermostat` as four `cool_setpoint_f` transitions
at 05:00 / 13:00 / 19:00 / 22:00 CT.

**4. `hvac.price_overlay` and `hvac.5cp_state` rows track decisions.**

Even when no overlay tier is active and 5CP isn't triggered, the §3
detector writes a `hvac.5cp_state` row every tick (so the decision trace
is auditable). `hvac.price_overlay` only writes on tier transitions, so
a flat normal-tier day produces zero rows -- that's expected.

```flux
// Expect ~288 rows per 24h (every-5-min ticks):
from(bucket: "energy")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "hvac.5cp_state")
  |> count()
```

A count near zero suggests the PJM data feed is degraded; not a
dry-run-mode bug, but blocks the validation until upstream is restored.

### Failure handling

If any condition fails:

  * Investigate the root cause; do NOT promote to Arm B.
  * If a real bug surfaces, file a fix and re-run the 24-hour validation
    after the fix lands.
  * The OSF filing is gated on this validation completing successfully
    (per [`ARM_B_IMPLEMENTATION.md§10`](ARM_B_IMPLEMENTATION.md#10-acceptance-criteria-for-osf-filing)
    item 3).
