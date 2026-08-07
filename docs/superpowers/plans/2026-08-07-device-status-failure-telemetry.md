---
date: 2026-08-07
owner: chris
status: draft
role-label: code-team
---

# `hvac.device_status` Failure Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the controller's scattered per-path error reporting with one measurement, one writer, and one reader, so device *read* failures — the entire real failure surface, ~26/day — become alertable without generating false alarms on self-healing blips.

**Architecture:** A new `hvac.device_status` measurement modeled line-for-line on the existing `pjm.feed_status` pattern. One row per device attempt, success *and* failure, tagged by class (`read` / `write` / `crash`) so "3 consecutive failures" means unbroken attempts rather than consecutive error rows. A read failure is recorded and ends the tick early rather than propagating, so only genuine controller crashes reach the top-level handler; infrastructure failures are wrapped in a marker exception and deliberately excluded from the crash class. A separate notifier reader counts consecutive same-class failures per class.

**Tech Stack:** Python 3.13, `influxdb_client` (via `influx_adapter.write_point` only), pytest (`asyncio_mode=auto`), Docker Compose on Pi-lab.

**Spec:** [2026-06-20-commissioning-controller-design.md §Telemetry → Failure telemetry](../specs/2026-06-20-commissioning-controller-design.md) (PR #137).

## Global Constraints

- **Import-linter contract (CI-enforced):** only `influx_adapter` may import `influxdb_client`. The new writer MUST go through `influx_adapter.write_point` — never construct `Point` directly.
- **Monitoring must never fail the control cycle.** A failure writing a `device_status` row is logged at `warn` and swallowed, exactly as `pjm_dm2_poller._write_feed_status` does. This also removes the exception-masking bug the superseded branch carried.
- **Flux schema collision:** any query that `group()`s `hvac.device_status` MUST pre-filter to a single `_field`. Grouping mixed field types is a runtime error.
- **Async seam:** never `asyncio.run` inside a tick; ONE event loop for the process lifetime.
- **Tests run per-service:** `cd deploy/energy-stack/<service>/ && python -m pytest .`. **NEVER** `python -m pytest deploy/energy-stack` from the repo root.
- **Pre-push:** run `bash deploy/energy-stack/run_typecheck.sh` (mypy + import-linter) — pytest does not cover it, and it bit PR #132.
- **Thresholds are constants, not env vars:** read 3, write 3, crash 1. No unrequested configurability.
- **Branching:** each PR `--base main`, no stacking, wait for the prior to merge. Agent stops at `gh pr create`.
- **Ordering is a safety property:** new coverage lands before old coverage is removed. Phase 3 must not begin until Phase 1 is merged and verified in production.

## File Structure

| File | Responsibility |
|---|---|
| `deploy/energy-stack/hvac_scheduler/controller/errors.py` | **NEW.** `InfrastructureError` marker only. Tiny and focused so the crash/infra boundary is one obvious place. |
| `deploy/energy-stack/hvac_scheduler/controller/telemetry.py` | Add `write_device_status`; later drop `error` from `write_action`. |
| `deploy/energy-stack/hvac_scheduler/controller/loop.py` | Instrument the read seam and write seam; wrap infra seams; record crash at the top handler; move `arm_mode`. |
| `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py` | Unit tests + the `Tel` fake. |
| `deploy/energy-stack/hvac_scheduler/test_rev4_acceptance.py` | Feature-level acceptance test (xfail strict until Phase 3). |
| `deploy/energy-stack/telegram_notifier/app.py` | New `check_device_status_failures`; retire `check_hvac_action_errors`. |
| `deploy/energy-stack/telegram_notifier/test_telegram_notifier.py` | Reader tests + the cross-service row-shape lock. |
| `docs/SERVICES.md` | Line 356 alert-pair description — update only in Phase 3, when it becomes true. |

---

## Phase 1 — Tracer bullet: the read class, end to end

Smallest cut through every layer the feature touches: writer → measurement → reader → alert. On completion, a sustained device-read outage alerts in ~3 minutes and intermittent blips alert never. Nothing is removed yet.

### Task 1: Acceptance test (xfail) + `write_device_status`

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/controller/errors.py`
- Modify: `deploy/energy-stack/hvac_scheduler/controller/telemetry.py` (after `write_action`, ~line 76)
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_acceptance.py`
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py`

**Interfaces:**
- Produces: `InfluxTelemetry.write_device_status(*, op: str, success: bool, tier: str, dry_run: bool, error_type: str = "", error_msg: str = "") -> None` — writes one `hvac.device_status` row. `op` is one of `"read"`, `"write"`, `"crash"`. Tags: `unit`, `op`, `success`, `tier`, `dry_run`. Fields: `error_type`, `error_msg` (truncated 200), `config_id`.
- Produces: `controller.errors.InfrastructureError` — marker exception, consumed in Task 5.

- [ ] **Step 1: Write the feature-level acceptance test, xfail(strict=True)**

Append to `test_rev4_acceptance.py`:

This is the **north star for the whole feature**, not just Task 1. It stays
`xfail(strict=True)` through Phases 1 and 2 and the marker comes off only in
Task 7, when the last piece (the liveness decouple) lands — that is the sole
definition of feature-complete.

```python
import pytest

from hvac_scheduler.controller.loop import ControllerLoop


@pytest.mark.xfail(strict=True,
                   reason="feature-complete only at Task 7 (liveness decouple)")
def test_read_outage_records_attempts_and_preserves_liveness(tmp_path):
    """North star: a sustained device-read outage must (a) record one read
    attempt row per tick, all failures, so a reader can count consecutive
    failures of a kind, and (b) leave the liveness beacon intact, so the
    watchdog never false-trips a live controller."""
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)

    class BoomClimate:
        async def snapshot(self): raise TimeoutError()

    tel = TelemetryRecorder()
    loop = ControllerLoop(
        cfg=make_cfg(tmp_path),
        price_source=FakePriceFeed(buckets=[(now - timedelta(seconds=400), 12.8)]),
        climate=BoomClimate(), telemetry=tel, mode="production",
        tz_name=CT, data_dir=str(tmp_path))

    for i in range(3):
        asyncio.run(loop.tick(now + timedelta(minutes=i)))

    reads = [r for r in tel.device_rows if r["op"] == "read"]
    assert len(reads) == 3, "one read attempt recorded per tick"
    assert all(r["success"] is False for r in reads)
    assert tel.arm_rows, "liveness must survive a device outage"
