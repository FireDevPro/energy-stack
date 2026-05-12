// ComEd hourly RTP prices. The poller (deploy/energy-stack/comed-poller/
// poller.py) writes two flavors of the same field per cycle:
//   period_type=5min       — raw RTP price for each 5-min window
//   period_type=hourly_avg — poller-computed hourly mean
// The Stage 2/3 analysis pipeline owns the aggregation math and uses
// only the 5-min rows: Rule 3 coverage counts them; Stage 3's hourly
// supply price is computed as their mean within each hour. We still
// pull both period_types here so an audit cross-check (or a future
// follow-on) can compare the poller's hourly_avg against the
// recomputed mean.
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "comed.prices")
  |> filter(fn: (r) => r._field == "price_cents_per_kwh")
  |> yield(name: "comed_prices")
