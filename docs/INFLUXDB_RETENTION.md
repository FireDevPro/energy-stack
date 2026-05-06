# InfluxDB Retention and Downsampling Design

**Status**: proposed. Not yet implemented.

Closes the InfluxDB downsampling open item in PROJECT.md. Designed alongside [`COMFORTNET_USE_CASES.md`](COMFORTNET_USE_CASES.md) so the ComfortNet `hvac.comfortnet` measurement schema can be built knowing which fields aggregate which way.

## Goals

- Keep all historical data queryable forever, at a resolution that supports the use cases the stack actually has (5CP analysis, monthly cost summaries, multi-month efficiency trends).
- Bound the storage growth from the per-frame writers (`eagle.meter`, `refoss.*`, soon `hvac.comfortnet`) so disk on `pi-lab` stays predictable.
- Don't lose event-grade fidelity on faults, stage transitions, or HVAC scheduler decisions.

## Non-goals

- Hourly or daily aggregation tiers. 1-min granularity is enough for every use case in the stack; adding more tiers is complexity without payoff at residential scale.
- Backfilling pre-existing raw data into the longterm bucket. The stack started writing in April 2026; ~1 month of history is not worth the migration risk.

## Source inventory and classification

Every measurement currently or imminently writing to the `energy` bucket, classified by what retention treatment it needs:

| Measurement | Source | Native cadence | Class | Treatment |
|-------------|--------|---------------:|-------|-----------|
| `eagle.meter` | `eagle-poller` | 30 s | per-frame continuous | Raw 90 d → 1-min `mean`+`max` longterm |
| `refoss.channel` | `refoss-poller` | 30 s × 18 ch | per-frame continuous | Raw 90 d → 1-min `mean`+`max` longterm |
| `refoss.system` | `refoss-poller` | 30 s | per-frame continuous | Raw 90 d → 1-min `mean` longterm |
| `hvac.comfortnet` (continuous fields) | comfortnet poller (planned) | ~33 s × ~5 frames/cycle | per-frame continuous | Raw 90 d → 1-min `mean`+`max` longterm |
| `hvac.comfortnet.events` (planned) | comfortnet poller (planned) | event-driven | event | Direct write to longterm, never aggregate |
| `hvac.actions` | `hvac-scheduler` | event-driven | event | Direct write to longterm, never aggregate |
| `hvac.overrides` | `thermostat-poller` | event-driven | event | Direct write to longterm, never aggregate |
| `hvac.thermostat` | `thermostat-poller` | 10 min | already coarse | Direct write to longterm |
| `comed.prices` | `comed-poller` | 5 min | already coarse | Direct write to longterm |
| `comed.bill` | bill ingest (Phase 8) | monthly | already coarse | Direct write to longterm |
| `nws.forecast` | `nws-poller` | 30 min | already coarse | Direct write to longterm |
| `haven.indoor` | `haven-ingest` | 5 min | already coarse | Direct write to longterm |
| `haven.outdoor` | `haven-ingest` | 5 min | already coarse | Direct write to longterm |

**Class definitions**:

- **Per-frame continuous**: high-cadence numeric measurements where the value at any instant is meaningful and aggregable (power, temperature, percentage, flow). Mean preserves the trend, max preserves peaks (matters for 5CP analysis).
- **Event**: discrete occurrences (faults, mode changes, scheduler decisions). Aggregating these is meaningless. Volumes are tiny; keep raw forever.
- **Already coarse**: cadence is at or below the 1-min downsample target. No transformation needed; just route directly to longterm.

## Bucket design

Two buckets:

- **`energy`** — raw, **90-day retention**. Receives only per-frame continuous writes. Source for the downsampling task.
- **`energy-longterm`** — aggregated and event, **infinite retention**. Receives 1-min aggregates from the task plus direct writes from coarse and event sources.

Why 90 days raw: covers a full PJM cooling season for retrospective 5CP-hour analysis. After 90 days, 1-min aggregates remain available forever for any longer-horizon question.

Why one longterm tier (not 1-min + 1-hour + 1-day): at ~13M longterm points/year compressed to ~30-50 bytes each, 10 years is ~6.5 GB. `pi-lab` has plenty. Adding tiers is operational complexity without a measurable benefit at this scale.