```

Also add the recorder method so the fake matches the real telemetry contract —
in `TelemetryRecorder` (~line 88):

```python
    device_rows: list[dict] = field(default_factory=list)

    def write_device_status(self, **kw): self.device_rows.append(kw)
```

- [ ] **Step 2: Run it, watch it fail**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_acceptance.py::test_sustained_read_outage_is_alertable_intermittent_is_not -v`
Expected: **XFAIL** — `AttributeError: 'InfluxTelemetry' object has no attribute 'write_device_status'`.

- [ ] **Step 3: Create the marker exception**

Create `deploy/energy-stack/hvac_scheduler/controller/errors.py`:

```python
"""Controller error boundary. Spec §Telemetry: infrastructure is NOT a
domain error class."""
from __future__ import annotations


class InfrastructureError(Exception):
    """The substrate the controller runs on failed (Influx query, local
    disk) — not the controller failing at its job.

    Spec §Telemetry: when Influx is down every measurement gaps at once,
    while a thermostat read failure gaps only `hvac.*`. The record already
    disambiguates the two, so recording infrastructure as `crash` would
    mislabel a substrate blip as a controller fault. Total loss is the
    off-box dead-man's job.
    """
```

- [ ] **Step 4: Write the failing unit test for the line contract**

Append to `test_rev4_loop.py`:

```python
def test_device_status_line_contract():
    from .controller.telemetry import InfluxTelemetry

    class Cap:
        lines: list = []
        def write(self, bucket, record): Cap.lines.append(record.to_line_protocol())

    tel = InfluxTelemetry(write_api=Cap(), bucket="energy", unit="C",
                          config_id="abc", tz_name="America/Chicago")
    tel.write_device_status(op="read", success=False, tier="elevated",
                            dry_run=False, error_type="TimeoutError", error_msg="")
    lp = Cap.lines[-1]
    assert lp.startswith("hvac.device_status,")
    assert "op=read" in lp
    assert "success=false" in lp
    assert "tier=elevated" in lp
    assert 'error_type="TimeoutError"' in lp
    assert 'error_msg=""' in lp


def test_device_status_write_failure_is_swallowed():
    """Monitoring must never fail the control cycle (pjm.feed_status precedent)."""
    from .controller.telemetry import InfluxTelemetry

    class Boom:
        def write(self, bucket, record): raise ConnectionError("influx down")

    tel = InfluxTelemetry(write_api=Boom(), bucket="energy", unit="C",
                          config_id="abc", tz_name="America/Chicago")
    tel.write_device_status(op="read", success=True, tier="normal", dry_run=False)
```

- [ ] **Step 5: Run them, watch them fail**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py::test_device_status_line_contract test_rev4_loop.py::test_device_status_write_failure_is_swallowed -v`
Expected: FAIL — `AttributeError: ... has no attribute 'write_device_status'`.

- [ ] **Step 6: Implement `write_device_status`**

In `telemetry.py`, immediately after `write_action` (ends ~line 75):

```python
    def write_device_status(self, *, op: str, success: bool, tier: str,
                            dry_run: bool, error_type: str = "",
                            error_msg: str = "") -> None:
        """One row per device attempt, success or failure. Spec §Telemetry.

        Mirrors `pjm.feed_status`: `op` and `success` are tags so the alert
        reader can count consecutive failures OF A KIND; `error_type` and
        `error_msg` stay separate fields (never concatenated — the old
        `f"{type(exc).__name__}: {exc}"` path produced `"TimeoutError: "`
        with the message half empty).

        A failure writing this row is logged and swallowed: monitoring must
        never fail the control cycle.
        """
        try:
            write_point(self.write_api, self.bucket, "hvac.device_status",
                        tags={"unit": self.unit, "op": op,
                              "success": "true" if success else "false",
                              "tier": tier,
                              "dry_run": "true" if dry_run else "false"},
                        fields={"error_type": error_type,
                                "error_msg": error_msg[:200],
                                "config_id": self.config_id})
        except Exception as exc:
            _log("warn", "device_status_write_failed", op=op,
                 error=str(exc), error_type=type(exc).__name__)
