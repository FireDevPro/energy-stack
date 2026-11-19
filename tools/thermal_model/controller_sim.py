"""Emulate the rev 4.1 spike-only controller (Arm B) on any thermostat program.

A port of ``hvac_scheduler/controller/tiers.py`` (evaluate_tier) and
``holds.py`` (compute_target / decide), using the live config
(``commissioning-controller.yaml``, unchanged since the 2026-07-05 cutover):

- Tier from the ComEd 5-min price: elevated >= 10c, scarcity >= 20c. Upgrades
  are immediate. Release needs ``release_confirm_buckets`` distinct buckets
  at/below (trigger - hysteresis).
- Warm-only target: elevated = min(program + 1.5C, 29.5C); scarcity = 29.5C.
  No hold when the program is already at/above the target.
- Humidity gate (thermostat RH): blocks at >= 61%, clears < 58%. It only
  updates while the controller needs the device (tier != normal), as in loop.py.
  Blocked means no engage, and a held hold is released.

Simplifications, stated so they can be checked against the action log:
every price bucket is treated as fresh (no stale backstop), every push
succeeds (the real log has 52 failed pushes), and manual holds are not modelled.

The tier sequence depends only on price and humidity, so it is identical
under any program. What changes with the program is the hold *target* and
whether the warm-only precondition lets a hold happen at all.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

NORMAL, ELEVATED, SCARCITY = 0, 1, 2


@dataclass(frozen=True)
class ControllerConfig:
    elevated_at: float = 10.0
    scarcity_at: float = 20.0
    hysteresis: float = 2.0
    elevated_offset_c: float = 1.5
    scarcity_absolute_c: float = 29.5
    release_confirm_buckets: int = 2
    rh_max_pct: float = 61.0
    rh_clear_pct: float = 58.0


def f_to_c_half(f: float) -> float:
    """Deg F -> deg C rounded to the CTK04's 0.5 C step."""
    return round((f - 32) * 5 / 9 * 2) / 2


def c_to_f(c: np.ndarray | float) -> np.ndarray | float:
    return np.asarray(c) * 9 / 5 + 32


def _target_tier(cents: float, cfg: ControllerConfig) -> int:
    if cents >= cfg.scarcity_at:
        return SCARCITY
    if cents >= cfg.elevated_at:
        return ELEVATED
    return NORMAL


def tier_series(price_5min: pd.Series, cfg: ControllerConfig) -> pd.Series:
    """Tier after each 5-min price bucket (evaluate_tier with fresh-strict always true)."""
    tier, confirm = NORMAL, 0
    out = np.empty(len(price_5min), dtype=int)
    for i, cents in enumerate(price_5min.to_numpy(float)):
        target = _target_tier(cents, cfg)
        if target > tier:
            tier, confirm = target, 0
        elif tier != NORMAL:
            release_at = (cfg.scarcity_at if tier == SCARCITY else cfg.elevated_at) - cfg.hysteresis
            if cents <= release_at:
                confirm += 1
                if confirm >= cfg.release_confirm_buckets:
                    tier, confirm = target, 0
            else:
                confirm = 0
        out[i] = tier
    return pd.Series(out, index=price_5min.index)


def humidity_blocked(tiers: pd.Series, rh: pd.Series, cfg: ControllerConfig) -> pd.Series:
    """Humidity gate state per bucket; updated only while tiered (loop.py needs_device)."""
    rh_b = rh.reindex(tiers.index, method="ffill").to_numpy(float)
    blocked = False
    out = np.empty(len(tiers), dtype=bool)
    for i, t in enumerate(tiers.to_numpy()):
        if t != NORMAL:
            r = rh_b[i]
            if not np.isfinite(r) or r >= cfg.rh_max_pct:
                blocked = True
            elif r < cfg.rh_clear_pct:
                blocked = False
        out[i] = blocked
    return pd.Series(out, index=tiers.index)


def commanded_setpoint_c(program_c: pd.Series, tiers: pd.Series, blocked: pd.Series,
                         cfg: ControllerConfig) -> tuple[pd.Series, pd.Series]:
    """Effective cool setpoint (deg C) per bucket, and whether a controller hold is active."""
    prog = program_c.to_numpy(float)
    t = tiers.to_numpy()
    target = np.where(t == SCARCITY, cfg.scarcity_absolute_c,
                      np.minimum(prog + cfg.elevated_offset_c, cfg.scarcity_absolute_c))
    hold = (t != NORMAL) & (prog < target) & ~blocked.to_numpy()
    sp = np.where(hold, target, prog)
    return pd.Series(sp, index=tiers.index), pd.Series(hold, index=tiers.index)


@dataclass
class ControllerRun:
    setpoint_f_15min: pd.Series  # what the thermal simulator consumes
    hold_5min: pd.Series
    tier_5min: pd.Series
    blocked_5min: pd.Series


