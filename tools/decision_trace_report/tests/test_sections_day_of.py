"""Tests for §2 live day-of decision audit."""
from tools.decision_trace_report.sections.day_of import (
    count_action_fire_mismatches,
    count_supervisor_non_approved,
    render,
)


def test_chronological_timeline_grouped_by_tick_id():
    """Events sharing tick_id render as a group, chronologically ordered."""
    layer_events = [
        {
            "msg": "decision_trace.layer_resolution",
            "ts": "2026-05-15T13:00:01-05:00",
            "tick_id": "tick_aaaa1234567890",
            "winning_layer": "schedule",
            "schedule_cool_f": 79,
            "price_cool_f": 79,
            "fivecp_cool_f": 79,
            "effective_cool_f": 79,
        },
    ]
    supervisor_events = [
        {
            "msg": "decision_trace.supervisor",
            "ts": "2026-05-15T13:00:01-05:00",
            "tick_id": "tick_aaaa1234567890",
            "decision": "approved",
            "reason_code": "SUPERVISOR_APPROVED",
        },
    ]
    out = render(
        target_date="2026-05-15",
        layer_events=layer_events,
        supervisor_events=supervisor_events,
        hvac_actions=[],
    )
    assert "tick_aaaa" in out
    assert "schedule" in out
    assert "SUPERVISOR_APPROVED" in out


def test_counts_supervisor_non_approved():
    layer_events = []
    supervisor_events = [
        {"decision": "approved", "tick_id": "a", "reason_code": "SUPERVISOR_APPROVED",
         "ts": "2026-05-15T12:00:00-05:00"},
        {"decision": "clamped", "tick_id": "b",
         "reason_code": "SUPERVISOR_CLAMPED_COOL_FLOOR",
         "ts": "2026-05-15T13:00:00-05:00"},
        {"decision": "emergency", "tick_id": "c",
         "reason_code": "SUPERVISOR_EMERGENCY_OVERHEAT",
         "ts": "2026-05-15T14:00:00-05:00"},
    ]
    assert count_supervisor_non_approved(supervisor_events) == 2


def test_action_fire_reconciliation_flags_missing_influx_row():
    """An action-fire trace (non-MID_PERIOD_REPUSH action_label) must
    have a matching hvac.actions row within +/- 2 minutes of the trace
    ts. Missing -> anomaly."""
    from datetime import datetime, timezone
    layer_events = [
        {
            "msg": "decision_trace.layer_resolution",
            "tick_id": "tick_x",
            "ts": "2026-05-15T18:00:00+00:00",
            "action_label": "COAST",      # action-fire, NOT MID_PERIOD_REPUSH
            "effective_cool_f": 78,
        },
    ]
    hvac_actions = []  # no row -> mismatch
    assert count_action_fire_mismatches(layer_events=layer_events,
                                          hvac_actions=hvac_actions) == 1


def test_action_fire_matches_only_by_label_AND_nearby_timestamp():
    """Reconciliation must match by (action_label, time-window). A
    COAST hvac.actions row at 13:00 should NOT satisfy a COAST trace
    event at 22:00 — one Influx row can't cover two separate firings
    of the same label on the same day."""
    from datetime import datetime, timezone
    layer_events = [
        {
            "msg": "decision_trace.layer_resolution",
            "tick_id": "tick_a",
            "ts": "2026-05-15T18:00:00+00:00",  # 13:00 CT
            "action_label": "COAST",
            "effective_cool_f": 78,
        },
        {
            "msg": "decision_trace.layer_resolution",
            "tick_id": "tick_b",
            "ts": "2026-05-16T03:00:00+00:00",  # 22:00 CT — second firing
            "action_label": "COAST",
            "effective_cool_f": 79,
        },
    ]
    hvac_actions = [
        {
            "action_label": "COAST",
            "_time": datetime(2026, 5, 15, 18, 0, 30, tzinfo=timezone.utc),  # matches tick_a
        },
        # No row for tick_b's 22:00 firing -> 1 mismatch expected
    ]
    assert count_action_fire_mismatches(layer_events=layer_events,
                                          hvac_actions=hvac_actions) == 1


def test_action_fire_matches_within_two_minute_window():
    """Match window is +/- 2 minutes to allow for tick-fire vs Influx-
    write latency (Pi clock skew, write batching, etc.)."""
    from datetime import datetime, timezone
    layer_events = [
        {
            "tick_id": "tick_x",
            "ts": "2026-05-15T18:00:00+00:00",
            "action_label": "COAST",
            "effective_cool_f": 78,
            "msg": "decision_trace.layer_resolution",
        },
    ]
    hvac_actions = [
        {
            "action_label": "COAST",
            "_time": datetime(2026, 5, 15, 18, 1, 30, tzinfo=timezone.utc),  # 90s later
        },
    ]
    assert count_action_fire_mismatches(layer_events=layer_events,
                                          hvac_actions=hvac_actions) == 0


def test_mid_period_repush_does_not_count_as_mismatch():
    """Mid-period repush traces without a matching action are NORMAL
    (most ticks emit trace + supervisor but write no row). Only
    action-fire labels are reconciled."""
    layer_events = [
        {
            "msg": "decision_trace.layer_resolution",
            "tick_id": "tick_y",
            "ts": "2026-05-15T13:00:00-05:00",
            "action_label": "MID_PERIOD_REPUSH:COAST",
            "effective_cool_f": 78,
        },
    ]
    hvac_actions = []
    assert count_action_fire_mismatches(layer_events=layer_events,
                                          hvac_actions=hvac_actions) == 0
