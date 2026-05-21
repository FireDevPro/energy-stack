---
date: 2026-05-21
owner: chris
status: active
role-label: chris
---

# Decision-trace report — repo-tracked artifacts

This directory holds the repo-tracked artifacts for the daily decision-trace
commissioning report. The report itself is produced by a Claude Code
**desktop scheduled task** (see [docs](https://code.claude.com/docs/en/desktop-scheduled-tasks)),
not by an n8n workflow.

## Architecture

```
~/.claude/scheduled-tasks/decision-trace-report/SKILL.md   # the scheduled task
                                                           # (Chris-local; not in repo)
                                ↓ (references)
tools/decision-trace-report/report-template.md             # style exemplar (this repo)
```

The scheduled task fires daily at 08:00 CT. Its instructions tell Claude
to pull yesterday's trace data from Loki and Influx, produce a
paragraph-form narrative matching the style of `report-template.md`, and
deliver the result via email through the Workspace MCP.

## Files in this directory

- **`report-template.md`** — style exemplar. The agent reads this each
  run to learn the section ordering, tone, level of detail, and how to
  render reason codes in plain English. The content of this file is a
  cleaned-up example from 2026-05-19; the date in its frontmatter is the
  creation date of the exemplar, not the date of the example.
- **`reports/`** — local copies of generated daily reports written by
  the scheduled task as a side effect of each run. Gitignored. Useful
  for local grep and reference; not the source of truth (the canonical
  delivery is the email, and each run re-derives its content from
  Loki/Influx).

## Editing the report style

To change how the daily report reads:

1. Edit `report-template.md` to demonstrate the new style.
2. Next morning's run picks it up automatically — no skill edit needed.

To change what data the report covers, or to add new sections / drop
existing ones, edit the SKILL.md at
`~/.claude/scheduled-tasks/decision-trace-report/SKILL.md`.

## History

Replaces the n8n workflow `sxYIzx3uV01fKsZi` ("Daily Decision-Trace
Commissioning Report"). The migration plan and the abandoned saturation-fix
PR series are archived at
[`docs/plans/archive/decision-trace-report-loki-fix-2026-05-19.md`](../../docs/plans/archive/decision-trace-report-loki-fix-2026-05-19.md).
