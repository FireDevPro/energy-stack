// Thermostat state from thermostat-poller (indoor temp, setpoints,
// running flag, hvac mode). Production measurement name is
// `hvac.thermostat` (note: the §2.1 table previously called this
// `thermostat.state` — fixed 2026-05-11).
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.thermostat")
  |> yield(name: "hvac_thermostat")
