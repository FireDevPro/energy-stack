# ComEd 2025 Threshold Analysis — source data (provenance)

Source data used to derive the Arm B threshold values for the (now-retired)
day-type controller design. The analysis **scripts** that consumed this data
(`analyze.py`, `correlate.py`, `q_humid.py`, `q_under87.py`,
`verify_appendix_a.py`, `check_partial_hours.py`) were removed in the
commissioning-controller demolition along with the day-type/SCED design they
fed. The raw source data below is **preserved** as an irreplaceable provenance
artifact.

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
