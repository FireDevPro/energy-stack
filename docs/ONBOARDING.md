---
date: 2026-10-21
owner: chris
status: active
role-label: chris
generator: understand-anything (knowledge-graph commit 61a210f, meta 9755028)
---

# Onboarding — energy-proxy

> Generated from the project's interactive knowledge graph
> (`.understand-anything/knowledge-graph.json`, incremental rebuild 2026-10-20
> against main `61a210f`). For the visual version, run
> `/understand-anything:understand-dashboard`. For the human-curated doc map,
> see [INDEX.md](../INDEX.md). For the behavior contract every AI session
> follows, see AGENTS.md (local, gitignored).

## Project Overview

**energy-proxy** is a home energy monitoring and **ComEd-RTP-aware HVAC cost
optimization** stack running as a Docker Compose deployment on Pi-lab (a
Raspberry Pi). Pollers pull smart-meter, per-circuit power, weather, air
quality, thermostat state, and ComEd/PJM price data into InfluxDB, and a
price-aware HVAC controller runs on top.

**The controller (the protagonist):** the **rev 4.1 spike-only controller**,
live in production since 2026-07-06. At normal prices the thermostat runs its
own onboard program and the controller writes nothing. During ComEd RTP price
spikes it pushes **timed, warm-only holds** through the Honeywell TCC cloud and
actively releases them once prices confirm cheap. Safety is device-owned: the
thermostat's setpoint min/max limits are the hard cap, and the timed-hold TTL
reverts the device to its own schedule if the controller dies. The binding
design is
[docs/superpowers/specs/2026-06-20-commissioning-controller-design.md](superpowers/specs/2026-06-20-commissioning-controller-design.md).

The **Arm A vs Arm B** framing you'll see throughout: Arm A = the thermostat's
own schedule; Arm B = that schedule plus price awareness (the controller). The
cost-savings comparison between them is a deferred **2027** experiment; the arm
apparatus (calendar, `hvac.arm_mode` telemetry, watchdog) is retained for it.

| | |
|---|---|
| **Languages** | python, yaml, markdown, json, toml, dockerfile, shell, powershell, flux, html |
| **Frameworks** | aiohttp, pydantic, pytest, Docker, Docker Compose, GitHub Actions |
| **Files analyzed** | 240 |
| **Graph** | 499 nodes / 821 edges across 8 layers |

---

## Architecture Layers

The graph assigns every file to exactly one of these 8 layers.

### 1. HVAC Controller (30 nodes)
The rev 4.1 controller at `deploy/energy-stack/hvac_scheduler/` (package
`controller/`): config loader, price fetch, tier state machine, warm-only hold
math, own-hold cleanup record, TCC device facade, telemetry, error boundary,
and the tick loop. The one component with write authority over the physical
thermostat.

### 2. Controller Safety Watchdog (5 nodes)
`hvac_scheduler_watchdog/`: a standalone service that checks for recent
`hvac.arm_mode` beacons independent of the controller process and flags a stall,
so a silent death becomes a visible alert.

### 3. Telemetry Ingestion (52 nodes)
The poller/ingest fleet: `comed_poller` (the sole live control input),
`eagle_poller`, `refoss_poller`, `nws_poller`, `pjm_dm2_poller` (5CP telemetry,
not a control input), `ecowitt_ingest`, `haven_ingest`, `thermostat_poller`,
`purpleair_poller`, `airnow_poller`. Each reads one upstream and writes InfluxDB.

### 4. Observability & Notification (34 nodes)
**The Vigil** cockpit (`cockpit/`: FastAPI backend + one self-contained
`frontend/index.html`, InfluxDB only), the notifier (`telegram_notifier/`, which
since 2026-10-20 posts every alert to the Chief's alert intake on pi-lab:4014
rather than Telegram directly), and the Grafana / Loki / Promtail configs.
Grafana is profile-gated and stopped since 2026-09-13; Loki is a debug log store
read on demand, with no service depending on it.

### 5. Data & Messaging Infrastructure (9 nodes)
`influx-init` (long-term bucket + downsampling Flux task), `mosquitto` and
`mosquitto-init` (TLS MQTT broker for the ComfortNet pipeline), and `telegraf`.

### 6. Analysis & Ops Scripts (33 nodes)
`deploy/energy-stack/scripts/` (ComEd bill parser, PJM backfill and 5CP scraper,
gray-box thermal observer) and `tools/analysis/` (arm calendar, HVAC dollars,
mode classification, shadow validation). The daily decision-trace report tool
is dormant (last report 2026-06-19).

