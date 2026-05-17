"""Tests for tools.analysis.validity_gate per spec §5 / §7."""
from __future__ import annotations

import pytest

from tools.analysis.mode_classification import HourMode
from tools.analysis.validity_gate import (
    arm_passes_validity_gate,
    fully_valid_count,
    max_continuous_invalid_run,
)


def _interleave_valid_invalid(valid_n: int, invalid_n: int,
                              total: int = 288) -> list[HourMode]:
    """Distribute ``invalid_n`` invalid hours across the timeline so no
    contiguous invalid run exceeds 24 hours. Pads with A_ACTIVE."""
    assert valid_n + invalid_n == total
    modes = [HourMode.A_ACTIVE] * total
    if invalid_n == 0:
        return modes
    step = max(2, total // (invalid_n + 1))
    placed = 0
    idx = step
    while placed < invalid_n and idx < total:
        modes[idx] = HourMode.TELEMETRY_INVALID
        placed += 1
        idx += step
    # Top up by inserting at sparse positions if still short
    fallback_idx = 0
    while placed < invalid_n:
        if modes[fallback_idx] == HourMode.A_ACTIVE:
            modes[fallback_idx] = HourMode.TELEMETRY_INVALID
            placed += 1
        fallback_idx += 2
    return modes


def test_259_fully_valid_passes():
    modes = _interleave_valid_invalid(259, 29)
    assert arm_passes_validity_gate(modes) is True


def test_258_fully_valid_fails():
    """One under the threshold fails even when interleaved."""
    modes = _interleave_valid_invalid(258, 30)
    assert arm_passes_validity_gate(modes) is False


def test_continuous_invalid_run_over_24h_fails():
    """270 valid hours but 25 contiguous invalid -> fails contiguous-cap."""
    modes = (
        [HourMode.A_ACTIVE] * 100
        + [HourMode.TELEMETRY_INVALID] * 25
        + [HourMode.A_ACTIVE] * 163
    )
    assert arm_passes_validity_gate(modes) is False


def test_exactly_24h_continuous_invalid_still_passes():
    modes = (
        [HourMode.A_ACTIVE] * 100
        + [HourMode.TELEMETRY_INVALID] * 24
        + [HourMode.A_ACTIVE] * 164
    )
    assert max_continuous_invalid_run(modes) == 24
    assert arm_passes_validity_gate(modes) is True


def test_b_fallback_counts_as_invalid_for_gate():
    """B-fallback is NOT fully-valid even though telemetry is fine."""
    modes = [HourMode.B_ACTIVE] * 258 + [HourMode.B_FALLBACK] * 30
    assert arm_passes_validity_gate(modes) is False


def test_b_down_counts_as_invalid_for_gate():
    modes = [HourMode.B_ACTIVE] * 258 + [HourMode.B_DOWN] * 30
    assert arm_passes_validity_gate(modes) is False


def test_fully_valid_count_helper():
    modes = ([HourMode.A_ACTIVE] * 200
             + [HourMode.B_FALLBACK] * 50
             + [HourMode.TELEMETRY_INVALID] * 38)
    assert fully_valid_count(modes) == 200


def test_wrong_length_raises():
    with pytest.raises(ValueError):
        arm_passes_validity_gate([HourMode.A_ACTIVE] * 287)
