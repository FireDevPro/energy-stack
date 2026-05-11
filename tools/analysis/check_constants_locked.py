"""Pre-OSF-lock gate: refuse if any stipulated constants file still
carries the PLACEHOLDER sentinel.

Run before tagging the OSF filing commit. Exit 0 means all constants
are locked; exit 1 lists the placeholder files.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

CONSTANT_FILES = [
    REPO_ROOT / "tools" / "comed_price_imputation" / "spread_constants.json",
    REPO_ROOT / "tools" / "o2_capacity_reconstruction" / "tariff_constants.json",
]


def main() -> int:
    blockers: list[Path] = []
    for path in CONSTANT_FILES:
        if not path.exists():
            blockers.append(path)
            print(f"MISSING: {path}")
            continue
        with open(path) as f:
            data = json.load(f)
        if data.get("PLACEHOLDER", False):
            blockers.append(path)
            print(f"PLACEHOLDER: {path}")
        else:
            print(f"locked:    {path}")
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
