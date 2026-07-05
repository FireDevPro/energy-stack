---
date: 2026-06-20
revised: 2026-07-03
owner: chris
status: draft (revision 4 — spike-only respec, 2026-07-03)
role-label: code-team
supersedes_intent_of: the now-removed day-type / deep-precool controller model (its docs were deleted in the demolition); revision 4 additionally supersedes revision 3's always-hold normal tier, config schedule copy, and extreme tier
depends_on: Control4->TCC actuation swap (landed; timed holds live since #112)
---

# Commissioning Controller — design spec

## What this is (read first)

This is a **controlled demolition** of the old `hvac_scheduler` and its
replacement with a much simpler controller.

- **Arm A** = a comfortable thermostat program (the static schedule, living
  **on the device**).
- **Arm B** = that same Arm A program **plus price awareness** — the thermostat
  runs its own program untouched, and the controller pushes a *warmer* timed
  hold only while power is expensive. **That is the entire controller.**
  Nothing else.

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

## Revision 4 (2026-07-03) — what changed and why

Grounded in the first night of production holds (2026-07-03: power outage,
zombie hold, and the discovery that the config schedule never matched the
onboard program). Four changes, each a deletion or a narrowing:

1. **Normal tier no longer writes.** Rev 3 re-pushed the comfort baseline as a
   rolling hold 24/7 ("faking the thermostat program"). A temporary hold is the
   device's representation of *deviation from its program*; "hold at the program
   value" is unrepresentable on the device UX and untested via the API — rev 3
   was only expressible because its config baseline was accidentally wrong.
   Holds now exist **only during spikes**.
2. **The device schedule is the single source of truth.** The config copy
   (`comfort_program`) is deleted. Rev 3's copy was a designed "ASHRAE seed"
   that silently diverged from the actual onboard program (morning +1.5 °C,
   sleep/evening +0.5 °C — surfaced only when production holds began). The
   controller now reads the program value live from the device.
3. **Three tiers, not four.** Scarcity becomes an absolute (the old pre-redesign
   "hard override" semantics); the extreme tier is retired — an absolute
   scarcity already means "the warmest tolerable," so a fourth tier had nothing
   left to do. This **visibly retires rev 3's extreme/5CP-tail rationale**
   ("an extreme-price hour carries tail probability of being a 5CP hour"): the
   maximally-aggressive posture survives as scarcity's absolute; 5CP remains
   telemetry-only.
4. **Safety fact #3 (zombie holds) + the alert pair.** Observed live: the CTK04
   hold release is edge-triggered, so a device unpowered at the expiry instant
   carries the expired hold indefinitely. The controller now cleans up after
   itself (and only itself), and controller-down / push-failure conditions
   alert instead of failing silently.
5. **No tier time lock.** Releases follow fresh data (confirmation count +
   hysteresis + stale backstop), not a clock — decided from the first three
   production events, all of which were punished only by lock time (see
   Reactive core → Release policy).

One deliberate supersession of a rev 3 choice: rev 3 allowed a **stale**
high-price sample to drive a warm upgrade. Rev 4 requires **fresh-strict**
price data to engage or upgrade a hold; extension tolerates only the routine
publish sawtooth (**fresh-loose** — see Feed-gap). Without fresh data, holds
lapse and the program runs. Rationale: in spike-only, the do-nothing state is
the safe, correct state; acting on stale data buys little and costs trust.

## How 2026 runs

1. **Shadow, Arm A in control.** The thermostat (Arm A) does the real
   controlling; Arm B runs in **shadow** — it computes and logs what it *would*
   do but never writes. Watch it for several weeks to catch glitches and things
   to fix.
2. **Flip.** Once Arm B looks solid, it takes over and **runs the season** for
   real; Arm A sits in the background. *(Flipped to `production` 2026-07-03
   under rev 3; rev 4 redeploys under the gates below.)*
3. **Review.** End of season: how did it run, did anything break, what to fix.

## Build discipline (binding): reuse, don't reinvent

**Rev 4 supersedes rev 3's extend-in-place discipline with fresh-derivation
(decided 2026-07-05, from the rev 3 post-mortem: extend-in-place let the old
architecture through unexamined).** The new controller is written **from this
spec only**, as new files, with a named **import whitelist** — the only rev 3
code allowed across: `tcc_client.py` (the proven seam), `freshness.py`, the
arm-calendar apparatus (2027 contract), and the Influx writer helpers.
Everything else — tick loop, tier machine with the release policy, hold
lifecycle, config loader — is written new. Copying any other rev 3 code is a
review-rejectable defect ("rev-3 leakage"); the fresh write doubles as this
spec's derivability test. Reuse-don't-reinvent still governs *mechanisms*
(the whitelist); it no longer shields *architecture*.

## Why this shape (the reframe)

1. **Cost-saving is primary; comfort is the constraint, not the goal.** Pure
   cost-minimization is degenerate ("set it as warm as tolerable"); the comfort
   *constraints* make it sensible — the device's own program is the floor it
   never cools below, and scarcity's absolute is the ceiling it never drifts
   above. Cost-first, within the comfort envelope.
2. **The old deep-precool model assumed the wrong house** — right-sized 2-stage
   equipment (stage 1 = 2.35 kW, stage 2 = 3.22 kW; stage 1 never carried a
   descent in 25 d -> **stage 2 = "get colder," stage 1 = "hold"**), high-solar
   envelope (banked coolth leaks before the afternoon peak), cold-sensitive
   occupant (the program floor caps how cold we ever go). Deep early precool is
   unreachable, mistimed, and uninhabitable here.
3. **Cost lives in the evening** — the deep evening pulldown to 73°F was ~60% of
   HVAC cost (stage 2); the afternoon coast was ~5%.
4. **Spikes, not average spread, are the prize, and not weather-forecastable**
   (daily-max-temp vs daily-max-price r = 0.08; spikes to 376¢). So the
   architecture is **reactive, not predictive** — **no weather input, no
   forecasting, no banking** in the controller. The live price signal is the
   only control input.
5. **Summer re-introduces sustained events** (2025 DA-LMP proxy: median 4 h, max
   10 h, evening-clustered; measured 2026-07: the ≥20¢ band ran 5–6 h/day at the
   heat-wave peak) vs shoulder 5-min blips. Spike values are therefore
   **for-hours temperatures**, not worst-case-minutes postures.
6. **The thermostat is the device designed to run a schedule.** The controller
   does not duplicate, shadow, or "fake" the program — it deviates from it,
   warmer, on price, using the device's own deviation primitive (the temporary
   hold), and gets out of the way otherwise.

