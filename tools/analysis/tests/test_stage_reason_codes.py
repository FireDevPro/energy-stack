"""Per-stage reason-code emission tests (OSF criterion 14(c)).

When a stage produces an empty output for a legitimate, classifiable
reason (e.g., no arm assignments in the window, no qualifying weeks
from upstream), it MUST also write a reason_report.json so that an
audit script can distinguish "the pipeline found no effect" from
"the pipeline ran but had nothing to operate on."
"""
from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path

import pytest

from tools.analysis import pipeline
from tools.analysis.replay.manifest import OBSERVED_RECENT
from tools.analysis.replay.reason_codes import ReasonCode
from tools.analysis.tests.fixture_real_shape import (
    build_refoss_channel_df,
    write_bundle,
)


def _read_reason_report(stage_dir: Path) -> list[dict]:
    """Helper: read reason_report.json from a stage dir, return entries
    list (or empty list if absent)."""
    report_path = stage_dir / "reason_report.json"
    if not report_path.exists():
        return []
    with open(report_path) as f:
        data = json.load(f)
    return data.get("entries", [])


def _write_test_assignment_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["iso_week", "monday_date", "arm"])
        w.writeheader()
        for row in rows:
            w.writerow(row)


def test_stage2_emits_reason_when_no_week_inputs(tmp_path, monkeypatch):
    """Bundle's window is before randomization start (no Mondays in
    assignment CSV fall in window) → Stage 2 emits
    NO_ARM_ASSIGNMENTS_IN_WINDOW."""
    # Build a tiny bundle whose window is before any assignment dates
    start = datetime.datetime(2026, 5, 1, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 5, 8, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_refoss_channel_df(start, end, cadence_minutes=1)
    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": df},
        window_start_ct="2026-05-01T00:00:00-05:00",
        window_end_ct="2026-05-08T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    # Assignment CSV has entries but none in this window
    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    out_dir = tmp_path
    pipeline.stage2_quality(stage1_dir, out_dir)

    entries = _read_reason_report(out_dir / "stage2")
    assert len(entries) == 1
    e = entries[0]
    assert e["stage"] == "stage2"
    assert e["reason_code"] == ReasonCode.NO_ARM_ASSIGNMENTS_IN_WINDOW.value
    assert e["output_file"] == "qualifying_weeks.csv"


def test_stage2_no_reason_when_weeks_qualify(tmp_path, monkeypatch):
    """When Stage 2 produces non-empty qualifying_weeks, no reason
    report is emitted (or the report is empty)."""
    start = datetime.datetime(2026, 6, 8, 5, 0, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 6, 15, 5, 0, tzinfo=datetime.timezone.utc)
    df = build_refoss_channel_df(start, end, cadence_minutes=1)
    stage1_dir = tmp_path / "stage1"
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": df},
        window_start_ct="2026-06-08T00:00:00-05:00",
        window_end_ct="2026-06-15T00:00:00-05:00",
        source_type=OBSERVED_RECENT,
    )
    assignment_csv = tmp_path / "assignment.csv"
    _write_test_assignment_csv(assignment_csv, [
        {"iso_week": "2026-W24", "monday_date": "2026-06-08", "arm": "A"},
    ])
    monkeypatch.setattr(pipeline, "ASSIGNMENT_CSV_PATH", assignment_csv)

    out_dir = tmp_path
    pipeline.stage2_quality(stage1_dir, out_dir)

    entries = _read_reason_report(out_dir / "stage2")
    assert entries == []


def test_stage4_emits_reason_when_single_arm(tmp_path):
    """When Stage 3 has qualifying weeks for only one arm, Stage 4
    emits SINGLE_ARM_IN_WINDOW."""
    import numpy as np
    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    weekly_csv = stage3_dir / "weekly.csv"
    # Build a weekly.csv with only Arm A
    with open(weekly_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pipeline.WEEKLY_CSV_LOCKED_COLUMNS))
        w.writeheader()
        row = {col: 0.0 for col in pipeline.WEEKLY_CSV_LOCKED_COLUMNS}
        row.update({
            "week_start_ct": "2026-06-08", "arm": "A", "qualifies": True,
        })
        w.writerow(row)

    cov_path = tmp_path / "baseline_cov.npz"
    np.savez(cov_path, cov=np.eye(6), mean=np.zeros(6))

    pipeline.stage4_matching(stage3_dir, cov_path, tmp_path)

    entries = _read_reason_report(tmp_path / "stage4")
    assert len(entries) == 1
    assert entries[0]["reason_code"] == ReasonCode.SINGLE_ARM_IN_WINDOW.value


