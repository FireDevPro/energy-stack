"""Day-ahead payload builder.

Assembles the data the narrative-cockpit Day-Ahead + Pre-Cool cards
render: tomorrow's persisted day-type decision (from `hvac.decisions`,
written at 21:00 the night before) and the §7 price-aware pre-cool
window (from `hvac.precool_window`, written at the same 21:00 call
when a qualifying cheap+spike pattern exists).

Pure function: I/O is the caller's job. Tests pass synthetic inputs
and assert the assembled output.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


CT = ZoneInfo("America/Chicago")


def build_day_ahead_live(
    *,
    now: datetime,
    day_type_row: dict[str, Any] | None,
    precool_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the day-ahead payload from live query results.

    Inputs:
      - now: tz-aware datetime (any zone; converted to CT for date).
      - day_type_row: latest hvac.decisions row for tomorrow's date,
        or None if no decision has been persisted yet for tomorrow.
      - precool_row: latest hvac.precool_window row for tomorrow's
        date, or None if no §7 window was selected.
    """
    now_ct = now.astimezone(CT)
    tomorrow_ct = now_ct + timedelta(days=1)

    return {
        "now": now_ct.isoformat(),
        "target_date": tomorrow_ct.date().isoformat(),
        "day_type": _build_day_type_card(day_type_row),
        "precool": _build_precool_card(precool_row),
    }


def _build_day_type_card(
    row: dict[str, Any] | None,
) -> dict[str, Any]:
    if row is None:
        return {
            "decided": False,
            "day_type": None,
            "high_f": None,
            "max_dewpoint_f": None,
            "is_heat_advisory": False,
            "alert_summary": "",
            "reason": "",
            "decided_at": None,
        }
    return {
        "decided": True,
        "day_type": row.get("day_type"),
        "high_f": _maybe_float(row.get("high_f")),
        "max_dewpoint_f": _maybe_float(row.get("max_dewpoint_f")),
        "is_heat_advisory": bool(row.get("is_heat_advisory")),
        "alert_summary": row.get("alert_summary") or "",
        "reason": row.get("reason") or "",
        "decided_at": row.get("source_ts"),
    }


def _build_precool_card(
    row: dict[str, Any] | None,
) -> dict[str, Any]:
    if row is None:
        return {
            "selected": False,
            "hour_ct": None,
            "depth_f": None,
            "decided_at": None,
        }
    return {
        "selected": True,
        "hour_ct": int(row.get("hour_ct") or 0),
        "depth_f": int(row.get("depth_f") or 0),
        "decided_at": row.get("source_ts"),
    }


def _maybe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- Canned payload --------------------------------------------------


def build_day_ahead_canned() -> dict[str, Any]:
    """Synthetic day-ahead payload — tomorrow forecast HOT with a
    qualifying §7 pre-cool window."""
    now_ct = datetime(2026, 7, 14, 13, 0, 30, tzinfo=CT)
    decided_at = (now_ct - timedelta(hours=16)).isoformat()
    return build_day_ahead_live(
        now=now_ct,
        day_type_row={
            "day_type": "HOT_5CP_RISK",
            "high_f": 89.0,
            "max_dewpoint_f": 71.0,
            "is_heat_advisory": False,
            "alert_summary": "",
            "reason": "HOT_HIGH_GE_85",
            "source_ts": decided_at,
        },
        precool_row={
            "hour_ct": 3,
            "depth_f": 4,
            "source_ts": decided_at,
        },
    )
