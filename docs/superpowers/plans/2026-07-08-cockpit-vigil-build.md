# Cockpit "The Vigil" Build — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans or
> subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the dead rev-3 "Reticule" cockpit with the rev-4.1 "Vigil" wall
board — three live `/api/vigil/*` endpoints backing the delivered single-file
frontend — deploy it to the Pi cockpit container, then flip the Surface kiosk to
swipe between weather + Vigil.

**Architecture:** Rebuild in place under `deploy/energy-stack/cockpit/`. Keep the
FastAPI app + Influx plumbing; rewrite the query layer into three focused
assemblers reading rev-4 InfluxDB measurements only; serve the delivered
`vigil.html` same-origin (no build step); source tier thresholds from a read-only
mount of the controller's own config. The Surface change is a one-file iframe-list
edit in a **separate repo** (`FireDevPro/grafana-dashboards`, checked out at
`/opt/grafana-dashboards` on the Surface).

**Tech Stack:** Python 3.12 / FastAPI / influxdb_client (Flux) / vanilla-JS static
HTML. No React/Vite/Tailwind (deleted). PyYAML for config.

## Global Constraints

- Scope: `deploy/energy-stack/cockpit/` + its compose block ONLY (cockpit scope
  boundary). The Surface edit is a second repo, its own PR.
- Read-only board: no writes to controller/device. Live state from **InfluxDB
  only** (no Loki dependency for `/now`).
- Temps: `hvac.thermostat` fields are already **°F** (poller reads TCC in °F) — no
  conversion. `hvac.actions` fields are **°C** — convert C→F at the read boundary.
  Client never converts.
- Timestamps: RFC3339 UTC + parallel `_ct` (America/Chicago) where displayed.
- Tests: cockpit = no heavy test investment (observability, not critical path).
  Unit-test the pure derivations; shape-test the contract with a mocked QueryApi;
  the real oracle is live smoke on the Pi + Chrome on the wall.
- Frontend contract is **binding to what `vigil.html` actually reads** (below),
  not just the DATA_MAP prose.
- Branching: `--base main`, no stacking, stop at `gh pr create`. energy-proxy PR
  first (deploys the board), grafana-dashboards PR second (flips the wall).

---

## Ground truth — rev-4 read schema (verified against writers + live Pi)

| Vigil value | Measurement | Field / tag | Notes |
|---|---|---|---|
| `price.cents` | `comed.prices` | field `price_cents_per_kwh`, tag `period_type=5min`, latest | |
| `price.hourly_avg_cents` | `comed.prices` | field `price_cents_per_kwh`, tag `period_type=hourly_avg`, latest | |
| `price.fresh` | derived | age ≤ 720s (controller fresh-strict) | |
| thresholds | **config mount** | `price_tiers_cents.{elevated_at,scarcity_at,hysteresis_cents}` | not from Influx |
| `thermostat.indoor_temp_f` | `hvac.thermostat` | `indoor_temp_f_hires` (fallback `indoor_temp_f`) | °F |
| `thermostat.cool_setpoint_f` | `hvac.thermostat` | `cool_setpoint_f` | °F |
| `thermostat.hold_mode` | `hvac.thermostat` | `hold_mode` (str: Off/Hold Until/Permanent) | |
| `thermostat.compressor_on` | `hvac.thermostat` | `hvac_state == "Cool"` | |
| `thermostat.fan_mode` | `hvac.thermostat` | `fan_mode` | |
| `humidity_guard.indoor_rh_pct` | `hvac.thermostat` | `humidity_pct` | |
| `humidity_guard.rh_max_pct` | **config mount** | `humidity_guard.rh_max_pct` | |
| `humidity_guard.gated` | `hvac.actions` | `humidity_gated` (int 0/1), latest | |
| `outdoor.{temp_f,rh_pct,dewpoint_f}` | `ecowitt.weather` | `ch1_temp_f/ch1_rh_pct/ch1_dewpoint_f` | canonical ch1 |
| `hold.commanded_cool_f` | `hvac.actions` | `commanded_cool` (**°C**→°F) | |
| `hold.schedule_cool_f` | `hvac.actions` | `schedule_cool` (**°C**→°F) | |
| `hold.minutes_held` | `hvac.actions` | now − row `_time` (pushed_at), minutes | |
| `hold.minutes_to_expiry` | `hvac.actions` | `hold_expires_at` − now, minutes, clamp ≥0 | TTL horizon (≤) |
| `hold.coasting` | derived | `not compressor_on` while holding | |
| tier / `this_spike.tiers_walked` | `hvac.price_overlay` | tags `prev_tier`/`new_tier`, field `current_price_cents`, `triggered_at_utc` | **transitions only** |
| `liveness.last_tick_age_sec` | `hvac.arm_mode` | now − latest `_time` | canonical liveness |
| `liveness.watchdog_down` | `hvac.heartbeat` | any `controller_alive=false` in last 10m | inverted DOWN beacon |
| `controller.mode` | `hvac.arm_mode` | tag `scheduler_mode`, latest | |

