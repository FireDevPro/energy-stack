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

A solution that addresses only the data gap is incomplete. Pre-fix, the bucket at 19:18Z classified as "fresh" by the cockpit's existing 11-min threshold while still being too old to safely make a downgrade decision. This spec collapses the two concepts into one — tightening the unified `"comed.prices"` fresh threshold to 7 min, used identically by cockpit and scheduler so the operator sees the same actionability the controller does.

This spec is PR 1. A separate spec (PR 2, after PR 1 merges) will scope a Python type checker for the scheduler + cockpit-backend trees.

## 2. Scope

### In scope (PR 1)

- Shared `freshness.py` module at canonical scheduler location, hand-paired with the cockpit's existing local copy, enforced by a CI drift-check workflow.
- `"comed.prices"` fresh threshold tightened from 11 min to 7 min across both consumers (cockpit + scheduler). Single source of truth; no separate scheduler-specific cutoff constant.
- `PriceSample(cents_per_kwh, source_ts, freshness)` frozen dataclass returned by `fetch_latest_comed`; scheduler-local, cockpit unchanged.
- Recency gate inside `_evaluate_layer_inputs` (the per-tick caller) that refuses **downgrade** decisions when `sample.freshness != "fresh"`. Holds and upgrades unaffected. The `evaluate_price_overlay` state machine stays freshness-agnostic (pure function of price + state + time-of-day).
- Rename `FiringState.price_feed_last_ok_at_utc` → `last_fresh_bucket_source_ts`. Update only on `freshness == "fresh"` reads. Use `sample.source_ts` (the bucket's `_time`), not `now_utc`.
- Unified safety release: when fresh data has been absent for 30 minutes — regardless of whether the sample is `None` or persistently warn/stale — release tier back to normal with a loud log. Single rule, two reason codes distinguishing no-data vs persistent-stale forensics.
- Audit telemetry fix: `hvac.input_feed_health.price_ok` derived directly from `sample.freshness == "fresh"`, not from a stored timestamp. Now equivalent to "controller would consider this data actionable" — mirrors cockpit display exactly.
- New decision-trace fields and reason codes: `bucket_age_sec` on every `decision_trace.price_overlay_eval` emission; existing misnamed `price_is_stale` field renamed to `price_feed_unavailable`; `HELD_DOWNGRADE_BUCKET_AGE`, `RELEASED_NO_DATA`, `RELEASED_PERSISTENT_STALE` on `PriceOverlayCode` (the existing `STALE_FEED_RELEASED` renames to `RELEASED_NO_DATA` for naming consistency).
- All 4 production callers of `fetch_latest_comed` updated to the new signature. Three of the four are day-type audit-log paths that simply unwrap `sample.cents_per_kwh if sample else None` for `write_decision`; the fourth (per-tick caller) applies the recency gate.
- `fetch_latest_comed` catches malformed Influx rows (missing `_time`), logs an `error`-level event, and returns `None` to preserve safety-supervisor continuity. Does NOT raise — see §7 for rationale.
- ~14 existing test mocks migrated to return `PriceSample` instead of bare floats; `_fresh_sample()` test helper added.
- Outside-in acceptance test simulating the 19:18Z scenario, initially marked `pytest.mark.xfail(strict=True)`; marker removed in the same commit that lands the implementation.

### Out of scope (PR 1)

- Python type checker (PR 2, separate spec).
- Cockpit frontend changes. Cockpit's data flow is unchanged; only its `freshness.py` gets a header-comment update pointing to the canonical scheduler copy.
- Poller changes. The poller correctly writes `_time = bucket millisUTC`; no fix needed at that layer.
- 5CP detector freshness (separate data path, separate concerns; not in the 19:18Z bug class).
- Telegram-notifier's parallel `check_pjm_feed_freshness` (`telegram-notifier/app.py:554-661`) — different vocabulary, different mechanism, future-unification spec.
- Frontend `freshness.ts` codegen / automated syncing — handoff explicitly punts this to a Phase-N future. The TS file gets the same threshold update by hand.
- Historical audit-row migration. Pre-fix `hvac.input_feed_health.price_ok=True` rows are not rewritten; the semantic shift is forward-only and visible at the deploy timestamp. (Pre-OSF, no amendment process needed.)
- Cockpit-side display of `bucket_age_sec`. The new field is emitted to Loki; operator-facing validation uses LogQL. Adding a cockpit panel for it is a follow-up cockpit PR if desired after field testing.

## 3. Architecture

### 3.1 Shared freshness module

**Canonical location:** `deploy/energy-stack/hvac-scheduler/freshness.py`. The scheduler is the most important consumer; the file lives next to it for Docker COPY simplicity (the scheduler's Dockerfile build context is `./hvac-scheduler/` only).

**Exports:**

- `Freshness = Literal["fresh", "warn", "stale", "missing"]` — four-state label set matching the cockpit's existing vocabulary.
- `Thresholds` frozen dataclass with `fresh_max_ms`, `warn_max_ms`, `stale_max_ms`.
- `_min(n)` / `_hr(n)` helpers (already used in the cockpit copy).
- `THRESHOLDS: dict[str, Thresholds]` — per-source table. The `"comed.prices"` entry tightens from 11/16/30 min (pre-fix cockpit value) to **7/16/30 min** to reflect the controller's actionability boundary; other entries (`nws.forecast`, `pjm.load_forecast`, etc.) copied verbatim from the cockpit's existing table.
- `classify(source: str, age_ms: int) -> Freshness`.

**Why 7/16/30 for `"comed.prices"`:**
- `fresh_max_ms = _min(7)` — the controller's downgrade-recency cutoff (full calibration justification in §6). Cockpit mirrors this so the operator sees the same actionability the controller does.
- `warn_max_ms = _min(16)` — unchanged from pre-fix. Roughly "one missed publish cycle" past the fresh boundary.
- `stale_max_ms = _min(30)` — matches `PRICE_FEED_STALE_THRESHOLD`. Bucket age past this triggers the unified safety release.

**Operator-visible UI impact:** the cockpit's freshness indicator will cycle green→yellow→green every ~5-min publish cycle (median bucket-age sawtooth peaks at 8.7 min, beyond the 7-min fresh boundary). This is BY DESIGN — yellow signals "the controller would refuse a downgrade decision RIGHT NOW," which is the correct operator signal. Yellow does NOT indicate feed sickness; bucket-age past 16 min (warn → stale) is when feed health concerns start. The header comment in `tools/cockpit/backend/freshness.py:40-47` is rewritten to explain the new semantics.

**Hand-paired duplicate at** `tools/cockpit/backend/freshness.py`. Content is byte-identical to the scheduler's copy except for the header docstring, which points to the canonical source. Drift is enforced by a new CI workflow.

**The TS file** at `tools/cockpit/frontend/src/freshness.ts` continues to be a separate hand-paired copy (cannot import Python). It gets the same 7/16/30 update. Codegen to automate this is explicitly out of scope per the handoff.

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
  - Extract `source_ts = record.get_time()`. If `None`: log `error` level structured event (`comed_row_missing_time`, with whatever record metadata is available for forensics), return `None`. Do NOT raise — see §7 for the safety-supervisor-continuity rationale.
  - Compute `age_ms = (now_utc - source_ts).total_seconds() * 1000`.
  - Compute `label = classify("comed.prices", age_ms)`.
  - Note: `label` can be one of `"fresh" | "warn" | "stale"` here. `"missing"` is reserved for the `None` return case.
  - Return `PriceSample(cents, source_ts, label)`.
- If no row has a non-None value, return `None`.

The `"missing"` state of the four-state Literal is represented by the `None` return rather than a dataclass with `freshness="missing"` and `cents=None`. Matches the cockpit's existing `query_latest_price` return convention; avoids tagged-union encoding issues in static type checking.

### 3.4 Recency gate in `_evaluate_layer_inputs` (caller-side)

`evaluate_price_overlay` in `price_overlay.py` stays freshness-agnostic — pure function of price + state + time-of-day, same signature as today (no `source_ts` parameter, no recency-cutoff parameter). The function continues to encapsulate min-hold + hysteresis + tier-priority semantics; freshness is a controller-level concern that belongs above it.

Why caller-side and not inside the state machine: there's only one caller of `evaluate_price_overlay` (the per-tick `_evaluate_layer_inputs` at `app.py:2434`), so the "gate would be duplicated across N callers" argument doesn't apply. The three day-type audit-log callers (`run_decision_revisit:2044`, `run_decision:2089`, `fetch_today_decision:2245`) never invoke `evaluate_price_overlay`; they unwrap `sample.cents_per_kwh` for `write_decision` audit metadata only. Co-locating the gate with `evaluate_price_overlay` would inject upstream-data-freshness into a module that deliberately knows nothing about its inputs' provenance.

The gate lives in `_evaluate_layer_inputs` immediately around the `evaluate_price_overlay` call:

```python
# Inside _evaluate_layer_inputs, after fetching sample and unified-safety-release check.

prev_tier = firing.price_overlay_state.current_tier
proposed_tier, proposed_state = evaluate_price_overlay(
    sample.cents_per_kwh,
    firing.price_overlay_state,
    now_utc,
)
proposed_tier_name = proposed_tier.name if proposed_tier else NORMAL_TIER_NAME

is_downgrade = _tier_priority(proposed_tier_name) < _tier_priority(prev_tier)

if is_downgrade and sample.freshness != "fresh":
    # Recency gate: refuse the downgrade; hold current tier; loud trace.
    # No state machine mutation — keep firing.price_overlay_state at prev_tier.
    active_tier = _tier_by_name(prev_tier)
    price_offset_f = active_tier.cool_setpoint_offset_f if active_tier else 0
    price_override_f = active_tier.cool_setpoint_override_f if active_tier else None
    price_tier_name = prev_tier
    # decision_trace.price_overlay_eval emission below records this as held + HELD_DOWNGRADE_BUCKET_AGE.
else:
    # Apply the state machine's proposal (upgrade, hold, or fresh-data downgrade).
    firing.price_overlay_state = proposed_state
    active_tier = proposed_tier
    # ... outputs from proposed_tier as today
```

**Why `sample.freshness != "fresh"` is the gate condition, not a separate threshold comparison:** the unified `"comed.prices"` fresh threshold IS the recency cutoff (§3.1 — 7 min). Asking "is the sample fresh?" and "is the bucket recent enough to downgrade?" are the same question now. One source of truth.

**Strict-`>` semantics at the boundary** are preserved by the existing `classify` function (`age_ms <= fresh_max_ms` is fresh; one millisecond more is warn). A bucket exactly at 7 min counts as fresh; the 19:18Z bug at 8 min would have classified as warn and the gate refuses.

**No new constants in `price_overlay.py`.** The downgrade-safety threshold is `THRESHOLDS["comed.prices"].fresh_max_ms` from the shared module — same value the cockpit uses for its UI.

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
    # Run the freshness-agnostic state machine to get its proposal.
    proposed_tier, proposed_state = evaluate_price_overlay(
        sample.cents_per_kwh, firing.price_overlay_state, now_utc,
    )
    proposed_name = proposed_tier.name if proposed_tier else NORMAL_TIER_NAME
    is_downgrade = tier_priority(proposed_name) < tier_priority(prev_tier)

    if is_downgrade and sample.freshness != "fresh":
        # Recency gate: refuse the downgrade. Do not mutate state.
        # Trace emission below records this as HELD_DOWNGRADE_BUCKET_AGE.
        active_tier = _tier_by_name(prev_tier)
        # ... use prev_tier's offset/override for outputs
    else:
        # Apply the state machine's proposal (upgrade / hold / fresh-data downgrade).
        firing.price_overlay_state = proposed_state
        active_tier = proposed_tier
        # ... use proposed tier's outputs
```

**Worst-case persistence bound:** 60 minutes from any tier trigger to forced safety release. Walked through in §5 below. Reasonable upper bound; aligns the per-tick check pattern's predictable time-to-release with operator expectations.

### 3.6 Audit telemetry (corrected)

`hvac.input_feed_health.price_ok` at `app.py:2856-2887` post-fix:

```python
price_ok = (sample is not None and sample.freshness == "fresh")
```

Derived directly from the current sample's freshness label, not from any stored timestamp. The audit row now matches what the controller actually observed AND what the operator sees on the cockpit (single source of truth — rule #6). With the unified 7-min fresh threshold, `price_ok=True` ↔ "controller would consider this data actionable for a downgrade decision" ↔ cockpit shows green for `comed.prices`. Three signals, one definition.

Pre-fix → post-fix: this is a tightening (was True for any feed reachable within 30 min; now True only when sample is fresh per the 7-min boundary). The pre-fix behavior was the bug — `price_ok=True` was being recorded for stale-but-present feeds, hiding the freshness-blindness from audit reviewers. The shift is forward-only; historical audit rows are not rewritten. Pre-OSF, no amendment process needed.

### 3.7 Decision-trace integration

`decision_trace.price_overlay_eval` emission at `app.py:2487-2501` gains and changes the following fields:

**New field:**
- `bucket_age_sec = (now_utc - sample.source_ts).total_seconds()` when sample is non-None; `null` when sample is None. Operator sees exactly how old the controller's data was at decision time.

**Renamed field:**
- Existing `price_is_stale` boolean at `app.py:2493` is renamed `price_feed_unavailable`. The current name was misleading even pre-fix (it just meant `current_price_cents is None`). The rename clarifies the semantic and prevents trace rows from looking contradictory post-fix (where a row can have `price_feed_unavailable=false` AND `freshness != "fresh"` AND `reason_code=HELD_DOWNGRADE_BUCKET_AGE`).

**New outcome branches in the classifier at lines 2453-2486:**
- Gate-refused downgrade → `outcome="held"`, `reason_code="PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"`, `level="info"`.
- Safety release on no-data → `outcome="released"`, `reason_code="PRICE_OVERLAY_RELEASED_NO_DATA"`, `level="warn"`.
- Safety release on persistent-stale → `outcome="released"`, `reason_code="PRICE_OVERLAY_RELEASED_PERSISTENT_STALE"`, `level="warn"`.

**Existing enum rename for naming consistency:** `STALE_FEED_RELEASED` → `RELEASED_NO_DATA`. The original name described the trigger condition (feed went stale) but the new semantic is more precise (no data is the specific cause; persistent-stale-with-data is a separate cause). Mechanical rename; existing tests that reference the old value update in the same PR.

**Visibility-level rationale:** the existing `FEED_UNAVAILABLE_TIER_PRESERVED` outcome (sample is None but within the carry-forward window) emits at `debug` because it's the common case during transient publish gaps. The new `HELD_DOWNGRADE_BUCKET_AGE` outcome emits at `info` because a refused-downgrade is a near-miss (the controller almost made an unsafe decision and the gate caught it) — operationally more significant than a routine publish gap. Safety releases (`RELEASED_*`) emit at `warn` because they reflect a real degraded state.

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
| `deploy/energy-stack/hvac-scheduler/app.py` | Add `PriceSample` dataclass. Update `fetch_latest_comed` signature/body (catch missing `_time`, log+return None). Rename `FiringState` field. Update `_evaluate_layer_inputs` (sample branching, unified safety release, **caller-side recency gate around `evaluate_price_overlay`**, audit derivation). Update 3 audit-log callers. Update `decision_trace.price_overlay_eval` emission (new `bucket_age_sec`, rename `price_is_stale` → `price_feed_unavailable`, new outcome branches). Add `_tier_priority` import from `price_overlay` for the downgrade-detection check. |
| `deploy/energy-stack/hvac-scheduler/price_overlay.py` | **No signature change.** Expose `_tier_priority` as `tier_priority` (drop the leading underscore so it's a public helper the caller uses). No new module constant. |
| `deploy/energy-stack/hvac-scheduler/decision_codes.py` | Rename existing `STALE_FEED_RELEASED` → `RELEASED_NO_DATA` for consistency. Append two new enum values: `HELD_DOWNGRADE_BUCKET_AGE`, `RELEASED_PERSISTENT_STALE`. |
| `tools/cockpit/backend/freshness.py` | Update `"comed.prices"` threshold to 7/16/30 min (was 11/16/30). Rewrite the header comment at lines 40-47 to explain the new semantics (7-min = controller's actionability boundary). Header docstring also updated to point to canonical scheduler copy. Content otherwise byte-identical. |
| `tools/cockpit/frontend/src/freshness.ts` | Same 7/16/30 threshold update (still hand-paired). Comment block updated similarly. |

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
fetch_latest_comed (read → compute age → classify; missing _time → log+None)
    │
    ▼
Optional[PriceSample(cents_per_kwh, source_ts, freshness)]
    │
    ├── _evaluate_layer_inputs (critical path)
    │      ├── if sample fresh: update firing.last_fresh_bucket_source_ts
    │      ├── unified safety release check (one rule, two reason codes)
    │      ├── proposed = evaluate_price_overlay(cents, state, now_utc)   # pure, freshness-agnostic
    │      ├── if (proposed is downgrade) and (sample.freshness != "fresh"):
    │      │      └── recency gate refuses: hold prev_tier, do NOT mutate state
    │      ├── else: apply proposed_state (upgrade / hold / fresh-data downgrade)
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

**Typical-case counter-walkthrough (feed dies during min-hold, not after):**

```
T=0   min   Trigger: tier goes elevated. last_fresh=T=0.
T=5   min   ComEd API breaks. No new fresh buckets.
T=5-30      Controller in min-hold; no downgrade attempted regardless of data.
            Sample ages past warn → stale; recency gate would refuse downgrade
            but min-hold already prevents any downgrade attempt.
T=30  min   Min-hold elapses. now - last_fresh = 30 min. AT threshold.
T=31  min   now - last_fresh = 31 min. Safety release fires.
```

**Total persistence: ~31 minutes** when feed dies during min-hold. The 60-min worst case applies only when feed happens to die exactly at min-hold expiry. Across the distribution of failure timings, most outages release between ~30 and ~60 min.

### 5.3 Failure-mode coverage

| Scenario | Mechanism | Time to release |
|----------|-----------|-----------------|
| ComEd API hard-down | Bucket eventually drops out of 30-min Influx query window. `sample is None`. Safety release fires. | ~30 min after API break |
| Influx outage | Query returns nothing. `sample is None`. Same path. | ~30 min after outage |
| Poller container crash (no Docker restart) | Same as above. | ~30 min |
| ComEd publishes with sustained lag (always 11+ min) | Sample non-None but never `fresh`. Field never updates. Unified safety release fires via persistent-stale branch. | ~30 min after lag onset |
| Brief publish jitter (one missed cycle, 10-15 min) | Field stops updating but resumes when next fresh bucket arrives. Tier preserved, no release. | N/A — recovers naturally |

## 6. Cutoff calibration

The 7-minute unified fresh threshold for `"comed.prices"` is the one judgment-call number in the design. It sets BOTH the cockpit's "fresh" boundary AND the scheduler's downgrade-recency cutoff (rule #6 — one definition, two consumers).

**Empirical inputs** (from `STALE_DATA_HANDOFF.md` §"Empirical Data on ComEd's Publish Behavior," based on 12 hours of poller logs):
- First-seen publish lag (from bucket's `_time` to first observation by poller): median 5.7 min, p90 7.7 min, p99 9.7 min, max 9.7 min in that sample.
- Bucket-age sawtooth (the latest bucket's age as it grows between publishes): median 8.7 min, p95 11.7 min, max 16.8 min in the sample.
- 34-day publish-cadence audit: 25 missed-publish events; most are 10-min gaps (1 bucket missed); 4 outlier events at 25-55 min gaps.
- 2026-05-19 bug fired at exactly 8 minutes of bucket age (at 19:18Z, latest bucket was 19:10Z = age 8 min).

**Cycle-coverage analysis** — fraction of a typical 5-min publish cycle during which a downgrade decision would be actionable (bucket age < cutoff):

| Cutoff | On median cycles (first-seen 5.7 min) | On p90 cycles (first-seen 7.7 min) |
|--------|---------------------------------------|------------------------------------|
| 6 min  | ~6%   | 0% (bucket arrives past cutoff) |
| 7 min  | ~26%  | 0% |
| 8 min  | ~46%  | ~6% |
| 9 min  | ~66%  | ~26% |

**Derivation (one cell shown to make the table auditable):** for cutoff=7 min and first-seen lag=5.7 min: the bucket arrives at age 5.7 min, ages linearly at 1 min/min until the next bucket lands (~5 min later, so cycle-top age is ~10.7 min). The bucket is < 7 min for the first 1.3 min of each cycle. That's 1.3 / 5.0 = 26% of the cycle. For cutoff=6 min and first-seen lag=7.7 min: the bucket arrives at age 7.7 min, which is already past the cutoff. Actionable window is 0% — gate refuses every tick of the cycle. Other cells derive analogously.

**Decision criteria:**
1. Must be strictly < 8 min to catch the observed bug threshold with any buffer.
2. The threshold serves two purposes simultaneously (downgrade safety + cockpit feed-health indicator), so the chosen value must satisfy both — which it does because the operator's question ("should I trust the controller's decisions RIGHT NOW?") and the controller's question ("is this data fresh enough to downgrade on?") are the same question.
3. Cost asymmetry: false-hold = comfort drift of 1-2°F for ~15-25 minutes until next fresh bucket allows re-evaluation. False-downgrade = wrong tier during a continuing spike, real dollar cost.

**6 min rejected:** too tight. Actionable window collapses to ~5% of cycles averaged across the lag distribution. Downgrades would compound false-holds.

**8 min rejected:** zero buffer below the observed bug. Marginal measurement noise (or a near-miss bug at 7.5 min) defeats it.

**7 min selected:** 1 min buffer below the observed bug; ~20% actionable window per cycle averaged across the lag distribution; expected false-hold duration on legitimate price drops is ~15-25 min (3-5 cycles), which is cheap comfort drift relative to the dollar cost of a false downgrade during a spike.

The cutoff is reviewable. If field testing reveals 7 min produces unacceptable false-holds (or doesn't reliably catch the bug class), the threshold is one-line-editable in `freshness.py` and the cockpit picks up the change automatically. The drift-check workflow ensures the cockpit and scheduler stay aligned.

## 7. Error handling

This fix IS a fail-loud remediation. Every new code path either succeeds visibly or fails visibly.

### Loud operator-visible signals

- **Gate refuses downgrade:** `decision_trace.price_overlay_eval` at `info` level (not debug) with `reason_code="PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"` and `bucket_age_sec`. Operator sees the refusal in Loki without verbose mode. `info` (not `debug`) because a refused downgrade is a near-miss the controller almost made — operationally more interesting than a routine carry-forward.
- **Safety release fires:** `decision_trace.price_overlay_eval` at `warn` level with one of `RELEASED_NO_DATA` / `RELEASED_PERSISTENT_STALE`. Separate `log("warn", "price_feed_stale_tier_released", ...)` entry for existing dashboards already querying that log line. `warn` because the release reflects a real degraded state, not a near-miss.
- **Drift between scheduler/cockpit Python files:** CI workflow `check-freshness-drift.yml` blocks PR merge on `diff` non-zero.
- **Outside-in acceptance test regression:** `xfail(strict=True)` flips to xpassed→failed if anyone breaks the gate later. Loud signal at every PR boundary.
- **Test mock migration miss:** any unmigrated `lambda q, b: 4.5` mock fails with `TypeError` (missing `now_utc` kwarg) and `AttributeError` (`float` has no `cents_per_kwh`) immediately in pytest.

### Malformed Influx row (missing `_time`) — degrade, don't crash

This is a deliberate departure from the project's "bubble up and kill" convention. `fetch_latest_comed` catches the missing-`_time` case and returns `None` rather than raising. Rationale:

The function runs on the per-tick safety-critical path. `test_hvac_scheduler.py:2151-2154` codifies that the safety supervisor MUST observe the thermostat every tick to catch indoor-temperature emergencies. If `fetch_latest_comed` raised, the tick would die before reaching the supervisor — a Docker restart cycle is ~60s of supervisor blindness. Trading away supervisor continuity to alert on a malformed Influx row (a vanishingly rare event for `comed.prices` data) is the wrong tradeoff.

Replacement: log an `error`-level structured event with whatever record metadata is available (`comed_row_missing_time`), return `None`. The downstream code path treats this exactly like "no bucket available" — the unified safety release takes over after 30 min if the condition persists. The operator sees an `error`-level log in Loki immediately (still loud) without a process death.

### Acceptable silent-by-design behaviors

- **Negative bucket age** (clock skew or test injection): treated as fresh (negative ≤ fresh_max_ms). Gate permits downgrade. Acceptable — future-dated buckets are a non-fault edge case; the test suite explicitly covers this.
- **Pre-fix audit rows** (`hvac.input_feed_health.price_ok` recorded under old semantics): not migrated. Operators reviewing data spanning the deploy see the semantic shift at the deploy timestamp. Documented; the pre-fix values were the bug. Pre-OSF, no amendment process needed.

## 8. Testing

### 8.1 Outside-in acceptance test (the north star)

`test_hvac_scheduler.py::test_19_18z_downgrade_refused_on_stale_bucket`. Replays the 2026-05-19 19:18Z scenario:

- FiringState in `elevated` tier with `triggered_at_utc = now - 30min - 1s` (min-hold elapsed).
- Mock returns `PriceSample(cents_per_kwh=2.5, source_ts=now - 8min, freshness="warn")` (8 min > new 7-min fresh threshold).
- Tick runs at `now = 19:18Z`.

Assertions:
- Tier unchanged (`current_tier == "elevated"`).
- `decision_trace.price_overlay_eval` has `outcome="held"`, `reason_code="PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"`, `bucket_age_sec ≈ 480`, `price_feed_unavailable=false`.
- `hvac.input_feed_health.price_ok == False` (post-fix; the unified 7-min threshold classifies 8-min bucket as not-actionable).

Initial marker: `pytest.mark.xfail(strict=True)`. Marker removed in the same commit that lands the implementation. Per AGENTS.md outside-in TDD rule.

### 8.2 Unit tests for the caller-side recency gate (`test_hvac_scheduler.py`)

(The gate lives in `_evaluate_layer_inputs`, not in `price_overlay.py`, so unit tests sit alongside the scheduler tests.)

- `test_gate_refuses_downgrade_when_sample_is_warn`
- `test_gate_refuses_downgrade_when_sample_is_stale`
- `test_gate_allows_downgrade_when_sample_is_fresh`
- `test_gate_does_not_affect_upgrade` (warn-aged spike data still upgrades immediately)
- `test_gate_does_not_affect_hold_within_tier` (price still above release; gate not consulted)
- `test_gate_treats_future_dated_bucket_as_fresh_and_allows_downgrade` (negative age clock-skew edge case)
- `test_gate_boundary_at_exact_seven_min` (age 7 min exactly → fresh → downgrade fires)
- `test_gate_boundary_at_seven_min_plus_one_second` (age 7 min 1 sec → warn → refused)

### 8.3 Unit tests for `fetch_latest_comed` new shape

- `test_fetch_latest_comed_returns_PriceSample_when_row_exists`
- `test_fetch_latest_comed_returns_None_when_no_row`
- `test_fetch_latest_comed_returns_None_and_logs_error_when_time_missing` (replaces the earlier "raises" test — see §7 for rationale)
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

`_fresh_sample` helper added to `conftest.py`:

```python
def _fresh_sample(cents: float, *, now_utc: datetime,
                  age_min: float = 1.0) -> PriceSample:
    """Default-fresh PriceSample for tests not specifically exercising
    the freshness gate. now_utc is REQUIRED (no fallback to wall-clock
    — tests must drive time deterministically). age_min defaults to 1
    (well under the 7-min fresh threshold)."""
    return PriceSample(
        cents_per_kwh=cents,
        source_ts=now_utc - timedelta(minutes=age_min),
        freshness="fresh",
    )
```

`now_utc` is required (no default) — protects against tests that forget the kwarg silently falling back to wall-clock and producing flaky behavior. Migration is mechanical; intent unchanged.

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
- During the spike, downgrade decisions occur ONLY when latest bucket age ≤ 7 min (sample.freshness == "fresh").
- Slow-publish cycles produce the new `HELD_DOWNGRADE_BUCKET_AGE` reason code.

**Path B — Historical replay if no live spike materializes.** Synthetic test running the new controller against historical Influx data from 2026-05-19 19:10Z-19:30Z window; assert the gate fires at 19:18Z. Scoped as a follow-up if Path A doesn't trigger before June 1.

Operational validation result is appended to this spec under §10 (Status & history) once observed.

## 10. Status & history

| Date       | Status | Change |
|------------|--------|--------|
| 2026-05-19 | draft  | Initial spec authored via brainstorming session. Decisions Q1-Q7 + safety-release extension locked. Outside-in acceptance test scoped, not yet implemented. |
| 2026-05-19 | draft  | Review-iteration pass. Code-reviewer subagent surfaced 14 findings (3 critical, 5 important, 6 minor). Operator confirmation collapsed C2 (two senses of stale) by unifying cockpit and scheduler thresholds — `"comed.prices"` fresh threshold tightened from 11 min to 7 min for both consumers. C1 closed by committing `STALE_DATA_HANDOFF.md` to repo root. C3 closed by confirming no OSF lock yet — normal forward-only semantic shift. I1 closed by moving the recency gate from `evaluate_price_overlay` to the caller-side in `_evaluate_layer_inputs`, keeping the state machine freshness-agnostic. I4 closed by changing missing-`_time` from raise to log+None for supervisor-continuity. M2 closed by renaming `price_is_stale` → `price_feed_unavailable`. Minor cleanups (M1, M3-M6) applied. |

Spec moves to `approved` after operator review of this revision. Then to `implementing` once the writing-plans skill produces the implementation plan and code work begins. Then to `shipped` once PR 1 merges + operational validation completes.

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
