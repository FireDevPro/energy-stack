"""Tests for the thermostat poller's override-detection logic.

The override-detection state machine compares the current thermostat
snapshot against the last applied scheduler action. A divergence after
the grace period == user manually changed setpoints. The branches that
matter:

- Missing expected or actual setpoints → no override
- Within the grace period (action just fired, read-back not settled) → no override
- Setpoints match within 0.5°F (rounding noise) → no override
- Otherwise → override summary

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from poller import classify_override


# ---- classify_override ---------------------------------------------------


def _last(cool=70, heat=65, label="PRE_COOL", minutes_since=30.0):
    return {
        "cool_setpoint_f": cool,
        "heat_setpoint_f": heat,
        "action_label": label,
        "minutes_since_last_action": minutes_since,
    }


def _snap(cool=70, heat=65):
    return {
        "cool_setpoint_f": cool,
        "heat_setpoint_f": heat,
    }


def test_classify_no_override_when_setpoints_match():
    """Snapshot matches last action exactly — no override."""
    assert classify_override(_snap(70, 65), _last(70, 65), override_grace_min=5) is None


def test_classify_override_when_cool_setpoint_changed():
    """User dragged cool setpoint from 70 to 72 after the grace window."""
    summary = classify_override(_snap(72, 65), _last(70, 65), override_grace_min=5)
    assert summary is not None
    assert summary["expected_cool_f"] == 70
    assert summary["actual_cool_f"] == 72
    assert summary["delta_cool_f"] == 2.0
    assert summary["delta_heat_f"] == 0.0


def test_classify_override_when_heat_setpoint_changed():
    summary = classify_override(_snap(70, 68), _last(70, 65), override_grace_min=5)
    assert summary is not None
    assert summary["delta_cool_f"] == 0.0
    assert summary["delta_heat_f"] == 3.0


def test_classify_no_override_within_rounding():
    """Sub-half-degree differences are rounding noise, not an override.
    Tested against both directions."""
    # 0.4°F delta (under threshold)
    assert classify_override(_snap(70.4, 65), _last(70, 65),
                              override_grace_min=5) is None
    assert classify_override(_snap(70, 65.3), _last(70, 65),
                              override_grace_min=5) is None
    # Negative direction
    assert classify_override(_snap(69.7, 65), _last(70, 65),
                              override_grace_min=5) is None


def test_classify_override_at_exactly_half_degree():
    """0.5°F is the threshold (>= 0.5 fires, < 0.5 doesn't). Test the
    boundary explicitly."""
    # 0.5°F exact — fires
    summary = classify_override(_snap(70.5, 65), _last(70, 65), override_grace_min=5)
    assert summary is not None
    # 0.49°F — doesn't fire
    assert classify_override(_snap(70.49, 65), _last(70, 65),
                              override_grace_min=5) is None


def test_classify_no_override_within_grace_period():
    """An action just fired (1 min ago) — the read-back may show the new
    setpoints sub-second-stale. Don't flag as override before the read-back
    has had time to settle."""
    last = _last(70, 65, minutes_since=1.0)
    assert classify_override(_snap(75, 65), last, override_grace_min=5) is None


def test_classify_override_at_grace_boundary():
    """Grace boundary: 5 min exactly fires, 4.99 doesn't."""
    fires = classify_override(_snap(75, 65), _last(70, 65, minutes_since=5.0),
                                override_grace_min=5)
    assert fires is not None
    silent = classify_override(_snap(75, 65), _last(70, 65, minutes_since=4.99),
                                override_grace_min=5)
    assert silent is None


def test_classify_no_override_when_expected_setpoint_missing():
    """Last action row missing fields (e.g. a release_hold action that
    didn't carry setpoints) — no override possible."""
    last = {"cool_setpoint_f": None, "heat_setpoint_f": 65,
             "minutes_since_last_action": 30.0}
    assert classify_override(_snap(70, 65), last, override_grace_min=5) is None


def test_classify_no_override_when_actual_setpoint_missing():
    """Thermostat read failed for one setpoint — don't synthesize an
    override from incomplete data."""
    snap = {"cool_setpoint_f": 70, "heat_setpoint_f": None}
    assert classify_override(snap, _last(70, 65), override_grace_min=5) is None


def test_classify_no_override_when_minutes_since_unknown():
    """If we can't determine grace-period status, don't fire."""
    last = {**_last(70, 65), "minutes_since_last_action": None}
    assert classify_override(_snap(75, 65), last, override_grace_min=5) is None


def test_classify_override_carries_label_for_diagnostics():
    """The override row records which action got overridden — useful when
    the user is fighting one specific time-of-day setpoint."""
    last = _last(80, 65, label="HOT_5CP_SHUTOFF", minutes_since=30)
    summary = classify_override(_snap(75, 65), last, override_grace_min=5)
    assert summary is not None
    assert summary["last_action_label"] == "HOT_5CP_SHUTOFF"


def test_classify_override_with_negative_delta():
    """User dragged setpoint DOWN (more cooling). Negative delta is
    semantically "user wants colder than the action set"."""
    summary = classify_override(_snap(68, 65), _last(73, 65), override_grace_min=5)
    assert summary is not None
    assert summary["delta_cool_f"] == -5.0
