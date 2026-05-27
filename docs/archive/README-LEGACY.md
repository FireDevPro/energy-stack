---
date: 2026-05-17
owner: chris
status: archived
archived_at: 2026-05-25
role-label: chris
---

> **Archived 2026-05-25.** Operator-flavored README from when this repo was a personal
> monitoring tool. Retained for provenance only. Current intent and reading path:
> [README.md](README.md).

# Home Energy Monitoring & HVAC Optimization

Real-time and historical residential energy monitoring with **dynamic-pricing-aware HVAC scheduling**, running as a Docker Compose stack on Pi-lab. Built around ComEd Hourly Pricing + PJM 5CP avoidance.

> **Research project**: starting summer 2026, this stack runs a pre-registered SCED field study comparing the CTK04AE thermostat's programmed schedule (Arm A) against the active `hvac-scheduler` (Arm B: RTP/DTOD/5CP-risk-aware RBC with safety supervisor), targeting a peer-reviewed publication. Binding pre-registration spec: **[docs/plans/sced-rebaseline-spec-2026-05-13.md](docs/plans/sced-rebaseline-spec-2026-05-13.md)** (frozen at OSF-filing commit, target 2026-05-30).

> Full project history, decisions, and roadmap: **[PROJECT.md](PROJECT.md)**.
> Operational guide for the stack: **[deploy/energy-stack/README.md](deploy/energy-stack/README.md)**.
> Per-service detail: **[docs/SERVICES.md](docs/SERVICES.md)**.
> HVAC scheduler logic and thermostat fallback: **[docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md)**.

## Architecture

```
                                                          ┌──────────────────────────────┐
EAGLE-3        (local HTTPS,   30s) ─┐                    │                              │
ComEd API      (public,        60s) ─┤                    │ Grafana            (3000)    │
Refoss EM16P   (local HTTP,    30s) ─┤                    │  (sole visualization layer)  │
NWS forecast   (public,      30min) ─┼───► InfluxDB 2.7 ──► Telegram notifier            │
PJM DataMiner2 (public, per-feed)   ─┤    (energy + ────► │  (daily summary + alerts)    │
HAVEN cloud    (public,    5 min)   ─┤    energy-longterm)│                              │
Ecowitt GW1200 (push, ~60s)         ─┤                    │ HVAC scheduler ──► Control4 ─┼─► Amana CTK04AE
Control4 → CTK04AE (10 min reads)   ─┘                    │  (day-type @ 21:00,          │   thermostat
                                                          │   safety supervisor on each  │
                                                          │   setpoint push)             │
                                                          │ hvac-scheduler-watchdog      │
                                                          │ (heartbeat when scheduler    │
                                                          │  silent > 10 min)            │
                                                          │ Loki + Promtail              │
                                                          └──────────────────────────────┘
```

Per-service detail (env vars, fields written, healthchecks) in [`docs/SERVICES.md`](docs/SERVICES.md). 24-hour activity timeline with all poller cadences and decision milestones in [`docs/SCHEDULER_TIMING.md`](docs/SCHEDULER_TIMING.md).

## Current Status (May 2026)

