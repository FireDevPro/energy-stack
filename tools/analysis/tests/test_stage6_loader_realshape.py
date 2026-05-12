"""Stage 6 loader real-shape acceptance tests.

Runs the actual `stage6_o2` orchestrator against synthetic stage1
parquet bundles that mirror the production shape (long-format Influx
columns). No `_load_stage6_inputs` monkeypatch; the goal is to
exercise the loader's manifest + parquet read path.

Plan: docs/plans/stage6-loader-plan.md
Spec: OSF_FILING.md criterion 14 (real-shape replay validation)
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path

import pandas as pd
import pytest

from tools.analysis import pipeline
from tools.analysis.replay.manifest import OBSERVED_RECENT
from tools.analysis.tests.fixture_real_shape import (
    build_long_format_df,
    build_refoss_channel_df,
    write_bundle,
)


def _write_assignment_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["iso_week", "monday_date", "arm"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _build_pjm_5cp_df(
    summer_year: int,
    peak_hours_utc: list[datetime.datetime],
    rto_mw: float = 150000.0,
    comed_mw: float = 18000.0,
) -> pd.DataFrame:
    """Build a long-format pjm.coincident_peak DataFrame.

    Each peak hour produces two field rows (peak_load_mw,
    comed_zone_load_mw), tagged by summer_year + peak_rank.
    """
    rows: list[dict] = []
    for rank, ts in enumerate(peak_hours_utc, start=1):
        for field, value in [
            ("peak_load_mw", rto_mw),
            ("comed_zone_load_mw", comed_mw),
        ]:
            rows.append({
                "_time": ts,
                "_measurement": "pjm.coincident_peak",
                "_field": field,
                "_value": value,
                "summer_year": str(summer_year),
                "peak_rank": str(rank),
            })
    df = pd.DataFrame(rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def test_phase1_layer1_populated_when_both_arms_have_peaks(tmp_path, monkeypatch):
    """Tracer: two PJM 5CP rows in two different Monday-weeks (one
    Arm A, one Arm B) plus matching refoss.channel power_w hours.

    Asserts:
      - o2_layer1.csv has one populated row with n_peaks_arm_a >= 1
        AND n_peaks_arm_b >= 1.
      - The CPL kW values come from mean(power_w)/1000, NOT from a
        nonexistent energy_wh field.
      - Other Stage 6 CSVs are header-only (no inputs wired yet).
    """
    # Arm A week starts Mon 2026-06-08; Arm B week starts Mon 2026-06-15
    # (per the synthetic assignment CSV we build below). Peak hours
    # land Tuesday afternoon of each week.
    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)   # 17:00 CDT Tue
    peak_b = datetime.datetime(2026, 6, 16, 23, 0, tzinfo=datetime.timezone.utc)  # 18:00 CDT Tue

    # PJM 5CP rows
    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a, peak_b],
    )

    # refoss.channel power_w at the two peak hours.
    # 2.0 kW for em:1 + 1.5 kW for em:7 = mean(power_w)/1000 sums to 3.5 kW
    # per arm (single-hour mean, single channel).
    refoss_rows: list[dict] = []
    for ts, ch_power in [
        (peak_a, {"em:1": 2000.0, "em:7": 1500.0}),
        (peak_b, {"em:1": 2000.0, "em:7": 1500.0}),
    ]:
        for channel, w in ch_power.items():
            refoss_rows.append({
                "_time": ts,
                "_measurement": "refoss.channel",
                "_field": "power_w",
                "_value": w,
                "channel": channel,
            })
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    # Synthetic assignment CSV covering the two Monday-weeks.
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    # Layer 1 populated with one row, both arms covered.
    with open(tmp_path / "stage6" / "o2_layer1.csv") as f:
        l1_rows = list(csv.DictReader(f))
    assert len(l1_rows) == 1
    row = l1_rows[0]
    assert int(row["n_peaks_arm_a"]) == 1
    assert int(row["n_peaks_arm_b"]) == 1
    # CPL = mean(power_w)/1000 over the peak hour for em:1 + em:7
    # = (2000 + 1500) / 1000 = 3.5 kW per arm. delta_kw = 0.
    assert float(row["a_cust_cpl_kw_arm_a"]) == pytest.approx(3.5, abs=0.01)
    assert float(row["a_cust_cpl_kw_arm_b"]) == pytest.approx(3.5, abs=0.01)

    # Layer 2 / 3 / detector header-only (no inputs for those layers
    # are wired yet; Phases 2-5 will populate).
    for name in ("o2_layer2.csv", "o2_layer3.csv", "detector_accuracy.csv"):
        with open(tmp_path / "stage6" / name) as f:
            rows = list(csv.DictReader(f))
        assert rows == [], f"{name} expected header-only but got {rows!r}"


# ----------------------------------------------------------------------
# Phase 2: ComEd 5CP from pjm.metered_load{zone=CE}
# ----------------------------------------------------------------------

def _build_metered_load_df(rows: list[dict]) -> pd.DataFrame:
    """Long-format pjm.metered_load DataFrame.

    Each `rows` entry: {ts, zone, is_verified (bool), mw}.
    """
    long_rows: list[dict] = []
    for r in rows:
        long_rows.append({
            "_time": r["ts"],
            "_measurement": "pjm.metered_load",
            "_field": "mw",
            "_value": float(r["mw"]),
            "zone": r["zone"],
            "is_verified": "true" if r["is_verified"] else "false",
        })
    df = pd.DataFrame(long_rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def test_phase2_verified_row_wins_even_when_lower_than_preliminary(tmp_path):
    """For each hour, prefer is_verified=true regardless of MW magnitude.

    Setup: one hour has both a verified row (17000 MW) and a
    preliminary row (18000 MW). The helper must select 17000, NOT
    the higher preliminary value.
    """
    summer_year = 2025
    target_hour = datetime.datetime(
        2025, 7, 15, 22, 0, tzinfo=datetime.timezone.utc,
    )
    # 4 additional distinct CT days so we have 5 distinct days total.
    extra_days = [
        datetime.datetime(2025, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (10, 11, 12, 13)
    ]
    rows = [
        # Conflicting verified vs preliminary at target_hour:
        {"ts": target_hour, "zone": "CE", "is_verified": True, "mw": 17000.0},
        {"ts": target_hour, "zone": "CE", "is_verified": False, "mw": 18000.0},
    ]
    for ts in extra_days:
        rows.append(
            {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0},
        )
    df = _build_metered_load_df(rows)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"pjm.metered_load": df},
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2025-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    from tools.analysis.replay.manifest import read_manifest
    manifest = read_manifest(stage1_dir / "manifest.json")
    result = pipeline._load_comed_5cp_hours(manifest, stage1_dir, summer_year)
    assert result is not None
    peak_hours, partial, preliminary_hours = result
    assert not partial    # 5 distinct days, complete
    # target_hour is selected at MW=17000 (verified), not 18000 (preliminary).
    # That hour should NOT appear in preliminary_hours either.
    assert target_hour in peak_hours
    assert target_hour not in preliminary_hours


def test_phase2_partial_under_5_days_returns_partial_flag(tmp_path):
    """Only 3 distinct CT days of verified ComEd metered load → partial=True."""
    summer_year = 2025
    three_days = [
        datetime.datetime(2025, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (10, 11, 12)
    ]
    rows = [
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in three_days
    ]
    df = _build_metered_load_df(rows)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"pjm.metered_load": df},
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2025-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    from tools.analysis.replay.manifest import read_manifest
    manifest = read_manifest(stage1_dir / "manifest.json")
    result = pipeline._load_comed_5cp_hours(manifest, stage1_dir, summer_year)
    assert result is not None
    peak_hours, partial, preliminary_hours = result
    assert partial is True
    assert len(peak_hours) == 3


def test_phase2_top5_picks_max_hour_per_distinct_ct_day(tmp_path):
    """5 distinct CT days, each with multiple hours; helper returns
    the max-MW hour per day."""
    summer_year = 2025
    # Five days, with two candidate hours each (one higher).
    rows: list[dict] = []
    expected_hours: list[datetime.datetime] = []
    for d in (10, 11, 12, 13, 14):
        h1 = datetime.datetime(2025, 6, d, 21, 0, tzinfo=datetime.timezone.utc)
        h2 = datetime.datetime(2025, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        rows.append({"ts": h1, "zone": "CE", "is_verified": True, "mw": 15500.0})
        # Make h2 the higher MW (one tier higher).
        rows.append({"ts": h2, "zone": "CE", "is_verified": True, "mw": 16500.0})
        expected_hours.append(h2)
    df = _build_metered_load_df(rows)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"pjm.metered_load": df},
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2025-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    from tools.analysis.replay.manifest import read_manifest
    manifest = read_manifest(stage1_dir / "manifest.json")
    result = pipeline._load_comed_5cp_hours(manifest, stage1_dir, summer_year)
    assert result is not None
    peak_hours, partial, preliminary_hours = result
    assert not partial
    assert set(peak_hours) == set(expected_hours)


# ----------------------------------------------------------------------
# Phase 3: Layer 2 portfolio-sum scenarios wiring
# ----------------------------------------------------------------------

def test_phase3_layer2_emits_three_scenarios_when_inputs_complete(
    tmp_path, monkeypatch,
):
    """Layer 2 produces three rows (low / anchor_2021 / high) when:
      - PJM 5CP has 2 peaks spanning both arms (Layer 1 OK)
      - ComEd 5CP has 5 distinct CT days
      - mains kW covers every peak hour
    """
    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    peak_b = datetime.datetime(2026, 6, 16, 23, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a, peak_b],
    )

    # 5 ComEd 5CP days. Three in Arm A week (Jun 8-14), two in Arm B
    # week (Jun 15-21). All 22:00 UTC = 17:00 CDT.
    comed_a_days = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (9, 10, 11)
    ]
    comed_b_days = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (16, 17)
    ]
    all_comed = comed_a_days + comed_b_days
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in all_comed
    ])

    # Mains kW: power_w rows for every peak (PJM + ComEd) hour.
    all_peak_hours = list({peak_a, peak_b} | set(all_comed))
    refoss_rows: list[dict] = []
    for ts in all_peak_hours:
        for channel, w in [("em:1", 2000.0), ("em:7", 1500.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer2.csv") as f:
        rows = list(csv.DictReader(f))
    assert {r["scenario"] for r in rows} == {"low", "anchor_2021", "high"}
    # Each row carries the locked portfolio_sum_mw values.
    by_scenario = {r["scenario"]: r for r in rows}
    assert float(by_scenario["low"]["portfolio_sum_mw"]) == pytest.approx(1500.0)
    assert float(by_scenario["anchor_2021"]["portfolio_sum_mw"]) == pytest.approx(2033.653)
    assert float(by_scenario["high"]["portfolio_sum_mw"]) == pytest.approx(3000.0)


def test_phase3_layer2_header_only_when_comed_5cp_partial(
    tmp_path, monkeypatch,
):
    """Only 3 distinct ComEd days → entire Layer 2 CSV header-only +
    INCOMPLETE_COMED_5CP_IN_WINDOW reason."""
    from tools.analysis.replay.reason_codes import ReasonCode

    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    peak_b = datetime.datetime(2026, 6, 16, 23, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a, peak_b],
    )
    # Only 3 distinct ComEd days.
    three_comed = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (9, 10, 16)
    ]
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in three_comed
    ])
    all_peak_hours = list({peak_a, peak_b} | set(three_comed))
    refoss_rows = []
    for ts in all_peak_hours:
        for channel, w in [("em:1", 2000.0), ("em:7", 1500.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer2.csv") as f:
        assert list(csv.DictReader(f)) == []

    import json
    with open(tmp_path / "stage6" / "reason_report.json") as f:
        entries = json.load(f)["entries"]
    codes = {e["reason_code"] for e in entries}
    assert ReasonCode.INCOMPLETE_COMED_5CP_IN_WINDOW.value in codes


def test_phase3_layer2_header_only_when_pjm_metered_load_missing(
    tmp_path, monkeypatch,
):
    """No pjm.metered_load measurement at all → NO_COMED_5CP_HOURS_IN_WINDOW."""
    from tools.analysis.replay.reason_codes import ReasonCode

    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    peak_b = datetime.datetime(2026, 6, 16, 23, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a, peak_b],
    )
    refoss_rows = []
    for ts in (peak_a, peak_b):
        for channel, w in [("em:1", 2000.0), ("em:7", 1500.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer2.csv") as f:
        assert list(csv.DictReader(f)) == []

    import json
    with open(tmp_path / "stage6" / "reason_report.json") as f:
        entries = json.load(f)["entries"]
    codes = {e["reason_code"] for e in entries}
    assert ReasonCode.NO_COMED_5CP_HOURS_IN_WINDOW.value in codes


# ----------------------------------------------------------------------
# Phase 4: Layer 3 from comed.bill_lineitems (May-Sep of Y+1)
# ----------------------------------------------------------------------

def _build_comed_bill_dfs(bills: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build long-format DataFrames for both `comed.bill` (service_from
    field) and `comed.bill_lineitems` (Capacity Charge amount).

    Each `bills` entry: {
        ts: service_to UTC datetime,
        account_no: str,
        service_from: ISO date string ("2026-05-05"),
        capacity_charge: float,
    }
    """
    bill_rows = []
    li_rows = []
    for b in bills:
        bill_rows.append({
            "_time": b["ts"],
            "_measurement": "comed.bill",
            "_field": "service_from",
            "_value": b["service_from"],
            "account_no": b["account_no"],
        })
        li_rows.append({
            "_time": b["ts"],
            "_measurement": "comed.bill_lineitems",
            "_field": "amount",
            "_value": float(b["capacity_charge"]),
            "account_no": b["account_no"],
            "line_item": "Capacity Charge",
            "category": "DELIVERY",
        })
    bill_df = pd.DataFrame(bill_rows)
    bill_df["_time"] = pd.to_datetime(bill_df["_time"], utc=True)
    li_df = pd.DataFrame(li_rows)
    li_df["_time"] = pd.to_datetime(li_df["_time"], utc=True)
    return bill_df, li_df


