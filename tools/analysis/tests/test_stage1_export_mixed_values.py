"""Stage 1 exporter: mixed numeric + string _value handling.

Real-data finding from the 2026-05-12 replay-validation run:
hvac.actions writes both numeric fields (cool_setpoint_f, etc.) and
string fields (fan_mode, setpoint_reason, supervisor_reason, error,
hvac_mode_before) into the same long-format `_value` column.
``df.to_parquet`` crashes with ``ArrowInvalid: Could not convert
'Auto' with type str: tried to convert to double`` because the
column ends up object dtype with mixed types.

Fix contract pinned by these tests:

- Numeric values are coerced to ``_value`` (float64; NaN for rows
  that originated as strings).
- String values are preserved in a new ``_value_text`` column.
- Manifest ``field_set`` lists ALL fields (numeric + string).
- A measurement with only string-valued fields writes parquet
  successfully and IS NOT marked missing.
- Downstream loaders that consume numeric ``_value`` see no change
  in behavior for numeric fields.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pandas as pd
import pytest

from tools.analysis.pipeline import _write_stage1_export
from tools.analysis.replay.manifest import OBSERVED_RECENT, read_manifest


def _mixed_hvac_actions_df() -> pd.DataFrame:
    """Synthetic hvac.actions long-format with numeric AND string
    `_value` rows at the same timestamp — mirrors production shape."""
    base_ts = datetime.datetime(2026, 5, 10, 17, 0, tzinfo=datetime.timezone.utc)
    rows = []
    for offset_min, tag_vals in [(0, {}), (5, {})]:
        ts = base_ts + datetime.timedelta(minutes=offset_min)
        # Numeric fields
        for field, value in [
            ("cool_setpoint_f", 76.0),
            ("heat_setpoint_f", 68.0),
            ("applied", 1.0),
        ]:
            rows.append({
                "_time": ts, "_measurement": "hvac.actions",
                "_field": field, "_value": value,
                "day_type": "HOT", "action_label": "HOT_PRE_COOL",
                "dry_run": "true",
            })
        # String fields
        for field, value in [
            ("fan_mode", "Auto"),
            ("setpoint_reason", "schedule"),
            ("hvac_mode_before", "cool"),
        ]:
            rows.append({
                "_time": ts, "_measurement": "hvac.actions",
                "_field": field, "_value": value,
                "day_type": "HOT", "action_label": "HOT_PRE_COOL",
                "dry_run": "true",
            })
    df = pd.DataFrame(rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def test_mixed_value_types_dont_crash_parquet_write(tmp_path):
    """Regression test for ArrowInvalid on object-dtype `_value`."""
    df = _mixed_hvac_actions_df()
    stage_dir = tmp_path / "stage1"
    _write_stage1_export(
        stage_dir=stage_dir,
        measurement_dataframes={"hvac.actions": df},
        window_start_ct="2026-05-10T00:00:00-05:00",
        window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_RECENT,
    )
    manifest = read_manifest(stage_dir / "manifest.json")
    entries = manifest.entries_for("hvac.actions", OBSERVED_RECENT)
    assert len(entries) == 1
    # Parquet file was written, not just header-only.
    parquet_path = stage_dir / entries[0].parquet_path
    assert parquet_path.exists()
    written = pd.read_parquet(parquet_path)
    assert len(written) > 0


def test_numeric_rows_preserved_in_value_column(tmp_path):
    """Rows whose _value is numeric land in a clean float64 _value
    column. Loaders that filter by _field then read _value continue
    to work unchanged."""
    df = _mixed_hvac_actions_df()
    stage_dir = tmp_path / "stage1"
    _write_stage1_export(
        stage_dir=stage_dir,
        measurement_dataframes={"hvac.actions": df},
        window_start_ct="2026-05-10T00:00:00-05:00",
        window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_RECENT,
    )
    manifest = read_manifest(stage_dir / "manifest.json")
    entry = manifest.entries_for("hvac.actions", OBSERVED_RECENT)[0]
    written = pd.read_parquet(stage_dir / entry.parquet_path)
    cool_rows = written[written["_field"] == "cool_setpoint_f"]
    # Numeric field → all 2 rows present with the original numeric value.
    assert len(cool_rows) == 2
    assert (cool_rows["_value"].astype(float) == 76.0).all()


def test_string_values_preserved_in_value_text_column(tmp_path):
    """Rows whose _value is a string survive in a `_value_text`
    column. The original _value column is NaN for those rows so the
    numeric float64 typing is preserved."""
    df = _mixed_hvac_actions_df()
    stage_dir = tmp_path / "stage1"
    _write_stage1_export(
        stage_dir=stage_dir,
        measurement_dataframes={"hvac.actions": df},
        window_start_ct="2026-05-10T00:00:00-05:00",
        window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_RECENT,
    )
    manifest = read_manifest(stage_dir / "manifest.json")
    entry = manifest.entries_for("hvac.actions", OBSERVED_RECENT)[0]
    written = pd.read_parquet(stage_dir / entry.parquet_path)
    assert "_value_text" in written.columns
    fan = written[written["_field"] == "fan_mode"]
    assert len(fan) == 2
    assert (fan["_value_text"] == "Auto").all()
    # _value for string-origin rows is NaN, NOT 0.0. The distinction
    # matters: a 0.0 sentinel would slip past loaders that filter on
    # `_value > 0`, silently treating audit strings like
    # `fan_mode="Auto"` as real numeric zeros.
    assert fan["_value"].isna().all()
    # Belt-and-suspenders against a future regression that introduces
    # fillna(0) or similar:
    assert not (fan["_value"] == 0.0).any()


def test_manifest_field_set_includes_both_numeric_and_string_fields(tmp_path):
    """field_set must list every field name appearing in the
    measurement, regardless of whether the value was numeric or
    string."""
    df = _mixed_hvac_actions_df()
    stage_dir = tmp_path / "stage1"
    _write_stage1_export(
        stage_dir=stage_dir,
        measurement_dataframes={"hvac.actions": df},
        window_start_ct="2026-05-10T00:00:00-05:00",
        window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_RECENT,
    )
    manifest = read_manifest(stage_dir / "manifest.json")
    entry = manifest.entries_for("hvac.actions", OBSERVED_RECENT)[0]
    expected_fields = {
        "cool_setpoint_f", "heat_setpoint_f", "applied",     # numeric
        "fan_mode", "setpoint_reason", "hvac_mode_before",   # string
    }
    assert set(entry.field_set) == expected_fields


def test_string_only_measurement_writes_parquet_not_marked_missing(tmp_path):
    """A measurement whose every row has a string _value must write
    parquet successfully and NOT appear in known_missing_measurements."""
    base_ts = datetime.datetime(2026, 5, 10, 17, 0, tzinfo=datetime.timezone.utc)
    rows = [{
        "_time": base_ts,
        "_measurement": "hvac.actions",
        "_field": "fan_mode",
        "_value": "Auto",
    }]
    df = pd.DataFrame(rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)

    stage_dir = tmp_path / "stage1"
    _write_stage1_export(
        stage_dir=stage_dir,
        measurement_dataframes={"hvac.actions": df},
        window_start_ct="2026-05-10T00:00:00-05:00",
        window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_RECENT,
    )
    manifest = read_manifest(stage_dir / "manifest.json")
    entries = manifest.entries_for("hvac.actions", OBSERVED_RECENT)
    assert len(entries) == 1
    # NOT marked missing.
    missing_meas = {m.measurement for m in manifest.known_missing_measurements}
    assert "hvac.actions" not in missing_meas
    # field_set still has the string field.
    assert "fan_mode" in entries[0].field_set


def test_all_numeric_measurement_unchanged_behavior(tmp_path):
    """A measurement whose every row is numeric writes parquet with
    a clean _value float64 column. The _value_text column may exist
    (all-null) or be absent; downstream code tolerates both."""
    base_ts = datetime.datetime(2026, 5, 10, 17, 0, tzinfo=datetime.timezone.utc)
    rows = [{
        "_time": base_ts,
        "_measurement": "refoss.channel",
        "_field": "power_w",
        "_value": 1500.0,
        "channel": "em:1",
    }]
    df = pd.DataFrame(rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)

    stage_dir = tmp_path / "stage1"
    _write_stage1_export(
        stage_dir=stage_dir,
        measurement_dataframes={"refoss.channel": df},
        window_start_ct="2026-05-10T00:00:00-05:00",
        window_end_ct="2026-05-11T00:00:00-05:00",
        source_bucket="energy",
        source_type=OBSERVED_RECENT,
    )
    manifest = read_manifest(stage_dir / "manifest.json")
    entry = manifest.entries_for("refoss.channel", OBSERVED_RECENT)[0]
    written = pd.read_parquet(stage_dir / entry.parquet_path)
    assert (written["_value"].astype(float) == 1500.0).all()
