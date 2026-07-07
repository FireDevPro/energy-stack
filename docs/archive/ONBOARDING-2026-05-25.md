> **ARCHIVED 2026-07-07 — thoroughly stale.** Generated 2026-05-25 from a
> pre-demolition knowledge graph; describes the killed SCED study, the
> safety supervisor, day-types, and precool — none of which exist. Regenerate
> a fresh onboarding doc via the Understand-Anything plugin after the
> knowledge-graph rebuild; until then, start at INDEX.md and the rev 4.1
> controller spec.

---
date: 2026-05-25
owner: chris
status: active
role-label: chris
generator: understand-anything (knowledge-graph commit ad1f128)
---

# Onboarding — energy-proxy

> Generated from the project's interactive knowledge graph (`.understand-anything/knowledge-graph.json`). For the visual version, run `/understand-anything:understand-dashboard`. For the human-curated doc map, see [INDEX.md](../INDEX.md).

## Project Overview

**energy-proxy** is a Home Energy Monitoring & HVAC Optimization stack running as a Docker Compose deployment on Pi-lab (Raspberry Pi). Real-time and historical residential energy monitoring with **dynamic-pricing-aware HVAC scheduling**, built around ComEd Hourly Pricing + PJM 5CP avoidance.

**Research context:** Starting **2026-06-01**, this stack runs a **pre-registered SCED field study** comparing the CTK04AE thermostat's programmed schedule (**Arm A**) against the active `hvac-scheduler` (**Arm B**: RTP/DTOD/5CP-risk-aware RBC with safety supervisor). Binding pre-registration spec: [docs/plans/sced-rebaseline-spec-2026-05-13.md](plans/sced-rebaseline-spec-2026-05-13.md), frozen at the OSF-filing commit.

| | |
|---|---|
| **Languages (13)** | python, typescript, javascript, markdown, yaml, json, toml, dockerfile, shell, powershell, css, html, flux |
| **Frameworks (15)** | Docker, Docker Compose, FastAPI, GitHub Actions, InfluxDB, Pydantic, React, Tailwind CSS, Uvicorn, Vite, Vitest, aiohttp, pandas, pytest, scipy |
| **Files analyzed** | 423 |
| **Graph** | 1109 nodes / 1642 edges across 9 layers |

---

## Architecture Layers

The graph assigns every file to exactly one of these 9 layers. Read in this order — each layer builds on what came before.

### 1. Specs & Documentation (86 files)
*Specs, plans, research narrative, ADRs, audit artifacts, and replay-validation bundles. Source-of-truth for "why."*

The binding pre-registration spec — [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](plans/sced-rebaseline-spec-2026-05-13.md) — is the load-bearing artifact in this layer. Every architectural decision downstream traces back to it. Pair with [HVAC_LOGIC.md](HVAC_LOGIC.md) (controller decision flow), [SCHEDULER_TIMING.md](SCHEDULER_TIMING.md) (24-hour activity timeline), [SERVICES.md](SERVICES.md) (per-service operational reference), and [REPLAY_VALIDATION.md](REPLAY_VALIDATION.md) (analysis pre-flight).

### 2. Infrastructure & Deployment (66 files)
*Dockerfiles, docker-compose definitions, Grafana dashboard JSON, Mosquitto/Telegraf config, InfluxDB init, secrets management, and stack-level scripts.*

Entry point: [`deploy/energy-stack/docker-compose.yml`](../deploy/energy-stack/docker-compose.yml) — defines all 16 production services. Operational guide of record: [`deploy/energy-stack/README.md`](../deploy/energy-stack/README.md). Secrets live in [`deploy/energy-stack/secrets/env.sops.env`](../deploy/energy-stack/secrets/env.sops.env) (SOPS-encrypted, age key on Pi-lab).

### 3. Data Ingest Pollers (34 files)
*Per-source telemetry collectors that fetch from external APIs / local devices and write to InfluxDB measurements.*

