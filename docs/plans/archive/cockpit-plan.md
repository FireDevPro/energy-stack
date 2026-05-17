---
date: 2026-05-16
owner: chris
status: draft
role-label: chris
---

# Controller Cockpit — live observability dashboard plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement task-by-task. Checkbox (`- [ ]`) steps track progress.

**Goal:** A workstation-local web dashboard that shows the controller's current decision-flow state at a glance — thermostat instrument on the left, parallel-then-merge decision flow on the right, live-tape complement to the daily n8n/Telegram commissioning report.

**Architecture:** TypeScript + Vite + React + React Flow + Framer Motion + Tailwind, served by `npm run dev` on `localhost:5173`. Phase 1 ships UI against checked-in mock fixtures with no backend. Phase 2 grows the fixture set and refines visual edge cases. Phase 3 adds a tiny FastAPI proxy that shapes Influx + Loki queries into the same snapshot contract.

**Tech stack:** Vite, React (whatever the current `npm create vite@latest --template react-ts` ships; React 18 or 19 both work — `@xyflow/react` v12 and Framer Motion 11+ support both), TypeScript, React Flow (`@xyflow/react`), Framer Motion, Tailwind CSS, Vitest + React Testing Library (Phase 1 outside-in test).

---

## Spec anchors

- [docs/plans/sced-rebaseline-spec-2026-05-13.md §3](sced-rebaseline-spec-2026-05-13.md) — `SCHEDULER_MODE` (shadow / experiment / production); arm-calendar gating; locked freeze at OSF commit hash. The cockpit renders `scheduler_mode` and `arm_mode.mode_actual` as first-class chips.
- [deploy/energy-stack/hvac-scheduler/decision_codes.py](../../deploy/energy-stack/hvac-scheduler/decision_codes.py) — append-only enums (`PriceOverlayCode`, `LayerResolutionCode`, `SupervisorCode`, `PrecoolCode`, `DayTypeCode`). Fixtures use the verbatim string values from these enums.
- [docs/plans/decision-trace-plan.md](decision-trace-plan.md) — emission shape of `decision_trace.price_overlay_eval`, `.layer_resolution`, `.supervisor`, `.day_type_decision`, `.precool_decision` events. Cockpit's snapshot contract is the curated derivative of these events.
- [deploy/energy-stack/hvac-scheduler/arm_calendar.py](../../deploy/energy-stack/hvac-scheduler/arm_calendar.py) — `current_arm_at` is the source of truth for `arm` field on every trace; cockpit's `arm_mode.arm` displays the same value.
- [deploy/energy-stack/hvac-scheduler/app.py:write_arm_mode](../../deploy/energy-stack/hvac-scheduler/app.py) — `mode_actual` vocabulary that the arm-mode chip and Action badge consume: `A-active | B-active | B-fallback | B-down | off-protocol-shadow | off-protocol-production | outside-window`.
- [docs/HVAC_LOGIC.md](../HVAC_LOGIC.md) — day-type rules, schedules, supervisor, humid override, precool. Cockpit fixtures must respect these as ground truth.
- This conversation (grilled to lockdown). All locked decisions captured below.

## Locked decisions

| Question | Decision |
|---|---|
| Primary job | Always-on glanceable view of current controller state. Walk-by answers: alive, current thermostat state, scheduler mode, current price tier, which layer is winning, would-act vs acted. Daily n8n/Telegram report covers yesterday's tape; cockpit covers right-now. |
| Phase 1 scope (snapshot semantics) | Latest-of-everything snapshot. No tick history, no scrubber, no drill-down. Snapshot contract is B-mode-ready (carries `latest_tick_id`, `latest_tick_time`, per-node `source.event` + `source.ts`) so a future drill-down can re-query Loki by `tick_id`. |
| Node taxonomy | 8 nodes: Weather, Day Type, Schedule, Price Overlay, 5CP, Winner, Supervisor, Action. Precool + humid override = sub-detail in Schedule. COMED / RTO scopes = sub-detail in 5CP. |
| Topology | Parallel-then-merge. Weather → Day Type → [Schedule \| Price Overlay \| 5CP] → Winner → Supervisor → Action. Schedule, Price Overlay, 5CP are competing lanes; winning lane glows, losers dimmed, not-applicable gray. Reflects how `decision_trace.layer_resolution` actually arbitrates. |
| State model | Single `role_state` drives visual; `freshness` is text badge only. Vocabulary: `winning \| dimmed \| stale \| missing \| not_applicable \| clamped \| emergency \| context`. Data model carries both fields; render uses `role_state` for body color, `freshness_label` for text. Winning encoding = semantic body color + `sky-400` outer ring/glow. |
| Snapshot contract | Top-level ambient (`scheduler_mode`, `latest_tick_id`, `latest_tick_time`, `thermostat`, `price`, `arm_mode`, `controller`) + `feed_health[]` (each `{name, status, label}`) + `flow.{weather, day_type, schedule, price_overlay, fivecp, winner, supervisor, action}`. Each flow node: `{role_state, freshness, freshness_label, title, subtitle, details, source}`. |
| `schedule.details` shape | Carries both `base_schedule_cool_f` (pre-modifier) and `effective_schedule_cool_f` (post humid override / precool). Plus `humid_override_active`, `humid_override_setpoint_f`, `precool_window` (nullable). |
| `winner.details` shape | Includes `prev_effective_cool_f` + `changed: bool` so Winner node can render setpoint transitions visually. |
| Reason codes | Use verbatim string values from `decision_codes.py` enums. Day Type fixtures include `evaluation_tape[]` with `{code, fired, actual, threshold}` entries per `DayTypeCode` docstring. |
| Page layout | Option A desktop-first. Header band full-width → feed-health strip full-width → 30% left thermostat card / 70% right React Flow canvas. Flow itself laid out left-to-right. Narrow-screen stack-vertical is Phase 4+. |
| Thermostat card composition | Header chips (scheduler_mode + arm-mode + alive + time) → 270° SVG ring with hero indoor temp inside + setpoint markers → "cool 76 · heat 68" readout → mode/fan/RH row → price chip (tier color, pulses, freshness label) → scheduler-mode line → tick footer (`latest_tick_id` + `latest_tick_time` + freshness). Read-only. No drag. No outdoor temp. No history. |
| Animation budget (Phase 1) | Continuous slow marching-dash on winning path (4-5s cycle), node pulse on `role_state` change, tier color pulse on thermostat price chip + Price Overlay node, smooth number count-up, supervisor red flash on clamp/emergency, dashed-vs-solid Supervisor → Action edge + Action badge for shadow/applied, feed-health dot pulse on fresh feeds. `prefers-reduced-motion` honored via Framer Motion `MotionConfig` + CSS media query. |
| Animation cuts | No particles, no gate animation, no idle breathing, no edge morphing, no odometer digits, no burst effects, no sci-fi HUD chrome. |
| Theme | Dark, ops-dashboard minimal. `bg-zinc-950` page, `bg-zinc-900` card surfaces, `border-zinc-800`, Inter UI, JetBrains Mono for IDs/timestamps. `sky-400` reserved for winning ring. Tier palette: emerald-500 / amber-400 / rose-500. Clamp = rose-500; emergency = rose-600 flash. No theme toggle in Phase 1. |
| Staleness thresholds | `freshness.ts` constants per source cadence. Pattern: `fresh` ≤ 1.2× cadence, `warn` → 2×, `stale` → 3-5×, `missing` beyond. `hvac.actions` is event-driven and NOT used as a staleness signal — Action node uses `NOT-FIRED-THIS-TICK` / last-fire / `APPLIED` / `SHADOW` semantics instead. Supervisor absence = `not_invoked`, not stale. Liveness comes from `hvac.arm_mode` + `hvac.heartbeat` + 5-min trace cadence. |
| Build infra | TypeScript everywhere. Repo path `tools/cockpit/frontend/`. React Flow uses manual node coordinates (8 nodes, fixed graph — no auto-layout engine). Dev fixture toggle via `?fixture=shadow` URL param only; no dropdown in Phase 1. |
| Phase 1 fixtures | Two **TypeScript** modules: `summer_normal.ts` + `shadow_current.ts`, each exporting `const ... : Snapshot = {...}`. TS modules are used instead of JSON because TS narrows literal types at the source (`'experiment'` stays the literal type, satisfying `SchedulerMode` union); JSON imports widen to `string` and silently swallow drift in string-union fields. Phase 3 will keep TS dev fixtures alongside a runtime validator (Zod or hand-rolled) for the live-fetch path. Fixtures land at `src/fixtures/summer_normal.ts` and `src/fixtures/shadow_current.ts`. |
| Reduced-motion implementation | Two cooperating mechanisms. (1) Framer Motion: `<MotionConfig reducedMotion="user">` at App root gates every `motion.*` component. (2) CSS animations / Tailwind `animate-*` classes (price tier pulse, feed-health dot pulse, BaseNode emergency border pulse, marching dash on ActiveEdge): wrapped with the `motion-safe:` Tailwind variant — Tailwind 3+ ships this variant as an automatic `prefers-reduced-motion: no-preference` media gate. ActiveEdge additionally reads `matchMedia` directly to set its `data-animated` test attribute (this is the only testable proof in jsdom). The `motion-safe:`-gated `animate-*` classes stay in the DOM regardless of preference; the gating happens via CSS media-query evaluation, which jsdom does not run. Acceptance test therefore asserts ONLY `data-animated="false"` on the active edge with `matchMedia` mocked; verification that the other CSS-media-gated animations also stop happens visually in a real browser, not in the test suite. |
| Phase 1 acceptance test | Written first. Vitest + React Testing Library + jsdom. Renders the App against both fixtures, asserts key UI states. Goes GREEN at the Phase 1 PR boundary (NOT xfail past the boundary). Intra-Phase-1 task PRs may keep it xfail; the Phase 1 merge PR must have it green. |
| Phase 2 fixture set | 7 additions: `price_spike`, `fivecp_risk`, `supervisor_clamp`, `controller_down`, `feed_outage`, `mild_day`, `arm_switch`. Plus refine node-state edge cases + animation polish against this fixture set. |
| Phase 3 | FastAPI proxy at `tools/cockpit/backend/`. Single `/api/snapshot` endpoint queries Influx + Loki, returns the same JSON shape the fixtures already match. Frontend swaps `import fixture from './fixtures/...'` for `fetch('/api/snapshot')` + polling. Snapshot contract is stable by Phase 3 so no breaking change. |
| Out-of-scope (forever or much-later) | Drag-to-set-setpoint (read-only by design). Control writes of any kind back to the scheduler. Mobile / narrow-screen responsive layout (Phase 4+). Light theme. Tick history scrubber. Per-node drill-down with raw event payloads. Fixture browser dropdown. Playwright e2e. |

## Feature-level acceptance test (outside-in)

Single test file `tools/cockpit/frontend/src/__tests__/cockpit.acceptance.test.tsx`. Written in **Phase 1 Task 2** (after scaffold, before any component implementation). Goes through xfail during intra-Phase-1 task PRs, **green at the Phase 1 PR boundary**.

The test asserts the tracer slice end-to-end:

```tsx
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import App from '../App';
import { summerNormal } from '../fixtures/summer_normal';
import { shadowCurrent } from '../fixtures/shadow_current';
import type { Snapshot } from '../types';

// Fixtures already declare `: Snapshot` at their definition (TS narrows
// the literal types there). These reaffirmations exist only so a future
// fixture refactor that loses the inline annotation still fails the test
// suite at compile time. NO `as Snapshot` cast — that would suppress the
// drift check this assignment is here to enforce.
const _typeCheckSummer: Snapshot = summerNormal;
const _typeCheckShadow: Snapshot = shadowCurrent;

describe('Cockpit Phase 1 outside-in acceptance', () => {
  beforeEach(() => {
    // Reset URL between tests.
    window.history.replaceState({}, '', '/');
  });

  it('renders summer_normal fixture with Schedule winning', () => {
    render(<App />);

    // Header chips present
    expect(screen.getByTestId('chip-scheduler-mode')).toHaveTextContent('experiment');
    expect(screen.getByTestId('chip-arm-mode')).toHaveTextContent('B-active');
    expect(screen.getByTestId('chip-controller-alive')).toBeInTheDocument();

    // Feed-health strip has 7 chips, all fresh.
    const feedHealth = screen.getByTestId('feed-health-strip');
    expect(within(feedHealth).getAllByTestId(/^feed-chip-/).length).toBe(7);

    // Thermostat card hero temp.
    expect(screen.getByTestId('thermostat-indoor-temp')).toHaveTextContent('74.8');
    expect(screen.getByTestId('thermostat-cool-setpoint')).toHaveTextContent('76');
    expect(screen.getByTestId('thermostat-price-chip')).toHaveTextContent('8.4');
    expect(screen.getByTestId('thermostat-price-chip')).toHaveAttribute('data-tier', 'normal');

    // Tick footer shows latest_tick_id + freshness.
    expect(screen.getByTestId('thermostat-tick-footer')).toHaveTextContent('a1b2c3d4');

    // Flow nodes: Schedule winning, Price Overlay + 5CP dimmed, Weather + Day Type context.
    expect(screen.getByTestId('node-schedule')).toHaveAttribute('data-role-state', 'winning');
    expect(screen.getByTestId('node-price-overlay')).toHaveAttribute('data-role-state', 'dimmed');
    expect(screen.getByTestId('node-fivecp')).toHaveAttribute('data-role-state', 'dimmed');
    expect(screen.getByTestId('node-weather')).toHaveAttribute('data-role-state', 'context');
    expect(screen.getByTestId('node-day-type')).toHaveAttribute('data-role-state', 'context');
    expect(screen.getByTestId('node-winner')).toHaveAttribute('data-role-state', 'winning');
    expect(screen.getByTestId('node-supervisor')).toHaveAttribute('data-role-state', 'winning');
    expect(screen.getByTestId('node-action')).toHaveAttribute('data-role-state', 'winning');

    // Action node shows APPLIED badge + solid edge encoding (production).
    expect(screen.getByTestId('action-badge')).toHaveTextContent('APPLIED');
    expect(screen.getByTestId('edge-supervisor-to-action')).toHaveAttribute('data-edge-style', 'solid');

    // Winner shows effective setpoint changed from prev tick.
    expect(screen.getByTestId('node-winner')).toHaveTextContent('76');
    expect(screen.getByTestId('node-winner')).toHaveAttribute('data-changed', 'true');
  });

  it('renders shadow_current fixture with SHADOW badge and dashed edge', () => {
    window.history.replaceState({}, '', '/?fixture=shadow');
    render(<App />);

    expect(screen.getByTestId('chip-scheduler-mode')).toHaveTextContent('shadow');
    expect(screen.getByTestId('chip-arm-mode')).toHaveTextContent('outside-window');
    expect(screen.getByTestId('action-badge')).toHaveTextContent('SHADOW');
    expect(screen.getByTestId('edge-supervisor-to-action')).toHaveAttribute('data-edge-style', 'dashed');
  });

  it('honors prefers-reduced-motion', () => {
    // jsdom mock: simulate prefers-reduced-motion: reduce.
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((q: string) => ({
        matches: q.includes('reduce'),
        media: q,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    render(<App />);

    // Marching-dash edges should not have the `animated` class when reduced motion is requested.
    expect(screen.getByTestId('edge-winner-active')).toHaveAttribute('data-animated', 'false');
  });
});
```

