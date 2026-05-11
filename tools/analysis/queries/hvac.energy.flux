from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "hvac.energy")
  |> filter(fn: (r) =>
       r._field == "em_2_kwh" or
       r._field == "em_8_kwh" or
       r._field == "em_9_kwh" or
       r._field == "em_1_kwh" or
       r._field == "em_7_kwh")
  |> aggregateWindow(every: 1m, fn: last, createEmpty: false)
  |> yield(name: "hvac_energy")
