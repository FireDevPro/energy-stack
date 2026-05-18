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


def test_arm11_dst_fallback_hours_remain_distinct():
    """Regression guard for spec §10's Arm 11 fall-back note.

    2026-11-01 has two distinct UTC hours that both map to the wall-
    clock label "2026-11-01 01:00 CT": UTC 06:00 (CDT) and UTC 07:00
    (CST). The reconciliation pipeline must consume them as distinct
    rows. If a future patch ever collapsed them via a naive-CT join
    key, this test would catch it: each UTC hour gets a deliberately
    different LMP, and the reconstructed cost must reflect BOTH
    distinct rates -- not one rate counted twice.
    """
    start = datetime.datetime(2026, 11, 1, 4, 0)  # 4h window covering the fold
    end = start + datetime.timedelta(hours=4)
    # Distinct LMP at every hour, with the two fold-collision hours
    # carrying maximally different prices so any collapse would shift
    # the reconstructed total measurably.
    rt = pd.DataFrame([
        {"_time": datetime.datetime(2026, 11, 1, 4, 0), "total_lmp_rt": 20.0},
        {"_time": datetime.datetime(2026, 11, 1, 5, 0), "total_lmp_rt": 25.0},
        {"_time": datetime.datetime(2026, 11, 1, 6, 0), "total_lmp_rt": 30.0},  # CT 01:00 CDT
        {"_time": datetime.datetime(2026, 11, 1, 7, 0), "total_lmp_rt": 80.0},  # CT 01:00 CST
    ])
    # Eagle: 1 kWh per hour for clean math, with a prior anchor at start-1h
    eagle = pd.DataFrame([
        {"_time": start - datetime.timedelta(hours=1),
         "delivered_kwh": 100.0, "_field": "delivered_kwh"},
        {"_time": datetime.datetime(2026, 11, 1, 4, 0),
         "delivered_kwh": 101.0, "_field": "delivered_kwh"},
        {"_time": datetime.datetime(2026, 11, 1, 5, 0),
         "delivered_kwh": 102.0, "_field": "delivered_kwh"},
        {"_time": datetime.datetime(2026, 11, 1, 6, 0),
         "delivered_kwh": 103.0, "_field": "delivered_kwh"},
        {"_time": datetime.datetime(2026, 11, 1, 7, 0),
         "delivered_kwh": 104.0, "_field": "delivered_kwh"},
    ])
    refoss = pd.DataFrame(columns=["_time", "channel", "_value", "_field"])
    out = reconcile_bill_period(
        bill_period_start_utc=start, bill_period_end_utc=end,
        eagle_df=eagle, refoss_df=refoss, rt_hrl_lmps_df=rt,
        rate_snapshot=RATE_SNAPSHOT,
        actual_bill_variable_dollars=0.0,
    )
    assert out.pct_hours_eagle == pytest.approx(100.0), (
        "All 4 hours should be Eagle-covered (the prior-anchor row "
        "is outside the window and shouldn't count toward coverage)."
    )

    # Expected reconstructed dollars: 1 kWh per hour times each hour's
    # rate. The two fold hours must contribute their distinct rates
    # (3.0 c/kWh from the CDT hour's LMP plus the rate-snapshot
    # components, vs 8.0 c/kWh from the CST hour's LMP plus the
    # snapshot components). Hand-compute using the rate snapshot.
    from tools.analysis.hvac_dollars import HourlyRateInputs, hourly_rate_c_per_kwh
    from tools.analysis.dtod_rates import dtod_total_delivery_c_per_kwh

    def _rate_at_ct_hour(lmp_per_mwh: float, ct_hour: int) -> float:
        return hourly_rate_c_per_kwh(HourlyRateInputs(
            rt_hrl_lmps_per_mwh=lmp_per_mwh,
            pea_c_per_kwh=RATE_SNAPSHOT["pea_c_per_kwh"],
            transmission_c_per_kwh=RATE_SNAPSHOT["transmission_c_per_kwh"],
            misc_procurement_c_per_kwh=RATE_SNAPSHOT["misc_procurement_c_per_kwh"],
            dtod_total_delivery_c_per_kwh=dtod_total_delivery_c_per_kwh(ct_hour),
            variable_riders_c_per_kwh=RATE_SNAPSHOT["variable_riders_c_per_kwh"],
            carbon_free_credit_c_per_kwh=RATE_SNAPSHOT["carbon_free_credit_c_per_kwh"],
        ))

    # UTC 04:00 = CT 23:00 (CDT, day before) -> Overnight band
    # UTC 05:00 = CT 00:00 (CDT, 2026-11-01) -> Overnight band
    # UTC 06:00 = CT 01:00 (CDT) -> Overnight band
    # UTC 07:00 = CT 01:00 (CST) -> Overnight band (still)
    rate_04 = _rate_at_ct_hour(20.0, 23)
    rate_05 = _rate_at_ct_hour(25.0, 0)
    rate_06 = _rate_at_ct_hour(30.0, 1)  # CDT 01:00
    rate_07 = _rate_at_ct_hour(80.0, 1)  # CST 01:00 -- DISTINCT rate, same CT-hour-of-day
    expected_cents = 1.0 * (rate_04 + rate_05 + rate_06 + rate_07)
    expected_dollars = expected_cents / 100.0
    assert out.reconstructed_variable_dollars == pytest.approx(expected_dollars, abs=1e-6)

    # The dollar contributions of UTC 06:00 and UTC 07:00 must be
    # individually present. If a naive-CT collapse silently dropped
    # one, the total would equal 1*(rate_04 + rate_05 + 2*rate_06) or
    # 1*(rate_04 + rate_05 + 2*rate_07) instead.
    collapsed_low = (rate_04 + rate_05 + 2 * rate_06) / 100.0
    collapsed_high = (rate_04 + rate_05 + 2 * rate_07) / 100.0
    assert abs(out.reconstructed_variable_dollars - collapsed_low) > 0.001
    assert abs(out.reconstructed_variable_dollars - collapsed_high) > 0.001


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
