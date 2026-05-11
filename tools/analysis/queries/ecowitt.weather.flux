from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "ecowitt.weather")
  |> filter(fn: (r) =>
       r._field == "outdoor_temp_f" or
       r._field == "outdoor_dewpoint_f" or
       r._field == "outdoor_rh_pct" or
       r._field == "wind_mph" or
       r._field == "solar_wm2" or
       r._field == "pressure_inhg")
  |> yield(name: "ecowitt_weather")
