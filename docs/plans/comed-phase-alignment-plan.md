---
date: 2026-05-23
owner: chris
status: ready-to-execute
role-label: chris
spec: docs/superpowers/specs/2026-05-19-comed-freshness-design.md
incident: STALE_DATA_HANDOFF.md (root-cause analysis 2026-05-23)
branch: plan/comed-phase-alignment
---

# ComEd Poller / Scheduler Phase Alignment Plan

## Goal

Make ComEd poller and HVAC scheduler tick on a deterministic wall-clock cadence so the scheduler reliably observes new ComEd buckets while still fresh (`bucket_age <= 7:00`). Eliminate restart-dependent behavior: post-fix, identical ComEd publish behavior produces identical controller behavior regardless of when containers booted.

This is the implementation work that makes the `comed-freshness-design` spec §6 cycle-coverage math actually achievable in practice. The spec's design budgeted a ~26% actionable window per cycle assuming random scheduler-tick alignment within the publish cycle. The current implementation phase-locks both processes to wall-clock seconds inherited from container boot time, so the actual actionable window collapses to 0% or ~100% depending on whether the boot phase happens to align well or poorly. The 2026-05-22 stale-release event was the bad-alignment case.

## Framing (load-bearing — quote in every dispatch prompt)

- **`:00` is the coordination anchor, not a moment of magic ComEd availability.** ComEd's publish lag is external and varies (4-9 min from interval END). The wall-clock anchor exists so our poller and scheduler have a stable phase relationship to each other, not to ComEd.
- **The goal is "scheduler reads shortly after our write."** Not "new bucket always available at `:00`." The scheduler `bucket_age` is `(scheduler_now - bucket._time)`; if our internal write-to-read gap is ~5-10s instead of ~53s, we get back the design's ~1 min of headroom under the 7-min threshold.
- **No binding-spec change.** The 7-min `comed.prices` fresh threshold in `freshness.py` is unchanged. The `PRICE_FEED_STALE_THRESHOLD = 30 min` safety-release timer is unchanged. Only the cadence/phase the implementation uses to satisfy those thresholds changes.

## Root cause (verified 2026-05-23)

Both `hvac-scheduler` and `comed-poller` restarted simultaneously at **2026-05-21 23:33:08 UTC** (single `docker compose up -d` for the type-checker work, PR 5 `1254b56`). Each process's tick cadence is a 60s `sleep` loop starting from boot, so the new poll phase locked to whatever wall-clock second the boot landed on:

- **Before** 5/21 23:33 deploy: poll at sec `:44`, scheduler at sec `:05` → scheduler reads `:44` poll at 21s gap → `bucket_age ≈ 6:04` (fresh) → natural release path works → Chris observed working releases.
- **After** 5/21 23:33 deploy: poll at sec `:12`, scheduler at sec `:05` → scheduler reads `:12` poll of prior minute at 53s gap → `bucket_age ≈ 7:05` (just past 7:00) → natural release gated every cycle → stale-release fires after 30 min.

Empirical verification of the 2026-05-22 19:52-20:22 incident window:
- 0 / 35 scheduler ticks observed `bucket_age <= 420s`.
- Every poll wrote its bucket at `bucket_age_at_write = 6:12-6:13` — i.e., **fresh at write**, before the scheduler's next tick 53s later.
- Reason codes fired correctly: `HELD_DOWNGRADE_BUCKET_AGE` on every tick during the window, `RELEASED_PERSISTENT_STALE` at the 30-min mark. Logic conforms to spec; the problem is exclusively the cadence/phase alignment.

## Architecture

Two coordinated wall-clock anchors:

- **Poller** sleeps to the next `XX:XX:00` boundary, then polls. Removes restart-induced phase drift entirely.
- **Scheduler** sleeps to the next `XX:XX:SS` boundary where `SS = SCHEDULER_TICK_SECOND` (proposed: `10`). Reads Influx ~10s after the poll has written, well within the 7-min fresh budget.

