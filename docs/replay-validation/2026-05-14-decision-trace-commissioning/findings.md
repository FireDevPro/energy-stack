---
date: 2026-05-14
owner: chris
status: path-c-complete-b1-pending
role-label: chris
---

# Decision-trace commissioning findings — 2026-05-14

## Summary

Path C executed successfully on 2026-05-14 22:58 CT (2026-05-15 03:58 UTC). All four previously-unverified `decision_trace.*` event types confirmed firing end-to-end through real production functions, real `_trace_*` helpers, real `log()` print, real container stdout, real promtail scrape, real Loki ingest.

Path B1 deferred — see §3 below.

---

## 1. Path C — controlled rule/trace commissioning ✅

### 1.1 What was exercised

Synthetic in-memory inputs through real production rule functions + emission helpers. **No InfluxDB writes**. **No modification to the running scheduler.** Separate Python process inside the `hvac-scheduler` container with its own `app` module instance.

Script: [`deploy/energy-stack/scripts/commission_decision_trace_path_c.py`](../../../deploy/energy-stack/scripts/commission_decision_trace_path_c.py)

Tick_id prefix: `commission_20260515T035818Z_` (filterable in Loki forever).

### 1.2 Coverage matrix

| Event type | Scenarios | Loki lines | Status |
|---|---|---|---|
| `decision_trace.day_type_decision` | 8 (HOT high≥85, HOT apparent-only, HOT heat-advisory, HOT_STREAK multi-day, NORMAL high 75–84, NORMAL missing-temps, NORMAL no-forecast, MILD high<75) | 8 | ✅ |
| `decision_trace.layer_resolution` | 5 (schedule_wins, price_overlay_wins_elevated, price_overlay_wins_scarcity, fivecp_wins, tie_warmer_wins) | 5 | ✅ |
| `decision_trace.supervisor` | 8 (approved, approved_no_indoor, 4× single-axis clamps, clamped_multiple, emergency_overheat) | 8 | ✅ |
| `decision_trace.precool_decision` | 6 (all 6 `PrecoolCode` outcomes) | 6 | ✅ |
| **Total** | **27** | **27** | **0 missing** |

### 1.3 Field-shape verification (representative samples)

**day_type_decision** (mild_high_lt_75 scenario) — full evaluation_tape inlined with 6 rules:

```json
{"ts": "2026-05-15T03:58:18.438078+00:00", "level": "info",
 "msg": "decision_trace.day_type_decision",
 "tick_id": "commission_20260515T035818Z_dt_mild_high_lt_75",
 "scheduler_mode": "shadow",
 "decision_for_date": "2026-05-15", "winning_day_type": "MILD",
 "evaluation_tape": [
   {"rule": "heat_advisory", "threshold": true, "actual": false, "fired": false,
    "reason_code": "DAY_TYPE_HOT_HEAT_ADVISORY"},
   {"rule": "high_ge_hot", "threshold": 85, "actual": 70.0, "fired": false,
    "reason_code": "DAY_TYPE_HOT_HIGH_GE_85"},
   {"rule": "apparent_ge_hot", "threshold": 90, "actual": 72.0, "fired": false,
    "reason_code": "DAY_TYPE_HOT_APPARENT_GE_90"},
   {"rule": "high_ge_normal", "threshold": 75, "actual": 70.0, "fired": false,
    "reason_code": "DAY_TYPE_NORMAL_HIGH_75_TO_84"},
   {"rule": "missing_temps_fallback", "threshold": null, "actual": null, "fired": false,
    "reason_code": "DAY_TYPE_NORMAL_MISSING_TEMPS_FALLBACK"},
   {"rule": "mild_default", "threshold": 75, "actual": 70.0, "fired": true,
    "reason_code": "DAY_TYPE_MILD_HIGH_LT_75"}
 ],
 "winning_reason": "high_lt_75", "high_f": 70.0, "apparent_max_f": 72.0,
 "max_dewpoint_f": 50.0, "is_heat_advisory": false, "day2_high_f": null}
```

Negative-branch reasoning visible in one line — exactly the "HOT_STREAK was considered but didn't fire because…" forensic signal Chris asked for during the original grill.

**layer_resolution** (tie_warmer_wins scenario):

```json
{"winning_layer": "tie",
 "reason_code": "LAYER_RESOLUTION_TIE_WARMER_WINS",
 "schedule_cool_f": 75, "price_cool_f": 85, "fivecp_cool_f": 85,
 "effective_cool_f": 85, "fivecp_active": true,
 "fivecp_scopes_fired": ["comed_zone"]}
```

