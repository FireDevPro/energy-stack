"""Tests for the Monday Arm A <-> Arm B transition logger (§5)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from log_arm_transition import build_transition_point


T_TYPICAL = datetime(2026, 6, 8, 5, 0, tzinfo=timezone.utc)  # Monday 00:00 CT in summer


def test_a_to_b_transition_marks_pi_active_and_air_off():
    """Entering Arm B: AIR off (Pi pushes setpoints exactly when fired),
    Pi dry_run false. Caller computes pi_dry_run; this row records what
    the caller asserted."""
    p = build_transition_point(
        from_arm="A",
        to_arm="B",
        air_setting="off",
        manual_or_auto="manual",
        pi_dry_run=False,
        timestamp_utc=T_TYPICAL,
    )
    line = p.to_line_protocol()
    assert line.startswith("hvac.arm_transitions")
    assert "from_arm=A" in line
    assert "to_arm=B" in line
    assert 'air_setting="off"' in line
    assert "pi_dry_run=false" in line


def test_b_to_a_transition_marks_pi_dryrun_and_air_on():
    """Entering Arm A: AIR on (thermostat learns recovery, matching
    consumer-grade behaviour), Pi dry_run true."""
    p = build_transition_point(
        from_arm="B",
        to_arm="A",
        air_setting="on",
        manual_or_auto="manual",
        pi_dry_run=True,
        timestamp_utc=T_TYPICAL,
    )
    line = p.to_line_protocol()
    assert "from_arm=B" in line
    assert "to_arm=A" in line
    assert 'air_setting="on"' in line
    assert "pi_dry_run=true" in line


def test_manual_or_auto_is_a_tag_for_filtering():
    """Tagged so dashboards can filter 'all auto transitions this season'
    once v2 (Control4 driver-driven AIR toggle) lands."""
    p = build_transition_point(
        from_arm="A", to_arm="B", air_setting="off",
        manual_or_auto="auto", pi_dry_run=False,
    )
    line = p.to_line_protocol()
    assert "manual_or_auto=auto" in line


def test_invalid_from_arm_rejected():
    with pytest.raises(ValueError, match="from_arm"):
        build_transition_point(
            from_arm="C", to_arm="A", air_setting="on",
            manual_or_auto="manual", pi_dry_run=True,
        )


def test_invalid_air_setting_rejected():
    with pytest.raises(ValueError, match="air_setting"):
        build_transition_point(
            from_arm="A", to_arm="B", air_setting="maybe",
            manual_or_auto="manual", pi_dry_run=False,
        )


def test_invalid_mode_rejected():
    with pytest.raises(ValueError, match="manual_or_auto"):
        build_transition_point(
            from_arm="A", to_arm="B", air_setting="off",
            manual_or_auto="scheduled", pi_dry_run=False,
        )
