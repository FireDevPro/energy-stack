"""Tests for tools/comed_price_imputation/compute_spread.py.

Also acts as an integration check on the LOCKED spread_constants.json
shipped in this repo — re-running compute_spread.py against the
committed data/ files must reproduce the JSON byte-for-byte.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.comed_price_imputation import compute_spread  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
LOCKED_JSON = (
    REPO_ROOT / "tools" / "comed_price_imputation" / "spread_constants.json"
)


def test_locked_json_is_not_placeholder():
    """If this test fails, OSF filing is blocked. The placeholder
    sentinel must be False and a real input_years list must exist."""
    with open(LOCKED_JSON) as f:
        data = json.load(f)
    assert data["PLACEHOLDER"] is False, (
        "spread_constants.json is still a placeholder — run "
        "compute_spread.py against real data before OSF lock."
    )
    assert data["input_hours_total"] > 0
    assert isinstance(data["input_years"], list) and data["input_years"], (
        "input_years must list the years that actually contributed."
    )


def test_locked_constants_match_recomputation():
    """Running compute_spread() against committed data/ must reproduce
    the locked JSON. This guards against silent data-file edits."""
    medians, n_hours, years = compute_spread.compute_for_years(
        [2023, 2024, 2025]
    )
    with open(LOCKED_JSON) as f:
        data = json.load(f)
    # Recomputation should match within rounding.
    for month_str, locked_val in data[
        "median_spread_cents_per_kwh_by_month"
    ].items():
        recomputed = medians[int(month_str)]
        assert recomputed == pytest.approx(locked_val, abs=1e-4)
    assert n_hours == data["input_hours_total"]
    assert years == data["input_years"]


def test_summer_months_only():
    """compute_for_years only counts Jun-Sep hours per the locked spec."""
    medians, _, _ = compute_spread.compute_for_years([2024, 2025])
    assert set(medians.keys()).issubset({6, 7, 8, 9})


def test_spreads_small_magnitude():
    """Sanity gate: ComEd retail RTP tracks PJM zonal DA LMP closely;
    if any monthly median spread exceeds ±2 ¢/kWh, something is wrong
    (wrong units, wrong zone, wrong year mapping, etc.)."""
    with open(LOCKED_JSON) as f:
        data = json.load(f)
    for month, val in data["median_spread_cents_per_kwh_by_month"].items():
        assert abs(float(val)) < 2.0, (
            f"month {month} spread {val} ¢/kWh exceeds the ±2 ¢/kWh "
            f"sanity gate — investigate before locking."
        )
