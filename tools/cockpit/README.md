---
date: 2026-05-17
owner: chris
status: phase-1-shipped
role-label: chris
---

# Controller Cockpit

Workstation-local read-only dashboard for the HVAC controller. Live-tape
complement to the daily n8n/Telegram commissioning report.

## Run

One-click launcher (Windows). First-time setup:

```pwsh
cp tools/cockpit/.env.example tools/cockpit/.env.local
# edit .env.local — fill INFLUXDB_TOKEN + confirm URLs.
# Pi-lab token lives in /home/chris/energy-proxy/.env as INFLUXDB_TOKEN.

pwsh tools/cockpit/install-shortcut.ps1   # creates Cockpit.lnk on desktop
```

Then double-click the Cockpit desktop icon. The launcher:

- sources `tools/cockpit/.env.local` (gitignored)
- kills any prior cockpit backend on `:8000` / Vite on `:5173`
  (only if the bound process command line matches the cockpit — leaves
  unrelated python/node alone)
- spawns uvicorn (live mode) and Vite in two visible pwsh windows
- waits for both to be healthy, then opens <http://localhost:5173/>

Close the two spawned pwsh windows to stop the cockpit.

Ports `:8000` (backend) and `:5173` (frontend) are pinned in
`start-cockpit.ps1` and `vite.config.ts` — change both together if
either ever needs to move. There is deliberately no
`COCKPIT_BACKEND_PORT` knob, because letting Vite's proxy target drift
from the backend's actual port silently serves stale data without
erroring.

Toggle to a fixture (offline / demo / screenshots) via `?fixture=<name>`:

- <http://localhost:5173/> — live data (default)
- <http://localhost:5173/?fixture=summer_normal> — Arm B-active, July
  afternoon, 1pm fire-time tick, Schedule winning, humid override active
- <http://localhost:5173/?fixture=shadow_current> — pre-experiment shadow
  mode, `mode_actual=outside-window`, dry-run action

Toggle to a fixture (offline / demo / screenshots) via `?fixture=<name>`:

- <http://localhost:5173/> — `summer_normal` (Arm B-active, July afternoon,
  1pm fire-time tick, Schedule winning, humid override active, action applied)
- <http://localhost:5173/?fixture=shadow> — `shadow_current` (pre-experiment
  shadow mode, `mode_actual=outside-window`, dry-run action, dashed
  Supervisor → Action edge, `SHADOW` badge)

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | dev server on `:5173` (proxies `/api/*` to backend) |
| `npm run build` | production build to `dist/` |
| `npm run typecheck` | `tsc -b --noEmit` |
| `npm run test` | Vitest run (acceptance + edge unit + live-fetch tests) |
| `npm run test:watch` | Vitest watch mode |
| `npm run lint` | ESLint |
| `pytest tools/cockpit/backend/tests/` | backend pytest (snapshot + freshness) |
| `uvicorn tools.cockpit.backend.app:app --reload` | run backend dev server |

## Architecture

Read-only by design. No drag, no setpoint controls, no writes back to the
scheduler. Snapshot is assembled from existing `decision_trace.*` Loki logs
and `hvac.*` Influx measurements — cockpit cannot introduce control-path
behavior changes.

The Action node shows whatever `hvac.actions` row is most recent and
does **not** try to distinguish synthetic commissioning emissions from
real controller behavior — that distinction must come from the writer
(e.g. a `source=commissioning` tag on the row). Without an upstream
marker, cockpit-side filtering by name pattern would risk hiding real
rows or missing synthetic ones, so by policy the cockpit just renders
what's there.

Three phases delivered, one follow-on:

- **Phase 1** — mock fixtures, full UI rendering, no backend.
- **Phase 2** — 7 additional fixtures + node-state edge cases.
- **Phase 3 (this)** — `tools/cockpit/backend/` FastAPI proxy at `:8000`
  serving the same `Snapshot` JSON shape. Frontend polls every 5s with
  fixture fallback on network errors and explicit fixture mode via
  `?fixture=`. Backend tests assert the snapshot contract; live
  Influx/Loki queries are stubbed (NotImplementedError) and land in a
  follow-on PR.