## Empirical grounds (measured 2026-06 / 2026-07)

| Fact | Value |
|---|---|
| Stage 1 / 2 compressor power | 2.35 / 3.22 kW (1.37×); stage 1 never descends |
| Weather ↔ spike | r = 0.08 (decoupled); max spike 376.5¢ |
| Spike duration | shoulder median 5 min; summer (proxy) median 4 h; ≥20¢ ran 5–6 h/day at 2026-07 heat-wave peak |
| Instrument | ecowitt ch2: 0.18°F, 60 s, at the control point (r=0.92 vs stat) |
| Cooling overshoot | the unit overshoots its setpoint ~1-1.5°F — watch this at the scarcity absolute |
| Hold release is edge-triggered | device unpowered at expiry ⇒ expired hold persists indefinitely (observed 2026-07-03) |
| `ScheduleCoolSp` | device reports its current program cool value in every read, including mid-hold (verified 2026-07-03) |
| ComEd bucket age at tick time | saws ~6.2–11.2 min; the floor **jitters with publish timing** (observed 370–430 s across cycles, 2026-07-05). Rev 3's ≤7-min (420 s) downgrade gate therefore passes only on favorable jitter — nondeterministic releases: 07-04's never cooperated (99-min hold, stale-timer release); 07-05's cooperated once, 14 min after lock expiry. A control gate must sit above the jitter ceiling, not inside the jitter band |

## What the controller does

### The program (the spine) — device-owned, and the floor

