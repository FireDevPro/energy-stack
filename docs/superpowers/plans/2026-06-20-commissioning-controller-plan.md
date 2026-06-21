---
date: 2026-06-20
owner: chris
status: draft (revision 3 — post context-fed review)
role-label: code-team
spec: docs/superpowers/specs/2026-06-20-commissioning-controller-design.md
---

# Commissioning Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax. **Read the spec first** — it's a controlled demolition; do not reintroduce day-types, 5CP control, deep precool, or a software supervisor.

**Goal:** Replace the old `hvac_scheduler` with a simple controller: **Arm B = the Arm A comfort program + price awareness** (holds the baseline, drifts warmer on price). Rewritten in place, shadow-validated, then run for the 2026 season. 2026 scope is *build it and run it solidly* — not tuning, not cost measurement (cost is the 2027 experiment).

**Architecture:** In-place, single-path rewrite — no parallel path, no flag. `shadow` is the isolation during development; git history is rollback. Keep the price-overlay state machine, feed-gap logic, `freshness.py`, telemetry writers, the write gate, **and the retained (2027) experiment apparatus**. **Remove** Arm B's day-type logic **and the software safety supervisor** (safety is device-owned).

**Tech Stack:** Python 3.13 + asyncio, `influxdb_client` (via `influx_adapter.write_point`), PyYAML (new), pytest, mypy (strict). Actuation: `pyControl4` -> `aiosomecomfort` (TCC swap; spec+plan on branch `design/control4-to-tcc-swap`, not merged). Controller and device share `temp_scale` so the device write is format-only.

**How 2026 runs:** (1) shadow — thermostat (Arm A) controls, Arm B logs what it would do, watch for glitches several weeks; (2) flip — Arm B runs the season; (3) review at season end.

## Safety model (binding — device-owned, NO software supervisor)

A software supervisor runs on the controller and dies with it; safety lives in the device:
1. **The thermostat's setpoint min/max limits are the hard cap** — load-bearing, exists today, a device setting. The controller's comfort ceiling is NOT a safety mechanism.
2. **Timed holds, never Permanent** -> a dead controller's hold lapses (≤ `hold_ttl_minutes`) and the thermostat reverts to its onboard schedule. Implemented in the TCC swap's timed-hold increment (prerequisite for go-active). Until then a dead controller leaves the last setpoint held — still bounded by #1.

`safety_supervisor.py` / `validate_setpoints` is **deleted** (with all callers, in one slice — see Slice A4).

## Global Constraints

- **Reuse, don't reinvent** — extend named backbones (Reuse Ledger). Net-new code: the YAML loader + one small `controller_core` helper module.
- **Warm-only + floor invariant** — cools *to* the comfort baseline, only ever warmer on price. **Never commands below the current baseline — enforced by an unconditional clamp in code, not just a test.** No below-baseline path (no cheap tier; no heat-stretch banking).
- **Cost primary, comfort the constraint.** 2026 measures nothing cost-wise; that's 2027.
- **`temp_scale`, controller-native** — logic, config, device I/O, telemetry in `temp_scale` (C or F, per config); conversion only at the (out-of-scope) analysis layer. Telemetry uses scale-neutral names + a `unit` tag. Config authored on the scale's grid; loader validates.
- **Config is the experimental surface** — baseline, offsets, thresholds, ceiling, TTL are config (in `temp_scale`); code owns invariants only. Record config identity in telemetry (path+SHA256 at startup, `config_id` on rows).
- **`SCHEDULER_MODE`** is the sole write gate; **stays pinned `shadow` through development**; the flip to `production` is explicit + gated.
- **Keep the (2027) experiment apparatus** — arm calendar, `experiment` mode, `current_arm_at`, `write_arm_mode`, the `_trace` arm call. Don't delete/redesign.
- **`hvac.arm_mode` keeps emitting** (watchdog liveness). **`required_feeds_for_arm_mode` derived from the enabled-mode set** (not a hardcoded dict); `weather` drops in the same slice it stops being a live input.
- **`freshness.py` is triple-mirrored** (CI byte-equality) — don't touch thresholds.
- **Phase discipline:** delete a thing's tests AND stop the main loop calling it in the same slice that removes the code — green every slice.
- **CI gates BOTH** per-service pytest (`cd deploy/energy-stack/hvac_scheduler && python -m pytest .`) and the typecheck runner (`bash deploy/energy-stack/run_typecheck.sh`, mypy strict). Every slice leaves both green.
- **Commit only when the user asks.**

## Phase 0 — prerequisites (separate)

- **TCC swap** `pyControl4` -> `aiosomecomfort` (spec+plan exist on `design/control4-to-tcc-swap`, not merged), operating in `temp_scale` (format-only), **including the timed-hold increment** (current code sets a Permanent hold; timed holds are the device fail-safe).
- Operational: set the thermostat's setpoint min/max limits + a known-safe onboard fallback schedule; confirm `aiosomecomfort` exposes time-based holds.
- Go-*active* depends on these; shadow work does not.