The 10s gap absorbs poller cycle time (HTTP fetch + JSON parse + Influx write — typically 1-3s, observed spikes to ~5-6s during ComEd API slowdowns). 10s gives ~2x margin over the 99th-percentile cycle time (to be verified empirically in Phase 1).

Both processes use **interruptible sleep loops** (not bare `time.sleep`) so SIGTERM still drops the cycle promptly during deploys.

## Tech stack

Python 3.13, asyncio (scheduler), threading + signal (poller), pytest. Docker Compose runtime on Pi-lab. No new dependencies.

## Reference

- **Spec:** `docs/superpowers/specs/2026-05-19-comed-freshness-design.md` (§6 cycle-coverage math; this plan makes that math operational).
- **Incident root-cause:** `STALE_DATA_HANDOFF.md` (2026-05-19 freshness-blindness investigation) and the 2026-05-22 stale-release diagnosis captured in this plan's §"Root cause" above.
- **Binding pre-OSF spec:** `docs/plans/sced-rebaseline-spec-2026-05-13.md` (unchanged by this plan).

---

## File structure

### Existing files modified

| Path | Phase | Why it changes |
|------|-------|----------------|
| `deploy/energy-stack/comed_poller/poller.py` | 1, 2 | P1: emit `cycle_elapsed_s`, `latest_5min_age_s_at_write`, `poll_phase_s` in `poll_ok` log. P2: replace 60s-from-boot sleep with sleep-to-next-`:00` boundary using interruptible loop. |
| `deploy/energy-stack/comed_poller/test_comed_poller.py` | 1, 2 | P1: tests for new log fields. P2: tests for wall-clock alignment math (no drift over multiple cycles, boundary guard correctness, SIGTERM responsiveness). |
| `deploy/energy-stack/hvac_scheduler/app.py` | 1, 2 | P1: add `sample_freshness` field to `decision_trace.price_overlay_eval` emission and `tick_complete` log line with `duration_s`. P2: replace `if minute != last_minute_seen` loop with sleep-to-next-`:SS` boundary using `SCHEDULER_TICK_SECOND = 10` constant. |
| `deploy/energy-stack/hvac_scheduler/test_hvac_scheduler.py` | 1, 2 | P1: assert `sample_freshness` field in trace. P2: outside-in acceptance test (replays 2026-05-22 scenario) — `xfail(strict=True)` at PR A boundary, marker removed in same commit that lands PR B. |

### No new files

This plan introduces no new modules, no new dependencies, no new configuration files. All changes are local refactors within existing files.

---

## Phasing

Three phases, vertical slices, tracer bullet first per AGENTS.md plan-authoring rule #4-5.

### Phase 1 — Telemetry tracer (PR A)

**Slice:** poller emits richer `poll_ok`; scheduler emits `sample_freshness` and `tick_complete`. Deploy to production, observe one morning's decision-trace report.

**Why first:** additive only — zero behavior change. Lands the observability we need to verify Phase 2's success and to catch any future regression of this kind in one log query instead of a multi-hour investigation. Per Chris's review: "would have made this whole investigation a one-line check."

**Outside-in acceptance test** (lives in `test_hvac_scheduler.py`): a feature-level test that replays the 2026-05-22 19:52-20:22 scenario (mocked Influx returns buckets that always age past 7:00 at scheduler read time) and asserts:

1. Phase 1 only: the trace events for those ticks emit `sample_freshness="warn"` (not `"fresh"`). Test marked `pytest.mark.xfail(strict=True, reason="Phase 2 phase-alignment fix not yet landed")` at PR A merge. The `xfail` strict means PR A boundary still visibly carries the gap.
2. Phase 2: after the phase fix, the test is rewritten to assert `sample_freshness="fresh"` on first observation of each new bucket, and the `xfail` marker is removed in the same commit. The marker comes off only when the real implementation passes against the real scenario.

**Implementation tasks:**

