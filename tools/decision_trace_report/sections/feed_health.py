"""§4 feed + telemetry health.

Continuous feeds (ComEd 5-min prices, NWS forecast, Refoss, etc.)
use simple age thresholds — `warn` = first concern, `stale` = real
problem.

Event feeds (PJM DA LMP daily, weekly metered load) are stale only
if last_write predates the most-recent expected fire window beyond
the grace period; using simple age would falsely flag them every
hour they haven't fired.
"""
from datetime import datetime, timedelta
from typing import Literal


Status = Literal["fresh", "warn", "stale"]


def classify_age(age: timedelta, *, warn: timedelta, stale: timedelta) -> Status:
    if age < warn:
        return "fresh"
    if age < stale:
        return "warn"
    return "stale"


def _classify_feed(now: datetime, feed: dict) -> tuple[Status, str]:
    """Return (status, age_or_freshness_label) for one feed dict.

    A missing feed (last_write=None) is ALWAYS stale — surfaced loudly.
    The report is a commissioning monitor; a poller that never wrote
    is exactly the failure we must catch, not silently filter."""
    if feed.get("last_write") is None:
        return "stale", "missing — no data found in Influx"
    if feed["kind"] == "continuous":
        age = now - feed["last_write"]
        status = classify_age(age, warn=feed["warn"], stale=feed["stale"])
        return status, _format_timedelta(age)
    if feed["kind"] == "event":
        expected = feed["last_expected_fire_utc"]
        grace = feed.get("grace", timedelta(hours=2))
        if feed["last_write"] >= expected:
            return "fresh", f"caught up through {expected.isoformat()}"
        if now - expected < grace:
            return "warn", f"missed expected fire at {expected.isoformat()}"
        return "stale", f"missed expected fire at {expected.isoformat()} (grace exceeded)"
    raise ValueError(f"unknown feed kind: {feed['kind']!r}")


def _format_timedelta(delta: timedelta) -> str:
    total_s = int(delta.total_seconds())
    if total_s < 60:
        return f"{total_s}s"
    if total_s < 3600:
        return f"{total_s // 60}m"
    return f"{total_s // 3600}h{(total_s % 3600) // 60}m"


def render(*, now: datetime, feeds: list[dict]) -> str:
    lines: list[str] = ["## §4 Feed + telemetry health", ""]
    lines.append("| Feed | Kind | Last write | Age / status | Verdict |")
    lines.append("|---|---|---|---|---|")
    for feed in feeds:
        status, label = _classify_feed(now, feed)
        icon = {"fresh": "✅", "warn": "⚠️", "stale": "🔴"}[status]
        last = feed["last_write"].isoformat() if feed["last_write"] else "—"
        lines.append(
            f"| `{feed['name']}` | {feed['kind']} | {last} | {label} | {icon} {status} |"
        )
    lines.append("")
    return "\n".join(lines)


def count_stale(*, now: datetime, feeds: list[dict]) -> int:
    return sum(1 for feed in feeds if _classify_feed(now, feed)[0] == "stale")
