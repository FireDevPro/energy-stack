---
date: 2026-05-21
owner: chris
status: draft
role-label: operator-checklist
name: THERMAL_COMMISSIONING_CHECKLIST
related:
  - docs/THERMAL_COMMISSIONING.md
---

# Thermal Commissioning — Operator Checklist

Step-by-step playlist for running the three thermal-commissioning
methods. Rationale, citations, and method comparison are in
[THERMAL_COMMISSIONING.md](THERMAL_COMMISSIONING.md). This file is the
"what to do."

---

## Prerequisites (one-time, before any test)

- [ ] WH31 ecowitt sensor #1 installed within 12 inches of the
      thermostat. Confirm it pairs to GW1200 on a free channel (2-8).
- [ ] WH31 ecowitt sensor #2 installed next to the RedLINK upstairs
      sensor (primary bedroom). Same pairing process.
- [ ] Confirm both new channels write to InfluxDB
      (`ecowitt.weather` measurement, fields `chN_temp_f`, `chN_rh_pct`,
      `chN_dewpoint_f`).
- [ ] Wait ≥ 24 hours for sensors to thermally equilibrate after install.
- [ ] Confirm Ecowitt outdoor sensor (`ch1_temp_f`) and solar (`solar_wm2`)
      are still writing — see `deploy/energy-stack/scripts/fit_thermal_observer.py`
      (script defaults already point at these fields).

---

## Method 1 — Planned Passive Decay Night

The high-rigor calibration test. Goal: one clean exponential decay
curve over a 6-8 hour window with HVAC fully off.

### Picking the night

- [ ] Check NWS 48-hour forecast for the upcoming nights. Look for:
  - Overnight low **stable within 4°F across 10pm-5am**
  - **Calm or near-calm wind** (< 5 mph mean overnight)
  - **Clear or thin overcast** (avoid thick cloud cover and fronts)
  - **No precipitation forecast**
- [ ] Confirm the night's overnight low gives **≥ 18°F initial ΔT**
      from your target indoor temp. May/June in Chicago at indoor 70°F:
      overnight outdoor ≤ 52°F is the threshold.
- [ ] Schedule the night on the operator calendar. Avoid Sunday
      nights (Monday transition disruption per ARM_TRANSITIONS.md).

### Day-of preparation (afternoon before)

- [ ] Note conditions in operator log:
      `docs/replay-validation/<date>-thermal-decay/log.md`
      (create new dated folder)
- [ ] Pre-cool the house to ~70°F by ~9pm using normal HVAC operation.
      Stage 2 OK during pre-cool, but stop with stage 2 off (let the
      system finish on stage 1) so the coil isn't ice-cold at test
      start.
- [ ] Confirm Influx is writing high-resolution indoor temp from at
      least one WH31 (visible in cockpit or via direct query).

### Starting the test (target: 10pm local)

- [ ] At 10pm: at the thermostat, set HVAC mode to **OFF** (not "auto",
      not "heat", actually off). Document the exact time.
- [ ] Close interior doors that are normally closed; leave open the
      ones normally open. Do not "stage" the house — match normal.
- [ ] Stop heat-generating activities (oven, dryer, etc.) for the
      duration of the test.
- [ ] Document everything: doors, occupant location, anything running
      (refrigerator, computers, etc.). These notes go in the log.

### During the test (10pm-5am)

- [ ] If you wake up: do not check the thermostat or open the front
      door more than needed. Each open is a perturbation.
- [ ] If interior temp climbs above 78°F or below 60°F unexpectedly,
      abort and document why.

### Ending the test (target: 5am or when conditions break)

- [ ] At 5am (or earlier if needed): turn HVAC back on. Document exact
      time and the indoor + outdoor temps at end-of-test.
- [ ] Resume normal operation.

### Post-test analysis (next day)

- [ ] Pull the indoor and outdoor temperature traces over the test
      window from Influx.
- [ ] Verify against the abort conditions: outdoor variation ≤ 4°F
      across window, no precipitation, no large wind events.
- [ ] Run the decay fit (planned: add `scripts/fit_decay_night.py`).
      Outputs: τ_hours with confidence interval, residual plot.
- [ ] **Acceptance criteria for the night to count:**
  - Fit R² ≥ 0.95
  - τ within plausibility range (2-48 hours per design doc)
  - No visible structural residuals (look at the plot)
- [ ] File the result in `docs/replay-validation/<date>-thermal-decay/results.md`.
- [ ] If the fit fails any criterion: document why, do not use, plan
      another night.

### Number of nights

- Run **at least 2 nights** across different outdoor temperature
  baselines (e.g., one with overnight low 45°F, one with overnight
  low 60°F). Three is better. Stop when the τ estimates agree within
  ±15%.

---

## Method 2 — Continuous Opportunistic Fit (passive — no operator action)

Already running via `thermal_observer`. The operator role is mostly
monitoring.