The test uses `data-testid` attributes the components must expose. These are stable contract points the test asserts against — components are free to restructure DOM beneath them.

Additionally `npm run typecheck` and `npm run build` are part of acceptance — wired in Task 1 and exercised in CI for the Phase 1 PR.

## File structure

```
tools/cockpit/
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── postcss.config.js
│   ├── index.html
│   ├── vitest.config.ts
│   ├── src/
│   │   ├── main.tsx                              # ReactDOM bootstrap
│   │   ├── App.tsx                               # routes + fixture loader + layout shell
│   │   ├── types.ts                              # Snapshot interface + all sub-interfaces
│   │   ├── freshness.ts                          # staleness threshold constants
│   │   ├── fixtures/
│   │   │   ├── summer_normal.ts                  # TS module, exports `summerNormal: Snapshot`
│   │   │   └── shadow_current.ts                 # TS module, exports `shadowCurrent: Snapshot`
│   │   ├── components/
│   │   │   ├── Header.tsx                        # scheduler_mode + arm-mode + alive + time chips
│   │   │   ├── FeedHealthStrip.tsx               # 7 chips driven by feed_health[]
│   │   │   ├── ThermostatCard.tsx                # left 30% — ring + temp + chips + price + footer
│   │   │   ├── ThermostatRing.tsx                # 270° SVG arc
│   │   │   ├── PriceChip.tsx                     # tier-colored chip with pulse
│   │   │   ├── DecisionFlow.tsx                  # right 70% — React Flow canvas + node/edge wiring
│   │   │   ├── nodes/
│   │   │   │   ├── BaseNode.tsx                  # shared envelope: title + body + role_state + freshness footer
│   │   │   │   ├── WeatherNode.tsx
│   │   │   │   ├── DayTypeNode.tsx
│   │   │   │   ├── ScheduleNode.tsx
│   │   │   │   ├── PriceOverlayNode.tsx
│   │   │   │   ├── FiveCPNode.tsx
│   │   │   │   ├── WinnerNode.tsx
│   │   │   │   ├── SupervisorNode.tsx
│   │   │   │   └── ActionNode.tsx
│   │   │   ├── edges/
│   │   │   │   ├── DefaultEdge.tsx               # static edge
│   │   │   │   ├── ActiveEdge.tsx                # winning-path edge with marching dash
│   │   │   │   └── ActionEdge.tsx                # Supervisor → Action; solid / dashed by snapshot
│   │   │   └── chips/
│   │   │       └── Chip.tsx                      # generic header chip primitive
│   │   ├── lib/
│   │   │   ├── loadFixture.ts                    # reads ?fixture= query param, returns snapshot
│   │   │   └── flowLayout.ts                     # manual node coords + edge definitions
│   │   ├── styles/
│   │   │   └── index.css                         # Tailwind directives + base layer overrides
│   │   └── __tests__/
│   │       └── cockpit.acceptance.test.tsx       # outside-in test (above)
│   └── README.md                                 # setup + URL params + Phase note
```

## Phases (vertical slices)

### Phase 1 — mock-fixture UI tracer

Smallest end-to-end cut: types → fixtures → component shells → render → animation → outside-in test green. Each task below is 2-5 minutes.

#### Task 1: Scaffold Vite + React + TypeScript at `tools/cockpit/frontend/`

**Files:**
- Create: `tools/cockpit/frontend/package.json`
- Create: `tools/cockpit/frontend/tsconfig.json`
- Create: `tools/cockpit/frontend/tsconfig.node.json`
- Create: `tools/cockpit/frontend/vite.config.ts`
- Create: `tools/cockpit/frontend/index.html`
- Create: `tools/cockpit/frontend/src/main.tsx`
- Create: `tools/cockpit/frontend/src/App.tsx` (stub)

- [ ] **Step 1: Generate Vite project**

Run from repo root:
```bash
cd tools && mkdir -p cockpit && cd cockpit && npm create vite@latest frontend -- --template react-ts
```

- [ ] **Step 2: Install deps**

```bash
cd tools/cockpit/frontend && npm install
npm install @xyflow/react framer-motion
npm install -D tailwindcss postcss autoprefixer
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom @vitest/coverage-v8
```

- [ ] **Step 3: Initialize Tailwind**

```bash
cd tools/cockpit/frontend && npx tailwindcss init -p
```

Edit `tools/cockpit/frontend/tailwind.config.ts`:
```ts
import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config
```

- [ ] **Step 4: Wire Tailwind into stylesheet**

Replace contents of `tools/cockpit/frontend/src/index.css` with:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body {
    @apply bg-zinc-950 text-zinc-100 font-sans antialiased;
  }
}
```

- [ ] **Step 5: Add scripts to package.json**

Add to `tools/cockpit/frontend/package.json` `"scripts"`:
```json
"scripts": {
  "dev": "vite",
  "build": "tsc -b && vite build",
  "preview": "vite preview",
  "typecheck": "tsc -b --noEmit",
  "test": "vitest run",
  "test:watch": "vitest"
}
```

- [ ] **Step 6: Replace App.tsx with placeholder**

`tools/cockpit/frontend/src/App.tsx`:
```tsx
export default function App() {
  return <div className="p-4">Controller Cockpit — Phase 1 scaffold</div>;
}
```

- [ ] **Step 7: Verify scaffold builds and runs**

```bash
cd tools/cockpit/frontend && npm run typecheck && npm run build
```
Expected: zero errors, `dist/` produced.

- [ ] **Step 8: Commit**

```bash
git add tools/cockpit/frontend
git commit -m "feat(cockpit): scaffold Vite + React + TypeScript + Tailwind"
```

#### Task 2: Write the outside-in acceptance test (failing)

**Files:**
- Create: `tools/cockpit/frontend/vitest.config.ts`
- Create: `tools/cockpit/frontend/src/__tests__/cockpit.acceptance.test.tsx`
- Create: `tools/cockpit/frontend/src/__tests__/setup.ts`

- [ ] **Step 1: Create vitest config**

`tools/cockpit/frontend/vitest.config.ts`:
```ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/__tests__/setup.ts'],
  },
})
```

- [ ] **Step 2: Create test setup file with React Flow jsdom mocks**

React Flow depends on browser APIs jsdom doesn't ship (`ResizeObserver`, `DOMMatrixReadOnly`, several pointer-capture / scroll methods on HTMLElement). Without these mocks, mounting `<ReactFlow>` inside Vitest throws `ResizeObserver is not defined` or similar and the acceptance test fails for environment reasons rather than UI logic.

`tools/cockpit/frontend/src/__tests__/setup.ts`:
```ts
import '@testing-library/jest-dom/vitest'

// React Flow requires browser APIs that jsdom doesn't ship.

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock })
  .ResizeObserver = ResizeObserverMock

class DOMMatrixReadOnlyMock {
  m22 = 1
  constructor(transform?: string) {
    if (transform) {
      const scale = transform.match(/scale\(([^,)]+)/)
      if (scale) this.m22 = Number(scale[1])
    }
  }
}
;(globalThis as unknown as { DOMMatrixReadOnly: typeof DOMMatrixReadOnlyMock })
  .DOMMatrixReadOnly = DOMMatrixReadOnlyMock

// React Flow's pointer + scroll paths touch these; jsdom no-ops are fine.
HTMLElement.prototype.scrollIntoView = function () {}
;(HTMLElement.prototype as unknown as { releasePointerCapture: () => void })
  .releasePointerCapture = function () {}
;(HTMLElement.prototype as unknown as { hasPointerCapture: () => boolean })
  .hasPointerCapture = function () { return false }

// React Flow measures node bounds via getBoundingClientRect; jsdom returns
// zeros which breaks edge path math. Stub realistic values.
const originalGetBCR = HTMLElement.prototype.getBoundingClientRect
HTMLElement.prototype.getBoundingClientRect = function () {
  const rect = originalGetBCR.call(this)
  return {
    ...rect,
    width: rect.width || 200,
    height: rect.height || 80,
  } as DOMRect
}
```

- [ ] **Step 3: Write the full acceptance test**

Create `tools/cockpit/frontend/src/__tests__/cockpit.acceptance.test.tsx` with the full body shown in the "Feature-level acceptance test" section above. Mark suite or individual tests with `it.fails(...)` (Vitest's analogue of xfail-strict) during intra-Phase-1 task PRs; remove `.fails` once each assertion passes.

- [ ] **Step 4: Run the test, confirm RED**

```bash
cd tools/cockpit/frontend && npm run test
```
Expected: failures referencing missing `Snapshot` type import, missing fixtures, missing `data-testid` attributes. This is the failing outside-in test; it stays red until Phase 1 finishes.

- [ ] **Step 5: Commit**

```bash
git add tools/cockpit/frontend/vitest.config.ts tools/cockpit/frontend/src/__tests__
git commit -m "test(cockpit): outside-in Phase 1 acceptance test (failing)"
```

#### Task 3: Define the Snapshot TypeScript contract

**Files:**
- Create: `tools/cockpit/frontend/src/types.ts`

- [ ] **Step 1: Write the type definitions**

`tools/cockpit/frontend/src/types.ts`:
```ts
// Locked snapshot contract — see docs/plans/cockpit-plan.md

export type Freshness = 'fresh' | 'warn' | 'stale' | 'missing';

export type RoleState =
  | 'winning'
  | 'dimmed'
  | 'stale'
  | 'missing'
  | 'not_applicable'
  | 'clamped'
  | 'emergency'
  | 'context';

export type SchedulerMode = 'shadow' | 'experiment' | 'production';

export type ArmModeActual =
  | 'A-active'
  | 'B-active'
  | 'B-fallback'
  | 'B-down'
  | 'off-protocol-shadow'
  | 'off-protocol-production'
  | 'outside-window';

export type PriceTier = 'normal' | 'elevated' | 'scarcity';

export interface Thermostat {
  indoor_temp_f: number;
  indoor_humidity_pct: number;
  cool_setpoint_f: number;
  heat_setpoint_f: number;
  hvac_mode: 'cool' | 'heat' | 'auto' | 'off';
  fan_mode: 'auto' | 'on' | 'circulate';
  source_ts: string;
  freshness: Freshness;
  freshness_label: string;
}

export interface Price {
  current_cents_per_kwh: number;
  tier: PriceTier;
  source_ts: string;
  freshness: Freshness;
  freshness_label: string;
}

export interface ArmMode {
  mode_actual: ArmModeActual;
  arm: 'A' | 'B' | null;
  source_ts: string;
  freshness: Freshness;
  freshness_label: string;
}

export interface Controller {
  alive: boolean;
  last_heartbeat_ts: string | null;
  freshness: Freshness;
}

export interface FeedHealthEntry {
  name: string;
  status: Freshness;
  label: string;
}

export interface NodeSource {
  event: string;
  tick_id: string | null;
  ts: string;
}

export interface BaseNodeEnvelope<TDetails> {
  role_state: RoleState;
  freshness: Freshness;
  freshness_label: string;
  title: string;
  subtitle: string;
  details: TDetails;
  source: NodeSource | null;
}

// Per-node details payloads.

export interface WeatherDetails {
  current_outdoor_f: number;
  today_high_f: number;
  apparent_max_f: number;
  dewpoint_max_f: number;
  heat_advisory: boolean;
}

export interface DayTypeTapeEntry {
  code: string;
  fired: boolean;
  actual: number | boolean | null;
  threshold: number | boolean | null;
}

export interface DayTypeDetails {
  winning_day_type: 'MILD' | 'NORMAL' | 'HOT_5CP_RISK' | 'HOT_STREAK_DAY1';
  decision_for_date: string;
  reason_code: string;
  evaluation_tape: DayTypeTapeEntry[];
}

export interface PrecoolWindow {
  hour_ct: number;
  depth_f: number;
}

export interface ScheduleDetails {
  action_label: string;
  base_schedule_cool_f: number;
  effective_schedule_cool_f: number;
  humid_override_active: boolean;
  humid_override_setpoint_f: number | null;
  precool_window: PrecoolWindow | null;
}

export interface PriceOverlayDetails {
  price_cents: number | null;
  prev_tier: PriceTier;
  new_tier: PriceTier;
  outcome: 'held' | 'upgraded' | 'downgraded' | 'released';
  reason_code: string;
  hold_minutes_remaining: number | null;
}

export interface FiveCPDetails {
  fivecp_active: boolean;
  fivecp_scopes_fired: Array<'COMED' | 'RTO'>;
  fivecp_cool_f: number | null;
  in_season: boolean;
}

export interface WinnerDetails {
  winning_layer: 'schedule' | 'price_overlay' | 'fivecp' | 'tie';
  effective_cool_f: number;
  prev_effective_cool_f: number;
  changed: boolean;
  reason_code: string;
}

export interface SupervisorDetails {
  decision: 'approved' | 'clamped' | 'emergency' | null;
  proposed_cool_f: number | null;
  proposed_heat_f: number | null;
  final_cool_f: number | null;
  final_heat_f: number | null;
  supervisor_reason: string | null;
  reason_code: string | null;
  indoor_temp_available: boolean | null;
}

export interface LastActionInfo {
  action_label: string;
  fire_ts: string;
  applied: boolean;
  dry_run: boolean;
  cool_setpoint_f: number;
}

export interface ActionDetails {
  applied: boolean | null;
  dry_run: boolean | null;
  action_label: string | null;
  cool_setpoint_f: number | null;
  heat_setpoint_f: number | null;
  fan_mode: string | null;
  setpoint_reason: string | null;
  fire_ts: string | null;
  error: string | null;
  last_fire?: LastActionInfo;
}

export interface Flow {
  weather: BaseNodeEnvelope<WeatherDetails>;
  day_type: BaseNodeEnvelope<DayTypeDetails>;
  schedule: BaseNodeEnvelope<ScheduleDetails>;
  price_overlay: BaseNodeEnvelope<PriceOverlayDetails>;
  fivecp: BaseNodeEnvelope<FiveCPDetails>;
  winner: BaseNodeEnvelope<WinnerDetails>;
  supervisor: BaseNodeEnvelope<SupervisorDetails>;
  action: BaseNodeEnvelope<ActionDetails>;
}

