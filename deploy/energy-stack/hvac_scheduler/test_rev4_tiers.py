from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .controller.config import load_config
from .controller.pricing import PriceSample
from .controller.tiers import TierState, evaluate_tier

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


def _s(cents, now, age=400.0):
    return PriceSample(cents, now - timedelta(seconds=age), age)


def test_engage_on_one_fresh_bucket(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, reason = evaluate_tier(TierState(), _s(12.8, now), cfg, now)
    assert st.tier == "elevated" and reason == "REV4_UPGRADED_TO_ELEVATED"
    st, reason = evaluate_tier(st, _s(40.0, now), cfg, now)
    assert st.tier == "scarcity" and reason == "REV4_UPGRADED_TO_SCARCITY"


def test_engage_blocked_when_not_fresh(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, reason = evaluate_tier(TierState(), _s(50.0, now, age=900), cfg, now)
    assert st.tier == "normal" and reason == "REV4_ENGAGE_BLOCKED_NOT_FRESH"


def test_release_needs_two_distinct_buckets(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(25.0, now), cfg, now)
    assert st.tier == "scarcity"
    # one cheap bucket, seen on three consecutive 1-min ticks: ONE confirmation
    cheap = _s(4.0, now + timedelta(minutes=5))
    for i in range(3):
        st, reason = evaluate_tier(st, cheap, cfg, now + timedelta(minutes=5 + i))
    assert st.tier == "scarcity" and reason == "REV4_RELEASE_CONFIRMING"
    # a SECOND distinct cheap bucket -> released to normal
    cheap2 = PriceSample(3.5, cheap.bucket_time_utc + timedelta(minutes=5), 400.0)
    st, reason = evaluate_tier(st, cheap2, cfg, now + timedelta(minutes=10))
    assert st.tier == "normal" and reason == "REV4_RELEASED_TO_NORMAL"


def test_stepdown_to_elevated_via_confirmations(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(40.0, now), cfg, now)
    b1 = _s(15.0, now + timedelta(minutes=5))     # < 18 (scarcity release), >= 10
    st, _ = evaluate_tier(st, b1, cfg, now + timedelta(minutes=5))
    b2 = PriceSample(15.5, b1.bucket_time_utc + timedelta(minutes=5), 400.0)
    st, reason = evaluate_tier(st, b2, cfg, now + timedelta(minutes=10))
    assert st.tier == "elevated" and reason == "REV4_DOWNGRADED_TO_ELEVATED"


def test_price_back_above_release_resets_confirmations(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(25.0, now), cfg, now)
    st, _ = evaluate_tier(st, _s(4.0, now + timedelta(minutes=5)), cfg, now + timedelta(minutes=5))
    assert st.confirm_count == 1
    st, reason = evaluate_tier(st, _s(30.0, now + timedelta(minutes=10)), cfg, now + timedelta(minutes=10))
    assert st.confirm_count == 0 and st.tier == "scarcity" and reason == "REV4_HELD_IN_TIER"


def test_stale_backstop_releases_after_30_min_without_fresh(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(25.0, now), cfg, now)
    later = now + timedelta(minutes=31)
    stale = PriceSample(25.0, now - timedelta(seconds=400), (later - (now - timedelta(seconds=400))).total_seconds())
    st, reason = evaluate_tier(st, stale, cfg, later)
    assert st.tier == "normal" and reason == "REV4_RELEASED_STALE_BACKSTOP"


def test_hysteresis_band_holds_tier(tmp_path):
    cfg, now = _cfg(tmp_path), datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    st, _ = evaluate_tier(TierState(), _s(25.0, now), cfg, now)
    # 19c: below scarcity trigger (20) but at/above release threshold (18) -> hold
    st, reason = evaluate_tier(st, _s(19.0, now + timedelta(minutes=5)), cfg, now + timedelta(minutes=5))
    assert st.tier == "scarcity" and reason == "REV4_HELD_IN_TIER" and st.confirm_count == 0
