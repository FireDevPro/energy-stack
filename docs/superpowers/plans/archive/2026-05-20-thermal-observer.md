# Thermal Observer Implementation Plan

**Status:** Implemented as a strict read-only observer on 2026-05-20.

## Goal

Build a separate thermal observer that fits the house's warm-up/cool-down
behavior from existing telemetry and prints diagnostics for inspection.

This slice is intentionally non-control and non-persistent:

- No thermostat setpoint writes.
- No `hvac-scheduler` integration.
- No safety-bound changes.
- No experiment-assignment changes.
- No Docker service changes.
- No derived InfluxDB measurement writes.
- No JSON artifact writes.

## Inputs

The observer only reads existing InfluxDB fields:

- `hvac.thermostat`: `indoor_temp_f_hires` (fractional, ~0.18°F resolution, preferred for the fit) or `indoor_temp_f` (whole-degree, fallback for data predating the field's addition); `cool_setpoint_f`
- `hvac.comfortnet`: `cool_actual_pct`, `heat_actual_pct`
- Configured local weather measurement, default `ecowitt.outdoor`:
  `outdoor_temp_f`, `solar_radiation_w_m2`

`cool_setpoint_f` is read only to infer setpoint-change masks. It is never
modified.

## Files

- `deploy/energy-stack/scripts/thermal_observer.py`
  - Pure fit engine: dataclasses, sample filtering, OLS fit, validation gates.
- `deploy/energy-stack/scripts/thermal_observer_influx.py`
  - Flux query builder and row-to-sample parsing only.
- `deploy/energy-stack/scripts/fit_thermal_observer.py`
  - CLI entrypoint. Queries telemetry, runs the fit, prints diagnostics.
- `deploy/energy-stack/scripts/tests/test_thermal_observer.py`
  - Synthetic fit, filtering, and acceptance-gate tests.
- `deploy/energy-stack/scripts/tests/test_thermal_observer_influx.py`
  - Query and row parsing tests.
- `deploy/energy-stack/scripts/tests/test_fit_thermal_observer_cli.py`
  - Guard that a normal CLI run does not request an Influx write API and does
    not create the deprecated JSON output path.
- `deploy/energy-stack/scripts/README.md`
  - Operator usage and printed diagnostics.
- `docs/archive/THERMAL_MODEL_DESIGN.md`
  - Design note distinguishing the current observer from future scheduler
    integration.

## Fit Method

For each valid interval, fit:

```text
dT_in/dt = a_env * (T_out - T_in)
          + c_stage1 * I[stage1_active]
          + c_stage2_delta * I[stage2_active]
          + a_solar * solar_radiation_w_m2
          + intercept
```

Reported diagnostics include:

- `tau_hours = 1 / a_env`
- `stage1_cooling_f_per_hr = -c_stage1`
- `stage2_cooling_f_per_hr = -(c_stage1 + c_stage2_delta)`
- train/test RMSE
- persistence RMSE
- holdout skill score
- filter counts
- rejection reasons

## Filtering

Drop intervals for:

- gaps or non-positive timestamps
- HVAC stage transitions
- 30 minutes after any cooling setpoint change
- heating-active samples
- missing or non-finite telemetry

Rows missing required HVAC/weather fields are skipped rather than coerced to
zero.

## Acceptance Gates

Fits are marked accepted only when all gates pass:

- enough valid intervals
- enough stage-1-only training intervals
- enough stage-2 training intervals
- `tau_hours` within configured physical bounds
- positive stage-1 cooling
- stage-2 cooling within configured physical bounds
- stage-2 cooling is not less than stage-1 cooling
- holdout skill score is available and meets `min_skill_score`

Accepted/rejected is diagnostic only; it does not feed any controller.

## Operator Workflow

Manual run:

```bash
cd ~/energy-stack/scripts
source .venv/bin/activate
set -a; source ~/energy-stack/.env; set +a
export INFLUX_URL="${INFLUXDB_URL:-${INFLUX_URL:-http://localhost:8086}}"
export INFLUX_TOKEN="${INFLUXDB_TOKEN:-${INFLUXDB_INIT_ADMIN_TOKEN:-${INFLUX_TOKEN}}}"
export INFLUX_ORG="${INFLUXDB_ORG:-${INFLUXDB_INIT_ORG:-${INFLUX_ORG}}}"
export INFLUX_BUCKET="${INFLUXDB_BUCKET:-${INFLUXDB_INIT_BUCKET:-${INFLUX_BUCKET}}}"
python fit_thermal_observer.py --window-days 14
```

`--dry-run` and `--output-json` are accepted only as no-op compatibility flags.
All runs are read-only.

Suggested cron logs stdout only:

```cron
17 3 * * * bash -lc 'cd ~/energy-stack/scripts && . .venv/bin/activate && set -a; . ~/energy-stack/.env; set +a; export INFLUX_URL="${INFLUXDB_URL:-${INFLUX_URL:-http://localhost:8086}}"; export INFLUX_TOKEN="${INFLUXDB_TOKEN:-${INFLUXDB_INIT_ADMIN_TOKEN:-${INFLUX_TOKEN}}}"; export INFLUX_ORG="${INFLUXDB_ORG:-${INFLUXDB_INIT_ORG:-${INFLUX_ORG}}}"; export INFLUX_BUCKET="${INFLUXDB_BUCKET:-${INFLUXDB_INIT_BUCKET:-${INFLUX_BUCKET}}}"; python fit_thermal_observer.py --window-days 14 >> /var/log/thermal-observer.log 2>&1'
```

## Verification

Local verification for this slice:

```bash
.venv/bin/python -m pytest deploy/energy-stack/scripts -q
.venv/bin/python deploy/energy-stack/scripts/fit_thermal_observer.py --help
git diff --check
```

Full stack wrapper verification may still require private/non-PyPI dependencies
such as `pyControl4`.

## Future Work

Persisting observer diagnostics, writing a model artifact, or using fit output
for pre-cool/coast decisions requires a separate post-experiment plan and an
explicit review of safety and OSF constraints.
