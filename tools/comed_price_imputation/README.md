# ComEd RTP price imputation

Computation that fills missing-RTP hours during analysis per
[`EXPERIMENT_DESIGN.md §4 Rule 3`](../../docs/EXPERIMENT_DESIGN.md#data-quality-rules-and-missing-data-handling).

## What this is

The analysis pipeline ([`docs/ANALYSIS_PIPELINE.md`](../../docs/ANALYSIS_PIPELINE.md) Stage 2 Rule 3) requires a way to fill RTP hours where fewer than 6 of 12 5-minute prints were captured. The locked method:

> day-ahead PJM LMP at COMED, adjusted by the historical month-matched
> median (RTP − day-ahead LMP) spread computed once at OSF lock from
> public PJM data over summers 2023-2025

This directory holds:
- [`compute_spread.py`](compute_spread.py) — the spread computation script (binding)
- [`fetch_lmp.py`](fetch_lmp.py) — fetches day-ahead LMP for COMED from PJM DM2 (`da_hrl_lmps?pnode_id=33092371` or the zone-aggregated equivalent)
- [`spread_constants.json`](spread_constants.json) — the OUTPUT of `compute_spread.py`, used by the pipeline at analysis time
- [`data/`](data/) — frozen input data: monthly ComEd RTP files + PJM day-ahead LMP files for summers 2023-2025

## Pre-OSF lock procedure

The shipped `spread_constants.json` is a **placeholder** that lets the
pipeline run end-to-end in CI but is not the final locked value. The
following must run once before OSF filing:

```sh
cd tools/comed_price_imputation
python fetch_lmp.py --years 2023,2024,2025
# (places PJM day-ahead LMP files in data/lmp_<year>.csv)

python compute_spread.py
# (overwrites spread_constants.json with the real per-month medians)

git diff spread_constants.json
# Verify the values look plausible (typically RTP > LMP in summer
# peak hours, RTP < LMP in shoulder hours).

git add spread_constants.json data/lmp_*.csv
git commit -m "lock: comed_price_imputation spread constants from 2023-2025 data"
```

Once locked, the values do not change for the duration of the
pre-registered study. [`tools/analysis/check_constants_locked.py`](../analysis/check_constants_locked.py) verifies this file does not contain placeholder sentinel values before blessing an OSF commit.

## Method

For each calendar month in summer (June, July, August, September):

1. Aggregate ComEd RTP 5-minute prints to hourly averages (matching
   the production `comed.prices period_type=hourly_avg`).
2. Pull PJM day-ahead LMP at the COMED zone aggregator for the same
   hour-beginning timestamps.
3. Compute `spread = RTP − LMP` hour by hour.
4. Take the median spread across all hours of that calendar month
   across the 2023-2025 sample.

The median (not the mean) is used because RTP has a heavy upper tail
(scarcity hours of 50-100+ ¢/kWh) that would bias a mean-based
estimator. The median represents typical conditions, which is what
the imputation is filling in for.

When the pipeline fills a missing RTP hour, it computes:
`imputed_rtp_cents = lmp_cents_at_that_hour + median_spread_for_that_calendar_month`.

## Output schema

`spread_constants.json`:

```json
{
  "computed_at": "<ISO 8601 UTC>",
  "input_years": [2023, 2024, 2025],
  "input_hours_total": <int>,
  "median_spread_cents_per_kwh_by_month": {
    "6": <float>,
    "7": <float>,
    "8": <float>,
    "9": <float>
  },
  "p95_lookup_for_qa": {
    "6": <float>,
    "7": <float>,
    "8": <float>,
    "9": <float>
  },
  "PLACEHOLDER": false
}
```

`PLACEHOLDER: true` is the sentinel that prevents OSF lock; the shipped file in this PR has `PLACEHOLDER: true`.