The Arm A program lives **on the thermostat and only on the thermostat**. There
is no config copy. The controller learns the current program cool value by
reading `ScheduleCoolSp` from the device snapshot (available even while a hold
is active). **Device reads are scoped to need:** ticks where the price tier is
≥ elevated — or where a persisted own-hold record exists (Safety #3) — read the
device; pure normal-tier ticks never touch it. The idle profile is no reads and
no writes (the poller remains the telemetry reader), preserving the current
code's deliberate no-needless-thermostat-read behavior and keeping TCC
throttle exposure where it is today.

For reference, the onboard program as captured 2026-07-03 (°C, cool):
**Wake 23.0 / Leave 25.5 / Return 24.0 / Sleep 23.0** — these are the true
enforced Arm A values revealed by the 2026-06-02 °C switch (the °F-era
schedule quantized to exactly these grid points). This table is
**informational, not configuration**; editing the program happens on the
device, and the controller follows automatically.

**The device's current program value is the floor — a code invariant.** The
controller NEVER commands below it; an unconditional clamp enforces it, not
just a test. The only direction from the program is *warmer*, on price. Heat is
pinned to a floor (config) on every hold push. If `ScheduleCoolSp` is missing
or unreadable on a tick, the controller **declines to engage or extend**
(fail toward the program).

### Reactive core — spike-only warm holds

**One rule set, 24/7.** No time-of-day levels, no per-block spike config. The
tier state machine (`price_overlay.py`) is retained; the action side changes:

| RTP tier | Action |
|---|---|
| normal | **no writes.** The thermostat runs its own program. |
| elevated (≥ `elevated_at`) | timed hold at `ScheduleCoolSp + elevated_offset` |
| scarcity (≥ `scarcity_at`) | timed hold at `scarcity_absolute` |

**Setpoint rule:**
`effective_cool = clamp(target, floor = ScheduleCoolSp, ceiling = scarcity_absolute)`
where `target` = `ScheduleCoolSp + elevated_offset` (elevated) or
`scarcity_absolute` (scarcity). The clamp floor preserves warm-only; the clamp
ceiling makes `scarcity_absolute` the single cap the controller can ever
command. Every number is config; code owns only the two clamps and the
warm-only direction — no hardcoded setpoints.

**Engage/extend precondition:** if `ScheduleCoolSp` ≥ the tier target, do not
engage or extend — the program is already at or above anything this tier would
command. This one check also neutralizes the clamp's floor/ceiling inversion
(operator warms the program past `scarcity_absolute` on the device) and the
degenerate hold-at-program-value state that rev 4 note #1 declares
unrepresentable.

This restores the **original overlay's hybrid semantics** (pre-redesign code:
elevated = "+3F to active cool setpoint", scarcity = "cool setpoint = 85F,
effective shutoff") with the numbers in yaml and the anchor read live from the
device. The rev 3 extreme tier is retired (see Revision 4 note #3);
`hvac.price_overlay` and the reason-classifier carry **three** tiers.

**Hold lifecycle.** Every hold is timed (`hold_ttl_minutes`, never Permanent).
Four rules:

1. **Engage** (normal -> elevated/scarcity) requires: price data *fresh-strict*
   (below), `ScheduleCoolSp` readable, and the precondition above.
2. **Correct** — on any tick during a live own-hold where the computed target
   differs from the held value (tier moved, program block changed), re-push
   immediately at the new target with a fresh TTL; near-expiry extension is
   the same-value case of this rule. **Warm-only outranks lapse-only:** if the
   held value falls below the current program value (a program step-up
   mid-hold) and no valid warmer target exists, **release the own-hold
   immediately** rather than letting the device cool below program at spike
   prices until lapse. This release is the one unforced write besides pushes.
3. **Extend** (re-push near expiry) continues only while ALL of: tier still
   ≥ elevated (release policy below), price data *fresh-loose* (below),
   humidity guard clear, `ScheduleCoolSp` readable, precondition holds.
4. **Otherwise stop extending** and the hold **lapses on the device** — the
   program resumes with no release write.

**Release policy — no time lock (decided 2026-07-05, from live data).**
Rev 3 locked tiers for a minimum-hold (silently doubled to 60 min by the
TTL coupling at `app.py:1101`); an interim 30/30/60 lock design was also
considered and superseded. The first three production events killed the
lock concept entirely: every observed event was punished *only* by lock
time (a 5-min blip → 99-min hold; a 40-min event → 56 min of ceiling-hold
on ≤8¢ power), the only oscillation in two days of data flapped harmlessly
between adjacent tiers (±1 °C), and the thermal-battery asymmetry means an
"early" release into a dip that re-spikes simply buys cooling at the cheap
price — which is the system's purpose. The rules:

- **Downgrade/release:** fresh-strict data at/below the tier's release
  threshold (trigger − `hysteresis_cents`) for **`release_confirm_buckets`
  consecutive buckets** (seed 2, ≈ 10 min). No time lock. A tier downgrade
  re-targets on the next tick (Correct rule above); a release to normal
  stops extending and the hold lapses (≤ `hold_ttl_minutes` of tail).
- **Upgrade/engage:** fresh-strict, single bucket — deliberately asymmetric
  (respond to real spikes immediately; the cost of a false engage is one
  warm push that the release rule unwinds ~10 min later).
- **Stale backstop:** while a hold is active and no fresh-strict sample has
  arrived for `stale_release_minutes` (seed 30), hard-release — stop
  extending, lapse home. Anti-thrash lives in the hysteresis band plus the
  confirmation count, not in a clock.

**Manual holds.** Outside spikes the controller never writes, so operator
holds survive — a deliberate property of this design. When a spike fires
while a manual hold is active, **price wins, but only warmward**: the
controller overwrites the manual hold only when the tier target is *warmer*
than the currently-held value; a manual hold already warmer than the tier's
command is left alone (it beats the controller's own posture — overwriting it
downward would buy cooling at peak price). An overwritten manual hold does not
return after the spike — the program does. *(Operator-flagged decision;
revisit if it grates in practice.)*

**No below-program path.** No cheap tier (cooling below target when cheap
spends cooling for no payback and runs a cold-sensitive house colder than
wanted); no heat-stretch banking (the floor invariant is absolute).

### Feed-gap behavior — fresh-strict to engage, fresh-loose to extend

Two named freshness predicates, both already implemented — reuse, don't
reinvent:

- **fresh-strict** = bucket age ≤ **12 min** at evaluation time. NOT the
  `freshness.py` 7-min display label: live measurement (2026-07-05) shows the
  ComEd bucket's age at tick time saws between **~7.2 and ~11.2 min**
  (publish lag) — the 7-min label is *never true at tick time*, which is
  exactly how rev 3's freshness-gated downgrade became dead code (see
  Empirical grounds). 12 min = the observed sawtooth ceiling plus margin: true
  whenever the feed is actually flowing, false within one missed publish.
  Required to **engage a new hold or upgrade a tier**. A genuinely stale
  sample never starts or escalates a hold (supersedes rev 3's stale-spike
  upgrade — see Revision 4 note).
- **fresh-loose** = the existing stale-release machinery
  (`PRICE_FEED_STALE_THRESHOLD` and the minimum-hold-then-release timers in
  `app.py`). Governs **extension**: an active hold keeps extending through the
  routine ComEd publish sawtooth (bucket age jitters ~6.2–11.2 min between
  publishes — a tight gate would wrongly lapse holds mid-spike on healthy
  data). "Sustained staleness" is not vague: it is the stale backstop in the
  release policy — no fresh-strict sample for `stale_release_minutes` while
  holding → hard release, extension stops, the hold lapses at its TTL.

In normal tier a stale feed requires no action at all: the do-nothing state is
the safe state. Neither threshold is a new config key — both are the existing
constants.

### Guards

- **Scarcity absolute — the hottest the house may get; ride it.** The
  absolute is the operator's maximum tolerable house temperature (seed 29.5 =
  the long-standing 85°F "effective shutoff"), not a comfort target. During a
  sustained ≥20¢ event the house rides it for the duration (keep the savings;
  ≥20¢ ran 5–6 h/day at the 2026-07 peak), then lapses home when price drops.
  Because the semantic is a *house-temp* max and the unit overshoots its
  setpoint ~1-1.5°F, watch commanded-vs-actual and lower the commanded value
  if the actual house temp exceeds the intent.
