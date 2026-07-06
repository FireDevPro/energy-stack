from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .controller.config import load_config
from .controller.holds import compute_target, decide, hold_until_minutes

UTC = timezone.utc


def _cfg(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "temp_scale: C\n"
        "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
        "elevated_offset: 1.5\nscarcity_absolute: 29.5\nheat_floor: 18.5\n"
        "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
        "hold_ttl_minutes: 30\nrelease_confirm_buckets: 2\nstale_release_minutes: 30\n",
        encoding="utf-8")
    return load_config(str(p), temp_scale_env="C")


@dataclass
class Snap:
    schedule_cool: float | None = 25.5
    cool_setpoint: float = 25.5
    hold_active: bool = False
    hold_until_minutes: int | None = None
    humidity: float | None = 45.0


@dataclass
class Own:
    value: float
    until_minutes: int
    expiry_utc: str


def test_targets_and_precondition(tmp_path):
    cfg = _cfg(tmp_path)
    assert compute_target("elevated", 25.5, cfg) == 27.0
    assert compute_target("scarcity", 25.5, cfg) == 29.5
    assert compute_target("scarcity", 30.0, cfg) is None   # program >= absolute
    assert compute_target("normal", 25.5, cfg) is None
    assert compute_target("elevated", 29.0, cfg) == 29.5   # clamped to absolute, still > program
    assert compute_target("elevated", 29.5, cfg) is None   # program == absolute: nothing warmer to command


def test_hold_until_quarter_floor():
    now = datetime(2026, 7, 10, 14, 7, tzinfo=UTC)      # 14:07 + 30 = 14:37 -> 14:30
    assert hold_until_minutes(now, 30) == 14 * 60 + 30
    now2 = datetime(2026, 7, 10, 23, 50, tzinfo=UTC)    # 23:50 + 30 = 00:20 -> 00:15 (wraps)
    assert hold_until_minutes(now2, 30) == 15


def test_engage_and_no_schedule_read(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    kind, cool, until, reason = decide("elevated", Snap(), None, cfg, now, now, False)
    assert kind == "push" and cool == 27.0 and until is not None and until % 15 == 0
    kind, *_ , reason = decide("elevated", Snap(schedule_cool=None), None, cfg, now, now, False)
    assert kind == "none" and reason == "REV4_NO_SCHEDULE_READ"


def test_manual_hold_respected_warmward_only(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    warm_manual = Snap(hold_active=True, hold_until_minutes=999, cool_setpoint=30.0)
    kind, *_ , reason = decide("scarcity", warm_manual, None, cfg, now, now, False)
    assert kind == "none" and reason == "REV4_MANUAL_HOLD_RESPECTED"
    cool_manual = Snap(hold_active=True, hold_until_minutes=999, cool_setpoint=22.0)
    kind, cool, _, _ = decide("scarcity", cool_manual, None, cfg, now, now, False)
    assert kind == "push" and cool == 29.5


def test_own_hold_correct_extend_and_warm_only_release(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    own = Own(value=27.0, until_minutes=hold_until_minutes(now, 30),
              expiry_utc=(now + timedelta(minutes=25)).isoformat())
    held = Snap(hold_active=True, hold_until_minutes=own.until_minutes, cool_setpoint=27.0)
    # same target, far from expiry -> none
    kind, *_ = decide("elevated", held, own, cfg, now, now, False)
    assert kind == "none"
    # near expiry -> extend (same value, new until)
    near = now + timedelta(minutes=22)
    kind, cool, until, reason = decide("elevated", held, own, cfg, near, near, False)
    assert kind == "push" and cool == 27.0 and reason == "REV4_EXTENDED"
    # humidity blocked -> stop extending
    kind, *_ , reason = decide("elevated", held, own, cfg, near, near, True)
    assert kind == "none" and reason == "REV4_HUMIDITY_STOP_EXTEND"
    # program stepped ABOVE held value and target invalid -> immediate release
    stepped = Snap(schedule_cool=29.5, hold_active=True,
                   hold_until_minutes=own.until_minutes, cool_setpoint=27.0)
    kind, *_ , reason = decide("scarcity", stepped, own, cfg, now, now, False)
    assert kind == "release" and reason == "REV4_WARM_ONLY_RELEASE"
    # program changed, valid new target -> corrected push on this tick
    stepped2 = Snap(schedule_cool=23.0, hold_active=True,
                    hold_until_minutes=own.until_minutes, cool_setpoint=27.0)
    kind, cool, _, reason = decide("elevated", stepped2, own, cfg, now, now, False)
    assert kind == "push" and cool == 24.5 and reason == "REV4_CORRECTED"
    # ...but humidity blocks corrective pushes too (only warm-only release acts)
    kind, *_ , reason = decide("elevated", stepped2, own, cfg, now, now, True)
    assert kind == "none" and reason == "REV4_HUMIDITY_STOP_EXTEND"


def test_zombie_cleanup_only_matching_and_expired(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    own = Own(value=27.0, until_minutes=870, expiry_utc=(now - timedelta(hours=1)).isoformat())
    zombie = Snap(hold_active=True, hold_until_minutes=870, cool_setpoint=27.0)
    kind, *_ , reason = decide("normal", zombie, own, cfg, now, now, False)
    assert kind == "release" and reason == "REV4_ZOMBIE_RELEASED"
    # non-matching hold: never touched
    foreign = Snap(hold_active=True, hold_until_minutes=915, cool_setpoint=27.0)
    kind, *_ = decide("normal", foreign, own, cfg, now, now, False)
    assert kind == "none"
    # matching but not yet past expiry+grace: leave alone
    own_live = Own(value=27.0, until_minutes=870,
                   expiry_utc=(now + timedelta(minutes=5)).isoformat())
    kind, *_ = decide("normal", zombie, own_live, cfg, now, now, False)
    assert kind == "none"
