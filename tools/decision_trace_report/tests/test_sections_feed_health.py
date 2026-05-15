"""Tests for §4 feed health section."""
from datetime import datetime, timedelta, timezone

from tools.decision_trace_report.sections.feed_health import (
    classify_age,
    render,
)


def test_classify_age_fresh_warn_stale():
    """Three buckets for a continuous feed with a 5-min warn / 10-min
    stale threshold."""
    assert classify_age(timedelta(minutes=1), warn=timedelta(minutes=5),
                         stale=timedelta(minutes=10)) == "fresh"
    assert classify_age(timedelta(minutes=7), warn=timedelta(minutes=5),
                         stale=timedelta(minutes=10)) == "warn"
    assert classify_age(timedelta(minutes=15), warn=timedelta(minutes=5),
                         stale=timedelta(minutes=10)) == "stale"


def test_render_reports_fresh_feed():
    """A fresh continuous feed shows ✅ status + age in the markdown."""
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    feeds = [
        {
            "name": "comed.prices",
            "kind": "continuous",
            "last_write": datetime(2026, 5, 16, 7, 59, tzinfo=timezone.utc),
            "warn": timedelta(minutes=5),
            "stale": timedelta(minutes=10),
        },
    ]
    out = render(now=now, feeds=feeds)
    assert "comed.prices" in out
    assert "✅" in out


def test_render_stale_feed_counted_for_anomaly():
    """Stale feed shows 🔴 status + can be counted via count_stale."""
    from tools.decision_trace_report.sections.feed_health import count_stale
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    feeds = [
        {
            "name": "nws.forecast",
            "kind": "continuous",
            "last_write": datetime(2026, 5, 16, 4, 0, tzinfo=timezone.utc),  # 4h ago
            "warn": timedelta(minutes=60),
            "stale": timedelta(hours=2),
        },
    ]
    out = render(now=now, feeds=feeds)
    assert "🔴" in out
    assert count_stale(now=now, feeds=feeds) == 1


def test_event_feed_uses_expected_next_fire_window():
    """Event feed (e.g., DA LMP at 17:00 CT daily) is stale only if
    last_write is older than the most-recent expected fire window
    plus a grace period."""
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)  # 03:00 CT on May 16
    feeds = [
        {
            "name": "pjm.lmp_da_hourly",
            "kind": "event",
            "last_write": datetime(2026, 5, 15, 22, 0, tzinfo=timezone.utc),
            # Last write = 17:00 CT May 15. Most recent expected fire
            # was 17:00 CT May 15. So fresh.
            "expected_fire_description": "17:00 CT daily",
            "last_expected_fire_utc": datetime(2026, 5, 15, 22, 0, tzinfo=timezone.utc),
            "grace": timedelta(hours=2),
        },
    ]
    out = render(now=now, feeds=feeds)
    assert "✅" in out


def test_missing_feed_renders_loudly_not_silently():
    """A feed with last_write=None (never seen) must NOT disappear from
    the report. Surface it as 'missing' and count it toward stale —
    silent-feed disappearance is exactly the kind of commissioning
    failure the report exists to catch."""
    from tools.decision_trace_report.sections.feed_health import count_stale
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    feeds = [
        {
            "name": "pjm.metered_load",
            "kind": "event",
            "last_write": None,  # never written / poller never ran
            "expected_fire_description": "Sunday 02:00 CT weekly",
            "last_expected_fire_utc": datetime(2026, 5, 11, 7, 0, tzinfo=timezone.utc),
            "grace": timedelta(hours=2),
        },
    ]
    out = render(now=now, feeds=feeds)
    # Feed name MUST appear — not silently dropped
    assert "pjm.metered_load" in out
    # Some visible "missing" indicator
    assert ("missing" in out.lower()
            or "never seen" in out.lower()
            or "🔴" in out)
    # Counts as stale for anomaly summary
    assert count_stale(now=now, feeds=feeds) == 1
