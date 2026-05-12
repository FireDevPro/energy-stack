from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "pjm.metered_load")
  |> yield(name: "pjm_metered_load")
