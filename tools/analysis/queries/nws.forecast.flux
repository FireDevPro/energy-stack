from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "nws.forecast")
  |> yield(name: "nws_forecast")