```

- [ ] **Step 7: Run the unit tests green**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py -q`
Expected: PASS, including the two new tests.

- [ ] **Step 8: Confirm the acceptance test still XFAILs**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_acceptance.py -q`
Expected: **XFAIL** (not XPASS). The writer exists now, but nothing calls it
from the loop yet and `arm_mode` still trails device I/O, so the north star is
correctly still unmet. An XPASS here would be reported as a failure by
`strict=True` and would mean the test is too weak — stop and strengthen it.

- [ ] **Step 9: Typecheck + commit**

```bash
bash deploy/energy-stack/run_typecheck.sh
git add deploy/energy-stack/hvac_scheduler/controller/errors.py \
        deploy/energy-stack/hvac_scheduler/controller/telemetry.py \
        deploy/energy-stack/hvac_scheduler/test_rev4_loop.py \
        deploy/energy-stack/hvac_scheduler/test_rev4_acceptance.py
git commit -m "feat(hvac): hvac.device_status attempt-outcome writer"
```

---

### Task 2: Record read attempts in the control loop

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/controller/loop.py:89-92` (the `if needs_device:` block)
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py`

**Interfaces:**
- Consumes: `telemetry.write_device_status(...)` from Task 1.
- Produces: a read failure no longer propagates out of `tick()` — it is recorded and the tick returns early. Only crashes and `InfrastructureError` reach `run_forever`.

**Why the early return replaces the re-raise:** if the snapshot failed there is nothing to decide and nothing to act on, so the tick has genuinely finished. Letting it propagate would also cause the same failure to be recorded twice — once as `read`, once as `crash` by the top-level handler in Task 5.

- [ ] **Step 1: Extend the `Tel` fake**

In `test_rev4_loop.py`, add to the `Tel` dataclass (after `overlay_rows`, ~line 40):

```python
    device_rows: list = field(default_factory=list)
    def write_device_status(self, **kw): self.device_rows.append(kw)
```

- [ ] **Step 2: Write the failing tests**

Append to `test_rev4_loop.py`:

> **Do NOT define a `_wired` helper** — one already exists at
> `test_rev4_loop.py:213` with signature `_wired(tmp_path, feed, dev, mode="production")`.
> Defining a second one shadows it and breaks
> `test_humidity_release_shadow_gate_dry_run` and
> `test_shadow_gate_never_writes_device`, which pass `mode=`. Use the existing one.

```python
def _ok_snapshot():
    from .controller.device import ControlSnapshot
    return ControlSnapshot(schedule_cool=24.0, cool_setpoint=24.0,
                           heat_setpoint=18.5, hold_active=False,
                           hold_until_minutes=None, indoor_temp=23.0,
                           humidity=50.0)


def test_read_failure_records_row_and_ends_tick(tmp_path):
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    feed = Feed(out=(12.8, now - timedelta(seconds=400), 400.0))  # elevated -> needs_device

    class BoomClimate:
        async def snapshot(self): raise TimeoutError()

    loop = _wired(tmp_path, feed, BoomClimate())
    asyncio.run(loop.tick(now))          # does NOT raise
    rows = [r for r in loop.telemetry.device_rows if r["op"] == "read"]
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["error_type"] == "TimeoutError"
    assert rows[0]["tier"] == "elevated"
    assert loop.telemetry.actions == []   # tick ended before deciding


def test_successful_read_records_a_success_row(tmp_path):
    from .controller.device import ControlSnapshot
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    feed = Feed(out=(12.8, now - timedelta(seconds=400), 400.0))

    class OkClimate:
        async def snapshot(self):
            return ControlSnapshot(schedule_cool=24.0, cool_setpoint=24.0,
                                   heat_setpoint=18.5, hold_active=False,
                                   hold_until_minutes=None, indoor_temp=23.0,
                                   humidity=50.0)
        async def push(self, *a): return None
        async def release(self): return None

    loop = _wired(tmp_path, feed, OkClimate())
    asyncio.run(loop.tick(now))
    reads = [r for r in loop.telemetry.device_rows if r["op"] == "read"]
    assert len(reads) == 1 and reads[0]["success"] is True


def test_normal_tier_records_no_read_row(tmp_path):
    """Pure normal ticks read nothing, so there is no attempt to record."""
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    loop = _loop(tmp_path, Feed(out=(4.2, now - timedelta(seconds=400), 400.0)))
    asyncio.run(loop.tick(now))
    assert loop.telemetry.device_rows == []
```

> **`ControlSnapshot` fields** (verified 2026-08-07 against
> `test_rev4_acceptance.py:47-58`): `schedule_cool`, `cool_setpoint`,
> `heat_setpoint`, `hold_active`, `hold_until_minutes`, `indoor_temp`,
> `humidity`. There is no `hold_until`.

- [ ] **Step 3: Run them, watch them fail**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py -k "read_failure or successful_read or normal_tier_records" -v`
Expected: FAIL — `TimeoutError` propagates out of `tick()`; no `device_rows` recorded.

- [ ] **Step 4: Instrument the read seam**

In `loop.py`, replace the `if needs_device:` block (lines 89-92):

