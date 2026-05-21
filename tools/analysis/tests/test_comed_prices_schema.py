"""ComEd RTP price loader schema tests against production poller shape.

Real-data finding from the 2026-05-12 replay-validation run:
``tools/analysis/queries/comed.prices.flux`` filtered on
``_field == "price_cents"``, but the production poller
(``deploy/energy-stack/comed_poller/poller.py``) writes
``price_cents_per_kwh`` with a ``period_type`` tag of either
``5min`` (raw RTP cadence) or ``hourly_avg`` (poller-computed
hourly mean).

The Stage 2 and Stage 3 loaders had the same field-name mismatch.
Result: comed.prices appeared empty in real-data export even though
Influx had thousands of 5-min rows.

Fix contract pinned here:

- Stage 2 Rule 3 (``_hourly_price_observation_counts``) counts ONLY
  ``period_type=5min`` rows. Raw cadence is the right source for
  coverage logic; the poller's hourly_avg roll-up would smear coverage.
- Stage 3 (``_stage3_hourly_supply_prices``) computes the hourly
  supply price as the MEAN of the available ``period_type=5min``
  observations within each hour. The analysis pipeline owns the
  aggregation math; the poller's ``hourly_avg`` row is NOT trusted
  as the primary input. Audit chain stays shorter for OSF
  reproducibility.

Optional cross-check (not enforced here): a follow-on helper can
compare the 5-min mean against the poller's hourly_avg row and
report mismatches beyond a small tolerance.
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from tools.analysis import pipeline


def _build_comed_prices_df(rows: list[dict]) -> pd.DataFrame:
    """Long-format comed.prices DataFrame matching production schema.

    Each row: {ts: tz-aware UTC, period_type: '5min'|'hourly_avg',
    price_cents: float}. Emits ``_field=price_cents_per_kwh`` with
    the appropriate tag.
    """
    long_rows = []
    for r in rows:
        long_rows.append({
            "_time": r["ts"],
            "_measurement": "comed.prices",
            "_field": "price_cents_per_kwh",
            "_value": float(r["price_cents"]),
            "period_type": r["period_type"],
        })
    df = pd.DataFrame(long_rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


# -- Stage 3: _stage3_hourly_supply_prices reads price_cents_per_kwh ----


def test_stage3_supply_prices_reads_5min_rows_not_legacy_field():
    """Production-shape rows tagged price_cents_per_kwh + period_type=5min
    must be picked up. Rows with the legacy price_cents field name are
    ignored (no such field in production)."""
    week_start_ct = datetime.date(2026, 5, 4)  # Monday
    week_start_utc = datetime.datetime(2026, 5, 4, 5, 0, tzinfo=datetime.timezone.utc)
    # 12 5-min rows at the top of week, all 4.0 cents.
    rows = []
    for i in range(12):
        ts = week_start_utc + datetime.timedelta(minutes=5 * i)
        rows.append({"ts": ts, "period_type": "5min", "price_cents": 4.0})
    df = _build_comed_prices_df(rows)

    prices = pipeline._stage3_hourly_supply_prices(df, week_start_ct)
    assert len(prices) == 168
    # Hour 0 covers the first 5-min window; mean of 12 × 4.0¢ = 4.0¢.
    assert prices[0] == pytest.approx(4.0)
    # Other hours have no rows → 0.0.
    assert prices[1] == 0.0


def test_stage3_supply_prices_ignores_hourly_avg_period_type():
    """The poller writes hourly_avg rows alongside 5min. Stage 3 must
    NOT use hourly_avg; the analysis pipeline owns the aggregation.

    Oracle: 5min mean is 4.0¢, hourly_avg row says 8.0¢. Loader returns
    4.0¢ — proving it uses 5min, not hourly_avg.
    """
    week_start_ct = datetime.date(2026, 5, 4)
    week_start_utc = datetime.datetime(2026, 5, 4, 5, 0, tzinfo=datetime.timezone.utc)
    rows = []
    # 12 × 4.0¢ at 5min cadence
    for i in range(12):
        ts = week_start_utc + datetime.timedelta(minutes=5 * i)
        rows.append({"ts": ts, "period_type": "5min", "price_cents": 4.0})
    # Plus one hourly_avg row at hour 0 with a DIFFERENT value
    rows.append({
        "ts": week_start_utc, "period_type": "hourly_avg", "price_cents": 8.0,
    })
    df = _build_comed_prices_df(rows)

    prices = pipeline._stage3_hourly_supply_prices(df, week_start_ct)
    # If the loader incorrectly mixes hourly_avg into the mean, the
    # answer would be the mean of [4.0]*12 + [8.0] = 4.31¢. Pinning
    # at exactly 4.0¢ proves only 5min rows were used.
    assert prices[0] == pytest.approx(4.0)


def test_stage3_supply_prices_means_variable_5min_rows():
    """Twelve 5-min observations with varying prices (1, 2, ..., 12)
    yield mean = 6.5¢ for that hour. Distinguishes mean from sum
    (sum would be 78)."""
    week_start_ct = datetime.date(2026, 5, 4)
    week_start_utc = datetime.datetime(2026, 5, 4, 5, 0, tzinfo=datetime.timezone.utc)
    rows = []
    for i in range(12):
        ts = week_start_utc + datetime.timedelta(minutes=5 * i)
        rows.append({"ts": ts, "period_type": "5min", "price_cents": float(i + 1)})
    df = _build_comed_prices_df(rows)

    prices = pipeline._stage3_hourly_supply_prices(df, week_start_ct)
    assert prices[0] == pytest.approx(6.5)


# -- Stage 2 Rule 3: _hourly_price_observation_counts counts 5min ----


def test_stage2_rule3_counts_only_5min_rows():
    """Rule 3 coverage check should count only period_type=5min rows.
    hourly_avg rows must be ignored — they roll up coverage and
    would obscure missing-data periods."""
    week_start_ct = datetime.date(2026, 5, 4)
    week_start_utc = datetime.datetime(2026, 5, 4, 5, 0, tzinfo=datetime.timezone.utc)
    rows = []
    # 6 × 5min observations in hour 0
    for i in range(6):
        ts = week_start_utc + datetime.timedelta(minutes=5 * i)
        rows.append({"ts": ts, "period_type": "5min", "price_cents": 4.0})
    # 1 hourly_avg row in same hour — must NOT be counted
    rows.append({
        "ts": week_start_utc, "period_type": "hourly_avg", "price_cents": 4.0,
    })
    df = _build_comed_prices_df(rows)

    counts = pipeline._hourly_price_observation_counts(df, week_start_ct)
    assert counts[0]["observed_prints"] == 6
    assert counts[1]["observed_prints"] == 0


def test_stage2_rule3_full_coverage_12_per_hour():
    """A complete week of 5min observations: 168 hours × 12 = 2016
    observations. Every hour reports 12."""
    week_start_ct = datetime.date(2026, 5, 4)
    week_start_utc = datetime.datetime(2026, 5, 4, 5, 0, tzinfo=datetime.timezone.utc)
    rows = []
    for h in range(168):
        for m in range(12):
            ts = week_start_utc + datetime.timedelta(hours=h, minutes=5 * m)
            rows.append({"ts": ts, "period_type": "5min", "price_cents": 4.0})
    df = _build_comed_prices_df(rows)

    counts = pipeline._hourly_price_observation_counts(df, week_start_ct)
    assert all(c["observed_prints"] == 12 for c in counts)


# -- Flux query string check ----


def test_flux_query_filters_price_cents_per_kwh_field():
    """The on-disk flux template must filter on the production field
    name. Belt-and-suspenders against a future regression that
    reverts to 'price_cents'."""
    from pathlib import Path
    flux_path = (
        Path(__file__).resolve().parents[1]
        / "queries" / "comed.prices.flux"
    )
    text = flux_path.read_text()
    assert 'r._field == "price_cents_per_kwh"' in text
    assert 'r._field == "price_cents"' not in text
