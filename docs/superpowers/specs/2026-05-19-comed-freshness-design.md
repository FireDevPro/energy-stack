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
- Unified safety release (controller-observation wall-clock timer): when the controller has observed 30 consecutive wall-clock minutes of non-fresh ComEd data AFTER min-hold has elapsed for the current tier, release tier back to normal with a loud log. The release clock is controller-observation time (`now_utc - firing.nonfresh_after_hold_started_at_utc`), NOT the bucket's `source_ts`. Single rule, two reason codes distinguishing no-data vs persistent-stale forensics. See §3.5 for the full timer semantics.
- Audit telemetry fix: the `hvac.input_feed_health` row for `feed=price` derives from a new local `price_feed_healthy` variable (renamed from `price_ok` for semantic clarity — see §3.6). `price_feed_healthy` checks whether fresh data has arrived within the 30-min safety window, NOT whether this tick's bucket is actionable for a downgrade. The two questions have separate names to prevent implementation-time conflation; both derive from the same source-of-truth field (`firing.last_fresh_bucket_source_ts`).
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
- Historical audit-row migration. Pre-fix `hvac.input_feed_health` rows (with stale-data being recorded as healthy=True) are not rewritten; the semantic shift is forward-only and visible at the deploy timestamp. (Pre-OSF, no amendment process needed.)
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
- `stale_max_ms = _min(30)` — the boundary above which `classify()` returns `"missing"` rather than `"stale"`. Numerically matches `PRICE_FEED_STALE_THRESHOLD`, but this is a freshness-CLASSIFICATION threshold (bucket-age-based, data-source clock), NOT the safety-release trigger. The safety release runs on a separate **controller-observation wall clock** that starts at first post-hold non-fresh observation (see §3.5). The two threshold-uses share a value (30 min) but answer different questions on different clocks.

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
# Inside _evaluate_layer_inputs. NOTE: this snippet shows the gate in isolation;
# §3.5 has the full per-tick flow including the controller-observation
# safety-release timer logic that wraps this gate.

prev_tier = firing.price_overlay_state.current_tier
proposed_tier, proposed_state = evaluate_price_overlay(
    sample.cents_per_kwh,
    firing.price_overlay_state,
    now_utc,
)
proposed_tier_name = proposed_tier.name if proposed_tier else NORMAL_TIER_NAME

is_downgrade = tier_priority(proposed_tier_name) < tier_priority(prev_tier)

if is_downgrade and sample.freshness != "fresh":
    # Recency gate: refuse the downgrade; hold current tier; loud trace.
    # No state machine mutation — keep firing.price_overlay_state at prev_tier.
    price_offset_f, price_override_f = offset_and_override_for_tier(prev_tier)
    price_tier_name = prev_tier
    downgrade_gate_held = True
    # decision_trace.price_overlay_eval emission below records this as held + HELD_DOWNGRADE_BUCKET_AGE.
else:
    # Apply the state machine's proposal (upgrade, hold, or fresh-data downgrade).
    firing.price_overlay_state = proposed_state
    active_tier = proposed_tier
    downgrade_gate_held = False
    # ... outputs from proposed_tier as today
```

The held branch uses the public `offset_and_override_for_tier` helper at `price_overlay.py:223-232` to look up the active tier's setpoint contributions; we do NOT reach into the private `_tier_by_name`.

**Why `sample.freshness != "fresh"` is the gate condition, not a separate threshold comparison:** the unified `"comed.prices"` fresh threshold IS the recency cutoff (§3.1 — 7 min). Asking "is the sample fresh?" and "is the bucket recent enough to downgrade?" are the same question now. One source of truth.

**Strict-`>` semantics at the boundary** are preserved by the existing `classify` function (`age_ms <= fresh_max_ms` is fresh; one millisecond more is warn). A bucket exactly at 7 min counts as fresh; the 19:18Z bug at 8 min would have classified as warn and the gate refuses.

**No new constants in `price_overlay.py`.** The downgrade-safety threshold is `THRESHOLDS["comed.prices"].fresh_max_ms` from the shared module — same value the cockpit uses for its UI.

### 3.5 Unified safety release (controller-observation wall-clock timer)

Replaces the existing None-only release path at `app.py:2402-2425` with a wall-clock timer measured in **controller-observation time** — not in data-source bucket time. The timer starts when the controller first observes non-fresh ComEd data AFTER min-hold has elapsed, and fires the safety release if 30 wall-clock minutes pass without the controller observing fresh data again.

**Two wall clocks, only one is the release timer:**

The spec carefully distinguishes:

1. **Bucket / source wall clock** — `now_utc - sample.source_ts`. Used ONLY to classify the latest ComEd sample as `fresh / warn / stale` for the per-tick freshness label (§3.1). The bucket's `_time` is the data-source timestamp.
2. **Controller / post-hold wall clock** — `now_utc - firing.nonfresh_after_hold_started_at_utc`. Used ONLY for the safety release. This is controller-observation time and starts only after min-hold has elapsed.

> **Do not use `sample.source_ts` or `last_fresh_bucket_source_ts` as the safety-release clock. Those timestamps belong to the data source. The release clock is controller-observation time and starts only after min-hold has elapsed.**

This is the rule that has caused this spec the most drift across review iterations. The bucket's `_time` informs the per-tick freshness label (correctly). It does NOT inform the safety-release timer (the data-source clock would count bucket aging that happened during the mandatory hold window against the controller).

**Invariant:**

> The safety-release timer (`firing.nonfresh_after_hold_started_at_utc`) is set on the first tick where (a) min-hold has elapsed for the current non-normal tier AND (b) the current ComEd sample is non-fresh (either `None` or `freshness != "fresh"`). It stays set across subsequent non-fresh post-hold ticks. Any fresh sample clears it. Any return to normal tier clears it. Any min-hold-not-elapsed state clears it (defensively — should be unreachable in practice since min-hold restarts on upgrade). Safety release fires when `now_utc - firing.nonfresh_after_hold_started_at_utc >= PRICE_FEED_STALE_THRESHOLD` (30 min).

**Timer update rule — per tick (in priority order):**

```python
# Inside _evaluate_layer_inputs.
prev_tier = firing.price_overlay_state.current_tier
min_hold_elapsed = hold_elapsed(
    firing.price_overlay_state, now_utc, DEFAULT_MINIMUM_HOLD_MINUTES,
)

