---
date: 2026-07-07
owner: chris
status: draft
role-label: code-team
supersedes_intent_of: the rev-3 narrative cockpit (decision-pipeline board) at deploy/energy-stack/cockpit/, which went blind at the 2026-07-06 rev 4 cutover
---

# Cockpit rev 4 — "The Vigil" design

## What this is

A rebuilt read-only wall dashboard for the **rev 4.1 spike-only HVAC
controller**, replacing the rev-3 narrative cockpit. It runs on the wall-mounted
MS Surface as one of several swipe-to boards (it does **not** replace the weather
station). Same container and URL as today (`cockpit`, `:8765`); the backend data
layer and the frontend are rebuilt.

**Division of labor (important):** this spec defines the **data contract**, the
**information priorities**, and a **design brief**. It does **not** define
look/feel, layout, color, or typography — those are owned by Claude Design,
which will read this repo to understand how things connect and will explore
visual variations against the endpoints defined here. Everything in the "Design
brief" section below is *intent for Claude Design to interpret*, not a
prescribed layout.

## Design brief (intent only — Claude Design owns the visuals)

The organizing metaphor is **"The Vigil":** a calm, always-on watchman, not a
busy pipeline. Rev 4.1 is asleep most of the time — at normal prices the
thermostat runs its own program and the controller writes nothing — so the
dashboard's honest default state is restful. The guiding principle:
**boring when the system is boring, earned drama only when something real
happens.**

Two moods the design should express (however it chooses to):

- **Resting** (normal tier, ~90% of the time): calm; conveys "all's well, money
  is safe, I'm watching." The hero is the current price shown *against its tier
  thresholds*, so a viewer sees how much headroom exists before a spike.
- **Engaged** (elevated/scarcity, a live spike): the board wakes up; the current
  intervention becomes the story — what tier, what setpoint it's holding, how
  the house is coasting (compressor off, indoor temp drifting toward the
  target), how long until the next price re-check, and a running estimate of
  what the event is avoiding in cost. The end of a spike (active release back to
  the program) is a moment worth making legible — it is the rev-4.1 signature.

Claude Design may offer multiple looks for any of this. The spec's job is only
to guarantee every visual idea has real data underneath it.

## Constraints

- **Always-on wall kiosk**, glanceable from across a room; one of several
  swipe-to dashboards on the Surface.
- **Single screen, no internal navigation** — swiping away is handled by the
  Surface's dashboard layer, not by tabs/routes inside the Vigil.
- **Read-only.** No control actions, no writes to the controller or device.
- **Polling client** — no websockets required; the page polls the endpoints
  below on a fixed cadence.

## Information priorities (ranked — *what* matters, not *where* it goes)

1. **Hero:** current ComEd RTP price relative to its tier thresholds (elevated
   10¢, scarcity 20¢).
2. **Always visible (controller context):** indoor temp; the setpoint currently
   in effect; indoor humidity relative to the RH-guard line; outdoor temp +
   humidity; compressor on-vs-coasting; controller liveness.
3. **Spike-only (engaged):** hold details (tier, commanded setpoint, time held,
   time to next re-check), the coast (indoor temp climbing toward target),
   estimated avoided cost, and the tier-ladder trace of the current spike.
4. **Ambient history:** a 24-hour timeline (price with tier bands, hold periods,
   spike markers) and a short list of recent spike events.

## Data contract

The backend exposes three JSON endpoints under `/api`. All timestamps are
RFC3339 UTC plus a parallel `_ct` field in America/Chicago for display. All
temperatures are °F for display (the controller runs °C natively; the backend
converts once, at the read boundary — the frontend never converts). Every field
below names its live rev-4 source measurement/field.

### `GET /api/vigil/now` — the live-state poll (client cadence ~15 s)

The heart of the board: one composite object recomputed server-side per request.

