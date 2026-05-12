// Per-channel power readings from the Refoss EM16P, written by
// refoss-poller. Long-format: one row per channel per tick.
//
// HVAC channels are em:2 + em:8 (AC compressor legs) + em:9 (furnace
// blower). Whole-home mains are em:1 + em:7.
//
// Field selection: only power_w (instantaneous, ~30 s cadence) is
// pulled. The poller also writes cumulative session counters
// (day_energy_kwh, week_energy_kwh, month_energy_kwh) but those
// require window-difference logic that the Stage 2/3 loaders don't
// use; energy per hour is derived from mean power_w within each
// hour bucket × 1 h. There is no per-interval energy_wh field
// emitted by the poller.
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "refoss.channel")
  |> filter(fn: (r) => r._field == "power_w")
  |> filter(fn: (r) =>
       r.channel == "em:1" or
       r.channel == "em:2" or
       r.channel == "em:7" or
       r.channel == "em:8" or
       r.channel == "em:9")
  |> yield(name: "refoss_channel")