# Update last-fresh field (used by audit telemetry, §3.6 — independent of this timer).
if sample is not None and sample.freshness == "fresh":
    firing.last_fresh_bucket_source_ts = sample.source_ts

# Safety-release timer update — wall-clock controller-observation time.
sample_is_fresh = sample is not None and sample.freshness == "fresh"

if prev_tier == NORMAL_TIER_NAME or not min_hold_elapsed:
    # No release possible — clear the timer.
    firing.nonfresh_after_hold_started_at_utc = None
elif sample_is_fresh:
    # Fresh observation — clear the timer.
    firing.nonfresh_after_hold_started_at_utc = None
elif firing.nonfresh_after_hold_started_at_utc is None:
    # First post-hold non-fresh observation — start the timer NOW
    # (controller-observation time, not bucket time).
    firing.nonfresh_after_hold_started_at_utc = now_utc
# else: timer was already set on a prior tick; leave it alone.

# Initialize trace-field defaults BEFORE the release/gate branches.
# The trace classifier (§3.7) reads these every tick; defaulting prevents
# undefined or stale-from-prior-tick values from leaking into the trace.
safety_release_fired = False
release_reason = None
downgrade_gate_held = False

# Safety release check.
if (firing.nonfresh_after_hold_started_at_utc is not None
        and now_utc - firing.nonfresh_after_hold_started_at_utc >= PRICE_FEED_STALE_THRESHOLD
        and prev_tier != NORMAL_TIER_NAME):
    # Two reason codes preserve the forensic split.
    release_reason = (
        PriceOverlayCode.RELEASED_NO_DATA if sample is None
        else PriceOverlayCode.RELEASED_PERSISTENT_STALE
    )
    log("warn", "price_feed_stale_tier_released",
        reason=release_reason.value,
        timer_started_at=firing.nonfresh_after_hold_started_at_utc.isoformat(),
        wall_clock_elapsed_sec=(now_utc - firing.nonfresh_after_hold_started_at_utc).total_seconds())
    firing.price_overlay_state = PriceOverlayState(
        current_tier=NORMAL_TIER_NAME,
        triggered_at_utc=None,
    )
    firing.nonfresh_after_hold_started_at_utc = None  # clear after release
    safety_release_fired = True
    # Explicit normal outputs — do NOT inherit prev_tier's offset/override.
    price_tier_name = NORMAL_TIER_NAME
    price_offset_f = 0
    price_override_f = None
    active_tier = None
    # downgrade_gate_held stays False from the default above.

elif sample is not None:
    # Apply the per-tick freshness gate (separate mechanism from the safety release).
    proposed_tier, proposed_state = evaluate_price_overlay(
        sample.cents_per_kwh, firing.price_overlay_state, now_utc,
    )
    proposed_name = proposed_tier.name if proposed_tier else NORMAL_TIER_NAME
    is_downgrade = tier_priority(proposed_name) < tier_priority(prev_tier)

    if is_downgrade and not sample_is_fresh:
        # Recency gate refuses downgrade. Hold prev_tier.
        downgrade_gate_held = True
        price_offset_f, price_override_f = offset_and_override_for_tier(prev_tier)
        price_tier_name = prev_tier
    else:
        # Apply state machine proposal. downgrade_gate_held stays False from default.
        firing.price_overlay_state = proposed_state
        active_tier = proposed_tier
        # ... use proposed tier's outputs

else:
    # sample is None, timer not yet at 30-min threshold: carry-forward.
    price_offset_f, price_override_f = offset_and_override_for_tier(prev_tier)
    price_tier_name = prev_tier
