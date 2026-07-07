<!-- docs/test-reports/README.md -->
# Decision-trace commissioning reports — scratch space

This directory is a local scratch space for ad-hoc Markdown report
dumps. Contents are **gitignored** — these are transient artifacts,
not permanent docs. Tracked: this README + `.gitkeep`. Everything
else under here ignored.

## Where reports actually live now

Daily commissioning reports are produced by
`tools/decision-trace-report/` (Python), run by a local Windows
scheduled task (~08:00 CT) on the operator's workstation, and
delivered by email via Gmail. Rendered copies land under
`tools/decision-trace-report/reports/` (gitignored).

This directory remains useful for one-off local renders when sharing
a specific run by file.

## History

An n8n workflow briefly produced these reports as a Telegram
prototype (May 2026); it was retired and its repo artifacts deleted
in the 2026-07-06 cleanup. NOTE: the report's Loki queries target
rev-3 `decision_trace.*` event names and go empty under the rev 4
controller (2026-07-06) until the report tool is refreshed.