def test_phase4_layer3_sums_capacity_charges_for_may_sep_y_plus_1(
    tmp_path, monkeypatch,
):
    """Three bills in May-Sep 2026 (capacity year for summer_year=2025).
    Layer 3 sums their Capacity Charge line-items."""
    peak_2025 = datetime.datetime(2025, 7, 15, 22, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2025, peak_hours_utc=[peak_2025],
    )
    bill_df, li_df = _build_comed_bill_dfs([
        {
            "ts": datetime.datetime(2026, 6, 5, tzinfo=datetime.timezone.utc),
            "account_no": "1234567890",
            "service_from": "2026-05-05",
            "capacity_charge": 22.50,
        },
        {
            "ts": datetime.datetime(2026, 7, 5, tzinfo=datetime.timezone.utc),
            "account_no": "1234567890",
            "service_from": "2026-06-05",
            "capacity_charge": 28.75,
        },
        {
            "ts": datetime.datetime(2026, 8, 5, tzinfo=datetime.timezone.utc),
            "account_no": "1234567890",
            "service_from": "2026-07-05",
            "capacity_charge": 31.10,
        },
    ])

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "comed.bill": bill_df,
            "comed.bill_lineitems": li_df,
        },
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2026-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer3.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    assert int(row["year"]) == 2026
    assert int(row["months_summed"]) == 3
    assert float(row["total_capacity_charge_dollars"]) == pytest.approx(82.35)


