"""Today-actions payload builder.

Assembles the data the narrative-cockpit `ActionLog` component
renders: today's planned ScheduleActions (per day_type) with per-item
status (past / current / future) derived from wall-clock time.

Pure function: I/O is the caller's job (app.py wires it to the
schedule mirror + current time + day_type from the snapshot trace).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .schedules import (
    PlannedAction,
    planned_setpoints_for_day,
)


CT = ZoneInfo("America/Chicago")


def build_today_actions_live(
    *,
    now: datetime,
    day_type: str | None,
) -> dict[str, Any]:
    """Build today's action list + per-item status from wall-clock.

    Status rules:
      - The most recent action whose (hour, minute) <= now is
        "current".
      - Earlier actions are "past".
      - Later actions are "future".
      - If no actions are scheduled yet today (all in the future),
        all are "future" and no "current" is set.
    """
    now_ct = now.astimezone(CT)
    schedule: list[PlannedAction] = planned_setpoints_for_day(day_type)

    # Identify the most-recent action index whose time is in the past
    # or right now.
    current_idx: int | None = None
    for i, a in enumerate(schedule):
        if (a.hour, a.minute) <= (now_ct.hour, now_ct.minute):
            current_idx = i
        else:
            break

    items: list[dict[str, Any]] = []
    for i, a in enumerate(schedule):
        if current_idx is None:
            status = "future"
        elif i < current_idx:
            status = "past"
        elif i == current_idx:
            status = "current"
        else:
            status = "future"
        items.append(
            {
                "hour": a.hour,
                "minute": a.minute,
                "label": a.label,
                "cool_setpoint_f": a.cool_setpoint_f,
                "status": status,
            }
        )

    return {
        "now": now_ct.isoformat(),
        "day_type": day_type or "NORMAL",
        "actions": items,
    }


# ---- Canned payload --------------------------------------------------


def build_today_actions_canned() -> dict[str, Any]:
    """Synthetic today_actions payload anchored to the same mid-summer
    afternoon as the summer_normal snapshot fixture."""
    return build_today_actions_live(
        now=datetime(2026, 7, 14, 13, 0, 30, tzinfo=CT),
        day_type="NORMAL",
    )
