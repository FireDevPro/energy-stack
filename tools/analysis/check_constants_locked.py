"""Pre-OSF-lock gate: refuse if any stipulated constants file still
carries the PLACEHOLDER sentinel.

Run before tagging the OSF filing commit. Exit 0 means all constants
are locked; exit 1 lists the placeholder files.

Gates:
  - tools/comed_price_imputation/spread_constants.json (PLACEHOLDER key)
  - tools/o2_capacity_reconstruction/tariff_constants.json (PLACEHOLDER key)
  - tools/analysis/data/baseline_cov.npz (source string contains "PLACEHOLDER")
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]

JSON_CONSTANT_FILES = [
    REPO_ROOT / "tools" / "comed_price_imputation" / "spread_constants.json",
    REPO_ROOT / "tools" / "o2_capacity_reconstruction" / "tariff_constants.json",
]

NPZ_CONSTANT_FILES = [
    REPO_ROOT / "tools" / "analysis" / "data" / "baseline_cov.npz",
]


def _check_json(path: Path) -> tuple[bool, str]:
    """Returns (is_locked, label)."""
    if not path.exists():
        return False, "MISSING"
    with open(path) as f:
        data = json.load(f)
    if data.get("PLACEHOLDER", False):
        return False, "PLACEHOLDER"
    return True, "locked"


def _check_npz(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "MISSING"
    arr = np.load(path)
    source = str(arr.get("source", "")) if "source" in arr.files else ""
    if "PLACEHOLDER" in source:
        return False, "PLACEHOLDER"
    return True, "locked"


def main() -> int:
    blockers: list[Path] = []
    for path in JSON_CONSTANT_FILES:
        ok, label = _check_json(path)
        print(f"{label}:    {path}")
        if not ok:
            blockers.append(path)
    for path in NPZ_CONSTANT_FILES:
        ok, label = _check_npz(path)
        print(f"{label}:    {path}")
        if not ok:
            blockers.append(path)
    if blockers:
        print(
            f"\n{len(blockers)} placeholder(s) remain. Refusing to bless "
            f"OSF commit.",
            file=sys.stderr,
        )
        return 1
    print("\nAll stipulated constants are locked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
