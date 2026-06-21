---
date: 2026-06-20
owner: chris
status: draft (revision 3 — post context-fed review)
role-label: code-team
supersedes_intent_of: the now-removed day-type / deep-precool controller model (its docs were deleted in the demolition)
depends_on: Control4->TCC actuation swap (spec + plan on branch design/control4-to-tcc-swap, not yet merged)
---

# Commissioning Controller — design spec

## What this is (read first)

This is a **controlled demolition** of the old `hvac_scheduler` and its
replacement with a much simpler controller.

- **Arm A** = a comfortable thermostat program (the static schedule).
- **Arm B** = that same Arm A program **plus price awareness** — it holds the
  comfort baseline and drifts *warmer* when power is expensive. **That is the
  entire controller.** Nothing else.

Gone: weather day-types, the 21:00 decision cycle, the four fixed schedules,
deep day-ahead precool, thermal models, live 5CP control, and the software safety
supervisor. The old controller failed under its own complexity and drift; this is
the cure, not a feature.

**2026 scope is only: get it built and running solidly.** Not tuning, not
cost-measurement. The cost-savings comparison (Arm A vs Arm B) is the **2027**
experiment; nothing in 2026 depends on it. 2026 calibration is at most: does it
run without glitches, and maybe the setpoints.

> **Context:** the previously-planned A/B field experiment is offline and its
> pre-registration is being **retracted** — no binding frozen schema or
> pre-registration constrains this work. The experiment apparatus (arm calendar,
> `experiment` mode, `current_arm_at`, `hvac.arm_mode`) is **retained** for the
> 2027 comparison but is not driving 2026. Old analysis/replay tooling that read
> the dead controller's telemetry is being rebuilt and is out of scope here.

## How 2026 runs

1. **Shadow, Arm A in control.** The thermostat (Arm A) does the real
   controlling; Arm B runs in **shadow** — it computes and logs what it *would*
   do but never writes. Watch it for several weeks to catch glitches and things
   to fix.
2. **Flip.** Once Arm B looks solid, it takes over and **runs the season** for
   real; Arm A sits in the background.
3. **Review.** End of season: how did it run, did anything break, what to fix.

## Build discipline (binding): reuse, don't reinvent

Every component is an extend-in-place of a named existing backbone. The only
net-new code is the YAML config loader (+ a small helper module). Do not rebuild
what exists.

## Why this shape (the reframe)

1. **Cost-saving is primary; comfort is the constraint, not the goal.** Pure
   cost-minimization is degenerate ("set it as warm as tolerable"); the comfort
   *constraints* make it sensible — a baseline floor it never cools below and a
   ceiling it never drifts above bound the warm drift. Cost-first, within the
   comfort envelope.
2. **The old deep-precool model assumed the wrong house** — right-sized 2-stage
   equipment (stage 1 = 2.35 kW, stage 2 = 3.22 kW; stage 1 never carried a
   descent in 25 d -> **stage 2 = "get colder," stage 1 = "hold"**), high-solar
   envelope (banked coolth leaks before the afternoon peak), cold-sensitive
   occupant (the baseline floor caps how cold we ever go). Deep early precool is
   unreachable, mistimed, and uninhabitable here.
3. **Cost lives in the evening** — the deep evening pulldown to 73°F was ~60% of
   HVAC cost (stage 2); the afternoon coast was ~5%.
4. **Spikes, not average spread, are the prize, and not weather-forecastable**
   (daily-max-temp vs daily-max-price r = 0.08; spikes to 376¢). So the
   architecture is **reactive, not predictive** — **no weather input, no
   forecasting, no banking** in the controller. The live price signal is the
   only control input.
5. **Summer re-introduces sustained events** (2025 DA-LMP proxy: median 4 h, max
   10 h, evening-clustered) vs shoulder 5-min blips.

## Empirical grounds (measured 2026-06)

