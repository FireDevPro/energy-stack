"""§2 live day-of decision audit.

Chronological timeline of layer_resolution + supervisor events.
Reconciliation against hvac.actions is narrowed to action-fire events
only (action_label NOT prefixed `MID_PERIOD_REPUSH:`) so the common
mid-period-no-op case doesn't false-flag.
"""
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_CT = ZoneInfo("America/Chicago")


def _format_ct_time(ts: Any) -> str:
    """Render a Loki/Influx timestamp as `HH:MM:SS-OFFSET` in CT.

    Accepts a tz-aware ISO string or a datetime. Microseconds are
    dropped — display is second-precision per spec §5 line 168.

    Live-verification regression: production `ts` strings carry
    microseconds (`2026-05-14T13:00:08.438328+00:00`); the pre-fix
    `ts[-14:]` slice cut mid-fractional and rendered garbage like
    `8.438328+00:00` in the table.
    """
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return ts
    elif isinstance(ts, datetime):
        dt = ts
    else:
        return str(ts)
    if dt.tzinfo is None:
        return dt.strftime("%H:%M:%S")
    ct = dt.astimezone(_CT)
    offset = ct.strftime("%z")  # e.g., "-0500"
    offset_colon = f"{offset[:3]}:{offset[3:]}" if offset else ""
    return ct.strftime("%H:%M:%S") + offset_colon


def _is_action_fire(event: dict[str, Any]) -> bool:
    """True when the event's action_label indicates a scheduled-action
    firing (not a mid-period repush)."""
    label = event.get("action_label")
    if not isinstance(label, str):
        return False
    return not label.startswith("MID_PERIOD_REPUSH:")


_HEADERS = [
    "time", "tick_id", "event", "winning_layer",
    "schedule_cool_f", "price_cool_f", "fivecp_cool_f", "effective_cool_f",
    "sup_decision", "sup_reason",
]


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _cell(value: Any) -> str:
    """Render a single cell value; missing/None → blank."""
    if value is None:
        return ""
    return str(value)


def render(
    *,
    target_date: str,
    layer_events: list[dict[str, Any]],
    supervisor_events: list[dict[str, Any]],
    hvac_actions: list[dict[str, Any]],
) -> str:
    """§2 live day-of decision audit per spec §5 (lines 162-180).

    Renders one row per layer_resolution + supervisor event in
    chronological order. Layer rows fill the four `*_cool_f` columns;
    supervisor rows fill the `sup_decision` + `sup_reason` columns.
    Consecutive events sharing the same `tick_id` are grouped — an
    all-blank separator row marks each tick boundary so the reader can
    scan tick-by-tick.
    """
    lines: list[str] = [f"## §2 Live day-of decision audit — {target_date}", ""]
    all_events = sorted(
        [*layer_events, *supervisor_events],
        key=lambda e: e.get("ts", ""),
    )
    if not all_events:
        lines.append("No `decision_trace.layer_resolution` or `decision_trace.supervisor` "
                     "events for this date.")
        lines.append("")
        return "\n".join(lines)

    lines.append(_row(_HEADERS))
    lines.append("|" + "|".join(["---"] * len(_HEADERS)) + "|")

    separator_row = _row([""] * len(_HEADERS))
    prev_tick: str | None = None
    for evt in all_events:
        tick = (evt.get("tick_id") or "")[:8]
        if prev_tick is not None and tick != prev_tick:
            lines.append(separator_row)
        ts = _format_ct_time(evt.get("ts", ""))
        if evt.get("msg") == "decision_trace.layer_resolution":
            cells = [
                ts, f"`{tick}`" if tick else "", "layer",
                _cell(evt.get("winning_layer")),
                _cell(evt.get("schedule_cool_f")),
                _cell(evt.get("price_cool_f")),
                _cell(evt.get("fivecp_cool_f")),
                _cell(evt.get("effective_cool_f")),
                "", "",
            ]
        else:
            reason = evt.get("reason_code", "")
            cells = [
                ts, f"`{tick}`" if tick else "", "sup",
                "", "", "", "", "",
                _cell(evt.get("decision")),
                f"`{reason}`" if reason else "",
            ]
        lines.append(_row(cells))
        prev_tick = tick
    lines.append("")

    fire_mismatches = count_action_fire_mismatches(
        layer_events=layer_events, hvac_actions=hvac_actions,
    )
    if fire_mismatches:
        lines.append(
            f"### ⚠️ Action-fire reconciliation: {fire_mismatches} trace event(s) "
            "without matching `hvac.actions` row."
        )
    return "\n".join(lines)


def count_supervisor_non_approved(supervisor_events: list[dict[str, Any]]) -> int:
    return sum(1 for evt in supervisor_events if evt.get("decision") != "approved")


def count_action_fire_mismatches(
    *,
    layer_events: list[dict[str, Any]],
    hvac_actions: list[dict[str, Any]],
    match_window_s: int = 120,
) -> int:
    """A layer_resolution event with an action-fire label and no
    matching hvac.actions row within +/- match_window_s seconds is
    a mismatch. Mid-period repushes are not reconciled.

    Matches by BOTH action_label AND timestamp window so that one
    Influx row can't accidentally satisfy two separate trace events
    sharing the same label (e.g., two COAST firings same day)."""
    from datetime import datetime
    from datetime import timedelta as _td

    def parse_ts(s: Any) -> datetime | None:
        if not isinstance(s, str):
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    # Build a list of (label, ts) tuples we can mark as consumed
    available = [
        (a.get("action_label"), a.get("_time"))
        for a in hvac_actions
        if a.get("action_label") and a.get("_time") is not None
    ]
    consumed = [False] * len(available)

    n_mismatch = 0
    window = _td(seconds=match_window_s)
    for evt in layer_events:
        if not _is_action_fire(evt):
            continue
        evt_label = evt.get("action_label")
        evt_ts = parse_ts(evt.get("ts"))
        if evt_ts is None or not evt_label:
            continue
        # Find first unconsumed action row matching label + within window
        matched = False
        for i, (label, row_ts) in enumerate(available):
            if consumed[i] or label != evt_label:
                continue
            if abs(row_ts - evt_ts) <= window:
                consumed[i] = True
                matched = True
                break
        if not matched:
            n_mismatch += 1
    return n_mismatch