```python
        needs_device = self.tier_state.tier != tiers.NORMAL or own is not None
        if needs_device:
            try:
                snap = await self.climate.snapshot()
            except Exception as exc:
                # Domain error, class `read` (spec §Telemetry). Recorded as an
                # attempt row, then the tick ends: with no snapshot there is
                # nothing to decide and nothing to act on. Deliberately NOT
                # re-raised — the top-level handler records `crash`, and this
                # is not a crash.
                self.telemetry.write_device_status(
                    op="read", success=False, tier=self.tier_state.tier,
                    dry_run=(self.mode == "shadow"),
                    error_type=type(exc).__name__, error_msg=str(exc))
                _tick_warn("device_read_failed", exc)
                return
            self.telemetry.write_device_status(
                op="read", success=True, tier=self.tier_state.tier,
                dry_run=(self.mode == "shadow"))
            self._update_humidity_gate(snap)
```

Add this helper next to `_tick_failure_log` (after line 36):

```python
def _tick_warn(msg: str, exc: Exception) -> None:
    """Loki line for a recorded domain failure. The Influx row is the alerting
    signal; this is for human log-reading only."""
    import json as _json
    print(_json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": "warn", "msg": msg,
        "error_type": type(exc).__name__, "error": str(exc),
    }), flush=True)
```

Add `timezone` to the existing `datetime` import at the top of `loop.py`:

```python
from datetime import datetime, timezone
```

- [ ] **Step 5: Run the tests green**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest . -q`
Expected: all PASS. The pre-existing suite (71 tests as of 2026-08-07) must stay green.

- [ ] **Step 6: Typecheck + commit**

```bash
bash deploy/energy-stack/run_typecheck.sh
git add deploy/energy-stack/hvac_scheduler/controller/loop.py \
        deploy/energy-stack/hvac_scheduler/test_rev4_loop.py
git commit -m "feat(hvac): record device-read attempts to hvac.device_status"
```

---

### Task 3: Per-class notifier reader + alert

**Files:**
- Modify: `deploy/energy-stack/telegram_notifier/app.py` (add near `check_hvac_action_errors`, ~line 808-845; register in the alert loop ~line 903)
- Test: `deploy/energy-stack/telegram_notifier/test_telegram_notifier.py`

**Interfaces:**
- Consumes: `hvac.device_status` rows from Tasks 1-2.
- Produces: `check_device_status_failures(query_api: Any, bucket: str) -> list[Alert]` — alert key `hvac_device:{op}` so each class dedupes independently.
- Produces: `DEVICE_FAILURE_THRESHOLDS: dict[str, int]` = `{"read": 3, "write": 3, "crash": 1}`.

- [ ] **Step 1: Write the failing tests**

First add the new name to the existing relative-import block at the top of
`test_telegram_notifier.py` (line 25, `from .app import (...)`), keeping it
alphabetical:

```python
    check_device_status_failures,
```

Then append the tests:

```python
# ---- check_device_status_failures -------------------------------------------
#
# Consecutive-of-a-kind. Measured over 12.3 days of production: 109 failed
# ticks, none reaching 3 consecutive, while the old any-class rule would have
# fired ~1x/day on blips that self-healed on the very next tick.


def test_device_status_alerts_on_three_consecutive_read_failures(monkeypatch):
    rows = [{"success": "false", "op": "read", "error_type": "TimeoutError",
             "error_msg": ""} for _ in range(3)]
    monkeypatch.setattr(app, "fetch_one", lambda q, f: rows)
    alerts = check_device_status_failures(MagicMock(), "energy")
    assert len(alerts) == 1
    assert alerts[0].key == "hvac_device:read"
    assert "TimeoutError" in alerts[0].text


def test_device_status_silent_when_a_success_interleaves(monkeypatch):
    """The whole point of attempt rows: one success breaks the run."""
    rows = [{"success": "false", "op": "read", "error_type": "TimeoutError", "error_msg": ""},
            {"success": "true",  "op": "read", "error_type": "", "error_msg": ""},
            {"success": "false", "op": "read", "error_type": "TimeoutError", "error_msg": ""}]
    monkeypatch.setattr(app, "fetch_one", lambda q, f: rows)
    assert check_device_status_failures(MagicMock(), "energy") == []


def test_device_status_crash_alerts_on_one(monkeypatch):
    rows = [{"success": "false", "op": "crash", "error_type": "ValueError",
             "error_msg": "bad state"}]
    monkeypatch.setattr(app, "fetch_one", lambda q, f: rows)
    alerts = check_device_status_failures(MagicMock(), "energy")
    assert len(alerts) == 1 and alerts[0].key == "hvac_device:crash"


def test_device_status_silent_with_too_few_rows(monkeypatch):
    rows = [{"success": "false", "op": "read", "error_type": "TimeoutError", "error_msg": ""}]
    monkeypatch.setattr(app, "fetch_one", lambda q, f: rows)
    assert check_device_status_failures(MagicMock(), "energy") == []


def test_device_status_query_failure_declines_to_alert(monkeypatch):
    def boom(q, f): raise RuntimeError("influx down")
    monkeypatch.setattr(app, "fetch_one", boom)
    assert check_device_status_failures(MagicMock(), "energy") == []
