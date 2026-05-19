# PJM Data Miner 2 Integration Design

**Status**: Phases 1 + 2 shipped May 2026. `pjm-dm2-poller` running in production; backfill complete; 5CP PDF scraper ready for the Nov 2026 annual run. Forward work below.
**Owner**: Chris dePaola
**Depends on**: existing `energy-stack` (InfluxDB, Telegram notifier), Non-Member API key on `pi-lab`
**Companion docs**: [`PJM_DM2_FEEDS.md`](PJM_DM2_FEEDS.md) (auto-generated feed catalog), [`plans/sced-rebaseline-spec-2026-05-13.md`](plans/sced-rebaseline-spec-2026-05-13.md) (binding spec for the field study this data feeds). Operational details in [`SERVICES.md#pjm-dm2-poller`](SERVICES.md#pjm-dm2-poller).

---

## Problem

Three open scheduler / experiment work items need PJM-zone data we don't currently capture:

1. **5CP-probability per hour**: the existing day-type classifier (`HOT_5CP_RISK`, `HOT_STREAK_DAY1`) is binary at day granularity. A probabilistic hour-level estimate of "is this hour a PJM 5CP coincident-peak hour" would let the scheduler weight stage-2 avoidance and pre-cool-depth choices with calibrated confidence rather than fixed thresholds. Drives the eventual capacity-charge bill.
2. **Forecast bias correction for ComEd retail hourly pricing**: ComEd's retail hourly rate is derived from PJM zonal RT-LMP plus a retail markup. Having the raw zonal LMP lets us decompose retail-price spikes into "LMP movement" vs "retail markup tier change" and detect sustained mismatch.
3. **Real-time peak-day signal**: PJM publishes its own daily projected peak load and projected peak hour. A scheduler-side cross-check against our day-type classifier catches days where PJM's signal disagrees with ours.

All three are supporting work for the [field study](plans/sced-rebaseline-spec-2026-05-13.md) (binding spec). None is in the critical path for summer 2026 alternation start, but all should be in place before summer ramps in.

## Goals

- Single low-frequency poller service writing PJM zonal data to InfluxDB measurements.
- One-time historical backfill from January 2021 onward (5 cooling seasons of training data for the 5CP-probability model).
- Annual scrape of the official PJM 5CP-hour PDF, since that data is not exposed via the DM2 API.
- Total steady-state load: ≤ 6 calls/day, well within Non-Member tier ceiling.

## Non-goals

- **Member-tier API access.** Non-Member tier covers everything we need. No subscription cost.
- **Ancillary services / reserves market data.** Not relevant for residential cooling-cost control.
- **Real-time 5-minute LMP polling.** The data exists (`rt_fivemin_hrl_lmps`) but the scheduler doesn't operate on a 5-minute cadence; daily DA LMP + hourly RT LMP are sufficient.
- **Multi-zone coverage.** ComEd zone for household-relevant feeds (DA LMP, ComEd-zone metered/inst load, ComEd-zone forecast). RTO totals also captured for the §3 dual-scope 5CP detector via `pjm.metered_load{zone=RTO}` and `pjm.inst_load{area="PJM RTO"}` because PJM 5CPs are RTO-wide hours, not zonal. No other PJM zones polled.

## Architecture

A new service in `deploy/energy-stack/docker-compose.yml`: `pjm-dm2-poller`. Container pattern matches the existing `comed-poller` and `nws-poller` (Python loop, tenacity retries, structured JSON logging, scheduled timed polls within an `asyncio` loop). Writes to a new set of InfluxDB measurements in the existing `energy` bucket.

### Service responsibilities

| Endpoint | Schedule | Calls/day | Purpose |
|---|---|---|---|
| `da_hrl_lmps` for ComEd zone | Once daily, ~17:00 CT (after DA market clear) | 1 | Tomorrow's DA LMP for forecast-bias correction |
| `load_frcstd_7_day` for ComEd | Twice daily (06:00 CT, 13:00 CT) | 2 | Tomorrow + day-after load forecast for 5CP-probability inference |
| `ops_sum_frcst_peak_rto` | Twice daily (06:00 CT, 13:00 CT), Jun-Sep only | 2 (cooling-season-only) | RTO peak-day signal |
| `hrl_load_metered` for ComEd (`zone=CE`) | Hourly, 5-day lookback | ~24 | Live ComEd-zone metered load for §3 5CP detector + training set |
| `hrl_load_metered_rto` (`zone=RTO`) | Hourly, 5-day lookback | ~24 | RTO-wide aggregate companion for the §3 dual-scope 5CP detector |
| `inst_load` for ComEd (`area=COMED`) + `inst_load_rto` (`area=PJM RTO`) | Every 5 min, both scopes | ~288 each | Sub-hourly approximate load for §3 5CP detector live signal |
| `rt_hrl_lmps` for ComEd zonal pnode | Daily ~12:00 CT (~1h after PJM 11-12 ET settled-data publish) | 1 | Settled hourly LMP for §8 bill-canonical HVAC$ |
| `annual_zonal_nspl` for ComEd | Annually (December 1) | <0.01 amortized | NSPL change detection |

Steady-state daily call budget: ~340 calls per scope per day for the `inst_load` 5-minute feeds, plus ~24 hourly for each metered-load scope, plus a handful of daily-cadence feeds. Total ~700-720 calls/day. With the 6 calls/min ceiling and 5-min tick spacing, the wake loop paces well under the rate limit (each tick fires at most 2-4 feeds, all space ≥ 5 s apart).

### Out-of-band scrapers

A separate cron-driven script, **`scripts/scrape_pjm_5cp_pdf.py`**, runs once on November 15 each year. Fetches `https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/summer-YYYY-peaks-and-5cps.pdf`, parses the 5 hour-by-hour rows for the just-completed summer, writes to `pjm.coincident_peak`. Hardcoded URL pattern with year template; fall back to manual-entry mode if PJM changes the PDF format.

A separate one-shot **`scripts/backfill_pjm.py`** runs at first deploy. Pulls 5 years (2021-01-01 through current) of:

- `da_hrl_lmps` for `pnode_id = 33092371` (ComEd zone): ~43,800 rows in 1 API call.
- `hrl_load_metered` for `zone = "COMED"`: ~43,800 rows in 1 API call.
- `load_frcstd_hist` for ComEd: depth depends on what's retained, but bounded.

Total backfill API budget: ~5 calls. Manual web-UI CSV download is the alternative if API is rate-limited; both paths produce the same Influx writes.

## InfluxDB schema

All measurements in the `energy` bucket. ComEd zonal context unless noted.

### `pjm.lmp_da_hourly`

One point per hour per pnode. Tagged `pnode_id`, `pnode_name`, `zone`.

| Field | Type | Notes |
|---|---|---|
| `total_lmp_da` | float | $/MWh, includes congestion + loss |
| `system_energy_price_da` | float | Reference energy price |
| `congestion_price_da` | float | Congestion component |
| `marginal_loss_price_da` | float | Loss component |

Timestamp: `datetime_beginning_ept` converted to UTC.

### `pjm.lmp_rt_hourly`

Same schema as `pjm.lmp_da_hourly`. Polled separately if/when we want RT bias correction (deferred; not in initial poller scope).

### `pjm.load_forecast`

One point per forecast-target hour per `forecast_area`. Tagged `forecast_area`, `evaluated_at_iso` (when this forecast was published).

| Field | Type | Notes |
|---|---|---|
| `forecast_load_mw` | float | Predicted load in MW |
| `horizon_hours` | int | (computed) `forecast_datetime_beginning - evaluated_at` |

Timestamp: `forecast_datetime_beginning_ept` converted to UTC. **Note**: a single hour-target can have multiple forecast points if PJM revises the forecast through the day. Tag on `evaluated_at_iso` keeps the revisions distinct.

### `pjm.metered_load`

One point per hour per zone. Tagged `zone` (`CE` for ComEd), `load_area`, `is_verified`.

| Field | Type | Notes |
|---|---|---|
| `mw` | float | Hourly metered load |

### `pjm.peak_forecast_rto`

One point per `generated_at_ept` (PJM's daily peak forecast publication). Tagged `area` (`PJM RTO`).

| Field | Type | Notes |
|---|---|---|
| `load_forecast_mw` | float | Projected daily peak load (MW) |
| `projected_peak_datetime_ept` | string | EPT timestamp of expected peak hour (string field for downstream parsing) |
| `total_scheduled_capacity_mw` | float | RTO scheduled capacity at peak |
| `operating_reserve_mw` | float | Reserve at peak |
| `internal_scheduled_capacity_mw` | float | Internal-resource component |
| `scheduled_tie_flow_mw` | float | Net scheduled tie flow at peak |
| `unscheduled_steam_capacity_mw` | float | Unscheduled steam capacity at peak |

### `pjm.coincident_peak`

One point per official 5CP hour. Written by `scripts/scrape_pjm_5cp_pdf.py` annually.

Tagged `summer_year`, `peak_rank` (1-5).

| Field | Type | Notes |
|---|---|---|
| `peak_load_mw` | float | RTO peak at the 5CP hour |
| `comed_zone_load_mw` | float | ComEd's coincident load at the 5CP hour (this is the metric that drives the next year's capacity charge) |

Timestamp: the actual 5CP hour.

### `pjm.nspl_zonal`

One point per year per zone. Annual feed (Dec 1) plus historical years already in the feed. Tagged `zone` (`COMED`) and `year`.

| Field | Type | Notes |
|---|---|---|
| `nspl_mw` | float | Network Service Peak Load allocation |

## Authentication

```
PJM_DM2_API_KEY=<32-char hex from PJM API Portal>
```

Lives in `deploy/energy-stack/.env` on `pi-lab` (chmod 600), placeholder in `.env.example`. Header passed as:

```python
headers = {"Ocp-Apim-Subscription-Key": os.environ["PJM_DM2_API_KEY"]}
```

Same secret-handling pattern as `EAGLE_INSTALL_CODE`, `CONTROL4_PASSWORD`, etc.

## Error handling

- **400 with `code: API_1014`**: filter field name invalid. Should never happen post-design; flag as bug if seen.
- **401**: auth failure. Telegram alert, halt poller, prompt for key check.
- **404**: feed slug doesn't exist. Catalog-level bug; flag in catalog.
- **429**: rate limit. Backoff with exponential delay (start at 30s, max 300s); retry. Should be rare given our 6 calls/day load against a 6/min ceiling.
- **500/503**: PJM-side outage. Tenacity retry with exponential backoff (60s, 180s, 600s). After 3 failed retries, log warn and skip the cycle. Telegram alert if 24h consecutive failure.

## Code layout (as shipped)

```
deploy/energy-stack/
├── docker-compose.yml           # pjm-dm2-poller service entry (lines 139-155)
├── .env.example                 # PJM_DM2_API_KEY placeholder
├── pjm-dm2-poller/
│   ├── Dockerfile
│   ├── app.py                   # main service loop (FEED_SCHEDULE + dispatchers + point builders)
│   ├── requirements.txt         # aiohttp, influxdb-client
│   └── test_pjm_dm2_poller.py   # canned-payload tests for each point builder
├── scripts/
│   ├── backfill_pjm.py          # one-shot 5-year history backfill
│   └── scrape_pjm_5cp_pdf.py    # annual 5CP PDF parser
```

The originally-planned `feeds.py` constants module collapsed into top-of-file constants in `app.py` (`COMED_PNODE_ID`, `COMED_FORECAST_AREA`, `COMED_METERED_ZONE`, `COMED_NSPL_ZONE`) since the feed set is small and stable. The originally-planned `dm2_feed_catalog.py` regenerator was not built — `PJM_DM2_FEEDS.md` is hand-maintained for now; if the feed set grows, the regenerator may become worth building.

## Testing

`pytest` in `pjm-dm2-poller/test_pjm_dm2_poller.py` (matching the pattern in `nws-poller/test_nws_poller.py`):

- Mock httpx responses against canned JSON fixtures (one per measurement). Fixtures derived from real `/metadata` responses to keep schemas honest.
- Test the timezone conversion (PJM ept → UTC) on DST boundaries.
- Test forecast-revision dedup (same target hour, different `evaluated_at`) writes correctly.
- Test backfill paginates correctly when `totalRows > 50000` (synthetic case; ComEd zone alone won't trigger this in 5 years).

## Sequencing

| When | Milestone | Status |
|---|---|---|
| 2026-05-06 | This design doc + [feed catalog](PJM_DM2_FEEDS.md) committed | ✅ shipped (PR [#27](https://github.com/Promithius-DR/energy-proxy/pull/27)) |
| 2026-05 | `pjm-dm2-poller` phase 1 (`da_hrl_lmps`, `load_frcstd_7_day`) | ✅ shipped (PR [#29](https://github.com/Promithius-DR/energy-proxy/pull/29)) |
| 2026-05 | `scripts/backfill_pjm.py` for one-shot 5-year history backfill | ✅ shipped (PR [#31](https://github.com/Promithius-DR/energy-proxy/pull/31)) |
| 2026-05 | `scripts/scrape_pjm_5cp_pdf.py` + 5-year backfill of 2020-2024 5CP hours | ✅ shipped (PR [#30](https://github.com/Promithius-DR/energy-proxy/pull/30)) |
| 2026-05 | `pjm-dm2-poller` phase 2 (`hrl_load_metered`, `ops_sum_frcst_peak_rto`, `annual_zonal_nspl`) | ✅ shipped (PR [#32](https://github.com/Promithius-DR/energy-proxy/pull/32)) |
| 2026-Q3 | 5CP-probability classifier model — depends on `pjm.metered_load` history + `pjm.coincident_peak` labels | Pending (parked behind summer 2026 SCED experiment) |
| 2026-11-15 | First annual 5CP PDF scrape runs (for summer 2026) | Scheduled |

## Risks and open questions

- **PJM PDF format drift**: the 5CP PDF format hasn't changed materially in 5+ years per the publicly archived versions, but a format change would silently break the scraper. Mitigation: the scraper validates row count == 5 + plausibility-checks peak loads (within 50–200 GW); fails loud if either check fails.
- **Forecast-revision write amplification**: `load_frcstd_7_day` re-publishes hour targets every hour. At 7 days × 24 hours × 25 forecast areas = 4,200 forecast points per publication, twice daily = 8,400 points/day if we ingest all. Most are duplicates. Mitigation: filter on `forecast_area = "COMED"` (and RTO total) at the API level, not client-side. Reduces to ~336 points/day.
- **DST boundary handling**: PJM publishes both `_ept` and `_utc` timestamps. We always use `_utc` for InfluxDB writes. EPT (Eastern Prevailing Time) follows DST; UTC does not. Cross-checked at one DST boundary in tests.
- **Backfill row-count limit**: `da_hrl_lmps` for ComEd zone alone over 5 years = ~43,800 rows, which fits in one 50,000-row API call. If we ever want multi-zone backfill or 10-year depth, we'd hit the row ceiling and need pagination. Not a current concern.
- **NSPL relevance**: NSPL is the transmission-charge metric, not the capacity-charge metric. It's structurally related to 5CP because both are computed from coincident-peak-hour load coincidence, but they're not the same. Annual-zonal-NSPL is a secondary signal at best; primary 5CP labels still come from the PDF.

## References

- [PJM Data Miner 2 portal](https://dataminer2.pjm.com/)
- [DM2 API Guide](https://learn.pjm.com/-/media/DotCom/etools/data-miner-2/data-miner-2-api-guide.ashx)
- [PJM API Portal (subscription management)](https://apiportal.pjm.com/)
- [PJM Limitations FAQ — rate limits](https://learn.pjm.com/three-priorities/keeping-the-lights-on/data-miner-faqs/are-there-any-limitations)
- [Annual 5CP PDF (example: summer 2024)](https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/summer-2024-peaks-and-5cps.pdf)
- [`PJM_DM2_FEEDS.md`](PJM_DM2_FEEDS.md) — feed catalog with filterable columns and ComEd-specific constants