export interface Snapshot {
  snapshot_ts: string;
  latest_tick_id: string;
  latest_tick_time: string;
  scheduler_mode: SchedulerMode;
  thermostat: Thermostat;
  price: Price;
  arm_mode: ArmMode;
  controller: Controller;
  feed_health: FeedHealthEntry[];
  flow: Flow;
}
```

- [ ] **Step 2: Verify types compile**

```bash
cd tools/cockpit/frontend && npm run typecheck
```
Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add tools/cockpit/frontend/src/types.ts
git commit -m "feat(cockpit): Snapshot contract types"
```

#### Task 4: Author the `summer_normal.ts` fixture

**Files:**
- Create: `tools/cockpit/frontend/src/fixtures/summer_normal.ts`

Fixtures are TS modules, not JSON. JSON imports widen string literals to `string`, which silently passes assignment to `Snapshot` even when string-union fields drift. TS modules narrow at the source: `'experiment'` stays `'experiment'`, satisfying `SchedulerMode`.

- [ ] **Step 1: Write the fixture**

`tools/cockpit/frontend/src/fixtures/summer_normal.ts`:
```ts
import type { Snapshot } from '../types';

export const summerNormal: Snapshot = {
  "snapshot_ts": "2026-07-14T13:00:30-05:00",
  "latest_tick_id": "a1b2c3d4",
  "latest_tick_time": "2026-07-14T13:00:00-05:00",
  "scheduler_mode": "experiment",
  "thermostat": {
    "indoor_temp_f": 74.8,
    "indoor_humidity_pct": 51,
    "cool_setpoint_f": 76,
    "heat_setpoint_f": 68,
    "hvac_mode": "cool",
    "fan_mode": "auto",
    "source_ts": "2026-07-14T12:54:00-05:00",
    "freshness": "fresh",
    "freshness_label": "6m ago"
  },
  "price": {
    "current_cents_per_kwh": 8.4,
    "tier": "normal",
    "source_ts": "2026-07-14T13:00:00-05:00",
    "freshness": "fresh",
    "freshness_label": "30s ago"
  },
  "arm_mode": {
    "mode_actual": "B-active",
    "arm": "B",
    "source_ts": "2026-07-14T13:00:00-05:00",
    "freshness": "fresh",
    "freshness_label": "30s ago"
  },
  "controller": {
    "alive": true,
    "last_heartbeat_ts": null,
    "freshness": "fresh"
  },
  "feed_health": [
    {"name": "ComEd",        "status": "fresh", "label": "30s ago"},
    {"name": "NWS",          "status": "fresh", "label": "1m ago"},
    {"name": "PJM forecast", "status": "fresh", "label": "0m ago"},
    {"name": "PJM RT LMP",   "status": "fresh", "label": "1h ago"},
    {"name": "Refoss",       "status": "fresh", "label": "15s ago"},
    {"name": "EAGLE",        "status": "fresh", "label": "15s ago"},
    {"name": "Thermostat",   "status": "fresh", "label": "6m ago"}
  ],
  "flow": {
    "weather": {
      "role_state": "context",
      "freshness": "fresh",
      "freshness_label": "23m ago",
      "title": "Weather",
      "subtitle": "high 86°F, dewpoint 64°F",
      "details": {
        "current_outdoor_f": 84.2,
        "today_high_f": 86,
        "apparent_max_f": 89,
        "dewpoint_max_f": 64,
        "heat_advisory": false
      },
      "source": {"event": "nws.forecast", "tick_id": null, "ts": "2026-07-14T13:00:00-05:00"}
    },
    "day_type": {
      "role_state": "context",
      "freshness": "fresh",
      "freshness_label": "decided 21:00 last night",
      "title": "Day Type",
      "subtitle": "NORMAL",
      "details": {
        "winning_day_type": "NORMAL",
        "decision_for_date": "2026-07-14",
        "reason_code": "DAY_TYPE_NORMAL_HIGH_75_TO_84",
        "evaluation_tape": [
          {"code": "DAY_TYPE_HOT_HEAT_ADVISORY",    "fired": false, "actual": false, "threshold": true},
          {"code": "DAY_TYPE_HOT_HIGH_GE_85",       "fired": false, "actual": 86,    "threshold": 88},
          {"code": "DAY_TYPE_HOT_APPARENT_GE_90",   "fired": false, "actual": 89,    "threshold": 90},
          {"code": "DAY_TYPE_NORMAL_HIGH_75_TO_84", "fired": true,  "actual": 86,    "threshold": 75}
        ]
      },
      "source": {"event": "decision_trace.day_type_decision", "tick_id": "x9y8z7w6", "ts": "2026-07-13T21:00:00-05:00"}
    },
    "schedule": {
      "role_state": "winning",
      "freshness": "fresh",
      "freshness_label": "30s ago",
      "title": "Schedule",
      "subtitle": "NORMAL afternoon: 76°F (humid override)",
      "details": {
        "action_label": "afternoon_start",
        "base_schedule_cool_f": 78,
        "effective_schedule_cool_f": 76,
        "humid_override_active": true,
        "humid_override_setpoint_f": 76,
        "precool_window": null
      },
      "source": {"event": "decision_trace.layer_resolution", "tick_id": "a1b2c3d4", "ts": "2026-07-14T13:00:00-05:00"}
    },
    "price_overlay": {
      "role_state": "dimmed",
      "freshness": "fresh",
      "freshness_label": "30s ago",
      "title": "Price Overlay",
      "subtitle": "normal — no override",
      "details": {
        "price_cents": 8.4,
        "prev_tier": "normal",
        "new_tier": "normal",
        "outcome": "held",
        "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
        "hold_minutes_remaining": 0
      },
      "source": {"event": "decision_trace.price_overlay_eval", "tick_id": "a1b2c3d4", "ts": "2026-07-14T13:00:00-05:00"}
    },
    "fivecp": {
      "role_state": "dimmed",
      "freshness": "fresh",
      "freshness_label": "30s ago",
      "title": "5CP Risk",
      "subtitle": "no risk — within season",
      "details": {
        "fivecp_active": false,
        "fivecp_scopes_fired": [],
        "fivecp_cool_f": null,
        "in_season": true
      },
      "source": {"event": "decision_trace.layer_resolution", "tick_id": "a1b2c3d4", "ts": "2026-07-14T13:00:00-05:00"}
    },
    "winner": {
      "role_state": "winning",
      "freshness": "fresh",
      "freshness_label": "30s ago",
      "title": "Winner",
      "subtitle": "Schedule",
      "details": {
        "winning_layer": "schedule",
        "effective_cool_f": 76,
        "prev_effective_cool_f": 78,
        "changed": true,
        "reason_code": "LAYER_RESOLUTION_SCHEDULE_WINS"
      },
      "source": {"event": "decision_trace.layer_resolution", "tick_id": "a1b2c3d4", "ts": "2026-07-14T13:00:00-05:00"}
    },
    "supervisor": {
      "role_state": "winning",
      "freshness": "fresh",
      "freshness_label": "30s ago",
      "title": "Supervisor",
      "subtitle": "approved",
      "details": {
        "decision": "approved",
        "proposed_cool_f": 76,
        "proposed_heat_f": 68,
        "final_cool_f": 76,
        "final_heat_f": 68,
        "supervisor_reason": null,
        "reason_code": "SUPERVISOR_APPROVED",
        "indoor_temp_available": true
      },
      "source": {"event": "decision_trace.supervisor", "tick_id": "a1b2c3d4", "ts": "2026-07-14T13:00:00-05:00"}
    },
    "action": {
      "role_state": "winning",
      "freshness": "fresh",
      "freshness_label": "30s ago",
      "title": "Action",
      "subtitle": "afternoon_start applied",
      "details": {
        "applied": true,
        "dry_run": false,
        "action_label": "afternoon_start",
        "cool_setpoint_f": 76,
        "heat_setpoint_f": 68,
        "fan_mode": "auto",
        "setpoint_reason": "schedule (NORMAL afternoon, humid override active)",
        "fire_ts": "2026-07-14T13:00:00-05:00",
        "error": null
      },
      "source": {"event": "hvac.actions", "tick_id": "a1b2c3d4", "ts": "2026-07-14T13:00:00-05:00"}
    }
  }
};
```

- [ ] **Step 2: Typecheck the fixture against the Snapshot interface**

Because the file declares `: Snapshot` inline, TS validates every field at the source. No temporary cast needed.

```bash
cd tools/cockpit/frontend && npm run typecheck
```

If errors → fix the fixture or the types. The interface is the contract; either side may need adjustment.

- [ ] **Step 3: Commit**

```bash
git add tools/cockpit/frontend/src/fixtures/summer_normal.ts
git commit -m "feat(cockpit): summer_normal Phase 1 fixture"
```

#### Task 5: Author the `shadow_current.ts` fixture

**Files:**
- Create: `tools/cockpit/frontend/src/fixtures/shadow_current.ts`

- [ ] **Step 1: Write the fixture**

`tools/cockpit/frontend/src/fixtures/shadow_current.ts`:
```ts
import type { Snapshot } from '../types';

export const shadowCurrent: Snapshot = {
  "snapshot_ts": "2026-05-16T15:23:30-05:00",
  "latest_tick_id": "f0e1d2c3",
  "latest_tick_time": "2026-05-16T15:20:00-05:00",
  "scheduler_mode": "shadow",
  "thermostat": {
    "indoor_temp_f": 72.1,
    "indoor_humidity_pct": 44,
    "cool_setpoint_f": 76,
    "heat_setpoint_f": 68,
    "hvac_mode": "cool",
    "fan_mode": "auto",
    "source_ts": "2026-05-16T15:14:00-05:00",
    "freshness": "fresh",
    "freshness_label": "9m ago"
  },
  "price": {
    "current_cents_per_kwh": 4.1,
    "tier": "normal",
    "source_ts": "2026-05-16T15:20:00-05:00",
    "freshness": "fresh",
    "freshness_label": "3m ago"
  },
  "arm_mode": {
    "mode_actual": "outside-window",
    "arm": null,
    "source_ts": "2026-05-16T15:20:00-05:00",
    "freshness": "fresh",
    "freshness_label": "3m ago"
  },
  "controller": {
    "alive": true,
    "last_heartbeat_ts": null,
    "freshness": "fresh"
  },
  "feed_health": [
    {"name": "ComEd",        "status": "fresh", "label": "3m ago"},
    {"name": "NWS",          "status": "fresh", "label": "12m ago"},
    {"name": "PJM forecast", "status": "fresh", "label": "2h ago"},
    {"name": "PJM RT LMP",   "status": "fresh", "label": "23m ago"},
    {"name": "Refoss",       "status": "fresh", "label": "20s ago"},
    {"name": "EAGLE",        "status": "fresh", "label": "20s ago"},
    {"name": "Thermostat",   "status": "fresh", "label": "9m ago"}
  ],
  "flow": {
    "weather": {
      "role_state": "context",
      "freshness": "fresh",
      "freshness_label": "12m ago",
      "title": "Weather",
      "subtitle": "high 71°F, dewpoint 52°F",
      "details": {
        "current_outdoor_f": 68.4,
        "today_high_f": 71,
        "apparent_max_f": 70,
        "dewpoint_max_f": 52,
        "heat_advisory": false
      },
      "source": {"event": "nws.forecast", "tick_id": null, "ts": "2026-05-16T15:00:00-05:00"}
    },
    "day_type": {
      "role_state": "context",
      "freshness": "fresh",
      "freshness_label": "decided 21:00 last night",
      "title": "Day Type",
      "subtitle": "MILD",
      "details": {
        "winning_day_type": "MILD",
        "decision_for_date": "2026-05-16",
        "reason_code": "DAY_TYPE_MILD_HIGH_LT_75",
        "evaluation_tape": [
          {"code": "DAY_TYPE_HOT_HEAT_ADVISORY",    "fired": false, "actual": false, "threshold": true},
          {"code": "DAY_TYPE_HOT_HIGH_GE_85",       "fired": false, "actual": 71,    "threshold": 88},
          {"code": "DAY_TYPE_HOT_APPARENT_GE_90",   "fired": false, "actual": 70,    "threshold": 90},
          {"code": "DAY_TYPE_NORMAL_HIGH_75_TO_84", "fired": false, "actual": 71,    "threshold": 75},
          {"code": "DAY_TYPE_MILD_HIGH_LT_75",      "fired": true,  "actual": 71,    "threshold": 75}
        ]
      },
      "source": {"event": "decision_trace.day_type_decision", "tick_id": "ab12cd34", "ts": "2026-05-15T21:00:00-05:00"}
    },
    "schedule": {
      "role_state": "winning",
      "freshness": "fresh",
      "freshness_label": "3m ago",
      "title": "Schedule",
      "subtitle": "MILD coast: 78°F",
      "details": {
        "action_label": "mild_coast",
        "base_schedule_cool_f": 78,
        "effective_schedule_cool_f": 78,
        "humid_override_active": false,
        "humid_override_setpoint_f": null,
        "precool_window": null
      },
      "source": {"event": "decision_trace.layer_resolution", "tick_id": "f0e1d2c3", "ts": "2026-05-16T15:20:00-05:00"}
    },
    "price_overlay": {
      "role_state": "dimmed",
      "freshness": "fresh",
      "freshness_label": "3m ago",
      "title": "Price Overlay",
      "subtitle": "normal — no override",
      "details": {
        "price_cents": 4.1,
        "prev_tier": "normal",
        "new_tier": "normal",
        "outcome": "held",
        "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
        "hold_minutes_remaining": 0
      },
      "source": {"event": "decision_trace.price_overlay_eval", "tick_id": "f0e1d2c3", "ts": "2026-05-16T15:20:00-05:00"}
    },
    "fivecp": {
      "role_state": "not_applicable",
      "freshness": "fresh",
      "freshness_label": "3m ago",
      "title": "5CP Risk",
      "subtitle": "out of season",
      "details": {
        "fivecp_active": false,
        "fivecp_scopes_fired": [],
        "fivecp_cool_f": null,
        "in_season": false
      },
      "source": {"event": "decision_trace.layer_resolution", "tick_id": "f0e1d2c3", "ts": "2026-05-16T15:20:00-05:00"}
    },
    "winner": {
      "role_state": "winning",
      "freshness": "fresh",
      "freshness_label": "3m ago",
      "title": "Winner",
      "subtitle": "Schedule",
      "details": {
        "winning_layer": "schedule",
        "effective_cool_f": 78,
        "prev_effective_cool_f": 78,
        "changed": false,
        "reason_code": "LAYER_RESOLUTION_SCHEDULE_WINS"
      },
      "source": {"event": "decision_trace.layer_resolution", "tick_id": "f0e1d2c3", "ts": "2026-05-16T15:20:00-05:00"}
    },
    "supervisor": {
      "role_state": "not_applicable",
      "freshness": "fresh",
      "freshness_label": "not invoked this tick",
      "title": "Supervisor",
      "subtitle": "not invoked",
      "details": {
        "decision": null,
        "proposed_cool_f": null,
        "proposed_heat_f": null,
        "final_cool_f": null,
        "final_heat_f": null,
        "supervisor_reason": null,
        "reason_code": null,
        "indoor_temp_available": null
      },
      "source": null
    },
    "action": {
      "role_state": "context",
      "freshness": "fresh",
      "freshness_label": "last fire 14:00 today",
      "title": "Action",
      "subtitle": "would have applied (shadow)",
      "details": {
        "applied": false,
        "dry_run": true,
        "action_label": "afternoon_start",
        "cool_setpoint_f": 78,
        "heat_setpoint_f": 68,
        "fan_mode": "auto",
        "setpoint_reason": "schedule (MILD coast)",
        "fire_ts": "2026-05-16T14:00:00-05:00",
        "error": null
      },
      "source": {"event": "hvac.actions", "tick_id": "ef67ab89", "ts": "2026-05-16T14:00:00-05:00"}
    }
  }
};
```

