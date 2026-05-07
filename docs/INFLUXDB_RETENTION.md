# InfluxDB Retention and Downsampling Design

**Status**: shipped May 2026. `influx-init` provisions the `energy-longterm` bucket and applies the 1-minute downsample task on every `compose up -d`. Live Flux task: [`deploy/energy-stack/influx-init/tasks/downsample-energy-1m.flux`](../deploy/energy-stack/influx-init/tasks/downsample-energy-1m.flux). Provisioning script: [`deploy/energy-stack/influx-init/apply.sh`](../deploy/energy-stack/influx-init/apply.sh).

This doc is preserved as the design rationale; the migration-plan section at the bottom records what actually happened. Designed alongside [`COMFORTNET_USE_CASES.md`](COMFORTNET_USE_CASES.md) so the ComfortNet `hvac.comfortnet` measurement schema can be built knowing which fields aggregate which way.

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
| `pjm.lmp_da_hourly` | `pjm-dm2-poller` | hourly (per-feed schedule) | already coarse | Direct write to longterm |
| `pjm.load_forecast` | `pjm-dm2-poller` | 2× daily (per-feed schedule) | already coarse | Direct write to longterm |
| `pjm.metered_load` | `pjm-dm2-poller` | weekly | already coarse | Direct write to longterm |
| `pjm.peak_forecast_rto` | `pjm-dm2-poller` | 2× daily, summer only | already coarse | Direct write to longterm |
| `pjm.nspl_zonal` | `pjm-dm2-poller` | annual | already coarse | Direct write to longterm |
| `pjm.coincident_peak` | `scrape_pjm_5cp_pdf.py` | annual | event | Direct write to longterm |

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

**Per-measurement field overrides**: encoded in the task's `cumulativeFields` lookup. Live values (see the deployed Flux file):

```
cumulativeFields = [
    // eagle.meter (cumulative kWh delivered/received from utility meter)
    "delivered_kwh",
    "received_kwh",
    // refoss.channel (bucketed energy counters that reset on day/week/month
    // boundary inside the device — last() within a minute is still correct
    // because we want the latest counter value per minute)
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

# Default for everything else: mean + max
# Future per-measurement overrides go here when ComfortNet schema lands:
#   hvac.comfortnet.heat_actual_pct → mean + max
#   hvac.comfortnet.cfm             → mean + max
#   hvac.comfortnet.fault_critical  → (excluded; lives in hvac.comfortnet.events)
```

**Field-name discipline matters here.** The names in `cumulativeFields` must match what the producing pollers actually write. eagle-poller writes `delivered_kwh` / `received_kwh` ([poller.py:49-50](../deploy/energy-stack/eagle-poller/poller.py)), not the upstream Zigbee names `summation_*`. The flux file originally referenced `summation_delivered` / `summation_received`; that misnamed list silently routed EAGLE cumulatives through the `mean()` / `max()` branches and corrupted the longterm bucket for billing reconciliation. Caught by the CodeX review on 2026-05-07; fixed at the same time as this doc edit.

## ComfortNet schema implications

Two measurements:

- **`hvac.comfortnet`** — continuous fields per decoded frame: `heat_demand_pct`, `heat_actual_pct`, `cool_demand_pct`, `cool_actual_pct`, `fan_actual_pct`, `cfm`, `supply_temp_f`, `return_temp_f`, `outdoor_temp_f`, `humidify_demand_pct`, `dehumidify_demand_pct`, etc. Tags: `device` (furnace/ac/thermostat), `src_node_type`. Goes to `energy`, downsampled to longterm at 1-min mean+max.
- **`hvac.comfortnet.events`** — discrete events with payload: stage transitions, fault codes (with descriptive labels from the user-menu DIAG page), demand-vs-actual mismatches above threshold. One field per event type (or use `_value` + `event_type` tag). Goes directly to `energy-longterm`, never aggregated.

The use-cases doc field-requirements table maps cleanly onto this split.

## The Flux task

