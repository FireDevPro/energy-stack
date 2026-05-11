// Per-channel power and energy readings from the Refoss EM16P, written
// by refoss-poller. Long-format: one row per channel per tick.
// HVAC channels are em:2 + em:8 (AC compressor legs) + em:9 (furnace
// blower). Whole-home mains are em:1 + em:7.
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "refoss.channel")
  |> filter(fn: (r) =>
       r._field == "power_w" or
       r._field == "energy_wh")
  |> filter(fn: (r) =>
       r.channel == "em:1" or
       r.channel == "em:2" or
       r.channel == "em:7" or
       r.channel == "em:8" or
       r.channel == "em:9")
  |> yield(name: "refoss_channel")
