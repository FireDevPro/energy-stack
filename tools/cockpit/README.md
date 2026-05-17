---
date: 2026-05-17
owner: chris
status: phase-1-shipped
role-label: chris
---

# Controller Cockpit

Workstation-local read-only dashboard for the HVAC controller. Live-tape
complement to the daily n8n/Telegram commissioning report.

## Phase 1 (current): mock fixtures, no backend

```bash
cd tools/cockpit/frontend
npm install
npm run dev
```

Open <http://localhost:5173/> for the default `summer_normal` fixture.

Toggle fixtures via `?fixture=<name>`:

- <http://localhost:5173/> — `summer_normal` (Arm B-active, July afternoon,
  1pm fire-time tick, Schedule winning, humid override active, action applied)
- <http://localhost:5173/?fixture=shadow> — `shadow_current` (pre-experiment
  shadow mode, `mode_actual=outside-window`, dry-run action, dashed
  Supervisor → Action edge, `SHADOW` badge)

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | dev server on `:5173` |
| `npm run build` | production build to `dist/` |
| `npm run typecheck` | `tsc -b --noEmit` |
| `npm run test` | Vitest run (acceptance + edge unit tests) |
| `npm run test:watch` | Vitest watch mode |
| `npm run lint` | ESLint |

## Architecture

Read-only by design. No drag, no setpoint controls, no writes back to the
scheduler. Snapshot is assembled from existing `decision_trace.*` Loki logs
and `hvac.*` Influx measurements — cockpit cannot introduce control-path
behavior changes.

Three phases planned:

- **Phase 1 (this)** — mock fixtures, full UI rendering, no backend.
- **Phase 2** — 7 additional fixtures (price_spike, fivecp_risk,
  supervisor_clamp, controller_down, feed_outage, mild_day, arm_switch) +
  refine node-state edge cases + animation polish.
- **Phase 3** — adds `tools/cockpit/backend/` FastAPI proxy that queries
  the live Pi-lab InfluxDB + Loki and returns the same `Snapshot` JSON
  shape. Frontend swaps the fixture import for `fetch('/api/snapshot')` +
  polling. Snapshot contract stable; backend just shapes Influx/Loki
  queries into JSON.

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
