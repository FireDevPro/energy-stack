---
date: 2026-07-08
owner: chris
status: shipped
role-label: chris
---

# The Vigil — Controller Cockpit (rev 4.1)

Read-only wall board for the spike-only HVAC controller. A calm always-on
watchman: boring when the system is boring, earned drama only when a real price
spike engages a hold. Replaces the rev-3 "Reticule" decision-pipeline board,
which went blind at the 2026-07-06 rev-4 cutover.

Design of record: [`docs/superpowers/specs/2026-07-07-cockpit-vigil-design.md`](../../../docs/superpowers/specs/2026-07-07-cockpit-vigil-design.md)
(data contract) + the Claude Design handoff (visual/interaction brief).

## Run (canonical: Pi-lab compose service)

The cockpit runs as the `cockpit` service in this stack's `docker-compose.yml`:
one container serving the FastAPI backend (`/api/vigil/*`) and the single-file
frontend same-origin on **<http://192.168.20.10:8765/>**. Merging changes under
`deploy/energy-stack/cockpit/` to `main` deploys it. It reaches InfluxDB via the
compose service name and reads tier thresholds from a **read-only mount of the
controller's own `commissioning-controller.yaml`** (single source of truth — no
threshold drift).

The wall Surface (`surface-kiosk`) frames this URL as one page of its swipe
carousel; the swipe shell lives in the separate `FireDevPro/grafana-dashboards`
repo (`kiosk/index.html`), not here.

Ops (on Pi-lab): `docker compose logs -f cockpit`, `docker compose restart cockpit`.

## Dev loop (workstation)

One-click launcher (Windows). First-time setup:

```pwsh
cp deploy/energy-stack/cockpit/.env.example deploy/energy-stack/cockpit/.env.local
# edit .env.local — fill INFLUXDB_TOKEN (Pi-lab /home/chris/energy-stack/.env INFLUXDB_INIT_ADMIN_TOKEN)
pwsh deploy/energy-stack/cockpit/install-shortcut.ps1   # desktop icon
```

Double-click the Cockpit icon (or run `start-cockpit.ps1`). It sources
`.env.local`, kills any prior backend on `:8765`, and runs `uvicorn` — which
serves both the API and the board on `:8765`. There is **no separate frontend
server and no build step**: the board is one static HTML file. Stop with
`stop-cockpit.ps1`.

QA the board without waiting for a real spike: append `?demo=engaged`
(`resting|engaged|release|down`) to force a fixture state; `?debug=1` shows an
on-screen state switcher.

## Endpoints

| Endpoint | Cadence | Feeds |
|---|---|---|
| `GET /api/vigil/now` | ~15 s | hero price + tier, banners, context tiles, liveness |
| `GET /api/vigil/timeline?hours=24` | ~2 min | the 24-hour price ribbon + hold bands |
| `GET /api/vigil/events?limit=10` | ~2 min | recent spike episodes |
| `GET /api/health` | — | container healthcheck |

Backend modules: `vigil_now.py` / `vigil_timeline.py` / `vigil_events.py`
(assemblers) over `vigil_queries.py` (rev-4 Flux) + `vigil_derive.py` (pure
tier/hold/why/episode logic) + `vigil_config.py` (the mounted thresholds).

## Architecture

Read-only by design — no writes to the controller or device. Live state comes
from **InfluxDB only** (`comed.prices`, `hvac.thermostat`, `hvac.actions`,
`hvac.price_overlay`, `hvac.arm_mode`, `hvac.heartbeat`, `ecowitt.weather`); no
Loki dependency. Temperatures: `ecowitt` outdoor is °F as stored; the controller
writes `hvac.actions` setpoints in °C (converted at the read boundary); the CTK04
runs in °C-display mode (2026-06-02), so the poller's `hvac.thermostat *_f`
fields hold °C and the backend converts them for display. The client never
converts.

## Visual acceptance

**Chrome on the wall Surface is the visual oracle.** The board is high-fidelity;
recreate/verify pixels there, not in Chromium/Playwright screenshots (they have
rendered differently in this project and caused bad visual fixes). Per repo
policy the cockpit is observability, not critical-path — no heavy test
investment. Automated coverage is intentionally narrow:

- `backend/tests/test_vigil_derive.py` — pure derivation unit tests (tier, hold,
  why-line, episode grouping, °C→°F, avoided-cost).
- `backend/tests/test_vigil_contract.py` — assembler shape/logic against a fake
  QueryApi (resting/engaged) + a route test asserting JSON-serializability.
- `backend/tests/test_vigil_config.py` — parses the real controller config.

Run: `pytest deploy/energy-stack/cockpit/backend` (from the repo; or
`python -m pytest backend` from the cockpit dir). The real oracle beyond these is
a live smoke against the Pi Influx + Chrome on the wall.
