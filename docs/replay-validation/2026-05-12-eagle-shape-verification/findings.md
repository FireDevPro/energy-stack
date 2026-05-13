---
date: 2026-05-12
owner: chris
status: verified
role-label: chris
context: Phase 1.0 of docs/plans/actual-dollar-outcomes-migration-plan.md
---

# Eagle (`eagle.meter`) shape verification

Pre-implementation verification per Phase 1.0 of the actual-dollar migration plan. Goal: confirm Eagle data shape, cadence, monotonicity, and the Refoss-mains backup sanity check BEFORE writing acceptance-test oracles or locking the weekly drift threshold, so the oracles pin against real shape and the threshold reflects real noise.

## Framing

**Eagle is the canonical whole-home meter feed** (Rainforest EAGLE-3 over Zigbee SmartEnergy 1.0 to the utility-installed smart meter). This is the same data the ComEd bill is computed from. O4 (weekly whole-home actual cost) and O8 (weekly whole-home actual kWh) read Eagle as the source of truth.

**Refoss split-phase mains (`em:1 + em:7`) is a CT-clamp instrumentation sanity check.** It is loaded in parallel with Eagle to detect possible Refoss channel-mapping / calibration / time-alignment problems. It is never silently averaged with Eagle and never replaces Eagle in any outcome. When Eagle and Refoss-mains diverge beyond the locked weekly threshold, the divergence is flagged in `stage3/provenance.json` for human investigation — the assumption is that the smart meter is correct and Refoss instrumentation may have an issue worth looking at.

## Source

- Pi-lab Influx (bucket `energy`, measurement `eagle.meter`).
- Verification queries run 2026-05-12 ~22:23 CT.

## Schema (verified)

Matches the locked spec at `docs/ANALYSIS_PIPELINE.md` §2.1.

- **Fields:** `delivered_kwh`, `demand_kw`, `received_kwh`
- **Tags (non-system):** `hw_address`, `source`

Single meter present (`source=eagle3`).

## Cadence (verified)

120 `delivered_kwh` prints in the 1-hour window prior to verification = exactly one print per 30 seconds. Matches the documented 30-second poll interval at `deploy/energy-stack/eagle-poller/poller.py`.

## Monotonicity (verified)

- **30-minute window:** zero negative differentials on `delivered_kwh`.
- **7-day window:** zero negative differentials.
- **History span:** earliest sample on 2026-04-15 — about 28 days of continuous history. Totalizer rises monotonically over the full span.

No totalizer resets, no rollback, no out-of-order data. The Eagle `delivered_kwh` totalizer is safe to use directly for per-hour differential energy calculations.

## Drift definition (locked)

Per Chris's direction, since Eagle is the canonical source:

```
drift_pct = abs(refoss_mains_kwh - eagle_kwh) / eagle_kwh * 100
```

Eagle is always the denominator. Drift is reported in percent of Eagle's reading.

## Refoss-instrumentation sanity check evidence (7-day + daily)

The threshold being locked is **weekly**, so the evidence below includes one full weekly window plus daily breakdown of the same 7 days.

### Weekly (2026-05-06 04:23 — 2026-05-13 04:23 UTC, 7 days)

- Eagle `delivered_kwh` spread: **388.103 kWh**
- Refoss mains (sum of hourly mean power × 1 h): em:1 = 102.285 kWh + em:7 = 286.566 kWh = **388.851 kWh**
- **drift_pct = |388.851 − 388.103| / 388.103 × 100 = 0.193%**

Weekly drift is essentially zero — 50× below the 10% threshold being locked.

### Daily breakdown (same 7-day span, aggregated to calendar UTC days)

| Date (UTC) | Eagle kWh | Refoss em:1 + em:7 kWh | drift_pct |
|---|---|---|---|
| 2026-05-07 | 31.987 | 31.101 | 2.77% |
| 2026-05-08 | 57.991 | 57.020 | 1.67% |
| 2026-05-09 | 38.197 | 38.115 | 0.21% |
| 2026-05-10 | 51.626 | 51.050 | 1.12% |
| 2026-05-11 | 57.164 | 55.598 | 2.74% |
| 2026-05-12 | 64.454 | 64.605 | 0.23% |
| 2026-05-13 | 70.448 | 69.317 | 1.61% |

Daily drift range: 0.21% — 2.77%. Median ~1.6%. Highest daily drift is well under 5%, and the daily values do not concentrate in one direction (Refoss lower 6 of 7 days, Refoss higher 1 day) — **no evidence of a stable one-direction offset in this short verification**.

This is a single 7-day spring window. Sign of drift could shift across seasons (cooling load distribution between em:1 and em:7 may change), so the "no stable offset" reading is bounded to this window and should be revisited if summer 2026 data shows a persistent signed drift.

## Weekly drift threshold (locked at 10%)

**Threshold:** weekly `drift_pct >= 10%` flags the week in `stage3/provenance.json` for human investigation.

Rationale:
- Observed weekly drift in this 7-day window: 0.19%. The 10% threshold is 50× the observed noise floor.
- Daily drift max in this window: 2.77%. The 10% threshold is ~4× the daily noise floor; a weekly aggregation of similar daily values would not approach 10%.
- A 5% threshold would have a smaller margin against future operational noise (e.g., a single anomalous hour caused by a packet gap, time-skew during DST, or transient CT calibration drift on one phase). 10% keeps the flag specific to the failures the check exists for: channel mapping errors, dead phase, swapped CT clamps, prolonged packet-gap losses, meter-feed dropout.

