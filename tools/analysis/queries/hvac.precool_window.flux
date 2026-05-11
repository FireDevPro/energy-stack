// Per-night price-aware pre-cool window decision from hvac-scheduler.
// One row per night at the 21:00 CT decision tick when a window is
// selected. Used by Stage 8 layer attribution (which Arm B layer
// triggered on grid-event days).
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.precool_window")
  |> yield(name: "hvac_precool_window")