## Aggregation rules

For per-frame continuous measurements, the task runs once per minute and applies:

| Field type | Aggregation | Stored as |
|------------|-------------|-----------|
| Continuous numeric (power W, temperature °F, percentage, CFM, dollars) | `mean()` | same field name |
| Same field, peak-relevant (power W, demand %) | `max()` | `<field>_max` |
| Cumulative counter (`summation_delivered`, `summation_received`, lifetime counters) | `last()` | same field name |
| Discrete state (stage number, mode enum) | `last()` | same field name |

**Naming convention**: the mean is stored under the original field name so existing dashboard queries keep working when they switch from raw to longterm. The max is stored under `<field>_max` and is the field analytical queries (5CP attribution, peak-demand panels) reach for explicitly.

**Per-measurement field overrides**: encode in the task's lookup tables. Initial values:

```
cumulative_fields = [
    "summation_delivered",
    "summation_received",
]

# Default for everything else: mean + max
# Future per-measurement overrides go here when ComfortNet schema lands:
#   hvac.comfortnet.heat_actual_pct → mean + max
#   hvac.comfortnet.cfm             → mean + max
#   hvac.comfortnet.fault_critical  → (excluded; lives in hvac.comfortnet.events)
```

## ComfortNet schema implications

Two measurements:

- **`hvac.comfortnet`** — continuous fields per decoded frame: `heat_demand_pct`, `heat_actual_pct`, `cool_demand_pct`, `cool_actual_pct`, `fan_actual_pct`, `cfm`, `supply_temp_f`, `return_temp_f`, `outdoor_temp_f`, `humidify_demand_pct`, `dehumidify_demand_pct`, etc. Tags: `device` (furnace/ac/thermostat), `src_node_type`. Goes to `energy`, downsampled to longterm at 1-min mean+max.
- **`hvac.comfortnet.events`** — discrete events with payload: stage transitions, fault codes (with descriptive labels from the user-menu DIAG page), demand-vs-actual mismatches above threshold. One field per event type (or use `_value` + `event_type` tag). Goes directly to `energy-longterm`, never aggregated.

The use-cases doc field-requirements table maps cleanly onto this split.

## The Flux task

Single task running every 1 min, downsamples the prior completed minute window. Concrete shape (final source lives in `deploy/energy-stack/influx-tasks/downsample-energy-1m.flux`):

```flux
import "date"

option task = {
    name: "downsample-energy-1m",
    every: 1m,
    offset: 30s,
}

src = "energy"
dst = "energy-longterm"

aggregateMeasurements = [
    "eagle.meter",
    "refoss.channel",
    "refoss.system",
    "hvac.comfortnet",
]

cumulativeFields = [
    "summation_delivered",
    "summation_received",
]

base = from(bucket: src)
    |> range(start: -2m, stop: -1m)
    |> filter(fn: (r) => contains(value: r._measurement, set: aggregateMeasurements))

// Cumulative counters: last(), keep field name
base
    |> filter(fn: (r) => contains(value: r._field, set: cumulativeFields))
    |> aggregateWindow(every: 1m, fn: last, createEmpty: false)
    |> to(bucket: dst)

// Continuous fields: mean(), keep field name
base
    |> filter(fn: (r) => not contains(value: r._field, set: cumulativeFields))
    |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
    |> to(bucket: dst)

// Continuous fields: max(), suffix _max
base
    |> filter(fn: (r) => not contains(value: r._field, set: cumulativeFields))
    |> aggregateWindow(every: 1m, fn: max, createEmpty: false)
    |> map(fn: (r) => ({r with _field: r._field + "_max"}))
    |> to(bucket: dst)
```

**Why the 30-second offset**: the task starts 30 seconds after the minute boundary so any in-flight writes from the per-frame pollers complete before the window is read. `range(start: -2m, stop: -1m)` then reads the prior fully-closed minute, not the partial current one.

**Tag preservation**: `aggregateWindow` preserves all existing tags by default. Don't add `set()` or `map()` calls that touch tag keys, or cardinality drifts between buckets.

## Healthcheck

Risk: silent task failure means raw rolls off `energy` at 90 days while `energy-longterm` stops receiving aggregates, with no symptom in the UI until somebody notices a gap in a Grafana panel three months later.

