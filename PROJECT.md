# Home Energy Monitoring System — Project Document

**Project**: `D:\Projects\energy-proxy`
**Started**: 2025
**Last Updated**: April 2026

---

## Purpose

Gain full visibility into the home's most significant ongoing operational cost — electricity — by aggregating real-time energy consumption, utility pricing, and environmental context into a unified monitoring system with historical data retention.

The goal is not just "what am I using right now" but "what did it cost, why, and what can I change."

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
EAGLE-3 (local HTTPS, 30s) ────────┐
ComEd Pricing API (public, 60s) ───┤   Pi-lab (192.168.20.10)
Refoss EM16P (local HTTP, 30s) ────┼──► energy-stack compose ──► InfluxDB ──► Grafana
TCC Thermostat (cloud, 600s) ──────┘   (eagle/comed/refoss          (storage)    (dashboards)
                                        pollers, planned tcc)
```

All pollers run as containers in the `energy-stack` Docker compose project on
Pi-lab. Each is a small Python service that calls one upstream and writes to
InfluxDB. The `refoss-poller` (Phase 5) replaced `sense-poller` (retired Phase 1.5).
The Windows-side `energy_proxy.py` and custom HTML dashboard are gone — Grafana is
the single visualization layer. State is durable: InfluxDB on a persistent volume.

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

### Thermostat Data: Honeywell TCC Cloud API

**Decision**: Poll the Honeywell Total Connect Comfort API every 10 minutes for context data.

**Rationale**:
- The home uses a Honeywell RedLINK thermostat system with the TCC app
- RedLINK has no local API — the gateway is encrypted cloud-only (HTTPS to alarmnet.com, no local endpoints, proprietary 900MHz RF)
- The TCC cloud API (via `pyhtcc` or `aiosomecomfort` libraries) provides indoor temp, outdoor temp, humidity, setpoints, equipment status, and system mode
- 10-minute polling interval is the community-tested safe minimum to avoid Honeywell rate limiting (403/500 errors with no graceful backoff)
- This data is context, not metering — it answers "why is the HVAC running" not "how much is it using"
- The Resideo Developer API (api.honeywellhome.com/v2, OAuth2) is also available as a cleaner alternative; requires developer registration

**Three API paths identified**:
1. Legacy SOAP: `rs.alarmnet.com/TotalConnectComfort/ws/MobileV2.asmx` (AppID from iOS app, may be retired)
2. Web portal scraping: `mytotalconnectcomfort.com/portal` via `pyhtcc`/`aiosomecomfort` (battle-tested by Home Assistant community, ~3,200 active installations)
3. Official REST: `api.honeywellhome.com/v2` (OAuth2, documented, requires developer registration)

---

## Target Architecture

The collector layer matches "Current Architecture" above. What's still ahead is more dashboards (Phase 3.4 is partially done — overview dashboard live, deeper Refoss/cost/HVAC panels still to build) and TCC integration (Phase 4).

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

### Phase 3.3 — EAGLE-3 Poller
Add EAGLE-3 polling to the proxy using the tested Local API. Write instantaneous demand and cumulative summation to InfluxDB. This is billing-grade ground truth.

### Phase 3.4 — Grafana Dashboards (in progress)
First dashboard live: **Home Energy — Overview** at `grafana/dashboards/home-energy-overview.json` (provisioned, auto-loaded). Four panels: current EAGLE demand, current ComEd 5-min price, 24h whole-home demand (EAGLE + Refoss `em:1+em:7` overlay), and 24h ComEd 5-min price chart. Updated in Phase 5 to swap the Sense overlay for Refoss split-phase mains. Still to build: per-circuit breakdowns (top-N circuits by power, HVAC vs other, fridge cluster), cost computation panels (price × demand integrated to $/hr), daily/weekly/monthly summaries, EAGLE-vs-Refoss agreement panel (sanity check).

### Phase 5 — Webdashboard prototype (April 2026)
Sci-fi HUD prototype generated via `claude.ai/design` and deployed as the `webdashboard` nginx container on the energy-stack at `http://192.168.20.10:8081/`. Static React + Babel-standalone (in-browser JSX compilation) — no build step, files served as-is from `deploy/energy-stack/webdashboard/`. Three-zone hero (price + ring + 24h chart), animated power-flow diagram, active-loads list, voltage/freq gauges, climate panel, cost stack with projections, EAGLE summation tile, scrolling ticker, scanlines, and a 60-tick sweep ring. Tweaks panel exposes 5 accent colors, density, motion intensity, scenarios, scanlines, and ticker on/off. **Currently mock-data only** — wiring to live InfluxDB requires building a `/api/energy` endpoint (probably a small Python `aiohttp` service alongside the existing pollers, queriable from the JSX via fetch). This is an additive second view; Grafana remains the production visualization.