| Component | Status |
|---|---|
| InfluxDB 2.7 + Grafana 11.4.0 | Active |
| `comed-poller` (60 s, 5min + hourly_avg) | Active |
| `eagle-poller` (30 s, billing-grade demand + summation) | Active |
| `refoss-poller` (30 s, 18 channels + system) | Active |
| `nws-poller` (30 min, forecast today/tomorrow/day2 + active alerts) | Active |
| `pjm-dm2-poller` (per-feed schedule: DA LMP, load forecast, metered, peak, NSPL) | Active (May 2026) |
| `hvac-scheduler` (Control4 → Amana CTK04AE, with safety supervisor) | Active |
| `hvac-scheduler-watchdog` (writes `hvac.heartbeat controller_alive=false` when no `hvac.arm_mode` rows in 10 min) | Active |
| `thermostat-poller` (continuous 10-min Control4 reads + override detection) | Active (May 2026) |
| `haven-ingest` (HAVEN cloud API → `haven.indoor` + `haven.outdoor`) | Active (May 2026) |
| `ecowitt-ingest` (GW1200 push → `ecowitt.weather`; WS90 sun-exposed + WN31 shaded canonical) | Active (May 2026) |
| `telegram-notifier` (daily 8 AM + 5 min alert checker) | Active |
| `loki` + `promtail` (log aggregation) | Active |
| ~~`webdashboard` + `webdashboard-api`~~ | Retired May 2026 — consolidated on Grafana |
| `influx-init` (one-shot: `energy-longterm` bucket + 1-min downsample task) | Active (May 2026) |
| ComfortNet pipeline (`mosquitto` + `telegraf`, profile `mqtt`) + Pi-3B publisher | Active (May 2026) — `hvac.comfortnet` flowing |
| Grafana dashboards (overview, full, hvac-scheduler, comed-bill-reconciliation, iaq-comparison) | Active |
| Restic → Backblaze B2 nightly (root cron @ 02:00, Telegram alerts) | Active |
| `sense-poller` | Retired (Phase 5 — replaced by Refoss) |

## Data Sources

### Rainforest EAGLE-3 (billing-grade meter)
Local HTTPS at `192.168.20.192:443`, basic auth, EAGLE-200/3 Local API. Reads instantaneous demand (kW) and cumulative delivered/received energy (kWh) from the ComEd smart meter via Zigbee HAN. Influx measurement: `eagle.meter`.

### ComEd Hourly Pricing
Public API at `hourlypricing.comed.com/api`. 5-minute price intervals (¢/kWh) plus current-hour average. Influx measurement: `comed.prices` (tag `period_type` = `5min` | `hourly_avg`). **Day-ahead forecast is NOT exposed by ComEd's public API** — true day-ahead source is PJM DataMiner2 (see below).

### PJM Data Miner 2 (zonal market context)
Public API at `api.pjm.com/api/v1`, Non-Member tier (6 calls/min, free). Per-feed schedule: day-ahead LMP at 17:00 CT, real-time hourly LMP backfilled hourly, 5-min instantaneous load every 5 min, 7-day load forecast at 06:00 + 13:00 CT, weekly metered load Sundays 02:00 CT, RTO peak forecast in cooling season, annual NSPL Dec 1. Influx measurements: `pjm.lmp_da_hourly`, `pjm.lmp_rt_hourly`, `pjm.inst_load`, `pjm.load_forecast`, `pjm.metered_load`, `pjm.peak_forecast_rto`, `pjm.nspl_zonal`, plus `pjm.feed_status` + `pjm.poller_heartbeat` for poller self-observability. `pjm.coincident_peak` is written separately by an annual scrape of the official PJM 5CP PDF. See [`docs/PJM_DM2_INTEGRATION.md`](docs/PJM_DM2_INTEGRATION.md) and [`docs/PJM_DM2_FEEDS.md`](docs/PJM_DM2_FEEDS.md).

### HAVEN IAQ
Official HAVEN Pro API at `havenapi.tzoa.io` (Auth0 refresh-token auth, bootstrap captured from browser login). Polls indoor sensor (return-air mix: temp/RH/tVOC continuous; PM2.5 + airflow CFM flow-dependent) and paired outdoor station every 5 min. Influx measurements: `haven.indoor`, `haven.outdoor`.

### Refoss EM16P (per-circuit local)
Local HTTP JSON-RPC at `192.168.20.140/rpc`. Single `Refoss.Status.Get` call returns all 18 `em:N` channels (2 split-phase mains + 16 branches) with power, voltage, current, power factor, and day/week/month energy buckets. Channel labels pulled from device's own `Refoss.Config.Get` and refreshed on `cfg_rev` change. Influx measurements: `refoss.channel`, `refoss.system`.

### National Weather Service (NWS)
Public API at `api.weather.gov` (no key). Pulls hourly forecast, daily high/low/dewpoint summaries for today/tomorrow/day2, and active heat/cold advisories. Influx measurement: `nws.forecast` (tagged by `for_period`).

