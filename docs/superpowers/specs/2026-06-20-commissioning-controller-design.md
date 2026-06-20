---
date: 2026-06-20
owner: chris
status: draft (post dual-review — Codex + Claude — revision)
role-label: code-team
supersedes_intent_of: docs/HVAC_LOGIC.md (day-type / deep-precool model)
depends_on: docs/superpowers/specs/2026-06-19-control4-to-tcc-swap-design.md
---

# Commissioning Controller — design spec

## Goal

Re-center the `hvac_scheduler` service from a weather day-type scheduler onto a
config-driven, real-time **price-reactive** controller that delivers a fixed
comfort program at minimum electricity cost, **rewritten in place** (single
path, shared infra), **shadow-validated** before it writes, and run live in
**`production` mode** through the 2026 commissioning season. The A/B experiment
is deferred to 2027.

Replaces the *intent* of [`docs/HVAC_LOGIC.md`](../../HVAC_LOGIC.md). Does not
change the actuation contract — that is the separate
[Control4→TCC swap](2026-06-19-control4-to-tcc-swap-design.md), this controller's
prerequisite write path.

**Build discipline (binding): reuse, don't reinvent.** Every component below is
an extend-in-place of a named existing backbone. Do not rebuild what exists.

## Why (the reframe)

1. **Pure-cost is degenerate** (the optimum is "set it as warm as tolerable").
   The real question: can price awareness deliver the *same comfort* for less?
   **Comfort is a held-equal constraint, not a free variable.**
2. **The old deep-precool model assumed the wrong house** — right-sized 2-stage
   equipment (stage 1 = 2.35 kW, stage 2 = 3.22 kW; stage 1 never carried a
   descent in 25 d → **stage 2 = "get colder," stage 1 = "hold"**), high-solar
   envelope (banked coolth leaks before the afternoon peak), cold-sensitive
   occupant (comfort floor caps precool depth). Deep early precool is
   unreachable, mistimed, and uninhabitable here.
3. **Cost lives in the evening** — the deep evening pulldown to 73°F was ~60% of
   HVAC cost (stage 2), landing when spikes cluster; the afternoon coast was ~5%.
4. **Spikes, not average spread, are the prize, and not weather-forecastable**
   (daily-max-temp vs daily-max-price r = 0.08; spikes to 376¢ this season). So
   the architecture is **reactive, not predictive**; weather is at most a regime
   detector for the (default-off) heat-stretch banking.
5. **Summer re-introduces sustained events** (2025 DA-LMP proxy: median 4 h, max
   10 h, evening-clustered, on hot-day streaks) vs shoulder 5-min blips.
6. **The literature doesn't cover this regime** — the controller's edge is
   unknown and plausibly material; commissioning measures it.

## Empirical grounds (measured 2026-06)

| Fact | Value |
|---|---|
| Stage 1 / 2 compressor power | 2.35 / 3.22 kW (1.37×); stage 1 never descends |
| Weather ↔ spike | r = 0.08 (decoupled); max spike 376.5¢ |
| Spike duration | shoulder median 5 min; summer (proxy) median 4 h |
| Cheap windows | sub-3¢ is common in mild conditions, day or night |
| Instrument | ecowitt ch2: 0.18°F, 60 s, at the control point (r=0.92 vs stat) |

## Design

### Comfort program (the spine)

Research-grounded (ASHRAE 55 summer zone ~73-80°F; sleep-neutral-with-bedding
68-84°F). Both the static baseline and, in 2027, the program both arms share.
**Reuse** the existing `ScheduleAction`/time-block lookup to represent it.

| Block (CT) | Cool °F | Grounding |
|---|---|---|
| 22:00-06:00 (sleep) | 74 | bedding-adjusted neutral; cool middle ground |
| 06:00-12:00 | 76 | ASHRAE mid-zone |
| 12:00-18:00 | 78 | ASHRAE warm edge; humidity-gated |
| 18:00-22:00 | 76 | ASHRAE mid-zone |

Heat pinned to a floor (65°F) every push (Auto deadband + freeze backstop).

### Reactive core — **warm-only overlay** above the comfort baseline

The controller holds the comfort baseline and only ever pushes **warmer** when
power is expensive (use less cooling now, recover when cheap — the warm-side
setback is where the savings live, ~2/3 of the value per the literature).
**Extend** the existing `price_overlay.py` state machine; do not rebuild it.

