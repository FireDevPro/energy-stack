"""Tests for the randomize_arms.py pre-registration assignment generator.

These tests pin the algorithm: changing any of them breaks the pre-registered
contract and requires an OSF amendment with explicit justification.
"""
from datetime import date
from pathlib import Path

import pytest

from randomize_arms import (
    WeekAssignment,
    _first_monday_on_or_after,
    _format_iso_week,
    generate_assignments,
    write_csv,
)


# ---------- helpers ----------


def test_first_monday_on_a_monday_returns_same_date():
    assert _first_monday_on_or_after(date(2026, 6, 1)) == date(2026, 6, 1)  # Mon


def test_first_monday_after_a_friday_returns_next_monday():
    assert _first_monday_on_or_after(date(2026, 6, 5)) == date(2026, 6, 8)  # Fri -> Mon


def test_first_monday_after_sunday_returns_next_monday():
    assert _first_monday_on_or_after(date(2026, 6, 7)) == date(2026, 6, 8)  # Sun -> Mon


def test_iso_week_format():
    assert _format_iso_week(date(2026, 6, 1)) == "2026-W23"
    assert _format_iso_week(date(2026, 1, 5)) == "2026-W02"


# ---------- determinism: the binding contract ----------


def test_same_seed_same_assignments():
    """Pre-registration validity depends on this. Changing the seed-to-assignment
    mapping breaks the binding artifact."""
    a1 = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 30))
    a2 = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 30))
    assert a1 == a2


def test_different_seed_different_assignments():
    a1 = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 30))
    a2 = generate_assignments(20260602, date(2026, 6, 1), date(2026, 9, 30))
    assert a1 != a2


# ---------- block balance (the methodological reason for 4-week blocks) ----------


def test_each_complete_block_has_two_a_and_two_b():
    """Block-of-4 design guarantees every complete 4-week block contains
    exactly 2 Arm A weeks and 2 Arm B weeks (in either AABB or BBAA
    order). This is the SCED 'alternating treatments' property at the
    arm-period unit (2 weeks) which gives 12 analyzable days per
    arm-period after the 48h post-switch washout."""
    a = generate_assignments(20260601, date(2026, 6, 1), date(2027, 5, 31))
    blocks_to_check = len(a) // 4
    for i in range(blocks_to_check):
        block_arms = [x.arm for x in a[4 * i : 4 * i + 4]]
        n_a_in_block = block_arms.count("A")
        n_b_in_block = block_arms.count("B")
        assert n_a_in_block == 2 and n_b_in_block == 2, (
            f"Block {i} arms = {block_arms}"
        )


def test_each_complete_block_runs_aabb_or_bbaa():
    """The 2 Arm A weeks must be CONSECUTIVE inside each block (and same
    for Arm B). Either AABB or BBAA; never ABAB or BABA. The 2-week-arm
    unit is what gives the 12-analyzable-days property."""
    a = generate_assignments(20260601, date(2026, 6, 1), date(2027, 5, 31))
    blocks_to_check = len(a) // 4
    for i in range(blocks_to_check):
        block_arms = tuple(x.arm for x in a[4 * i : 4 * i + 4])
        assert block_arms in (("A", "A", "B", "B"), ("B", "B", "A", "A")), (
            f"Block {i} pattern = {block_arms}"
        )


def test_arm_counts_balanced_when_4_aligned_weeks():
    """48 weeks (12 complete 4-week blocks) -> exactly 24 A and 24 B."""
    a = generate_assignments(20260601, date(2026, 6, 1), date(2027, 4, 26))
    # 2026-06-01 to 2027-04-26 inclusive = 48 Mondays
    assert len(a) == 48
    n_a = sum(1 for x in a if x.arm == "A")
    n_b = sum(1 for x in a if x.arm == "B")
    assert n_a == n_b == 24


def test_arm_counts_within_three_when_partial_block():
    """Partial 4-week block at the tail: trailing 1-3 weeks are
    randomized independently so arm counts can differ by up to that
    many. Across 17 weeks (4 complete blocks + 1 trailing), max
    imbalance is 1."""
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 28))
    # 2026-06-01 -> 2026-09-28 = 18 Mondays = 4 blocks + 2 trailing
    assert len(a) == 18
    n_a = sum(1 for x in a if x.arm == "A")
    n_b = sum(1 for x in a if x.arm == "B")
    # 4 complete blocks contribute 8 A + 8 B. Trailing 2 each ind. random.
    assert abs(n_a - n_b) <= 2


# ---------- coverage of the date range ----------


def test_first_monday_at_or_after_start():
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 30))
    assert a[0].monday_date == date(2026, 6, 1)


def test_handles_start_on_non_monday():
    # 2026-06-03 is a Wednesday; first Monday in window is 2026-06-08.
    a = generate_assignments(20260601, date(2026, 6, 3), date(2026, 9, 30))
    assert a[0].monday_date == date(2026, 6, 8)


def test_no_monday_after_end():
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 30))
    assert a[-1].monday_date <= date(2026, 9, 30)


def test_all_weeks_one_apart():
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 30))
    for prev, curr in zip(a, a[1:]):
        assert (curr.monday_date - prev.monday_date).days == 7


# ---------- empty / edge cases ----------


def test_empty_range_returns_empty():
    assert generate_assignments(20260601, date(2026, 6, 5), date(2026, 6, 6)) == []


def test_single_week_range():
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 6, 7))
    assert len(a) == 1
    assert a[0].arm in {"A", "B"}


# ---------- CSV write ----------


def test_csv_write_round_trip(tmp_path):
    out = tmp_path / "assignments.csv"
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 6, 28))
    write_csv(a, out)
    text = out.read_text(encoding="utf-8")
    lines = text.strip().splitlines()
    assert lines[0] == "iso_week,monday_date,arm"
    assert len(lines) == len(a) + 1


def test_csv_creates_parent_dirs(tmp_path):
    out = tmp_path / "deep" / "nested" / "assignments.csv"
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 6, 7))
    write_csv(a, out)
    assert out.exists()


# ---------- the actual pre-registered seed: snapshot the first row ----------


def test_pre_registered_seed_first_block_is_pinned():
    """Pinning the first 4-week block under seed 20260601. If this fails,
    the seed-to-output mapping has drifted and the pre-registration is
    invalid. Update the snapshot ONLY in tandem with an OSF amendment and
    a corresponding update to EXPERIMENT_DESIGN.md."""
    a = generate_assignments(20260601, date(2026, 6, 1), date(2027, 5, 31))
    first_four = [(x.iso_week, x.monday_date.isoformat(), x.arm) for x in a[:4]]
    # Seed 20260601 selects AABB for the first 4-week block. Updated
    # from pre-§11 1-week-block ABAB pattern; the algorithm switched to
    # 4-week blocks per EXPERIMENT_DESIGN §5 before OSF filing.
    assert first_four == [
        ("2026-W23", "2026-06-01", "A"),
        ("2026-W24", "2026-06-08", "A"),
        ("2026-W25", "2026-06-15", "B"),
        ("2026-W26", "2026-06-22", "B"),
    ]
