"""Weather matching per docs/plans/sced-rebaseline-spec-2026-05-13.md §6.

Within-sample z-score on a 4-component weather vector, then rectangular
Hungarian (optimal bipartite assignment) on Euclidean distance.

This module knows nothing about which arms are A vs B or which arms
passed the validity gate -- it works on already-z-scored matrices.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from tools.analysis.weather_vector import WeatherVector


def within_sample_zscore(vecs: Sequence[WeatherVector]) -> np.ndarray:
    """Return an N x 4 z-scored matrix using mean/std computed from
    ``vecs`` itself (within-sample standardization per spec §6 -- avoids
    cross-source z-score concerns since means and stds are computed from
    the same data source as the vectors).

    If a component has zero variance across the sample, its z-scored
    column is zero (matching what z-scoring a constant column should
    yield, instead of NaN).
    """
    arr = np.stack([v.as_array() for v in vecs])
    means = arr.mean(axis=0)
    stds = arr.std(axis=0, ddof=0)
    z = arr - means
    nonzero = stds > 0
    z[:, nonzero] = z[:, nonzero] / stds[nonzero]
    z[:, ~nonzero] = 0.0
    return z


def zscore_vectors(
    vecs: Sequence[WeatherVector],
    baseline_means: np.ndarray,
    baseline_stds: np.ndarray,
) -> np.ndarray:
    """Z-score against caller-supplied means and stds.

    Kept for plan compatibility / sensitivity work; primary code path
    uses within-sample standardization (spec §6).
    """
    arr = np.stack([v.as_array() for v in vecs])
    return (arr - baseline_means) / baseline_stds


def pairwise_distance_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Euclidean distance matrix between rows of ``a`` and rows of ``b``."""
    return np.linalg.norm(a[:, np.newaxis, :] - b[np.newaxis, :, :], axis=2)


def hungarian_match(arm_a_z: np.ndarray, arm_b_z: np.ndarray) -> list[tuple[int, int]]:
    """Rectangular Hungarian (scipy linear_sum_assignment) on Euclidean
    distance.

    Returns a list of ``(a_idx, b_idx)`` pairs of length
    ``min(n_A, n_B)``. The larger pool has unmatched arms; the caller
    reports those descriptively.
    """
    if arm_a_z.shape[0] == 0 or arm_b_z.shape[0] == 0:
        return []
    cost = pairwise_distance_matrix(arm_a_z, arm_b_z)
    row_ind, col_ind = linear_sum_assignment(cost)
    return list(zip(row_ind.tolist(), col_ind.tolist()))


def caliper_p90_distance(arm_a_z: np.ndarray, arm_b_z: np.ndarray) -> float:
    """90th percentile of the full A-B pairwise z-distance distribution.

    Used per spec §6 to flag pairs as ``poor_weather_match_flag`` (NOT
    excluded). Uses ``method='lower'`` so the percentile collapses to
    an actual observed value -- when n=6 the top decile contains five
    or six discrete distances, and the Hungarian-matched outlier pair
    sits exactly at the smallest of them. Linear interpolation would
    land the threshold between the 31st and 32nd sorted distance, just
    above the matched outlier, which would silently fail to flag the
    single weather outlier the spec wants to surface.

    Returns NaN if either pool is empty.
    """
    if arm_a_z.shape[0] == 0 or arm_b_z.shape[0] == 0:
        return float("nan")
    cost = pairwise_distance_matrix(arm_a_z, arm_b_z)
    return float(np.percentile(cost.flatten(), 90, method="lower"))