def test_phase4_layer3_header_only_when_bills_outside_locked_months(
    tmp_path, monkeypatch,
):
    """Bills only in Apr/Oct 2026 (outside the May-Sep locked window) →
    Layer 3 sums to 0 of 0 matching bills; emits NO_COMED_BILLS_IN_WINDOW."""
    from tools.analysis.replay.reason_codes import ReasonCode

    peak_2025 = datetime.datetime(2025, 7, 15, 22, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2025, peak_hours_utc=[peak_2025],
    )
    bill_df, li_df = _build_comed_bill_dfs([
        {
            "ts": datetime.datetime(2026, 5, 5, tzinfo=datetime.timezone.utc),
            "account_no": "1234567890",
            "service_from": "2026-04-05",      # April bill
            "capacity_charge": 18.00,
        },
        {
            "ts": datetime.datetime(2026, 11, 5, tzinfo=datetime.timezone.utc),
            "account_no": "1234567890",
            "service_from": "2026-10-05",      # October bill
            "capacity_charge": 19.50,
        },
    ])

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "comed.bill": bill_df,
            "comed.bill_lineitems": li_df,
        },
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2026-12-31T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer3.csv") as f:
        assert list(csv.DictReader(f)) == []

    import json
    with open(tmp_path / "stage6" / "reason_report.json") as f:
        entries = json.load(f)["entries"]
    codes = {e["reason_code"] for e in entries}
    assert ReasonCode.NO_COMED_BILLS_IN_WINDOW.value in codes


def test_phase4_layer3_header_only_when_no_pjm_5cp(
    tmp_path, monkeypatch,
):
    """No pjm.coincident_peak → can't derive capacity_year → Layer 3
    header-only + NO_PJM_5CP_HOURS_IN_WINDOW propagated."""
    from tools.analysis.replay.reason_codes import ReasonCode

    bill_df, li_df = _build_comed_bill_dfs([
        {
            "ts": datetime.datetime(2026, 6, 5, tzinfo=datetime.timezone.utc),
            "account_no": "1234567890",
            "service_from": "2026-05-05",
            "capacity_charge": 22.50,
        },
    ])

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "comed.bill": bill_df,
            "comed.bill_lineitems": li_df,
        },
        window_start_ct="2026-05-01T00:00:00-05:00",
        window_end_ct="2026-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer3.csv") as f:
        assert list(csv.DictReader(f)) == []

    import json
    with open(tmp_path / "stage6" / "reason_report.json") as f:
        entries = json.load(f)["entries"]
    codes = {e["reason_code"] for e in entries}
    assert ReasonCode.NO_PJM_5CP_HOURS_IN_WINDOW.value in codes


# ----------------------------------------------------------------------
# Phase 5: detector_accuracy per-scope wiring from hvac.5cp_state
# ----------------------------------------------------------------------

