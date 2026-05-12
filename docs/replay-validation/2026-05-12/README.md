---
date: 2026-05-12
owner: chris
status: locked
role-label: chris
---

# Real-shape replay validation — 2026-05-12

Pre-OSF-filing audit artifact. Demonstrates the analysis pipeline can run end-to-end against a live Stage 1 export from pi-lab InfluxDB, with every empty output reason-coded for a classifiable cause rather than caused by silent schema mismatches.

## Source bundles

| Window | Path | Generated |
|---|---|---|
| 7-day | `7d/inspection.json` + `manifest.json` | 2026-05-12 08:20 UTC |
| 90-day | `90d/inspection.json` + `manifest.json` | 2026-05-12 08:20 UTC |

Both bundles were produced by running `tools.analysis.pipeline` (full Stages 1–9) against `192.168.20.10` (pi-lab) via the SSH-tunneled Influx port. The exports themselves (parquet files) are large and ephemeral and live under `analysis/exports/` locally; only the manifest and the inspection summary are committed.

## How to reproduce

```bash
# SSH tunnel to pi-lab Influx
ssh -fN -L 18086:localhost:8086 chris@192.168.20.10

# Pull credentials from pi-lab .env
TOK=$(ssh chris@192.168.20.10 "grep '^INFLUXDB_INIT_ADMIN_TOKEN=' ~/energy-stack/.env | cut -d= -f2-")
export INFLUXDB_INIT_ADMIN_TOKEN="$TOK" \
       INFLUXDB_INIT_ORG=depaola-home \
       INFLUXDB_INIT_BUCKET=energy \
       INFLUXDB_URL=http://localhost:18086

# 7-day run
START=$(python -c "import datetime; print((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat())")
END=$(python -c "import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())")
python -m tools.analysis.pipeline --start "$START" --end "$END" --out analysis/exports/7d

# 90-day run
START=$(python -c "import datetime; print((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)).isoformat())")
python -m tools.analysis.pipeline --start "$START" --end "$END" --out analysis/exports/90d

# Inspect both
python -m tools.analysis.replay_inspect analysis/exports/7d/<run_ts>
python -m tools.analysis.replay_inspect analysis/exports/90d/<run_ts>
```

## Findings surfaced and fixed

The replay validation surfaced four schema-drift / production-shape bugs that prevented the pipeline from producing correct output against real data. All four were fixed in dedicated PRs before this artifact was captured:

| # | PR | Issue | Severity |
|---|---|---|---|
| 1 | [#94](https://github.com/Promithius-DR/energy-stack/pull/94) | Stage 2/3 loaders filtered `_field=energy_wh`; production refoss writes only `power_w` + cumulative kWh counters. | Silently returned 0 kWh against real data. |
| 2 | [#95](https://github.com/Promithius-DR/energy-stack/pull/95) | `hvac.actions` mixes numeric + string `_value` rows; pyarrow rejected parquet write. | Pipeline crashed mid-export. Fixed by splitting `_value` (float64) + `_value_text` (object), preserving audit-signal strings. |
| 3 | [#96](https://github.com/Promithius-DR/energy-stack/pull/96) | `comed.prices` schema: producer writes `price_cents_per_kwh` with `period_type` tag; flux + Stage 2/3 loaders filtered `price_cents`. | Stage 2 Rule 3 silently counted zero observations; Stage 3 dollar arithmetic saw zero prices. |
| 4 | [#97](https://github.com/Promithius-DR/energy-stack/pull/97) | `comed.bill_lineitems.flux` query file did not exist; Stage 1 silently skipped the measurement. | Stage 6 Layer 3 had no Capacity Charge rows to sum even when bills were present. Audit test added to catch the bug class at PR time. |

## Final state (this artifact)

### 7-day window
- **12 measurements populated** with real data: `comed.prices`, `ecowitt.weather`, `hvac.5cp_state`, `hvac.actions`, `hvac.comfortnet`, `hvac.decisions`, `hvac.price_overlay`, `hvac.thermostat`, `nws.forecast`, `pjm.inst_load`, `pjm.metered_load`, `refoss.channel`.
- **6 measurements legitimately empty**, all reason-coded `measurement_empty_in_window`:
  - `comed.bill` / `comed.bill_lineitems` — bills arrive ~monthly; the 7-day window misses the cycle. Present in the 90-day window.
  - `hvac.arm_transitions` — pre-experiment; randomization starts 2026-06-01.
  - `hvac.overrides` — no manual setpoint overrides in the window.
  - `hvac.precool_window` — scheduler not yet writing precool decisions (pre-experiment).
  - `pjm.coincident_peak` — annual scrape; last loaded mid-October 2024, outside the window.
- **Downstream propagation:** every Stages 2–9 output carries a reason code explaining its emptiness (`no_arm_assignments_in_window` → `no_qualifying_weeks_from_stage2` → `insufficient_qualifying_weeks_per_arm` → `no_primary_quality_pairs` → `no_pjm_5cp_hours_in_window` → `no_pair_differences_from_stage5`). All explanations chain correctly to the actual upstream cause.

### 90-day window
- **14 measurements populated**, adding `comed.bill` (36 rows / 3 bills) and `comed.bill_lineitems` (195 rows / 13 line items per bill × 3 bills × multiple field types) compared to the 7-day window.
- **4 measurements legitimately empty**, same reason codes as the 7-day window for the slow-cadence cases:
  - `hvac.arm_transitions` (pre-experiment)
  - `hvac.overrides` (no manual overrides)
  - `hvac.precool_window` (pre-experiment)
  - `pjm.coincident_peak` (annual; last in Oct 2024)
- **Downstream propagation:** same chain as 7-day. The 90-day window does NOT change the downstream state because Stage 2 still finds no arm assignments (randomization starts 2026-06-01).

## Sanity highlights (from `inspection.json`)

- **`refoss.channel` mains coverage:** 7d run reports 40,320 nonzero `em:1+em:7` power_w rows; 90d run reports 71,356. HVAC channels (em:2+em:8+em:9): 60,237 (7d) and 106,157 (90d). Aggregator path returns sensible nonzero values.
- **`comed.prices` coverage:** 7d run reports 1,993 `period_type=5min` rows with 1,779 nonzero prices; 90d run reports 7,813 / 6,498. Rule 3 has data to check coverage with.

## What the artifact does NOT prove

- **It does not prove correctness for the experiment-running phase.** Stage 2's `no_arm_assignments_in_window` propagates because randomization hasn't started yet. The first post-2026-06-01 weekly run is the actual experiment-time validation.
- **It does not exercise Stage 8 / Stage 9 loaders against real data.** Those loaders remain stub-returns-None in `tools/analysis/pipeline.py`; their CSV outputs are header-only by design until those PRs land.
- **It does not validate detector accuracy with real `hvac.5cp_state` data.** Stage 6 detector_accuracy reasons out with `no_pjm_5cp_hours_in_window` because `pjm.coincident_peak` is annual and outside the window. A summer 2026 window run (post-July, after PJM publishes the 2025 PDF) will exercise the detector with real predictions vs real truth.

## Filed artifacts

- `7d/manifest.json` — locked source bundle for the 7-day run (window, sha256 per measurement, source_type per entry, known_missing list).
- `7d/inspection.json` — per-stage populated / header-only / row-count / reason-code summary with per-measurement sanity heuristics.
- `90d/manifest.json` — same shape, 90-day window.
- `90d/inspection.json` — same shape.

All four files are JSON with sorted keys for cross-run diff stability.
