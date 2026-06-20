---
date: 2026-06-20
owner: chris
status: active
role-label: doc-index
name: INDEX
related:
  - docs/PROJECT.md
---

# Canonical doc index

Curated map of active documentation in this repository, grouped by
intent. For agents: this is the authoritative starting point — every
doc listed here is current and load-bearing. For humans: this is the
table of contents.

What's NOT in this index is documented in the [Excluded](#excluded-from-this-index)
section at the bottom — primarily archived docs, per-event findings,
and superseded plan-execution artifacts.

---

## Project entry points

Read these first if you're new to the repo.

- [README.md](README.md) — landing page; project summary and stack
  overview
- [docs/PROJECT.md](docs/PROJECT.md) — project narrative, current
  status, phase plan

## Controller logic (Arm A and Arm B)

Current source of truth for the commissioning controller (warm-only
price-aware overlay on the Arm A comfort program).

- [docs/superpowers/specs/2026-06-20-commissioning-controller-design.md](docs/superpowers/specs/2026-06-20-commissioning-controller-design.md)
  — **current controller spec.** Arm B = Arm A comfort baseline + RTP
  warm-offset. Warm-only; device-owned safety (setpoint limits + timed
  holds); no day-types, no 5CP control, no deep precool, no software
  supervisor. Config is the experimental surface.
- [docs/superpowers/plans/2026-06-20-commissioning-controller-plan.md](docs/superpowers/plans/2026-06-20-commissioning-controller-plan.md)
  — implementation plan for the commissioning controller (shadow →
  active commissioning → 2027 A/B comparison)
- [docs/THERMOSTAT_ARM_A_SCHEDULE.md](docs/THERMOSTAT_ARM_A_SCHEDULE.md)
  — designated home for the CTK04AE onboard fallback schedule (the
  device-native safety fallback a lapsed timed-hold reverts to).
  ⚠ CONTENT IS STALE (old OSF-era schedule + Permanent-hold/day-type
  language) — must be refreshed to the deployed program before
  go-active; not yet authoritative.

**Historical / superseded** (old controller design; do not treat as
current source of truth):

- [docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md) — superseded: old Arm B
  with day-types, DTOD bands, 5CP control, and software supervisor
- [docs/CONTROLLER_CONSTANTS.md](docs/CONTROLLER_CONSTANTS.md) —
  superseded: old locked thresholds from the SCED experiment era
- [docs/SCHEDULER_TIMING.md](docs/SCHEDULER_TIMING.md) — superseded:
  Mermaid diagrams of old day-type/5CP/supervisor tick cadence (pending
  rewrite for new controller)
- [docs/ARM_B_IMPLEMENTATION.md](docs/ARM_B_IMPLEMENTATION.md) —
  superseded: old Arm B implementation details (pending rewrite for
  new controller)
- [docs/ARM_TRANSITIONS.md](docs/ARM_TRANSITIONS.md) — superseded: old
  SCED arm-transition procedure (Permanent holds,
  SCHEDULER_MODE=experiment, Monday transitions); needs rewrite for
  timed holds / 2027
- [docs/plans/sced-rebaseline-spec-2026-05-13.md](docs/plans/sced-rebaseline-spec-2026-05-13.md)
  — superseded: original SCED binding spec (experiment retracted)
- [docs/plans/sced-rebaseline-implementation-2026-05-13.md](docs/plans/sced-rebaseline-implementation-2026-05-13.md)
  — superseded: SCED implementation plan
- [docs/OSF_FILING_MECHANICS.md](docs/OSF_FILING_MECHANICS.md) —
  superseded: OSF/Zenodo freeze procedure (experiment retracted; repo
  going private)

## Data, telemetry, and integrations

How telemetry flows from devices through the stack into InfluxDB and
out to analysis.

- [docs/SERVICES.md](docs/SERVICES.md) — per-service detail for every
  container in the deploy stack
- [docs/DEBUG_TELEMETRY.md](docs/DEBUG_TELEMETRY.md) — quick-reference
  catalog: per-measurement consumption status (FULL / PARTIAL / NONE)
  for every Influx measurement in the `energy` bucket; includes 5CP
  telemetry (retained for analysis; not a live control input)
- [docs/INFLUXDB_RETENTION.md](docs/INFLUXDB_RETENTION.md) — bucket
  policies, downsampling rules, retention windows