def _build_5cp_state_df(rows: list[dict]) -> pd.DataFrame:
    """Long-format hvac.5cp_state DataFrame.

    Each `rows` entry: {ts, scope ('rto'|'comed_zone'), is_active: bool}.
    """
    long_rows = []
    for r in rows:
        long_rows.append({
            "_time": r["ts"],
            "_measurement": "hvac.5cp_state",
            "_field": "current_load_mw",
            "_value": 15000.0,
            "scope": r["scope"],
            "zone": "RTO" if r["scope"] == "rto" else "CE",
            "is_active": "true" if r["is_active"] else "false",
        })
    df = pd.DataFrame(long_rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def test_phase5_detector_perfect_per_scope(tmp_path, monkeypatch):
    """All truth hours predicted active in the matching scope; all
    other hours predicted off. Detector should report TP=truth_n,
    FP=0, FN=0 per scope."""
    summer_year = 2026
    # 2 PJM 5CP hours (rto truth)
    pjm_hours = [
        datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 6, 10, 23, 0, tzinfo=datetime.timezone.utc),
    ]
    pjm_df = _build_pjm_5cp_df(
        summer_year=summer_year, peak_hours_utc=pjm_hours,
    )
    # 5 ComEd hours (5 distinct CT days)
    comed_hours = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (11, 12, 13, 14, 15)
    ]
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in comed_hours
    ])
    # hvac.5cp_state: is_active=true at each truth hour for the
    # matching scope.
    state_rows: list[dict] = []
    for ts in pjm_hours:
        state_rows.append({"ts": ts, "scope": "rto", "is_active": True})
    for ts in comed_hours:
        state_rows.append({"ts": ts, "scope": "comed_zone", "is_active": True})
    state_df = _build_5cp_state_df(state_rows)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
            "hvac.5cp_state": state_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "detector_accuracy.csv") as f:
        rows = list(csv.DictReader(f))
    by_scope = {r["scope"]: r for r in rows}
    assert set(by_scope) == {"rto", "comed_zone", "combined_any"}
    assert int(by_scope["rto"]["tp"]) == 2
    assert int(by_scope["rto"]["fp"]) == 0
    assert int(by_scope["rto"]["fn"]) == 0
    assert int(by_scope["comed_zone"]["tp"]) == 5
    assert int(by_scope["comed_zone"]["fp"]) == 0
    assert int(by_scope["combined_any"]["tp"]) == 7


def test_phase5_detector_records_false_positive_at_off_truth_hour(
    tmp_path, monkeypatch,
):
    """is_active=true at an hour not in the published 5CP truth set
    yields one FP for that scope."""
    summer_year = 2026
    truth = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    false_positive_hour = datetime.datetime(
        2026, 6, 10, 14, 0, tzinfo=datetime.timezone.utc,
    )
    pjm_df = _build_pjm_5cp_df(
        summer_year=summer_year, peak_hours_utc=[truth],
    )
    # 5 ComEd 5CP days so comed_zone path is complete.
    comed_hours = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (11, 12, 13, 14, 15)
    ]
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in comed_hours
    ])
    state_df = _build_5cp_state_df([
        {"ts": truth, "scope": "rto", "is_active": True},
        {"ts": false_positive_hour, "scope": "rto", "is_active": True},
    ])

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
            "hvac.5cp_state": state_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "detector_accuracy.csv") as f:
        rows = list(csv.DictReader(f))
    by_scope = {r["scope"]: r for r in rows}
    assert int(by_scope["rto"]["tp"]) == 1
    assert int(by_scope["rto"]["fp"]) == 1
    assert int(by_scope["rto"]["fn"]) == 0


def test_phase5_detector_header_only_when_no_5cp_state(
    tmp_path, monkeypatch,
):
    """No hvac.5cp_state measurement → NO_5CP_STATE_IN_WINDOW reason
    and detector_accuracy.csv header-only."""
    from tools.analysis.replay.reason_codes import ReasonCode

    summer_year = 2026
    pjm_df = _build_pjm_5cp_df(
        summer_year=summer_year,
        peak_hours_utc=[
            datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc),
        ],
    )
    comed_hours = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (11, 12, 13, 14, 15)
    ]
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in comed_hours
    ])

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "detector_accuracy.csv") as f:
        assert list(csv.DictReader(f)) == []

    import json
    with open(tmp_path / "stage6" / "reason_report.json") as f:
        entries = json.load(f)["entries"]
    codes = {e["reason_code"] for e in entries}
    assert ReasonCode.NO_5CP_STATE_IN_WINDOW.value in codes


# ----------------------------------------------------------------------
# Phase 6: provenance.json sidecar
# ----------------------------------------------------------------------

def test_phase6_provenance_records_summer_year_and_tariff_capacity_year(
    tmp_path, monkeypatch,
):
    """When summer_year is derived, stage6/provenance.json captures
    it and the corresponding tariff capacity year (Y+1)."""
    summer_year = 2025
    pjm_df = _build_pjm_5cp_df(
        summer_year=summer_year,
        peak_hours_utc=[
            datetime.datetime(2025, 7, 15, 22, 0, tzinfo=datetime.timezone.utc),
        ],
    )
    comed_hours = [
        datetime.datetime(2025, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (10, 11, 12, 13, 14)
    ]
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in comed_hours
    ])

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
        },
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2025-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    import json
    provenance_path = tmp_path / "stage6" / "provenance.json"
    assert provenance_path.exists()
    with open(provenance_path) as f:
        prov = json.load(f)
    assert prov["summer_year"] == 2025
    assert prov["tariff_capacity_year"] == 2026
    assert prov["comed_distinct_day_tz"] == "CT"
    # No preliminary ComEd hours in this fixture.
    assert prov["comed_5cp_preliminary"] is False