```

- [ ] **Step 2: Run them, watch them fail**

Run: `cd deploy/energy-stack/telegram_notifier && python -m pytest test_telegram_notifier.py -k device_status -v`
Expected: FAIL — `ImportError: cannot import name 'check_device_status_failures'`.

- [ ] **Step 3: Implement the reader**

In `app.py`, after `check_hvac_action_errors` (~line 845):

```python
# Per-class consecutive-failure thresholds (spec §Telemetry). Constants, not
# env vars. Read/write are TCC network I/O: transient and self-healing, and
# consecutive-of-a-kind cleanly separates blips from outages. A crash is a
# code fault and does not self-heal, so one is enough.
DEVICE_FAILURE_THRESHOLDS = {"read": 3, "write": 3, "crash": 1}


def check_device_status_failures(query_api: Any, bucket: str) -> list[Alert]:
    """Alert when the newest N attempts of a class are ALL failures.

    Because the controller writes a row per attempt (success AND failure), a
    single success between failures breaks the run — which is exactly what
    stops self-healing blips from alerting.

    `_field` is pre-filtered to a single key before `group()`; grouping mixed
    field types is a Flux runtime error.
    """
    alerts: list[Alert] = []
    for op, threshold in DEVICE_FAILURE_THRESHOLDS.items():
        flux = f'''
from(bucket: "{bucket}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "hvac.device_status"
                    and r.op == "{op}"
                    and r._field == "error_type")
  |> group()
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {threshold})
'''
        try:
            rows = fetch_one(query_api, flux)
        except Exception as exc:
            log("warn", "device_status_check_skipped", op=op,
                error=str(exc), error_type=type(exc).__name__)
            continue
        if len(rows) < threshold:
            continue
        if not all(r.get("success") == "false" for r in rows[:threshold]):
            continue
        newest = rows[0]
        err_type = newest.get("error_type") or "Exception"
        err_msg = (newest.get("error_msg") or "").strip()
        detail = f"<code>{err_type}</code>"
        if err_msg:
            detail += f": {err_msg[:200]}"
        alerts.append(Alert(
            key=f"hvac_device:{op}",
            text=(f"🌡️ <b>HVAC device {op} failing</b> — "
                  f"{threshold} consecutive.\n  • {detail}"),
        ))
    return alerts
```

- [ ] **Step 4: Register it in the alert loop**

In `app.py` (~line 903), directly after the `check_hvac_action_errors` line:

```python
            alerts.extend(check_device_status_failures(query_api, cfg.influx_bucket))
```

Both checks run in parallel for now. `check_hvac_action_errors` is retired in Phase 3, once this one is verified in production.

- [ ] **Step 5: Run the tests green**

Run: `cd deploy/energy-stack/telegram_notifier && python -m pytest . -q`
Expected: all PASS (36 pre-existing as of 2026-08-07, plus 5 new).

- [ ] **Step 6: Full suite, typecheck, commit**

```bash
bash deploy/energy-stack/run_tests.sh
bash deploy/energy-stack/run_typecheck.sh
git add deploy/energy-stack/telegram_notifier/app.py \
        deploy/energy-stack/telegram_notifier/test_telegram_notifier.py
git commit -m "feat(notifier): per-class hvac.device_status failure alerting"
```

- [ ] **Step 7: Open the Phase 1 PR and STOP**

```bash
git push -u origin feat/device-status-telemetry
gh pr create --base main --title "feat: hvac.device_status failure telemetry (read class, phase 1)" \
  --body "Phase 1 of docs/superpowers/plans/2026-08-07-device-status-failure-telemetry.md. Adds the hvac.device_status attempt-outcome measurement, records device-read attempts, and adds the per-class consecutive-failure alert. Purely additive — no existing alert or field is removed. Touches hvac_scheduler/** and telegram_notifier/**, so merging rebuilds both containers."
```

Surface the PR URL. **Do not merge.**

---

## Phase 2 — The write and crash classes

Phase 1 covers the class that actually fails ~26x/day. Phase 2 completes the taxonomy. Still purely additive.

### Task 4: Record write attempts

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/controller/loop.py:154-170` (`_apply_push`, `_apply_release`)
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py`

**Interfaces:**
- Consumes: `telemetry.write_device_status(...)`.
- Produces: no signature change. `_apply_push` / `_apply_release` keep returning `tuple[bool, str]` so `write_action` still records `applied`; they additionally emit a `write`-class row.

- [ ] **Step 1: Write the failing test**

```python
def test_write_failure_records_write_row_and_still_writes_action(tmp_path):
    from .controller.device import ControlSnapshot
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    feed = Feed(out=(12.8, now - timedelta(seconds=400), 400.0))

    class PushFails:
        async def snapshot(self):
            return ControlSnapshot(schedule_cool=24.0, cool_setpoint=24.0,
                                   heat_setpoint=18.5, hold_active=False,
                                   hold_until_minutes=None, indoor_temp=23.0,
                                   humidity=50.0)
        async def push(self, *a): raise TimeoutError()
        async def release(self): return None

    loop = _wired(tmp_path, feed, PushFails())
    asyncio.run(loop.tick(now))
    writes = [r for r in loop.telemetry.device_rows if r["op"] == "write"]
    assert len(writes) == 1
    assert writes[0]["success"] is False and writes[0]["error_type"] == "TimeoutError"
    assert loop.telemetry.actions and loop.telemetry.actions[-1]["applied"] is False