```

**No new threshold constant.** Reuses the existing `PRICE_FEED_STALE_THRESHOLD = timedelta(minutes=30)` at `app.py:2330` — same 30-min wall-clock duration the pre-fix release used, just measured from a CORRECTED start point (controller-observation of first post-hold non-fresh, not bucket source_ts).

**Stale-with-any-state-machine-outcome does NOT reset the timer.** Earlier draft (third-pass review) had a tick-counter that reset on "stale but state machine would propose hold." That was wrong per operator clarification: if data is non-fresh, it is non-fresh, regardless of what the stale price value would propose. The state machine's proposal informs the per-tick GATE decision (downgrade vs. hold), but not the safety timer. If the feed has gone 30 wall-clock minutes without producing fresh data after min-hold expiry, that IS the feed-broken signal we're protecting against — the stale bucket's price value doesn't change that.

**Why now_utc, not min-hold-expiry-time, as the timer start:** if data is fresh at min-hold expiry, the timer doesn't start (no problem yet). If data becomes non-fresh later, the timer starts AT THAT MOMENT — not at min-hold expiry. This gives the operator a clean property: "if you're staring at a non-fresh-data situation post-hold, the safety release is exactly 30 wall-clock minutes from when you first noticed."

**Worst-case persistence bound:** ~60 wall-clock minutes from tier trigger to forced safety release in the worst case (data fresh at min-hold expiry, goes non-fresh immediately after at T=30, safety release fires at T=60). If data goes non-fresh later than min-hold expiry, the wall-clock to release is longer (still 30 min from first post-hold non-fresh observation). If data is non-fresh DURING min-hold but becomes fresh again post-hold, the timer never starts and no release fires.

**Reset rules (explicitly enumerated per operator's request; all encoded in the timer update rule above):**

1. **Fresh ComEd sample arrives** (regardless of when, regardless of state machine outcome) → timer cleared to `None`.
2. **Tier returns to normal** (any path: natural downgrade, safety release, hypothetical future paths) → timer cleared via the `prev_tier == NORMAL_TIER_NAME` branch.
3. **Min-hold not yet elapsed** (covers cold start, new upgrade, hypothetical other resets) → timer cleared via the `not min_hold_elapsed` branch.
4. **No "stale-would-hold" reset** — explicitly removed per operator clarification. Any non-fresh post-hold sample either starts or continues the timer.

**Implementation note on FiringState:** the timer is stored as `nonfresh_after_hold_started_at_utc: Optional[datetime] = None`. In-memory state; not persisted across scheduler restarts. On restart, the timer is `None`; the next tick re-evaluates the state from fresh observations. This is the correct conservative behavior — a freshly-started scheduler shouldn't fire a release based on stale timer state from before the restart.

**Resilience to delayed scheduler execution:** because the timer is wall-clock based (not tick-counter based), a scheduler that ticks irregularly still releases at the right wall-clock moment. If Control4 lag causes the scheduler to skip ticks between minute 50 and minute 65 in a sustained-non-fresh scenario, the first tick at T=65 still observes `now_utc - timer_start >= 30 min` and fires the release. The release fires when the controller next gets to evaluate, regardless of how long the gap was.

### 3.6 Audit telemetry (corrected) — three named concepts, one source of truth

The scheduler maintains THREE distinct mechanisms that all reflect "how is the ComEd feed doing?" but answer different questions with different thresholds. They are NOT interchangeable. To prevent conflation during implementation, each has a separate name and lives in a separate code path:

| Concept | Variable / expression | Clock used | Where used |
|---------|----------------------|------------|------------|
| **Per-tick downgrade actionability** | `sample.freshness == "fresh"` | Data-source wall clock: `now - sample.source_ts <= 7 min` | Recency gate inside `_evaluate_layer_inputs`; cockpit per-tick freshness indicator |
| **Broad feed health** | `price_feed_healthy` (renamed from `price_ok`) | Data-source wall clock: `now - last_fresh_bucket_source_ts <= 30 min` | `hvac.input_feed_health` audit row (tag `feed=price`, field `healthy`); `required_feeds_for_arm_mode` B-active classification |
| **Safety release trigger** (§3.5) | `firing.nonfresh_after_hold_started_at_utc` | **Controller-observation wall clock**: `now - nonfresh_after_hold_started_at_utc >= 30 min` (timer set on first post-hold non-fresh observation) | Tier safety release inside `_evaluate_layer_inputs` |

All three reflect the same underlying ComEd feed but capture different operational questions on different clocks:

- **Per-tick actionability** answers "right now, is THIS tick's data new enough to act on?" Direct comparison of the latest bucket's age against a 7-min wall-clock cutoff. Data-source clock.
- **Broad feed health** answers "has the feed been broadly healthy over the recent window?" Compares the last-fresh bucket's `source_ts` against a 30-min wall-clock cutoff. Data-source clock. Stays True during normal yellow-cycle ticks (the per-tick actionability is False for ~74% of the cycle but the feed itself is publishing fine within the broader 30-min window). Independent of safety release state.
- **Safety release trigger** answers "have we observed 30 wall-clock minutes of continuous non-fresh data after min-hold expired?" Controller-observation clock — starts when the controller first NOTICES the post-hold non-fresh state, not when the bucket's `source_ts` indicates staleness. See §3.5 for the timer's full update rule and the rationale for the two-wall-clocks distinction.

**Why an implementer can't accidentally conflate them:** three different names, three different code paths, two different clocks. The data-source clock answers freshness questions ("is the data new enough?"); the controller-observation clock answers safety-release questions ("how long have we been stuck post-hold without fresh data?"). An implementer writing `nonfresh_after_hold_started_at_utc = last_fresh_bucket_source_ts` (collapsing the two clocks into one) would fail the spec's explicit guard rule: *Do not use `sample.source_ts` or `last_fresh_bucket_source_ts` as the safety-release clock.*

The per-tick question — "is this specific tick's bucket new enough to safely base a downgrade decision on?" — is answered directly by the freshness label that `fetch_latest_comed` already computed. The recency gate uses it inline.

The broad-health question — "has the feed produced fresh data within the controller's safety window, such that the protocol is still able to run?" — is answered by:

```python
# In run_schedule_check, replacing the old derivation at lines 2859-2863:
price_feed_healthy = (
    firing.last_fresh_bucket_source_ts is not None
    and (now_utc_for_audit - firing.last_fresh_bucket_source_ts) <= PRICE_FEED_STALE_THRESHOLD
)

