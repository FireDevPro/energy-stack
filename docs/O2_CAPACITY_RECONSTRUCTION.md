---
name: O2_CAPACITY_RECONSTRUCTION
date: 2026-05-18
owner: chris
status: active
role-label: binding-reference
extracted_from: archive/EXPERIMENT_DESIGN.md (§O2)
extraction_pr: docs/plans/pre-osf-doc-audit-execution-2026-05-18.md PR6
related:
  - plans/sced-rebaseline-spec-2026-05-13.md
  - ../tools/o2_capacity_reconstruction/tariff_snapshot.md
---

# O2 capacity-charge avoidance — three-layer measurement

This page extracts what was originally `EXPERIMENT_DESIGN.md` §O2 so that the capacity-reconstruction framing lives as a current top-level reference co-located with the supporting math artifact at [`tools/o2_capacity_reconstruction/tariff_snapshot.md`](../tools/o2_capacity_reconstruction/tariff_snapshot.md). The named-scenarios framing (Layer 2 with 1500 / 2,033.653 / 3000 MW portfolio-sum scenarios) reflects the FERC ER22-1520-001 research completed 2026-05 and is verified against the tariff snapshot per [`docs/plans/pre-osf-doc-audit-truth-tables-2026-05-18.md`](plans/pre-osf-doc-audit-truth-tables-2026-05-18.md).

The one substantive change from the original Appendix text: the "Bootstrap CI for Layer 1" sentence has been dropped because the rebaseline spec §9.5 retires bootstrap-CI inference machinery in favor of per-pair descriptive reporting.

## Truth source for "which hours count"

PJM's final-published 5CP hour lists for the RTO (5 PJM Five Peak hours) and the ComEd zone (5 ComEd Five Peak hours), released mid-October after the season closes per [PJM Manual 19 §4.3](https://www.pjm.com/-/media/documents/manuals/m19.ashx). The Arm B live 5CP detector's hour-by-hour accuracy is reported descriptively as a process metric (see Detector accuracy report below); it does NOT define O2. O2 is computed at the PJM-published peak hours regardless of which hours the live detector flagged.

## Three-layer outcome reporting

The outcome is reported at three layers, each strictly more inclusive of stipulation than the prior.

### Layer 1 — Observed `ACustCPL` difference (primary, fully observable)

Arm B minus Arm A difference in `ACustCPL_Y`, the household's average metered demand across the five PJM Five Peak hours of summer Y. Fully observable from the household's revenue meter (EAGLE feed for instantaneous; cross-checked against ComEd-bill kWh for accumulation). Maps to the first branch of PJM OATT Attachment M-2 (ComEd) §2:

- `CPLC_(Y+1) = ACustCPL_Y` when `ACustCPL_Y ≥ ACustPL_Y`.

This is the dominant case for most residential profiles. Reported as the primary O2 number. **Diluted-per-kW caveat:** a single-hour reduction at one of the five peaks shifts the average by roughly `kW / 5`, not full kW; the reported delta reflects the five-hour average.

### Layer 2 — Stipulated `CPLC_(Y+1)` reconstruction (descriptive, one stipulated input)

Full `CPLC_(Y+1)` reconstruction using both Att. M-2 branches:

- Branch 1 as in Layer 1.
- Branch 2: `CPLC_(Y+1) = ACustCPL_Y + (ComEdNPL_Y − AComEdCPL_Y) × (ACustPL_Y − ACustCPL_Y) / Σ_5Pc(ACustPL − ACustCPL)` where `ACustPL_Y` is the household's average demand across the five ComEd Five Peak hours, `ComEdNPL_Y` is ComEd's weather-normalized peak load, `AComEdCPL_Y` is ComEd's average coincident peak at the PJM five peaks, and `Σ_5Pc(ACustPL − ACustCPL)` is the portfolio sum across all customers in branch 2.

The portfolio sum is unobservable from a single household. ComEd computes it internally each year from customer-register data across the positive-gap customer population defined by Att. M-2 §2, and does not publish current-year values. The Summer 2021 value for the Weather Sensitive Customer class (the relevant class for Rate BESH residential AC-driven load) was disclosed as **2,033.653 MW** in the FERC ER22-1520-001 deficiency response, Exhibits 1(b), 2(b)(i), and 2(b)(ii); see [`tools/o2_capacity_reconstruction/tariff_snapshot.md`](../tools/o2_capacity_reconstruction/tariff_snapshot.md) §4 for the source PDF, exhibit references, and verification math. The aggregation inequality `Σ max(ACustPL_i − ACustCPL_i, 0) ≠ max(Σ ACustPL_i − Σ ACustCPL_i, 0)` means no PJM Data Miner zonal feed reconstructs this denominator from public aggregates.

Layer 2 is descriptive only and is reported across three pre-registered named denominators rather than a single point estimate with a confidence band:

| Scenario | portfolio_sum_mw | Source |
|---|---|---|
| `low` | 1,500 MW | prior planning case |
| `anchor_2021` | 2,033.653 MW | FERC ER22-1520-001 disclosed Summer 2021 Weather Sensitive denominator |
| `high` | 3,000 MW | wide upper sensitivity near historical system-gap scale |

These are scenario analyses, not confidence intervals. The reported CPLC reconstruction is presented as a side-table with one row per scenario. If a future ICC e-Docket or ComEd workpaper publishes the current-year denominator, the locked scenarios will be replaced with the disclosed value and a tighter sensitivity under an OSF amendment.

### Layer 3 — Bill reconciliation (descriptive, post-Y+1)

The actual ComEd Capacity Charge line item on the Y+1 bills (May-Sep months, the period over which `CPLC_(Y+1)` is applied per tariff) is recorded month-by-month and summed. The ratio (Layer 2 / Layer 3) is reported as a tariff-reconstruction fidelity number. Layer 3 has no within-house counterfactual — there is only one realized bill trajectory for the realized arm assignment in summer Y — so Layer 3 is descriptive only and does not enter any effect-size statement.

## Counterfactual scope

Layers 1 and 2 are computed twice: once on the Arm A periods' realized demand, once on the Arm B periods' realized demand. The per-pair difference is what the §7 weather-matched pairing permits as descriptive comparison. Layer 3 is anchored only to the realized assignment.

## Detector accuracy report (process metric, not an outcome)

Separately, the Arm B live 5CP detector's hour-by-hour decisions during summer Y are cross-referenced against PJM's October-published 5CP hour list. Reported: true-positive rate (detector held shutoff during a published 5CP hour), false-positive rate (detector held shutoff during a non-5CP hour), false-negative rate (detector did not hold during a published 5CP hour). This characterizes the live detector as an engineering subsystem; it is decoupled from O2's outcome statement.
