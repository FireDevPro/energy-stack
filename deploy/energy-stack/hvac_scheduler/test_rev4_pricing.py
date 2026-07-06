from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .controller.pricing import (
    FRESH_STRICT_MAX_AGE_SEC, PriceSample, fetch_price, is_fresh_strict,
)

UTC = timezone.utc


def test_fresh_strict_boundary():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    ok = PriceSample(5.0, now - timedelta(seconds=719), 719)
    old = PriceSample(5.0, now - timedelta(seconds=721), 721)
    assert is_fresh_strict(ok) and not is_fresh_strict(old)
    assert FRESH_STRICT_MAX_AGE_SEC == 720.0


class _Rec:
    def __init__(self, t, v): self._t, self._v = t, v
    def get_time(self): return self._t
    def get_value(self): return self._v


class _Table:
    def __init__(self, recs): self.records = recs


class _QueryApi:
    def __init__(self, recs): self._recs = recs
    def query(self, flux): return [_Table(self._recs)] if self._recs else []


def test_fetch_returns_latest_bucket_with_age():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    t = now - timedelta(seconds=430)
    api = _QueryApi([_Rec(t, 12.8)])
    s = fetch_price(api, "energy", now)
    assert s is not None and s.cents == 12.8 and s.age_sec == 430.0


def test_fetch_none_when_empty():
    assert fetch_price(_QueryApi([]), "energy", datetime.now(UTC)) is None
