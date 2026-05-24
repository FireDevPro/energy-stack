"""Mirror of the scheduler's per-day-type ScheduleAction tables.

The canonical source is `deploy/energy-stack/hvac_scheduler/app.py`
(NORMAL_SCHEDULE, HOT_SCHEDULE, MILD_SCHEDULE, HOT_STREAK_DAY1_SCHEDULE).
Those constants are **locked at the OSF freeze hash** per AGENTS.md
binding-spec rules, so a static mirror here is safe — the schedule
shape does not drift mid-study.

This module exists only to support the cockpit `day_at_a_glance`
endpoint, which needs to draw the planned future setpoint line. It
does NOT influence the controller; the scheduler reads its own
constants directly.

If a future PR amends the canonical schedules (post-study, or via a
spec amendment), update this file in the same commit.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedAction:
    """Cockpit-side view of a scheduler ScheduleAction.

    Subset of the scheduler's dataclass — only the fields the chart
    actually consumes (hour, minute, label, cool_setpoint_f). Excludes
    fields like release_hold and humid override variants since the
    chart shows the baseline cool setpoint, not the runtime-adjusted
    one.
    """
    hour: int
    minute: int
    label: str
    cool_setpoint_f: int | None  # None for release-hold-only actions


# Day-type -> ordered list of (hour, minute, label, cool_f) actions.
# Mirrors deploy/energy-stack/hvac_scheduler/app.py:
#   NORMAL_SCHEDULE (line 168), HOT_SCHEDULE (line 214),
#   MILD_SCHEDULE (line 227), HOT_STREAK_DAY1_SCHEDULE (line 235).
SCHEDULES_BY_DAY_TYPE: dict[str, list[PlannedAction]] = {
    "NORMAL": [
        PlannedAction(6, 0, "PRE_COOL", 70),
        PlannedAction(13, 0, "COAST", 79),
        PlannedAction(19, 0, "RECOVER", 75),
        PlannedAction(21, 0, "SLEEP", 73),
    ],
    "HOT_5CP_RISK": [
        PlannedAction(4, 0, "HOT_PRE_COOL", 68),
        PlannedAction(12, 0, "HOT_COAST", 80),
        PlannedAction(19, 0, "HOT_RECOVER", 75),
        PlannedAction(21, 0, "SLEEP", 73),
    ],
    "MILD": [
        PlannedAction(0, 5, "MILD_RELEASE_HOLD", None),
    ],
    "HOT_STREAK_DAY1": [
        PlannedAction(3, 0, "STREAK_PRE_COOL_EARLY", 66),
        PlannedAction(12, 0, "HOT_COAST", 80),
        PlannedAction(19, 0, "HOT_RECOVER", 75),
        PlannedAction(21, 0, "SLEEP", 73),
    ],
}


def planned_setpoints_for_day(
    day_type: str | None,
) -> list[PlannedAction]:
    """Return the planned ScheduleAction list for `day_type`.

    Unknown / missing day_type returns NORMAL as the safest default
    (matches what the scheduler does when day_type evaluation is
    inconclusive).
    """
    if day_type and day_type in SCHEDULES_BY_DAY_TYPE:
        return SCHEDULES_BY_DAY_TYPE[day_type]
    return SCHEDULES_BY_DAY_TYPE["NORMAL"]