## Reuse Ledger (audited)

| Need | Backbone | Action |
|---|---|---|
| Tier state machine | `price_overlay.py` | extend 3->4 tiers (add `extreme` + classifier branch) |
| Overlay merge | `app.py:resolve_layer_priority` (`max`) | reuse warm-only `max` vs baseline; add floor clamp |
| Feed-gap (3-clock) | `app.py:_evaluate_layer_inputs` | reuse as-is (stale-spike react is intentional) |
| Freshness | `freshness.py` | reuse unchanged (mirrored) |
| Comfort blocks | `ScheduleAction` + `action_in_effect_at` | reuse; baseline = floor |
| Decision log | `hvac.actions` + `decision_trace` | reshape in place; no new measurement |
| Liveness/write-gate/arm | `write_arm_mode`, `_writes_allowed`, arm calendar | reuse unchanged (retained) |
| Config | `Config` + `from_env` | add a YAML-file field |
| Safety | the **device** (setpoint limits + timed holds) | NOT software; supervisor deleted (Slice A4) |
| Decision oracle | NONE (net-new) | shadow validation = log Arm B + sanity checks; `run_shadow_validation.py` is the INGESTION validator and does NOT check setpoints — do not "extend" it for this |

## Outside-in acceptance test

`test_commissioning_controller_acceptance.py`, `@pytest.mark.xfail(strict=True)` from Slice A. Replays a recorded day; asserts: setpoints follow the comfort program; **never below the current baseline** (floor clamp); warm tiers raise toward the ceiling and **ride it** (don't bounce); the humidity guard releases the overlay to baseline; setpoints land on the `temp_scale` grid; commanded + actual indoor temp both logged; no day-type consulted; **no `validate_setpoints` called**. Marker off in Cleanup on a clean pass.

---

## Slice A — Tracer: comfort baseline through the pipeline, in shadow

### A1: YAML config loader (net-new) + a small `controller_core` helper
**Files:** create `controller_config.py` + `controller_core.py` + tests; modify `Config`/`from_env` (`CONTROLLER_CONFIG_FILE`); `requirements.txt` +PyYAML; `commissioning-controller.example.yaml`.
**Interface:** `ControllerConfig` (frozen; no `cheap`/`runtime`/`supervisor` keys); `load_controller_config(path)`; `comfort_baseline_cool(program, now)`.
**Validations:** parse; midnight-wrap block; **on-grid for `temp_scale`** (whole F / 0.5 C); hysteresis `rh_clear_pct < rh_max_pct`; `hold_ttl_minutes` positive. Log config path + SHA256.
- [ ] failing tests (parse, midnight-wrap, off-grid reject, hysteresis-order reject) -> FAIL -> implement -> PASS (pytest + mypy) -> commit.

### A2: per-tick comfort baseline (control-loop need)
**Files:** `app.py` `run_schedule_check` / `_push_layer_change_mid_period`; `controller_core.py`.
Compute the baseline **every tick** before `_evaluate_layer_inputs`; feed it where `last_schedule_cool` was sourced. Removes the day-type startup-reconstruction dependency. *(No supervisor-continuity concern — supervisor removed in A4.)*
- [ ] failing tests: block-boundary baseline; mid-block restart recomputes baseline; no-scheduled-actions path -> FAIL -> implement -> PASS -> commit.

### A3: `temp_scale` config wired through + on-grid enforcement
**Files:** `controller_config.py`, `app.py`. Drive the comfort/ceiling values from config in native `temp_scale`; no conversion. Effective setpoints land on the scale's grid.
- [ ] failing test (config `temp_scale` drives the program; off-grid rejected; setpoints on-grid) -> FAIL -> implement -> PASS -> commit.

### A4: rewrite resolve to baseline + floor clamp + DELETE the supervisor + delete day-type — one slice
**Files:** `app.py` (`run_schedule_check`/`resolve_layer_priority`, main loop, `write_action`, `_trace`), `safety_supervisor.py` (delete), `decision_codes.py` (drop `SupervisorCode`), tests.
Replace day-type/schedule resolution with `comfort_baseline_cool`; resolve = baseline (no price yet) with an **unconditional `max(effective, baseline)` floor clamp**; **remove `validate_setpoints` and every caller** (import, the 2 call sites, `write_action` supervisor params, `_classify/_trace_supervisor`, the enum); write via `write_action` + `write_arm_mode` + a reactive `_trace`. Stop the main loop calling `run_decision`. Derive `required_feeds_for_arm_mode` from enabled modes; drop `weather`. Delete the day-type + supervisor tests **in this commit**.
- [ ] failing test: tick computes baseline (floor-clamped), shadow row written, `validate_setpoints`/`fetch_today_decision`/`schedule_for`/`run_decision` not called, `required_feeds` excludes weather -> FAIL -> implement + delete obsolete tests -> **full suite green (pytest + mypy)** -> commit.

### A5: outside-in acceptance test (xfail) -> commit.

**Slice A demo:** in shadow, the house tracks the baseline on a real day, native-unit, floor-clamped, no day-types, no supervisor in the code. Suite green.

---

## Slice B — Warm price tiers + reactive decision log

- **B1** — `PRICE_TIERS` -> `normal/elevated/scarcity/extreme` (warmer-or-equal), thresholds from config; **reason-classifier gets an `extreme` branch**. Reuse hold/hysteresis/`max` merge + the floor clamp. Retune `test_price_overlay.py` + `test_integration_2025_replay.py` here.
- **B2** — feed-gap **reuse as-is**; add tests: warm tier holds through a 5-min gap; **reacts to a stale spike (warm upgrade allowed)**; refuses a stale downgrade; releases to baseline after sustained staleness (the stale-release timer runs after the min-hold window, so ~min-hold + 30 min — the existing timing, asserted as-is).
- **B3** — reshape `hvac.actions`: **scale-neutral names + `unit` tag**; carry tier, baseline, **commanded setpoint + actual indoor temp**, drift state; **purge `day_type`, `fivecp_*`, supervisor fields**; add `config_id`. No new measurement. `extreme` first-class in `hvac.price_overlay`.
**Demo:** tiers raise toward the ceiling and ride it on a recorded spike day, in shadow; gaps hold then release; stale spike reacts; rows show commanded vs actual + `config_id`.

## Slice C — Guards

- **C1 — humidity-release guard:** after the snapshot read, both action-fire + mid-period paths, `snapshot["humidity"] >= rh_max_pct` (missing -> conservative) gates the overlay off -> effective = current baseline; overrides the spike-hold min-hold; re-enables under `rh_clear_pct`. Never below baseline; no DEHUM.
- **C2 — ride-the-ceiling:** the warm drift rises to `comfort_max` and **holds** (does not release); wire both guards into resolve + acceptance assertions.
**Demo:** high RH releases the drift to baseline; a spike rides the ceiling and holds; the two guards are asymmetric (temp rides, humidity releases).

## Cleanup — remove dead code, finalize telemetry + config deployment, docs

- **Remove remaining day-type code:** `decide_day_type`/`_classify_*`, the four schedules + `schedule_for`, the 21:00 cycle (`run_decision`/`revisit`/`write_decision`/`fetch_today_decision`), day-ahead precool (`precool.py`), `reconstruct_startup_baseline`. (Supervisor already gone in A4.)
- **Do NOT remove:** the retained experiment apparatus, `pjm_5cp.py`/`hvac.5cp_state` (telemetry-only).
- **Telemetry migration:** remove `write_decision`/`write_precool_window`. **Observers are intentionally second-class — cockpit, the daily trace report, and `tools/analysis/` WILL break and are rebuilt after the controller is stable; record the breakage, don't avoid it.**
- **Config deployment:** add `CONTROLLER_CONFIG_FILE` + a mounted config path + `TEMP_SCALE` to `docker-compose.yml`; put the validated config on Pi-lab.
- **Remove heat-stretch + all forecast plumbing** (`fetch_latest_forecast` and the NWS-forecast wiring into the scheduler) — the controller has no weather input; don't leave forecast hooks "parked on standby." (NWS stays a *telemetry* poller; it just no longer feeds the controller.)
- **Acceptance test:** remove xfail; full suite green (pytest + mypy), zero scaffolding.
- **Docs:** rewrite `docs/HVAC_LOGIC.md`; update `docs/SERVICES.md#hvac-scheduler`; archive this plan on merge.

## Go-active (the flip to `production`)

TCC swap landed incl. timed holds + thermostat setpoint min/max limits set + **confirm the thermostat onboard program is a safe cool schedule (the timed-hold fallback); values live on the device, not the repo** + **hold-expiry-reverts-to-onboard-schedule** verified on hardware + format/readback round-trip + container-restart recompute + SOPS/`SCHEDULER_MODE` confirmation -> flip `shadow` -> `production`.

## Pre-execution gates

1. **Canonical docs aligned** — `README.md`, `INDEX.md`, `docs/PROJECT.md` reframed off the OSF/day-type/old-Arm-B/supervisor framing. **Currently working-tree edits (NOT yet committed) — must be committed before execution** so implementers don't resurrect the dead design. `AGENTS.md` aligned locally (gitignored — local AI context, not a tracked deliverable).
2. Re-review of this revised spec+plan (context-fed).
3. TCC swap (incl. timed holds) landed before go-active; confirm `SCHEDULER_MODE`/SOPS.
