---
date: 2026-07-11
updated: 2026-07-18
owner: chris
status: in-progress
role-label: code-team
---

# TCC I/O + Liveness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove a redundant per-push TCC round-trip and make the device writes session-resilient; stop transient TCC timeouts from logging at `error`; and (separately) make the `hvac.arm_mode` liveness beacon independent of device I/O — so the controller is cleaner, self-diagnosing, and legible for the 2027 experiment.

**Architecture:** Small, targeted changes to the rev-4 controller's device-I/O and telemetry layer. No change to the tier machine, hold math, or the config surface. Split into **two PRs** because an adversarial review showed the liveness decoupling is only safe when paired with a device-reachability alarm:

- **PR 1 (this document, executing now):** Task 1 + Task 3 — self-contained, no alerting gap.
- **PR 2 (separate plan, later):** Task 2 + a device-stall alarm — shipped together.

**Tech Stack:** Python 3.13, `aiosomecomfort==0.0.36` (async TCC client) over `aiohttp`, `influxdb_client`, pytest (`asyncio_mode=auto`). Runtime is Docker Compose on Pi-lab.

## Why (evidence)

Live analysis of the rev-4-live window (2026-07-06 → 07-10) found ~24 `rev4_tick_failed` TimeoutErrors/day — ~100% bare 30s TCC request timeouts (aiosomecomfort's hard-coded `self._timeout = 30`), hitting `thermostat-poller` independently too → TCC-server-side latency, not a controller bug. Control impact is near-zero (device-side timed holds + 12-min freshness + retry-next-tick). Two concrete defects fall out:

1. **Redundant second `refresh()` on every push tick.** `device.py` `push()`/`release()` call `get_climate()` (a network refresh) even though `snapshot()` refreshed the same session microseconds earlier in the same tick. Proven safe to drop: the setters read only cached `device._data`; `loop.py` guarantees push/release run only after a successful `snapshot()` (`if snap is not None`). Dropping it also removes the pre-setter reauth checkpoint — so this plan **reauth-wraps the writes** instead, which is strictly more robust than today (a 401 mid-write now self-heals in-tick rather than failing the push).
2. **Transient timeouts log at `error`.** They self-heal next tick; a SUSTAINED outage is caught out-of-band (watchdog `arm_mode` staleness → down-beacon; thermostat-poller silence). Logging them at `error` buries genuine faults. PR #131 (2026-07-17) already logs transient poller fetch failures at `warn` — this aligns the controller with that convention.

**Adversarial-review correction (do not restore the earlier "no blind spot" claim):** a `snapshot()` timeout aborts the tick *before* any `hvac.actions` row is written, so `check_hvac_action_errors` does NOT fire in that mode. The device-reachability net for a snapshot-timeout outage is **`telegram_notifier.check_poller_silence` on `thermostat-poller` (`hvac.thermostat`, 30-min tolerance)** — NOT the action-error or down-beacon alerts. This is why decoupling `arm_mode` (Task 2) is deferred to PR 2 and paired with a fast, correctly-labeled device-stall alarm: decoupling alone would defer spike-time TCC-outage detection from ~10 min (today's mislabeled down-beacon) to ~30 min (poller silence).

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from the spec / AGENTS.md.

- **Config is °C-native; temps sit on the 0.5°C grid.** No unit conversion in the device seam.
- **Async seam:** never `asyncio.run` inside a tick; ONE event loop for the process lifetime (the TCC `aiohttp` session binds to the first loop it sees).
- **`tcc_client.py` is a VERBATIM DUAL-COPY** across `deploy/energy-stack/hvac_scheduler/tcc_client.py` and `deploy/energy-stack/thermostat_poller/tcc_client.py`. Every edit is applied **identically to both**; after editing they must `diff` clean. (Verified identical at plan time.)
- **Preserve `hvac.actions` `applied` / `error` / `dry_run` fields** — telegram-notifier's action-error alert filters `error`.
- **Tests run per-service:** `cd deploy/energy-stack/<service>/ && python -m pytest .`, or the whole stack via `bash deploy/energy-stack/run_tests.sh`. **NEVER** `python -m pytest deploy/energy-stack` from the repo root.
- **Branching:** never push to `main`; branch off `main` (this plan is on `harden/tcc-io-liveness`); every PR `--base main`, no stacking; agent stops at `gh pr create`. No `--no-verify`.

## File Structure (PR 1)

- `deploy/energy-stack/hvac_scheduler/tcc_client.py` — `TCCClient.get_climate` gains `refresh: bool = True` (guards the network refresh). Backward-compatible.
- `deploy/energy-stack/thermostat_poller/tcc_client.py` — identical edit (dual-copy). Poller calls `get_climate()` with no arg → default `refresh=True` → unchanged.
- `deploy/energy-stack/hvac_scheduler/controller/device.py` — `push()`/`release()` pass `refresh=False` and route each write through `call_with_reauth`.
- `deploy/energy-stack/hvac_scheduler/controller/loop.py` — add module helper `_tick_failure_log`; use it in `run_forever`'s except to log `TimeoutError` at `warn`.
- `deploy/energy-stack/hvac_scheduler/test_tcc_client.py` — no-refresh test.
- `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py` — update the adapter fake; add the reauth-routing test and the tick-failure-log test.

---

### Task 1: Drop the redundant refresh + reauth-wrap the writes

**Spec:** §Runtime "Scoped device reads … no-needless-thermostat-read short-circuit" (drop the redundant read); §Telemetry alerting intent (make the write path resilient for an unattended season).

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/tcc_client.py` (`TCCClient.get_climate`, ~lines 192-202)
- Modify: `deploy/energy-stack/thermostat_poller/tcc_client.py` (same method — identical edit)
- Modify: `deploy/energy-stack/hvac_scheduler/controller/device.py` (`push`, `release`, lines 46-55)
- Test: `deploy/energy-stack/hvac_scheduler/test_tcc_client.py`
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py`

**Interfaces:**
- Produces: `TCCClient.get_climate(self, refresh: bool = True) -> TCCClimate`; `refresh=False` returns a climate bound to the already-refreshed session with no network read.
- Consumes: `TccClimateAdapter.push/release` call `get_climate(refresh=False)` then route each write through `self._client.call_with_reauth(lambda: ...)`.

- [ ] **Step 1: Failing test — no-refresh path (test_tcc_client.py)**

```python
async def test_get_climate_refresh_false_skips_network_read() -> None:
    """push()/release() already have a fresh session from snapshot() this tick,
    so get_climate(refresh=False) must NOT issue another device.refresh()."""
    client = TCCClient("u", "p", 4750378)
    client._client = MagicMock()
    dev = _fake_device()
    dev.refresh = AsyncMock()
    client.device = dev
    climate = await client.get_climate(refresh=False)
    dev.refresh.assert_not_awaited()
    assert climate._dev is dev
```

- [ ] **Step 2: Run it, watch it fail**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_tcc_client.py::test_get_climate_refresh_false_skips_network_read -v`
Expected: FAIL — `TypeError: get_climate() got an unexpected keyword argument 'refresh'`.

- [ ] **Step 3: Add the `refresh` param (both tcc_client.py copies, identical)**

Replace `get_climate` with:

```python
    async def get_climate(self, refresh: bool = True) -> TCCClimate:
        if self._client is None or self.device is None:
            await self._login()
        # refresh() IS the network read for TCC — wrap it in reauth so a dead
        # session re-logs in. Callers that already refreshed this tick
        # (push()/release() run only after snapshot() per loop.py's
        # `if snap is not None` guard) pass refresh=False to skip the redundant
        # round-trip; the setters read only cached device._data.
        if refresh:
            await self.call_with_reauth(lambda: self.device.refresh())
        return TCCClimate(self)
```

Then verify: `diff deploy/energy-stack/hvac_scheduler/tcc_client.py deploy/energy-stack/thermostat_poller/tcc_client.py` → no output.

- [ ] **Step 4: Reauth-wrap the writes (device.py push/release)**

```python
    async def push(self, cool: float, heat: float, until_minutes: int) -> None:
        # snapshot() refreshed the session this tick (loop.py runs push only
        # after a successful snapshot); reuse it — no redundant refresh. Wrap
        # each write in reauth so a session that 401-expires mid-sequence
        # self-heals this tick instead of failing the push (setters are
        # otherwise unwrapped).
        clim = await self._client.get_climate(refresh=False)
        await self._client.call_with_reauth(lambda: clim.set_heat_setpoint_f(heat))
        await self._client.call_with_reauth(lambda: clim.set_cool_setpoint_f(cool))
        await self._client.call_with_reauth(
            lambda: clim.set_hold_until(dtime(hour=until_minutes // 60,
                                              minute=until_minutes % 60)))

    async def release(self) -> None:
        clim = await self._client.get_climate(refresh=False)
        await self._client.call_with_reauth(lambda: clim.set_hold_mode("Schedule"))
```

- [ ] **Step 5: Update the existing adapter fake + add the reauth-routing test (test_rev4_loop.py)**

In `test_adapter_maps_seam_to_snapshot`, the `FakeClient` must accept the new kwarg and expose `call_with_reauth`:

```python
    class FakeClient:
        def __init__(self): self.clim = FakeClim()
        async def get_climate(self, refresh=True): return self.clim
        async def call_with_reauth(self, fn): return await fn()
```

Add a new test:

```python
def test_push_and_release_route_writes_through_reauth():
    import asyncio
    from .controller.device import TccClimateAdapter

    class FakeClim:
        def __init__(self): self.calls = []
        async def set_heat_setpoint_f(self, v): self.calls.append(("heat", v))
        async def set_cool_setpoint_f(self, v): self.calls.append(("cool", v))
        async def set_hold_until(self, t): self.calls.append(("until", t.hour * 60 + t.minute))
        async def set_hold_mode(self, m): self.calls.append(("mode", m))

    class FakeClient:
        def __init__(self):
            self.clim = FakeClim(); self.reauth_count = 0; self.refresh_requested = None
        async def get_climate(self, refresh=True):
            self.refresh_requested = refresh; return self.clim
        async def call_with_reauth(self, fn):
            self.reauth_count += 1; return await fn()

    c = FakeClient(); a = TccClimateAdapter(c)
    asyncio.run(a.push(29.5, 18.5, 870))
    assert c.refresh_requested is False          # no redundant refresh
    assert c.reauth_count == 3                    # each write wrapped
    assert ("cool", 29.5) in c.clim.calls and ("until", 870) in c.clim.calls
    asyncio.run(a.release())
    assert ("mode", "Schedule") in c.clim.calls
    assert c.reauth_count == 4                     # release write also wrapped
```

- [ ] **Step 6: Run the affected tests green**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_tcc_client.py test_rev4_loop.py -v`
Expected: PASS all (incl. still-green `test_get_climate_refreshes_inside_reauth`, `test_adapter_maps_seam_to_snapshot`).

- [ ] **Step 7: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/tcc_client.py \
        deploy/energy-stack/thermostat_poller/tcc_client.py \
        deploy/energy-stack/hvac_scheduler/controller/device.py \
        deploy/energy-stack/hvac_scheduler/test_tcc_client.py \
        deploy/energy-stack/hvac_scheduler/test_rev4_loop.py
git commit -m "perf(hvac): drop redundant per-push TCC refresh; reauth-wrap the writes"
```

---

### Task 3: Reclassify transient TCC timeouts to `warn`

**Spec:** §Telemetry "Alerting" — sustained faults are covered out-of-band, so a lone self-healing timeout is not an `error`. Aligns with PR #131's poller-fetch-failure convention.

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/controller/loop.py` (add `_tick_failure_log`; use in `run_forever` except, lines 187-192)
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py`

**Interfaces:**
- Produces: `_tick_failure_log(exc: Exception, now_utc: datetime) -> dict[str, Any]`; `level` is `"warn"` for `TimeoutError`, else `"error"`.

- [ ] **Step 1: Failing test (test_rev4_loop.py)**

```python
def test_tick_failure_log_downgrades_timeout():
    from .controller.loop import _tick_failure_log
    t = datetime(2026, 7, 11, tzinfo=UTC)
    assert _tick_failure_log(TimeoutError(), t)["level"] == "warn"
    assert _tick_failure_log(ValueError("boom"), t)["level"] == "error"
    rec = _tick_failure_log(TimeoutError(), t)
    assert rec["msg"] == "rev4_tick_failed" and rec["error_type"] == "TimeoutError"
```

- [ ] **Step 2: Run it, watch it fail**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py::test_tick_failure_log_downgrades_timeout -v`
Expected: FAIL — `ImportError: cannot import name '_tick_failure_log'`.

- [ ] **Step 3: Add the helper + wire it in**

Add at module level (after imports, before `class ControllerLoop`):

```python
def _tick_failure_log(exc: Exception, now_utc: datetime) -> dict[str, Any]:
    # TCC 30s request timeouts are transient and self-heal next tick; a
    # SUSTAINED outage is caught out-of-band (watchdog arm_mode staleness +
    # thermostat-poller silence). So a lone TimeoutError is warn, not error —
    # genuine faults (any other exception type) stay at error.
    transient = isinstance(exc, TimeoutError)
    return {
        "ts": now_utc.isoformat(),
        "level": "warn" if transient else "error",
        "msg": "rev4_tick_failed",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
```

In `run_forever`, replace the `except Exception as exc:` body (the inline `print(_json.dumps({...}))`) with:

```python
                except Exception as exc:
                    print(_json.dumps(_tick_failure_log(
                        exc, datetime.now(timezone.utc))), flush=True)
```

- [ ] **Step 4: Run the test**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py::test_tick_failure_log_downgrades_timeout -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/controller/loop.py \
        deploy/energy-stack/hvac_scheduler/test_rev4_loop.py
git commit -m "chore(hvac): log transient TCC tick timeouts at warn, not error"
```

---

## Final verification (before PR)

- [ ] **hvac_scheduler + thermostat_poller suites green**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest . -q`
Then: `cd deploy/energy-stack/thermostat_poller && python -m pytest . -q`
Expected: all PASS. `test_rev4_acceptance.py` is the regression guard — stays green untouched.

- [ ] **Dual-copy still identical**

Run: `diff deploy/energy-stack/hvac_scheduler/tcc_client.py deploy/energy-stack/thermostat_poller/tcc_client.py`
Expected: no output.

- [ ] **Cold review of the diff** (`/code-review` or an equal-model reviewer) — focus the reauth-wrap edge cases (device-swap mid-write-sequence).

- [ ] **Push + open PR (stop — do not merge)**

```bash
git push -u origin harden/tcc-io-liveness
gh pr create --base main --title "Harden TCC writes: drop redundant refresh, reauth-wrap, warn on transient timeouts" \
  --body "PR 1 of 2 from docs/superpowers/plans/2026-07-11-tcc-io-liveness-hardening.md. Drops the redundant per-push TCC refresh and reauth-wraps the device writes (session-resilient); logs transient tick timeouts at warn (aligns with #131). No control-path behavior change; tier machine / hold math / config untouched. PR 2 (arm_mode liveness decouple + device-stall alarm) follows separately."
```

Surface the PR URL. Touches `hvac_scheduler/**` + `thermostat_poller/tcc_client.py` → **deploys on merge** (rebuilds hvac-scheduler + thermostat-poller). Chris reviews and merges.

## Post-merge

- [ ] Sync main + delete branch: `git switch main && git pull --ff-only origin main && git branch -d harden/tcc-io-liveness`
- [ ] Confirm on Pi-lab: `rev4_tick_failed` now at `warn`; pushes still land through spikes.
- [ ] Start PR 2 (Task 2 + device-stall alarm) from a fresh branch off main.

## PR 2 (deferred — separate plan)

- **Task 2:** move `_maybe_write_arm_mode` ahead of `snapshot()` so liveness is device-I/O-independent (spec §Telemetry: "liveness never depended on holds"). Locking test: beacon lands even when `snapshot()` raises.
- **Device-stall alarm:** in `telegram_notifier`, a "controller in a spike tier but no successful device write in N min" deadman — restores fast, correctly-labeled detection that Task 2 removes from the down-beacon. Ship together with Task 2.

## Self-Review

- **Spec coverage:** Task 1 ← §Runtime scoped-reads + write-path resilience; Task 3 ← §Telemetry alerting (aligned with #131). Task 2 deferred to PR 2 with its device-reachability companion — no alerting gap shipped.
- **Placeholder scan:** none — every code step shows the exact block.
- **Type consistency:** `get_climate(self, refresh: bool = True)` used identically in both copies and called `get_climate(refresh=False)` in `device.py`; `_tick_failure_log(exc, now_utc)` matches its test and call site.
- **Reauth-wrap edge case (device-swap mid-sequence):** the `_dev` property re-reads `self._client.device`, and a reauth `_login()` re-`discover()`s a fresh, refreshed device — so a 401 between writes retries on the new device with server-consistent cache; only the failing write is retried (no double-apply). Confirm in the cold review.