- **Humidity guard — one more reason not to extend.** When indoor RH ≥
  `rh_max_pct` (or humidity is missing -> conservative, treat as humid), the
  controller stops extending; the hold lapses and the program resumes — at
  program setpoints the AC cycles and dries the air. Re-engagement requires RH
  < `rh_clear_pct` (hysteresis). **Never below program, no DEHUM** (DEHUM
  pre-cools early — against the goal). Rev 3's effective-layer gating,
  min-hold override, and re-enable machinery are deleted — in lapse-world the
  guard is a condition on extension, not a subsystem.

### Units — `temp_scale`, controller-native, conversion only at analysis

The controller runs **entirely in its native `temp_scale`** (env `TEMP_SCALE`;
parameterization merged in #104). The value is a config choice (C or F); the
device is set to the same unit, so the device write is **format-only**, never a
conversion. No conversion anywhere in the controller — the single unit bridge
lives in the (out-of-scope, being-rebuilt) analysis layer, which normalizes on
read. Telemetry uses **scale-neutral field names + a `unit` tag**. Config values
are authored on the scale's grid (whole for F, 0.5 for C); the loader validates.
`ScheduleCoolSp` arrives in the device's display unit — same unit, no
conversion.

### Config is the experimental surface

Offsets, thresholds, the absolute, guard thresholds, hold TTL — **all config,
not hardcoded**, in `temp_scale`. **Code owns invariants only:** warm-only;
never below the device's current program value; fresh-strict to engage,
fresh-loose to extend; device fail-safe. 2026's "refine the setpoints" happens by editing config, so
config identity must be recorded (see Telemetry) to keep refinements
interpretable.

**The values below are seeds for the first deploy, plus the rationale for
each choice — not maintained contracts.** After first deploy the deployed
yaml is the source of truth, tuned at will; `config_id` provenance in
telemetry records what actually ran and when. Do not PR this spec to change
a number; the spec binds the keys, semantics, and invariants only.

```yaml
temp_scale: C            # or F — config choice. All temps below in temp_scale.
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}
elevated_offset: 1.5     # added to the device's current program cool value (≈ the old +3°F; 1.0 blew past in <1h under heat-wave load)
scarcity_absolute: 29.5  # the hottest the house may get (85°F, the operator's long-standing max); the single cap on all commands. Unit overshoots ~1-1.5°F above commanded — lower this if actual exceeds intent.
heat_floor: 18.5         # heat pinned at/below this on every hold push
humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}  # intent = 65/62 real; CTK04 reads ~4.4 low vs the ch2 reference (263-h comparison, 2026-07-05)
hold_ttl_minutes: 30       # device-hold TTL (lapse horizon); there is NO tier time lock
release_confirm_buckets: 2  # consecutive fresh buckets at/below release threshold to downgrade (~10 min)
stale_release_minutes: 30  # no fresh-strict data while holding for this long -> hard release
```

Removed keys vs rev 3: `comfort_program` (device owns the schedule),
`flexibility.spike_extra` (scarcity is absolute), `ceiling.comfort_max`
(merged into `scarcity_absolute`), `price_tiers_cents.extreme_at` (tier
retired), `modes.stage1_ramp` (was disabled; dead). Tier **release** thresholds
derive as `trigger − hysteresis_cents` (anti-thrash buffer). No hardcoded
numbers in the overlay.

No `cheap` tier, no `runtime.mode` key, no `supervisor` block. `SCHEDULER_MODE`
(env) is the sole write gate; secrets stay in env.

## Safety — device-owned (no software supervisor)

Safety lives in the **device** (which keeps running when the Pi is dead), not in
controller code (which dies with the Pi). Three facts:

1. **The thermostat's own setpoint min/max limits are the hard cap.** No command
   — buggy or not — can be set outside them. This is the load-bearing safety and
   it exists today (a device setting). **`scarcity_absolute` is NOT a safety
   mechanism** — it's a control target; the device limit is the cap.
2. **Holds are timed, never Permanent** (landed in #112) -> a dead/hung
   controller's hold lapses (≤ `hold_ttl_minutes`) and the thermostat reverts
   to its onboard schedule. Under rev 4 this graceful-reversion state is also
   the controller's **normal-tier state** — the failure mode and the idle mode
   are the same state, by construction.
3. **Zombie holds (the power-cycle edge, observed live 2026-07-03).** The CTK04
   hold release is **edge-triggered**: if the device is unpowered at the expiry
   instant, the expired hold persists indefinitely on power restore (it does
   not retro-process the missed release). Bounding facts: #1 still caps it, and
   spike-only means any zombie is a *warm* hold (cheap-direction failure), live
   only during the small fraction of hours holds exist. Mitigations:
   - **Controller self-cleanup — it cleans up after itself, and only itself.**
     The controller persists its last-pushed hold on disk (survives restarts):
     value, until-slot, and **expiry UTC = the floored quarter-hour device slot
     converted to UTC on the push date** (the device slot is dateless
     minutes-since-midnight; the record carries the date). Lifecycle:
     - On any tick where the tier is normal and the device reports an active
       hold **matching the persisted record** with expiry past (+
       `HOLD_CLEANUP_GRACE`, constant, 5 min), release it; the record is
       cleared **only after the release write succeeds** — on failure, retry on
       subsequent normal ticks (the push-failure alert covers persistent
       failure). This matters precisely because zombies are born in power
       events, when TCC may briefly serve cached reads while writes still fail.
     - The record is also cleared on the first tick that observes **no device
       hold, or a non-matching one** — a normally-lapsed hold must not leave a
       stale record that could later match a coincidental manual hold on the
       dateless slot.
     - A hold that doesn't match the record (a manual hold, or no record) is
       never touched.
   - **The down-beacon alert (below) pages the operator for the
     controller-dead branch**, where no controller logic can help.
   - Container restart policy stays `unless-stopped` — a manual stop is a kill
     switch and must stick across reboots (settled 2026-07-03; do not "fix").

Prerequisites (operational checklist): the thermostat's setpoint min/max are
set appropriately; **the CTK04 onboard program is a known-safe cool schedule,
captured as an authoritative current snapshot** — under rev 4 it is not merely
the fallback but the *primary* schedule (current capture: this spec's
informational table + `docs/THERMOSTAT_ARM_A_TCC_SCREENSHOT_2026-05-26.png`
predates the °C switch; refresh it). `aiosomecomfort` exposes the time-based
hold.

## Telemetry

- **Decision log = reshape `hvac.actions` in place** (no new measurement) + the
  `decision_trace` Loki lines:
  - **scale-neutral field names + a `unit` tag**;
  - carry tier, **observed program value (`ScheduleCoolSp`)**, commanded
    setpoint, and actual indoor temp side by side (watch the
    absolute-vs-actual overshoot), humidity-guard state, hold expiry;
  - normal tier emits **traces, not writes** — the per-tick decision trace
    continues 24/7 so "controller alive and deciding" stays observable even
    when it never touches the device;
  - **the `applied` / `error` / `dry_run` fields survive the reshape** —
    telegram-notifier's existing action-error alert filters on `error` and
    thermostat-poller's `fetch_last_action` filters on `dry_run`; dropping them
    silently kills a live alert.
- **thermostat-poller's override detection is retired.** It compares device
  setpoints to the most recent `hvac.actions` row; under spike-only that row
  is days old or absent, and manual holds are now first-class operator action,
  not "overrides." Remove the comparison (and its `override_detected` field)
  rather than leaving it to emit nonsense.
- **Config provenance:** log the config file path + SHA256 at startup, and tag
  the decision rows / trace with a `config_id`; archive deployed config
  snapshots with effective dates.
- **`hvac.price_overlay` carries three tiers** (extreme retired — observers
  note the enum change).
- **`hvac.arm_mode` keeps emitting** (watchdog liveness — unchanged; liveness
  never depended on holds). `required_feeds_for_arm_mode` derived from the
  enabled-mode set, unchanged.
- **Alerting (new, closes 2026-07-03 gaps):**
  - **Down-beacon alert:** telegram-notifier alerts when the watchdog's
    `hvac.heartbeat controller_alive=false` beacon appears (controller dead >
    threshold). On 2026-07-03 the controller was down 2 h with zero
    notification while the beacon wrote faithfully — detection existed,
    consumption didn't.
  - **Push-failure alert:** a failed hold push/extension (spike engage
    included) alerts after `PUSH_FAILURE_ALERT_N` consecutive failures
    (constant, seed 3) — a missed engage is a silently missed spike, the
    system's one job. This **extends** telegram-notifier's existing
    `check_hvac_action_errors` (which already watches `hvac.actions.error`);
    it is not a new alert built from scratch.

## Runtime

- **`SCHEDULER_MODE`** is the write gate: `shadow` (compute/log, never write) ->
  `production` (write for the season). `experiment` (arm-gated) is the 2027
  layer, untouched. The zombie self-cleanup release is a write and obeys the
  gate.
- **Scoped device reads.** Spike-tier ticks (and normal ticks holding a
  cleanup record) read the device snapshot (`ScheduleCoolSp` + hold state);
  pure normal-tier ticks read nothing — matching the current code's deliberate
  no-needless-thermostat-read short-circuit. No startup baseline push, no
  startup reconstruction — a restarted controller in normal tier writes
  nothing; in a spike tier it engages on its first tick from live data.
- **Shadow validation for rev 4** = watch the would-engage/extend/lapse traces
  across real spikes for a short window (days, not weeks — the spike machinery
  is unchanged and already production-proven; what's new is mostly deletion),
  plus sanity checks: floor never violated, setpoints on-grid, no writes traced
  in normal tier.

## Go-active (rev 4 redeploy gates)

**Ordering:** gates 1–4 require production writes, so the sequence is: SOPS /
`SCHEDULER_MODE` confirmation -> flip to `production` **with the operator
watching** -> gates 1–4 run as the first-production-day checklist. Forcing a
spike without waiting for the market: temporarily lower `elevated_at` in
config (config-as-surface — no code, and `config_id` records the epoch).

1. **Clean kill test on real hardware** — push a spike hold, stop the
   controller, watch the device drop the hold at expiry and resume its program
   unaided. *(The 2026-07-03 attempt is VOID — the outage unpowered the device
   across the expiry instant; that run instead discovered safety fact #3. The
   test must be re-run clean, with its restore step parked on the Pi — e.g.
   `at`-scheduled `docker compose start` — not in an agent session.)*
2. Spike-hold round-trip readback (engage on a real elevated tick, confirm
   device state and lapse).
3. Zombie self-cleanup: matching/lifecycle logic covered by tests, and the
   record-clearing path (normal lapse observed -> record cleared) verified
   live. The true zombie-release path requires a power cut across an expiry —
   verify opportunistically, or optionally by cutting thermostat power across
   a short-TTL hold's expiry.
4. Alert pair live-fired once (beacon + push-failure).

## Architecture

**In-repo fresh-write, staged beside the running controller.** New modules
land as fresh files in `deploy/energy-stack/hvac_scheduler/`, NOT wired into
the container entrypoint — rev 3 keeps running the season through every WIP
merge (deploys bounce it harmlessly). The **cutover slice** swaps the
entrypoint to the new controller and deletes the rev 3 modules **and their
tests in the same PR** (phase discipline). Whitelist imports only (see Build
discipline); rev-3 leakage is an explicit review dimension on every task PR.
Both `tcc_client.py` copies (documented verbatim duplicates in
`hvac_scheduler` and `thermostat_poller`) gain `ScheduleCoolSp` and
`TemporaryHoldUntilTime` through the ClimateDevice seam. Removed at cutover:
the normal-tier push path, the config `comfort_program` + per-tick config
baseline, the extreme tier, the effective-layer humidity gating, the poller's
override detection. Added: the persisted own-hold record + normal-tier
cleanup, the two alerts. `shadow` is the isolation; git history is rollback.

## Scope

**IN:** spike-only warm-hold controller (3 tiers; device-read anchor; hybrid
offset+absolute; fresh-strict/fresh-loose feed gates; humidity stop-extend;
timed holds, lapse-home),
device-owned safety (min/max + timed holds + zombie self-cleanup),
`temp_scale` actuation (format-only), config-as-surface + provenance, alert
pair, `shadow -> production` runtime, reshaped `hvac.actions`, live for 2026
commissioning.

**OUT / removed:** the always-hold normal tier and its config schedule copy,
the extreme tier, day-types, four schedules, 21:00 cycle, day-ahead precool,
startup reconstruction, **the software safety supervisor**, below-program
cooling (cheap tier + heat-stretch banking), DEHUM, predictive control,
**cost/savings measurement (that is 2027)**. Observer rebuilds (cockpit, trace,
analysis) are tracked but out of scope here.
