"""vigil_derive — pure derivation unit tests (the bug-prone core)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..vigil_derive import (
    alive_from,
    avoided_cost,
    build_episodes,
    build_hold,
    c_to_f,
    compose_why,
    this_spike_from_episodes,
    tier_from_cents,
    to_display_f,
)

UTC = timezone.utc


def _t(mins_ago: int, now: datetime) -> datetime:
    return now - timedelta(minutes=mins_ago)


def test_c_to_f() -> None:
    assert c_to_f(25.0) == 77.0
    assert c_to_f(29.5) == 85.1
    assert c_to_f(None) is None


def test_to_display_f_celsius_mode() -> None:
    # CTK04 is in °C mode: poller stores °C in the *_f fields.
    assert to_display_f(24.5) == 76.1   # 24.5°C indoor
    assert to_display_f(23.0) == 73.4   # 23°C setpoint
    # already-°F values (>= 50) pass through unchanged (self-heals on flip)
    assert to_display_f(74.2) == 74.2
    assert to_display_f(85.1) == 85.1
    assert to_display_f(None) is None


def test_build_episodes_dedupes_consecutive_tiers() -> None:
    now = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
    trans = [
        {"tier": "scarcity", "at": _t(50, now), "cents": 21.0},
        {"tier": "scarcity", "at": _t(45, now), "cents": 30.0},  # re-affirm, deduped
        {"tier": "elevated", "at": _t(30, now), "cents": 12.0},
        {"tier": "normal", "at": _t(5, now), "cents": 6.0},
    ]
    eps = build_episodes(trans, now=now)
    assert eps[0]["tiers_walked_strs"] == ["scarcity", "elevated"]
    assert eps[0]["peak_cents"] == 30.0  # peak still from all transition prices


def test_tier_boundaries() -> None:
    assert tier_from_cents(9.9, 10, 20) == "normal"
    assert tier_from_cents(10.0, 10, 20) == "elevated"
    assert tier_from_cents(19.9, 10, 20) == "elevated"
    assert tier_from_cents(20.0, 10, 20) == "scarcity"
    assert tier_from_cents(None, 10, 20) == "normal"


def test_alive_from() -> None:
    assert alive_from(14, False) is True
    assert alive_from(601, False) is False       # stale arm_mode
    assert alive_from(14, True) is False          # watchdog beacon
    assert alive_from(None, False) is False


def test_avoided_cost() -> None:
    # 60 min coasting at 2.35 kW while price is 20¢/kWh -> 2.35 * 0.20 = $0.47
    assert avoided_cost(60, 20.0) == 0.47
    assert avoided_cost(0, 20.0) == 0.0


def test_build_episodes_engage_then_release() -> None:
    now = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
    trans = [
        {"tier": "elevated", "at": _t(40, now), "cents": 12.0},
        {"tier": "scarcity", "at": _t(29, now), "cents": 24.8},
        {"tier": "normal", "at": _t(5, now), "cents": 6.0},
    ]
    eps = build_episodes(trans, now=now)
    assert len(eps) == 1
    ep = eps[0]
    assert ep["tiers_walked_strs"] == ["elevated", "scarcity"]
    assert ep["resolution"] == "released"
    assert ep["peak_cents"] == 24.8
    assert ep["duration_min"] == 35  # 40 -> 5 min ago
    assert ep["tiers_walked"][0] == {"tier": "elevated", "at": _t(40, now).isoformat()}


def test_build_episodes_ongoing() -> None:
    now = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
    trans = [{"tier": "elevated", "at": _t(20, now), "cents": 13.0}]
    eps = build_episodes(trans, now=now)
    assert len(eps) == 1
    assert eps[0]["resolution"] == "ongoing"
    assert eps[0]["ended_at"] is None
    assert eps[0]["duration_min"] == 20


def test_this_spike_ongoing_is_engaged() -> None:
    now = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
    eps = build_episodes(
        [{"tier": "scarcity", "at": _t(22, now), "cents": 24.8}], now=now
    )
    sp = this_spike_from_episodes(eps, now=now, tier_current="scarcity", hold_active=True)
    assert sp is not None and sp["ended"] is False
    assert sp["peak_cents"] == 24.8


def test_this_spike_recently_ended_is_release() -> None:
    now = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
    trans = [
        {"tier": "scarcity", "at": _t(40, now), "cents": 31.2},
        {"tier": "normal", "at": _t(4, now), "cents": 7.1},
    ]
    eps = build_episodes(trans, now=now)
    sp = this_spike_from_episodes(eps, now=now, tier_current="normal", hold_active=False)
    assert sp is not None and sp["ended"] is True


def test_this_spike_old_episode_is_none() -> None:
    now = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
    trans = [
        {"tier": "scarcity", "at": _t(120, now), "cents": 31.2},
        {"tier": "normal", "at": _t(100, now), "cents": 7.1},
    ]
    eps = build_episodes(trans, now=now)
    sp = this_spike_from_episodes(eps, now=now, tier_current="normal", hold_active=False)
    assert sp is None


def test_build_hold_active() -> None:
    now = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
    action = {"commanded_cool": 29.5, "schedule_cool": 25.5, "_time": _t(22, now)}
    hold = build_hold(
        action, now=now, compressor_on=False, hold_expires_at=now + timedelta(minutes=8)
    )
    assert hold is not None
    assert hold["commanded_cool_f"] == 85.1
    assert hold["schedule_cool_f"] == 77.9
    assert hold["minutes_held"] == 22
    assert hold["minutes_to_expiry"] == 8
    assert hold["coasting"] is True


def test_build_hold_expired_is_none() -> None:
    now = datetime(2026, 7, 7, 21, 0, tzinfo=UTC)
    action = {"commanded_cool": 29.5, "schedule_cool": 25.5, "_time": _t(60, now)}
    assert build_hold(action, now=now, compressor_on=False,
                      hold_expires_at=now - timedelta(minutes=1)) is None
    assert build_hold(None, now=now, compressor_on=False, hold_expires_at=None) is None


def test_compose_why_cases() -> None:
    base = dict(gated=False, fresh=True, alive=True, releasing=False)
    assert compose_why(tier="normal", cents=3.4, commanded_cool_f=None,
                       minutes_to_expiry=None, **base) == \
        "Cheap power, 3.4¢ — thermostat's running its own schedule."
    assert compose_why(tier="scarcity", cents=24.8, commanded_cool_f=85.1,
                       minutes_to_expiry=8, **base) == \
        "SCARCITY, 24.8¢ — holding 85°, riding the thermal battery, ~8 min left on this hold."
    assert compose_why(tier="elevated", cents=12.0, commanded_cool_f=80.4,
                       minutes_to_expiry=15, **base) == \
        "Elevated power, 12.0¢ — holding 80°, ~15 min left on this hold."


def test_compose_why_overrides() -> None:
    assert compose_why(tier="normal", cents=5.2, commanded_cool_f=None,
                       minutes_to_expiry=None, gated=False, fresh=True,
                       alive=False, releasing=False) == \
        "⚠ Controller not responding — thermostat is on its own program."
    assert compose_why(tier="normal", cents=5.2, commanded_cool_f=None,
                       minutes_to_expiry=None, gated=False, fresh=False,
                       alive=True, releasing=False) == \
        "Price feed stale — standing down to your schedule."
    assert compose_why(tier="normal", cents=7.1, commanded_cool_f=None,
                       minutes_to_expiry=None, gated=False, fresh=True,
                       alive=True, releasing=True) == \
        "Spike over — releasing back to your schedule."
    assert compose_why(tier="scarcity", cents=24.8, commanded_cool_f=85.1,
                       minutes_to_expiry=8, gated=True, fresh=True, alive=True,
                       releasing=False).endswith("(humidity gate: cooling to dry the air)")
