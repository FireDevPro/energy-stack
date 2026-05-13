// Rainforest EAGLE-3 HAN smart-meter feed, written by eagle-poller
// (deploy/energy-stack/eagle-poller/poller.py). 30-second cadence.
//
// Canonical whole-home meter source for O4 (weekly whole-home actual
// cost) and O8 (weekly whole-home actual kWh) per docs/EXPERIMENT_DESIGN.md
// §2. This is the same data the ComEd bill is computed from.
//
// Field selection:
//   - delivered_kwh: monotonic cumulative totalizer. Per-hour energy
//     is computed as the differential (last sample in hour) minus
//     (last sample in prior hour). Monotonicity verified across the
//     bundle's history in docs/replay-validation/2026-05-12-eagle-shape-verification/findings.md.
//   - demand_kw: instantaneous power. Not used by the primary
//     dollar / kWh aggregation; kept available for diagnostics.
//   - received_kwh: export totalizer (future solar). Currently
//     zero / negligible; included so the schema is complete.
//
// Tags retained: hw_address (survives meter swaps), source (set to
// "eagle3" by the poller; reserved for future multi-source filtering).
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "eagle.meter")
  |> filter(fn: (r) =>
       r._field == "delivered_kwh" or
       r._field == "demand_kw" or
       r._field == "received_kwh")
  |> yield(name: "eagle_meter")
