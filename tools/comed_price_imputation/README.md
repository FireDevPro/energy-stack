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
- [`fetch_rtp.py`](fetch_rtp.py) — fetches ComEd RTP 5-minute history from the public Hourly Pricing endpoint
- [`fetch_lmp.py`](fetch_lmp.py) — fetches day-ahead LMP for COMED from PJM DM2 (`da_hrl_lmps?pnode_id=33092371`)
- [`spread_constants.json`](spread_constants.json) — the OUTPUT of `compute_spread.py`, used by the pipeline at analysis time
- [`data/`](data/) — frozen input data: monthly ComEd RTP files + PJM day-ahead LMP files for the contributing summers

## Lock status (as of OSF filing prep)

The shipped `spread_constants.json` is the **real locked value**,
computed from 2024-2025 Jun-Sep data (5,840 hour-pairs):

| Month | Median (RTP − LMP) ¢/kWh |
|---|---|
| June | +0.0089 |
| July | −0.0518 |
| August | −0.0260 |
| September | +0.0018 |

### What this constant is — and is not

This is the **fallback imputation spread** used by §4 Rule 3 when an
hourly RTP average is missing (fewer than 6 of 12 5-minute prints
captured). Its only job is to convert a known DA LMP into a plausible
RTP fill value:

```
imputed_rtp_cents = lmp_cents + median_spread_for_calendar_month
```

It is NOT a claim that RTP and DA LMP are functionally equivalent.
RTP diverges sharply from DA LMP during real-time scarcity (the
locked sample includes hours where hourly-average RTP cleared above
100 ¢/kWh while the matching DA LMP was a tiny fraction of that).
The MEDIAN spread is small because most hours are quiet hours where
the two prices track the same balancing-area clear; the tail
divergence is real but doesn't move the median estimator.

If a missing RTP hour falls during a scarcity event the imputation
under-estimates RTP. This is acceptable because:

1. Per Rule 3, weeks with >20% imputed hours are excluded from
   formal analysis; the imputation is for sparse single-hour gaps,
   not for systematic outages during scarcity windows.
2. A sustained outage during a real RTP spike would push the week
   over the imputation cap and exclude it; the bias is bounded by
   the cap.
3. The locked downstream Arm B trigger values (10 ¢ / 20 ¢) are not
   sensitive to a ≤0.05 ¢/kWh imputation bias in fill-in hours.

### Why 2024-2025 only (not 2023-2025 as the original spec text said)

PJM DM2's `da_hrl_lmps` standard tier covers the last 731 days
(per the `/metadata` endpoint and confirmed by
[`scripts/backfill_pjm.py`](../../deploy/energy-stack/scripts/backfill_pjm.py)).
Today's window therefore reaches back to mid-May 2024. Summer 2024
(Jun 1 onward) is fully reachable; summer 2023 sits entirely in
PJM's archive tier, which requires a different query (`type=Zone`,
no `pnode_id` filter, client-side parse). Archive support is
deferred — it is not required for OSF lock since the locked spread
is a Δ¢ correction of order 0.05 ¢/kWh, well below the smallest
spike-threshold granularity in the pre-registered analysis.

EXPERIMENT_DESIGN.md §4 Rule 3 has been updated to reflect this
data-availability constraint.

## Reproducing the lock

```sh
cd tools/comed_price_imputation

# 1. Fetch ComEd RTP (public Hourly Pricing endpoint; no auth):
python fetch_rtp.py --years 2024,2025
# (already fetched 2023 too; that file ships in data/ for archival
#  even though it doesn't contribute to the current spread)

# 2. Fetch PJM DA-LMP (requires PJM_DM2_API_KEY in env; standard
#    tier only — 2023 in archive is not supported yet):
PJM_DM2_API_KEY=<your-key> python fetch_lmp.py --years 2024,2025

# 3. Recompute and overwrite the locked JSON:
python compute_spread.py
git diff spread_constants.json
```

[`tools/analysis/check_constants_locked.py`](../analysis/check_constants_locked.py) verifies the JSON no longer carries the placeholder sentinel before blessing an OSF commit.

## Method

For each calendar month in summer (June, July, August, September):

1. Aggregate ComEd RTP 5-minute prints to hourly averages (matching
   the production `comed.prices period_type=hourly_avg`; ≥6 of 12
   prints required per §4 Rule 3).
2. Pull PJM day-ahead LMP at the COMED zone aggregator
   (`pnode_id=33092371`) for the same hour-beginning timestamps.
3. Compute `spread = RTP − LMP` hour by hour, in matched units
   (cents/kWh after the $/MWh → ¢/kWh conversion).
4. Take the median spread across all hours of that calendar month
   pooled across the contributing summers (currently 2024-2025).

The median (not the mean) is used because RTP has a heavy upper tail
(scarcity hours where hourly average exceeds 100 ¢/kWh) that would
pull a mean-based estimator far from typical conditions. The
imputation is filling in for missed single-hour quiet-hour prints,
not modelling scarcity; median is the right estimator for that.

## Output schema

`spread_constants.json`:

```json
{
  "PLACEHOLDER": false,
  "computed_at": "<ISO 8601 UTC>",
  "input_years": [<years that actually contributed>],
  "requested_years": [<years the run tried, including any skipped>],
  "input_hours_total": <int>,
  "median_spread_cents_per_kwh_by_month": {
    "6": <float>,
    "7": <float>,
    "8": <float>,
    "9": <float>
  }
}
```

`PLACEHOLDER: true` is the sentinel that prevents OSF lock; the
file shipped on `main` has `PLACEHOLDER: false` (see [PR #71](https://github.com/Promithius-DR/energy-stack/pull/71)).