C→F: `f = c * 9/5 + 32`.

## Binding frontend contract (what `vigil.html` reads)

`/api/vigil/now`:
- `as_of.{utc,ct}`; `why` (str, rendered verbatim)
- `price.{cents,hourly_avg_cents,fresh}`
- `tier.{current,elevated_at,scarcity_at}` (`current` ∈ normal|elevated|scarcity)
- `posture` (resting|engaged)
- `liveness.{alive,watchdog_down,last_tick_age_sec}`
- `thermostat.{indoor_temp_f,cool_setpoint_f,hold_mode,compressor_on,fan_mode}`
- `humidity_guard.{indoor_rh_pct,rh_max_pct,gated}`
- `outdoor.{temp_f,rh_pct,dewpoint_f}`
- `hold` = null OR `{commanded_cool_f,schedule_cool_f,minutes_held,minutes_to_expiry,coasting}`
- `this_spike` = null OR `{tiers_walked:[{tier,at}], peak_cents, est_avoided_cost_usd, duration_min, ended:bool}`
  - **engaged** requires `hold` present + tier≠normal. **release** is driven by
    `this_spike.ended === true` (spike just ended, price back to normal, no hold).

`/api/vigil/timeline?hours=24`:
- `thresholds.{elevated_at,scarcity_at}`
- `price_series:[{t,cents}]` (~48 buckets)
- `holds:[{from,to,tier}]` (frontend draws `holds[0]` as the band)

`/api/vigil/events?limit=10` — `events:[…]`, each:
- `tiers_walked:[str,…]` (**strings**, unlike `this_spike`)
- `started_at_ct` (or `started_at`), `peak_cents`, `duration_min`, `resolution`
  (released|lapsed|ongoing), `est_avoided_cost_usd`

## Derivations

- **tier.current:** if an own-hold is active (`hold_expires_at` in the future and
  latest action `applied`), use the latest `hvac.price_overlay.new_tier`; else
  derive from price vs thresholds (`cents≥scarcity_at`→scarcity, `≥elevated_at`→
  elevated, else normal).
- **posture:** `engaged` iff tier≠normal OR own-hold active; else `resting`.
- **hold:** null unless own-hold active; else assembled from latest `hvac.actions`.
- **this_spike / episodes:** read `hvac.price_overlay` transitions over the
  window. An episode = the run from a `new_tier` leaving `normal` to the next
  `new_tier=normal`. `tiers_walked` = ordered `new_tier` values (objects `{tier,at}`
  for `this_spike`; strings for `/events`). `peak_cents` = max `comed.prices` 5-min
  value over the episode window. `resolution`: `released` if it ended on a
  transition to normal (active stand-down), `ongoing` if no closing transition yet.
  `ended` (this_spike) = the current episode closed within the last N minutes and
  price is back to normal with no active hold.
- **why (verbatim cases):** normal→`"Cheap power, {c}¢ — thermostat's running its
  own schedule."`; normal+lapsing→`"Spike over — releasing back to your schedule."`;
  elevated→`"Elevated power, {c}¢ — holding {commanded}°, ~{mins} min left on this
  hold."`; scarcity→`"SCARCITY, {c}¢ — holding {commanded}°, riding the thermal
  battery, ~{mins} min left on this hold."`; append `" (humidity gate: cooling to
  dry the air)"` when gated; stale feed→`"Price feed stale — standing down to your
  schedule."`; controller down→`"⚠ Controller not responding — thermostat is on its
  own program."`
- **avoided-cost (labeled est.):** `(compressor_off_minutes_at_elevated+ / 60) ×
  ASSUMED_KW × (price_cents/100)`. `ASSUMED_KW = 2.35` (single v1 constant).
- **liveness.alive:** `last_tick_age_sec < 600` AND not `watchdog_down`.

## File structure

Create (energy-proxy):
- `backend/vigil_config.py` — load thresholds/humidity/hold_ttl from mounted yaml;
  env `VIGIL_CONFIG_PATH` (default `/config/commissioning-controller.yaml`), dev
  fallback to the repo path. Loaded once at startup.
