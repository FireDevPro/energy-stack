---
date: 2026-05-17
owner: chris
status: shipped
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
# Pi-lab token lives in /home/chris/energy-stack/.env as INFLUXDB_INIT_ADMIN_TOKEN.

pwsh tools/cockpit/install-shortcut.ps1   # creates Cockpit.lnk on desktop
```

Then double-click the Cockpit desktop icon. The launcher:

- sources `tools/cockpit/.env.local` (gitignored)
- kills any prior cockpit backend on `:8000` / Vite on `:5173`
  (only if the bound process command line matches the cockpit — leaves
  unrelated python/node alone)
- spawns uvicorn (live mode) and Vite as hidden background processes; logs at `tools/cockpit/logs/backend.log` and `tools/cockpit/logs/frontend.log`
- waits for both to be healthy, then opens <http://localhost:5173/>

To stop the cockpit, re-run `start-cockpit.ps1` (its first action is killing any prior bound process), or kill the uvicorn/Vite processes manually via Task Manager.

Backend port `:8000` is pinned in `vite.config.ts` (proxy target) and
in `start-cockpit.ps1` (`$BackendPort`). Frontend port `:5173` is
Vite's default and is pinned in `start-cockpit.ps1` (`$FrontendPort`,
passed as `--port`). Change all three call sites together if either
port ever needs to move. There is deliberately no
`COCKPIT_BACKEND_PORT` knob, because letting Vite's proxy target drift
from the backend's actual port silently serves stale data without
erroring.

Toggle to a fixture (offline / demo / screenshots) via `?fixture=<name>`.
The launcher's default URL gives live data; appending `?fixture=...` swaps
in a static snapshot from `src/fixtures/`. The full set:

- `summer_normal` (default fixture) — Arm B-active, July afternoon, 1pm
  fire-time tick, Schedule winning, humid override active, action applied
- `shadow` / `shadow_current` — pre-experiment shadow mode,
  `mode_actual=outside-window`, dry-run action
- `price_spike` — RTP Spike layer wins with scarcity tier
- `fivecp_risk` — 5CP layer fires with COMED scope
- `supervisor_clamp` — Supervisor in `clamped` role
- `controller_down` — B-down arm, controller dead, missing nodes
- `feed_outage` — PJM RT LMP stale, B-fallback, price overlay stale
- `mild_day` — MILD day type, full evaluation tape
- `arm_switch` — boundary moment with arm B-active

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | dev server on `:5173` (proxies `/api/*` to backend) |
| `npm run build` | production build to `dist/` |
| `npm run typecheck` | `tsc -b --noEmit` |
| `npm run test` | Vitest run (acceptance + live-fetch tests) |
| `npm run test:watch` | Vitest watch mode |
| `npm run lint` | ESLint |
| `pytest tools/cockpit/backend/tests/` | backend pytest (snapshot + freshness) |
| `uvicorn tools.cockpit.backend.app:app --reload` | run backend dev server (set `COCKPIT_BACKEND_MODE=live` explicitly — the launcher sets it, but bare `uvicorn` may default to canned-fixture mode) |

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

Shipped state:

- **Phase 1** — mock fixtures, full UI rendering, no backend.
- **Phase 2** — 9 fixtures total covering normal operation + edge cases.
- **Phase 3** — `tools/cockpit/backend/` FastAPI proxy at `:8000` serving
  the `Snapshot` JSON shape. Frontend polls every 5s with fixture
  fallback on network errors and explicit fixture mode via `?fixture=`.
  Backend tests assert the snapshot contract.
- **Live wire-up** — `tools/cockpit/backend/influx.py` +
  `loki.py` query builders are implemented against Pi-lab InfluxDB +
  Loki; live mode (`COCKPIT_BACKEND_MODE=live`, default in the
  launcher) is the canonical operator path. Canned mode
  (`COCKPIT_BACKEND_MODE=canned`) still serves the `summer_normal`
  fixture for offline development.

Locked design decisions live in [`docs/plans/archive/cockpit-plan.md`](../../docs/plans/archive/cockpit-plan.md) (archived 2026-05-17 when the feature shipped).

## Layout

```
┌─────────────────────────────────────────────────────────┐
│ Header: scheduler_mode | arm-mode | alive | time        │
├─────────────────────────────────────────────────────────┤
│ Feed health: ◉ ComEd  ◉ NWS  ◯ PJM-fcst ◉ Refoss ◉…    │
├──────────────────┬──────────────────────────────────────┤
│  THERMOSTAT 30%  │  DECISION FLOW (custom SVG)    70%   │
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

Vite, React 19, TypeScript, Tailwind 3 (preflight only, no utility
classes used directly), Framer Motion (count-up animation only),
Vitest + Testing Library + jsdom. Custom SVG decision-flow renderer
(absolute-positioned nodes + Bezier paths in `DecisionBoard.tsx`) —
not React Flow / @xyflow. Dark "Reticule" theme: Sora display font,
Hanken Grotesk for UI text, JetBrains Mono for IDs/timestamps. Loaded
via Google Fonts CDN; design source ported verbatim from operator's
reference (gitignored at `tools/cockpit/reference/`).

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
  Renders the full app against all 9 fixtures, asserts header chips,
  feed-health strip, thermostat card, all 8 flow-node `role_state`s,
  action strip, tick footer, and reduced-motion gating.
- `src/__tests__/cockpit.live.test.tsx` — live-fetch path tests (polling
  hook re-fires on interval, fixture fallback on fetch failure, mocked
  `fetch` against the live snapshot shape).

Per repo policy, Vitest is currently non-gating for cockpit PRs —
failures are predominantly missing-jsdom-API noise (e.g. `ResizeObserver`
in components that measure their container), not data-path or build
failures. The required gates are: `npm run typecheck`, `npm run lint`,
`npm run build`, `pytest tools/cockpit/backend/tests/`, and manual
Chrome smoke on the operator's display.

## Known jsdom limitations

The decision-flow renderer is a custom SVG component
(`DecisionBoard.tsx`) that positions nodes via absolute coordinates
computed from the wrapper's measured `ResizeObserver` width/height,
and draws edges as Bezier paths. jsdom does not implement
`ResizeObserver`, and `setup.ts` only stubs `matchMedia` (matching
the "Vitest non-gating" policy above — see [Visual acceptance](#visual-acceptance)).
The consequence: acceptance tests that mount the full app produce
runtime `ReferenceError: ResizeObserver is not defined` traces from
components that measure their container. The data-shape assertions
those tests intend to make (node `role_state`, winning-lane
attribute, action-strip text) are observable in Chrome and via the
`/api/snapshot` JSON.

Adding a `ResizeObserver` polyfill to `setup.ts` would re-green the
suite, but is parked until there's a concrete non-visual regression
worth catching. Chrome on the operator's real display is the visual
oracle; backend pytest + typecheck + build + manual smoke cover the
data path.
