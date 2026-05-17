"""Tests for tools.analysis.matching per spec §6."""
from __future__ import annotations

import numpy as np
import pytest

from tools.analysis.matching import (
    caliper_p90_distance,
    hungarian_match,
    pairwise_distance_matrix,
    within_sample_zscore,
    zscore_vectors,
)
from tools.analysis.weather_vector import WeatherVector


def test_within_sample_zscore_means_zero():
    vecs = [
        WeatherVector(100.0, 95.0, 70.0, 60.0),
        WeatherVector(140.0, 100.0, 72.0, 62.0),
        WeatherVector(180.0, 105.0, 74.0, 64.0),
    ]
    z = within_sample_zscore(vecs)
    np.testing.assert_allclose(z.mean(axis=0), [0.0, 0.0, 0.0, 0.0], atol=1e-9)
    # Each std (ddof=0) of the z-scored column should be 1
    np.testing.assert_allclose(z.std(axis=0, ddof=0), [1.0, 1.0, 1.0, 1.0], atol=1e-9)


def test_within_sample_zscore_zero_variance_column_returns_zero_not_nan():
    """If a component is constant, its z-scored column should be all 0."""
    vecs = [
        WeatherVector(100.0, 95.0, 70.0, 60.0),
        WeatherVector(140.0, 95.0, 72.0, 60.0),  # mean_daily_max_temp_f constant; dewpoint constant
    ]
    z = within_sample_zscore(vecs)
    assert not np.isnan(z).any()
    assert np.allclose(z[:, 1], 0.0)  # mean_daily_max constant
    assert np.allclose(z[:, 3], 0.0)  # mean_dewpoint constant


def test_zscore_vectors_with_external_baseline():
    """Plan-style baseline z-score: x-mu)/sigma using caller-supplied params."""
    vecs = [
        WeatherVector(100.0, 80.0, 60.0, 60.0),
        WeatherVector(150.0, 85.0, 65.0, 65.0),
    ]
    means = np.array([100.0, 80.0, 60.0, 60.0])
    stds = np.array([50.0, 10.0, 5.0, 5.0])
    z = zscore_vectors(vecs, means, stds)
    assert z.shape == (2, 4)
    np.testing.assert_allclose(z[0], [0.0, 0.0, 0.0, 0.0])
    np.testing.assert_allclose(z[1], [1.0, 0.5, 1.0, 1.0])


def test_hungarian_match_3x3_returns_optimal_pairs():
    arm_a_z = np.array([[0.0, 0.0, 0.0, 0.0],
                        [1.0, 1.0, 1.0, 1.0],
                        [2.0, 2.0, 2.0, 2.0]])
    arm_b_z = np.array([[0.1, 0.1, 0.1, 0.1],
                        [1.1, 1.1, 1.1, 1.1],
                        [2.1, 2.1, 2.1, 2.1]])
    pairs = hungarian_match(arm_a_z, arm_b_z)
    assert pairs == [(0, 0), (1, 1), (2, 2)]


def test_rectangular_hungarian_drops_far_arm():
    """2 A vs 3 B: pick the closest two, the third B is unmatched."""
    arm_a_z = np.array([[0.0, 0.0, 0.0, 0.0],
                        [1.0, 1.0, 1.0, 1.0]])
    arm_b_z = np.array([[0.5, 0.5, 0.5, 0.5],
                        [1.5, 1.5, 1.5, 1.5],
                        [10.0, 10.0, 10.0, 10.0]])
    pairs = hungarian_match(arm_a_z, arm_b_z)
    assert len(pairs) == 2
    matched_b = {b for _, b in pairs}
    assert 2 not in matched_b


def test_empty_pool_returns_no_pairs():
    arm_a_z = np.empty((0, 4))
    arm_b_z = np.array([[1.0, 1.0, 1.0, 1.0]])
    assert hungarian_match(arm_a_z, arm_b_z) == []
    assert hungarian_match(arm_b_z, arm_a_z) == []


def test_pairwise_distance_matrix_known_values():
    a = np.array([[0.0, 0.0]])
    b = np.array([[3.0, 4.0], [0.0, 1.0]])
    d = pairwise_distance_matrix(a, b)
    np.testing.assert_allclose(d, [[5.0, 1.0]])


def test_caliper_p90_is_percentile_of_all_pair_distances():
    """Spec §6 caliper: 90th percentile across the full A-B distribution.

    Uses method='lower' so the percentile collapses to an actual
    observed value -- see ``caliper_p90_distance`` docstring for the
    n=6 single-outlier rationale. With 4 distances {1, 2, 3, 4} the
    90th-percentile-lower is sorted[floor(0.9 * 3)] = sorted[2] = 3.
    """
    arm_a_z = np.array([[0.0, 0.0, 0.0, 0.0]])
    arm_b_z = np.array([[1.0, 0.0, 0.0, 0.0],
                        [2.0, 0.0, 0.0, 0.0],
                        [3.0, 0.0, 0.0, 0.0],
                        [4.0, 0.0, 0.0, 0.0]])
    p90 = caliper_p90_distance(arm_a_z, arm_b_z)
    assert p90 == pytest.approx(3.0, abs=1e-6)
