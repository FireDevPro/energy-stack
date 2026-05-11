from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.arm_transitions")
  |> yield(name: "hvac_arm_transitions")
