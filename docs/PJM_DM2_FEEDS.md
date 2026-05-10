# PJM Data Miner 2 — Feed Catalog

Auto-generated metadata catalog for the candidate feeds we surveyed for residential energy-management use cases (5CP probabilistic forecasting, LMP forecast-bias correction, historical training data). One row per feed, sourced from `https://api.pjm.com/api/v1/<feed>/metadata` on 2026-05-06.

**Tier**: Non-Member API. Rate ceiling: 6 calls/min. Row ceiling: 50,000 per call.
**Auth**: header `Ocp-Apim-Subscription-Key: $PJM_DM2_API_KEY`.
**Base URL**: `https://api.pjm.com/api/v1/`.

To regenerate: hit `/metadata` on each feed name listed in §"Feeds surveyed", parse `displayName`, `description`, `postingFrequency`, `retentionTime`, `firstAvailable`, `lastDataLoad`, `columns[]`. Pace ≥ 11s between calls.

---

## Feeds surveyed

### ✅ Available at Non-Member tier

| Feed | Display name | Posting | First available | Last data load | Columns |
|---|---|---|---|---|---|
| `da_hrl_lmps` | Day-Ahead Hourly LMPs | Daily | 2000-06-01 | 2026-05-06 | 14 |
| `rt_hrl_lmps` | Real-Time Hourly LMPs | Daily on Business Days | 1998-04-01 | 2026-05-06 | 14 |
| `rt_fivemin_hrl_lmps` | Real-Time Five Minute LMPs | Daily on Business Days | 2018-04-01 | 2026-05-06 | 14 |
| `load_frcstd_7_day` | Seven-Day Load Forecast | Hourly | (rolling) | 2026-05-06 | 8 |
| `load_frcstd_hist` | Historical Load Forecasts | Daily | 2011-01-01 | 2026-05-06 | 6 |
| `hrl_load_metered` | Hourly Load: Metered | Daily | 1993-01-01 | 2026-05-06 | 8 |
| `inst_load` | Instantaneous Load | Every 5 minutes | (rolling) | 2026-05-06 | 4 |
| `ops_sum_frcst_peak_rto` | Operations Summary - Projected RTO Stats at Peak | Daily | 2011-08-24 | 2026-05-06 | 11 |
| `ops_sum_frcst_peak_area` | Operations Summary - Projected Area Stats at Peak | Daily | 2011-08-24 | 2026-05-06 | 7 |
| `annual_zonal_nspl` | Annual Zonal Network Service Peak Loads | Yearly | 2022-01-01 | 2025-11-17 | 7 |
| `bill_deter_mnt_load` | Load Reconciliation Billing Determinants | Monthly | 2013-05-01 | 2026-05-01 | 4 |
| `da_marginal_value` | Day-Ahead Marginal Value | Daily | 2010-01-01 | 2026-05-06 | 7 |
| `da_reserve_market_results` | Day-Ahead Ancillary Service Market Results | Daily | 2022-10-01 | 2026-05-06 | 13 |
| `da_ancillary_services` | Day-Ahead Ancillary Service LMPs | Daily on Business Days | 2022-10-01 | 2026-05-06 | 7 |

### ❌ Not exposed at Non-Member tier (HTTP 404)

| Slug attempted | Notes |
|---|---|
| `load_frcstd_5_min` | No such slug. Use `load_frcstd_7_day` for forward forecasts; `inst_load` for current state. |
| `peak_load_history` | Not a standalone slug. Use `hrl_load_metered` with daily aggregation for historical peaks. |
| `system_5cp` | Coincident-peak hour history is not exposed via API. Published as annual PDF only — see §"5CP-hour list". |
| `forecasted_5min_rtolmp` | No such slug. |

---

## Filterable columns (high-value feeds)

Filter columns are how `?param=value` queries are built. Non-filterable columns return in the response payload but cannot be used in WHERE-style filtering.

### `da_hrl_lmps` — Day-Ahead Hourly LMPs

**Filterable**: `datetime_beginning_ept`, `datetime_beginning_utc`, `equipment`, `pnode_id`, `row_is_current`, `type`, `version_nbr`, `voltage`, `zone`.
**Returned only (not filterable)**: `pnode_name`, `system_energy_price_da`, `total_lmp_da`, `congestion_price_da`, `marginal_loss_price_da`.

**ComEd zonal aggregate**: `pnode_id = 33092371`. Filtering on `pnode_id` is reliable; `pnode_name` is not filterable.

### `load_frcstd_7_day` — Seven-Day Load Forecast

**Filterable**: `forecast_area`, `forecast_datetime_beginning_ept`, `forecast_datetime_beginning_utc`, `forecast_datetime_ending_ept`, `forecast_datetime_ending_utc`.
**Returned only**: `evaluated_at_datetime_ept`, `evaluated_at_datetime_utc`, `forecast_load_mw`.

**ComEd**: `forecast_area = "COMED"`.
**Available `forecast_area` values include**: `AE/MIDATL`, `AEP`, `AP`, `ATSI`, `BG&E/MIDATL`, `COMED`, `DAYTON`, `DEOK`, `DOMINION`, plus full RTO totals.

### `hrl_load_metered` — Hourly Metered Load (history)