### 7. CI/CD & Deploy (14 nodes)
`.github/workflows/` (deploy, typecheck, freshness-drift), `docker-compose.yml`,
`.env.example`, SOPS secrets config, test/typecheck runners, and backup tooling.
Merging a `deploy/**` change to `main` is the deploy.

### 8. Documentation (63 nodes)
`docs/` (spec, plans, PROJECT, SERVICES, archive), the experiment arm calendar,
root README/INDEX, `pyproject.toml`, and the committed knowledge graph.

---

## Key Concepts

- **Spike-only control.** Normal prices: no writes, the thermostat's program
  rules. Spikes: timed warm-only holds. Release after confirmed cheap buckets.
- **Warm-only, never cooler.** The controller can only drift the setpoint up
  from the Arm A baseline, and respects any manual hold a human placed.
- **Device-owned safety.** Timed holds (never Permanent) lapse on their own if
  the controller dies; setpoint min/max on the device is the hard cap. No
  software supervisor.
- **Config is the experimental surface.** Every tunable lives in
  `commissioning-controller.yaml` and is required (no code defaults); code owns
  invariants. `config_id` (sha256 of the file) tags every telemetry row.
- **Fresh-strict pricing.** A 5-minute ComEd price older than 720 s is refused;
  stale price means stand down. The 7-minute freshness label is display-only.
- **No time locks, asymmetric tiers.** Engage on one fresh bucket; release only
  after a confirm-count of distinct cheap buckets. Fast to react, slow to relax.
- **Liveness beacon first.** `hvac.arm_mode` is written before the device read,
  so liveness survives a thermostat outage. `hvac.heartbeat` is an inverted
  DOWN beacon: empty means healthy.
- **Failure telemetry boundary.** `hvac.device_status` records read/write/crash
  attempts; an `InfrastructureError` (Influx or disk down) is not a crash.
- **Adapter seams, enforced.** Only `tcc_client` imports `aiosomecomfort`; only
  `influx_adapter` imports `influxdb_client`. import-linter enforces it in CI
  (`run_typecheck.sh`), which pytest does not catch.
- **Hand-paired duplicates.** `freshness.py` exists in both `hvac_scheduler/`
  and `cockpit/backend/`, kept byte-identical by a CI drift check;
  `tcc_client.py` is copied verbatim into `thermostat_poller/`.
- **The Vigil reads the controller's own config** via a read-only mount, so
  cockpit tier thresholds can never drift from the controller's.

---

## Guided Tour

Read in this order. It follows the controller's own data flow, from mission to
merge (same 12 steps as the interactive dashboard tour).

**1. What this project is**: `README.md`, `docs/PROJECT.md`, `INDEX.md`. The
mission, the Arm A / Arm B design, the phase-by-phase build history, and the
hand-curated doc map.

**2. The binding controller design**:
`docs/superpowers/specs/2026-06-20-commissioning-controller-design.md`. Read the
contract before the code: the thermostat's schedule stays in command; the
controller pushes timed warm-only holds only during spikes. Defines the program
spine, reactive core, feed-gap behavior, device-owned safety, and `temp_scale`
units.

**3. Entry point & the tick loop**: `controller/__main__.py` →
`controller/loop.py`. `python -m hvac_scheduler.controller` loads config, builds
the Influx price source, TCC device adapter and telemetry sink, and hands them
to `ControllerLoop`. Each tick: write liveness beacon → fetch price → evaluate
tier → snapshot device → decide the hold → push/release (production mode only)
→ persist the own-hold record → emit telemetry.

**4. Config: the experimental surface**: `controller/config.py`. Every tunable
is required; temperatures must land on the `temp_scale` grid (0.5 C / 1.0 F);
the yaml `temp_scale` must match the `TEMP_SCALE` env; `config_id` is the
sha256 of the file bytes.

**5. The price signal**: `controller/pricing.py`. The latest 5-minute ComEd RTP
bucket from InfluxDB, gated fresh-strict (≤ 720 s, calibrated to ComEd's
publish-lag jitter).

**6. The tier state machine**: `controller/tiers.py`. Normal / elevated /
scarcity with no time locks; engage fast, release on confirmation, hard-release
on the stale backstop.

