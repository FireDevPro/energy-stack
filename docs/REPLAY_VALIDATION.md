---
date: 2026-05-18
owner: chris
status: locked
role-label: code-team
binding_for: pre-OSF-tag replay validation methodology per binding spec §11 #13
---

# Replay validation: pre-flight cases and source-type catalog

This document is the locked methodology for the pre-OSF-tag replay validation that satisfies [binding spec §11 #13](plans/sced-rebaseline-spec-2026-05-13.md). Two things live here:

1. The **source-type catalog**: which categories of data the replay bundle can include and what each one is for (this section is locked at OSF tag).
2. The **locked injection-case list**: pre-registered list of synthetic fault/event cases that get generated into the bundle to exercise paths that don't fire naturally before the experiment starts (initial stub here; full list lands with the injection-case generator PR).

## Why this document exists

The OSF tag locks the analysis methodology before the experiment runs. The replay validation that demonstrates "the pipeline is ready to consume real data" can't use real experimental data because the experiment hasn't happened yet. So it has to use a labeled mix of real (where it exists), derived (where it can be safely synthesized from a real source), and injected (where the path needs coverage but can't be sourced).

The discipline that keeps this honest: every row in the replay bundle carries an explicit source-type label, every stage output carries provenance, and the locked methodology requires both. Future readers can audit which numbers in the filing bundle trace to real-world conditions vs validation-case synthetic conditions.

## Source-type catalog (locked)

Source types are recorded **per manifest entry**, and entries are traceable to stage-output provenance. A single measurement may have multiple manifest entries with different source types (e.g., `ecowitt.weather` with both `observed_recent` and `weather_derived_compatibility` entries covering different windows). Each manifest entry's source type is exactly one of:

### `observed_historical`

Real data from a historical period when the measurement existed. This is the gold standard for pipeline validation: it tests the loaders against the same shape and content the pipeline will see during the experiment, where available.

Examples in scope for this work:
- 2025 PJM `inst_load`, `metered_load`, `peak_forecast_rto`, `lmp_da_hourly`, annual `coincident_peak` if scraped from 2024-or-earlier PJM PDFs
- 2025 ComEd RTP prices at `tools/comed_2025_analysis/data/may2025.txt` through `sep2025.txt`
- KORD ASOS 5-min routine reports (Iowa State Mesonet archive) or NWS hourly observations for 2025

**Stage 6 ComEd 5CP source.** Stage 6's ComEd zone 5CP truth is derived from `pjm.metered_load{zone=CE}`, not from `pjm.coincident_peak` (which carries PJM/RTO 5CP hours plus the ComEd zone load AT those PJM hours — not ComEd's own 5 highest hours). The loader takes the top-5 distinct-CT-day hourly maxima after preferring `is_verified=true` rows over preliminary ones. When any top-5 hour came from a preliminary row, the bundle's `stage6/provenance.json` records `comed_5cp_preliminary: true` plus the per-hour list.

### `observed_recent`

Real telemetry written by services whose history doesn't reach back to summer 2025 because the services themselves are newer. The data is real, just not historical.

Examples:
- `ecowitt.weather` from 2026-05-11 onward (receiver deployment date)
- `hvac.5cp_state`, `hvac.price_overlay`, `hvac.arm_transitions`, `hvac.precool_window` from whenever the scheduler started writing them
- Refoss `channel`, `system` from whenever the Refoss EM16P came online if that postdates 2025

### `weather_derived_compatibility`

Synthetic Ecowitt-shape rows built from a historical weather source. Fills the pre-2026-05-11 gap where Ecowitt didn't yet exist, so the analysis pipeline's `ecowitt.weather` loader can be exercised against the historical window.

**Canonical producer**: [`tools/analysis/replay/weather_compat.py`](../tools/analysis/replay/weather_compat.py). CLI entry: `python -m tools.analysis.replay.weather_compat fetch ...`. See module docstring for current options.

**Primary ASOS source**: Iowa State Mesonet bulk CGI (`https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py`) with `report_type=1,3,4` (HFMETAR 5-min ASOS plus routine and special METAR). Field map: ASOS `tmpf` → `outdoor_temp_f` (°F identity), `dwpf` → `outdoor_dewpoint_f` (°F identity), `relh` → `outdoor_rh_pct` (% identity), `mslp` → `pressure_inhg` (divide by 33.8639), `sknt` → `wind_mph` (multiply by 1.15078).

**Solar source (always)**: Open-Meteo Historical Weather API (`archive-api.open-meteo.com/v1/archive`), `shortwave_radiation` hourly, ERA5-Land backed. Solar is never derived from ASOS cloud cover; ASOS has no shortwave field.

**Gap-fill source**: Open-Meteo also supplies the five ASOS-equivalent fields (`temperature_2m`, `dew_point_2m`, `relative_humidity_2m`, `pressure_msl`, `wind_speed_10m`) for slots where ASOS has a material gap (≥ 60 minutes without an observation).

**Cadence handling — forward-fill, not interpolation**. Both Open-Meteo hourly data and the rare hourly-only METAR routine reports are forward-filled onto the 5-min Ecowitt grid. Interpolation would invent intermediate values that no observation supports; forward-fill preserves observed-value provenance (every filled slot carries the value of the most recent native observation within 60 minutes). The converter never invents intermediate values.

**Per-row provenance columns** (audit-only; the Stage 3 loader reads only `_time`/`_measurement`/`_field`/`_value`):

| Column | Values | Meaning |
|---|---|---|
| `weather_source` | `iem_asos` / `open_meteo_era5_gap_fill` / null on solar rows | Where the non-solar value came from |
| `solar_source` | `open_meteo_era5` / null on non-solar rows | Where solar came from |
| `station` | e.g. `KORD` | ASOS station the bundle was built for |
| `cadence` | `5min` | Grid cadence (always 5-min in this converter) |
| `upsampled` | boolean | True for forward-filled slots; False for slots where the row matches a native observation timestamp |

**Not a claim about observed conditions at the sensor location**. These rows test that the pipeline's `ecowitt.weather` loader handles real-shape data; they do NOT claim Ecowitt-the-instrument recorded these values.

### `injected_validation_case`

Synthetic rows for path coverage of conditions that don't fire naturally before the experiment starts, or that are bad-data conditions the pipeline's quality rules need to be tested against.

**Not a claim about historical events**. The injected rows test pipeline behavior, not investigator behavior.

The locked list of injection cases lives in `## Locked injection cases` below.

## Bundle-level requirement

The replay bundle MUST contain at least some real observed data (`observed_historical` OR `observed_recent`). At least one measurement must have at least one row with an `observed_*` source type. A purely-synthetic bundle does not satisfy [binding spec §11 #13](plans/sced-rebaseline-spec-2026-05-13.md).

## Per-output provenance requirement

Every stage's output carries a `<stage>/provenance.json` sidecar listing which source types contributed to its computation:

```json
{
  "outputs": {
    "qualifying_weeks.csv": {
      "input_source_types": ["observed_recent", "injected_validation_case"],
      "row_source_breakdown": {
        "from_observed": 2,
        "from_injected": 3
      }
    }
  }
}
```

The audit script can grep these sidecars to identify which numbers in the filing bundle trace to real-world conditions vs validation-case conditions. An output produced entirely from `injected_validation_case` rows is not rejected by the filing gate, but it is labeled so a reviewer reading the bundle can tell.

## Reason-code requirement

When a stage's required input is legitimately absent (e.g., Stage 7 SCED when the export window has no arm cycling), the stage produces a header-only CSV AND writes a `<stage>/reason_report.json` sidecar with an entry from the pre-registered enumeration in [`tools/analysis/replay/reason_codes.py`](../tools/analysis/replay/reason_codes.py).

An empty CSV without a matching reason-report entry fails the filing-gate audit.

## Locked injection cases

Locked 2026-05-18 per [`docs/plans/pre-osf-doc-audit-execution-2026-05-18.md`](plans/pre-osf-doc-audit-execution-2026-05-18.md) F-007 resolution. 19 cases. Generator code lands in a follow-up PR (PR4b in the execution plan); the locked methodology gate is the list below.

**Bad-data / quality-rule coverage:**

1. **Missing weather rows** — tests Ecowitt-gap NOAA fallback per binding spec §6 (KJOT). Inject hours with no Ecowitt rows, verify NOAA fill.
2. **Duplicate timestamps** — tests deduplication. Inject duplicate rows on a single timestamp.
3. **Partial price hours** — tests hour-validity per spec §5. Drop minute-tick rows within an hour so coverage falls below the per-hour threshold.
4. **Stale price feed** — `comed.prices` returns the same `price_cents_per_kwh` value for ≥6 consecutive 5-min ticks. Inject by copying a value across 6 rows.
5. **Scheduler outages** — ≥5-min gap in both `hvac.5cp_state` and `hvac.actions` writes. Inject a gap.
6. **Refoss channel gaps (Tier 1-4)** — drop specific channels for specific durations to exercise each Refoss-gap tier classification.
7. **Detector false positive** — inject `hvac.5cp_state="holding"` row at a non-5CP hour.
8. **Detector false negative** — inject 5CP hour list AND ensure no `holding` row exists during that hour.
9. **Injected published 5CP hour list** — load fixture (since post-summer publication won't exist before OSF tag).
10. **Bill window with no `comed.bill` entry** — tests `no_comed_bills_in_window` reason code (Layer 3).
11. **Bill window with one `comed.bill` entry** — tests Layer 3 single-entry sum path.

**Boundary / qualification coverage:**

12. **CT-slip-like interval** — one Refoss channel suspiciously low while others normal. Tests ratio-anomaly detection.
13. **Manual operational override** — inject `hvac.overrides` row; verify pipeline tags the affected hour.
14. **Synthetic arm transition on 2026-06-01** — inject `hvac.arm_transitions` row at the experiment-start boundary (since the natural transition hasn't fired yet pre-experiment).
15. **Successful verification action within 6h** — pair an arm transition with a follow-up `hvac.actions` row.
16. **Failure to verify** — arm transition without follow-up action (or with wrong-arm action).
17. **Missed / late arm switch** — inject `hvac.switch_event` row with `boundary_actual_ts` delayed past `boundary_planned_ts`; verify the affected hours are classified as not-fully-valid in `hvac.arm_mode` and dropped from `fully_valid_hours_count` per spec §5.
18. **Arm period at exactly 259 valid hours** — validity-gate boundary case (spec §5 line 168). Construct a fixture with exactly 259 fully-valid hours; verify the arm enters the matching pool.
19. **Arm period that fails the ≥259 gate** — construct a fixture with <259 fully-valid hours (e.g., 258); verify (a) the arm is dropped from the matching pool and (b) the pipeline emits the descriptive output for the dropped arm (not silently disappeared) per spec §5 line 170.

**Cases dropped from the pre-rebaseline draft** (no translation to current pipeline):

- "Exactly 10% imputed kWh (Rule 1 threshold)" and "Exactly 20% imputed price hours (Rule 3 threshold)" — pre-rebaseline tiered-imputation concepts; the rebaseline pipeline has a single binary validity gate (≥259 fully-valid hours per arm per spec §5), with no per-source tiered imputation. The validity-gate boundary case (18) is the closest rebaseline equivalent.
- "One week that barely qualifies after Tier 1-3 imputation (just under 10% cap)" — same retired framing; case 18 (exactly 259 hours) is the rebaseline equivalent.
- "One week that must be excluded by a specific rule" — weekly framing retired; case 19 (arm period that fails the gate) is the rebaseline equivalent.

## Audit invariants

The filing-gate audit script walks the output tree and asserts:

1. Every stage directory exists.
2. Every locked-schema CSV exists with the correct header (header-only is acceptable if reason-code-justified).
3. For every empty CSV beyond the locked schema header, a corresponding `reason_report.json` entry exists.
4. Every non-empty CSV has a corresponding `provenance.json` entry.
5. At least one measurement in the bundle manifest has at least one `observed_*` source-type entry.
6. The injection cases listed in this document are all represented in the bundle's manifest as `injected_validation_case` entries (with case IDs that match the locked list).

If any invariant fails, the audit fails and the filing is blocked.