all_feeds = {
    "price": price_feed_healthy,
    "weather": weather_ok,
    "pjm_capacity_risk": pjm_ok,
}
write_input_feed_health(write_api, cfg.influx_bucket, now_local, all_feeds)

required_feeds = required_feeds_for_arm_mode(
    when_ct=now_local,
    price_feed_healthy=price_feed_healthy,   # parameter renamed to match
    weather_ok=weather_ok,
    pjm_capacity_risk_ok=pjm_ok,
)
```

The `required_feeds_for_arm_mode` parameter at `app.py:1781-1799` renames its `price_ok` parameter to `price_feed_healthy` in the same PR for consistency. Internal dict keys (`"price"`) and the Influx tag value (`"price"`) and the Influx field name (`"healthy"`) are unchanged — those are schema, not local variables.

**Why this naming matters:** under the old name `price_ok`, an implementer could naturally write `price_ok = sample.freshness == "fresh"` and think they were doing the right thing. The new name `price_feed_healthy` communicates "this is the BROAD feed-health verdict, not the per-tick actionability." A future implementer reading the spec or the code can no longer accidentally conflate them.

Pre-fix → post-fix: the pre-fix `price_ok` was True whenever the feed had been reachable within 30 min based on `now_utc` (set on EVERY non-None read, including stale reads). Post-fix `price_feed_healthy` is True whenever the feed has produced FRESH data within 30 min based on the bucket's own `source_ts`. Same threshold, corrected semantics. The shift is forward-only; historical audit rows are not rewritten. Pre-OSF, no amendment process needed.

**B-active classification stays operationally stable.** With the 30-min health threshold preserved (not tightened to 7), normal-operation ticks with healthy ComEd publishing continue to classify as B-active. The 7-min per-tick boundary only governs the downgrade-gate decision, not the arm-mode classification.

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

**Explicit `downgrade_gate_held` flag threaded from gate to classifier.** When the recency gate fires (`is_downgrade and sample.freshness != "fresh"`), `prev_tier == new_tier` because the state machine's proposed downgrade was suppressed. The existing classifier at lines 2462-2486 routes equal-tier outcomes to `HELD_IN_TIER` by default. Without an explicit signal, gate-refused downgrades would be MIS-classified as routine in-tier holds.

The implementation threads a boolean from `_evaluate_layer_inputs` to the trace emission:

```python
# Inside _evaluate_layer_inputs, after the gate decision:
downgrade_gate_held = is_downgrade and (sample is not None and sample.freshness != "fresh")

# In the classifier block (replacing the lines 2462-2486 chain), checked
# BEFORE the generic prev_tier == new_tier branch:
if downgrade_gate_held:
    po_outcome = "held"
    po_reason = PriceOverlayCode.HELD_DOWNGRADE_BUCKET_AGE
    po_level = "info"
elif prev_tier == new_tier:
    # ... existing HELD_IN_TIER / NORMAL_BELOW_TRIGGER branches
