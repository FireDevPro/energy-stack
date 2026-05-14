"""Hash-sync check: tools/analysis/arm_calendar.py and the
deploy/energy-stack/hvac-scheduler/arm_calendar.py copy MUST be
byte-identical.

The hvac-scheduler container does not have tools/analysis on its
PYTHONPATH (per the existing service-isolation pattern). The
arm-calendar logic is duplicated locally so the controller can read
the locked calendar at runtime without cross-service imports. To keep
the duplicates in sync, this test asserts the SHA-256 hashes match.

If you legitimately edit one, edit both; this test is the safety net.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_COPY = REPO_ROOT / "tools" / "analysis" / "arm_calendar.py"
SCHEDULER_COPY = REPO_ROOT / "deploy" / "energy-stack" / "hvac-scheduler" / "arm_calendar.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_arm_calendar_copies_are_byte_identical():
    assert ANALYSIS_COPY.exists(), f"missing canonical copy: {ANALYSIS_COPY}"
    assert SCHEDULER_COPY.exists(), f"missing scheduler copy: {SCHEDULER_COPY}"
    a = _sha256(ANALYSIS_COPY)
    b = _sha256(SCHEDULER_COPY)
    assert a == b, (
        f"arm_calendar.py copies are out of sync.\n"
        f"  {ANALYSIS_COPY} -> {a}\n"
        f"  {SCHEDULER_COPY} -> {b}\n"
        "Edit both files identically; this hash check is the safety net."
    )
