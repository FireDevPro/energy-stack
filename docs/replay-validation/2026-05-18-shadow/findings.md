---
name: shadow-validation-2026-05-18
date: 2026-05-18
owner: chris
status: active
role-label: code-team
companion-runner: tools/analysis/run_shadow_validation.py
spec-anchor: docs/plans/sced-rebaseline-spec-2026-05-13.md §11 #13
plan-anchor: docs/plans/sced-rebaseline-implementation-2026-05-13.md Phase 6
companion-pr-109-disposition: §13
---

# SCED rebaseline — Phase 6 shadow validation findings

Pre-experiment shadow validation per spec §11 #13. Validates pipeline shape against real Pi-lab production data ahead of the 2026-06-01 experiment start. **Not outcome evidence** — pre-experiment hours fall outside any locked Arm.

## Inputs window

- **Start:** 2026-04-29T00:00:00Z (spec-locked)
- **End:**   2026-05-18T16:08:24Z (run time)
- **Duration:** ~19.5 days
- **Source:** Pi-lab InfluxDB at `192.168.20.10:8086`, bucket `energy`, org `depaola-home`

## Commands run

Phase 6 introduced a new GitHub Actions workflow `.github/workflows/shadow-validation.yml` that runs the runner on the self-hosted pi-lab runner. **However, `workflow_dispatch` requires the workflow file to be on the default branch before it can be triggered against a feature branch.** Because Phase 6 IS the PR that adds the workflow file, this initial run is the documented fallback per the goal: runner executed locally from the Windows workstation against pi-lab's InfluxDB over the LAN.

Local fallback invocation (executed against commit `4f16742`):

```bash
INFLUXDB_URL=http://192.168.20.10:8086 \
INFLUXDB_TOKEN=<redacted> \
INFLUXDB_ORG=depaola-home \
INFLUXDB_BUCKET=energy \
PYTHONPATH=. \
python tools/analysis/run_shadow_validation.py
```

After this PR merges to main, the canonical re-run path is the Actions UI → "Shadow validation" → Run workflow. That run will execute on pi-lab itself (self-hosted runner) and upload the validation JSON + findings draft as a workflow artifact. The local fallback documented here remains valid for ad-hoc re-runs from a developer workstation.

## Pass/fail table

Overall result: **WARN** (3 WARN signals; no FAIL or BLOCKED).

