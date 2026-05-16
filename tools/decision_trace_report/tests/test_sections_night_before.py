"""Tests for §1 night-before decision audit."""
from tools.decision_trace_report.sections.night_before import (
    count_discrepancies,
    render,
)


def test_renders_winning_day_type_and_evaluation_tape():
    """Happy path: section renders the day-type winner + tape."""
    day_type_events = [
        {
            "msg": "decision_trace.day_type_decision",
            "ts": "2026-05-14T21:00:00-05:00",
            "decision_for_date": "2026-05-15",
            "winning_day_type": "NORMAL",
            "winning_reason": "high_75_to_84",
            "evaluation_tape": [
                {"rule": "high_ge_hot", "threshold": 85, "actual": 80,
                 "fired": False, "reason_code": "DAY_TYPE_HOT_HIGH_GE_85"},
                {"rule": "high_ge_normal", "threshold": 75, "actual": 80,
                 "fired": True, "reason_code": "DAY_TYPE_NORMAL_HIGH_75_TO_84"},
            ],
            "high_f": 80.0,
            "apparent_max_f": 82.0,
        },
    ]
    precool_events = [
        {
            "msg": "decision_trace.precool_decision",
            "decision_for_date": "2026-05-15",
            "selected": False,
            "reason_code": "PRECOOL_REJECTED_NO_CHEAP_WINDOW",
        },
    ]
    hvac_decisions = [
        {"decision_for_date": "2026-05-15", "day_type": "NORMAL"},
    ]
    hvac_precool_window = None

    out = render(
        target_date="2026-05-15",
        day_type_events=day_type_events,
        precool_events=precool_events,
        hvac_decisions=hvac_decisions,
        hvac_precool_window=hvac_precool_window,
    )

    assert "NORMAL" in out
    assert "DAY_TYPE_HOT_HIGH_GE_85" in out
    assert "DAY_TYPE_NORMAL_HIGH_75_TO_84" in out
    assert "PRECOOL_REJECTED_NO_CHEAP_WINDOW" in out
    # Section heading present
    assert "§1" in out or "§1 Night-before" in out


def test_flags_trace_vs_influx_disagreement():
    """If the trace says day_type=NORMAL but the hvac.decisions row says
    day_type=HOT, that's an anomaly."""
    day_type_events = [
        {
            "msg": "decision_trace.day_type_decision",
            "decision_for_date": "2026-05-15",
            "winning_day_type": "NORMAL",
            "winning_reason": "high_75_to_84",
            "evaluation_tape": [],
            "high_f": 80.0,
            "apparent_max_f": 82.0,
        },
    ]
    hvac_decisions = [
        {"decision_for_date": "2026-05-15", "day_type": "HOT_5CP_RISK"},
    ]

    out = render(
        target_date="2026-05-15",
        day_type_events=day_type_events,
        precool_events=[],
        hvac_decisions=hvac_decisions,
        hvac_precool_window=None,
    )
    assert "disagree" in out.lower() or "mismatch" in out.lower()
    assert count_discrepancies(
        day_type_events=day_type_events,
        precool_events=[],
        hvac_decisions=hvac_decisions,
        hvac_precool_window=None,
    ) == 1


def test_no_day_type_events_renders_gracefully():
    """No 21:00 decision trace found -> note it in the section, don't
    crash. Counts as a discrepancy if Influx has the row."""
    hvac_decisions = [
        {"decision_for_date": "2026-05-15", "day_type": "MILD"},
    ]
    out = render(
        target_date="2026-05-15",
        day_type_events=[],
        precool_events=[],
        hvac_decisions=hvac_decisions,
        hvac_precool_window=None,
    )
    assert "no decision_trace.day_type_decision" in out.lower()


def test_missing_trace_with_influx_row_counts_as_discrepancy():
    """Codex P1 #2 regression: when the trace is missing but Influx has
    the row, that's a real anomaly. The §1 body warns about it but the
    pre-fix `count_discrepancies` returned 0 — heartbeat went "all
    green" while the body said missing trace.
    """
    hvac_decisions = [
        {"decision_for_date": "2026-05-15", "day_type": "NORMAL"},
    ]
    assert count_discrepancies(
        day_type_events=[],
        precool_events=[],
        hvac_decisions=hvac_decisions,
        hvac_precool_window=None,
    ) == 1


def test_missing_precool_trace_with_influx_row_counts_as_discrepancy():
    """Same anomaly class for precool: missing trace + present
    hvac.precool_window row -> count = 1."""
    assert count_discrepancies(
        day_type_events=[
            {"winning_day_type": "NORMAL"},  # day_type side green
        ],
        precool_events=[],  # but no precool trace
        hvac_decisions=[{"day_type": "NORMAL"}],
        hvac_precool_window={"hour_ct": 14, "depth_f": 2.0},
    ) == 1


def test_both_sides_missing_no_discrepancy():
    """No trace AND no Influx row -> not a discrepancy. There's nothing
    to disagree about. Other checks (feed health, coverage) handle the
    "scheduler never ran" case."""
    assert count_discrepancies(
        day_type_events=[],
        precool_events=[],
        hvac_decisions=[],
        hvac_precool_window=None,
    ) == 0
