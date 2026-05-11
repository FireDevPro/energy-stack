# ComEd 2025 Threshold Analysis

Frozen scripts and source data used to derive the Arm B threshold values locked in `docs/EXPERIMENT_DESIGN.md` Appendix A. Bundled with the OSF pre-registration so a reader can re-run every cited number against the same inputs.

## Provenance

**Price data — `data/{may,jun,jul,aug,sep}2025.txt`**
ComEd 5-minute Hourly Pricing real-time print (Rate BESH), May 1 – Sep 30 2025. Exported from the public ComEd Hourly Pricing tool ([hourlypricing.comed.com](https://hourlypricing.comed.com/)). Each file is the raw response payload: `unixMillis:cents_per_kWh,...`. Public price data only — no account number, meter ID, or personal metadata.

**Weather data — `data/weather2025.json`**
Open-Meteo ERA5 historical archive, requested at the same coordinates the production `nws-poller` reads from (`NWS_LAT=41.6151`, `NWS_LON=-88.2018`; Plainfield IL; NWS office LOT, gridpoint 57/60). ERA5 snaps the request to its grid; the stored payload reflects the served point at lat 41.581722 / lon -88.18182. Hourly fields: `temperature_2m`, `relative_humidity_2m`, `dew_point_2m`, `wind_speed_10m`, `shortwave_radiation`, `apparent_temperature`. Units: °F / mph. Date range: 2025-05-01 to 2025-09-30 (3672 hours). Timezone returned as `America/Chicago`.

Request URL (without API key):
```
https://archive-api.open-meteo.com/v1/archive
  ?latitude=41.6151&longitude=-88.2018
  &start_date=2025-05-01&end_date=2025-09-30
  &hourly=temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m,shortwave_radiation,apparent_temperature
  &temperature_unit=fahrenheit&wind_speed_unit=mph
  &timezone=America/Chicago
```

Source pulled 2026-05-11; weather is read-only historical reanalysis, so the same URL re-pulls the identical payload.

## How to run

```sh
python analyze.py
python correlate.py
python q_humid.py
python q_under87.py
python verify_appendix_a.py
```

Each script is self-contained (stdlib only, Python 3.9+ for `zoneinfo`). Paths resolve relative to the script's own directory, so the bundle is portable.

`expected_output.md` captures the output every script produces against the frozen `data/`. Any change to the scripts or data must reproduce these numbers; deviations indicate either a data refresh or a script change and must be reconciled before the change is merged.

## What each script answers

| Script | Purpose | Drives |
|---|---|---|
| `analyze.py` | Hourly price distribution, hour-of-day patterns, scarcity-day catalog, current-window analysis | P95/P99/max threshold derivation; 18:00 CT peak hour and 23.8% spike fraction; 14-17 CT window critique |
| `correlate.py` | Pearson/Spearman correlation of hourly price vs weather; daily max temp vs spike count; individual scarcity-day weather conditions | Temperature-correlated vs grid-event split; hot-no-spike and mild-spike counts |
| `q_humid.py` | Hot-day stratification by dewpoint and heat index; spike rate × temperature × dewpoint matrix | Day-type apparent-temperature threshold (≥90°F) |
| `q_under87.py` | Spike and scarcity days by max-temp threshold; full list of <87°F spike days | "8 of 17 scarcity days had max temp <87°F" claim |
| `verify_appendix_a.py` | Direct re-derivation of every numeric claim in Appendix A | Sanity check before any threshold edit |

## Appendix A claim mapping

Every numeric claim in `docs/EXPERIMENT_DESIGN.md` Appendix A is reproducible from this bundle:

| Appendix A claim | Produced by | Captured in `expected_output.md` as |
|---|---|---|
| `n = 3,663 hourly observations` | `analyze.py` | `Total hourly records: 3663` |
| `n = 122 summer days` (Jun-Sep) | `q_under87.py` | `Total summer days (Jun-Sep 2025): 122` |
| `P95 = 9.53¢/kWh` | `analyze.py` | `P95: 9.53` (overall hourly distribution) |
| `P99 = 20.47¢/kWh` | `analyze.py` | `P99: 20.47` |
| `peak hour was 18:00 CT (mean 11.03¢, max 161.29¢)` | `analyze.py` | `18:00 CT: mean=11.03 ... max=146.29` (interior mean); top-20 list shows `2025-06-24 Tue 17:00 CT 161.29c/kWh` (which is the overall max; 18:00 is the highest-mean hour). |
| `8 of 17 scarcity days had max temp <87°F` | `q_under87.py` / `verify_appendix_a.py` | `Max temp < 87F: 8 of 17 scarcity days (47.1%)` |
| `18:00 CT had 23.8% of hours above 10¢` | `analyze.py` / `verify_appendix_a.py` | `18:00: 29h ( 23.8%)` |
| `54% of spike days and 71% of scarcity days are temperature-correlated (max temp ≥85°F OR apparent ≥90°F)` | `verify_appendix_a.py` | `Spike days (>=10c): 29 of 54 = 53.7% ... Scarcity days (>=20c): 12 of 17 = 70.6%` |
| `Remaining 46% of spike days are grid-event-driven` | `verify_appendix_a.py` | `Grid-event-driven spike days: 25 of 54 = 46.3%` |

## Scope

This bundle is the **threshold-derivation** evidence locked at OSF filing. The 2026 cooling-season YoY comparison work (the `tools/comed_2026_analysis/...` referenced elsewhere in planning notes) is **not** included here and is not pre-registered — it is post-filing observational work and lives outside this directory.
