"""Tests for §3 price spike reaction audit.

Spike fixtures use the post-normalization shape that
`InfluxClient.fetch_comed_prices_above*` returns in production:
    {"_time": tz-aware datetime, "price_cents": float}
This is the regression guard for the Codex P1 row-shape bug. The
client-side contract is locked by
`test_fetch_comed_prices_normalizes_value_to_price_cents` in
`test_influx_client.py`.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.decision_trace_report.sections.price_spikes import (
    count_unexplained,
    is_explained,
    render,
)

CT = ZoneInfo("America/Chicago")


def test_explained_when_normal_tier_below_threshold():
    """A price under the elevated threshold with tier=normal IS
    explained (the controller is correctly inactive)."""
    spike = {"price_cents": 9.5, "_time": datetime(2026, 5, 15, 13, 0, tzinfo=CT)}
    trace = {
        "ts": "2026-05-15T13:00:01-05:00",
        "outcome": "held",
        "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
        "prev_tier": "normal",
        "new_tier": "normal",
    }
    assert is_explained(spike, trace) is True


def test_explained_when_elevated_upgrade_after_spike():
    """A spike >=10c followed by an UPGRADED_TO_ELEVATED is explained."""
    spike = {"price_cents": 12.0, "_time": datetime(2026, 5, 15, 13, 0, tzinfo=CT)}
    trace = {
        "outcome": "upgraded",
        "reason_code": "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
        "prev_tier": "normal",
        "new_tier": "elevated",
    }
    assert is_explained(spike, trace) is True


def test_unexplained_when_spike_but_normal_tier_held():
    """Price >=10c with overlay HELD_IN_TIER at normal -> unexplained."""
    spike = {"price_cents": 15.0, "_time": datetime(2026, 5, 15, 13, 0, tzinfo=CT)}
    trace = {
        "outcome": "held",
        "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
        "prev_tier": "normal",
        "new_tier": "normal",
    }
    assert is_explained(spike, trace) is False


def test_render_no_spikes_today():
    """Empty spike list -> 'No spikes today' message."""
    out = render(target_date="2026-05-15", spikes=[], overlay_events=[])
    assert "no spikes today" in out.lower() or "no spikes" in out.lower()


def test_render_accepts_datetime_time_field():
    """§3 must accept `_time` as a tz-aware datetime (real shape post-
    normalization by InfluxClient). String `_time` was the fixture lie
    that hid the Codex P1 wire-up bug."""
    spikes = [
        {"price_cents": 12.0, "_time": datetime(2026, 5, 15, 13, 0, tzinfo=CT)},
    ]
    overlay_events = [
        {"ts": "2026-05-15T13:00:01-05:00",
         "reason_code": "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
         "prev_tier": "normal", "new_tier": "elevated", "outcome": "upgraded"},
    ]
    out = render(target_date="2026-05-15", spikes=spikes, overlay_events=overlay_events)
    # Datetime renders as ISO; price renders with 2 decimals
    assert "12.00" in out
    assert "2026-05-15T13:00:00" in out


def test_renders_microsecond_trace_ts_cleanly():
    """Live-verification regression: trace `ts` carries microseconds
    in production ('2026-05-15T13:00:08.452837+00:00'). Pre-fix
    `trace['ts'][-14:]` rendered garbage like '8.452837+00:00'.
    Render proper HH:MM:SS-offset in CT."""
    spikes = [
        {"price_cents": 15.0, "_time": datetime(2026, 5, 15, 13, 0, tzinfo=CT)},
    ]
    overlay_events = [
        {"ts": "2026-05-15T18:00:09.452837+00:00",  # UTC microsecond ts
         "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
         "prev_tier": "normal", "new_tier": "normal", "outcome": "held"},
    ]
    out = render(target_date="2026-05-15", spikes=spikes, overlay_events=overlay_events)
    # UTC 18:00:09 → CDT 13:00:09 (May, UTC-5)
    assert "13:00:09-05:00" in out
    assert "9.452837" not in out
    assert "452837+00:00" not in out


def test_count_unexplained_aggregates():
    spikes = [
        {"price_cents": 15.0, "_time": datetime(2026, 5, 15, 13, 0, tzinfo=CT)},
        {"price_cents": 11.0, "_time": datetime(2026, 5, 15, 13, 5, tzinfo=CT)},
    ]
    overlay_events = [
        {"ts": "2026-05-15T13:00:01-05:00", "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
         "prev_tier": "normal", "new_tier": "normal", "outcome": "held"},
        {"ts": "2026-05-15T13:05:01-05:00", "reason_code": "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
         "prev_tier": "normal", "new_tier": "elevated", "outcome": "upgraded"},
    ]
    # First spike: held in normal -> unexplained. Second: upgrade -> explained.
    assert count_unexplained(spikes=spikes, overlay_events=overlay_events) == 1
