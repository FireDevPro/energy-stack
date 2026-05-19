---
date: 2026-05-19
owner: chris
status: active
role-label: chris
---

# n8n workflow SDK sources

Canonical SDK source for every n8n workflow in this repo. The corresponding JSON files in `docs/n8n/` are derived exports for review-diff visibility only — never hand-edit them.

See `AGENTS.md` § "n8n workflows" for the standing authoring rule.

## Workflows

| ID | Name | SDK source | Derived JSON | Pin-data fixture |
|---|---|---|---|---|
| `sxYIzx3uV01fKsZi` | Daily Decision-Trace Commissioning Report | `decision_trace_report.workflow.ts` *(coming in fix/decision-trace-sdk-rebuild PR)* | `docs/n8n/decision-trace-report.workflow.json` | `fixtures/decision_trace_report.pin.json` *(coming)* |

## Available MCP capabilities (n8n 2.19.5)

- **Workflow lifecycle**: search, get details, create/update/archive, publish/unpublish.
- **Authoring**: `get_sdk_reference`, `search_nodes`, `get_node_types`, `validate_workflow`.
- **Testing**: `prepare_test_pin_data`, `test_workflow` (pins trigger + credential + HTTP nodes; Code nodes execute normally; 5-minute MCP timeout).
- **Execution**: `execute_workflow` (`executionMode="manual"` = draft; `executionMode="production"` = published version), `get_execution`.
- **Data tables (2.16.0+)**: search/create/rename/archive tables; add/rename/delete columns; add rows (max 1000/call, values must be string/number/boolean/null). Use for cross-run persistence such as daily history snapshots or event logs.

## Operating notes

- `update_workflow` writes the workflow's DRAFT only. The live active version keeps running until `publish_workflow` is explicitly called. Use this to test safely against production.
- `update_workflow` preserves user-configured credentials by matching nodes by name + type. Keep node names stable across updates to avoid manual credential reattachment.
- After every publish, run `execute_workflow` in `executionMode="production"` once to confirm the published version's jsCode wasn't mangled (per memory `project-n8n-publish-may-corrupt-jscode`).
- `validate_workflow` returns warnings AND errors as separate concepts. Warnings can coexist with `valid: true`; surface them in PR review but don't auto-block.

## Adding a new workflow

1. Author `tools/n8n/<workflow>.workflow.ts`.
2. `mcp__n8n__validate_workflow(code=<file contents>)` until clean.
3. `mcp__n8n__create_workflow_from_code(code=<file contents>, name=..., description=...)`. Capture the returned `workflowId`.
4. Manually attach credentials to HTTP Request nodes via n8n UI (per docs, these are skipped during auto-assignment).
5. Author pin-data fixture at `tools/n8n/fixtures/<workflow>.pin.json` using `prepare_test_pin_data` output.
6. `test_workflow` until pass.
7. `execute_workflow` (manual) for one live run.
8. `publish_workflow`.
9. `execute_workflow` (production) for the publish-corruption verify.
10. Update the table above.