```

The flag is local to the tick (not persisted on FiringState — the next tick re-derives it). Tests that exercise the gate must assert `reason_code == "PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"` specifically, not just `outcome == "held"`, to catch the case where the flag isn't threaded correctly and the trace falls into `HELD_IN_TIER`.

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

### Modified production files (5)

| File | Changes |
|------|---------|
| `deploy/energy-stack/hvac-scheduler/app.py` | Add `PriceSample` dataclass. Update `fetch_latest_comed` signature/body (catch missing `_time`, log+return None). Rename `FiringState.price_feed_last_ok_at_utc` → `last_fresh_bucket_source_ts`. Add `FiringState.nonfresh_after_hold_started_at_utc: Optional[datetime] = None` for the §3.5 safety-release timer (controller-observation wall clock). NO new threshold constant — the existing `PRICE_FEED_STALE_THRESHOLD = timedelta(minutes=30)` at `app.py:2330` is reused unchanged. Update `_evaluate_layer_inputs` (sample branching, **controller-observation wall-clock safety release timer per §3.5 — replaces old data-source-clock release**, caller-side recency gate using `offset_and_override_for_tier` in the held branch, `downgrade_gate_held` flag threaded to trace classifier). Update 3 audit-log callers (mechanical unwrap). Update `run_schedule_check` audit derivation: `price_ok` → `price_feed_healthy` local variable, derives from `last_fresh_bucket_source_ts` vs `PRICE_FEED_STALE_THRESHOLD` (data-source clock — broad-health question, independent of the safety timer). Update `decision_trace.price_overlay_eval` emission (new `bucket_age_sec`, rename `price_is_stale` → `price_feed_unavailable`, new outcome branches with `downgrade_gate_held` short-circuit). Rename `required_feeds_for_arm_mode` parameter `price_ok` → `price_feed_healthy`. Import `tier_priority` and `hold_elapsed` from `price_overlay` for the downgrade-detection check and the min-hold-elapsed gate. |
| `deploy/energy-stack/hvac-scheduler/Dockerfile` | Add `freshness.py` to the `COPY` line at `Dockerfile:10`. Without this, the container build succeeds but runtime fails on import. |
| `deploy/energy-stack/hvac-scheduler/price_overlay.py` | **No signature change.** Expose `_tier_priority` as `tier_priority` AND `_hold_elapsed` as `hold_elapsed` (drop the leading underscores so the caller can use them for the downgrade-detection check and the min-hold-elapsed gate respectively). No new module constant. |
| `deploy/energy-stack/hvac-scheduler/decision_codes.py` | Rename existing `STALE_FEED_RELEASED` → `RELEASED_NO_DATA` for consistency. Append two new enum values: `HELD_DOWNGRADE_BUCKET_AGE`, `RELEASED_PERSISTENT_STALE`. |
| `tools/cockpit/backend/freshness.py` | Update `"comed.prices"` threshold to 7/16/30 min (was 11/16/30). Rewrite the header comment at lines 40-47 to explain the new semantics (7-min = controller's actionability boundary). Header docstring also updated to point to canonical scheduler copy. Content otherwise byte-identical. |
| `tools/cockpit/frontend/src/freshness.ts` | Same 7/16/30 threshold update (still hand-paired). Comment block updated similarly. |

### Modified test files (3)

| File | Changes |
|------|---------|
| `deploy/energy-stack/hvac-scheduler/test_hvac_scheduler.py` | Outside-in acceptance test (19:18Z replay), initially `xfail(strict=True)`. **Recency-gate unit tests live here** (caller-side gate, not in `test_price_overlay.py`): refuses-downgrade-when-warn, refuses-when-stale, allows-when-fresh, doesn't-affect-upgrades, doesn't-affect-holds, boundary cases at exact-cutoff and cutoff+1s, future-dated-bucket edge case. New unit tests for `fetch_latest_comed` shape (PriceSample construction, None on empty, **returns None and logs error when `_time` missing**, warn/stale classification). FiringState rename + tightened semantics tests. Audit-derivation test asserting `price_feed_healthy` uses the 30-min threshold not the 7-min one. Trace-classifier test asserting `downgrade_gate_held=True` routes to `HELD_DOWNGRADE_BUCKET_AGE`, not generic `HELD_IN_TIER`. ~14 mock migrations to use `_fresh_sample()` helper. |
| `deploy/energy-stack/hvac-scheduler/test_price_overlay.py` | No new gate tests (gate is caller-side, tests live in `test_hvac_scheduler.py`). Only update: tier-priority helper rename (`_tier_priority` → `tier_priority`) if any test references it directly. |
| `deploy/energy-stack/hvac-scheduler/conftest.py` | Add `_fresh_sample(cents, *, now_utc: datetime, age_min: float = 1.0)` test helper. `now_utc` is REQUIRED (no default) to prevent flaky wall-clock fallbacks in tests that forget the kwarg. |

### Total diff estimate

~420-530 lines across 10 files. Approximately 50-60% test changes, 30% production code, 10% new infrastructure.

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
    │      └── hvac.input_feed_health (healthy derived from last_fresh_bucket_source_ts vs 30-min — broad health, NOT per-tick actionability)
    │
    └── 3 audit-log callers (unwrap to float, pass to write_decision)
```

### 5.2 Safety bound walkthroughs (controller-observation wall-clock timer)

The safety release timer measures controller-observation wall-clock time after the first post-hold non-fresh observation (per §3.5). It is NOT based on the bucket's `_time` and does NOT count bucket aging that happened during min-hold against the controller.

**Worked example — feed fails mid-hold; release fires 30 min after first post-hold non-fresh observation:**

```
T=0   min   Tier upgrades to elevated. triggered_at_utc = T=0.
            nonfresh_after_hold_started_at_utc = None.
T=22  min   Last fresh ComEd bucket arrives. last_fresh_bucket_source_ts = T=22.
T=22+ min   ComEd API breaks. No new buckets arrive.
T=23-29     Min-hold still active. Bucket from T=22 ages 1, 2, ... 7 min.
            Per the timer rule: "not min_hold_elapsed" branch → timer stays None.
            (We don't count bucket aging during min-hold against the controller.)
T=30  min   Min-hold elapses. Latest bucket from T=22, age 8 min → freshness=warn.
            Timer was None. Sample is not fresh AND min-hold elapsed AND non-normal tier.
            Per the rule: "nonfresh_after_hold_started_at_utc is None → set to now_utc."
            nonfresh_after_hold_started_at_utc = T=30. (Controller-observation time.)
T=30-59     Non-fresh continues. Timer stays at T=30.
            Per-tick recency gate refuses any proposed downgrade (sample not fresh).
T=60  min   now - timer = 30 min. Safety release fires.
            Reason code: RELEASED_PERSISTENT_STALE (sample is non-None warn/stale)
                     or  RELEASED_NO_DATA (sample is None, bucket aged out of Influx).
            Tier returns to NORMAL. Timer cleared to None.
```

**Total persistence: 60 wall-clock minutes from upgrade trigger** — the operator's stated upper bound (30 min min-hold + 30 min timer). The timer's 30-min window starts at min-hold expiry exactly because that's when the controller first OBSERVED non-fresh data post-hold; the data-source clock (bucket aging during min-hold) is irrelevant.

**Variant: data fresh at min-hold expiry, goes non-fresh later:**

```
T=0   min   Upgrade. triggered_at_utc = T=0.
T=0-29      Data flowing fresh.
T=29  min   Last fresh ComEd bucket arrives. last_fresh_bucket_source_ts = T=29.
T=30  min   Min-hold elapses. Bucket from T=29 is 1 min old, fresh.
            Per the timer rule: sample is fresh → timer cleared (still None).
T=30-36     Bucket ages 1, 2, ... 7 min. Sample stays fresh through T=36 (age 7 min).
T=37  min   Bucket age 8 min → freshness=warn. First post-hold non-fresh observation.
            Timer = T=37.
T=37-66     Timer at T=37. Non-fresh continues.
T=67  min   now - timer = 30 min. Safety release fires.
```

