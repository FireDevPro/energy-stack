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
    spike_time_iso: str,
    overlay_events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the overlay event closest in time to spike_time_iso."""
    if not overlay_events:
        return None
    # Lexicographic compare on ISO timestamps works for same-tz strings.
    nearest = min(
        overlay_events,
        key=lambda e: abs(_iso_to_sortable(e.get("ts", "")) -
                          _iso_to_sortable(spike_time_iso)),
    )
    return nearest


def _iso_to_sortable(s: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


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
        trace = _nearest_trace(spike.get("_time", ""), overlay_events)
        if trace is None:
            lines.append(
                f"| {spike.get('_time')} | {spike.get('price_cents'):.2f} | "
                "(no nearby trace) | — | — | ❌ no trace |"
            )
            continue
        tier = trace.get("new_tier", "")
        code = trace.get("reason_code", "")
        ok = is_explained(spike, trace)
        icon = "✅" if ok else "❌"
        lines.append(
            f"| {spike.get('_time')} | {spike.get('price_cents'):.2f} | "
            f"{trace.get('ts', '')[-14:]} | `{tier}` | `{code}` | {icon} |"
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
