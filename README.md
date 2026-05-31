---
date: 2026-05-25
owner: chris
status: active
role-label: chris
---

<p align="center">
  <img src="docs/assets/hero.jpg" alt="Stylized cutaway of a house with sensor inputs (smart meter, thermostat, AC condenser, weather, water heater, breaker panel) on the left flowing into a central 'energy-proxy' hub, which connects to five output panels on the right: optimized HVAC schedule, comfort guardrails, price-aware decisions, analytics and insights, and peak-risk avoidance." width="900">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href=".python-version"><img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13"></a>
  <a href="https://github.com/FireDevPro/energy-stack/actions/workflows/typecheck.yml"><img src="https://img.shields.io/github/actions/workflow/status/FireDevPro/energy-stack/typecheck.yml?branch=main&label=type-check" alt="Type-check status"></a>
  <a href="https://github.com/FireDevPro/energy-stack/actions/workflows/shadow-validation.yml"><img src="https://img.shields.io/github/actions/workflow/status/FireDevPro/energy-stack/shadow-validation.yml?branch=main&label=shadow-validation" alt="Shadow-validation status"></a>
  <a href="https://osf.io/w52kq/"><img src="https://img.shields.io/badge/OSF-preregistered-blue" alt="OSF: preregistered"></a>
  <a href="https://doi.org/10.5281/zenodo.20477728"><img src="https://zenodo.org/badge/DOI/10.5281/zenodo.20477728.svg" alt="DOI: 10.5281/zenodo.20477728"></a>
  <img src="https://img.shields.io/github/last-commit/FireDevPro/energy-stack" alt="Last commit">
</p>

# energy-proxy

Code, telemetry pipeline, and analysis pipeline for a pre-registered single-case
experimental-design (SCED) field study comparing an RTP / DTOD / 5CP-risk-aware HVAC
controller against the household's existing programmable-thermostat schedule, run as
a 24-week alternation summer through fall 2026 in a single ComEd Hourly Pricing
household.

## The study

A 12-arm deterministic A/B/A/B alternation from 2026-06-01 through 2026-11-16 in an
IECC climate zone 5A single-occupant household on ComEd Rate BESH (Hourly Pricing) +
DTOD (Distribution Time-of-Day). Each arm is 14 calendar days with a 48 h washout;
6 Arm A periods alternate with 6 Arm B periods.

- **Arm A** — Amana CTK04AE thermostat's internal programmed schedule runs
  autonomously. `hvac-scheduler` is in `shadow` mode (telemetry only, no writes).
- **Arm B** — `hvac-scheduler` is active. RTP/DTOD live price-responsive control with
  day-type scheduling, price-aware pre-cool, 5CP capacity-risk planning/telemetry,
  and a safety supervisor that clamps every setpoint push.

Framing is transparent single-household n-of-1 matched-pair, descriptive (no
statistical decision rule — readers draw their own conclusions from the per-pair
table). Per-protocol algorithm efficacy is the primary estimand; reliability is
reported as provenance, not the outcome under test.

Pre-registration is binding: hypotheses, arm definitions, calendar, metric
definitions, and the statistical analysis plan lock at a frozen commit hash on
OSF filing. The binding spec is the source of truth.

- Binding spec: **[`docs/plans/sced-rebaseline-spec-2026-05-13.md`](docs/plans/sced-rebaseline-spec-2026-05-13.md)**
- Arm calendar: **[`tools/analysis/arm_calendar.py`](tools/analysis/arm_calendar.py)** (byte-identical with the controller-side copy at [`deploy/energy-stack/hvac_scheduler/arm_calendar.py`](deploy/energy-stack/hvac_scheduler/arm_calendar.py); CI hash-sync checked)
- OSF filing target: **2026-05-30**

## What's in this repo

