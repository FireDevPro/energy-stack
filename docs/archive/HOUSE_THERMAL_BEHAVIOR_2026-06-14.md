---
date: 2026-06-14
owner: chris
status: exploratory analysis
role-label: analysis
name: house-thermal-behavior-2026-06-14
---

# House Thermal Behavior & Schedule Evaluation — Cooling Season Cut (2026-06-14)

## Purpose

Empirical, plain-language characterization of **how the house actually behaves** under the
current cooling schedule, sliced by day type, and a first look at the question:
**does the setback schedule (73 night / 78 afternoon) make sense, or would holding a
steadier band (e.g. 75-76 round the clock) cost less?** Also records the result of the
original ask — whether the telemetry supports a **solar model of the house**.

This is **observational and read-only**. It does not touch the scheduler and is **not** the
pre-registered experiment analysis. The data window straddles the pre-experiment period and
the start of the Arm A washout (the thermostat ran its CTK04 program throughout), so it
describes the **Arm A schedule's behavior**. The steady-hold idea below is a post-experiment
question, not a mid-study change (parameters are locked at the OSF freeze).

Successor in spirit to [`THERMAL_ROUGH_CUT_2026-05-26.md`](THERMAL_ROUGH_CUT_2026-05-26.md);
related plan [`archive/THERMAL_MODEL_DESIGN.md`](archive/THERMAL_MODEL_DESIGN.md).

## Data & method

- **Window:** 2026-05-27 → 2026-06-14 (19 days), bound by the start of `indoor_temp_f_hires`.
- **Sources (Influx, all canonical fields):** `hvac.thermostat` (`indoor_temp_f_hires`,
  `cool_setpoint_f`), `ecowitt.weather` (`ch1_temp_f`, `solar_wm2` — `ch1` is canonical
  outdoor; `outdoor_*` is a stale alias and was avoided), `hvac.comfortnet`
  (`cool_actual_pct` → stage), `refoss.channel` (`power_w` on em:2+em:8 compressor legs +
  em:9 blower), `comed.prices` (`price_cents_per_kwh`). All times Central (UTC-5),
  validated against the dawn-minimum / afternoon-peak shape of outdoor temp.
- **Day types** by daily peak outdoor temp: **cool ≤80°F (n=3)**, **warm 81-88°F (n=11)**,
  **hot ≥89°F (n=6)**. Solar coverage spanned heavy overcast (peak 178 W/m²) to brilliant
  clear (1,269 W/m²), so clearness varies within each band.
- **Stage:** off (<5% cool demand), stage 1 (5-75%), stage 2 (≥75%).

### Data-quality notes (read before trusting fine detail)

1. **Indoor sensor spikes — known, handled.** `indoor_temp_f_hires` carried a handful of
   impossible single-sample values (120.2°F, 23.0°F) caused by the failed attempt to switch
   the Control4 driver to Celsius (the driver mishandles C). Filtered out (`50 < x < 95`).
2. **Effective indoor resolution is ~0.5°C (~0.9°F), not the 0.18°F** the poller comment
   claims. In practice the thermostat reports in half-degree-C steps, so the indoor trace
   moves in ~0.9°F jumps and sits flat between them. Fine for the multi-hour, multi-degree
   questions in this report; **fatal for instantaneous-slope fitting** (see Solar section).

## The schedule the house actually ran

Median cooling setpoint by hour (CT):

| Block | Hours (CT) | Setpoint |
|---|---|---|
| Overnight / morning | 12am – 12pm | **73°F** |
| Afternoon setback | ~1pm – 6pm | **78°F** |
| Early evening | ~7pm – 9pm | **75°F** |
| Late evening | ~10pm – 12am | **73°F** |

This is a **peak-avoidance** design: hold cool overnight and morning (cheap power), let the
house coast warm through the expensive afternoon, then pull back down in the evening.

## Your questions, answered

### 1. How much / how fast does the house heat up vs what the AC holds?

