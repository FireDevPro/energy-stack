# Commissioning Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Re-center `hvac_scheduler` from a weather day-type scheduler onto a config-driven, **warm-only** price-reactive controller (comfort baseline; warm above it on price), rewritten in place, shadow-validated, run in `production`.

**Architecture:** **In-place, single-path rewrite — no parallel path, no flag.** `shadow` mode provides safety isolation (computes/logs, never writes); git history is rollback. Rewrite the decision/resolve logic; keep the supervisor, price-overlay state machine, feed-gap logic, `freshness.py`, telemetry writers, **and the full experiment/arm apparatus** as shared single-copy infra. Commissioning runs in **`production`** (calendar-independent); the arm calendar is the 2027 experiment's tool, untouched.

**Tech Stack:** Python 3.12 + asyncio, `influxdb_client` (via `influx_adapter.write_point`), PyYAML (new), pytest. Actuation: `pyControl4` → `aiosomecomfort` (TCC swap); controller and device share `temp_scale`, so the device write is format-only — no unit conversion in that adapter.

**Spec:** [`../specs/2026-06-20-commissioning-controller-design.md`](../specs/2026-06-20-commissioning-controller-design.md).

**Status (2026-06-20):** the `temp_scale` parameterization (unit-as-config foundation) merged in **#104** — controller logic is already scale-agnostic (default `F`, behavior-preserving). This plan executes the reactive rewrite on that base.

## Global Constraints

- **Reuse, don't reinvent** — each task extends a named backbone (Reuse Ledger). The only net-new code is the YAML loader.
- **Warm-only overlay** — the controller cools *to* the comfort baseline and only ever goes *warmer* on price. No below-baseline ("cheap") tier. (Below-baseline exists only in the off-by-default heat-stretch mode.)
- **Commissioning = `production` mode** — controller writes continuously, never consults the arm calendar. `SCHEDULER_MODE` (env) is the sole write gate; `runtime.mode` is NOT added to YAML.
- **Keep the experiment apparatus** — arm calendar, `current_arm_at`, the `experiment` write-gate branch, `write_arm_mode`, `_trace`'s arm call all stay (2027 backbone). Don't delete/redesign.
- **`hvac.arm_mode` keeps emitting** (watchdog liveness). **`required_feeds_for_arm_mode` must drop `weather`** while no enabled mode uses it.
- **Configured unit (`temp_scale`).** Controller logic runs in `temp_scale` (the merged parameter); the device is set to the same unit so the device write is format-only. Comfort program, supervisor bounds, and the decision telemetry are all in `temp_scale`. The Ecowitt sensor stream stays °F (independent instrument). Config values are authored on the scale's grid (whole for `F`, 0.5 for `C`); the loader validates this.
- **`freshness.py` is triple-mirrored** (CI byte-equality) — don't touch thresholds.
- **Phase discipline:** delete day-type tests **in the same slice that removes the code**, so every slice lands green — no multi-PR red-suite window.
- **Tests per-service:** `cd deploy/energy-stack/hvac_scheduler && python -m pytest .`.
- **Commit only when the user asks.**

## Phase 0 — prerequisite (separate branch)

TCC swap `pyControl4`→`aiosomecomfort`, operating in `temp_scale` (device set to the same unit) — device write is format-only, not a conversion. Tracked by [the swap plan](2026-06-19-control4-to-tcc-swap.md). Go-*active* depends on it + a read-after-write check; shadow work does not.

## Reuse Ledger (audited)

