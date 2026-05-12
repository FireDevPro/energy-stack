# Replay validation: pre-flight cases and source-type catalog

This document is the locked methodology for the pre-OSF-tag replay validation that satisfies [`OSF_FILING.md` criterion 14](OSF_FILING.md#acceptance-criteria-pre-flight-checklist). Two things live here:

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

### `observed_recent`

Real telemetry written by services whose history doesn't reach back to summer 2025 because the services themselves are newer. The data is real, just not historical.

Examples:
- `ecowitt.weather` from 2026-05-11 onward (receiver deployment date)
- `hvac.5cp_state`, `hvac.price_overlay`, `hvac.arm_transitions`, `hvac.precool_window` from whenever the scheduler started writing them
- Refoss `channel`, `system` from whenever the Refoss EM16P came online if that postdates 2025

### `weather_derived_compatibility`

Synthetic Ecowitt-shape rows built from a historical weather source. Fills the pre-2026-05-11 gap where Ecowitt didn't yet exist, so the analysis pipeline's `ecowitt.weather` loader can be exercised against the historical window.

**Preferred source**: KORD ASOS 5-min routine reports (Iowa State Mesonet `mesonet.agron.iastate.edu/request/download.phtml`). Cadence matches Ecowitt's native 5-min, captures hourly weather variation. Fields map: ASOS `tmpf`/`dwpf`/`relh`/`sknt`/`alti` → Ecowitt `outdoor_temp_f`/`outdoor_dewpoint_f`/`outdoor_rh_pct`/`wind_mph`/`pressure_inhg`. Solar irradiance is not in standard ASOS; need to derive from `skyc1/2/3` (cloud cover) + clear-sky model, OR pull from a separate solar product (e.g., NSRDB), OR fall back to ERA5's surface shortwave.

**Fallback source**: ERA5-Land hourly reanalysis at KORD coordinates. Already used elsewhere in the repo for `baseline_cov.npz`. Hourly cadence requires interpolation to Ecowitt's 5-min shape; that interpolation smooths sub-hourly variation that real Ecowitt would capture, so the converter should explicitly document this limitation.

**Not a claim about observed conditions at the sensor location**. These rows test that the pipeline's `ecowitt.weather` loader handles real-shape data; they do NOT claim Ecowitt-the-instrument recorded these values.

### `injected_validation_case`

Synthetic rows for path coverage of conditions that don't fire naturally before the experiment starts, or that are bad-data conditions the pipeline's quality rules need to be tested against.

**Not a claim about historical events**. The injected rows test pipeline behavior, not investigator behavior.

The locked list of injection cases lives in `## Locked injection cases` below.

## Bundle-level requirement

The replay bundle MUST contain at least some real observed data (`observed_historical` OR `observed_recent`). At least one measurement must have at least one row with an `observed_*` source type. A purely-synthetic bundle does not satisfy [criterion 14](OSF_FILING.md#acceptance-criteria-pre-flight-checklist).

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

(Stub — full pre-registered list lands with the injection-case generator PR. Below is the initial draft from the 2026-05-11 design discussion; subject to refinement before being locked at OSF tag.)

Bad-data / quality-rule coverage:

- Missing weather rows (test Rule 5 NWS fallback)
- Duplicate timestamps (test deduplication)
- Partial price hours (test Rule 3 observation-count threshold)
- Stale price feed: `comed.prices` returns the same `price_cents` value for ≥6 consecutive 5-min ticks (definition pending)
- Scheduler outages: ≥5-min gap in both `hvac.5cp_state` and `hvac.actions` writes (test Rule 7)
- Manual operational override
- Missing or late arm transition (test Rule 10)
- Refoss channel gaps (Tier 1, 2, 3, 4 each)
- CT-slip-like interval (one Refoss channel suspiciously low while others normal)
- One week that must be excluded by a specific rule
- One week that barely qualifies after Tier 1-3 imputation (just under 10% cap)

Threshold-boundary coverage:

- Exactly 10% imputed kWh (Rule 1 threshold)
- Exactly 20% imputed price hours (Rule 3 threshold)

Detector accuracy coverage:

- Injected `hvac.5cp_state="holding"` at a non-5CP hour (false positive)
- Injected non-holding at a published 5CP hour (false negative)
- Injected published 5CP hour list (since post-summer publication won't exist before OSF tag)

Bill-availability coverage:

- Window with no `comed.bill` entry (test Layer 3 `no_comed_bills_in_window` reason code)
- Window with one bill entry (test Layer 3 sum)

Arm-transition coverage:

- Synthetic arm transition on 2026-06-01 (since natural transition hasn't fired yet)
- Successful verification action within 6h
- Failure to verify (missing or wrong-arm action)

## Audit invariants

The filing-gate audit script walks the output tree and asserts:

1. Every stage directory exists.
2. Every locked-schema CSV exists with the correct header (header-only is acceptable if reason-code-justified).
3. For every empty CSV beyond the locked schema header, a corresponding `reason_report.json` entry exists.
4. Every non-empty CSV has a corresponding `provenance.json` entry.
5. At least one measurement in the bundle manifest has at least one `observed_*` source-type entry.
6. The injection cases listed in this document are all represented in the bundle's manifest as `injected_validation_case` entries (with case IDs that match the locked list).

If any invariant fails, the audit fails and the filing is blocked.