# ComEd publishes each 5-min price several minutes after the bucket (pricing.py:
# 370-430 s floor, ~11 min ceiling). A 10-min lag reproduces the real hold
# timing at 97% recall / 97% precision (Jaccard 0.94) on Jul 6 - Sep 30 2026.
PUBLISH_LAG_MIN = 10


def run_controller(program_f_by_hour: dict[int, float], price_5min: pd.Series, rh: pd.Series,
                   index_15min: pd.DatetimeIndex, cfg: ControllerConfig | None = None,
                   enabled: bool = True, publish_lag_min: int = PUBLISH_LAG_MIN,
                   day_programs: dict[pd.Timestamp, dict[int, float]] | None = None) -> ControllerRun:
    """Program (deg F by hour, as the CTK04 runs it in 0.5 C steps) plus optional controller,
    returned as a 15-min setpoint series (mean of the three 5-min buckets).

    ``day_programs`` overrides the program on specific local days (keyed by the
    tz-aware local midnight), e.g. a pre-cool program on forecast-flagged days.
    """
    cfg = cfg or ControllerConfig()
    grid = pd.date_range(index_15min[0], index_15min[-1] + pd.Timedelta(minutes=10), freq="5min")
    lagged = price_5min.copy()
    lagged.index = lagged.index + pd.Timedelta(minutes=publish_lag_min)
    px = lagged.reindex(grid, method="ffill").bfill()
    day_programs = day_programs or {}
    days = grid.normalize()
    prog_c = pd.Series([f_to_c_half(day_programs.get(d, program_f_by_hour)[int(h)])
                        for d, h in zip(days, grid.hour, strict=True)], index=grid)
    tiers = tier_series(px, cfg)
    blocked = humidity_blocked(tiers, rh, cfg)
    if enabled:
        sp_c, hold = commanded_setpoint_c(prog_c, tiers, blocked, cfg)
    else:
        sp_c, hold = prog_c, pd.Series(False, index=grid)
    sp_f = pd.Series(c_to_f(sp_c.to_numpy()), index=grid)
    sp15 = sp_f.groupby(sp_f.index.floor("15min")).mean().reindex(index_15min).ffill()
    return ControllerRun(sp15, hold, tiers, blocked)


def actual_hold_5min(actions: pd.DataFrame, grid: pd.DatetimeIndex, ttl_min: int = 30) -> pd.Series:
    """Reconstruct when the real controller held the device, from hvac.actions.

    A hold is active from an applied SPIKE push until the next applied RELEASE,
    or until ``ttl_min`` after the last applied push if nothing extends it.
    """
    a = actions[(actions["applied"] == 1) & actions["action_label"].isin(["SPIKE", "RELEASE"])].sort_values("_time")
    held = np.zeros(len(grid), dtype=bool)
    t = grid.as_unit("ns").asi8  # UTC nanoseconds (pandas 3 date_range defaults to us)
    ttl = ttl_min * 60 * 10**9
    start: int | None = None
    last_push: int | None = None
    for label, ts_obj in zip(a["action_label"], a["_time"], strict=True):
        ts = int(pd.Timestamp(ts_obj).as_unit("ns").value)
        if label == "SPIKE":
            if start is not None and last_push is not None and ts - last_push > ttl:
                held |= (t >= start) & (t < last_push + ttl)  # previous hold lapsed by TTL
                start = None
            if start is None:
                start = ts
            last_push = ts
        elif start is not None:
            held |= (t >= start) & (t < ts)
            start, last_push = None, None
    if start is not None and last_push is not None:
        held |= (t >= start) & (t < last_push + ttl)
    return pd.Series(held, index=grid)


def validate_against_log(program_f_by_hour: dict[int, float], price_5min: pd.Series, rh: pd.Series,
                         actions: pd.DataFrame, index_15min: pd.DatetimeIndex) -> dict[str, float]:
    """Hold-timing agreement between the emulator and the real controller's action log."""
    run = run_controller(program_f_by_hour, price_5min, rh, index_15min)
    emu = run.hold_5min
    act = actual_hold_5min(actions, pd.DatetimeIndex(emu.index))
    both = float((emu & act).sum())
    daily = pd.DataFrame({"emu": emu.resample("D").sum(), "act": act.resample("D").sum()})
    return {
        "emulated_hold_h": float(emu.sum() / 12), "actual_hold_h": float(act.sum() / 12),
        "recall": both / float(act.sum()), "precision": both / float(emu.sum()),
        "jaccard": both / float((emu | act).sum()),
        "daily_hold_corr": float(daily.corr().iloc[0, 1]),
        "emulated_episodes": float((emu.astype(int).diff() == 1).sum()),
        "actual_episodes": float((act.astype(int).diff() == 1).sum()),
    }