| Fact | Value |
|---|---|
| Stage 1 / 2 compressor power | 2.35 / 3.22 kW (1.37×); stage 1 never descends |
| Weather ↔ spike | r = 0.08 (decoupled); max spike 376.5¢ |
| Spike duration | shoulder median 5 min; summer (proxy) median 4 h |
| Instrument | ecowitt ch2: 0.18°F, 60 s, at the control point (r=0.92 vs stat) |
| Cooling overshoot | the unit overshoots its setpoint ~1-1.5°F — watch this at the ceiling |

## What the controller does

### Comfort program (the spine) — and the floor

The Arm A program, represented with the existing `ScheduleAction`/time-block
lookup. Values are **config**, in `temp_scale`. A reasonable seed (shown in °F
as shorthand; the config is almost certainly authored in °C for the 0.5° grid):

| Block (CT) | Cool (seed) | Grounding |
|---|---|---|
| 22:00-06:00 (sleep) | 74 | bedding-adjusted neutral |
| 06:00-12:00 | 76 | ASHRAE mid-zone |
| 12:00-18:00 | 78 | ASHRAE warm edge |
| 18:00-22:00 | 76 | ASHRAE mid-zone |

**The comfort baseline is the floor — a code invariant.** The controller NEVER
commands below the current block's baseline; an unconditional clamp enforces it,
not just a test. The only direction from baseline is *warmer*, on price. Heat is
pinned to a floor (config) every push.

### Reactive core — warm-only overlay above the baseline

Holds the comfort baseline; only ever pushes **warmer** when power is expensive.
**Extend** the existing `price_overlay.py` state machine. Four tiers, all
warmer-or-equal to baseline; thresholds and offsets are config:

| RTP tier | Action |
|---|---|
| normal | hold the comfort baseline |
| elevated | +offset, drift up |
| scarcity | float toward the ceiling |
| extreme | snap to the ceiling |

`extreme` is a first-class 4th tier everywhere; its reason-classifier branch
exists (else it mislabels as release-to-normal). **Extreme rationale:** an
extreme-price hour carries tail probability of being a 5CP hour worth a year of
capacity charge — so be maximally aggressive. No separate live 5CP mechanism.

**No below-baseline path.** No cheap tier (cooling below target when cheap spends
cooling for no payback and runs a cold-sensitive house colder than wanted); no
heat-stretch below-baseline banking (the floor invariant is absolute).

### Feed-gap behavior — reuse as-is (controller logic, not safety)

