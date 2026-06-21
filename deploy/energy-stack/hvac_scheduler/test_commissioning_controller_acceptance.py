"""Outside-in acceptance test for the commissioning controller — the north star.

This represents the WHOLE feature (Arm B = the Arm A comfort program + price
awareness). It exercises the controller over a recorded day in shadow and asserts
the full target behavior:

  * setpoints track the comfort-program baseline and never go below it (floor invariant);
  * a price spike warms the setpoint above the baseline toward the ceiling and rides it;
  * high humidity releases the warm overlay back to the baseline;
  * setpoints land on the temp_scale grid;
  * the decision log records the commanded AND the actual indoor temperature;
  * no day-type resolution and no safety supervisor are consulted.

Feature-complete (Slices A+B+C): the comfort baseline + floor clamp (A), the
config-driven warm price tiers + feed-gap reuse + commanded-vs-actual telemetry
(B), and the humidity-release guard + ride-the-ceiling (C) are all implemented,
so this test PASSES against the real implementation with zero scaffolding — the
``xfail`` marker is removed. That is the definition of feature-complete.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from . import app
from .app import FiringState
# Reuse the existing scheduler-tick harness rather than reinventing a replay rig.
from .test_hvac_scheduler import (
    _make_cfg_with_controller_config,
    _mock_c4_client,
    _stub_layer_eval_io,
)

# The stub comfort program (_A2_PROGRAM_F) is in degF: 73 overnight / 75 morning /
# 78 midday (13:00-19:00) / 75 evening. 13:30 sits mid-midday-block, past the
# 5-min makeup window, so no boundary action fires and the per-tick baseline is
# the pushed value.
_MIDDAY = datetime(2026, 7, 15, 13, 30, tzinfo=ZoneInfo("America/Chicago"))
_MIDDAY_BASELINE = 78.0
# The comfort ceiling carried by the stub config (_make_controller_config_stub):
# comfort_max=85, above the 78 midday baseline so an extreme spike rides to it.
_CEILING = 85.0


def _run_tick(
    monkeypatch: Any,
    *,
    price_cents: float,
    humidity_pct: float,
    now_local: datetime,
) -> tuple[float | None, MagicMock, dict[str, int]]:
    """Drive one shadow ``run_schedule_check`` tick with the given RTP price and
    indoor humidity. Returns (pushed_cool_setpoint, write_action_mock, trip_wires)."""
    trip: dict[str, int] = {
        "validate_setpoints": 0,
        "fetch_today_decision": 0,
        "schedule_for": 0,
    }

    def _trip(name: str, ret: Any) -> Any:
        def _stub(*a: Any, **k: Any) -> Any:
            trip[name] += 1
            return ret
        return _stub

    # The supervisor is deleted; only patch if a regression re-introduces it.
    if hasattr(app, "validate_setpoints"):
        monkeypatch.setattr(app, "validate_setpoints", _trip("validate_setpoints", None))
    if hasattr(app, "fetch_today_decision"):
        monkeypatch.setattr(app, "fetch_today_decision", _trip("fetch_today_decision", "NORMAL"))
    if hasattr(app, "schedule_for"):
        monkeypatch.setattr(app, "schedule_for", _trip("schedule_for", []))

    _stub_layer_eval_io(monkeypatch, price_cents=price_cents)

    captured: dict[str, Any] = {}

    async def _exec(
        c4: Any, action: Any, cool: Any, heat: Any, snapshot: Any,
        dry_run: Any, *, when_ct: Any = None,
    ) -> tuple[bool, None]:
        captured["cool"] = cool
        return (False, None)  # shadow: not applied, no error

    monkeypatch.setattr(app, "execute_action", AsyncMock(side_effect=_exec))

    write_action_mock = MagicMock()
    monkeypatch.setattr(app, "write_action", write_action_mock)
    monkeypatch.setattr(
        app, "read_thermostat_snapshot",
        AsyncMock(return_value={
            "indoor_temp_f": 74.0,
            "humidity": humidity_pct,
            "hvac_mode": "Cool",
            "cool_setpoint_f": 75,
            "heat_setpoint_f": 65,
        }),
    )

    firing = FiringState()
    cfg = _make_cfg_with_controller_config()
    c4, _climate = _mock_c4_client()

    asyncio.run(app.run_schedule_check(
        cfg, c4, MagicMock(), MagicMock(),
        ZoneInfo(cfg.tz_name), now_local, firing,
    ))
    return captured.get("cool"), write_action_mock, trip


def _run_sustained_spike_rides_ceiling(monkeypatch: Any) -> float | None:
    """Drive TWO consecutive extreme-spike ticks (dry air) on a SHARED
    ``FiringState`` and return the effective the second tick holds at.

    The first tick snaps to the ceiling. On the second tick the effective is
    unchanged (still comfort_max), so the push short-circuits — the "ride" is
    the held ``last_pushed_effective_cool`` at the ceiling, and the tier stays
    ``extreme`` (held via min-hold). It does NOT bounce back to baseline.
    """
    _stub_layer_eval_io(monkeypatch, price_cents=50.0)
    monkeypatch.setattr(app, "execute_action",
                        AsyncMock(return_value=(False, None)))
    monkeypatch.setattr(app, "write_action", MagicMock())
    monkeypatch.setattr(
        app, "read_thermostat_snapshot",
        AsyncMock(return_value={
            "indoor_temp_f": 74.0, "humidity": 45.0, "hvac_mode": "Cool",
            "cool_setpoint_f": 75, "heat_setpoint_f": 65,
        }),
    )

    firing = FiringState()
    cfg = _make_cfg_with_controller_config()
    c4, _climate = _mock_c4_client()

    for _ in range(2):
        asyncio.run(app.run_schedule_check(
            cfg, c4, MagicMock(), MagicMock(),
            ZoneInfo(cfg.tz_name), _MIDDAY, firing,
        ))

    # Tier held across both ticks (no bounce); effective pinned at the ceiling.
    assert firing.price_overlay_state.current_tier == "extreme"
    return firing.last_pushed_effective_cool


def test_commissioning_controller_full_day_acceptance(monkeypatch: Any) -> None:
    # 1) Normal price, dry air: hold the comfort baseline. (True in Slice A.)
    cool_n, wa_n, trip_n = _run_tick(
        monkeypatch, price_cents=5.0, humidity_pct=45.0, now_local=_MIDDAY)
    assert cool_n == _MIDDAY_BASELINE                      # tracks the baseline
    assert cool_n is not None and cool_n >= _MIDDAY_BASELINE   # floor invariant
    assert (cool_n * 2) == round(cool_n * 2)               # on the temp_scale grid
    assert trip_n["fetch_today_decision"] == 0             # no day-type
    assert trip_n["schedule_for"] == 0
    assert trip_n["validate_setpoints"] == 0               # no supervisor
    for c in wa_n.call_args_list:
        assert "supervisor_decision" not in c.kwargs

    # 2) Price SPIKE (dry air): warm above the baseline toward the ceiling and
    #    RIDE it — an extreme spike snaps the effective straight to comfort_max.
    cool_spike, _wa_s, _trip_s = _run_tick(
        monkeypatch, price_cents=50.0, humidity_pct=45.0, now_local=_MIDDAY)
    assert cool_spike is not None and cool_spike > _MIDDAY_BASELINE, (
        "a price spike must warm the setpoint above the comfort baseline toward "
        f"the ceiling; got {cool_spike}"
    )
    assert cool_spike == _CEILING, (
        f"an extreme spike must ride the comfort ceiling {_CEILING}; got {cool_spike}"
    )

    # 2b) RIDE-THE-CEILING (C2): a SUSTAINED extreme spike holds at the ceiling
    #     across consecutive ticks — it does not bounce. Same firing state, so
    #     the tier is held (min-hold) and the clamp pins the effective at
    #     comfort_max tick after tick.
    cool_spike_2 = _run_sustained_spike_rides_ceiling(monkeypatch)
    assert cool_spike_2 == _CEILING, (
        f"a sustained extreme spike must keep riding the ceiling {_CEILING} "
        f"(no bounce); got {cool_spike_2}"
    )

    # 3) Price spike + HIGH HUMIDITY: the humidity guard RELEASES the warm
    #    overlay back to the baseline (asymmetric with the temp ceiling).
    cool_humid, _wa_h, _trip_h = _run_tick(
        monkeypatch, price_cents=50.0, humidity_pct=70.0, now_local=_MIDDAY)
    assert cool_humid == _MIDDAY_BASELINE, (
        "high indoor humidity must release the warm overlay back to the comfort "
        f"baseline; got {cool_humid}"
    )

    # 3b) ASYMMETRY (explicit): same spike price, dry air RIDES to the ceiling
    #     while humid air RELEASES to baseline. Temp rides, humidity releases.
    assert cool_spike == _CEILING and cool_humid == _MIDDAY_BASELINE, (
        "asymmetric guards: a spike rides to the ceiling in dry air but releases "
        f"to baseline in humid air; got dry={cool_spike}, humid={cool_humid}"
    )
    assert cool_spike > cool_humid

    # 4) The decision log records the actual indoor temperature alongside the
    #    commanded setpoint (B3 telemetry reshape).
    logged_actual = any(
        "indoor" in str(c.args).lower() or "indoor" in str(c.kwargs).lower()
        or "actual" in str(c.kwargs).lower()
        for c in wa_n.call_args_list
    )
    assert logged_actual, (
        "the decision log must record the actual indoor temperature alongside "
        "the commanded setpoint (Slice B3 telemetry reshape)"
    )
