---
date: 2026-05-19
owner: chris
status: open-for-investigation
role-label: chris
---

# Stale ComEd Data — Handoff Document

A bug was observed in the hvac-scheduler controller on 2026-05-19. This document captures the findings as facts, not solutions. The next agent should read this, investigate the code thoroughly, and propose a refactor.

---

## The Bug Event

**When:** 2026-05-19 14:18 CT (19:18 UTC)

**What happened:** the hvac-scheduler downgraded the price-overlay tier from `elevated` to `normal` during an active ComEd price spike. The downgrade was incorrect — real-time prices were continuing to spike, but the scheduler acted on stale data showing a low price.

**Sequence of events that day (UTC):**

```
18:48Z  Scheduler entered elevated tier (triggered by 18:40Z bucket = 17.5¢)
19:18Z  Min-hold elapsed. Scheduler evaluated downgrade.
         Latest bucket available: 19:10Z (price 2.5¢, age 8 min stale)
         Scheduler downgraded to normal based on the stale bucket.
19:20Z  ComEd shed a 30.1¢ price for the [19:15, 19:20] interval.
19:23Z  Scheduler observed the new bucket (14.8¢ for 19:15Z), re-triggered elevated.
19:27Z  Scheduler upgraded to scarcity (saw the 30.1¢ at 19:20Z).
19:58Z  Scheduler released scarcity (prices had collapsed by then).
```

The 19:18Z downgrade was a wrong decision. The bucket the scheduler trusted was 8 minutes stale, and during those 8 minutes the actual real-time prices had moved into scarcity territory (30.1¢ was published 2 minutes after the bad downgrade).

**Cost of the bug for that one event:** about 5 minutes of running at the normal-tier setpoint when the controller should have been at the elevated or scarcity setpoint. The wrong tier persisted from 19:18Z to 19:23Z, when fresh data corrected it.

---

## Why the Bug Happened — Architectural Root Cause

The scheduler is **freshness-blind**. The function it uses to read the current ComEd price returns only the price value, with no timestamp or freshness information attached.

### The function at `deploy/energy-stack/hvac_scheduler/app.py:821-827`:

```python
def fetch_latest_comed(query_api, bucket: str) -> float | None:
    for table in query_api.query(fq_latest_comed_5min(bucket)):
        for record in table.records:
            v = record.get_value()
            if v is not None:
                return float(v)
    return None
```

The function returns a bare `float`. The bucket's `_time` is available inside the Influx record (`record.get_time()`) but is never read. The function discards the timestamp before returning.

### The downstream effect

Callers (especially the scheduler at `app.py:2383` in `_evaluate_layer_inputs`) receive a number and have no way to determine:
- How old the data is
- Whether the bucket is from 1 minute ago or 29 minutes ago
- Whether the data is fresh enough to act on

The scheduler trusts whatever value the function returns as if it were the current real-time price.

### The "stale check" that exists is also blind

There is a field `firing.price_feed_last_ok_at_utc` on FiringState (defined at `app.py:1377`) intended to track stale-feed protection. The field is updated to `now_utc` every tick where `fetch_latest_comed` returned a non-None value — including ticks where the bucket is 25+ minutes stale.

So the field tracks "the last time we made a query that returned anything at all" — NOT "the last time we had fresh data." The variable name is misleading.

### The Flux query

`deploy/energy-stack/hvac_scheduler/app.py:799-807`:

```python
def fq_latest_comed_5min(bucket: str) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "comed.prices"
                    and r._field == "price_cents_per_kwh"
                    and r.period_type == "5min")
  |> last()
'''
```

The query returns the most recent bucket within the last 30 minutes. A bucket that is 25 minutes stale still gets returned. The query has no notion of "fresh enough."

---

## The Four Callers of `fetch_latest_comed`

Grep for `fetch_latest_comed` in `deploy/energy-stack/hvac_scheduler/app.py` shows four production call sites:

1. `app.py:2044` — in `revisit_today_decision`. Used for audit logging only (records price-at-decision in `hvac.decisions` table).
2. `app.py:2089` — in `run_decision`. Also audit logging only.
3. `app.py:2245` — in `fetch_today_decision`. Also audit logging only.
4. `app.py:2383` — in `_evaluate_layer_inputs`. **This is the critical control-loop caller.** Every scheduler tick. Drives tier decisions.

Caller 4 is where the bug fires. Callers 1-3 have lower stakes (audit logging) but exhibit the same freshness blindness.

---

## The Poller Layer Also Doesn't Check Freshness

`deploy/energy-stack/comed_poller/poller.py:86-96, 108-115, 132-143`:

