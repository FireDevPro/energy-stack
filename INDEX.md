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
- The rev 4 implementation plan is **executed and archived**
  ([docs/superpowers/plans/archive/2026-07-05-spike-only-controller-plan.md](docs/superpowers/plans/archive/2026-07-05-spike-only-controller-plan.md),
  PRs #116–#121; feature closed 2026-07-07 — validation record in the spec's
  Go-active section). The superseded rev-3 plan is in the same archive.

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

- [docs/THERMAL_ROUGH_CUT_2026-05-26.md](docs/THERMAL_ROUGH_CUT_2026-05-26.md)
  — empirical fit (AC-off drift) sanity-checking hand-tuned constants;
  surfaced the wrong-variable choice in pyControl4 that motivated the
  `indoor_temp_f_hires` telemetry field

## Stack operations

How to deploy, run, and tweak the running stack on Pi-lab.

- [deploy/energy-stack/README.md](deploy/energy-stack/README.md) —
  full stack guide of record: services, ports, env, ops cheat sheet
- [deploy/energy-stack/scripts/README.md](deploy/energy-stack/scripts/README.md)
  — ComEd bill ingest + per-script workflows

## Tools

Workstation-local utilities and analysis scaffolds. Most are
self-contained mini-projects with their own READMEs.

- [tools/decision-trace-report/README.md](tools/decision-trace-report/README.md)
  — daily decision-trace commissioning report tooling

---

## Excluded from this index

Intentionally omitted to keep the index focused on active canonical
content:

- **`docs/archive/`** — superseded historical docs preserved for
  provenance. Reference these only when asking "what was the prior
  state of X" — not for current behavior.
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
