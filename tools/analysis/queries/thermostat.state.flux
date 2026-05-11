from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "thermostat.state")
  |> yield(name: "thermostat_state")