- [ ] Task 1.1 — Add `cycle_elapsed_s`, `latest_5min_age_s_at_write`, `poll_phase_s` to `poll_ok` log in `comed_poller/poller.py`. `latest_5min_age_s_at_write = (write_time - bucket._time)`. `poll_phase_s = int(time.time() % 60)`.
- [ ] Task 1.2 — Tests for new poller log fields in `test_comed_poller.py`.
- [ ] Task 1.3 — Add `sample_freshness` field to `decision_trace.price_overlay_eval` emission in `hvac_scheduler/app.py`. Value is `sample.freshness if sample else "missing"`. Cheap — the classify call already runs.
- [ ] Task 1.4 — Add `tick_complete` info log at end of each scheduler tick with `duration_s = (time.monotonic() - tick_start)`. For overlap-detection visibility.
- [ ] Task 1.5 — Outside-in acceptance test stub (xfail-strict): `test_phase_alignment_recovers_fresh_observation`. Asserts scheduler observes `sample_freshness="fresh"` on first scheduler tick after each bucket landing. Currently xfailed because phase fix not yet landed.
- [ ] Task 1.6 — Run `bash deploy/energy-stack/run_tests.sh`, fix anything red.
- [ ] Task 1.7 — `gh pr create --base main` for PR A. Stop. Wait for operator merge.

**Verification (post-PR-A merge):** open the next morning's decision-trace report (or pull Loki manually). Confirm `cycle_elapsed_s` distribution shows the 99th-percentile poll time (informs the `SCHEDULER_TICK_SECOND` choice for Phase 2 — if p99 cycle time is comfortably below 10s, `:10` is safe; if it's ~8-10s, consider `:15` for more margin).

### Phase 2 — Phase alignment (PR B)

**Slice:** poller anchored to wall-clock `:00`; scheduler anchored to `SCHEDULER_TICK_SECOND = 10`. Outside-in acceptance test's xfail marker removed in this PR's same commit.

**Why second:** depends on Phase 1's telemetry being live to verify the fix worked. Also lets us tune the `SCHEDULER_TICK_SECOND` value based on Phase 1's empirical `cycle_elapsed_s` data instead of guessing.

**Implementation tasks:**

- [ ] Task 2.1 — Refactor `comed_poller/poller.py` main loop. Replace the trailing `sleep(poll_interval - elapsed)` with a leading sleep-to-next-`:00` boundary. Pattern (boundary-guard + interruptible):
  ```python
  while not stop_requested:
      now = time.time()
      secs_into_minute = now % 60.0
      sleep_for = (60.0 - secs_into_minute) if secs_into_minute > 0.001 else 60.0
      deadline = time.monotonic() + sleep_for
      while not stop_requested and time.monotonic() < deadline:
          time.sleep(min(1.0, deadline - time.monotonic()))
      if stop_requested:
          break
      cycle_start = time.monotonic()
      # ... fetch + write (unchanged) ...
  ```
  Remove the trailing sleep block entirely — the next iteration's leading sleep replaces it.
- [ ] Task 2.2 — Tests for poller wall-clock alignment: (a) across 5 simulated cycles, every cycle starts within `<= 100ms` of an `XX:XX:00` boundary; (b) boundary guard prevents 60s sleep when called within 1ms of a boundary; (c) SIGTERM received during the sleep terminates within `<= 1.1s`. Use `freezegun` for deterministic time, threading for the SIGTERM test.
- [ ] Task 2.3 — Add module-level `SCHEDULER_TICK_SECOND = 10` constant near the top of `hvac_scheduler/app.py` (above `main_async`). Brief docstring: "Wall-clock second-of-minute at which the scheduler ticks. Chosen to fall after the comed-poller's wall-clock `:00` poll-write so the scheduler reads the same minute's poll (not the prior minute's). See `docs/plans/comed-phase-alignment-plan.md`."
- [ ] Task 2.4 — Refactor `hvac_scheduler/app.py` `main_async` loop. Remove `last_minute_seen` and the `if now_local.minute != last_minute_seen` guard. Replace with:
  ```python
  while not stop.is_set():
      now = datetime.now(tz)
      target = now.replace(second=SCHEDULER_TICK_SECOND, microsecond=0)
      if target <= now:
          target += timedelta(minutes=1)
      sleep_for = (target - now).total_seconds()
      try:
          await asyncio.wait_for(stop.wait(), timeout=sleep_for)
      except asyncio.TimeoutError:
          pass
      if stop.is_set():
          break
      tick_start = time.monotonic()
      # ... existing tick body ...
      log("info", "tick_complete", duration_s=(time.monotonic() - tick_start))
  ```
