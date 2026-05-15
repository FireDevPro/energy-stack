"""§5 coverage scorecard — which reason_codes have been observed live."""


def render(
    *,
    reference_codes: dict[str, list[str]],
    cumulative_counts: dict[str, int],
    recent_7d_counts: dict[str, int],
) -> str:
    """Render the coverage scorecard as a markdown string.

    For each enum group, list every reference code with status (observed
    live vs not) + cumulative count + last-7d count. Codes appearing in
    cumulative_counts but NOT in any enum group are reported as
    'unexpected reason codes' (anomaly).
    """
    lines: list[str] = ["## §5 Coverage scorecard", ""]
    all_reference: set[str] = {c for codes in reference_codes.values() for c in codes}

    # Unexpected codes — counts but not in any enum
    unexpected = sorted(set(cumulative_counts) - all_reference)
    if unexpected:
        lines.append("### ⚠️ Unexpected reason codes (in trace but NOT in any enum)")
        lines.append("")
        for code in unexpected:
            n_cum = cumulative_counts.get(code, 0)
            n_7d = recent_7d_counts.get(code, 0)
            lines.append(f"- `{code}` — cumulative: {n_cum:,}, last 7d: {n_7d:,}")
        lines.append("")

    # Per-enum tables
    for enum_name in sorted(reference_codes):
        codes = reference_codes[enum_name]
        lines.append(f"### {enum_name}")
        lines.append("")
        lines.append("| Code | Status | Cumulative | Last 7 days |")
        lines.append("|---|---|---:|---:|")
        for code in codes:
            n_cum = cumulative_counts.get(code, 0)
            n_7d = recent_7d_counts.get(code, 0)
            status = "✅ observed live" if n_cum > 0 else "⚪ not observed live"
            lines.append(f"| `{code}` | {status} | {n_cum:,} | {n_7d:,} |")
        lines.append("")

    return "\n".join(lines)


def count_unexpected(
    reference_codes: dict[str, list[str]],
    cumulative_counts: dict[str, int],
) -> int:
    """Number of cumulative_counts keys not in any reference enum.
    Used by the top-level anomaly summary."""
    all_reference = {c for codes in reference_codes.values() for c in codes}
    return len(set(cumulative_counts) - all_reference)