def test_phase6_provenance_flags_preliminary_comed_when_top5_includes_unverified(
    tmp_path, monkeypatch,
):
    """If any of the top-5 selected ComEd hours came from a preliminary
    (is_verified=false) row, provenance records the marker."""
    summer_year = 2025
    pjm_df = _build_pjm_5cp_df(
        summer_year=summer_year,
        peak_hours_utc=[
            datetime.datetime(2025, 7, 15, 22, 0, tzinfo=datetime.timezone.utc),
        ],
    )
    # 4 verified days + 1 preliminary-only day; all 5 land in top-5.
    verified_hours = [
        datetime.datetime(2025, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (10, 11, 12, 13)
    ]
    preliminary_hour = datetime.datetime(
        2025, 6, 14, 22, 0, tzinfo=datetime.timezone.utc,
    )
    rows = [
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in verified_hours
    ]
    rows.append(
        {"ts": preliminary_hour, "zone": "CE", "is_verified": False, "mw": 16500.0},
    )
    metered_df = _build_metered_load_df(rows)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
        },
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2025-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    import json
    with open(tmp_path / "stage6" / "provenance.json") as f:
        prov = json.load(f)
    assert prov["comed_5cp_preliminary"] is True
    assert preliminary_hour.isoformat() in prov["comed_5cp_preliminary_hours"]


def test_phase1_oracle_load_for_summer_year_dispatches_to_y_plus_one():
    """Pin the Y+1 rule independently of capacity-rate values.

    tariff_constants.json happens to carry identical rates for adjacent
    capacity years (2026 and 2027 are both $10.13567/kW-mo per the
    placeholder note). That means the orchestrator-level Phase 1
    oracle's `delta_dollars_total == $177.37` would pass even if
    load_for_summer_year buggily called load(summer_year) instead of
    load(summer_year + 1). The .year attribute distinguishes the
    two paths even when other fields collide.
    """
    from tools.o2_capacity_reconstruction.reconstruct import TariffConstants
    assert TariffConstants.load_for_summer_year(2025).year == 2026
    assert TariffConstants.load_for_summer_year(2026).year == 2027


def test_phase1_oracle_hourly_mains_kw_exact_values(tmp_path):
    """Pin the intermediate _load_hourly_mains_kw arithmetic directly.

    Asymmetric power_w across two hours and across mains channels.
    The helper means per (hour, channel) and SUMS across channels,
    so the test verifies BOTH stages of aggregation.

    Hour 1 (Jun 9 22:00 UTC):
      em:1 ticks: 800, 1200 → mean = 1000 W
      em:7 ticks: 400, 600  → mean = 500 W
      sum across channels = 1500 W → 1.5 kW
    Hour 2 (Jun 16 23:00 UTC):
      em:1 single tick: 3000 W → 3000 W
      em:7 single tick: 2000 W → 2000 W
      sum = 5000 W → 5.0 kW
    """
    hour_1 = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    hour_2 = datetime.datetime(2026, 6, 16, 23, 0, tzinfo=datetime.timezone.utc)
    # Within hour_1, two ticks per channel to exercise the mean.
    hour_1_tick_1 = hour_1
    hour_1_tick_2 = hour_1 + datetime.timedelta(minutes=30)
    refoss_rows = [
        # em:1 hour 1: 800, 1200 (mean 1000)
        {"_time": hour_1_tick_1, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 800.0, "channel": "em:1"},
        {"_time": hour_1_tick_2, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 1200.0, "channel": "em:1"},
        # em:7 hour 1: 400, 600 (mean 500)
        {"_time": hour_1_tick_1, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 400.0, "channel": "em:7"},
        {"_time": hour_1_tick_2, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 600.0, "channel": "em:7"},
        # Hour 2 single ticks per channel
        {"_time": hour_2, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 3000.0, "channel": "em:1"},
        {"_time": hour_2, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 2000.0, "channel": "em:7"},
    ]
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": refoss_df},
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    from tools.analysis.replay.manifest import read_manifest
    manifest = read_manifest(stage1_dir / "manifest.json")
    hourly_kw = pipeline._load_hourly_mains_kw(manifest, stage1_dir)

    assert hourly_kw[hour_1] == pytest.approx(1.5)
    assert hourly_kw[hour_2] == pytest.approx(5.0)
    # No other hours present in the fixture.
    assert set(hourly_kw.keys()) == {hour_1, hour_2}


def test_phase1_oracle_asymmetric_arm_kw_produces_known_delta(
    tmp_path, monkeypatch,
):
    """Hand-calculated oracle for Layer 1 arithmetic.

    Setup with asymmetric power_w so the delta is non-zero and the
    dollar arithmetic isn't masked by equal arms.

    Arm A peak (em:1=1000 W, em:7=500 W) → 1.5 kW
    Arm B peak (em:1=3000 W, em:7=2000 W) → 5.0 kW
    delta_kw = 5.0 - 1.5 = 3.5 kW

    Tariff for summer_year=2026 → capacity_year=2027 →
    rate = $10.13567/kW-month from tariff_constants.json.
    months_billed default = 5.

    delta_dollars_total = 3.5 × 10.13567 × 5 = $177.37 (1 cent tol).
    """
    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    peak_b = datetime.datetime(2026, 6, 16, 23, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a, peak_b],
    )

    refoss_rows = [
        # Arm A peak: 1.5 kW total
        {"_time": peak_a, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 1000.0, "channel": "em:1"},
        {"_time": peak_a, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 500.0, "channel": "em:7"},
        # Arm B peak: 5.0 kW total
        {"_time": peak_b, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 3000.0, "channel": "em:1"},
        {"_time": peak_b, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 2000.0, "channel": "em:7"},
    ]
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer1.csv") as f:
        row = next(csv.DictReader(f))
    assert float(row["a_cust_cpl_kw_arm_a"]) == pytest.approx(1.5)
    assert float(row["a_cust_cpl_kw_arm_b"]) == pytest.approx(5.0)
    assert float(row["delta_kw"]) == pytest.approx(3.5)
    # 3.5 * 10.13567 * 5 = 177.374225
    assert float(row["delta_dollars_total"]) == pytest.approx(177.374225, abs=0.01)
    assert float(row["capacity_rate_dollars_per_kw_month"]) == pytest.approx(10.13567)
    assert int(row["months_billed"]) == 5