| RTP tier | Action |
|---|---|
| normal (< ~10¢) | hold the comfort setpoint |
| elevated (~10-20¢) | +offset, drift up |
| scarcity (~20-50¢) | float toward the ceiling |
| extreme (≥ ~50¢) | snap to the ceiling |

All tiers are **warmer-or-equal** to baseline. Flexibility band is warm-side:
baseline → ceiling, +2°F working / +3-4°F on spikes (Blonz: ~0.75°F accepted,
rare overrides; utility DR 2-4°F). The price signal carries the diurnal shape;
no clock schedule beyond the comfort program.

**Extreme-tier rationale:** be maximally aggressive on extreme price — not only
for live energy but because an extreme-price hour carries tail probability of
being a 5CP hour worth a year of capacity charge. No separate live 5CP mechanism.

**No "cheap"/below-baseline tier.** Cooling below the comfort target when power
is cheap *spends* more cooling for no payback (it only pays if it offsets a
*later* expensive period — that's forecast-gated banking, not a price point),
and a bare sub-3¢ gate would fire most of a mild day, running the house colder
than a cold-sensitive occupant wants. **Cut.** *(Deferred future idea: a
properly time-gated, deep-overnight, sub-3¢ banking variant — revisit only if it
earns its complexity.)* The only below-baseline path is heat-stretch banking
(below, off by default, forecast-gated).

### Feed-gap safety — reuse the existing mechanism **as-is**

Because the overlay is warm-only, the existing safety theorem holds (a stale feed
fails toward *less* cooling = safe). **Reuse** the existing minimum-hold +
30-minute safety-release in `app.py:_evaluate_layer_inputs` unchanged: hold the
active tier, release to baseline after 30 min of genuine staleness. The delayed
release rides out the routine ~5-min publish gaps; feed-health telegram alerting
is the human backstop. No asymmetry, no rework (the cheap-tier cut removed that
need entirely).

### Guards

- **Humidity guard** — runs **after the snapshot read, before the supervisor, in
  both the action-fire and mid-period paths**. Reads `snapshot["humidity"]`
  (missing → guard-conservative, not RH=0). If indoor RH > 60% (≈12 g/kg /
  dewpoint ≤60°F), drop the setpoint / extend stage-1 regardless of price (gates
  the 78°F daytime block). Not merely a `resolve_cool_setpoint` extension.
- **Spike-shed ceiling** — 82°F operational, 85°F hard cap, humidity-aware
  (lower when RH high; dog-bound). The supervisor's 86°F indoor emergency
  overrides the ceiling by design and will alert — expected during a deep shed.

### Units — `temp_scale` parameter (controller logic)

The controller's temperature unit is a **config parameter, `temp_scale`** (env
`TEMP_SCALE`; default `F`). The **controller decision logic speaks `temp_scale`**
end to end — float internally, scale-agnostic field names — so there is no
hardcoded °F/°C in the control path. **The value is a config choice, not baked
into the design** (default `F` = whole-degree; `C` gives 0.5° on-grid
granularity; switching is a one-line config change, not a code change).

**Scope boundary (deliberate): `temp_scale` is the *controller* unit, not the
whole stack.** The **experiment telemetry/analysis stays °F** (Ecowitt-anchored);
the controller emits its scale-agnostic values into the existing °F telemetry
keys at a single emission seam. Humidity/dewpoint thresholds and forecast fields
stay °F (Ecowitt °F data). Cockpit is downstream observability, never a
constraint.

**Device seam — formatting, not conversion (the one cross-cutting thing).** The
device is set to match `temp_scale`, so the control path needs no unit
conversion — only **format-on-write**: render the internal float to the form the
thermostat accepts for that scale (`F` → whole degree, `78`; `C` → 0.5 grid,
`23.5`). Values are already on-grid by construction (config authored on-grid;
on-grid arithmetic stays on-grid; loader validates), so it's formatting, not
snapping. Readback / override-detection compares **numerically** (`78 == 78.0`).
This lives in the device adapter (the TCC swap surface) + the poller's compare.

**Status: implemented.** The parameterization landed in the controller (default
`F`, behavior-preserving, dual-reviewed, suite green); the reactive rewrite
inherits it. The F/C question is now a config knob, not a code decision.

