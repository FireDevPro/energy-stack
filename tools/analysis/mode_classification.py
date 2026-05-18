"""Hour-level mode classification per docs/plans/sced-rebaseline-spec-2026-05-13.md
§5 + §5.1.

5 outcomes per hour:
- A_ACTIVE:         Arm A, telemetry valid.
- B_ACTIVE:         Arm B, controller healthy, all required-for-that-hour
                    control inputs healthy.
- B_FALLBACK:       Arm B, controller alive, >=1 required-for-that-hour
                    input stale.
- B_DOWN:           Arm B, controller process not writing (CTK04AE took over).
- TELEMETRY_INVALID: HVAC measurement fails spec §7 sample/gap rules, regardless
                    of arm.

Per spec §5: "required inputs are price and weather/forecast for ALL Arm B
hours, plus PJM capacity-risk inputs ONLY during the pre-registered
capacity-risk operating window".
"""
from __future__ import annotations

import datetime
from enum import Enum
from typing import Literal


# Pre-registered capacity-risk operating window per spec §5.1.
# 2026-06-01 00:00 CT through 2026-09-30 23:59 CT (audit-phase item to confirm
# pre-OSF). Stored as CT-naive datetimes. End is exclusive.
CAPACITY_RISK_WINDOW_START = datetime.datetime(2026, 6, 1, 0, 0)
CAPACITY_RISK_WINDOW_END = datetime.datetime(2026, 10, 1, 0, 0)


class HourMode(Enum):
    A_ACTIVE = "A-active"
    B_ACTIVE = "B-active"
    B_FALLBACK = "B-fallback"
    B_DOWN = "B-down"
    TELEMETRY_INVALID = "telemetry-invalid"


def in_capacity_risk_window(when_ct: datetime.datetime) -> bool:
    """True if ``when_ct`` falls within the §5.1 capacity-risk operating window.

    ``when_ct`` is CT-local naive (no tzinfo). Comparison is half-open
    [start, end).
    """
    return CAPACITY_RISK_WINDOW_START <= when_ct < CAPACITY_RISK_WINDOW_END


def required_feeds_at(when_ct: datetime.datetime) -> set[str]:
    """Required input-feed names for B-active classification at ``when_ct``.

    Per spec §5 / §5.1: price + weather always; pjm_capacity_risk only
    in-window.
    """
    required = {"price", "weather"}
    if in_capacity_risk_window(when_ct):
        required.add("pjm_capacity_risk")
    return required


def classify_hour(
    *,
    arm: Literal["A", "B"],
    when_ct: datetime.datetime,
    telemetry_valid: bool,
    controller_alive: bool,
    feeds: dict[str, bool],
) -> HourMode:
    """Classify a single hour given the four spec inputs.

    Order of precedence (spec §5):
    1. telemetry-invalid wins over everything.
    2. Arm A telemetry-valid -> A_ACTIVE.
    3. Arm B + dead controller -> B_DOWN.
    4. Arm B + alive controller + any required-for-this-hour feed stale -> B_FALLBACK.
    5. Otherwise -> B_ACTIVE.
    """
    if not telemetry_valid:
        return HourMode.TELEMETRY_INVALID
    if arm == "A":
        return HourMode.A_ACTIVE
    if not controller_alive:
        return HourMode.B_DOWN
    for feed_name in required_feeds_at(when_ct):
        if not feeds.get(feed_name, False):
            return HourMode.B_FALLBACK
    return HourMode.B_ACTIVE


def is_fully_valid(mode: HourMode) -> bool:
    """Per spec §5: fully-valid = telemetry-valid AND in intended treatment mode."""
    return mode in (HourMode.A_ACTIVE, HourMode.B_ACTIVE)
