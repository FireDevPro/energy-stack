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

It is marked ``xfail(strict=True)`` and currently XFAILS: Slice A delivers only
the comfort baseline + floor clamp, so the price overlay is telemetry-only and
does not yet warm the setpoint (Slice B), the humidity guard does not exist
(Slice C), and the commanded-vs-actual telemetry reshape is not done (Slice B3).
The marker comes off in Cleanup only when this passes against the real
implementation with zero scaffolding — that is the definition of feature-complete.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

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


@pytest.mark.xfail(
    strict=True,
    reason="commissioning controller incomplete until Slices B (warm price tiers) "
           "+ C (humidity guard + ceiling) + B3 (commanded-vs-actual telemetry)",
)
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

    # 2) Price SPIKE: warm above the baseline toward the ceiling and ride it.
    #    Requires Slice B — XFAILS today (the overlay is telemetry-only, so the
    #    effective setpoint stays at the baseline).
    cool_spike, _wa_s, _trip_s = _run_tick(
        monkeypatch, price_cents=50.0, humidity_pct=45.0, now_local=_MIDDAY)
    assert cool_spike is not None and cool_spike > _MIDDAY_BASELINE, (
        "a price spike must warm the setpoint above the comfort baseline toward "
        f"the ceiling (Slice B); got {cool_spike}"
    )

    # 3) Price spike + HIGH HUMIDITY: the humidity guard releases the warm overlay
    #    back to the baseline. Requires Slice C.
    cool_humid, _wa_h, _trip_h = _run_tick(
        monkeypatch, price_cents=50.0, humidity_pct=70.0, now_local=_MIDDAY)
    assert cool_humid == _MIDDAY_BASELINE, (
        "high indoor humidity must release the warm overlay back to the comfort "
        f"baseline (Slice C); got {cool_humid}"
    )

    # 4) The decision log records the actual indoor temperature alongside the
    #    commanded setpoint. Requires the Slice B3 telemetry reshape.
    logged_actual = any(
        "indoor" in str(c.args).lower() or "indoor" in str(c.kwargs).lower()
        or "actual" in str(c.kwargs).lower()
        for c in wa_n.call_args_list
    )
    assert logged_actual, (
        "the decision log must record the actual indoor temperature alongside "
        "the commanded setpoint (Slice B3 telemetry reshape)"
    )
