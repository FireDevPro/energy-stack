"""§2 live day-of decision audit.

Chronological timeline of layer_resolution + supervisor events.
Reconciliation against hvac.actions is narrowed to action-fire events
only (action_label NOT prefixed `MID_PERIOD_REPUSH:`) so the common
mid-period-no-op case doesn't false-flag.
"""
from typing import Any


def _is_action_fire(event: dict[str, Any]) -> bool:
    """True when the event's action_label indicates a scheduled-action
    firing (not a mid-period repush)."""
    label = event.get("action_label")
    if not isinstance(label, str):
        return False
    return not label.startswith("MID_PERIOD_REPUSH:")


def render(
    *,
    target_date: str,
    layer_events: list[dict[str, Any]],
    supervisor_events: list[dict[str, Any]],
    hvac_actions: list[dict[str, Any]],
) -> str:
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

    lines.append("| time | tick_id | event | winning_layer / decision | "
                  "effective_cool_f | reason_code |")
    lines.append("|---|---|---|---|---|---|")
    for evt in all_events:
        ts = evt.get("ts", "")[-14:]  # HH:MM:SS.xxx-zz suffix-ish
        tick = (evt.get("tick_id") or "")[:12]
        kind = "layer" if evt.get("msg") == "decision_trace.layer_resolution" else "sup"
        if kind == "layer":
            secondary = evt.get("winning_layer", "")
            eff = evt.get("effective_cool_f", "")
            reason = ""
        else:
            secondary = evt.get("decision", "")
            eff = ""
            reason = f"`{evt.get('reason_code', '')}`" if evt.get('reason_code') else ""
        lines.append(f"| {ts} | `{tick}` | {kind} | {secondary} | {eff} | {reason} |")
    lines.append("")

    # Reconciliation summary
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
