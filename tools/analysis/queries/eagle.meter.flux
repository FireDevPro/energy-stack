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
// (delta = current - previous within the bill period). The range
// starts 1 HOUR BEFORE the bill window so the loader gets the
// "prior totalizer" anchor that ``bill_reconciliation`` needs to
// produce a non-zero kWh for the first hour of the window. Without
// this anchor, the first hour silently shows up as Eagle-covered
// with 0 kWh, suppressing the Refoss-mains fallback that spec §10
// expects when Eagle is genuinely missing.

from(bucket: "${bucket}")
  |> range(start: time(v: ${start}) -1h, stop: ${stop})
  |> filter(fn: (r) => r._measurement == "eagle.meter")
  |> filter(fn: (r) => r._field == "delivered_kwh")
  |> yield(name: "eagle_meter")
