---
date: 2026-05-13
owner: chris
status: deferred-phase2
role-label: chris
---

# Eagle gap allocation — Phase 2 quality rule

Filed deferred from PR #109 (Phase 1 actual-dollar tracer). Phase 1 ships
with conservative drop-on-gap behavior; this plan captures the richer
gap-allocation quality rule for follow-on work after Phase 2 (drop-of
$/CDD-scaffolding) lands.

## Why

PR #109 review (2026-05-13) surfaced a real silent-smearing failure mode
in `eagle_hourly_kwh_from_delivered`: when `delivered_kwh` has a
mid-week gap longer than the cadence, all accumulated gap energy lands
in the first post-gap hour bucket, misattributing kWh into wrong
RTP/DTOD price hours.

Phase 1 fix (in PR #109): coverage gate at `max_gap_seconds >= 300 s`
drops O4 + O8 for the week with reason `eagle_meter_gap_exceeds_threshold`.
Refoss is NOT substituted. This is minimal-correct but conservative —
it drops weeks that have brief Eagle outages even when Refoss-mains is
available with full hourly shape during the same gap.

Phase 2 adds Refoss-shape allocation: preserve Eagle as canonical for
total energy (via `delivered_kwh` endpoint differential across the gap),
allocate the missing-hour kWh across the gap interval using Refoss-mains
hourly shape as the allocation weights. Eagle remains canonical; Refoss
supplies timing only.

## Spec anchors (post-Phase-2 targets)

- `docs/ANALYSIS_PIPELINE.md` §2.1 — current language describes drop-on-gap;
  Phase 2 amends to describe shape-allocated imputation rule.
- `docs/EXPERIMENT_DESIGN.md` §4 — extend data-quality rules with the
  new allocation/drop branches (or document in §2 O4/O8 definitions).

## Behavioral rule set (locked at planning time)

1. **Eagle present for week, no material gaps** (`exceeds_max_gap_threshold = False`):
   Use `eagle_hourly_kwh_from_delivered` directly. Unchanged from Phase 1.

2. **Eagle missing for entire week** (`n_samples == 0` OR Eagle parquet absent):
   Drop O4 + O8 with reason `no_eagle_meter_data_in_window`. Refoss is
   NOT substituted. Unchanged from Phase 1.

3. **Eagle partial gap, Refoss mains available during gap window:**
   - Identify each gap span where consecutive Eagle samples are
     separated by ≥ `EAGLE_MAX_GAP_SECONDS_THRESHOLD`.
   - For each gap: compute `gap_eagle_kwh = (eagle_value_after_gap −
     eagle_value_before_gap)` from the totalizer endpoints — this is
     the canonical total energy consumed during the gap.
   - Sum Refoss-mains hourly kWh across the gap-spanning hours.
     Allocate `gap_eagle_kwh` proportionally to those Refoss hourly
     values to produce Eagle hourly kWh estimates for the gap hours.
   - Provenance: append to `stage3/provenance.json` an
     `eagle_gap_allocated_by_refoss_shape` record per gap with span,
     `allocated_kwh`, `gap_fraction_of_weekly_eagle_kwh`, and the
     Refoss hourly shape vector.

4. **Eagle partial gap, Refoss also missing in same gap:**
   - If the gap overlaps an existing Stage 2 rule-7 scheduler-outage or
     a whole-house power outage marker, defer to those existing
     exclusion paths (drop affected days entirely; O4/O8 fall out
     downstream).
   - If standalone (no Rule 7 overlap, no whole-house outage marker):
     emit reason `eagle_and_refoss_both_missing_in_gap`. Drop O4. O8
     MAY survive if `delivered_kwh(week_start)` and
     `delivered_kwh(week_end)` are both reliable (totalizer continuity)
     — total weekly energy doesn't need hourly shape.

5. **Allocation-fraction secondary threshold:**
   - If `gap_eagle_kwh / weekly_eagle_kwh > MAX_ALLOCATION_FRACTION`
     (recommend 0.05–0.10; finalize from real summer 2026 data) OR
     `total_gap_duration > MAX_ALLOCATION_DURATION` (recommend 4 h),
     drop O4 with reason `eagle_gap_allocation_too_large`. O8 may
     still survive if endpoint totalizer continuity holds.

## O4 vs O8 asymmetry (locked)

- **O4 (weekly_whole_home_dollars):** Requires hourly shape (because
  pricing varies by hour). Drops more readily — any gap that can't be
  shape-allocated safely.
- **O8 (weekly_whole_home_kwh):** Requires only weekly total. Survives
  any gap as long as both `delivered_kwh(week_start)` and
  `delivered_kwh(week_end)` are reliable. Drops only when totalizer
  continuity itself is broken (Eagle absent at week boundaries; meter
  swap mid-week; resets).

## Whole-house power outage detection (new locked item)

When Eagle, Refoss, AND scheduler telemetry are simultaneously silent,
the most likely cause is a whole-house power outage (no consumption was
occurring anyway, so any imputation would be wrong). Phase 2 adds:

- A detector that identifies simultaneous gaps across `eagle.meter`,
  `refoss.channel` (em:1/em:7 mains), and `hvac.5cp_state` /
  `hvac.actions` for ≥ some threshold (5 min?).
- When detected: mark the gap span as a power-outage interval. Affected
  hours are EXCLUDED from O1, O3, O4, O7, O8 (zero consumption is real;
  imputing anything would be wrong).
- New Rule 11 (or extend Rule 7 / Rule 8 — design call) in
  `EXPERIMENT_DESIGN.md` §4 to lock this exclusion.

## RED test list (Phase 2 acceptance set)

1. **Refoss-shape allocation oracle.** Eagle 2-hour gap mid-week with
   known endpoint differential. Refoss mains has known hourly shape
   during the gap (e.g., 1 kWh/h in hour 12, 3 kWh/h in hour 13).
   Allocation produces Eagle hourly kWh of `gap_kwh × (1/4)` for hour
   12 and `gap_kwh × (3/4)` for hour 13. Pin exact values.
2. **Allocation-fraction-too-large drop.** Gap allocation = 8% of
   weekly Eagle kWh; threshold = 5%. O4 drops, O8 survives. Provenance
   records `eagle_gap_allocation_too_large` for O4 +
   totalizer-continuity-OK for O8.
3. **Allocation-duration-too-large drop.** 6h gap; threshold = 4h. O4
   drops with `eagle_gap_allocation_too_large`. O8 survives.
4. **O8-survives-O4-drops.** Gap fails allocation but week-boundary
   totalizers are valid. O4 drops, O8 emits.
5. **Both-missing in gap, no Rule-7 overlap.** Refoss also missing during
   Eagle gap. O4 drops with `eagle_and_refoss_both_missing_in_gap`. O8
   survives if endpoints OK; otherwise drops too.
6. **Both-missing in gap, Rule-7 overlap.** Eagle + Refoss + scheduler
   all silent simultaneously. Power-outage rule fires; affected days
   excluded via Rule 7 (or new Rule 11). O4/O8 inherit the exclusion.
7. **Coverage-OK pre-allocation.** Eagle has a 4-min gap (< 5-min
   threshold). No allocation triggered; differentials used as-is.
   Validates that Phase 1's coverage gate is the entry point.

## Open thresholds to finalize from real summer 2026 data

- `MAX_ALLOCATION_FRACTION` (recommend 5–10%): how much of weekly
  Eagle kWh can be shape-allocated before we lose confidence in the
  result. Finalize after observing real summer gap distribution.
- `MAX_ALLOCATION_DURATION` (recommend 2–4 h): max gap span eligible
  for allocation. Beyond this, allocation is too speculative even
  with Refoss-shape support.
- Whole-house power-outage minimum simultaneous-silence duration
  (recommend 5 min).

## Out of scope for Phase 2

- Statistical imputation models (e.g., learned hourly shape from
  prior-week same-weekday-same-hour). Allocation rule stays
  deterministic Refoss-mains-based.
- Multi-source whole-home reconciliation beyond Eagle + Refoss-mains.
- Eagle handling during meter swaps (separate `hw_address` change
  detection task).

## Tracking

- Single PR `feature/eagle-gap-allocation-phase2`, base `main`. No
  stacking. After Phase 1 (PR #109) merges and Phase 2 (drop-$/CDD)
  lands.
- Sequential commits per acceptance test from the locked list above.
- Spec amendment commit (single, per the §13 single-amendment
  pattern established in PR #108).
- Archive plan in closing commit.

## When to revisit

After summer 2026 cooling season produces real gap distribution. If
Eagle gaps cluster on high-RTP-volatility days, the 5-min Phase 1
threshold may need tightening or the Phase 2 allocation may need
additional safeguards. Defer threshold finalization until that data is
available.