Representative pollers (each is a separate Docker service):
- `comed_poller/poller.py` — ComEd Hourly Pricing (5-min RTP + hourly avg → `comed.prices`)
- `eagle_poller/poller.py` — EAGLE-3 smart-meter local HTTPS (instantaneous demand + cumulative kWh → `eagle.meter`)
- `pjm_dm2_poller/app.py` — PJM Data Miner 2 per-feed schedules (LMP, load forecast, peak → `pjm.*`)
- `nws_poller/app.py` — NWS forecast (hourly + today/tomorrow/day2 → `nws.forecast`)
- `refoss_poller/poller.py` — Refoss EM16P 18-channel circuit-level (→ `refoss.channel`, `refoss.system`)
- `thermostat_poller/poller.py` — Continuous 10-min Control4 reads of CTK04AE (→ `hvac.indoor`)
- `haven_ingest/app.py` — HAVEN IAQ cloud API (push, 5-min → `haven.indoor`, `haven.outdoor`)
- `ecowitt_ingest/app.py` — Ecowitt GW1200 push (~60s → `ecowitt.weather`)

### 4. HVAC Control Plane (25 files)
*The heart of Arm B. The scheduler decides daily day-types, applies layered control rules, enforces safety, and pushes setpoints to the thermostat.*

- [`deploy/energy-stack/hvac_scheduler/app.py`](../deploy/energy-stack/hvac_scheduler/app.py) — main persistent asyncio loop; day-type classifier; arm-calendar gating; layer-priority resolver
- `price_overlay.py` — Real-time ComEd RTP scarcity-tier overlay (elevated ≥10¢/kWh +3F, scarcity ≥20¢/kWh → 85F effective cool)
- `precool.py` — §7 precool depth/timing modulation; HOT → HOT_STREAK_DAY1 escalation; day-ahead DTOD-modulated precool window
- `pjm_5cp.py` — PJM 5CP-eligibility detector (planning + telemetry only, NOT live setpoint-forcing)
- `safety_supervisor.py` — Wraps every command with hard-bound clamps [cool 65,86 / heat 55,75] + emergency indoor override (>86F → 74F)
- `freshness.py` — **Canonical** per-source staleness classifier (fresh/warn/stale/missing). Hand-paired byte-equal copies live in `tools/cockpit/backend/freshness.py` and `tools/cockpit/frontend/src/freshness.ts` — enforced by [`check-freshness-drift.yml`](../.github/workflows/check-freshness-drift.yml).
- `hvac_scheduler_watchdog/check.py` — Liveness watchdog that writes `hvac.heartbeat` to InfluxDB when the controller stalls past threshold; pairs with Telegram alerts.

Plus the test suite (`test_hvac_scheduler.py`, `test_pjm_5cp.py`, `test_decision_trace.py`, etc.) that locks the contract.

### 5. Cockpit Observability UI (69 files)
*FastAPI backend + React narrative dashboard for live debugging. Distinct from Grafana (which is the dashboards-of-record).*

- [`tools/cockpit/backend/app.py`](../tools/cockpit/backend/app.py) — FastAPI entry point; exposes `/api/snapshot`, `/api/day-ahead`, `/api/today-actions`, `/api/day-at-a-glance`. Routes to fixtures or live assemblers based on `COCKPIT_BACKEND_MODE`.
- `tools/cockpit/backend/snapshot.py` — Live-snapshot assembler. Fuses raw thermostat, price, arm-mode, heartbeat, weather, day-type, scheduler trace, supervisor data into the locked envelope schema.
- `tools/cockpit/frontend/src/narrative/NarrativeCockpit.tsx` — Top-level dashboard composing Hero, DayAtAGlance, ActionLog, DayAheadPanel, WhyThisDecision, DecisionPipeline panels.
- `tools/cockpit/frontend/src/fixtures/*.ts` — 9 canned fixture scenarios (`mild_day`, `fivecp_risk`, `price_spike`, `supervisor_clamp`, `arm_switch`, `controller_down`, `feed_outage`, `shadow_current`, `summer_normal`) used for offline UI development.

> **Cockpit scope boundary:** per [memory](../../.claude/projects/D--Projects-energy-proxy/memory/feedback_cockpit_scope_boundary.md), cockpit work stays inside `tools/cockpit/` — does not touch scheduler, pollers, databases, or Influx schema.

### 6. SCED Analysis Pipeline (91 files)
*Offline analysis turning telemetry into the pre-registered metrics. NOT runtime — runs ad-hoc or in CI for the field study.*

