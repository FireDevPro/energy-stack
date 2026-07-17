---
date: 2026-07-17
owner: chris
status: current
role-label: code-team
---

# Visualization & Dashboards (current state)

**TL;DR:** Grafana is deployed but **unused**. The live UI is the **surface-kiosk
wall display** — a Chrome kiosk that cycles two custom dashboards: a **weather**
board and the **Vigil** HVAC-controller cockpit. Neither is Grafana.

> **Naming trap** (it has repeatedly misled readers and agents): the custom kiosk
> code lives in a repo named **`FireDevPro/grafana-dashboards`**, and a `grafana`
> container still runs in the compose file. Neither means Grafana is the UI — the
> name is legacy from when Grafana *was* the dashboard layer. To confirm what's
> live, look at the surface host (below), not the repo name.

## What's live: the surface-kiosk wall display

Host **`surface-kiosk`** (ssh alias) — a small box running X + Chrome in kiosk
mode plus nginx. **No Grafana on this host.** It shows a swipe carousel
(`kiosk/index.html`) of two pages:

### 1. Weather dashboard — custom vanilla JS (not Grafana)
- **Code:** `FireDevPro/grafana-dashboards` repo, `kiosk/weather/` (`weather-data.js`,
  `cards.js`, `app.js`, `sky-engine.js`, `nws-alerts.js`, `icons.js`, `index.html`).
- **Served by:** nginx on `surface-kiosk`, `root /opt/grafana-dashboards/kiosk/`
  — straight out of the git checkout (`GET /weather/` → 200).
- **Data:** `weather-data.js` queries InfluxDB (Flux) through nginx's `/influxdb/`
  same-origin proxy → `192.168.20.10:8086`. Today it reads one measurement
  (`ecowitt.weather`) via a `FIELDS` map and exposes `getCurrent()/getSeries()`
  that the UI consumes.
- **Deploy:** `ssh surface-kiosk 'bash /opt/grafana-dashboards/kiosk/deploy-kiosk.sh'`
  = `git pull` on `/opt/grafana-dashboards` + a cache-safe Chrome bounce. So the
  flow is **merge to the grafana-dashboards repo → pull on the surface**. (Note:
  a workstation clone can be stale — the surface pulls independently; check the
  surface's `git log` before assuming your local copy is current.)

### 2. Vigil cockpit — the HVAC controller board
- **Code:** THIS repo, `deploy/energy-stack/cockpit/` (FastAPI backend
  `/api/vigil/*` + a single static HTML frontend). See
  [`cockpit/README.md`](../deploy/energy-stack/cockpit/README.md) +
  [the Vigil design spec](superpowers/specs/2026-07-07-cockpit-vigil-design.md).
- **Served by:** the `cockpit` compose service on Pi-lab,
  **`http://192.168.20.10:8765/`**. The kiosk iframes this URL as its second page.
- **Deploy:** merge changes under `deploy/energy-stack/cockpit/` to `main` (normal
  energy-proxy deploy).

## Grafana: deployed but unused
The `grafana` container (+ `grafana-image-renderer`) still runs in the
`energy-stack` compose (Pi-lab :3000; provisioned dashboards under
`deploy/energy-stack/grafana/dashboards/`, legacy JSON also in the
grafana-dashboards repo's `energy-proxy/`). **Nobody views it; it is not part of
any live workflow** — retained/vestigial and a removal candidate, **not** the
visualization layer. [`docs/SERVICES.md#grafana`](SERVICES.md#grafana) documents
the still-running service for reference only.

## Adding a panel — where it goes
- **Air quality / weather / outdoor conditions →** the **weather** dashboard:
  add a query in `grafana-dashboards/kiosk/weather/weather-data.js` + a card in
  `cards.js`/`app.js`; deploy via `deploy-kiosk.sh`.
- **HVAC / controller / return-air →** the **Vigil** cockpit: add a Flux query in
  `deploy/energy-stack/cockpit/backend/vigil_queries.py` + a panel in the
  frontend; merge to `main`.
