# Home Energy Monitoring System — Project Document

**Project**: `D:\Projects\energy-proxy`
**Started**: 2025
**Last Updated**: May 2026

---

## Purpose

Gain full visibility into the home's most significant ongoing operational cost — electricity — by aggregating real-time energy consumption, utility pricing, and environmental context into a unified monitoring system with historical data retention.

The goal is not just "what am I using right now" but "what did it cost, why, and what can I change."

### Research trajectory

Starting summer 2026, the same instrumentation runs a pre-registered single-case experimental design (SCED) field study comparing a baseline rule-based controller against a thermal-model-informed controller, with the explicit goal of producing a publishable peer-reviewed contribution. The design directly addresses the methodological gap flagged in [Khabbazi et al. 2025](https://arxiv.org/abs/2503.05022) — that 71% of reviewed residential HVAC field studies are confounded by pre/post sequential design, and the field's most consistent unmet need is long-duration randomized within-subject comparisons. We adopt SCED randomization-test methodology (Heyvaert & Onghena 2014) explicitly.

What this means for the project shape:

- **Pre-registration is binding**. Once filed to OSF, hypotheses, arm definitions, randomization seed, metric definitions, statistical analysis plan, and decision rules are locked at a frozen commit hash. See [`docs/EXPERIMENT_DESIGN.md`](docs/EXPERIMENT_DESIGN.md).
- **Both arms (Arm A baseline RBC and Arm B model-informed) are owned in this repo**. The thermal model methodology is grounded in [Bacher & Madsen 2011](https://doi.org/10.1016/j.enbuild.2011.02.005); design in [`docs/THERMAL_MODEL_DESIGN.md`](docs/THERMAL_MODEL_DESIGN.md).
- **Open data and open code at submission time**. Telemetry published as Apache Parquet on Zenodo (CC BY 4.0), with [Brick Schema](https://brickschema.org/) JSON-LD metadata. Repo tagged at the OSF commit hash; Zenodo issues a citable code DOI.
- **Negative or null results are publishable**. The decision rule explicitly commits to publishing a negative result if the model-informed controller fails to outperform baseline. Not gating publication on a confirmatory outcome.
- **N=1, single-occupant household, IECC climate zone 5A**. Generalization claims are bounded; the contribution is methodological as much as substantive.

Practical implication for in-flight work: anything touching the scheduler, thermal model, or telemetry between now and OSF filing is on the critical path for the June 1 experiment start. Operational features that don't directly serve Arm A / Arm B / observability are parked.

---

## What Has Been Built

### Phase 1 — ComEd Pricing Dashboard (complete)

A Python async proxy server (`energy_proxy.py`) that polls the ComEd Hourly Pricing API every 60 seconds, capturing 5-minute real-time market rate intervals and current hour averages. Served a custom HTML dashboard at `localhost:8099`.

### Phase 1.5 — Sense Energy Monitor Integration (retired April 2026)

Extended the proxy to poll the Sense Energy Monitor cloud API every 60 seconds via the `sense_energy` Python library. Captured whole-home active power (W), voltage, frequency, per-device power breakdown (ML-detected), and daily/weekly/monthly usage (kWh). **Retired in Phase 5** — the Sense hardware was removed and replaced with the Refoss EM16P (per-circuit local monitoring).

### Phase 3.1 — InfluxDB + Grafana Storage/Visualization Layer (complete, April 2026)

Deployed InfluxDB 2.7 + Grafana 11.4.0 as the `energy-stack` Docker compose project on Pi-lab (192.168.20.10). Compose artifacts and Grafana data source provisioning authored at `deploy/energy-stack/`, synced to `~/energy-stack/` on Pi-lab. The `energy` bucket is provisioned with infinite retention; Grafana has InfluxDB configured as the default read-only data source. Persistence verified via `compose down && up -d` cycle. No data flowing yet — collector refactor is Phase 3.2. See `deploy/energy-stack/README.md` for operations.

### Phase 2 — EAGLE-3 Smart Meter Integration (connected & tested, not yet in proxy)

Rainforest EAGLE-3 gateway connected to the ComEd smart meter via Zigbee HAN. Provides billing-grade ground truth for instantaneous demand (kW) and cumulative energy delivered (kWh), read directly from the utility meter.

**Connection details (tested and working April 2026):**

- IP: 192.168.20.192 (homelab VLAN, Ethernet)
- Port: 443 (HTTPS, self-signed cert)
- Endpoint: POST to `/cgi-bin/post_manager`
- Auth: HTTP Basic (Cloud ID + Install Code)
- API: EAGLE-200/EAGLE-3 Local API (NOT the old REST/cloud API)
- Meter HardwareAddress: `0x001350050037ac6b`
- Command format: XML with `<Name>` tags, using `device_query` to read variables
- Values returned as pre-computed ASCII strings with units (e.g., "1.602 kW", "78967.773 kWh")

**Available meter variables:**
- `zigbee:InstantaneousDemand` — real-time power draw (kW)
- `zigbee:CurrentSummationDelivered` — cumulative energy to home (kWh)
- `zigbee:CurrentSummationReceived` — cumulative energy from home (kWh, 0.0 = no solar)
- `zigbee:Multiplier` / `zigbee:Divisor` — scaling factors (already applied in Value strings)
- `zigbee:Price`, `zigbee:PriceTrailingDigits`, `zigbee:PriceCurrency`, `zigbee:PriceTier`
- Block pricing, billing period, and messaging variables also available

---

## Current Architecture

```
EAGLE-3 (local HTTPS, 30s) ─────────┐
ComEd Pricing API (public, 60s) ────┤
Refoss EM16P (local HTTP, 30s) ─────┤   Pi-lab (192.168.20.10)
NWS forecast (public, 30min) ───────┼──► energy-stack compose ──► InfluxDB ──► Grafana
PJM Data Miner 2 (public, hourly) ──┤   (eagle / comed / refoss /     (storage)    (dashboards)
HAVEN IAQ API (cloud, 5min) ────────┤    nws / pjm-dm2 / haven /
Control4 → CTK04AE (10min) ─────────┘    thermostat / hvac pollers)
```

All pollers run as containers in the `energy-stack` Docker compose project on
Pi-lab. Each is a small Python service that calls one upstream and writes to
InfluxDB. The `refoss-poller` (Phase 5) replaced `sense-poller` (retired Phase 1.5).
The thermostat path uses Control4 EA-5 (`192.168.1.30`) → Cinegration C4 driver →
TCC cloud, not direct TCC API. The Windows-side `energy_proxy.py` and custom HTML
dashboard are gone — Grafana is the sole visualization layer. State is durable:
InfluxDB on a persistent volume, with a separate `energy-longterm` bucket for 1-min
downsampled history.

---

## Decisions Made

### Architecture: InfluxDB + Grafana (replaces custom dashboard)

**Decision**: Move from the custom HTML dashboard to Grafana for all visualization, backed by InfluxDB for time-series storage.

**Rationale**:
- The custom dashboard is real-time only with no persistence; adding historical views would mean reimplementing what Grafana already does
- InfluxDB is purpose-built for metering/IoT time-series data; natural fit for energy queries (total kWh, average cost/hour, peak demand) vs Prometheus which is optimized for infrastructure monitoring
- Grafana handles multi-source correlation natively — overlay pricing, demand, device power, and temperature on the same time axis
- The proxy simplifies to a pure data collector/writer — no more serving HTML or maintaining API endpoints
- Single visualization layer instead of maintaining two interfaces

**Prometheus was considered** but InfluxDB was chosen because:
- Push-based (proxy writes directly) vs pull-based (would need `/metrics` endpoint)
- InfluxQL/Flux queries are more natural for energy analysis than PromQL
- Built-in retention policies and downsampling
- Better fit for event-style metering data vs infrastructure health checks

### Energy Monitor: Refoss EM16P replaces Sense (April 2026)

**Decision**: Replaced the Sense Energy Monitor with a Refoss EM16P. Sense hardware removed; Refoss installed and polling locally on `192.168.20.140`.

**Rationale**:
- Sense used an unofficial cloud API that could break at any time; Sense stopped selling consumer monitors December 2025
- Refoss EM16P offers fully local operation — JSON-RPC 2.0 over HTTP (single `Refoss.Status.Get` call returns all 18 channels per cycle), no cloud dependency, cloud disabled in device config
- Per-circuit CT clamp monitoring (2 mains + 16 branch, up to 60A each) vs Sense's ML-based device detection
- Deterministic per-circuit data (you know exactly what's on each breaker) vs probabilistic device identification
- Native Home Assistant support; vendor-maintained `aiorefoss` Python library available (we use raw `requests` instead since auth is off)

**Implementation note**: Auth is disabled on the device (open on the homelab VLAN, consistent with the project threat model: local-only, single-user). The `refoss-poller` container caches user-assigned channel labels via `Refoss.Config.Get` and refreshes the cache automatically when the device's `sys.cfg_rev` increments — renaming a circuit in the Refoss app propagates within one poll cycle. Per-channel and system metrics land in `refoss.channel` and `refoss.system` measurements; mains aggregates are computed in Grafana from the two split-phase main channels (`em:1` + `em:7`) since the device's `emmerge:N` rollups return empty objects on this firmware.

### Thermostat Data: Honeywell TCC Cloud API (originally planned, superseded by Phase 4)

> **Status**: superseded. The plan in this section was not what shipped. Phase 4 (May 2026) implemented thermostat integration via Control4 EA-5 → Cinegration C4 driver → TCC cloud → RedLINK gateway, not direct TCC polling. See Phase 4 entry in the Development Roadmap below for the actual architecture; this section is retained for historical context on why direct-TCC was originally considered.

**Decision (originally)**: Poll the Honeywell Total Connect Comfort API every 10 minutes for context data.

**Rationale (originally)**:
- The home uses an Amana CTK04AE (Honeywell-OEM whitelabel with native RedLINK) on the TCC app
- RedLINK has no local API — the gateway is encrypted cloud-only (HTTPS to alarmnet.com, no local endpoints, proprietary 900MHz RF)
- The TCC cloud API (via `pyhtcc` or `aiosomecomfort` libraries) provides indoor temp, outdoor temp, humidity, setpoints, equipment status, and system mode
- 10-minute polling interval is the community-tested safe minimum to avoid Honeywell rate limiting (403/500 errors with no graceful backoff)
- This data is context, not metering — it answers "why is the HVAC running" not "how much is it using"
- The Resideo Developer API (api.honeywellhome.com/v2, OAuth2) is also available as a cleaner alternative; requires developer registration

**Three API paths originally identified**:
1. Legacy SOAP: `rs.alarmnet.com/TotalConnectComfort/ws/MobileV2.asmx` (AppID from iOS app, may be retired)
2. Web portal scraping: `mytotalconnectcomfort.com/portal` via `pyhtcc`/`aiosomecomfort` (battle-tested by Home Assistant community, ~3,200 active installations)
3. Official REST: `api.honeywellhome.com/v2` (OAuth2, documented, requires developer registration)

**Why this got superseded**: a Control4 EA-5 controller already ran in the house with the Cinegration TCC bridge driver installed. Routing through Control4 was simpler (no separate TCC creds for the stack to manage), more reliable (local Director access without internet round-trip per setpoint push), and let `hvac-scheduler` reuse the same automation infrastructure already maintained for other home automation. Direct TCC remains a fallback option if Control4 ever loses access.

---

## Target Architecture

The collector layer matches "Current Architecture" above. Phase 3.4 dashboards, Phase 4 thermostat integration, Phase 8 bill ingest, Phase 9 PJM DM2 poller, and Phase 10 safety supervisor all landed by May 2026 (see Development Roadmap below). Active follow-on work: Arm B (model-informed scheduler) ahead of June 1 experiment start. ComfortNet pipeline (broker + telegraf + Pi-3B publisher) is live as of May 6 2026 and `hvac.comfortnet` is flowing — see [`docs/COMFORTNET_USE_CASES.md`](docs/COMFORTNET_USE_CASES.md) for downstream consumption and [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet) for the live implementation.

**Grafana dashboards will provide:**
- Real-time "right now" view (10-30s auto-refresh): current demand, current rate, current cost/hour, top circuits by power
- Daily/weekly/monthly historical views: total kWh, total cost, demand curves, pricing trends
- HVAC correlation panels: cost vs outdoor temperature, runtime vs setpoint delta, equipment status timeline
- Data source comparison: EAGLE (billing truth) vs Refoss (per-circuit detail)

---

## Development Roadmap

### Phase 3.1 — InfluxDB + Grafana Infrastructure ✅ (April 2026)
Complete. InfluxDB 2.7 + Grafana 11.4.0 running as the `energy-stack` compose project on Pi-lab. `energy` bucket ready, data source provisioned, persistence verified. Artifacts at `deploy/energy-stack/`.

### Phase 3.2 — ComEd + Sense Pollers ✅ (April 2026)
Complete. Replaced the old monolithic `energy_proxy.py` with two independent
containerized pollers in the `energy-stack` compose project, matching the
Phase 3.3 EAGLE-3 pattern: `comed-poller` (60 s, public ComEd Hourly Pricing
API, measurement `comed.prices`) and `sense-poller` (60 s realtime + 300 s
trend, `sense_energy` library, measurements `sense.realtime`, `sense.device`,
`sense.trend`). The old `energy_proxy.py` on Windows became dead code and
was deleted in Phase 5 cleanup. The `sense-poller` itself was retired in
Phase 5 when the Sense hardware was removed.

### Phase 3.3 — EAGLE-3 Poller ✅ (April 2026)
Containerized `eagle-poller` polls the Rainforest EAGLE-3 Local API every 30 s and writes instantaneous demand + cumulative summation to `eagle.meter` (billing-grade ground truth). Operational details in [`docs/SERVICES.md`](docs/SERVICES.md#eagle-poller); historical design in [`docs/archive/phase-3.3-eagle-poller-design.md`](docs/archive/phase-3.3-eagle-poller-design.md).

### Phase 3.4 — Grafana Dashboards ✅ (May 2026)
Five dashboards provisioned at `deploy/energy-stack/grafana/dashboards/`:
- `home-energy-overview.json` — current EAGLE demand, current ComEd 5-min price, 24h whole-home demand (EAGLE + Refoss `em:1+em:7` overlay), 24h ComEd price chart.
- `home-energy-full.json` — per-circuit breakdowns, cost panels (price × demand integrated to $/hr), daily/weekly/monthly summaries, EAGLE-vs-Refoss agreement panel.
- `hvac-scheduler.json` — scheduler decision history, day-type, override events, runtime correlation.
- `comed-bill-reconciliation.json` — Phase 8 bill-vs-meter reconciliation (4 panels).
- `iaq-comparison.json` — HAVEN indoor/outdoor air-quality comparison.

The fixed Flux cost-calc pattern is shared with `telegram-notifier` daily summary.

### Phase 5.5 — Webdashboard (retired May 2026)
The `webdashboard` nginx container + `webdashboard-api` FastAPI backend shipped as a sci-fi HUD on port 8081, sourced from `claude.ai/design`. Retired in favor of consolidating on Grafana — the additional surface (custom React + Babel-in-browser, FastAPI service, nginx proxy, separate cost-calc code path) wasn't pulling enough weight against a Grafana dashboard with the same data. Code removed from the repo and compose; Grafana is the only visualization layer going forward.

### Phase 4 — Thermostat Integration ✅ (May 2026)
Implemented via the Control4 EA-5 path rather than the originally-planned direct Honeywell TCC API: `thermostat-poller` container reads via `pyControl4.C4Climate` every 10 minutes, with override-detection logic, and writes to `hvac.thermostat`. The Control4 path was simpler and more reliable than direct TCC. Direct TCC remains a fallback option if Control4 ever loses access.

### Phase 5 — Refoss EM16P ✅ (April 2026)
Complete. Sense hardware removed and replaced with Refoss EM16P at `192.168.20.140` (homelab VLAN, auth disabled). New `refoss-poller` container in `energy-stack` polls `Refoss.Status.Get` every 30 s — single round-trip per cycle returns all 18 `em:N` channels plus `sys`/`wifi` blocks. Channel labels are sourced from the device's own `Refoss.Config.Get` (whatever was named in the Refoss app) and refreshed automatically when `sys.cfg_rev` increments. Measurements: `refoss.channel` (one point per channel) and `refoss.system`. The `sense-poller` container was removed from compose; old `energy_proxy.py`, `dashboard.html`, launchers, and Sense `.env` credentials were deleted from the Windows-side repo.

### Phase 6 — HVAC Optimization & Observability ✅ (April–May 2026)
Multi-service expansion that turned the project from monitoring-and-dashboards into actively scheduling HVAC for cost minimization: `nws-poller`, `hvac-scheduler`, `telegram-notifier`, Loki + Promtail log aggregation, and nightly restic backup to Backblaze B2. Detail block under "Phase 6+" further down.

### Phase 7 — Control4 Integration ✅ (subsumed by Phase 4)
The originally-described "automated load management based on real-time pricing and demand data" landed via Phase 4's Control4 EA-5 path: the `hvac-scheduler` already pushes setpoints through Control4 (Cinegration C4 driver) based on NWS forecast + ComEd pricing context. No separate Phase 7 deliverable.

### Phase 8 — ComEd Bill Ingest ✅ (May 2026)
Manual-upload `parse_comed_bill.py` script writes bill PDFs into `comed.bill` and `comed.bill_lineitems`. 9 historical bills backfilled (8/2025-4/2026). Detailed block at the bottom of this doc.

### Phase 9 — PJM Data Miner 2 Poller ✅ (May 2026)
Non-Member API access provisioned via PJM tech-support (the path the May 2026 Known-Follow-up blocked on). New `pjm-dm2-poller` container fires per-feed on a local-time schedule:
- `da_hrl_lmps` (ComEd zonal pnode `33092371`) at 17:00 CT — tomorrow's day-ahead LMP after market clear.
- `load_frcstd_7_day` (`forecast_area=COMED`) at 06:00 + 13:00 CT — 7-day load forecast with `evaluated_at_iso` tags so revisions stay distinct.
- `hrl_load_metered` (`zone=CE`) Sundays 02:00 CT — last 7 days of metered load, kept fresh for the 5CP-probability training set.
- `ops_sum_frcst_peak_rto` at 06:00 + 13:00 CT in Jun–Sep — RTO peak-day signal for cross-checking the day-type classifier.
- `annual_zonal_nspl` (`zone=COMED`) Dec 1 03:00 CT — yearly NSPL snapshot.
Plus two scripts: `backfill_pjm.py` for one-shot 5-year history (`scripts/`), and `scrape_pjm_5cp_pdf.py` to parse the official PJM 5CP PDF each November. Design + schema in [`docs/PJM_DM2_INTEGRATION.md`](docs/PJM_DM2_INTEGRATION.md); feed catalog in [`docs/PJM_DM2_FEEDS.md`](docs/PJM_DM2_FEEDS.md).

### Phase 10 — HVAC Scheduler Safety Supervisor ✅ (May 2026)
New `safety_supervisor.py` module gates every setpoint push the scheduler proposes. Three decision kinds (`approved` / `clamped` / `emergency`):
- **Clamp** — cool to `[65, 86]°F`, heat to `[55, 75]°F`. Catches a controller bug producing e.g. cool=55 or cool=95.
- **Emergency** — if thermostat snapshot reports indoor ≥ 86°F, override cool to 74°F regardless of what the schedule says. Catches `HOT_5CP_SHUTOFF` overshoots in real heat-wave conditions.
- **Approved** — proposed values pass through.
Decision logged to `hvac.actions` for audit. Shipped before Arm B because the supervisor is shared infrastructure that hangs every controller (RBC and future model-informed) off the same gate.

### Phase 11 — Field Study Pre-Registration (drafted May 2026, filing pending)
SCED randomized-alternation field study comparing baseline RBC (Arm A) against RBC + Step 1 model-informed (Arm B), week-level alternation, June 1 – Sep 30, 2026. Pre-registration draft in [`docs/EXPERIMENT_DESIGN.md`](docs/EXPERIMENT_DESIGN.md). Assignment list pre-generated and committed: [`deploy/energy-stack/scripts/randomize_arms.py`](deploy/energy-stack/scripts/randomize_arms.py) → [`docs/experiment-assignments-summer-2026.csv`](docs/experiment-assignments-summer-2026.csv) (18 weeks, 9 Arm A / 9 Arm B). Pinned-snapshot test fails loud if seed or algorithm ever drifts.

---

## Known Follow-ups

- **Arm B controller (Step 1 model-informed)**: critical path for the June 1 experiment start. Three integration points from [`docs/THERMAL_MODEL_DESIGN.md`](docs/THERMAL_MODEL_DESIGN.md): pre-cool depth from envelope-ODE integration, COAST shutoff lead time in closed form, Stage-2-during-5CP advisory log. Prereq: per-house thermal model fit (`τ`, cooling capacities, solar proxy) against existing telemetry.
- **Arm-switch wiring in `hvac-scheduler`**: read [`docs/experiment-assignments-summer-2026.csv`](docs/experiment-assignments-summer-2026.csv) at week boundary, branch to Arm A or Arm B logic, tag every `hvac.actions` row with `arm`. CSV and randomization script committed; scheduler integration not yet wired.
- **OSF pre-registration filing**: [`docs/EXPERIMENT_DESIGN.md`](docs/EXPERIMENT_DESIGN.md) is currently a draft. Both arms must be pinned at one frozen commit hash before week 1 of alternation.
- **ComfortNet decoder extension (write-side opcodes)**: full pipeline is live (broker + telegraf consumer in compose under profile `mqtt`; Pi-3B publisher live as of May 6 2026; `hvac.comfortnet` measurement flowing). The current decoder handles read-side user-menu traffic (`0xC1` GetUserMenuResponse); capturing a setting change to extract the write-side SetUserMenu opcode is an open follow-on. See [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet) `docs/SETTING_REVIEW.md` for the capture protocol; historical design context at [`docs/archive/COMFORTNET_PIPELINE.md`](docs/archive/COMFORTNET_PIPELINE.md).
- **Open-Meteo as second weather source**: ECMWF IFS, 9 km, free since Oct 2025. Research showed it beats NWS at 1–3 day temp forecast. Worth running as a parallel input to the day-type classifier.
- **Forecast bias correction**: affine intercept+slope per lead time (NOAA NCEP Office Note 520 method). Needs paired observation history; Ecowitt GW1200 + WN32 hardware en route May 2026.
- **Phase 8 forward-projection panel**: bill-vs-projected stub exists in `comed-bill-reconciliation.json`; full formula (supply-so-far + delivery + capacity + taxes + extrapolation) lands after a few cycles of bill data accumulate.
- **Pre-cool depth A/B test**: research review (May 2026) suggests softening `HOT_PRE_COOL` from 68°F starting 4am to 71–72°F starting 3am for ~90% of peak shift at materially less off-peak kWh. Hits the comfort-aware-scheduling research's recommendation set. Will fall out of the SCED study automatically when run as an Arm B variant.
- **Fridge anomaly detection upgrade**: current univariate threshold; Merlion (Salesforce, BSD-3) is multivariate and a better fit for the 18-channel Refoss surface.
- **SOPS encryption** (complete, April 2026): `.env` is mirrored as age-encrypted `deploy/energy-stack/secrets/env.sops.env`, committed to the repo. Recipients: Windows workstation + Pi-lab. Rotation workflow documented in `deploy/energy-stack/README.md`. Both age private keys must be stored in 1Password (single-host loss is recoverable; both-host loss is not).
- **InfluxDB downsampling** (complete, May 2026): `influx-init` container provisions the `energy-longterm` bucket and a 1-minute downsample Flux task on every `compose up -d`. Per-frame measurements (`eagle.meter`, `refoss.channel`, `refoss.system`, future `hvac.comfortnet`) keep raw 90 d → mean+max longterm; event measurements (`hvac.actions`, `hvac.overrides`) write directly to longterm. See [`docs/INFLUXDB_RETENTION.md`](docs/INFLUXDB_RETENTION.md).

---

## Project Files

| Path | Purpose |
|---|---|
| `PROJECT.md` | This document — decisions, architecture, roadmap |
| `README.md` | Top-level entry point pointing at the live system |
| `Test-Eagle.ps1` | Ad-hoc EAGLE-3 Local API probe (kept for manual debugging) |
| `deploy/energy-stack/` | The live system — InfluxDB + Grafana + ~10 service containers as a Docker compose project, deployed to Pi-lab via GitHub Actions self-hosted runner |
| `deploy/energy-stack/backup/RESTORE.md` | Pi rebuild procedure from B2 backups |
| `docs/SERVICES.md` | Per-service reference (env vars, fields written, healthchecks) |
| `docs/HVAC_LOGIC.md` | HVAC scheduler logic, schedules, fallback, ISU settings, safety supervisor, equipment |
| **Research track** | |
| `docs/EXPERIMENT_DESIGN.md` | Pre-registration draft for the SCED field study (summer 2026 onwards) |
| `docs/THERMAL_MODEL_DESIGN.md` | Step 1 affine-fit thermal model for Arm B, methodology grounded in Bacher–Madsen 2011 |
| `docs/experiment-assignments-summer-2026.csv` | Pre-generated arm assignments (18 weeks, 9 Arm A / 9 Arm B) |
| `deploy/energy-stack/scripts/randomize_arms.py` | Arm-assignment generator with pinned-snapshot test |
| **Subsystem designs** | |
| `docs/PJM_DM2_INTEGRATION.md` | Design + status for the PJM Data Miner 2 poller |
| `docs/PJM_DM2_FEEDS.md` | Feed catalog with filterable columns and ComEd-specific constants |
| `docs/INFLUXDB_RETENTION.md` | Retention/downsampling design (`energy` raw + `energy-longterm` aggregates) |
| `docs/COMFORTNET_USE_CASES.md` | What we do with ComfortNet bus data (active reference). Pipeline implementation lives in [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet) |
| `docs/archive/` | Historical design and planning docs for shipped features (see [`docs/archive/README.md`](docs/archive/README.md) for index) |

---

## Hardware Inventory

See [Updated Hardware Inventory (May 2026)](#updated-hardware-inventory-may-2026) below for the authoritative current state.

---

## Key Technical Notes

- **EAGLE-3 uses HTTPS (port 443)** with self-signed cert, not HTTP/80 like the older EAGLE-200
- **EAGLE-3 uses the Local API** (`device_query` with `<Name>` tags), not the old REST API (`get_instantaneous_demand` via cloud relay)
- **Honeywell RedLINK has no local API** — gateway is encrypted cloud-only; all thermostat data must come through TCC cloud
- **Honeywell TCC rate limiting**: 10-minute minimum polling interval; no graceful backoff headers; 403/500 errors kill the session
- **Refoss EM16P** uses JSON-RPC over plain HTTP (port 80) at `/rpc`. GET shortcut works for read methods (`GET /rpc/Refoss.Status.Get`). Auth-en is opt-in (Shelly-Gen2-style SHA-256 digest); off in this deployment. One `Refoss.Status.Get` returns all 18 `em:N` channels plus `sys`/`wifi` — no per-channel polling needed.
- **Refoss energy fields are bucketed**, not lifetime totalizers: `day_energy`/`week_energy`/`month_energy` reset on the day/week/month boundary inside the device. For true cumulative kWh, integrate `power_w` over time in Flux.
- **InfluxDB chosen over Prometheus** for time-series storage based on data characteristics (metering vs infrastructure monitoring)

---

## Phase 6+ — HVAC Optimization & Observability (April–May 2026)

A second wave of work that took the project from "monitoring + dashboards" to "actively scheduling HVAC for cost minimization." Six new services + Loki log aggregation + nightly backups + comprehensive notifications.

### What was built

- **`nws-poller`** — pulls hourly + daily NWS forecasts (today/tomorrow/day2) and active alerts (heat advisories, etc.) every 30 min. Core input to the day-type decision.
- **`hvac-scheduler`** — the centerpiece. Daily 21:00 decision classifies tomorrow as MILD / NORMAL / HOT_5CP_RISK / HOT_STREAK_DAY1 based on NWS forecast + day-after lookahead, picks a schedule, fires `cool_setpoint`/`heat_setpoint`/`fan_mode`/`hold` actions at scheduled times. Pushes to the Amana CTK04AE via Pi → Control4 EA-5 (`192.168.1.30`) → Cinegration C4 driver → TCC cloud → RedLINK gateway → physical thermostat. Token persistence + reauth-on-401. Override mechanism (`/data/overrides.json`) for vacation flat-holds and manual day-type forces.
- **`telegram-notifier`** — daily summary at 8 AM (yesterday's cost / kWh / peak demand / fridge anomaly / scheduler activity / today's forecast) + alert checker every 5 min (poller silence, price spikes, fridge anomalies). Bot: `@EnergyStackBot` (separate from any other Telegram bots used elsewhere).
- **`loki` + `promtail`** — log aggregation with Docker service-discovery filtered to the energy-stack project. JSON log lines have `level` and `msg` lifted to Loki labels for easy LogQL filtering in Grafana Explore.
- **Nightly restic backup** to Backblaze B2 (root cron 02:00). Includes InfluxDB backup staged per-run, energy-stack/, chris-brain/, dns-stack/, Network_Management/, .ssh, .config/restic, and the backup script itself. Telegram notifications on success and failure (failure includes last 20 log lines).
- **HVAC scheduler dashboard** (`grafana/dashboards/hvac-scheduler.json`): day-type history, action timeline, setpoint vs. indoor temp.
- **Equipment health row** added to `home-energy-full.json`: daily AC kWh (em:2 + em:8), daily furnace blower kWh (em:9), AC % of whole-home (last 24 h).

### Cost integration fix (significant)

Initial naive cost calculation (sum of power × current price) over-estimated cost by ~2× during peak hours. Two bugs:

1. **Timestamp alignment** — Refoss `aggregateWindow` defaulted to `timeSrc=_stop` (window end), but ComEd `hourly_avg` uses timestamp-at-hour-start. The join missed because timestamps didn't align. Fixed with `timeSrc:"_start"` on the aggregateWindow.
2. **Timezone** — `date.truncate(t: now(), unit: 1d)` truncates UTC midnight, not local. At 19:01 CDT, "today UTC" had only just started 1 minute earlier. Fixed with `import "timezone"; option location = timezone.location(name: "America/Chicago")`.

After both fixes: $2.33 (proper price-weighted) vs $4.16 (naive over-estimate) for one observed day. Naive treated all 46 kWh at the current peak 9¢ rate; proper integration weighted each kWh by the actual hourly price at the time it was consumed.

The fixed Flux pattern is now the canonical cost calc — used in `telegram-notifier` daily summary and Grafana cost panels.

---

## Recent Decisions (May 2026)

### Telegram replaces Pushover for backup notifications

`pi-backup.sh` originally used Pushover (its own credentials in `~/.config/restic/b2.env`). Switched to Telegram (`@EnergyStackBot` via `~/energy-stack/.env`) so all energy-stack alerts flow through one channel. Failure messages now include `<pre>`-formatted last-20-log-lines for one-tap diagnosis without SSHing in. Pushover env vars stay in `b2.env` (inert — nothing else on the Pi uses them).

### Cinegration C4 driver chosen over standalone Honeywell HA integrations

Considered: HomeAssistant Honeywell integration, direct `aiosomecomfort`/`pyhtcc` cloud polling, Cinegration driver via Control4. Chose **Cinegration via Control4 + pyControl4** because:

- Control4 already runs in the house (EA-5 controller present, automation infrastructure already there)
- Cinegration driver is the most mature TCC bridge in the Control4 ecosystem
- Local Director access (no internet round-trip per setpoint push) — faster, more reliable
- Token-based auth with persistence — doesn't re-login every cycle
- Same path could later push to other Control4-controlled HVAC zones if added

Tradeoff: TCC rate limiting (10-min minimum poll) still applies for thermostat reads. Mitigated by (a) the scheduler reading thermostat state only at action time (4-6× per day, well under limit) and (b) using cached state where stale data is acceptable.

### `pyControl4` API casing gotcha

Initial implementation used camelCase (`getAccountBearerToken`) from old documentation. v2.0.2 uses snake_case (`get_account_bearer_token`). Discovered via library introspection. Same applied to `send_post_request` URI — wrong path `/api/v1/items/{id}` returns empty; correct is `/api/v1/items/{id}/commands`. Better path: use the high-level `C4Climate` wrapper (`set_cool_setpoint_f`, etc.) instead of raw item commands.

### Thermostat ID 3231, NOT 3230

Control4 exposes both:
- `3230` — the Cinegration backing driver (raw TCC bridge)
- `3231` — the THERMOSTAT proxy (Climate-class abstraction)

Push setpoints to 3231. The 3230 backing driver doesn't accept setpoint commands directly.

### Adaptive Recovery (ISU 409) MUST be OFF

Honeywell's "Adaptive Intelligent Recovery" starts the AC 30-60 min BEFORE a scheduled setpoint change so the room hits the target temp AT the scheduled time. Great for comfort-only schedules, **wrong for this scheduler** because:

1. Pulls AC runtime into peak pricing windows (the scheduler's whole point is to keep peak runtime LOW)
2. Makes the Pi's setpoint pushes unpredictable — "set 78 at 13:00" should mean exactly that, not "AR starts cooling at 12:30 to hit 78 by 13:00"
3. Fights the dynamic re-evaluation when the scheduler updates today's behavior from overrides

Documented in [HVAC_LOGIC.md ISU table](docs/HVAC_LOGIC.md#honeywell-isu-settings-the-set-once-stuff). Verify after any thermostat factory reset.

### ComEd public API does NOT expose day-ahead forecast

Initial design assumed ComEd would publish day-ahead price forecasts (would unlock surprise-spike escalation). Investigation found:

- Documented public API has only `5minutefeed` and `currenthouraverage` endpoints
- Undocumented `?type=daynexttoday` returns *today's settled* day-ahead prices (already in the past at query time), not tomorrow's
- True day-ahead source is **PJM DataMiner2 API**, which requires a subscription key
- PJM API portal signup requires non-VPN egress (Mullvad routes at the gateway broke it for our Trusted VLAN)
- Non-member ("Other") accounts cannot self-provision DataMiner access via Account Manager — required emailing PJM tech-support to request provisioning

**Net: resolved May 2026.** PJM tech-support provisioned Non-Member API access; the `pjm-dm2-poller` service (Phase 9) now writes `pjm.lmp_da_hourly`, `pjm.load_forecast`, `pjm.metered_load`, `pjm.peak_forecast_rto`, and `pjm.nspl_zonal` to InfluxDB on a per-feed schedule. NWS-driven day-type heuristic still drives Arm A's classifier; PJM data is the input to the upcoming 5CP-probability classifier and the Arm B model-informed controller. We use `aiohttp` directly against the DM2 API rather than the `gridstatus` library (the per-feed call set is small enough that the wrapper's abstraction wasn't pulling weight).

### Mullvad WireGuard at gateway level (relevant for ops)

UniFi has two policy-based traffic routes (`Trusted → VPN US-CHI`, `Trusted → VPN EU-STO`) that send all internet traffic from the Trusted VLAN through Mullvad WireGuard tunnels with kill switch enabled. Means workstation traffic always exits via Mullvad. Some destinations block known VPN exits (PJM, banks, certain regulators). For affected sites, temporarily toggle the route off via UniFi MCP / UI. Pi-lab on Homelab VLAN is NOT routed through Mullvad — that's why the Pi reaches PJM cleanly while the workstation can't.

### Pre-cool depth: research suggests softening (open re-tune)

Research review (May 2026, three agents, sources include NREL/Davis Energy Group, Wang et al. 2020, ACEEE 2014) found:

- The current `HOT_PRE_COOL` at 68°F starting 4am is **past the diminishing-returns knee** for wood-frame US homes
- Schedule SHAPE matters more than depth (~57% optimized cost savings vs. 20-45% for fixed-rule depth tuning)
- Realistic field savings for PJM mid-Atlantic humid summers: 20-35% reduction in 14-18:00 kWh, 2-8% annual cooling kWh penalty
- Better candidate: **71-72°F starting at 3am** — captures 90%+ of peak shift at materially less off-peak kWh
- Coast on stage 1 with slightly lower setpoint (78-79°F) in humid weather rather than free-coasting to 80+ — leverages the 2-stage compressor for dehumidification
- Humid override threshold could tighten 65→62°F per ASHRAE 55-2020 (humidity ratio 0.012 kg/kg)

Open work: A/B test against a few HOT days in summer 2026, log results, decide whether to ship as the new default. Logged in [HVAC_LOGIC.md](docs/HVAC_LOGIC.md) as a known re-tune opportunity, not yet applied.

### Haven IAQ ingest + thermostat-poller (May 2026)

The "Haven data is unreachable" conclusion was wrong twice:

1. **First pivot (May 3, 2026)** — discovered the homeowner portal at `my.haveniaq.com` has a CSV export per device. Initial `haven-ingest` shipped as a CSV watcher (drop file in inbox → service parses).
2. **Second pivot (mid-May 2026)** — replaced the CSV watcher with a direct HAVEN API poller (commit `3cccd63`, "Replace haven-ingest CSV watcher with HAVEN API poller"). Hits the official HAVEN Pro API at `https://havenapi.tzoa.io` directly. Auth uses Auth0 refresh-token rotation: the bootstrap `HAVEN_REFRESH_TOKEN` is captured once from a browser login session at `my.haveniaq.com`, and the poller persists the rotating refresh token to its `/data` volume across restarts. Per-poll backfill bounded by `HAVEN_BACKFILL_DAYS` (default 7).

Continuous fields are temp/RH/tVOC; PM2.5 and airflow CFM are flow-dependent and only populate when the blower is moving air past the return-duct sensor. That flow-dependence is a feature, not a defect: non-null airflow CFM rows give blower-runtime ground truth (cross-validates against Refoss `em:9` furnace blower power), and the measured CFM at that moment becomes useful for delivered-BTU calc when paired with future supply-air temp instrumentation.

Two new services shipped:

- **`haven-ingest`** — polls the HAVEN cloud API every `HAVEN_POLL_INTERVAL` seconds (default 300), writes `haven.indoor` (one device, indoor sensor) and `haven.outdoor` (paired outdoor station) measurements. Idempotent on timestamp.
- **`thermostat-poller`** — continuous 10-min Control4 reads of CTK04AE state (TCC rate-limit floor). Writes `hvac.thermostat` measurement on every poll. Also implements **automatic override detection**: compares current setpoints against the last applied `hvac.actions` row; if they differ by ≥ 0.5°F AND the last action was > 5 min ago, writes `hvac.overrides` row. This unlocks the comfort-aware scheduling research's #1 recommendation ("log every override as training data") essentially for free.

Enabled because: the existing `hvac-scheduler` snapshots thermostat state only at action-firing moments (4-7 timestamps per day) — too sparse for proper time-series correlation against Haven's 5-min cadence, and provides no foundation for override detection. The poller fills both gaps.

New Grafana dashboard: `iaq-comparison.json` — Haven (return-air mix) vs thermostat (wall) for both temp and RH, plus a derived bias panel (positive = thermostat reads warmer than house mix), tVOC trend, blower-activity cross-validation (Haven CFM vs Refoss em:9), and manual override events.

Why this matters for the existing scheduler: the return-air mix is a **fundamentally better whole-home signal** than a single wall thermostat (volume-weighted average of every room contributing to the return). Hallway thermostats often read drier than the actual house average because moisture loads (cooking, showers, basements) get pulled into the return mix. The new data makes calibration vs. mixed-air the diagnosis path for "does the humid override threshold need re-tuning."

### Other research findings (parked)

- **`gridstatus` Python lib** — alternative wrapper for PJM DataMiner2; not adopted (the `pjm-dm2-poller`'s per-feed call set is small enough that direct `aiohttp` is simpler than the abstraction)
- **Open-Meteo (ECMWF IFS, 9 km, free since Oct 2025)** — better than NWS at 1-3 day temp forecast; worth running as second weather source
- **Forecast bias correction** — affine intercept+slope per lead time (NOAA NCEP Office Note 520 method); needs paired observation history (Ecowitt sensor en route)
- **EMHASS / Predheat** — open-source MILP/MPC layers for HA; worth reading even if not adopting (reference thermal model + tariff abstractions)
- **Merlion (Salesforce, BSD-3)** — multivariate anomaly detection; better fit for the 18-channel Refoss data than the current univariate threshold approach
- **OpenADR 3.x** — ComEd doesn't currently expose a residential VTN; revisit if/when they do
- **Override logging** ✅ shipped May 2026 via `thermostat-poller` automatic override detection: writes `hvac.overrides` rows whenever current thermostat setpoints diverge ≥ 0.5°F from the last `hvac.actions` row by more than `OVERRIDE_GRACE_MIN` (default 5 min). Foundational training data for future ML/MPC layers.

Full research output: agent reports retained in session transcripts; key findings summarized in [docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md) and the open-questions section above.

---

## Updated Hardware Inventory (May 2026)

| Device | Role | Connection | Status |
|---|---|---|---|
| Rainforest EAGLE-3 | Smart meter gateway (billing-grade) | Ethernet, homelab VLAN, 192.168.20.192:443 | Connected & polling |
| Refoss EM16P | Per-circuit energy monitoring (2 mains + 16 branch) | Wi-Fi, homelab VLAN, 192.168.20.140 (HTTP, no auth) | Connected & polling |
| Amana CTK04AE thermostat (Honeywell-OEM whitelabel) | HVAC control | Native RedLINK Wi-Fi + CT-485 bus. RedLINK reached via Control4 EA-5 (192.168.1.30) → Cinegration → TCC cloud; CT-485 sniffed read-only by Promithius-DR/comfortnet | Active scheduling (May 2026) |
| Amana ASXC160481BE | Outdoor AC condenser, 16 SEER 2-stage 4-ton | (HVAC) | Active; 2-stage capability is what makes pre-cool strategy viable |
| Amana AMVM971005CN | Modulating gas furnace + variable-speed ECM blower | (HVAC) | Active; ECM blower enables Circulate-fan during coast at low W |
| Control4 EA-5 controller | HA-style automation hub bridging to TCC | LAN, 192.168.1.30 | Active; pyControl4 token persisted in `hvac_scheduler_data` volume |
| Ecowitt GW1200 + WN32 + Rain Shield | Outdoor temp/humidity/barometric sensor (local push) | LAN; en route May 2026 | **Pending hardware delivery** |
| ComEd Smart Meter | Utility meter | Via EAGLE-3 Zigbee HAN | Connected |
| ~~Sense Energy Monitor~~ | ~~Whole-home power + device detection~~ | — | Removed April 2026 (replaced by Refoss EM16P) |

---

## Phase 8 — ComEd Bill Ingest (May 2026)

Manual-upload Python script (`deploy/energy-stack/scripts/parse_comed_bill.py`)
that parses ComEd bill PDFs into InfluxDB. Run by hand each month after
downloading the PDF from the ComEd portal. Backfilled 9 historical bills
covering 8/2025-4/2026.

**Why bills, not just real-time telemetry**: the dashboard's cost calc
(`Σ power × hourly_supply_price`) was supply-only. Real bills add delivery,
capacity, riders, and taxes — typically 40-70% on top. The capacity charge
in particular is what the HVAC scheduler exists to suppress (latest bill:
$54.64 from `6.56 kW × $8.32925`, locked annually from prior summer's PJM
5CP). Without bill ingest, no way to measure scheduler effectiveness.

**Why script not container**: 12 events/year, parser is the only hard
part. A service loop, Dockerfile, healthcheck, and Telegram failure-alert
wiring add LOC and zero capability over `python parse_comed_bill.py file.pdf`.

**Why pypdf not Docling**: empirically tested both. Docling table inference
conflates layout-adjacent columns on ComEd's multi-column print layout
(SUPPLY values mixed with DELIVERY values in the same table row, or DELIVERY
section header dropped entirely). pypdf's flat reading order keeps each line
item adjacent to its values, so the regex `Capacity Charge\s*([\d.]+)\s*kW`
is unambiguous. Docling stays in the kit for future utility ingests where
the source is genuinely tabular (water, gas, property tax).

**Schema**: `comed.bill` (top-line per cycle: total_due, kwh, peak_kw,
supply/delivery/taxes/misc totals) + `comed.bill_lineitems` (full GL
breakdown). Idempotency comes from Influx upserts: re-running a bill writes
the same `(measurement, account_no + rate_plan + bill_type, service_to @
23:59:59 CDT)` tuple, which collides and overwrites — safe to retry.

**Dashboard**: `deploy/energy-stack/grafana/dashboards/comed-bill-reconciliation.json` —
bill-vs-projected, EAGLE-vs-billed-kWh, capacity-charge tracker, and a
stub forward-projection panel. The projection panel's full formula
(supply-so-far + delivery estimate + capacity + taxes estimate + days-remaining
extrapolation) is sketched but lands as a follow-on after a few cycles
of data accumulate.

Historical design: `docs/archive/phase-8-comed-bill-ingest-design.md`
Historical plan: `docs/archive/phase-8-comed-bill-ingest-plan.md`