- Poller calls ComEd API every 60 seconds
- Picks the entry with the largest `millisUTC` from the response
- Writes that entry to Influx with `_time = millisUTC` of that bucket
- **Never compares the bucket's timestamp to current wall-clock time**

If ComEd hasn't published in 20 minutes, the poller keeps getting the same old bucket back and re-writing it (idempotently — same `_time`, same row in Influx). The poller has no concept of "this bucket is too old to be useful."

The same is true at every layer of the pipeline: data flows from ComEd → poller → Influx → scheduler with NO layer ever checking "is this data fresh?"

---

## Empirical Data on ComEd's Publish Behavior

Measured against 34 days of Influx history (April 15 → May 19, 2026):

- **Buckets stored:** 9,970 (5-min granular ComEd prices)
- **Expected at perfect 5-min cadence:** 10,022
- **Missed publish events:** 25 in 34 days (about 0.73 per day on average)
- **Distribution of missed events:**
  - 10-min gap (1 bucket missed): 19 events
  - 15-min gap: 1 event
  - 20-min gap: 1 event
  - 25-min gap: 1 event
  - 30-min gap: 1 event
  - 50-min gap: 1 event (May 18 around 12:35-13:25 UTC)
  - 55-min gap: 1 event (May 7 around 07:50-08:45 UTC)

Measured against 12 hours of recent poller logs:

- **First-seen publish lag** (from bucket's `_time` to first observation by poller): median 5.7 min, p90 7.7 min, p99 9.7 min, max 9.7 min in that sample
- **Bucket-age sawtooth** (the latest bucket's age as it grows between publishes): median 8.7 min, p95 11.7 min, max 16.8 min in the sample

---

## Other Project Components Affected by the Same Blindness

### The audit/health-check telemetry

`app.py:2856-2887` writes `hvac.input_feed_health` per binding spec §11 #4. The `price_ok` boolean it writes uses `firing.price_feed_last_ok_at_utc` (the misleadingly-named field above) to determine "is the price feed healthy?" Same blindness — counts stale-but-present data as healthy.

### The cockpit

The cockpit at `tools/cockpit/backend/freshness.py` has its own freshness module with a `comed.prices` threshold set to 11 minutes warn. The cockpit and the controller have completely independent notions of "is this data fresh," which can drift.

The cockpit caught the 2026-05-19 bug visually first (its freshness indicator showed warning colors) which is what prompted this investigation. The controller has no equivalent visibility into its own data freshness.

---

## The Project's "Fail Loud" Convention

The project has a documented design principle of failing loud rather than silently producing wrong output. Quoting `deploy/energy-stack/comed_poller/poller.py:189-190`:

> *"Influx write errors are NOT caught — they bubble up and kill the process so Docker restarts it."*

This pattern is applied in at least 11 places across services and docs (search for "fail loud" or "bubble up and kill"). The freshness blindness bug VIOLATES this principle — the controller silently produces wrong decisions rather than failing loudly when data is bad.

---

## What's Requested

1. **Refactor `fetch_latest_comed` and downstream code** to make freshness a first-class concept that all consumers can check.
2. **Unify the freshness definition** across the controller and the cockpit (they currently have independent definitions).
3. **Add a Python type checker** (`mypy --strict` or equivalent) to the test suite for `deploy/energy-stack/hvac_scheduler/` and `tools/cockpit/backend/`. The project currently has no type checker enforcing the type hints in the code. This is a contributing factor to bugs that should have been caught at write-time but were caught only at runtime via review.

The next agent should investigate the code at the file:line references above, understand the full picture, and propose a refactor that respects the project's principles (fail loud, single source of truth, surgical changes).

---

## Key Files to Read

- `deploy/energy-stack/hvac_scheduler/app.py` — the controller, particularly:
  - `fetch_latest_comed` at 821-827
  - `fq_latest_comed_5min` at 799-807
  - `FiringState.price_feed_last_ok_at_utc` at 1377
  - `_evaluate_layer_inputs` at 2382-2487 (the critical-path caller block)
  - `write_input_feed_health` at 1849-1865 (the audit telemetry)
- `deploy/energy-stack/hvac_scheduler/price_overlay.py` — the price-overlay state machine and tier definitions
- `deploy/energy-stack/comed_poller/poller.py` — the data ingestion layer (also freshness-blind)
- `tools/cockpit/backend/freshness.py` — the cockpit's independent freshness module
- `tools/cockpit/README.md` — explains why the cockpit runs locally rather than in the docker stack
- `docs/plans/sced-rebaseline-spec-2026-05-13.md` — the binding spec (note: pre-OSF, not yet filed, draft thresholds still revisable)
- `AGENTS.md` / `CLAUDE.md` — project rules and conventions
