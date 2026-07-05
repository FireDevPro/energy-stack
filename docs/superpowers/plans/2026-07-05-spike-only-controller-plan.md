# Spike-Only Controller (Rev 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---
date: 2026-07-05
owner: chris
status: active
role-label: code-team
spec: docs/superpowers/specs/2026-06-20-commissioning-controller-design.md (rev 4, merged PR #114)
---

**Goal:** Replace the rev 3 always-hold controller with the rev 4 spike-only controller: normal tier writes nothing (the thermostat runs its own program); timed warm holds exist only during price spikes, anchored on a live device read.

**Architecture:** Fresh-derivation, in-repo. A new `controller/` subpackage is written from the spec only and staged beside the running rev 3 code (not wired into the container entrypoint) until the cutover task swaps the Dockerfile CMD and deletes rev 3 with its tests in the same PR. Rev 3 keeps running production through every intermediate merge.

**Tech Stack:** Python 3.13, aiosomecomfort (via the existing `tcc_client.py` seam), influxdb-client, PyYAML, pytest (async via anyio/asyncio patterns already in the repo).

## Global Constraints

Copied from the spec — every task inherits these:

- **Import whitelist (spec §Build discipline).** The ONLY rev 3 modules the new code may import: `hvac_scheduler/tcc_client.py`, `hvac_scheduler/freshness.py`, `hvac_scheduler/arm_calendar.py`, `hvac_scheduler/influx_adapter.py`. Importing or copying from `app.py`, `price_overlay.py`, `controller_core.py`, `controller_config.py`, or `decision_codes.py` is **rev-3 leakage** — a review-rejectable defect. Do not open those files while implementing; the spec is the source.
- **No defaults for tunables (spec §Config).** Every config key is required in the yaml; a missing key is a startup error naming the key. No `dict.get(key, fallback)` for tunables anywhere in controller code.
- **Warm-only is a code invariant.** The controller NEVER commands cool below the device's current program value (`ScheduleCoolSp`). Enforced by clamp + the engage/extend precondition + the own-hold-below-program release rule.
- **fresh-strict = bucket age ≤ 720 s** (12 min). NOT the `freshness.py` 7-min label — that label is display-only (the ComEd bucket-age floor jitters 370–430 s; a control gate must sit above the jitter band).
- **Holds are timed, never Permanent.** Every push uses `set_hold_until` with a quarter-hour-floored expiry ≤ `hold_ttl_minutes` ahead. `set_hold_mode("Permanent")` must not appear in controller code.
- **`SCHEDULER_MODE` is the sole write gate.** `shadow` = compute + trace, zero device writes (including zombie-cleanup releases). `production` = writes live. Mode read from env at startup; invalid → exit 2.
- **Scale-native.** All temps flow in the device's `temp_scale` (env `TEMP_SCALE` must equal yaml `temp_scale`, validated at load). No F↔C conversion anywhere.
- **Three tiers exactly:** `normal` / `elevated` / `scarcity`. No extreme tier.
- **No time locks.** Tier release = `release_confirm_buckets` consecutive fresh-strict buckets at/below the release threshold. Engage = 1 fresh-strict bucket. Stale backstop = `stale_release_minutes` without fresh-strict data while holding → hard release.
- **Device reads are scoped:** ticks at tier ≥ elevated, or with a persisted own-hold record, read the device; pure normal ticks never touch it.
- **Tests run per-service:** `cd deploy/energy-stack/hvac_scheduler && python -m pytest . -q`. NEVER `pytest deploy/energy-stack` from the repo root. New test files use the `test_rev4_*.py` prefix and package-relative imports, matching the existing files.
- **Telemetry field contract (transcribed from rev 3 — these exact names, because live consumers filter on them):** `hvac.actions` tags `unit`, `tier`, `action_label`, `dry_run` ("true"/"false"); fields `commanded_cool`, `commanded_heat`, `baseline_cool` (= observed `ScheduleCoolSp` under rev 4), `drift`, `humidity_gated`, `setpoint_reason`, `applied` (int 0/1), `error` (str, "" when none), `config_id`, `actual_indoor_temp`, `actual_cool_before`, `actual_heat_before`, `actual_humidity`. New rev 4 fields: `schedule_cool` (same value as `baseline_cool`, explicit name), `hold_expires_at` (RFC3339 str, "" when no hold). `hvac.arm_mode`: tags `scheduler_mode`, `arm`; field `mode_actual` (in `production` outside an experiment window the value is `off-protocol-production`; `arm` tag comes from `arm_calendar.current_arm_at`, omitted when None — transcribe rev 3's exact three-branch behavior as specified in Task 10).
- **Commit style:** every task commits on green; end commit messages with the two standard trailer lines used in this repo.

## File Structure

New (all under `deploy/energy-stack/hvac_scheduler/`):

```
controller/
  __init__.py      # empty
  config.py        # yaml loader; ControllerConfig; no-defaults validation; config_id
  pricing.py       # PriceSample; fetch_price (Influx comed.prices 5min); fresh-strict
  tiers.py         # 3-tier state machine: engage-on-1, confirm-count release, stale backstop
  holds.py         # pure hold math: targets, clamps, precondition, quarter-floor expiry, decide_action
  ownhold.py       # persisted own-hold record (/data/own_hold.json) + zombie predicate
  device.py        # ControlSnapshot read + push/release over the TCCClimate seam
  telemetry.py     # hvac.actions row, hvac.arm_mode row, decision-trace JSON lines
  loop.py          # ControllerLoop: 60s ticks, mode gate, healthcheck touch, signals
  __main__.py      # python -m hvac_scheduler.controller
test_rev4_acceptance.py   # outside-in north star (xfail strict until it passes for real)
test_rev4_config.py
test_rev4_pricing.py
test_rev4_tiers.py
test_rev4_holds.py
test_rev4_ownhold.py
test_rev4_loop.py
commissioning-controller-rev4.yaml   # staged config; renamed over the live one at cutover
```

Modified: `hvac_scheduler/tcc_client.py` + `thermostat_poller/tcc_client.py` (seam additions, both verbatim copies), `telegram_notifier/app.py` (two alerts), `thermostat_poller/poller.py` (override retirement), `hvac_scheduler/Dockerfile` (cutover), `docker-compose.yml` (config mount at cutover), `docs/SERVICES.md` (cutover).

Deleted at cutover: `app.py`, `price_overlay.py`, `controller_core.py`, `controller_config.py`, `decision_codes.py`, `commissioning-controller.yaml` (replaced by rev4 file), and their tests: `test_hvac_scheduler.py`, `test_price_overlay.py`, `test_controller_core.py`, `test_controller_config.py`, `test_decision_trace.py`, `test_commissioning_controller_acceptance.py`, `test_integration_2025_replay.py`.

## PR boundaries

- **PR-1 (Phase 1, tracer bullet):** Tasks 1–4 — acceptance test (xfail) + config + pricing + normal-tier-only loop skeleton. Demoable: the new module runs in shadow and traces.
- **PR-2 (Phase 2):** Tasks 5–8 — full tier machine, hold math, seam additions, own-hold record.
- **PR-3 (Phase 3):** Tasks 9–11 — device wrapper, telemetry, full loop wiring. Acceptance test passes; xfail marker removed here.
- **PR-4 (Phase 4):** Tasks 12–14 — notifier alerts, poller override retirement.
- **PR-5 (Phase 5, cutover):** Task 15 — entrypoint swap + rev 3 deletion + live config replacement + docs. Merging this PR deploys rev 4.
- **Post-merge:** go-active gates (operator checklist, end of this plan).

Each PR merges to `main` (no stacking); wait for merge before starting the next. Every intermediate merge redeploys the stack — harmless: the new module is not in the entrypoint until PR-5.

---

### Task 1: Acceptance test — the north star (xfail)

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/test_rev4_acceptance.py`

**Interfaces:**
- Produces (later tasks MUST match these exactly): `controller.loop.ControllerLoop(cfg, price_source, climate, telemetry, mode, tz_name, data_dir)` with `async def tick(self, now_utc: datetime) -> None`; `controller.config.load_config(path: str, temp_scale_env: str) -> ControllerConfig`; the fake seams defined in the test body.

The test drives the REAL `ControllerLoop` end-to-end through fakes at the two external seams only (price feed, device). It is the feature-complete signal: `xfail(strict=True)` until it passes against the real implementation, then the marker comes off (Task 11) and it guards forever.

- [ ] **Step 1: Write the test**

```python
"""Rev 4 outside-in acceptance: the whole spike-only story in one scenario.

Drives the real ControllerLoop through fake seams (price feed + device).
xfail(strict=True) until the implementation is complete — see plan Task 11.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.xfail(
    strict=True, reason="rev 4 controller not complete yet (plan Tasks 2-11)"
)

UTC = timezone.utc
CT = "America/Chicago"


# ---- Fakes at the two external seams --------------------------------------

@dataclass
class FakePriceFeed:
    """Scripted 5-min buckets: list of (bucket_time_utc, cents). fetch() returns
    the newest bucket at-or-before now, mimicking pricing.fetch_price."""
    buckets: list[tuple[datetime, float]] = field(default_factory=list)

    def latest(self, now_utc: datetime):
        past = [(t, v) for t, v in self.buckets if t <= now_utc]
        if not past:
            return None
        t, v = max(past, key=lambda x: x[0])
        return (v, t, (now_utc - t).total_seconds())


@dataclass
class FakeClimate:
    """Device state machine: program value + one temporary hold slot.
    Mirrors the ControlSnapshot fields device.read_control_snapshot returns."""
    schedule_cool: float = 25.5
    cool_setpoint: float = 25.5
    heat_setpoint: float = 18.5
    hold_active: bool = False
    hold_until_minutes: int | None = None
    indoor_temp: float = 25.0
    humidity: float = 45.0
    read_count: int = 0
    pushes: list[tuple[float, float, int]] = field(default_factory=list)  # (cool, heat, until_min)
    releases: int = 0

    def snapshot(self):
        self.read_count += 1
        from hvac_scheduler.controller.device import ControlSnapshot
        return ControlSnapshot(
            schedule_cool=self.schedule_cool,
            cool_setpoint=self.cool_setpoint,
            heat_setpoint=self.heat_setpoint,
            hold_active=self.hold_active,
            hold_until_minutes=self.hold_until_minutes,
            indoor_temp=self.indoor_temp,
            humidity=self.humidity,
        )

    def push(self, cool: float, heat: float, until_minutes: int):
        self.pushes.append((cool, heat, until_minutes))
        self.cool_setpoint = cool
        self.hold_active = True
        self.hold_until_minutes = until_minutes

    def release(self):
        self.releases += 1
        self.hold_active = False
        self.hold_until_minutes = None
        self.cool_setpoint = self.schedule_cool

    def lapse_if_due(self, now_local_minutes: int):
        """The device's own TTL behavior (edge-triggered, like the real CTK04)."""
        if self.hold_active and self.hold_until_minutes is not None \
                and now_local_minutes == self.hold_until_minutes:
            self.release()
            self.releases -= 1  # device lapse, not a controller release


@dataclass
class TelemetryRecorder:
    actions: list[dict] = field(default_factory=list)
    arm_rows: list[dict] = field(default_factory=list)
    traces: list[dict] = field(default_factory=list)

    def write_action(self, **kw): self.actions.append(kw)
    def write_arm_mode(self, **kw): self.arm_rows.append(kw)
    def trace(self, **kw): self.traces.append(kw)


def make_cfg(tmp_path):
    from hvac_scheduler.controller.config import load_config
    y = tmp_path / "c.yaml"
    y.write_text(
        "temp_scale: C\n"
        "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
        "elevated_offset: 1.5\n"
        "scarcity_absolute: 29.5\n"
        "heat_floor: 18.5\n"
        "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
        "hold_ttl_minutes: 30\n"
        "release_confirm_buckets: 2\n"
        "stale_release_minutes: 30\n",
        encoding="utf-8",
    )
    return load_config(str(y), temp_scale_env="C")


# ---- The scenario -----------------------------------------------------------

def test_full_spike_story(tmp_path):
    from hvac_scheduler.controller.loop import ControllerLoop

    cfg = make_cfg(tmp_path)
    feed = FakePriceFeed()
    dev = FakeClimate(schedule_cool=25.5, cool_setpoint=25.5)
    tel = TelemetryRecorder()
    loop = ControllerLoop(
        cfg=cfg, price_source=feed, climate=dev, telemetry=tel,
        mode="production", tz_name=CT, data_dir=str(tmp_path),
    )

    # t0 = 2026-07-10 19:00Z = 14:00 CT (midday block on the real device)
    t0 = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)

    def run_tick(now):
        asyncio.run(loop.tick(now))

    # -- 1. Normal tier: cheap fresh price -> ZERO device interaction
    feed.buckets.append((t0 - timedelta(seconds=400), 4.2))
    run_tick(t0)
    assert dev.read_count == 0 and dev.pushes == [] and dev.releases == 0
    assert tel.traces[-1]["new_tier"] == "normal"

    # -- 2. Spike engages on ONE fresh bucket >= 10c: hold at program + 1.5
    t1 = t0 + timedelta(minutes=5)
    feed.buckets.append((t1 - timedelta(seconds=400), 12.8))
    run_tick(t1)
    assert len(dev.pushes) == 1
    cool, heat, until = dev.pushes[-1]
    assert cool == 27.0            # 25.5 + 1.5
    assert heat == 18.5            # heat pinned on every push
    assert until % 15 == 0         # quarter-hour slot
    assert tel.traces[-1]["new_tier"] == "elevated"

    # -- 3. Escalation to scarcity: absolute 29.5
    t2 = t1 + timedelta(minutes=5)
    feed.buckets.append((t2 - timedelta(seconds=400), 40.6))
    run_tick(t2)
    assert dev.pushes[-1][0] == 29.5
    assert tel.traces[-1]["new_tier"] == "scarcity"

    # -- 4. Program block change mid-hold: corrected on the NEXT tick, not at expiry
    dev.schedule_cool = 23.0       # device program steps down (evening block)
    t3 = t2 + timedelta(minutes=1)
    run_tick(t3)                   # scarcity target still 29.5 -> unchanged, no extra push
    n_before = len(dev.pushes)
    # now drop tier to elevated via two confirming buckets below 18c (scarcity release = 20-2)
    feed.buckets.append((t3 + timedelta(minutes=4, seconds=20), 15.0))
    run_tick(t3 + timedelta(minutes=5))
    feed.buckets.append((t3 + timedelta(minutes=9, seconds=20), 15.5))
    run_tick(t3 + timedelta(minutes=10))
    # two consecutive fresh buckets below scarcity-release but above elevated trigger:
    # tier downgrades to elevated, target re-anchors to program(23.0) + 1.5 = 24.5
    assert tel.traces[-1]["new_tier"] == "elevated"
    assert dev.pushes[-1][0] == 24.5
    assert len(dev.pushes) == n_before + 1

    # -- 5. Collapse: two fresh buckets below elevated-release (8c) -> release to normal,
    #       NO device release write (lapse-only): hold left to expire on the device.
    t4 = t3 + timedelta(minutes=15)
    feed.buckets.append((t4 - timedelta(seconds=400), 4.0))
    run_tick(t4)
    feed.buckets.append((t4 + timedelta(minutes=4, seconds=20), 3.5))
    run_tick(t4 + timedelta(minutes=5))
    assert tel.traces[-1]["new_tier"] == "normal"
    assert dev.releases == 0               # lapse-only: no release write on spike end
    pushes_at_release = len(dev.pushes)

    # -- 6. Normal ticks after release: no further extension, hold lapses on device
    run_tick(t4 + timedelta(minutes=6))
    assert len(dev.pushes) == pushes_at_release

    # -- 7. Zombie cleanup: simulate power-cycle-stuck hold (expired, still active),
    #       with the controller's own record persisted from step 4's push.
    rec_path = tmp_path / "own_hold.json"
    assert rec_path.exists()
    rec = json.loads(rec_path.read_text())
    dev.hold_active = True
    dev.hold_until_minutes = rec["until_minutes"]
    dev.cool_setpoint = rec["value"]
    t5 = t4 + timedelta(hours=2)           # long past expiry + grace
    feed.buckets.append((t5 - timedelta(seconds=400), 3.0))
    run_tick(t5)
    assert dev.releases == 1               # released our zombie, once
    assert not json.loads(rec_path.read_text() or "null")  # record cleared
    run_tick(t5 + timedelta(minutes=1))
    assert dev.releases == 1               # never touches the device again

    # -- 8. Manual hold respected: foreign hold warmer than tier target survives
    dev.hold_active = True
    dev.hold_until_minutes = 999           # not ours (no record)
    dev.cool_setpoint = 30.0               # manually warmer than scarcity_absolute
    t6 = t5 + timedelta(minutes=10)
    feed.buckets.append((t6 - timedelta(seconds=400), 45.0))
    run_tick(t6)
    assert dev.pushes[-1][0] != 30.0 or len(dev.pushes) == pushes_at_release + 0
    # precise assertion: no push occurred (target 29.5 is NOT warmer than held 30.0)
    assert len(dev.pushes) == pushes_at_release
```

- [ ] **Step 2: Run it — expect xfail (collection error would be a failure; the import inside the test body defers module resolution)**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_acceptance.py -q`
Expected: `1 xfailed` (ModuleNotFoundError inside the test body → xfail).

- [ ] **Step 3: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/test_rev4_acceptance.py
git commit -m "test(rev4): outside-in acceptance for spike-only controller (xfail strict)"
```

---

### Task 2: Config loader — no defaults, on-grid validation, config_id

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/controller/__init__.py` (empty)
- Create: `deploy/energy-stack/hvac_scheduler/controller/config.py`
- Create: `deploy/energy-stack/hvac_scheduler/controller/py.typed` — skip; not used elsewhere in repo. (Do not add.)
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_config.py`

**Interfaces:**
- Produces: `ConfigError(Exception)`; `@dataclass(frozen=True) ControllerConfig(temp_scale, elevated_at, scarcity_at, hysteresis_cents, elevated_offset, scarcity_absolute, heat_floor, rh_max_pct, rh_clear_pct, hold_ttl_minutes, release_confirm_buckets, stale_release_minutes, config_id)`; `load_config(path: str, temp_scale_env: str) -> ControllerConfig`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import pytest

from .controller.config import ConfigError, load_config

GOOD = (
    "temp_scale: C\n"
    "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
    "elevated_offset: 1.5\n"
    "scarcity_absolute: 29.5\n"
    "heat_floor: 18.5\n"
    "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
    "hold_ttl_minutes: 30\n"
    "release_confirm_buckets: 2\n"
    "stale_release_minutes: 30\n"
)


def _write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_loads_all_fields_and_config_id(tmp_path):
    cfg = load_config(_write(tmp_path, GOOD), temp_scale_env="C")
    assert cfg.elevated_at == 10.0 and cfg.scarcity_at == 20.0
    assert cfg.elevated_offset == 1.5 and cfg.scarcity_absolute == 29.5
    assert cfg.release_confirm_buckets == 2 and cfg.stale_release_minutes == 30
    assert len(cfg.config_id) == 64  # sha256 hex of file bytes


def test_missing_key_names_the_key(tmp_path):
    bad = GOOD.replace("release_confirm_buckets: 2\n", "")
    with pytest.raises(ConfigError, match="release_confirm_buckets"):
        load_config(_write(tmp_path, bad), temp_scale_env="C")


def test_no_silent_defaults_every_tunable_required(tmp_path):
    for key in ("elevated_offset", "scarcity_absolute", "heat_floor",
                "hold_ttl_minutes", "stale_release_minutes"):
        bad = "\n".join(l for l in GOOD.splitlines() if not l.startswith(key)) + "\n"
        with pytest.raises(ConfigError, match=key):
            load_config(_write(tmp_path, bad), temp_scale_env="C")


def test_off_grid_temp_rejected_for_celsius(tmp_path):
    bad = GOOD.replace("elevated_offset: 1.5", "elevated_offset: 1.3")
    with pytest.raises(ConfigError, match="grid"):
        load_config(_write(tmp_path, bad), temp_scale_env="C")


def test_temp_scale_env_mismatch_rejected(tmp_path):
    with pytest.raises(ConfigError, match="TEMP_SCALE"):
        load_config(_write(tmp_path, GOOD), temp_scale_env="F")


def test_invariants(tmp_path):
    bad = GOOD.replace("scarcity_at: 20", "scarcity_at: 9")
    with pytest.raises(ConfigError, match="elevated_at"):
        load_config(_write(tmp_path, bad), temp_scale_env="C")
    bad2 = GOOD.replace("rh_clear_pct: 58", "rh_clear_pct: 61")
    with pytest.raises(ConfigError, match="rh_clear"):
        load_config(_write(tmp_path, bad2), temp_scale_env="C")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_config.py -q`
Expected: FAIL / collection error `ModuleNotFoundError: hvac_scheduler.controller.config`.

- [ ] **Step 3: Implement**

```python
"""Rev 4 controller config loader. Spec: rev 4 §Config is the experimental surface.

Every tunable is REQUIRED — no code defaults, so no seed can fossilize into a
fallback. temp_scale must match the TEMP_SCALE env (coherence check). Temps
must sit on the scale's grid (0.5 for C, 1.0 for F). config_id = sha256 of the
file bytes, stamped into telemetry for provenance.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import yaml


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ControllerConfig:
    temp_scale: str
    elevated_at: float
    scarcity_at: float
    hysteresis_cents: float
    elevated_offset: float
    scarcity_absolute: float
    heat_floor: float
    rh_max_pct: float
    rh_clear_pct: float
    hold_ttl_minutes: int
    release_confirm_buckets: int
    stale_release_minutes: int
    config_id: str


def _require(mapping: dict, key: str, ctx: str = ""):
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigError(f"missing required config key: {ctx}{key}")
    return mapping[key]


def _on_grid(value: float, scale: str, name: str) -> float:
    step = 0.5 if scale == "C" else 1.0
    if round(value / step) * step != value:
        raise ConfigError(f"{name}={value} is off the {scale} grid (step {step})")
    return float(value)


def load_config(path: str, temp_scale_env: str) -> ControllerConfig:
    with open(path, "rb") as f:
        raw_bytes = f.read()
    doc = yaml.safe_load(raw_bytes)
    if not isinstance(doc, dict):
        raise ConfigError("config root must be a mapping")

    scale = str(_require(doc, "temp_scale"))
    if scale not in ("C", "F"):
        raise ConfigError(f"temp_scale must be C or F, got {scale!r}")
    if scale != temp_scale_env:
        raise ConfigError(
            f"TEMP_SCALE env ({temp_scale_env!r}) != yaml temp_scale ({scale!r})"
        )

    tiers = _require(doc, "price_tiers_cents")
    elevated_at = float(_require(tiers, "elevated_at", "price_tiers_cents."))
    scarcity_at = float(_require(tiers, "scarcity_at", "price_tiers_cents."))
    hysteresis = float(_require(tiers, "hysteresis_cents", "price_tiers_cents."))
    guard = _require(doc, "humidity_guard")
    rh_max = float(_require(guard, "rh_max_pct", "humidity_guard."))
    rh_clear = float(_require(guard, "rh_clear_pct", "humidity_guard."))

    cfg = ControllerConfig(
        temp_scale=scale,
        elevated_at=elevated_at,
        scarcity_at=scarcity_at,
        hysteresis_cents=hysteresis,
        elevated_offset=_on_grid(float(_require(doc, "elevated_offset")), scale, "elevated_offset"),
        scarcity_absolute=_on_grid(float(_require(doc, "scarcity_absolute")), scale, "scarcity_absolute"),
        heat_floor=_on_grid(float(_require(doc, "heat_floor")), scale, "heat_floor"),
        rh_max_pct=rh_max,
        rh_clear_pct=rh_clear,
        hold_ttl_minutes=int(_require(doc, "hold_ttl_minutes")),
        release_confirm_buckets=int(_require(doc, "release_confirm_buckets")),
        stale_release_minutes=int(_require(doc, "stale_release_minutes")),
        config_id=hashlib.sha256(raw_bytes).hexdigest(),
    )

    if not (0 < cfg.elevated_at < cfg.scarcity_at):
        raise ConfigError("invariant: 0 < elevated_at < scarcity_at")
    if cfg.hysteresis_cents <= 0:
        raise ConfigError("invariant: hysteresis_cents > 0")
    if cfg.elevated_offset <= 0:
        raise ConfigError("invariant: elevated_offset > 0 (warm-only)")
    if not (0 < cfg.rh_clear_pct < cfg.rh_max_pct):
        raise ConfigError("invariant: rh_clear_pct < rh_max_pct")
    if cfg.hold_ttl_minutes < 15 or cfg.release_confirm_buckets < 1 \
            or cfg.stale_release_minutes < 5:
        raise ConfigError("invariant: hold_ttl_minutes>=15, release_confirm_buckets>=1, stale_release_minutes>=5")
    return cfg
```

- [ ] **Step 4: Run to verify pass**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_config.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/ deploy/energy-stack/hvac_scheduler/test_rev4_config.py
git commit -m "feat(rev4): config loader — required keys, on-grid, config_id"
```

---

### Task 3: Pricing — fetch + fresh-strict

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/controller/pricing.py`
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_pricing.py`

**Interfaces:**
- Produces: `FRESH_STRICT_MAX_AGE_SEC: float = 720.0`; `@dataclass(frozen=True) PriceSample(cents: float, bucket_time_utc: datetime, age_sec: float)`; `def is_fresh_strict(s: PriceSample) -> bool`; `def fetch_price(query_api, bucket: str, now_utc: datetime) -> PriceSample | None`.
- Consumes: nothing from other tasks (influxdb query API duck-typed).

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .controller.pricing import (
    FRESH_STRICT_MAX_AGE_SEC, PriceSample, fetch_price, is_fresh_strict,
)

UTC = timezone.utc


def test_fresh_strict_boundary():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    ok = PriceSample(5.0, now - timedelta(seconds=719), 719)
    old = PriceSample(5.0, now - timedelta(seconds=721), 721)
    assert is_fresh_strict(ok) and not is_fresh_strict(old)
    assert FRESH_STRICT_MAX_AGE_SEC == 720.0


class _Rec:
    def __init__(self, t, v): self._t, self._v = t, v
    def get_time(self): return self._t
    def get_value(self): return self._v


class _Table:
    def __init__(self, recs): self.records = recs


class _QueryApi:
    def __init__(self, recs): self._recs = recs
    def query(self, flux): return [_Table(self._recs)] if self._recs else []


def test_fetch_returns_latest_bucket_with_age():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    t = now - timedelta(seconds=430)
    api = _QueryApi([_Rec(t, 12.8)])
    s = fetch_price(api, "energy", now)
    assert s is not None and s.cents == 12.8 and s.age_sec == 430.0


def test_fetch_none_when_empty():
    assert fetch_price(_QueryApi([]), "energy", datetime.now(UTC)) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_pricing.py -q`
Expected: collection error `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Rev 4 price input. fresh-strict = bucket age <= 720s (12 min), calibrated to
the measured ComEd publish-lag jitter (floor 370-430s, sawtooth ceiling ~11.2
min). Spec: rev 4 §Feed-gap. The freshness.py 7-min label is display-only and
must NOT gate control decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

FRESH_STRICT_MAX_AGE_SEC: float = 720.0


@dataclass(frozen=True)
class PriceSample:
    cents: float
    bucket_time_utc: datetime
    age_sec: float


def is_fresh_strict(s: PriceSample) -> bool:
    return s.age_sec <= FRESH_STRICT_MAX_AGE_SEC


def _flux_latest_5min(bucket: str) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices" and r.period_type == "5min")
  |> filter(fn: (r) => r._field == "price_cents_per_kwh")
  |> last()
'''


def fetch_price(query_api, bucket: str, now_utc: datetime) -> PriceSample | None:
    for table in query_api.query(_flux_latest_5min(bucket)):
        for rec in table.records:
            t = rec.get_time()
            v = rec.get_value()
            if t is None or v is None:
                return None
            return PriceSample(
                cents=float(v),
                bucket_time_utc=t,
                age_sec=(now_utc - t).total_seconds(),
            )
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_pricing.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/pricing.py deploy/energy-stack/hvac_scheduler/test_rev4_pricing.py
git commit -m "feat(rev4): price fetch + fresh-strict (720s, jitter-calibrated)"
```

---

### Task 4: Loop skeleton — normal-tier tracer bullet

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/controller/tiers.py` (minimal: state + normal-only evaluate; Task 5 completes it)
- Create: `deploy/energy-stack/hvac_scheduler/controller/loop.py` (skeleton)
- Create: `deploy/energy-stack/hvac_scheduler/controller/__main__.py`
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py`

**Interfaces:**
- Produces: `tiers.NORMAL/ELEVATED/SCARCITY` (str constants `"normal"/"elevated"/"scarcity"`); `@dataclass TierState(tier: str = "normal", confirm_count: int = 0, confirm_below: str | None = None, last_confirm_bucket: datetime | None = None, last_fresh_utc: datetime | None = None)`; `evaluate_tier(state, sample, cfg, now_utc) -> tuple[TierState, str]` (reason-code str); `loop.ControllerLoop` per Task 1's constructor, with `tick()` that this task implements for the normal path only: fetch price → evaluate → trace → NO device access.
- Consumes: `config.ControllerConfig`, `pricing.PriceSample/is_fresh_strict/fetch_price` (via injected `price_source.latest(now_utc)` returning `(cents, bucket_time, age_sec) | None` — the injection seam the acceptance test fakes; production wiring in Task 11 adapts `fetch_price`).

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .controller.config import load_config
from .controller.loop import ControllerLoop

UTC = timezone.utc

CFG_YAML = (
    "temp_scale: C\n"
    "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
    "elevated_offset: 1.5\nscarcity_absolute: 29.5\nheat_floor: 18.5\n"
    "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
    "hold_ttl_minutes: 30\nrelease_confirm_buckets: 2\nstale_release_minutes: 30\n"
)


@dataclass
class Feed:
    out: tuple | None = None
    def latest(self, now_utc): return self.out


@dataclass
class NeverClimate:
    """Blows up on any access — normal tier must never touch the device."""
    def snapshot(self): raise AssertionError("device read in normal tier")
    def push(self, *a): raise AssertionError("device write in normal tier")
    def release(self): raise AssertionError("device release in normal tier")


@dataclass
class Tel:
    traces: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    arm_rows: list = field(default_factory=list)
    def trace(self, **kw): self.traces.append(kw)
    def write_action(self, **kw): self.actions.append(kw)
    def write_arm_mode(self, **kw): self.arm_rows.append(kw)


def _loop(tmp_path, feed):
    p = tmp_path / "c.yaml"; p.write_text(CFG_YAML, encoding="utf-8")
    cfg = load_config(str(p), temp_scale_env="C")
    return ControllerLoop(cfg=cfg, price_source=feed, climate=NeverClimate(),
                          telemetry=Tel(), mode="shadow", tz_name="America/Chicago",
                          data_dir=str(tmp_path))


def test_normal_tick_traces_and_never_touches_device(tmp_path):
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    feed = Feed(out=(4.2, now - timedelta(seconds=400), 400.0))
    loop = _loop(tmp_path, feed)
    asyncio.run(loop.tick(now))
    t = loop.telemetry.traces[-1]
    assert t["new_tier"] == "normal" and t["price_cents"] == 4.2
    assert t["scheduler_mode"] == "shadow"


def test_missing_feed_is_a_traced_noop(tmp_path):
    loop = _loop(tmp_path, Feed(out=None))
    asyncio.run(loop.tick(datetime(2026, 7, 10, 12, 0, tzinfo=UTC)))
    assert loop.telemetry.traces[-1]["reason_code"] == "REV4_FEED_MISSING"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py -q`
Expected: collection error `ModuleNotFoundError`.

- [ ] **Step 3: Implement minimal tiers.py**

```python
"""Rev 4 tier machine — three tiers, no time locks. Spec: rev 4 §Reactive core.
This file lands minimal (normal-only) in the tracer slice; Task 5 completes
engage/confirm-release/stale-backstop.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

NORMAL = "normal"
ELEVATED = "elevated"
SCARCITY = "scarcity"
_ORDER = {NORMAL: 0, ELEVATED: 1, SCARCITY: 2}


@dataclass(frozen=True)
class TierState:
    tier: str = NORMAL
    confirm_count: int = 0
    confirm_below: str | None = None          # tier the confirmations point at
    last_confirm_bucket: datetime | None = None
    last_fresh_utc: datetime | None = None


def evaluate_tier(state: TierState, sample, cfg, now_utc: datetime):
    """Returns (new_state, reason_code). Minimal tracer version: stays normal."""
    if sample is None:
        return state, "REV4_FEED_MISSING"
    return state, "REV4_NORMAL_BELOW_TRIGGER"
```

- [ ] **Step 4: Implement loop.py skeleton**

```python
"""Rev 4 controller loop. Spec: rev 4 §Runtime.

Injection seams (constructor) so the acceptance test drives the real loop:
  price_source.latest(now_utc) -> (cents, bucket_time_utc, age_sec) | None
  climate.snapshot() / .push(cool, heat, until_minutes) / .release()
  telemetry.trace(**kw) / .write_action(**kw) / .write_arm_mode(**kw)
Production wiring (Task 11) adapts the real Influx + TCC clients to these.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import ControllerConfig
from .pricing import PriceSample
from . import tiers


class ControllerLoop:
    def __init__(self, *, cfg: ControllerConfig, price_source, climate,
                 telemetry, mode: str, tz_name: str, data_dir: str) -> None:
        if mode not in ("shadow", "production"):
            raise ValueError(f"SCHEDULER_MODE must be shadow|production, got {mode!r}")
        self.cfg = cfg
        self.price_source = price_source
        self.climate = climate
        self.telemetry = telemetry
        self.mode = mode
        self.tz = ZoneInfo(tz_name)
        self.data_dir = data_dir
        self.tier_state = tiers.TierState()

    async def tick(self, now_utc: datetime) -> None:
        tick_id = uuid.uuid4().hex
        raw = self.price_source.latest(now_utc)
        sample = None
        if raw is not None:
            cents, bucket_time, age_sec = raw
            sample = PriceSample(cents=cents, bucket_time_utc=bucket_time, age_sec=age_sec)

        prev = self.tier_state.tier
        self.tier_state, reason = tiers.evaluate_tier(
            self.tier_state, sample, self.cfg, now_utc)

        self.telemetry.trace(
            tick_id=tick_id,
            scheduler_mode=self.mode,
            price_cents=(sample.cents if sample else None),
            bucket_age_sec=(sample.age_sec if sample else None),
            prev_tier=prev,
            new_tier=self.tier_state.tier,
            reason_code=reason,
            config_id=self.cfg.config_id,
        )
        # Device interaction (spike path + cleanup) lands in Task 11.
```

- [ ] **Step 5: Implement `__main__.py` (stub that will gain real wiring in Task 11)**

```python
"""Entrypoint: python -m hvac_scheduler.controller  (wired at cutover)."""
from __future__ import annotations

import sys


def main() -> int:
    print("rev4 controller: production wiring lands in plan Task 11", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run all rev4 tests**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py test_rev4_config.py test_rev4_pricing.py -q`
Expected: all pass; acceptance still xfails.

- [ ] **Step 7: Commit — end of PR-1 (tracer bullet)**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/ deploy/energy-stack/hvac_scheduler/test_rev4_loop.py
git commit -m "feat(rev4): loop tracer — normal tier traces, zero device access"
```

---

### Task 5: Tier machine — engage, confirm-release, stale backstop

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/controller/tiers.py` (replace `evaluate_tier` body; keep signatures)
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_tiers.py`

**Interfaces:**
- Consumes: `TierState`, `PriceSample`, `ControllerConfig` (Tasks 2–4).
- Produces: complete `evaluate_tier(state, sample, cfg, now_utc) -> (TierState, reason_code)` with reason codes: `REV4_FEED_MISSING`, `REV4_NORMAL_BELOW_TRIGGER`, `REV4_UPGRADED_TO_ELEVATED`, `REV4_UPGRADED_TO_SCARCITY`, `REV4_HELD_IN_TIER`, `REV4_ENGAGE_BLOCKED_NOT_FRESH`, `REV4_RELEASE_CONFIRMING`, `REV4_DOWNGRADED_TO_ELEVATED`, `REV4_RELEASED_TO_NORMAL`, `REV4_RELEASED_STALE_BACKSTOP`.

Semantics (spec §Release policy, transcribed): target tier for a price = highest tier whose trigger ≤ price. Upgrades: single fresh-strict bucket, immediate. Downgrades: a fresh-strict bucket whose price < (current tier's trigger − hysteresis) counts as one confirmation toward the tier it points at — counted **once per bucket** (`bucket_time_utc` must differ from `last_confirm_bucket`; the 1-min tick sees each 5-min bucket ~5 times). `release_confirm_buckets` confirmations → move to the pointed tier (could be normal or elevated), reset counter. A fresh bucket at/above the current tier's release threshold resets the counter. Stale backstop: while tier ≥ elevated, if `now − last_fresh_utc ≥ stale_release_minutes` → straight to normal. `last_fresh_utc` updates on every fresh-strict sample regardless of tier.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .controller.config import load_config
from .controller.pricing import PriceSample
from .controller.tiers import TierState, evaluate_tier

UTC = timezone.utc


def _cfg(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "temp_scale: C\n"
        "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
        "elevated_offset: 1.5\nscarcity_absolute: 29.5\nheat_floor: 18.5\n"
        "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
        "hold_ttl_minutes: 30\nrelease_confirm_buckets: 2\nstale_release_minutes: 30\n",
        encoding="utf-8")
    return load_config(str(p), temp_scale_env="C")


def _s(cents, now, age=400.0):
    return PriceSample(cents, now - timedelta(seconds=age), age)


def test_engage_on_one_fresh_bucket(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, reason = evaluate_tier(TierState(), _s(12.8, now), cfg, now)
    assert st.tier == "elevated" and reason == "REV4_UPGRADED_TO_ELEVATED"
    st, reason = evaluate_tier(st, _s(40.0, now), cfg, now)
    assert st.tier == "scarcity" and reason == "REV4_UPGRADED_TO_SCARCITY"


def test_engage_blocked_when_not_fresh(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, reason = evaluate_tier(TierState(), _s(50.0, now, age=900), cfg, now)
    assert st.tier == "normal" and reason == "REV4_ENGAGE_BLOCKED_NOT_FRESH"


def test_release_needs_two_distinct_buckets(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(25.0, now), cfg, now)
    assert st.tier == "scarcity"
    # one cheap bucket, seen on three consecutive 1-min ticks: ONE confirmation
    cheap = _s(4.0, now + timedelta(minutes=5))
    for i in range(3):
        st, reason = evaluate_tier(st, cheap, cfg, now + timedelta(minutes=5 + i))
    assert st.tier == "scarcity" and reason == "REV4_RELEASE_CONFIRMING"
    # a SECOND distinct cheap bucket -> released to normal
    cheap2 = PriceSample(3.5, cheap.bucket_time_utc + timedelta(minutes=5), 400.0)
    st, reason = evaluate_tier(st, cheap2, cfg, now + timedelta(minutes=10))
    assert st.tier == "normal" and reason == "REV4_RELEASED_TO_NORMAL"


def test_stepdown_to_elevated_via_confirmations(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(40.0, now), cfg, now)
    b1 = _s(15.0, now + timedelta(minutes=5))     # < 18 (scarcity release), >= 10
    st, _ = evaluate_tier(st, b1, cfg, now + timedelta(minutes=5))
    b2 = PriceSample(15.5, b1.bucket_time_utc + timedelta(minutes=5), 400.0)
    st, reason = evaluate_tier(st, b2, cfg, now + timedelta(minutes=10))
    assert st.tier == "elevated" and reason == "REV4_DOWNGRADED_TO_ELEVATED"


def test_price_back_above_release_resets_confirmations(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(25.0, now), cfg, now)
    st, _ = evaluate_tier(st, _s(4.0, now + timedelta(minutes=5)), cfg, now + timedelta(minutes=5))
    assert st.confirm_count == 1
    st, reason = evaluate_tier(st, _s(30.0, now + timedelta(minutes=10)), cfg, now + timedelta(minutes=10))
    assert st.confirm_count == 0 and st.tier == "scarcity" and reason == "REV4_HELD_IN_TIER"


def test_stale_backstop_releases_after_30_min_without_fresh(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(25.0, now), cfg, now)
    later = now + timedelta(minutes=31)
    stale = PriceSample(25.0, now - timedelta(seconds=400), (later - (now - timedelta(seconds=400))).total_seconds())
    st, reason = evaluate_tier(st, stale, cfg, later)
    assert st.tier == "normal" and reason == "REV4_RELEASED_STALE_BACKSTOP"


def test_hysteresis_band_holds_tier(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(25.0, now), cfg, now)
    # 19c: below scarcity trigger (20) but at/above release threshold (18) -> hold
    st, reason = evaluate_tier(st, _s(19.0, now + timedelta(minutes=5)), cfg, now + timedelta(minutes=5))
    assert st.tier == "scarcity" and reason == "REV4_HELD_IN_TIER" and st.confirm_count == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_tiers.py -q`
Expected: FAILs (minimal evaluate_tier stays normal).

- [ ] **Step 3: Implement the full state machine (replace `evaluate_tier`)**

```python
def _target_for_price(cents: float, cfg) -> str:
    if cents >= cfg.scarcity_at:
        return SCARCITY
    if cents >= cfg.elevated_at:
        return ELEVATED
    return NORMAL


def _release_threshold(tier: str, cfg) -> float:
    trigger = cfg.scarcity_at if tier == SCARCITY else cfg.elevated_at
    return trigger - cfg.hysteresis_cents


def evaluate_tier(state: TierState, sample, cfg, now_utc: datetime):
    from .pricing import is_fresh_strict

    if sample is None:
        if state.tier != NORMAL and state.last_fresh_utc is not None and \
                (now_utc - state.last_fresh_utc).total_seconds() >= cfg.stale_release_minutes * 60:
            return TierState(tier=NORMAL), "REV4_RELEASED_STALE_BACKSTOP"
        return state, "REV4_FEED_MISSING"

    fresh = is_fresh_strict(sample)
    if fresh:
        state = replace(state, last_fresh_utc=now_utc)

    # Stale backstop (only bites while holding a tier)
    if state.tier != NORMAL and not fresh:
        anchor = state.last_fresh_utc
        if anchor is not None and (now_utc - anchor).total_seconds() >= cfg.stale_release_minutes * 60:
            return TierState(tier=NORMAL), "REV4_RELEASED_STALE_BACKSTOP"

    target = _target_for_price(sample.cents, cfg)

    # Upgrades: one fresh bucket, immediate.
    if _ORDER[target] > _ORDER[state.tier]:
        if not fresh:
            reason = ("REV4_ENGAGE_BLOCKED_NOT_FRESH" if state.tier == NORMAL
                      else "REV4_HELD_IN_TIER")
            return state, reason
        new = TierState(tier=target, last_fresh_utc=state.last_fresh_utc)
        return new, ("REV4_UPGRADED_TO_SCARCITY" if target == SCARCITY
                     else "REV4_UPGRADED_TO_ELEVATED")

    if state.tier == NORMAL:
        return state, "REV4_NORMAL_BELOW_TRIGGER"

    # Downgrade path: fresh bucket strictly below the current tier's release threshold.
    if fresh and sample.cents < _release_threshold(state.tier, cfg):
        if sample.bucket_time_utc == state.last_confirm_bucket:
            return state, "REV4_RELEASE_CONFIRMING"  # same bucket, already counted
        count = state.confirm_count + 1
        if count >= cfg.release_confirm_buckets:
            new_tier = target  # where the confirming prices actually point
            new = TierState(tier=new_tier, last_fresh_utc=state.last_fresh_utc)
            if new_tier == NORMAL:
                return new, "REV4_RELEASED_TO_NORMAL"
            return new, "REV4_DOWNGRADED_TO_ELEVATED"
        return replace(state, confirm_count=count, confirm_below=target,
                       last_confirm_bucket=sample.bucket_time_utc), "REV4_RELEASE_CONFIRMING"

    # In-band (hysteresis) or non-fresh: hold tier, reset confirmations.
    if state.confirm_count:
        state = replace(state, confirm_count=0, confirm_below=None, last_confirm_bucket=None)
    return state, "REV4_HELD_IN_TIER"
```

- [ ] **Step 4: Run to verify pass**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_tiers.py test_rev4_loop.py -q`
Expected: all pass (loop tests still green — evaluate_tier signature unchanged).

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/tiers.py deploy/energy-stack/hvac_scheduler/test_rev4_tiers.py
git commit -m "feat(rev4): tier machine — engage-on-1, confirm-count release, stale backstop"
```

---

### Task 6: Hold math — targets, clamps, expiry slots, decide_action

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/controller/holds.py`
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_holds.py`

**Interfaces:**
- Produces:
  - `REFRESH_MARGIN_SEC: int = 300`; `CLEANUP_GRACE_SEC: int = 300`
  - `def compute_target(tier: str, schedule_cool: float, cfg) -> float | None` — None when no valid warmer-than-program target exists (precondition).
  - `def hold_until_minutes(now_local: datetime, ttl_minutes: int) -> int` — minutes-since-midnight, quarter-hour floored, of now+TTL (wraps mod 1440).
  - `@dataclass(frozen=True) Action` variants as module constants: `def decide(tier, snap, own, cfg, now_utc, now_local, humidity_blocked) -> tuple[str, float | None, int | None, str]` returning `(kind, cool_target, until_minutes, reason)` where kind ∈ `"none" | "push" | "release"`.
- Consumes: `ControlSnapshot` (Task 9 defines the dataclass; this task defines the duck-typed fields it reads: `schedule_cool, cool_setpoint, hold_active, hold_until_minutes, humidity`), `OwnHoldRecord | None` (Task 8: fields `value, until_minutes, expiry_utc`).

Decision table (spec §Hold lifecycle rules 1–4 + §Safety #3 + manual-holds — transcribed):

1. `schedule_cool is None` → `("none", …, "REV4_NO_SCHEDULE_READ")` (fail toward program).
2. Zombie cleanup (tier == normal, own record exists, device hold matches record, `now_utc > expiry_utc + grace`) → `("release", …, "REV4_ZOMBIE_RELEASED")`.
3. Tier == normal otherwise → none.
4. Foreign hold active (hold on device, does NOT match own record): push only if `target > snap.cool_setpoint` (price wins only warmward), else none (`REV4_MANUAL_HOLD_RESPECTED`).
5. Own hold active: recompute target. Target None → `("release", …, "REV4_WARM_ONLY_RELEASE")` if own value < schedule_cool else none (let lapse). Target ≠ own value → push (correct). Target == own value and within `REFRESH_MARGIN_SEC` of expiry and not humidity_blocked → push (extend). Humidity_blocked → none (stop extending; lapse).
6. No hold, tier ≥ elevated, target valid, not humidity_blocked → push (engage). Humidity_blocked → none.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .controller.config import load_config
from .controller.holds import compute_target, decide, hold_until_minutes

UTC = timezone.utc


def _cfg(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "temp_scale: C\n"
        "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
        "elevated_offset: 1.5\nscarcity_absolute: 29.5\nheat_floor: 18.5\n"
        "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
        "hold_ttl_minutes: 30\nrelease_confirm_buckets: 2\nstale_release_minutes: 30\n",
        encoding="utf-8")
    return load_config(str(p), temp_scale_env="C")


@dataclass
class Snap:
    schedule_cool: float | None = 25.5
    cool_setpoint: float = 25.5
    hold_active: bool = False
    hold_until_minutes: int | None = None
    humidity: float | None = 45.0


@dataclass
class Own:
    value: float
    until_minutes: int
    expiry_utc: str


def test_targets_and_precondition(tmp_path):
    cfg = _cfg(tmp_path)
    assert compute_target("elevated", 25.5, cfg) == 27.0
    assert compute_target("scarcity", 25.5, cfg) == 29.5
    assert compute_target("scarcity", 30.0, cfg) is None   # program >= absolute
    assert compute_target("normal", 25.5, cfg) is None
    assert compute_target("elevated", 29.0, cfg) == 29.5   # clamped to absolute, still > program
    assert compute_target("elevated", 29.5, cfg) is None   # program == absolute: nothing warmer to command


def test_hold_until_quarter_floor():
    now = datetime(2026, 7, 10, 14, 7, tzinfo=UTC)      # 14:07 + 30 = 14:37 -> 14:30
    assert hold_until_minutes(now, 30) == 14 * 60 + 30
    now2 = datetime(2026, 7, 10, 23, 50, tzinfo=UTC)    # 23:50 + 30 = 00:20 -> 00:15 (wraps)
    assert hold_until_minutes(now2, 30) == 15


def test_engage_and_no_schedule_read(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    kind, cool, until, reason = decide("elevated", Snap(), None, cfg, now, now, False)
    assert kind == "push" and cool == 27.0 and until % 15 == 0
    kind, *_ , reason = decide("elevated", Snap(schedule_cool=None), None, cfg, now, now, False)
    assert kind == "none" and reason == "REV4_NO_SCHEDULE_READ"


def test_manual_hold_respected_warmward_only(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    warm_manual = Snap(hold_active=True, hold_until_minutes=999, cool_setpoint=30.0)
    kind, *_ , reason = decide("scarcity", warm_manual, None, cfg, now, now, False)
    assert kind == "none" and reason == "REV4_MANUAL_HOLD_RESPECTED"
    cool_manual = Snap(hold_active=True, hold_until_minutes=999, cool_setpoint=22.0)
    kind, cool, _, _ = decide("scarcity", cool_manual, None, cfg, now, now, False)
    assert kind == "push" and cool == 29.5


def test_own_hold_correct_extend_and_warm_only_release(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    own = Own(value=27.0, until_minutes=hold_until_minutes(now, 30),
              expiry_utc=(now + timedelta(minutes=25)).isoformat())
    held = Snap(hold_active=True, hold_until_minutes=own.until_minutes, cool_setpoint=27.0)
    # same target, far from expiry -> none
    kind, *_ = decide("elevated", held, own, cfg, now, now, False)
    assert kind == "none"
    # near expiry -> extend (same value, new until)
    near = now + timedelta(minutes=22)
    kind, cool, until, reason = decide("elevated", held, own, cfg, near, near, False)
    assert kind == "push" and cool == 27.0 and reason == "REV4_EXTENDED"
    # humidity blocked -> stop extending
    kind, *_ , reason = decide("elevated", held, own, cfg, near, near, True)
    assert kind == "none" and reason == "REV4_HUMIDITY_STOP_EXTEND"
    # program stepped ABOVE held value and target invalid -> immediate release
    stepped = Snap(schedule_cool=29.5, hold_active=True,
                   hold_until_minutes=own.until_minutes, cool_setpoint=27.0)
    kind, *_ , reason = decide("scarcity", stepped, own, cfg, now, now, False)
    assert kind == "release" and reason == "REV4_WARM_ONLY_RELEASE"
    # program changed, valid new target -> corrected push on this tick
    stepped2 = Snap(schedule_cool=23.0, hold_active=True,
                    hold_until_minutes=own.until_minutes, cool_setpoint=27.0)
    kind, cool, _, reason = decide("elevated", stepped2, own, cfg, now, now, False)
    assert kind == "push" and cool == 24.5 and reason == "REV4_CORRECTED"


def test_zombie_cleanup_only_matching_and_expired(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    own = Own(value=27.0, until_minutes=870, expiry_utc=(now - timedelta(hours=1)).isoformat())
    zombie = Snap(hold_active=True, hold_until_minutes=870, cool_setpoint=27.0)
    kind, *_ , reason = decide("normal", zombie, own, cfg, now, now, False)
    assert kind == "release" and reason == "REV4_ZOMBIE_RELEASED"
    # non-matching hold: never touched
    foreign = Snap(hold_active=True, hold_until_minutes=915, cool_setpoint=27.0)
    kind, *_ = decide("normal", foreign, own, cfg, now, now, False)
    assert kind == "none"
    # matching but not yet past expiry+grace: leave alone
    own_live = Own(value=27.0, until_minutes=870,
                   expiry_utc=(now + timedelta(minutes=5)).isoformat())
    kind, *_ = decide("normal", zombie, own_live, cfg, now, now, False)
    assert kind == "none"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_holds.py -q`
Expected: collection error `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Rev 4 hold math — pure functions, no I/O. Spec: rev 4 §Reactive core
(setpoint rule, precondition, hold lifecycle rules 1-4), §Safety #3, §Manual holds.
"""
from __future__ import annotations

from datetime import datetime, timedelta

REFRESH_MARGIN_SEC: int = 300
CLEANUP_GRACE_SEC: int = 300

from .tiers import ELEVATED, NORMAL, SCARCITY


def compute_target(tier: str, schedule_cool: float, cfg) -> float | None:
    """Warm-only target with the engage/extend precondition folded in.
    Returns None when the program already sits at/above anything this tier
    would command (also neutralizes the floor>ceiling inversion)."""
    if tier == ELEVATED:
        target = min(schedule_cool + cfg.elevated_offset, cfg.scarcity_absolute)
    elif tier == SCARCITY:
        target = cfg.scarcity_absolute
    else:
        return None
    if schedule_cool >= target:
        return None
    return target


def hold_until_minutes(now_local: datetime, ttl_minutes: int) -> int:
    expiry = now_local + timedelta(minutes=ttl_minutes)
    minutes = expiry.hour * 60 + expiry.minute
    return (minutes // 15) * 15


def _matches_own(own, snap) -> bool:
    return (own is not None and snap.hold_active
            and snap.hold_until_minutes == own.until_minutes
            and snap.cool_setpoint == own.value)


def decide(tier: str, snap, own, cfg, now_utc: datetime, now_local: datetime,
           humidity_blocked: bool):
    """-> (kind, cool_target, until_minutes, reason). kind: none|push|release."""
    none = ("none", None, None, "")

    # Zombie cleanup: normal tier, matching own record, expired past grace.
    if tier == NORMAL:
        if _matches_own(own, snap):
            expiry = datetime.fromisoformat(own.expiry_utc)
            if (now_utc - expiry).total_seconds() > CLEANUP_GRACE_SEC:
                return ("release", None, None, "REV4_ZOMBIE_RELEASED")
        return none

    if snap.schedule_cool is None:
        return ("none", None, None, "REV4_NO_SCHEDULE_READ")

    target = compute_target(tier, snap.schedule_cool, cfg)
    until = hold_until_minutes(now_local, cfg.hold_ttl_minutes)

    if _matches_own(own, snap):
        if target is None:
            if own.value < snap.schedule_cool:
                return ("release", None, None, "REV4_WARM_ONLY_RELEASE")
            return none
        if target != own.value:
            return ("push", target, until, "REV4_CORRECTED")
        expiry = datetime.fromisoformat(own.expiry_utc)
        if (expiry - now_utc).total_seconds() <= REFRESH_MARGIN_SEC:
            if humidity_blocked:
                return ("none", None, None, "REV4_HUMIDITY_STOP_EXTEND")
            return ("push", target, until, "REV4_EXTENDED")
        return none

    if snap.hold_active:  # a hold we don't own: manual. Price wins only warmward.
        if target is not None and target > snap.cool_setpoint and not humidity_blocked:
            return ("push", target, until, "REV4_ENGAGED_OVER_MANUAL")
        return ("none", None, None, "REV4_MANUAL_HOLD_RESPECTED")

    if target is None:
        return ("none", None, None, "REV4_PRECONDITION_PROGRAM_WARMER")
    if humidity_blocked:
        return ("none", None, None, "REV4_HUMIDITY_BLOCKED_ENGAGE")
    return ("push", target, until, "REV4_ENGAGED")
```

- [ ] **Step 4: Run to verify pass**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_holds.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/holds.py deploy/energy-stack/hvac_scheduler/test_rev4_holds.py
git commit -m "feat(rev4): hold math — warm-only targets, quarter-slot expiry, decide()"
```

---

### Task 7: Seam additions — `ScheduleCoolSp` + `TemporaryHoldUntilTime` (both copies)

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/tcc_client.py` (add two getters to `TCCClimate`, after `get_hold_mode`)
- Modify: `deploy/energy-stack/thermostat_poller/tcc_client.py` (identical edit — documented verbatim duplicate)
- Test: extend `deploy/energy-stack/hvac_scheduler/test_tcc_client.py` (this file survives cutover — the seam is whitelisted infra)

**Interfaces:**
- Produces: `async def get_schedule_cool_f(self) -> Any` (returns `raw_ui_data["ScheduleCoolSp"]`, `None` when absent); `async def get_hold_until_minutes(self) -> Any` (returns `raw_ui_data["TemporaryHoldUntilTime"]`, `None` when absent). Names keep the seam's `_f` convention where applicable (pass-through, no conversion).

- [ ] **Step 1: Write the failing test (append to existing `test_tcc_client.py`, matching its existing fake-device pattern)**

```python
def test_schedule_cool_and_hold_until_read_raw_ui_data():
    import asyncio

    class _Dev:
        raw_ui_data = {"ScheduleCoolSp": 25.5, "TemporaryHoldUntilTime": 1290,
                       "StatusCool": 1}

    class _Client:
        device = _Dev()

    from .tcc_client import TCCClimate
    clim = TCCClimate(_Client())
    assert asyncio.run(clim.get_schedule_cool_f()) == 25.5
    assert asyncio.run(clim.get_hold_until_minutes()) == 1290

    class _Empty:
        raw_ui_data = {}

    class _C2:
        device = _Empty()

    clim2 = TCCClimate(_C2())
    assert asyncio.run(clim2.get_schedule_cool_f()) is None
    assert asyncio.run(clim2.get_hold_until_minutes()) is None
```

- [ ] **Step 2: Run to verify failure** — `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_tcc_client.py -q` → new test FAILS (`AttributeError`).

- [ ] **Step 3: Implement in BOTH `tcc_client.py` copies (insert after `get_hold_mode`)**

```python
    async def get_schedule_cool_f(self) -> Any:
        # Rev 4: the device's current program cool value — the drift anchor and
        # warm-only floor. Present in every uiData read, including mid-hold
        # (verified live 2026-07-03). Pass-through, display unit, no conversion.
        return (self._dev.raw_ui_data or {}).get("ScheduleCoolSp")

    async def get_hold_until_minutes(self) -> Any:
        # Rev 4: the device's dateless hold-until (minutes since midnight).
        # Used ONLY for own-hold record matching (zombie cleanup) — never as a
        # clock (no date; see spec Safety #3).
        return (self._dev.raw_ui_data or {}).get("TemporaryHoldUntilTime")
```

- [ ] **Step 4: Run both services' suites**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_tcc_client.py -q && cd ../thermostat_poller && python -m pytest . -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/tcc_client.py deploy/energy-stack/thermostat_poller/tcc_client.py deploy/energy-stack/hvac_scheduler/test_tcc_client.py
git commit -m "feat(seam): expose ScheduleCoolSp + TemporaryHoldUntilTime (both tcc_client copies)"
```

---

### Task 8: Own-hold record — persistence + lifecycle

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/controller/ownhold.py`
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_ownhold.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) OwnHoldRecord(value: float, until_minutes: int, expiry_utc: str)`; `def load_record(data_dir: str) -> OwnHoldRecord | None`; `def save_record(data_dir: str, rec: OwnHoldRecord) -> None`; `def clear_record(data_dir: str) -> None`. File: `<data_dir>/own_hold.json`; cleared = file contains `null` (so the acceptance test's `json.loads(...) or null` check works and a missing-vs-cleared distinction never matters).
- Consumes: nothing.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json

from .controller.ownhold import OwnHoldRecord, clear_record, load_record, save_record


def test_roundtrip(tmp_path):
    d = str(tmp_path)
    assert load_record(d) is None
    rec = OwnHoldRecord(value=27.0, until_minutes=870, expiry_utc="2026-07-10T19:30:00+00:00")
    save_record(d, rec)
    assert load_record(d) == rec
    clear_record(d)
    assert load_record(d) is None
    assert json.loads((tmp_path / "own_hold.json").read_text()) is None


def test_corrupt_file_reads_as_none(tmp_path):
    (tmp_path / "own_hold.json").write_text("{not json", encoding="utf-8")
    assert load_record(str(tmp_path)) is None
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest test_rev4_ownhold.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Rev 4 own-hold record: what the controller last pushed, on disk, so a
restarted controller can clean up ONLY its own zombie holds. Spec: Safety #3.
The record's expiry_utc carries the DATE the device's dateless until-slot lacks.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

_FILENAME = "own_hold.json"


@dataclass(frozen=True)
class OwnHoldRecord:
    value: float
    until_minutes: int
    expiry_utc: str


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, _FILENAME)


def load_record(data_dir: str) -> OwnHoldRecord | None:
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            doc = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    try:
        return OwnHoldRecord(value=float(doc["value"]),
                             until_minutes=int(doc["until_minutes"]),
                             expiry_utc=str(doc["expiry_utc"]))
    except (KeyError, TypeError, ValueError):
        return None


def save_record(data_dir: str, rec: OwnHoldRecord) -> None:
    with open(_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(asdict(rec), f)


def clear_record(data_dir: str) -> None:
    with open(_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(None, f)
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest test_rev4_ownhold.py -q` → `2 passed`.

- [ ] **Step 5: Commit — end of PR-2**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/ownhold.py deploy/energy-stack/hvac_scheduler/test_rev4_ownhold.py
git commit -m "feat(rev4): persisted own-hold record"
```

---

### Task 9: Device wrapper — ControlSnapshot + push/release

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/controller/device.py`
- Test: append to `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py` (one focused test)

**Interfaces:**
- Produces: `@dataclass(frozen=True) ControlSnapshot(schedule_cool: float | None, cool_setpoint: float, heat_setpoint: float, hold_active: bool, hold_until_minutes: int | None, indoor_temp: float | None, humidity: float | None)`; `class TccClimateAdapter` with `def snapshot()`, `def push(cool, heat, until_minutes)`, `def release()` — the sync facade `ControllerLoop` consumes (matching the fakes), wrapping the async seam: `snapshot()` = `get_climate()` refresh + getters (`get_schedule_cool_f`, `get_cool_setpoint_f`, `get_heat_setpoint_f`, `get_hold_mode() == "Hold Until"`, `get_hold_until_minutes`, `get_current_temperature_f`, `get_humidity`); `push` = `set_heat_setpoint_f(heat)` + `set_cool_setpoint_f(cool)` + `set_hold_until(time(hour=until//60, minute=until%60))`; `release` = `set_hold_mode("Schedule")`. Uses `asyncio.run` internally (the loop is sync-driven per tick).
- Consumes: whitelisted `tcc_client.TCCClient/TCCClimate` (+ Task 7 getters).

- [ ] **Step 1: Failing test (fake TCCClimate-shaped object, assert adapter mapping)**

```python
def test_adapter_maps_seam_to_snapshot():
    import asyncio
    from .controller.device import TccClimateAdapter, ControlSnapshot

    class FakeClim:
        async def get_schedule_cool_f(self): return 25.5
        async def get_cool_setpoint_f(self): return 27.0
        async def get_heat_setpoint_f(self): return 18.5
        async def get_hold_mode(self): return "Hold Until"
        async def get_hold_until_minutes(self): return 1290
        async def get_current_temperature_f(self): return 25.0
        async def get_humidity(self): return 52.0
        pushed = []
        async def set_cool_setpoint_f(self, v): self.pushed.append(("cool", v))
        async def set_heat_setpoint_f(self, v): self.pushed.append(("heat", v))
        async def set_hold_until(self, t): self.pushed.append(("until", t.hour * 60 + t.minute))
        async def set_hold_mode(self, m): self.pushed.append(("mode", m))

    class FakeClient:
        def __init__(self): self.clim = FakeClim()
        async def get_climate(self): return self.clim

    a = TccClimateAdapter(FakeClient())
    s = a.snapshot()
    assert s == ControlSnapshot(25.5, 27.0, 18.5, True, 1290, 25.0, 52.0)
    a.push(29.5, 18.5, 14 * 60 + 30)
    assert ("cool", 29.5) in FakeClim.pushed and ("until", 870) in FakeClim.pushed
    a.release()
    assert ("mode", "Schedule") in FakeClim.pushed
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Rev 4 device facade over the whitelisted TCCClimate seam.
Sync interface (the tick is synchronous logic); one network refresh per snapshot.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import time as dtime


@dataclass(frozen=True)
class ControlSnapshot:
    schedule_cool: float | None
    cool_setpoint: float
    heat_setpoint: float
    hold_active: bool
    hold_until_minutes: int | None
    indoor_temp: float | None
    humidity: float | None


class TccClimateAdapter:
    def __init__(self, client) -> None:
        self._client = client

    def snapshot(self) -> ControlSnapshot:
        async def _read():
            clim = await self._client.get_climate()
            sched = await clim.get_schedule_cool_f()
            return ControlSnapshot(
                schedule_cool=(float(sched) if sched is not None else None),
                cool_setpoint=float(await clim.get_cool_setpoint_f()),
                heat_setpoint=float(await clim.get_heat_setpoint_f()),
                hold_active=(await clim.get_hold_mode()) == "Hold Until",
                hold_until_minutes=await clim.get_hold_until_minutes(),
                indoor_temp=await clim.get_current_temperature_f(),
                humidity=await clim.get_humidity(),
            )
        return asyncio.run(_read())

    def push(self, cool: float, heat: float, until_minutes: int) -> None:
        async def _push():
            clim = await self._client.get_climate()
            await clim.set_heat_setpoint_f(heat)
            await clim.set_cool_setpoint_f(cool)
            await clim.set_hold_until(dtime(hour=until_minutes // 60,
                                            minute=until_minutes % 60))
        asyncio.run(_push())

    def release(self) -> None:
        async def _rel():
            clim = await self._client.get_climate()
            await clim.set_hold_mode("Schedule")
        asyncio.run(_rel())
```

- [ ] **Step 4: Run** — `python -m pytest test_rev4_loop.py -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/device.py deploy/energy-stack/hvac_scheduler/test_rev4_loop.py
git commit -m "feat(rev4): device facade over the TCC seam"
```

---

### Task 10: Telemetry — actions row, arm_mode row, trace lines

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/controller/telemetry.py`
- Test: append two tests to `test_rev4_loop.py`

**Interfaces:**
- Produces: `class InfluxTelemetry(write_api, bucket, unit, config_id, tz)` with `def trace(**kw)` (prints one JSON line, msg `decision_trace.rev4_tick`, transitions at info, holds at debug honoring `SCHEDULER_DECISION_TRACE_VERBOSE`), `def write_action(*, tier, action_label, dry_run, commanded_cool, commanded_heat, schedule_cool, applied, error, hold_expires_at, snapshot_before)` writing the `hvac.actions` contract (Global Constraints — including `baseline_cool` = `schedule_cool`, `drift` = `commanded_cool − schedule_cool`, `humidity_gated`, `setpoint_reason` = the rev 4 reason code, `actual_*` from snapshot), and `def write_arm_mode(now_ct, scheduler_mode)` transcribing rev 3's three-branch contract: `current_arm_at(now_ct)` None → tags `{scheduler_mode}` fields `{mode_actual: "outside-window"}`; arm present and mode ≠ "experiment" → tags `{scheduler_mode, arm}` fields `{mode_actual: f"off-protocol-{scheduler_mode}"}` (the rev 4 production path); the `experiment` branch is out of scope (2027) and not implemented — a comment marks it.
- Consumes: whitelisted `influx_adapter.write_point`, `arm_calendar.current_arm_at`.

- [ ] **Step 1: Failing tests** (fake `write_api` capturing `write(bucket=..., record=Point)` — assert measurement names and key fields via `record.to_line_protocol()` containing `hvac.actions`, `commanded_cool=`, `error=`, `dry_run=`).

```python
def test_influx_telemetry_action_row_contract():
    from .controller.telemetry import InfluxTelemetry
    from .controller.device import ControlSnapshot

    class Cap:
        lines = []
        def write(self, bucket, record): Cap.lines.append(record.to_line_protocol())

    tel = InfluxTelemetry(write_api=Cap(), bucket="energy", unit="C",
                          config_id="abc123", tz_name="America/Chicago")
    snap = ControlSnapshot(25.5, 27.0, 18.5, True, 870, 25.0, 52.0)
    tel.write_action(tier="elevated", action_label="SPIKE", dry_run=False,
                     commanded_cool=27.0, commanded_heat=18.5, schedule_cool=25.5,
                     applied=True, error="", hold_expires_at="2026-07-10T19:30:00+00:00",
                     snapshot_before=snap, setpoint_reason="REV4_ENGAGED",
                     humidity_gated=False)
    lp = Cap.lines[-1]
    assert lp.startswith("hvac.actions,")
    for token in ("commanded_cool=27", "baseline_cool=25.5", "schedule_cool=25.5",
                  "drift=1.5", "applied=1i", 'error=""', "config_id="):
        assert token in lp, token
    assert "dry_run=false" in lp  # tag


def test_arm_mode_row_production_branch():
    from .controller.telemetry import InfluxTelemetry
    from datetime import datetime
    from zoneinfo import ZoneInfo

    class Cap:
        lines = []
        def write(self, bucket, record): Cap.lines.append(record.to_line_protocol())

    tel = InfluxTelemetry(write_api=Cap(), bucket="energy", unit="C",
                          config_id="abc", tz_name="America/Chicago")
    tel.write_arm_mode(datetime(2026, 7, 10, 14, 0, tzinfo=ZoneInfo("America/Chicago")),
                       scheduler_mode="production")
    assert Cap.lines[-1].startswith("hvac.arm_mode,")
    assert "mode_actual=" in Cap.lines[-1]
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""Rev 4 telemetry: hvac.actions rows (field contract preserved for live
consumers: telegram-notifier filters `error`, thermostat-poller filtered
`dry_run` pre-retirement), hvac.arm_mode liveness rows (watchdog contract),
and decision-trace JSON lines.
Whitelisted imports only: influx_adapter.write_point, arm_calendar.current_arm_at.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..arm_calendar import current_arm_at
from ..influx_adapter import write_point

_VERBOSE = os.environ.get("SCHEDULER_DECISION_TRACE_VERBOSE", "false").lower() == "true"
_TRANSITION_REASONS = {
    "REV4_UPGRADED_TO_ELEVATED", "REV4_UPGRADED_TO_SCARCITY",
    "REV4_DOWNGRADED_TO_ELEVATED", "REV4_RELEASED_TO_NORMAL",
    "REV4_RELEASED_STALE_BACKSTOP", "REV4_ZOMBIE_RELEASED",
    "REV4_ENGAGED", "REV4_ENGAGED_OVER_MANUAL", "REV4_CORRECTED",
    "REV4_WARM_ONLY_RELEASE",
}


def _log(level: str, msg: str, **fields) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, default=str), flush=True)


class InfluxTelemetry:
    def __init__(self, *, write_api, bucket: str, unit: str, config_id: str,
                 tz_name: str) -> None:
        self.write_api = write_api
        self.bucket = bucket
        self.unit = unit
        self.config_id = config_id
        self.tz = ZoneInfo(tz_name)

    def trace(self, **kw) -> None:
        reason = kw.get("reason_code", "")
        level = "info" if reason in _TRANSITION_REASONS else "debug"
        if level == "debug" and not _VERBOSE:
            return
        _log(level, "decision_trace.rev4_tick", **kw)

    def write_action(self, *, tier: str, action_label: str, dry_run: bool,
                     commanded_cool: float, commanded_heat: float,
                     schedule_cool: float, applied: bool, error: str,
                     hold_expires_at: str, snapshot_before,
                     setpoint_reason: str, humidity_gated: bool) -> None:
        tags = {"unit": self.unit, "tier": tier, "action_label": action_label,
                "dry_run": "true" if dry_run else "false"}
        fields = {
            "commanded_cool": float(commanded_cool),
            "commanded_heat": float(commanded_heat),
            "baseline_cool": float(schedule_cool),
            "schedule_cool": float(schedule_cool),
            "drift": float(commanded_cool) - float(schedule_cool),
            "humidity_gated": int(humidity_gated),
            "setpoint_reason": setpoint_reason,
            "applied": int(applied),
            "error": error or "",
            "config_id": self.config_id,
            "hold_expires_at": hold_expires_at or "",
            "actual_indoor_temp": float(snapshot_before.indoor_temp or 0),
            "actual_cool_before": float(snapshot_before.cool_setpoint),
            "actual_heat_before": float(snapshot_before.heat_setpoint),
            "actual_humidity": float(snapshot_before.humidity or 0),
        }
        write_point(self.write_api, self.bucket, "hvac.actions",
                    tags=tags, fields=fields)

    def write_arm_mode(self, now_ct: datetime, scheduler_mode: str) -> None:
        arm = current_arm_at(now_ct)
        if arm is None:
            write_point(self.write_api, self.bucket, "hvac.arm_mode",
                        tags={"scheduler_mode": scheduler_mode},
                        fields={"mode_actual": "outside-window"}, time=now_ct)
            return
        # `experiment` mode (arm-gated A/B) is the retained 2027 layer; rev 4
        # runs shadow|production only, so every in-window row is off-protocol.
        write_point(self.write_api, self.bucket, "hvac.arm_mode",
                    tags={"scheduler_mode": scheduler_mode, "arm": arm},
                    fields={"mode_actual": f"off-protocol-{scheduler_mode}"},
                    time=now_ct)
```

- [ ] **Step 4: Run** — `python -m pytest test_rev4_loop.py -q` → pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/telemetry.py deploy/energy-stack/hvac_scheduler/test_rev4_loop.py
git commit -m "feat(rev4): telemetry — actions/arm_mode contracts + trace lines"
```

---

### Task 11: Full loop wiring — spike path, cleanup, humidity, production entrypoint; acceptance goes green

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/controller/loop.py` (complete `tick`; add `run_forever`)
- Modify: `deploy/energy-stack/hvac_scheduler/controller/__main__.py` (real wiring)
- Modify: `deploy/energy-stack/hvac_scheduler/test_rev4_acceptance.py` (remove the xfail marker — final step, only when green)
- Test: extend `test_rev4_loop.py` (humidity hysteresis + shadow-gate tests)

**Interfaces:**
- Consumes: everything above. The `tick` orchestration, exactly:
  1. price → `evaluate_tier` → trace (always).
  2. `own = load_record(data_dir)`; device read IF `tier != normal or own is not None` → `climate.snapshot()`.
  3. humidity hysteresis state: `humidity_blocked` set when snapshot humidity is None or ≥ `rh_max_pct`; cleared when < `rh_clear_pct`.
  4. `holds.decide(...)` → act: `push` → (production only) `climate.push(cool, heat_floor, until)`, then `save_record` with `expiry_utc` computed from the pushed until-slot on today's date in local tz (crossing midnight: if slot < now-slot, tomorrow) converted to UTC; `release` → (production only) `climate.release()`, then `clear_record` **only when the release write did not raise** (zombie case) — on failure the action row carries `applied=0, error=...` and the record stays on disk, so the decide() rule re-fires on subsequent normal ticks (the retry).
  5. Record hygiene: when a snapshot was taken and it shows no hold or a non-matching hold while a record exists and the decision was `none` → `clear_record` (normally-lapsed hold; spec Safety #3).
  6. `write_action` on every push/release attempt (applied/error per outcome; `dry_run=True` rows in shadow mode with no device call).
  7. arm_mode row throttled to ~5-min cadence (`_last_arm_write` state).
  8. `run_forever()`: tick at second :10 each minute; touch `/tmp/last_tick_ok` after each completed tick (Dockerfile healthcheck contract); SIGTERM → log + clean exit.
- Produces: green acceptance test; `python -m hvac_scheduler.controller` runs production wiring: env (`SCHEDULER_MODE`, `SCHEDULER_TZ`, `TCC_*`, `INFLUXDB_*`, `CONTROLLER_CONFIG_FILE`, `TEMP_SCALE`), `InfluxDBClient`, `TCCClient` + `TccClimateAdapter`, `InfluxTelemetry`, `fetch_price` adapter (`latest(now)` → `fetch_price(query_api, bucket, now)` unpacked to the 3-tuple), `/data` as data_dir.

Complete `tick` implementation (replace the skeleton's trailing comment):

```python
        own = None
        snap = None
        from .ownhold import OwnHoldRecord, clear_record, load_record, save_record
        from . import holds
        own = load_record(self.data_dir)

        needs_device = self.tier_state.tier != tiers.NORMAL or own is not None
        if needs_device:
            snap = self.climate.snapshot()
            self._update_humidity_gate(snap)

        if snap is not None:
            now_local = now_utc.astimezone(self.tz)
            kind, cool, until, dreason = holds.decide(
                self.tier_state.tier, snap, own, self.cfg,
                now_utc, now_local, self.humidity_blocked)
            if kind == "push":
                applied, err = self._apply_push(cool, until)
                if applied:
                    save_record(self.data_dir, OwnHoldRecord(
                        value=cool, until_minutes=until,
                        expiry_utc=self._slot_to_utc(until, now_local).isoformat()))
                self.telemetry.write_action(
                    tier=self.tier_state.tier, action_label="SPIKE",
                    dry_run=(self.mode == "shadow"), commanded_cool=cool,
                    commanded_heat=self.cfg.heat_floor,
                    schedule_cool=snap.schedule_cool or 0.0, applied=applied,
                    error=err, hold_expires_at=self._slot_to_utc(until, now_local).isoformat(),
                    snapshot_before=snap, setpoint_reason=dreason,
                    humidity_gated=self.humidity_blocked)
            elif kind == "release":
                applied, err = self._apply_release()
                if applied:
                    clear_record(self.data_dir)
                self.telemetry.write_action(
                    tier=self.tier_state.tier, action_label="RELEASE",
                    dry_run=(self.mode == "shadow"),
                    commanded_cool=snap.schedule_cool or 0.0,
                    commanded_heat=self.cfg.heat_floor,
                    schedule_cool=snap.schedule_cool or 0.0, applied=applied,
                    error=err, hold_expires_at="", snapshot_before=snap,
                    setpoint_reason=dreason, humidity_gated=self.humidity_blocked)
            else:
                # record hygiene: normally-lapsed or foreign hold -> drop stale record
                if own is not None and not holds._matches_own(own, snap):
                    clear_record(self.data_dir)

        self._maybe_write_arm_mode(now_utc)
```

With helpers (same class):

```python
    def _apply_push(self, cool: float, until: int) -> tuple[bool, str]:
        if self.mode == "shadow":
            return False, ""
        try:
            self.climate.push(cool, self.cfg.heat_floor, until)
            return True, ""
        except Exception as exc:  # transient TCC errors self-heal next tick
            return False, f"{type(exc).__name__}: {exc}"

    def _apply_release(self) -> tuple[bool, str]:
        if self.mode == "shadow":
            return False, ""
        try:
            self.climate.release()
            return True, ""
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _update_humidity_gate(self, snap) -> None:
        rh = snap.humidity
        if rh is None or rh >= self.cfg.rh_max_pct:
            self.humidity_blocked = True
        elif rh < self.cfg.rh_clear_pct:
            self.humidity_blocked = False

    def _slot_to_utc(self, until_minutes: int, now_local):
        from datetime import timedelta, timezone
        base = now_local.replace(hour=until_minutes // 60,
                                 minute=until_minutes % 60, second=0, microsecond=0)
        if base <= now_local:
            base += timedelta(days=1)
        return base.astimezone(timezone.utc)
```

(`humidity_blocked = False` and `_last_arm_write = None` initialized in `__init__`; `_maybe_write_arm_mode` writes via `self.telemetry.write_arm_mode(now_utc.astimezone(self.tz), self.mode)` at most every 300 s. Note for the shadow path: `_apply_*` returns `applied=False, error=""`, the record is NOT saved, matching "shadow never writes" — the trace still shows the would-push via `write_action(dry_run=True)`.)

- [ ] **Step 1: Add loop tests** — humidity hysteresis (blocked at 61, stays blocked at 59, clears at 57.9), shadow gate (production=False → `dev.pushes == []`, action row has `dry_run=True/applied=0`), record hygiene (record + no device hold → record cleared).
- [ ] **Step 2: Run all rev4 unit tests** — green.
- [ ] **Step 3: Run the acceptance test** — `python -m pytest test_rev4_acceptance.py -q`. With strict xfail, a PASSING test reports **XPASS(strict) = FAILURE of the suite**: that is the signal to remove the marker.
- [ ] **Step 4: Delete the `pytestmark = pytest.mark.xfail(...)` line** from `test_rev4_acceptance.py`.
- [ ] **Step 5: Full service suite** — `cd deploy/energy-stack/hvac_scheduler && python -m pytest . -q` → everything green (rev 3 tests included — they still pass; nothing of rev 3 was touched).
- [ ] **Step 6: Wire `__main__.py`** — replace the stub:

```python
"""Entrypoint: python -m hvac_scheduler.controller."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

from ..tcc_client import TCCClient
from .config import ConfigError, load_config
from .device import TccClimateAdapter
from .loop import ControllerLoop
from .pricing import fetch_price
from .telemetry import InfluxTelemetry


class InfluxPriceSource:
    def __init__(self, query_api, bucket: str) -> None:
        self._api, self._bucket = query_api, bucket

    def latest(self, now_utc: datetime):
        s = fetch_price(self._api, self._bucket, now_utc)
        return None if s is None else (s.cents, s.bucket_time_utc, s.age_sec)


def main() -> int:
    mode = os.environ.get("SCHEDULER_MODE", "shadow")
    if mode not in ("shadow", "production"):
        print(f"invalid SCHEDULER_MODE: {mode!r}", flush=True)
        return 2
    try:
        cfg = load_config(os.environ["CONTROLLER_CONFIG_FILE"],
                          temp_scale_env=os.environ.get("TEMP_SCALE", "C"))
    except (KeyError, ConfigError) as exc:
        print(f"config error: {exc}", flush=True)
        return 2

    influx = InfluxDBClient(url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
                            token=os.environ["INFLUXDB_TOKEN"],
                            org=os.environ["INFLUXDB_ORG"])
    bucket = os.environ["INFLUXDB_BUCKET"]
    tz_name = os.environ.get("SCHEDULER_TZ", "America/Chicago")

    loop = ControllerLoop(
        cfg=cfg,
        price_source=InfluxPriceSource(influx.query_api(), bucket),
        climate=TccClimateAdapter(TCCClient(
            os.environ["TCC_USERNAME"], os.environ["TCC_PASSWORD"],
            int(os.environ.get("TCC_DEVICE_ID", "4750378")))),
        telemetry=InfluxTelemetry(
            write_api=influx.write_api(write_options=SYNCHRONOUS),
            bucket=bucket, unit=cfg.temp_scale, config_id=cfg.config_id,
            tz_name=tz_name),
        mode=mode, tz_name=tz_name, data_dir="/data",
    )
    loop.run_forever()   # 60s ticks at second :10; touches /tmp/last_tick_ok
    return 0


if __name__ == "__main__":
    sys.exit(main())
```
- [ ] **Step 7: Commit — end of PR-3**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/ deploy/energy-stack/hvac_scheduler/test_rev4_*.py
git commit -m "feat(rev4): full loop wiring — acceptance test passes, xfail removed"
```

---

### Task 12: Notifier — controller-down beacon alert

**Files:**
- Modify: `deploy/energy-stack/telegram_notifier/app.py` (new check + register in alert loop beside `check_hvac_action_errors`)
- Test: `deploy/energy-stack/telegram_notifier/` existing test file pattern (add `test_check_controller_down` beside existing check tests)

**Interfaces:**
- Produces: `def check_controller_down(query_api: Any, bucket: str) -> list[Alert]` — fires when a `hvac.heartbeat` row with `controller_alive=false` exists in the last 10 min (the watchdog's down-beacon; absence = healthy). Alert key `"controller_down"`, text: `🛑 <b>HVAC controller DOWN</b> — watchdog beacon active. Device may be carrying a stale hold; it reverts to its program within the hold TTL, but check <code>docker compose ps hvac-scheduler</code>.` Registered in the same checks list the alert loop iterates; the existing 30-min dedupe applies.

- [ ] **Step 1: Failing test**

```python
def test_check_controller_down_fires_on_beacon():
    from .app import check_controller_down

    class _Rec:
        def __init__(self, v): self._v = v
        @property
        def values(self): return {"_value": self._v}

    class _Table:
        def __init__(self, recs): self.records = recs

    class Api:
        def __init__(self, rows): self._rows = rows
        def query(self, flux): return [_Table(self._rows)] if self._rows else []

    class _R:
        def __init__(self, v): self.values = {"_value": v}

    alerts = check_controller_down(Api([_R(False)]), "energy")
    assert len(alerts) == 1 and alerts[0].key == "controller_down"
    assert check_controller_down(Api([]), "energy") == []
```

(Adapt the fake row shape to whatever `fetch_one` consumes in this file — match the existing check tests' fake pattern exactly.)

- [ ] **Step 2: Implement** (flux mirrors `check_hvac_action_errors` shape):

```python
def check_controller_down(query_api: Any, bucket: str) -> list[Alert]:
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -10m)
  |> filter(fn: (r) => r._measurement == "hvac.heartbeat" and r._field == "controller_alive")
  |> filter(fn: (r) => r._value == false)
  |> last()
'''
    rows = fetch_one(query_api, flux)
    if not rows:
        return []
    return [Alert(
        key="controller_down",
        text=("🛑 <b>HVAC controller DOWN</b> — watchdog beacon active.\n"
              "Device may be carrying a stale hold; it reverts to its program "
              "within the hold TTL. Check <code>docker compose ps hvac-scheduler</code>."),
    )]
```

- [ ] **Step 3: Register it** in the alert-loop checks list (same place `check_hvac_action_errors` is called).
- [ ] **Step 4: Run** — `cd deploy/energy-stack/telegram_notifier && python -m pytest . -q` → green.
- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/telegram_notifier/
git commit -m "feat(alerts): controller-down beacon alert (closes the 2026-07-03 silent-death gap)"
```

---

### Task 13: Notifier — push-failure alert becomes N-consecutive

**Files:**
- Modify: `deploy/energy-stack/telegram_notifier/app.py` (`check_hvac_action_errors`)
- Test: same file as Task 12's test.

**Interfaces:**
- Produces: `check_hvac_action_errors` reworked: query last `PUSH_FAILURE_ALERT_N` (env, default 3) `hvac.actions` rows' `error` field (sorted desc, `|> limit(n: 3)` after `sort(columns: ["_time"], desc: true)`); alert only when **all N** are non-empty (excluding the two `hvac_mode_not_cooling` benign strings, preserved from the current filter). Key stays `hvac_error:<first-40-chars>`. This kills the single-transient double-bark observed 2026-07-05 while catching genuine consecutive failures (a missed spike engage).

- [ ] **Step 1: Failing tests** (same fake-api pattern as Task 12; rows are the last-3 `error` values, newest first):

```python
def test_push_failure_alert_requires_three_consecutive():
    from .app import check_hvac_action_errors

    def api_with(errors):  # newest-first error field values of the last 3 rows
        return _make_fake_api_rows(errors)  # reuse this file's fake helper

    # one transient followed by a success: NO alert (kills the 07-05 double-bark)
    assert check_hvac_action_errors(api_with(["", "TimeoutError: ", ""]), "energy") == []
    # three consecutive failures: one alert
    alerts = check_hvac_action_errors(
        api_with(["TimeoutError: ", "TimeoutError: ", "TimeoutError: "]), "energy")
    assert len(alerts) == 1 and alerts[0].key.startswith("hvac_error:")
    # benign not-cooling strings never count
    assert check_hvac_action_errors(
        api_with(["hvac_mode_not_cooling ('Off')"] * 3), "energy") == []
```

- [ ] **Step 2: Implement** — flux fetches the last `PUSH_FAILURE_ALERT_N` (env, default 3) `hvac.actions` `error` rows (`sort desc` + `limit(n)`); alert only when all N are non-empty after excluding the two benign `hvac_mode_not_cooling` strings (preserve the existing exclusion filter); key stays `hvac_error:<first-40-chars>`.
- [ ] **Step 3: Run notifier suite** — green.
- [ ] **Step 4: Commit**

```bash
git add deploy/energy-stack/telegram_notifier/
git commit -m "feat(alerts): hvac push-failure alert requires N consecutive failures (default 3)"
```

---

### Task 14: Poller — retire override detection

**Files:**
- Modify: `deploy/energy-stack/thermostat_poller/poller.py` — remove `classify_override`, the `hvac.overrides` write, the `override_detected`/`override` fields from the poll row/log, `OVERRIDE_GRACE_MIN` config, and `fetch_last_action`.
- Modify: `deploy/energy-stack/docker-compose.yml` — remove `OVERRIDE_GRACE_MIN` if present in the poller env block.
- Test: update `deploy/energy-stack/thermostat_poller/` tests: delete `classify_override` tests; assert the poll row no longer carries override fields.

Spec §Telemetry: under spike-only the `hvac.actions` row it compared against is days old or absent, and manual holds are first-class operator action, not "overrides". Remove rather than emit nonsense.

- [ ] **Step 1: Delete + adjust tests first** (they fail against current code where override fields still exist — inverted TDD is fine for a removal: write the post-removal assertions, watch them fail, remove code, watch them pass).
- [ ] **Step 2: Remove the code paths.** `grep -n "override" poller.py` must return only comments/docstring history notes, ideally nothing.
- [ ] **Step 3: Run poller suite** — `cd deploy/energy-stack/thermostat_poller && python -m pytest . -q` → green.
- [ ] **Step 4: Commit — end of PR-4**

```bash
git add deploy/energy-stack/thermostat_poller/ deploy/energy-stack/docker-compose.yml
git commit -m "refactor(poller): retire override detection (manual holds are first-class under rev 4)"
```

---

### Task 15: Cutover — entrypoint swap, rev 3 deletion, live config

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/commissioning-controller-rev4.yaml` → content below, then **rename over** `commissioning-controller.yaml` in this task (compose mount path stays unchanged).
- Modify: `deploy/energy-stack/hvac_scheduler/Dockerfile` — `CMD ["python", "-m", "hvac_scheduler.controller"]`.
- Delete: `app.py`, `price_overlay.py`, `controller_core.py`, `controller_config.py`, `decision_codes.py`, and rev 3 test files listed in §File Structure.
- Modify: `docs/SERVICES.md` (hvac-scheduler entry describes rev 4), archive this plan per repo convention on merge.

The live yaml (seeds from the spec — the operator tunes at will afterward):

```yaml
# Rev 4 spike-only controller config — the experimental surface (tune freely).
# Values are first-deploy seeds; rationale in the spec (rev 4). temp_scale C.
temp_scale: C
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}
elevated_offset: 1.5     # ≈ old +3F; 1.0 coasted away <1h at heat-wave load
scarcity_absolute: 29.5  # hottest the house may get (85F); watch overshoot
heat_floor: 18.5
humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}  # = 65/62 real; CTK04 reads ~4.4 low vs ch2
hold_ttl_minutes: 30
release_confirm_buckets: 2
stale_release_minutes: 30
```

- [ ] **Step 1: Wire-up audit BEFORE deleting (memory: verify-production-wireup).** `git grep -nE "hvac_scheduler\.(app|price_overlay|controller_core|controller_config|decision_codes)"` and `git grep -n "from .app import\|from .price_overlay"` — every hit must be a file being deleted in this task, the knowledge graph, or docs. Also `git grep -n "hvac.overrides"` (cockpit/grafana refs are observer-rebuild scope, note them in the PR body, don't fix here).
- [ ] **Step 2: Swap Dockerfile CMD; replace the yaml; delete the rev 3 files and tests.**
- [ ] **Step 3: Full per-service suites:** `bash deploy/energy-stack/run_tests.sh` → all green (rev 4 tests + surviving infra tests: `test_tcc_client.py`, `test_freshness.py`, `test_influx_adapter.py`).
- [ ] **Step 4: Local container build check:** `cd deploy/energy-stack && docker compose build hvac-scheduler` (or on the Pi at deploy) — image builds, CMD resolves (`python -c "import hvac_scheduler.controller.__main__"` in an RUN check is acceptable).
- [ ] **Step 5: Update `docs/SERVICES.md` hvac-scheduler entry** (three tiers, spike-only, no schedule copy, own-hold cleanup, alert pair).
- [ ] **Step 6: Commit — PR-5. Merging = deploying rev 4 in `SCHEDULER_MODE` as set on the Pi.** Coordinate with the operator: the Pi `.env` stays `production` (rev 4 boots straight into production) **only if** the operator wants the go-active gates run same-day; otherwise flip the Pi `.env` to `shadow` before merging and run gates per the checklist below.

```bash
git add -A deploy/energy-stack/hvac_scheduler/ docs/SERVICES.md
git commit -m "feat(rev4)!: cutover — spike-only controller replaces rev 3"
```

---

## Go-active gates (operator checklist — run after PR-5 merges; spec §Go-active)

Ordering: flip/confirm `SCHEDULER_MODE=production` with the operator watching, then gates 1–4 as the first-production-day checklist. Force a spike without waiting for the market by temporarily lowering `elevated_at` in the yaml (config-as-surface; `config_id` records the epoch).

1. [ ] **Clean kill test** (the 2026-07-03 attempt is VOID): engage a spike hold (forced via lowered `elevated_at`), then `docker compose stop hvac-scheduler` **with the restore parked on the Pi first**: `echo "cd ~/energy-stack && docker compose start hvac-scheduler" | at now + 90 minutes`. Watch the device drop the hold at its quarter-slot expiry and resume its program unaided. Then confirm the restarted controller behaves (normal tier: no writes).
2. [ ] **Spike round-trip:** one real (or forced) engage → device shows the hold → tier releases on 2 cheap buckets → no release write → device lapses at TTL → program resumes.
3. [ ] **Zombie cleanup:** verify the record-clearing path live (record exists after a lapsed hold → next normal tick clears it, no device write). Full zombie-release path: opportunistic (next real power event), or optional forced test (cut thermostat power across a short-TTL hold's expiry).
4. [ ] **Alert pair live-fire:** stop the scheduler ≥10 min → down-beacon Telegram arrives (then restart); simulate 3 consecutive push failures only if convenient (unit-tested otherwise).
5. [ ] **RH guard forced-fire:** temporarily set `rh_max_pct` below current thermostat RH during a forced spike → hold stops extending, lapses; revert config.
6. [ ] Revert any forced thresholds; confirm `config_id` change is visible in telemetry; update the spec's status header to `active (rev 4 live)`; archive this plan to `docs/superpowers/plans/archive/` (or repo convention) in the closing PR.

## Feature-complete definition (per repo standing rule)

1. `test_rev4_acceptance.py` passes with no xfail marker and no scaffolding (done at Task 11).
2. All phases implemented (Tasks 1–15) or explicitly descoped with a note here.
3. Go-active gates 1–5 pass, or expected-empties are reason-coded here.
4. Docs updated (SERVICES.md), plan archived on the closing merge.
