"""Locked SCED arm calendar per docs/plans/sced-rebaseline-spec-2026-05-13.md §2.

12 arm periods, A/B alternating, 14 days each, Sun -> Mon 00:00 CT switches.
First arm = A on 2026-06-01, last arm ends 2026-11-16. 48h washout per arm,
288 ELAPSED UTC hours per arm (12 days x 24 hours) used for analysis
aggregation. Arms 1-10 and 12 don't cross DST so 288 elapsed UTC hours
equals 288 wall-clock hours; arm 11 crosses 2026-11-01 02:00 CDT -> 01:00
CST so 288 UTC hours equals 289 wall-clock hours and the extra wall-clock
hour (11-01 23:00 CST) is the planned DST equalization exclusion (spec §2).

The hour-index <-> datetime helpers therefore do UTC arithmetic via
``zoneinfo``, not naive CT arithmetic, so arm 11 hour-index 287 correctly
maps to 2026-11-01 22:00 CST (NOT 23:00).

Datetimes are CT-local naive (no tzinfo). Callers must convert UTC -> CT
before passing to ``current_arm_at`` / ``datetime_to_hour_index``.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal, Optional
from zoneinfo import ZoneInfo

_CT = ZoneInfo("America/Chicago")


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

    Uses UTC arithmetic so arm 11 (DST fall-back at 2026-11-01) maps
    hour-index 287 to 2026-11-01 22:00 CST per spec §2 (not 23:00,
    which is the DST-equalization-excluded hour).

    **DST fold caveat (Phase 3 caller contract).** Two distinct
    hour-indices in arm 11 — k=265 (2026-11-01 01:00 CDT) and k=266
    (2026-11-01 01:00 CST) — return naive datetimes that compare
    equal and hash equal. The Python ``fold`` bit (0 vs 1) is the
    sole discriminator. ``str(dt)``, dict/set keying, and JSON
    serialization all collide. Phase 3 joins MUST key on hour-index
    or UTC instants, NEVER on the naive CT datetime returned here.
    Stripping the tzinfo for display is fine; using the naive value
    as a join key is a silent data loss bug.
    """
    if not 0 <= k < HOURS_PER_ARM:
        raise ValueError(f"hour_index {k} out of range [0, {HOURS_PER_ARM})")
    start_aware = post_washout_start(arm).replace(tzinfo=_CT)
    end_utc = start_aware.astimezone(datetime.timezone.utc) + datetime.timedelta(hours=k)
    return end_utc.astimezone(_CT).replace(tzinfo=None)


def datetime_to_hour_index(
    arm: ArmPeriod, when_ct: datetime.datetime
) -> Optional[int]:
    """CT-local datetime -> hour-index within ``arm``'s post-washout
    window, or None if ``when_ct`` falls in the 48h washout, after the
    arm's end, or in some other arm.

    For arm 11 the DST-equalization-excluded wall-clock hour
    (2026-11-01 23:00 CST) returns None — it falls in the 1-hour gap
    between hour-index 287 (22:00 CST) and arm 12's start (11-02 00:00
    CST) and is intentionally not part of the analysis index.
    """
    start_naive = post_washout_start(arm)
    if not start_naive <= when_ct < arm.end_ct:
        return None
    start_utc = start_naive.replace(tzinfo=_CT).astimezone(datetime.timezone.utc)
    when_utc = when_ct.replace(tzinfo=_CT).astimezone(datetime.timezone.utc)
    delta_hours = int((when_utc - start_utc).total_seconds() // 3600)
    if not 0 <= delta_hours < HOURS_PER_ARM:
        return None
    # Roundtrip-consistency guard: confirm the computed hour-index
    # actually maps back to ``when_ct``. For arm 11's DST-excluded hour
    # (11-01 23:00 CST) this catches the case where when_utc falls in
    # the [288 UTC, 289 UTC) gap that maps to the same delta_hours as
    # an earlier valid index.
    if hour_index_to_datetime(arm, delta_hours) != when_ct:
        return None
    return delta_hours