### Modes (default OFF)

- **Heat-stretch** — forecast-gated banking ahead of a hot stretch (the *only*
  below-baseline path; bank target validated ≥ comfort floor). Re-enabling needs
  a forecast-fetch helper — **keep one alive** (dead until the mode flips on)
  rather than deleting all forecast plumbing.
- **Stage-1 ramp** — gradual descent to stay in low stage.

### Config (YAML) — no cheap tier, no `runtime.mode`

```yaml
temp_scale: F            # controller unit; config choice (default F whole-degree; C for 0.5° on-grid). Temps below in temp_scale.
comfort_program:
  - {from: "22:00", to: "06:00", cool: 74}   # sleep
  - {from: "06:00", to: "12:00", cool: 76}
  - {from: "12:00", to: "18:00", cool: 78}   # humidity-gated
  - {from: "18:00", to: "22:00", cool: 76}
  heat_floor: 65
  comfort_floor: 70        # loader validates: on the temp_scale grid, and ≥ supervisor floor
flexibility:     {warm_band: 2, spike_extra: 2}
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, extreme_at: 50}
humidity_guard:  {rh_max_pct: 60, action: cap_at_baseline}   # warm-side cap; never below baseline
ceiling:         {operational: 82, hard: 85, humidity_aware: true}
supervisor:      {cool_min: 65, cool_max: 86, emergency_indoor: 86, emergency_target: 74}  # in temp_scale; loader-validated WITHIN a hardcoded absolute backstop (config may only narrow)
modes:           {heat_stretch: {enabled: false, trigger_high_f: 88, bank_to: 72, band_shift: -2}, stage1_ramp: {enabled: false, step: 1}}
```
All temperature values are in `temp_scale` (shown at the `F` default). The unit
is a single config parameter; field names are scale-agnostic, not `_f`-baked.

`SCHEDULER_MODE` (env) is the sole write gate; secrets stay in env.

## Telemetry

- **Decision log = reuse `hvac.actions`** (the existing per-push decision record)
  + the existing `decision_trace` Loki lines — **no new measurement.**
  `hvac.actions` already carries the tier (`price_overlay_tier` tag), the baseline
  (`schedule_cool_f`, repurposed as the comfort baseline), the effective setpoint
  (`effective_cool_f`), the reason, the supervisor decision, and the humidity
  before-state. Reshape it for the reactive model (drop the `day_type` tag; values
  in `temp_scale`). Per-tick reasoning (even when no push fires) is
  already the `decision_trace` eval line. There is no separate daily "decision"
  anymore — the controller decides every tick.
- **Leave `hvac.price_overlay` untouched** — do not inject the new `extreme` tier
  name into its transition rows (its typed consumers know only
  normal/elevated/scarcity); `hvac.actions` carries the full tier.
- **Remove the day-type writers** (`hvac.decisions`, `hvac.precool_window`) —
  artifacts of the removed day-type controller. Their consumers (the OSF-frozen
  analysis pipeline; the cockpit day-type/precool panels, which read the Loki
  `day_type` trace, not the measurement) **migrate to `hvac.actions` / traces** as
  tracked downstream work (2027 analysis-prep + a cockpit PR); the replay
  manifest reason-codes the gap. Not silent deletion, not fake inert rows.
- **`hvac.arm_mode` unchanged** — keeps emitting (watchdog liveness). In
  `production` it tags `off-protocol-production` (correct: commissioning isn't
  the frozen A/B protocol). **`required_feeds_for_arm_mode` must match active
  live inputs** — drop `weather` from required feeds while no enabled mode uses
  it, else an NWS outage mislabels `B-fallback`.
- **5CP** (`pjm_5cp.py`, `hvac.5cp_state`) retained as telemetry-only.

## Runtime / safety

- **`SCHEDULER_MODE` keeps all three values; the controller's operation is
  independent of the arm calendar.** Commissioning = **`production`** (writes
  continuously, `_writes_allowed` never consults the calendar). `experiment` is
  the 2027 layer where the arm calendar gates A/B. `shadow` computes/logs without
  writing. The arm calendar is the *experiment's* tool, kept fully intact and
  untouched.
- **Supervisor** (`validate_setpoints`) gates every write; its **structure is
  reused unchanged**, but its **bounds are `temp_scale` values** (loader-
  validated within a hardcoded absolute backstop; config may only narrow).
  Comfort floor ≥ supervisor clamp floor; the loader validates any bank target
  stays above the floor.
