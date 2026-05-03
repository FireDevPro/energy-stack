# Home Energy Monitoring & HVAC Optimization

Real-time and historical residential energy monitoring with **dynamic-pricing-aware HVAC scheduling**, running as a Docker Compose stack on Pi-lab. Built around ComEd Hourly Pricing + PJM 5CP avoidance.

> Full project history, decisions, and roadmap: **[PROJECT.md](PROJECT.md)**.
> Operational guide for the stack: **[deploy/energy-stack/README.md](deploy/energy-stack/README.md)**.
> Per-service detail: **[docs/SERVICES.md](docs/SERVICES.md)**.
> HVAC scheduler logic and thermostat fallback: **[docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md)**.

## Architecture

```
                                                          ┌──────────────────────────────┐
EAGLE-3       (local HTTPS, 30s)  ─┐                      │                              │
ComEd API     (public,       60s) ─┤                      │ Grafana            (3000)    │
Refoss EM16P  (local HTTP,   30s) ─┼───► InfluxDB 2.7 ────► Web dashboard      (8081)    │
NWS forecast  (public,     30min) ─┤    (energy bucket)   │ webdashboard-api   (8082)    │
                                   │                      │                              │
                                   │                      │ Telegram notifier            │
                                   │                      │  (daily summary + alerts)    │
                                   │                      │                              │
                                   │                      │ HVAC scheduler ──► Control4 ─┼─► Honeywell
                                   │                      │  (day-type decision @ 21:00) │   thermostat
                                   │                      │                              │
                                   │                      │ Loki + Promtail              │
                                   └──── log shipping ────► (log aggregation)            │
                                                          └──────────────────────────────┘
```

## Current Status (May 2026)

| Component | Status |
|---|---|
| InfluxDB 2.7 + Grafana 11.4.0 | Active |
| `comed-poller` (60 s, 5min + hourly_avg) | Active |
| `eagle-poller` (30 s, billing-grade demand + summation) | Active |
| `refoss-poller` (30 s, 18 channels + system) | Active |
| `nws-poller` (30 min, forecast today/tomorrow/day2 + active alerts) | Active |
| `hvac-scheduler` (Control4 → Honeywell VisionPRO 8000) | Active |
| `thermostat-poller` (continuous 10-min Control4 reads + override detection) | Active (May 2026) |
| `haven-ingest` (Haven IAQ CSV → InfluxDB) | Active (May 2026) |
| `telegram-notifier` (daily 8 AM + 5 min alert checker) | Active |
| `webdashboard` + `webdashboard-api` (live data, port 8081) | Active |
| `loki` + `promtail` (log aggregation) | Active |
| Grafana dashboards (`home-energy-full`, `home-energy-overview`, `hvac-scheduler`) | Active |
| Restic → Backblaze B2 nightly (root cron @ 02:00, Telegram alerts) | Active |
| `sense-poller` | Retired (Phase 5 — replaced by Refoss) |

## Data Sources

### Rainforest EAGLE-3 (billing-grade meter)
Local HTTPS at `192.168.20.192:443`, basic auth, EAGLE-200/3 Local API. Reads instantaneous demand (kW) and cumulative delivered/received energy (kWh) from the ComEd smart meter via Zigbee HAN. Influx measurement: `eagle.meter`.

### ComEd Hourly Pricing
Public API at `hourlypricing.comed.com/api`. 5-minute price intervals (¢/kWh) plus current-hour average. Influx measurement: `comed.prices` (tag `period_type` = `5min` | `hourly_avg`). **Day-ahead forecast is NOT exposed by ComEd's public API** — see [PROJECT.md](PROJECT.md) decision log.

### Refoss EM16P (per-circuit local)
Local HTTP JSON-RPC at `192.168.20.140/rpc`. Single `Refoss.Status.Get` call returns all 18 `em:N` channels (2 split-phase mains + 16 branches) with power, voltage, current, power factor, and day/week/month energy buckets. Channel labels pulled from device's own `Refoss.Config.Get` and refreshed on `cfg_rev` change. Influx measurements: `refoss.channel`, `refoss.system`.

### National Weather Service (NWS)
Public API at `api.weather.gov` (no key). Pulls hourly forecast, daily high/low/dewpoint summaries for today/tomorrow/day2, and active heat/cold advisories. Influx measurement: `nws.forecast` (tagged by `for_period`).

### Honeywell VisionPRO 8000 thermostat
Read-write via **Control4 EA-5** controller (`192.168.1.30`) using **pyControl4 v2.0.2**. Indoor temp, humidity, setpoints, HVAC state, fan/hold mode. RedLINK has no local API — Control4's Cinegration driver bridges TCC cloud to the local Director, which the scheduler talks to over the LAN with token persistence + reauth-on-401.

## HVAC Optimization

The scheduler classifies tomorrow's day-type (`MILD` / `NORMAL` / `HOT_5CP_RISK` / `HOT_STREAK_DAY1`) at **21:00 local** based on NWS forecast + day-after lookahead, picks a schedule, and pushes setpoints + fan mode to the thermostat at each scheduled time.

Key constraints encoded in the schedules:
- **Pre-cool 4-6am** at off-peak rates to bank thermal mass
- **Coast 12-7pm** through ComEd peak pricing window
- **Hard 5CP shutoff 14:00-18:00 CDT** on HOT days (PJM peak avoidance — each kW shaved ≈ $240-480/yr in next-year capacity charges)
- **Auto-mode safe**: heat setpoint floor 65°F always paired with cool setpoint to satisfy Honeywell ISU 300 deadband
- **Humid override**: dewpoint > 65°F drops the coast cool setpoint to keep low-stage AC running for latent removal

Detailed schedules + thermostat fallback (programmed into VisionPRO directly): **[docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md)**.

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

See **[PROJECT.md](PROJECT.md)** for the full phased history. Open items at time of writing:

- **PJM DataMiner2 API access** — pending tech-support email (non-member accounts can't self-provision through Account Manager)
- **Pre-cool depth retune** — academic research suggests `HOT` schedule could shift 68°F → 71-72°F starting at 3am instead of 4am @ 68°F (90%+ of peak shift, materially less off-peak kWh)
- **Open-Meteo (ECMWF) as second weather source** — Free; better than NWS for 1-3 day temp since their Oct 2025 IFS upgrade
- **Override logging** — tag every manual thermostat override with state snapshot; foundational for any future ML/MPC layer
- **Forecast bias correction** — pair NWS observations against local Ecowitt sensor (cart en route May 2026), fit per-station affine bias by lead time
- **Fridge anomaly detection upgrade** to Merlion (multivariate, currently univariate threshold)
