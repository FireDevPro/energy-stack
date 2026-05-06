"""Tests for webdashboard-api's pure-logic helpers.

The webdashboard-api assembles a snapshot for the live HUD from EAGLE,
ComEd, and Refoss data. Most of the orchestration is Flux-query glue,
but the data-shaping helpers (categorization, health-signal, age math)
are pure and worth pinning down so the live UI doesn't silently start
showing wrong numbers.

Run from this directory:
    python -m pytest tests.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import (
    _category_for,
    _health,
    _sum_devices_in_category,
    age_seconds,
)


# ---- _category_for -------------------------------------------------------


def test_category_for_hvac_keywords():
    assert _category_for("AC Compressor") == "hvac"
    assert _category_for("Air Conditioning") == "hvac"
    assert _category_for("Furnace Blower") == "hvac"
    assert _category_for("Heat Pump") == "hvac"


def test_category_for_fridge_keywords():
    assert _category_for("Kitchen Fridge") == "fridge"
    assert _category_for("Basement Freezer") == "fridge"


def test_category_for_living_keywords():
    """Living/social spaces: family room, bar, AV rack, dining, kitchen."""
    assert _category_for("Family Room") == "living"
    assert _category_for("Bar Outlets") == "living"
    assert _category_for("AV Rack") == "living"
    assert _category_for("Dining Room") == "living"
    # "Kitchen Fridge" matches "fridge" first (more specific)
    assert _category_for("Kitchen Lights") == "living"


def test_category_for_bedrooms_keywords():
    assert _category_for("Master Bedroom") == "bedrooms"
    assert _category_for("Primary Bath") == "bedrooms"
    assert _category_for("Washer") == "bedrooms"
    assert _category_for("Dryer") == "bedrooms"


def test_category_for_unknown_returns_other():
    assert _category_for("Random Outlet") == "other"
    assert _category_for("Unlabeled") == "other"


def test_category_for_handles_none():
    """Unnamed channel (None or empty) doesn't crash."""
    assert _category_for(None) == "other"
    assert _category_for("") == "other"


def test_category_for_case_insensitive():
    assert _category_for("MASTER BEDROOM") == "bedrooms"
    assert _category_for("kitchen FRIDGE") == "fridge"


def test_category_for_priority_order():
    """When a name could match multiple buckets, the function's check order
    determines the winner. HVAC beats living when both keywords are present
    (because hvac is checked first)."""
    # "Furnace" wins over "Family"
    assert _category_for("Family Furnace") == "hvac"
    # "Fridge" wins over "Kitchen" because fridge is checked before living
    assert _category_for("Kitchen Fridge") == "fridge"


# ---- _sum_devices_in_category --------------------------------------------


def test_sum_devices_basic():
    devices = [
        {"name": "Furnace", "category": "hvac", "power": 500.0, "on": True},
        {"name": "AC", "category": "hvac", "power": 1500.0, "on": True},
        {"name": "Master Bedroom", "category": "bedrooms", "power": 100.0, "on": True},
    ]
    assert _sum_devices_in_category(devices, "hvac") == 2000.0
    assert _sum_devices_in_category(devices, "bedrooms") == 100.0
    assert _sum_devices_in_category(devices, "fridge") == 0.0


def test_sum_devices_skips_off_devices():
    """Devices flagged off don't contribute to the cluster total — caller's
    contract for "what's actually drawing right now"."""
    devices = [
        {"name": "AC", "category": "hvac", "power": 1500.0, "on": True},
        {"name": "Furnace", "category": "hvac", "power": 500.0, "on": False},
    ]
    assert _sum_devices_in_category(devices, "hvac") == 1500.0


def test_sum_devices_empty_list():
    assert _sum_devices_in_category([], "hvac") == 0.0


# ---- age_seconds ---------------------------------------------------------


def test_age_seconds_returns_positive_for_past_timestamp():
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    age = age_seconds(past)
    assert age is not None
    assert 29 < age < 32   # generous slop for test execution time


def test_age_seconds_handles_none():
    assert age_seconds(None) is None


def test_age_seconds_recent_timestamp_is_small():
    recent = datetime.now(timezone.utc) - timedelta(seconds=1)
    age = age_seconds(recent)
    assert age is not None
    assert age < 5


# ---- _health -------------------------------------------------------------


def test_health_idle_when_no_timestamp():
    """Service that's never written returns idle (not warn) so the UI shows
    a neutral indicator instead of a red one on first boot."""
    assert _health(None, stale_after_s=120) == {"state": "idle", "ageSeconds": None}


def test_health_ok_when_fresh():
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    h = _health(fresh, stale_after_s=120)
    assert h["state"] == "ok"
    assert h["ageSeconds"] is not None
    assert h["ageSeconds"] < 10


def test_health_warn_when_stale():
    stale = datetime.now(timezone.utc) - timedelta(seconds=300)
    h = _health(stale, stale_after_s=120)
    assert h["state"] == "warn"
    assert h["ageSeconds"] > 120


def test_health_boundary_at_stale_threshold():
    """Boundary: > stale_after_s fires warn; <= stays ok."""
    # 121s stale, threshold 120 → warn
    stale = datetime.now(timezone.utc) - timedelta(seconds=121)
    assert _health(stale, stale_after_s=120)["state"] == "warn"
    # 119s stale, threshold 120 → ok (well under)
    fresh = datetime.now(timezone.utc) - timedelta(seconds=119)
    assert _health(fresh, stale_after_s=120)["state"] == "ok"
