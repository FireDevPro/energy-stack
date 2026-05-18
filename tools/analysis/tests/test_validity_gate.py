"""Tests for tools.analysis.validity_gate per spec §5 / §7."""
from __future__ import annotations

import pytest

from tools.analysis.mode_classification import HourMode
from tools.analysis.validity_gate import (
    arm_passes_validity_gate,
    fully_valid_count,
    is_fully_valid_for_arm,
    max_continuous_invalid_run,
)


def _interleave_valid_invalid(
    valid_mode: HourMode, invalid_mode: HourMode,
    valid_n: int, invalid_n: int, total: int = 288,
) -> list[HourMode]:
    """Spread ``invalid_n`` invalid hours across the timeline so no
    contiguous invalid run exceeds 24 hours."""
    assert valid_n + invalid_n == total
    modes = [valid_mode] * total
    if invalid_n == 0:
        return modes
    step = max(2, total // (invalid_n + 1))
    placed = 0
    idx = step
    while placed < invalid_n and idx < total:
        modes[idx] = invalid_mode
        placed += 1
        idx += step
    fallback_idx = 0
    while placed < invalid_n:
        if modes[fallback_idx] == valid_mode:
            modes[fallback_idx] = invalid_mode
            placed += 1
        fallback_idx += 2
    return modes


def test_259_fully_valid_passes_for_arm_a():
    modes = _interleave_valid_invalid(
        HourMode.A_ACTIVE, HourMode.TELEMETRY_INVALID, 259, 29,
    )
    assert arm_passes_validity_gate(modes, arm="A") is True


def test_258_fully_valid_fails_for_arm_a():
    modes = _interleave_valid_invalid(
        HourMode.A_ACTIVE, HourMode.TELEMETRY_INVALID, 258, 30,
    )
    assert arm_passes_validity_gate(modes, arm="A") is False


def test_b_arm_populated_with_a_active_modes_fails_gate():
    """Defense-in-depth: a B arm whose modes were classified as if A-arm
    must NOT pass the gate. Spec §5 fully-valid is arm-specific."""
    all_a = [HourMode.A_ACTIVE] * 288
    assert arm_passes_validity_gate(all_a, arm="B") is False
    assert arm_passes_validity_gate(all_a, arm="A") is True


def test_a_arm_populated_with_b_active_modes_fails_gate():
    all_b = [HourMode.B_ACTIVE] * 288
    assert arm_passes_validity_gate(all_b, arm="A") is False
    assert arm_passes_validity_gate(all_b, arm="B") is True


def test_continuous_invalid_run_over_24h_fails():
    """270 valid hours but 25 contiguous invalid -> fails contiguous-cap."""
    modes = (
        [HourMode.A_ACTIVE] * 100
        + [HourMode.TELEMETRY_INVALID] * 25
        + [HourMode.A_ACTIVE] * 163
    )
    assert arm_passes_validity_gate(modes, arm="A") is False


def test_exactly_24h_continuous_invalid_still_passes():
    modes = (
        [HourMode.A_ACTIVE] * 100
        + [HourMode.TELEMETRY_INVALID] * 24
        + [HourMode.A_ACTIVE] * 164
    )
    assert max_continuous_invalid_run(modes, arm="A") == 24
    assert arm_passes_validity_gate(modes, arm="A") is True


def test_b_fallback_counts_as_invalid_for_gate():
    """B-fallback is NOT fully-valid even though telemetry is fine."""
    modes = _interleave_valid_invalid(
        HourMode.B_ACTIVE, HourMode.B_FALLBACK, 258, 30,
    )
    assert arm_passes_validity_gate(modes, arm="B") is False


def test_b_down_counts_as_invalid_for_gate():
    modes = _interleave_valid_invalid(
        HourMode.B_ACTIVE, HourMode.B_DOWN, 258, 30,
    )
    assert arm_passes_validity_gate(modes, arm="B") is False


def test_fully_valid_count_helper_is_arm_specific():
    modes = ([HourMode.A_ACTIVE] * 200
             + [HourMode.B_ACTIVE] * 50
             + [HourMode.TELEMETRY_INVALID] * 38)
    assert fully_valid_count(modes, arm="A") == 200
    assert fully_valid_count(modes, arm="B") == 50


def test_is_fully_valid_for_arm():
    assert is_fully_valid_for_arm(HourMode.A_ACTIVE, "A") is True
    assert is_fully_valid_for_arm(HourMode.A_ACTIVE, "B") is False
    assert is_fully_valid_for_arm(HourMode.B_ACTIVE, "B") is True
    assert is_fully_valid_for_arm(HourMode.B_ACTIVE, "A") is False
    assert is_fully_valid_for_arm(HourMode.B_FALLBACK, "B") is False
    assert is_fully_valid_for_arm(HourMode.TELEMETRY_INVALID, "A") is False


def test_wrong_length_raises():
    with pytest.raises(ValueError):
        arm_passes_validity_gate([HourMode.A_ACTIVE] * 287, arm="A")
