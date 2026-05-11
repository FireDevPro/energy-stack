from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "comed.bill")
  |> yield(name: "comed_bill")