- [ ] **Step 2: Typecheck**

```bash
cd tools/cockpit/frontend && npm run typecheck
```
Expected: pass. The inline `: Snapshot` annotation makes TS validate every field; any drift surfaces here.

- [ ] **Step 3: Commit**

```bash
git add tools/cockpit/frontend/src/fixtures/shadow_current.ts
git commit -m "feat(cockpit): shadow_current Phase 1 fixture"
```

#### Task 6: Freshness constants

**Files:**
- Create: `tools/cockpit/frontend/src/freshness.ts`

- [ ] **Step 1: Write the constants**

`tools/cockpit/frontend/src/freshness.ts`:
```ts
// Staleness thresholds per source cadence. Used by future Phase 3 backend
// AND by any frontend code that derives `freshness` from a raw timestamp.
//
// IMPORTANT: hvac.actions is event-driven and NOT a staleness signal.
// Action node renders NOT-FIRED-THIS-TICK / last-fire / APPLIED / SHADOW
// semantics. Liveness comes from hvac.arm_mode + hvac.heartbeat + 5-min
// trace cadence.

import type { Freshness } from './types';

export interface FreshnessThresholds {
  fresh_max_ms: number;
  warn_max_ms: number;
  stale_max_ms: number;
}

const min = (n: number) => n * 60 * 1000;
const hr = (n: number) => n * 60 * 60 * 1000;

export const FRESHNESS_THRESHOLDS: Record<string, FreshnessThresholds> = {
  'decision_trace.price_overlay_eval': { fresh_max_ms: min(6),  warn_max_ms: min(10), stale_max_ms: min(15) },
  'decision_trace.layer_resolution':   { fresh_max_ms: min(6),  warn_max_ms: min(10), stale_max_ms: min(15) },
  'decision_trace.day_type_decision':  { fresh_max_ms: hr(16),  warn_max_ms: hr(30),  stale_max_ms: hr(72)  },
  'decision_trace.precool_decision':   { fresh_max_ms: hr(26),  warn_max_ms: hr(40),  stale_max_ms: hr(72)  },
  'hvac.arm_mode':                     { fresh_max_ms: min(6),  warn_max_ms: min(10), stale_max_ms: min(15) },
  'hvac.thermostat':                   { fresh_max_ms: min(12), warn_max_ms: min(20), stale_max_ms: min(30) },
  'comed.prices':                      { fresh_max_ms: min(6),  warn_max_ms: min(10), stale_max_ms: min(15) },
  'nws.forecast':                      { fresh_max_ms: min(35), warn_max_ms: min(90), stale_max_ms: hr(12)  },
  'pjm.load_forecast':                 { fresh_max_ms: hr(14),  warn_max_ms: hr(28),  stale_max_ms: hr(50)  },
  'pjm.rt_hrl_lmps':                   { fresh_max_ms: min(75), warn_max_ms: hr(3),   stale_max_ms: hr(12)  },
};

export function classifyFreshness(
  source: string,
  ageMs: number,
): Freshness {
  const t = FRESHNESS_THRESHOLDS[source];
  if (!t) return 'fresh';
  if (ageMs <= t.fresh_max_ms) return 'fresh';
  if (ageMs <= t.warn_max_ms)  return 'warn';
  if (ageMs <= t.stale_max_ms) return 'stale';
  return 'missing';
}
```

- [ ] **Step 2: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck
git add tools/cockpit/frontend/src/freshness.ts
git commit -m "feat(cockpit): freshness threshold constants"
```

#### Task 7: Fixture loader + URL param routing

**Files:**
- Create: `tools/cockpit/frontend/src/lib/loadFixture.ts`

- [ ] **Step 1: Write the loader**

`tools/cockpit/frontend/src/lib/loadFixture.ts`:
```ts
import { summerNormal } from '../fixtures/summer_normal';
import { shadowCurrent } from '../fixtures/shadow_current';
import type { Snapshot } from '../types';

// No `as Snapshot` casts. The TS fixtures already declare `: Snapshot` at
// their definition; `satisfies Record<string, Snapshot>` reaffirms shape
// without widening the inferred type (so `FIXTURES.summer_normal` keeps
// its literal-field types).
const FIXTURES = {
  normal: summerNormal,
  summer_normal: summerNormal,
  shadow: shadowCurrent,
  shadow_current: shadowCurrent,
} satisfies Record<string, Snapshot>;

export function loadFixtureFromUrl(): Snapshot {
  const params = new URLSearchParams(window.location.search);
  const name = params.get('fixture') ?? 'summer_normal';
  return (FIXTURES as Record<string, Snapshot>)[name] ?? FIXTURES.summer_normal;
}
```

- [ ] **Step 2: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck
git add tools/cockpit/frontend/src/lib/loadFixture.ts
git commit -m "feat(cockpit): fixture loader with ?fixture= URL param"
```

#### Task 8: Header band component

**Files:**
- Create: `tools/cockpit/frontend/src/components/chips/Chip.tsx`
- Create: `tools/cockpit/frontend/src/components/Header.tsx`

- [ ] **Step 1: Generic Chip primitive**

`tools/cockpit/frontend/src/components/chips/Chip.tsx`:
```tsx
import { type ReactNode } from 'react';

export type ChipTone =
  | 'neutral' | 'sky' | 'emerald' | 'amber' | 'rose' | 'zinc';

const TONE: Record<ChipTone, string> = {
  neutral: 'bg-zinc-800 text-zinc-100 border-zinc-700',
  sky:     'bg-sky-500/20 text-sky-200 border-sky-500/40',
  emerald: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/40',
  amber:   'bg-amber-400/20 text-amber-200 border-amber-400/40',
  rose:    'bg-rose-500/20 text-rose-200 border-rose-500/40',
  zinc:    'bg-zinc-900 text-zinc-400 border-zinc-700',
};

export function Chip({
  tone = 'neutral',
  children,
  testId,
}: { tone?: ChipTone; children: ReactNode; testId?: string }) {
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}
```

- [ ] **Step 2: Header component**

`tools/cockpit/frontend/src/components/Header.tsx`:
```tsx
import { Chip, type ChipTone } from './chips/Chip';
import type { Snapshot, ArmModeActual, SchedulerMode } from '../types';

function armTone(mode: ArmModeActual): ChipTone {
  switch (mode) {
    case 'B-active':                return 'emerald';
    case 'B-fallback':              return 'amber';
    case 'B-down':                  return 'rose';
    case 'A-active':                return 'zinc';
    case 'off-protocol-shadow':     return 'sky';
    case 'off-protocol-production': return 'sky';
    case 'outside-window':          return 'neutral';
  }
}

function modeTone(mode: SchedulerMode): ChipTone {
  if (mode === 'production') return 'emerald';
  if (mode === 'experiment') return 'sky';
  return 'neutral';
}

export function Header({ snapshot }: { snapshot: Snapshot }) {
  const time = new Date(snapshot.snapshot_ts).toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });

  return (
    <header className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-950 px-4 py-2">
      <Chip tone={modeTone(snapshot.scheduler_mode)} testId="chip-scheduler-mode">
        {snapshot.scheduler_mode}
      </Chip>
      <Chip tone={armTone(snapshot.arm_mode.mode_actual)} testId="chip-arm-mode">
        {snapshot.arm_mode.mode_actual}
      </Chip>
      <span
        data-testid="chip-controller-alive"
        className={`inline-block h-2 w-2 rounded-full ${snapshot.controller.alive ? 'bg-emerald-400' : 'bg-rose-500'}`}
        title={snapshot.controller.alive ? 'controller alive' : 'controller down'}
      />
      <span className="ml-auto font-mono text-xs text-zinc-400">{time}</span>
    </header>
  );
}
```

- [ ] **Step 3: Render Header in App**

`tools/cockpit/frontend/src/App.tsx`:
```tsx
import { useMemo } from 'react';
import { loadFixtureFromUrl } from './lib/loadFixture';
import { Header } from './components/Header';

export default function App() {
  const snapshot = useMemo(() => loadFixtureFromUrl(), []);
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <Header snapshot={snapshot} />
    </div>
  );
}
```

- [ ] **Step 4: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck && npm run build
git add tools/cockpit/frontend/src/App.tsx tools/cockpit/frontend/src/components
git commit -m "feat(cockpit): header band with mode + arm + alive + time chips"
```

#### Task 9: Feed-health strip

**Files:**
- Create: `tools/cockpit/frontend/src/components/FeedHealthStrip.tsx`

- [ ] **Step 1: Write the component**

`tools/cockpit/frontend/src/components/FeedHealthStrip.tsx`:
```tsx
import type { FeedHealthEntry, Snapshot, Freshness } from '../types';

const DOT: Record<Freshness, string> = {
  // `motion-safe:` variant gates the pulse on prefers-reduced-motion:
  // no-preference. Static color renders for users who request reduced
  // motion.
  fresh:   'bg-emerald-400 motion-safe:animate-pulse-slow',
  warn:    'bg-amber-400',
  stale:   'bg-rose-500',
  missing: 'bg-zinc-600',
};

const TEXT: Record<Freshness, string> = {
  fresh: 'text-zinc-300', warn: 'text-amber-300',
  stale: 'text-rose-300', missing: 'text-zinc-500',
};

