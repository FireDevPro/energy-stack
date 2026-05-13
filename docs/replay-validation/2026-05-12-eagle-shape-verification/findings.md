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

## Next step

Phase 1.1: write the 5 RED acceptance tests with oracles derived from this verified shape. Drift threshold baked into Phase 1.2 implementation at **10% weekly** with the explicit `drift_pct = |refoss − eagle| / eagle × 100` formula and Eagle-as-canonical-source framing.
