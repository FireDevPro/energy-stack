---
date: 2026-05-12
owner: chris
status: draft
role-label: chris
---

# Weather-derived Ecowitt compatibility — execution plan

## Spec anchor

Implements OSF_FILING.md criterion 14 source-type `weather_derived_compatibility` and `docs/REPLAY_VALIDATION.md` (source-type catalog).

Pre-experiment replay validation requires synthetic Ecowitt-shaped rows for pre-deployment windows where Ecowitt was not yet installed. These rows are not Ecowitt readings. They are weather data from ASOS plus ERA5 mapped into the Ecowitt long-format schema so the Stage 3 loader can consume them via the same code path it uses for real Ecowitt parquet.

Naming rule: "weather-derived Ecowitt compatibility rows". Not "Ecowitt-from-NWS". Source is ASOS + ERA5, not `nws.forecast`.

## Locked decisions (from brainstorm)

| Question | Decision |
|---|---|
| Primary ASOS source | Iowa State Mesonet ASOS bulk CGI (`https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py`) with `report_type=1` for HFMETAR / 5-min ASOS plus routine and special METAR reports. (IEM API v1 JSON is sized for small queries; bulk CGI is the locked endpoint for season-length fetches.) |
| Solar radiation source | Open-Meteo / ERA5-Land shortwave (always; ASOS has no solar field) |
| ASOS gap-fill | Open-Meteo / ERA5 when material ASOS outage in a window |
| Default station | KORD (locked; repo baseline weather work already uses it). Configurable via CLI. |
| Reports type | IEM 5-min ASOS / HFMETAR where available; hourly METAR upsampled where not |
| Default time window | 2025-05-01 through 2025-09-30 (ComEd price-analysis season) |
| Output cadence | 5-minute Ecowitt-shape (matches real Ecowitt) |
| Output location | `tools/analysis/replay/weather_compat.py` (replay package) |
| Output dir layout | Compatibility bundle dir, merged into a Stage 1 replay bundle |
| Manifest source_type | `weather_derived_compatibility` |
| Per-row provenance | Tag columns `weather_source`, `solar_source`, `station`, `cadence`, `upsampled` |

## Field mapping (units explicit)

| Ecowitt field | Source | Conversion |
|---|---|---|
| `outdoor_temp_f` | IEM `tmpf` (°F) | identity |
| `outdoor_dewpoint_f` | IEM `dwpf` (°F) | identity |
| `outdoor_rh_pct` | IEM `relh` (%) | identity |
| `pressure_inhg` | IEM `mslp` (mb) | divide by 33.8639 |
| `wind_mph` | IEM `sknt` (knots) | multiply by 1.15078 |
| `solar_wm2` | Open-Meteo `shortwave_radiation` (W/m²) | identity |

## Phases (vertical slices)

Each phase is end-to-end demoable. Acceptance test exists from Phase 1; subsequent phases unblock features behind xfail markers.

### Phase 1 — tracer bullet (single hour, single field)

Smallest possible cut through every layer the feature touches.

- Fetch one hour of KORD ASOS via IEM bulk CGI (e.g., 2025-07-15 14:00 CDT).
- Map ASOS `tmpf` into one Ecowitt-shaped `outdoor_temp_f` parquet row.
- Write to `compat_bundle/ecowitt.weather.parquet` plus a manifest entry tagged `source_type=weather_derived_compatibility`.
- Acceptance test (outside-in, narrowly scoped): build a stage1 bundle containing only the compat-bundle entry. Call `pipeline._stage3_daily_avg_temps_f` (or `_load_stage3_inputs_for_week`) and assert ONLY:
  - The single row lands in the correct day index (`result[day_for_2025_07_15]` is the fetched value, within the rounding tolerance).
  - All other days remain at the empty-day default (0.0).
  - The manifest entry's `source_type` is `weather_derived_compatibility`.
  Do NOT assert the full weekly weather vector or any aggregate built from > 1 hour of data; the week is intentionally sparse and aggregate outputs are not yet meaningful.

Demoable: an audit script can read the manifest, see `source_type=weather_derived_compatibility`, and verify per-row provenance columns.

Verify steps:
1. `python -m tools.analysis.replay.weather_compat fetch --station KORD --start 2025-07-15T14:00 --end 2025-07-15T15:00 --out /tmp/compat`
2. Manifest entry present with correct sha256.
3. Stage 3 loader test passes.

### Phase 2 — full 5-min cadence + upsample with provenance

Real fetch of a multi-day window. Handle the cadence-mismatch question.

- IEM 5-min ASOS / HFMETAR endpoint where available. Native rows tagged `cadence=5min, upsampled=false`.
- When only hourly METAR exists for a sub-window: forward-fill to 5-min, tagged `cadence=5min, upsampled=true`. Document forward-fill not interpolation (preserves observed-value provenance; avoids inventing values).
- All five non-solar fields populated.
- Time-zone handling: ASOS timestamps are UTC. Convert to UTC-aware in parquet.

