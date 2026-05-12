"""Manifest format for real-data replay bundles.

Contract per `docs/OSF_FILING.md` criterion 14 and
`docs/REPLAY_VALIDATION.md`. A replay bundle is a directory
containing:

  - Per-(measurement, source_type) parquet files, named
    `<measurement>.<source_type>.parquet`. A single measurement
    may have multiple entries with different source types (e.g.,
    `ecowitt.weather.observed_recent.parquet` for the post-receiver
    window AND `ecowitt.weather.weather_derived_compatibility.parquet`
    for the earlier window).
  - One `manifest.json` describing the window, every entry's source
    type, row counts, content hashes, and missing measurements with
    reason codes.

The manifest's `entries` is a list, not a dict — measurements appear
multiple times when they have data from multiple source types.

Loaders read the manifest first, then consume the parquet files it
points at. If a measurement has zero entries OR is in
`known_missing_measurements`, loaders emit the corresponding reason
code rather than failing.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
from pathlib import Path
from typing import Any


# Source-type catalog per docs/REPLAY_VALIDATION.md. Exactly one
# applies to each manifest entry.
OBSERVED_HISTORICAL = "observed_historical"
OBSERVED_RECENT = "observed_recent"
WEATHER_DERIVED_COMPATIBILITY = "weather_derived_compatibility"
INJECTED_VALIDATION_CASE = "injected_validation_case"

SOURCE_TYPES: frozenset[str] = frozenset({
    OBSERVED_HISTORICAL,
    OBSERVED_RECENT,
    WEATHER_DERIVED_COMPATIBILITY,
    INJECTED_VALIDATION_CASE,
})

OBSERVED_SOURCE_TYPES: frozenset[str] = frozenset({
    OBSERVED_HISTORICAL,
    OBSERVED_RECENT,
})


# All measurements the analysis pipeline reads, per
# ANALYSIS_PIPELINE.md §2.1.
KNOWN_MEASUREMENTS: tuple[str, ...] = (
    "comed.bill",
    "comed.bill_lineitems",
    "comed.prices",
    "ecowitt.weather",
    "hvac.5cp_state",
    "hvac.actions",
    "hvac.arm_transitions",
    "hvac.comfortnet",
    "hvac.decisions",
    "hvac.overrides",
    "hvac.precool_window",
    "hvac.price_overlay",
    "hvac.thermostat",
    "nws.forecast",
    "pjm.coincident_peak",
    "pjm.inst_load",
    "pjm.metered_load",
    "refoss.channel",
)


# Measurements that did not exist in summer 2025. Cannot be
# observed_historical; must come from observed_recent,
# weather_derived_compatibility (ecowitt only), or
# injected_validation_case.
POST_2025_MEASUREMENTS: frozenset[str] = frozenset({
    "hvac.5cp_state",
    "hvac.price_overlay",
    "hvac.arm_transitions",
    "hvac.precool_window",
    "ecowitt.weather",
})


@dataclasses.dataclass(frozen=True)
class MeasurementEntry:
    """One entry in the bundle's manifest. Each entry has a single
    source type; a measurement may have multiple entries with
    different source types."""
    measurement: str
    source_type: str            # one of the SOURCE_TYPES constants
    parquet_path: str           # relative to manifest.json
    row_count: int
    sha256: str
    field_set: tuple[str, ...]
    first_timestamp_utc: str | None = None
    last_timestamp_utc: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(
                f"unknown source_type {self.source_type!r}; "
                f"must be one of {sorted(SOURCE_TYPES)}"
            )


@dataclasses.dataclass(frozen=True)
class MissingMeasurement:
    """A measurement that was queried but produced zero rows, or that
    was deliberately omitted."""
    measurement: str
    reason_code: str
    note: str | None = None


@dataclasses.dataclass(frozen=True)
class Manifest:
    """Canonical description of a replay bundle."""
    export_window_start_ct: str
    export_window_end_ct: str
    source_bucket: str
    exported_at_utc: str
    exporter: dict[str, Any]
    entries: tuple[MeasurementEntry, ...]
    known_missing_measurements: tuple[MissingMeasurement, ...] = ()

    def entries_for(
        self, measurement: str,
        source_type: str | None = None,
    ) -> list[MeasurementEntry]:
        """All manifest entries for a measurement, optionally filtered
        to one source type. Returns empty list if none."""
        out = [e for e in self.entries if e.measurement == measurement]
        if source_type is not None:
            out = [e for e in out if e.source_type == source_type]
        return out

    def has_any_observed_data(self) -> bool:
        """Bundle-level minimum gate (criterion 14): at least one
        measurement has at least one observed_* entry."""
        return any(e.source_type in OBSERVED_SOURCE_TYPES for e in self.entries)

    def to_dict(self) -> dict:
        return {
            "export_window_start_ct": self.export_window_start_ct,
            "export_window_end_ct": self.export_window_end_ct,
            "source_bucket": self.source_bucket,
            "exported_at_utc": self.exported_at_utc,
            "exporter": dict(self.exporter),
            "entries": [dataclasses.asdict(e) for e in self.entries],
            "known_missing_measurements": [
                dataclasses.asdict(m) for m in self.known_missing_measurements
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        entries = tuple(
            MeasurementEntry(
                measurement=e["measurement"],
                source_type=e["source_type"],
                parquet_path=e["parquet_path"],
                row_count=int(e["row_count"]),
                sha256=e["sha256"],
                field_set=tuple(e["field_set"]),
                first_timestamp_utc=e.get("first_timestamp_utc"),
                last_timestamp_utc=e.get("last_timestamp_utc"),
                note=e.get("note"),
            )
            for e in d.get("entries", [])
        )
        known_missing = tuple(
            MissingMeasurement(
                measurement=m["measurement"],
                reason_code=m["reason_code"],
                note=m.get("note"),
            )
            for m in d.get("known_missing_measurements", [])
        )
        return cls(
            export_window_start_ct=d["export_window_start_ct"],
            export_window_end_ct=d["export_window_end_ct"],
            source_bucket=d["source_bucket"],
            exported_at_utc=d["exported_at_utc"],
            exporter=dict(d.get("exporter", {})),
            entries=entries,
            known_missing_measurements=known_missing,
        )


def compute_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes. Streamed for memory efficiency."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(manifest: Manifest, manifest_path: Path) -> None:
    """Serialize a manifest to JSON with stable key ordering."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest.to_dict(), f, indent=2, sort_keys=True)


def read_manifest(manifest_path: Path) -> Manifest:
    """Load a manifest from JSON."""
    with open(manifest_path) as f:
        return Manifest.from_dict(json.load(f))


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds",
    ).replace("+00:00", "Z")


def parquet_filename(measurement: str, source_type: str) -> str:
    """Canonical filename for a measurement+source_type parquet file.

    Used so multiple manifest entries for the same measurement (with
    different source types) land at distinct paths.
    """
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unknown source_type {source_type!r}")
    return f"{measurement}.{source_type}.parquet"