During the **1pm-6pm setback to 78°F, the house never reaches 78** — it floats up on its own
(AC off) and tops out at **~76°F on warm days, ~78°F only on the hottest days**, climbing at
roughly **0.9°F/hour** from its midday 73-74°F. So on most days the 78 ceiling is never
binding; the house simply isn't going to get that hot before the sun comes off it.

### 2. How long to come down to the 73°F sleep setpoint? (and staging)

Measured from the 8pm pulldown to the first time indoor reaches ≤73.5°F:

| Day type | Recovery time | Reaches 73 by | Staging during recovery |
|---|---|---|---|
| Cool | ~2.2 h | ~10:20pm | **mix** (≈35 min stg1 / 45 min stg2) |
| Warm | ~2.7 h | ~10:40pm | **all stage 2** (~170 min) |
| Hot | **~5.0 h** | **~1:50am** | **all stage 2** (~310 min) |

On warm and hot evenings the pulldown is the compressor at **full tilt (stage 2) the entire
time** — stage 1 barely participates. On hot days the house **doesn't reach the sleep
setpoint until ~2am**.

### 3. Night cool-down, coast, and creep-up

- **Cool nights:** after the pulldown the house keeps drifting and **overshoots to ~70.7°F**
  (below the 73 target) with the AC off — it has somewhere cold to go.
- **Warm nights:** settles at ~73°F and **holds flat with the AC off** once outdoor drops
  into the 60s.
- **Hot nights:** still ~76-77°F at midnight, AC running 100% — no free coast.
- **Creep-up:** there is no plateau during the afternoon setback — once released to 78 the
  house starts rising immediately at ~0.9°F/hr (this *is* the solar/envelope gain, visible
  behaviorally).

### 4. The 2am retained-heat question

**Yes on hot days, no otherwise.** On hot days indoor is still **+3°F above target at
11pm-midnight** and the house sits **warmer than the outdoor air** late at night (e.g. ~76°F
indoor vs ~74°F out at midnight) — clear retained heat / thermal lag, with the AC working to
shed it. On cool and warm nights the house is at or below target well before midnight.

### 5. Stage 1 vs stage 2 overall

Median cooling runtime per day climbs steeply with heat, and **stage 2 dominates**:

| Day type | Stage 1 | Stage 2 | Total cooling |
|---|---|---|---|
| Cool | 110 min | 190 min | ~5.0 h |
| Warm | 120 min | 360 min | ~8.0 h |
| Hot | 150 min | **540 min** | ~11.5 h |

## Where the money goes

HVAC supply cost by time of day (share of total HVAC energy cost across the window, priced at
ComEd hourly rates):

| Window (CT) | Share of HVAC cost |
|---|---|
| Overnight 12-6am | 17% |
| Morning 6-10am | 0.5% |
| Midday 10am-2pm | 18% |
| **Peak 2-7pm (setback)** | **4.7%** |
| **Evening 7pm-12am** | **60%** |

Two things stand out:

1. **The setback works.** Only **4.7%** of HVAC cost falls in the 2-7pm peak, even though
   ComEd prices are highest then (peaking ~6¢/kWh at 7pm vs ~2¢ overnight). The house coasts
   through peak almost for free.
2. **The bill is the evening pulldown.** ~**60%** of HVAC cost is the 7pm-midnight recovery
   to 73°F — a lot of stage-2 energy, landing while prices are still elevated (~4-6¢) on the
   way down from the 7pm peak. The midday hold (18%) and overnight hold (17%) are the rest.

*(Absolute supply cost over the window is small — order $25-30 of ComEd hourly energy.
Delivery and PJM capacity/5CP charges are separate and larger; this section is the supply
component and, more importantly, the **timing**, which is what a price-aware schedule moves.)*

## Does the setback schedule make sense? Would a steady 75-76 hold be cheaper?

**What the data clearly supports:**
- The afternoon setback is doing real work: it keeps the expensive 2-7pm window nearly
  cooling-free, and the house genuinely doesn't overheat (tops ~76°F most days) doing it.
- The expensive part of the current schedule is the **deep evening pulldown to 73°F**, not
  the daytime behavior.

