---
name: ETHICS_FRAMING
date: 2026-05-18
owner: chris
status: active
role-label: binding-reference
extracted_from: archive/EXPERIMENT_DESIGN.md (§11)
extraction_pr: docs/plans/pre-osf-doc-audit-execution-2026-05-18.md PR6
chris_approved: 2026-05-18
related:
  - plans/sced-rebaseline-spec-2026-05-13.md
  - THERMOSTAT_ARM_A_SCHEDULE.md
---

# Ethical conduct and ethics framing

This study is conducted as **building-as-subject measurement** by an unaffiliated owner-investigator who is also the sole occupant of the residence under study.

## Building-as-subject framing

The subject of measurement is the HVAC system and the building envelope. Measured outcomes are kWh, $, peak kW, and PJM 5CP coincident-peak demand. The setpoint envelope (cool setpoint range across both arms) is set by homeowner preference and is identical across arms by construction; it is an **input** to the experiment, not an outcome being optimized or measured. The experiment compares the cost of reaching the same homeowner-set envelope under two different control strategies.

The thermostat-programmed schedule running in Arm A operates entirely within the ASHRAE 55-2020 summer comfort envelope (cool setpoints 73-78°F per [`THERMOSTAT_ARM_A_SCHEDULE.md`](THERMOSTAT_ARM_A_SCHEDULE.md)). Arm B's setpoints during pre-cool and scarcity-price-driven shutoff windows (price-overlay scarcity tier ≥ 20¢/kWh → 85°F effective; see binding spec §11 #14) are deliberately outside ASHRAE 55, as a homeowner-set cost-optimization choice that predates this study. The smart-system safety supervisor enforces a hard `[65°F, 86°F]` cool setpoint clamp on every push, so no controller bug can drive the house outside engineering-safe operating bounds.

## No institutional affiliation

The investigator holds no Federalwide Assurance and no institutional research affiliation. 45 CFR 46 applies to research conducted by FWA-bound institutions; independent owner self-experimentation in one's own residence does not fall within its institutional enforcement scope.

## COPE proportionality

The Committee on Publication Ethics has acknowledged that requiring formal IRB review for independent self-experimenters with no third-party participants imposes a publication barrier disproportionate to the actual ethical risk. The study includes a Statement of Ethics in the eventual manuscript covering: sole participant, self-consent, no recruited or affected third parties, intervention limited to standard homeowner thermostat operation, and no risk beyond daily life.

## Companion-animal welfare

The household includes companion dogs. Indoor temperature is held within the comfort-ceiling envelope governing human comfort, which is consistent with [AVMA companion-animal welfare guidance](https://www.avma.org/resources-tools/animal-health-and-welfare/animal-welfare-changing-environment) (sustained indoor temperature should not exceed 80°F). Realized indoor temperature distributions during 13:00-20:00 CT 5CP-eligibility windows (per [`CONTROLLER_CONSTANTS.md`](CONTROLLER_CONSTANTS.md) §PJM 5CP-eligibility detection) are reported descriptively (median, 90th, 95th percentile) so any envelope excursions are auditable.

## Conduct commitments

- The investigator has unilateral authority to terminate any arm at any time for any reason (comfort, equipment safety, household event, ethical concern); the deviation is logged and reported transparently.
- Household guests, if any, are not enrolled subjects. If a guest objects to the operating envelope at any point, the arm is paused for the guest's stay and the deviation is logged.

## Self-experimentation precedent

Self-experimentation is offered as context, not as a regulatory exemption: [Forssmann 1929 cardiac catheterization](https://doi.org/10.1007/BF01884716), [Marshall & Warren 1984 *H. pylori*](https://doi.org/10.1016/S0140-6736(84)91816-6), and the broader N=1 / quantified-self lineage. The building-as-subject framing above is the primary defense; self-experimentation precedent is supplementary.