function FeedChip({ entry }: { entry: FeedHealthEntry }) {
  return (
    <span
      data-testid={`feed-chip-${entry.name}`}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs"
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${DOT[entry.status]}`} />
      <span className="font-medium text-zinc-100">{entry.name}</span>
      <span className={`font-mono ${TEXT[entry.status]}`}>{entry.label}</span>
    </span>
  );
}

export function FeedHealthStrip({ snapshot }: { snapshot: Snapshot }) {
  return (
    <div
      data-testid="feed-health-strip"
      className="flex flex-wrap items-center gap-2 border-b border-zinc-800 bg-zinc-950 px-4 py-1.5"
    >
      {snapshot.feed_health.map((f) => <FeedChip key={f.name} entry={f} />)}
    </div>
  );
}
```

- [ ] **Step 2: Add the `pulse-slow` keyframe to Tailwind config**

Edit `tools/cockpit/frontend/tailwind.config.ts` `theme.extend`:
```ts
theme: {
  extend: {
    fontFamily: { /* ...as before... */ },
    keyframes: {
      'pulse-slow': {
        '0%, 100%': { opacity: '1' },
        '50%':      { opacity: '0.45' },
      },
      'march': {
        from: { strokeDashoffset: '0' },
        to:   { strokeDashoffset: '-12' },
      },
    },
    animation: {
      'pulse-slow': 'pulse-slow 2.4s ease-in-out infinite',
      'march':      'march 4.5s linear infinite',
    },
  },
},
```

- [ ] **Step 3: Wire strip into App**

`tools/cockpit/frontend/src/App.tsx`:
```tsx
import { useMemo } from 'react';
import { loadFixtureFromUrl } from './lib/loadFixture';
import { Header } from './components/Header';
import { FeedHealthStrip } from './components/FeedHealthStrip';

export default function App() {
  const snapshot = useMemo(() => loadFixtureFromUrl(), []);
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <Header snapshot={snapshot} />
      <FeedHealthStrip snapshot={snapshot} />
    </div>
  );
}
```

- [ ] **Step 4: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck && npm run build
git add tools/cockpit/frontend
git commit -m "feat(cockpit): feed-health strip with status dots + freshness labels"
```

#### Task 10: Thermostat ring SVG

**Files:**
- Create: `tools/cockpit/frontend/src/components/ThermostatRing.tsx`

- [ ] **Step 1: Write the ring component**

`tools/cockpit/frontend/src/components/ThermostatRing.tsx`:
```tsx
// 270° SVG ring. Indoor temp inside; cool + heat setpoints as ticks on
// the perimeter, color-coded. Range 60-90°F maps to the 270° sweep.

const RANGE_MIN_F = 60;
const RANGE_MAX_F = 90;
const SWEEP_DEG = 270;
const START_DEG = 135; // bottom-left, sweeping clockwise to bottom-right

function tempToAngle(f: number) {
  const clamped = Math.max(RANGE_MIN_F, Math.min(RANGE_MAX_F, f));
  const frac = (clamped - RANGE_MIN_F) / (RANGE_MAX_F - RANGE_MIN_F);
  return START_DEG + frac * SWEEP_DEG;
}

function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = ((deg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

export function ThermostatRing({
  indoor_f,
  cool_f,
  heat_f,
}: { indoor_f: number; cool_f: number; heat_f: number }) {
  const size = 240;
  const cx = size / 2;
  const cy = size / 2;
  const r = 96;

  const trackStart = polar(cx, cy, r, START_DEG);
  const trackEnd = polar(cx, cy, r, START_DEG + SWEEP_DEG);
  const trackPath = `M ${trackStart.x} ${trackStart.y} A ${r} ${r} 0 1 1 ${trackEnd.x} ${trackEnd.y}`;

  const coolTick = polar(cx, cy, r, tempToAngle(cool_f));
  const heatTick = polar(cx, cy, r, tempToAngle(heat_f));

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="block">
      <path d={trackPath} stroke="#27272a" strokeWidth={8} fill="none" strokeLinecap="round" />
      <circle cx={coolTick.x} cy={coolTick.y} r={6} fill="#38bdf8" />
      <circle cx={heatTick.x} cy={heatTick.y} r={6} fill="#fb7185" />
      <text
        x={cx}
        y={cy + 4}
        textAnchor="middle"
        className="fill-zinc-50 font-sans"
        fontSize={56}
        fontWeight={700}
        data-testid="thermostat-indoor-temp"
      >
        {indoor_f.toFixed(1)}
      </text>
      <text
        x={cx}
        y={cy + 36}
        textAnchor="middle"
        className="fill-zinc-400"
        fontSize={14}
      >
        °F
      </text>
    </svg>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck
git add tools/cockpit/frontend/src/components/ThermostatRing.tsx
git commit -m "feat(cockpit): 270° thermostat ring SVG with setpoint ticks"
```

#### Task 11: Price chip

**Files:**
- Create: `tools/cockpit/frontend/src/components/PriceChip.tsx`

- [ ] **Step 1: Write the price chip**

`tools/cockpit/frontend/src/components/PriceChip.tsx`:
```tsx
import type { Price, PriceTier } from '../types';

const TIER_BG: Record<PriceTier, string> = {
  normal:   'bg-emerald-500/15 border-emerald-500/40',
  elevated: 'bg-amber-400/20 border-amber-400/50',
  scarcity: 'bg-rose-500/25 border-rose-500/60',
};

const TIER_TEXT: Record<PriceTier, string> = {
  normal:   'text-emerald-200',
  elevated: 'text-amber-100',
  scarcity: 'text-rose-100',
};

const TIER_PULSE: Record<PriceTier, string> = {
  // `motion-safe:` gates each pulse on prefers-reduced-motion: no-preference.
  // Tier color still applies via TIER_BG; only the pulse animation drops.
  normal:   'motion-safe:animate-pulse-slow',
  elevated: 'motion-safe:animate-pulse-slow',
  scarcity: 'motion-safe:animate-pulse',
};

export function PriceChip({ price }: { price: Price }) {
  return (
    <div
      data-testid="thermostat-price-chip"
      data-tier={price.tier}
      className={`rounded-lg border px-4 py-3 ${TIER_BG[price.tier]} ${TIER_PULSE[price.tier]}`}
    >
      <div className={`font-mono text-3xl font-bold ${TIER_TEXT[price.tier]}`}>
        {price.current_cents_per_kwh.toFixed(1)}{' '}
        <span className="text-base font-medium">¢/kWh</span>
      </div>
      <div className={`text-xs uppercase tracking-wide ${TIER_TEXT[price.tier]}`}>
        {price.tier} tier · {price.freshness_label}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck
git add tools/cockpit/frontend/src/components/PriceChip.tsx
git commit -m "feat(cockpit): price chip with tier color + pulse + freshness label"
```

#### Task 12: Thermostat card

**Files:**
- Create: `tools/cockpit/frontend/src/components/ThermostatCard.tsx`

- [ ] **Step 1: Compose the card**

`tools/cockpit/frontend/src/components/ThermostatCard.tsx`:
```tsx
import type { Snapshot } from '../types';
import { ThermostatRing } from './ThermostatRing';
import { PriceChip } from './PriceChip';

export function ThermostatCard({ snapshot }: { snapshot: Snapshot }) {
  const t = snapshot.thermostat;
  return (
    <aside className="flex w-[30%] min-w-[360px] flex-col gap-4 border-r border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex flex-col items-center">
        <ThermostatRing
          indoor_f={t.indoor_temp_f}
          cool_f={t.cool_setpoint_f}
          heat_f={t.heat_setpoint_f}
        />
        <div className="mt-1 font-mono text-sm text-zinc-300">
          cool <span data-testid="thermostat-cool-setpoint" className="text-sky-300">{t.cool_setpoint_f}</span>°F
          <span className="px-2 text-zinc-600">·</span>
          heat <span className="text-rose-300">{t.heat_setpoint_f}</span>°F
        </div>
        <div className="mt-1 text-xs text-zinc-400">
          {t.hvac_mode} · {t.fan_mode} fan · {t.indoor_humidity_pct}% RH
        </div>
      </div>

      <PriceChip price={snapshot.price} />

      <div className="text-xs text-zinc-400">
        <span className="uppercase tracking-wide text-zinc-500">scheduler:</span>{' '}
        {snapshot.scheduler_mode} · {snapshot.arm_mode.mode_actual}
      </div>

      <div
        data-testid="thermostat-tick-footer"
        className="mt-auto border-t border-zinc-800 pt-2 font-mono text-[10px] text-zinc-500"
      >
        tick {snapshot.latest_tick_id} · {snapshot.thermostat.freshness_label}
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: Wire into App with split layout**

`tools/cockpit/frontend/src/App.tsx`:
```tsx
import { useMemo } from 'react';
import { loadFixtureFromUrl } from './lib/loadFixture';
import { Header } from './components/Header';
import { FeedHealthStrip } from './components/FeedHealthStrip';
import { ThermostatCard } from './components/ThermostatCard';

export default function App() {
  const snapshot = useMemo(() => loadFixtureFromUrl(), []);
  return (
    <div className="flex min-h-screen flex-col bg-zinc-950 text-zinc-100">
      <Header snapshot={snapshot} />
      <FeedHealthStrip snapshot={snapshot} />
      <main className="flex flex-1 overflow-hidden">
        <ThermostatCard snapshot={snapshot} />
        <section className="flex-1 p-4">{/* Decision flow goes here */}</section>
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck + build + run dev server smoke**

```bash
cd tools/cockpit/frontend && npm run typecheck && npm run build
npm run dev  # eyeball-check at localhost:5173, then ctrl-c
```

- [ ] **Step 4: Commit**

```bash
git add tools/cockpit/frontend
git commit -m "feat(cockpit): thermostat card with ring + price chip + scheduler line + footer"
```

#### Task 13: Flow layout coordinates + edges definition

**Files:**
- Create: `tools/cockpit/frontend/src/lib/flowLayout.ts`

- [ ] **Step 1: Define the 8 node positions + edge wiring**

`tools/cockpit/frontend/src/lib/flowLayout.ts`:
```ts
import type { Node, Edge } from '@xyflow/react';

// Manual coordinates for the 8-node parallel-then-merge layout.
// LTR flow: Weather (0) → Day Type (1) → [Schedule, Price, 5CP] → Winner (5) → Supervisor (6) → Action (7)

export const NODE_POSITIONS: Record<string, { x: number; y: number }> = {
  weather:       { x:   0, y: 180 },
  day_type:      { x: 200, y: 180 },
  schedule:      { x: 420, y:  40 },
  price_overlay: { x: 420, y: 180 },
  fivecp:        { x: 420, y: 320 },
  winner:        { x: 640, y: 180 },
  supervisor:    { x: 840, y: 180 },
  action:        { x: 1040, y: 180 },
};

export const STATIC_NODES: Node[] = Object.entries(NODE_POSITIONS).map(
  ([id, position]) => ({
    id,
    position,
    type: id,  // each node type registered with the same key
    data: {},  // populated at render time from the Snapshot
  }),
);

export const STATIC_EDGES: Edge[] = [
  { id: 'e-weather-daytype',           source: 'weather',       target: 'day_type',      type: 'default' },
  { id: 'e-daytype-schedule',          source: 'day_type',      target: 'schedule',      type: 'active' },
  { id: 'e-daytype-price',             source: 'day_type',      target: 'price_overlay', type: 'active' },
  { id: 'e-daytype-fivecp',            source: 'day_type',      target: 'fivecp',        type: 'active' },
  { id: 'e-schedule-winner',           source: 'schedule',      target: 'winner',        type: 'active' },
  { id: 'e-price-winner',              source: 'price_overlay', target: 'winner',        type: 'active' },
  { id: 'e-fivecp-winner',             source: 'fivecp',        target: 'winner',        type: 'active' },
  { id: 'e-winner-supervisor',         source: 'winner',        target: 'supervisor',    type: 'active',
    data: { testId: 'edge-winner-active' } },
  { id: 'e-supervisor-action',         source: 'supervisor',    target: 'action',        type: 'actionEdge',
    data: { testId: 'edge-supervisor-to-action' } },
];
```

- [ ] **Step 2: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck
git add tools/cockpit/frontend/src/lib/flowLayout.ts
git commit -m "feat(cockpit): manual coords + edge wiring for 8-node flow"
```

#### Task 14: BaseNode envelope component

**Files:**
- Create: `tools/cockpit/frontend/src/components/nodes/BaseNode.tsx`

- [ ] **Step 1: Write the shared node envelope**

`tools/cockpit/frontend/src/components/nodes/BaseNode.tsx`:
```tsx
import { type ReactNode } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { RoleState, Freshness } from '../../types';

const BODY_BG: Record<RoleState, string> = {
  winning:        'bg-zinc-900',
  dimmed:         'bg-zinc-900/60',
  stale:          'bg-zinc-900/40',
  missing:        'bg-zinc-900/20',
  not_applicable: 'bg-zinc-900/30',
  clamped:        'bg-rose-900/40',
  emergency:      'bg-rose-700/50',
  context:        'bg-zinc-900/40',
};

const BODY_BORDER: Record<RoleState, string> = {
  winning:        'border-zinc-700',
  dimmed:         'border-zinc-800',
  stale:          'border-amber-500/40',
  missing:        'border-zinc-800',
  not_applicable: 'border-zinc-800',
  clamped:        'border-rose-500',
  emergency:      'border-rose-400 motion-safe:animate-pulse',
  context:        'border-zinc-800',
};

const TEXT: Record<RoleState, string> = {
  winning:        'text-zinc-100',
  dimmed:         'text-zinc-400',
  stale:          'text-amber-200',
  missing:        'text-zinc-600',
  not_applicable: 'text-zinc-500',
  clamped:        'text-rose-100',
  emergency:      'text-rose-50',
  context:        'text-zinc-400',
};

const FRESHNESS_DOT: Record<Freshness, string> = {
  fresh: 'bg-emerald-400', warn: 'bg-amber-400',
  stale: 'bg-rose-500', missing: 'bg-zinc-600',
};

export interface BaseNodeProps {
  nodeId: string;
  role_state: RoleState;
  freshness: Freshness;
  freshness_label: string;
  title: string;
  subtitle: string;
  children?: ReactNode;
  testId: string;
  changed?: boolean;
}

export function BaseNode({
  nodeId, role_state, freshness, freshness_label, title, subtitle, children, testId, changed,
}: BaseNodeProps) {
  const ring = role_state === 'winning'
    ? 'ring-2 ring-sky-400/80 shadow-[0_0_24px_rgba(56,189,248,0.35)]'
    : '';
  return (
    <div
      data-testid={testId}
      data-role-state={role_state}
      data-changed={changed ? 'true' : 'false'}
      className={`relative w-[200px] rounded-lg border p-3 ${BODY_BG[role_state]} ${BODY_BORDER[role_state]} ${ring}`}
    >
      <Handle id={`${nodeId}-in`} type="target" position={Position.Left} className="!bg-zinc-700" />
      <Handle id={`${nodeId}-out`} type="source" position={Position.Right} className="!bg-zinc-700" />

      <div className={`text-[11px] uppercase tracking-wider ${TEXT[role_state]}`}>{title}</div>
      <div className={`mt-0.5 text-sm font-medium ${TEXT[role_state]}`}>{subtitle}</div>
      {children && (
        <div className={`mt-2 space-y-1 text-xs ${TEXT[role_state]}`}>{children}</div>
      )}
      <div className="mt-2 flex items-center gap-1.5 text-[10px] text-zinc-500">
        <span className={`inline-block h-1 w-1 rounded-full ${FRESHNESS_DOT[freshness]}`} />
        <span className="font-mono">{freshness_label}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck
git add tools/cockpit/frontend/src/components/nodes/BaseNode.tsx
git commit -m "feat(cockpit): BaseNode envelope with role_state styling + freshness footer"
```

#### Task 15: Per-node body components (8 nodes)

**Files (all create):**
- `tools/cockpit/frontend/src/components/nodes/WeatherNode.tsx`
- `tools/cockpit/frontend/src/components/nodes/DayTypeNode.tsx`
- `tools/cockpit/frontend/src/components/nodes/ScheduleNode.tsx`
- `tools/cockpit/frontend/src/components/nodes/PriceOverlayNode.tsx`
- `tools/cockpit/frontend/src/components/nodes/FiveCPNode.tsx`
- `tools/cockpit/frontend/src/components/nodes/WinnerNode.tsx`
- `tools/cockpit/frontend/src/components/nodes/SupervisorNode.tsx`
- `tools/cockpit/frontend/src/components/nodes/ActionNode.tsx`

Each node receives the `BaseNodeEnvelope<TDetails>` shape via React Flow's `data` prop and renders a body specific to its details. The signature is `function FooNode({ data }: NodeProps<MyNodeType>)`.

- [ ] **Step 1: WeatherNode**

`tools/cockpit/frontend/src/components/nodes/WeatherNode.tsx`:
```tsx
import type { NodeProps, Node } from '@xyflow/react';
import { BaseNode } from './BaseNode';
import type { BaseNodeEnvelope, WeatherDetails } from '../../types';

type WeatherNodeType = Node<BaseNodeEnvelope<WeatherDetails>>;

export function WeatherNode({ data, id }: NodeProps<WeatherNodeType>) {
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      testId="node-weather"
    >
      <div>outdoor: {data.details.current_outdoor_f.toFixed(1)}°F</div>
      <div>high: {data.details.today_high_f}°F (apparent {data.details.apparent_max_f}°F)</div>
      <div>dewpoint: {data.details.dewpoint_max_f}°F</div>
      {data.details.heat_advisory && <div className="text-rose-300">heat advisory</div>}
    </BaseNode>
  );
}
```

- [ ] **Step 2: DayTypeNode**

`tools/cockpit/frontend/src/components/nodes/DayTypeNode.tsx`:
```tsx
import type { NodeProps, Node } from '@xyflow/react';
import { BaseNode } from './BaseNode';
import type { BaseNodeEnvelope, DayTypeDetails } from '../../types';

type DayTypeNodeType = Node<BaseNodeEnvelope<DayTypeDetails>>;

export function DayTypeNode({ data, id }: NodeProps<DayTypeNodeType>) {
  const fired = data.details.evaluation_tape.find((e) => e.fired);
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.details.winning_day_type}
      testId="node-day-type"
    >
      <div className="font-mono text-[10px]">{fired?.code ?? data.details.reason_code}</div>
      <div className="text-zinc-500">{data.details.evaluation_tape.length} rules evaluated</div>
    </BaseNode>
  );
}
```

- [ ] **Step 3: ScheduleNode**

`tools/cockpit/frontend/src/components/nodes/ScheduleNode.tsx`:
```tsx
import type { NodeProps, Node } from '@xyflow/react';
import { BaseNode } from './BaseNode';
import type { BaseNodeEnvelope, ScheduleDetails } from '../../types';

type ScheduleNodeType = Node<BaseNodeEnvelope<ScheduleDetails>>;

export function ScheduleNode({ data, id }: NodeProps<ScheduleNodeType>) {
  const d = data.details;
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      testId="node-schedule"
    >
      <div>action: <span className="font-mono">{d.action_label}</span></div>
      <div>
        cool: {d.base_schedule_cool_f}°F
        {d.base_schedule_cool_f !== d.effective_schedule_cool_f && (
          <> → <span className="text-sky-300">{d.effective_schedule_cool_f}°F</span></>
        )}
      </div>
      {d.humid_override_active && (
        <div className="text-amber-300">humid override → {d.humid_override_setpoint_f}°F</div>
      )}
      {d.precool_window && (
        <div className="text-cyan-300">precool {d.precool_window.hour_ct}:00 → {d.precool_window.depth_f}°F</div>
      )}
    </BaseNode>
  );
}
```

- [ ] **Step 4: PriceOverlayNode**

`tools/cockpit/frontend/src/components/nodes/PriceOverlayNode.tsx`:
```tsx
import type { NodeProps, Node } from '@xyflow/react';
import { BaseNode } from './BaseNode';
import type { BaseNodeEnvelope, PriceOverlayDetails } from '../../types';

type PriceOverlayNodeType = Node<BaseNodeEnvelope<PriceOverlayDetails>>;

const TIER_TEXT = {
  normal: 'text-emerald-300', elevated: 'text-amber-300', scarcity: 'text-rose-300',
} as const;

export function PriceOverlayNode({ data, id }: NodeProps<PriceOverlayNodeType>) {
  const d = data.details;
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      testId="node-price-overlay"
    >
      <div>
        price: <span className="font-mono">
          {d.price_cents !== null ? `${d.price_cents.toFixed(1)}¢` : '—'}
        </span>
      </div>
      <div>
        tier: <span className={TIER_TEXT[d.new_tier]}>{d.new_tier}</span>
        {d.prev_tier !== d.new_tier && (
          <span className="text-zinc-500"> (was {d.prev_tier})</span>
        )}
      </div>
      <div className="font-mono text-[10px] text-zinc-500">{d.reason_code}</div>
    </BaseNode>
  );
}
```

- [ ] **Step 5: FiveCPNode**

`tools/cockpit/frontend/src/components/nodes/FiveCPNode.tsx`:
```tsx
import type { NodeProps, Node } from '@xyflow/react';
import { BaseNode } from './BaseNode';
import type { BaseNodeEnvelope, FiveCPDetails } from '../../types';

type FiveCPNodeType = Node<BaseNodeEnvelope<FiveCPDetails>>;

export function FiveCPNode({ data, id }: NodeProps<FiveCPNodeType>) {
  const d = data.details;
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      testId="node-fivecp"
    >
      <div>
        active: <span className={d.fivecp_active ? 'text-rose-300' : 'text-zinc-500'}>
          {d.fivecp_active ? 'yes' : 'no'}
        </span>
      </div>
      <div>in season: {d.in_season ? 'yes' : 'no'}</div>
      {d.fivecp_scopes_fired.length > 0 && (
        <div>scopes: <span className="font-mono">{d.fivecp_scopes_fired.join(', ')}</span></div>
      )}
      {d.fivecp_cool_f !== null && <div>shutoff: {d.fivecp_cool_f}°F</div>}
    </BaseNode>
  );
}
```

- [ ] **Step 6: WinnerNode**

`tools/cockpit/frontend/src/components/nodes/WinnerNode.tsx`:
```tsx
import type { NodeProps, Node } from '@xyflow/react';
import { BaseNode } from './BaseNode';
import type { BaseNodeEnvelope, WinnerDetails } from '../../types';

type WinnerNodeType = Node<BaseNodeEnvelope<WinnerDetails>>;

const LAYER_LABEL: Record<WinnerDetails['winning_layer'], string> = {
  schedule: 'Schedule', price_overlay: 'Price Overlay',
  fivecp: '5CP', tie: 'Tie (warmer wins)',
};

export function WinnerNode({ data, id }: NodeProps<WinnerNodeType>) {
  const d = data.details;
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={LAYER_LABEL[d.winning_layer]}
      testId="node-winner"
      changed={d.changed}
    >
      <div>
        cool: <span className="font-mono text-sky-300 text-base">{d.effective_cool_f}°F</span>
      </div>
      {d.changed && (
        <div className="text-zinc-500">was {d.prev_effective_cool_f}°F</div>
      )}
      <div className="font-mono text-[10px] text-zinc-500">{d.reason_code}</div>
    </BaseNode>
  );
}
```

- [ ] **Step 7: SupervisorNode**

`tools/cockpit/frontend/src/components/nodes/SupervisorNode.tsx`:
```tsx
import type { NodeProps, Node } from '@xyflow/react';
import { BaseNode } from './BaseNode';
import type { BaseNodeEnvelope, SupervisorDetails, RoleState } from '../../types';

type SupervisorNodeType = Node<BaseNodeEnvelope<SupervisorDetails>>;

function supervisorRoleOverride(d: SupervisorDetails, fallback: RoleState): RoleState {
  if (d.decision === 'emergency') return 'emergency';
  if (d.decision === 'clamped') return 'clamped';
  return fallback;
}

export function SupervisorNode({ data, id }: NodeProps<SupervisorNodeType>) {
  const d = data.details;
  const role = supervisorRoleOverride(d, data.role_state);
  return (
    <BaseNode
      nodeId={id}
      role_state={role}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={d.decision ?? 'not invoked'}
      testId="node-supervisor"
    >
      {d.decision === null ? (
        <div className="text-zinc-500">no setpoint proposed this tick</div>
      ) : (
        <>
          <div>
            cool: {d.proposed_cool_f}
            {d.proposed_cool_f !== d.final_cool_f && (
              <> → <span className="text-rose-300">{d.final_cool_f}°F</span></>
            )}
          </div>
          <div>heat: {d.proposed_heat_f}</div>
          <div className="font-mono text-[10px] text-zinc-500">{d.reason_code}</div>
          {d.indoor_temp_available === false && (
            <div className="text-amber-300">indoor temp unavailable</div>
          )}
        </>
      )}
    </BaseNode>
  );
}
```

- [ ] **Step 8: ActionNode**

`tools/cockpit/frontend/src/components/nodes/ActionNode.tsx`:
```tsx
import type { NodeProps, Node } from '@xyflow/react';
import { BaseNode } from './BaseNode';
import type { BaseNodeEnvelope, ActionDetails } from '../../types';

type ActionNodeType = Node<BaseNodeEnvelope<ActionDetails>>;

function actionBadge(d: ActionDetails): { label: string; tone: string } {
  if (d.applied === true && d.dry_run === false) {
    return { label: 'APPLIED', tone: 'bg-emerald-500/30 text-emerald-100' };
  }
  if (d.dry_run === true) {
    return { label: 'SHADOW', tone: 'bg-sky-500/30 text-sky-100' };
  }
  if (d.error) {
    return { label: 'ERROR', tone: 'bg-rose-500/30 text-rose-100' };
  }
  return { label: 'NOT-FIRED-THIS-TICK', tone: 'bg-zinc-700 text-zinc-300' };
}

export function ActionNode({ data, id }: NodeProps<ActionNodeType>) {
  const d = data.details;
  const badge = actionBadge(d);
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={d.action_label ?? '—'}
      testId="node-action"
    >
      <div className="flex items-center gap-2">
        <span
          data-testid="action-badge"
          className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${badge.tone}`}
        >
          {badge.label}
        </span>
      </div>
      {d.cool_setpoint_f !== null && <div>cool: {d.cool_setpoint_f}°F</div>}
      {d.fire_ts && (
        <div className="font-mono text-[10px] text-zinc-500">
          {new Date(d.fire_ts).toLocaleTimeString()}
        </div>
      )}
      {d.error && <div className="text-rose-300">{d.error}</div>}
    </BaseNode>
  );
}
```

- [ ] **Step 9: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck
git add tools/cockpit/frontend/src/components/nodes
git commit -m "feat(cockpit): 8 per-node body components"
```

#### Task 16: Edge components

**Files (all create):**
- `tools/cockpit/frontend/src/components/edges/DefaultEdge.tsx`
- `tools/cockpit/frontend/src/components/edges/ActiveEdge.tsx`
- `tools/cockpit/frontend/src/components/edges/ActionEdge.tsx`

- [ ] **Step 1: DefaultEdge — static, no animation**

`tools/cockpit/frontend/src/components/edges/DefaultEdge.tsx`:
```tsx
import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react';

export function DefaultEdge(props: EdgeProps) {
  const [path] = getBezierPath(props);
  return <BaseEdge id={props.id} path={path} style={{ stroke: '#3f3f46', strokeWidth: 1.5 }} />;
}
```

- [ ] **Step 2: ActiveEdge — marching dash when `data.active`, plain when not**

`tools/cockpit/frontend/src/components/edges/ActiveEdge.tsx`:
```tsx
import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react';

type ActiveEdgeData = { active?: boolean; testId?: string };

export function ActiveEdge(props: EdgeProps) {
  const [path] = getBezierPath(props);
  const data = props.data as ActiveEdgeData | undefined;
  const active = data?.active ?? false;
  const motionOk = typeof window === 'undefined'
    ? true
    : !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const stroke = active ? '#38bdf8' : '#3f3f46';
  const animatedAttr = active && motionOk ? 'true' : 'false';

  return (
    <g data-testid={data?.testId} data-animated={animatedAttr}>
      <BaseEdge
        id={props.id}
        path={path}
        style={{
          stroke,
          strokeWidth: active ? 2 : 1.5,
          strokeDasharray: active ? '6 6' : undefined,
        }}
        className={active && motionOk ? 'animate-march' : undefined}
      />
    </g>
  );
}
```

- [ ] **Step 3: ActionEdge — solid in production, dashed in shadow**

`tools/cockpit/frontend/src/components/edges/ActionEdge.tsx`:
```tsx
import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react';

type ActionEdgeData = { shadow?: boolean; testId?: string };

export function ActionEdge(props: EdgeProps) {
  const [path] = getBezierPath(props);
  const data = props.data as ActionEdgeData | undefined;
  const shadow = data?.shadow ?? false;
  const style = shadow ? 'dashed' : 'solid';
  return (
    <g data-testid={data?.testId} data-edge-style={style}>
      <BaseEdge
        id={props.id}
        path={path}
        style={{
          stroke: shadow ? '#71717a' : '#10b981',
          strokeWidth: 2,
          strokeDasharray: shadow ? '8 5' : undefined,
        }}
      />
    </g>
  );
}
```

- [ ] **Step 4: Typecheck + commit**

```bash
cd tools/cockpit/frontend && npm run typecheck
git add tools/cockpit/frontend/src/components/edges
git commit -m "feat(cockpit): edge components (default, active, action shadow/solid)"
```

#### Task 17: DecisionFlow canvas (the Phase 1 capstone)

**Files:**
- Create: `tools/cockpit/frontend/src/components/DecisionFlow.tsx`

- [ ] **Step 1: Compose the canvas**

`tools/cockpit/frontend/src/components/DecisionFlow.tsx`:
```tsx
import { useMemo } from 'react';
import { ReactFlow, Background } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import type { Snapshot, Flow } from '../types';
import { STATIC_NODES, STATIC_EDGES } from '../lib/flowLayout';

import { WeatherNode } from './nodes/WeatherNode';
import { DayTypeNode } from './nodes/DayTypeNode';
import { ScheduleNode } from './nodes/ScheduleNode';
import { PriceOverlayNode } from './nodes/PriceOverlayNode';
import { FiveCPNode } from './nodes/FiveCPNode';
import { WinnerNode } from './nodes/WinnerNode';
import { SupervisorNode } from './nodes/SupervisorNode';
import { ActionNode } from './nodes/ActionNode';

import { DefaultEdge } from './edges/DefaultEdge';
import { ActiveEdge } from './edges/ActiveEdge';
import { ActionEdge } from './edges/ActionEdge';

const NODE_TYPES = {
  weather: WeatherNode,
  day_type: DayTypeNode,
  schedule: ScheduleNode,
  price_overlay: PriceOverlayNode,
  fivecp: FiveCPNode,
  winner: WinnerNode,
  supervisor: SupervisorNode,
  action: ActionNode,
};

const EDGE_TYPES = {
  default: DefaultEdge,
  active: ActiveEdge,
  actionEdge: ActionEdge,
};

import type { WinnerDetails } from '../types';

// Returns the set of lane node IDs that should glow as "winning." The
// `'tie'` case is real data (LAYER_RESOLUTION_TIE_WARMER_WINS fires when
// multiple lanes propose the same warmest setpoint); rendering it as all
// three lanes glowing matches the semantic "multiple lanes agreed at the
// warmest." A future Phase 2 polish task may add an explicit "tie" badge
// on the Winner node body; until then, the lane-set rendering is the
// only visible cue.
function winningLanes(layer: WinnerDetails['winning_layer']): Set<string> {
  if (layer === 'tie') return new Set(['schedule', 'price_overlay', 'fivecp']);
  if (layer === 'price_overlay') return new Set(['price_overlay']);
  if (layer === 'fivecp') return new Set(['fivecp']);
  return new Set(['schedule']);
}

export function DecisionFlow({ snapshot }: { snapshot: Snapshot }) {
  const nodes = useMemo(() => {
    return STATIC_NODES.map((n) => ({
      ...n,
      data: snapshot.flow[n.id as keyof Flow],
    }));
  }, [snapshot]);

  const edges = useMemo(() => {
    const lanes = winningLanes(snapshot.flow.winner.details.winning_layer);
    const supervisorInvoked = snapshot.flow.supervisor.details.decision !== null;
    // Edge style represents the DELIVERY CHANNEL state: are setpoint writes
    // PHYSICALLY HAPPENING right now? Solid when yes:
    //   - scheduler_mode='production' — writes always happen (regardless of
    //     arm_mode value, including off-protocol-production where writes
    //     are happening but outside the experiment protocol).
    //   - scheduler_mode='experiment' AND arm_mode='B-active' — writes
    //     during the B-arm of the locked calendar.
    // Dashed when no:
    //   - scheduler_mode='shadow' — never writes (including off-protocol-
    //     shadow inside the calendar window).
    //   - scheduler_mode='experiment' with arm_mode='A-active' / 'B-fallback'
    //     / 'B-down' / 'outside-window'.
    // The off-protocol distinction (writes happening but NOT under protocol)
    // is signaled by the arm-mode chip in the header band, NOT by the edge
    // style. This decoupling lets the operator read "writes are firing" AND
    // "this is off-protocol" as two independent visual signals.
    // Action node badge (APPLIED / SHADOW / NOT-FIRED-THIS-TICK / ERROR)
    // drives separately from action.details.applied / dry_run / error — it
    // describes the most-recent action outcome, not the delivery channel.
    const writesAllowed =
      snapshot.scheduler_mode === 'production' ||
      (snapshot.scheduler_mode === 'experiment' &&
        snapshot.arm_mode.mode_actual === 'B-active');
    const shadowEdge = !writesAllowed;

    return STATIC_EDGES.map((e) => {
      // Day Type → lane: every lane in the winning set is "active" (marching).
      if (e.source === 'day_type') {
        return { ...e, data: { ...(e.data ?? {}), active: lanes.has(e.target) } };
      }
      // Lane → Winner: every lane in the winning set is active.
      if (e.target === 'winner') {
        return { ...e, data: { ...(e.data ?? {}), active: lanes.has(e.source) } };
      }
      // Winner → Supervisor: active when supervisor was invoked.
      if (e.id === 'e-winner-supervisor') {
        return { ...e, data: { ...(e.data ?? {}), active: supervisorInvoked } };
      }
      // Supervisor → Action: dashed when writes are not currently allowed.
      if (e.id === 'e-supervisor-action') {
        return { ...e, data: { ...(e.data ?? {}), shadow: shadowEdge } };
      }
      return e;
    });
  }, [snapshot]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      fitView
      proOptions={{ hideAttribution: true }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      panOnDrag={false}
      zoomOnScroll={false}
      zoomOnDoubleClick={false}
    >
      <Background gap={24} size={1} color="#27272a" />
    </ReactFlow>
  );
}
```

- [ ] **Step 2: Wire DecisionFlow into App**

`tools/cockpit/frontend/src/App.tsx`:
```tsx
import { useMemo } from 'react';
import { loadFixtureFromUrl } from './lib/loadFixture';
import { Header } from './components/Header';
import { FeedHealthStrip } from './components/FeedHealthStrip';
import { ThermostatCard } from './components/ThermostatCard';
import { DecisionFlow } from './components/DecisionFlow';

export default function App() {
  const snapshot = useMemo(() => loadFixtureFromUrl(), []);
  return (
    <div className="flex min-h-screen flex-col bg-zinc-950 text-zinc-100">
      <Header snapshot={snapshot} />
      <FeedHealthStrip snapshot={snapshot} />
      <main className="flex flex-1 overflow-hidden">
        <ThermostatCard snapshot={snapshot} />
        <section className="flex-1">
          <DecisionFlow snapshot={snapshot} />
        </section>
      </main>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck + build + dev smoke**

```bash
cd tools/cockpit/frontend && npm run typecheck && npm run build
npm run dev
```
Open `localhost:5173/` — should render full cockpit with the `summerNormal` fixture. Open `localhost:5173/?fixture=shadow` — should render `shadowCurrent`.

- [ ] **Step 4: Commit**

```bash
git add tools/cockpit/frontend
git commit -m "feat(cockpit): DecisionFlow canvas — 8 nodes + edge state derivation"
```

#### Task 18: Framer Motion polish — BaseNode pulse + count-up + supervisor red flash + MotionConfig

Locked Q6 animation budget includes node pulse on `role_state` change, smooth number count-up for temp/setpoint, and supervisor red flash on clamp/emergency. Tasks 14-17 implemented only static Tailwind classes. This task lands the four Framer Motion pieces.

**Files:**
- Modify: `tools/cockpit/frontend/src/main.tsx`
- Modify: `tools/cockpit/frontend/src/components/nodes/BaseNode.tsx`
- Modify: `tools/cockpit/frontend/src/components/ThermostatRing.tsx`
- Create:  `tools/cockpit/frontend/src/lib/useCountUp.ts`

- [ ] **Step 1: Wrap App in Framer MotionConfig**

`tools/cockpit/frontend/src/main.tsx`:
```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MotionConfig } from 'framer-motion';
import App from './App';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <App />
    </MotionConfig>
  </StrictMode>,
);
```

`reducedMotion="user"` reads `prefers-reduced-motion` from the OS and disables every `motion.*` component animation when reduced motion is requested. CSS `animate-*` classes are independently gated by the `motion-safe:` Tailwind variant (Tasks 9, 11, 14).

- [ ] **Step 2: BaseNode pulse on `role_state` change**

Wrap BaseNode's outer div in `motion.div` and re-fire a scale animation when `role_state` changes. Implementation uses a `useRef` to track the previous role and a `useState` key counter to re-key the motion component (forcing the animation to re-run only on transition, not on every re-render).

Modify `tools/cockpit/frontend/src/components/nodes/BaseNode.tsx`. Replace the outer `<div>` with `motion.div`, add the transition logic, and bump the key on role change:

```tsx
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Handle, Position } from '@xyflow/react';
import type { RoleState, Freshness } from '../../types';

// ... BODY_BG / BODY_BORDER / TEXT / FRESHNESS_DOT constants unchanged ...

export function BaseNode({
  nodeId, role_state, freshness, freshness_label, title, subtitle, children, testId, changed,
}: BaseNodeProps) {
  // Re-fire the pulse animation on role_state transition. Bumping `pulseKey`
  // remounts the motion component, which re-runs `animate` from `initial`.
  // Without this, Framer Motion treats `animate={{scale:[...]}}` as a
  // continuous loop on every render and the pulse becomes wallpaper.
  const prevRoleRef = useRef(role_state);
  const [pulseKey, setPulseKey] = useState(0);
  useEffect(() => {
    if (prevRoleRef.current !== role_state) {
      setPulseKey((k) => k + 1);
      prevRoleRef.current = role_state;
    }
  }, [role_state]);

  const ring = role_state === 'winning'
    ? 'ring-2 ring-sky-400/80 shadow-[0_0_24px_rgba(56,189,248,0.35)]'
    : '';

  // Supervisor clamped + emergency: one-shot red-flash transition on top of
  // the persistent border-rose styling. Driven by an `animate` keyframe
  // sequence; gated automatically by MotionConfig reducedMotion.
  const flashKeyframes =
    role_state === 'emergency' ? { boxShadow: ['0 0 0 rgba(244,63,94,0)', '0 0 32px rgba(244,63,94,0.7)', '0 0 0 rgba(244,63,94,0)'] }
    : role_state === 'clamped' ? { boxShadow: ['0 0 0 rgba(244,63,94,0)', '0 0 18px rgba(244,63,94,0.5)', '0 0 0 rgba(244,63,94,0)'] }
    : undefined;

  return (
    <motion.div
      key={pulseKey}
      data-testid={testId}
      data-role-state={role_state}
      data-changed={changed ? 'true' : 'false'}
      className={`relative w-[200px] rounded-lg border p-3 ${BODY_BG[role_state]} ${BODY_BORDER[role_state]} ${ring}`}
      initial={{ scale: 1 }}
      animate={flashKeyframes ? { scale: [1, 1.04, 1], ...flashKeyframes } : { scale: [1, 1.04, 1] }}
      transition={{ duration: 0.3, ease: 'easeInOut' }}
    >
      {/* ...Handle + content unchanged... */}
    </motion.div>
  );
}
```

The pulse fires only on `role_state` transition (key change). The clamp / emergency red-flash piggybacks on the same animation cycle when the role enters those states.

- [ ] **Step 3: Smooth count-up hook + ThermostatRing wiring**

`tools/cockpit/frontend/src/lib/useCountUp.ts`:
```ts
import { useEffect } from 'react';
import { useMotionValue, useTransform, useSpring, type MotionValue } from 'framer-motion';

// Returns a MotionValue that smoothly tracks `target`. Display via
// `useTransform(value, (v) => v.toFixed(decimals))` and bind into a
// `<motion.tspan>` or `<motion.span>`.
export function useCountUp(target: number, decimals = 1): MotionValue<string> {
  const raw = useMotionValue(target);
  const spring = useSpring(raw, { stiffness: 100, damping: 30 });
  const display = useTransform(spring, (v) => v.toFixed(decimals));
  useEffect(() => {
    raw.set(target);
  }, [target, raw]);
  return display;
}
```

Modify `tools/cockpit/frontend/src/components/ThermostatRing.tsx`. Replace the static `<text>` for indoor temp with `motion.text` bound to the count-up motion value:

```tsx
import { motion } from 'framer-motion';
import { useCountUp } from '../lib/useCountUp';

// ... existing constants + polar / tempToAngle helpers unchanged ...

export function ThermostatRing({
  indoor_f, cool_f, heat_f,
}: { indoor_f: number; cool_f: number; heat_f: number }) {
  const indoorDisplay = useCountUp(indoor_f, 1);
  // ... size / cx / cy / r / track / ticks setup unchanged ...

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="block">
      <path d={trackPath} stroke="#27272a" strokeWidth={8} fill="none" strokeLinecap="round" />
      <circle cx={coolTick.x} cy={coolTick.y} r={6} fill="#38bdf8" />
      <circle cx={heatTick.x} cy={heatTick.y} r={6} fill="#fb7185" />
      <motion.text
        x={cx}
        y={cy + 4}
        textAnchor="middle"
        className="fill-zinc-50 font-sans"
        fontSize={56}
        fontWeight={700}
        data-testid="thermostat-indoor-temp"
      >
        {indoorDisplay}
      </motion.text>
      <text x={cx} y={cy + 36} textAnchor="middle" className="fill-zinc-400" fontSize={14}>°F</text>
    </svg>
  );
}
```

When MotionConfig has `reducedMotion="user"` and the OS requests reduced motion, the spring jumps directly to target without interpolating — count-up degrades to instant value-set, which is the correct accessibility behavior.

- [ ] **Step 4: Typecheck + build + run**

```bash
cd tools/cockpit/frontend && npm run typecheck && npm run build && npm run dev
```

Open `localhost:5173/?fixture=shadow` then back to `localhost:5173/`. Observe the indoor temp counts up smoothly between fixtures, the lane node pulses on the role-state difference, and the Supervisor (when in a clamp/emergency fixture) red-flashes once on the transition.

- [ ] **Step 5: Commit**

```bash
git add tools/cockpit/frontend
git commit -m "feat(cockpit): Framer Motion polish — node pulse + count-up + supervisor flash + MotionConfig"
```

#### Task 19: Drive the outside-in test green

**Files:**
- Modify: `tools/cockpit/frontend/src/__tests__/cockpit.acceptance.test.tsx`

- [ ] **Step 1: Remove any `.fails` / xfail markers added in Task 2**

The test was written with markers during intra-Phase-1 development. Strip them now so the suite asserts genuine passing.

- [ ] **Step 2: Run the test**

```bash
cd tools/cockpit/frontend && npm run test
```
Expected: all three test cases pass.

If any fail, fix the underlying component (NOT the test) — the test is the contract.

- [ ] **Step 3: Run full acceptance gates**

```bash
cd tools/cockpit/frontend && npm run typecheck && npm run build && npm run test
```
Expected: all three pass clean.

- [ ] **Step 4: Commit**

```bash
git add tools/cockpit/frontend/src/__tests__
git commit -m "test(cockpit): Phase 1 outside-in acceptance test green"
```

#### Task 20: README + Phase 1 PR

**Files:**
- Create: `tools/cockpit/README.md`

- [ ] **Step 1: Write the README**

`tools/cockpit/README.md`:
```markdown
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

- `<http://localhost:5173/>` — `summer_normal` (Arm B-active, July, fire-time tick)
- `<http://localhost:5173/?fixture=shadow>` — `shadow_current` (pre-experiment shadow, outside-window)

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | dev server on :5173 |
| `npm run build` | production build to `dist/` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run test` | Vitest suite (acceptance test) |

## Phase 2 (planned)

Adds 7 more fixtures (price_spike, fivecp_risk, supervisor_clamp, controller_down,
feed_outage, mild_day, arm_switch) and refines node-state edge cases + animation.

## Phase 3 (planned)

Adds a FastAPI proxy under `tools/cockpit/backend/` that queries the live
Pi-lab InfluxDB + Loki and returns the same snapshot JSON shape. Frontend
swaps the fixture import for `fetch('/api/snapshot')` + polling.

## Architecture

Read-only by design. No drag, no setpoint controls, no writes back to the
scheduler. Snapshot is assembled from existing `decision_trace.*` Loki logs
and `hvac.*` Influx measurements — cockpit cannot introduce control-path
behavior changes.

Locked design decisions live in [`docs/plans/cockpit-plan.md`](../../docs/plans/cockpit-plan.md).
```

- [ ] **Step 2: Final pre-PR gates**

```bash
cd tools/cockpit/frontend && npm run typecheck && npm run build && npm run test
```
All three must pass.

- [ ] **Step 3: Open PR**

```bash
git push -u origin cockpit-phase1
gh pr create --base main --title "feat(cockpit): Phase 1 mock-fixture UI tracer" --body "$(cat <<'EOF'
## Summary

- New `tools/cockpit/` workstation-local dashboard
- 8-node parallel-then-merge React Flow canvas + thermostat instrument card
- Two checked-in TS fixtures (`summer_normal.ts`, `shadow_current.ts`) drive Phase 1 rendering — typed `: Snapshot` at the source so drift fails typecheck
- Outside-in acceptance test green at this PR boundary

## Test plan

- [x] `npm run typecheck` passes
- [x] `npm run build` passes
- [x] `npm run test` passes (Phase 1 acceptance test green)
- [x] Manual smoke at `localhost:5173/` shows summer_normal
- [x] Manual smoke at `localhost:5173/?fixture=shadow` shows shadow_current with dashed edge + SHADOW badge

Plan: `docs/plans/cockpit-plan.md`
EOF
)"
```

Stop at `gh pr create`. Chris reviews and merges per AGENTS.md branching policy.

### Phase 2 — fixture set expansion + edge-case polish

Adds 7 fixtures and the visual polish work surfaced by rendering them.

Each fixture lands in its own task; refinement work batches at the end. All Phase 2 tasks extend or add to the outside-in test — its scope grows but it stays green at every Phase 2 PR boundary.

#### Task 2.1: `price_spike.ts` fixture + Price Overlay winning visuals

- [ ] Author `src/fixtures/price_spike.ts` exporting `priceSpike: Snapshot`: same calendar as `summerNormal` but `price.current_cents_per_kwh: 53.4`, `price.tier: 'scarcity'`, `flow.price_overlay.role_state: 'winning'`, `flow.price_overlay.details.reason_code: 'PRICE_OVERLAY_UPGRADED_TO_SCARCITY'`, `flow.winner.details.winning_layer: 'price_overlay'`, `flow.winner.details.reason_code: 'LAYER_RESOLUTION_PRICE_OVERLAY_WINS'`, `flow.action.details.action_label: 'price_spike_response'`, `flow.action.details.cool_setpoint_f: 82`. Register in `loadFixture.ts` so `?fixture=price_spike` resolves.
- [ ] Verify `typecheck` + the fixture loader includes it as `?fixture=price_spike`.
- [ ] Extend acceptance test with a third `it()` asserting Price Overlay winning + tier-scarcity pulse class + correct lane edges.
- [ ] Commit; open PR; verify Tier-scarcity pulse renders correctly when dev-loaded.

#### Task 2.2: `fivecp_risk.ts` fixture + 5CP scope detail rendering

- [ ] Author `src/fixtures/fivecp_risk.ts` exporting `fivecpRisk: Snapshot`: `flow.fivecp.details.fivecp_active: true`, `fivecp_scopes_fired: ['COMED']`, `fivecp_cool_f: 85`, `flow.winner.details.winning_layer: 'fivecp'`, `flow.winner.details.reason_code: 'LAYER_RESOLUTION_5CP_WINS'`. Register in `loadFixture.ts`.
- [ ] Verify FiveCPNode body renders scopes inline.
- [ ] Extend acceptance test.

#### Task 2.3: `supervisor_clamp.ts` fixture + Supervisor clamped role styling

- [ ] Author `src/fixtures/supervisor_clamp.ts` exporting `supervisorClamp: Snapshot` where `flow.supervisor.details.decision: 'clamped'`, `reason_code: 'SUPERVISOR_CLAMPED_COOL_CEILING'`, `proposed_cool_f: 92`, `final_cool_f: 86`. Register in `loadFixture.ts`.
- [ ] Verify the Supervisor node body renders the `92 → 86` clamp transition + the `clamped` role_state styling (rose border).
- [ ] Extend acceptance test asserting `data-role-state="clamped"`.

#### Task 2.4: `controller_down.ts` fixture + B-down handling

- [ ] Author `src/fixtures/controller_down.ts` exporting `controllerDown: Snapshot`: `controller.alive: false`, `arm_mode.mode_actual: 'B-down'`, several flow nodes `role_state: 'missing'`, recent `last_heartbeat_ts`. Register in `loadFixture.ts`.
- [ ] Verify the controller-alive dot renders rose; arm chip renders rose; missing nodes render gray.
- [ ] Extend acceptance test.

#### Task 2.5: `feed_outage.ts` fixture + B-fallback amber chips

- [ ] Author `src/fixtures/feed_outage.ts` exporting `feedOutage: Snapshot`: `feed_health[]` entry for `'PJM RT LMP'` with `status: 'stale'`, `arm_mode.mode_actual: 'B-fallback'`, `flow.price_overlay.role_state: 'stale'` with `freshness: 'stale'`. Register in `loadFixture.ts`.
- [ ] Verify arm chip amber, feed chip amber, Price Overlay node amber-bordered.
- [ ] Extend acceptance test.

#### Task 2.6: `mild_day.ts` fixture + Day Type evaluation-tape demo

- [ ] Author `src/fixtures/mild_day.ts` exporting `mildDay: Snapshot` exercising the `DAY_TYPE_MILD_HIGH_LT_75` path with full `evaluation_tape` entries. Register in `loadFixture.ts`.
- [ ] Optionally upgrade DayTypeNode body to render a tape pop-out / hover detail. (Decision: deferred to a separate UI-polish task within Phase 2 if rendering inline becomes too dense.)
- [ ] Extend acceptance test.

#### Task 2.7: `arm_switch.ts` fixture + switch-event visual

- [ ] Author `src/fixtures/arm_switch.ts` exporting `armSwitch: Snapshot` for the Mon 00:00 CT arm boundary moment: `arm_mode.arm` flipped, fresh switch_event reference in `arm_mode.source`, arm chip styled differently for the first 60-second post-boundary window. Register in `loadFixture.ts`.
- [ ] Add a header-level subtle "arm switch detected" badge when source ts is within last 5 min.
- [ ] Extend acceptance test.

#### Task 2.8: Animation polish pass

- [ ] Review the marching-dash cycle timing against actual visual feel — adjust from 4.5s if needed (target: noticeable but not arcade).
- [ ] Tune Framer Motion node-pulse keyframe (scale 1 → 1.04 → 1, 300ms) — verify it triggers on `role_state` change and not on every render.
- [ ] Tune supervisor red-flash (`emergency` role pulse) — verify it fires once on transition into emergency, then steady red border.
- [ ] Verify `prefers-reduced-motion: reduce` disables all of the above in a separate test pass.
- [ ] Document any timing constants in `src/styles/index.css` comments.

#### Task 2.9: Commit + PR

- [ ] Run full gate (typecheck + build + test) against all 9 fixtures via parametrized acceptance tests.
- [ ] Open PR. Stop at `gh pr create`.

### Phase 3 — FastAPI proxy + live data

Snapshot contract is stable; this phase swaps the data source from checked-in JSON to a live HTTP call against a tiny local FastAPI service that shapes Influx + Loki queries.

**File structure (additions):**

```
tools/cockpit/
├── backend/
│   ├── app.py                    # FastAPI single-endpoint /api/snapshot
│   ├── influx.py                 # query builders for hvac.* + comed.prices
│   ├── loki.py                   # LogQL helpers for decision_trace.*
│   ├── snapshot.py               # assembles Snapshot dict from Influx+Loki results
│   ├── freshness.py              # mirrors frontend freshness.ts constants
│   ├── requirements.txt
│   └── tests/
│       └── test_snapshot.py      # asserts shape conformance to frontend contract
└── frontend/
    ├── vite.config.ts            # MODIFY: add proxy for /api/* → :8000
    └── src/
        ├── lib/
        │   ├── api.ts            # fetchSnapshot() — replaces loadFixtureFromUrl
        │   └── usePolling.ts     # custom hook: poll every N seconds
        └── App.tsx               # MODIFY: useState + useEffect + usePolling
```

#### Task 3.1: FastAPI scaffold + `/api/snapshot` stub returning summer_normal

- [ ] Create `tools/cockpit/backend/app.py` with FastAPI app and one endpoint `/api/snapshot` that returns a hand-rolled Python dict matching the `Snapshot` interface — content equivalent to the `summerNormal` TS fixture (Python literal mirroring its values). FastAPI serializes dict → JSON automatically.
- [ ] Create `tools/cockpit/backend/requirements.txt`: `fastapi`, `uvicorn[standard]`, `influxdb-client`, `httpx`, `python-dotenv`.
- [ ] Configure CORS for `http://localhost:5173`.
- [ ] Add `make backend` / `npm run backend` convenience script in `tools/cockpit/`.
- [ ] Smoke: `curl localhost:8000/api/snapshot | jq .latest_tick_id`.

#### Task 3.2: Frontend fetch path + polling hook

- [ ] Create `src/lib/api.ts`: `async function fetchSnapshot(): Promise<Snapshot>`.
- [ ] Create `src/lib/usePolling.ts`: hook accepting interval ms + fetch fn, returning `{ data, loading, error, lastFetched }`.
- [ ] Modify `vite.config.ts` to proxy `/api/*` → `http://localhost:8000`.
- [ ] Modify `App.tsx` to use `usePolling(fetchSnapshot, 5000)` instead of `loadFixtureFromUrl()`. Keep the URL-param fixture loader as a fallback when `?fixture=` is present (operator dev affordance + offline demo).
- [ ] Add visible "last fetched" indicator in header.

#### Task 3.3: Influx query builders for ambient state

- [ ] Implement `tools/cockpit/backend/influx.py` with functions:
  - `latest_thermostat(client) -> dict` — latest `hvac.thermostat` row
  - `latest_arm_mode(client) -> dict` — latest `hvac.arm_mode` row
  - `latest_price(client) -> dict` — latest `comed.prices` row (most-recent 5-min interval) + tier classification (mirror of scheduler logic)
  - `latest_heartbeat(client) -> dict | None` — last `hvac.heartbeat` row in last 30 min
  - `feed_health_table(client) -> list[dict]` — last-write time per feed, classified
- [ ] Unit tests with mocked `query_api` (per memory `feedback_flux_schema_validation`: don't rely on mocks alone for `|> group()` validation; spot-check live Flux on Pi-lab).

#### Task 3.4: Loki query builders for `decision_trace.*` per-tick assembly

- [ ] Implement `tools/cockpit/backend/loki.py`: `fetch_latest_tick_traces(client) -> dict` returning all five `decision_trace.*` event types for the most recent shared `tick_id`. Strategy: query for the latest `decision_trace.layer_resolution` row (it fires every tick), pull its `tick_id`, then query each other event with that `tick_id` filter.
- [ ] Day-type + precool traces don't share a tick with the 5-min ones — fetch the most recent regardless of `tick_id`.
- [ ] Loki client wraps `LokiClient.query_range` (existing pattern in the codebase per `report-tool` precedent).

#### Task 3.5: Snapshot assembler

- [ ] Implement `tools/cockpit/backend/snapshot.py`: `build_snapshot(influx_results, loki_results) -> dict` that produces the exact JSON shape the frontend types expect.
- [ ] Implement `tools/cockpit/backend/freshness.py`: thresholds mirroring frontend's `freshness.ts`, plus `classify_freshness(now_ms, source_ts_ms, source) -> Freshness`.
- [ ] Unit test in `tools/cockpit/backend/tests/test_snapshot.py`: feed canned Influx + Loki results, assert assembled dict matches a canonical Python copy of the `summerNormal` fixture content (kept at `tools/cockpit/backend/tests/fixtures/summer_normal.py`). The Python copy and the TS module mirror each other by hand; a follow-up could codegen one from the other, but Phase 3 keeps them as paired source files. Drift between the two surfaces as a test failure.

#### Task 3.6: Wire `/api/snapshot` to live data

- [ ] Replace stub in `app.py` with: build Influx client from env (`INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG`, `INFLUX_BUCKET`), build Loki client (`LOKI_URL`), call `build_snapshot(...)`, return.
- [ ] Add `/api/health` endpoint for the frontend polling hook to ping if `/api/snapshot` is slow.
- [ ] Smoke test against Pi-lab: `curl --connect-to localhost:8000:pi-lab.lan:8000 /api/snapshot` returns valid Snapshot.

#### Task 3.7: Phase 3 acceptance test — live fetch path

- [ ] Add `tools/cockpit/frontend/src/__tests__/cockpit.live.test.tsx`: mocks `fetch('/api/snapshot')` to return the `summerNormal` fixture content (imported from `src/fixtures/summer_normal.ts`), asserts the polling hook drives re-renders, asserts a stale-fetch state renders a degraded header indicator.
- [ ] Phase 3 acceptance gate: `npm run typecheck && npm run build && npm run test && pytest tools/cockpit/backend/tests`.

#### Task 3.8: Commit + PR

- [ ] Run all gates. Open PR. Stop at `gh pr create`.

## Risks

| Risk | Mitigation |
|---|---|
| React Flow's manual coords break on small windows | Phase 1 explicitly desktop-first; narrow-screen stack is Phase 4+. Test on 1920×1080 + 2560×1440. |
| Acceptance test couples too tightly to DOM structure | Tests reference `data-testid` attributes only, not class names or DOM hierarchy. Components free to restructure under those handles. |
| Tailwind `animate-march` keyframe doesn't honor `prefers-reduced-motion` automatically | `ActiveEdge` reads `matchMedia` directly and gates the animation class. Acceptance test covers this case via mocked matchMedia. |
| Fixture drift from real emitted shapes | Phase 3 round-trip test (`test_snapshot.py`) asserts the backend's assembled dict matches the fixture shape exactly. Drift surfaces in CI, not in production. |
| Phase 3 Influx/Loki query semantics differ from the scheduler's emitted shape (e.g., field types, tag vs field) | Cross-reference `decision_trace.*` log fields (Loki) and `hvac.*` Influx points by READING `app.py` emission code, not by inferring. Per memory `feedback_flux_schema_validation`: don't rely on mock query_api tests alone — spot-check Flux on Pi. |
| Frontend snapshot type drifts ahead of backend implementation | Phase 3 backend MUST match the Snapshot interface checked into Phase 1. Any new field requires `types.ts` update + fixture update + new acceptance test case in same PR. |
| `?fixture=` URL param in production confuses operator | Phase 3 keeps fixture loader as a fallback only when the param is present. Phase 4+ may remove or guard behind a build flag. |
| Cockpit starts feeling like a "second controller" if Phase 4 adds drill-down logic | Phase 4 brainstorming explicitly re-asserts read-only. Any tempt to add a "force shadow" or "trigger fire-now" button bounces back to a separate plan, never lands in cockpit. |
| Animation cost on long-running screen | Marching dash is GPU-accelerated CSS; node pulse is one-shot Framer transition; no continuous JS-driven animation. Should be sub-1% CPU when idle. Re-verify after Phase 2 polish. |

## Non-goals (locked)

- No control writes back to the scheduler. Read-only by design.
- No drag-to-set-setpoint affordance.
- No mobile / narrow-screen responsive layout in Phase 1 (deferred to Phase 4+).
- No light theme; no theme toggle.
- No tick history scrubber or per-tick drill-down (deferred to Phase 4+ B-mode work).
- No fixture browser dropdown (operator uses URL param).
- No Playwright e2e in Phases 1-3; Vitest + RTL suffices for the outside-in test scope.
- No second decision engine. Cockpit consumes decision-trace data, never re-evaluates.
- No new Influx measurements. Phase 3 reads existing `hvac.*` + `decision_trace.*` only.
- No new Loki labels. `tick_id` stays a JSON field per `decision-trace-plan.md` cardinality lock.
- No fancy chart library (Recharts/Victory). All visuals are React Flow nodes/edges + custom SVG.
- No state management library (Redux/Zustand). React's built-in state + hooks suffice for Phase 1-3.

## See also

- [docs/plans/sced-rebaseline-spec-2026-05-13.md](sced-rebaseline-spec-2026-05-13.md) — OSF-binding experiment spec; cockpit consumes its data substrate.
- [docs/plans/decision-trace-plan.md](decision-trace-plan.md) — predecessor plan that produced the `decision_trace.*` events the cockpit visualizes.
- [docs/SERVICES.md](../SERVICES.md) — per-service reference for the upstream measurements + log streams.
- [AGENTS.md](../../AGENTS.md) — branching policy, plan discipline, outside-in TDD rule.
