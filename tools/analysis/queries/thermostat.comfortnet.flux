from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "thermostat.comfortnet")
  |> filter(fn: (r) =>
       r._field == "cool_actual_pct" or
       r._field == "heat_actual_pct" or
       r._field == "blower_cfm")
  |> yield(name: "thermostat_comfortnet")
