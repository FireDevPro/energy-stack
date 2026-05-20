---
date: 2026-05-19
owner: chris
status: draft
role-label: chris
---

# ComEd Price Freshness — Design Spec

## 1. Purpose

Fix the freshness-blindness bug class observed on 2026-05-19 at 19:18Z, where the hvac-scheduler downgraded its price-overlay tier from `elevated` to `normal` based on an 8-minute-old ComEd bucket while real-time prices were spiking. Close two coupled gaps in one cohesive change:

- **Data gap.** `fetch_latest_comed` returns a bare `float | None`, discarding the bucket's `_time`. Callers cannot determine freshness even if they wanted to.
- **Behavior gap.** Even with freshness visible, no rule prevents the controller from making downgrade decisions on stale-but-present data. The 19:18Z bug fired specifically at the moment minimum-hold expired — the first tick where a downgrade evaluation could happen — and the data the decision relied on represented a 5-minute interval that had already ended 8 minutes earlier.

A solution that addresses only the data gap is incomplete. The bucket at 19:18Z would classify as "fresh" by any reasonable feed-health definition (cockpit's 11-min threshold; handoff's measured median publish jitter is 8.7 min). The bug requires a second control rule beyond the unified freshness label.

This spec is PR 1. A separate spec (PR 2, after PR 1 merges) will scope a Python type checker for the scheduler + cockpit-backend trees.

## 2. Scope

### In scope (PR 1)

