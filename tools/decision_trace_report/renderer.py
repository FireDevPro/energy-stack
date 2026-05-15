"""Assembles section markdown into a full report + anomaly summary."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AnomalySummary:
    unexpected_codes: int
    supervisor_non_approved: int
    stale_feeds: int
    trace_influx_discrepancies: int
    unexplained_spikes: int
    query_errors: int

    def total(self) -> int:
        return (
            self.unexpected_codes
            + self.supervisor_non_approved
            + self.stale_feeds
            + self.trace_influx_discrepancies
            + self.unexplained_spikes
            + self.query_errors
        )

    def is_all_green(self) -> bool:
        return self.total() == 0

    def heartbeat_status(self) -> str:
        return "all green" if self.is_all_green() else "open the report"

    def render_table(self) -> str:
        lines = [
            "## Anomaly summary",
            "",
            "| Type | Count |",
            "|---|---:|",
            f"| Unexpected reason codes | {self.unexpected_codes} |",
            f"| Supervisor non-approved decisions | {self.supervisor_non_approved} |",
            f"| Stale feeds | {self.stale_feeds} |",
            f"| Trace-vs-Influx discrepancies | {self.trace_influx_discrepancies} |",
            f"| Unexplained price spikes | {self.unexplained_spikes} |",
            f"| Query errors | {self.query_errors} |",
            "",
            f"**Status: {self.heartbeat_status()}**",
            "",
        ]
        return "\n".join(lines)


def build_report(
    *,
    target_date: str,
    rendered_at: datetime,
    sections: dict[str, str],
    anomaly_summary: AnomalySummary,
) -> str:
    """Assemble the full markdown report."""
    lines = [
        f"# Decision-trace commissioning report — {target_date}",
        "",
        f"_Rendered at {rendered_at.isoformat()}_",
        "",
        "## Table of contents",
        "",
        "- [Anomaly summary](#anomaly-summary)",
        "- [§1 Night-before decision audit](#1-night-before-decision-audit)",
        "- [§2 Live day-of decision audit](#2-live-day-of-decision-audit)",
        "- [§3 Price spike reaction audit](#3-price-spike-reaction-audit)",
        "- [§4 Feed + telemetry health](#4-feed--telemetry-health)",
        "- [§5 Coverage scorecard](#5-coverage-scorecard)",
        "",
        anomaly_summary.render_table(),
    ]
    for key in ["night_before", "day_of", "price_spikes", "feed_health", "coverage_scorecard"]:
        body = sections.get(key)
        if body:
            lines.append(body)
            lines.append("")
    return "\n".join(lines)