- `backend/vigil_queries.py` — rev-4 Flux builders (reuse `influx.py`
  `_first_row`/`_to_iso`/`QueryApi`): `latest_price_5min`, `latest_hourly_avg`,
  `latest_thermostat`, `latest_action`, `latest_arm_mode`, `heartbeat_down`,
  `outdoor_now`, `price_series_24h`, `price_overlay_transitions`.
- `backend/vigil_derive.py` — pure helpers: `c_to_f`, `tier_from_cents`,
  `build_hold`, `build_episodes`, `this_spike_from_episodes`, `compose_why`,
  `avoided_cost`, `alive_from`. (unit-tested)
- `backend/vigil_now.py` / `vigil_timeline.py` / `vigil_events.py` — assemblers.
- `backend/tests/test_vigil_derive.py`, `backend/tests/test_vigil_contract.py`.
- `frontend/index.html` — the delivered `vigil.html` board (single self-contained
  file; served as `/`).

Modify:
- `backend/app.py` — drop rev-3 routes (`/api/snapshot`, `/api/day_at_a_glance`,
  `/api/day_ahead`, `/api/today_actions`); add `/api/vigil/{now,timeline,events}`;
  load config at startup; mount `frontend/` (single file) at `/`.
- `backend/influx.py` — remove dead rev-3 queries (`query_today_forecast`,
  `query_da_lmp_forecast`, `query_day_type_decision`, `query_precool_window`,
  `FEED_DEFINITIONS`); keep the reusable helpers.
- `Dockerfile` — remove the node/Vite build stage; COPY `frontend/index.html`.
- `docker-compose.yml` (cockpit service) — add
  `- ./hvac_scheduler/commissioning-controller.yaml:/config/commissioning-controller.yaml:ro`.
- `start-cockpit.ps1` / `vite.config.ts` refs — drop the Vite half of the dev loop
  (uvicorn serves the single file at :8765).
- `README.md` (cockpit) — new endpoints, single-file frontend, updated dev loop.

Delete: `backend/snapshot.py`, `day_ahead.py`, `schedules.py`, `today_actions.py`,
`day_at_a_glance.py`, `loki.py`, their tests/fixtures; the entire React
`frontend/src/`, `frontend/package*.json`, `vite.config.ts`, `tailwind.config.js`,
`postcss.config.js`, `eslint.config.js`, `tsconfig*.json`, `vitest.config.ts`,
`frontend/index.html`(old React entry)→replaced.

---

## Task 1 — Config loader (tracer through the mount)

**Files:** Create `backend/vigil_config.py`, `backend/tests/test_vigil_config.py`.

- [ ] Write `VigilConfig` dataclass (`elevated_at, scarcity_at, hysteresis_cents,
  rh_max_pct, rh_clear_pct, hold_ttl_minutes`) + `load_config(path)` parsing the
  yaml. Fallback path resolution: env `VIGIL_CONFIG_PATH` → `/config/…` → repo
  `../../hvac_scheduler/commissioning-controller.yaml`.
- [ ] Unit test: parse the real yaml fixture → asserts elevated_at=10, scarcity_at=20,
  rh_max_pct=61, hold_ttl_minutes=30.
- [ ] Run `pytest backend/tests/test_vigil_config.py -v` → PASS. Commit.

## Task 2 — Pure derivations (the bug-prone core)

**Files:** Create `backend/vigil_derive.py`, `backend/tests/test_vigil_derive.py`.

- [ ] Implement `c_to_f`, `tier_from_cents`, `build_episodes` (group price_overlay
  transition rows into episodes), `this_spike_from_episodes`, `build_hold`,
  `compose_why` (all 7 cases), `avoided_cost`, `alive_from`.
- [ ] Unit tests with synthetic rows: C→F (25→77.0), tier boundaries (10→elevated,
  20→scarcity, 9.9→normal), episode grouping (elevated→scarcity→normal = one
  episode, tiers_walked ['elevated','scarcity'], resolution released), why-line for
  each case string-exact, avoided_cost arithmetic, alive threshold (601s→down).
- [ ] `pytest backend/tests/test_vigil_derive.py -v` → PASS. Commit.

## Task 3 — rev-4 query builders

**Files:** Create `backend/vigil_queries.py`; modify `backend/influx.py` (trim dead).