- **Phase 3 follow-on** — wire `tools/cockpit/backend/influx.py` and
  `loki.py` against the Pi-lab InfluxDB + Loki. Snapshot contract stable
  at this point; the follow-on only fills the query builders.

Locked design decisions live in [`docs/plans/cockpit-plan.md`](../../docs/plans/cockpit-plan.md).

## Layout

```
┌─────────────────────────────────────────────────────────┐
│ Header: scheduler_mode | arm-mode | alive | time        │
├─────────────────────────────────────────────────────────┤
│ Feed health: ◉ ComEd  ◉ NWS  ◯ PJM-fcst ◉ Refoss ◉…    │
├──────────────────┬──────────────────────────────────────┤
│  THERMOSTAT 30%  │  DECISION FLOW (React Flow)    70%   │
│  ring + temp     │  Weather → DayType → [3 lanes]       │
│  setpoints       │             → Winner → Supervisor    │
│  price chip      │             → Action                 │
│  scheduler line  │                                      │
│  tick footer     │                                      │
└──────────────────┴──────────────────────────────────────┘
```

The right pane is the parallel-then-merge decision flow with 8 nodes:

```
                    ┌─ Schedule ──┐
Weather → Day Type ─┼─ Price ─────┼─→ Winner → Supervisor → Action
                    └─ 5CP ───────┘
```

The active winning lane glows (sky-400 ring); losers dim; not-applicable
nodes go gray. `decision_trace.layer_resolution.winning_layer` drives the
selection. The Supervisor → Action edge is solid when writes are
physically happening (`scheduler_mode=production` OR
`experiment + arm_mode=B-active`) and dashed otherwise.

## Stack

Vite, React 19, TypeScript, `@xyflow/react` v12, Framer Motion 12,
Tailwind 3, Vitest 4 + Testing Library + jsdom. Dark zinc ops-dashboard
theme. Inter UI font + JetBrains Mono for IDs/timestamps.

## Visual acceptance

**Chrome on the operator's real display is the visual oracle.** Vitest +
jsdom is non-visual regression only — node-state derivation, data
attributes, edge state metadata. Chromium / Playwright screenshots are
NOT authoritative; they have rendered differently from Chrome in this
project and caused bad visual fixes. Do not adjust layout or CSS to
satisfy Chromium / 1280x720 / jsdom unless the operator explicitly
reports the problem in Chrome.

## Tests

Two suites:

- `src/__tests__/cockpit.acceptance.test.tsx` — outside-in acceptance test.
  Renders the full app against both Phase 1 fixtures, asserts header
  chips, feed-health strip, thermostat card, all 8 flow node `role_state`s,
  action badge, tick footer, and reduced-motion gating.
- `src/__tests__/edges.test.tsx` — unit tests for `ActiveEdge` and
  `ActionEdge` components in isolation. Verifies the `data-animated` and
  `data-edge-style` data-attribute derivation directly. Exists because
  React Flow's edge SVGs don't render fully in jsdom (viewport
  measurement is incomplete), so the acceptance test asserts edge state
  via `data-*` mirrors on the `<DecisionFlow>` wrapper rather than on the
  edge `<g>` elements themselves. The edge unit tests close that
  coverage gap by exercising the edge components directly.

## Known jsdom limitations

React Flow v12 measures node and viewport dimensions via `ResizeObserver`
and `getBoundingClientRect`. `setup.ts` mocks these to return realistic
stub values and to fire the observer callback synchronously, which is
enough for **node** rendering and DOM presence assertions. **Edge** SVGs
do not fully render in jsdom regardless — the renderer requires a real
viewport. Visual edge state (marching dash, dashed vs solid Supervisor →
Action edge, glowing winning lane) is verified by:

1. Acceptance test wrapper-level data attributes (`data-writes-allowed`,
   `data-motion-allowed`, `data-winning-lanes`, `data-supervisor-active`)
   on `<div data-testid="decision-flow">`.
2. Edge unit tests for `ActiveEdge` / `ActionEdge` in isolation.
3. Manual browser smoke at `localhost:5173/` and
   `localhost:5173/?fixture=shadow`.

This is a deviation from the original plan, which assumed jsdom would
render edges with measured dimensions. The actual derivation logic and
visual output in real browsers match the locked design.