Both contributors visible. `winning_layer="tie"` schema (the post-Phase-2 plan amendment) confirmed in Loki.

**supervisor** (emergency_overheat scenario):

```json
{"proposed_cool_f": 85, "proposed_heat_f": 68,
 "indoor_temp_f": 87.0, "indoor_temp_available": true,
 "decision": "emergency", "reason_code": "SUPERVISOR_EMERGENCY_OVERHEAT",
 "supervisor_reason": "indoor_87.0F_above_86F",
 "final_cool_f": 74, "final_heat_f": 68}
```

Emergency override behavior (proposed cool 85 → final cool 74) reflected in trace.

**precool_decision** (selected scenario):

```json
{"selected": true, "hour_ct": 6, "depth_f": 67,
 "reason_code": "PRECOOL_SELECTED",
 "decision_for_date": "2026-05-15", "day_type": "MILD"}
```

depth_f=67 reflects spike-magnitude scaling (spike max=15¢, interpolated between 68F default and 66F deepest).

### 1.4 Live behavior incidentally confirmed

- **P2.7 warn restoration working.** The `normal_missing_temps` scenario exercised the `_classify_with_tape` path that the Phase 5 review-fix commit (`6b03647`) added a warn to. Post-run logs show exactly one `forecast_no_temperature_fields_falling_back_to_normal` warn at the script's timestamp — confirming the fix landed live.

- **Existing `evaluation_tape` returns NORMAL_NO_FORECAST_FALLBACK on `None` forecast.** Single-entry tape with `fired=true`. Matches the Phase 5 specification.

### 1.5 What Path C does NOT prove

- **Scheduler tick-loop wiring under live `FiringState` mutation.** `run_schedule_check` → `_evaluate_layer_inputs` → `_push_layer_change_mid_period` are NOT exercised in this pass. The chain test in `test_decision_trace.py` covers tick_id correlation in pytest; live verification deferred to Path B1.

- **Mid-period repush no-push short-circuit mechanic.** `firing.last_pushed_effective_cool_f` skipping the push when supervisor approves an unchanged setpoint — only exercised by real consecutive ticks. Pytest-covered, not live-verified in C.

- **Real-clock cadence.** Trace volume per real day, promtail back-pressure under sustained per-minute emissions over 24h — Loki ingest verified for the burst of 27 lines but not for sustained per-tick cadence at scale. The price_overlay_eval ambient operation (firing every minute since 2026-05-14 deploy) provides the cadence evidence; Path C's burst is a different test.

### 1.6 `decision_trace.price_overlay_eval` — not in Path C scope

Verified live via ambient operation since 2026-05-14 22:39 CT (post-Phase-1 verification snapshot in earlier session notes: `tick_id=789d6d4de4dc414c85ef207592981e21, price_cents=4.7, prev_tier=normal, new_tier=normal, outcome=held, reason_code=PRICE_OVERLAY_NORMAL_BELOW_TRIGGER, hold_minutes_remaining=null`). Re-testing in Path C would have duplicated ~30 lines of caller-side classification logic from `_evaluate_layer_inputs`; the production code is already running once per scheduler tick (~1/min in commissioning verbose mode).

---

## 2. Pre/post flight + cleanup

| Check | Before run | After run |
|---|---|---|
| `SCHEDULER_MODE` | `shadow` | `shadow` |
| `overrides.json` | 1 entry (expired 2026-05-02 NORMAL test) | identical |
| `applied=true` thermostat write count (last 30m / 10m) | 0 | 0 |
| Container health | healthy | healthy |
| Unexpected errors/warns | 0 | 0 (one expected warn from `normal_missing_temps` scenario, see §1.4) |

**No cleanup required.** Path C did not write to InfluxDB, did not modify `overrides.json`, did not change `SCHEDULER_MODE`, did not push to thermostat. The trace lines persist in Loki tagged `commission_20260515T035818Z_*` — identifiable as commissioning data forever, excluded from any analysis by virtue of the tick_id prefix.

Verification: `curl -s 'http://localhost:3100/loki/api/v1/query_range?... |~ "commission_20260515T035818Z_"'` returns the 27 commissioning trace lines; any analysis pipeline that wants to exclude them filters by that prefix.

---

## 3. Path B1 — live scheduler-loop commissioning (pending)

### 3.1 Goal

Verify the scheduler's tick-loop wiring under real conditions: `run_schedule_check` → `_evaluate_layer_inputs` → `_push_layer_change_mid_period` actually share a single `tick_id` per tick, mid-period repush no-push short-circuit mechanic works, real-clock cadence is sane.