- [ ] Task 2.5 — Tests for scheduler wall-clock alignment: (a) across 5 simulated minutes, every tick fires within `<= 100ms` of `XX:XX:10`; (b) `stop.set()` during the sleep ends the loop within `<= 1.1s` (`asyncio.wait_for` already supports this); (c) regression test that the old `if minute != last_minute_seen` behavior is gone (no double-tick within the same minute).
- [ ] Task 2.6 — Remove `xfail(strict=True)` marker from `test_phase_alignment_recovers_fresh_observation` and rewrite assertions to require `sample_freshness="fresh"` on first scheduler tick after each bucket landing. Per AGENTS.md rule #4: "the marker comes off the moment the test passes against the real implementation with zero scaffolding — that is the only definition of feature-complete."
- [ ] Task 2.7 — Run `bash deploy/energy-stack/run_tests.sh`. The acceptance test must pass without the xfail marker. Anything else red, fix.
- [ ] Task 2.8 — `gh pr create --base main` for PR B. Stop. Wait for operator merge.

### Phase 3 — Post-deploy verification (no PR)

**Slice:** observe production behavior for 7+ days after PR B merges. No code changes unless verification fails.

**Verification criteria:**

1. **Fresh observations dominate.** Across one full day of `decision_trace.price_overlay_eval` events, `sample_freshness="fresh"` appears on the majority of ticks where a new bucket just landed (= the first tick of each 5-min publish cycle).
2. **No `PRICE_OVERLAY_RELEASED_PERSISTENT_STALE` events** for 7 consecutive days. Stale-release should be reserved for genuine ComEd outages, not routine publish-lag variation.
3. **`tick_duration_s` well under 60s** on every tick. Confirms no overlap risk.
4. **`poll_phase_s` consistently `0` or `1`** across container restarts. Confirms wall-clock anchor survives restart-induced phase drift.

If any criterion fails, the failure mode is observable in the new telemetry. Triage from there.

---

## Branching policy

Per AGENTS.md:
- Branch from `main`. No stacking — wait for PR A merge before opening PR B.
- Stop at `gh pr create`. Operator reviews and merges.
- After merge, sync local main (`git pull --ff-only origin main`), delete local feature branch, then branch the next phase.
- Never use `--no-verify`, never bypass safety hooks.

## Out of scope

- **Threshold changes.** The 7-min `fresh_max_ms` and 30-min `PRICE_FEED_STALE_THRESHOLD` are not touched. If post-Phase-3 the design's 26% actionable window proves insufficient in field testing, that is a separate spec amendment (pre-OSF, reviewable).
- **ComEd publish-lag improvements.** Outside our control. Phase fix accepts ComEd's empirical 5-6 min publish lag and works within it.
- **Cockpit frontend changes.** The `freshness.ts` thresholds remain unchanged.
- **5CP detector phase.** The 5CP path reads different feeds with different cadence; not in scope.
- **Other-service tick alignment** (`thermostat-poller`, `haven-ingest`, etc.). If their phase alignment matters for any downstream consumer, those are separate plans.

## Archive on merge

When PR B closes the feature (Phase 2 lands, Phase 3 verification green), move this file to `docs/plans/archive/comed-phase-alignment-plan.md` per AGENTS.md plan-authoring rule #7.
