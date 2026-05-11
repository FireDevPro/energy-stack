# ComEd tariff snapshot — O2 Layer 2 portfolio constant source

This file records the specific ComEd tariff filing the Layer 2
stipulated portfolio constants were drawn from.

**STATUS:** placeholder. Verify and update before OSF filing.

## What we need from the tariff

For each summer year Y of the experimental period:

| Symbol | Definition | Approximate magnitude | Where it lives in the filing |
|---|---|---|---|
| `ComEdNPL_Y` | ComEd's weather-normalized peak load | ~22,000-23,000 MW | PJM Manual 19, summer peak forecast; ComEd publishes their value in their annual PJM zonal filing |
| `AComEdCPL_Y` | ComEd's average coincident peak at the PJM five peaks | ~18,000-19,000 MW | PJM's annual 5CP filing (the same PDF [`scripts/scrape_pjm_5cp_pdf.py`](../../deploy/energy-stack/scripts/scrape_pjm_5cp_pdf.py) reads), zonal subtable |
| `Σ_5Pc(ACustPL − ACustCPL)` | Sum across all customers in branch 2 of (ComEd-peak avg − PJM-peak avg) | A few thousand MW; depends on customer distribution | ComEd files this implicitly as part of their cost-allocation in Att. M-2 inputs at FERC; the explicit value is in the ICC docket-page reference below |
| capacity rate $/kW-month | Residential-rate $ multiplier on the kW value | Roughly $0.50-5/kW-month depending on year | ComEd Rider PE (Purchased Electricity) tariff sheets |

## Reference filing (TO BE FILLED IN BEFORE OSF LOCK)

| Field | Value |
|---|---|
| Tariff name | _e.g., "ComEd Rider PE — Purchased Electricity, ILL.C.C. No. 10"_ |
| ICC Docket | _e.g., XX-XXXX_ |
| Effective date | _YYYY-MM-DD_ |
| Page / section | _e.g., "Sheet 200, §3.b.iii"_ |
| Filed-with-FERC link | _https://elibrary.ferc.gov/eLibrary/docket/..._ |
| Best public mirror | _https://..._ |
| Retrieved | _YYYY-MM-DD (the date the snapshot was taken)_ |

## Acquisition procedure

1. Visit [ICC e-Docket](https://www.icc.illinois.gov/) and search for the most recent ComEd Rider PE filing.
2. Locate the Att. M-2-aligned residential capacity-charge schedule for the relevant program year.
3. Record `ComEdNPL_Y`, `AComEdCPL_Y`, the per-customer-class peak-load filings that build the portfolio sum, and the per-kW $ rate.
4. Update `tariff_constants.json` with the verified values.
5. Update this file with the citation.

## Why the values are "stipulated" not "observed"

A single household cannot observe ComEd-wide aggregates. PJM
publishes RTO-wide aggregates (which is why Layer 1 is observable)
but ComEd's zonal portfolio numbers are filed by ComEd, not by the
household. The values are taken at face value from the most recent
filed tariff and reported alongside Layer 1; Layer 2 explicitly
labels them as stipulated.

The sensitivity band (±10% on the portfolio sum, per
[EXPERIMENT_DESIGN.md §6](../../docs/EXPERIMENT_DESIGN.md#o2-capacity-charge-avoidance-three-layer-measurement)) covers
filing-to-filing variation and is reported as a side-table.