```jsonc
{
  "as_of": { "utc": "...", "ct": "..." },

  "price": {                         // comed.prices, period_type="5min"
    "cents": 3.4,                    //   latest bucket value
    "bucket_time": "...",            //   bucket _time
    "age_sec": 410,                  //   now - bucket_time
    "fresh": true,                   //   age <= 720s (controller fresh-strict)
    "hourly_avg_cents": 6.1          //   period_type="hourly_avg" latest
  },

  "tier": {                          // thresholds from the controller config
    "current": "normal",             //   normal|elevated|scarcity
    "elevated_at": 10.0,             //   config price_tiers_cents.elevated_at
    "scarcity_at": 20.0,             //   config .scarcity_at
    "hysteresis_cents": 2.0,         //   config .hysteresis_cents
    "elevated_release": 8.0,         //   elevated_at - hysteresis
    "scarcity_release": 18.0         //   scarcity_at - hysteresis
  },

  "posture": "resting",              // resting | engaged
                                     //   engaged iff tier != normal OR own-hold active

  "why": "Cheap power, 3.4¢ — thermostat's running its own schedule.",
                                     //   server-composed (see "Why-line" below)

  "thermostat": {                    // hvac.thermostat, latest
    "indoor_temp_f": 74.2,           //   indoor_temp_f_hires (fallback indoor_temp_f)
    "cool_setpoint_f": 73.4,         //   cool_setpoint_f
    "hold_mode": "Off",              //   hold_mode ("Off"|"Hold Until"|"Permanent")
    "compressor_on": false,          //   hvac_state == "Cool"
    "fan_mode": "Follow Schedule",   //   fan_mode
    "sample_age_sec": 220
  },

  "humidity_guard": {                // indoor RH vs the controller's RH gate
    "indoor_rh_pct": 44.0,           //   hvac.thermostat humidity_pct
    "rh_max_pct": 61.0,              //   config humidity_guard.rh_max_pct
    "rh_clear_pct": 58.0,            //   config .rh_clear_pct
    "gated": false                   //   latest hvac.actions humidity_gated (0/1)
  },

  "outdoor": {                       // ecowitt.weather, canonical ch1 (shaded N/E)
    "temp_f": 88.5,                  //   ch1_temp_f
    "rh_pct": 39.0,                  //   ch1_rh_pct
    "dewpoint_f": 60.1,              //   ch1_dewpoint_f
    "sample_age_sec": 45
  },

  "hold": null,                      // null when resting; object when engaged:
  //  {                              // from latest rev-4 hvac.actions row + own-hold expiry
  //    "tier": "scarcity",
  //    "commanded_cool_f": 85.1,    //   commanded_cool (°C) -> °F
  //    "schedule_cool_f": 77.9,     //   schedule_cool (the program value it sits above)
  //    "pushed_at": "...",          //   last applied action _time
  //    "expires_at": "...",         //   hold_expires_at
  //    "minutes_held": 22,
  //    "minutes_to_expiry": 8,      //   hold_expires_at - now (device TTL horizon;
  //                                 //   the controller re-evaluates every tick and may
  //                                 //   release sooner on confirmed-cheap price — this is
  //                                 //   the max time before the hold lapses on its own)
  //    "coasting": true             //   compressor_on == false while holding
  //  }

  "this_spike": null,                // null when resting; object when engaged:
  //  {                              // from hvac.price_overlay transitions since spike start
  //    "started_at": "...",
  //    "tiers_walked": [ {"tier":"elevated","at":"..."},
  //                      {"tier":"scarcity","at":"..."} ],
  //    "peak_cents": 94.1,
  //    "est_avoided_cost_usd": 0.42 // ESTIMATE, label "est." (see below)
  //  }

  "liveness": {                      // hvac.arm_mode recency + watchdog beacon
    "alive": true,
    "last_tick_age_sec": 14,         //   now - latest hvac.arm_mode _time
    "watchdog_down": false           //   any hvac.heartbeat controller_alive=false in last 10m
  },

  "controller": {
    "mode": "production",            //   from latest hvac.arm_mode scheduler_mode tag
    "config_id": "58542630…"         //   latest hvac.actions config_id (sha256 short)
  }
}
```

### `GET /api/vigil/timeline?hours=24` — the day ribbon (client cadence ~2 min)

```jsonc
{
  "hours": 24,
  "thresholds": { "elevated_at": 10.0, "scarcity_at": 20.0 },
  "price_series": [ { "t": "...", "cents": 3.4 }, … ],   // comed.prices 5-min
  "tier_bands":   [ { "from": "...", "to": "...", "tier": "scarcity" }, … ],
                                                          // derived from price_overlay transitions
  "holds":        [ { "from": "...", "to": "...", "tier": "elevated",
                      "commanded_cool_f": 78.8 }, … ],    // from hvac.actions push/release pairs
  "indoor_series":  [ { "t": "...", "temp_f": 74.1 }, … ],// hvac.thermostat (optional overlay)
  "outdoor_series": [ { "t": "...", "temp_f": 88.0 }, … ] // ecowitt ch1 (optional overlay)
}
```

### `GET /api/vigil/events?limit=10` — recent spike episodes (client cadence ~2 min)

Each object is one engage→release episode, newest first.

```jsonc
{
  "events": [
    {
      "started_at": "...", "ended_at": "...",
      "duration_min": 41,
      "peak_cents": 94.1,
      "tiers_walked": ["elevated","scarcity","elevated"],
      "resolution": "released",         // "released" (active) | "lapsed" (TTL) | "ongoing"
      "est_avoided_cost_usd": 0.60      // ESTIMATE, label "est."
    }, …
  ]
}
```

