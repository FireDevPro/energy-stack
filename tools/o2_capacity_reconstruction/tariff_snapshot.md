# ComEd tariff snapshot — O2 Layer 2 portfolio constants source

This file records the specific filings the [O2 Layer 2](../../docs/EXPERIMENT_DESIGN.md#o2-capacity-charge-avoidance-three-layer-measurement) stipulated constants come from.

**STATUS: locked at OSF filing prep, 2026-05-11.** Three of four values from primary published sources; `portfolio_sum_mw` is the only stipulation (explicit per spec).

## What we need from the tariff

For each summer year Y of the experimental period, the second branch of the Att. M-2 §2 formula uses:

```
CPLC_(Y+1) = ACustCPL_Y
           + (ComEdNPL_Y − AComEdCPL_Y)
             × (ACustPL_Y − ACustCPL_Y)
             / Σ_5Pc(ACustPL − ACustCPL)
```

`ACustCPL_Y` and `ACustPL_Y` come from the household's own revenue meter (observable, not stipulated). Four other values are stipulated from filed sources:

| Symbol | What | Source | Locked |
|---|---|---|---|
| `$/kW-month` | Residential capacity rate (MCC) | ComEd ICC Schedule of Rates, Informational Sheet 4 | ✅ primary |
| `ComEdNPL_Y` | ComEd zonal weather-normalized peak (MW) | PJM weather-normalized-peaks.xlsx | ✅ primary |
| `AComEdCPL_Y` | ComEd avg load at the 5 PJM Five Peaks (MW) | Derived from PJM annual 5CP PDF (already ingested into `pjm.coincident_peak` Influx measurement by [scripts/scrape_pjm_5cp_pdf.py](../../deploy/energy-stack/scripts/scrape_pjm_5cp_pdf.py)) | ✅ derived from primary |
| `Σ_5Pc(ACustPL − ACustCPL)` | Portfolio sum across branch-2 customers (MW) | NOT separately published — computed inside ComEd's bill-allocation engine | ⚠️ stipulated |

## Primary citations

### 1. Capacity rate ($/kW-month)

| Field | Value |
|---|---|
| **Locked value (Jun 2026 – May 2027)** | **$10.13567/kW-month** |
| Tariff document | **ILL. C. C. No. 10, 71st Revised Informational Sheet No. 4** — "Capacity Charge — Supplement to Rate BESH and Rider PPO" |
| Filing date | 2026-04-24 (per [`2026 Index of Filings`](data/) page 4) |
| Effective date | **2026-04-25** |
| Issued by | David R. Perez, EVP and COO, Commonwealth Edison Company |
| Filing description | "Spring Procurement Charges and Factors Determined by Formulae" |
| Local archival | [`data/comed_icc_info_sheet_4_capacity_charge.pdf`](data/comed_icc_info_sheet_4_capacity_charge.pdf) (128 KB) |
| Notes from sheet | MCC includes System Average Supply Base Uncollectible Cost Factor (SBUFsys, Info Sheet 21) and System Average Incremental Supply Uncollectible Cost Factor (ISUFsys, Info Sheet 20). |

For provenance: same sheet shows **$8.32925/kW-month** for January–May 2026 (prior MCC). The roughly 22% jump reflects the 2025-26 PJM Base Residual Auction clearing roughly 10× the prior year's residential capacity rate (per EXPERIMENT_DESIGN.md §2 O2 framing).

### 2. ComEdNPL_Y (zonal weather-normalized peak, MW)

| Field | Value |
|---|---|
| **2024 SUMMER** | **20,699 MW** |
| **2025 SUMMER** | **20,736 MW** |
| Source | [PJM Weather-Normalized Peaks](https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/weather-normalized-peaks.xlsx) |
| Publisher | PJM Resource Adequacy Planning Department |
| Format | XLSX with `ZONE, Delivery Year, Season, WN Peak (MW)` columns |
| Local archival | [`data/pjm_weather_normalized_peaks.xlsx`](data/pjm_weather_normalized_peaks.xlsx) (27 KB) |
| Retrieved | 2026-05-11 |
| Provenance | ComEd zone 2014-2025 SUMMER values range 20,699–22,088 MW; tight clustering around 20,700-21,000 MW since 2020. |

The 2026 PJM Load Forecast Report ([`data/pjm_2026_load_forecast_report.pdf`](data/pjm_2026_load_forecast_report.pdf), published January 2026) contains zonal forecast dashboards but the numeric tables are accompanied by a separate Excel companion; this xlsx is that companion's WN-peaks tab for historical published values.

### 3. AComEdCPL_Y (avg ComEd load at the 5 PJM RTO peaks, MW)

| Field | Value |
|---|---|
| **2021** | 18,491.50 MW |
| **2022** | 17,371.96 MW |
| **2023** | 17,723.74 MW |
| **2024** | 17,767.66 MW |
| **2025** | 19,138.22 MW |
| Source | Mean of `pjm.coincident_peak.comed_zone_load_mw` over the 5 ranks per summer year |
| Underlying primary | PJM annual 5CP PDF, e.g. [summer-2024-peaks-and-5cps.pdf](https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/summer-2024-peaks-and-5cps.pdf) |
| Ingestion code | [`scripts/scrape_pjm_5cp_pdf.py`](../../deploy/energy-stack/scripts/scrape_pjm_5cp_pdf.py) — already in production |
| Stored in Influx | `pjm.coincident_peak` measurement, tags: `summer_year` + `peak_rank`, field: `comed_zone_load_mw` |
| Computed in this lock by | Direct Flux query against pi-lab Influx (lines preserved in commit message) |

This is the only one of the four that we DERIVE rather than read directly from a published table — but the derivation is a trivial 5-value mean and the underlying primary data is the same PJM 5CP PDF that the production pipeline already parses.

### 4. Σ_5Pc(ACustPL − ACustCPL) — portfolio sum (MW) ⚠️ STIPULATED

| Field | Value |
|---|---|
| **2026** | **1,500 MW** (stipulated) |
| **2027** | 1,500 MW (stipulated) |
| Sensitivity reported | ±20% (widened from spec's ±10% to reflect honest uncertainty) |
| Why stipulated | The portfolio sum is computed INSIDE ComEd's bill-allocation engine across the subset of customers in Att. M-2 branch 2. PJM does not publish customer-class portfolio aggregates as a public scalar. |
| Magnitude anchor | (ComEdNPL − AComEdCPL) = 1,598 MW for 2025, 2,931 MW for 2024 — system-wide zonal-vs-RTO peak gap. The portfolio sum is restricted to branch-2 customers (those whose ComEd-zone peak load exceeds their PJM-RTO peak load) and is therefore smaller than the system gap. 1,500 MW is a defensible point estimate; ±20% gives the 1,200–1,800 MW band. |
| Reference document | [`data/pjm_oatt_attM2_comed.pdf`](data/pjm_oatt_attM2_comed.pdf) — PJM OATT Attachment M-2 (ComEd) — Determination of Capacity Peak Load Contributions |
| Reference URL | https://www.pjm.com/pjmfiles/directory/etariff/MasterTariffs/23TariffSections/18111.pdf |
| Future precision | A FERC eLibrary or PJM annual compliance filing may publish a customer-class breakdown that allows tighter estimation. If found, replace this stipulation and tighten the sensitivity band. Until then, this is the single explicit stipulation in the O2 Layer 2 reconstruction, as the locked spec already anticipates. |

The system-aggregate proxy is the **ComEd zonal NSPL = 21,559.6 MW** at the 2024-08-27 18:00 EPT ComEd zonal peak (from [`data/pjm_nspl_2025.pdf`](data/pjm_nspl_2025.pdf)), but that's a single-peak figure for the transmission charge (NSPL), not the 5-peak capacity-charge denominator. Included here as provenance, not as the value used.

## Reading the Att. M-2 second branch in context

For Chris's household (Single-Family Non-Electric Heat on Rate BESH):

```
Layer 1 (primary, observable):
    Δ$_capacity = (ACustCPL_arm_B − ACustCPL_arm_A)_2026 × $10.13567/kW-mo × 5 months

Layer 2 (descriptive, stipulated):
    CPLC_2027_arm = ACustCPL_arm + (20,736 − 19,138) × (ACustPL_arm − ACustCPL_arm) / 1,500
                  = ACustCPL_arm + 1,598 × (ACustPL_arm − ACustCPL_arm) / 1,500
                  ≈ ACustCPL_arm + 1.065 × max(ACustPL_arm − ACustCPL_arm, 0)
    Δ$_layer_2 = (CPLC_2027_arm_B − CPLC_2027_arm_A) × $10.13567/kW-mo × 5 months
    Sensitivity: re-run with portfolio_sum ∈ [1,200, 1,800] MW
```

Layer 2's branch-2 adjustment factor (`(ComEdNPL − AComEdCPL) / portfolio_sum`) lands near 1.0 — i.e., the redistribution roughly doubles the household's individual gap. If empirical ACustPL > ACustCPL for the household by 1 kW, Layer 2 adds ~1 kW to CPLC.

For most residential profiles ACustCPL ≥ ACustPL (branch 1) and Layer 2 collapses to Layer 1. Branch 2 activates when the household's load peaks more with ComEd-zone-only afternoons than with PJM-RTO-wide peaks. AC-driven residential loads in the ComEd zone can sit in either branch depending on whether the household's coincidence with RTO-wide peaks (broader and later) matches or misses.

## Local PDFs / data files archived alongside this snapshot

| File | Size | Source URL |
|---|---|---|
| `data/comed_icc_info_sheet_4_capacity_charge.pdf` | 128 KB | ComEd Schedule of Rates filing 2026-04-24 |
| `data/pjm_weather_normalized_peaks.xlsx` | 27 KB | PJM Resource Adequacy Planning |
| `data/pjm_2026_load_forecast_report.pdf` | 8.5 MB | PJM Resource Adequacy Planning, January 2026 |
| `data/pjm_nspl_2025.pdf` | 85 KB | PJM Markets-Ops Settlements |
| `data/pjm_oatt_attM2_comed.pdf` | 185 KB | PJM OATT eTariff |
