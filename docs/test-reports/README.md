<!-- docs/test-reports/README.md -->
# Decision-trace commissioning reports — scratch space

This directory is a local scratch space for ad-hoc Markdown report
dumps. Contents are **gitignored** — these are transient artifacts,
not permanent docs. Tracked: this README + `.gitkeep`. Everything
else under here ignored.

## Where reports actually live now

Daily commissioning reports are produced by the n8n workflow
`docs/n8n/decision-trace-report.workflow.json` and delivered to
Telegram (short summary message + full Markdown as a document
attachment). See `docs/n8n/decision-trace-report.README.md` for the
operator runbook.

This directory remains useful for one-off local renders extracted
from n8n execution data when debugging the workflow or sharing a
specific run by file rather than by Telegram.

## History

The Python tool that previously rendered reports into this directory
(`tools/decision_trace_report/`) was sunset after the n8n workflow
proved out.
