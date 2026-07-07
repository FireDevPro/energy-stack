---
date: 2026-07-07
owner: chris
status: active
role-label: chris
generator: understand-anything (knowledge-graph commit db0bfe7)
---

# Onboarding — energy-proxy

> Generated from the project's interactive knowledge graph
> (`.understand-anything/knowledge-graph.json`, rebuilt 2026-07-07 against the
> post-rev-4 tree). For the visual version, run
> `/understand-anything:understand-dashboard`. For the human-curated doc map,
> see [INDEX.md](../INDEX.md). For the behavior contract every AI session
> follows, see AGENTS.md (local, gitignored).

## Project Overview

**energy-proxy** is a home energy monitoring and **ComEd-RTP-aware HVAC cost
optimization** stack running as a Docker Compose deployment on Pi-lab (a
Raspberry Pi). It aggregates real-time and historical residential energy data
(smart meter, per-circuit power, weather, thermostat state, PJM feeds) into
InfluxDB, and runs a price-aware HVAC controller on top.

**The controller (the protagonist):** the **rev 4.1 spike-only controller**,
live in production since 2026-07-06. At normal prices the thermostat runs its
own onboard program and the controller writes nothing; during ComEd RTP price
spikes it pushes **timed, warm-only holds** (anchored on a live read of the
device's current program value) through the Honeywell TCC cloud, and actively
releases them once prices confirm cheap. Safety is device-owned: the
thermostat's setpoint min/max limits are the hard cap, and the timed-hold TTL
reverts the device to its own schedule if the controller dies — a safety net
independent of the price-driven release path. The binding design is
[docs/superpowers/specs/2026-06-20-commissioning-controller-design.md](superpowers/specs/2026-06-20-commissioning-controller-design.md)
(rev 4.1).

The **Arm A vs Arm B** framing you'll see throughout: Arm A = the thermostat's
own schedule; Arm B = that schedule plus price awareness (the controller). The
Arm-A-vs-Arm-B cost-savings comparison is a deferred **2027** experiment; the
arm apparatus (calendar, `hvac.arm_mode` telemetry, watchdog) is retained for it.

| | |
|---|---|
| **Languages (13)** | python, typescript, markdown, yaml, json, toml, dockerfile, shell, powershell, javascript, flux, css, html |
| **Frameworks** | aiohttp, pydantic, pytest, Docker, Docker Compose, GitHub Actions |
| **Files analyzed** | 270 |
| **Graph** | 621 nodes / 941 edges across 8 layers |

---

## Architecture Layers

The graph assigns every file to exactly one of these 8 layers.

### 1. HVAC Controller (29 nodes)
The rev 4.1 spike-only controller at `deploy/energy-stack/hvac_scheduler/`
(package `controller/`): config loader, price fetch, the tier state machine,
warm-only hold math, the own-hold cleanup record, the TCC device facade,
telemetry, and the tick loop. The one component with write authority over the
physical thermostat.

### 2. Controller Safety Watchdog (5 nodes)
`hvac_scheduler_watchdog/` — a standalone service that verifies the controller
is alive (by watching for recent `hvac.arm_mode` beacons) independent of the
controller process, and flags a stall so a silent death becomes a visible alert.

### 3. Telemetry Ingestion (44 nodes)
The poller/ingest fleet: `comed_poller` (the sole live control input),
`eagle_poller`, `refoss_poller`, `nws_poller`, `pjm_dm2_poller`,
`ecowitt_ingest`, `haven_ingest`, `thermostat_poller`. Each reads one upstream
and writes InfluxDB.

### 4. Observability & Notification (82 nodes)
The `cockpit` dashboard (FastAPI backend + frontend), `telegram_notifier`
(alerts including the controller-down beacon), and the Grafana / Loki /
Promtail visualization and log-shipping stack.

### 5. Data & Messaging Infrastructure (9 nodes)
`influx-init` (bucket + downsampling Flux tasks), `mosquitto` (MQTT broker for
the ComfortNet pipeline), and `telegraf`.

### 6. Analysis & Ops Scripts (32 nodes)
`tools/analysis/` (shadow validation, arm calendar, dollars),
`deploy/energy-stack/scripts/` (ComEd bill ingest, PJM backfill/scrape), and
`tools/decision-trace-report/` (the daily report generator).

### 7. CI/CD & Deploy (14 nodes)
`.github/workflows/` (deploy, typecheck, freshness-drift), `docker-compose.yml`,
per-service Dockerfiles, SOPS secrets config, and backup/restore tooling.
Merging to `main` is the deploy.

### 8. Documentation (57 nodes)
`docs/` (spec, plan, PROJECT, SERVICES, archive), the experiment arm calendar,
root README/INDEX, and the committed knowledge graph.

---

## Guided Tour

Read in this order — it follows the controller's own data flow, from mission to
merge. (Same 12 steps as the interactive dashboard tour.)

**1. What this project is** — `README.md`, `docs/PROJECT.md`, `INDEX.md`. The
mission (monitor energy, shave HVAC cost against 5-minute ComEd prices), the
Arm A / Arm B design, the phase-by-phase build history, and the hand-curated
doc map.

**2. The binding controller design** —
`docs/superpowers/specs/2026-06-20-commissioning-controller-design.md`. Read the
contract before the code: the thermostat's schedule stays in command; the
controller pushes timed warm-only holds only during spikes. Defines the program
spine, reactive core, feed-gap behavior, device-owned safety, and `temp_scale`
units.

**3. Entry point & the tick loop** — `controller/__main__.py` →
`controller/loop.py`. `python -m hvac_scheduler.controller` loads config, builds
the Influx price source + TCC device adapter + telemetry sink, and hands them to
`ControllerLoop`. Each tick: fetch price → evaluate tier → snapshot device →
decide the hold → push/release (production mode only) → persist the own-hold
record → emit telemetry.

**4. Config: the experimental surface** — `controller/config.py`. Every tunable
is required (no silent code defaults); temperatures must land on the
`temp_scale` grid; the yaml `temp_scale` must match the env; `config_id` is the
sha256 of the file bytes so every telemetry row traces to the exact config.

**5. The price signal** — `controller/pricing.py`. The sole live control input:
the latest 5-minute ComEd RTP bucket from InfluxDB, gated `fresh-strict`
(≤ 720 s, calibrated to ComEd's publish-lag jitter). The 7-minute freshness
label is display-only and never gates control; stale price means stand down.

**6. The tier state machine** — `controller/tiers.py`. Normal / elevated /
scarcity, **no time locks**: engage on one fresh bucket, release only after a
confirm-count of distinct cheap buckets (hysteresis-adjusted), hard-release on
the stale backstop. Fast to react, slow to relax — the anti-flap asymmetry.

**7. Warm-only holds & zombie cleanup** — `controller/holds.py`,
`controller/ownhold.py`. `holds.py` is pure math: warm-only target,
quarter-hour-floored expiry, the `decide()` lifecycle that respects manual holds
and never drifts cooler. `ownhold.py` persists the last-pushed hold so a
restarted controller cleans up only its OWN expired hold (spec Safety #3).

**8. The thermostat seam** — `controller/device.py`,
`hvac_scheduler/tcc_client.py`. A synchronous snapshot/push/release facade over
the async Honeywell TCC client (`aiosomecomfort`). Timed holds — never Permanent
— are the safety mechanism: a dead controller's hold lapses and the device
resumes its onboard schedule.

**9. Telemetry out** — `controller/telemetry.py`. Four streams via the
whitelisted `influx_adapter` seam: `hvac.actions` (what was pushed),
`hvac.price_overlay` (tier transitions), `hvac.arm_mode` (liveness beacon), and
decision-trace JSON lines. The `arm_mode` row is what the watchdog listens for.

**10. Safety out-of-band: the watchdog** — `hvac_scheduler_watchdog/check.py`.
A separate service that never touches the thermostat: it checks InfluxDB for a
recent `hvac.arm_mode` beacon and, if none, writes an `hvac.heartbeat` stall
flag — turning a silent controller death into the `telegram_notifier`
controller-down alert (live-fire tested 2026-07-07, 11-minute detection).

**11. How data arrives** — `comed_poller/poller.py`. The source of the sole live
control input: a wall-clock-aligned loop fetching ComEd's current-hour-average
and latest 5-minute prices into InfluxDB — the same bucket step 5 reads. Every
ingest service follows this pattern: poll, normalize, write to Influx.

**12. Observed & shipped** — `telegram_notifier/app.py`, `cockpit/backend/app.py`,
`docker-compose.yml`, `.github/workflows/deploy.yml`. Deduplicated alerting +
daily summary; the FastAPI dashboard; the compose fleet; and the deploy —
merging to `main` joins an ephemeral CI runner to the tailnet, rsyncs the stack
over Tailscale SSH, rebuilds, and smoke-tests.

---

*Regenerate this doc after a major refactor via
`/understand-anything:understand-onboard` once the knowledge graph is rebuilt.*
