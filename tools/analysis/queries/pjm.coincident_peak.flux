from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "pjm.coincident_peak")
  |> yield(name: "pjm_coincident_peak")