def test_phase3_oracle_branch2_scenarios_produce_distinct_cplc(
    tmp_path, monkeypatch,
):
    """Hand-calculated Layer 2 oracle. Setup forces Att. M-2 branch 2
    (ACustCPL < ACustPL) so the three portfolio-sum denominators
    yield distinct CPLC values.

    PJM 5CP (both arms): 1.0 kW per hour (low → ACustCPL = 1.0)
    ComEd 5CP Arm A: 3 hours × 3.0 kW (ACustPL_A = 3.0)
    ComEd 5CP Arm B: 2 hours × 5.0 kW (ACustPL_B = 5.0)

    Tariff capacity_year=2027:
      ComEdNPL = 20736 MW
      AComEdCPL = 19138.22 MW
      gap = (ComEdNPL − AComEdCPL) × 1000 = 1,597,780 kW

    Arm A adjustment(scenario) = 1597780 × (3.0 − 1.0) / (portfolio × 1000)
    Arm B adjustment(scenario) = 1597780 × (5.0 − 1.0) / (portfolio × 1000)

    | scenario     | portfolio_kW | CPLC_a               | CPLC_b               |
    | low (1500)   | 1,500,000    | 1 + 2.130373 = 3.130 | 1 + 4.260747 = 5.261 |
    | anchor (2034)| 2,033,653    | 1 + 1.571330 = 2.571 | 1 + 3.142660 = 4.143 |
    | high (3000)  | 3,000,000    | 1 + 1.065187 = 2.065 | 1 + 2.130373 = 3.130 |
    """
    # Disjoint PJM and ComEd hours so no power_w collision pollutes the
    # arithmetic. PJM peaks at xx:00, ComEd at xx:22 (different days too).
    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    peak_b = datetime.datetime(2026, 6, 16, 23, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a, peak_b],
    )
    comed_a_days = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (10, 11, 12)
    ]
    comed_b_days = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (17, 18)
    ]
    all_comed = comed_a_days + comed_b_days
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in all_comed
    ])

    # PJM peak hours @ 1.0 kW total (em:1=500, em:7=500).
    # Arm A ComEd hours @ 3.0 kW (em:1=2000, em:7=1000).
    # Arm B ComEd hours @ 5.0 kW (em:1=3000, em:7=2000).
    refoss_rows: list[dict] = []
    for ts in (peak_a, peak_b):
        for channel, w in [("em:1", 500.0), ("em:7", 500.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    for ts in comed_a_days:
        for channel, w in [("em:1", 2000.0), ("em:7", 1000.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    for ts in comed_b_days:
        for channel, w in [("em:1", 3000.0), ("em:7", 2000.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer2.csv") as f:
        rows = list(csv.DictReader(f))
    by_scenario = {r["scenario"]: r for r in rows}
    expected = {
        "low":         (3.130373, 5.260747),
        "anchor_2021": (2.571330, 4.142660),
        "high":        (2.065187, 3.130373),
    }
    for scenario, (cplc_a_expected, cplc_b_expected) in expected.items():
        row = by_scenario[scenario]
        assert float(row["cplc_kw_arm_a"]) == pytest.approx(
            cplc_a_expected, abs=0.001,
        ), f"{scenario}: CPLC arm A"
        assert float(row["cplc_kw_arm_b"]) == pytest.approx(
            cplc_b_expected, abs=0.001,
        ), f"{scenario}: CPLC arm B"
        # delta_kw = CPLC_b - CPLC_a
        assert float(row["delta_kw"]) == pytest.approx(
            cplc_b_expected - cplc_a_expected, abs=0.001,
        ), f"{scenario}: delta_kw"


def test_phase5_oracle_perfect_detector_per_scope_with_tn(
    tmp_path, monkeypatch,
):
    """Phase 5 detector oracle including TN + summer_hours_n.

    14-day window (Jun 8 → Jun 22, CT) at 1-hour cadence = 336 hours.
    Truth: 2 RTO + 5 ComEd (disjoint), so combined truth = 7.
    All truth hours predicted active in their scope; no FPs.

    rto:          tp=2, fp=0, fn=0, tn=334, summer_hours_n=336
    comed_zone:   tp=5, fp=0, fn=0, tn=331, summer_hours_n=336
    combined_any: tp=7, fp=0, fn=0, tn=329, summer_hours_n=336
    """
    summer_year = 2026
    pjm_hours = [
        datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 6, 10, 23, 0, tzinfo=datetime.timezone.utc),
    ]
    pjm_df = _build_pjm_5cp_df(
        summer_year=summer_year, peak_hours_utc=pjm_hours,
    )
    comed_hours = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (11, 12, 13, 14, 15)
    ]
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in comed_hours
    ])
    state_rows: list[dict] = []
    for ts in pjm_hours:
        state_rows.append({"ts": ts, "scope": "rto", "is_active": True})
    for ts in comed_hours:
        state_rows.append({"ts": ts, "scope": "comed_zone", "is_active": True})
    state_df = _build_5cp_state_df(state_rows)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
            "hvac.5cp_state": state_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "detector_accuracy.csv") as f:
        rows = list(csv.DictReader(f))
    by_scope = {r["scope"]: r for r in rows}
    expected = {
        "rto":          (2, 0, 0, 334, 336),
        "comed_zone":   (5, 0, 0, 331, 336),
        "combined_any": (7, 0, 0, 329, 336),
    }
    for scope, (tp, fp, fn, tn, hours_n) in expected.items():
        r = by_scope[scope]
        assert int(r["tp"]) == tp, f"{scope} tp"
        assert int(r["fp"]) == fp, f"{scope} fp"
        assert int(r["fn"]) == fn, f"{scope} fn"
        assert int(r["tn"]) == tn, f"{scope} tn"
        assert int(r["summer_hours_n"]) == hours_n, f"{scope} summer_hours_n"
        # Rates: perfect detector → tpr=1.0, fpr=0.0, fnr=0.0
        assert float(r["tpr"]) == pytest.approx(1.0)
        assert float(r["fpr"]) == pytest.approx(0.0)
        assert float(r["fnr"]) == pytest.approx(0.0)


# ----------------------------------------------------------------------
# Gap-coverage patches per audit (PR #94 patch round)
# ----------------------------------------------------------------------

def test_audit_ambiguous_summer_year_emits_reason(tmp_path, monkeypatch):
    """Pre-audit gap: the AMBIGUOUS_SUMMER_YEAR path was wired in
    `_load_pjm_5cp_hours` (via the `_AmbiguousSummerYear` sentinel)
    but no test triggered it. A bundle whose pjm.coincident_peak
    parquet carries two distinct summer_year tags must yield
    AMBIGUOUS_SUMMER_YEAR for every Stage 6 output that depends on
    summer_year (Layer 1, Layer 2, Layer 3, detector).
    """
    from tools.analysis.replay.reason_codes import ReasonCode
    # Two summers in the same bundle: summer_year tags "2025" and "2026".
    peak_2025 = datetime.datetime(2025, 7, 15, 22, 0, tzinfo=datetime.timezone.utc)
    peak_2026 = datetime.datetime(2026, 7, 14, 22, 0, tzinfo=datetime.timezone.utc)
    pjm_df = pd.concat([
        _build_pjm_5cp_df(summer_year=2025, peak_hours_utc=[peak_2025]),
        _build_pjm_5cp_df(summer_year=2026, peak_hours_utc=[peak_2026]),
    ], ignore_index=True)
    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"pjm.coincident_peak": pjm_df},
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2026-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    # All four CSVs header-only.
    for name in ("o2_layer1.csv", "o2_layer2.csv",
                 "o2_layer3.csv", "detector_accuracy.csv"):
        with open(tmp_path / "stage6" / name) as f:
            assert list(csv.DictReader(f)) == [], f"{name} expected header-only"

    # AMBIGUOUS_SUMMER_YEAR present in reason_report.json.
    import json
    with open(tmp_path / "stage6" / "reason_report.json") as f:
        entries = json.load(f)["entries"]
    codes = {e["reason_code"] for e in entries}
    assert ReasonCode.AMBIGUOUS_SUMMER_YEAR.value in codes


