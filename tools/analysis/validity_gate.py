"""Single pre-matching arm-period validity gate per
docs/plans/sced-rebaseline-spec-2026-05-13.md §5 / §7.

Rules:
- >=259 of 288 fully-valid hours (= >=90%)
- No contiguous invalid run > 24 hours

Fully-valid hour = A_ACTIVE (for Arm A) or B_ACTIVE (for Arm B), per
``mode_classification.is_fully_valid``. B_FALLBACK, B_DOWN, and
TELEMETRY_INVALID all count as invalid for the gate.
"""
from __future__ import annotations

from collections.abc import Sequence

from tools.analysis.mode_classification import HourMode, is_fully_valid


MIN_FULLY_VALID_HOURS = 259
MAX_CONTINUOUS_INVALID_HOURS = 24
EXPECTED_HOURS_PER_ARM = 288


def fully_valid_count(modes: Sequence[HourMode]) -> int:
    return sum(1 for m in modes if is_fully_valid(m))


def max_continuous_invalid_run(modes: Sequence[HourMode]) -> int:
    """Longest contiguous run of NOT-fully-valid hours."""
    longest = 0
    current = 0
    for m in modes:
        if is_fully_valid(m):
            current = 0
        else:
            current += 1
            if current > longest:
                longest = current
    return longest


def arm_passes_validity_gate(modes: Sequence[HourMode]) -> bool:
    """True iff the arm passes BOTH spec §5/§7 rules."""
    if len(modes) != EXPECTED_HOURS_PER_ARM:
        raise ValueError(
            f"expected {EXPECTED_HOURS_PER_ARM} modes, got {len(modes)}"
        )
    if fully_valid_count(modes) < MIN_FULLY_VALID_HOURS:
        return False
    if max_continuous_invalid_run(modes) > MAX_CONTINUOUS_INVALID_HOURS:
        return False
    return True