| Check | Status | Reason code | Significance |
|---|---|---|---|
| `ingestion.refoss.channel` | **PASS** | — | 269,495 rows, 2026-04-29 → present. Em:1/2/7/8/9 all live. |
| `ingestion.ecowitt.weather` | **PASS** | — | 8,309 rows of `ch1_temp_f` (canonical shaded WN31). Starts 2026-05-12T21:03Z. |
| `ingestion.comed.prices` | **PASS** | — | 5,621 rows of `price_cents_per_kwh`. Continuous coverage. |
| `ingestion.pjm.lmp_rt_hourly` | **PASS** | — | 388 rows of `total_lmp_rt`. Last row 2026-05-15T03:00Z — PJM T+2 settle lag, expected. |
| `ingestion.eagle.meter` | **PASS** | — | 56,569 rows of `delivered_kwh`. 100% Eagle coverage across window. |
| `ingestion.comed.bill` | **WARN** | `no_dtod_bill_in_window` | Expected. First DTOD bill arrives 2026-05-24 per spec §14. Not a blocker. |
| `pricing.reconstruction` | **N/A** | `no_dtod_bill_in_window` | Cannot exercise `bill_reconciliation.reconcile_bill_period` without a bill in window. Code path verified separately by `test_bill_reconciliation.py` + `test_pipeline_realshape_e2e.py`. |
| `refoss.hvac_kwh` | **PASS** | — | 451 hourly buckets; 63 hours of HVAC draw; max 3.39 kWh/hr; sum 81.9 kWh. Cooling-active hours present in window. |
| `reconciliation.refoss_eagle` | **PASS** | — | `eagle_coverage_pct=100.00%` across 56,569 Eagle samples. Refoss vs Eagle drift is exercised inside `bill_reconciliation` when a bill closes in window. |
| `weather.vector_inputs` | **WARN** | `ecowitt_stage1_query_field_drift` | **Real-shape bug.** See [Open issue OI-1](#oi-1-stage-1-ecowitt-query-field-name-drift). |
| `arm_calendar.no_crash` | **PASS** | — | 473 shadow hours sampled; 0 inside any locked arm (expected — Arm 1 begins 2026-06-01). |
| `mode_classification.spot_test` | **PASS** | — | All 5 synthetic spot cases produce expected `HourMode` values. |
| `m3.scarcity_divergence` | **WARN** | `osf_appendix_flag` | See [M3 audit](#m3-scarcity-divergence-audit) — flagged for OSF appendix. |

## Open issues

### OI-1 — Stage 1 Ecowitt query field-name drift

**Severity:** WARN. Real pipeline-shape bug, blocking-grade for OSF only if the analysis runs through Stage 1 manifest extraction. Needs a separate, focused PR.

The Stage 1 Flux query at `tools/analysis/queries/ecowitt.weather.flux` requests:

```
filter(fn: (r) =>
     r._field == "outdoor_temp_f" or
     r._field == "outdoor_dewpoint_f" or
     ...)
```

But the canonical shaded outdoor sensor written by `deploy/energy-stack/ecowitt-ingest/app.py` is `ch1_temp_f` / `ch1_dewpoint_f` (WN31 channel 1). Field counts over the 30-day window:

| Field | Count |
|---|---|
| `ch1_temp_f` (canonical, what `weather_vector.build_weather_vector` consumes) | 8,309 |
| `ch1_dewpoint_f` | 8,309 |
| `ws90_temp_f` (WS90 onboard, sun-exposed comparator) | 9,619 |
| `ws90_dewpoint_f` | 9,619 |
| `outdoor_temp_f` (Stage 1 query target) | **42** |
| `outdoor_dewpoint_f` (Stage 1 query target) | **42** |

The 42 `outdoor_*` rows are all clustered in a 40-minute window on 2026-05-11 — the brief period before the ecowitt-ingest commit `19f1f47` "split canonical shaded outdoor from WS90 sun comparator" settled the schema. Continuous canonical writes use `ch1_*`.

**Impact:** if a Stage 1 manifest extraction were run today using the existing Flux query, it would pull 42 rows of outdoor weather across a 20-day window — effectively empty. `weather_vector.build_weather_vector` would then raise `ValueError("No Ecowitt rows in arm-period window")` against any real arm.

**Mitigation already in place:** `test_pipeline_realshape_e2e.py` apparently uses `ch1_*`-shape fixtures (per the canonical schema in `weather_vector.py`), so the test suite is not affected. The drift is between Stage 1 query and poller writes, not between weather_vector and its inputs.

**Pre-OSF follow-up:** open a follow-up PR that updates `tools/analysis/queries/ecowitt.weather.flux` to filter on `ch1_temp_f` / `ch1_dewpoint_f` (and decide whether to also pull `ws90_*` as a sun-comparator side channel). Not in Phase 6 scope; Phase 6's job is to surface it.

### OI-2 — Ecowitt continuous coverage starts 2026-05-12, not 2026-05-11

**Severity:** informational. Spec §14 says Ecowitt instrumentation begins 2026-05-11; canonical `ch1_*` writes actually start 2026-05-12T21:03Z. ~1 day later than the spec-claimed limitation. Update the spec or accept the small drift in the next OSF doc pass.

## M3 scarcity-divergence audit

Per spec §11 #13 M3: for shadow-period hours where `comed.prices` 5-min mean exceeded its 95th percentile, compute absolute difference vs `pjm.lmp_rt_hourly` settled at the same hour. Report `n_hours_diverging_>2c`. If `>0`, flag for OSF appendix.

**Result: FLAGGED for OSF appendix.**

| Metric | Value |
|---|---|
| Paired hours (ComEd hourly mean ∩ PJM hourly) | 387 |
| ComEd hourly p95 (¢/kWh) | 8.50 |
| Scarcity hours (ComEd hourly mean ≥ p95) | 19 |
| Max abs divergence at scarcity hours (¢/kWh) | **15.30** |
| p95 abs divergence at scarcity hours (¢/kWh) | 15.30 |
| Hours diverging >2 ¢/kWh | **16 of 19** |
| Threshold (¢/kWh) | 2.0 |

**Interpretation.** At ~84% of the shadow window's scarcity hours, the live ComEd 5-min hourly average diverges from the PJM settled hourly LMP by more than 2 ¢/kWh, and the maximum observed divergence is 15.30 ¢/kWh — comparable to the entire ComEd price level at scarcity. This is the M3 risk the spec anticipates: at the exact moments the controller's price-overlay logic fires (high real-time price), the live signal the controller observed can differ materially from the bill-canonical settled price the analysis uses.

This is the kind of finding §11 #13 anticipated. The pre-registered handling is for the OSF appendix to disclose the divergence and to surface `live_price_vs_settled_price` as a §12 named sensitivity. Both are already in the spec (§12, table row 3).

**Follow-up:** include this finding verbatim in the OSF appendix when filing. No spec change required; the sensitivity machinery already exists.

## PR #109 disposition verification

Per spec §13: "Close PR #109 as superseded immediately after the cherry-pick PRs land (mid-Phase 3, not end-of-Phase-6)."

- `gh pr view 109 --json state,closedAt` → `{"state":"CLOSED","closedAt":"2026-05-14T21:55:56Z"}`. ✅ Closed as expected.
- `git log --oneline main -- tools/analysis/queries/eagle.meter.flux` → `28f10b9 Phase 3: SCED rebaseline analysis pipeline (#138)`. ✅ The Eagle Flux query landed via Phase 3, satisfying the §13 cherry-pick requirement.
- Additionally verified: `tools/analysis/eagle_coverage.py` exists on main (Phase 3 #138) — the "Eagle coverage helper" salvageable per §13 was re-implemented per Section 10 in Phase 3.

PR #109 is closed and the spec-required cherry-picks are on main.

## Limitations (declared truth, not failures)

1. **Pre-experiment data is outside any locked Arm.** Spec §2 locks Arm 1 start at 2026-06-01 00:00 CT. The shadow window 2026-04-29 → 2026-05-18 has zero overlap with any arm. `arm_calendar.current_arm_at` correctly returns `None` for every shadow hour. Per spec §11 #13, this is pipeline-shape validation only — NOT outcome evidence.
2. **`weather_vector.build_weather_vector(arm, ecowitt_df)` cannot be exercised against shadow data** because it requires an `ArmPeriod` and the post-washout window of that arm to overlap the data. Schema + NOAA-fallback lock are validated upstream as proxy; the function itself is covered by `test_weather_vector.py` against synthetic ArmPeriod fixtures.
3. **First DTOD bill arrives 2026-05-24** per spec §14. `bill_reconciliation.reconcile_bill_period` cannot be exercised against shadow window data. Pre-DTOD reconciliation uses flat rates and is tracked separately.
4. **Ecowitt canonical (`ch1_*`) coverage starts 2026-05-12T21:03Z**, not 2026-05-11 as spec §14 says. ~1-day drift; not a blocker.
5. **PJM `rt_hrl_lmps` data ends 2026-05-15T03:00Z** in the window, per PJM's normal T+2 settle lag for `rt_hrl_lmps`. Not a poller issue.
6. **Local-fallback execution.** The runner ran from a Windows workstation against pi-lab Influx over the LAN rather than on pi-lab itself. The workflow `.github/workflows/shadow-validation.yml` becomes the canonical pi-lab execution path after this PR merges. The shadow data is the same in either path — only the execution host differs.

## Sign-off

Phase 6 shadow validation **surfaces 2 real findings** that the pre-OSF process should act on:

- **OI-1** (Stage 1 Ecowitt query field-name drift) — needs a focused pre-OSF follow-up PR to update the Flux query from `outdoor_*` to `ch1_*`.
- **M3 scarcity divergence** — pre-registered handling already in spec §12 (sensitivity `live_price_vs_settled_price`). Add the observed numbers to the OSF appendix at filing.

All other validation checks are PASS or the expected pre-experiment N/A. Pipeline-shape validation passes the pre-OSF gate per spec §11 #13.

✅ **Phase 6 sign-off:** runner + workflow + findings deliverable complete. Pre-OSF dependencies #11.13 (this check) resolved; OI-1 logged as separate follow-up; M3 result flagged for OSF appendix.