**Filterable**: `datetime_beginning_ept`, `datetime_beginning_utc`, `is_verified`, `load_area`, `mkt_region`, `nerc_region`, `zone`.
**Returned only**: `mw`.

**ComEd**: `zone = "CE"` (verified against the official PJM DM2 OpenAPI spec — the `zone` allowed-values list is `AE, AEP, AP, ATSI, BC, CE, DAY, DEOK, DOM, DPL, DUQ, EKPC, JC, ME, OTHER, PE, PEP, PL, PN, PS, RECO, RTO, OVEC`. Earlier revisions of this doc said `"COMED"`; that was wrong — empirically `zone="COMED"` returns 0 rows while `zone="CE"` returns the expected data).

**Posting cadence**: per the PJM spec, "There will be a lag in updated data availability due to wait time for possible corrections. Data adjustments can occur up to 90 days after the actual date." Empirically the typical publish lag is 2-3 days. The poller's hourly polling cadence (post-§0b, May 2026) catches newly-posted historical data within ~1h of when PJM ships it; the 5-day fetch lookback absorbs the typical multi-day publish lag plus weekend gaps.

### `inst_load` — Instantaneous Load (real-time, approximate)

**Filterable**: `datetime_beginning_ept`, `datetime_beginning_utc`, `area`.
**Returned only**: `area`, `instantaneous_load`.

**ComEd**: `area = "COMED"`. Note the asymmetry vs `hrl_load_metered`: same utility, different filter parameter name AND different code value. This is per the PJM spec's per-feed allowed-values lists; the convention varies by feed.

**Per the PJM spec**: "Loads are calculated from raw telemetry data and are approximate. **The displayed values are NOT official PJM Loads.** This feed represents data frequently updated throughout the operating day. In the event of a technical issue that prevents data from being updated, PJM will work to resolve the issue but typically will not update the data to replace the missed intervals."

**Use case**: real-time directional signal for the §3 5CP detector's `current_load_mw` side. The official metered values come from `hrl_load_metered` (with multi-day publish lag) and feed the season-to-date 5th-highest baseline. Both feeds cooperate by purpose — one without the other doesn't deliver the locked detector rule (`current_zone_load_mw / season_to_date_5th_highest_mw > 0.95`).

### `ops_sum_frcst_peak_rto` — Projected RTO Stats at Peak

**Filterable**: `area`, `generated_at_ept`, `projected_peak_datetime_ept`, `projected_peak_datetime_utc`.
**Returned only**: `capacity_adjustments`, `internal_scheduled_capacity`, `load_forecast`, `operating_reserve`, `scheduled_tie_flow_total`, `total_scheduled_capacity`, `unscheduled_steam_capacity`.

The `load_forecast` field is **PJM's projected peak load for the day**; `projected_peak_datetime_ept` is **the hour PJM expects today's peak**. Single most useful real-time signal for 5CP-risk-day classification at the RTO level.

### `annual_zonal_nspl` — Annual Zonal Network Service Peak Loads

**Filterable**: `datetime_beginning_ept`, `datetime_beginning_utc`, `datetime_ending_ept`, `datetime_ending_utc`, `year`, `zone`.
**Returned only**: `nspl_mw`.

NSPL = the zone's locked annual peak-load allocation, derived from the prior summer's peak coincidence. Useful as a secondary 5CP signal (transmission-charge driver, structurally related to capacity-charge driver). Posted yearly in November.

---

## 5CP-hour list (out-of-band)

The official 5 PJM coincident-peak hours per summer (the hours that determine the next year's capacity charges) are **not in the DM2 API**. PJM publishes them in an annual PDF at:

`https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/summer-YYYY-peaks-and-5cps.pdf`

Revised each November (for the just-completed summer). For the 5CP-probability training pipeline, this file is the source of ground-truth labels.

---

## Endpoints

- `/api/v1/<feed>/metadata` — schema + posting cadence + retention. ~6.5 KB JSON.
- `/api/v1/<feed>` — search. Query params correspond to filterable columns. Pagination via `startRow` (1-based) + `rowCount` (max 50000). `totalRows` returned in payload + `X-TotalRows` header.

**Example**: yesterday's ComEd DA LMP, 24 hours.

```bash
curl -H "Ocp-Apim-Subscription-Key: $PJM_DM2_API_KEY" \
  "https://api.pjm.com/api/v1/da_hrl_lmps?pnode_id=33092371&datetime_beginning_ept=2026-05-05T00:00:00.0&rowCount=24"
```

Returns: `{"items":[...24 hourly LMPs...], "totalRows": 24}`.

## Error codes observed

| Code | Meaning | Example |
|---|---|---|
| 200 | OK | normal response |
| 400 (`API_1014`) | Filter field name invalid | filtering on a non-filterable column (e.g. `pnode_name`) |
| 404 | Feed slug doesn't exist | typo, deprecated, or never existed |
| 401 | Auth failed | wrong/expired key |

## Methodology notes

- All 18 candidate feeds queried with `/metadata` between 17:14 and 17:18 ET on 2026-05-06.
- One additional search call confirmed `pnode_id=33092371&type=Zone` filter resolves ComEd zonal aggregate to 24 hourly rows for a given day.
- One additional search call confirmed `forecast_area=COMED` resolves the 7-day load forecast subset.
- Total API budget consumed: 21 calls. Within Non-Member tier ceiling.