def test_audit_layer2_branch1_invariant_all_scenarios_collapse_to_same_cplc(
    tmp_path, monkeypatch,
):
    """Att. M-2 §2 branch 1: when ACustCPL >= ACustPL, the portfolio
    denominator is unused. All three Layer 2 scenarios MUST return
    identical CPLC per arm.

    Setup forces branch 1 by making PJM peaks higher than ComEd peaks:
      PJM peaks (both arms): 5.0 kW → ACustCPL_a = ACustCPL_b = 5.0
      ComEd Arm A: 3 hours × 3.0 kW → ACustPL_a = 3.0 (< 5.0 → branch 1)
      ComEd Arm B: 2 hours × 3.0 kW → ACustPL_b = 3.0 (< 5.0 → branch 1)

    Expected: CPLC_a = CPLC_b = 5.0 kW across all three scenarios,
    delta_kw = 0 for every scenario.
    """
    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    peak_b = datetime.datetime(2026, 6, 16, 23, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a, peak_b],
    )
    comed_a_days = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (10, 11, 12)
    ]
    comed_b_days = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (17, 18)
    ]
    all_comed = comed_a_days + comed_b_days
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in all_comed
    ])

    # PJM peaks @ 5.0 kW (em:1=3000, em:7=2000). ComEd @ 3.0 kW.
    refoss_rows: list[dict] = []
    for ts in (peak_a, peak_b):
        for channel, w in [("em:1", 3000.0), ("em:7", 2000.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    for ts in all_comed:
        for channel, w in [("em:1", 2000.0), ("em:7", 1000.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    with open(tmp_path / "stage6" / "o2_layer2.csv") as f:
        rows = list(csv.DictReader(f))
    by_scenario = {r["scenario"]: r for r in rows}
    assert set(by_scenario) == {"low", "anchor_2021", "high"}
    # Branch 1 invariant: all three scenarios have identical CPLC.
    cplc_a_values = {float(by_scenario[s]["cplc_kw_arm_a"])
                     for s in ("low", "anchor_2021", "high")}
    cplc_b_values = {float(by_scenario[s]["cplc_kw_arm_b"])
                     for s in ("low", "anchor_2021", "high")}
    assert len(cplc_a_values) == 1, f"branch 1 violated for arm A: {cplc_a_values}"
    assert len(cplc_b_values) == 1, f"branch 1 violated for arm B: {cplc_b_values}"
    # Exact branch 1 value: CPLC = ACustCPL = 5.0 kW for both arms.
    assert next(iter(cplc_a_values)) == pytest.approx(5.0, abs=0.001)
    assert next(iter(cplc_b_values)) == pytest.approx(5.0, abs=0.001)
    # delta_kw = 0 across all scenarios in this symmetric setup.
    for s in ("low", "anchor_2021", "high"):
        assert float(by_scenario[s]["delta_kw"]) == pytest.approx(0.0, abs=0.001)


def test_audit_layer2_inherits_insufficient_peaks_by_arm(
    tmp_path, monkeypatch,
):
    """When Layer 1 fails with INSUFFICIENT_PEAKS_BY_ARM (only one arm
    has PJM peaks), Layer 2 must also go header-only with the same
    reason, even if ComEd 5CP is complete. Layer 2's arm-delta
    arithmetic depends on the SAME arm partition that Layer 1 needs.
    """
    from tools.analysis.replay.reason_codes import ReasonCode
    # Single PJM peak in Arm A week — leaves Arm B empty.
    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a],
    )
    # Full ComEd 5CP (5 distinct CT days).
    comed_hours = [
        datetime.datetime(2026, 6, d, 22, 0, tzinfo=datetime.timezone.utc)
        for d in (10, 11, 12, 17, 18)
    ]
    metered_df = _build_metered_load_df([
        {"ts": ts, "zone": "CE", "is_verified": True, "mw": 16500.0}
        for ts in comed_hours
    ])
    refoss_rows: list[dict] = []
    for ts in [peak_a] + comed_hours:
        for channel, w in [("em:1", 2000.0), ("em:7", 1500.0)]:
            refoss_rows.append({
                "_time": ts, "_measurement": "refoss.channel",
                "_field": "power_w", "_value": w, "channel": channel,
            })
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "pjm.metered_load": metered_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-22T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
        {"iso_week": "2026-W25", "monday_date": "2026-06-15", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    # Both Layer 1 AND Layer 2 header-only.
    for name in ("o2_layer1.csv", "o2_layer2.csv"):
        with open(tmp_path / "stage6" / name) as f:
            assert list(csv.DictReader(f)) == [], f"{name} expected header-only"

    # Both outputs in reason_report.json with INSUFFICIENT_PEAKS_BY_ARM.
    import json
    with open(tmp_path / "stage6" / "reason_report.json") as f:
        entries = json.load(f)["entries"]
    by_output = {e["output_file"]: e["reason_code"] for e in entries}
    assert by_output["o2_layer1.csv"] == ReasonCode.INSUFFICIENT_PEAKS_BY_ARM.value
    assert by_output["o2_layer2.csv"] == ReasonCode.INSUFFICIENT_PEAKS_BY_ARM.value


def test_audit_tariff_missing_capacity_year_emits_reason(
    tmp_path, monkeypatch,
):
    """When tariff_constants.json has no entry for the required
    capacity year (summer_year + 1), Stage 6 must NOT crash. It
    emits NO_TARIFF_FOR_CAPACITY_YEAR for the affected layers and
    proceeds with header-only output.

    summer_year=2027 → capacity_year=2028. The locked JSON only
    has rate entries for 2026 and 2027, so 2028 raises KeyError
    inside TariffConstants.load — the loader catches it and
    converts to a reason code.
    """
    from tools.analysis.replay.reason_codes import ReasonCode
    peak_2027 = datetime.datetime(2027, 7, 15, 22, 0, tzinfo=datetime.timezone.utc)
    pjm_df = _build_pjm_5cp_df(
        summer_year=2027, peak_hours_utc=[peak_2027],
    )
    # Provide enough other inputs that Layer 1 would otherwise succeed.
    refoss_rows = [
        {"_time": peak_2027, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 2000.0, "channel": "em:1"},
        {"_time": peak_2027, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 1500.0, "channel": "em:7"},
    ]
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2027-06-01T00:00:00-05:00",
        window_end_ct="2027-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    # Inject a 2027 Monday into the assignment CSV so partition succeeds
    # for at least one arm (still produces INSUFFICIENT_PEAKS_BY_ARM
    # eventually, but the tariff guard should fire FIRST). To isolate
    # the tariff path, give peak both arms by adding a second peak.
    peak_2027_b = datetime.datetime(
        2027, 7, 22, 22, 0, tzinfo=datetime.timezone.utc,
    )
    pjm_df_full = _build_pjm_5cp_df(
        summer_year=2027, peak_hours_utc=[peak_2027, peak_2027_b],
    )
    refoss_rows.extend([
        {"_time": peak_2027_b, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 2000.0, "channel": "em:1"},
        {"_time": peak_2027_b, "_measurement": "refoss.channel",
         "_field": "power_w", "_value": 1500.0, "channel": "em:7"},
    ])
    refoss_df_full = pd.DataFrame(refoss_rows)
    refoss_df_full["_time"] = pd.to_datetime(refoss_df_full["_time"], utc=True)
    # Rewrite the bundle with the full inputs.
    import shutil
    shutil.rmtree(stage1_dir)
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df_full,
            "refoss.channel": refoss_df_full,
        },
        window_start_ct="2027-06-01T00:00:00-05:00",
        window_end_ct="2027-09-30T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        # Two 2027 Mondays alternating arms.
        {"iso_week": "2027-W28", "monday_date": "2027-07-12", "arm": "A"},
        {"iso_week": "2027-W29", "monday_date": "2027-07-19", "arm": "B"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    # Must NOT crash.
    pipeline.stage6_o2(stage1_dir, tmp_path)

    # Layer 1 header-only with NO_TARIFF_FOR_CAPACITY_YEAR.
    with open(tmp_path / "stage6" / "o2_layer1.csv") as f:
        assert list(csv.DictReader(f)) == []
    import json
    with open(tmp_path / "stage6" / "reason_report.json") as f:
        entries = json.load(f)["entries"]
    by_output = {e["output_file"]: e["reason_code"] for e in entries}
    assert by_output["o2_layer1.csv"] == \
        ReasonCode.NO_TARIFF_FOR_CAPACITY_YEAR.value


def test_phase1_layer1_header_only_when_only_one_arm_has_peaks(tmp_path, monkeypatch):
    """Zero-arm-guard companion test. One peak hour falls in an Arm A
    week only; Arm B has zero peaks. Layer 1 must NOT report a real
    delta (compute_a_cust_cpl_kw returns 0.0 for the empty arm).
    Output: header-only + INSUFFICIENT_PEAKS_BY_ARM reason."""
    from tools.analysis.replay.reason_codes import ReasonCode

    peak_a = datetime.datetime(2026, 6, 9, 22, 0, tzinfo=datetime.timezone.utc)

    pjm_df = _build_pjm_5cp_df(
        summer_year=2026, peak_hours_utc=[peak_a],
    )
    refoss_rows = [
        {
            "_time": peak_a, "_measurement": "refoss.channel",
            "_field": "power_w", "_value": 2000.0, "channel": "em:1",
        },
        {
            "_time": peak_a, "_measurement": "refoss.channel",
            "_field": "power_w", "_value": 1500.0, "channel": "em:7",
        },
    ]
    refoss_df = pd.DataFrame(refoss_rows)
    refoss_df["_time"] = pd.to_datetime(refoss_df["_time"], utc=True)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "pjm.coincident_peak": pjm_df,
            "refoss.channel": refoss_df,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    assignment_csv = tmp_path / "assignment.csv"
    _write_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    pipeline.stage6_o2(stage1_dir, tmp_path)

    # Layer 1 is header-only because Arm B has zero peaks.
    with open(tmp_path / "stage6" / "o2_layer1.csv") as f:
        rows = list(csv.DictReader(f))
    assert rows == []

    # Reason code emitted.
    import json
    reason_report = tmp_path / "stage6" / "reason_report.json"
    assert reason_report.exists()
    with open(reason_report) as f:
        entries = json.load(f)["entries"]
    codes = {e["reason_code"] for e in entries}
    assert ReasonCode.INSUFFICIENT_PEAKS_BY_ARM.value in codes