Verify steps:
1. Fetch 2025-07-14 through 2025-07-16 KORD.
2. Row count matches expected 5-min ticks for the window (864 rows ± gap).
3. Provenance column `upsampled` flips to true on rows derived from hourly METAR.
4. Stage 3 loader's `hourly_weather` produces 168 records for a 7-day fetch with realistic temp/dewpoint variation.

### Phase 3 — Open-Meteo / ERA5 solar enrichment

ASOS lacks solar. Solar always comes from ERA5 / Open-Meteo.

- Open-Meteo Historical Weather API (`archive-api.open-meteo.com`): `shortwave_radiation` for the same lat/lon + time range as ASOS fetch.
- Map to `solar_wm2`. Tag `solar_source=open_meteo_era5`. (Other rows have `solar_source=null`.)
- Open-Meteo native cadence is hourly. Forward-fill to 5-min the same way Phase 2 handled hourly METAR.
- Acceptance test: solar_wm2 rows present in compat bundle. Stage 3 loader's `hourly_weather[h]["solar_wm2"]` returns non-zero values during daylight hours.

Verify steps:
1. Solar field present in field_set of manifest entry.
2. Solar rows during nighttime (22:00–04:00 CT) are ~0.
3. Solar rows during midday peak >500 W/m² in summer.

### Phase 4 — ERA5 gap-fill for ASOS material outages

When ASOS has a sustained gap (e.g., station offline for 6+ hours), the row would otherwise be missing. ERA5 fills.

- Gap detection: contiguous absence of any ASOS field for ≥ `MATERIAL_GAP_MINUTES` (default 60).
- For gap rows: pull from Open-Meteo ERA5 the missing fields (temp_f, dewpoint_f, rh_pct, pressure_inhg, wind_mph; not solar — already on ERA5 by Phase 3).
- Tag those rows `weather_source=open_meteo_era5_gap_fill, upsampled=true`.
- Acceptance test: synthesize an ASOS response with a 2-hour gap. Confirm gap rows fill from ERA5 with correct provenance tag.

Verify steps:
1. Inject a known ASOS gap. Run converter. Inspect parquet for `weather_source=open_meteo_era5_gap_fill` rows.
2. Manifest entry `note` field summarizes gap-fill stats ("ASOS gap-fill rows: 24 of 8640 total, 0.28%").

### Phase 5 — CLI polish + bundle-merge + docs

User-facing interface, manifest merge tooling, documentation update.

- CLI `python -m tools.analysis.replay.weather_compat`:
  - `fetch` subcommand: produce a standalone compat bundle dir.
  - `merge` subcommand: merge a compat bundle into an existing stage1 bundle (combines manifests, copies parquet, validates source_type uniqueness per measurement).
- Update `docs/REPLAY_VALIDATION.md`:
  - Link to weather_compat as the canonical `weather_derived_compatibility` producer.
  - Document the per-row provenance columns and what an auditor reads.
  - Rewrite the existing language that says ERA5 hourly fallback "requires interpolation" to say forward-fill / compatibility rows. The choice preserves observed-value provenance and avoids inventing intermediate values; the doc must match the code.
- Add `tools/analysis/replay/README.md` section.
- Caveat doc: this is not Ecowitt. Don't run analysis as if it were post-deployment Ecowitt observations. Stage 2 still gates on rule 5 if needed.

Verify steps:
1. `python -m tools.analysis.replay.weather_compat fetch --help` prints sensible help.
2. End-to-end smoke: fetch a 1-week compat bundle, merge into a fake stage1 export, run Stages 2 + 3 against the merged bundle, observe weekly.csv populated.
3. REPLAY_VALIDATION.md mentions weather_compat with link.

## Out of scope this PR

- NOAA NCEI fallback. Locked-out of this PR. IEM bulk CGI is the only ASOS source wired here; Open-Meteo / ERA5 is the only non-ASOS source wired. If a real-world Mesonet gap surfaces post-merge that ERA5 can't fill, NOAA NCEI becomes a separate follow-on PR.
- KMDW / KJOT exploratory comparison fetch (configurable station only; comparisons are separate analysis).
- 2026 windows (window is configurable; default is 2025; production-summer 2026 bundles run post-hoc from live Ecowitt).

## Risks

- IEM API rate limiting on long ranges. Mitigation: 1-day chunked fetches + retry on 429.
- Open-Meteo historical lag (most-recent data may be 5–7 days delayed). Mitigation: 2025 window safely in archive.
- Per-row provenance columns add ~5 columns to ecowitt.weather parquet. Stage 3 loader reads only `_time, _measurement, _field, _value`, so columns are audit-only. Documented in Phase 1.
- ASOS timestamp boundary cases (METAR reports irregular, occasional duplicates). Mitigation: dedupe on `(_time, _field)` post-fetch.

## Tracking

- Sequential commits per phase. Each phase commits its tests + code, flips its xfail.
- Single PR `feature/weather-compat`, base `main`. No stacking.
- Phase 1 is the only one with a published acceptance-test name (others build on it).
- Archive this doc to `docs/plans/archive/weather-compat-plan.md` in the closing commit.
