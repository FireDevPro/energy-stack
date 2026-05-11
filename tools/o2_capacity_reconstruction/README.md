# O2 capacity-charge reconstruction (Layer 2)

Holds the stipulated portfolio constant and the Att. M-2 branch-2
reconstruction code for [O2 Layer 2](../../docs/EXPERIMENT_DESIGN.md#o2-capacity-charge-avoidance-three-layer-measurement).

## What this is

The locked O2 outcome reports three layers (per
[`EXPERIMENT_DESIGN.md §6`](../../docs/EXPERIMENT_DESIGN.md#o2-capacity-charge-avoidance-three-layer-measurement)):

- **Layer 1** (primary, fully observable): `ACustCPL` difference at
  the five PJM Five Peak hours. No stipulation. Computed by the
  pipeline directly from meter data.
- **Layer 2** (descriptive, one stipulated input): full `CPLC_(Y+1)`
  reconstruction using both Att. M-2 branches, including the
  portfolio sum `Σ_5Pc(ACustPL − ACustCPL)` that spans all customers
  in branch 2 and is not observable from one household. THIS
  DIRECTORY supplies that stipulated constant.
- **Layer 3** (descriptive, post-Y+1): actual ComEd Capacity Charge
  line item. No stipulation. Read directly from bill PDFs.

The Att. M-2 §2 second-branch formula:

> `CPLC_(Y+1) = ACustCPL_Y + (ComEdNPL_Y − AComEdCPL_Y) × (ACustPL_Y − ACustCPL_Y) / Σ_5Pc(ACustPL − ACustCPL)`

In words: when the household's ComEd-zone-peak average load exceeds
its PJM-peak average load (`ACustPL > ACustCPL`), the next-year
capacity charge picks up a proportional share of the zone-vs-RTO
gap that ComEd files at FERC.

Three of the four right-hand-side terms (`ACustCPL_Y`, `ACustPL_Y`,
and the branch-1 case via `ACustCPL_Y` alone) are observable from
this household's meter. The two ComEd-portfolio terms are NOT:

- `ComEdNPL_Y` — ComEd's weather-normalized peak load (MW).
- `AComEdCPL_Y` — ComEd's average coincident peak at the PJM five
  peaks (MW).
- `Σ_5Pc(ACustPL − ACustCPL)` — portfolio sum across all customers
  in the second branch (MW).

## What this directory provides

- [`tariff_constants.json`](tariff_constants.json) — locked stipulated
  values for the three portfolio terms, sourced from ComEd's
  published Schedule of Rates.
- [`tariff_snapshot.md`](tariff_snapshot.md) — citation of the
  specific tariff revision the constants are pulled from, with the
  ICC docket number, effective date, and exact page reference.
- [`reconstruct.py`](reconstruct.py) — the Att. M-2 reconstruction
  function. Pure: takes household `ACustCPL`, `ACustPL`, and the
  stipulated constants; returns `CPLC_(Y+1)` in $.

## Pre-OSF lock procedure

The shipped `tariff_constants.json` is a **placeholder**. Before OSF
filing:

1. Open the most recent ComEd Schedule of Rates Rider PE — Purchased
   Electricity (or whichever Schedule contains the Att. M-2 portfolio
   inputs at the time of filing). The reference snapshot in
   [`tariff_snapshot.md`](tariff_snapshot.md) shows where these values
   appear; the user must verify the snapshot still matches the live
   tariff.
2. Update `tariff_constants.json` with the verified values; set
   `PLACEHOLDER: false` and fill `tariff_source.effective_date`.
3. Update `tariff_snapshot.md` with the verified citation if the
   tariff has been re-filed since the snapshot was taken.
4. Commit. [`tools/analysis/check_constants_locked.py`](../analysis/check_constants_locked.py) verifies this file no longer carries the placeholder sentinel.

## Output schema

`tariff_constants.json`:

```json
{
  "PLACEHOLDER": <bool>,
  "tariff_source": {
    "title": "<schedule name>",
    "icc_docket": "<docket number>",
    "effective_date": "<YYYY-MM-DD>",
    "page": "<section reference>",
    "url": "<best public link to the filed tariff>"
  },
  "ComEdNPL_mw_by_year": {
    "<year>": <float MW>
  },
  "AComEdCPL_mw_by_year": {
    "<year>": <float MW>
  },
  "portfolio_sum_mw_by_year": {
    "<year>": <float MW>
  },
  "capacity_rate_dollars_per_kw_by_year": {
    "<year>": <float $/kW-month>
  }
}
```

Per-year keys because the portfolio constants are re-filed annually.

## Reconstruction sensitivity

Layer 2 is reported with ±10% sensitivity on the portfolio sum
(per [`EXPERIMENT_DESIGN.md §6`](../../docs/EXPERIMENT_DESIGN.md#o2-capacity-charge-avoidance-three-layer-measurement)).
`reconstruct.py` exposes a `sensitivity_band(...)` helper that
returns the band low/high alongside the point estimate.
