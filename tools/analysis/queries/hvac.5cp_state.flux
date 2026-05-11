from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.5cp_state")
  |> yield(name: "hvac_5cp_state")
