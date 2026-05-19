---
date: 2026-05-19
owner: chris
status: draft
role-label: chris
---

# Decision-trace report — Loki saturation fix + n8n SDK adoption + observability persistence plan

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision history.** Drafted first as a surgical Python-script file edit
(Path A). Pivoted to Path B (SDK rebuild via `mcp__n8n__*`) after live
verification that the n8n SDK MCP is the canonical authoring path and
that file-Edit-based workflow edits have burned multiple agent sessions.
Then expanded again to "Option B / bundled" — the SDK rebuild is the
load-bearing change; bundling Data Tables (observability persistence)
and pin-data test fixture (test harness for this and every future
workflow) costs ~2 additional focused hours and avoids the rework-loop
of patching now + enhancing later.

---

**Goal.** Stop silent truncation of 7-day reason-code counts; pre-empt
imminent target-day truncation; standardize on n8n SDK MCP authoring;
persist daily reason-code history + query-error events in n8n Data
Tables for cross-week observability that the rolling-7d Loki window
cannot provide.

**Architecture.**
1. Raise Loki's per-query entry cap 5000 → 20000 in `loki-config.yml`.
2. Re-author the daily-decision-trace workflow as canonical n8n SDK
   source in `tools/n8n/decision_trace_report.workflow.ts`. Preserve
   every existing behavior verbatim; apply three deliberate Loki
   changes; add two Data Table writer nodes (reason-code history,
   query-error log).
3. Validate via `mcp__n8n__validate_workflow`. Deploy via
   `mcp__n8n__update_workflow` against existing workflow ID
   `sxYIzx3uV01fKsZi` (draft only — live cron keeps running the
   prior active version until explicit publish).
4. Commit pin-data test fixture; run `mcp__n8n__test_workflow` as the
   feature-level acceptance harness (no real Anthropic / Telegram /
   live data needed for the JS-logic acceptance signal).
5. After test pass: `mcp__n8n__execute_workflow` manual mode (live
   services, draft); same-`time=` LogQL deterministic oracle;
   `mcp__n8n__publish_workflow`; production-mode verify per
   `project-n8n-publish-may-corrupt-jscode` memory.
6. Codify n8n SDK MCP as canonical authoring in AGENTS.md.

**Tech stack.** n8n 2.19.5 on pi-lab. SDK via `@n8n/workflow-sdk` import.
MCP tools: `get_sdk_reference`, `search_nodes`, `get_node_types`,
`search_projects`, `search_data_tables`, `create_data_table`,
`add_data_table_column`, `add_data_table_rows`, `validate_workflow`,
`update_workflow`, `prepare_test_pin_data`, `test_workflow`,
`execute_workflow`, `publish_workflow`, `unpublish_workflow`,
`get_execution`. Loki 3.3.2 with `max_entries_limit_per_query: 20000`.

**Scope.**
- `deploy/energy-stack/loki/loki-config.yml` (1 line)
- `AGENTS.md` (1 section)
- `tools/n8n/README.md` (new)
- `tools/n8n/decision_trace_report.workflow.ts` (new — canonical SDK source)
- `tools/n8n/fixtures/decision_trace_report.pin.json` (new — test fixture)
- `docs/n8n/decision-trace-report.workflow.json` (regenerated, marked DERIVED)
- `docs/n8n/decision-trace-report.README.md` (small updates)
- Two new n8n Data Tables created via MCP (not in git; n8n manages persistence)

**Out of scope (separate follow-ups, tracked but not in this plan):**
- `energy-longterm` bucket-wiring audit + downsample healthcheck.
- Daily storage-line guardrail in Telegram brief.
- Migrating other existing workflows to SDK source (only one exists).
- Reason-code trend dashboard / Grafana panel built off the new Data
  Table (write happens here; reads happen in future work).

---

## Pre-flight findings (live-verified 2026-05-19)

Reference for the implementer. Treat as inputs; don't re-verify.