- Shared `freshness.py` module at canonical scheduler location, hand-paired with the cockpit's existing local copy, enforced by a CI drift-check workflow.
- `PriceSample(cents_per_kwh, source_ts, freshness)` frozen dataclass returned by `fetch_latest_comed`; scheduler-local, cockpit unchanged.
- Recency gate inside `evaluate_price_overlay` that refuses **downgrade** decisions (and only downgrades) when the bucket's interval ended more than 7 minutes ago. Holds and upgrades unaffected.
- Rename `FiringState.price_feed_last_ok_at_utc` → `last_fresh_bucket_source_ts`. Update only on `freshness == "fresh"` reads. Use `sample.source_ts` (the bucket's `_time`), not `now_utc`.
- Unified safety release: when fresh data has been absent for 30 minutes — regardless of whether the sample is `None` or persistently warn/stale — release tier back to normal with a loud log. Single rule, two reason codes distinguishing no-data vs persistent-stale forensics.
- Audit telemetry fix: `hvac.input_feed_health.price_ok` derived directly from `sample.freshness == "fresh"`, not from a stored timestamp.
- New decision-trace fields and reason codes: `bucket_age_sec` on every `decision_trace.price_overlay_eval` emission; `HELD_DOWNGRADE_BUCKET_STALE`, `RELEASED_NO_DATA`, `RELEASED_PERSISTENT_STALE` on `PriceOverlayCode`.
- All 4 production callers of `fetch_latest_comed` updated to the new signature.
- ~14 existing test mocks migrated to return `PriceSample` instead of bare floats; `_fresh_sample()` test helper added.
- Outside-in acceptance test simulating the 19:18Z scenario, initially marked `pytest.mark.xfail(strict=True)`; marker removed in the same commit that lands the implementation.

### Out of scope (PR 1)

- Python type checker (PR 2, separate spec).
- Cockpit frontend changes. Cockpit's data flow is unchanged; only its `freshness.py` gets a header-comment update pointing to the canonical scheduler copy.
- Poller changes. The poller correctly writes `_time = bucket millisUTC`; no fix needed at that layer.
- 5CP detector freshness (separate data path, separate concerns; not in the 19:18Z bug class).
- Telegram-notifier's parallel `check_pjm_feed_freshness` (`telegram-notifier/app.py:554-661`) — different vocabulary, different mechanism, future-unification spec.
- Frontend `freshness.ts` codegen / automated syncing — handoff explicitly punts this to a Phase-N future.
- Historical audit-row migration. Pre-fix `hvac.input_feed_health.price_ok=True` rows are not rewritten; the semantic shift is forward-only and visible at the deploy timestamp.

## 3. Architecture

### 3.1 Shared freshness module

**Canonical location:** `deploy/energy-stack/hvac-scheduler/freshness.py`. The scheduler is the most important consumer; the file lives next to it for Docker COPY simplicity (the scheduler's Dockerfile build context is `./hvac-scheduler/` only).

**Exports:**

- `Freshness = Literal["fresh", "warn", "stale", "missing"]` — four-state label set matching the cockpit's existing vocabulary.
- `Thresholds` frozen dataclass with `fresh_max_ms`, `warn_max_ms`, `stale_max_ms`.
- `_min(n)` / `_hr(n)` helpers (already used in the cockpit copy).
- `THRESHOLDS: dict[str, Thresholds]` — per-source table. Sources include `"comed.prices"` (11/16/30 min, calibrated for operator feed-health UI), plus the rest of the cockpit's existing entries (`nws.forecast`, `pjm.load_forecast`, etc.) for full source-of-truth coverage.
- `classify(source: str, age_ms: int) -> Freshness`.

**Hand-paired duplicate at** `tools/cockpit/backend/freshness.py`. Content is byte-identical to the scheduler's copy except for the header docstring, which points to the canonical source. Drift is enforced by a new CI workflow.

**The TS file** at `tools/cockpit/frontend/src/freshness.ts` continues to be a separate hand-paired copy (cannot import Python). Codegen to automate this is explicitly out of scope per the handoff.

### 3.2 PriceSample dataclass (scheduler-local)

```python
@dataclass(frozen=True)
class PriceSample:
    cents_per_kwh: float
    source_ts: datetime      # Bucket's _time from Influx (interval-end of the 5-min window)
    freshness: Freshness     # Imported from freshness.py
```

Lives in `deploy/energy-stack/hvac-scheduler/app.py` next to the existing Influx-query helpers (after line 787 banner). NOT in the shared `freshness.py` module — the cockpit doesn't need this dataclass; it reads price data into a JSON-shaped dict for snapshot serialization. Keeping `PriceSample` scheduler-local prevents premature generalization (rule #4 — surgical).

### 3.3 fetch_latest_comed (new signature)

```python
def fetch_latest_comed(
    query_api, bucket: str, *, now_utc: datetime
) -> Optional[PriceSample]:
    """Read the latest comed.prices 5-min row; bundle value + bucket _time + freshness."""
```

Body:
- Query as today (`fq_latest_comed_5min`, unchanged).
- For the first row with non-None `_value`:
  - Extract `cents = float(record.get_value())`.
  - Extract `source_ts = record.get_time()`. If `None`, raise `ValueError` (fail-loud — bad Influx state should be visible).
  - Compute `age_ms = (now_utc - source_ts).total_seconds() * 1000`.
  - Compute `label = classify("comed.prices", age_ms)`.
  - Note: `label` can be one of `"fresh" | "warn" | "stale"` here. `"missing"` is reserved for the `None` return case.
  - Return `PriceSample(cents, source_ts, label)`.
- If no row has a non-None value, return `None`.

The `"missing"` state of the four-state Literal is represented by the `None` return rather than a dataclass with `freshness="missing"` and `cents=None`. Matches the cockpit's existing `query_latest_price` return convention; avoids tagged-union encoding issues in static type checking.

### 3.4 Recency gate in evaluate_price_overlay

Signature change:

```python
def evaluate_price_overlay(
    current_price_cents: float,
    source_ts: datetime,
    state: PriceOverlayState,
    now_utc: datetime,
    minimum_hold_minutes: int = DEFAULT_MINIMUM_HOLD_MINUTES,
    downgrade_recency_cutoff: timedelta = DEFAULT_DOWNGRADE_RECENCY_CUTOFF,
) -> tuple[Optional[PriceTier], PriceOverlayState]:
```

The gate adds one branch in the existing downgrade path at `price_overlay.py:186-203`:

```python
# Existing: min-hold check
if not _hold_elapsed(state, now_utc, minimum_hold_minutes):
    return current_tier_obj, state

# Existing: price-above-release check
if current_price_cents >= current_tier_obj.release_price_cents_per_kwh:
    return current_tier_obj, state

# NEW: recency gate — refuse downgrade on stale-but-present data
if now_utc - source_ts > downgrade_recency_cutoff:
    return current_tier_obj, state

# Falls through to downgrade as before.
```

**Strict greater-than** at the boundary (`>`, not `>=`): age exactly 7 min fires the downgrade; age 7 min + 1 sec refuses. The 19:18Z bug was at 8 min — past the cutoff with 1 min buffer.

**Module constant** in `price_overlay.py` (single source of truth — no duplication in `app.py`):

```python
DEFAULT_DOWNGRADE_RECENCY_CUTOFF = timedelta(minutes=7)
```

Reasoning (full empirical justification in §6 below):
- Must be < 8 min to catch the observed bug threshold.
- Median first-seen publish lag is 5.7 min; cutoff < 5.7 min would mean downgrades never fire.
- 7 min gives 1 min buffer below the bug threshold AND leaves a realistic actionable window (~20% of each 5-min cycle).
- 6 min was considered and rejected — too tight, false-holds would compound.
- 8 min was considered and rejected — zero buffer below the observed bug.

### 3.5 Unified safety release (new)

Replaces the existing None-only release path at `app.py:2402-2425`. Single rule: when `firing.last_fresh_bucket_source_ts` indicates no fresh data has arrived for `PRICE_FEED_STALE_THRESHOLD` (30 min), release the tier back to normal.

Per-tick flow inside `_evaluate_layer_inputs`:

```python
sample = fetch_latest_comed(query_api, cfg.influx_bucket, now_utc=now_utc)
prev_tier = firing.price_overlay_state.current_tier

# Step 1: update last-fresh field if applicable.
if sample is not None and sample.freshness == "fresh":
    firing.last_fresh_bucket_source_ts = sample.source_ts

# Step 2: check unified safety release.
last_fresh = (firing.last_fresh_bucket_source_ts
              or firing.price_overlay_state.triggered_at_utc)
no_fresh_for_too_long = (
    last_fresh is None
    or now_utc - last_fresh > PRICE_FEED_STALE_THRESHOLD
)

if no_fresh_for_too_long and prev_tier != NORMAL_TIER_NAME:
    # Safety release: distinct reason codes for forensics.
    release_reason = (PriceOverlayCode.RELEASED_NO_DATA if sample is None
                      else PriceOverlayCode.RELEASED_PERSISTENT_STALE)
    log("warn", "price_feed_stale_tier_released", ...)
    firing.price_overlay_state = PriceOverlayState(
        current_tier=NORMAL_TIER_NAME,
        triggered_at_utc=None,
    )
    # ... outputs set to normal

elif sample is None:
    # Within carry-forward window: preserve tier.
    ...

else:  # sample is not None
    # Evaluate overlay with recency gate inside.
    active_tier, firing.price_overlay_state = evaluate_price_overlay(
        sample.cents_per_kwh, sample.source_ts, ...
    )
    ...
```

**Worst-case persistence bound:** 60 minutes from any tier trigger to forced safety release. Walked through in §5 below. Reasonable upper bound; aligns the per-tick check pattern's predictable time-to-release with operator expectations.

### 3.6 Audit telemetry (corrected)

`hvac.input_feed_health.price_ok` at `app.py:2856-2887` post-fix:

```python
price_ok = (sample is not None and sample.freshness == "fresh")
```

Derived directly from the current sample's freshness label, not from any stored timestamp. The audit row now matches what the controller actually observed (single source of truth — rule #6).

### 3.7 Decision-trace integration

`decision_trace.price_overlay_eval` emission at `app.py:2487-2501` gains:

- **New field:** `bucket_age_sec = (now_utc - sample.source_ts).total_seconds()` when sample is non-None; `null` when sample is None.
- **New outcome branches:**
  - Gate-refused downgrade → `outcome="held"`, `reason_code="PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_STALE"`, `level="info"`.
  - Safety release on no-data → `outcome="released"`, `reason_code="PRICE_OVERLAY_RELEASED_NO_DATA"`, `level="warn"`.
  - Safety release on persistent-stale → `outcome="released"`, `reason_code="PRICE_OVERLAY_RELEASED_PERSISTENT_STALE"`, `level="warn"`.

The existing `STALE_FEED_RELEASED` enum value is renamed `RELEASED_NO_DATA` to make room for the new persistent-stale code; OSF is not yet filed so the rename is in-window per `decision_codes.py`'s append-only-post-OSF discipline.

### 3.8 Cockpit unchanged

The cockpit's existing flow (`tools/cockpit/backend/influx.py:117-138` → `snapshot.py:142-159` → frontend `freshness.ts`) is functionally unchanged. The only edit is a header-comment update on `tools/cockpit/backend/freshness.py` to point to the canonical scheduler copy. The drift-check workflow enforces byte-equality of the two Python files.

Cockpit operators will observe the new `bucket_age_sec` field on `decision_trace.price_overlay_eval` rows. If desired, the cockpit's decision-trace panel can surface this field in a follow-up cockpit PR (out of scope here).

## 4. Components

### New files (2)

| File | Purpose | LOC est. |
|------|---------|----------|
| `deploy/energy-stack/hvac-scheduler/freshness.py` | Canonical shared module (Freshness literal, Thresholds, THRESHOLDS dict, classify). | ~65 |
| `.github/workflows/check-freshness-drift.yml` | PR-triggered workflow that diffs the two Python copies; fails the PR on drift. | ~20 |

### Modified production files (4)

| File | Changes |
|------|---------|
| `deploy/energy-stack/hvac-scheduler/app.py` | Add `PriceSample` dataclass. Update `fetch_latest_comed` signature/body. Rename `FiringState` field. Update `_evaluate_layer_inputs` (sample branching, unified safety release, audit derivation). Update 3 audit-log callers. Update `decision_trace.price_overlay_eval` emission (new field + outcome branches). |
| `deploy/energy-stack/hvac-scheduler/price_overlay.py` | Update `evaluate_price_overlay` signature; add recency gate as new conditional. Add `DEFAULT_DOWNGRADE_RECENCY_CUTOFF = timedelta(minutes=7)` module constant with empirical-justification comment block. |
| `deploy/energy-stack/hvac-scheduler/decision_codes.py` | Append new enum values: `HELD_DOWNGRADE_BUCKET_STALE`, `RELEASED_NO_DATA` (renamed from `STALE_FEED_RELEASED`), `RELEASED_PERSISTENT_STALE`. |
| `tools/cockpit/backend/freshness.py` | Header docstring updated to point to canonical scheduler copy. Content otherwise byte-identical to scheduler's. |

### Modified test files (3)

| File | Changes |
|------|---------|
| `deploy/energy-stack/hvac-scheduler/test_price_overlay.py` | Add unit tests for the recency gate: refuses-downgrade-when-old, allows-when-fresh, doesn't-affect-upgrades, doesn't-affect-holds, boundary cases (exact cutoff, cutoff+1s). |
| `deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py` | Outside-in acceptance test (19:18Z replay), initially `xfail(strict=True)`. New unit tests for `fetch_latest_comed` shape (PriceSample construction, None on empty, raises on missing _time, warn/stale classification). FiringState rename + tightened semantics. Audit telemetry derivation test. ~14 mock migrations to use `_fresh_sample()` helper. |
| `deploy/energy-stack/hvac-scheduler/conftest.py` | Add `_fresh_sample(cents, *, age_min=1, now_utc=None)` test helper. |

### Total diff estimate

~400-500 lines across 9 files. Approximately 50-60% test changes, 30% production code, 10% new infrastructure.

## 5. Data flow and safety bounds

### 5.1 Per-tick flow

```
Influx row (_time, _value)
    │
    ▼
fetch_latest_comed (read → compute age → classify)
    │
    ▼
Optional[PriceSample(cents_per_kwh, source_ts, freshness)]
    │
    ├── _evaluate_layer_inputs (critical path)
    │      ├── if fresh: update firing.last_fresh_bucket_source_ts
    │      ├── unified safety release check (one rule, two reason codes)
    │      ├── evaluate_price_overlay(cents, source_ts, ...)
    │      │      └── recency gate inside (refuses downgrade if source_ts > 7 min old)
    │      ├── decision_trace.price_overlay_eval (+ bucket_age_sec, + reason_code)
    │      └── hvac.input_feed_health (price_ok from sample.freshness)
    │
    └── 3 audit-log callers (unwrap to float, pass to write_decision)
```

### 5.2 Safety bound walkthrough

**Worst-case timing for stale-data-triggered release (data goes stale exactly when min-hold expires):**

```
T=0   min   Trigger: tier goes elevated. triggered_at_utc=T=0. last_fresh=T=0.
T=29  min   Last fresh bucket lands. last_fresh=T=29.
T=30  min   Min-hold elapses. Downgrade is now possible (subject to price + recency).
T=30+ min   ComEd API breaks. No new fresh buckets arrive.
T=30-44     Bucket ages from 1 → 15 min. Goes from fresh → warn → stale.
            Recency gate refuses downgrade attempts (age > 7 min).
            Safety release: now - last_fresh < 30 min, NOT yet triggered.
T=59  min   now - last_fresh = 30 min. AT threshold.
T=60  min   now - last_fresh = 31 min. > threshold. Safety release fires.
            Tier returns to NORMAL. Loud log emitted.
```

**Maximum 60 minutes** from any trigger to forced safety release in the worst case. In any other timing pattern, the release fires earlier (min-hold window and stale-release window overlap progressively).

### 5.3 Failure-mode coverage

| Scenario | Mechanism | Time to release |
|----------|-----------|-----------------|
| ComEd API hard-down | Bucket eventually drops out of 30-min Influx query window. `sample is None`. Safety release fires. | ~30 min after API break |
| Influx outage | Query returns nothing. `sample is None`. Same path. | ~30 min after outage |
| Poller container crash (no Docker restart) | Same as above. | ~30 min |
| ComEd publishes with sustained lag (always 11+ min) | Sample non-None but never `fresh`. Field never updates. Unified safety release fires via persistent-stale branch. | ~30 min after lag onset |
| Brief publish jitter (one missed cycle, 10-15 min) | Field stops updating but resumes when next fresh bucket arrives. Tier preserved, no release. | N/A — recovers naturally |

## 6. Cutoff calibration

The 7-minute downgrade recency cutoff is the one judgment-call number in the design. Reasoning:

**Empirical inputs** (from handoff §"Empirical Data," based on 12 hours of poller logs):
- First-seen publish lag: median 5.7 min, p90 7.7 min, p99 9.7 min, max 9.7 min.
- Bucket-age sawtooth: median 8.7 min, p95 11.7 min, max 16.8 min.
- 2026-05-19 bug fired at exactly 8 minutes of bucket age.

**Cycle-coverage analysis** (estimated fraction of a typical 5-min cycle during which a downgrade attempt would succeed, i.e., bucket age < cutoff):

| Cutoff | On median cycles (first-seen 5.7 min) | On p90 cycles (first-seen 7.7 min) |
|--------|---------------------------------------|------------------------------------|
| 6 min  | ~6%   | 0% (bucket arrives past cutoff) |
| 7 min  | ~26%  | 0% |
| 8 min  | ~46%  | ~6% |
| 9 min  | ~66%  | ~26% |

**Decision criteria:**
1. Must be strictly < 8 min to catch the observed bug threshold with any buffer.
2. The threshold is calibrating proximity to "current moment" for downgrade safety, NOT feed health (the cockpit's `"comed.prices"` 11/16/30 thresholds calibrate feed health independently).
3. Cost asymmetry: false-hold = comfort drift of 1-2°F for ~15-25 minutes until next fresh bucket allows re-evaluation. False-downgrade = wrong tier during a continuing spike, real dollar cost.

**6 min rejected:** too tight. Actionable window collapses to ~5% of cycles averaged across the lag distribution. Downgrades would compound false-holds.

**8 min rejected:** zero buffer below the observed bug. Marginal measurement noise (or a near-miss bug at 7.5 min) defeats it.

**7 min selected:** 1 min buffer below the observed bug; ~20% actionable window per cycle averaged across the lag distribution; expected false-hold duration on legitimate price drops is ~15-25 min (3-5 cycles), which is cheap comfort drift relative to the dollar cost of a false downgrade during a spike.

The cutoff is reviewable. If field testing during the pre-OSF window reveals 7 min produces unacceptable false-holds (or doesn't reliably catch the bug class), the constant is one-line-editable. Re-locking the value post-OSF-filing requires a spec amendment.

## 7. Error handling

This fix IS a fail-loud remediation. Every new code path either succeeds visibly or fails visibly.

### Loud operator-visible signals

- **Gate refuses downgrade:** `decision_trace.price_overlay_eval` at `info` level (not debug) with `reason_code="PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_STALE"` and `bucket_age_sec`. Operator sees the refusal in Loki without verbose mode.
- **Safety release fires:** `decision_trace.price_overlay_eval` at `warn` level with one of `RELEASED_NO_DATA` / `RELEASED_PERSISTENT_STALE`. Separate `log("warn", "price_feed_stale_tier_released", ...)` entry for legacy operators / dashboards already querying that log line.
- **Influx row missing `_time`:** `fetch_latest_comed` raises `ValueError`. Process dies; Docker restarts. Matches the convention referenced in AGENTS.md (>8 distinct fail-loud sites already in the codebase).
- **Drift between scheduler/cockpit Python files:** CI workflow `check-freshness-drift.yml` blocks PR merge on `diff` non-zero.
- **Outside-in acceptance test regression:** `xfail(strict=True)` flips to xpassed→failed if anyone breaks the gate later. Loud signal at every PR boundary.
- **Test mock migration miss:** any unmigrated `lambda q, b: 4.5` mock fails with `AttributeError: 'float' object has no attribute 'cents_per_kwh'` immediately in pytest.

### Acceptable silent-by-design behaviors

- **Negative bucket age** (clock skew or test injection): treated as fresh (negative ≤ fresh_max_ms). Gate permits downgrade. Acceptable — future-dated buckets are a non-fault edge case.
- **Pre-fix audit rows** (`hvac.input_feed_health.price_ok` recorded under old semantics): not migrated. Operators reviewing data spanning the deploy see the semantic shift at the deploy timestamp. Documented; the pre-fix values were the bug.

## 8. Testing

### 8.1 Outside-in acceptance test (the north star)

`test_hvac_scheduler.py::test_19_18z_downgrade_refused_on_stale_bucket`. Replays the 2026-05-19 19:18Z scenario:

- FiringState in `elevated` tier with `triggered_at_utc = now - 30min - 1s` (min-hold elapsed).
- Mock returns `PriceSample(cents_per_kwh=2.5, source_ts=now - 8min, freshness="fresh")`.
- Tick runs at `now = 19:18Z`.

Assertions:
- Tier unchanged (`current_tier == "elevated"`).
- `decision_trace.price_overlay_eval` has `outcome="held"`, `reason_code="PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_STALE"`, `bucket_age_sec ≈ 480`.
- `hvac.input_feed_health.price_ok == True` (feed itself is healthy; 8 min < cockpit's 11-min fresh threshold).

Initial marker: `pytest.mark.xfail(strict=True)`. Marker removed in the same commit that lands the implementation. Per AGENTS.md outside-in TDD rule.

### 8.2 Unit tests for the recency gate (`test_price_overlay.py`)

- `test_gate_refuses_downgrade_when_bucket_too_old`
- `test_gate_allows_downgrade_when_bucket_fresh`
- `test_gate_does_not_affect_upgrade`
- `test_gate_does_not_affect_hold_within_tier`
- `test_gate_boundary_at_exact_cutoff` (age == 7.0 min → downgrade fires)
- `test_gate_boundary_at_cutoff_plus_one_second` (age 7 min 1 sec → refused)

### 8.3 Unit tests for `fetch_latest_comed` new shape

- `test_fetch_latest_comed_returns_PriceSample_when_row_exists`
- `test_fetch_latest_comed_returns_None_when_no_row`
- `test_fetch_latest_comed_raises_when_time_missing`
- `test_fetch_latest_comed_classifies_warn_age`
- `test_fetch_latest_comed_classifies_stale_age`

### 8.4 FiringState semantic test

- `test_last_fresh_bucket_source_ts_updates_only_on_fresh_read` (renamed/extended from existing `test_price_feed_last_ok_at_utc_updates_on_healthy_tick`):
  - Fresh sample → field updates to `sample.source_ts`.
  - Warn sample → field unchanged.
  - Stale sample → field unchanged.
  - None sample → field unchanged.

### 8.5 Audit telemetry test

- `test_input_feed_health_price_ok_reflects_sample_freshness`:
  - Sample fresh → `price_ok=True`.
  - Sample warn / stale / None → `price_ok=False`.

### 8.6 Unified safety release test

- `test_safety_release_on_persistent_stale`: sample returns warn/stale for 30+ min → tier releases with `RELEASED_PERSISTENT_STALE` reason.
- `test_safety_release_on_no_data`: sample returns None for 30+ min → tier releases with `RELEASED_NO_DATA` reason.
- `test_safety_release_recovers_naturally`: brief stale period followed by fresh data → no release, tier preserved.

### 8.7 Mock migration

14+ existing mocks (`test_hvac_scheduler.py` lines ~588, 630, 665, 690, 712, 754, 787, 817, 841, 1173, 1250, 1454, 2193+) migrate from:

```python
monkeypatch.setattr(app, "fetch_latest_comed", lambda q, b: 4.5)
```

to:

```python
monkeypatch.setattr(app, "fetch_latest_comed",
                    lambda q, b, *, now_utc: _fresh_sample(4.5, now_utc=now_utc))
```

`_fresh_sample` helper added to `conftest.py`. Mechanical migration; intent unchanged.

### 8.8 Test commands

Per AGENTS.md:
- Canonical: `bash deploy/energy-stack/run_tests.sh`.
- Just scheduler: `cd deploy/energy-stack/hvac-scheduler/ && python -m pytest .`.
- Drift check (local): `diff deploy/energy-stack/hvac-scheduler/freshness.py tools/cockpit/backend/freshness.py`.

PR 1 gates on: all three green. Plus pre-merge operational validation (§9 below).

## 9. Operational validation gate

Per AGENTS.md Multi-Phase Feature Workflow §4 ("required real-shape replay or operational validation gates pass"): after PR 1 merges and is deployed via the merge-to-main → deploy workflow, validate via one of the following:

**Path A — Live spike event before June 1.** Operator monitors Loki for the next ComEd spike. Confirms:
- New `bucket_age_sec` field appears on every `decision_trace.price_overlay_eval` row.
- During the spike, downgrade decisions occur ONLY when latest bucket age ≤ 7 min.
- Slow-publish cycles produce the new `HELD_DOWNGRADE_BUCKET_STALE` reason code.

**Path B — Historical replay if no live spike materializes.** Synthetic test running the new controller against historical Influx data from 2026-05-19 19:10Z-19:30Z window; assert the gate fires at 19:18Z. Scoped as a follow-up if Path A doesn't trigger before June 1.

Operational validation result is appended to this spec under §10 (Status & history) once observed.

## 10. Status & history

| Date       | Status | Change |
|------------|--------|--------|
| 2026-05-19 | draft  | Initial spec authored via brainstorming session. Decisions Q1-Q7 + safety-release extension locked. Outside-in acceptance test scoped, not yet implemented. |

Spec moves to `approved` after user review (per brainstorming skill flow). Then to `implementing` once the writing-plans skill produces the implementation plan and code work begins. Then to `shipped` once PR 1 merges + operational validation completes.

## 11. References

- Handoff: `STALE_DATA_HANDOFF.md` (repo root)
- Bug timeline: handoff §"The Bug Event"
- Empirical data: handoff §"Empirical Data on ComEd's Publish Behavior"
- Existing freshness module: `tools/cockpit/backend/freshness.py`, `tools/cockpit/frontend/src/freshness.ts`
- Existing price overlay logic: `deploy/energy-stack/hvac-scheduler/price_overlay.py`
- Existing decision-trace codes: `deploy/energy-stack/hvac-scheduler/decision_codes.py`
- Project conventions: `AGENTS.md`
- Outside-in TDD discipline: `AGENTS.md` §"Core coding rules" #4
- Plan-authoring rules (for the next phase): `AGENTS.md` §"Plan-authoring discipline"
- Branching policy: `AGENTS.md` §"Branching policy"
