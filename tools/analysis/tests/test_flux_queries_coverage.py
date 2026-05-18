"""Audit: every measurement in KNOWN_MEASUREMENTS has a flux query file.

Real-data finding from the 2026-05-12 replay run: comed.bill_lineitems
was in KNOWN_MEASUREMENTS but had no corresponding
tools/analysis/queries/comed.bill_lineitems.flux file. The Stage 1
extractor iterates *.flux in that directory, so the measurement was
silently never queried — even though Influx had rich line-item data.
Stage 6 Layer 3 then had no Capacity Charge rows to sum.

This regression-defense test codifies the audit pass that surfaced
the gap.
"""
from __future__ import annotations

from pathlib import Path

from tools.analysis.replay.manifest import KNOWN_MEASUREMENTS


QUERIES_DIR = (
    Path(__file__).resolve().parents[1] / "queries"
)


def test_every_known_measurement_has_a_flux_query():
    """Every KNOWN_MEASUREMENT must have a corresponding *.flux file
    that the Stage 1 extractor will discover and run."""
    existing = {p.stem for p in QUERIES_DIR.glob("*.flux")}
    missing = sorted(set(KNOWN_MEASUREMENTS) - existing)
    assert not missing, (
        f"KNOWN_MEASUREMENTS without flux query files: {missing}. "
        f"Stage 1 extract iterates *.flux and will skip these "
        f"measurements silently. Add a flux file per the pattern in "
        f"the queries/ directory."
    )


def test_each_flux_file_uses_only_stage1_placeholders():
    """The Stage 1 loader (tools/analysis/pipeline.py) substitutes
    ``$bucket``, ``$start``, ``$end`` and nothing else. A query that
    uses ``${bucket}``, ``${start}``, ``$stop`` (etc.) will render with
    literal placeholder characters and produce an invalid Flux body --
    the Influx client either rejects it or returns empty rows.

    Catch any future drift by asserting that after the Stage 1
    substitution no ``$``-prefixed identifier survives in the rendered
    Flux body.
    """
    import re
    placeholder_re = re.compile(r"\$\{?[A-Za-z_][A-Za-z_0-9]*\}?")
    allowed = {"$bucket", "$start", "$end"}
    for flux_file in QUERIES_DIR.glob("*.flux"):
        text = flux_file.read_text()
        # Strip line comments before scanning; placeholder words in
        # documentation comments aren't a runtime concern.
        body_lines = [
            line for line in text.splitlines()
            if not line.lstrip().startswith("//")
        ]
        body = "\n".join(body_lines)
        used = set(placeholder_re.findall(body))
        unexpected = used - allowed
        assert not unexpected, (
            f"{flux_file.name}: uses placeholder(s) {sorted(unexpected)} "
            f"that Stage 1 will not substitute. Allowed: "
            f"{sorted(allowed)}. The Stage 1 loader at "
            f"tools/analysis/pipeline.py does string-replace on the "
            f"three allowed names only."
        )


def test_no_orphan_flux_files():
    """A flux file with no corresponding KNOWN_MEASUREMENT entry would
    be queried but never tracked in the manifest. Catch that too."""
    existing = {p.stem for p in QUERIES_DIR.glob("*.flux")}
    orphans = sorted(existing - set(KNOWN_MEASUREMENTS))
    assert not orphans, (
        f"Flux query files with no KNOWN_MEASUREMENT entry: {orphans}. "
        f"Either add them to KNOWN_MEASUREMENTS in "
        f"tools/analysis/replay/manifest.py or remove the file."
    )


def test_each_flux_file_filters_its_own_measurement():
    """Filename-vs-content alignment audit.

    A file named ``comed.bill_lineitems.flux`` whose body filters
    ``r._measurement == "comed.bill"`` (wrong measurement) would pass
    the filename-only audits above but silently query the wrong
    measurement at Stage 1 extract time. The Stage 1 extractor uses
    the filename as the manifest key, so the parquet would land
    under ``comed.bill_lineitems.<source_type>.parquet`` while
    actually containing ``comed.bill`` rows.

    This test parses each flux file and verifies its body contains a
    measurement filter matching the filename stem.
    """
    import re
    mismatches = []
    pattern = re.compile(
        r'r\._measurement\s*==\s*"([^"]+)"'
    )
    for flux_path in sorted(QUERIES_DIR.glob("*.flux")):
        stem = flux_path.stem
        body = flux_path.read_text()
        matches = pattern.findall(body)
        if not matches:
            mismatches.append(
                f"{flux_path.name}: no `r._measurement == \"...\"` "
                f"filter found"
            )
            continue
        if stem not in matches:
            mismatches.append(
                f"{flux_path.name}: filename stem {stem!r} not in "
                f"measurement filters {matches!r}"
            )
    assert not mismatches, (
        "Flux files whose body does not match their filename:\n  "
        + "\n  ".join(mismatches)
    )