### Phase 4 — TCC Thermostat Integration
Add Honeywell TCC polling (~10 min interval) for indoor/outdoor temp, humidity, setpoints, equipment status. Write to InfluxDB as context data for HVAC correlation panels.

### Phase 5 — Refoss EM16P ✅ (April 2026)
Complete. Sense hardware removed and replaced with Refoss EM16P at `192.168.20.140` (homelab VLAN, auth disabled). New `refoss-poller` container in `energy-stack` polls `Refoss.Status.Get` every 30 s — single round-trip per cycle returns all 18 `em:N` channels plus `sys`/`wifi` blocks. Channel labels are sourced from the device's own `Refoss.Config.Get` (whatever was named in the Refoss app) and refreshed automatically when `sys.cfg_rev` increments. Measurements: `refoss.channel` (one point per channel) and `refoss.system`. The `sense-poller` container was removed from compose; old `energy_proxy.py`, `dashboard.html`, launchers, and Sense `.env` credentials were deleted from the Windows-side repo.

### Phase 6 — Prometheus/Grafana Migration (if needed)
If the data volume or query patterns outgrow InfluxDB, migrate to Prometheus. This is unlikely for residential energy monitoring but remains an option.

### Phase 7 — Control4 Integration
Automated load management based on real-time pricing and demand data.

---

## Known Follow-ups

- **Refoss CT polarity on em:5 and em:14**: both circuits sit on B-phase breakers but the EM16P's voltage reference wire is on A phase, so power is reported as negative (small return-energy values accumulate). Fix in the Refoss app by setting per-channel `factor` to `-1` for `em:5` (Master Bedroom) and `em:14` (Basement Micro/Fridge). Confirm with `curl http://192.168.20.140/rpc/Refoss.Config.Get` after — the field shows up alongside `name` for each `em:N`. Poller will pick up the corrected sign automatically; no code change needed.
- **InfluxDB downsampling**: single `energy` bucket with infinite retention chosen for Phase 3.1 (YAGNI). Revisit after Phase 3.4 dashboards are in place and data cardinality/query patterns are known. Candidate design: `energy-longterm` bucket + Flux task downsampling 1-minute aggregates. Refoss adds ~18 channels × 10 fields per 30 s cycle, so cardinality bears watching.
- **SOPS encryption** (complete, April 2026): `.env` is mirrored as age-encrypted `deploy/energy-stack/secrets/env.sops.env`, committed to the repo. Recipients: Windows workstation + Pi-lab. Rotation workflow documented in `deploy/energy-stack/README.md`. Both age private keys must be stored in 1Password (single-host loss is recoverable; both-host loss is not).
- **Grafana dashboards**: overview dashboard live (`home-energy-overview.json`); per-circuit / cost / HVAC panels are Phase 3.4 follow-on. Refoss-specific panels compute whole-home aggregate as `em:1 + em:7` (the two split-phase mains) since the device's `emmerge:N` rollups return empty on firmware 3.1.8.
- **Versioning energy-proxy**: project is not a git repo yet. Phase 5 added a third poller and deleted ~6 Windows-side files; risk of accidental loss is rising. Worth `git init` + private GitHub push as a snapshot.

---

## Project Files

| Path | Purpose |
|---|---|
| `PROJECT.md` | This document — decisions, architecture, roadmap |
| `README.md` | Top-level entry point pointing at the live system |
| `Test-Eagle.ps1` | Ad-hoc EAGLE-3 Local API probe (kept for manual debugging) |
| `deploy/energy-stack/` | The live system — InfluxDB + Grafana + 3 pollers (eagle, comed, refoss) as a Docker compose project, deployed to Pi-lab via rsync |
| `docs/phase-3.3-eagle-poller-design.md` | Design notes for the EAGLE-3 poller (historical) |
| `docs/sessions/session-3.1-influxdb-grafana.md` | Phase 3.1 planning prompt (historical snapshot) |
| `docs/SERVICES.md` | Per-service reference (env vars, fields written, healthchecks) |
| `docs/HVAC_LOGIC.md` | HVAC scheduler logic, schedules, fallback, ISU settings, equipment |
| `deploy/energy-stack/backup/RESTORE.md` | Pi rebuild procedure from B2 backups |

---

## Hardware Inventory