- [ ] Once WH31 data has accumulated for 3 days, re-run the observer
      with the new indoor source:
      `python scripts/fit_thermal_observer.py --indoor-measurement ecowitt.weather --indoor-temp-field chN_temp_f`
      (the `--indoor-measurement` and `--indoor-temp-field` arguments
      do not yet exist — they are a planned small PR. Until then, the
      observer reads from `hvac.thermostat`.)
- [ ] After the indoor-source PR lands, schedule the observer to run
      weekly on Pi-lab and compare τ to Method 1.
- [ ] Watch for any new rejection reasons. Document in the operator log.

---

## Method 3 — Bus-Derived Steady-State UA (peak summer only)

DO NOT run this method in May or June. See methodology doc for why.

### When to run

- After **at least one week of sustained 90°F+ outdoor highs** has
  produced enough long stable cooling runs.
- Realistically: late June at the earliest, July typical.

### Conditions for a valid sample

- [ ] System has been running at stable `cool_actual_pct` for **≥ 30
      minutes** (coil at op temp, system in steady-state).
- [ ] **ΔT (outdoor - indoor) ≥ 15°F.** Smaller ΔT and the formula
      systematically inflates.
- [ ] Outdoor temperature has been stable (< 2°F variation) over the
      sample window.
- [ ] No setpoint change in the prior 30 minutes.

### What to compute

```
UA_implicit = (cool_actual_pct/100 · 48000) / (T_out - T_in)
```

Where 48,000 BTU/hr is the ASXC16 4-ton rated capacity. Update if the
unit changes.

### Acceptance criteria

- [ ] Sample count ≥ 30 across at least 3 different outdoor temperature
      bins (e.g., 85-90°F, 90-95°F, 95°F+).
- [ ] Median UA in the **500-1,500 BTU/hr/°F** range for typical
      residential construction. Outside that range means a physical
      problem (charge, leakage) or a math error.
- [ ] Cross-check: UA from Method 1 × m·Cp (assume m·Cp ≈ 8,000-12,000
      BTU/°F for a 2,500 sq ft single-family home) should give a τ
      consistent with the Method 1 result within ±25%.

---

## Cross-Validation Decision Rules

After running Methods 1 and 2 (Method 3 deferred until summer):

| Comparison | Within ±15% | Within ±15-30% | Beyond ±30% |
|---|---|---|---|
| Method 1 τ vs Method 2 τ | "Commissioned" — both methods agree, scheduler can consume | "Provisional" — use lower-confidence value, plan more decay nights | "Disputed" — investigate before SCED start |

**If disputed**, debug in this order:

1. Check the indoor sensor on Method 1 — was it actually high-res, or
   did we accidentally use the thermostat?
2. Check Method 2 filters — is the setpoint mask dropping most data?
   Is the outdoor source the same in both methods (ecowitt
   `ch1_temp_f`)?
3. Re-run Method 1 on a different night. If it still disagrees, the
   issue is in Method 2's data filtering or model structure.
4. Add Method 3 (with summer data) as a tiebreaker.

---

## Log Template

Use this in `docs/replay-validation/<YYYY-MM-DD>-thermal-decay/log.md`:

```yaml
---
date: YYYY-MM-DD
test_type: thermal-decay-night
window_start_local: HH:MM
window_end_local: HH:MM
indoor_temp_start_f: X.X
indoor_temp_end_f: X.X
outdoor_temp_start_f: X.X
outdoor_temp_end_f: X.X
outdoor_max_variation_f: X.X
wind_mean_mph: X.X
wind_max_mph: X.X
sky_condition: clear|overcast|partly_cloudy
precipitation: none|light|heavy
abort: false|true
abort_reason: (if applicable)
indoor_sensor_used: ch2_temp_f|ch3_temp_f|thermostat
---

# Conditions and notes

(free-form, e.g., "doors closed normally, single occupant in
2nd-floor bedroom, refrigerator and one PC running")

# Result

tau_hours: X.X
confidence_interval_hours: [X.X, X.X]
fit_r_squared: 0.XX
accepted: true|false
notes: ...
```

---

## Open follow-ups (track in this file)

These are from the methodology doc but live here too as operator-facing
TODOs:

- [ ] Add `--indoor-measurement` and `--indoor-temp-field` args to
      `fit_thermal_observer.py` (small PR, similar to the
      ecowitt-canonical defaults fix from 2026-05-21).
- [ ] Write `scripts/fit_decay_night.py` — pulls a time window from
      Influx, fits the exponential decay, plots residuals. Used by
      the Method 1 post-test step above.
- [ ] Audit the setpoint mask and fan-only filter behavior in
      `thermal_observer.py` against the design doc's intent.
- [ ] Update the binding spec to state explicitly that "indoor
      temperature" in the SCED definition refers to the upstairs
      RedLINK sensor air, not house-average.
