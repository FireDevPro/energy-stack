"""Pure-function controller helpers for the commissioning-controller build.

No I/O, no state, no side effects. Safe to import and test in isolation.

Interface consumed by later tasks (names are load-bearing — do not rename):
    comfort_baseline_cool(program, when) -> float
    hold_expiry(now_local, ttl_minutes) -> datetime
    needs_hold_refresh(now_local, hold_expires_at) -> bool
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time as dtime, timedelta
from typing import Any

# Re-push a live timed hold this many minutes before its device-side expiry.
# At the 60s tick cadence this gives ~5 retry chances against transient TCC
# timeouts before the hold would lapse (and a lapse is the safe direction:
# the device reverts to its onboard schedule until the next successful push).
HOLD_REFRESH_MARGIN_MINUTES = 5


def hold_expiry(now_local: datetime, ttl_minutes: int) -> datetime:
    """Device-side expiry for a timed hold: now + TTL, floored to the
    quarter-hour slot grid.

    Floor, not ceil — spec Safety #2 binds the lapse to <= hold_ttl_minutes,
    and aiosomecomfort raises on non-quarter-hour times. Crossing midnight
    advances the date; the device receives only the time-of-day slot and
    holds to its next occurrence.
    """
    raw = now_local + timedelta(minutes=ttl_minutes)
    return raw.replace(minute=raw.minute - raw.minute % 15, second=0, microsecond=0)


def needs_hold_refresh(
    now_local: datetime,
    hold_expires_at: datetime | None,
    margin_minutes: int = HOLD_REFRESH_MARGIN_MINUTES,
) -> bool:
    """True when the last applied timed hold is within ``margin_minutes`` of
    (or past) its device-side expiry, so the controller must re-push to keep
    it. False when no applied hold is being tracked. Stays True past expiry:
    a missed refresh keeps retrying every tick until a push succeeds.
    """
    if hold_expires_at is None:
        return False
    return now_local >= hold_expires_at - timedelta(minutes=margin_minutes)


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
