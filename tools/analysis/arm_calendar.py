"""Locked SCED arm calendar per docs/plans/sced-rebaseline-spec-2026-05-13.md §2.

12 arm periods, A/B alternating, 14 days each, Sun -> Mon 00:00 CT switches.
First arm = A on 2026-06-01, last arm ends 2026-11-16. 48h washout per arm,
288 post-washout hours (12 days x 24 hours) used for analysis aggregation.

Datetimes are CT-local naive (no tzinfo). Callers must convert UTC -> CT
before passing to ``current_arm_at`` / ``datetime_to_hour_index``.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class ArmPeriod:
    index: int  # 1..12
    arm: Literal["A", "B"]
    start_ct: datetime.datetime  # CT-local, naive, inclusive
    end_ct: datetime.datetime  # CT-local, naive, exclusive


WASHOUT_HOURS = 48
POST_WASHOUT_DAYS = 12
HOURS_PER_ARM = POST_WASHOUT_DAYS * 24  # 288
_FIRST_ARM_START_CT = datetime.datetime(2026, 6, 1, 0, 0)  # Monday 00:00 CT


def _build_calendar() -> tuple[ArmPeriod, ...]:
    return tuple(
        ArmPeriod(
            index=i + 1,
            arm="A" if i % 2 == 0 else "B",
            start_ct=_FIRST_ARM_START_CT + datetime.timedelta(days=14 * i),
            end_ct=_FIRST_ARM_START_CT + datetime.timedelta(days=14 * (i + 1)),
        )
        for i in range(12)
    )


ARM_CALENDAR: tuple[ArmPeriod, ...] = _build_calendar()


def current_arm_at(when_ct: datetime.datetime) -> Optional[Literal["A", "B"]]:
    """Return the active arm letter at CT-local ``when_ct``, or None if
    outside the locked experiment window (before 2026-06-01 or on/after
    2026-11-16). Boundary semantics: start inclusive, end exclusive.
    """
    for arm in ARM_CALENDAR:
        if arm.start_ct <= when_ct < arm.end_ct:
            return arm.arm
    return None


def post_washout_start(arm: ArmPeriod) -> datetime.datetime:
    """First post-washout hour for ``arm`` = arm.start_ct + 48h.
    For the locked Mon 00:00 CT calendar this is Wed 00:00 CT.
    """
    return arm.start_ct + datetime.timedelta(hours=WASHOUT_HOURS)


def hour_index_to_datetime(arm: ArmPeriod, k: int) -> datetime.datetime:
    """Hour-index k (0..287) -> CT-local datetime within ``arm``'s
    post-washout window. Raises ValueError if k is out of range.
    """
    if not 0 <= k < HOURS_PER_ARM:
        raise ValueError(f"hour_index {k} out of range [0, {HOURS_PER_ARM})")
    return post_washout_start(arm) + datetime.timedelta(hours=k)


def datetime_to_hour_index(
    arm: ArmPeriod, when_ct: datetime.datetime
) -> Optional[int]:
    """CT-local datetime -> hour-index within ``arm``'s post-washout
    window, or None if ``when_ct`` falls in the 48h washout, after the
    arm's end, or in some other arm.
    """
    start = post_washout_start(arm)
    if not start <= when_ct < arm.end_ct:
        return None
    delta_hours = int((when_ct - start).total_seconds() // 3600)
    if not 0 <= delta_hours < HOURS_PER_ARM:
        return None
    return delta_hours
