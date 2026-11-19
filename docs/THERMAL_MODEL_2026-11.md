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

- **Yes, the data supports a model.** A one-node RC model predicts the house
  temperature a full day ahead with 0.85°F error on held-out September. The
  "temperature stays where it started" baseline errs by 1.9°F. Fitted
  separately for June, July and August, it gives the same building: a ~40 h
  time constant every month. Driven by the actual setpoint history, it
  reproduces the summer's HVAC energy within 1%.
- **The setback program beats a steady hold on cost.** Every steady hold from
  75 to 77°F costs the same or more over Jun–Sep. Steady 75°F costs **16–25%
  more** on the bill. The program wins because it moves ~1,200 kWh of cooling
  out of the 2–7pm window, where prices are high.
- **Solar gain is seasonal (new finding).** The model's sun sensitivity rises
  every month from June to November. That fits south-facing glass taking in
  the low autumn sun.
- **Heating is not modeled yet.** The furnace has barely run, so it can't be fit.

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
- **Cooling.** The AC at full removes about 0.9°F/h. On a hot evening (85°F
  out, 77°F in) the envelope and internal gains add back ~0.4°F/h, so net
  pulldown is ~0.5°F/h. (June measured ~1°F/h recoveries under the old 73°F
  program. The gap is unexplained; it may be the house mean lagging the
  thermostat's own room.)
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

## Limits

- One summer, one house. Internal gains are a constant (no occupancy or cooking schedule).
- The thermostat is idealized (no deadband or staging logic). The replay check
  suggests this is adequate for seasonal energy.
- Scenarios exclude Arm B spike holds. These would trim peak-price cooling under
  any schedule, and they are what the 2027 experiment measures.
- Solar uses an approximate site location and treats all irradiance as direct
  beam. It is good enough for a seasonal ratio, not for per-window gains.
- Heating capacity is unidentified, so no winter what-ifs yet.

## Next steps

1. **Winter refit** once there are a few weeks of real furnace runtime
   (mid-December onward), with the south-vertical solar term.
2. **Evening recovery shape** (optional): the program's 7–11pm pulldown is now
   most of its cost. The model can test a staggered 79→77→75 descent against
   the current step, using the same replay-gated method.
3. Keep the 2027 A/B as the authoritative cost comparison. Use this model to
   pick candidate schedules, not to replace the experiment.
