---
date: 2026-05-19
owner: chris
status: active
role-label: doc-index
name: INDEX
related:
  - AGENTS.md
  - PROJECT.md
---

# Canonical doc index

Curated map of active documentation in this repository, grouped by
intent. For agents: this is the authoritative starting point — every
doc listed here is current and load-bearing. For humans: this is the
table of contents.

**Project rules for agents live in [`AGENTS.md`](AGENTS.md).** This
file is the WHAT (content map). AGENTS.md is the HOW (behavior
contract).

What's NOT in this index is documented in the [Excluded](#excluded-from-this-index)
section at the bottom — primarily archived docs, per-event findings,
and superseded plan-execution artifacts.

---

## Project entry points

Read these first if you're new to the repo.

- [README.md](README.md) — public landing page; project summary and
  pointers to the OSF deposit
- [PROJECT.md](PROJECT.md) — project narrative, current status, study
  trajectory, phase plan
- [AGENTS.md](AGENTS.md) — standing behavior rules for AI agents
  working in this repo (tone, coding rules, branching policy, skill
  protocol)
- [CLAUDE.md](CLAUDE.md) — Claude-specific pointer that defers to
  AGENTS.md for project rules
- [HANDOFF.md](HANDOFF.md) — session-handoff template for continuity
  across operator sessions

## Binding pre-registration (OSF freeze)

These are the documents that lock at the OSF freeze tag. Changes after
freeze are protocol deviations requiring an OSF amendment.

- [docs/plans/sced-rebaseline-spec-2026-05-13.md](docs/plans/sced-rebaseline-spec-2026-05-13.md)
  — **THE binding spec.** Hypotheses, arm definitions, calendar,
  metrics, statistical analysis plan, decision rules. Source of truth
  for what the experiment is.
- [docs/plans/sced-rebaseline-implementation-2026-05-13.md](docs/plans/sced-rebaseline-implementation-2026-05-13.md)
  — implementation plan tracking spec phases through code

## Controller logic (Arm A and Arm B)

The two arms of the SCED comparison and how each is configured.

- [docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md) — Arm B (active scheduler):
  RTP overlays, DTOD bands, 5CP avoidance, safety supervisor, fallback
  to thermostat schedule
- [docs/CONTROLLER_CONSTANTS.md](docs/CONTROLLER_CONSTANTS.md) —
  locked Arm B thresholds (price triggers, day-type cutoffs, PJM 5CP
  parameters); pre-committed before OSF filing
- [docs/SCHEDULER_TIMING.md](docs/SCHEDULER_TIMING.md) — Mermaid
  diagrams of scheduler tick cadence and decision sequencing
- [docs/THERMOSTAT_ARM_A_SCHEDULE.md](docs/THERMOSTAT_ARM_A_SCHEDULE.md)
  — Arm A (CTK04AE programmed): autonomous 4-event daily schedule +
  equipment-level settings
- [docs/ARM_B_IMPLEMENTATION.md](docs/ARM_B_IMPLEMENTATION.md) — Arm
  B implementation details (per-day-type behavior, transition logic)
- [docs/ARM_TRANSITIONS.md](docs/ARM_TRANSITIONS.md) — operator
  checklist for every Monday 00:00 CT arm-boundary transition

## Data, telemetry, and integrations

How telemetry flows from devices through the stack into InfluxDB and
out to analysis.

- [docs/SERVICES.md](docs/SERVICES.md) — per-service detail for every
  container in the deploy stack
- [docs/DEBUG_TELEMETRY.md](docs/DEBUG_TELEMETRY.md) — quick-reference
  catalog: Arm A schedule at a glance, Arm B scheduler inputs, and
  per-measurement consumption status (FULL / PARTIAL / NONE) for every
  Influx measurement in the `energy` bucket
- [docs/INFLUXDB_RETENTION.md](docs/INFLUXDB_RETENTION.md) — bucket
  policies, downsampling rules, retention windows
- [docs/PJM_DM2_FEEDS.md](docs/PJM_DM2_FEEDS.md) — PJM Data Miner 2
  feed catalog and shapes
- [docs/PJM_DM2_INTEGRATION.md](docs/PJM_DM2_INTEGRATION.md) — how
  the pjm-dm2-poller consumes those feeds
- [docs/COMFORTNET_USE_CASES.md](docs/COMFORTNET_USE_CASES.md) —
  ComfortNet bus integration scenarios

## Methodology, validation, and ethics

The science layer: what counts as evidence, how it's validated, the
ethical posture.

- [docs/REPLAY_VALIDATION.md](docs/REPLAY_VALIDATION.md) — replay
  validation methodology and validation-bundle structure
- [docs/DRY_RUN_VALIDATION.md](docs/DRY_RUN_VALIDATION.md) — dry-run
  validation procedure for scheduler changes
- [docs/O2_CAPACITY_RECONSTRUCTION.md](docs/O2_CAPACITY_RECONSTRUCTION.md)
  — ComEd Attachment M-2 capacity reconstruction methodology
- [docs/ETHICS_FRAMING.md](docs/ETHICS_FRAMING.md) — single-occupant
  household research ethics, consent posture, data minimization
- [docs/THERMAL_ROUGH_CUT_2026-05-26.md](docs/THERMAL_ROUGH_CUT_2026-05-26.md)
  — pre-OSF empirical fit (AC-off drift) sanity-checking the
  scheduler's hand-tuned constants. Disposition: no parameter
  changes; surfaced the wrong-variable choice in pyControl4 that
  motivated the `indoor_temp_f_hires` telemetry field

## OSF / Zenodo deposit and security

Operational mechanics for the academic publication track.

- [docs/OSF_FILING_MECHANICS.md](docs/OSF_FILING_MECHANICS.md) —
  12-step freeze-day procedure for OSF + Zenodo deposit
- [docs/plans/public-flip-readiness-2026-05-19.md](docs/plans/public-flip-readiness-2026-05-19.md)
  — GitHub-platform hardening checklist for the 2026-05-30 public
  flip (active until flip complete, then archives)
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

- [tools/cockpit/README.md](tools/cockpit/README.md) — Controller
  Cockpit: read-only live dashboard
- [tools/analysis/queries/README.md](tools/analysis/queries/README.md)
  — Flux query library for analysis pipelines
- [tools/analysis/replay/README.md](tools/analysis/replay/README.md)
  — replay framework for replaying historical telemetry against
  current scheduler
- [tools/comed_2025_analysis/README.md](tools/comed_2025_analysis/README.md)
  — frozen analysis bundle for the 2025 ComEd RTP distribution that
  anchors the Arm B threshold values
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
  the May 2026 doc audit pass. Useful as historical context for why
  the current doc state looks the way it does; not load-bearing for
  ongoing work.
- **`tools/cockpit/frontend/`** and other vendored / generated
  artifacts (`node_modules/`, `.pytest_cache/`, `site/`).
- **Per-service code, configs, and tests** under
  `deploy/energy-stack/<service>/` — discoverable from
  `deploy/energy-stack/README.md` and the per-service Dockerfiles +
  source.

## Maintenance

When adding a new canonical doc, add it to the relevant section above
in the same PR. When archiving a doc, remove it from the index and
add it to `docs/archive/` per the archive policy in `AGENTS.md`. When
in doubt, lean toward inclusion — an over-inclusive index is cheaper
than a missing entry that hides a load-bearing doc.
