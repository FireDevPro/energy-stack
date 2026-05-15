"""§1 night-before decision audit.

Renders the 21:00 day-type decision + revisits, §7 precool decision,
and reconciles trace events against the hvac.decisions /
hvac.precool_window Influx rows. Discrepancies count toward the top
anomaly summary.
"""
from typing import Any


def render(
    *,
    target_date: str,
    day_type_events: list[dict[str, Any]],
    precool_events: list[dict[str, Any]],
    hvac_decisions: list[dict[str, Any]],
    hvac_precool_window: dict[str, Any] | None,
) -> str:
    lines: list[str] = [f"## §1 Night-before decision audit — {target_date}", ""]

    # Day-type
    lines.append("### Day-type decision")
    lines.append("")
    if not day_type_events:
        lines.append("⚠️ No decision_trace.day_type_decision events found for this date.")
        if hvac_decisions:
            lines.append("")
            lines.append(
                f"`hvac.decisions` row present: `{hvac_decisions[0].get('day_type')}` "
                "— possible trace/Influx disagreement (no trace to compare)."
            )
    else:
        for evt in day_type_events:
            lines.append(
                f"- **`{evt.get('winning_day_type')}`** "
                f"(reason: `{evt.get('winning_reason')}`)"
            )
            lines.append(
                f"  high_f={evt.get('high_f')}, apparent_max_f={evt.get('apparent_max_f')}"
            )
            tape = evt.get("evaluation_tape", [])
            if tape:
                lines.append("")
                lines.append("  | rule | threshold | actual | fired | reason_code |")
                lines.append("  |---|---|---|---|---|")
                for entry in tape:
                    fired = "✅" if entry["fired"] else "❌"
                    lines.append(
                        f"  | {entry['rule']} | {entry['threshold']} | "
                        f"{entry['actual']} | {fired} | `{entry['reason_code']}` |"
                    )
            lines.append("")

    # Trace vs Influx reconciliation
    trace_dt = day_type_events[0]["winning_day_type"] if day_type_events else None
    influx_dt = hvac_decisions[0]["day_type"] if hvac_decisions else None
    if trace_dt and influx_dt and trace_dt != influx_dt:
        lines.append(
            f"### ⚠️ Reconciliation mismatch: trace says `{trace_dt}`, "
            f"hvac.decisions says `{influx_dt}` — investigate."
        )
        lines.append("")

    # Precool decision
    lines.append("### §7 Precool decision")
    lines.append("")
    if not precool_events:
        lines.append("⚠️ No `decision_trace.precool_decision` event found for this date.")
    else:
        evt = precool_events[0]
        lines.append(
            f"- selected: **{evt.get('selected')}**, "
            f"reason_code: `{evt.get('reason_code')}`"
        )
        if evt.get("selected"):
            lines.append(
                f"  hour_ct={evt.get('hour_ct')}, depth_f={evt.get('depth_f')}"
            )
    lines.append("")

    # Precool reconciliation
    if precool_events and hvac_precool_window is not None:
        trace_sel = precool_events[0].get("selected")
        influx_sel = hvac_precool_window is not None and "hour_ct" in hvac_precool_window
        if trace_sel != influx_sel:
            lines.append(
                f"### ⚠️ Precool reconciliation mismatch: trace selected={trace_sel}, "
                f"hvac.precool_window row present={influx_sel}"
            )
            lines.append("")

    return "\n".join(lines)


def count_discrepancies(
    *,
    day_type_events: list[dict[str, Any]],
    precool_events: list[dict[str, Any]],
    hvac_decisions: list[dict[str, Any]],
    hvac_precool_window: dict[str, Any] | None,
) -> int:
    """Count §1 anomalies for the top summary / heartbeat.

    Two distinct anomaly classes per side:
      1. Both present + disagree
      2. Trace missing while Influx row exists (silent skip — scheduler
         wrote the decision but never emitted the trace; commissioning
         must catch this)

    "Neither present" is NOT a §1 anomaly — §4 feed health and §5
    coverage handle the "scheduler never ran" case.
    """
    n = 0

    # Day-type side
    trace_dt = day_type_events[0]["winning_day_type"] if day_type_events else None
    influx_dt = hvac_decisions[0]["day_type"] if hvac_decisions else None
    if trace_dt and influx_dt and trace_dt != influx_dt:
        n += 1
    elif influx_dt and not trace_dt:
        n += 1

    # Precool side
    influx_precool_present = hvac_precool_window is not None
    if precool_events and influx_precool_present:
        trace_sel = precool_events[0].get("selected")
        influx_sel = "hour_ct" in hvac_precool_window
        if trace_sel != influx_sel:
            n += 1
    elif influx_precool_present and not precool_events:
        n += 1

    return n
