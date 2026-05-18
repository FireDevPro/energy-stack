---
date: 2026-05-18
owner: chris
status: active
role-label: code-team
---

# NOAA fallback station selection (ASOS / AWOS-3)

**Spec anchor:** `docs/plans/sced-rebaseline-spec-2026-05-13.md` §6 (Weather data source — Fallback) and §11 #11 (Pre-OSF audit-phase deliverable).

**Decision:** lock **KJOT (Joliet Regional Airport, AWOS-3)** as the NOAA-archived automated airport weather station that serves as fallback for Ecowitt-gap hours during the Summer 2026 experiment window.

## Terminology clarification (spec wording vs FAA station-type)

Spec §6 uses the phrase "NOAA ASOS" as colloquial shorthand for "NOAA-archived automated airport surface weather observations." FAA's automated airport-weather programs include two variants — ASOS (Automated Surface Observing System, jointly operated by NWS, FAA, and DoD) and AWOS-3 (Automated Weather Observing System, FAA-operated). Both produce NWS-issued METARs and both flow into NCEI's Integrated Surface Database through the same archive ingest, so for the temperature and dew-point fields this audit cares about, the data-layer consumption is equivalent.

This audit verifies the program assignment for the selected (winning) station only. The Iowa Environmental Mesonet station-info page for KJOT (`https://mesonet.agron.iastate.edu/sites/site.php?station=JOT&network=IL_ASOS`) explicitly carries `IS_AWOS=1`, confirming KJOT is FAA AWOS-3. The station-info pages for the three non-winning candidates (KARR, KMDW, KORD) do NOT carry `IS_AWOS=1` in IEM's metadata; this audit does not separately verify their FAA program assignment because the decision is dominated by the proximity criterion and the candidate pool is set by the spec, not by independent program verification. The IEM "ASOS Network" mirror used to fetch the sample data deliberately includes AWOS-3 stations under the same network label. The audit does not assert sensor-model identity between ASOS and AWOS-3 programs — that would be a finer-grained claim than the 168-hour completeness data here can support.

This document refers to the four candidates collectively as "NOAA airport weather stations" to keep the spec-language continuity, and notes the program assignment per station. If the spec's "NOAA ASOS" wording is read strictly, KJOT is not eligible and the next-closest ASOS would be KMDW at 26.3 mi. Doing that would substantially degrade the proximity criterion the spec prioritizes for what amounts to a sensor-program technicality at the analysis-relevant variables, so the audit recommends accepting KJOT and tightening the spec wording rather than trading 19 miles of proximity for a label.

## Scope of this document

