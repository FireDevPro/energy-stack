# Dry-run mode validation procedure

> [!WARNING]
> **`SCHEDULER_DRY_RUN` was retired in Phase 1 #112 (2026-05-14)** and replaced with `SCHEDULER_MODE` (values: `shadow` for Arm A, `active` for Arm B) per the rebaseline impl plan standing rule #5. The validation procedure below describes the retired env var; the operational mechanics still apply but the env var name and the values are different. See [`docs/plans/sced-rebaseline-implementation-2026-05-13.md`](plans/sced-rebaseline-implementation-2026-05-13.md) standing-rule #5 + Phase 1 deploy notes for the migration. Also: "randomization begins" framing here predates the rebaseline; the experiment starts 2026-06-01 with **deterministic alternation** (no PRNG seed). Tracked since [PR #137 F3 deferral](https://github.com/Promithius-DR/energy-stack/pull/137).

The Pi-side `hvac-scheduler` runs in two modes:

  * **Active** (`SCHEDULER_DRY_RUN=false`) -- Arm B weeks. The scheduler
    pushes cool setpoints + Permanent hold to the CTK04AE via Control4.
  * **Dry-run** (`SCHEDULER_DRY_RUN=true`) -- Arm A weeks. The scheduler
    runs all classification, schedule firing, price-overlay evaluation,
    and 5CP detection, logs every intended action to `hvac.actions` with
    `dry_run=true`, and pushes nothing.

Per [`EXPERIMENT_DESIGN.md§3`](EXPERIMENT_DESIGN.md#3-arms--conditions)
and [`ARM_B_IMPLEMENTATION.md§6`](ARM_B_IMPLEMENTATION.md). This document
specifies the binding pre-flight validation that must complete before
the experiment starts (2026-06-01).

---

## Env var contract

`SCHEDULER_DRY_RUN` (default `true`). Read at scheduler startup; the
arm-transition Monday procedure flips it alongside the AIR toggle (see
[`ARM_TRANSITIONS.md`](ARM_TRANSITIONS.md)).

```bash
# In ~/energy-stack/.env on pi-lab:
SCHEDULER_DRY_RUN=true   # Arm A weeks
SCHEDULER_DRY_RUN=false  # Arm B weeks
```

A change requires `docker compose restart hvac-scheduler` to pick up.

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
2. SSH to pi-lab and confirm dry-run mode:
   ```bash
   ssh chris@192.168.20.10 \
     'grep SCHEDULER_DRY_RUN ~/energy-stack/.env && \
      docker exec hvac-scheduler env | grep SCHEDULER_DRY_RUN'
   ```
   Both must report `SCHEDULER_DRY_RUN=true`.
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
