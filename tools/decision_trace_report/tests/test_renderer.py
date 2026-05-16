"""Tests for renderer.build_report — assembles section markdown into one
file + anomaly summary."""
from datetime import datetime, timezone

from tools.decision_trace_report.renderer import AnomalySummary, build_report


def test_build_report_starts_with_header_and_toc():
    """Every report has a YAML-style frontmatter or header + ToC."""
    summary = AnomalySummary(
        unexpected_codes=0,
        supervisor_non_approved=0,
        stale_feeds=0,
        trace_influx_discrepancies=0,
        unexplained_spikes=0,
        query_errors=0,
    )
    out = build_report(
        target_date="2026-05-15",
        rendered_at=datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc),
        sections={
            "night_before": "## §1 Night-before\n\nbody",
            "day_of": "## §2 Day-of\n\nbody",
            "price_spikes": "## §3 Price spikes\n\nbody",
            "feed_health": "## §4 Feed health\n\nbody",
            "coverage_scorecard": "## §5 Coverage\n\nbody",
        },
        anomaly_summary=summary,
    )
    assert "# Decision-trace commissioning report — 2026-05-15" in out
    assert "## Table of contents" in out or "## ToC" in out
    assert "§1" in out and "§2" in out and "§3" in out and "§4" in out and "§5" in out


def test_build_report_includes_anomaly_summary():
    summary = AnomalySummary(
        unexpected_codes=1,
        supervisor_non_approved=2,
        stale_feeds=0,
        trace_influx_discrepancies=0,
        unexplained_spikes=0,
        query_errors=0,
    )
    out = build_report(
        target_date="2026-05-15",
        rendered_at=datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc),
        sections={},
        anomaly_summary=summary,
    )
    assert "Anomaly summary" in out or "anomaly" in out.lower()
    assert "Unexpected reason codes" in out
    assert "1" in out  # the count
    assert "2" in out


def test_anomaly_summary_all_green():
    summary = AnomalySummary(0, 0, 0, 0, 0, 0)
    assert summary.is_all_green() is True
    assert "all green" in summary.heartbeat_status().lower()


def test_anomaly_summary_open_report():
    summary = AnomalySummary(1, 0, 0, 0, 0, 0)
    assert summary.is_all_green() is False
    assert "open the report" in summary.heartbeat_status().lower()