### Amana CTK04AE thermostat
Read-write via **Control4 EA-5** controller (`192.168.1.30`) using **pyControl4 v2.0.2**. Indoor temp, humidity, setpoints, HVAC state, fan/hold mode. The CTK04AE is a Honeywell-OEM whitelabel that natively speaks RedLINK Wi-Fi (Control4's Cinegration driver bridges via the same RedLINK gateway that connects to TCC) and the CT-485 communicating bus (read-only sniffed by [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet) for `hvac.comfortnet`).

## HVAC Optimization

The scheduler classifies tomorrow's day-type (`MILD` / `NORMAL` / `HOT_5CP_RISK` / `HOT_STREAK_DAY1`) at **21:00 local** based on NWS forecast + day-after lookahead, picks a schedule, and pushes setpoints + fan mode to the thermostat at each scheduled time.

Key constraints encoded in the schedules (full table in [HVAC_LOGIC.md](docs/HVAC_LOGIC.md) and [SCHEDULER_TIMING.md](docs/SCHEDULER_TIMING.md)):
- **Pre-cool** at off-peak rates to bank thermal mass — start time is day-type-dependent (03:00 CT on `HOT_STREAK_DAY1`, 04:00 CT on `HOT_5CP_RISK`, 06:00 CT on `NORMAL`), runs until coast.
- **Coast** through ComEd peak pricing window — 12:00–19:00 CT on `HOT_5CP_RISK`, 13:00–19:00 CT on `NORMAL`.
- **Dynamic shutoff** on HOT days driven by the real-time price-overlay layer (scarcity tier >= 20c/kWh -> 85F effective cool; no fixed shutoff clock; see [HVAC_LOGIC.md HOT_5CP_RISK section](docs/HVAC_LOGIC.md#hot_5cp_risk---85f-max-or-apparent--90f-per-experiment_design-appendix-a)). PJM and ComEd 5CP capacity-risk signals are planning/telemetry only (informs day-ahead pre-cool deepening at the 21:00 daily decision, recorded in `hvac.5cp_state` / `fivecp_eval` for post-hoc analysis); they do NOT independently force live setpoint changes per binding spec §11 #14. Capacity-charge impact computed via PJM OATT Attachment M-2's CPLC formula; see [HVAC_LOGIC.md "Capacity peak context"](docs/HVAC_LOGIC.md#capacity-peak-context-pjm-5cp--comed-5cp).
- **Auto-mode safe**: heat setpoint floor 65°F always paired with cool setpoint to satisfy Honeywell ISU 300 deadband
- **Humid override**: dewpoint > 65°F drops the coast cool setpoint to keep low-stage AC running for latent removal

Detailed schedules + thermostat fallback (programmed into the CTK04AE directly): **[docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md)**.

## Operator tooling

**Controller Cockpit** — workstation-local read-only dashboard at [`tools/cockpit/`](tools/cockpit/). FastAPI backend on `:8000` proxies the Pi-lab InfluxDB + Loki feed; Vite/React frontend on `:5173` polls the backend every 5 s. Surfaces thermostat state, scheduler decision flow (Weather → Day Type → Schedule / RTP Spike / 5CP → Winner → Supervisor → Action), price tier, controller liveness via `hvac.heartbeat`, and feed-health for ComEd / NWS / PJM / Refoss / EAGLE / Thermostat. Not deployed via compose — runs on the operator's workstation against Pi-lab over the homelab VLAN.

## Quick Start

The system runs on Pi-lab. To bring it up:

```bash
ssh chris@192.168.20.10
cd ~/energy-stack
docker compose ps                       # check status
docker compose up -d                    # start anything stopped
docker compose logs -f hvac-scheduler   # tail one service
```

For first-time bootstrap (SOPS, `.env`, secrets), see [deploy/energy-stack/README.md](deploy/energy-stack/README.md).

## Authoring & Deployment

Edit on Windows under `D:\Projects\energy-proxy\deploy\energy-stack\`, then sync to Pi-lab:

```bash
rsync -av --delete --exclude '.env' \
  "D:/Projects/energy-proxy/deploy/energy-stack/" \
  chris@192.168.20.10:~/energy-stack/
```

Rebuild only the service you changed:

```bash
ssh chris@192.168.20.10 \
  "cd ~/energy-stack && docker compose build hvac-scheduler && docker compose up -d hvac-scheduler"
```

## Backup & Restore

Restic to Backblaze B2 nightly via root cron at 02:00. Snapshots include `~/energy-stack`, `~/chris-brain`, `~/dns-stack`, `~/Network_Management`, `~/.ssh`, `~/.config/restic`, `/usr/local/bin/pi-backup.sh`, plus a fresh InfluxDB backup staged into `/tmp` per-run. Telegram notification on success and failure (failure includes last 20 log lines for diagnosis).

Full restore procedure: **[deploy/energy-stack/backup/RESTORE.md](deploy/energy-stack/backup/RESTORE.md)**.

## Network Layout

UniFi-managed home network with multiple VLANs. Relevant for ops:

| VLAN | Subnet | Purpose | Notes |
|---|---|---|---|
| 1 | `192.168.1.0/24` | Default LAN — AV, IoT, Control4 | Most home devices |
| 10 | `192.168.10.0/24` | Trusted — workstation/laptop | **Routes through Mullvad WireGuard at the gateway** (US-CHI + SE-STO tunnels with kill switch). Some destinations (PJM, banks) block VPN exits — disable the route at the UniFi level temporarily for those. |
| 20 | `192.168.20.0/24` | Homelab — Pi-lab, EAGLE, Refoss | No VPN; all energy-stack containers live here. **This is what reaches the public Internet directly.** |

## Ad-hoc EAGLE-3 testing

```powershell
.\Test-Eagle.ps1 -CloudId "YOUR_CLOUD_ID" -InstallCode "YOUR_INSTALL_CODE"
```

## Roadmap

See **[PROJECT.md](PROJECT.md)** for the full phased history. Critical-path items for the June 1, 2026 SCED experiment start (binding spec: [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](docs/plans/sced-rebaseline-spec-2026-05-13.md)):

- **Pre-OSF spec §11 deliverables** — 13 binding items including arm-calendar + `SCHEDULER_MODE=experiment` gating in `hvac-scheduler`, mode telemetry (`hvac.arm_mode`), switch-event logging, input-feed health, controller watchdog, `rt_hrl_lmps` poller, DTOD analysis-rate table, CTK04AE Arm A schedule freeze ([`docs/THERMOSTAT_ARM_A_SCHEDULE.md`](docs/THERMOSTAT_ARM_A_SCHEDULE.md)), dry-run guard audit, analysis-pipeline rewrite, NOAA fallback station selection, day-type schedule verification, shadow validation run. Progress in [`docs/plans/pre-osf-doc-audit-execution-2026-05-18.md`](docs/plans/pre-osf-doc-audit-execution-2026-05-18.md).
- **OSF pre-registration filing** — binding spec frozen (`status: frozen` + `frozen_at_commit: <SHA>`) at the OSF-filing commit, target 2026-05-30. Filing uses the OSF open-ended template with the spec attached via Zenodo DOI.

Other open items, parked behind the experiment:

- **ComfortNet decoder extension for write-side opcodes** — the live publisher decodes the read side of the user-menu protocol; capturing a setting change to extract the SetUserMenu opcode is a follow-on (see `Promithius-DR/comfortnet` `docs/SETTING_REVIEW.md`)
- **Open-Meteo (ECMWF) as second weather source** — Free; better than NWS for 1-3 day temp since their Oct 2025 IFS upgrade
- **Forecast bias correction** — pair NWS observations against local Ecowitt sensor (en route May 2026), fit per-station affine bias by lead time
- **Pre-cool depth retune** — research suggests `HOT_PRE_COOL` could shift 68°F → 71-72°F starting 3am instead of 4am @ 68°F. May inform a post-experiment retune; mid-experiment variant changes would be protocol deviations.
- **Fridge anomaly detection upgrade** to Merlion (multivariate, currently univariate threshold)
- **Phase 8 forward-projection panel** — full bill-vs-projected formula lands after a few cycles of bill data accumulate