| Probe | Result |
|---|---|
| Loki container memory | 142 MiB / 15.6 GiB |
| `max_entries_limit_per_query` in current `loki-config.yml` | not set (Loki default 5000) |
| 7d decision_trace total | 14531 |
| Target-day peak (rolling 24h) | d-2 = 4324, d-1 = 3852 |
| Empty `tick_id` lines 7d | 0 |
| `\| json` parse errors 7d | 0 |
| "commission" substring outside `tick_id` 7d | 0 |
| Earliest visible trace | 2026-05-14T23:39:09Z |
| Organic + synthetic vector aggregation sum | 14666 vs raw 14531 (~135 newer events in interim) |
| Empty `reason_code` bucket | 14 organic lines (skipped by JS `tally()`; SDK adds `\| reason_code!=""` for parity) |
| Live workflow ID | `sxYIzx3uV01fKsZi` |
| Live workflow active flag | **true** (repo file shows `false` — drift from PR #164 sanitization; reconciled in Phase 3) |
| n8n version | 2.19.5 (all MCP tools from 2.12 / 2.14 / 2.15 / 2.16 available) |
| 4 jsCode substitution targets in current Build Fact Packet | all FOUND verbatim |

**Acceptance signal (feature-level, real test harness):** A `test_workflow`
run with the committed pin-data fixture produces a `fact_packet` matching
expected shape (vector-derived organic/synthetic counts, first-seen from
mini-query, target-day saturation guard at 20000) AND zero
`limit_saturated` entries in `query_errors`. THEN a same-`time=` LogQL
probe against live Loki sums to exact match against the fact_packet's
organic count. Both gates must be green before publish.

---

## n8n MCP capability map (already verified available on 2.19.5)

| Tool | Version | Used for |
|---|---|---|
| `validate_workflow` | 2.12.0 | parse SDK code, catch errors before update |
| `update_workflow` | 2.12.0 | write SDK source to draft (preserves credentials by node-name + type match) |
| `publish_workflow` | 2.12.0 | promote draft → active |
| `unpublish_workflow` | 2.12.0 | rollback path |
| `execute_workflow` | 2.12.0 | full live run (manual mode → draft; production mode → published version) |
| `get_execution` | 2.12.0 | post-publish verification |
| `search_nodes` / `get_node_types` | 2.12.0 | discovery during authoring |
| `search_projects` | 2.14.0 | find project ID for data table creation |
| `prepare_test_pin_data` | 2.15.0 | generate JSON schemas for pin-data fixture |
| `test_workflow` | 2.15.0 | **feature-level acceptance harness; pin trigger + credential + HTTP nodes; Code nodes execute normally (5-min timeout)** |
| `search_data_tables` / `create_data_table` / `add_data_table_column` / `add_data_table_rows` | 2.16.0 | persistent reason-code history + query-error log |

**Critical operational notes from official docs:**
- `update_workflow` modifies the **draft** only. The active version
  (running the daily 8am cron) keeps using the prior version until
  `publish_workflow` is explicitly called. **This protects production
  during testing.**
- Credentials preserve via node-name + type match. As long as we keep
  existing node names + types stable, the 6 Influx + 2 Telegram + 1
  Anthropic credentials carry through automatically. The Loki HTTP
  nodes have no credentials anyway. Verify via the response's
  `autoAssignedCredentials` array.
- `test_workflow` enforces a 5-minute MCP execution timeout. Our
  workflow's Claude call is well under that.
- `validate_workflow` warnings can coexist with `valid: true`. Treat
  warnings as review items, not blockers, unless egregious.
- After `update_workflow` / `create_workflow_from_code`, n8n marks the
  workflow with `aiBuilderAssisted` metadata + `availableInMCP: true`.
- `add_data_table_rows`: max 1000 rows per call; values must be
  string/number/boolean/null.

---

## File structure

**New files (in this PR series):**
- `tools/n8n/` — canonical SDK source directory.
- `tools/n8n/README.md` — authoring discipline.
- `tools/n8n/decision_trace_report.workflow.ts` — canonical SDK source.
- `tools/n8n/fixtures/decision_trace_report.pin.json` — pin-data test fixture.

**Modified files:**
- `AGENTS.md` — standing rule on n8n SDK authoring.
- `deploy/energy-stack/loki/loki-config.yml` — Loki cap bump.
- `docs/n8n/decision-trace-report.workflow.json` — regenerated derived export.
- `docs/n8n/decision-trace-report.README.md` — point at SDK source; pipeline diagram + troubleshooting line corrections.

**New n8n resources (managed by n8n; not in git):**
- Data table `decision_trace_reason_code_history` — columns: `date` (string), `reason_code` (string), `variant` (string), `count` (number).
- Data table `decision_trace_query_errors` — columns: `event_at_utc` (string), `query` (string), `warn` (string), `note` (string), `target_date` (string).

**Untouched (deliberately):**
- All `deploy/energy-stack/*` service code.
- Influx schema / buckets / retention.
- Loki retention (`retention_period: 168h`).
- Promtail / scrape config.
- Decision-codes definitions.

---

## Phase 0 — Discipline foundation

### Task 0.1: Working-tree audit + branch

- [ ] `git status && git stash list`. Triage anything unexpected per AGENTS.md.
- [ ] `git checkout main && git pull --ff-only origin main`
- [ ] `git checkout -b fix/n8n-sdk-discipline`

### Task 0.2: Update AGENTS.md

**File:** `AGENTS.md`. ADD a new section immediately after "Skill protocol":

```markdown
## n8n workflows

**Authoring is via the n8n SDK MCP only.** Use `mcp__n8n__*` tools.
Never edit workflow JSON files with the Edit tool — that path has
burned multiple agent sessions on escape-encoded strings and Postgres
publish corruption.

Canonical SDK source lives in `tools/n8n/<workflow>.workflow.ts`.
Workflow JSON in `docs/n8n/` is a derived export for diff visibility
only; never hand-edit. Re-export from SDK after each change.

Authoring flow:
1. Read SDK reference once per session: `mcp__n8n__get_sdk_reference`.
2. Discover nodes: `search_nodes` + `get_node_types` for exact
   parameter shapes — never guess.
3. Edit the `.workflow.ts`. Validate: `mcp__n8n__validate_workflow`.
4. Deploy to draft: `mcp__n8n__update_workflow` (live cron stays on
   prior active version).
5. Test with pin data: `prepare_test_pin_data` + `test_workflow`.
   Pin-data fixtures live in `tools/n8n/fixtures/<workflow>.pin.json`.
6. Live-data manual run: `execute_workflow` (`executionMode="manual"`).
7. Publish: `publish_workflow`.
8. Verify post-publish: `execute_workflow` (`executionMode="production"`)
   per `project-n8n-publish-may-corrupt-jscode` memory.
9. Rollback path: `unpublish_workflow` if production-mode run reveals
   corruption.
```

### Task 0.3: Create `tools/n8n/README.md`

```markdown
---
date: 2026-05-19
owner: chris
status: active
role-label: chris
---

# n8n workflow SDK sources

Canonical SDK source for every n8n workflow in this repo. The
corresponding JSON files in `docs/n8n/` are derived exports.

See `AGENTS.md` § "n8n workflows" for the authoring rule.

## Workflows

| ID | Name | SDK source | Derived JSON | Pin-data fixture |
|---|---|---|---|---|
| `sxYIzx3uV01fKsZi` | Daily Decision-Trace Commissioning Report | `decision_trace_report.workflow.ts` | `docs/n8n/decision-trace-report.workflow.json` | `fixtures/decision_trace_report.pin.json` |

## Available MCP capabilities (n8n 2.19.5)

- Workflow lifecycle: search, get details, create/update/archive, publish/unpublish.
- Authoring: get_sdk_reference, search_nodes, get_node_types, validate_workflow.
- Testing: prepare_test_pin_data, test_workflow (pins trigger + credential + HTTP nodes; Code nodes run normally; 5-min timeout).
- Execution: execute_workflow (manual = draft, production = published), get_execution.
- Data tables: search/create/rename/archive tables; add/rename/delete columns; add rows. Use for cross-run persistence (history, event logs).
```

### Task 0.4: Commit + open Phase-0 PR

- [ ] `git add AGENTS.md tools/n8n/README.md`
- [ ] Commit with message explaining: codify SDK MCP as canonical, no behavior change.
- [ ] `git push -u origin fix/n8n-sdk-discipline`
- [ ] `gh pr create --base main`
- [ ] **STOP.** Surface PR URL. Wait for Chris merge.

---

## Phase 1 — Loki cap bump (config-only, deploys)

### Task 1.1: Sync main + branch

- [ ] Post-Phase-0-merge: `git checkout main && git pull --ff-only origin main`
- [ ] `git branch -d fix/n8n-sdk-discipline`
- [ ] `git checkout -b fix/loki-max-entries-bump`

### Task 1.2: Edit `loki-config.yml`

ADD to `limits_config:` after `ingestion_burst_size_mb: 16`:

```yaml
  max_entries_limit_per_query: 20000
```

### Task 1.3: Commit + PR + STOP

- [ ] Commit message explains 14.5k vs 5k saturation, d-2 4324 peak, 4x headroom, 142 MiB Loki memory headroom.
- [ ] `gh pr create --base main`
- [ ] **STOP.** Surface PR URL. Wait for merge.

### Task 1.4: Post-merge verify

- [ ] Sync main, delete branch.
- [ ] Verify Loki restarted + cap raised:

```bash
ssh chris@192.168.20.10 'docker compose -p energy-stack ps loki && curl -sG "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode "query={container=\"hvac-scheduler\"} |= \"decision_trace\"" \
  --data-urlencode "start=$(date -u -d 1day\ ago +%s)000000000" \
  --data-urlencode "end=$(date -u +%s)000000000" \
  --data-urlencode "limit=20000" 2>/dev/null | python3 -c "import sys,json; r=json.load(sys.stdin); print(r[\"status\"])"'
```

Expected: `Up` for loki + `success` for the query.

---

## Phase 2 — SDK rebuild + Data Tables + pin-data fixture

This is the load-bearing phase. ~3-5 focused hours.

**Dependency on Phase 0**: this phase assumes the SDK-MCP-as-canonical
rule landed in AGENTS.md (Phase 0 merged). If Phase 0 was rejected or
reduced (e.g., Chris decided not to standardize on SDK), Phase 2's
bundled scope collapses. **Revert path:** drop SDK rebuild + Data
Tables + pin-data fixture; reduce Phase 2 to a single targeted edit of
the existing JSON file via Python `json.load → modify jsCode → json.dump`
(the original Path A from this plan's revision history), applying only
the four Build Fact Packet jsCode substitutions + the target-day
`limit=5000→20000` swap + the 7d-aggregation node replacements (still
needs three new Loki nodes but expressed in the JSON file, not SDK
source). Reuse the same-`time=` LogQL oracle from Phase 3 as the
acceptance gate.

### Task 2.1: Sync main + branch

- [ ] `git checkout main && git pull --ff-only origin main`
- [ ] `git checkout -b fix/decision-trace-sdk-rebuild`

### Task 2.2: Discover n8n project + create data tables (one-time MCP setup)

- [ ] `mcp__n8n__search_projects(query="")`. Capture the project ID where `sxYIzx3uV01fKsZi` lives. Note: if a personal project, the projectId is returned in the workflow details from earlier search.
- [ ] `mcp__n8n__search_data_tables(query="decision_trace_reason_code_history")`. If found → use that ID. If not → create:

```
mcp__n8n__create_data_table(
  projectId=<id>,
  name="decision_trace_reason_code_history",
  columns=[
    { name: "date", type: "string" },
    { name: "reason_code", type: "string" },
    { name: "variant", type: "string" },
    { name: "count", type: "number" }
  ]
)
```

Capture the returned `dataTableId`.

- [ ] Same flow for `decision_trace_query_errors` with columns: `event_at_utc` (string), `query` (string), `warn` (string), `note` (string), `target_date` (string). Capture `dataTableId`.

- [ ] Save both table IDs in a session note (they'll be embedded in the SDK source as literal IDs).

### Task 2.3: Load SDK reference + discover node types

- [ ] `mcp__n8n__get_sdk_reference(section="all")`. Re-read. Pay specific attention to the `patterns` section for **fan-out branches after a chain** (multiple `.add(parent)` invocations vs explicit `merge()` / parallel-branch idiom). The Task 2.5 skeleton's `.add(buildFactPacket).to(writer)` repeats are PRELIMINARY — verify against the patterns section and replace if the SDK uses different syntax for "two independent post-fact-packet branches sharing one parent."
- [ ] `mcp__n8n__search_nodes(queries=["schedule trigger", "http request", "code", "anthropic", "telegram", "data table", "n8n data table", "insert rows"])`. Capture node IDs + discriminators.
- [ ] `mcp__n8n__get_node_types(nodeIds=[<all relevant nodes>])`. Capture exact parameter shapes — DO NOT guess. Per the SDK rules, "Use exact parameter names and structures from the type definitions."
- [ ] **Required outputs of this task before moving to Task 2.5:**
  - Data-table writer node ID + exact `parameters` shape for "insert rows" (don't carry the placeholder `n8n-nodes-base.n8n` into Task 2.5).
  - Anthropic node ID + `messages.values` shape (the existing workflow uses `@n8n/n8n-nodes-langchain.anthropic`).
  - SDK pattern for parallel branches after a shared parent, confirmed from `get_sdk_reference`.
  - If `search_nodes` reveals NO native data-table writer node (e.g., it's exposed only via internal HTTP API), surface this as a scope event before starting Task 2.5 — fall back to HTTP Request against `/rest/data-stores/<id>/rows` or similar, and budget for the discovery time.

### Task 2.4: Pull current workflow graph for faithful reproduction

- [ ] `mcp__n8n__get_workflow_details(workflowId="sxYIzx3uV01fKsZi")`. Output is large (~63 KB); saved to tool-results file by the harness.
- [ ] Use `jq` to extract per-node snapshots:

```bash
jq '.workflow.nodes[] | {name, type, typeVersion, position, parameters}' <saved-file>
```

- [ ] Cross-reference against `docs/n8n/decision-trace-report.workflow.json`. Note any drift (especially `active: true` in live vs `false` in file from PR #164 sanitization). Document drift in the Phase 2 PR description.

### Task 2.4-bis: Node inventory diagram (gate before Task 2.5)

Baseline workflow has **15 nodes** (not the 14 the README claims; pre-existing
README undercount — corrected in Task 2.10):

```
Trigger (1):    Daily 08:00 CT
Code (3):       Compute Target Date, Build Fact Packet, Extract Short Summary
HTTP Loki (2):  Loki Target Day Traces, Loki 7d Trace Counts
HTTP Influx (6): HVAC Decisions, Precool Window, HVAC Actions,
                 ComEd Spikes, Feed Health, Price Overlay Audit
Anthropic (1):  Claude Generate Report
Telegram (2):   Short Summary, Full Report
```

Post-rebuild target (**21 nodes**):
- Remove 1: `Loki 7d Trace Counts` → -1
- Add 3 Loki: Organic Counts, Synthetic Counts, First Seen → +3
- Add 4 data-table chain: 2× (Code mapper → data-table writer) → +4
- Net: 15 - 1 + 3 + 4 = **21**

- [ ] Sketch this as an ASCII diagram + linear connection list in your working notes. If Task 2.3 reveals there's no native data-table writer node and we fall back to HTTP, the +4 shrinks to +2 (Code mapper does HTTP itself) — target becomes 19.
- [ ] Confirm the inventory math BEFORE starting Task 2.5 authoring. Task 2.11 will verify the JSON parse count against this number.

### Task 2.5: Author `tools/n8n/decision_trace_report.workflow.ts`

The compose-block in the skeleton below is a **preliminary sketch** —
the `.add(buildFactPacket).to(...)` fan-out pattern is NOT verified
against the n8n SDK reference. Task 2.3 must surface the actual SDK
idiom for "two independent post-fact-packet branches sharing one
parent" (likely a `merge()`-style join or repeated `.add()` calls; do
not guess). Replace the compose-block with the verified pattern.
After authoring, hard-gate via `validate_workflow` before moving to
Task 2.6.

High-level structure (verbatim parameter values come from Task 2.4
snapshots + Task 2.3 type defs):

```typescript
import { workflow, node, trigger, newCredential, expr } from '@n8n/workflow-sdk';

// --- Trigger ---
const dailyTrigger = trigger({ /* Schedule Trigger, cron '0 8 * * *', position [0, 96] */ });

// --- Date computation ---
const computeTargetDate = node({ /* Code node, jsCode verbatim from current workflow */ });

// --- Loki queries ---
// URL distinction: target-day + first-seen use /loki/api/v1/query_range (range query
// returning raw log entries). Organic + synthetic counts use /loki/api/v1/query
// (instant query returning aggregated vector at single time= point). Do not copy
// the URL from the existing target-day node when defining the new aggregation nodes.
const lokiTargetDay = node({ /* HTTP url=http://192.168.20.10:3100/loki/api/v1/query_range, limit=20000 (was 5000), rest verbatim */ });
const lokiOrganicCounts = node({ /* HTTP url=http://192.168.20.10:3100/loki/api/v1/query (NOTE: NOT query_range), sum by (reason_code)(count_over_time({...} | json reason_code, tick_id | __error__="" | reason_code!="" | tick_id !~ "^commission.*" [7d])), time=target_end_utc */ });
const lokiSyntheticCounts = node({ /* HTTP url=.../api/v1/query, same as organic but tick_id =~ "^commission.*" */ });
const lokiFirstSeen = node({ /* HTTP url=.../api/v1/query_range, limit=1, direction=forward, start=cumulative_start_utc, end=target_end_utc */ });

// --- Influx queries (6 nodes, verbatim from current) ---
const influxHvacDecisions = node({ /* HTTP, credentials: { httpHeaderAuth: newCredential('InfluxDB Token') } */ });
// ... 5 more Influx nodes ...

// --- Build Fact Packet (Code, jsCode = current jsCode with 4 substitutions applied) ---
const buildFactPacket = node({ /* see Task 2.6 for the substitutions */ });

// --- Claude generates report ---
const claudeReport = node({ /* @n8n/n8n-nodes-langchain.anthropic, credentials: { anthropicApi: newCredential('Anthropic API') } */ });

// --- Extract short summary (Code, verbatim) ---
const extractShortSummary = node({ /* Code, verbatim */ });

// --- Telegram delivery (2 nodes, verbatim) ---
const telegramShort = node({ /* text, chatId from credential context, credentials: { telegramApi: newCredential('Telegram Bot') } */ });
const telegramFull = node({ /* sendDocument with binary */ });

// --- NEW: Data Table writers (post-fact-packet) ---
const writeReasonCodeHistory = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Persist Reason-Code History',
    parameters: {
      jsCode: `
        const fp = $('Build Fact Packet').first().json.fact_packet;
        const date = fp.target_date;
        const rows = [];
        for (const [code, count] of Object.entries(fp.observed_reason_codes_7d_organic || {})) {
          rows.push({ date, reason_code: code, variant: 'organic', count });
        }
        for (const [code, count] of Object.entries(fp.observed_reason_codes_7d_synthetic || {})) {
          rows.push({ date, reason_code: code, variant: 'synthetic', count });
        }
        return rows.map(r => ({ json: r }));
      `
    },
    position: [2128, 192]
  },
  output: [{ date: '2026-05-18', reason_code: 'LAYER_RESOLUTION_SCHEDULE_WINS', variant: 'organic', count: 3604 }]
});

const appendReasonCodeRows = node({
  type: 'n8n-nodes-base.n8n',  // n8n data table writer — confirm exact node type via search_nodes("data table")
  version: 1,
  config: {
    name: 'Write Reason-Code History',
    parameters: {
      resource: 'dataTable',
      operation: 'insertRows',
      dataTableId: '<captured-id-from-Task-2.2>',
      // rows mapped from upstream Code node
    },
    position: [2352, 192]
  },
  output: [{ inserted: 24 }]
});

// Similar pair for query_errors (conditional via IF node)
const writeQueryErrors = node({ /* Code that emits rows only if fact_packet.query_errors is non-empty */ });
const appendQueryErrorRows = node({ /* same insertRows pattern against decision_trace_query_errors table */ });

// --- Compose ---
export default workflow('sxYIzx3uV01fKsZi', 'Daily Decision-Trace Commissioning Report')
  .add(dailyTrigger)
  .to(computeTargetDate)
  .to(lokiTargetDay)
  .to(lokiOrganicCounts)
  .to(lokiSyntheticCounts)
  .to(lokiFirstSeen)
  .to(influxHvacDecisions)
  .to(influxPrecoolWindow)
  .to(influxHvacActions)
  .to(influxComedSpikes)
  .to(influxFeedHealth)
  .to(influxPriceOverlayAudit)
  .to(buildFactPacket)
  .to(claudeReport)
  .to(extractShortSummary)
  .to(telegramShort)
  .add(extractShortSummary)
  .to(telegramFull)
  .add(buildFactPacket)  // parallel branch: persistence
  .to(writeReasonCodeHistory)
  .to(appendReasonCodeRows)
  .add(buildFactPacket)
  .to(writeQueryErrors)
  .to(appendQueryErrorRows);
```

- [ ] **Step note:** Confirm exact n8n data table node type via
  `search_nodes("data table")` during Task 2.3. The SDK reference shows
  `node({ type: ... })` for general nodes; data table insert may be a
  specific node ID or a generic operation flag. Use the type def.

### Task 2.6: Apply four jsCode substitutions to Build Fact Packet

Within the `buildFactPacket` SDK definition, the jsCode parameter is the
**existing 9131-char jsCode** with these four targeted substitutions:

- **Substitution A — add `parseLokiVector` near `parseLokiResponse`:**

Add this preamble comment immediately above the function (one line — narrow exception to the "no comments" rule because the WHY is a non-obvious data-shape invariant that future schema drift would silently violate):

```javascript
// Organic vs synthetic LogQL split assumes every organic decision_trace
// line has empty-or-absent tick_id. Verified 2026-05-19 (0 empty tick_id
// over 7d). If organic tick_ids ever take non-commission string values,
// they'll silently disappear from BOTH aggregation branches.
function parseLokiVector(resp) {
  if (!resp || !resp.data || !resp.data.result) return {};
  const out = {};
  for (const entry of resp.data.result) {
    const code = entry.metric && entry.metric.reason_code;
    const v = entry.value && entry.value[1];
    if (typeof code === 'string' && code !== '' && v !== undefined) {
      const n = Number(v);
      if (!isNaN(n)) out[code] = n;
    }
  }
  return out;
}
```

- **Substitution B — replace the `loki7dItem`/`traces7d`/`tally()` block:**

OLD (verbatim from current jsCode):
```javascript
const loki7dItem = $('Loki 7d Trace Counts').first();
checkError('loki_7d', loki7dItem);
const traces7d = parseLokiResponse(loki7dItem.json);
if (traces7d.length >= 5000) queryErrors.push({ query: 'loki_7d', warn: 'limit_saturated', note: 'observed counts may be truncated at 5000 entries' });
const organic7d = traces7d.filter(function (t) { return !isSynthetic(t); });
const synthetic7d = traces7d.filter(isSynthetic);
const observed_reason_codes_7d_organic = tally(organic7d);
const observed_reason_codes_7d_synthetic = tally(synthetic7d);
```

NEW:
```javascript
const lokiOrganicItem = $('Loki 7d Organic Reason Counts').first();
checkError('loki_7d_organic', lokiOrganicItem);
const observed_reason_codes_7d_organic = parseLokiVector(lokiOrganicItem.json);
const lokiSyntheticItem = $('Loki 7d Synthetic Reason Counts').first();
checkError('loki_7d_synthetic', lokiSyntheticItem);
const observed_reason_codes_7d_synthetic = parseLokiVector(lokiSyntheticItem.json);
```

- **Substitution C — replace `trace_first_seen_ms` for-loop:**

OLD:
```javascript
let trace_first_seen_ms = null;
for (const t of traces7d) {
  const ms = new Date(t.ts).getTime();
  if (!isNaN(ms) && (trace_first_seen_ms === null || ms < trace_first_seen_ms)) {
    trace_first_seen_ms = ms;
  }
}
```

NEW:
```javascript
const lokiFirstSeenItem = $('Loki 7d Trace First Seen').first();
checkError('loki_7d_first_seen', lokiFirstSeenItem);
const firstSeenTraces = parseLokiResponse(lokiFirstSeenItem.json);
let trace_first_seen_ms = null;
if (firstSeenTraces.length > 0 && firstSeenTraces[0].ts) {
  const ms = new Date(firstSeenTraces[0].ts).getTime();
  if (!isNaN(ms)) trace_first_seen_ms = ms;
}
```

- **Substitution D — target-day saturation guard. Immediately after `const targetTraces = parseLokiResponse(lokiTargetItem.json);` insert:**

```javascript
if (targetTraces.length >= 20000) queryErrors.push({ query: 'loki_target_day', warn: 'limit_saturated', note: 'observed target-day traces may be truncated at 20000 entries' });
```

All four substrings are verified present verbatim in the current
jsCode (Task 2.4's parity check confirmed this in pre-flight research).

### Task 2.7: Validate SDK source

- [ ] `mcp__n8n__validate_workflow(code=<file contents>)`.
- [ ] If `errors` non-empty: fix systematically, re-validate. Loop until `errors` empty.
- [ ] Read `warnings` carefully. Surface non-trivial warnings in the PR description; resolve egregious ones (e.g. expression syntax errors masked as warnings).
- [ ] When validation returns `valid: true`, the response includes the parsed workflow JSON. **Hold it in memory; do NOT save to disk yet** — saving happens in Task 2.8 only AFTER `update_workflow` succeeds, so a failed update doesn't leave the repo with a stale derived file.

### Task 2.8: Deploy draft + save derived JSON + generate pin-data fixture

- [ ] `mcp__n8n__update_workflow(workflowId="sxYIzx3uV01fKsZi", code=<SDK source>)` to write the new draft. Capture the response's `autoAssignedCredentials` array — verify all 6 Influx + 1 Anthropic + 2 Telegram credentials carried through via name+type match. If any non-HTTP node shows missing, manual reattach in n8n UI.
- [ ] **NOW save the derived JSON** (from Task 2.7's validate response) to `docs/n8n/decision-trace-report.workflow.json` (overwriting). Prepend description-field marker `[DERIVED FROM tools/n8n/decision_trace_report.workflow.ts — DO NOT HAND-EDIT]`.
- [ ] `mcp__n8n__prepare_test_pin_data(workflowId="sxYIzx3uV01fKsZi")`. Capture the returned JSON schemas.
- [ ] Author `tools/n8n/fixtures/decision_trace_report.pin.json` with deterministic sample data for every node in `nodeSchemasToGenerate`. Default pattern per docs: empty `[{"json": {}}]` for nodes in `nodesWithoutSchema`. For our case, the substantive fixtures are:
  - Schedule Trigger: `[{}]`
  - 4 Loki HTTP nodes: realistic Loki response shapes (use samples captured in Task 2.4 via `get_execution` against the prior live run, OR hand-craft from the JSON-schema returned by prepare_test_pin_data).
  - 6 Influx HTTP nodes: realistic Flux CSV response samples (similarly).
  - Anthropic node: a sample Markdown report text response.
  - Telegram nodes: success acknowledgments.
- [ ] Include in the fixture at least one scenario per change-under-test:
  - Normal day: organic counts populated, synthetic small, no saturation.
  - Saturation scenario: target-day fixture with >=20000 stream entries → expect `query_errors[].warn == "limit_saturated"`.
  - Empty 7d window: organic + synthetic both empty → expect coverage='none' branch.
- [ ] Commit fixture file.

### Task 2.9: Test with pin data (acceptance gate 1)

**What this gate covers**: Build Fact Packet jsCode logic + connection
graph + parser correctness + saturation-guard wiring. The Loki / Influx
HTTP nodes are pinned to fixture data; Anthropic + Telegram are also
pinned (per `test_workflow` semantics — credential-bearing + HTTP nodes
all pin). That's not a deficiency — Anthropic/Telegram behavior is not
under test in this fix; their pinning is by design.

- [ ] `mcp__n8n__test_workflow(workflowId="sxYIzx3uV01fKsZi", pinData=<fixture contents>)`.
- [ ] Inspect output:
  - Status: `success`.
  - Build Fact Packet output: organic + synthetic counts present, derived from vector parsing (not from `tally()`).
  - First-seen timestamp populated.
  - For the saturation-scenario fixture pin: `query_errors` contains the `loki_target_day` saturation warning.
- [ ] If failures: fix SDK source, re-validate, re-update, re-test. Loop.

### Task 2.10: Update README

**File:** `docs/n8n/decision-trace-report.README.md`

- [ ] Add a "## Source of truth" section near top pointing at `tools/n8n/decision_trace_report.workflow.ts` and citing AGENTS.md.
- [ ] Update the pipeline diagram inside the ``` ``` block (the ASCII chain showing `Schedule → Compute Target Date → Loki: target-day → Loki: 7-day ...`). New shape: replace the single "Loki: 7-day decision_trace.* stream" line with three lines (Organic Reason Counts instant query, Synthetic Reason Counts instant query, Trace First Seen range query limit=1). Add two parallel-branch lines after Build Fact Packet for the data-table persistence chains.
- [ ] Find string `14 nodes total` and update to `21 nodes total` (the README's existing "14" was already wrong against the current 15-node workflow — bump to 21 for the post-rebuild count; document the prior undercount in the PR description as a "while we're in here" correction).
- [ ] Find string `Linear chain except the final Extract Short Summary fan-out` and update to reflect the new fan-out (Extract Short Summary still fans out to two Telegram nodes; Build Fact Packet now ALSO fans out to two persistence chains).
- [ ] Fix the troubleshooting line that mentions Loki cap at 5000 — replace `limit > 5000` text with `limit > 20000` and add a reference to `loki-config.yml`'s `max_entries_limit_per_query` key.
- [ ] Update Step 6 (`Attach to all 5 Influx HTTP Request nodes ...`) — current README undercount; bump to "6 Influx HTTP Request nodes" and add `Influx Price Overlay Audit` to the parenthesized list.
- [ ] Add a "## Persistence" section describing the two data tables, their columns, and that the SDK source has the table IDs hardcoded.
- [ ] Bump frontmatter `date:`.

### Task 2.11: JSON parity + commit

- [ ] `python3 -c "import json; doc = json.load(open('docs/n8n/decision-trace-report.workflow.json')); print('nodes:', len(doc['nodes']))"`. **Expect 21** (15 baseline - 1 removed `Loki 7d Trace Counts` + 3 new Loki + 4 data-table chain nodes). If Task 2.3 surfaced no native data-table writer and the implementation fell back to HTTP-Request-driven inserts (2 nodes per chain → 4 still, but combined Code+HTTP variant possible at 2 nodes total → 17), restate accordingly and reconcile with the Task 2.4-bis diagram.
- [ ] `git status` — expect:
  - `tools/n8n/decision_trace_report.workflow.ts` (new)
  - `tools/n8n/fixtures/decision_trace_report.pin.json` (new)
  - `docs/n8n/decision-trace-report.workflow.json` (modified, regenerated)
  - `docs/n8n/decision-trace-report.README.md` (modified)
- [ ] Commit. Message body explains the bundled scope (saturation fix + SDK adoption + data tables + test fixture) + acknowledges that this is the canonical pattern future workflows follow.
- [ ] `git push -u origin fix/decision-trace-sdk-rebuild`
- [ ] `gh pr create --base main` with body covering all phase-2 changes, pre-flight verification table, and explicit pointer at Phase 3 verification gates.
- [ ] **STOP.** Surface PR URL. Wait for Chris merge.

---

## Phase 3 — Live verification + publish

Runs AFTER Phase-2 PR merge. Plan stops at merge per AGENTS.md
branching policy; Chris executes Phase 3 or asks for hand-off.

### Task 3.1: Sync main

- [ ] `git checkout main && git pull --ff-only origin main && git branch -d fix/decision-trace-sdk-rebuild`

### Task 3.2: Manual-mode live run (acceptance gate 2)

- [ ] `mcp__n8n__execute_workflow(workflowId="sxYIzx3uV01fKsZi", executionMode="manual")`. Manual mode runs the draft against live services. ONE real Telegram message + ONE real Anthropic call. Expected cost $0.01-0.02 per README.
- [ ] Wait for execution to finish. `mcp__n8n__get_execution(executionId=<id>, workflowId="sxYIzx3uV01fKsZi")` if status isn't immediately available.
- [ ] Inspect Build Fact Packet output:
  - `observed_reason_codes_7d_organic` is non-empty object.
  - `observed_reason_codes_7d_synthetic` is non-empty object.
  - `observability.decision_trace_first_seen_utc` is a valid ISO string.
  - `query_errors` is empty (no saturation in live data).
- [ ] Inspect Data Table writes: rows appended to both tables for today's date.

### Task 3.3: Same-`time=` deterministic LogQL oracle (acceptance gate 3)

- [ ] Capture `fact_packet.target_window_end_utc` from Task 3.2.
- [ ] Probe Loki at the SAME `time=`:

```bash
ssh chris@192.168.20.10 'curl -sG "http://localhost:3100/loki/api/v1/query" \
  --data-urlencode "query=sum by (reason_code)(count_over_time({container=\"hvac-scheduler\"} |= \"decision_trace\" | json reason_code=\"reason_code\", tick_id=\"tick_id\" | __error__=\"\" | reason_code!=\"\" | tick_id !~ \"^commission.*\" [7d]))" \
  --data-urlencode "time=<TARGET_END_UTC_NS>" 2>/dev/null'
```

- [ ] Compare each reason-code's count from the probe against the fact_packet's `observed_reason_codes_7d_organic` object. Same-`time=` instant queries against the same Loki instance are deterministic on the underlying chunks. **Expect exact match per reason_code.** If any nonzero divergence appears: do NOT auto-fail. First compare the timestamps of the newest 5 events for that reason_code in each query (one query via `topk(5, ...)` on the probe side, one via raw log inspection in n8n's workflow execution). A nanosecond-boundary race on `time=` is possible but should affect at most ±1 event per code. Anything beyond that magnitude IS a real bug — stop and diagnose.

### Task 3.4: Publish (promote draft to active)

- [ ] `mcp__n8n__publish_workflow(workflowId="sxYIzx3uV01fKsZi")`. Capture the new `activeVersionId`.
- [ ] From this point, the 8am daily cron will run the new published version.

### Task 3.5: Production-mode verification (acceptance gate 4)

Per memory `project-n8n-publish-may-corrupt-jscode` — publish can mangle
backslash-n escapes in jsCode. Mandatory verification.

- [ ] `mcp__n8n__execute_workflow(workflowId="sxYIzx3uV01fKsZi", executionMode="production")`. Runs the published version.
- [ ] Verify no JS errors in any Code node (especially Build Fact Packet — `parseLokiVector is not defined` would indicate publish corruption).
- [ ] If corruption detected: `mcp__n8n__unpublish_workflow(workflowId="sxYIzx3uV01fKsZi")` to roll back. Then repair via the Postgres workaround per memory (`UPDATE workflow_history.nodes FROM workflow_entity.nodes`). Re-publish + re-verify.
- [ ] Verify data table rows appended for this run.

### Task 3.6: Reconcile `active` flag in derived JSON

The validate_workflow output may declare `active: false`. Live workflow
is `active: true`. Per project doc-hygiene, the derived JSON should
reflect live state.

- [ ] Open a tiny PR that sets `active: true` in the derived JSON OR
  document the divergence in `docs/n8n/decision-trace-report.README.md`
  as expected (derived export is review-default-off; live is on).
  **Recommend the documentation route** — keeping derived files
  review-default-off is a sane convention for any project handling PII
  sanitization in PRs.

### Task 3.7: Archive plan

- [ ] `git mv docs/plans/decision-trace-report-loki-fix-2026-05-19.md docs/plans/archive/`
- [ ] Bump status `draft` → `archived`.
- [ ] Commit as final close-out.

---

## Self-review

**Spec coverage:** every operator-approved item maps to a task —
saturation fix, target-day 20k limit + warning, 7d aggregation
organic/synthetic split, first-seen preserved, build-fact-packet
refactor, README correction, no service code touched, n8n SDK adoption,
pin-data test fixture, Data Tables for reason-code history + query
errors, operational verification.

**Removed earlier framing:** the "documented deviation from outside-in
TDD" caveat in prior plan revisions is removed. `test_workflow` IS the
test harness; pin-data fixtures ARE acceptance tests. The acceptance
gates are: pin-data test_workflow pass → live manual run with same-`time=`
oracle → publish → production-mode verify. No skip / xfail; real signal at
every gate.

**Risks consciously taken:**
- SDK + Data Tables learning curve absorbs ~1h before authoring is
  productive. Acceptable per `feedback-hold-deadlines-invest-hours`.
- Faithful reproduction of 14 unchanged nodes (15 baseline - 1 removed)
  carries subtle-drift risk. Mitigated by pin-data test (catches
  code/connection bugs) + same-`time=` oracle (catches data-shape
  divergence on changed paths).
- `validate_workflow` may re-format the exported JSON differently than
  the current file. Accepted; JSON is derived from this PR onward.
- Data Tables become a new persistent surface to maintain. Two tables,
  no schema migrations expected pre-OSF.
- Phase 0 dependency: Phase 2's bundled scope assumes SDK-as-canonical
  lands. Explicit revert path documented at the head of Phase 2 if
  Phase 0 doesn't merge.

**What Gate 1 covers and doesn't:** `test_workflow` with pin data
exercises Build Fact Packet jsCode against deterministic inputs plus
connection graph + saturation guard wiring. Anthropic + Telegram nodes
are pinned (per `test_workflow` semantics, credential-bearing and HTTP
nodes all pin). Their behavior is NOT under test in this fix; pinning
them is by design. Gates 2-4 cover live-data behavior + publish
integrity.

**Placeholder scan:** zero TBDs. Three genuine implementation-time
discoveries are explicit (Task 2.3 type definitions + SDK fan-out
pattern + data-table writer node identification; Task 2.4 live
node-graph snapshots) — that's the SDK rules demanding "look up the
real shape before coding," not hand-waving.
