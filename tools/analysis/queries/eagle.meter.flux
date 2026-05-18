// Eagle HAN-to-meter delivered-kWh totalizer for bill reconciliation.
// Per docs/plans/sced-rebaseline-spec-2026-05-13.md §10: Eagle smart-
// meter delivered-kWh is the canonical whole-home kWh source for bill
// reconciliation (Refoss mains is fallback / sanity).
//
// Schema (from deploy/energy-stack/eagle-poller/poller.py):
//   _measurement: "eagle.meter"
//   _field:       "delivered_kwh"   (monotonic totalizer in kWh)
//                 "received_kwh"    (monotonic totalizer in kWh)
//   _value:       numeric kWh
//
// Stage 1 substitution contract (see tools/analysis/pipeline.py): the
// loader replaces ``$bucket`` with a double-quoted bucket name and
// ``$start`` / ``$end`` with bare ISO timestamps. Use that exact form
// here so the rendered Flux is valid.
//
// First-hour kWh edge case: ``bill_reconciliation`` derives per-hour
// kWh from totalizer differences. The first hour of the bill window
// has no prior anchor inside the query result and therefore registers
// as zero kWh -- analytically bounded to under one dollar of monthly
// residual per spec §10's "documented non-material edge case" note,
// well below the >5%/$10 sanity threshold. No tolerance machinery
// required.

from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "eagle.meter")
  |> filter(fn: (r) => r._field == "delivered_kwh")
  |> yield(name: "eagle_meter")