Two-layer detection:

1. **InfluxDB native deadman check** (preferred): create a check rule on a query against `energy-longterm` filtered to a sentinel measurement (`refoss.channel` works — high-cadence and reliable). If no points received in the last 5 min, fire. Notification rule sends to a webhook on `telegram-notifier`, which routes to the existing Telegram path.
2. **Telegram-notifier daily summary**: include a "downsampling task: ok / stale" line in the existing daily summary message, computed by querying the same sentinel against longterm.

Both layers are cheap. The deadman gives quick alerts; the daily summary catches anything the deadman misses (deadman misconfigured, notification rule broken).

## Storage projection

Per-frame writers (eagle, refoss, comfortnet) at 30-33 s native cadence:

- Raw daily volume: ~62k points/day
- Raw 90-day footprint: ~5.6M points compressed at ~30-50 bytes/point ≈ **280 MB**

Longterm (1-min mean+max + coarse passthrough + events):

- Aggregate daily volume: ~33k points/day for the per-frame sources
- Coarse + event daily volume: ~2k points/day
- Longterm yearly footprint: ~13M points × ~40 bytes ≈ **520 MB/year**
- 10-year longterm projection: **~5.2 GB**

Total at the 10-year mark: 280 MB raw + 5.2 GB longterm + InfluxDB index/WAL overhead ≈ **6-8 GB**. `pi-lab` is comfortable with this.

If ComfortNet's actual emit rate turns out higher than the ~5 frames/cycle estimate (more decoded message types, write traffic in v2), redo the projection but the order of magnitude doesn't change.

## Migration plan

Phased to minimize risk. Each phase is independently revertible.

1. **Create `energy-longterm` bucket** on `pi-lab`'s InfluxDB with infinite retention. Provision via the existing compose / setup-script pattern in `deploy/energy-stack/`. No data flowing yet.
2. **Deploy the Flux task** as a one-shot Influx config alongside compose (or via the InfluxDB CLI on first run). Verify: after one minute, `energy-longterm` should contain mean + max for `eagle.meter` and `refoss.channel`. Let it run for 24 h before proceeding. If it fails, just delete the task; nothing else changes.
3. **Reroute coarse + event pollers** to write directly to `energy-longterm`. One poller at a time, in this order: `nws-poller`, `comed-poller`, `thermostat-poller`, `haven-ingest`, `comed-bill` ingest, `hvac-scheduler` (events). After each, verify writes land in longterm and stop landing in raw. Bump container env: `INFLUX_BUCKET=energy-longterm`.
4. **Set 90-day retention on `energy`**. Last step. After this, the historical pre-task data starts rolling off; only fields covered by the downsampling task survive in longterm. Skip if you change your mind about whether to ever drop raw.
5. **Add the healthcheck**. Influx deadman check + telegram-notifier daily-summary line.

ComfortNet integration lands after step 4 and is built knowing the retention design.

## Open decisions before implementing

1. **Does anyone actually want backfill?** April-May 2026 raw data exists only in `energy`; it'll roll off 90 days after step 4. Backfilling into longterm is a one-shot Flux script: doable, marginal value. **Recommendation: skip.** If you ever want long-horizon trends covering this initial period, accept the gap.
2. **Refoss CT polarity fix on `em:5` and `em:14`** is now done (Refoss app `factor=-1`). Mean+max aggregation handles signed values correctly so this is no longer a concern, but flag it here in case there's another sign-flip lurking.
3. **`max()` on temperatures and percentages**: useful or noise? Power max is unambiguously useful (peak demand → 5CP). Temperature max is mildly useful (peak heat). Percentage max is useful for "did we ever hit 100% firing rate this hour?" Keep it; the storage cost is negligible.
4. **Cumulative counter handling in longterm**: `last()` per minute is correct for cumulative kWh. Cross-check after step 2 by computing `delta(summation_delivered)` over a known interval in both buckets and confirming agreement.
5. **Per-measurement aggregation overrides**: the cumulative-fields list is the only override needed today. ComfortNet may need per-field rules once the schema lands; the task structure supports this with minimal change (extend `cumulativeFields` or add a `lastFields` set).