### 3.2 What B1 will NOT cover

- All rule branches. B1 fires only the combinations naturally produced by real forecast + real ComEd prices + the day-type forced by the override. Most reason_codes won't fire under May Chicago weather. **Rule-branch coverage is Path C's job, not B1's.**

- HOT day-type, scarcity-tier price, 5CP active. These depend on summer weather + grid events. Even with a forced NORMAL override, real conditions in mid-May won't produce a scarcity-tier ComEd print or 5CP-eligible PJM load.

### 3.3 Proposed approach (pending operator decision)

Three options for getting B1 coverage:

- **(B1.a)** Scoped day-type override for 2026-05-15 only (`from_date=2026-05-15, to_date=2026-05-15, day_type=NORMAL`). Tomorrow's NORMAL schedule has 13:00 COAST + 19:00 RECOVER + 22:00 SLEEP — three opportunities to capture `layer_resolution` + `supervisor` traces through the real tick loop. Plus 06:00 + 11:00 revisits + 21:00 nightly = three opportunities for `day_type_decision`. Wait time: ~14 hours to first observation.

- **(B1.b)** Passive observation over several days. No override needed. Wait for natural forecast variability to produce a non-MILD day. Risk: may not happen before 2026-05-30 OSF filing.

- **(B1.c)** No B1 pass before OSF filing. Test coverage of the tick-loop wiring (`test_causal_chain_reconstructable_from_log`) is the substitute. Path C results stand as the commissioning artifact.

### 3.4 Cleanup procedure for B1 (if B1.a is taken)

Per Chris's 2026-05-14 instruction: "Do not rely on remove-the-override-file-entry as cleanup if the entry expires by date. The required cleanup is: verify after the date/tick window that no active override remains and scheduler is still in shadow/no-write behavior."

Concrete checks:

1. After the observation window closes (e.g., 2026-05-16 00:01 CT or after the last 22:00 action of 2026-05-15), query `find_active_override(overrides, today_iso=...)` returns None for any subsequent `today_iso`.
2. `SCHEDULER_MODE` still `shadow`.
3. Zero `applied=true` thermostat writes in `hvac.actions` since override added.
4. Loki query for `decision_trace.supervisor` lines in the override window: `final_cool_f` values match the supervisor's clamped/approved decisions, none reached `execute_action`'s thermostat write path (gated by `_writes_allowed()` which returns False in shadow mode).

If all four green: cleanup verified. The override entry can stay in `overrides.json` as a historical record (date-scoped, no active effect) or be removed manually — operator preference.

---

## 4. Caveats (explicit framing per Chris's 2026-05-14 instruction)

- **Path C does NOT prove full live-loop behavior.** It proves trace emission code is correct under controlled inputs through real production functions. The scheduler tick loop, FiringState mutation across ticks, and the mid-period repush no-push short-circuit are NOT exercised.

- **Path B1 (when run) will NOT cover all rule branches.** It only fires the combinations naturally produced by real forecast + ComEd prices on the forced day-type.

- **Neither pass exercises** HOT day-types under live conditions (Chicago mid-May), 5CP scope detection under live PJM load, or scarcity-tier price activity. Those depend on summer weather + grid events naturally occurring during the experimental window.

- **The two paths are complementary, not redundant.** C verifies "code paths emit traces correctly." B1 verifies "live scheduler wires the code paths together correctly." Both are necessary for full commissioning confidence; either alone is incomplete.

---

## 5. Open items for OSF filing

- B1 decision (B1.a / B1.b / B1.c per §3.3) — operator's call.
- Investigation: tonight's 21:00 `decision_trace.precool_decision` line reported `PRECOOL_REJECTED_NO_DA_LMP_DATA` for `decision_for_date=2026-05-15`. PJM typically publishes day-ahead LMP by ~16-17 CT for next day. Either the `pjm-dm2-poller` missed today's pull, the Influx lookup window is off, or the wrapper's date logic has an edge issue. Not a decision-trace bug (the trace correctly reported what happened); a real signal worth following up before OSF.

---

## 6. Sign-off

Path C commissioning artifact considered complete and acceptable for OSF filing as scaffolding evidence that the decision-trace emission paths work end-to-end under real container conditions. Test coverage in `test_decision_trace.py` (293 passing tests, 0 xfailed) plus this artifact substitute for B1 if B1 is deferred.

Pending Chris's decision on §3.3 and §5.