**Reuse** the existing minimum-hold + stale-release in
`app.py:_evaluate_layer_inputs` unchanged:
- brief gap -> **hold** the active tier (rides out routine ~5-min publish gaps);
- sustained staleness -> **release to baseline**. Note the stale-release timer
  runs *after* the minimum-hold window, so a release takes roughly **min-hold +
  ~30 min**, not a flat 30 min. This is the existing mechanism, reused as-is and
  intended (don't "fix" it to a flat 30 min);
- **react to a stale spike** — a stale-but-present high-price sample drives a warm
  upgrade (deliberate cost-primary choice; bounded by the ceiling, the humidity
  guard, the 30-min release, and fresh-data correction). Downgrades stay
  freshness-gated.

### Guards

- **Ceiling — ride it (a cap the controller holds at, not a bounce).** The drift
  rises to the configured comfort ceiling and **holds there** for the spike (keep
  the savings, sit at the warmest tolerable point), then cools back to baseline
  when price drops. The ceiling is a **config value tuned during 2026** — because
  the unit overshoots its setpoint, the *actual* indoor temp can run above the
  commanded ceiling; watch the commanded-vs-actual gap and lower the ceiling if
  the house runs hotter than intended.
- **Humidity guard — a second ceiling that *releases* (unlike the temp ceiling).**
  When indoor RH ≥ threshold (config), gate the overlay off so the effective
  setpoint falls back to the current comfort baseline (reuses the
  no-overlay=baseline path). It overrides the spike-hold min-hold and re-enables
  with hysteresis. Missing humidity -> conservative (treat as humid -> baseline).
  At baseline the AC cycles and dries the air; **never below baseline, no DEHUM**
  (DEHUM pre-cools early — against the goal). Asymmetric with the temp ceiling on
  purpose: temp **rides**, humidity **releases** (because only cooling fixes
  humidity, but riding warm *is* the temp goal).

### Units — `temp_scale`, controller-native, conversion only at analysis

The controller runs **entirely in its native `temp_scale`** (env `TEMP_SCALE`;
parameterization merged in #104). The value is a config choice (C or F); the
device is set to the same unit, so the device write is **format-only**, never a
conversion. No conversion anywhere in the controller — the single unit bridge
lives in the (out-of-scope, being-rebuilt) analysis layer, which normalizes on
read. Telemetry uses **scale-neutral field names + a `unit` tag**. Config values
are authored on the scale's grid (whole for F, 0.5 for C); the loader validates.

### Config is the experimental surface

Baseline, warm offsets, guard thresholds, ceiling, hold TTL — **all config, not
hardcoded**, in `temp_scale`. **Code owns invariants only:** warm-only; never
below the current baseline; the **humidity** guard revokes the overlay to
baseline while the **temp ceiling rides** (caps-and-holds, does not revoke);
device fail-safe. 2026's
"refine the setpoints" happens by editing config, so config identity must be
recorded (see Telemetry) to keep refinements interpretable.

```yaml
temp_scale: C            # or F — config choice. All temps below in temp_scale.
comfort_program:         # blocks {from, to, cool}; supports midnight wrap
  - {from: "22:00", to: "06:00", cool: 23.5}
  - {from: "06:00", to: "12:00", cool: 24.5}
  - {from: "12:00", to: "18:00", cool: 25.5}
  - {from: "18:00", to: "22:00", cool: 24.5}
heat_floor: 18.5
flexibility:     {warm_band: 1.0, spike_extra: 1.0}
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, extreme_at: 50}
humidity_guard:  {rh_max_pct: 65, rh_clear_pct: 62}
ceiling:         {comfort_max: 29.0}        # the warm-drift ceiling the controller rides (tuned in 2026)
hold_ttl_minutes: 60
modes:           {stage1_ramp: {enabled: false}}
```

No `cheap` tier, no `runtime.mode` key, no `supervisor` block. `SCHEDULER_MODE`
(env) is the sole write gate; secrets stay in env.

## Safety — device-owned (no software supervisor)

Safety lives in the **device** (which keeps running when the Pi is dead), not in
controller code (which dies with the Pi). Two facts:

1. **The thermostat's own setpoint min/max limits are the hard cap.** No command
   — buggy or not — can be set outside them. This is the load-bearing safety and
   it exists today (a device setting). **The controller's comfort ceiling is NOT
   a safety mechanism** — it's a control target; the device limit is the cap.
2. **Holds are timed, never Permanent** -> a dead/hung controller's hold lapses
   (≤ `hold_ttl_minutes`) and the thermostat reverts to its onboard schedule.
   This is the graceful-reversion enhancement; it is implemented in the TCC swap
   (current code sets a Permanent hold; the swap's timed-hold increment is the
   prerequisite). **Until it lands, a dead controller leaves the last setpoint
   held — still bounded by fact #1.**

There is **no software safety supervisor** (`safety_supervisor.py` /
`validate_setpoints` is deleted). Worst case is bounded by fact #1: the house
can't be commanded past the device's max.

Prerequisites (verify in the swap / operational checklist): the thermostat's
setpoint min/max are set appropriately; **the CTK04 onboard fallback schedule is
a known-safe cool schedule, captured as an authoritative current snapshot** — it
is the safety fallback a lapsed hold reverts to, so it is *operationally current*,
not historical. Its dedicated schedule doc was deleted in the demolition; capture
an authoritative snapshot of the deployed CTK04 onboard program (a new home for it
is TBD). `aiosomecomfort` exposes a time-based hold.

## Telemetry

- **Decision log = reshape `hvac.actions` in place** (no new measurement) + the
  `decision_trace` Loki lines. Reshape for the reactive model:
  - **scale-neutral field names + a `unit` tag**;
  - carry tier, comfort baseline, **commanded setpoint, and actual indoor temp**
    side by side (so you can watch Arm B behave in shadow and see the ceiling
    overshoot), humidity-guard state, drift state;
  - **purge the old controller's provenance:** drop the `day_type` tag, the
    `fivecp_*` fields, and the supervisor fields. No controller row should imply
    a day-type or that 5CP participated. PJM 5CP stays only as its own separate
    telemetry (`hvac.5cp_state`) for later analysis.
- **Config provenance:** log the config file path + SHA256 at startup, and tag
  the decision rows / trace with a `config_id`; archive deployed config snapshots
  with effective dates. Without it, 2026's "refine and re-look" is not
  interpretable (controller change vs config change is indistinguishable).
- **`hvac.price_overlay`** carries the full 4 tiers including `extreme`.
- **Remove the day-type writers** `hvac.decisions` and `hvac.precool_window`.
- **Observers are intentionally second-class during the rebuild — the final
  controller schema wins.** Cockpit, the daily trace report, and the
  `tools/analysis/` pipeline **will break and are rebuilt after the controller is
  stable.** This is a deliberate choice, not an oversight; the breakage is
  recorded, not avoided. (The scheduler's own startup read-back of the removed
  measurements is removed by the rewrite — the per-tick baseline replaces it.)
- **`hvac.arm_mode` keeps emitting** (watchdog liveness).
  **`required_feeds_for_arm_mode` is derived from the enabled-mode set** (not a
  hardcoded dict); `weather` drops when no enabled mode uses it.

## Runtime

- **`SCHEDULER_MODE`** is the write gate: `shadow` (compute/log, never write) ->
  flip to `production` (write for the season) per "How 2026 runs." `experiment`
  (arm-gated) is the 2027 layer, untouched. A partial slice deployed via merge
  must stay in `shadow`; the flip to `production` is explicit and gated.
- **Per-tick comfort baseline.** Compute the current baseline **every tick** (the
  loop needs it to decide), which also removes the old day-type startup
  reconstruction. *(No supervisor-continuity concern — supervisor removed.)*
- **Shadow validation for 2026 = log Arm B's proposed actions and watch them**
  (the several-weeks shadow phase), plus a small set of sanity checks (floor
  never violated, setpoints on-grid, no day-type consulted). **This is net-new —
  `run_shadow_validation.py` is the ingestion-pipeline validator and does NOT
  check setpoints; do not claim to "extend" it for the decision oracle.**

## Go-active (the flip to `production`)

The TCC swap landed (incl. the timed-hold increment) + the thermostat's setpoint
limits and onboard fallback schedule confirmed set + a **hold-expiry-reverts-to-
onboard-schedule** check on real hardware + a format-on-write/readback round-trip
+ a container-restart recompute check + SOPS/`SCHEDULER_MODE` confirmation ->
flip `shadow` -> `production`.

## Architecture

**In-place, single-path rewrite.** Rewrite the decision/resolve logic in place;
keep the price-overlay state machine, the feed-gap machinery, the write gate, the
telemetry writers, `freshness.py`, **and the retained experiment apparatus** (arm
calendar, `experiment` mode, `current_arm_at`, `hvac.arm_mode`) as shared infra.
No parallel path, no flag — `shadow` is the isolation, git history is rollback.
Removed: Arm B's day-type logic (day-types, four schedules, 21:00 cycle, day-ahead
precool, startup reconstruction) **and the software safety supervisor** — each
removed with all its callers in a single slice so the suite never goes red.
**Phase discipline:** delete a thing's tests and stop the main loop calling it in
the same slice that removes it.

## Scope

**IN:** in-place warm-only reactive controller (comfort program + 4 warm tiers +
warm band + humidity-release guard + ride-the-ceiling), device-owned safety
(thermostat min/max now; timed-hold via the swap), `temp_scale` actuation
(format-only), config-as-surface + provenance, `shadow -> production` runtime,
reshaped `hvac.actions`, live for 2026 commissioning (build/validate/run/review).

**OUT / removed:** day-types, four schedules, 21:00 cycle, day-ahead precool,
startup reconstruction, **the software safety supervisor**, below-baseline cooling
(cheap tier + heat-stretch banking), DEHUM, predictive control, **cost/savings
measurement (that is 2027)**. Observer rebuilds (cockpit, trace, analysis) are
tracked but out of scope here.