| Device | Role | Connection | Status |
|---|---|---|---|
| Rainforest EAGLE-3 | Smart meter gateway (billing-grade) | Ethernet, homelab VLAN, 192.168.20.192:443 | Connected & polling |
| Refoss EM16P | Per-circuit energy monitoring (2 mains + 16 branch) | Wi-Fi, homelab VLAN, 192.168.20.140 (HTTP, no auth) | Connected & polling (Phase 5, April 2026) |
| Honeywell RedLINK Thermostat | HVAC context (temp/humidity/setpoints) | Cloud API (TCC) | Not yet integrated |
| ComEd Smart Meter | Utility meter | Via EAGLE-3 Zigbee HAN | Connected |
| ~~Sense Energy Monitor~~ | ~~Whole-home power + device detection~~ | — | Removed April 2026 (replaced by Refoss EM16P) |

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
- **`hvac-scheduler`** — the centerpiece. Daily 21:00 decision classifies tomorrow as MILD / NORMAL / HOT_5CP_RISK / HOT_STREAK_DAY1 based on NWS forecast + day-after lookahead, picks a schedule, fires `cool_setpoint`/`heat_setpoint`/`fan_mode`/`hold` actions at scheduled times. Pushes to Honeywell VisionPRO 8000 via Pi → Control4 EA-5 (`192.168.1.30`) → Cinegration C4 driver → TCC cloud → physical thermostat. Token persistence + reauth-on-401. Override mechanism (`/data/overrides.json`) for vacation flat-holds and manual day-type forces.
- **`telegram-notifier`** — daily summary at 8 AM (yesterday's cost / kWh / peak demand / fridge anomaly / scheduler activity / today's forecast) + alert checker every 5 min (poller silence, price spikes, fridge anomalies). Bot: `@EnergyStackBot` (separate from any other Telegram bots used elsewhere).
- **`webdashboard-api`** — FastAPI backend serving `/api/energy/snapshot` for the webdashboard. Replaced the design-tool mock data with live InfluxDB queries.
- **`loki` + `promtail`** — log aggregation with Docker service-discovery filtered to the energy-stack project. JSON log lines have `level` and `msg` lifted to Loki labels for easy LogQL filtering in Grafana Explore.
- **Nightly restic backup** to Backblaze B2 (root cron 02:00). Includes InfluxDB backup staged per-run, energy-stack/, chris-brain/, dns-stack/, Network_Management/, .ssh, .config/restic, and the backup script itself. Telegram notifications on success and failure (failure includes last 20 log lines).
- **HVAC scheduler dashboard** (`grafana/dashboards/hvac-scheduler.json`): day-type history, action timeline, setpoint vs. indoor temp.
- **Equipment health row** added to `home-energy-full.json`: daily AC kWh (em:2 + em:8), daily furnace blower kWh (em:9), AC % of whole-home (last 24 h).

### Cost integration fix (significant)

Initial naive cost calculation (sum of power × current price) over-estimated cost by ~2× during peak hours. Two bugs:

1. **Timestamp alignment** — Refoss `aggregateWindow` defaulted to `timeSrc=_stop` (window end), but ComEd `hourly_avg` uses timestamp-at-hour-start. The join missed because timestamps didn't align. Fixed with `timeSrc:"_start"` on the aggregateWindow.
2. **Timezone** — `date.truncate(t: now(), unit: 1d)` truncates UTC midnight, not local. At 19:01 CDT, "today UTC" had only just started 1 minute earlier. Fixed with `import "timezone"; option location = timezone.location(name: "America/Chicago")`.

After both fixes: $2.33 (proper price-weighted) vs $4.16 (naive over-estimate) for one observed day. Naive treated all 46 kWh at the current peak 9¢ rate; proper integration weighted each kWh by the actual hourly price at the time it was consumed.

The fixed Flux pattern is now the canonical cost calc — used in `webdashboard-api`, `telegram-notifier` daily summary, and Grafana cost panels.

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
- Non-member ("Other") accounts cannot self-provision DataMiner access via Account Manager — pending PJM tech-support email response (May 2026)

Net: feature parked. NWS-driven day-type heuristic remains the source of truth. May revisit when DataMiner access lands. The `gridstatus` Python library wraps DataMiner2 cleanly (verified by research agents) — would be the integration path once we have the key.

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

### Haven IAQ CSV ingest + thermostat-poller (May 3, 2026)

The "Haven data is unreachable" conclusion was wrong. The homeowner portal at `my.haveniaq.com` has a CSV export per device (`CAM_<device-id>_<start>_to_<end>.csv` filename pattern). 7-day export = ~2,000 rows of 5-min samples. Columns: timestamp, PM2.5 + status, tVOC + status, temp °C/°F, RH%, combined status, airflow CFM. Continuous fields are temp/RH/tVOC; PM2.5 and airflow CFM are sparse (~3% coverage) because they're flow-dependent measurements that only populate when the blower is moving air past the return-duct sensor.

