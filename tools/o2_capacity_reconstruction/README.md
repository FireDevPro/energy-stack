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

## Lock status

`tariff_constants.json` is locked (`PLACEHOLDER: false`, `locked_at: 2026-05-11`). All four tariff inputs come from the citations in [`tariff_snapshot.md`](tariff_snapshot.md):

- **ComEdNPL_Y** — PJM Weather-Normalized Peaks XLSX (per year)
- **AComEdCPL_Y** — derived from `pjm.coincident_peak` Influx measurement (per year)
- **Capacity rate** — ComEd ICC Schedule of Rates Informational Sheet 4 ($/kW-month, per year)
- **portfolio_sum_mw** — three pre-registered named scenarios (see §"Reconstruction scenarios" below); the `anchor_2021` value is taken from the FERC ER22-1520-001 deficiency response

[`tools/analysis/check_constants_locked.py`](../analysis/check_constants_locked.py) is the pre-OSF-tag gate; it currently passes.

If ComEd re-files Informational Sheet 4 between now and OSF tag, refresh `capacity_rate_dollars_per_kw_month_by_year` and bump `tariff_source.icc_capacity_rate.effective_date`. Similarly if PJM publishes a new WN Peaks XLSX or the next summer's 5CP PDF lands, refresh those year entries.

## Output schema

`tariff_constants.json`:

```json
{
  "PLACEHOLDER": <bool>,
  "locked_at": "<YYYY-MM-DD>",
  "tariff_source": {
    "icc_capacity_rate": { ... },
    "pjm_wn_peaks": { ... },
    "pjm_5cp_zonal": { ... },
    "pjm_oatt_att_m2_comed": { ... },
    "ferc_er22_1520_001_deficiency_response": { ... }
  },
  "ComEdNPL_mw_by_year":            {"<year>": <MW>},
  "AComEdCPL_mw_by_year":           {"<year>": <MW>},
  "portfolio_sum_mw_scenarios":     {"low": 1500.0, "anchor_2021": 2033.653, "high": 3000.0},
  "capacity_rate_dollars_per_kw_month_by_year": {"<year>": <float $/kW-month>}
}
```

ComEdNPL, AComEdCPL, and the capacity rate are keyed by year because ComEd and PJM re-file them annually. `portfolio_sum_mw_scenarios` is not year-keyed: the scenarios are pre-registered values used across all study years, since the current-year denominator is not publicly disclosed and re-keying by year would imply a precision the data does not support.

## Reconstruction scenarios

Layer 2 is descriptive only and is reported across three pre-registered named denominators rather than a confidence band (per [`EXPERIMENT_DESIGN.md §6`](../../docs/EXPERIMENT_DESIGN.md#o2-capacity-charge-avoidance-three-layer-measurement)):

| Scenario | portfolio_sum_mw | Source |
|---|---|---|
| `low` | 1,500 MW | prior planning case |
| `anchor_2021` | 2,033.653 MW | FERC ER22-1520-001 disclosed Summer 2021 Weather Sensitive denominator |
| `high` | 3,000 MW | wide upper sensitivity near historical system-gap scale |

`reconstruct.py` exposes a `scenarios(a_cust_cpl_kw, a_cust_pl_kw, constants)` helper that returns a dict of CPLC(kW) keyed by scenario name. When branch 1 applies (`ACustCPL >= ACustPL`), the portfolio denominator is unused and all three scenarios collapse to the same value. The locked scenario set is exposed as `PORTFOLIO_SUM_SCENARIOS_MW` for tests and external consumers.

These are pre-registered scenario analyses, not confidence intervals. See [`tariff_snapshot.md`](tariff_snapshot.md) §4 for the FERC exhibit citations, verification math, and rationale for the scenario choice over a ±pct band.