**7. Warm-only holds & zombie cleanup**: `controller/holds.py`,
`controller/ownhold.py`. Pure hold math (warm-only target, quarter-hour-floored
expiry, `decide()` lifecycle), plus an on-disk record so a restarted controller
cleans up only its OWN expired hold (spec Safety #3).

**8. The thermostat seam**: `controller/device.py`,
`hvac_scheduler/tcc_client.py`. A synchronous snapshot/push/release facade over
the async Honeywell TCC client, one network refresh per snapshot.

**9. Telemetry out**: `controller/telemetry.py`, `controller/errors.py`. Five
streams through the `influx_adapter` seam: `hvac.actions`, `hvac.price_overlay`,
`hvac.arm_mode`, `hvac.device_status`, and decision-trace JSON lines on stdout.
`errors.py` separates infrastructure failures from controller crashes.

**10. Safety out-of-band: the watchdog**: `hvac_scheduler_watchdog/check.py`.
Never touches the thermostat; if no recent `hvac.arm_mode` row exists, writes an
`hvac.heartbeat` stall flag that the notifier turns into a controller-down alert.

**11. How data arrives**: `comed_poller/poller.py`. A wall-clock-aligned loop
writing ComEd's hour-average and 5-minute prices into the bucket step 5 reads.
Every ingest service follows this pattern: poll, normalize, write to Influx.

**12. Observed & shipped**: `telegram_notifier/app.py`, `cockpit/backend/app.py`,
`cockpit/backend/vigil_now.py`, `docker-compose.yml`,
`.github/workflows/deploy.yml`. Deduplicated, episode-based alerting to the
Chief's intake; The Vigil's three read-only `/api/vigil/*` polls; the compose
fleet; and the deploy, which joins an ephemeral CI runner to the tailnet,
rsyncs the stack over Tailscale SSH, rebuilds, and smoke-tests.

---

## File Map

Key files by layer (tests, Dockerfiles and empty `__init__.py` omitted).

### HVAC Controller — `deploy/energy-stack/hvac_scheduler/`
| File | Role |
|---|---|
| `controller/__main__.py` | Production entrypoint; wires config, price source, TCC adapter, telemetry |
| `controller/loop.py` | `ControllerLoop.tick`: the per-tick orchestration |
| `controller/config.py` | Strict YAML loader; grid and scale invariants; `config_id` |
| `controller/pricing.py` | Latest 5-min ComEd bucket + fresh-strict rule |
| `controller/tiers.py` | Normal/elevated/scarcity machine with confirm-count release |
| `controller/holds.py` | Pure warm-only hold math and `decide()` |
| `controller/ownhold.py` | On-disk record of the controller's own last hold |
| `controller/device.py` | Sync facade over the TCC climate seam |
| `controller/telemetry.py` | Five telemetry streams |
| `controller/errors.py` | `InfrastructureError` boundary |
| `tcc_client.py` | Honeywell TCC adapter (sole `aiosomecomfort` import) |
| `influx_adapter.py` | Typed Influx surface (sole `influxdb_client` import) |
| `freshness.py` | Canonical staleness thresholds (paired with cockpit copy) |
| `arm_calendar.py` | 2027 A/B arm calendar, DST-correct |
| `commissioning-controller.yaml` | Live config: tiers (10¢ elevated / 20¢ scarcity), offsets, TTL |
| `test_rev4_acceptance.py` | Feature-level acceptance test (full spike story) |

### Controller Safety Watchdog
| File | Role |
|---|---|
| `hvac_scheduler_watchdog/check.py` | `hvac.arm_mode` recency check → `hvac.heartbeat` stall flag |

### Telemetry Ingestion — `deploy/energy-stack/`
| File | Role |
|---|---|
| `comed_poller/poller.py` | ComEd RTP prices (sole live control input) |
| `eagle_poller/poller.py` | Rainforest EAGLE-3 whole-home meter, local XML API |
| `refoss_poller/poller.py` | Refoss EM16P per-circuit power, JSON-RPC |
| `nws_poller/app.py` | NWS gridded forecast + alerts, daily rollups |
| `pjm_dm2_poller/app.py` | PJM Data Miner 2 LMPs, load, peaks, NSPL (5CP telemetry) |
| `ecowitt_ingest/app.py` | Ecowitt GW1200 push receiver; canonical outdoor channel |
| `haven_ingest/app.py` | HAVEN Pro indoor air quality via OAuth refresh token |
| `thermostat_poller/poller.py` | CTK04 state via TCC, one-retry on timeout |
| `purpleair_poller/app.py` | PurpleAir PM2.5 with EPA smoke correction |
| `airnow_poller/app.py` | EPA AirNow official AQI |

### Observability & Notification — `deploy/energy-stack/`
| File | Role |
|---|---|
| `cockpit/backend/app.py` | The Vigil FastAPI app: `/api/health`, `/api/vigil/{now,timeline,events}` |
| `cockpit/backend/vigil_queries.py` | Flux builders over rev-4 measurements (pre-filter `_field`) |
| `cockpit/backend/vigil_derive.py` | Pure derivations: units, tiers, liveness, episodes, avoided cost |
| `cockpit/backend/vigil_now.py` / `vigil_timeline.py` / `vigil_events.py` | Endpoint assemblers |
| `cockpit/backend/vigil_config.py` | Reads tier thresholds from the controller's YAML |
| `cockpit/frontend/index.html` | The wall board itself, no build step |
| `telegram_notifier/app.py` | Alert loop + daily summary, posted to the Chief's intake |
| `loki/loki-config.yml`, `promtail/promtail-config.yml` | Debug log store and shipper |

### Data & Messaging Infrastructure — `deploy/energy-stack/`
| File | Role |
|---|---|
| `influx-init/apply.sh` | Idempotent bucket + task provisioning |
| `influx-init/tasks/downsample-energy-1m.flux` | Raw → 1-minute long-term rollup |
| `mosquitto/mosquitto.conf`, `mosquitto/acls` | TLS broker, per-topic ACLs |
| `telegraf/telegraf.conf` | MQTT → Influx routing for ComfortNet |

### Analysis & Ops Scripts
| File | Role |
|---|---|
| `deploy/energy-stack/scripts/parse_comed_bill.py` (+ `comed_parser.py`, `comed_influx.py`) | Monthly ComEd bill PDF ingest |
| `deploy/energy-stack/scripts/thermal_observer.py` (+ `_influx`, `fit_thermal_observer.py`) | Gray-box house thermal model fit |
| `deploy/energy-stack/scripts/backfill_pjm.py` | 5-year PJM history backfill |
| `deploy/energy-stack/scripts/scrape_pjm_5cp_pdf.py` | Annual PJM 5CP peaks scraper |
| `tools/analysis/run_shadow_validation.py` | Pipeline-shape validation report |
| `tools/analysis/hvac_dollars.py`, `mode_classification.py` | 2027 analysis apparatus |

### CI/CD & Deploy
| File | Role |
|---|---|
| `.github/workflows/deploy.yml` | Merge-to-main deploy over Tailscale SSH |
| `.github/workflows/typecheck.yml` | mypy + import-linter gate |
| `.github/workflows/check-freshness-drift.yml` | Fails if the two `freshness.py` copies drift |
| `deploy/energy-stack/docker-compose.yml` | The whole fleet (profiles: `grafana`, `mqtt`) |
| `deploy/energy-stack/run_tests.sh` | Per-service pytest runner (avoids `app.py` collisions) |
| `deploy/energy-stack/run_typecheck.sh` | Per-service mypy + import contracts |
| `deploy/energy-stack/secrets/env.sops.env` | Encrypted recovery copy of the Pi-only `.env` |
| `deploy/energy-stack/backup/pi-backup.sh` | Pi-lab backup |

---

## Complexity Hotspots

Files the graph rates **complex** (non-test). Read the tests alongside them.

| File | Why to tread carefully |
|---|---|
| `hvac_scheduler/controller/loop.py` | The only code that writes to the thermostat; ordering of beacon, read, decide, write and telemetry is load-bearing. Covered by `test_rev4_loop.py` and `test_rev4_acceptance.py`. |
| `docker-compose.yml` | Every service, profile, mount and healthcheck; a `deploy/**` merge can recreate all built containers. |
| `.github/workflows/deploy.yml` | Production deploy path; Tailscale auth and rsync exclude list (`.env` is excluded). |
| `telegram_notifier/app.py` | Many alert families, episode tracking, pending-until-accepted delivery, mute prefixes. |
| `cockpit/backend/vigil_queries.py` | Flux schema traps: `group()` on multi-field data collides unless `_field` is pre-filtered. |
| `cockpit/frontend/index.html` | Whole board in one file; tune layout on the real wall kiosk, not Playwright. |
| `pjm_dm2_poller/app.py` | Per-feed schedules, EPT vs Chicago windows, zone codes (111-test suite). |
| `eagle_poller/poller.py`, `refoss_poller/poller.py` | LAN device protocols with quirky payloads. |
| `ecowitt_ingest/app.py`, `haven_ingest/app.py`, `nws_poller/app.py` | Vendor payload parsing, OAuth token rotation, ISO-interval expansion. |
| `scripts/thermal_observer.py`, `scripts/comed_parser.py` | Model fitting and regex bill parsing against real fixtures. |
| `scripts/backfill_pjm.py`, `scripts/scrape_pjm_5cp_pdf.py`, `tools/analysis/run_shadow_validation.py` | One-shot/annual tools with DST and hour-ending conversions. |

---

*Regenerate this doc after a major refactor via
`/understand-anything:understand-onboard` once the knowledge graph is rebuilt.*