```

- [ ] **Step 2: Run it, watch it fail**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py::test_write_failure_records_write_row_and_still_writes_action -v`
Expected: FAIL — no `write`-class rows recorded.

- [ ] **Step 3: Instrument both write seams**

Replace `_apply_push` and `_apply_release` in `loop.py`:

```python
    async def _apply_push(self, cool: float, until: int) -> tuple[bool, str]:
        if self.mode == "shadow":
            return False, ""
        try:
            await self.climate.push(cool, self.cfg.heat_floor, until)
        except Exception as exc:  # transient TCC errors self-heal next tick
            self.telemetry.write_device_status(
                op="write", success=False, tier=self.tier_state.tier,
                dry_run=False, error_type=type(exc).__name__, error_msg=str(exc))
            return False, f"{type(exc).__name__}: {exc}"
        self.telemetry.write_device_status(
            op="write", success=True, tier=self.tier_state.tier, dry_run=False)
        return True, ""

    async def _apply_release(self) -> tuple[bool, str]:
        if self.mode == "shadow":
            return False, ""
        try:
            await self.climate.release()
        except Exception as exc:
            self.telemetry.write_device_status(
                op="write", success=False, tier=self.tier_state.tier,
                dry_run=False, error_type=type(exc).__name__, error_msg=str(exc))
            return False, f"{type(exc).__name__}: {exc}"
        self.telemetry.write_device_status(
            op="write", success=True, tier=self.tier_state.tier, dry_run=False)
        return True, ""
```

Shadow mode returns before any device call, so it records nothing — there was no attempt.

- [ ] **Step 4: Run green, typecheck, commit**

```bash
cd deploy/energy-stack/hvac_scheduler && python -m pytest . -q
cd ../../.. && bash deploy/energy-stack/run_typecheck.sh
git add deploy/energy-stack/hvac_scheduler/controller/loop.py \
        deploy/energy-stack/hvac_scheduler/test_rev4_loop.py
git commit -m "feat(hvac): record device-write attempts to hvac.device_status"
```

---

### Task 5: The crash class, with infrastructure excluded

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/controller/loop.py` (`tick` infra seams; `run_forever` handler ~lines 213-220)
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py`

**Interfaces:**
- Consumes: `controller.errors.InfrastructureError` from Task 1.
- Produces: `run_forever` records `op="crash"` for any exception that is NOT an `InfrastructureError`.

- [ ] **Step 1: Write the failing tests**

```python
def test_price_query_failure_is_infrastructure_not_crash(tmp_path):
    from .controller.errors import InfrastructureError

    class BoomFeed:
        def latest(self, now_utc): raise ConnectionError("influx unreachable")

    loop = _wired(tmp_path, BoomFeed(), NeverClimate())
    with pytest.raises(InfrastructureError):
        asyncio.run(loop.tick(datetime(2026, 7, 10, 12, 0, tzinfo=UTC)))
    assert [r for r in loop.telemetry.device_rows if r["op"] == "crash"] == []


def test_logic_exception_is_a_crash(tmp_path):
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)

    class BadTel(Tel):
        def trace(self, **kw): raise ValueError("logic fault")

    p = tmp_path / "c.yaml"; p.write_text(CFG_YAML, encoding="utf-8")
    cfg = load_config(str(p), temp_scale_env="C")
    loop = ControllerLoop(cfg=cfg, price_source=Feed(out=(4.2, now, 0.0)),
                          climate=NeverClimate(), telemetry=BadTel(),
                          mode="production", tz_name="America/Chicago",
                          data_dir=str(tmp_path))
    with pytest.raises(ValueError):
        asyncio.run(loop.tick(now))
```

Add `import pytest` at the top of `test_rev4_loop.py` if not already present.

- [ ] **Step 2: Run them, watch them fail**

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_loop.py -k "infrastructure_not_crash or logic_exception" -v`
Expected: FAIL — `ConnectionError` is raised, not `InfrastructureError`.

- [ ] **Step 3: Wrap the infrastructure seams in `tick`**

In `loop.py`, add the import at the top:

```python
from .errors import InfrastructureError
```

Replace the price fetch at the start of `tick` (lines 57-61):

```python
        tick_id = uuid.uuid4().hex
        try:
            raw = self.price_source.latest(now_utc)
        except Exception as exc:
            # Influx read — substrate, not a domain error (spec §Telemetry).
            raise InfrastructureError(f"price query: {type(exc).__name__}: {exc}") from exc
```

Wrap the hold-record disk writes. Replace the `save_record` call (line 116-118):

```python
                if applied:
                    try:
                        save_record(self.data_dir, OwnHoldRecord(
                            value=cool, until_minutes=until,
                            expiry_utc=self._slot_to_utc(until, now_local).isoformat()))
                    except OSError as exc:
                        raise InfrastructureError(f"save_record: {exc}") from exc
```

and the two `clear_record` calls (lines 130 and 142):

```python
                if applied:
                    try:
                        clear_record(self.data_dir)
                    except OSError as exc:
                        raise InfrastructureError(f"clear_record: {exc}") from exc
```

```python
                if own is not None and not holds._matches_own(own, snap):
                    try:
                        clear_record(self.data_dir)
                    except OSError as exc:
                        raise InfrastructureError(f"clear_record: {exc}") from exc
