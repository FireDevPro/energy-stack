# Session Package: Phase 3.1 — Deploy InfluxDB + Grafana on Pi-lab

**From:** Claude (chat) — Architecture & Design
**To:** Claude Code — Implementation
**Project:** Home Energy Monitoring (`D:\Projects\energy-proxy`)
**Date:** April 2026

---

## Objective

Stand up InfluxDB and Grafana on Pi-lab as a new Docker compose stack. These will become the storage and visualization layer for the home energy monitoring system. No data is being written yet — this session is infrastructure only.

---

## Background

Read `D:\Projects\energy-proxy\PROJECT.md` for the full project context. The short version:

We're building a home energy monitoring system that aggregates data from multiple sources (ComEd real-time pricing, a Rainforest EAGLE-3 smart meter gateway, a Sense energy monitor, and eventually a Honeywell thermostat and Refoss EM16P circuit monitor). The architecture decision has been made to use **InfluxDB** for time-series storage and **Grafana** for all visualization — both real-time dashboards and historical analysis. The existing custom HTML dashboard will be retired.

A Python data collector (currently running on the Windows desktop) will be refactored and deployed to Pi-lab in a future session to poll all data sources and write to InfluxDB. That's not part of this session.

---

## Design Decisions (already made — do not revisit)

- **InfluxDB over Prometheus** — the data is metering/IoT (energy demand, pricing, temperatures), not infrastructure health. InfluxDB's push model and query language are a better fit.
- **Grafana as the single visualization layer** — replaces the custom HTML dashboard for both real-time and historical views.
- **Everything on Pi-lab** — the EAGLE-3 smart meter gateway is on the same VLAN (192.168.20.192), and the Pi has plenty of capacity (16GB RAM, 916GB NVMe at 3% usage).

---

## Pi-lab Current State

- **IP:** 192.168.20.10, Homelab VLAN 20
- **Hardware:** Raspberry Pi 5, 16 GB RAM, NVMe 916 GB (3% used), ARM Cortex-A76 4-core
- **OS:** Debian 13 (trixie)
- **Existing containers:** Pi-hole (53, 80), Unbound (5335), Portainer (8000, 9443)
- **Existing compose:** `~/dns-stack/docker-compose.yml` manages Pi-hole + Unbound
- **Portainer:** Standalone container, not in a compose stack

**Do not touch the dns-stack or Portainer.** The energy stack is a separate compose project.

---

## What Needs to Exist When This Session is Done

1. **A new compose stack** (suggest `~/energy-stack/` but use your judgment) running InfluxDB and Grafana.

2. **InfluxDB** — accessible on port 8086. Needs an initial database/bucket for energy data. Think about retention policies — we'll want high-resolution data (every 30-60 seconds) for recent history and downsampled data for long-term. The data that will eventually flow in:
   - EAGLE-3 smart meter: instantaneous demand (kW), cumulative energy (kWh) — polled ~30s
   - ComEd pricing: 5-minute price intervals (¢/kWh), hourly averages — polled ~60s
   - Sense energy: whole-home power (W), per-device power, daily/weekly/monthly kWh — polled ~60s
   - Honeywell thermostat: indoor/outdoor temp, humidity, setpoints, equipment status — polled ~600s
   
3. **Grafana** — accessible on port 3000. InfluxDB should be pre-configured as a data source. No dashboards yet — those come after data starts flowing.

4. **Persistence** — both InfluxDB data and Grafana config must survive container restarts and Pi reboots. Use named volumes or bind mounts — your call.

5. **Credentials** — handle InfluxDB admin credentials and Grafana admin password sensibly. Don't hardcode in the compose file. Use an `.env` file, environment variables, or whatever pattern fits.

---

## Networking Notes

- Grafana on port 3000 and InfluxDB on port 8086 will be reachable from the Trusted VLAN (192.168.10.0/24) — the existing ZBF rule "Allow Trusted to Homelab" already covers this. No firewall changes needed.
- The future Python data collector will run on the same Pi-lab host, so it can reach InfluxDB on localhost. Keep that in mind for the Docker networking setup.

---

## InfluxDB Version Consideration

InfluxDB 1.x vs 2.x is a meaningful choice. 1.x is simpler (InfluxQL, databases, retention policies). 2.x is the current product (Flux query language, organizations/buckets, built-in UI). Both work with Grafana. Consider what makes sense for a single-user homelab energy monitoring use case — we don't need enterprise features, but we also don't want to deploy something that's going to be EOL'd. Make the call and document your reasoning.

---

## Validation

When done:
- Grafana web UI is reachable at `http://192.168.20.10:3000` from the desktop (192.168.10.42)
- InfluxDB is reachable at `http://192.168.20.10:8086`
- InfluxDB has a database/bucket ready for energy data
- Grafana has InfluxDB configured as a data source
- Everything survives a `docker compose down && docker compose up -d`
- Document what you deployed, what ports, what credentials pattern, and any decisions you made

---

## Out of Scope

- Python energy proxy deployment (future session)
- Dashboard creation (future session — needs data flowing first)
- EAGLE-3 integration code (future session)
- DNS stack, Portainer, or any existing infrastructure
