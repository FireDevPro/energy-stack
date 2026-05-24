# Narrative cockpit (v2) — file map

This Vite project serves **two cockpit UIs from one bundle**:

| Route | UI | Status |
|---|---|---|
| `/` | **v1** (classic cockpit, "Reticule cockpit v3") | live; kept as fallback while v2 bakes in |
| `/narrative` | **v2** (narrative cockpit, "Sentinel narrative v4") | the new UI; will fully replace v1 |

v2 is intentionally scoped under `src/narrative/` so it can be reviewed
in isolation. When v1 is retired, everything under v1's footprint
gets deleted and v2 either stays at `src/narrative/` or gets promoted
up to `src/`.

---

## Quick start

From `tools/cockpit/frontend/`:

```bash
npm install
npm run dev
# open http://localhost:5173/narrative?fixture=summer_normal
```

Add `?fixture=<name>` to render against a hand-rolled snapshot (no
backend needed). Drop the query string for live data (requires the
FastAPI backend on :8000 — see `tools/cockpit/start-cockpit.ps1`).

Fixture names live in `src/lib/loadFixture.ts`: `summer_normal`,
`price_spike`, `fivecp_risk`, `supervisor_clamp`, `controller_down`,
`feed_outage`, `mild_day`, `arm_switch`, `shadow_current`.

---

## v2 file inventory

### Entry + route shim (shared with v1)

- **`src/main.tsx`** — React bootstrap (StrictMode + framer-motion `MotionConfig`)
- **`src/App.tsx`** — tiny router: if `window.location.pathname` starts with `/narrative`, render `NarrativeCockpit`; else render `ClassicCockpit`
- **`src/index.css`** — global theme (CSS variables: `--ink`, `--ice`, `--ember`, `--live`, `--warn`, `--danger`, font families, etc.). v2 components consume these.

### v2-only code (under `src/narrative/`)

```
src/narrative/
├── NarrativeCockpit.tsx              ← v2 root component; mounts the panels below
├── narrative.css                     ← v2-specific layout + panel styles (no overlap with index.css)
├── types.ts                          ← v2 API payload shapes (DayAtAGlance, TodayActions, DayAhead, etc.)
├── lib/
│   └── api.ts                        ← fetchers for the v2 endpoints
├── fixtures/
│   ├── day_at_a_glance.ts            ← canned chart payload for ?fixture=summer_normal
│   ├── today_actions.ts              ← canned action-log payload
│   └── day_ahead.ts                  ← canned day-ahead payload
└── components/
    ├── HeroPanel.tsx                 ← left column: ring + cool/heat tiles + RTP tile + context strip
    ├── DayAtAGlance.tsx              ← center top: live 24h chart (price bars + indoor line + setpoint lines + NOW marker)
    ├── ActionLog.tsx                 ← center bottom: vertical list of today's ScheduleActions w/ past/current/future status
    ├── WhyThisDecision.tsx           ← right column: Schedule / Price overlay / Winner / Supervisor arbitration cards + ACTION SENT footer
    ├── DayAheadPanel.tsx             ← right column: tomorrow's Day-type + Pre-cool window cards
    └── DecisionPipeline.tsx          ← bottom ribbon: 7-cell live pipeline (Weather → Day Type → Schedule → RTP → Winner → Supervisor → Action)
```

### Shared infrastructure (v2 reuses from v1's tree)

These files are NOT v2-specific but are imported by v2 components. They
should stay put even after v1 is retired:

- **`src/types.ts`** — locked Snapshot contract (matches FastAPI backend response shape)
- **`src/lib/api.ts`** — `fetchSnapshot()` (v2's `WhyThisDecision` consumes the same `/api/snapshot` v1 does)
- **`src/lib/usePolling.ts`** — polling hook (v2 panels poll their own endpoints with it)
- **`src/lib/loadFixture.ts`** — fixture loader (v2 reuses the same snapshot fixtures for `WhyThisDecision`)
- **`src/lib/tone.ts`** — chip-tone helpers + `formatClock` / `formatDate`
- **`src/fixtures/*.ts`** — all snapshot fixtures (shared)
- **`src/components/Header.tsx`** — top chips + clock (v2 passes `variant="narrative"` for the brand suffix)
- **`src/components/ThermostatRing.tsx`** — SVG ring (reused by `HeroPanel`)
- **`src/components/PriceChip.tsx`** — RTP tile (reused by `HeroPanel`)

### v1-only (will get deleted when v1 retires)

- `src/ClassicCockpit.tsx`
- `src/components/ThermostatCard.tsx`, `DecisionBoard.tsx`, `ActionStrip.tsx`, `FeedHealthStrip.tsx`, `nodes/*`
- `src/__tests__/cockpit.acceptance.test.tsx`, `cockpit.live.test.tsx`

---

## v2 backend endpoints

v2 consumes the same FastAPI backend as v1 plus three new endpoints:

| Endpoint | Backend module | Powered by |
|---|---|---|
| `GET /api/snapshot` | `tools/cockpit/backend/snapshot.py` | v1 + v2 (hero, why-this-decision, decision-pipeline ribbon) |
| `GET /api/day_at_a_glance` | `tools/cockpit/backend/day_at_a_glance.py` | v2 chart |
| `GET /api/today_actions` | `tools/cockpit/backend/today_actions.py` | v2 action log |
| `GET /api/day_ahead` | `tools/cockpit/backend/day_ahead.py` | v2 day-ahead panel |

All four endpoints support `?fixture` via `COCKPIT_BACKEND_MODE=canned`
(default). Set `COCKPIT_BACKEND_MODE=live` to query InfluxDB + Loki for
real data.

---

## Where data comes from (v2)

All Influx measurements + Loki streams used by v2 already exist —
**no cockpit code modifies the controller, scheduler, pollers, or
InfluxDB schema.**

| v2 surface | Source |
|---|---|
| Hero ring + setpoints | `hvac.thermostat` (latest row) |
| RTP tile | `comed.prices` `period_type=5min` (latest row) |
| Chart: indoor + setpoint history | `hvac.thermostat` (24h) |
| Chart: price forecast bars | `pjm.lmp_da_hourly` zone=COMED, $/MWh ÷ 10 |
| Chart: realized price overlay | `comed.prices` `period_type=hourly_avg` (today) |
| Action log | scheduler's per-day-type schedule constants (mirrored in `tools/cockpit/backend/schedules.py`) + wall-clock |
| Why this decision cards | latest Loki `decision_trace.*` lines via `tools/cockpit/backend/loki.py` |
| Day ahead card | `hvac.decisions` (latest row for tomorrow's date) |
| Pre-cool card | `hvac.precool_window` (latest row for tomorrow's date) |
| Decision pipeline ribbon | same Loki traces + snapshot data |

---

## Conventions

- All v2 components use `data-testid="narrative-*"` so they're greppable
  and distinguishable from v1 testids (`thermostat-*`, `node-*`, etc.).
- v2-specific styles live in `src/narrative/narrative.css`. CSS class
  names use the `narrative-*` prefix; global theme variables (`--ink`,
  `--ember`, `--ice`, etc.) come from `src/index.css`.
- v2 doesn't have vitest coverage — by design, per project guidance.
  Manual smoke in real Chrome is the test.

---

## Retire-v1 checklist (for when v2 is proven)

1. Delete `src/ClassicCockpit.tsx`.
2. Delete `src/components/ThermostatCard.tsx`, `DecisionBoard.tsx`, `ActionStrip.tsx`, `FeedHealthStrip.tsx`, `nodes/`.
3. Delete `src/__tests__/cockpit.acceptance.test.tsx` + `cockpit.live.test.tsx`.
4. Simplify `src/App.tsx` to render `NarrativeCockpit` directly (no route shim).
5. Optionally promote `src/narrative/*` up to `src/` so the directory isn't nested for no reason.
6. Drop the `variant` prop on `Header.tsx` (only narrative variant remains).
7. Delete this file.

After: only one cockpit UI exists. Backend untouched.
