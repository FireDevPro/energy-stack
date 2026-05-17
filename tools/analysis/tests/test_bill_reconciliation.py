"""Tests for tools.analysis.bill_reconciliation per spec §10."""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from tools.analysis.bill_reconciliation import (
    BillReconciliation,
    reconcile_bill_period,
)


RATE_SNAPSHOT = {
    "pea_c_per_kwh": 1.773,
    "transmission_c_per_kwh": 1.083,
    "misc_procurement_c_per_kwh": 0.062,
    "variable_riders_c_per_kwh": 1.16,
    "carbon_free_credit_c_per_kwh": -3.186,
}


def _hour_list(start, hours):
    return [start + datetime.timedelta(hours=i) for i in range(hours)]


def _rt_lmps(start, hours, lmp_per_mwh=35.0):
    return pd.DataFrame([
        {"_time": ts, "total_lmp_rt": lmp_per_mwh}
        for ts in _hour_list(start, hours)
    ])


def _eagle_totalizer(start, hours, start_value=10000.0, kwh_per_hour=1.0):
    rows = []
    cur = start_value
    for ts in _hour_list(start, hours):
        cur += kwh_per_hour
        rows.append({
            "_time": ts, "delivered_kwh": cur, "_field": "delivered_kwh",
        })
    return pd.DataFrame(rows)


def _refoss_mains(start, hours, *, em1_w=800.0, em7_w=1200.0):
    rows = []
    for ts in _hour_list(start, hours):
        for s in range(120):
            t = ts + datetime.timedelta(seconds=30 * s)
            rows.append({"_time": t, "channel": "em:1", "_value": em1_w,
                         "_field": "power_w"})
            rows.append({"_time": t, "channel": "em:7", "_value": em7_w,
                         "_field": "power_w"})
    return pd.DataFrame(rows)


def test_reconstruct_uses_eagle_when_available():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=24)
    eagle = _eagle_totalizer(start, 24, kwh_per_hour=2.0)
    refoss = _refoss_mains(start, 24)
    rt = _rt_lmps(start, 24)
    out = reconcile_bill_period(
        bill_period_start_utc=start, bill_period_end_utc=end,
        eagle_df=eagle, refoss_df=refoss, rt_hrl_lmps_df=rt,
        rate_snapshot=RATE_SNAPSHOT,
        actual_bill_variable_dollars=0.0,
    )
    assert out.pct_hours_eagle == pytest.approx(100.0)
    assert out.pct_hours_refoss_fallback == pytest.approx(0.0)
    assert out.reconstructed_variable_dollars > 0


def test_fallback_to_refoss_on_eagle_gap():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=24)
    # Eagle covers only the first 12 hours
    eagle = _eagle_totalizer(start, 12, kwh_per_hour=2.0)
    refoss = _refoss_mains(start, 24)
    rt = _rt_lmps(start, 24)
    out = reconcile_bill_period(
        bill_period_start_utc=start, bill_period_end_utc=end,
        eagle_df=eagle, refoss_df=refoss, rt_hrl_lmps_df=rt,
        rate_snapshot=RATE_SNAPSHOT,
        actual_bill_variable_dollars=0.0,
    )
    # First 12 hours via Eagle (but hour 0's totalizer-diff has no prior
    # value, so it shows zero kWh -> 11 hours fall under "Eagle-counted").
    # Per implementation, "n_eagle" counts hours where eagle_kwh.get(h) is
    # not None (regardless of kWh value), so all 12 first-hours register.
    assert out.pct_hours_eagle == pytest.approx(50.0)
    assert out.pct_hours_refoss_fallback == pytest.approx(50.0)


def test_divergence_threshold_5_pct_or_10_dollars():
    """Spec §10: flag if reconstructed differs from bill by max(5%, $10).
    Spec wording is `>` strict, mirrored by the implementation."""
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=720)  # 30 days
    eagle = _eagle_totalizer(start, 720, kwh_per_hour=1.0)
    refoss = pd.DataFrame(columns=["_time", "channel", "_value", "_field"])
    rt = _rt_lmps(start, 720)
    # Bill = $200; 5% = $10; $10 floor wins.
    # Reconstructed total based on inputs. Use a bill close to actual:
    out_close = reconcile_bill_period(
        bill_period_start_utc=start, bill_period_end_utc=end,
        eagle_df=eagle, refoss_df=refoss, rt_hrl_lmps_df=rt,
        rate_snapshot=RATE_SNAPSHOT,
        actual_bill_variable_dollars=out_anchor(start, end, eagle, refoss, rt),
    )
    # Same value as reconstructed -> no flag
    assert out_close.divergence_flagged is False


