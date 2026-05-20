"""Tests for the scheduler-canonical freshness module."""
from __future__ import annotations

import pytest

from freshness import THRESHOLDS, Thresholds, classify


def test_comed_prices_threshold_is_seven_minutes_fresh():
    """Per spec §3.1: 'comed.prices' threshold tightened from 11/16/30
    (pre-fix cockpit value) to 7/16/30 reflecting the controller's
    actionability boundary."""
    t = THRESHOLDS["comed.prices"]
    assert t.fresh_max_ms == 7 * 60 * 1000
    assert t.warn_max_ms == 16 * 60 * 1000
    assert t.stale_max_ms == 30 * 60 * 1000


def test_classify_returns_fresh_under_threshold():
    assert classify("comed.prices", 6 * 60 * 1000) == "fresh"
    assert classify("comed.prices", 7 * 60 * 1000) == "fresh"  # boundary inclusive


def test_classify_returns_warn_above_fresh_threshold():
    assert classify("comed.prices", 7 * 60 * 1000 + 1) == "warn"
    assert classify("comed.prices", 16 * 60 * 1000) == "warn"


def test_classify_returns_stale_above_warn_threshold():
    assert classify("comed.prices", 16 * 60 * 1000 + 1) == "stale"
    assert classify("comed.prices", 30 * 60 * 1000) == "stale"


def test_classify_returns_missing_above_stale_threshold():
    assert classify("comed.prices", 30 * 60 * 1000 + 1) == "missing"


def test_classify_returns_fresh_for_unknown_source():
    """Unknown source defaults to 'fresh' (matches cockpit's existing behavior).
    Per spec §3.1, the THRESHOLDS dict is the source-of-truth registry."""
    assert classify("not.a.real.source", 9999999) == "fresh"


def test_classify_handles_negative_age_as_fresh():
    """Clock skew or test injection. Per spec §7: 'treated as fresh
    (negative ≤ fresh_max_ms). Future-dated buckets are a non-fault edge case.'"""
    assert classify("comed.prices", -1000) == "fresh"
