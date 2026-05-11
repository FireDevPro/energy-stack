// ComfortNet CT-485 telemetry from thermostat-poller. Production
// measurement name is `hvac.comfortnet` (fixed 2026-05-11 from the
// previous `thermostat.comfortnet` in the §2.1 table).
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.comfortnet")
  |> filter(fn: (r) =>
       r._field == "cool_actual_pct" or
       r._field == "heat_actual_pct" or
       r._field == "blower_cfm")
  |> yield(name: "hvac_comfortnet")