**Total persistence: 67 wall-clock minutes** — longer than the 60-min worst case because the timer didn't start until non-fresh was first observed at T=37. The operator gets MORE grace period when fresh data was available at min-hold expiry — a strictly more conservative property than the data-source-clock approach.

**Resilience to delayed scheduler execution:**

```
T=30  min   Min-hold elapses. Sample non-fresh. Timer = T=30.
T=30-50     Ticks fire normally. Timer still T=30.
T=50  min   Control4 lag causes 15-min hang. Scheduler blocked from T=50 to T=65.
T=65  min   Scheduler resumes. First tick at T=65.
            now - timer = 35 min. Already past the 30-min threshold.
            Safety release fires on this tick.
```

The wall-clock timer fires correctly even when ticks are delayed — the controller releases as soon as it gets to evaluate, regardless of how long the gap was.

### 5.3 Failure-mode coverage

| Scenario | Timer behavior | Time to release |
|----------|----------------|-----------------|
| ComEd API hard-down at or before min-hold expiry | Timer starts at T=30 (min-hold expiry); fires at T=60. | 60 min from upgrade trigger; 30 min from min-hold expiry |
| ComEd API fails AFTER min-hold expiry | Timer starts when sample first observed non-fresh post-hold; fires 30 min later. | 30 min from first post-hold non-fresh observation (typically T=API-fail + ~7 min while latest bucket ages out of fresh) |
| Influx outage | Sample is None. Timer starts on first post-hold None observation. Fires 30 min later. | Same as ComEd hard-down |
| Poller container crash (no Docker restart) | Same as Influx outage from the scheduler's perspective. | Same |
| ComEd publishes with sustained lag (always warn/stale) | Timer starts on first post-hold non-fresh observation; fires 30 min later if no fresh data observed in between. | 30 min from first post-hold observation |
| Brief publish jitter (one missed cycle, 10-15 min between fresh buckets) | Timer may set briefly, then clear when next fresh sample arrives. | N/A — recovers naturally |
| Scheduler tick delays (Control4 lag, slow Influx queries) | Wall-clock-based: fires correctly on next tick after threshold crossed. | Possibly delayed by the lag duration, but still wall-clock-anchored |

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

Replacement: log an `error`-level structured event with whatever record metadata is available (`comed_row_missing_time`), return `None`. The downstream code path treats this exactly like "no bucket available" — the unified safety release takes over after min-hold has elapsed AND the controller has observed 30 wall-clock minutes of continuous non-fresh ComEd (per §3.5). The operator sees an `error`-level log in Loki immediately (still loud) without a process death.

### Acceptable silent-by-design behaviors

- **Negative bucket age** (clock skew or test injection): treated as fresh (negative ≤ fresh_max_ms). Gate permits downgrade. Acceptable — future-dated buckets are a non-fault edge case; the test suite explicitly covers this.
- **Pre-fix audit rows** (`hvac.input_feed_health` with stale-data-recorded-as-healthy under old `price_ok` semantics): not migrated. Operators reviewing data spanning the deploy see the semantic shift at the deploy timestamp. Documented; the pre-fix values were the bug. Pre-OSF, no amendment process needed.

## 8. Testing

### 8.1 Outside-in acceptance test (the north star)

`test_hvac_scheduler.py::test_19_18z_downgrade_refused_on_stale_bucket`. Replays the 2026-05-19 19:18Z scenario:

- FiringState in `elevated` tier with `triggered_at_utc = now - 30min - 1s` (min-hold elapsed).
- Mock returns `PriceSample(cents_per_kwh=2.5, source_ts=now - 8min, freshness="warn")` (8 min > new 7-min fresh threshold).
- Tick runs at `now = 19:18Z`.

Assertions:
- Tier unchanged (`current_tier == "elevated"`).
- `decision_trace.price_overlay_eval` has `outcome="held"`, `reason_code="PRICE_OVERLAY_HELD_DOWNGRADE_BUCKET_AGE"`, `bucket_age_sec ≈ 480`, `price_feed_unavailable=false`.
- `hvac.input_feed_health` row for `feed=price` has `healthy=True` (broad-health verdict uses the 30-min threshold; the 8-min bucket is well inside that window — feed is broadly healthy, just not actionable for a downgrade RIGHT NOW). This is the critical distinction that the named-split in §3.6 enforces.

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

- `test_input_feed_health_uses_broad_health_threshold_not_per_tick_freshness`:
  - This test guards specifically against the regression class where an implementer conflates the two derivations. Asserts that during normal operation (last fresh bucket within last 30 min, but current sample is warn-aged at 10 min), `price_feed_healthy=True` is written to the audit row. The 7-min downgrade-actionability boundary must NOT bleed into this check.
  - `firing.last_fresh_bucket_source_ts = now - 5min`, current sample warn-aged → `price_feed_healthy=True`.
  - `firing.last_fresh_bucket_source_ts = now - 31min`, sample is None → `price_feed_healthy=False`.
  - `firing.last_fresh_bucket_source_ts = None` (cold start) → `price_feed_healthy=False`.