- [ ] Port/adapt the reusable builders and add `latest_hourly_avg`,
  `latest_thermostat` (rev-4 fields incl. `indoor_temp_f_hires`, `hold_mode`,
  `hvac_state`), `latest_action` (rev-4 °C fields + `hold_expires_at`,
  `humidity_gated`, `applied`), `price_series_24h`, `price_overlay_transitions`.
  Pre-filter `_field` before any `group()` (avoid multi-field schema collision).
- [ ] Delete dead rev-3 query functions from `influx.py`.
- [ ] Contract test with a mocked `QueryApi` (existing test pattern) asserting each
  builder returns the documented dict shape. `pytest -v` → PASS. Commit.

## Task 4 — `/api/vigil/now` assembler + route

**Files:** Create `backend/vigil_now.py`; modify `backend/app.py`;
`backend/tests/test_vigil_contract.py`.

- [ ] `assemble_now(query_api, bucket, config)` → dict matching the binding contract;
  wire hold/this_spike/liveness/why/posture/tier from Tasks 2-3.
- [ ] Add the FastAPI route; load config at startup; keep `/api/health`.
- [ ] Contract test: mocked Influx (resting + engaged) → assert every
  frontend-required key present and typed; engaged has `hold` + `this_spike`;
  resting has neither; no nulls in resting tiles. PASS. Commit.

## Task 5 — `/timeline` + `/events` assemblers + routes

**Files:** Create `backend/vigil_timeline.py`, `backend/vigil_events.py`; modify
`backend/app.py`.

- [ ] `assemble_timeline` (48-bucket price_series + thresholds + holds from
  push/release pairs); `assemble_events` (episodes, strings tiers_walked, newest
  first, limit). Add routes.
- [ ] Contract tests (mocked). PASS. Commit.

## Task 6 — Serve the frontend; strip React/Vite

**Files:** Add `frontend/index.html` (the delivered board); modify `app.py` static
mount + `Dockerfile`; delete React app + rev-3 backend modules/tests; update
`start-cockpit.ps1`.

- [ ] Copy the handoff `vigil.html` → `frontend/index.html` verbatim.
- [ ] `app.py`: mount `frontend/` at `/` (html=True). Remove rev-3 routes/imports.
- [ ] `Dockerfile`: drop node stage; COPY `backend/` + `frontend/index.html`.
- [ ] Delete rev-3 backend modules + React `src/`/configs. Update dev launcher.
- [ ] `pytest backend -v` green; `uvicorn` locally → `GET /` serves the board,
  `/api/vigil/now` returns 200 live JSON. Commit.

## Task 7 — Compose config mount + local live smoke

**Files:** modify `docker-compose.yml`; `README.md`.

- [ ] Add the read-only config mount to the cockpit service.
- [ ] Local: run backend against Pi Influx (`.env.local`) → `/api/vigil/now`,
  `/timeline`, `/events` return live rev-4 data; resting view fully populated;
  `?demo=engaged` renders the engaged board. Update README. Commit.

## Task 8 — energy-proxy PR

- [ ] `run_tests.sh` (cockpit backend) green; typecheck/lint/build as applicable.
- [ ] Push `feat/cockpit-vigil`; `gh pr create --base main`. **Flag to Chris:** this
  is a `deploy/**` PR — merging bounces the built services incl. the live
  controller (timed holds auto-revert; low risk). Stop for review/merge.
- [ ] After merge: verify `http://192.168.20.10:8765/` serves the live Vigil and all
  three endpoints return 200; confirm no `X-Frame-Options` blocks framing.

## Task 9 — Surface flip (grafana-dashboards repo)

**Files:** `/opt/grafana-dashboards/kiosk/index.html` (2nd repo).

- [ ] Branch `FireDevPro/grafana-dashboards`; edit the swipe app: pages = weather +
  Vigil iframe (`http://192.168.20.10:8765/`); `TOTAL_PAGES=2`; `.pages` `200vw`;
  2 dots; remove both dead Grafana iframes. `gh pr create --base main`. Stop for merge.
- [ ] After merge: `ssh surface-kiosk 'bash /opt/grafana-dashboards/kiosk/deploy-kiosk.sh'`.
- [ ] Verify on the wall: 2 pages, swipe weather ↔ Vigil, Vigil shows live data.

## Task 10 — Close-out

- [ ] Flip spec `status: draft → accepted`; archive this plan to
  `docs/superpowers/plans/archive/`. Update INDEX/SERVICES if they reference the
  rev-3 cockpit. (Doc PR or fold into the energy-proxy PR.)
