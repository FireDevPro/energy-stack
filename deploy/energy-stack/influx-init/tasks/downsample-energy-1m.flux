// 1-minute downsample from `energy` to `energy-longterm`.
//
// Per-frame continuous measurements (eagle.meter, refoss.channel, refoss.system,
// hvac.comfortnet) emit on a 30-33s cadence. This task rolls each completed
// 1-minute window into mean + max aggregates in the longterm bucket, leaving
// raw data in `energy` to roll off at the bucket's 90-day retention.
//
// Cumulative counters (kWh totals) use last() instead of mean() so the
// longterm value still represents "running total at end of window".
//
// Tags are preserved as-is by aggregateWindow; do not transform them or
// cardinality drifts between buckets.
//
// Design: docs/INFLUXDB_RETENTION.md

import "date"

option task = {
    name: "downsample-energy-1m",
    every: 1m,
    offset: 30s,
}

src = "energy"
dst = "energy-longterm"

// Explicit minute-aligned window. With offset: 30s the task fires at :30 of
// each clock minute, so we read the prior fully-closed minute [-1m boundary,
// -0m boundary). Using date.truncate makes the window correct regardless of
// offset value and avoids partial-minute aggregates that range(-2m, -1m)
// would produce when the task firing isn't itself minute-aligned.
windowEnd = date.truncate(t: now(), unit: 1m)
windowStart = date.sub(d: 1m, from: windowEnd)

// Measurements that need 1-min downsampling. Anything not in this list is
// either already coarse (haven, comed.prices, nws, thermostat) and writes
// straight to `energy-longterm`, or an event measurement (hvac.actions,
// hvac.overrides, hvac.comfortnet.events) that bypasses aggregation.
aggregateMeasurements = [
    "eagle.meter",
    "refoss.channel",
    "refoss.system",
    "hvac.comfortnet",
]

// Cumulative counters: aggregated via last() so the value at end-of-window
// represents the running total at that timestamp, not the mean of running totals.
cumulativeFields = [
    // eagle.meter
    "summation_delivered",
    "summation_received",
    // refoss.channel
    "day_energy_kwh",
    "day_ret_energy_kwh",
    "week_energy_kwh",
    "week_ret_energy_kwh",
    "month_energy_kwh",
    "month_ret_energy_kwh",
    // refoss.system
    "uptime_s",
    "cfg_rev",
]

base = from(bucket: src)
    |> range(start: windowStart, stop: windowEnd)
    |> filter(fn: (r) => contains(value: r._measurement, set: aggregateMeasurements))

// Cumulatives: last(). Keep field name unchanged so dashboards are agnostic
// to which bucket they're querying for these fields.
base
    |> filter(fn: (r) => contains(value: r._field, set: cumulativeFields))
    |> aggregateWindow(every: 1m, fn: last, createEmpty: false)
    |> to(bucket: dst)

// Continuous: mean(). Keep field name unchanged for the same reason.
base
    |> filter(fn: (r) => not contains(value: r._field, set: cumulativeFields))
    |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
    |> to(bucket: dst)

// Continuous: max(). Suffix _max so analytical queries (peak demand for 5CP
// attribution, peak firing rate) reach for it explicitly.
base
    |> filter(fn: (r) => not contains(value: r._field, set: cumulativeFields))
    |> aggregateWindow(every: 1m, fn: max, createEmpty: false)
    |> map(fn: (r) => ({r with _field: r._field + "_max"}))
    |> to(bucket: dst)