- `tools/analysis/pipeline.py` — DEPRECATED single-file orchestrator for original weekly Stages 1-9 of the pre-registered SCED study. Retained because Stage 7/8 still depend on it while `arm_period_pipeline` takes over per migration plan.
- `tools/analysis/arm_period_pipeline.py` — End-to-end SCED arm-period analysis orchestrator: per-hour modes, weather-vector matching, baseline distribution, statistical analysis.
- `tools/analysis/weather_vector.py` — Builds the 4-component arm-period weather vector per spec §6 (cdd_total, mean_daytime_dewpoint, nocturnal_mean_temp, cooling_hours).
- `tools/analysis/baseline_distribution.py` — Historical weekly weather-vector baseline distribution from ERA5 reanalysis.
- `tools/analysis/replay/manifest.py` — Manifest contract pairing each measurement with parquet + SHA256 integrity hash.
- `tools/analysis/replay/weather_compat.py` — Backward-compat weather replay bundles (IEM/ASOS + Open-Meteo fusion).
- `tools/analysis/run_shadow_validation.py` — Pre-experiment shadow validation CLI per spec §11 #13 (8 pipeline-shape checks).

### 7. Research & Reconciliation Tooling (39 files)
*Ancillary tools: ComEd RTP/DA-LMP spread imputation, PJM data reconciliation, O2 capacity reconstruction, thermal-observer model fitting, n8n workflow SDK, log overrides.*

Highlights:
- `tools/o2_capacity_reconstruction/` — PJM OATT M-2 capacity reconstruction (CPLC formula, scenarios per locked branch-2 denominator)
- `tools/comed_price_imputation/` — ComEd RTP / PJM DA-LMP spread for OSF-lock pipeline
- `tools/n8n/` — n8n SDK + decision-trace-report workflow
- `tools/thermal_observer/` — Thermal model fitting

### 8. Platform & Observability (36 files)
*Telegram notifier, ComEd bill PDF parser, PJM 5CP scrape, arm randomization, decision-trace commissioning, backup, Telegraf MQTT pipeline.*

- `deploy/energy-stack/telegram_notifier/app.py` — Daily 8 AM summary + 5-min alert checker
- `deploy/energy-stack/scripts/parse_comed_bill.py` — Monthly bill PDF reconciliation (see memory: `project_comed_bill_monthly_ingest`)
- `deploy/energy-stack/scripts/scrape_pjm_5cp_pdf.py` — Annual PJM 5CP PDF scrape
- `deploy/energy-stack/scripts/randomize_arms.py` — Arm-assignment randomization for the experiment calendar
- `deploy/energy-stack/scripts/thermal_observer.py` — On-Pi thermal-model fitting service

### 9. CI/CD Pipelines (4 files)
*GitHub Actions: deploy, type-check, shadow-validation, freshness-drift guard.*

- [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) — Canonical deploy. Push to `main` under `deploy/**` → ephemeral `tag:ci` Tailscale node → rsync + `docker compose up -d` on Pi-lab.
- [`.github/workflows/typecheck.yml`](../.github/workflows/typecheck.yml) — Branch-protected mypy strict (job name = "type-check"). Required status check.
- [`.github/workflows/check-freshness-drift.yml`](../.github/workflows/check-freshness-drift.yml) — PR guard diffing the two parallel `freshness.py` modules (scheduler canonical + cockpit backend copy). Fails if drifted.
- [`.github/workflows/shadow-validation.yml`](../.github/workflows/shadow-validation.yml) — Manual-dispatch pre-experiment validation per spec §11 #13. Runs `tools/analysis/run_shadow_validation.py` on the self-hosted pi-lab runner.

---

## Key Concepts

These are the cross-cutting patterns and design decisions that show up repeatedly across the codebase. Internalize these first.

1. **Pre-registration is binding.** Once the spec is filed to OSF (target 2026-05-30), the hypotheses, arm definitions, arm calendar, metric definitions, statistical analysis plan, and decision rules lock at a frozen commit hash. Code changes that touch scheduler logic, telemetry shape, or analysis pipeline are on the critical path until June 1, 2026.

