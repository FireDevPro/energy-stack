// Per-tick day-type and schedule-decision audit rows from
// hvac-scheduler. Used as a cross-check on Stage 8 day classification
// and as the secondary signal for Stage 2 Rule 7 outage detection
// (gap in hvac.decisions writes alongside hvac.5cp_state).
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.decisions")
  |> yield(name: "hvac_decisions")