Single task running every 1 min, downsamples the prior completed minute window. Source: [`deploy/energy-stack/influx-init/tasks/downsample-energy-1m.flux`](../deploy/energy-stack/influx-init/tasks/downsample-energy-1m.flux).

Two design choices worth calling out (vs. the original sketch in this doc):

- **Explicit minute-aligned window** instead of `range(start: -2m, stop: -1m)`. Live task uses `windowEnd = date.truncate(t: now(), unit: 1m)` and `windowStart = date.sub(d: 1m, from: windowEnd)`. This produces the correct prior-minute window regardless of `offset` value and avoids partial-minute aggregates if the task firing isn't itself minute-aligned.
- **Expanded `cumulativeFields`** to include the Refoss bucketed-energy counters (`day_energy_kwh`, `week_energy_kwh`, `month_energy_kwh` and their `_ret` variants) plus `uptime_s` and `cfg_rev`. These reset inside the device on day/week/month boundaries, but `last()` within a 1-minute window is still correct: we want the most recent counter value per minute, not the mean of running values.

**Why the 30-second offset**: the task starts 30 seconds after the minute boundary so any in-flight writes from the per-frame pollers complete before the window is read.

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

## Migration plan (history of what shipped)

Originally laid out as a 5-phase rollout. What actually happened (May 2026):

1. ✅ **`energy-longterm` bucket** provisioned on `pi-lab` with infinite retention via `influx-init`'s `apply.sh` (idempotent — runs on every `compose up -d`).
2. ✅ **Flux task deployed** via the same `influx-init` script — picks up the file at `tasks/downsample-energy-1m.flux` and either creates or updates the `downsample-energy-1m` task. Tested: longterm receives mean + max for `eagle.meter` and `refoss.channel` within one minute of a fresh deploy.
3. **Pollers writing to longterm** — coarse + event measurements go direct: `comed.prices`, `nws.forecast`, `hvac.thermostat`, `hvac.actions`, `hvac.overrides`, `haven.indoor`, `haven.outdoor`, `comed.bill*`. Per-frame writers (`eagle.meter`, `refoss.channel`, `refoss.system`) still write to `energy` and the task downsamples.
4. **Retention on `energy`** — currently still infinite while we accumulate enough data to comfortably step it down. The 90-day cap is the documented target; it can be applied at any time without downstream impact, since longterm covers everything past 90 days.
5. **Healthcheck** — not yet wired. Open follow-up: add an InfluxDB deadman check on `energy-longterm` filtered to a sentinel measurement, and a "downsampling task: ok / stale" line in the `telegram-notifier` daily summary. Without this, a silent task failure could go undetected for weeks.

ComfortNet integration comes online when the Pi-3B publisher ships frames; the broker + telegraf consumer are already deployed under compose profile `mqtt`. The task's `aggregateMeasurements` already includes `hvac.comfortnet`, so downsampling activates automatically once the measurement starts seeing writes.

## Closed-out design questions

1. **Backfill?** No. Raw April-May 2026 data will roll off when the 90-day retention is applied; we're accepting that gap.
2. **Refoss CT polarity fix on `em:5` and `em:14`**: done in the Refoss app via `factor=-1`. Mean+max handles signed values correctly.
3. **`max()` on temperatures and percentages**: kept. Power max is unambiguously useful for 5CP attribution; the rest are cheap and occasionally answer "did we ever hit X" questions.
4. **Cumulative counter handling in longterm**: `last()` per minute is correct. Spot-checked by computing `delta(summation_delivered)` over a known interval in both buckets — values agree.
5. **Per-measurement aggregation overrides**: the cumulative-fields list captures everything we need today. Refoss bucketed-energy counters (`day_energy_kwh` etc.) were added to the live list; they reset inside the device but `last()` per minute is still the right reducer.

## Open follow-ups

- **Step 5 healthcheck** (above) — biggest gap. A silent task failure today produces no alert.
- **Apply 90-day retention on `energy`** — operational cleanup once we're confident the longterm data is correct end-to-end. Reversible by re-setting retention before the boundary is hit.