- **Arm A definition** — frozen CTK04AE program in [`docs/THERMOSTAT_ARM_A_SCHEDULE.md`](docs/THERMOSTAT_ARM_A_SCHEDULE.md).
- **Arm B controller** — `hvac-scheduler` service plus safety supervisor and watchdog. Logic in [`docs/HVAC_LOGIC.md`](docs/HVAC_LOGIC.md); locked thresholds in [`docs/CONTROLLER_CONSTANTS.md`](docs/CONTROLLER_CONSTANTS.md); timing in [`docs/SCHEDULER_TIMING.md`](docs/SCHEDULER_TIMING.md).
- **Telemetry pipeline** — Docker Compose stack of pollers + InfluxDB + Grafana + Loki, under [`deploy/energy-stack/`](deploy/energy-stack/). Per-service detail in [`docs/SERVICES.md`](docs/SERVICES.md).
- **Analysis pipeline** — Flux query library and replay harness under [`tools/analysis/`](tools/analysis/), plus the frozen [`tools/comed_2025_analysis/`](tools/comed_2025_analysis/) bundle that anchors the Arm B thresholds and [`tools/o2_capacity_reconstruction/`](tools/o2_capacity_reconstruction/) for the capacity-charge outcome.
- **Validation methodology** — [`docs/REPLAY_VALIDATION.md`](docs/REPLAY_VALIDATION.md) and [`docs/DRY_RUN_VALIDATION.md`](docs/DRY_RUN_VALIDATION.md).
- **Ethics framing** — [`docs/ETHICS_FRAMING.md`](docs/ETHICS_FRAMING.md).

## Architecture

![Architecture: inputs (EAGLE-3, ComEd Hourly Pricing, PJM Data Miner 2, NWS, Refoss EM16P, Ecowitt, CTK04AE state, ComfortNet CT-485 bus via MQTT) flow into InfluxDB 2.7, which feeds the hvac-scheduler Arm B controller (with safety supervisor), Grafana dashboards, Loki + Promtail logs, and the telegram-notifier; the scheduler pushes setpoints to the CTK04AE thermostat driving a 2-stage Amana AC and modulating furnace.](docs/diagrams/architecture.png)

> Diagram source: [`docs/diagrams/architecture.mmd`](docs/diagrams/architecture.mmd) (Mermaid).
> Re-render with `npx -y @mermaid-js/mermaid-cli -i docs/diagrams/architecture.mmd -o docs/diagrams/architecture.png -t neutral -b white -w 1600 -H 1400` (SVG is also committed at the same path).

Stack runs as a single Docker Compose project. Deployment is automated: merging to
`main` triggers a GitHub Actions workflow that joins the operator's tailnet as an
ephemeral CI node and updates the stack in place. No public ingress.

## Reading path

Start here:

1. [`docs/PROJECT.md`](docs/PROJECT.md) — project narrative, decisions, phase history, hardware inventory.
2. [`INDEX.md`](INDEX.md) — canonical document map (every active doc, grouped by intent).
3. [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](docs/plans/sced-rebaseline-spec-2026-05-13.md) — the binding spec. Read this if you want to know what the experiment actually is.
4. [`docs/HVAC_LOGIC.md`](docs/HVAC_LOGIC.md) — Arm B controller logic (overlays, day-types, 5CP, safety supervisor).
5. [`docs/THERMOSTAT_ARM_A_SCHEDULE.md`](docs/THERMOSTAT_ARM_A_SCHEDULE.md) — Arm A (CTK04AE program).
6. [`deploy/energy-stack/README.md`](deploy/energy-stack/README.md) — stack ops, ports, secrets, restore.

For AI agents: [`AGENTS.md`](AGENTS.md) is the standing behavior contract for this repo.

## Open data and open code

At OSF filing the repo is tagged at the freeze commit hash and a citable code DOI
is issued via Zenodo. The binding spec is the OSF deposit's primary artifact;
telemetry collected during the experiment is planned for release as Apache Parquet
on Zenodo (CC BY 4.0) with [Brick Schema](https://brickschema.org/) JSON-LD metadata
after the experiment window closes.

- OSF filing mechanics: [`docs/OSF_FILING_MECHANICS.md`](docs/OSF_FILING_MECHANICS.md)
- Citation DOI: _placeholder — added post-filing_
- Code license: [MIT](LICENSE)
- Security policy: [`SECURITY.md`](SECURITY.md)