```

- [ ] **Step 4: Record crash in the top-level handler**

In `run_forever`, replace the try/except around `await self.tick(now)` (lines 215-220):

```python
                try:
                    await self.tick(now)
                    pathlib.Path("/tmp/last_tick_ok").touch()  # Dockerfile healthcheck
                except InfrastructureError as exc:
                    # Substrate failure. Deliberately NOT recorded as a domain
                    # error: an Influx outage gaps every measurement at once,
                    # so the record already shows it, and total loss is the
                    # off-box dead-man's job (spec §Telemetry).
                    print(_json.dumps(_tick_failure_log(
                        exc, datetime.now(timezone.utc))), flush=True)
                except Exception as exc:
                    self.telemetry.write_device_status(
                        op="crash", success=False, tier=self.tier_state.tier,
                        dry_run=(self.mode == "shadow"),
                        error_type=type(exc).__name__, error_msg=str(exc))
                    print(_json.dumps(_tick_failure_log(
                        exc, datetime.now(timezone.utc))), flush=True)
```

- [ ] **Step 5: Run green, typecheck, commit, open Phase 2 PR**

```bash
cd deploy/energy-stack/hvac_scheduler && python -m pytest . -q
cd ../../.. && bash deploy/energy-stack/run_tests.sh && bash deploy/energy-stack/run_typecheck.sh
git add deploy/energy-stack/hvac_scheduler/controller/loop.py \
        deploy/energy-stack/hvac_scheduler/test_rev4_loop.py
git commit -m "feat(hvac): crash class with infrastructure excluded"
git push -u origin feat/device-status-write-crash
gh pr create --base main --title "feat: hvac.device_status write + crash classes (phase 2)"
```

Surface the PR URL. **Do not merge.**

---

## Phase 3 — Retire the old machinery

**Do not start until Phase 1 is merged AND verified in production** (see the verification gate below). This phase removes coverage, so the replacement must be proven first.

### Task 6: Delete `hvac.actions.error` and the old check

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/controller/telemetry.py` (`write_action`)
- Modify: `deploy/energy-stack/hvac_scheduler/controller/loop.py` (both `write_action` call sites)
- Modify: `deploy/energy-stack/telegram_notifier/app.py` (delete `check_hvac_action_errors`, `_BENIGN_HVAC_ERRORS`, `PUSH_FAILURE_ALERT_N`, and the alert-loop registration)
- Modify: `deploy/energy-stack/telegram_notifier/test_telegram_notifier.py` (delete `test_push_failure_alert_requires_three_consecutive` and the import)
- Modify: `docs/SERVICES.md:356`

- [ ] **Step 1: Confirm nothing else reads the field**

Run: `git grep -n "hvac.actions" -- deploy/ | grep -v test`
Expected: `vigil_queries.py` (does not select `error`), `freshness.py` (comment only), `telegram_notifier/app.py:318` (bare `last()`, no field selection). If anything else selects `error`, stop and report before deleting.

- [ ] **Step 2: Drop `error` from the writer**

In `telemetry.py`, delete the `"error": error or "",` line from `write_action`'s `fields` dict, and remove `error: str,` from its signature.

- [ ] **Step 3: Drop it at both call sites**

In `loop.py`, delete `error=err,` from the SPIKE `write_action(...)` call and from the RELEASE one. The `applied, err = await self._apply_push(...)` unpacking stays — `err` is now unused at the call site, so rename it to `_err` to keep linters quiet.

- [ ] **Step 4: Delete the retired check**

In `app.py`, delete `PUSH_FAILURE_ALERT_N`, `_BENIGN_HVAC_ERRORS`, the whole `check_hvac_action_errors` function, and its `alerts.extend(...)` line in the alert loop. Update the module docstring at line 11 which describes the old rule.

- [ ] **Step 5: Delete its test**

Remove `test_push_failure_alert_requires_three_consecutive` and drop `check_hvac_action_errors` from the import block at line 29.

- [ ] **Step 6: Update SERVICES.md**

Replace line 356's second clause:

```markdown
**Alert pair (telegram-notifier):** `check_controller_down` fires on the watchdog's `hvac.heartbeat controller_alive=false` down-beacon (controller silent ≥ 10 min); `check_device_status_failures` fires per class on N consecutive same-class `hvac.device_status` failures (read 3, write 3, crash 1). Unit-tested in `telegram_notifier/`.
```

- [ ] **Step 7: Run everything, typecheck, commit**

```bash
bash deploy/energy-stack/run_tests.sh
bash deploy/energy-stack/run_typecheck.sh
git commit -am "refactor: retire hvac.actions.error and check_hvac_action_errors"
```

---

### Task 7: Move the liveness beacon ahead of device I/O

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/controller/loop.py`
- Test: `deploy/energy-stack/hvac_scheduler/test_rev4_loop.py`

This lands last on purpose: it removes the watchdog's accidental read-outage coverage, and the `read`-class alert from Phase 1 is its replacement.

- [ ] **Step 1: Write the failing test**

```python
def test_arm_mode_written_before_device_read(tmp_path):
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    feed = Feed(out=(12.8, now - timedelta(seconds=400), 400.0))

    class BoomClimate:
        async def snapshot(self): raise TimeoutError()

    loop = _wired(tmp_path, feed, BoomClimate())
    asyncio.run(loop.tick(now))
    assert len(loop.telemetry.arm_rows) == 1   # beacon landed before the failed read
