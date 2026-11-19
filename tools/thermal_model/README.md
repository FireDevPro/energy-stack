---
date: 2026-11-19
owner: chris
status: active
role-label: analysis
---

# thermal_model: grey-box RC model of the house

Workstation tool that fits a physics-based (resistance-capacitance) thermal model
of the house from pi-lab telemetry. It also runs closed-loop what-if
simulations of thermostat schedules on real weather and ComEd prices. Findings and
caveats are in [`docs/THERMAL_MODEL_2026-11.md`](../../docs/THERMAL_MODEL_2026-11.md).

Read-only against Influx. Nothing here touches the controller or the running stack.

## Run

From the repo root, in a Python 3.13 env with `tools/analysis/requirements.txt`
installed (numpy, pandas, scipy):

```bash
python -m tools.thermal_model pull            # SSH to pi-lab, pull May 26 -> now into tools/thermal_model/.data/
python -m tools.thermal_model fit             # validation fit, monthly stability, fall/solar diagnosis
python -m tools.thermal_model counterfactual  # replay check + steady-hold vs setback scenarios
python -m tools.thermal_model validate        # leave-one-month-out energy, peak events, pulldown rates
python -m tools.thermal_model costcurve       # cheapest cooling per afternoon cap (LP planner)
python -m tools.thermal_model fourway         # schedule x controller, rev 4.1 controller emulated
python -m tools.thermal_model pjm             # PJM forecasts: spike predictability, forecast-gated strategies, 5CP
```

Set `THERMAL_MODEL_CACHE=<dir>` to pull into a separate cache. Running `pull` into
an empty directory reproduces every number in the doc.

`pull` uses `ssh chris@192.168.20.10 docker exec influxdb influx query`, so no
Influx token is needed locally. It caches month-chunked 15-min CSVs and is
re-runnable (`--refresh` forces a re-pull). Results are written as JSON to
`.data/results/`. The `.data/` directory is gitignored.

## Files

| File | What it does |
|---|---|
| `data.py` | Influx pull + assembly into one 15-min frame (local time); unit and naming fixes |
| `model.py` | RC model, whole-day simulation fit, scoring |
| `solar.py` | Horizontal -> south-vertical irradiance approximation (seasonal window gain) |
| `counterfactual.py` | Closed-loop thermostat simulation, kW curve, cost, scenario programs |
| `validate.py` | Leave-one-month-out energy replay, peak-event and pulldown checks |
| `controller_sim.py` | Port of the rev 4.1 spike controller (tiers, warm-only targets, humidity gate, 10-min publish lag), checked against `hvac.actions` |
| `pjm_value.py` | Day-ahead/day-of predictors, forecast-triggered strategies (controller on), ComEd 10-hour capacity estimate (5 PJM + 5 ComEd peaks, billing factors), circuit breakdown + EAGLE check, selective capacity protocols |
| `optimize.py` | LP planner (perfect / day-ahead / hour-of-day price beliefs), re-priced through the simulator; derived fixed programs |
| `__main__.py` | CLI and the fixed analysis windows |

## Data traps this code handles

- **Model target is the Ecowitt rooms, not the thermostat.** `house_f` = mean of
  `ch2_temp_f` (2F hall), `ch3_temp_f` (2F south bedroom) and `indoor_temp_f`
  (gateway, 1F office). These sensors step in 0.18°F every minute. The
  thermostat reports whole display units only, which is what defeated the
  May/June fits.
- **`hvac.thermostat` `*_f` fields hold °C since the TCC cutover (~2026-06-22).**
  This is a deliberate display-unit pass-through (see `thermostat_poller/tcc_client.py`)
  and is converted back here (value < 40 ⇒ °C).
- **Two different fields share the name `indoor_temp_f`:** `ecowitt.weather` (the
  gateway) and `hvac.thermostat`. They are pulled separately, and the
  thermostat one is renamed `tstat_indoor_raw`.
- **`pjm.lmp_da_hourly`** (ComEd zone, $/MWh) starts ~2026-06-24 with small gaps.
  Planner gaps are filled with the typical hour-of-day price.
- **`comed.prices` carries two `period_type`s** (`5min`, `hourly_avg`). Only `5min` is pulled.
- **Refoss comes from `energy-longterm` 1-min aggregates.** Six months of raw
  30 s data from three channels is too heavy a query for the Pi.
- `ch8_temp_f` (≈3.8°F below the house mean, presumed basement) and Haven
  return-air temperature (sd 2°F vs the rooms) are pulled but not used as the target.

## Why one-node, not two-node

The 2R2C (air + mass) model fits single days as well as 1R1C. In a continuous
closed-loop replay of the summer, though, it uses 4,690 kWh against the
measured 2,748. Day-by-day fitting re-initialises the hidden mass temperature
every midnight, so the fit parks solar heat in the mass and never pays for
releasing it overnight. Fitting on 7-day blocks did not fix it. 1R1C replays at
2,776 kWh (1% off), so it is the primary model. Gate any future model change
on the same replay check, and on `validate` (which holds out parameters, not
structure).

Known weakness: hot-afternoon heat and the energy in the most expensive hours
are under-predicted (~1°F, ~30%). Don't use the model to time individual heat
events or recoveries.
