"""Single pre-matching arm-period validity gate per
docs/plans/sced-rebaseline-spec-2026-05-13.md §5 / §7.

Rules:
- >=259 of 288 fully-valid hours (= >=90%)
- No contiguous invalid run > 24 hours

Per spec §5, a fully-valid hour is arm-specific: A-active for Arm A,
B-active for Arm B. The gate functions take the arm letter explicitly so
that a B arm accidentally populated with A_ACTIVE modes (or vice versa)
cannot pass. ``B_FALLBACK``, ``B_DOWN``, and ``TELEMETRY_INVALID`` are
never fully-valid for either arm.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from tools.analysis.mode_classification import HourMode


MIN_FULLY_VALID_HOURS = 259
MAX_CONTINUOUS_INVALID_HOURS = 24
EXPECTED_HOURS_PER_ARM = 288


def is_fully_valid_for_arm(mode: HourMode, arm: Literal["A", "B"]) -> bool:
    """Arm-specific fully-valid check per spec §5."""
    if arm == "A":
        return mode == HourMode.A_ACTIVE
    return mode == HourMode.B_ACTIVE


def fully_valid_count(modes: Sequence[HourMode], arm: Literal["A", "B"]) -> int:
    return sum(1 for m in modes if is_fully_valid_for_arm(m, arm))


def max_continuous_invalid_run(
    modes: Sequence[HourMode], arm: Literal["A", "B"],
) -> int:
    """Longest contiguous run of NOT-fully-valid-for-this-arm hours."""
    longest = 0
    current = 0
    for m in modes:
        if is_fully_valid_for_arm(m, arm):
            current = 0
        else:
            current += 1
            if current > longest:
                longest = current
    return longest


def arm_passes_validity_gate(
    modes: Sequence[HourMode], arm: Literal["A", "B"],
) -> bool:
    """True iff the arm passes BOTH spec §5/§7 rules for ``arm``."""
    if len(modes) != EXPECTED_HOURS_PER_ARM:
        raise ValueError(
            f"expected {EXPECTED_HOURS_PER_ARM} modes, got {len(modes)}"
        )
    if fully_valid_count(modes, arm) < MIN_FULLY_VALID_HOURS:
        return False
    if max_continuous_invalid_run(modes, arm) > MAX_CONTINUOUS_INVALID_HOURS:
        return False
    return True