**What we cannot yet conclude (the honest non-finding):** whether a steady 75-76°F hold beats
it is a **counterfactual we never ran**, and the traces argue *both* ways:
- *For steady hold:* it would skip most of the 60%-of-cost evening pulldown, and the house
  already floats around 76°F midday, so a 76 ceiling would stay nearly free during peak.
- *Against steady hold:* the house only coasts to 76°F during peak *because* it was chilled
  to 73°F all morning and rides on thermal mass. Starting the afternoon at 75-76°F could push
  it higher and **force cooling into the 2-7pm peak** the current schedule avoids.

Settling this needs either a fitted thermal model (to simulate the steady-hold day) or a
deliberate A/B of the two strategies on matched day types. The strongest single lever the
data points to: **if sleep comfort tolerates ~75°F instead of 73°F, you avoid the biggest,
priciest chunk of the day's cooling** — that is the experiment worth running.

## The solar model (original ask)

Two parts were requested: (a) solar **gain** — how much the sun heats the house — and (b)
solar **resource** — characterizing the irradiance signal itself.

- **(b) Resource: yes, we have it.** `solar_wm2` is clean and rich — 35 days spanning
  178→1,269 W/m², full overcast-to-clear range. A clear-sky/clearness characterization is
  straightforward whenever wanted.
- **(a) Gain: NOT fittable from this telemetry as-is** — and the reason is not day-type
  variety (we have plenty now). Running the existing `thermal_observer` fit produced a
  **rejected, physically impossible** result (negative solar coupling, inverted stages,
  worse-than-persistence skill). Diagnosis:
  1. **Indoor signal can't resolve passive drift.** At ~0.9°F sensor steps, a well-insulated
     house's drift (~0.1°F per 10-min step) is below the resolution floor, so AC-off slopes
     read **exactly zero** almost everywhere. Confirmed: one overnight window moved a single
     0.9°F step in 4.5 hours.
  2. **Solar and outdoor temperature are collinear (+0.69)** — sun and heat arrive together,
     so a simple regression can't separate "sun heated it" from "warm air heated it."
  3. (Plus the indoor spikes above, now filtered.)

  **Net:** we cannot put a credible °F-per-W/m² number on solar gain from the current data.
  The black-envelope solar load is real and **visible behaviorally** (the ~0.9°F/hr afternoon
  creep with the AC off), but quantifying it needs either a coarser-timescale drift method
  (regress temperature change over multi-hour spans, salvaging the rare resolvable steps) or
  the planned grey-box RC fit — both post-experiment.

## Significant findings & non-findings

**Findings**
- Schedule is peak-avoidance and the **setback works**: 2-7pm peak = only 4.7% of HVAC cost.
- **60% of HVAC cost is the evening pulldown to 73°F**, run almost entirely at stage 2.
- **Hot days don't reach the 73°F sleep target until ~2am** (~5h of stage-2 recovery); the
  house retains heat and sits warmer than outdoors late at night.
- **The 78°F afternoon ceiling is essentially never reached** (house tops ~76°F most days).
- Cool nights **overshoot below 73°F** unaided; warm nights hold flat with AC off.
- Stage-2 runtime scales steeply with heat (190→360→540 min/day cool→warm→hot).

**Non-findings / limits**
- **No quantitative solar-gain coefficient** is obtainable from current telemetry (sensor
  resolution + solar/temp collinearity), independent of how many day types we collect.
- **Steady-hold vs setback cost is unresolved** — a counterfactual needing a model or a
  matched A/B, not readable from observed data.
- **Indoor `*_hires` resolution is ~0.5°C in practice**, not the documented 0.18°F; the
  poller comment overstates it. Worth a doc correction in `thermostat_poller`.
- 19 days, single cooling season, single occupant; cool-day bucket is thin (n=3).

## Suggested next steps (not started)

1. If the steady-hold question matters: scope a matched-day A/B (post-experiment) or revive
   the grey-box RC fit with a multi-hour drift method that tolerates the 0.9°F steps.
2. Quick doc fix: correct the `indoor_temp_f_hires` resolution claim in the poller header.
3. The (b) solar-resource characterization is cheap and unblocked if it's independently useful.