```

- [ ] **Step 2: Run it, watch it fail**

Expected: FAIL — `assert 0 == 1`; the tick returns early on read failure, before the tick-end beacon.

- [ ] **Step 3: Move the call**

In `loop.py`, immediately after the `self.telemetry.trace(...)` block (~line 76), insert:

```python

        # Liveness beacon BEFORE any device I/O (spec §Telemetry). A device-read
        # failure must not suppress arm_mode, or the watchdog false-trips
        # "controller DOWN" on a live controller. The read-class alert is the
        # replacement for the down-beacon's accidental coverage.
        self._maybe_write_arm_mode(now_utc)
```

Then DELETE the existing `self._maybe_write_arm_mode(now_utc)` at the end of `tick`.

- [ ] **Step 4: Take the marker off the north star — feature-complete**

The acceptance test written in Task 1 has been `xfail(strict=True)` since Phase
1. Its liveness assertion (`assert tel.arm_rows`) is exactly the piece Step 3
just satisfied. Delete only the decorator:

```python
@pytest.mark.xfail(strict=True,
                   reason="feature-complete only at Task 7 (liveness decouple)")
```

Run: `cd deploy/energy-stack/hvac_scheduler && python -m pytest test_rev4_acceptance.py -q`
Expected: PASS, against the real implementation with zero scaffolding. **That
is the only definition of feature-complete** — if it needs any test-only
accommodation to pass, the feature is not done.

- [ ] **Step 5: Run everything, typecheck, commit, open the Phase 3 PR**

```bash
bash deploy/energy-stack/run_tests.sh
bash deploy/energy-stack/run_typecheck.sh
git commit -am "fix(hvac): emit arm_mode liveness before device I/O"
git push -u origin feat/device-status-retire-old
gh pr create --base main --title "refactor: retire hvac.actions.error, decouple arm_mode (phase 3)"
```

Surface the PR URL. **Do not merge.**

---

## Verification gate (between Phase 1 merge and Phase 3 start)

Phase 3 removes live coverage. Do not begin it until all of the following hold on Pi-lab:

- [ ] `hvac.device_status` rows exist with `op=read` and both `success=true` and `success=false` values:
  ```
  from(bucket:"energy") |> range(start:-24h)
    |> filter(fn:(r)=>r._measurement=="hvac.device_status" and r._field=="error_type")
    |> group(columns:["op","success"]) |> count()
  ```
- [ ] Success rows vastly outnumber failure rows (expected ≈ 26 failures/day against far more attempts). If failures dominate, the seam is misinstrumented — stop.
- [ ] No `device_status_write_failed` warn lines in the scheduler logs:
  `docker compose logs --since 24h hvac-scheduler | grep device_status_write_failed`
- [ ] No new `hvac_device:*` Telegram alerts fired for intermittent blips over at least 48h. Given the measured pattern (109 failures in 12.3 days, none reaching 3 consecutive) the expected count is **zero**. Any alert in this window means the threshold logic is wrong.
- [ ] The pre-existing alert `check_hvac_action_errors` has not fired either — confirming the two agree.

## Post-merge

- [ ] Sync main, delete local branches.
- [ ] Archive this plan to `docs/superpowers/plans/archive/` in the commit that closes the feature.
- [ ] Remove the "not yet implemented" status block from the spec's §Failure telemetry subsection.
- [ ] Open the separate work item for the second healthchecks.io check (Influx-freshness-conditional ping) — out of scope here.

## Self-Review

- **Spec coverage:** `hvac.device_status` own measurement ← Task 1; three classes ← Tasks 2/4/5; infra not a class ← Task 5; attempt rows ← Tasks 2/4; separate `error_type`/`error_msg` ← Task 1; thresholds 3/3/1 ← Task 3; `hvac.actions.error` deleted ← Task 6; `check_hvac_action_errors` retired ← Task 6; `arm_mode` before device I/O ← Task 7; alerting as a separate reader ← Task 3. The off-box dead-man is explicitly out of scope and carried to Post-merge.
- **Placeholder scan:** none — every code step shows the exact block. `ControlSnapshot`'s constructor args were verified against `test_rev4_acceptance.py:47-58` rather than guessed.
- **Outside-in discipline:** one feature-level acceptance test, written first in Task 1, `xfail(strict=True)` across every PR boundary, marker removed only in Task 7. Never `skip` — a skip is silent across PRs and trains everyone to ignore the north star.
- **Type consistency:** `write_device_status(*, op, success, tier, dry_run, error_type, error_msg)` is used identically in Tasks 1, 2, 4, and 5; the `Tel` fake gains the same method name; `DEVICE_FAILURE_THRESHOLDS` keys (`read`/`write`/`crash`) match the `op` values written by the loop.
- **Known behavior change:** a device-read failure no longer produces `rev4_tick_failed`; it produces `device_read_failed` at `warn` plus an Influx row. Anything watching the old string in Loki must be updated — a repo grep found no such consumer.
- **Ordering:** additive phases (1, 2) precede the subtractive one (3), and the `arm_mode` decouple lands after its replacement alert is verified in production.
