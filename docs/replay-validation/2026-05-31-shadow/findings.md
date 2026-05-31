---
name: shadow-validation-2026-05-31
date: 2026-05-31
owner: chris
status: active
role-label: code-team
companion-runner: tools/analysis/run_shadow_validation.py
companion-artifact: docs/replay-validation/2026-05-31-shadow/validation_results.json
spec-anchor: docs/plans/sced-rebaseline-spec-2026-05-13.md §11 #13
plan-anchor: docs/plans/sced-rebaseline-implementation-2026-05-13.md Phase 6
osf-freeze: true
---

# SCED rebaseline — OSF-freeze shadow validation findings

Final pre-experiment shadow validation, run at the OSF-freeze commit per spec §11 #13
and the D6 replay-artifact policy (`docs/plans/pre-osf-doc-audit-execution-2026-05-18.md`).
Validates pipeline shape against real Pi-lab production data ahead of the 2026-06-01
experiment start. **Not outcome evidence** — every shadow hour falls outside any locked
Arm. The machine-readable companion (`validation_results.json`) is committed at this
commit only (otherwise git-ignored); intermediate runs live on GitHub Actions.

## Inputs window

- **Start:** 2026-04-29T00:00:00Z (spec-locked instrumentation start)
- **End:**   2026-05-31T15:22:32Z (run time)
- **Runner:** v0.1.0, commit `f1d7401`, host `dePaola` (workstation), Influx `192.168.20.10:8086`, bucket `energy`

## Overall: WARN

No `FAIL`, no `BLOCKED`. All ingestion feeds and pipeline spot-checks `PASS`. The three
non-`PASS` items are all structurally expected at freeze time and are documented below —
none gates the freeze.

## Per-check results

| Check | Status | Reason code | Notes |
|---|---|---|---|
| `ingestion.ecowitt.weather` | **PASS** | `-` | Canonical shaded `ch1_*` stream present (ch1_temp_f=26681, ch1_dewpoint_f=26681); ws90_temp_f=28125; descriptive `outdoor_*`=174. |
| `ingestion.refoss.channel` | **PASS** | `-` | 454640 rows, 2026-04-29 → 2026-05-31 15:22Z. |
| `ingestion.comed.prices` | **PASS** | `-` | 9288 rows, 2026-04-29 → 2026-05-31 15:15Z. |
| `ingestion.pjm.lmp_rt_hourly` | **PASS** | `-` | 724 rows through 2026-05-29 03:00Z (RT hourly LMP is PJM settled data with a 2–3 day publish lag; recent days backfill). |
| `ingestion.eagle.meter` | **PASS** | `-` | 93750 rows, 2026-04-29 → 2026-05-31 15:22Z. |
| `ingestion.comed.bill` | **WARN** | `no_dtod_bill_in_window` | First DTOD bill arrives 2026-05-24 per spec §14; pre-DTOD bills used flat rates, tracked separately. **Expected.** |
| `pricing.reconstruction` | **N/A** | `no_dtod_bill_in_window` | No bills landed in shadow window; bill reconciliation runs post-experiment. **Expected.** |
| `refoss.hvac_kwh` | **PASS** | `-` | 762 hourly buckets, 196 cooling-active hours per spec §9; max 4.112 kWh, sum 362.4 kWh. |
| `reconciliation.refoss_eagle` | **PASS** | `-` | eagle_coverage_pct=100.00% across 93750 Eagle samples. |
| `weather.vector_inputs` | **PASS** | `-` | Canonical `ch1_*` populated; ws90 baseline 28125/28125; NOAA fallback station = KJOT (spec §11 #11). |
| `arm_calendar.no_crash` | **PASS** | `-` | 784 shadow hours checked; 0 inside any locked arm (expected — experiment begins 2026-06-01). |
| `mode_classification.spot_test` | **PASS** | `-` | All 5 spot cases produce expected HourMode values. |
| `m3.scarcity_divergence` | **WARN** | `osf_appendix_flag` | p95=8.82, n_paired=723, n_scarcity=36, max_diff=58.23 ¢/kWh, p95_diff=39.75 ¢/kWh, 30 hours diverging >2.0¢. **Pre-registered appendix flag per spec §11 #13 M3 — reported as provenance, not a failure mode.** |

## Sign-off

Generated artifact reviewed; the three non-`PASS` items are the expected freeze-time
states (DTOD bill not yet in window ×2; pre-registered M3 scarcity-divergence appendix
flag ×1). Human sign-off recorded via PR9 approval.