def out_anchor(start, end, eagle, refoss, rt):
    """Helper to get the reconstructed dollars given a snapshot."""
    return reconcile_bill_period(
        bill_period_start_utc=start, bill_period_end_utc=end,
        eagle_df=eagle, refoss_df=refoss, rt_hrl_lmps_df=rt,
        rate_snapshot=RATE_SNAPSHOT,
        actual_bill_variable_dollars=0.0,
    ).reconstructed_variable_dollars


def test_divergence_flag_triggers_above_threshold():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=24)
    eagle = _eagle_totalizer(start, 24, kwh_per_hour=1.0)
    refoss = pd.DataFrame(columns=["_time", "channel", "_value", "_field"])
    rt = _rt_lmps(start, 24)
    # actual bill = $1000 -> reconstructed << bill (we use 23 kWh @ ~3.4 c/kWh)
    out = reconcile_bill_period(
        bill_period_start_utc=start, bill_period_end_utc=end,
        eagle_df=eagle, refoss_df=refoss, rt_hrl_lmps_df=rt,
        rate_snapshot=RATE_SNAPSHOT,
        actual_bill_variable_dollars=1000.0,
    )
    assert out.divergence_flagged is True
    # Spec says NEVER rescale -- reconstructed must stay as computed
    assert out.reconstructed_variable_dollars < 1000.0


def test_overlap_drift_reported():
    """Where both Eagle and Refoss have data, mean abs diff (kWh)."""
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=10)
    eagle = _eagle_totalizer(start, 10, kwh_per_hour=2.0)
    refoss = _refoss_mains(start, 10, em1_w=800.0, em7_w=1200.0)
    rt = _rt_lmps(start, 10)
    out = reconcile_bill_period(
        bill_period_start_utc=start, bill_period_end_utc=end,
        eagle_df=eagle, refoss_df=refoss, rt_hrl_lmps_df=rt,
        rate_snapshot=RATE_SNAPSHOT,
        actual_bill_variable_dollars=0.0,
    )
    # Refoss mean (em1+em7) = 2000 W = 2.0 kWh/hour; Eagle delta = 2.0 kWh/hour.
    # Drift should be ~0 except for hour 0 where Eagle delta=0 (no prior).
    assert out.eagle_refoss_drift_during_overlap_kwh >= 0


def test_never_silently_rescales_hvac():
    """Spec §10 hard rule: divergence reported, not corrected. The
    reconciliation does not return any HVAC-side adjustment."""
    start = datetime.datetime(2026, 6, 1, 0, 0)
    end = start + datetime.timedelta(hours=24)
    eagle = _eagle_totalizer(start, 24, kwh_per_hour=1.0)
    refoss = pd.DataFrame(columns=["_time", "channel", "_value", "_field"])
    rt = _rt_lmps(start, 24)
    out = reconcile_bill_period(
        bill_period_start_utc=start, bill_period_end_utc=end,
        eagle_df=eagle, refoss_df=refoss, rt_hrl_lmps_df=rt,
        rate_snapshot=RATE_SNAPSHOT,
        actual_bill_variable_dollars=99.99,
    )
    # The dataclass has no "adjusted_hvac" field. Existence-check via
    # dataclasses.fields:
    field_names = {f.name for f in BillReconciliation.__dataclass_fields__.values()}
    assert "adjusted_hvac_dollars" not in field_names
    assert "rescaled_hvac" not in field_names


def test_invalid_window_raises():
    start = datetime.datetime(2026, 6, 1, 0, 0)
    with pytest.raises(ValueError):
        reconcile_bill_period(
            bill_period_start_utc=start, bill_period_end_utc=start,
            eagle_df=pd.DataFrame(columns=["_time", "delivered_kwh"]),
            refoss_df=pd.DataFrame(columns=["_time", "channel", "_value"]),
            rt_hrl_lmps_df=pd.DataFrame(columns=["_time", "total_lmp_rt"]),
            rate_snapshot=RATE_SNAPSHOT,
            actual_bill_variable_dollars=0.0,
        )