| Need | Backbone | Action |
|---|---|---|
| Tier state machine | `price_overlay.py` | extend 2→4 warm tiers (add `extreme`), in place |
| Overlay merge | `app.py:resolve_layer_priority` (`max(...)`) | reuse warm-only `max` against the comfort baseline |
| Feed-gap (3-clock) | `app.py:_evaluate_layer_inputs` | **reuse as-is** (warm-only ⇒ existing safety theorem holds) |
| Freshness | `freshness.py` | reuse unchanged (mirrored) |
| Supervisor | `safety_supervisor.validate_setpoints` | reuse unchanged (scale-agnostic since #104) |
| Humidity | `HUMID_DEWPOINT_F` / `cool_setpoint_humid_f` concepts | new guard after snapshot, both paths |
| Comfort blocks | `ScheduleAction` + `action_in_effect_at` | reuse to represent the program |
| Decision log | `hvac.actions` (per-push record) + `decision_trace` | reshape `hvac.actions`; **no new measurement** |
| Liveness/write-gate/arm | `write_arm_mode`, `_writes_allowed`, arm calendar | reuse unchanged |
| Shadow validation | `tools/analysis/run_shadow_validation.py` | extend checks to the reshaped `hvac.actions` decision log |
| Config | `Config` + `from_env` | add a YAML-file field (only net-new code) |

## Outside-in acceptance test

`test_commissioning_controller_acceptance.py`, **`@pytest.mark.xfail(strict=True)`** from Slice A. Replays a recorded day; asserts setpoints follow the comfort program, warm tiers raise toward the ceiling, humidity guard fires, setpoints land on the `temp_scale` grid, no day-type consulted, supervisor continuity across a mid-block restart. Marker comes off in Cleanup only on a clean pass.

---

## Slice A — Tracer: comfort baseline, per-tick, through the existing pipeline, in shadow

Thinnest vertical cut, and it fixes the supervisor-continuity trap up front. Spec §"Comfort program", §"Units", §"Runtime/safety" (per-tick baseline).

### A1: YAML config loader (only net-new code)
**Files:** create `controller_config.py` + test; modify `Config`/`from_env` (`CONTROLLER_CONFIG_FILE`); `requirements.txt` +PyYAML; `commissioning-controller.example.yaml`.
**Interface:** `ControllerConfig` (no `cheap`/`runtime` keys; has `comfort_floor`); `load_controller_config(path)`. Loader **validates** `bank_to_f ≥ comfort_floor ≥ supervisor 65`.
- [ ] failing test (parse, midnight-wrap block, floor-invariant rejection) → FAIL → implement → PASS → commit.

### A2: per-tick comfort baseline + mid-period continuity (the safety fix)
**Files:** `controller_core.py` (`comfort_baseline_cool(program, now)`); `app.py` `run_schedule_check` / `_push_layer_change_mid_period`.
Compute the comfort baseline **every tick** before `_evaluate_layer_inputs`, and feed it where `last_schedule_cool_f` was sourced — so `_push_layer_change_mid_period` no longer dead-ends on `None` and the indoor-≥86°F supervisor continuity survives restarts.
- [ ] failing tests: block-boundary baseline; **mid-block restart → supervisor still arms tick 1**; `_push_layer_change_mid_period` runs with no scheduled actions → FAIL → implement → PASS → commit.

### A3: wire `temp_scale` config through + on-grid validation
**Files:** `controller_config.py` (temperature values in `temp_scale`); confirm comfort program + supervisor bounds flow through in `temp_scale` (the parameter itself merged in #104 — controller logic is already scale-agnostic). Loader validates each value is on the scale's grid (whole for `F`, 0.5 for `C`). Device-write format-only is the device-adapter's concern (Phase 0 / go-active), not here.
- [ ] failing test (config `temp_scale` drives comfort program + supervisor bounds; off-grid config value rejected; setpoints land on the scale's grid) → FAIL → implement → PASS → commit.

### A4: rewrite resolve to comfort-baseline (warm-band trivial) in shadow + delete day-type tests here
**Files:** `app.py` `run_schedule_check` / `resolve_layer_priority`; **delete the day-type/schedule resolution tests in this same commit** (phase discipline).
Replace day-type/schedule resolution with `comfort_baseline_cool`; resolve = baseline (no price offset yet) → supervisor → existing `write_action` + `write_arm_mode` + a reactive `_trace` line. Arm apparatus untouched.
- [ ] failing test: tick computes baseline setpoint, supervisor runs, shadow `hvac.actions` row written, `fetch_today_decision`/`schedule_for` not called → FAIL → implement + delete now-obsolete day-type tests → **full suite green** → commit.

### A5: outside-in acceptance test (xfail) → commit.

**Slice A demo:** in shadow, the house tracks 74/76/78/76 on a real day, native-unit, no day-types, supervisor continuous across restart — validated via the extended `run_shadow_validation.py`. Suite green.

---

## Slice B — Warm price tiers + reactive decision log

Spec §"Reactive core". **Extend** `price_overlay.py` warm-only.
- **B1** — `PRICE_TIERS` → `normal/elevated/scarcity/extreme`, all warmer-or-equal (add `extreme` → ceiling); thresholds from config. Reuse hold/hysteresis/`max` merge against the comfort baseline. Retune `test_price_overlay.py` + `test_integration_2025_replay.py` **in this slice** (they encode the old tiers).
- **B2** — feed-gap: **reuse `_evaluate_layer_inputs` as-is** (warm-only ⇒ hold-then-30-min-release stays correct); add a couple of tests confirming a warm tier holds through a 5-min gap and releases at genuine staleness.
- **B3** — reshape `hvac.actions` as the decision log (tier + baseline + effective + guards; values in `temp_scale`). **No new measurement.** Do **not** add new tier names to `hvac.price_overlay`.
**Demo:** elevated/scarcity/extreme raise the setpoint toward the ceiling on a recorded spike day, in shadow; feed-loss holds then releases.

## Slice C — Guards

Spec §"Guards". **C1** humidity guard after snapshot, before supervisor, in **both** action-fire and mid-period paths (reads `snapshot["humidity"]`, missing→conservative). **C2** dog-bound ceiling (82/85, humidity-aware; supervisor-86 override documented). **C3** wire into the resolve chain + acceptance assertions.
**Demo:** high-RH drops the setpoint; spike float caps at 82, never >85.

## Cleanup — remove dead day-type code, finalize telemetry, docs

- **Remove (Arm-B internal logic only):** `decide_day_type`/`_classify_*`, the four schedules + `schedule_for`, the 21:00 cycle (`run_decision`/`revisit`/`write_decision`/`fetch_today_decision`), day-ahead precool (`precool.py`, etc.), `reconstruct_startup_baseline`. Tests for each were deleted in their slice; sweep any stragglers (`test_decision_trace.py` day-type halves).
- **Do NOT remove:** the experiment apparatus (arm calendar, `experiment` mode, `current_arm_at`, `write_arm_mode`, `_trace` arm call), `pjm_5cp.py`/`hvac.5cp_state`.
- **Telemetry migration:** remove the day-type writers (`write_decision`, `write_precool_window`); the reshaped `hvac.actions` + traces are the decision log (**no new measurement**). **Flag + track** the consumer migration — the OSF analysis pipeline + cockpit day-type/precool panels move to `hvac.actions`/traces (2027 analysis-prep + a scope-fenced cockpit PR); reason-code the replay-manifest gap. Not silent.
- **`required_feeds_for_arm_mode`:** drop `weather` while no enabled mode uses it.
- **Heat-stretch:** keep one `fetch_latest_forecast` helper alive (dead until the mode flips on); don't delete all forecast plumbing.
- **Acceptance test:** remove xfail; full suite green, zero scaffolding.
- **Docs:** rewrite `docs/HVAC_LOGIC.md`; update `docs/SERVICES.md#hvac-scheduler`; archive this plan on merge.

## Go-active (separate, gated)

TCC swap landed + adapter conversion + **read-after-write round-trip** + native-unit/range preflight against `aiosomecomfort` limits + container-restart recompute check + SOPS/`SCHEDULER_MODE` confirmed → flip `shadow`→**`production`**.

## Self-Review

- **Spec coverage:** comfort program (A2/A4), per-tick baseline + continuity (A2), units-at-adapter (A3), warm tiers (B1), feed-gap reuse (B2), reactive log (B3), guards (C), telemetry migration + required-feeds + heat-stretch helper (Cleanup), production mode + go-active gates (Global + Go-active), arm apparatus kept (Global/Cleanup). Covered.
- **Reuse:** only net-new code is the YAML loader; feed-gap is reuse-as-is (cheap-tier cut removed the asymmetry).
- **Load-bearing traps from the dual-review:** per-tick baseline/supervisor continuity (A2), warm-only ⇒ no signed-offset/no-thrash (Global/B), units don't leak into `_f` (A3), arm apparatus untouched + production-not-calendar (Global), `required_feeds` weather (Cleanup), green-every-slice (Global/A4/B1), telemetry consumer migration tracked not silent (Cleanup).

## Pre-execution gates

1. **Dual-review (Codex + Claude)** — done this round; re-run after this revision if desired.
2. Out-of-repo consumer audit (Grafana repo; the local decision-trace email task) — informs the downstream migration.
3. Confirm `SCHEDULER_MODE` live state + SOPS sync before go-active.