- [docs/PJM_DM2_FEEDS.md](docs/PJM_DM2_FEEDS.md) — PJM Data Miner 2
  feed catalog and shapes
- [docs/PJM_DM2_INTEGRATION.md](docs/PJM_DM2_INTEGRATION.md) — how
  the pjm-dm2-poller consumes those feeds
- [docs/COMFORTNET_USE_CASES.md](docs/COMFORTNET_USE_CASES.md) —
  ComfortNet bus integration scenarios

## Methodology, validation, and analysis

The science layer: what counts as evidence, how it's validated.

- [docs/REPLAY_VALIDATION.md](docs/REPLAY_VALIDATION.md) — replay
  validation methodology and validation-bundle structure
- [docs/DRY_RUN_VALIDATION.md](docs/DRY_RUN_VALIDATION.md) — dry-run
  validation procedure for scheduler changes
- [docs/O2_CAPACITY_RECONSTRUCTION.md](docs/O2_CAPACITY_RECONSTRUCTION.md)
  — ComEd Attachment M-2 capacity reconstruction methodology
- [docs/ETHICS_FRAMING.md](docs/ETHICS_FRAMING.md) — single-occupant
  household research ethics, consent posture, data minimization
- [docs/THERMAL_ROUGH_CUT_2026-05-26.md](docs/THERMAL_ROUGH_CUT_2026-05-26.md)
  — empirical fit (AC-off drift) sanity-checking hand-tuned constants;
  surfaced the wrong-variable choice in pyControl4 that motivated the
  `indoor_temp_f_hires` telemetry field

## Security

- [SECURITY.md](SECURITY.md) — security policy and private-reporting
  channel

## Stack operations

How to deploy, run, and tweak the running stack on Pi-lab.

- [deploy/energy-stack/README.md](deploy/energy-stack/README.md) —
  full stack guide of record: services, ports, env, ops cheat sheet
- [deploy/energy-stack/scripts/README.md](deploy/energy-stack/scripts/README.md)
  — ComEd bill ingest + per-script workflows

## Tools

Workstation-local utilities and analysis scaffolds. Most are
self-contained mini-projects with their own READMEs.

- [tools/analysis/queries/README.md](tools/analysis/queries/README.md)
  — Flux query library for analysis pipelines
- [tools/analysis/replay/README.md](tools/analysis/replay/README.md)
  — replay framework for replaying historical telemetry against
  current scheduler
- [tools/comed_2025_analysis/README.md](tools/comed_2025_analysis/README.md)
  — frozen analysis bundle for the 2025 ComEd RTP distribution
  (reference for price threshold calibration)
- [tools/comed_price_imputation/README.md](tools/comed_price_imputation/README.md)
  — ComEd 5-min RTP imputation for sparse-data periods
- [tools/o2_capacity_reconstruction/README.md](tools/o2_capacity_reconstruction/README.md)
  — capacity-charge reconstruction script

---

## Excluded from this index

Intentionally omitted to keep the index focused on active canonical
content:

- **`docs/archive/`** — superseded historical docs preserved for
  provenance. Reference these only when asking "what was the prior
  state of X" — not for current behavior.
- **`docs/replay-validation/`** — per-event validation findings
  (specific moments in time, not narrative reference).
- **`docs/plans/pre-osf-doc-audit-*-2026-05-18.md`** — audit working
  artifacts (codex review, findings, truth tables, execution log) from
  the May 2026 doc audit pass. Historical context only.
- **`docs/plans/public-flip-readiness-2026-05-19.md`** — GitHub
  hardening checklist for the 2026-05-30 public flip. Superseded;
  repo is going private.
- **Vendored / generated artifacts** (`node_modules/`,
  `.pytest_cache/`, `site/`).
- **Per-service code, configs, and tests** under
  `deploy/energy-stack/<service>/` (including the Controller Cockpit
  at `deploy/energy-stack/cockpit/`) — discoverable from
  `deploy/energy-stack/README.md` and the per-service Dockerfiles +
  source.

## Maintenance

When adding a new canonical doc, add it to the relevant section above
in the same PR. When archiving a doc, remove it from the index and
add it to `docs/archive/` per the archive policy in `AGENTS.md`. When
in doubt, lean toward inclusion — an over-inclusive index is cheaper
than a missing entry that hides a load-bearing doc.
