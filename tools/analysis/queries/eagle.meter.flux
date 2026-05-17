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
// The analysis layer derives per-hour kWh from totalizer differences
// (delta = current - previous within the bill period) and handles
// counter-reset / restart edge cases at the loader boundary.

from(bucket: "${bucket}")
  |> range(start: ${start}, stop: ${stop})
  |> filter(fn: (r) => r._measurement == "eagle.meter")
  |> filter(fn: (r) => r._field == "delivered_kwh")
  |> yield(name: "eagle_meter")