def test_stage4_emits_reason_when_no_qualifying_weeks(tmp_path):
    """When weekly.csv has zero qualifying rows for either arm, Stage 4
    emits INSUFFICIENT_QUALIFYING_WEEKS_PER_ARM."""
    import numpy as np
    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    weekly_csv = stage3_dir / "weekly.csv"
    with open(weekly_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pipeline.WEEKLY_CSV_LOCKED_COLUMNS))
        w.writeheader()

    cov_path = tmp_path / "baseline_cov.npz"
    np.savez(cov_path, cov=np.eye(6), mean=np.zeros(6))

    pipeline.stage4_matching(stage3_dir, cov_path, tmp_path)

    entries = _read_reason_report(tmp_path / "stage4")
    assert len(entries) == 1
    assert entries[0]["reason_code"] == ReasonCode.INSUFFICIENT_QUALIFYING_WEEKS_PER_ARM.value


def test_stage5_emits_reason_when_no_primary_pairs(tmp_path):
    """When Stage 4's matched_pairs.csv has no primary pairs, Stage 5
    emits NO_PRIMARY_QUALITY_PAIRS."""
    stage3_dir = tmp_path / "stage3"
    stage3_dir.mkdir()
    with open(stage3_dir / "weekly.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pipeline.WEEKLY_CSV_LOCKED_COLUMNS))
        w.writeheader()
    stage4_dir = tmp_path / "stage4"
    stage4_dir.mkdir()
    with open(stage4_dir / "matched_pairs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pair_id", "week_a", "week_b", "distance", "quality"])

    pipeline.stage5_effects(stage3_dir, stage4_dir, tmp_path)

    entries = _read_reason_report(tmp_path / "stage5")
    assert len(entries) == 1
    assert entries[0]["reason_code"] == ReasonCode.NO_PRIMARY_QUALITY_PAIRS.value


def test_stage7_emits_reason_when_no_pair_diffs(tmp_path):
    """When Stage 5's pair_diffs.csv has no rows, Stage 7 emits
    NO_PAIR_DIFFERENCES_FROM_STAGE5."""
    stage5_dir = tmp_path / "stage5"
    stage5_dir.mkdir()
    with open(stage5_dir / "pair_diffs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outcome", "pair_id", "diff"])

    pipeline.stage7_sced(stage5_dir, tmp_path)

    entries = _read_reason_report(tmp_path / "stage7")
    assert len(entries) == 1
    assert entries[0]["reason_code"] == ReasonCode.NO_PAIR_DIFFERENCES_FROM_STAGE5.value


def test_stage3_emits_reason_when_stage2_empty(tmp_path):
    """When Stage 2's qualifying_weeks.csv is header-only, Stage 3
    emits NO_QUALIFYING_WEEKS_FROM_STAGE2."""
    stage1_dir = tmp_path / "stage1"
    stage1_dir.mkdir()
    stage2_dir = tmp_path / "stage2"
    stage2_dir.mkdir()
    # Write a header-only qualifying_weeks.csv
    qual_csv = stage2_dir / "qualifying_weeks.csv"
    with open(qual_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pipeline.QUALIFYING_WEEKS_LOCKED_COLUMNS))
        w.writeheader()

    pipeline.stage3_weekly(stage1_dir, tmp_path, tmp_path)

    entries = _read_reason_report(tmp_path / "stage3")
    assert len(entries) == 1
    e = entries[0]
    assert e["stage"] == "stage3"
    assert e["reason_code"] == ReasonCode.NO_QUALIFYING_WEEKS_FROM_STAGE2.value
    assert e["output_file"] == "weekly.csv"
