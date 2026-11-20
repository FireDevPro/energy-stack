---
date: 2026-11-19
owner: chris
status: active
role-label: analysis
name: thermal-model-2026-11
related:
  - docs/THERMAL_ROUGH_CUT_2026-05-26.md
  - docs/archive/HOUSE_THERMAL_BEHAVIOR_2026-06-14.md
  - tools/thermal_model/README.md
---

# Thermal Model: First Credible Fit + Steady-Hold Counterfactual (2026-11-19)

## Purpose

1. Is there now enough data for a real thermal model? The May rough cut and the
   June behavior study both failed to fit one.
2. If so, use it on the question June left open: **would a steady 75–76°F hold
   cost less than the afternoon setback program?**

Read-only analysis. No controller or config change. The 2027 Arm A vs Arm B
experiment is still the authoritative cost test. Code: [`tools/thermal_model/`](../tools/thermal_model/README.md).

## Answer in brief

- **Yes, the data supports a model, within stated limits.** A one-node RC
  model predicts house temperature a day ahead with 0.85°F error on held-out
  September, against 1.9°F for the "temperature stays where it started"
  baseline. The envelope looks the same in June, July and August (~40 h time
  constant).
- **Energy, out of sample:** fit on three months and predict the fourth, and
  season energy comes out within 0.4%. Monthly errors are −8% to +32% and
  partly cancel. The model **under-predicts hot-afternoon heat** (peak ~1°F
  low) and **energy in the most expensive hours by ~30%**. See
  [Independent validation](#independent-validation).
- **Costs use your actual ComEd DTOD delivery rates** (Mid-Day Peak 1–7pm
  11.85¢ vs 3.3–4.5¢ otherwise, from the ingested bills) plus hourly supply.
  An earlier version used a flat 7¢, which understated the value of timing.
- **The setback program beats a steady hold by a wide margin.** Steady 75°F
  costs **55–66% more**, because it puts ~1,500–1,700 kWh into the 1–7pm DTOD
  peak versus the program's ~85.
- **The system you actually run is program + controller, and the controller
  is the bigger lever.** Emulated from its own code and checked against its
  action log (97% hold-timing agreement), the controller cuts the 12-week HVAC
  bill from ~$227–231 to **~$177 (−22 to −23%)**. See
  [Schedule × controller](#schedule--controller-four-way-comparison).
- **A gentler pre-cool baseline gives the controller a better starting point.**
  With the controller on, a 3-period program (24.0°C night → **23.0°C from 3am
  → 25.5°C from 1pm**) is cost-neutral to slightly cheaper (−$4 / 12 weeks).
  It cuts hours above 78°F by ~60% and above 80°F by ~75–85%, with
  afternoons, evenings and nights all cooler, at the cost of ~1.6°F cooler
  mornings. The deeper 22.5°C pre-cool first proposed costs **+$7** once the
  controller is included: it and the controller avoid the same hours.
- **PJM forecasts matter most for capacity, not energy.**
  - **Capacity hours:** ComEd sets the capacity obligation from your average
    demand over 10 hours of the prior summer (PJM's 5 system peaks plus ComEd's
    5 zone peaks). Estimated 2026 hours caught the house at **2.67 kW** on
    average.
  - **Projection:** with ComEd's billing factors, today's 7.01 kW billed
    obligation would fall to **~2.4–2.7 kW**, about $527–557/yr lower at
    today's rate. That is projected, not realized: confirm it against the
    official peak lists and the June 2027 bill.
  - **Advance warning:** one day-before flag (ComEd-zone forecast ≥ 19 GW,
    13 weekdays) covered every one of those days.
  - **Energy:** forecast-gated pre-cooling barely changes the energy bill
    ($0–6 a summer).
  - **Comfort and protection:** pre-cool plus a 3–6pm window on flagged days
    cuts >80°F hours on those days by ~60% and removes nearly all HVAC load
    from the capacity hours (−$68–79/yr more), at ~$0 energy cost. See
    [PJM forecasts](#pjm-forecasts-what-would-they-have-changed).
- **The biggest non-HVAC item is a ~0.6–0.8 kW continuous load on leg B of
  the panel.** 41% of house kWh runs through circuits without a CT. Overnight,
  ~820 W of it sits on leg B (at least ~640 W after allowing for CTs whose leg
  couldn't be tested) and ~115 W on leg A. So it is one or more 120 V leg-B
  circuits, not a 240 V appliance. It barely changes with outdoor
  temperature. A breaker test is next. See
  [Unmonitored load](#unmonitored-load-before-the-breaker-test).
- *Schedule-only results* (no controller) in
  [Comfort vs cost](#comfort-vs-cost) show what each schedule does on its own.
  They are not a comparison against the current system.
- **Solar gain is seasonal (new finding).** Sun sensitivity rises every month
  from June to November, consistent with south glazing.
- **Heating is not modeled yet.**

## Data: what changed since June

| | June study | Now |
|---|---|---|
| Window | 19 days | 177 days (May 26 – Nov 19), ≥99.8% complete on core fields |
| Indoor signal | thermostat, ~0.9°F steps | Ecowitt ch2/ch3/gateway mean, 0.18°F steps, 1 min |
| Day types (peak outdoor) | cool 3 / warm 11 / hot 6 | <60°F 14 · 60–70 29 · 70–80 44 · 80–88 55 · ≥89 35 |
| HVAC-off coasting | sparse | 2,918 h in windows ≥3 h |
| Sun vs outdoor-temp correlation | 0.69 | 0.57 hourly daytime, 0.40 daily (Jun–Sep) |

The Ecowitt room sensors were what was needed: they have been logging at 0.1°C
all along. Model target `house_f` is the mean of ch2 (2F hall), ch3 (2F south
bedroom) and the gateway (1F office). ch8 (presumed basement, 3.8°F cooler)
and Haven return air (too noisy, sd 2°F) are not used as the target. Data traps
(thermostat °C in `*_f` fields since the TCC cutover; the `indoor_temp_f` name
collision) are listed in the tool README.

The EAGLE outage and the PJM login issue do not affect this work. HVAC power
comes from the Refoss CTs and ComfortNet.

## Model

```
dTi/dt = a_env (To − Ti) + b_solar · S + c − k_cool · g(u_cool) + k_heat · u_heat     [°F/h]
```

Ti is the house mean, To the shaded outdoor sensor (ch1), S horizontal solar
(kW/m²), and u the ComfortNet actual cool/heat fraction. The model is fit by
simulating each 24 h day from midnight and minimizing whole-day error, which
makes it robust to sensor stepping.

### Validation (fit Jun–Aug, test on September)

| | RMSE over 24 h | Baseline (temp unchanged) | Bias |
|---|---|---|---|
| Fit months (90 days) | 0.68°F | 2.07°F | +0.04 |
| **Held-out Sep (30 days)** | **0.85°F** | 1.94°F | −0.07 |

### Month-by-month stability

| Month | a_env (τ) | b_solar | c (internal) | k_cool |
|---|---|---|---|---|
| Jun | 0.0248 (40 h) | 0.25 | 0.27 | 0.88 |
| Jul | 0.0250 (40 h) | 0.33 | 0.25 | 0.94 |
| Aug | 0.0250 (40 h) | 0.37 | 0.19 | 0.94 |
| Sep | 0.0184 (54 h) | 0.94 | 0.13 | 0.94 |

Summer envelope and cooling effect are effectively constant. The envelope also
matches the May rough cut's thermostat-based 0.0246. Plain-language reading:

- **Envelope.** With the HVAC off, the house closes about 2.5% of the
  indoor–outdoor gap per hour. A 15°F gap moves it ~0.35°F/h.
- **Cooling.** The AC at full removes about 0.9°F/h. Measured evening
  full-stage-2 pulldowns run 0.77–0.93°F/h depending on the sensor; the model
  gives 0.72°F/h at the same conditions, ~10–15% slow (see
  [Independent validation](#independent-validation)). An earlier version of
  this doc quoted 0.5°F/h. That was a hand calculation at an unrepresentative
  condition (outdoor 8°F above indoor; real evenings average 2.75°F *below*).
- **Sun.** A clear summer afternoon (~800 W/m²) adds ~0.3°F/h. Together with
  the envelope term, this reproduces the ~0.9°F/h AC-off afternoon creep June measured.

Cooling-season production fit (Jun–Sep), used for the counterfactual:
a_env 0.0207 (τ 48 h), b_solar 0.48, c 0.19, k_cool 0.85.

### Solar is seasonal, and south glazing explains it

b_solar climbs 0.25 → 0.33 → 0.37 → 0.94 (Sep) → ~1.6 (Oct–Nov). A summer
model therefore under-predicts autumn afternoons. Converting horizontal
irradiance to approximate irradiance on a south-facing wall (solar geometry,
`solar.py`) recovers much of it:

| Solar input | Sep | late Oct | Nov |
|---|---|---|---|
| horizontal | 0.78°F | 1.31°F | 2.11°F |
| 50% south-vertical blend | 0.70°F | 1.09°F | 1.77°F |
| 80% south-vertical blend | 0.78°F | 1.01°F | 1.63°F |

November remains weak. Heating is barely exercised (heat setpoint ~66°F, and
the house holds 69–72°F mostly on its own), so k_heat (~3.6–3.9°F/h) is not
credible yet.

### Rejected: two-node (air + mass) model

It fits single days as well (0.84°F on held-out Sep). In a continuous
closed-loop replay of Jun 25 – Sep 30, though, it predicts **4,690 kWh vs
2,748 measured**. Day-by-day fitting resets the hidden mass temperature each
midnight, so the fit stores solar heat in the mass and never releases it.
Fitting on 7-day blocks did not fix it. The one-node model is used for all
closed-loop work, and any future model has to pass the replay check below.

## Counterfactual: steady hold vs the setback program

### Method

A simulated thermostat runs the AC just enough each 15 min to keep its reading
at or below the setpoint. Its reading is the modelled house mean plus the
measured thermostat-vs-house offset by hour (−0.6 to +0.2°F).
ComfortNet % → kW comes from the measured Refoss curve: stage 1 ("50%") draws
2.6 kW, stage 2 3.9 kW. Weather and ComEd 5-min prices are the actual Jun 1 –
Sep 30 values. **Bill = actual hourly supply price + ComEd DTOD delivery.**
DTOD rates are by window and billing period, from the ingested Jun–Oct 2026
bills (`comed.bill_lineitems`):

| DTOD window | Distribution Facility Charge |
|---|---|
| Overnight 9pm–6am | 3.345–3.349¢ |
| Morning 6am–1pm | 4.475–4.479¢ |
| **Mid-Day Peak 1pm–7pm** | **11.852–11.863¢** |
| Evening 7pm–9pm | 4.185–4.189¢ |

Each bill period also adds the flat per-kWh items: IL Electricity
Distribution (0.128¢), Transmission Services (~1.07¢), and the Carbon-Free
Energy Resource credit (−0.1 to −1.6¢, varying by month). The capacity charge
($/kW-month on the PLC set by the prior summer's 5CP), fixed charges and the
lump Purchased Electricity Adjustment are excluded: this summer's usage
doesn't change them on this summer's bill.

### Replay check (does the simulator use the right energy?)

Driven by the actual setpoint history, Jun 25 – Sep 30:

| | Simulated | Measured |
|---|---|---|
| HVAC energy | 2,776 kWh | 2,748 kWh |
| Supply cost | $129 | $140 |
| DTOD delivery cost (HVAC) | $117 | $119 |
| Daily kWh correlation / mean abs error | r = 0.94 / 4.8 kWh | |
| Indoor temp RMSE (continuous, 3 months) | 0.82°F | |

Energy matches within 1%, and DTOD delivery within 2%, so the model puts energy
in the right delivery windows. Measured HVAC energy over this window was 61%
overnight, 15% morning, 17% evening and **7.7% in the 1–7pm peak**. Supply cost
runs ~8% low (sub-hour spike timing), so **treat the dollar figures as
relative, not absolute.**

**This is a consistency check, not an independent prediction.** No parameter
was fit to the energy total, but the total *was* used to choose the one-node
over the two-node structure. The parameter-out-of-sample version is under
[Independent validation](#independent-validation).

### Results (Jun 1 – Sep 30)

Program = the cool setpoints the CTK04 actually ran Jul–Sep: 75°F to noon, 78–79°F
1–7pm, 76°F 8–9pm, 75°F overnight. Stage-1 delivered capacity is only weakly
identified, so two bounds are shown.

**Stage 1 = 50% of capacity (data's best fit):**

| Schedule | kWh | Supply | DTOD delivery | Bill | vs program | 1–7pm kWh | Sleep-hours mean | Afternoon max (median day) |
|---|---|---|---|---|---|---|---|---|
| **Program (75 / 79 pm)** | 3,212 | $211 | $129 | **$340** | — | **85** | 75.1°F | 78.3°F |
| Steady 75 | 3,640 | $288 | $277 | $564 | **+66%** | 1,673 | 74.9°F | 75.7°F |
| Steady 76 | 3,387 | $273 | $261 | $534 | +57% | 1,602 | 75.7°F | 76.6°F |
| Steady 77 | 3,135 | $259 | $245 | $504 | +48% | 1,529 | 76.5°F | 77.5°F |

**Stage 1 = 70% of capacity (typical two-stage spec):**

| Schedule | kWh | Bill | vs program |
|---|---|---|---|
| **Program** | 3,171 | **$335** | — |
| Steady 75 | 3,270 | $519 | +55% |
| Steady 76 | 3,037 | $488 | +46% |
| Steady 77 | 2,808 | $457 | +36% |

### Reading the results

- **On energy alone it's close.** Depending on the stage-1 assumption, steady
  holds use −11% to +13% kWh relative to the program.
- **On cost, the program wins by a wide margin.** It runs ~85 kWh of cooling
  in the 1–7pm DTOD peak all summer; a steady hold runs ~1,400–1,700 kWh there,
  each paying 11.85¢ delivery on top of the highest supply prices. Delivery
  alone roughly doubles. June's worry was confirmed: without the coast,
  cooling lands in peak hours.
- **Even steady 77 costs 36–48% more,** and it is worse for sleep (nights
  ~1.4°F warmer).
- **What the program costs is afternoon comfort.** The house mean reaches
  ~78.3°F on a median day, with ~290 h above 78°F over the summer.
- **Not in the dollar figures, and it favors the program further:** PJM
  capacity (the PLC set by summer-afternoon 5CP hours) bills the following
  year. It is currently $70.89/month (7.01 kW × $10.11), and the program's
  ~15–20× lower afternoon load directly lowers it.

## Independent validation

`python -m tools.thermal_model validate`. Everything here is out of sample:
for each summer month, the parameters, the kW curve and the thermostat offset
are estimated from the other three months only. The held-out month is then
replayed under its real setpoint history. Caveat: the one-node *structure* was
chosen on the full-season replay, so structure selection is not held out.

### Energy, leave-one-month-out

| Held-out month | Simulated kWh | Measured kWh | Error | Daily r | Indoor RMSE |
|---|---|---|---|---|---|
| Jun | 806 | 869 | −7% | 0.94 | 1.10°F |
| Jul | 1,186 | 1,295 | −8% | 0.92 | 0.83°F |
| Aug | 880 | 664 | **+32%** | 0.91 | 0.80°F |
| Sep | 511 | 541 | −6% | 0.97 | 1.15°F |
| **Season** | 3,384 | 3,370 | **+0.4%** | | |

The season total holds, but partly through cancellation: typical months run
~7% low, and August runs 32% high. August's error is a steady ~7 kWh/day on
every day regardless of weather, with the simulated house ~0.4°F warm. That
points to a change in the house that month (occupancy, blinds, a vacation),
not weather. The model has no occupancy input.

### Peak events: where the money is

| Event set (held-out) | Simulated | Measured | Ratio |
|---|---|---|---|
| 10 hottest afternoons (noon–8pm) | 44 kWh | 90 kWh | 0.49 |
| Peak indoor temp on those days | | | **−1.0°F** (model cool) |
| All evenings 7pm–midnight (122) | 1,396 kWh | 1,675 kWh | 0.83 (daily r 0.77) |
| Hot evenings, ≥89°F days (30) | 444 kWh | 522 kWh | 0.85 (**daily r 0.32**) |
| Most expensive 5% of slots (≥13¢) | 74 kWh / $25.5 | 106 kWh / $34.8 | **0.70** |

**The model systematically under-predicts heat build-up on hot sunny
afternoons, and with it the energy in the hours that cost the most.** It
should not be used to time individual heat events or individual evening
recoveries. Two partial causes were found:

- **Thermostat room.** On ≥92°F afternoons the thermostat reads
  ~0.4°F warmer relative to the house mean than on an average afternoon
  (offset −0.08 vs −0.44°F). The real thermostat therefore calls for cooling
  earlier than a simulation using the average offset. This accounts for
  roughly half of the ~1°F peak miss.
- **Lagged roof/attic gain.** Adding a 2–6 h lagged solar input improves
  September temperature (0.82°F) and evening recovery energy (0.83 → 0.91 of
  measured), and halves the peak-temp bias. It does **not** fix the
  expensive-slot energy (78 vs 106 kWh) and makes August worse. Not adopted:
  the gain is small, and it was selected on these same held-out results.

### Evening pulldown rate

Evening runs (start 7pm–2am) at full stage 2 for ≥1 h, median slope:

| Period | Thermostat | House mean | 2F hall | 2F S-BR | 1F office | Model | Out − in |
|---|---|---|---|---|---|---|---|
| Jul–Sep (86 runs) | −0.77 | −0.85 | −0.79 | −0.93 | −0.85 | −0.72 | −2.75°F |
| Late May–Jun 21 (28 runs) | −0.30* | −0.71 | −0.72 | −0.79 | −0.69 | −0.80 | −6.82°F |

\*Control4-era thermostat, 0.5°C steps: unreliable slopes.

The model is ~10–15% slow in Jul–Sep and about right in June. June's "~1°F/h"
was a time-to-reach-setpoint figure under a different program and is not
comparable to a full-stage slope.

## Comfort vs cost

`python -m tools.thermal_model costcurve`. The question: what is the cheapest
way to cool the house for a given afternoon maximum, and what is each degree
worth?

### Method

1. **Plan.** A linear program over Jun 25 – Sep 30 chooses cooling every 15 min
   to minimize (supply price + DTOD delivery) × kW, subject to the one-node
   dynamics, house mean ≤ cap, and a soft 72°F pre-cool floor.
   **Sleep comfort is held at the program's level** (house ≤ 75.2°F, 10pm–7am)
   for every cap. Without that constraint, the cheap plans buy their savings
   by running ~78°F nights, which is not a like-for-like comparison.
2. **Price.** The planned temperature path becomes a setpoint schedule and is
   run through the replay-validated thermostat simulator with the measured
   kW curve. The LP's simplified kW model never sets a reported cost.
3. **Planner knowledge.** Weather is always perfect foresight. Prices are:
   - **perfect**: actual real-time (unachievable; the ceiling)
   - **day-ahead**: PJM DA LMP, ComEd zone, knowable the evening before
     (correlation with real-time 0.64; peak 100¢ vs real-time 299¢)
   - **hour-of-day**: median price shape only, nothing about specific days

   Plus a **derived fixed program**: the hour-of-day plan collapsed to one
   24-hour thermostat program, which the CTK04 can run with no forecasts at all.

### Results (Jun 25 – Sep 30; stage-1 = 50% / 70% where they differ)

| Strategy | Afternoon max (median day) | Season max | 1–7pm kWh | Bill | vs program |
|---|---|---|---|---|---|
| **Current program** (75 / 79 pm) | 78.3°F | 79.2°F | 82 | $301 / $296 | — |
| Fixed 76°F day, 75 night | 76.0 | 76.6–76.9 | 1,140–1,284 | $480 / $436 | +59% / +47% |
| Fixed 78°F day, 75 night | 78.0 | 78.4–78.6 | 831–910 | $385 / $358 | +28% / +21% |
| Derived hourly program, cap 76 | 75.5 | 76.0 | 217–220 | $305 / $295 | +1.4% / −0.3% |
| Planner, cap 76, hour-of-day prices | 75.8 | 76.1 | 69–85 | $273 / $273 | −9.5% / −7.9% |
| **Planner, cap 76, day-ahead prices** | 75.8 | 76.1 | 90–106 | $262 / $264 | **−13% / −11%** |
| Planner, cap 76, perfect prices | 75.9 | 76.1 | 120–139 | $237 / $244 | −21% / −18% |

Under DTOD, every optimized plan **pre-cools overnight and through the
morning** (3.3–4.5¢ delivery) and coasts through the 11.85¢ peak. The derived
hourly program (house-mean targets) holds 75°F until ~2am, pulls down to ~72°F
by 6am, holds ~72°F to noon, then coasts up to ~76°F by 8pm and back to 75°F
by 10pm. Nights average ~74°F, below the 75.2°F cap, because overnight
cooling is cheap.

### Implementable 3-period programs (schedule only, no controller)

> These compare schedules **without** the spike controller. For the comparison
> against the system actually running, see
> [Schedule × controller](#schedule--controller-four-way-comparison).

The CTK04 runs a few program periods in °C half-steps, so the hourly plan was
approximated with three periods: night (10pm), pre-cool (from 1–4am) and coast
(from 1pm). Same window, simulator and DTOD billing:

| Program (°C) | Bill vs current (50% / 70% stage-1) | Afternoon max median / season | 1–7pm kWh | Morning low |
|---|---|---|---|---|
| Current: 24 → 26 (1–7pm) → 24.5 → 24 | — | 78.3 / 79.2°F | 82 | ~72°F |
| 24.0 → **22.0 from 3am → 24.5 from 1pm** | **−4.5% / −3.1%** | 75.2 / 76.5°F | 38–48 | ~70°F |
| 24.0 → **22.5 from 3am → 25.0 from 1pm** | **−10.4% / −10.2%** | 76.0 / 77.4°F | 30–36 | ~70.5°F |
| 24.0 → 22.5 from 3am → 24.5 from 1pm | +3.6% / +1.4% | 76.0 / 76.5°F | 127–132 | ~70.5°F |
| 24.0 → 23.0 from 3am → 24.5 from 1pm | +13.7% / +8.8% | 76.5 / 76.6°F | 309–329 | ~71°F |
| 24.0 → 22.5 from **1am** / **4am** → 24.5 from 1pm | +3.1% / +4.7% (50%) | 76.0 / 76.5°F | 127 | ~70.5°F |

**The pre-cool depth matters more than its start time.** At 23°C the house
runs out of stored coolness and the AC comes on during the peak. At 22–22.5°C
it coasts through. The coast setpoint (24.5 vs 25.0°C) decides whether the AC
touches the peak at all. In exchange, mornings run ~72°F (season low ~70°F).

### Reading the curve

- **With smart timing, afternoon comfort is almost free.** Once sleep is held
  at 75°F, the optimal plans stop changing above a 77°F cap. A 76°F cap costs
  $0–6 more than no cap.
- **With fixed schedules, each degree costs ~$40–50 per summer** under DTOD.
  Fixed 76 → 80°F runs $480 → $276 (50% stage-1) and $436 → $262 (70%).
  "Fixed at 80" looks cheaper than the program only because it misses the
  sleep target (nights 76.2°F).
- **The current program is already well shaped for DTOD** (7.7% of measured
  HVAC kWh in the peak). The remaining gain comes from pre-cooling *deeper and
  earlier* so the coast lasts through 7pm, rather than holding 75°F all morning.
- **A planner adds 7–13%,** robust to the stage-1 assumption: −8 to −10% with
  only the typical daily price shape, −11 to −13% with day-ahead prices.
- **The ceiling is low in absolute terms.** Even perfect foresight saves
  ~$52–64 over this 14-week window (~18–21% of a ~$300 HVAC cooling bill).
  That bounds the summer payback for any HVAC-timing investment.
- **Bias direction is now favorable.** The better plans and 3-period programs
  put *less* energy in the 1–7pm peak than the current program (30–48 vs 82
  kWh), so the model's afternoon under-prediction mostly hits the current
  program. What it does put at risk is the coast itself: on the hottest days
  the real house warms ~1°F more than modelled, so a 24.5°C coast may trigger
  some peak cooling the model doesn't show. The 25.0°C coast has the most margin.

## Schedule × controller (four-way comparison)

`python -m tools.thermal_model fourway`. Window: Jul 6 – Sep 30, 2026 (the
controller was live, and its config was unchanged throughout).

### Emulating the controller

`controller_sim.py` ports the rev 4.1 decision code (`tiers.evaluate_tier`,
`holds.compute_target` / `decide`) with the live config:
- **Tiers:** elevated at ≥10¢ (program + 1.5°C) and scarcity at ≥20¢ (29.5°C
  absolute). Release takes two confirming buckets 2¢ below the trigger.
- **Warm-only precondition:** no hold when the program is already at or
  above the target.
- **Humidity gate:** blocks at RH ≥ 61% and clears below 58%; it only
  updates while the controller is in a tier.

It runs on the real ComEd 5-min prices and thermostat RH. The tier sequence
depends only on price and humidity, so it is identical under any program.
What changes with the program is the hold *target* and whether a hold
happens at all.

**Checked against the controller's own log** (`hvac.actions`, 1,400 rows),
on the current program:

| | Emulated | Actual (from applied pushes/releases) |
|---|---|---|
| Hold time | 208 h | 209 h |
| Hold episodes | 355 | 344 |
| Timing agreement (5-min) | recall 96.7%, precision 97.1%, Jaccard 0.94 | |
| Daily hold-hours correlation | 0.99 | |

Hitting that agreement needed a **10-minute price publish lag**: the
controller can only act once ComEd publishes a bucket. With no lag, agreement
was 70%.

Each scenario is an independent closed-loop simulation from the same starting
state, weather and prices. Indoor temperature and AC use are computed from
that scenario's own setpoints; measured HVAC history is used only to fit and
check the model. A cooler pre-cooled house therefore gains heat faster in
the simulation. That is why the pre-cool programs use 10–16% more kWh.

### Results (Jul 6 – Sep 30; stage-1 = 50% / 70%)

Anchors: the measured HVAC bill was **$199** (supply $100 + DTOD $99) on
2,274 kWh, 172 kWh of it at 1–7pm. Replaying the actual setpoint history
gives **$195** on 2,352 kWh. "Current program + controller" ($177) is lower
than both because the real history also includes:
- program edits (e.g. 23°C in July),
- manual changes,
- 52 failed pushes.

The emulated scenarios use one fixed program each.

| Scenario | Bill | Supply / DTOD | kWh | 1–7pm kWh | Controller cost in spike tiers |
|---|---|---|---|---|---|
| Current program, no controller | $231 / $227 | $137 / $93 | 2,315 | 56 | $70–72 |
| **Current program + controller** (today) | **$177 / $177** | $89 / $88 | 2,259 | 29–32 | $7–8 |
| 3-period 22.5 → 25.0, no controller | $211 / $209 | $108 / $102 | 2,673 | 15–19 | $34–35 |
| 3-period 22.5 → 25.0 + controller | $184 / $184 | $84 / $100 | 2,628 | 7–11 | $1.2–1.5 |
| 3-period 23.0 → 25.5, no controller | $196 / $194 | $101 / $95 | 2,529 | 11–14 | $30 |
| **3-period 23.0 → 25.5 + controller** | **$173 / $173** | $80 / $93 | 2,488 | 4–6 | $1.5–1.6 |
| 3-period 23.5 → 26.0 + controller | $163 / $163 | $76 / $87 | 2,338 | 2–3 | $1.7 |

Comfort (house mean, hours over the 12 weeks):

| Scenario | Afternoon max (median) | >78°F | >80°F | Season max | Evening 7–10pm | Bedtime 10pm | Sleep | Morning 6–12 | <72°F |
|---|---|---|---|---|---|---|---|---|---|
| Current, no controller | 78.3 | 209–224 | 0 | 79.2 | 77.3 | 76.2 | 75.1 | 75.0 | 4–6 |
| **Current + controller** | 78.3 | **277–298** | **25–30** | 81.4–81.6 | 77.7 | 76.6 | 75.3 | 75.0 | 4–6 |
| 22.5 → 25.0 + controller | 76.0 | 35–43 | 0 | 79.7–79.9 | 76.1 | 75.8 | 74.3 | 72.5 | 42–46 |
| **23.0 → 25.5 + controller** | **76.7** | **99–118** | **4–7** | 80.5–80.7 | **76.8–77.0** | 76.5–76.6 | 74.7–74.8 | 73.4 | 15–16.5 |
| 23.5 → 26.0 + controller | 77.5 | 226–250 | 18–23 | 81.2–81.4 | 77.6 | 77.2 | 75.3 | 74.2 | 8.5–10.5 |

### Reading the four-way

- **The controller is worth ~$50–54 over 12 weeks on the current program**
  (−22 to −23%). That is larger than any schedule change alone.
- **Its comfort cost is real.** It adds 25–30 hours above 80°F (holds of
  27.5–29.5°C), which matches the replayed history (20–23 h). The review's
  concern is borne out: schedule and controller interact.
- **On a pre-cooled baseline the controller does less, and both are cheaper.**
  Its spike-tier cost drops from $7–8 to ~$1.5, because the house enters
  spikes cooler and the program's afternoon value (25.5°C) makes holds land
  at 27.0°C instead of 27.5°C. Its marginal value falls from ~$50 to ~$21–23.
  The two overlap: both avoid the same expensive hours.
- **Pre-cool depth decides it.** At 22.5°C, the extra pre-cool energy costs
  more than the overlap saves (+$7). At 23.0°C, it roughly breaks even
  (−$4) while removing most of the heat excursions the controller causes.
  At 23.5°C, it saves $14 but keeps most of the excursions.
- **23.0 → 25.5°C + controller is a comfort improvement at no extra cost, not
  a savings play.** The $4 is within model error. What is robust across both
  stage-1 assumptions is: ~60% fewer hours above 78°F, ~75–85% fewer above
  80°F, and cooler afternoons, evenings and nights. The cost is ~1.6°F cooler
  mornings and ~15 h below 72°F over 12 weeks.
- **Bias check.** The model under-predicts 1–7pm energy (measured 172 vs
  replayed 121 kWh). The current program puts 29–32 kWh there and the
  candidate 4–6, so correcting the bias would favor the candidate slightly.

### Limits specific to this comparison

- Humidity is replayed from the real history. A pre-cooled house would likely
  run drier, which matters little here: the gate never blocked in emulation,
  and the real log has one humidity release.
- Every push succeeds and there are no manual holds. Both apply equally
  to every scenario.
- The real controller sets holds against the program value it reads from the
  thermostat. That is modelled, but the CTK04 program here is idealized: one
  fixed 3-period schedule, every day.

## PJM forecasts: what would they have changed?

`python -m tools.thermal_model pjm`. Window: Jun 1 – Sep 30, 2026, with the
controller emulated throughout (it went live Jul 6; the earlier weeks are
hypothetical).

### Were the spike days predictable?

105 of 122 days had at least one afternoon bucket at ≥10¢, so a bare "spike
day" is common. What matters is the *big* days: the top 10% of days hold 34%
of the 229 afternoon spike-hours. Each predictor is scored using only what was
known in time:

| Flag | Known | Days flagged | Of which no spike | Share of spike-hours on flagged days | Top-10% days caught |
|---|---|---|---|---|---|
| Day-ahead LMP (12–9pm max) ≥ 7.5¢ | day before ~1:30pm | 69 | 4 | 78% | 86% |
| Day-ahead LMP ≥ 10¢ | day before | 39 | 2 | 58% | 79% |
| ComEd-zone peak load forecast ≥ 17 GW | day before (noon issue) | 23 | **0** | 46% | 79% |
| ComEd-zone forecast ≥ 19 GW | day before | 13 | 0 | 30% | 57% |
| PJM RTO peak forecast ≥ 140 GW | same day ~5–7am | 22 | **0** | 39% | 64% |
| PJM RTO peak forecast ≥ 150 GW | same day | 10 | 0 | 26% | 57% |

The load forecasts never false-alarm. Day-ahead LMP casts a wider net.

### Would acting on them have changed the bill?

With the controller always on, the current program on normal days, and a
switch to the 23.0 → 25.5°C pre-cool program only on flagged days. Day-before
flags start the pre-cool at 3am; same-day flags at 7am. Bills are supply +
DTOD, Jun 1 – Sep 30; ranges cover the 50% / 70% stage-1 assumptions.

| Strategy (+ controller) | Flagged days | Bill vs current | >80°F h | >78°F h | <72°F h | Morning mean |
|---|---|---|---|---|---|---|
| Current program | 0 | $241–242 | 51.5–60 | 394–424 | 4–6 | 74.9–75.0 |
| Pre-cool every day | 122 | −$5.6 to −6.1 | 15.5–24 | 150–179 | 15–17 | 73.4 |
| Pre-cool if DA ≥ 7.5¢ | 69 | −$5.6 to −5.8 | 20–28 | 238–273 | 8–11 | 74.0 |
| **Pre-cool if ComEd fcst ≥ 17 GW** | 23 | **−$3.5 to −4.0** | **17.5–26** | 320–357 | **4–6** | 74.6–74.7 |
| Pre-cool if RTO fcst ≥ 140 GW (7am start) | 22 | −$0 to −1.3 | 25.5–33 | 337–370 | 4–6 | 74.8 |

- **Energy: forecasts barely move the bill** (−$0 to −6 over four months).
  The controller already reacts within ~10 minutes of a published price, and
  most spikes are short.
- **Comfort: the gain is targeted.** The controller's >80°F excursions come
  from scarcity holds on the big days. Pre-cooling only on the ~23 days the
  ComEd forecast flags removes 55–66% of them. Unlike daily
  pre-cooling, it adds no cold mornings on the other ~100 days.
- A same-day 7am start is too late to bank much coolness. Day-before flags
  are what make pre-cooling work.

### Capacity: the bigger lever (projected, not yet realized)

**How the obligation is set.**
- **Ten hours, not five.** Per ComEd's Hourly Pricing FAQ, the capacity
  obligation averages your demand over **10 hours of the prior summer**: the
  5 PJM system peak hours *and* the 5 ComEd-zone peak hours. The average is
  adjusted for T&D losses.
- **The bill shows a scaled figure.** Per CUB (2026-06-23), the charge is
  Capacity PLC × 0.97854 (scaling) × 0.9291 (forecast pool requirement) ×
  rate. So the **7.01 kW on the bill is already scaled**: 7.01 × $10.11236 =
  $70.89. The underlying Capacity PLC is ~7.71 kW.
- **When it changes.** Summer 2026's hours set the obligation billed from
  June 2027.

**Estimated 2026 hours.** Official lists were not in Influx on 2026-11-19.
Each set is the top 5 daily peaks on weekdays, NERC holidays excluded, from
`pjm.metered_load`. Fri Jul 3 is kept because NERC does not shift a Saturday
holiday; excluding it swaps in Sep 1 and changes the average by −0.04 kW.

| Set | Hour | Zone MW | House kW | HVAC kW | Fcst rank: RTO morning / ComEd day-before |
|---|---|---|---|---|---|
| PJM | Thu Jul 2, 4pm | 162,221 | 1.56 | 0.00 | 1 / 5 |
| PJM | Wed Jul 1, 4pm | 161,600 | 2.82 | 1.61 | 3 / 3 |
| PJM | Wed Jul 15, 4pm | 158,676 | 3.10 | 1.28 | 2 / 2 |
| PJM | Fri Jul 3, 4pm | 154,388 | 2.15 | 0.00 | 4 / 18 |
| PJM | Thu Jul 16, 4pm | 153,784 | 2.10 | 0.06 | 5 / 7 |
| ComEd | Wed Sep 2, 4pm | 21,228 | 1.97 | 0.00 | 11 / 6 |
| ComEd | **Tue Jun 30, 5pm** | 20,677 | **6.46** | **4.00** | 9 / 4 |
| ComEd | Tue Sep 1, 4pm | 20,633 | 1.78 | 0.00 | 8 / 10 |
| ComEd | Tue Jul 14, 5pm | 20,612 | 1.60 | 0.19 | 6 / 1 |
| ComEd | Wed Jul 15, 4pm | 20,598 | 3.10 | 1.28 | 2 / 2 |
| **10-hour mean** | | | **2.67** | **0.84** | |

**Projected billed obligation.** 2.67 kW × loss factor (1.00–1.10) × 0.9092
gives **2.42–2.67 kW**, against 7.01 kW today. That's $527–557/yr lower *at
today's rate and factors*. Shifting the hours by ±1 h moves the 10-hour mean
by at most −0.4 kW.

Why it is "projected":
- the peak hours are estimates,
- the loss factor is bracketed, not confirmed,
- the 2027/28 rate and factors will differ.

Book it only once the June 2027 bill shows the new figure.

**What the data shows:**
- **The afternoon setback already did most of the work.** HVAC was ~0 in six
  of the ten hours. The exception is **Tue Jun 30, 5pm** (before the rev 4
  controller went live): the AC ran flat out (4.0 kW). That one hour adds
  ~0.4 kW to the 10-hour mean.
- **Capacity peaks and price spikes are different events.** Jun 30 was not a
  top-RTO day (rank 9), and its ComEd-zone peak needs a ComEd signal. A
  capacity mode should not wait for a price spike. A capacity hour at a cheap
  real-time price would otherwise let the AC run.
- **The ComEd-zone day-before forecast is the better single trigger.** At
  ≥ 19 GW (13 weekdays) it flagged **every one of the 8 distinct peak days**.
  The RTO morning forecast ranked the PJM days well but put two ComEd-zone
  days at 9 and 11.
- **Hindsight check:** each `pjm.peak_forecast_rto` point is stamped with
  PJM's `generated_at_ept`, i.e. as published. Every revision is stored, and
  the ranking uses same-day 6:01/8:01 EPT issues. The ComEd forecast is
  filtered on its `evaluated_at` publish time. The ≥ 19 GW threshold is
  tuned on one summer only; it needs checking against earlier summers before
  automating. 2025 forecasts are not in Influx.

**Selective capacity protection** on the 13 flagged weekdays, controller on.
Billed kW assumes a 1.05 loss factor; ranges are 50% / 70% stage-1.

| Protocol on flagged days | HVAC at the 10 hours | Billed capacity | Capacity vs current | Energy | Flagged days: >80°F h | 3–6pm mean | Morning mean |
|---|---|---|---|---|---|---|---|
| Current program | 0.61 / 0.68 kW | 2.32 / 2.39 kW | — | — | 45 / 51 | 79.1 / 79.2°F | 75.4°F |
| 26.5°C 3–6pm only | 0.39 kW | 2.12 kW | −$25 / −33 | −$1 | 49 / 55 | 79.1 / 79.3°F | 75.4°F |
| **Pre-cool 23.0°C 3am–1pm + 26.5°C 3–6pm** | **0.00 / 0.09 kW** | **1.74 / 1.83 kW** | **−$70 / −68** | **+$0.2–0.4** | **15.5 / 21** | **77.6 / 77.8°F** | 73.7 / 73.9°F |
| Full: 22.5°C pre-cool, 26.5°C 1–7pm | 0.00 kW | 1.74 kW | −$70 / −79 | −$0.1 to +$0.4 | 12.5 / 15 | 76.9 / 77.1°F | 73.1 / 73.3°F |

- **A window without pre-cool barely helps** and makes the afternoon *hotter*.
- **The selective pre-cool + 3–6pm window** removes nearly all HVAC load from
  the capacity hours for ~$0 of energy. It also makes flagged days *more*
  comfortable: ~60% fewer hours above 80°F, because the controller's 29.5°C
  scarcity holds start from a cooler house. The cost is ~1.6°F cooler mornings
  on 13 days.
- **The model under-predicts hot afternoons by ~1°F**, so "0.00 kW" is
  optimistic. The direction and comfort result are robust; the last fraction
  of a kW is not.

**Non-HVAC load at those hours** (Refoss, all 18 circuits). The ~1.8 kW is
almost all **always-on baseline**, the same at 3–5am:

| Circuit | At the 10 hours | 3–5am |
|---|---|---|
| **Unmonitored remainder** (mains − all CT'd circuits) | **0.99 kW** | **1.00 kW** |
| AV rack (network/servers) | 0.29 | 0.32 |
| Fridges/freezers (4 circuits) | 0.26 | 0.23 |
| Room circuits | 0.23 | 0.23 |
| Washer/dryer (the only deferrable load) | 0.00 | 0.01 |

- **Nothing deferrable was running at the peak hours**, so a "defer laundry"
  alert has little to act on.
- **The lever is the ~1.0 kW unmonitored baseline that runs around the
  clock.** The utility meter confirms it is real: the EAGLE and the Refoss
  mains agree within 1% overnight (2.54 vs 2.56 kW), and the house rarely
  drops below 1.4 kW (5th percentile; minimum 1.08 kW).
- **What it is worth.** At ~1 kW continuous it is ~8,800 kWh/yr, on the
  order of **~$850/yr in energy** (~10¢/kWh all-in), plus ~$110–120/yr of capacity per kW. It
  may be essential (sump, dehumidifier, freezer) or waste. See
  [Unmonitored load](#unmonitored-load-before-the-breaker-test).

## Unmonitored load: before the breaker test

Reproduce with `python -m tools.thermal_model loads` (`tools/thermal_model/loads.py`).
"Night" is 1–5am local, when cooking, laundry and lighting are off.

### How the Refoss measures each leg

- **Mains.** em:1 and em:7 clamp the two service hots. Each multiplies its
  current by its own leg's voltage, so em:1 + em:7 is the whole house (the
  EAGLE agrees within 1%) and each main is that leg's load. A 120 V circuit
  draws on one hot (current returns on the neutral), so it shows on one main
  only. A 240 V load shows on both equally.
- **Branch CTs only measure current.** The EM16P's voltage harness has three
  references, La, Lb and Lc. Each CT bank (A = em:1–6, B = em:7–12,
  C = em:13–18) computes power from its own reference. In a split-phase
  house La and Lb go to the two legs; Lc can go to either
  ([Home Network Guy setup guide](https://homenetworkguy.com/hardware/meross-refoss-em16p-energy-monitor-setup/);
  [Refoss manual](https://support.refoss.net/Support/Downloads)). **Here Lc
  is on leg A:** bank C's voltage matches em:1 within 0.02 V on average over
  Nov 12–19, and sits 1.85 V from em:7.
- **What a negative channel means.** A CT on a circuit fed from the leg
  *opposite* its bank's reference sees current 180° out of phase with that
  voltage. So it reads negative power with PF near −1, exactly as a CT clamped
  backwards would. The app's per-channel `factor = -1` flips the sign. It does
  not change the leg the device thinks the circuit is on, and it keeps
  scaling by the other leg's voltage. Legs here differ by up to ~6 V
  (≤ 5% error on that channel).
- **This house:**
  - The device config has `factor: -1` on **em:14 (C2, Basement
    Micro/Fridge)**, and on no other channel.
  - C1 (em:13, Basement Fridge/Freezer) and C2 are the two basement fridge
    circuits. The step test puts C1 on leg A and C2 on leg B, which matches
    C2's flipped factor. A panel check: if the two breakers sit next to each
    other in one column (full-size, not a tandem), the bus alone puts them on
    opposite legs.
  - **em:18 (C6, Dining Room)** shows the same signature now: −24 W at
    0.21 A, PF −0.95 on 2026-11-19. It is either on leg B or clamped
    backwards. Either way `factor = -1` fixes the sign. Flipping its breaker
    during the breaker test will show which main drops.
- **So per-leg accounting works,** provided every CT is credited to the leg
  its breaker is on, not the leg its bank is referenced to. That is what
  `loads.PHYSICAL_LEG` does.
- **Leg voltages.** They move apart as the legs are loaded unevenly: V_A − V_B
  rises ~0.25 Ω × (I_B − I_A), up to ~6 V at the 95th percentile. Leg B is
  usually the heavier one. That is the service conductors and neutral sharing
  the load. It has been stable since May (0.20–0.32 Ω by month), so it is not
  a connection getting worse. Leg A has touched ~128 V on light-load nights
  in June.

### Instruments first

- **Same intervals.** The EAGLE and Refoss comparisons both use hourly means
  of the 1-min long-term aggregates, on the same hour starts. Overnight they
  agree within 1% (above).
- **CT direction.** Over Nov 12–19 (raw 30 s data), 17 of 18 channels never
  read negative. The exception is **em:18 Dining Room**: negative 84% of the
  time, PF −0.67, 0.08 A. That is a reversed CT, or a CT on a leg-B circuit
  referenced to leg A. It is −8 W, so it doesn't move the totals.
- **Phase assignment.** The EM16P's CT banks (A = em:1–6, B = em:7–12,
  C = em:13–18 referenced to leg A) do not fix which leg a breaker is on. So
  each circuit's leg was found from the data. In 1-min data for Nov 12–19
  (AC off), the test takes minutes where one circuit stepped by > 15 W while
  every other CT was quiet, and asks which main moved:

  | Circuit | Steps | em:1 (leg A) | em:7 (leg B) | Leg |
  |---|---|---|---|---|
  | em:3 Washer/Dryer | 59 | 0.78 | 0.02 | A |
  | em:6 Bar | 232 | 0.98 | 0.03 | A |
  | em:13 Basement Fridge/Freezer | 75 | 1.00 | 0.03 | A |
  | em:9 Furnace | 980 | 0.00 | 0.92 | B |
  | em:10 Laundry Fridge | 446 | 0.00 | 0.96 | B |
  | em:11 Kitchen Fridge | 373 | 0.00 | 0.98 | B |
  | **em:14 Basement Micro/Fridge** | 184 | 0.00 | **0.95** | **B, though in bank C** |
  | em:4 Family Room / em:5 Master Bedroom | 4 / 4 | 0.94 / 1.00 | −0.22 / 0.18 | A (few steps) |

  - The AC is 240 V, with one CT per leg (em:2, em:8).
  - **em:12 AV Rack's** own jitter doesn't reach either main, so it can't be
    step-tested. It must be on leg B: on leg A, the leg-A remainder would be
    at least its 270 W minimum, and it is ~115 W.
  - **em:15–18** are too steady to test, and are left on leg A. The leg-B
    *lower bound* below moves every untested bank-A/C circuit (em:4, em:5,
    em:15–18) to leg B.
  - `loads.PHYSICAL_LEG` records this. The old `docs/SERVICES.md` note that
    em:14 was on the B phase was right; its CT is now reading positive.
- **Double counting.** A CT on a sub-feed that also carries other CT'd
  circuits would drive a remainder negative. In 4,253 hours (May 26 – Nov 19)
  neither leg's remainder was ever negative. The total's 1st percentile is
  +475 W. With em:14 on the wrong leg, leg A went negative in 0.8% of hours;
  with it corrected, in none.
- **Coverage.** 41% of mains kWh since May 26 runs through circuits without a
  CT. That is *unmonitored*, not *unexplained*.

### The baseline is on one leg

| Night (1–5am), May 26 – Nov 19 | Leg A | Leg B | Leg B, lower bound | Total |
|---|---|---|---|---|
| Median | 115 W | **823 W** | 641 W | 958 W |
| 5th percentile | 87 W | **546 W** | 402 W | 641 W |
| 95th percentile | 323 W | 1,337 W | 1,133 W | 1,618 W |

| Night mean (W) | May | Jun | Jul | Aug | Sep | Oct | Nov |
|---|---|---|---|---|---|---|---|
| Leg A | 170 | 181 | 188 | 147 | 173 | 149 | 133 |
| Leg B | 1,002 | 1,126 | 888 | 773 | 847 | 866 | 726 |
| Leg B, lower bound | 769 | 823 | 592 | 647 | 700 | 736 | 536 |

- **Leg A is largely covered by CTs.** Both the persistent floor and most of
  the month-to-month change are on leg B, even at the lower bound.
- **It is not a 240 V appliance.** A dryer, range, water heater or well pump draws
  on both legs equally, and leg A has only ~115 W unmonitored.
- **So the target is 120 V load on leg B with no CT.** That rules out every
  leg-A single-pole breaker. It does **not** rule out a two-pole breaker that
  feeds a subpanel (garage, outdoor kitchen) or a shared-neutral multi-wire
  circuit. Those carry 120 V loads on each leg separately, so a garage
  subpanel with its loads on leg B would look exactly like this.
- **At the panel:** the breakers for the confirmed leg-B circuits (Furnace,
  Laundry Fridge, Kitchen Fridge, Basement Micro/Fridge) show which rows are
  leg B. Panel rows usually alternate legs.

### Weather sensitivity is weak

| Night outdoor temp | Nights | Leg A | Leg B | Leg B, lower bound | Total | Monitored fridges |
|---|---|---|---|---|---|---|
| ≤ 45°F (mean 38.7) | 19 | 145 | **827** | 641 | **972** | 219 |
| 45–55°F | 26 | 147 | 824 | 668 | 970 | 242 |
| 55–65°F | 49 | 164 | 912 | 687 | 1,077 | 231 |
| 65–75°F | 70 | 172 | 908 | 709 | 1,080 | 234 |
| > 75°F (mean 77.4) | 14 | 184 | 855 | 635 | 1,039 | 237 |

- The four monitored fridge circuits (Laundry, Kitchen, Basement F/F,
  Basement Micro/Fridge) are flat: +0.2 W/°F.
- The unmonitored total rises ~100 W from cold to mild nights (+2.9 W/°F
  overall) but drops again on the hottest nights. That is not a clean
  fridge-in-a-hot-space signature.
- **~640–830 W stays on leg B on the coldest nights.** The garage and
  outdoor-kitchen fridges are unmonitored, but a fridge averages tens to
  ~100 W, and they barely run at 39°F.

**What this does and doesn't say:** a stable floor doesn't mean one device. It
can be several small continuous loads. Don't buy CTs or plugs, or model the
fridges, until the breakers are known.

### Breaker test procedure

On a quiet night with the HVAC idle:
1. Run `tools/thermal_model/breaker_watch.sh` from any directory. It prints the
   per-leg remainder every 30 s and appends it to `./breaker_test_<date>.csv`.
2. Switch off **leg-B single-pole breakers without a CT**, one at a time,
   then any **two-pole breakers feeding a subpanel or multi-wire circuit**.
   Skip anything critical (sump, freezers you can't interrupt, network gear).
   Never open or work inside an energized panel; operate the breakers only.
3. Hold each off through at least two readings, note the clock time, restore
   it, and confirm the remainder returns.
4. A drop can understate a circuit if something restarts in a low-power
   state, and a flip can reset electronics.

Record:

| Breaker | Leg-B drop (W) | Back after restore? | Devices on it | Constant / cycling / intermittent | Likely seasonal? |
|---|---|---|---|---|---|

If the floor is found and worth tracking, the idle CTs (em:16 Primary Bath
GFCI ~0 W, em:17 Primary Bedroom ~4 W, em:18 Dining Room) can be moved to it.

## Ops note: thermostat push failures (Jul 4 – Nov 19)

Audited because the controller log showed failed pushes. Counts, reconciled:

| Window | Failed hold pushes | Failed releases | Other | Total |
|---|---|---|---|---|
| Jul 6 – Sep 30 | 52 | 2 | — | 54 |
| Jul 4 – Nov 19 | 80 | 7 | 4 rev-3 `BASELINE` rows (Jul 4–5) | 91 |
| Aug 8 – Nov 19 | 59 | 6 | — | **65 = the 65 write failures in `hvac.device_status`** |

**What the 80 hold pushes were:**
- **53 while a hold was active** (39 renewals, 14 target corrections). The
  hold stayed in force; retries succeeded within seconds.
- **27 hold starts** (23 at spike onset, 4 over a manual hold). Median delay
  3.5 min.

**Causes.** All 65 write failures with a recorded cause were `TimeoutError`
(aiosomecomfort's hard-coded 30 s TCC timeout; see the 2026-07-11 hardening
plan). Read timeouts run at a flat ~8.6% across hours and tiers.

**Impact.**
- AC energy during requested-but-unapplied holds was **~3.7 kWh (~$0.50) for
  the season**.
- Failed reads delayed 72 of 428 engagements, by a median of 30 s.
- Failed releases leave the warmer hold up to its 30-min TTL. That is a
  comfort metric worth watching, not a cost.

**Verdict.** Leave the retry architecture alone: device-owned timed holds and
retry-next-tick absorb the flakiness.

## Reproducibility

`pull` into an empty cache (`THERMAL_MODEL_CACHE=<new dir>`) followed by `fit`,
`counterfactual`, `validate`, `costcurve`, `fourway`, `pjm` and `loads` reproduces every number in this
doc exactly (2026-11-19, data through 2026-11-19 18:00 UTC; DTOD rates from
the Jun–Oct 2026 bills, encoded in `counterfactual.DTOD_PERIODS`).

## Limits

- One summer, one house. Internal gains are a constant (no occupancy or
  cooking schedule). August shows what that costs.
- DTOD rates come from the Jun–Oct 2026 bills. Capacity, fixed charges and the
  Purchased Electricity Adjustment are excluded. The Aug and Sep bills used
  estimated meter reads, which doesn't affect the per-kWh rates used here.
- **Peak-hour bias:** hot-afternoon heat and expensive-slot energy are
  under-predicted by ~1°F and ~30% respectively (see above).
- The thermostat is idealized (no deadband or staging logic) and uses an
  average hourly offset; the real thermostat room runs warmer on hot afternoons.
- All scenarios and plans exclude Arm B spike holds and assume perfect weather foresight.
- Solar uses an approximate site location and treats all irradiance as direct
  beam. It is good enough for a seasonal ratio, not for per-window gains.
- Heating capacity is unidentified, so no winter what-ifs yet.

## Next steps

In priority order:

1. **Identify the continuous leg-B load** with the breaker test above. It
   runs all year, so it is worth more than any schedule change here.
2. **Capacity protection, in observation mode first.**
   - A separate capacity mode, independent of price tiers. It would be
     triggered the day before by the ComEd-zone forecast, then pre-cool to
     23.0°C from 3am and hold 26.5°C 3–6pm, with a comfort ceiling.
   - For the first summer, have it log only: the flag, the forecast and its
     rank, and the action it would have taken and why. It should not move the
     thermostat yet.
   - Treat ≥ 19 GW as a candidate threshold, tuned on one summer. Compare it
     against a ranking rule (e.g. top-N forecast days so far this summer)
     before automating.
3. **Confirm the official peaks and billing.** Check PJM's official 2026 5CP
   and ComEd's zone peak hours against the estimates here, and the June 2027
   bill against the ~2.4–2.7 kW expected.
4. **Selective pre-cool, then peak protection.** If afternoon comfort
   matters, trial **24.0°C night → 23.0°C from 3am → 25.5°C from 1pm, with the
   controller on**, on matched hot days next summer. Compare HVAC $ (supply and
   DTOD), 1–7pm kWh, hours above 78/80°F, and morning lows against the current
   program + controller. Don't change the program for savings before then.
5. **Weather-dependent thermostat offset:** model the thermostat room
   separately on hot afternoons. This is the cheapest fix for half the peak miss.
6. **Winter refit** from mid-December, with the south-vertical solar term.
7. **Day-ahead planner:** if pursued, it is an Arm B design question for the
   2027 experiment, not a mid-season change.
8. Keep the 2027 A/B as the authoritative cost comparison. Hold off on
   batteries until the baseline and the capacity numbers are confirmed.
