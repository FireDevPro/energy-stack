"""Tests for tools/analysis/baseline_distribution.py + the locked
baseline_cov.npz shipped in this repo.

Asserts the locked npz has the expected shape and provenance, and
that its magnitudes are plausible for the 6-component weather
summary vector. Also exercises the placeholder path and the
ISO-week + CDD-filter helpers.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tools.analysis import baseline_distribution as bd


REPO_ROOT = Path(__file__).resolve().parents[3]
LOCKED_NPZ = REPO_ROOT / "tools" / "analysis" / "data" / "baseline_cov.npz"


# ---- The locked file --------------------------------------------------------


def _load_locked():
    return np.load(LOCKED_NPZ)


def test_locked_npz_is_not_placeholder():
    arr = _load_locked()
    source = str(arr["source"]) if "source" in arr.files else ""
    assert "PLACEHOLDER" not in source, (
        f"baseline_cov.npz still has PLACEHOLDER source: {source!r}. "
        f"Run `python tools/analysis/baseline_distribution.py --from-era5` "
        f"to lock it."
    )
    assert int(arr["n_weeks"]) > 0


def test_locked_components_in_locked_order():
    """Components must match pipeline.WEATHER_VECTOR_COMPONENTS in
    exactly the spec-locked order — Mahalanobis distance pulls Σ⁻¹
    rows/cols by index, so any reorder corrupts every pair distance."""
    arr = _load_locked()
    assert tuple(arr["components"].tolist()) == bd.COMPONENTS


def test_locked_cov_shape_and_positive_definite():
    arr = _load_locked()
    cov = arr["cov"]
    assert cov.shape == (6, 6)
    # Positive semi-definite (covariance always is); positive eigenvalues
    # confirm Σ⁻¹ exists for the Mahalanobis computation.
    eigvals = np.linalg.eigvalsh(cov)
    assert (eigvals > 0).all(), (
        f"Cov is not positive definite; eigenvalues = {eigvals}"
    )


def test_locked_means_are_plausible_for_chicago_cooling_weeks():
    """Sanity gate: if any mean is wildly outside the expected range
    we probably have a units bug somewhere (e.g. the hPa→inHg fix
    that landed in this PR)."""
    arr = _load_locked()
    mean = dict(zip(bd.COMPONENTS, arr["mean"]))
    # Cooling-relevant weeks (CDD>=5) have weekly CDD typically 10-150;
    # mean should land in the lower-middle of that range across 6
    # years that include shoulder weeks.
    assert 5 <= mean["weekly_cdd"] <= 150
    # Mean enthalpy at outdoor conditions during summer weeks in
    # Chicago: ~25-35 BTU/lb. A value near 15 would mean the hPa→inHg
    # conversion is missing; near 50 would mean wrong pressure scale.
    assert 22 <= mean["mean_enthalpy_btu_lb"] <= 38
    # Solar Wh/m² weekly sum: ~20k-60k.
    assert 15_000 <= mean["total_solar_wm2"] if False else True
    assert 20_000 <= mean["total_solar_wh_m2"] <= 60_000
    # Chicago surface wind: low single digits mph mean.
    assert 3 <= mean["mean_wind_mph"] <= 15
    # Cooling-relevant week max temp: typically 80-95°F.
    assert 75 <= mean["max_temp_f"] <= 100
    # Summer max dewpoint: 60-78°F.
    assert 55 <= mean["max_dewpoint_f"] <= 80


def test_locked_diagonal_variances_positive_and_finite():
    arr = _load_locked()
    diag = np.diag(arr["cov"])
    assert (diag > 0).all()
    assert np.isfinite(diag).all()


# ---- Placeholder path -------------------------------------------------------


def test_placeholder_emits_identity_with_zero_mean(tmp_path):
    out = tmp_path / "baseline_cov.npz"
    rc = bd.emit_placeholder(out)
    assert rc == 0
    arr = np.load(out)
    assert (arr["cov"] == np.eye(6)).all()
    assert (arr["mean"] == 0).all()
    assert int(arr["n_weeks"]) == 0
    assert "PLACEHOLDER" in str(arr["source"])


# ---- Internal helpers -------------------------------------------------------


def test_week_key_groups_iso_week():
    import datetime
    # Same ISO week (Mon-Sun) buckets together; week 23 of 2024
    # is Mon 2024-06-03 .. Sun 2024-06-09.
    a = datetime.datetime(2024, 6, 5, 12, 0)
    b = datetime.datetime(2024, 6, 8, 23, 0)
    c = datetime.datetime(2024, 6, 10, 0, 0)  # next week
    assert bd._week_key(a) == bd._week_key(b)
    assert bd._week_key(a) != bd._week_key(c)


def test_weekly_vector_returns_none_on_thin_week():
    # Fewer than 24 hours -> None
    row = {"time": __import__("datetime").datetime(2025, 7, 15, 12),
           "temp_f": 85.0, "dew_f": 65.0, "wind_mph": 5.0,
           "solar_wm2": 700.0, "pressure_inhg": 29.5}
    assert bd._compute_weekly_vector([row]) is None


def test_weekly_vector_basic_aggregation():
    import datetime as dt
    # Build a synthetic 7-day week with constant-ish conditions.
    rows = []
    for day in range(7):
        for hr in range(24):
            t = dt.datetime(2025, 7, 14 + day, hr)  # Mon-Sun
            rows.append({
                "time": t,
                "temp_f": 80.0 + (10.0 if 12 <= hr <= 17 else 0.0),
                "dew_f": 65.0,
                "wind_mph": 7.0,
                "solar_wm2": 400.0 if 6 <= hr <= 18 else 0.0,
                "pressure_inhg": 29.5,
            })
    v = bd._compute_weekly_vector(rows)
    assert v is not None
    # Daily avg = (80*18 + 90*6) / 24 = 82.5; cdd_day = max(82.5-65,0) = 17.5
    # weekly cdd = 17.5 * 7 = 122.5
    assert v["weekly_cdd"] == pytest.approx(122.5, abs=0.5)
    # Max temp from afternoon hours.
    assert v["max_temp_f"] == 90.0
    # Solar 400 * 13h * 7d = 36400
    assert v["total_solar_wh_m2"] == pytest.approx(36_400)
    # Wind constant.
    assert v["mean_wind_mph"] == pytest.approx(7.0)
    # Max dewpoint constant.
    assert v["max_dewpoint_f"] == 65.0
