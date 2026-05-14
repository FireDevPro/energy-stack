"""Unit tests for tools/analysis/replay/manifest.py + reason_codes.py
+ the _write_stage1_export helper in pipeline.py.

These tests prove the manifest infrastructure works against synthetic
DataFrames — no Influx connection required. The actual stage1_extract
function that queries Influx is exercised separately (operator-run).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tools.analysis import pipeline
from tools.analysis.replay.manifest import (
    INJECTED_VALIDATION_CASE,
    KNOWN_MEASUREMENTS,
    OBSERVED_HISTORICAL,
    OBSERVED_RECENT,
    OBSERVED_SOURCE_TYPES,
    POST_2025_MEASUREMENTS,
    SOURCE_TYPES,
    WEATHER_DERIVED_COMPATIBILITY,
    Manifest,
    MeasurementEntry,
    MissingMeasurement,
    compute_sha256,
    parquet_filename,
    read_manifest,
    write_manifest,
)
from tools.analysis.replay.reason_codes import (
    ReasonCode,
    StageReasonReport,
    write_reason_report,
)


# -- Source-type constants -------------------------------------------------


def test_source_types_set_matches_criterion_14():
    """Exactly four source types per OSF_FILING.md criterion 14."""
    assert SOURCE_TYPES == {
        "observed_historical",
        "observed_recent",
        "weather_derived_compatibility",
        "injected_validation_case",
    }


def test_observed_source_types_is_proper_subset():
    assert OBSERVED_SOURCE_TYPES == {"observed_historical", "observed_recent"}
    assert OBSERVED_SOURCE_TYPES.issubset(SOURCE_TYPES)
    assert "weather_derived_compatibility" not in OBSERVED_SOURCE_TYPES
    assert "injected_validation_case" not in OBSERVED_SOURCE_TYPES


def test_parquet_filename_includes_source_type():
    """Multiple entries per measurement need distinct filenames."""
    assert parquet_filename("ecowitt.weather", OBSERVED_RECENT) == (
        "ecowitt.weather.observed_recent.parquet"
    )
    assert parquet_filename("ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY) == (
        "ecowitt.weather.weather_derived_compatibility.parquet"
    )


def test_parquet_filename_rejects_unknown_source_type():
    with pytest.raises(ValueError):
        parquet_filename("ecowitt.weather", "made_up_source")


def test_measurement_entry_rejects_unknown_source_type():
    with pytest.raises(ValueError):
        MeasurementEntry(
            measurement="comed.prices",
            source_type="not_a_source",
            parquet_path="x.parquet",
            row_count=1,
            sha256="abc",
            field_set=(),
        )


# -- Manifest round-trip ---------------------------------------------------


def test_manifest_round_trip_single_entry(tmp_path):
    original = Manifest(
        export_window_start_ct="2026-04-27T00:00:00-05:00",
        export_window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        exported_at_utc="2026-05-11T22:00:00Z",
        exporter={"version": "stage1_extract", "commit_hash": "abc123"},
        entries=(
            MeasurementEntry(
                measurement="comed.prices",
                source_type=OBSERVED_RECENT,
                parquet_path="comed.prices.observed_recent.parquet",
                row_count=8064,
                sha256="ab12cd34",
                field_set=("price_cents",),
                first_timestamp_utc="2026-04-27T05:00:00Z",
                last_timestamp_utc="2026-05-11T04:55:00Z",
            ),
        ),
        known_missing_measurements=(
            MissingMeasurement(
                measurement="hvac.arm_transitions",
                reason_code="no_arm_assignments_in_window",
                note="Pre-randomization window",
            ),
        ),
    )
    path = tmp_path / "manifest.json"
    write_manifest(original, path)
    loaded = read_manifest(path)
    assert loaded == original


def test_manifest_round_trip_multi_entry_same_measurement(tmp_path):
    """A single measurement (ecowitt.weather) with two entries from
    different source types. Round-trip preserves both."""
    original = Manifest(
        export_window_start_ct="2025-06-01T00:00:00-05:00",
        export_window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        exported_at_utc="2026-05-11T22:00:00Z",
        exporter={"version": "bundle_assembler"},
        entries=(
            MeasurementEntry(
                measurement="ecowitt.weather",
                source_type=OBSERVED_RECENT,
                parquet_path="ecowitt.weather.observed_recent.parquet",
                row_count=4032,
                sha256="aaa",
                field_set=("outdoor_temp_f", "outdoor_dewpoint_f"),
                note="Receiver deployed 2026-05-11; this covers the live window since.",
            ),
            MeasurementEntry(
                measurement="ecowitt.weather",
                source_type=WEATHER_DERIVED_COMPATIBILITY,
                parquet_path="ecowitt.weather.weather_derived_compatibility.parquet",
                row_count=96768,
                sha256="bbb",
                field_set=("outdoor_temp_f", "outdoor_dewpoint_f"),
                note="KORD ASOS 5-min routines, 2025-06 through 2025-09",
            ),
        ),
    )
    path = tmp_path / "manifest.json"
    write_manifest(original, path)
    loaded = read_manifest(path)
    assert loaded == original
    assert len(loaded.entries_for("ecowitt.weather")) == 2
    obs = loaded.entries_for("ecowitt.weather", source_type=OBSERVED_RECENT)
    assert len(obs) == 1
    assert obs[0].row_count == 4032


def test_manifest_has_any_observed_data_true_with_observed_entry():
    m = Manifest(
        export_window_start_ct="x", export_window_end_ct="x",
        source_bucket="energy", exported_at_utc="x", exporter={},
        entries=(MeasurementEntry(
            measurement="comed.prices",
            source_type=OBSERVED_RECENT,
            parquet_path="x", row_count=1, sha256="", field_set=(),
        ),),
    )
    assert m.has_any_observed_data() is True


def test_manifest_has_any_observed_data_false_when_only_synthetic():
    m = Manifest(
        export_window_start_ct="x", export_window_end_ct="x",
        source_bucket="energy", exported_at_utc="x", exporter={},
        entries=(MeasurementEntry(
            measurement="hvac.5cp_state",
            source_type=INJECTED_VALIDATION_CASE,
            parquet_path="x", row_count=1, sha256="", field_set=(),
        ),),
    )
    assert m.has_any_observed_data() is False


def test_manifest_has_any_observed_data_false_when_only_weather_derived():
    """weather_derived_compatibility is NOT observed; alone it doesn't
    satisfy the bundle-level minimum."""
    m = Manifest(
        export_window_start_ct="x", export_window_end_ct="x",
        source_bucket="energy", exported_at_utc="x", exporter={},
        entries=(MeasurementEntry(
            measurement="ecowitt.weather",
            source_type=WEATHER_DERIVED_COMPATIBILITY,
            parquet_path="x", row_count=100, sha256="", field_set=(),
        ),),
    )
    assert m.has_any_observed_data() is False


def test_manifest_has_any_observed_data_false_when_empty():
    m = Manifest(
        export_window_start_ct="x", export_window_end_ct="x",
        source_bucket="energy", exported_at_utc="x", exporter={},
        entries=(),
    )
    assert m.has_any_observed_data() is False


def test_manifest_json_is_stable_keyed(tmp_path):
    m = Manifest(
        export_window_start_ct="2026-04-27T00:00:00-05:00",
        export_window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        exported_at_utc="2026-05-11T22:00:00Z",
        exporter={"version": "stage1_extract"},
        entries=(),
    )
    path = tmp_path / "manifest.json"
    write_manifest(m, path)
    text = path.read_text()
    keys_in_order = [
        line.strip().rstrip(":").strip('"')
        for line in text.splitlines()
        if line.lstrip().startswith('"') and ":" in line
    ]
    top_level_keys = [
        k for k in keys_in_order
        if k in {
            "entries", "export_window_start_ct", "export_window_end_ct",
            "exported_at_utc", "exporter", "known_missing_measurements",
            "source_bucket",
        }
    ]
    assert top_level_keys == sorted(top_level_keys)


# -- SHA-256 helper --------------------------------------------------------


def test_compute_sha256_matches_known_input(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello world")
    assert compute_sha256(path) == (
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )


# -- KNOWN_MEASUREMENTS list ----------------------------------------------


def test_known_measurements_matches_analysis_pipeline_doc():
    expected_subset = {
        "comed.prices", "refoss.channel", "hvac.thermostat",
        "hvac.comfortnet", "hvac.overrides", "hvac.actions",
        "hvac.decisions", "hvac.5cp_state", "hvac.price_overlay",
        "hvac.precool_window", "hvac.arm_transitions",
        "nws.forecast", "ecowitt.weather", "pjm.inst_load",
        "pjm.coincident_peak", "pjm.metered_load",
        "comed.bill", "comed.bill_lineitems",
        # Phase 2 SCED rebaseline: bill-canonical settled hourly LMP
        # for COMED zone (spec §8). Backfilled from 2026-01-01.
        "pjm.lmp_rt_hourly",
    }
    assert set(KNOWN_MEASUREMENTS) == expected_subset


def test_post_2025_measurements_only_contains_new_services():
    assert POST_2025_MEASUREMENTS == {
        "hvac.5cp_state", "hvac.price_overlay",
        "hvac.arm_transitions", "hvac.precool_window",
        "ecowitt.weather",
        # Phase 2 SCED rebaseline: rt_hrl_lmps polling stood up
        # 2026-05; no observed_historical for 2025.
        "pjm.lmp_rt_hourly",
    }


# -- Reason-report writing -------------------------------------------------


def test_write_reason_report_emits_machine_readable_codes(tmp_path):
    reports = [
        StageReasonReport(
            stage="stage4",
            output_file="matched_pairs.csv",
            reason_code=ReasonCode.SINGLE_ARM_IN_WINDOW,
            note="Pre-randomization: window contains only Arm A weeks",
        ),
        StageReasonReport(
            stage="stage7",
            output_file="sced_pvalues.csv",
            reason_code=ReasonCode.NO_PAIR_DIFFERENCES_FROM_STAGE5,
            related_inputs=("stage5/pair_diffs.csv",),
        ),
    ]
    out_path = write_reason_report(tmp_path, reports)
    data = json.loads(out_path.read_text())
    codes = [e["reason_code"] for e in data["entries"]]
    assert "single_arm_in_window" in codes
    assert "no_pair_differences_from_stage5" in codes
    assert data["entries"][1]["related_inputs"] == ["stage5/pair_diffs.csv"]


def test_write_reason_report_empty_list_still_writes_file(tmp_path):
    path = write_reason_report(tmp_path, [])
    assert path.exists()
    assert json.loads(path.read_text()) == {"entries": []}


# -- _write_stage1_export helper ------------------------------------------


def _make_synthetic_measurement_df(
    measurement: str,
    n_rows: int = 5,
    field: str = "value",
) -> pd.DataFrame:
    return pd.DataFrame({
        "_time": pd.date_range("2026-05-01", periods=n_rows, freq="5min", tz="UTC"),
        "_measurement": [measurement] * n_rows,
        "_field": [field] * n_rows,
        "_value": [i * 1.5 for i in range(n_rows)],
    })


def test_write_stage1_export_emits_observed_recent_by_default(tmp_path):
    """Default source_type is observed_recent since stage1_extract
    typically runs against current Influx data."""
    dfs = {
        "comed.prices": _make_synthetic_measurement_df(
            "comed.prices", n_rows=12, field="price_cents",
        ),
    }
    manifest = pipeline._write_stage1_export(
        stage_dir=tmp_path / "stage1",
        measurement_dataframes=dfs,
        window_start_ct="2026-05-01T00:00:00-05:00",
        window_end_ct="2026-05-02T00:00:00-05:00",
        source_bucket="energy",
    )
    assert len(manifest.entries) == 1
    assert manifest.entries[0].source_type == OBSERVED_RECENT
    assert manifest.entries[0].parquet_path == (
        "comed.prices.observed_recent.parquet"
    )
    assert (tmp_path / "stage1" / "comed.prices.observed_recent.parquet").exists()


def test_write_stage1_export_observed_historical_source_type(tmp_path):
    """Caller can mark the bundle as observed_historical for a past-
    window extract (e.g., re-extracting 2025 ComEd RTP)."""
    dfs = {
        "comed.prices": _make_synthetic_measurement_df(
            "comed.prices", n_rows=8, field="price_cents",
        ),
    }
    manifest = pipeline._write_stage1_export(
        stage_dir=tmp_path / "stage1",
        measurement_dataframes=dfs,
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2025-09-30T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_HISTORICAL,
    )
    assert manifest.entries[0].source_type == OBSERVED_HISTORICAL
    assert manifest.entries[0].parquet_path == (
        "comed.prices.observed_historical.parquet"
    )


def test_write_stage1_export_rejects_unknown_source_type(tmp_path):
    with pytest.raises(ValueError):
        pipeline._write_stage1_export(
            stage_dir=tmp_path / "stage1",
            measurement_dataframes={},
            window_start_ct="2026-05-01T00:00:00-05:00",
            window_end_ct="2026-05-02T00:00:00-05:00",
            source_bucket="energy",
            source_type="invented_type",
        )


def test_write_stage1_export_post_2025_missing_with_historical_source(tmp_path):
    """observed_historical against a post-2025 measurement → reason
    code POST_2025_MEASUREMENT_NO_HISTORY (legitimately can't have data)."""
    manifest = pipeline._write_stage1_export(
        stage_dir=tmp_path / "stage1",
        measurement_dataframes={},
        window_start_ct="2025-06-01T00:00:00-05:00",
        window_end_ct="2025-09-30T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_HISTORICAL,
    )
    missing_meas = {m.measurement: m for m in manifest.known_missing_measurements}
    for m in POST_2025_MEASUREMENTS:
        assert missing_meas[m].reason_code == "post_2025_measurement_no_history"


def test_write_stage1_export_post_2025_missing_with_recent_source(tmp_path):
    """observed_recent against a measurement that's currently empty in
    Influx → reason code MEASUREMENT_EMPTY_IN_WINDOW (the service
    exists but didn't write any rows for the window)."""
    manifest = pipeline._write_stage1_export(
        stage_dir=tmp_path / "stage1",
        measurement_dataframes={},
        window_start_ct="2026-05-01T00:00:00-05:00",
        window_end_ct="2026-05-02T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_RECENT,
    )
    missing_meas = {m.measurement: m for m in manifest.known_missing_measurements}
    # Even hvac.5cp_state (a post-2025 measurement) gets the
    # in-window reason because the source_type is observed_recent
    assert missing_meas["hvac.5cp_state"].reason_code == "measurement_empty_in_window"


def test_write_stage1_export_no_empty_parquet_files(tmp_path):
    """Empty DataFrames must NOT produce empty parquet files."""
    dfs = {
        "comed.prices": _make_synthetic_measurement_df(
            "comed.prices", n_rows=12, field="price_cents",
        ),
    }
    pipeline._write_stage1_export(
        stage_dir=tmp_path / "stage1",
        measurement_dataframes=dfs,
        window_start_ct="2026-05-01T00:00:00-05:00",
        window_end_ct="2026-05-02T00:00:00-05:00",
        source_bucket="energy",
    )
    parquet_files = sorted((tmp_path / "stage1").glob("*.parquet"))
    assert [p.name for p in parquet_files] == [
        "comed.prices.observed_recent.parquet",
    ]


def test_write_stage1_export_sha256_matches_file_content(tmp_path):
    df = _make_synthetic_measurement_df("comed.prices", n_rows=5, field="price_cents")
    manifest = pipeline._write_stage1_export(
        stage_dir=tmp_path / "stage1",
        measurement_dataframes={"comed.prices": df},
        window_start_ct="2026-05-01T00:00:00-05:00",
        window_end_ct="2026-05-02T00:00:00-05:00",
        source_bucket="energy",
    )
    parquet_path = tmp_path / "stage1" / "comed.prices.observed_recent.parquet"
    assert manifest.entries[0].sha256 == compute_sha256(parquet_path)


def test_write_stage1_export_first_last_timestamps_recorded(tmp_path):
    df = _make_synthetic_measurement_df("comed.prices", n_rows=10, field="price_cents")
    manifest = pipeline._write_stage1_export(
        stage_dir=tmp_path / "stage1",
        measurement_dataframes={"comed.prices": df},
        window_start_ct="2026-05-01T00:00:00-05:00",
        window_end_ct="2026-05-02T00:00:00-05:00",
        source_bucket="energy",
    )
    entry = manifest.entries[0]
    assert entry.first_timestamp_utc is not None
    assert entry.last_timestamp_utc is not None
    assert entry.first_timestamp_utc < entry.last_timestamp_utc