- **Per-tick baseline (supervisor continuity).** The mid-period re-push
  (`_push_layer_change_mid_period`'s indoor-emergency continuity path) short-
  circuits when `last_schedule_cool is None`. Removing the schedule boundaries
  + startup-reconstruction would leave it permanently None → supervisor silently
  off after a restart. So **compute the comfort baseline every tick and feed it
  there**; test block-boundary and mid-block-restart continuity.
- **Shadow validation = the existing harness** (`run_shadow_validation.py` +
  `shadow-validation.yml` + dated `docs/replay-validation/*`), checks extended to
  the reshaped `hvac.actions` decision log. Not a new oracle.
- **Resilience.** Controller down → `hvac.arm_mode` liveness alert → manual
  takeover (in place). The distinct case is **TCC down while away** (manual
  takeover also needs TCC): evaluate **temporary/expiring holds** (the swap's
  timed-hold increment) so a lapsed hold reverts to a sane onboard fallback
  schedule at the device level.
- **Go-active gates:** read-after-write verification (a real write/readback
  round-trip — can't be validated in shadow, where an out-of-range native write
  hard-fails silently); native-unit/range preflight against `aiosomecomfort`
  limits; a container-restart check that `dry_run`/mode is recomputed; SOPS /
  `SCHEDULER_MODE` confirmation.

## Architecture

**In-place, single-path rewrite.** Rewrite the decision/resolve logic in place;
keep the supervisor, the price-overlay state machine, the feed-gap machinery,
the write gate, the telemetry writers, `freshness.py`, **and the full
experiment-scheduling apparatus** (arm calendar, `experiment` mode,
`current_arm_at`, `hvac.arm_mode`) as shared single-copy infra. No parallel path,
no feature flag — `shadow` provides safety isolation, git history is rollback.
Only **Arm B's internal decision logic** (day-types, the four schedules, the
21:00 decision cycle, day-ahead precool, startup baseline reconstruction) is
removed/replaced by the reactive core. **Phase discipline:** delete the day-type
tests in the same slice that removes the code, so every slice lands green (no
multi-PR red-suite window).

## Actuation dependency

The controller cannot write without the TCC path
([swap spec](2026-06-19-control4-to-tcc-swap-design.md), docs-only). The swap
lands first and operates in `temp_scale` (the device set to the same unit), so
the device write is format-only — not a unit conversion. Shadow work doesn't
block on the swap; go-*active* does.

## Scope

**IN:** in-place warm-only reactive controller (comfort program + warm price
tiers + warm band + humidity guard + dog ceiling), `temp_scale` actuation
(device-write format-only), `shadow → production` runtime, YAML config, reshaped
`hvac.actions` decision log, live for 2026 commissioning.

**OUT / removed:** day-types, four schedules, 21:00 decision cycle, day-ahead
precool, startup baseline reconstruction, **vacation override** (Chris doesn't
travel), the **cheap/below-baseline tier** (cut; deferred future idea), full MPC.
**Deferred behind toggles:** heat-stretch (the only below-baseline path),
stage-1 ramp. **Kept (not removed):** the experiment apparatus (arm calendar +
`experiment` mode + `hvac.arm_mode`), `pjm_5cp.py`/`hvac.5cp_state`.

## Open questions → answered by commissioning

1. Warm-band width (is +2°F the right cost/comfort trade?).
2. Heat-stretch banking value on summer streaks (right-sized unit, leaky envelope).
3. Stage-1 descent feasibility + per-degree efficiency.
4. The controller's actual marginal $ value vs a well-set static program under
   the current spike regime — the headline unknown.

## Relationship to the 2027 experiment

This *is* the 2027 Arm-B controller, run full-time now to commission it. **2027
reuses the exact existing experiment design — Arm A vs Arm B, 14-day arms, 48h
washouts — on the kept apparatus** (`SCHEDULER_MODE=experiment`, restore the
alternating calendar with 2027 dates). Arm A = the existing static comparator
(thermostat's own program, per the current design, locked at 2027 prereg). Only
Arm B's internal logic was redesigned. Keeping the scheduler/arm machinery whole
is load-bearing — it's the experiment's backbone, not commissioning scaffolding.
