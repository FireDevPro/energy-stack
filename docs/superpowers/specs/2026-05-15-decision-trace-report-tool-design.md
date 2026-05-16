---
date: 2026-05-15
owner: chris
status: superseded
role-label: chris
superseded_by: docs/n8n/decision-trace-report.prompt.md
superseded_on: 2026-05-16
---

> **Superseded 2026-05-16.** This spec described a Python Markdown
> renderer at `tools/decision_trace_report/`. That implementation
> shipped (PRs #125, #126) and ran live, but the rendered Markdown
> tables proved hard to interpret as a commissioning artifact. The
> tool was replaced by an n8n + Claude pipeline that delegates
> narrative writing to the Anthropic node (PRs #127, #128, #129,
> #130-equivalent sunset). The fact-collection lessons earned
> here — Flux query patterns, Loki retention/limit handling,
> microsecond ts formatting, synthetic-trace tagging, ±5 min
> spike-to-trace pairing — carried forward into the n8n workflow.
>
> Live artifact: `docs/n8n/decision-trace-report.workflow.json`
> Prompt of record: `docs/n8n/decision-trace-report.prompt.md`
> Operator README: `docs/n8n/decision-trace-report.README.md`
>
> This file is retained as historical context for the design
> decisions that informed the n8n fact-packet contract.

# Decision-Trace Commissioning Report Tool — Design Spec

## 1. Purpose

A daily + on-demand markdown report rendered from `decision_trace.*` Loki lines + `hvac.*` InfluxDB measurements. Surfaces surprises, silent skips, unexpected reason codes, missing trace events, and mismatches between expected and observed scheduler behavior **before June 1 OSF experiment start**.

**Primary purpose: pre-experiment commissioning monitor** (verbose for debugging).

Not yet optimized for long-term ops dashboard use. After commissioning closes, the tool either evolves toward a terser ops-dashboard mode or retires; that decision is post-commissioning, out of scope for this spec.

## 2. Scope

### In scope (v1)

Five sections, all rendered every run:

1. **Night-before decision audit** — yesterday's 21:00 + revisits day-type decision + §7 precool decision. Includes full `evaluation_tape` (rules considered + which fired). Cross-references `hvac.decisions` + `hvac.precool_window` Influx rows.
2. **Live day-of decision audit** — chronological table of every `layer_resolution` + `supervisor` event from the target CT day. Grouped by `tick_id`. Cross-references `hvac.actions`.
3. **Price-spike reaction audit** — every ComEd 5-min price ≥10¢ on the target day, correlated with the nearest `decision_trace.price_overlay_eval`. Per-spike: time, price, observed tier, reason_code, expected reaction yes/no, whether the trace explains the outcome. Latency/release timing is nice-to-have, only if it falls out naturally. **No state reconstruction** in v1.
4. **Feed + telemetry health** — last-write timestamp per critical feed. Prefer existing `feed_status` / heartbeat measurements where available; use `max(_time)` of the data measurement only as fallback for feeds without status/heartbeat rows. Be careful with event-only feeds (no continuous cadence, can't use staleness thresholds naively).
5. **Coverage scorecard** — for each `reason_code` enum in `decision_codes.py`, show **both** cumulative-since-trace-started count AND last-7-days count. Status = "observed live" if cumulative > 0; recent-7d count shown separately.

### Out of scope (v1)

- Rule-compliance verdict layer (pass/explainable/unexpected against an independent rule re-implementation). Explicitly deferred per the original decision-trace plan.
- Long-term operations dashboard polish (terse output, brevity, Telegram-as-primary-channel).
- Pi-side rendering or storage. Tool runs entirely on the Windows workstation, queries Pi over LAN.
- Mermaid diagrams in the report. Daily reports are tabular; the timing/flow diagrams live in `docs/SCHEDULER_TIMING.md` separately.
- Auto-commit to repo or auto-upload anywhere besides the local output path.

## 3. Architecture

### Runtime model

- Runs on **Windows workstation** (desktop, mostly always-on). Queries Pi-lab Loki (port 3100) + InfluxDB (port 8086) over the homelab LAN.
- Daily automated run via Windows Task Scheduler at 08:00 CT — renders **yesterday's CT day** (the rendered day = the day before the run day; e.g., 08:00 run on 2026-05-16 writes `2026-05-15-decision-trace.md`).
- On-demand CLI for ad-hoc investigation (specific date or arbitrary time range).
- If desktop is off at 08:00 CT, the daily run is skipped silently. Operator re-renders manually with `--date`. Source data persists on Pi within Loki/Influx retention windows (Influx is long-lived; Loki retention is shorter — exact threshold depends on `loki-config.yml`. Re-rendering deep historical days may hit Loki retention before it hits Influx.)

### Module structure

```
tools/decision_trace_report/
├── __init__.py
├── cli.py                       # argparse entry; dispatches to renderer
├── loki_client.py               # LogQL HTTP wrapper, time-range + tick_id helpers
├── influx_client.py             # Flux wrapper for hvac.* + feed-health queries
├── telegram_client.py           # heartbeat send (reuses @EnergyStackBot creds)
├── renderer.py                  # markdown builder; assembles section outputs
├── sections/
│   ├── __init__.py
│   ├── night_before.py
│   ├── day_of.py
│   ├── price_spikes.py
│   ├── feed_health.py
│   └── coverage_scorecard.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # shared fixtures: stub Loki + Influx responses
│   ├── test_cli.py
│   ├── test_loki_client.py
│   ├── test_influx_client.py
│   ├── test_telegram_client.py
│   ├── test_renderer.py
│   └── test_sections_*.py
├── .env.example                 # documents required env vars; tracked
└── README.md                    # operator-facing: invocation + setup
```

### Data flow

For each report run:

```
CT day target → renderer.run(target_date)
    ├── loki_client.query_decision_traces(target_date) → events grouped by msg
    ├── influx_client.query_hvac_measurements(target_date) → rows per measurement
    ├── influx_client.query_feed_health() → max(_time) or status row per feed
    └── sections.<each_section>(loki_events, influx_rows) → markdown text
        ↓
    renderer.assemble(sections, anomaly_summary) → full markdown
        ↓
    write file to <output_path>
        ↓
    telegram_client.send_heartbeat(anomaly_summary) (unless --no-telegram)
```

Sections are pure functions: given the queried events/rows, return a markdown string. Tests inject stub data directly.

## 4. CLI surface

```
python -m tools.decision_trace_report [flags]
```

| Flag | Default | Meaning |
|---|---|---|
| (none) | renders yesterday CT | Default daily-run mode |
| `--date YYYY-MM-DD` | — | Render a specific CT calendar day |
| `--from YYYY-MM-DDTHH:MM --to YYYY-MM-DDTHH:MM` | — | Arbitrary CT-local time range; interpreted in `America/Chicago` |
| `--output PATH` | `D:\Projects\energy-proxy\docs\test-reports\YYYY-MM-DD-decision-trace.md` | Override output path |
| `--no-telegram` | false | Suppress heartbeat (for ad-hoc reruns) |
| `--verbose` | false | Echo Loki + Influx query bodies (debug the queries themselves) |
| `--loki-url URL` | env `LOKI_URL` | Override Loki endpoint (testing) |
| `--influx-url URL` | env `INFLUXDB_URL` | Override InfluxDB endpoint (testing) |
| `--env-file PATH` | — | Optional: load env vars from a file (convenience for ad-hoc local runs) |

### Output path default

```
D:\Projects\energy-proxy\docs\test-reports\YYYY-MM-DD-decision-trace.md
```

- `docs/test-reports/` is **gitignored**.
- Tracked: `docs/test-reports/README.md` explaining the directory's purpose.

### Heartbeat content

Short Telegram message (~5 lines):

```
Decision-trace commissioning report ready: 2026-05-15
Events: 3,847 (price_overlay 1,440; layer_res 1,401; sup 1,002; precool 1; day_type 3)
Anomalies: 0 unexpected reason codes, 0 supervisor non-approved, 0 stale feeds, 0 query errors
Status: all green
File: D:\Projects\energy-proxy\docs\test-reports\2026-05-15-decision-trace.md
```

If any anomaly count > 0, status reads `open the report` instead of `all green`.

## 5. Per-section data sources + content

### §1 — Night-before decision audit

**Loki queries:**
- `{container="hvac-scheduler"} |= "decision_trace.day_type_decision"` — **key on the `decision_for_date` JSON field** matching `target_date_iso`. The night-before 21:00 decision fires on CT day `target - 1`; the 06:00 + 11:00 revisits fire on CT day `target`. Querying by `decision_for_date` content (not by `_time`-of-emission) captures all four events regardless of which CT day they emitted on.
- `{container="hvac-scheduler"} |= "decision_trace.precool_decision"` — same approach: filter by `decision_for_date == target_date_iso`. The trace line for precool also fires on CT day `target - 1` at 21:00.

Backup time-range envelope (defensive, in case Loki LogQL JSON-field filtering misses any edge case): query `_time` in `[target - 1 @ 20:00 CT, target @ 12:00 CT]` and then filter by `decision_for_date` field client-side.

**InfluxDB cross-reference:**
- `hvac.decisions` row(s) tagged `decision_for_date = target` (one per decision firing, multiple if revisits revised)
- `hvac.precool_window` row tagged `target_date = target`

**Rendered:**
- Day-type winner with `winning_reason`
- Full `evaluation_tape` as a table: `rule | threshold | actual | fired | reason_code`
- Precool decision: `selected` bool, `hour_ct`, `depth_f`, `reason_code`
- Reconciliation block: trace's `winning_day_type` vs `hvac.decisions[day_type]`. Flag if they disagree (counts as anomaly).
- Reconciliation block: trace's precool `selected/hour_ct/depth_f` vs `hvac.precool_window`. Flag if they disagree.

### §2 — Live day-of decision audit

**Loki queries:**
- `{container="hvac-scheduler"} |~ "decision_trace.(layer_resolution|supervisor)"` filtered to target CT day, sorted by `_time`

**InfluxDB cross-reference:**
- `hvac.actions` rows for target CT day (one per `action_fired` or mid-period repush)

**Rendered:**
- Chronological table, one row per event. Columns: time (CT), `tick_id` (truncated to 8 chars), event type, `winning_layer`, `schedule_cool_f`, `price_cool_f`, `fivecp_cool_f`, `effective_cool_f`, supervisor `decision` (if event = supervisor), supervisor `reason_code` (if non-approved).
- Group consecutive events sharing the same `tick_id` (so one tick's chain reads as a unit).

**Reconciliation (narrowed to avoid mid-period-no-op false positives):**

`hvac.actions` rows are written only on (a) a scheduled action firing or (b) a mid-period repush where `effective_cool_f` changed since the last push. Most ticks emit `layer_resolution` + `supervisor` trace events but write NO `hvac.actions` row — that is normal idempotent mid-period behavior, NOT an anomaly.

v1 reconciliation rule applies only to action-fire events:
- An action-fire trace (identified by surrounding `action_fired` log line OR an `action_label` field on the layer_resolution event other than `MID_PERIOD_REPUSH:*`) MUST have a matching `hvac.actions` row tagged with the same `action_label` for the target CT day.
- Mid-period-repush traces are NOT reconciled — too many normal no-op cases to discriminate without re-implementing the scheduler's push-or-skip logic.
- `SCHEDULER_MODE=shadow` does not change the rule: `hvac.actions` audit rows are written regardless of mode (with `applied=0` + `dry_run=true` tags in shadow). The row's presence is what's checked, not its `applied` value.

Future tightening (post-v1) could reconcile mid-period repushes by recomputing the "should-push" predicate, but that's the kind of rule-re-implementation we're explicitly avoiding in v1.

### §3 — Price-spike reaction audit

**InfluxDB query:**
- `comed.prices` rows with `price_cents_per_kwh >= 10` for target CT day, ordered by `_time`

**Loki query:**
- `{container="hvac-scheduler"} |= "decision_trace.price_overlay_eval"` for target CT day (large query — filtered down to near-spike times)

**Rendered:**
- "No spikes today" if no rows match.
- Otherwise: per-spike table. Columns: spike time, price (¢/kWh), nearest `decision_trace.price_overlay_eval` (time, `outcome`, `reason_code`, `prev_tier`, `new_tier`), expected reaction (computed from spike price + tier rules), explained yes/no.

**"Expected reaction"** is a static lookup against the spec:
- price ≥10¢ — controller should be in elevated or scarcity tier within 1 tick of the spike OR be in a minimum-hold from a prior triggering event
- price ≥20¢ — controller should be in scarcity tier within 1 tick OR be in a minimum-hold

**"Explained yes/no" is a coarse v1 check, NOT a re-implemented state machine.** Explained = `true` if the nearest trace's `reason_code` falls in a small allow-list given the price tier:

| Price tier observed at spike | reason_codes that count as "explained" |
|---|---|
| `elevated` or `scarcity` | UPGRADED_TO_ELEVATED, UPGRADED_TO_SCARCITY, HELD_IN_TIER |
| `normal` | NORMAL_BELOW_TRIGGER (allowed only if price < threshold at trace time), STALE_FEED_RELEASED, FEED_UNAVAILABLE_TIER_PRESERVED |

Anything else → "explained no" → counted as anomaly. This is a coarse correctness check, not a rule-compliance verdict (which the original plan deferred). False positives expected at minimum-hold edges; operator follows up via the trace fields directly. v1 trades precision for not-re-implementing-the-state-machine.

- v1 does NOT reconstruct overlay state for past hours. Reports only what the nearest trace line said. Latency = (transition time − spike time) shown only if a transition trace exists in the near window.

### §4 — Feed + telemetry health

**InfluxDB queries (per feed):**

Critical feed list:
- `comed.prices` (continuous, 60s)
- `nws.forecast` (continuous, ~30min)
- `pjm.lmp_da_hourly` (event — daily at 17:00 CT)
- `pjm.inst_load` (continuous, ~5min)
- `pjm.metered_load` (event — Sunday 02:00 CT; this is the Influx measurement name the poller writes; the PJM API endpoint that feeds it is `hrl_load_metered`)
- `refoss.channel` (continuous, 30s)
- `hvac.thermostat` (continuous, 10min)
- `haven.indoor` (continuous, ~5min)

**Strategy per feed type:**
- **Continuous feeds**: prefer existing `<service>_heartbeat` or `feed_status` measurements (if the service writes one); fall back to `max(_time)` of the data measurement. Stale if age > feed-specific threshold (e.g., 5min for ComEd, 60min for NWS).
- **Event feeds** (DA LMP, weekly metered): can't use simple age threshold. Compare `max(_time)` to expected next-fire schedule. E.g., DA LMP stale if `max(_time)` hasn't moved since the most-recent expected 17:00 CT publish. Note the expected publish window in the report rather than a single threshold.

**Rendered:**
- Table: feed name, source measurement, last-write timestamp (CT), age, threshold, status (✅ fresh / ⚠️ warn / 🔴 stale).
- Stale feeds count toward the anomaly summary.

### §5 — Coverage scorecard

**Loki query:**
- `{container="hvac-scheduler"} |~ "decision_trace\\."` for two ranges:
  - Cumulative since trace started (or last 30d, whichever is shorter — Loki retention dependent)
  - Last 7 days

Aggregate by `reason_code` field (extracted from JSON).

**Reference list:** all `*Code` enums from `decision_codes.py`. Tool reads the enum module at runtime.

**Rendered:**
- Per-enum subsection (PriceOverlayCode, LayerResolutionCode, SupervisorCode, PrecoolCode, DayTypeCode).
- For each code in the enum: status (✅ observed live / ⚪ not observed live), cumulative count, last-7d count.
- A code in the trace data that's NOT in any enum is an **unexpected reason code** and counts as anomaly.

## 6. Anomaly summary + heartbeat

Rendered at the TOP of the report (above any section), drives the Telegram heartbeat:

| Anomaly type | Counted from |
|---|---|
| Unexpected reason codes | §5 (codes in trace not in enums) |
| Supervisor non-approved decisions | §2 (decision != "approved") |
| Stale feeds | §4 (status = stale) |
| Trace-vs-Influx discrepancies | §1 + §2 |
| Unexplained price spikes | §3 (price ≥10¢ with no plausible trace reaction) |
| Query errors | section-level errors caught + counted |

If all counts = 0 → Telegram heartbeat says `Status: all green`. Otherwise `Status: open the report` plus the specific non-zero counts.

## 7. Configuration

Default config source: **shell environment / Task Scheduler environment**. No default `.env` file location.

Required env vars:

| Var | Purpose |
|---|---|
| `LOKI_URL` | e.g., `http://192.168.20.10:3100` |
| `INFLUXDB_URL` | e.g., `http://192.168.20.10:8086` |
| `INFLUXDB_TOKEN` | Same admin token as Pi `.env` |
| `INFLUXDB_ORG` | Same org as Pi `.env` |
| `INFLUXDB_BUCKET` | `energy` |
| `TELEGRAM_BOT_TOKEN` | Same `@EnergyStackBot` token |
| `TELEGRAM_CHAT_ID` | Same chat ID |

Optional convenience: `--env-file PATH` loads vars from a file before running. The file format is dotenv (`KEY=value` lines). Useful for ad-hoc local invocations (`python -m tools.decision_trace_report --env-file C:\path\to\my.env --date 2026-05-15`).

`.env.example` is tracked in the tool dir as documentation. Any `*.env` matching pattern under `tools/decision_trace_report/` is gitignored.

CLI overrides for `--loki-url` / `--influx-url` are available for testing against alternate endpoints (rare).

## 8. Error handling philosophy

**Partial reports always beat no reports.** Each section runs in its own try/except. If a query fails, the section captures the error and renders it in-line:

```
## §1 Night-before decision audit

⚠️  Loki query failed: ConnectionError: Pi-lab unreachable at 192.168.20.10:3100.
    decision_trace.day_type_decision unavailable for 2026-05-15.
    Influx fallback succeeded — see hvac.decisions row below.

| field | value |
|---|---|
| day_type | NORMAL |
| ...
```

Section-level errors increment the `query_errors` count in the anomaly summary, both inline AND in the top summary block. Heartbeat reports the count.

CLI exit codes:
- `0` — report rendered successfully, even if it contains inline section errors
- `1` — rendering itself crashed (e.g., a Python exception in `renderer.py` or `cli.py`)
- `2` — invalid CLI args

Daily Task Scheduler does **not** retry on exit 0; it relies on the operator opening the report via the heartbeat. Exit 1 should be rare and indicates a bug in the tool itself, not a data-availability issue.

## 9. Testing

- **Unit tests per client** (`test_loki_client.py`, `test_influx_client.py`, `test_telegram_client.py`):
  - Mock the HTTP calls (`requests.post` / `influxdb_client.QueryApi.query`).
  - Assert query body construction (LogQL strings, Flux strings, time-range formats).
  - Assert response parsing handles success + various error shapes.
- **Unit tests per section** (`test_sections_<name>.py`):
  - Inject stub event lists + InfluxDB row lists directly into the section function.
  - Assert the markdown output matches a snapshot fixture (small markdown file checked in under `tests/fixtures/`).
  - Cover at least: happy path (typical data), empty path (no events), anomaly path (unexpected code, stale feed, discrepancy).
- **Integration test on the CLI** (`test_cli.py`):
  - Feed synthetic Loki + Influx fixtures end-to-end via mocked clients.
  - Assert the full assembled output markdown matches a fixture file.
  - One per "happy day" scenario (normal MILD day) + one per "anomaly" scenario.
- **No live Pi/LAN HTTP in tests.** Tests use mocks; local fake HTTP fixtures (e.g., `pytest-httpserver`, local stubbed FastAPI app, or a fake server inside the test process) are allowed if a fixture is cleaner than mocking the client. Live HTTP to the real Pi or any remote endpoint is forbidden in CI. Live verification is the operator running the tool against the real Pi.
- **Snapshot fixtures kept small** — under ~100 lines each so PR review can actually read them. Larger fixtures broken into per-section files.

## 10. Markdown format

GitHub-flavored markdown. Plain tables, headers, code blocks. **No Mermaid in v1.** The timing/flow diagrams live in `docs/SCHEDULER_TIMING.md`; daily reports are tabular.

ToC at top of every report so jumping to a section is fast in a long file. Section headers (`## §1`, `## §2`, etc.) match the spec numbering for cross-reference.

Estimated daily file size: 200-800 lines depending on activity (longer on days with HOT day-type, price spikes, or supervisor non-approved events).

## 11. Maintenance + future direction

- New `reason_code` enum values added in source code automatically pick up in §5 coverage scorecard via runtime reflection.
- New `decision_trace.*` event types added in source code would require a new section module + tests.
- Post-commissioning (post-OSF filing or post-June-1 experiment start), this tool either evolves toward a terser ops-dashboard mode or retires. Decision deferred.
- If the on-demand CLI grows beyond simple date / range flags (e.g., per-tick drill-downs, multi-day comparisons), consider extracting a query API layer rather than piling onto `cli.py`.

## 12. Non-goals (locked)

- No Pi-side service. Runs entirely on Windows.
- No InfluxDB or Loki schema changes.
- No new poller or measurement.
- No second rule engine or independent rule re-implementation.
- No commit to the energy-proxy repo from the tool itself.
- No persistent file index — daily files identified by filename + date; if you want a multi-day comparison, run the CLI with a wider range.
