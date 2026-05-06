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


# ---------- block balance (the methodological reason for blocks of 2) ----------


def test_each_complete_block_has_one_a_and_one_b():
    """Block-of-2 design guarantees every consecutive pair contains one of each.
    This is the SCED 'alternating treatments' property and the reason this
    design beats sequential pre/post per Khabbazi 2025."""
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 30))
    # All but possibly the last assignment if odd count
    pairs_to_check = len(a) // 2
    for i in range(pairs_to_check):
        block_arms = {a[2 * i].arm, a[2 * i + 1].arm}
        assert block_arms == {"A", "B"}, f"Block {i} arms = {block_arms}"


def test_arm_counts_balanced_when_even_weeks():
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 28))
    # 2026-06-01 is a Monday; 2026-09-28 is a Monday. Mondays in [Jun 1, Sep 28] = 18.
    assert len(a) == 18
    n_a = sum(1 for x in a if x.arm == "A")
    n_b = sum(1 for x in a if x.arm == "B")
    assert n_a == n_b == 9


def test_arm_counts_within_one_when_odd_weeks():
    """Odd week count means the trailing single week is randomized
    independently; arm counts can differ by at most 1."""
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 10, 5))
    n_a = sum(1 for x in a if x.arm == "A")
    n_b = sum(1 for x in a if x.arm == "B")
    assert abs(n_a - n_b) <= 1


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


def test_pre_registered_seed_first_assignment_is_pinned():
    """If this fails, the seed-to-output mapping has drifted and the
    pre-registration is invalid. Update the snapshot ONLY in tandem with
    an OSF amendment and a corresponding update to EXPERIMENT_DESIGN.md."""
    a = generate_assignments(20260601, date(2026, 6, 1), date(2026, 9, 30))
    # Seed 20260601, window starting 2026-06-01: pin the first 4 assignments
    # so any algorithm change is loud.
    first_four = [(x.iso_week, x.monday_date.isoformat(), x.arm) for x in a[:4]]
    assert first_four == [
        ("2026-W23", "2026-06-01", "B"),
        ("2026-W24", "2026-06-08", "A"),
        ("2026-W25", "2026-06-15", "B"),
        ("2026-W26", "2026-06-22", "A"),
    ]
