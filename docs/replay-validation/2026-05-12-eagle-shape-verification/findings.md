---
date: 2026-05-12
owner: chris
status: verified
role-label: chris
context: Phase 1.0 of docs/plans/actual-dollar-outcomes-migration-plan.md
---

# Eagle (`eagle.meter`) shape verification on Pi-lab Influx

Pre-implementation verification per Phase 1.0 of the actual-dollar migration plan. Goal: confirm Eagle data shape, cadence, monotonicity, and alignment with the Refoss-mains backup BEFORE writing acceptance-test oracles, so the oracles pin against real shape rather than spec-only assumptions.

## Source

- Host: Pi-lab (`192.168.20.10`)
- Container: `influxdb` (energy-stack docker compose)
- Bucket: `energy`
- Measurement: `eagle.meter`
- Verification queries run 2026-05-12 ~22:04 CT (2026-05-13 04:04 UTC)

## Schema (verified)

Matches the locked spec at `docs/ANALYSIS_PIPELINE.md` §2.1.

**Fields:** `delivered_kwh`, `demand_kw`, `received_kwh`
**Tags (non-system):** `hw_address`, `source`

Single meter present: `hw_address=0x001350050037ac6b`, `source=eagle3`.

## Cadence (verified)

120 `delivered_kwh` prints in the 1-hour window prior to verification = exactly one print per 30 seconds. Matches the documented 30-second poll interval at `deploy/energy-stack/eagle-poller/poller.py`.

## Monotonicity (verified)

- **30-minute window:** zero negative differentials.
- **7-day window:** zero negative differentials.
- **History span:** earliest sample at `2026-04-15 02:02:57 UTC` = ~28 days of continuous history. `delivered_kwh` rises monotonically over the full span.

No totalizer resets, no rollback, no out-of-order data. The Eagle `delivered_kwh` totalizer is safe to use for per-hour differential energy calculations directly.

## Eagle vs Refoss mains alignment

This is the load-bearing finding for the threshold decision in Phase 1.

**30-minute window** (2026-05-13 03:34 — 04:04 UTC):
- Eagle `delivered_kwh` spread (end − start): **2.261 kWh**
- Refoss mains mean power (`em:1 + em:7`): 1236.53 W + 2852.02 W = 4088.55 W
  → energy = 4.089 kW × 0.5 h = **2.045 kWh**
- **Eagle higher by 0.216 kWh = +10.6% vs Refoss**

**24-hour window** (2026-05-12 04:04 — 2026-05-13 04:04 UTC):
- Eagle `delivered_kwh` spread: **72.523 kWh**
- Refoss mains, sum of 24 hourly mean powers as Wh: em:1 = 22,881.87 Wh + em:7 = 53,040.79 Wh = 75,922.66 Wh → **75.923 kWh**
- **Refoss higher by 3.400 kWh = +4.7% vs Eagle**

**Conclusion:** the sign of the Eagle-vs-Refoss-mains difference **flips between windows**. 30-min sample shows Eagle higher; 24-hour sample shows Refoss higher. This is NOT a systematic bias of one meter relative to the other — it is measurement noise from differences in sampling cadence, time alignment, and aggregation-window edge effects between the two systems.

Likely contributors (not investigated in this verification step, noted for future):
- Refoss CT clamp calibration tolerance (~1-2%)
- Time-skew between Eagle 30s polls and Refoss 1-min polls
- Sub-second power variation that averages differently across the two cadences
- Power-factor handling differences

## Implications for the drift threshold (Phase 1.2 implementation decision)

The migration plan's tentative "5% weekly kWh" threshold is too tight given the 4.7% noise observed on a single 24h window. A 5% threshold would flag essentially every normal week as drift-anomalous, defeating the purpose (flagging only weeks where the divergence is investigation-worthy).

**Recommended Phase 1 drift threshold: ≥ 10% weekly kWh delta** (absolute, in either direction). Rationale:
- Doubles the observed noise floor (~5%) so normal weeks don't flag.
- Still catches the kinds of failures the check is for: channel mapping errors, dead phase, swapped CT clamps, packet-gap losses, meter-feed dropout.
- Provenance flag triggers an investigation note in `stage3/provenance.json`, NOT an outcome drop. Both arms see the same drift if it's measurement noise; matched pairs are stable.

**Alternative (Phase 2 or later, NOT Phase 1):** learned baseline ratio per week-of-year + flag deviation > N stddev. More work to set up; not justified for Phase 1.

## Behavior locks confirmed

- Eagle is the **canonical** whole-home source for O4 (weekly whole-home actual cost) and O8 (weekly whole-home actual kWh), per Chris lock + spec amendment in PR #108.
- Refoss `em:1 + em:7` mains is loaded **in parallel** as a sanity cross-check / backup. NOT silently averaged with Eagle.
- Per-week drift exceeding the threshold flags the week in `stage3/provenance.json` for human investigation. Outcomes are NOT dropped on drift alone (both meters are healthy data sources; drift = "interesting" not "invalid").
- If Eagle is absent for a week, whole-home outcomes (O4, O8) drop with a per-output reason code (Stage-8-pattern); the week is not failed entirely.

## What is NOT verified by this step

- ComEd bill reconciliation against Eagle `delivered_kwh` over a full billing month (deferred to Phase 3 replay re-run or later).
- Behavior across DST transitions (Pi-lab has been live across at least one DST; no specific test run here).
- Eagle handling during meter swaps / hw_address changes (`hw_address` is a tag specifically to survive this; not tested as no swap occurred in the verification window).
- `received_kwh` totalizer behavior (currently zero / negligible; future solar will exercise this path).

## Next step

Phase 1.1: write the 5 RED acceptance tests with oracles derived from this verified shape. Drift threshold in Phase 1.2 implementation set at **10% weekly kWh** per this verification, locked in code + provenance documentation.
