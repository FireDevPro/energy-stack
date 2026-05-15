#!/usr/bin/env python
"""Path C commissioning: exercise every decision-trace event type via
controlled synthetic inputs through real production functions. Run inside
the hvac-scheduler container with stdout redirected to /proc/1/fd/1 so
promtail scrapes the emitted trace lines into Loki.

Run inside the container:

  docker exec hvac-scheduler bash -c \
    "python /tmp/commission_decision_trace_path_c.py 1>>/proc/1/fd/1"

Scope (what this script DOES):
  * Exercise the real `decide_day_type`, `resolve_layer_priority`,
    `validate_setpoints`, `compute_price_aware_precool_window` rule
    functions with synthetic in-memory inputs.
  * Emit each event type via its real `_trace_*` helper so the trace
    line shape, level filtering, and JSON serialisation all run
    through production code.
  * Tag every emitted trace with `tick_id` prefixed
    `commission_<ISO-UTC>_<event>_<scenario>` so the lines are
    filterable in Loki forever.

Out of scope (what this script does NOT do):
  * NOT run the scheduler's tick loop. `_evaluate_layer_inputs` +
    `_push_layer_change_mid_period` wiring under live `FiringState`
    state is NOT exercised -- that's Path B1's job, supplementary.
  * NOT write to InfluxDB. No analysis-bound measurements touched.
  * NOT modify the running scheduler. Separate Python process with
    its own in-memory state and its own `app` module instance.

Skipped event type: `decision_trace.price_overlay_eval`. Already
verified live via ambient operation (firing every minute at real
ComEd prices, debug level, all fields correct per the post-Phase-1
verification on 2026-05-14). Re-testing the classifier here would
duplicate ~30 lines of inline emission code from
`_evaluate_layer_inputs`. Documented in the findings as "verified
live via ambient operation, not re-tested in Path C."
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

# SCHEDULER_MODE is already `shadow` in the container env; importing
# `app` passes `_validate_scheduler_mode_or_exit()`.
sys.path.insert(0, "/app")
os.environ["SCHEDULER_DECISION_TRACE_VERBOSE"] = "true"

import app  # noqa: E402
from app import (  # noqa: E402
    FiringState,
    LayerInputs,
    _trace_day_type,
    _trace_layer_resolution,
    _trace_precool,
    _trace_supervisor,
    compute_price_aware_precool_window,
    decide_day_type,
    resolve_layer_priority,
)
from safety_supervisor import validate_setpoints  # noqa: E402

CT = ZoneInfo("America/Chicago")
NOW = datetime.now(CT)
NOW_UTC_TAG = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
TICK_PREFIX = f"commission_{NOW_UTC_TAG}_"


def narrate(msg: str) -> None:
    """Narration only -- goes to stderr so the operator sees progress
    in the SSH session. Trace JSON goes to stdout (which gets routed to
    PID 1's stdout by the shell-level redirect)."""
    print(f"[path-c] {msg}", file=sys.stderr, flush=True)


narrate(f"start. tick_id prefix: {TICK_PREFIX}")


# ---------- decision_trace.day_type_decision ----------
narrate("=== decision_trace.day_type_decision ===")
DAY_TYPE_SCENARIOS = [
    (
        "hot_high_ge_85",
        {"high_f": 90.0, "apparent_max_f": 88.0, "is_heat_advisory": 0,
         "max_dewpoint_f": 65.0, "alert_summary": ""},
        None,
    ),
    (
        "hot_apparent_only",
        {"high_f": 82.0, "apparent_max_f": 95.0, "is_heat_advisory": 0,
         "max_dewpoint_f": 70.0, "alert_summary": ""},
        None,
    ),
    (
        "hot_heat_advisory",
        {"high_f": 70.0, "apparent_max_f": 72.0, "is_heat_advisory": 1,
         "max_dewpoint_f": 55.0, "alert_summary": "Heat advisory"},
        None,
    ),
    (
        "hot_streak_multi_day",
        {"high_f": 92.0, "apparent_max_f": 95.0, "is_heat_advisory": 0,
         "max_dewpoint_f": 70.0, "alert_summary": ""},
        {"high_f": 88.0, "apparent_max_f": 90.0, "is_heat_advisory": 0,
         "max_dewpoint_f": 68.0},
    ),
    (
        "normal_high_75_to_84",
        {"high_f": 80.0, "apparent_max_f": 82.0, "is_heat_advisory": 0,
         "max_dewpoint_f": 60.0, "alert_summary": ""},
        None,
    ),
    (
        "normal_missing_temps",
        {"is_heat_advisory": 0, "max_dewpoint_f": 60.0, "alert_summary": ""},
        None,
    ),
    (
        "normal_no_forecast",
        None, None,
    ),
    (
        "mild_high_lt_75",
        {"high_f": 70.0, "apparent_max_f": 72.0, "is_heat_advisory": 0,
         "max_dewpoint_f": 50.0, "alert_summary": ""},
        None,
    ),
]
for name, forecast, day2 in DAY_TYPE_SCENARIOS:
    day_type, reasons = decide_day_type(forecast, day2_forecast=day2)
    _trace_day_type(
        tick_id=TICK_PREFIX + "dt_" + name, now_ct=NOW,
        decision_for_date="2026-05-15",
        winning_day_type=day_type, reasons=reasons,
    )
    narrate(f"  {name} -> {day_type}")


# ---------- decision_trace.layer_resolution ----------
narrate("=== decision_trace.layer_resolution ===")
LAYER_SCENARIOS = [
    # (name, schedule_cool, price_tier, offset, override, fivecp_active)
    ("schedule_wins", 78, "normal", 0, None, False),
    ("price_overlay_wins_elevated", 75, "elevated", 3, None, False),
    ("price_overlay_wins_scarcity", 75, "scarcity", 0, 85, False),
    ("fivecp_wins", 75, "normal", 0, None, True),
    ("tie_warmer_wins", 75, "scarcity", 0, 85, True),
]
for name, schedule_cool, price_tier, price_offset, price_override, fivecp_active in LAYER_SCENARIOS:
    lr = resolve_layer_priority(
        schedule_cool,
        price_overlay_tier=price_tier,
        price_offset_f=price_offset,
        price_override_f=price_override,
        fivecp_active=fivecp_active,
    )
    li = LayerInputs(
        price_tier_name=price_tier,
        price_offset_f=price_offset,
        price_override_f=price_override,
        price_prev_tier=price_tier,
        current_price_cents=10.0 if price_tier != "normal" else 5.0,
        fivecp_active=fivecp_active,
        fivecp_scopes_fired=("comed_zone",) if fivecp_active else (),
        fivecp_load_mw=20000.0 if fivecp_active else 0.0,
        fivecp_derivative=0.5 if fivecp_active else 0.0,
        fivecp_forecast_peak=21000.0 if fivecp_active else 0.0,
        fivecp_season_5th_mw=20375.0,
        fivecp_data_available=True,
    )
    _trace_layer_resolution(
        tick_id=TICK_PREFIX + "lr_" + name, now_ct=NOW,
        firing=FiringState(),  # fresh per scenario so level=info fires on each
        layer_resolution=lr, layer_inputs=li,
    )
    narrate(f"  {name}: effective_cool_f={lr.effective_cool_f}")


# ---------- decision_trace.supervisor ----------
narrate("=== decision_trace.supervisor ===")
SUPERVISOR_SCENARIOS = [
    # (name, proposed_cool, proposed_heat, snapshot)
    ("approved", 78, 68, {"indoor_temp_f": 72.0}),
    ("approved_no_indoor", 78, 68, {}),
    ("clamped_cool_floor", 60, 68, {"indoor_temp_f": 72.0}),
    ("clamped_cool_ceiling", 90, 68, {"indoor_temp_f": 72.0}),
    ("clamped_heat_floor", 78, 50, {"indoor_temp_f": 72.0}),
    ("clamped_heat_ceiling", 78, 80, {"indoor_temp_f": 72.0}),
    ("clamped_multiple", 90, 50, {"indoor_temp_f": 72.0}),
    ("emergency_overheat", 85, 68, {"indoor_temp_f": 87.0}),
]
for name, proposed_cool, proposed_heat, snapshot in SUPERVISOR_SCENARIOS:
    decision = validate_setpoints(proposed_cool, proposed_heat, snapshot)
    _trace_supervisor(
        tick_id=TICK_PREFIX + "sup_" + name, now_ct=NOW,
        proposed_cool_f=proposed_cool, proposed_heat_f=proposed_heat,
        snapshot=snapshot, decision=decision,
    )
    narrate(f"  {name}: decision={decision.decision}")


# ---------- decision_trace.precool_decision ----------
narrate("=== decision_trace.precool_decision ===")
PRECOOL_SCENARIOS = [
    # (name, prices, forecast)
    ("no_da_lmp_data", None, None),
    ("no_forecast", [3.0] * 24, None),
    ("da_lmp_incomplete", [3.0] * 20, {"high_f": 80.0}),
    ("no_cheap_window", [9.0] * 24, {"high_f": 80.0}),
    (
        "no_spike_window_after_gap",
        [9.0] * 6 + [2.0] * 6 + [5.0] * 12,
        {"high_f": 80.0},
    ),
    (
        "selected",
        [9.0] * 6 + [2.0] * 6 + [5.0] * 5 + [15.0] * 4 + [5.0] * 3,
        {"high_f": 92.0},
    ),
]
for name, prices, forecast in PRECOOL_SCENARIOS:
    # Monkey-patch the fetch helpers ONLY in this process. The running
    # scheduler's `app` module instance is separate and untouched.
    app.fetch_day_ahead_prices_for_date = lambda *a, _p=prices, **kw: _p
    app.fetch_latest_forecast = lambda *a, _f=forecast, **kw: _f
    trace_reason: list[str] = []
    window = compute_price_aware_precool_window(
        MagicMock(), "energy", "2026-05-15", CT,
        forecast_period="tomorrow", trace_reason=trace_reason,
    )
    reason_code = trace_reason[-1] if trace_reason else "UNKNOWN_NO_TRACE_REASON"
    _trace_precool(
        tick_id=TICK_PREFIX + "pc_" + name, now_ct=NOW,
        decision_for_date="2026-05-15", day_type="MILD",
        window=window, reason_code=reason_code,
    )
    narrate(f"  {name}: reason={reason_code}")


narrate(f"done. tick_id prefix: {TICK_PREFIX}")