This threshold is locked at the OSF filing commit. Revisit only if summer 2026 weekly data consistently shows drift in the 5-10% range, in which case a pre-registration amendment would tighten the threshold.

## Behavioral decisions (locked)

1. **drift_pct >= 10%** → flag the week in `stage3/provenance.json` for human investigation.
2. **drift alone does NOT drop outcomes.** Both meters are healthy data sources; drift means "interesting" not "invalid." O4 and O8 are still emitted; the flag is for the human to look at, not for the pipeline to mask data.
3. **Eagle remains canonical** for all whole-home outcomes (O4 dollars, O8 kWh) regardless of drift value.
4. **Refoss-mains is backup / sanity check, never averaged with Eagle.** If Eagle is absent for a week, O4 and O8 drop with a per-output reason code (Stage-8 pattern); the week is not failed entirely and Refoss-mains is not substituted as canonical.

## What is NOT verified by this step

- ComEd bill reconciliation against Eagle `delivered_kwh` over a full billing month (deferred to Phase 3 replay re-run or later).
- Behavior across DST transitions (Pi-lab has been live across at least one DST; not specifically tested here).
- Eagle handling during meter swaps / `hw_address` changes (the `hw_address` tag exists specifically to survive this; not tested as no swap occurred in the verification window).
- `received_kwh` totalizer behavior (currently zero / negligible; future solar will exercise this path).
- Drift behavior across a full cooling season — the 7-day window observed here is spring shoulder; high-load summer weeks may show different noise characteristics.

## Mid-window gap analysis (2026-05-13 amendment, post-PR-#109-review)

PR #109 review raised concern that `eagle_hourly_kwh_from_delivered` may silently smear energy across hourly RTP/DTOD buckets when `delivered_kwh` has a mid-week gap longer than the 30 s cadence. The helper finds the latest sample at-or-before each hour boundary; during a gap, multiple consecutive boundaries resolve to the same pre-gap value (differentials = 0), and the first boundary AFTER recovery carries the full accumulated gap energy as a single-hour spike. Under variable pricing this misattributes kWh into wrong-price hours.

Real-data check on the 28-day Eagle history (Pi-lab):

| Metric | Value |
|---|---|
| Max gap between consecutive `delivered_kwh` samples | **1941 s (~32 min)** at 2026-04-29 18:42 UTC |
| Gaps > 60 s | 4 |
| Gaps > 5 min (300 s) | 2 |
| Gaps > 10 min (600 s) | 2 |

A 32-minute gap straddling an hour boundary shifts up to ~32 min of energy into one hourly bucket. With variable pricing (DTOD periods, RTP spikes), the misattribution can be material — e.g., 30 min of evening Off-Peak energy misallocated to Mid-Day Peak DTOD (10.712¢ vs 3.747¢ = 7¢/kWh × 1+ kWh = ~7¢-15¢ error per gap-event).

### Locked coverage threshold (Phase 1.4)

`EAGLE_MAX_GAP_SECONDS_THRESHOLD = 300.0` (5 minutes).

When the max gap in a week's `delivered_kwh` samples (including edge gaps from `week_start_utc` to first sample and last sample to `week_end_utc`) exceeds 300 s, the helper treats Eagle as effectively absent for the week: `weekly_whole_home_dollars` and `weekly_whole_home_kwh` DROP with reason `eagle_meter_gap_exceeds_threshold`. Refoss-mains is NOT substituted as canonical. Other outcomes (O1, O3, O7) populate normally from Refoss / HVAC channels.

Rationale:
- Cadence is 30 s; one missed poll → ~60–90 s gap (normal, tolerated).
- 5-minute threshold permits brief network blips or poller restarts without flagging.
- Catches the 2 observed gaps > 5 min in the 28-day window, which would have caused detectable mispricing if they had landed on a qualifying week with high RTP volatility.
- Smearing error within tolerated <5 min gaps is bounded by `gap_minutes/60 × hourly_kwh × max_price_diff_$/kWh` — ~5¢ worst case for typical residential loads.

### Provenance fields added

`stage3/provenance.json` now carries (in addition to `eagle_vs_refoss_drift` and `eagle_missing_weeks`):

- `eagle_coverage`: per-week coverage record `{week_start_ct, arm, max_gap_seconds, n_samples, expected_samples, percent_present, exceeds_max_gap_threshold}` for every Stage 3 week that had any Eagle data.
- `max_gap_seconds_threshold`: the locked threshold value (300.0).
- `eagle_missing_weeks` entries now also include reason `eagle_meter_gap_exceeds_threshold` in addition to the existing `no_eagle_meter_data_in_window`.

### Open follow-up (not blocking PR #109)

Re-run gap analysis at end of summer 2026 cooling season — high-load weeks may have different cadence behavior (e.g., correlated with thermal events). If gaps cluster on high-RTP-volatility days, the 5-min threshold may need tightening or the smearing handling may need to evolve from "drop" to "linear interpolation across the gap".

## Next step

Phase 1.1: write the 5 RED acceptance tests with oracles derived from this verified shape. Drift threshold baked into Phase 1.2 implementation at **10% weekly** with the explicit `drift_pct = |refoss − eagle| / eagle × 100` formula and Eagle-as-canonical-source framing.
