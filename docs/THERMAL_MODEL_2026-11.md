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
- **The setback program beats a steady hold on cost.** Steady 75°F costs
  **16–25% more**. The program moves ~1,200 kWh of cooling out of the 2–7pm window.
- **Comfort is cheaper than the program implies.** Holding sleep comfort at
  the program's 75°F, a **fixed pre-cool program keeps afternoons ≤76°F
  instead of 79°F for +2.5–4% (~$10–15 per summer by the model; up to ~$35
  allowing for its afternoon bias)**. A planner using day-ahead
  prices does it for **~8–10% less** than the current program. Each degree of
  afternoon comfort is worth ~$20–40 per summer under fixed schedules and ~$0–4
  with good timing. See [Comfort vs cost](#comfort-vs-cost).
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
Sep 30 values. Bill = actual hourly supply price + 7¢/kWh delivery. The 7¢ is
from 2026 bills: delivery ÷ kWh is 7.3–7.7¢, including the fixed charge.

### Replay check (does the simulator use the right energy?)

Driven by the actual setpoint history, Jun 25 – Sep 30:

| | Simulated | Measured |
|---|---|---|
| HVAC energy | 2,776 kWh | 2,748 kWh |
| Supply cost | $129 | $140 |
| Daily kWh correlation / mean abs error | r = 0.94 / 4.8 kWh | |
| Indoor temp RMSE (continuous, 3 months) | 0.82°F | |

Energy matches within 1%. Supply cost runs ~8% low (sub-hour spike timing), so
**treat the dollar figures as relative, not absolute.**

**This is a consistency check, not an independent prediction.** No parameter
was fit to the energy total, but the total *was* used to choose the one-node
over the two-node structure. The parameter-out-of-sample version is under
[Independent validation](#independent-validation).

### Results (Jun 1 – Sep 30)

Program = the cool setpoints the CTK04 actually ran Jul–Sep: 75°F to noon, 78–79°F
1–7pm, 76°F 8–9pm, 75°F overnight. Stage-1 delivered capacity is only weakly
identified, so two bounds are shown.

**Stage 1 = 50% of capacity (data's best fit):**

| Schedule | kWh | Bill | vs program | Avg supply ¢ paid | 2–7pm kWh | Sleep-hours mean | Afternoon max (median day) |
|---|---|---|---|---|---|---|---|
| **Program (75 / 79 pm)** | 3,212 | **$435** | — | 6.6 | **85** | 75.1°F | 78.3°F |
| Steady 75 | 3,640 | $542 | **+25%** | 7.9 | 1,360 | 74.9°F | 75.7°F |
| Steady 76 | 3,387 | $510 | +17% | 8.1 | 1,301 | 75.7°F | 76.6°F |
| Steady 77 | 3,135 | $478 | +10% | 8.3 | 1,240 | 76.5°F | 77.5°F |

**Stage 1 = 70% of capacity (typical two-stage spec):**

| Schedule | kWh | Bill | vs program |
|---|---|---|---|
| **Program** | 3,171 | **$430** | — |
| Steady 75 | 3,270 | $499 | +16% |
| Steady 76 | 3,037 | $466 | +8% |
| Steady 77 | 2,808 | $434 | +1% |

### Reading the results

- **On energy alone it's close.** Depending on the stage-1 assumption, steady
  holds use −11% to +13% kWh relative to the program.
- **On cost, the program wins in every case.** It runs only ~85 kWh of cooling
  in the 2–7pm window all summer; a steady hold runs ~1,100–1,360 kWh there.
  The average supply price paid rises from 6.6¢ to ~8¢. June's worry was
  confirmed: without the morning pre-cool and afternoon coast, cooling lands in
  peak hours.
- **Steady 77 is the only near-tie, and it is worse for sleep.** Nights run
  ~1.4°F warmer (76.5 vs 75.1°F) for no saving.
- **What the program costs is afternoon comfort.** The house mean reaches
  ~78.3°F on a median day, with ~290 h above 78°F over the summer.
- **Not in the dollar figures, and it favors the program further:** PJM
  capacity (the PLC set by summer-afternoon 5CP hours) bills the following year.
  The program's ~15× lower 2–7pm load directly lowers that.

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
   to minimize (supply price + 7¢ delivery) × kW, subject to the one-node
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

| Strategy | Afternoon max (median day) | Season max | Bill | vs program |
|---|---|---|---|---|
| **Current program** (75 / 79 pm) | 78.3°F | 79.2°F | $381 / $375 | — |
| Fixed 76°F day, 75 night | 76.0 | 76.6–76.9 | $470 / $429 | +23% / +14% |
| Fixed 78°F day, 75 night | 78.0 | 78.4–78.6 | $403 / $379 | +6% / +1% |
| **Derived fixed program, cap 76** | 75.5 | **76.0** | $394 / $384 | **+3.6% / +2.5%** |
| Planner, cap 76, hour-of-day prices | 75.8 | 76.1 | $360 / $369 | −5.6% / −1.5% |
| **Planner, cap 76, day-ahead prices** | 75.7 | 76.1 | $347 / $346 | **−9% / −8%** |
| Planner, cap 76, perfect prices | 75.8 | 76.1 | $313 / $317 | −18% / −15% |

The derived fixed program (house-mean targets) pre-cools through the morning
and coasts up through the afternoon: 75°F overnight → 74°F at 8am → 73°F
10am–noon → rising to 75°F by 5pm → 76°F at 7–8pm → 75°F by 10pm.

### Reading the curve

- **With smart timing, afternoon comfort is almost free.** Once sleep is held
  at 75°F, the optimal plans stop changing above a 77°F cap. The cheapest way
  to reach 75°F by bedtime is not to let the afternoon climb past ~77°F. A
  76°F cap costs $0–4 more than no cap.
- **With fixed schedules, each degree costs ~$20–40 per summer.** Fixed
  76 → 80°F runs $470 → $334 (50% stage-1) and $429 → $322 (70%). "Fixed at
  79/80" look cheaper than the program only because they miss the sleep target:
  pulling down from 80°F runs past 10pm, and nights average 75.8–76.2°F.
- **3°F cooler afternoons for ~$10–15 per summer** with the derived fixed
  program, no forecasting needed. Adjusting for the model's ~30% peak-hour
  under-prediction (it cools more in the afternoon than the program does)
  could raise that to roughly $25–35. This is a trial-worthy change, not a
  settled number.
- **Day-ahead price knowledge is worth ~8–10%** and is robust to the stage-1
  assumption. The hour-of-day planner's −1.5 to −5.6% is not robust and is within
  model error.
- **The ceiling is low in absolute terms.** Even perfect foresight saves
  ~$60–70 over this 14-week window (~15–18% of a ~$380 HVAC cooling bill).
  That bounds the summer payback for any HVAC-timing investment.
- **Bias direction:** every optimized plan cools *more* in the afternoon than
  the program (~160–500 vs ~82 kWh in 2–7pm). That is exactly where the model
  under-predicts load, so the optimized savings are optimistic. Of the
  optimized plans, the day-ahead results have the most margin.

## Reproducibility

`pull` into an empty cache (`THERMAL_MODEL_CACHE=<new dir>`) followed by `fit`
and `counterfactual` reproduces every number in this doc exactly
(2026-11-19, data through 2026-11-19 18:00 UTC).

## Limits

- One summer, one house. Internal gains are a constant (no occupancy or
  cooking schedule). August shows what that costs.
- **Peak-hour bias:** hot-afternoon heat and expensive-slot energy are
  under-predicted by ~1°F and ~30% respectively (see above).
- The thermostat is idealized (no deadband or staging logic) and uses an
  average hourly offset; the real thermostat room runs warmer on hot afternoons.
- All scenarios and plans exclude Arm B spike holds and assume perfect weather foresight.
- Solar uses an approximate site location and treats all irradiance as direct
  beam. It is good enough for a seasonal ratio, not for per-window gains.
- Heating capacity is unidentified, so no winter what-ifs yet.

## Next steps

1. **Trial the derived fixed program** for a few hot weeks next summer.
   Matched-day comparison against the current program is the real test of the
   "3°F cooler for ~$10–35" claim and of the model's afternoon bias.
2. **Weather-dependent thermostat offset:** model the thermostat room
   separately on hot afternoons. This is the cheapest fix for half the peak miss.
3. **Winter refit** from mid-December, with the south-vertical solar term.
4. **Day-ahead planner:** if pursued, it is an Arm B design question for the
   2027 experiment, not a mid-season change.
5. Keep the 2027 A/B as the authoritative cost comparison.