### 8.6 Safety release timer tests (controller-observation wall-clock model per §3.5)

These tests pin the timer's exact behavior — specifically that the clock is controller-observation time (not data-source `source_ts`), that any non-fresh post-hold sample starts the timer (regardless of what the stale price would propose), and that the release fires at the 30-min wall-clock mark. Tests live in `test_hvac_scheduler.py` and use `freezegun` (or equivalent time-control) to advance `now_utc` deterministically.

**Timer set / clear conditions:**

- `test_timer_does_not_set_during_min_hold`: tier elevated, min-hold has NOT elapsed, sample is stale → `nonfresh_after_hold_started_at_utc` stays `None`. (The data-source clock would have started here in the wrong implementation; the controller-observation clock correctly defers until min-hold expires.)
- `test_timer_does_not_set_at_normal_tier`: tier is normal → timer stays `None` regardless of sample state.
- `test_timer_sets_on_first_post_hold_nonfresh_with_stale_sample`: tier elevated, min-hold elapsed, sample non-fresh (warn or stale), timer was `None` → timer = `now_utc` exactly (NOT `sample.source_ts`).
- `test_timer_sets_on_first_post_hold_nonfresh_with_none_sample`: tier elevated, min-hold elapsed, sample is None, timer was `None` → timer = `now_utc`. (No-data verification miss.)
- `test_timer_clears_on_fresh_sample`: timer at any non-None value, fresh sample arrives → timer cleared to `None` regardless of state machine outcome.
- `test_timer_clears_on_tier_return_to_normal`: timer at non-None, safety release fires or natural downgrade returns tier to normal → timer cleared.
- `test_timer_clears_on_new_protective_upgrade`: timer at non-None, sample shows price crossing higher tier's trigger, upgrade fires → next tick min-hold restarts; timer cleared via the `not min_hold_elapsed` branch.
- `test_timer_does_NOT_clear_on_stale_would_hold`: timer at non-None, sample stays stale but stale price value is above release threshold (state machine would propose HOLD) → timer stays non-None. **This is the critical anti-regression test** — earlier draft (third-pass) had this resetting; operator clarification says non-fresh is non-fresh regardless of what stale price would propose.

**Boundary and release tests:**

- `test_safety_release_at_29_min_59_sec_still_held`: timer set 29 min 59 sec ago, sample still non-fresh → tier preserved, no release.
- `test_safety_release_at_30_min_exactly_fires`: timer set EXACTLY 30 min ago (`now_utc - timer == PRICE_FEED_STALE_THRESHOLD`) → release fires (uses `>=` comparison).
- `test_safety_release_at_30_min_fires_persistent_stale_reason`: timer 30 min ago with sample non-fresh (non-None) → release fires; reason = `RELEASED_PERSISTENT_STALE`; tier returns to normal; timer cleared; loud warn-level log emitted.
- `test_safety_release_at_30_min_fires_no_data_reason`: timer 30 min ago with sample is None → release fires; reason = `RELEASED_NO_DATA`.
- `test_safety_release_does_not_fire_at_normal_tier`: timer set to 30+ min ago (artificially) AND tier is normal → release does NOT fire (`prev_tier != NORMAL_TIER_NAME` guard).

**Resilience tests:**

