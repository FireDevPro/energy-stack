from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.overrides")
  |> yield(name: "hvac_overrides")
