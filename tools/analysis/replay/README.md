# Real-data replay export

Contract for satisfying [OSF_FILING.md](../../../docs/OSF_FILING.md) criterion 14 (real-shape replay validation). The analysis pipeline's Stage 1-9 must run end-to-end against an export of recent live Influx data, producing each stage's declared output schema with non-empty rows where the export window contains the required source measurements, and machine-readable reason codes where it does not.

## Components

- `manifest.py` — schema for the export manifest (JSON file describing the export window, measurements present, row counts, SHA-256 hashes, and known-missing measurements with reason codes).
- `reason_codes.py` — enumeration of legitimately-empty-output reasons. Stages emit these via `reason_report.json` sidecars.
- `weather_compat.py` — weather-derived Ecowitt compatibility converter. Builds Ecowitt-shaped parquet from IEM ASOS + Open-Meteo / ERA5 for pre-2026-05-11 windows where Ecowitt didn't yet exist. Source-type `weather_derived_compatibility`. See [`docs/REPLAY_VALIDATION.md`](../../../docs/REPLAY_VALIDATION.md) for the wider catalog.
- `__init__.py` — re-exports the public API.

## Weather compatibility (pre-deployment windows)

For windows before Ecowitt receiver deployment (2026-05-11), use `weather_compat` to build a synthetic-but-real-data Ecowitt-shaped parquet:

```bash
# Build a compat bundle for a single summer week:
python -m tools.analysis.replay.weather_compat fetch \
    --station KORD \
    --start 2025-07-14T05:00:00+00:00 \
    --end   2025-07-21T05:00:00+00:00 \
    --out   analysis/exports/compat-2025-W29

# Merge it into an existing stage1 bundle:
python -m tools.analysis.replay.weather_compat merge \
    --compat-dir         analysis/exports/compat-2025-W29 \
    --target-stage1-dir  analysis/exports/20260511T220000Z/stage1
```

The converter:

- Pulls IEM ASOS bulk CGI (`report_type=1,3,4` — HFMETAR 5-min plus routine and special METAR) for `tmpf`, `dwpf`, `relh`, `sknt`, `mslp`.
- Pulls Open-Meteo Historical Weather API (ERA5-Land backed) for `shortwave_radiation` (solar; ASOS has no solar field) and for the same five ASOS-equivalent fields (used as gap-fill when ASOS has a ≥60-minute outage).
- Emits long-format parquet on a 5-min UTC grid. Forward-fill from the nearest preceding native observation within 60 minutes; slots beyond the gap threshold fill from ERA5. No interpolation.
- Tags every row with per-row provenance columns (`weather_source`, `solar_source`, `station`, `cadence`, `upsampled`). These are audit-only; the Stage 3 loader ignores them.
- Writes a manifest entry tagged `source_type=weather_derived_compatibility` per the source-type catalog.

These rows test that the pipeline's `ecowitt.weather` loader handles real-shape data. They do NOT claim Ecowitt-the-instrument recorded these values. See [`docs/REPLAY_VALIDATION.md`](../../../docs/REPLAY_VALIDATION.md#weather_derived_compatibility).

## Export procedure

Operator-run, with Influx credentials. Pi-lab has the live Influx instance; export runs from the operator's machine (Windows) against pi-lab's exposed Influx port.

```bash
# Recent-window export (default: last 14 days for criterion 14(a)):
python -m tools.analysis.pipeline \
    --stage 1 \
    --start "$(date -u -d '14 days ago' --iso-8601=seconds)" \
    --end "$(date -u --iso-8601=seconds)" \
    --out analysis/exports/$(date -u +%Y%m%dT%H%M%SZ)
```

The Stage 1 extract writes:

```
analysis/exports/20260511T220000Z/stage1/
  manifest.json
  comed.bill.parquet           (if any rows in window)
  comed.prices.parquet
  ecowitt.weather.parquet      (post-2025 measurement; only present after receiver deploy)
  hvac.5cp_state.parquet       (post-2025 measurement)
  hvac.actions.parquet
  hvac.arm_transitions.parquet (post-2025 measurement)
  hvac.comfortnet.parquet
  hvac.decisions.parquet
  hvac.overrides.parquet
  hvac.precool_window.parquet  (post-2025 measurement)
  hvac.price_overlay.parquet   (post-2025 measurement)
  hvac.thermostat.parquet
  nws.forecast.parquet
  pjm.coincident_peak.parquet  (yearly; only present in summer/post-summer windows)
  pjm.inst_load.parquet
  refoss.channel.parquet
```

Plus a `manifest.json` with the canonical description per `manifest.Manifest`.

## Manifest format

```json
{
  "export_window_start_ct": "2026-04-27T00:00:00-05:00",
  "export_window_end_ct": "2026-05-11T00:00:00-05:00",
  "source_bucket": "energy",
  "exported_at_utc": "2026-05-11T22:00:00Z",
  "exporter": {
    "version": "stage1_extract",
    "commit_hash": "abc1234..."
  },
  "measurements": {
    "comed.prices": {
      "measurement": "comed.prices",
      "parquet_path": "comed.prices.parquet",
      "row_count": 8064,
      "sha256": "ab12cd34...",
      "field_set": ["price_cents"],
      "first_timestamp_utc": "2026-04-27T05:00:00Z",
      "last_timestamp_utc": "2026-05-11T04:55:00Z"
    },
    ...
  },
  "known_missing_measurements": [
    {
      "measurement": "hvac.arm_transitions",
      "reason_code": "no_arm_assignments_in_window",
      "note": "Window 2026-04-27 to 2026-05-11 is pre-randomization (starts 2026-06-01); no transitions exist yet."
    }
  ]
}
```

## Reason codes

Pre-registered enum in `reason_codes.py`. Each downstream stage that produces an empty outcome table writes a `reason_report.json` sidecar with one or more entries explaining why. The audit script can grep these codes to distinguish "the pipeline ran and found nothing" from "the pipeline ran successfully and produced findings."

Example for a pre-randomization export:

```json
{
  "entries": [
    {
      "stage": "stage4",
      "output_file": "matched_pairs.csv",
      "reason_code": "single_arm_in_window",
      "note": "Window contains only Arm A weeks; need both arms to form pairs.",
      "related_inputs": ["weekly.csv"]
    },
    {
      "stage": "stage7",
      "output_file": "sced_pvalues.csv",
      "reason_code": "no_pair_differences_from_stage5",
      "note": "Stage 5 produced no pair differences (Stage 4 had no pairs).",
      "related_inputs": ["pair_diffs.csv"]
    }
  ]
}
```

## Filing gate (criterion 14(c))

When invoked on a real-shape replay export, the full pipeline must:
1. Read the manifest at `stage1/manifest.json`.
2. Process every measurement present, skip every measurement in `known_missing_measurements`.
3. Stages 2-9 each write their declared schema CSV(s).
4. Stages whose required inputs are present produce non-empty rows.
5. Stages whose required inputs are absent (per the manifest) produce an empty CSV AND a `reason_report.json` entry.

The audit script for the filing gate walks the output tree and asserts:
- Every stage directory exists.
- Every locked-schema CSV exists with the correct header.
- For every empty outcome CSV, the corresponding `reason_report.json` has a matching entry.

If a CSV is empty without a reason entry, the audit fails — that's the "silent empty output" condition the gate exists to prevent.