- `test_safety_release_fires_after_scheduler_tick_gap`: timer set at T=30; scheduler ticks at T=31, T=32, then no tick (simulated Control4 hang) until T=65. At T=65 first post-hang tick, `now_utc - timer = 35 min ≥ 30 min` → release fires immediately. This pins the wall-clock-vs-tick-count distinction; missed ticks do NOT prevent release.
- `test_safety_release_does_not_use_data_source_clock`: scenario where `sample.source_ts` is 45 min in the past (very stale bucket) but the controller only just observed non-fresh post-hold (timer = `now_utc - 5 min`). Even though bucket is "45 min old in data-source clock," the timer (controller-observation clock) is only 5 min in → release does NOT fire. This is the explicit anti-regression test for the `Do not use sample.source_ts as the safety-release clock` guard.

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
| 2026-05-19 | draft  | First review-iteration pass. Code-reviewer subagent surfaced 14 findings (3 critical, 5 important, 6 minor). Operator confirmation collapsed C2 (two senses of stale) by unifying cockpit and scheduler thresholds — `"comed.prices"` fresh threshold tightened from 11 min to 7 min for both consumers. C1 closed by committing `STALE_DATA_HANDOFF.md` to repo root. C3 closed by confirming no OSF lock yet — normal forward-only semantic shift. I1 closed by moving the recency gate from `evaluate_price_overlay` to the caller-side in `_evaluate_layer_inputs`, keeping the state machine freshness-agnostic. I4 closed by changing missing-`_time` from raise to log+None for supervisor-continuity. M2 closed by renaming `price_is_stale` → `price_feed_unavailable`. Minor cleanups (M1, M3-M6) applied. |
| 2026-05-19 | draft  | Second review-iteration pass via external reviewer. Caught a regression I introduced in pass 1: making the audit-telemetry derivation use the 7-min `sample.freshness == "fresh"` would have broken `required_feeds_for_arm_mode`, mis-classifying ~74% of normal-operation ticks as B-fallback. Fix: rename the local variable `price_ok` → `price_feed_healthy` at the audit derivation site (and the `required_feeds_for_arm_mode` parameter); derive it from `last_fresh_bucket_source_ts` vs the 30-min `PRICE_FEED_STALE_THRESHOLD` (broad health), keeping `sample.freshness == "fresh"` for the per-tick downgrade gate only (per-tick actionability). Named the split explicitly in §3.6 so the same conflation cannot recur during implementation. Also closed: Dockerfile COPY line (would have broken container deploy), explicit `downgrade_gate_held` flag threaded to trace classifier (would have mis-routed gate refusals to `HELD_IN_TIER`), §4 components-table cleanups (gate tests in `test_hvac_scheduler.py` not `test_price_overlay.py`; `_fresh_sample` requires `now_utc`). |
| 2026-05-19 | draft  | Third review-iteration pass. Operator clarified that the safety release should be tick-counter based (counting opportunities post-min-hold), not wall-clock based on `last_fresh_bucket_source_ts` — the wall-clock approach I had would have started the release timer at the last fresh bucket's `_time`, which counts bucket aging during the min-hold window against the controller. Operator's framing: "for that first 60 seconds after a mandatory 30 min tier increase hold, the clock isn't based off the last good comed poll. It could be 15 min old at that first tick but we still check each min to see if it's fresh, for 30 min." With a further refinement: increment the counter only when the gate is ACTIVELY BLOCKING a relaxation (would-be downgrade refused due to staleness) OR when sample is None — NOT when stale data would-hold-anyway. Renamed the semantic to "consecutive missed relaxation/verification opportunities." Spec changes: §3.5 rewritten as tick-counter with full condition table + worked example + reset rules; new `FiringState.ticks_without_fresh_after_hold_elapsed` field + `PRICE_FEED_STALE_TICK_THRESHOLD = 30` constant; §3.4 gate held-branch uses `offset_and_override_for_tier` (Codex P2.5); §3.6 expanded to a three-concepts table (per-tick actionability / broad feed health / safety release trigger) so all three are explicitly distinguished; §5.2 worked example replaces the wrong "typical case ~31 min" walkthrough; §8.6 expanded with counter-state tests (5 increment/reset condition tests + 4 boundary/release tests) including assertions for both release reason codes (`RELEASED_NO_DATA`, `RELEASED_PERSISTENT_STALE`). |
| 2026-05-19 | draft  | Fourth review-iteration pass. External reviewer flagged that the third-pass tick-counter model was still wrong in two ways: (1) it could reset on "stale-would-hold," which the operator's intent rules out (any non-fresh post-hold sample should advance the timer; the stale price value doesn't change feed-staleness), and (2) it conflated "scheduler tick count" with "feed-staleness duration." Operator articulated the load-bearing distinction: **two wall clocks exist, and only one is the release timer.** The data-source clock (`now - sample.source_ts`) classifies sample freshness; the controller-observation clock (`now - firing.nonfresh_after_hold_started_at_utc`) drives the safety release. Spec changes: §3.5 fully rewritten as a controller-observation wall-clock timer (`nonfresh_after_hold_started_at_utc`) that sets on the first post-hold non-fresh observation, clears on any fresh sample / return to normal / min-hold-restart, and fires release at 30 wall-clock minutes regardless of state machine proposal. Tick counter, tick threshold, and "missed opportunities" semantic all REMOVED. New verbatim guard added: "Do not use `sample.source_ts` or `last_fresh_bucket_source_ts` as the safety-release clock." §3.6 three-concepts table updated to label each concept's clock-of-origin. §4 components-table: `ticks_without_fresh_after_hold_elapsed: int = 0` replaced with `nonfresh_after_hold_started_at_utc: Optional[datetime] = None`; new `PRICE_FEED_STALE_TICK_THRESHOLD = 30` constant REMOVED (reuses existing `PRICE_FEED_STALE_THRESHOLD = timedelta(minutes=30)`); `_hold_elapsed` exposed as public `hold_elapsed` for scheduler-side call. §5.2 walkthroughs rewritten — new worked example showing timer start at min-hold expiry, anti-regression scenario showing timer does NOT use bucket source_ts, resilience example showing timer fires correctly after delayed scheduler execution. §8.6 tests rewritten as wall-clock tests with `test_safety_release_does_not_use_data_source_clock` as the explicit anti-regression test for the two-wall-clocks distinction. §2 scope language and §3.1 threshold-note language corrected to distinguish freshness classification (data-source clock) from safety release (controller-observation clock). |
| 2026-05-19 | draft  | Fifth review-iteration pass. External reviewer confirmed the controller-observation wall-clock model is now correct (no more big-conceptual-fix needed). Four small spec-text cleanups applied: (P2) §3.5 pseudocode now initializes `safety_release_fired = False`, `release_reason = None`, `downgrade_gate_held = False` as defaults BEFORE the release/gate branches so the trace classifier never reads undefined or stale-from-prior-tick values; (P2) §3.5 safety-release branch now explicitly assigns `price_tier_name = NORMAL_TIER_NAME`, `price_offset_f = 0`, `price_override_f = None`, `active_tier = None` instead of saying "outputs set to normal" — prevents implementation from accidentally preserving the released tier's offset/override; (P3) §3.4 inline-comment changed from "safety-release counter logic" to "controller-observation safety-release timer logic"; (P3) §7 malformed-row paragraph sharpened from "safety release takes over after 30 min if the condition persists" to the precise "after min-hold has elapsed AND the controller has observed 30 wall-clock minutes of continuous non-fresh ComEd (per §3.5)." No design changes; the conceptual model from pass 4 stands. |

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
