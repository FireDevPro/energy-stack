from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.price_overlay")
  |> yield(name: "hvac_price_overlay")
