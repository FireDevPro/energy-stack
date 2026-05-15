"""Tests for §3 price spike reaction audit."""
from tools.decision_trace_report.sections.price_spikes import (
    count_unexplained,
    is_explained,
    render,
)


def test_explained_when_normal_tier_below_threshold():
    """A price under the elevated threshold with tier=normal IS
    explained (the controller is correctly inactive)."""
    spike = {"price_cents": 9.5, "_time": "2026-05-15T13:00:00Z"}
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
    spike = {"price_cents": 12.0, "_time": "2026-05-15T13:00:00Z"}
    trace = {
        "outcome": "upgraded",
        "reason_code": "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
        "prev_tier": "normal",
        "new_tier": "elevated",
    }
    assert is_explained(spike, trace) is True


def test_unexplained_when_spike_but_normal_tier_held():
    """Price >=10c with overlay HELD_IN_TIER at normal -> unexplained."""
    spike = {"price_cents": 15.0, "_time": "2026-05-15T13:00:00Z"}
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


def test_count_unexplained_aggregates():
    # Spike timestamps in CT (matching overlay event timezone) so the
    # _nearest_trace pairing is by wall-clock-second, not skewed by a
    # 5h UTC↔CT offset. Production spike `_time` arrives from InfluxDB
    # — the report tool normalizes timezones before passing here in
    # Phase 5. v1 tests keep them aligned for clarity.
    spikes = [
        {"price_cents": 15.0, "_time": "2026-05-15T13:00:00-05:00"},
        {"price_cents": 11.0, "_time": "2026-05-15T13:05:00-05:00"},
    ]
    overlay_events = [
        {"ts": "2026-05-15T13:00:01-05:00", "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
         "prev_tier": "normal", "new_tier": "normal", "outcome": "held"},
        {"ts": "2026-05-15T13:05:01-05:00", "reason_code": "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
         "prev_tier": "normal", "new_tier": "elevated", "outcome": "upgraded"},
    ]
    # First spike: held in normal -> unexplained. Second: upgrade -> explained.
    assert count_unexplained(spikes=spikes, overlay_events=overlay_events) == 1
