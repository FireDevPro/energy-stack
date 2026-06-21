"""Pure-function controller helpers for the commissioning-controller build.

No I/O, no state, no side effects. Safe to import and test in isolation.

Interface consumed by later tasks (names are load-bearing — do not rename):
    comfort_baseline_cool(program, when) -> float
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time as dtime
from typing import Any


def comfort_baseline_cool(
    program: Sequence[dict[str, Any]],
    when: datetime,
) -> float:
    """Return the cool setpoint for the comfort block active at `when`.

    Each block is a dict with keys 'from', 'to', 'cool' (string times
    "HH:MM" and float setpoint). Supports midnight-wrap blocks where
    the 'to' time is earlier than 'from' (e.g., "22:00" to "06:00").

    The first matching block wins. Raises RuntimeError if no block
    covers the given time (caller config must be gapless; the example
    YAML is gapless by design).
    """
    t = dtime(when.hour, when.minute)

    for block in program:
        from_str: str = block["from"]
        to_str: str = block["to"]

        fh, fm = (int(x) for x in from_str.split(":"))
        th, tm = (int(x) for x in to_str.split(":"))
        t_from = dtime(fh, fm)
        t_to = dtime(th, tm)

        if t_from < t_to:
            # Normal (non-wrap) block: from <= t < to
            if t_from <= t < t_to:
                return float(block["cool"])
        else:
            # Midnight-wrap block: from <= t OR t < to
            if t >= t_from or t < t_to:
                return float(block["cool"])

    raise RuntimeError(
        f"No comfort block covers time {when.strftime('%H:%M')}. "
        "Ensure comfort_program covers all 24 hours without gaps."
    )
