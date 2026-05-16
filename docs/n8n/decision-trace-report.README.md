---
date: 2026-05-16
owner: chris
status: active
role-label: chris
---

# n8n workflow — daily decision-trace commissioning report

Replaces the Python `tools/decision_trace_report` Markdown renderer. Same
fact-collection (Loki + InfluxDB queries, the load-bearing lessons from
PRs #125/#126 ported into n8n nodes); narrative generation is delegated
to Claude via the Anthropic node. Output is delivered entirely through
Telegram (short summary + full Markdown as a document attachment) — no
server-side file persistence.

**Workflow file:** `decision-trace-report.workflow.json`
**Prompt:** `decision-trace-report.prompt.md`

## What it does

```
Schedule (08:00 CT)
   → Compute target date (yesterday CT, UTC bounds)
   → Loki: target-day decision_trace.* events
   → Loki: 7-day decision_trace.* stream (for observed reason-code counts)
   → Influx: hvac.decisions for target date
   → Influx: hvac.precool_window for target date
   → Influx: hvac.actions for target CT day
   → Influx: comed.prices spikes >=10c for target CT day
   → Influx: feed last-write times (unioned 8-measurement query)
   → Build fact packet (JS: parse Flux CSV + Loki JSON, normalize, tag
     synthetic commission_* traces, pair spikes with overlay traces +/-5min)
   → Claude Sonnet 4.6 (Anthropic node, system prompt from prompt.md,
     maxTokens 4096)
   → Extract short summary (executive summary + needs-attention sections,
     emits binary Markdown for the document attachment)
   → Telegram short summary (Markdown-formatted text message)
   → Telegram full report (same Markdown as a document attachment)
```

14 nodes total. Linear chain except the final Extract Short Summary
fan-out (Telegram short summary + Telegram document from one parent).

## Initial setup (one-time)

### 1. Import the workflow

In the n8n UI (http://192.168.20.10:5678):

1. Menu → Import from File → select
   `docs/n8n/decision-trace-report.workflow.json`
2. n8n creates the workflow in your personal project.

### 2. Configure credentials

Three credentials need to be created in n8n's Credentials panel
(Settings → Credentials → New):

**A. `InfluxDB Token` (type: Header Auth)**
- Header Name: `Authorization`
- Header Value: `Token <INFLUXDB_INIT_ADMIN_TOKEN>` (from
  `deploy/energy-stack/secrets/env.sops.env` — decrypt with
  `sops -d` to read the token)
- Attach to all 5 Influx HTTP Request nodes (`Influx HVAC Decisions`,
  `Influx Precool Window`, `Influx HVAC Actions`, `Influx ComEd Spikes`,
  `Influx Feed Health`).

**B. `Anthropic API` (type: Anthropic API)**
- API Key: your Anthropic API key
- Attach to the `Claude Generate Report` node.

**C. `Telegram Bot` (type: Telegram API)**
- Access Token: `<TELEGRAM_BOT_TOKEN>` (from SOPS-encrypted env file —
  same `@EnergyStackBot` token used by the existing stack)
- Attach to **both** `Telegram Short Summary` and `Telegram Full Report`
  nodes.

### 3. Fill the Telegram chat ID

Both Telegram nodes have a `chatId` parameter. If imported fresh, they
may carry placeholder text — replace with the numeric chat ID (same
value as `TELEGRAM_CHAT_ID` in SOPS env).

### 4. Verify timezone

The Schedule Trigger uses cron `0 8 * * *`. This fires at 08:00 in the
n8n container's local timezone. Verify the container has
`TZ=America/Chicago` set (most Pi-lab containers do). If not, the cron
fires in UTC and you'll need to adjust the expression to `0 13 * * *`
during CDT or `0 14 * * *` during CST.

## First test run

Before activating the schedule:

1. Open the workflow in the n8n UI.
2. Click "Execute workflow" (top right). This fires a one-off run with
   yesterday CT as the target.
3. Watch each node's output:
   - Compute Target Date: should show `target_date_iso` matching
     yesterday CT.
   - Loki + Influx nodes: should return successfully (not 4xx).
   - Build Fact Packet: should produce a `fact_packet_json` string.
   - Claude Generate Report: should produce Markdown starting with
     `# Decision-trace commissioning report — YYYY-MM-DD`.
   - Telegram Short Summary: should send a text message to your chat.
   - Telegram Full Report: should send the Markdown report as a document attachment.

If any node fails, fix the credential / URL / parameter and re-run.

## Activating the daily schedule

Once a manual test run is green, activate the workflow (toggle in the
top-right of the workflow editor). The schedule fires at 08:00 CT every
day after activation.

## Tweaking the prompt

The system prompt lives in two places:

1. **Source of truth**: `docs/n8n/decision-trace-report.prompt.md`
2. **Live**: the `options.system` parameter of the `Claude Generate Report`
   node in the n8n UI.

When you edit the prompt:
- Edit the `.prompt.md` file (review + version-control).
- Copy the new prompt into the Claude node's `options.system` field via
  the n8n UI. (Future improvement: read prompt from a mounted file.)
- Save the workflow.

## Tweaking the fact packet

The `Build Fact Packet` Code node's `jsCode` parameter is the
structural normalizer. If you need to add a new field to the fact packet
(e.g., expose 5CP avoidance bands), edit the Code node's JS, then update
the prompt to mention what the new field means.

Keep the contract honest: the Code node should not interpret the data,
only restructure it. Interpretation belongs in the Claude prompt.

## Troubleshooting

**Empty Markdown output**: Claude received an empty fact packet. Check
the Build Fact Packet node's output — likely a Loki/Influx query failed
silently (each HTTP node has `neverError: true` to keep the workflow
going on partial failure; query errors land in `fact_packet.query_errors`).

**Telegram message says "(no summary section)"**: Claude didn't follow
the prompt's `## Executive summary` heading. Lower the temperature
(`Claude Generate Report` → `options.temperature` from `0.3` to `0.1`)
or tighten the prompt's output-format instructions.

**HTTP 400 from Loki**: limit > 5000 hits Loki's server-side cap. The
workflow caps at 5000 already; if you see this, your Loki config
overrides the default lower.

**Schema collision error from Influx Feed Health**: the canonical pattern
is `keep(columns: ["_time"])` BEFORE `group()`. Verify the Flux body in
the `Influx Feed Health` node hasn't been edited to skip the `keep`.

## Operating notes

- The workflow runs entirely on Pi-lab (no Windows-side dependency).
  If the Pi-lab n8n container is up at 08:00 CT, the daily report is
  produced.
- Anthropic API cost per run is roughly $0.01-0.02 (fact packet
  ~2-5 KB, output ~2-4 KB). Daily cost: well under $1/month.
- The legacy Python tool at `tools/decision_trace_report/` is still
  importable for ad-hoc CLI investigation. Plan to sunset it after this
  n8n workflow has run successfully for ~1 week.

## See also

- Spec for the fact packet contract: this directory's `*.prompt.md`
- Source of the Flux query lessons: PR #125 (initial impl), #126 (live-
  verification fixes)
- Decision codes the report references: `deploy/energy-stack/hvac-scheduler/decision_codes.py`
