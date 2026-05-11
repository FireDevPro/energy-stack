# ComEd tariff snapshot — O2 Layer 2 portfolio constants source

This file records the specific filings the [O2 Layer 2](../../docs/EXPERIMENT_DESIGN.md#o2-capacity-charge-avoidance-three-layer-measurement) stipulated constants come from.

**STATUS: locked at OSF filing prep, 2026-05-11.** ComEdNPL, AComEdCPL, and the capacity rate come from primary published sources. The Summer 2021 `portfolio_sum_mw` for Weather Sensitive Customers is disclosed in the FERC ER22-1520-001 deficiency response; current-year values are not separately published, so Layer 2 is reported across three pre-registered named denominator scenarios (see §4 and EXPERIMENT_DESIGN.md §6).

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
| `Σ_5Pc(ACustPL − ACustCPL)` | Portfolio sum across positive-gap customer population (MW) | Summer 2021 disclosed in FERC ER22-1520-001 deficiency response (Exhibits 1(b)/2(b)(i)/2(b)(ii)); current-year not published | ⚠️ pre-registered scenarios (see §4) |

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

### 4. Σ_5Pc(ACustPL − ACustCPL) — portfolio sum (MW) ⚠️ pre-registered scenarios

#### Primary citation: FERC ER22-1520-001 deficiency response

The Summer 2021 value for the Weather Sensitive Customer class (the relevant class for residential AC-driven load on Rate BESH) is disclosed in the FERC ER22-1520-001 deficiency response, **Exhibits 1(b), 2(b)(i), and 2(b)(ii)**, archived at [`data/ferc_er22_1520_filing_letter.pdf`](data/ferc_er22_1520_filing_letter.pdf):

| Quantity | Value |
|---|---|
| **Difference of the Average ComEd and Average PJM 5 CPs for Weather Sensitive Customers** | **2,033,653 kW = 2,033.653 MW** |
| Summer 2021 Weather Normalized PJM Coincident Peak | 20,290 MW |
| Summer 2021 ComEd Zone PJM Coincident 5 CPs | 18,491 MW |
| Allocation to Weather Sensitive Load | 1,798.5104 MW |

**Math verification.** Per-customer adjustment factor = 1,798.5104 / 2,033.653 = 0.884374. For the sample customer in Exhibit 2(b)(ii) with a 0.27012 kW gap, the resulting Customer Weather Sensitivity Adjustment is 0.27012 × 0.884374 = 0.2389 kW, which matches the exhibit's stated value. The 2,033.653 MW is the verified denominator for Summer 2021.

**Search term that locates this in similar future filings:** "Difference of the Average ComEd and Average PJM 5 CPs for Weather Sensitive Customers".

#### Why scenarios, not a confidence band

The denominator is a customer-population sum (Σ over customers with positive `ACustPL − ACustCPL`), not a fraction of a zonal aggregate. The aggregation inequality

> `Σ max(ACustPL_i − ACustCPL_i, 0) ≠ max(Σ ACustPL_i − Σ ACustCPL_i, 0)`

means no PJM Data Miner zonal feed (NSPL, weather-normalized peaks, `load_recon_bill_deter_hrly`) can reconstruct it from public aggregates. ComEd computes it internally each year from customer-register data and does not publish current-year values. The 2021 disclosure is the single source-backed historical anchor available.

#### Locked scenarios

| Scenario | portfolio_sum_mw | Source |
|---|---|---|
| `low` | **1,500 MW** | prior planning case |
| `anchor_2021` | **2,033.653 MW** | FERC ER22-1520-001 disclosed Summer 2021 Weather Sensitive denominator (above) |
| `high` | **3,000 MW** | wide upper sensitivity near historical system-gap scale |

Per EXPERIMENT_DESIGN.md §6, Layer 2 is reported across all three named scenarios as a side-table. These are pre-registered scenario analyses, not confidence intervals.

#### Class qualifier

The 2,033.653 MW figure is specifically for the **Weather Sensitive Customer class**. ComEd's branch-2 calculation runs per customer class; Weather Sensitive is the class that captures residential AC-driven load, which is what the household in this study (Rate BESH residential) presents. If a future filing discloses a different applicable class for the household, the `anchor_2021` scenario value should be revisited.

#### Reference documents

- [`data/pjm_oatt_attM2_comed.pdf`](data/pjm_oatt_attM2_comed.pdf) — PJM OATT Attachment M-2 (ComEd), formula definition. URL: https://www.pjm.com/pjmfiles/directory/etariff/MasterTariffs/23TariffSections/18111.pdf
- [`data/ferc_er22_1520_filing_letter.pdf`](data/ferc_er22_1520_filing_letter.pdf) — FERC ER22-1520-001 deficiency response containing Exhibits 1(b)/2(b)(i)/2(b)(ii).

#### Future precision

A future ICC e-Docket attachment or ComEd annual compliance workpaper may publish the current-year customer-class denominator. If found, the locked scenarios will be replaced under an OSF amendment with the disclosed value and a tighter sensitivity. Until then, the three named scenarios are the pre-registered position.

The ComEd zonal NSPL (21,559.6 MW at 2024-08-27 18:00 EPT, from [`data/pjm_nspl_2025.pdf`](data/pjm_nspl_2025.pdf)) is a single-peak transmission-charge figure, not the 5-peak capacity-charge denominator. Retained as provenance only.

## Reading the Att. M-2 second branch in context

For Chris's household (Single-Family Non-Electric Heat on Rate BESH):

```
Layer 1 (primary, observable):
    Δ$_capacity = (ACustCPL_arm_B − ACustCPL_arm_A)_2026 × $10.13567/kW-mo × 5 months

Layer 2 (descriptive, scenarios):
    gap_2025 = ComEdNPL_2026 − AComEdCPL_2026 = 20,736 − 19,138 = 1,598 MW

    CPLC_2027_arm(scenario) = ACustCPL_arm + 1,598 × (ACustPL_arm − ACustCPL_arm) / scenario_mw

    Scenario          | scenario_mw  | branch-2 adjustment factor
    ------------------|--------------|---------------------------
    low               | 1,500 MW     | 1.065  (≈ doubles the per-customer gap)
    anchor_2021       | 2,033.653 MW | 0.786
    high              | 3,000 MW     | 0.533

    Δ$_layer_2(scenario) = (CPLC_2027_arm_B − CPLC_2027_arm_A)(scenario)
                         × $10.13567/kW-mo × 5 months
    Report all three scenarios side-by-side; no single point estimate.
```

Layer 2's branch-2 adjustment factor (`(ComEdNPL − AComEdCPL) / portfolio_sum`) spans ~0.5 to ~1.1 across the locked scenarios. Under `low` the redistribution roughly doubles the household's individual gap; under `high` it cuts the redistribution by half. The disclosed Summer 2021 anchor sits between.

For most residential profiles ACustCPL ≥ ACustPL (branch 1) and Layer 2 collapses to Layer 1. Branch 2 activates when the household's load peaks more with ComEd-zone-only afternoons than with PJM-RTO-wide peaks. AC-driven residential loads in the ComEd zone can sit in either branch depending on whether the household's coincidence with RTO-wide peaks (broader and later) matches or misses.

## Layer 1 observability — does the per-customer §3 formula actually apply to this household?

Att. M-2 §3 carries a "for certain situations" clause that lets ComEd substitute a delivery-class-average CPLC instead of the per-customer 5CP formula. The federal tariff doesn't define "certain situations"; it points at the General Terms and Conditions of Ill. C. C. No. 10. If the class-average clause applied to this household, O2 Layer 1's "ACustCPL_arm_B − ACustCPL_arm_A" framing would measure the right physics but the wrong bill — the household's individual peak-hour load shape wouldn't enter the actual capacity charge.

**Verdict for this household: PER-CUSTOMER applies. Layer 1's observability claim stands.**

Resolved against the 2026 Ratebook (Ill. C. C. No. 10, 867 pages). The ICC tariff uses "PLC" (Peak Load Contribution) — same quantity PJM calls CPLC — and the substitution clause maps, in ICC-tariff terms, to the **load-profile substitution for non-interval-metered customers** described in the GT&C "Measurement of Energy and Demand" section. The bifurcation is by metering, not by customer class:

> **GT&C Definitions (PDF p. 176)**: "PLC means peak load contribution, in kW. The retail customer's PLC is determined by the Company based on PJM's Reliability Pricing Model methodology for a period of twelve (12) monthly billing periods beginning with the June monthly billing period and extending through the following May monthly billing period. For a situation in which insufficient historical electric power and energy consumption data exist for a retail customer, the Company determines such retail customer's PLC based upon, in the Company's judgment, the retail customer's expected electric power and energy requirements and its expected contribution to such peak electric load on the PJM electric system region."

> **GT&C "Measurement of Energy and Demand" (PDF p. 260)**: "a. For a situation in which an interval demand recording metering installation is provided for such retail customer, the average of the interval demand recording meter's data for the two (2) thirty (30) minute intervals within each hour is used to determine such sixty (60) minute demand. b. For a situation in which no metering installation or a metering installation that does not have an interval demand recording register is provided for the retail customer, the sixty (60) minute demands established by the retail customer are statistically derived utilizing the load profile applicable to the retail customer..."

The household in this study is on **Rate BESH (Basic Electric Service Hourly Pricing)**, which requires interval/AMI metering to operate — the supply rate literally depends on hourly prints. So the household is by definition AMI-metered and falls in GT&C item (a): per-customer interval-measured demand → per-customer PJM-RPM-methodology PLC → per-customer CPLC. The Watt-Hour Delivery Class (the residential non-AMI analog) is **nonresidential-only** per the tariff text and does not apply here; Rider NAM (the AMI opt-out) is also not in play.

Across all 867 pages of Ill. C. C. No. 10 the ICC ratebook contains **no "class average PLC" language** for AMI customers — the only class/profile-substitution language in the tariff is the non-interval clause above. The Att. M-2 §3 "average CPLC attributable to the delivery class" clause is therefore the federal-tariff phrasing of the same non-interval substitution rule, applied to customers in situations where interval data is unavailable (new account without 12-month history, missing interval reads, non-AMI accounts, etc.) — not a generic residential override.

**Caveat noted for future amendment.** The agent that resolved this did not fully read Rate RDS body (Ill. C. C. No. 10 sheet 47, PDF p. 80-100), which is the delivery rate that translates PLC into the capacity-charge line on the bill. If a class-average override exists, it would be in the "Determination of Customer Charges" or "Billing Demand" subsection of Rate RDS. This is the place to look if the Layer 1 framing ever needs to be amended post-filing.

**Provenance note: the ICC 22-0036 docket challenge was about Net Metering, not the class-average mechanism.** The ICC Staff Rebuttal Testimony of Torsten Clausen ([`data/icc_22-0036_staff_exhibit_2.0_rebuttal_torsten_clausen.pdf`](data/icc_22-0036_staff_exhibit_2.0_rebuttal_torsten_clausen.pdf)) and the parallel FERC ER22-1520 docket ([`data/ferc_er22_1520_filing_letter.pdf`](data/ferc_er22_1520_filing_letter.pdf), [`data/ferc_er22_1520_order.pdf`](data/ferc_er22_1520_order.pdf)) concern Rider POGNM (solar net metering) and the parallel FERC change allowing negative CPLC values for net-metered customers. The class-average clause for non-net-metered residential customers was carried forward verbatim and not litigated.

**Separately note that the FERC ER22-1520-001 *deficiency response* (filing letter PDF above) discloses the pre-existing Att. M-2 portfolio-sum mechanics for Weather Sensitive Customers in Exhibits 1(b), 2(b)(i), and 2(b)(ii).** That disclosure is incidental to the net-metering amendment but it is the public source for the Summer 2021 denominator value used as the `anchor_2021` scenario in §4 above.

---

## Local PDFs / data files archived alongside this snapshot

### Primary citations (value-locking sources)

| File | Size | Source |
|---|---|---|
| `data/comed_icc_info_sheet_4_capacity_charge.pdf` | 128 KB | ComEd Schedule of Rates Informational Sheet 4, 71st Revised, eff 2026-04-25 — locks MCC = $10.13567/kW-month |
| `data/pjm_weather_normalized_peaks.xlsx` | 27 KB | PJM Resource Adequacy Planning — locks ComEdNPL |
| `data/pjm_oatt_attM2_comed.pdf` | 185 KB | PJM OATT eTariff Att. M-2 (ComEd), eff 2022-09-01 — the locked formula |
| `data/ferc_er22_1520_filing_letter.pdf` | 679 KB | FERC ER22-1520-001 deficiency response, Exhibits 1(b)/2(b)(i)/2(b)(ii) — discloses Summer 2021 Weather Sensitive Customer portfolio sum = 2,033.653 MW (the `anchor_2021` scenario in §4) |
| `data/cub_comed_delivery_tod_fact_sheet_2026-03.pdf` | 505 KB | CUB DTOD fact sheet, March 2026 — source for delivery-rate base values locked in `deploy/energy-stack/hvac-scheduler/precool.py` |

### Provenance / procedural context

| File | Size | Purpose |
|---|---|---|
| `data/pjm_2026_load_forecast_report.pdf` | 8.5 MB | PJM Jan 2026 forecast — context for ComEdNPL forward values |
| `data/pjm_nspl_2025.pdf` | 85 KB | PJM Markets-Ops NSPL Summary 2025 — ComEd zonal single-peak NSPL provenance |
| `data/pjm_oatt_attM2_comed_2015_2019_pre_er22_1520.pdf` | 282 KB | Older Att. M-2 (created 2015-11, modified 2019-04) for tariff-version diff |
| `data/pjm_oatt_attM_appendix_imm_2026-01-08.pdf` | 177 KB | OATT Att. M Appendix (IMM procedural rules) — orthogonal to load-side portfolio sum |
| `data/imm_2025_som_sec5_capacity.pdf` | 3.1 MB | IMM 2025 State of the Market Section 5 (Capacity) — searched, no current-year portfolio-sum disclosure |
| `data/comed_ipa_5yr_load_forecast_2026-2031_filed_2025-07-15.pdf` | 1.9 MB | ComEd IPA-filed 5-year load forecast — searched, no customer-class peaks |
| `data/icc_22-0036_staff_exhibit_2.0_rebuttal_torsten_clausen.pdf` | 136 KB | ICC docket 22-0036 Staff Rebuttal — Net Metering proceeding (procedural context for the parallel ER22-1520 amendment) |
| `data/ferc_er22_1520_order.pdf` | 102 KB | FERC order accepting the ER22-1520 net-metering amendment (no Illinois protests) — procedural companion to the filing letter above |

The ER22-1520-001 deficiency response disclosure is a historical (Summer 2021) Weather Sensitive denominator. Current-year customer-class portfolio sums are not separately published in IL/PJM public sources (verified across IMM SOM Section 5, ComEd IPA Appendix C, and the ICC e-Docket index). Layer 2 is therefore reported across three pre-registered named scenarios (`low` = 1,500 MW, `anchor_2021` = 2,033.653 MW, `high` = 3,000 MW) rather than as a single point estimate with a confidence band.