2. **Arm A vs Arm B.** The field study compares two HVAC control regimes:
   - **Arm A** = CTK04AE thermostat's programmed schedule (passive baseline; programmed into the thermostat directly per [`THERMOSTAT_ARM_A_SCHEDULE.md`](THERMOSTAT_ARM_A_SCHEDULE.md))
   - **Arm B** = Active `hvac-scheduler` with RTP/DTOD/5CP-risk-aware rule-based control + safety supervisor
   - Transitions happen Monday per [`ARM_TRANSITIONS.md`](ARM_TRANSITIONS.md). Arm-calendar gating in `hvac_scheduler/app.py` locks Arm B writes to the 2026-06-01..2026-11-16 SCED window.

3. **Layered control.** The scheduler resolves competing pressures through a strict layer-priority order:
   - Layer 0: safety supervisor (hard physical limits)
   - Layer 1: day-type schedule (MILD/NORMAL/HOT_5CP_RISK/HOT_STREAK_DAY1)
   - Layer 2: price overlay (RTP scarcity tier)
   - Layer 3: PJM 5CP planning (telemetry-only per spec §11 #14; does NOT live-force setpoints)

4. **Single source of truth for freshness.** The `freshness.py` classifier exists in three identical copies (scheduler canonical + cockpit backend + cockpit frontend). The CI drift check enforces byte-equality. Never edit one copy without the others.

5. **Replay manifest + reason coding.** Every analysis bundle ships with a `Manifest` (per-measurement parquet path + SHA256) plus reason codes for expected empties (`no_qualifying_days_from_stage3`, etc.). This protects the pre-registered analysis from silent shape drift.

6. **Adapter-boundary enforcement.** `import-linter` contracts enforce that only `influx_adapter` may import `influxdb_client`. New adapter-boundary contracts get added per service as the type-checker rollout proceeds.

7. **Brownfield boundaries.** Before editing any deployed service, read Dockerfile + compose + InfluxDB schema + external API docs + callers + scheduler timing. Spec correctness alone produces production failures (per memory: `feedback_brownfield_production_boundaries`).

---

## Guided Tour

A 10-step walkthrough generated from the graph. Each step picks 2-5 nodes that, together, tell one part of the story.

### Step 1 — Project Framing and Doc Map
**Nodes:** [`README.md`](../README.md) · [`INDEX.md`](../INDEX.md) · [`AGENTS.md`](../AGENTS.md)

Start with `README.md` to learn what energy-proxy is. Then `INDEX.md` (canonical doc map by intent). Then `AGENTS.md` (standing rules every contributor follows, including the binding pre-registration constraint).

### Step 2 — The Binding Pre-Registration Spec
**Nodes:** [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](plans/sced-rebaseline-spec-2026-05-13.md) · [`PROJECT.md`](PROJECT.md)

Every architectural decision downstream traces back to this single document. Once filed to OSF, hypotheses + arm definitions + arm calendar + metric definitions + statistical analysis plan + decision rules freeze at a commit hash. PROJECT.md provides the historical narrative — how the project evolved through 11 phases.

### Step 3 — The Deployment Stack
**Nodes:** [`deploy/energy-stack/docker-compose.yml`](../deploy/energy-stack/docker-compose.yml) · [`docs/SERVICES.md`](SERVICES.md) · [`deploy/energy-stack/README.md`](../deploy/energy-stack/README.md)

16 production services on Pi-lab — InfluxDB 2.7 (single source of truth for telemetry), Grafana (sole visualization), 7 data ingest pollers, HVAC scheduler + watchdog, Telegram notifier, Loki/Promtail. The README is the operational guide of record (GitHub Actions + Tailscale deploy, SOPS secret recovery, ComfortNet MQTT profile).

### Step 4 — The Data Ingest Layer
**Nodes:** [`comed_poller/poller.py`](../deploy/energy-stack/comed_poller/poller.py) · [`eagle_poller/poller.py`](../deploy/energy-stack/eagle_poller/poller.py) · [`pjm_dm2_poller/app.py`](../deploy/energy-stack/pjm_dm2_poller/app.py)

Every decision in the system is downstream of telemetry. `comed_poller` is the real-time price signal that drives the scarcity-tier overlay. `eagle_poller` provides whole-house demand. `pjm_dm2_poller` (highest-fan-in poller) provides LMP, load, forecast, and peak feeds that drive the 5CP classifier. All write typed points to InfluxDB measurements with strict schema contracts.

### Step 5 — The HVAC Scheduler Entry Point
**Nodes:** [`hvac_scheduler/app.py`](../deploy/energy-stack/hvac_scheduler/app.py) · [`docs/HVAC_LOGIC.md`](HVAC_LOGIC.md)

The heart of Arm B. Persistent asyncio loop that decides each day's day-type at 21:00 local from NWS/PJM/ComEd Influx feeds, then pushes COOL_SETPOINT + HOLD_MODE commands to the Honeywell-based CTK04AE thermostat via Control4 Director. Hosts the layer-priority resolver, safety-supervisor wiring, price-overlay state machine, and arm-calendar gating. Read `HVAC_LOGIC.md` alongside the code — controller constants are defined there.

### Step 6 — Scheduler Control Modules
**Nodes:** `price_overlay.py` · `precool.py` · `pjm_5cp.py` · `safety_supervisor.py` · `freshness.py`

The direct dependencies of `hvac_scheduler/app.py` that implement each piece of the control story. `price_overlay.py` (real-time scarcity-tier ≥20¢/kWh → 85F). `precool.py` (when to pre-cool ahead of price spikes). `pjm_5cp.py` (classifies tomorrow's 5CP risk). `safety_supervisor.py` (hard limits on every command). `freshness.py` (classifies upstream data ages — paired with cockpit copies via CI drift check).

### Step 7 — The Watchdog Safety Net
**Nodes:** [`hvac_scheduler_watchdog/check.py`](../deploy/energy-stack/hvac_scheduler_watchdog/check.py) · [`docs/ARM_TRANSITIONS.md`](ARM_TRANSITIONS.md)

Alerts via Telegram when the scheduler's last-decision timestamp falls behind, when freshness inputs degrade, or when arm-calendar transitions don't fire as expected. The field study cannot have undetected gaps in Arm B coverage — that would invalidate the comparison against Arm A.

### Step 8 — The Cockpit Observability UI
**Nodes:** [`tools/cockpit/backend/app.py`](../tools/cockpit/backend/app.py) · [`tools/cockpit/backend/snapshot.py`](../tools/cockpit/backend/snapshot.py) · [`tools/cockpit/frontend/src/narrative/NarrativeCockpit.tsx`](../tools/cockpit/frontend/src/narrative/NarrativeCockpit.tsx)

Grafana is dashboards-of-record; the Cockpit is the narrative observability layer for live debugging. FastAPI backend fuses raw signals into a snapshot DTO; React frontend renders a single-glance pane explaining what the scheduler is doing right now and why. Note the `freshness.py` copy in `cockpit/backend/` is the paired module enforced by the CI drift check.

### Step 9 — The Offline SCED Analysis Pipeline
**Nodes:** [`tools/analysis/pipeline.py`](../tools/analysis/pipeline.py) · [`tools/analysis/replay/manifest.py`](../tools/analysis/replay/manifest.py) · [`docs/REPLAY_VALIDATION.md`](REPLAY_VALIDATION.md)

Production stack produces telemetry; this pipeline turns it into the pre-registered metrics. `pipeline.py` is the 4692-line mega-module running Stages 1-9 (mode classification, weather vector, matching, baseline distribution, arm-period assembly, locked statistical analysis). `manifest.py` makes every analysis run fully reproducible. `REPLAY_VALIDATION.md` formalizes the source-type catalog protecting against silent shape drift.

### Step 10 — CI/CD: Deploy, Validate, Type-Check
**Nodes:** [`deploy.yml`](../.github/workflows/deploy.yml) · [`typecheck.yml`](../.github/workflows/typecheck.yml) · [`check-freshness-drift.yml`](../.github/workflows/check-freshness-drift.yml) · [`shadow-validation.yml`](../.github/workflows/shadow-validation.yml)

How every change gets to Pi-lab and what gates protect production. `deploy.yml` (canonical deploy). `typecheck.yml` (branch-protected mypy strict). `check-freshness-drift.yml` (enforces the single-source-of-truth contract for `freshness.py`). `shadow-validation.yml` (manual-dispatch pre-experiment validation harness).

---

## Complexity Hotspots

Files marked **complex** at the file level. Approach these carefully — many are load-bearing on the pre-registered analysis or the live control loop.

### Critical-path code (production scheduler / pollers)
- `deploy/energy-stack/hvac_scheduler/app.py` — Main scheduler loop (controls thermostat)
- `deploy/energy-stack/hvac_scheduler/pjm_5cp.py` — 5CP-eligibility detector
- `deploy/energy-stack/hvac_scheduler/precool.py` — §7 precool depth/timing modulation
- `deploy/energy-stack/hvac_scheduler_watchdog/check.py` — Liveness watchdog
- `deploy/energy-stack/pjm_dm2_poller/app.py` — Highest-fan-in poller (multi-feed scheduling)

### Critical-path analysis (pre-registered metrics)
- `tools/analysis/pipeline.py` — 4692-line Stages 1-9 orchestrator
- `tools/analysis/arm_period_pipeline.py` — Arm-period analysis end-to-end
- `tools/analysis/weather_vector.py` — 4-component weather vector per spec §6
- `tools/analysis/baseline_distribution.py` — ERA5 baseline distribution
- `tools/analysis/run_shadow_validation.py` — Spec §11 #13 pre-flight CLI
- `tools/analysis/bill_reconciliation.py` — Monthly bill reconciliation (Eagle-primary, Refoss-fallback)
- `tools/analysis/replay/weather_compat.py` — IEM/ASOS + Open-Meteo fusion

### Heavy test suites (read alongside their target code)
- `tools/analysis/tests/test_pipeline.py` — 145 test functions, largest synthetic-data suite
- `tools/analysis/tests/test_stage2_loader_realshape.py` — 52 real-shape tests
- `tools/analysis/tests/test_stage6_loader_realshape.py` — 31 real-shape tests
- `tools/analysis/tests/test_stage8_loader_realshape.py` — 48 real-shape tests (largest)
- `deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py` — Big regression suite
- `deploy/energy-stack/hvac_scheduler/test_pjm_5cp.py` — 5CP state machine

### Cockpit (narrative UI; observability)
- `tools/cockpit/frontend/src/narrative/components/DayAtAGlance.tsx` — 693-line hand-rolled SVG chart with 13 internal components
- `tools/cockpit/frontend/src/narrative/components/WhyThisDecision.tsx` — Decision narrative panel
- `tools/cockpit/frontend/src/types.ts` — Locked snapshot envelope contract
- All 9 `tools/cockpit/frontend/src/fixtures/*.ts` — Snapshot fixtures (each captures a scenario the live UI must handle)

---

## Where to Go Next

| You want to... | Read this |
|---|---|
| Understand what's deployed and how | [`deploy/energy-stack/README.md`](../deploy/energy-stack/README.md) |
| Understand scheduler decisions | [`docs/HVAC_LOGIC.md`](HVAC_LOGIC.md) + `hvac_scheduler/app.py` |
| Understand timing (when things happen) | [`docs/SCHEDULER_TIMING.md`](SCHEDULER_TIMING.md) |
| Understand the field-study design | [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](plans/sced-rebaseline-spec-2026-05-13.md) |
| Understand per-service operational detail | [`docs/SERVICES.md`](SERVICES.md) |
| Add or modify a service | [`AGENTS.md`](../AGENTS.md) "Branching policy" + the "Brownfield boundaries" memory |
| Look at the graph visually | `/understand-anything:understand-dashboard` |
| Ask a targeted question about a file | `/understand-anything:understand-explain <path>` |
| See blast radius of a PR before merging | `/understand-anything:understand-diff` |
| Continue exploring interactively | `/understand-anything:understand-chat <question>` |

The full doc map is at [`INDEX.md`](../INDEX.md).

---

*Generated 2026-05-25 from knowledge-graph commit `ad1f128`. Re-generate after major refactors with `/understand-anything:understand-onboard`. Auto-update is enabled — the post-commit hook keeps the underlying graph in sync.*