Phase 4 of the SCED rebaseline implementation plan asks for a single audit deliverable: pick which NOAA-archived station serves as the fallback when the Ecowitt local station has gaps. Spec §6 names four candidates: KJOT (Joliet), KARR (Aurora), KMDW (Chicago Midway), KORD (Chicago O'Hare). Spec criteria: proximity to Plainfield, IL first, hourly temperature and dew-point completeness second. **No historical baseline pull is needed**, because spec §6's matching uses within-sample standardization across the 12 experiment blocks rather than an external multi-year baseline. The pre-Phase-4 plan called for a 6-year ERA5 reanalysis pull plus z-score-parameter freeze; the H2 adversarial review collapsed that work in favour of within-sample standardization, leaving Phase 4 as a station-selection audit only.

This document does not implement the fallback logic. It records the audit decision so the spec can drop the "likely KJOT Joliet" hedge in §6, and so a future operator running the analysis pipeline knows which station identifier to point the fallback at.

## Data source

**Iowa Environmental Mesonet (IEM) ASOS Network — request_asos endpoint.** IEM provides a convenience HTTP mirror of the same METAR observation stream that NCEI archives in its Integrated Surface Database (DSI-3505 / DSI-3283 hourly). IEM is widely used in research as an easier access layer than NCEI's bulk archive, but the citable authoritative archive of record is NCEI. This audit verifies hourly completeness via IEM only — a per-row IEM-vs-NCEI value-identity spot-check is NOT performed here, so the doc claims "same observation stream, archived in parallel," not bit-identical values. IEM was chosen for endpoint friction reasons (the Phase 4 goal explicitly allows "another authoritative NOAA ASOS source if NCEI endpoint friction blocks progress"), and a future operator can re-pull from NCEI for OSF deposit if a stricter chain-of-custody is desired.

Endpoint base: `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py`

Per-station request URL (substituting `${st}` for KJOT / KARR / KMDW / KORD):

```
https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py
  ?station=${st}
  &data=tmpf&data=dwpf
  &year1=2024&month1=7&day1=15
  &year2=2024&month2=7&day2=22
  &tz=America%2FChicago
  &format=onlycomma
  &latlon=yes
  &missing=M
  &trace=T
  &direct=yes
  &report_type=3   (routine hourly METAR)
  &report_type=4   (SPECI / special observation)
```

`tmpf` is dry-bulb temperature in Fahrenheit; `dwpf` is dew-point temperature in Fahrenheit. These are the two ASOS fields the spec §6 weather vector consumes (every component of the 4-tuple — `cdd_total`, `mean_daily_max_temp_f`, `mean_nocturnal_min_temp_f`, `mean_dewpoint_f` — derives from one or the other). The `tz=America/Chicago` parameter requests timestamps in CT; `missing=M` flags absent values as the literal string `M`.

Both routine hourly METARs (`report_type=3`) and special observations (`report_type=4`) are included, so a SPECI issued mid-hour for a weather event doesn't depress an otherwise-complete hour's coverage count.

## Sample window

**2024-07-15 00:00 CT through 2024-07-21 23:59 CT (7 days, 168 hours).**

This is the exact window the goal-condition specified. It sits in mid-July of the most recent complete summer prior to the OSF filing, which is the season the experiment will run in.

**Severe-weather note:** the sample week contains the 2024-07-15 northern Illinois derecho — the NWS Chicago / Lot office documented widespread wind damage and 32 tornadoes in its county warning area on that date (https://www.weather.gov/lot/2024_07_15_Derecho). This is the opposite of a "quiet representative week." The audit explicitly keeps the window anyway because a 100% completeness result *through a derecho* is a stronger reliability signal than completeness during a quiet week: a station that keeps reporting METARs hourly while a severe-weather event passes overhead is demonstrating sensor and uplink resilience under stress. This is the most useful reliability evidence a 168-hour sample can produce — more useful than picking an undisturbed week and getting trivially-clean data.

The 7-day duration provides a single-event-period reliability check, not a multi-week or multi-year reliability characterization. Spec §6 does not require multi-year — the fallback is "fill missing hours within an arm period," not "validate the station against long-term climatology." If a future audit needs to characterize multi-year reliability (for OSF supplementary material, e.g.), the same IEM endpoint can be re-issued with a longer window.

## Method

For each candidate station, the IEM CSV response was parsed and bucketed into UTC-naive CT-local hour boundaries. An hour is counted as "covered" for `tmpf` if at least one observation within that hour carries a non-missing numeric `tmpf` value; same rule for `dwpf` independently. Completeness percentages are the number of covered hours divided by 168 (= 7 × 24).

The station coordinates reported in the IEM CSV (`lat`, `lon` columns on each row, identical across rows from the same station) were used to compute haversine distance to Plainfield, IL at approximate centroid (41.6147°N, 88.2070°W). Earth-radius constant 3,958.8 statute miles (IAU mean radius); switching to a WGS84 ellipsoid produces sub-0.1-mile differences at these distances and does not affect the ordering.

Multi-METAR hours (some stations issue 4-per-hour) inflate row counts but do not double-count the hour itself — each hour contributes at most one tick to the covered-hour count.

## Completeness results

| Station | Description | IEM `IS_AWOS` | Lat / Lon | Distance to Plainfield | tmpf hours covered | dwpf hours covered | Both fields covered |
|---|---|---|---|---|---|---|---|
| **KJOT** | Joliet Regional Airport | 1 (AWOS-3) | 41.5177 / -88.1756 | **6.9 mi** | 168 / 168 (**100.0%**) | 168 / 168 (**100.0%**) | 100.0% |
| KARR | Aurora Municipal Airport | not flagged | 41.7700 / -88.4800 | 17.7 mi | 159 / 168 (94.6%) | 159 / 168 (94.6%) | 94.6% |
| KMDW | Chicago Midway International | not flagged | 41.7860 / -87.7524 | 26.3 mi | 168 / 168 (100.0%) | 168 / 168 (100.0%) | 100.0% |
| KORD | Chicago O'Hare International | not flagged | 41.9602 / -87.9316 | 27.8 mi | 168 / 168 (100.0%) | 168 / 168 (100.0%) | 100.0% |

These percentages describe completeness *over the 168-hour sample window only*; they are not a long-term reliability claim. The fact that three of four stations cleared 100% over a derecho week supports treating the candidate pool as broadly reliable for retrospective Ecowitt-gap fill, but operators should expect occasional gaps in the actual experiment window and plan to record them via the spec §6 `pct_hours_noaa_fallback` provenance field.

The KARR shortfall is a single contiguous 9-hour outage from 2024-07-15 21:00 CT through 2024-07-16 05:00 CT inclusive — an overnight station gap, not a chronic-flakiness signal. It does not change the decision (KJOT wins on proximity regardless) but is worth noting if KARR is ever reconsidered as a secondary fallback.

## Distance estimates vs the plan

The plan's coarse distance estimates were KJOT ~10 mi, KARR ~15 mi, KMDW ~30 mi, KORD ~35 mi. Haversine-from-station-lat-lon-to-Plainfield-centroid produces KJOT 6.9 mi, KARR 17.7 mi, KMDW 26.3 mi, KORD 27.8 mi. The plan's estimates were directionally correct; the precise figures shorten KJOT slightly and bring KMDW / KORD closer together than the plan suggested. None of these revisions change the ordering on the proximity criterion.

Plainfield, IL is a village of approximately 25 square miles (Village of Plainfield published figures). The audit reference point is the village geographic centroid — the precise residence location is not used. The decision is not sensitive to within-village reference-point choice because the nearest-candidate distance (KJOT at 6.9 mi) is large relative to Plainfield's diameter, and the next-nearest candidate (KARR at 17.7 mi) is more than twice as far again.

## Decision and rationale

**Selected fallback station: KJOT (Joliet Regional Airport).**

Rationale, in spec §6's stated priority order:

1. **Proximity (primary criterion).** KJOT at 6.9 mi is more than twice as close to Plainfield as the next nearest candidate (KARR at 17.7 mi) and roughly four times closer than the Chicago-area stations. For an Ecowitt-gap fallback whose purpose is to fill a small number of missing hours per arm period with the most microclimate-faithful available substitute, the closest station is the right pick.
2. **Completeness (secondary criterion).** KJOT delivers 100% / 100% completeness for both temperature and dew point over the 168-hour sample window. Three of the four candidates tie at 100% / 100%; KARR alone has a 9-hour outage. Since KJOT also wins on proximity, the completeness criterion never has to break a tie.
3. **No historical baseline pull.** Spec §6's within-sample standardization computes z-score means and standard deviations from the 12 experiment blocks themselves, not from any external historical baseline. Phase 4 therefore does not need an ERA5 reanalysis pull, a multi-year ASOS download, or a z-score-parameter JSON freeze.

The spec wording in §6 said "likely KJOT Joliet" prior to this audit. The findings here remove the hedge: KJOT is the lock.

## Operational notes

- **Fallback semantics (per spec §6):** the KJOT fallback feed is only consulted for hours where Ecowitt has no observation. It does not invalidate the arm period and it does not silently substitute when Ecowitt has data. The pipeline reports `pct_hours_ecowitt`, `pct_hours_noaa_fallback`, and the fallback-source station identity per arm.
- **Pull cadence:** the analysis pipeline does not need to hit IEM or NCEI in real time. Post-arm-period retrospective pulls are sufficient (analysis runs after each arm period closes per spec §8 latency notes). Operators can pull the full experiment window in one batch at analysis time rather than running a daily poller.
- **Station identifier on the wire:** the IEM API uses the three-letter station code (`JOT` for KJOT) in the CSV response's `station` column. Wherever the pipeline tags fallback rows with a station ID, the canonical value is `KJOT` (the four-letter ICAO identifier). Both `JOT` and `KJOT` refer to Joliet Regional Airport; the analysis layer should normalize.

## Out of scope for this PR

This document does not implement the fallback merge logic, does not pull any historical baseline, does not touch the analysis pipeline code, and does not change the controller, deploy stack, or any test fixtures. The single artifact is this findings file plus a one-line spec §6 update (the "likely" hedge removed) if the spec amendment lands in the same PR.

The fallback-merge wiring itself is part of Phase 6 (shadow validation) — the shadow run is when the Ecowitt-NOAA merge gets exercised end-to-end with real data.

## Reproducibility

The four IEM URLs above can be re-issued at any time; the IEM endpoint serves historical ASOS data going back decades. The completeness numbers above are recomputable in under one minute via:

The script was last validated against the IEM endpoint on 2026-05-18; the response shape at that time was a header row `station,valid,lon,lat,tmpf,dwpf` followed by data rows, with no `#`-comment preamble. The script strips any line starting with `#` defensively in case IEM's response format changes.

The mid-July 2024 sample window is safely outside any DST transition, so naive datetime comparison against the IEM `tz=America/Chicago` response is fine for this run. If a future operator reuses this script for a window that crosses 2024-11-03 02:00 CT (or 2026-11-01 02:00 CT during the actual experiment), they should switch to `zoneinfo`-aware datetimes to handle the fall-back fold correctly. See `project-phase3-dst-fold-keying` in the memory index for the trap.

```python
import csv, datetime, urllib.request

WIN_START = datetime.datetime(2024, 7, 15, 0, 0)
WIN_END   = datetime.datetime(2024, 7, 22, 0, 0)
SAMPLE_HOURS = 168

for st in ("KJOT", "KARR", "KMDW", "KORD"):
    url = (
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
        f"?station={st}&data=tmpf&data=dwpf"
        "&year1=2024&month1=7&day1=15"
        "&year2=2024&month2=7&day2=22"
        "&tz=America%2FChicago&format=onlycomma&latlon=yes"
        "&missing=M&trace=T&direct=yes&report_type=3&report_type=4"
    )
    raw = urllib.request.urlopen(url).read().decode("utf-8").splitlines()
    # Strip any #-comment preamble defensively (IEM doesn't emit one
    # for format=onlycomma at the time of writing, but the script
    # tolerates it if a future endpoint revision adds one).
    text = [ln for ln in raw if not ln.startswith("#")]
    reader = csv.DictReader(text)
    tmpf_hours, dwpf_hours = set(), set()
    for row in reader:
        t = datetime.datetime.strptime(row["valid"], "%Y-%m-%d %H:%M")
        if not (WIN_START <= t < WIN_END):
            continue
        hkey = t.replace(minute=0, second=0)
        if row.get("tmpf", "").strip() not in ("", "M"):
            tmpf_hours.add(hkey)
        if row.get("dwpf", "").strip() not in ("", "M"):
            dwpf_hours.add(hkey)
    print(f"{st}: tmpf {len(tmpf_hours)}/{SAMPLE_HOURS}  "
          f"dwpf {len(dwpf_hours)}/{SAMPLE_HOURS}")
```

Expected output matches the completeness table above.