The flow-dependence is actually a feature: non-null airflow CFM rows give blower-runtime ground truth (cross-validates against Refoss em:9 furnace blower power), and the measured CFM value at that moment becomes useful for delivered-BTU calc when paired with future supply-air temp instrumentation.

Two new services shipped:

- **`haven-ingest`** — watches `~/energy-stack/inbox/haven/` for CSV exports, parses, writes `haven.airquality` measurement. Idempotent on timestamp. Files move to `processed/` on success or `failed/` on parse error. Workflow: export weekly from my.haveniaq.com → scp to Pi → service ingests within 60 s.
- **`thermostat-poller`** — continuous 10-min Control4 reads of VisionPRO state (TCC rate-limit floor). Writes `hvac.thermostat` measurement on every poll. Also implements **automatic override detection**: compares current setpoints against the last applied `hvac.actions` row; if they differ by ≥ 0.5°F AND the last action was > 5 min ago, writes `hvac.overrides` row. This unlocks the comfort-aware scheduling research's #1 recommendation ("log every override as training data") essentially for free.

Enabled because: the existing `hvac-scheduler` snapshots thermostat state only at action-firing moments (4-7 timestamps per day) — too sparse for proper time-series correlation against Haven's 5-min cadence, and provides no foundation for override detection. The poller fills both gaps.

New Grafana dashboard: `iaq-comparison.json` — Haven (return-air mix) vs thermostat (wall) for both temp and RH, plus a derived bias panel (positive = thermostat reads warmer than house mix), tVOC trend, blower-activity cross-validation (Haven CFM vs Refoss em:9), and manual override events.

Why this matters for the existing scheduler: the return-air mix is a **fundamentally better whole-home signal** than a single wall thermostat (volume-weighted average of every room contributing to the return). Hallway thermostats often read drier than the actual house average because moisture loads (cooking, showers, basements) get pulled into the return mix. The new data makes calibration vs. mixed-air the diagnosis path for "does the humid override threshold need re-tuning."

### Other research findings (parked)

- **`gridstatus` Python lib** — would be the right wrapper for PJM DataMiner2 once API key is available
- **Open-Meteo (ECMWF IFS, 9 km, free since Oct 2025)** — better than NWS at 1-3 day temp forecast; worth running as second weather source
- **Forecast bias correction** — affine intercept+slope per lead time (NOAA NCEP Office Note 520 method); needs paired observation history (Ecowitt sensor en route)
- **EMHASS / Predheat** — open-source MILP/MPC layers for HA; worth reading even if not adopting (reference thermal model + tariff abstractions)
- **Merlion (Salesforce, BSD-3)** — multivariate anomaly detection; better fit for the 18-channel Refoss data than the current univariate threshold approach
- **OpenADR 3.x** — ComEd doesn't currently expose a residential VTN; revisit if/when they do
- **Override logging** — every manual thermostat override should be tagged + correlated with current state; foundational for future ML/MPC. Cheap to add, deferred to later session.

Full research output: agent reports retained in session transcripts; key findings summarized in [docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md) and the open-questions section above.

---

## Updated Hardware Inventory (May 2026)

| Device | Role | Connection | Status |
|---|---|---|---|
| Rainforest EAGLE-3 | Smart meter gateway (billing-grade) | Ethernet, homelab VLAN, 192.168.20.192:443 | Connected & polling |
| Refoss EM16P | Per-circuit energy monitoring (2 mains + 16 branch) | Wi-Fi, homelab VLAN, 192.168.20.140 (HTTP, no auth) | Connected & polling |
| Honeywell VisionPRO 8000 thermostat | HVAC control | Via Control4 EA-5 (192.168.1.30) → Cinegration → TCC cloud | Active scheduling (May 2026) |
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
breakdown). Idempotency: SHA-256 of (account, from, to) → same Influx
(measurement, tags, timestamp) → safe to re-run same bill.

**Dashboard**: `grafana/dashboards/comed-bill-reconciliation.json` —
bill-vs-projected, EAGLE-vs-billed-kWh, capacity-charge tracker, and a
stub forward-projection panel. The projection panel's full formula
(supply-so-far + delivery estimate + capacity + taxes estimate + days-remaining
extrapolation) is sketched but lands as a follow-on after a few cycles
of data accumulate.

Spec: `docs/phase-8-comed-bill-ingest-design.md`
Plan: `docs/phase-8-comed-bill-ingest-plan.md`
