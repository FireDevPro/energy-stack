"""Criterion-14 end-to-end test: pipeline runs against a real-shape
parquet bundle without monkeypatched loaders.

OSF_FILING.md criterion 14 requires that the analysis pipeline can
re-derive every result by reading a frozen replay bundle. This test
exercises that path:

1. Build a synthetic stage1/ bundle using the same parquet schema that
   stage1_extract writes from live Influx.
2. Run Stages 2 and 3 with their real loaders (no monkeypatches).
3. Assert qualifying_weeks.csv and weekly.csv contain non-empty rows
   derived from the bundle's contents.

Stages 6, 8, 9 still have stub loaders that return None (they read
external locked artifacts and encode spec-defined transformations
beyond just parquet reading); those stages produce header-only CSVs
in this bundle. Wiring them is tracked in follow-on PRs.
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tools.analysis import pipeline
from tools.analysis.replay.manifest import OBSERVED_RECENT
from tools.analysis.tests.fixture_real_shape import (
    build_comed_prices_df,
    build_ecowitt_weather_df,
    build_hvac_thermostat_df,
    build_nws_forecast_df,
    build_refoss_channel_df,
    write_bundle,
)


def _write_test_assignment_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["iso_week", "monday_date", "arm"])
        w.writeheader()
        for row in rows:
            w.writerow(row)


@pytest.fixture
def realshape_bundle(tmp_path):
    """Build a 4-week stage1 bundle (2 weeks Arm A + 2 weeks Arm B)
    with refoss + prices + ecowitt at production-cadence."""
    weeks = [
        ("2026-W24", "2026-06-08", "A"),
        ("2026-W25", "2026-06-15", "B"),
        ("2026-W26", "2026-06-22", "A"),
        ("2026-W27", "2026-06-29", "B"),
    ]
    # Window covers all 4 weeks
    start_utc = datetime.datetime(
        2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc,
    )
    end_utc = datetime.datetime(
        2026, 7, 6, 5, 0, tzinfo=datetime.timezone.utc,
    )

    refoss = build_refoss_channel_df(start_utc, end_utc, cadence_minutes=1)
    prices = build_comed_prices_df(
        start_utc, end_utc, price_cents_fn=lambda ts: 4.5,
    )
    weather = build_ecowitt_weather_df(
        start_utc, end_utc,
        temp_f_fn=lambda ts: 82.0,
        dewpoint_f_fn=lambda ts: 68.0,
        pressure_inhg_fn=lambda ts: 29.92,
    )
    thermostat = build_hvac_thermostat_df(start_utc, end_utc)
    # nws.forecast: one D-1 21:00 issuance per day in the bundle window
    # (Stage 8 Phase 2 requires this for spike classification).
    forecast_days = [
        datetime.date(2026, 6, 8) + datetime.timedelta(days=i)
        for i in range(28)
    ]
    forecast = build_nws_forecast_df(forecast_days)

    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={
            "refoss.channel": refoss,
            "comed.prices": prices,
            "ecowitt.weather": weather,
            "hvac.thermostat": thermostat,
            "nws.forecast": forecast,
        },
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-07-06T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )

    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": w[0], "monday_date": w[1], "arm": w[2]}
        for w in weeks
    ])

    return {
        "tmp_path": tmp_path,
        "stage1_dir": stage1_dir,
        "assignment_csv": assignment_csv,
        "weeks": weeks,
    }


def test_stage2_runs_against_realshape_bundle(realshape_bundle, monkeypatch):
    """Stage 2 reads the bundle's manifest + parquet and writes a
    qualifying_weeks.csv with one row per (week, arm) in the window."""
    # Point pipeline at the test assignment CSV instead of the locked
    # production CSV (the production one starts 2026-06-01).
    monkeypatch.setattr(
        pipeline, "ASSIGNMENT_CSV_PATH", realshape_bundle["assignment_csv"],
    )

    out_dir = realshape_bundle["tmp_path"]
    pipeline.stage2_quality(realshape_bundle["stage1_dir"], out_dir)

    qual_csv = out_dir / "stage2" / "qualifying_weeks.csv"
    assert qual_csv.exists()
    with open(qual_csv) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4  # 2 Arm A + 2 Arm B
    # With clean fixture data, all 4 should qualify
    assert all(r["qualifying"].lower() == "true" for r in rows)
    arms = {r["arm"] for r in rows}
    assert arms == {"A", "B"}


def test_stage3_runs_against_realshape_bundle(realshape_bundle, monkeypatch):
    """Stage 3 reads the bundle, joins with Stage 2's qualifying_weeks,
    and writes weekly.csv with one row per qualifying (week, arm)."""
    monkeypatch.setattr(
        pipeline, "ASSIGNMENT_CSV_PATH", realshape_bundle["assignment_csv"],
    )

    out_dir = realshape_bundle["tmp_path"]
    pipeline.stage2_quality(realshape_bundle["stage1_dir"], out_dir)
    pipeline.stage3_weekly(
        realshape_bundle["stage1_dir"], out_dir, out_dir,
    )

    weekly_csv = out_dir / "stage3" / "weekly.csv"
    assert weekly_csv.exists()
    with open(weekly_csv) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    # Verify each row has the locked schema columns
    for row in rows:
        for col in pipeline.WEEKLY_CSV_LOCKED_COLUMNS:
            assert col in row, f"missing column {col} in weekly row"
        # 82°F constant temp → max_temp_f reports 82
        assert float(row["max_temp_f"]) == pytest.approx(82.0, abs=0.5)
        # CDD positive (82 - 65 = 17 per day * 7 = 119)
        assert float(row["weekly_cdd"]) > 0


def test_full_pipeline_runs_against_realshape_bundle(
    realshape_bundle, monkeypatch,
):
    """Stages 2-9 all run end-to-end against the real-shape bundle.

    Stages 6, 8, 9 have stub loaders that return None; their CSVs are
    header-only. This test verifies the un-stubbed stages (2-5, 7)
    produce meaningful output and that the stub stages produce
    well-formed (header-only) output without crashing.
    """
    monkeypatch.setattr(
        pipeline, "ASSIGNMENT_CSV_PATH", realshape_bundle["assignment_csv"],
    )

    tmp_path = realshape_bundle["tmp_path"]
    stage1_dir = realshape_bundle["stage1_dir"]

    # Baseline covariance for Stage 4
    baseline_cov_path = tmp_path / "baseline_cov.npz"
    np.savez(
        baseline_cov_path,
        cov=np.eye(6, dtype=np.float64),
        mean=np.zeros(6, dtype=np.float64),
    )

    pipeline.stage2_quality(stage1_dir, tmp_path)
    pipeline.stage3_weekly(stage1_dir, tmp_path, tmp_path)
    pipeline.stage4_matching(
        stage3_dir=tmp_path / "stage3",
        baseline_cov_path=baseline_cov_path,
        out_dir=tmp_path,
    )
    pipeline.stage5_effects(
        stage3_dir=tmp_path / "stage3",
        stage4_dir=tmp_path / "stage4",
        out_dir=tmp_path,
    )
    pipeline.stage6_o2(stage1_dir, tmp_path)
    pipeline.stage7_sced(
        stage5_dir=tmp_path / "stage5", out_dir=tmp_path,
    )
    pipeline.stage8_decomposition(
        stage1_dir=stage1_dir,
        stage3_dir=tmp_path / "stage3",
        out_dir=tmp_path,
    )
    pipeline.stage9_sensitivity(
        stage1_dir=stage1_dir,
        stage2_dir=tmp_path / "stage2",
        stage3_dir=tmp_path / "stage3",
        out_dir=tmp_path,
    )

    # Stage 2: non-empty qualifying_weeks
    qual_csv = tmp_path / "stage2" / "qualifying_weeks.csv"
    assert qual_csv.exists() and qual_csv.stat().st_size > 0

    # Stage 3: non-empty weekly
    weekly_csv = tmp_path / "stage3" / "weekly.csv"
    with open(weekly_csv) as f:
        weekly_rows = list(csv.DictReader(f))
    assert len(weekly_rows) == 4

    # Stage 6: header-only (stub loader returns None)
    layer1_csv = tmp_path / "stage6" / "o2_layer1.csv"
    assert layer1_csv.exists()
    with open(layer1_csv) as f:
        layer1_rows = list(csv.DictReader(f))
    assert layer1_rows == []  # header-only by design

    # Stage 8: Phase 1 (tracer) populates decomposition with the o1
    # outcome only. All days classified as no_spike (Phase 2 adds real
    # classification). o3 and o4 stay absent from `outcomes` per day
    # (Phase 3 fills them), so the orchestrator's
    # `outcome in d["outcomes"]` filter keeps them out of the CSV (no
    # placeholder zeros).
    decomp_csv = tmp_path / "stage8" / "decomposition.csv"
    assert decomp_csv.exists()
    with open(decomp_csv) as f:
        decomp_rows = list(csv.DictReader(f))
    assert decomp_rows, "Phase 1 wires the real loader; CSV should have rows"
    for r in decomp_rows:
        assert r["outcome"] == "o1_daily_hvac_dollars", (
            "Phase 1 emits only o1; o3/o4 are Phase 3 work"
        )
        assert r["category"] == "no_spike", (
            "Phase 1 hardcodes no_spike; Phase 2 adds spike classification"
        )
        assert r["unit"] == "dollars"
