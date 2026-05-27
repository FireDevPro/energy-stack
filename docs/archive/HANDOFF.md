---
date: 2026-05-12
owner: chris
status: superseded
role-label: chris
superseded_by: docs/plans/pre-osf-doc-audit-execution-2026-05-18.md
---

> [!WARNING]
> **SUPERSEDED 2026-05-18.** This handoff was written before the 2026-05-13 SCED rebaseline and the 2026-05-18 pre-OSF doc audit. Its description of the in-flight branch (`feature/stage8-decomp`), open PRs, locked priority queue, and Stage 8/9 framing all predate the rebaseline — Stage 8 loader, weekly fact-table architecture, formal SCED randomization-test machinery, and bootstrap CI framing are explicitly retired per [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](docs/plans/sced-rebaseline-spec-2026-05-13.md) §0. Current execution plan and PR queue: [`docs/plans/pre-osf-doc-audit-execution-2026-05-18.md`](docs/plans/pre-osf-doc-audit-execution-2026-05-18.md). Retained here as historical context only — do NOT act on this document.

# Session handoff (2026-05-12, SUPERSEDED)

Picked-up by: any fresh Claude session. **Read this first.** Skip the rest of the conversation history unless you need specific verbiage.

## Where we are

Pre-OSF-filing (target 2026-05-30, **18 days out**). The analysis pipeline (`tools/analysis/pipeline.py`) has been wired against real-shape parquet bundles through Stage 7, with Stages 8 and 9 still stub-returns-None. A four-PR replay-validation chain (#94-#97) surfaced and fixed schema-drift bugs against pi-lab Influx; the validation artifacts are in `docs/replay-validation/2026-05-12/`.

The next task in the locked OSF-priority queue is **Stage 8 loader (decomposition)**. The plan has been drafted, brainstormed through two rounds of pushback, and committed to branch `feature/stage8-decomp`. Implementation starts at **Phase 0** (schema rename + spec wording + Stage 2 day-exclusion exposure).

## Current branch + uncommitted state

- **Branch:** `feature/stage8-decomp` (pushed to origin)
- **Working tree:** clean
- **Stash list:** empty
- **Open PRs:** the Stage 8 fresh-branch PR is in flight. PRs #90-#98 already merged.

## Locked priority queue (in order)

1. Stage 2/3 `energy_wh` -> `power_w` aggregation fix (#94 merged)
2. Stage 1 `_value` split for mixed string/numeric (#95 merged)
3. comed.prices `price_cents_per_kwh` + `period_type=5min` (#96 merged)
4. comed.bill_lineitems flux file + coverage audit test (#97 merged)
5. 7-day + 90-day real-shape replay artifacts (#98 merged)
6. **Stage 8 loader (in flight; plan committed, Phase 0 pending)**
7. Stage 9 sensitivity loader (depends on Stage 8)
8. FERC ER22-1520-001 exhibit cross-check (small follow-on in `o2_capacity_reconstruction/`)
9. `pjm_5cp.py` `fn:max` verified-preferred fix (live-scheduler PR with harness test; do not casually defer per user)

## The Stage 8 plan in flight

Locked at `docs/plans/stage8-loader-plan.md`. Six phases:

| Phase | Scope |
|---|---|
| **0** | Schema column renames (`arm_a_median_cost` -> `arm_a_median_value` + add `unit` column); Stage-8-specific outcome names (`o1_daily_hvac_dollars` / `o3_daily_peak_hvac_kw` / `o4_daily_mains_dollars`); new reason codes (`INSUFFICIENT_ARM_DAYS_FOR_CATEGORY`, `NO_QUALIFYING_DAYS_FROM_STAGE3`, `NO_NWS_FORECAST_FOR_CLASSIFICATION`); Stage 2 day-exclusion exposure (new `stage2/qualifying_days.csv` OR derived from existing outputs; audit first); doc updates (drop "matched-pair" wording for Stage 8 specifically). |
| **1** | Tracer: one no-spike day -> one decomposition row. New helpers `_load_qualifying_days_from_stage2_stage3`, `_load_daily_hourly_records`. |
| **2** | Spike classification: comed.prices hourly mean + nws.forecast 21:00-prior with `high_f` -> `max_forecast_temp_f` mapping. **First step**: quick Influx query for `nws.forecast.for_period` tag values (`today` / `tomorrow` / ?). |
| **3** | Per-day dollar arithmetic + peak kW. |
| **4** | Layer attribution from `hvac.price_overlay` state-machine reconstruction (24h lookback). 5CP from `hvac.5cp_state` in-hour. **DO NOT** use `hvac.actions` tags as continuous state; they're written on schedule decisions, not every tick. |
| **5** | Orchestrator wiring + reason codes + `stage8/provenance.json` + replay re-run + doc updates. |

### Critical locked decisions

- **Daily DOLLARS, not USD per CDD.** Zero-CDD grid-event days stay in.
- **Layer enum has 5 values: `price_spike_reactivity`, `5cp_detection`, `both`, `neither`, `unknown`.** `unknown` is for "no price-overlay transition in lookback"; NEVER defaults to `normal` or `neither`.
- **Stage 8 outcomes are descriptive only**, NOT fed into Stage 5 (matched-pair effects) or Stage 7 (SCED).
- **Day-level exclusions from Stage 2 MUST be respected.** A week with 5-6 qualifying days qualifies overall, but its 1-2 excluded days (Tier 4 / weather gap / scheduler outage / vacation) must NOT appear in Stage 8's decomposition.
- **Quiet-zero arm guard:** if one arm has zero days in a category, blank delta + `INSUFFICIENT_ARM_DAYS_FOR_CATEGORY` reason. Do NOT compute delta against 0.0.

## Recurring pattern to watch for (LOAD-BEARING)

Every replay-validation finding so far has been schema drift between the production producer and the loader's assumption. Pattern:

- `_field` name mismatch (`energy_wh` vs `power_w`, `price_cents` vs `price_cents_per_kwh`)
- Tag-vs-untag confusion (`comed.prices.period_type`)
- Missing flux file entirely (`comed.bill_lineitems.flux`)
- Sparse-vs-continuous measurement misuse (`hvac.actions` != continuous state)

**Before assuming any measurement's schema, verify the producer.** Grep `deploy/energy-stack/<service>/*.py` for the `.field()` and `.tag()` calls. Verify against real Influx if the pi-lab tunnel is up. This habit has caught all four pre-merge bugs that would have invalidated criterion 14.

## Production schemas verified so far (use as reference)

| Measurement | Tags | Fields |
|---|---|---|
| `refoss.channel` | `channel` in {em:1..em:18} | `power_w`, `voltage_v`, `current_a`, `power_factor`, cumulative session kWh counters (`day_energy_kwh` etc.); **no per-interval `energy_wh`** |
| `comed.prices` | `period_type` in {`5min`, `hourly_avg`} | `price_cents_per_kwh` |
| `comed.bill` | `account_no`, `rate_plan`, `bill_type` | `total_due`, `kwh`, `peak_kw`, `supply_total`, `delivery_total`, `taxes_total`, `misc_total`, `effective_rate_per_kwh`, `service_days`, `issued_date`, `service_from`, `service_to` (last 3 are string fields, land in `_value_text`) |
| `comed.bill_lineitems` | `account_no`, `category` in {`SUPPLY`, `DELIVERY`, `TAXES_FEES_CREDITS`}, `line_item` | `amount`, `quantity`, `unit` (string), `rate` |
| `ecowitt.weather` | none (single station) | `outdoor_temp_f`, `outdoor_dewpoint_f`, `outdoor_rh_pct`, `pressure_inhg`, `solar_wm2`, `wind_mph` |
| `nws.forecast` | `for_period` (values TBD; **Phase 2 first action**) | `period_date` (string), `is_heat_advisory` (int), `alert_summary` (string), `high_f`, `low_f`, `hours_covered` (int), `max_dewpoint_f`, `max_wind_mph`, `max_precip_prob_pct`, `apparent_max_f`, `apparent_min_f`, `apparent_avg_f`, `rh_max_pct`, `rh_avg_pct`, `sky_cover_avg_pct`, `wind_gust_max_mph` |
| `hvac.5cp_state` | `scope` in {`rto`, `comed_zone`}, `zone` in {`RTO`, `CE`}, `is_active` in {`true`, `false`} | `current_load_mw`, `season_5th_highest_mw`, `load_ratio`, `load_derivative_mw_per_hour`, `forecast_peak_today_mw` |
| `hvac.actions` | `day_type`, `action_label`, `dry_run`, `supervisor_decision`, `price_overlay_tier` in {`normal`, `elevated`, `scarcity`}, `fivecp_active` in {`true`, `false`} (the layer-resolution tags appear on rows that included a `LayerResolution`; most rows when scheduler is running) | numeric setpoint fields, plus string fields `fan_mode`, `setpoint_reason`, `supervisor_reason`, `hvac_mode_before`, `error` (land in `_value_text`) |
| `hvac.price_overlay` | `prev_tier`, `new_tier` (both `normal`/`elevated`/`scarcity`) | `current_price_cents`, `schedule_cool_f`, `effective_cool_f`, `triggered_at_utc` (string). **Written ONLY on tier change.** |
| `hvac.thermostat` | `thermostat_id` | `indoor_temp_f`, `humidity_pct`, `cool_setpoint_f`, `heat_setpoint_f`, plus string fields `hvac_mode`, `hvac_state`, `fan_mode`, `hold_mode` (land in `_value_text`) |
| `pjm.coincident_peak` | `summer_year` (str), `peak_rank` (str, "1".."5") | `peak_load_mw`, `comed_zone_load_mw`. Annual scrape; present only after PJM publishes the year's PDF (mid-October). |
| `pjm.metered_load` | `zone` in {`CE`, `RTO`}, `is_verified` in {`true`, `false`} | `mw`. Hourly publish with verified corrections appearing as separate series at the same `_time`. |

## Standing rules in force (from AGENTS.md)

- **Caveman tone:** drop articles, filler, pleasantries. Fragments OK. **No em dashes.**
- **Outside-in TDD:** acceptance test first, watch it fail, then implement. Each phase needs at least one hand-derived oracle assertion (not just shape tests).
- **Session-start audit:** `git status` + `git stash list` BEFORE any other action.
- **Plan discipline:** unified plan at `docs/plans/<feature>-plan.md`, vertical-slice phases, Phase 1 = tracer bullet. Front-load full decomposition.
- **Branching:** never push to main directly. Agent stops at `gh pr create`. User merges in GitHub UI. After merge, sync local main and delete the local feature branch.
- **Surgical changes:** touch only what you must. Mention adjacent issues, don't fix them in the same PR.
- **No probably / no em dashes / no vacation in expected-conditions framing.**

## OSF lock state

- Tag target: 2026-05-30 (18 days). Once filed, hypotheses + arm definitions + arm calendar + metric definitions + statistical analysis plan + decision rules freeze at a frozen commit hash.
- Experiment starts 2026-06-01 with deterministic 14-day arm alternation (12 arms total). Pre-experiment replay always reasons out at Stage 2 (`no_arm_assignments_in_window`).
- Replay-validation artifacts are in `docs/replay-validation/2026-05-12/`.

## Pi-lab connection

- Host: `chris@192.168.20.10`
- SSH tunnel to Influx **may still be up** on local port 18086; check with `netstat -an | grep 18086`. If not up:
  ```bash
  ssh -fN -L 18086:localhost:8086 chris@192.168.20.10
  ```
- Credentials live in `/home/chris/energy-stack/.env` on pi-lab (`INFLUXDB_INIT_ADMIN_TOKEN`, `INFLUXDB_INIT_ORG=depaola-home`, `INFLUXDB_INIT_BUCKET=energy`). Reproduction recipe in `docs/replay-validation/2026-05-12/README.md`.
- Influx 2.7.12 ARM64 in docker container named `influxdb` on pi-lab.

## Where to start fresh-session work

1. Run session-start audit (`git status`, `git stash list`).
2. Read `docs/plans/stage8-loader-plan.md` (the locked plan).
3. Begin **Phase 0**:
   - Audit `tools/analysis/pipeline.py` Stage 2 output (`outages.csv`, `imputed_intervals.csv`) for day-level exclusion recoverability.
   - If not cleanly recoverable: design + add `stage2/qualifying_days.csv` with locked columns.
   - Schema rename + outcome rename + reason-code additions.
   - Update `docs/EXPERIMENT_DESIGN.md` §7 line ~334 and §8 line ~363 + `docs/ANALYSIS_PIPELINE.md` to drop "matched-pair" wording for Stage 8 (Stage 4-5-7 stay matched-pair).
   - Acceptance tests for Phase 0 (column constant audit, quiet-zero guard, day-exclusion correctness).
4. Open draft PR after Phase 0 + Phase 1 land.

## Repo state at handoff

- Tests: 314 passing as of last `pytest tools/analysis/tests/`.
- Latest main commit on this branch's parent: `cc6315a docs(agents): drop dangling repo-doc-hygiene references`.
- This branch: `feature/stage8-decomp` with three commits: gitignore chore, plan, this handoff. Supersedes the abandoned `feature/stage8-loader` which carried Desktop-worktree provenance.

## Skills / tooling notes

- `Superpowers` skills auto-load. `brainstorming` / `grill-me` was used for the Stage 8 plan via in-line user back-and-forth; full skill protocol not invoked because the conversation channel was already brainstorming-shaped.
- AGENTS.md branching policy: agent stops at `gh pr create`. User reviews + merges in GitHub UI.
- AGENTS.md doc-hygiene rules (Layer 1) are in force: YAML header required (date, owner, status, role-label), ISO 8601 dates, headerless-non-session-doc-is-a-bug. This file follows it.
