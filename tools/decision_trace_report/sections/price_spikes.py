"""§3 price spike reaction audit.

Coarse correctness check: for each ComEd 5-min price >=10c, look at
the nearest decision_trace.price_overlay_eval and check whether the
observed reason_code is in a small allow-list for the observed tier.
NOT a re-implementation of the state machine — false positives at
minimum-hold edges are accepted as a v1 trade-off.
"""
from typing import Any

# Allow-list per observed tier
ALLOWED_BY_TIER: dict[str, set[str]] = {
    "elevated": {
        "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
        "PRICE_OVERLAY_UPGRADED_TO_SCARCITY",
        "PRICE_OVERLAY_HELD_IN_TIER",
    },
    "scarcity": {
        "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
        "PRICE_OVERLAY_UPGRADED_TO_SCARCITY",
        "PRICE_OVERLAY_HELD_IN_TIER",
    },
    "normal": {
        "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
        "PRICE_OVERLAY_STALE_FEED_RELEASED",
        "PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED",
    },
}


def is_explained(spike: dict[str, Any], trace: dict[str, Any]) -> bool:
    """Coarse v1 check — observed reason_code in tier-specific allow-list.

    Special case: if spike price >= 10c and observed tier is `normal`,
    the only "explained" reason_codes are stale-feed / feed-unavailable.
    NORMAL_BELOW_TRIGGER does not explain a real spike."""
    tier = trace.get("new_tier") or "normal"
    code = trace.get("reason_code", "")
    if tier == "normal" and spike.get("price_cents", 0) >= 10.0:
        return code in {"PRICE_OVERLAY_STALE_FEED_RELEASED",
                         "PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED"}
    return code in ALLOWED_BY_TIER.get(tier, set())


def _nearest_trace(
    spike_time: Any,
    overlay_events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the overlay event closest in time to `spike_time`.

    `spike_time` is whatever the InfluxClient.fetch_comed_prices_above*
    contract returns in the `_time` slot — a tz-aware datetime in
    production. Overlay events come from Loki with `ts` as an ISO
    string. We collapse both to epoch seconds for distance compare.
    """
    if not overlay_events:
        return None
    spike_epoch = _to_epoch(spike_time)
    nearest = min(
        overlay_events,
        key=lambda e: abs(_to_epoch(e.get("ts", "")) - spike_epoch),
    )
    return nearest


def _to_epoch(value: Any) -> float:
    """Coerce datetime or ISO-8601 string to epoch seconds."""
    from datetime import datetime
    if hasattr(value, "timestamp"):
        return value.timestamp()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _render_time(value: Any) -> str:
    """Stringify a `_time` value for the rendered table cell."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _format_ct_trace_time(ts: Any) -> str:
    """Render a trace event's `ts` as `HH:MM:SS-OFFSET` in CT.

    Mirror of `day_of._format_ct_time`; duplicated here so §3 doesn't
    cross-import from another section. Both serve the same display
    contract — see §2 module for the rationale (microsecond ts in
    production breaks the `[-14:]` slice).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
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
    ct = dt.astimezone(ZoneInfo("America/Chicago"))
    offset = ct.strftime("%z")
    offset_colon = f"{offset[:3]}:{offset[3:]}" if offset else ""
    return ct.strftime("%H:%M:%S") + offset_colon


def render(
    *,
    target_date: str,
    spikes: list[dict[str, Any]],
    overlay_events: list[dict[str, Any]],
) -> str:
    lines: list[str] = [f"## §3 Price spike reaction audit — {target_date}", ""]
    if not spikes:
        lines.append("No spikes today (no `comed.prices` ≥10¢ on this date).")
        lines.append("")
        return "\n".join(lines)

    lines.append("| spike time | price ¢/kWh | nearest trace | tier | reason_code | explained |")
    lines.append("|---|---:|---|---|---|---|")
    for spike in spikes:
        spike_t = spike.get("_time", "")
        trace = _nearest_trace(spike_t, overlay_events)
        spike_display = _render_time(spike_t)
        if trace is None:
            lines.append(
                f"| {spike_display} | {spike.get('price_cents'):.2f} | "
                "(no nearby trace) | — | — | ❌ no trace |"
            )
            continue
        tier = trace.get("new_tier", "")
        code = trace.get("reason_code", "")
        ok = is_explained(spike, trace)
        icon = "✅" if ok else "❌"
        lines.append(
            f"| {spike_display} | {spike.get('price_cents'):.2f} | "
            f"{_format_ct_trace_time(trace.get('ts', ''))} | `{tier}` | `{code}` | {icon} |"
        )
    lines.append("")
    return "\n".join(lines)


def count_unexplained(
    *,
    spikes: list[dict[str, Any]],
    overlay_events: list[dict[str, Any]],
) -> int:
    n = 0
    for spike in spikes:
        trace = _nearest_trace(spike.get("_time", ""), overlay_events)
        if trace is None or not is_explained(spike, trace):
            n += 1
    return n
