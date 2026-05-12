"""Real-data replay bundle export and loading.

Implements OSF_FILING.md criterion 14. The manifest format, source
types, and reason codes here are the contract between Stage 1's
export (`stage1_extract` writes parquet files + manifest) and the
downstream stages' loaders (which read those parquet files and
emit reason codes when their inputs are legitimately empty).
"""
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

__all__ = [
    "INJECTED_VALIDATION_CASE",
    "KNOWN_MEASUREMENTS",
    "Manifest",
    "MeasurementEntry",
    "MissingMeasurement",
    "OBSERVED_HISTORICAL",
    "OBSERVED_RECENT",
    "OBSERVED_SOURCE_TYPES",
    "POST_2025_MEASUREMENTS",
    "ReasonCode",
    "SOURCE_TYPES",
    "StageReasonReport",
    "WEATHER_DERIVED_COMPATIBILITY",
    "compute_sha256",
    "parquet_filename",
    "read_manifest",
    "write_manifest",
    "write_reason_report",
]