## Why-line composition (server-side)

`why` is a single plain-English sentence composed from current state (Influx
only — no Loki dependency for the live poll). Cases:

- **Normal, no hold:** `"Cheap power, {cents}¢ — thermostat's running its own schedule."`
- **Normal, own-hold lapsing (just released/expiring):** `"Spike over — releasing back to your schedule."`
- **Elevated:** `"Elevated power, {cents}¢ — holding {commanded}°, ~{minutes_to_expiry} min left on this hold."`
- **Scarcity:** `"SCARCITY, {cents}¢ — holding {commanded}°, riding the thermal battery, ~{minutes_to_expiry} min left on this hold."`
- **Humidity-gated:** append `" (humidity gate: cooling to dry the air)"`.
- **Stale feed / controller down:** liveness surfaces this; `why` states `"Price feed stale — standing down to your schedule."` or `"⚠ Controller not responding — thermostat is on its own program."`

## Avoided-cost estimate (v1: deliberately simple, always labeled "est.")

Not billing truth. v1 formula: for the minutes of a hold where the compressor
was **off** (`hvac_state != "Cool"`) while price was ≥ elevated, estimate the
cooling that *would* have run at the program setpoint and multiply by price:

```
est_avoided_usd  =  (compressor_off_minutes_at_elevated+ / 60)
                    × ASSUMED_KW            // stage-appropriate, from the spec (2.35 / 3.22 kW); pick one constant for v1
                    × (price_cents / 100)
```

`ASSUMED_KW` and the "would-have-run fraction" are single constants in v1 (no
thermal model); the number is always rendered with an "est." qualifier. Refining
this is explicitly out of scope for v1.

## Backend architecture (rebuild in place)

Scope is `deploy/energy-stack/cockpit/` and its compose block only (per the
cockpit scope boundary). Reuse the plumbing, replace the query layer and
frontend.

- **Keep:** `app.py` (FastAPI wiring, same-origin static serve), `influx.py`
  (Flux access), `freshness.py` (feed-health), the container, the compose
  service, the deploy wiring.
- **Rewrite** the query layer as three focused modules — `vigil_now.py`,
  `vigil_timeline.py`, `vigil_events.py` — backing the three endpoints. Live
  state comes **from InfluxDB only** (`comed.prices`, `hvac.thermostat`,
  `hvac.actions`, `hvac.price_overlay`, `hvac.arm_mode`, `hvac.heartbeat`,
  `ecowitt.weather`); `decision_trace.rev4_tick` in Loki is **not** required for
  the live poll (the why-line is derived from Influx state), keeping a single
  data source for `/now`.
- **Delete** the rev-3 modules whose telemetry is dead: `day_ahead.py`
  (day-types), `schedules.py` (fixed schedules), `snapshot.py`,
  `today_actions.py`, `day_at_a_glance.py`, `loki.py` (unless a later view needs
  it), and the rev-3 frontend components (`DecisionPipeline.tsx`,
  `DayAheadPanel.tsx`, `WhyThisDecision.tsx`, `DayAtAGlance.tsx`, the old
  narrative shell).
- **Frontend:** rebuilt new (not patched) by Claude Design against the three
  endpoints above; the repo is readable to it for wiring context.
- **Tier thresholds:** to keep one source of truth (no config duplication — the
  drift failure this project already paid for), the cockpit reads the
  controller's `commissioning-controller.yaml` thresholds. Simplest: add a
  read-only mount of that file into the cockpit compose block and parse
  `price_tiers_cents` + `humidity_guard` at startup. **(Flag: this references a
  scheduler-owned file from the cockpit compose block — confirm the boundary is
  acceptable, else fall back to a small cockpit-side config kept in sync.)**

## Out of scope / non-goals

- Look/feel, layout, color, typography, animation — **owned by Claude Design.**
- History scrubbing / date-range navigation (the timeline is a fixed rolling
  window; deeper history lives in Grafana).
- Billing-accurate cost (avoided-cost is a labeled estimate).
- Any write path to the controller or thermostat (read-only board).
- Replacing the weather station (independent swipe-to board).

## Success criteria

- All three endpoints return live, correct rev-4 data on the Pi (no rev-3
  measurement reads remain; no dead-measurement nulls in the resting view).
- The resting view is fully populated at normal prices (price, tier headroom,
  indoor/outdoor/humidity, compressor state, liveness) — nothing blank.
- During a real (or forced-low-threshold) spike, `/now` returns a populated
  `hold` + `this_spike`, and the day ribbon/events show the episode.
- Controller-down and stale-feed states are visibly distinguishable from a
  healthy resting state.
- Runs as a swipe-to board on the Surface without interaction.
