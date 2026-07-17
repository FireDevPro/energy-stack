"""Unit tests for the EPA correction + AQI math (the non-trivial logic).

The fetch/write path is validated live against the PurpleAir API; these lock
the pure functions so a coefficient typo can't slip through silently.
"""
import pytest

from purpleair_poller.app import epa_correct, pm_to_aqi


def test_epa_correct_low_band():
    # 0.524*10 - 0.0862*50 + 5.75
    assert epa_correct(10, 50) == pytest.approx(6.68, abs=0.01)


def test_epa_correct_mid_band():
    # 50 <= pa < 210: 0.786*pa - 0.0862*rh + 5.75
    assert epa_correct(80, 55) == pytest.approx(0.786 * 80 - 0.0862 * 55 + 5.75, abs=0.01)


def test_epa_correct_extreme_smoke_band():
    # pa >= 260: 2.966 + 0.69*pa + 8.84e-4*pa^2  (no humidity term)
    assert epa_correct(300, 40) == pytest.approx(2.966 + 0.69 * 300 + 8.84e-4 * 300 ** 2, abs=0.01)


def test_epa_correct_clamps_nonpositive():
    assert epa_correct(0, 50) == 0.0
    assert epa_correct(-5, 50) == 0.0


def test_epa_correct_continuous_across_30_boundary():
    # the piecewise should not jump discontinuously at pa=30
    assert epa_correct(29.9, 55) == pytest.approx(epa_correct(30.1, 55), abs=1.5)


def test_pm_to_aqi_breakpoints():
    assert pm_to_aqi(0) == (0, "Good")
    assert pm_to_aqi(9.0) == (50, "Good")
    assert pm_to_aqi(9.1)[0] == 51                    # Moderate lower edge
    assert pm_to_aqi(35.4)[0] == 100
    assert pm_to_aqi(55.5) == (151, "Unhealthy")
    assert pm_to_aqi(125.5)[0] == 201                 # Very unhealthy lower edge
    assert pm_to_aqi(1000)[0] == 500                  # above table -> Hazardous cap


def test_pm_to_aqi_midpoint_interpolates():
    # midpoint of Moderate band (9.1-35.4 -> 51-100) ~ AQI 75-76
    aqi, cat = pm_to_aqi((9.1 + 35.4) / 2)
    assert cat == "Moderate"
    assert 74 <= aqi <= 77
