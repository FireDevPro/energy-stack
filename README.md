---
date: 2026-05-25
owner: chris
status: active
role-label: chris
---

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

```
Inputs                                  Storage              Decision & output
─────────────────────────────────────   ─────────────────    ──────────────────────────
EAGLE-3 (smart meter, billing-grade) ┐                       hvac-scheduler ──► Control4
ComEd Hourly Pricing (5-min + hourly)├─►                     │  (Arm B: day-type @ 21:00,
PJM Data Miner 2 (DA LMP, load,      │                       │   price overlay, 5CP
  metered, peak, NSPL)               │                       │   planning, safety
NWS forecast + alerts (30-min)       ├─► InfluxDB 2.7 ──►    │   supervisor on every
Refoss EM16P (per-circuit, 30 s)     │   (energy +           │   setpoint push)
Ecowitt GW1200 + WS90 + WN31 ch1     │    energy-longterm)   │
  (canonical outdoor + sun comparator│                       │  Grafana (dashboards)
  + indoor characterization)         │                       │  telegram-notifier
CTK04AE thermostat state (10-min)    ┘                       │  Loki + Promtail (logs)
                                                             │
                                                       ──► CTK04AE thermostat
                                                           (Amana ASXC160481BE 2-stage AC
                                                            + AMVM971005CN modulating
                                                            furnace + ECM blower)
```

Stack runs as a single Docker Compose project. Deployment is automated: merging to
`main` triggers a GitHub Actions workflow that joins the operator's tailnet as an
ephemeral CI node and updates the stack in place. No public ingress.

## Reading path

Start here:

1. [`PROJECT.md`](PROJECT.md) — project narrative, decisions, phase history, hardware inventory.
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
