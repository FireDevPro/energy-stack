"""Tests for controller_config — YAML loader + validations.

TDD: tests written before implementation. All must fail RED until
controller_config.py exists and passes them.

Run: cd deploy/energy-stack/hvac_scheduler && python -m pytest test_controller_config.py
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from .controller_config import ControllerConfig, load_controller_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "controller.yaml"
    p.write_text(textwrap.dedent(content))
    return str(p)


VALID_YAML = """\
temp_scale: C
comfort_program:
  - {from: "22:00", to: "06:00", cool: 23.5}
  - {from: "06:00", to: "12:00", cool: 24.5}
  - {from: "12:00", to: "18:00", cool: 25.5}
  - {from: "18:00", to: "22:00", cool: 24.5}
heat_floor: 18.5
flexibility:     {warm_band: 1.0, spike_extra: 1.0}
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, extreme_at: 50}
humidity_guard:  {rh_max_pct: 65, rh_clear_pct: 62}
ceiling:         {comfort_max: 29.0}
hold_ttl_minutes: 60
modes:           {stage1_ramp: {enabled: false}}
"""


# ---------------------------------------------------------------------------
# 1. Parse — well-formed file produces a populated ControllerConfig
# ---------------------------------------------------------------------------

def test_parse_well_formed(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_controller_config(path)

    assert isinstance(cfg, ControllerConfig)
    assert cfg.temp_scale == "C"
    assert len(cfg.comfort_program) == 4
    assert cfg.heat_floor == 18.5
    assert cfg.flexibility.warm_band == 1.0
    assert cfg.flexibility.spike_extra == 1.0
    assert cfg.price_tiers_cents.elevated_at == 10
    assert cfg.price_tiers_cents.scarcity_at == 20
    assert cfg.price_tiers_cents.extreme_at == 50
    assert cfg.humidity_guard.rh_max_pct == 65
    assert cfg.humidity_guard.rh_clear_pct == 62
    assert cfg.ceiling.comfort_max == 29.0
    assert cfg.hold_ttl_minutes == 60
    assert cfg.modes.stage1_ramp.enabled is False


# ---------------------------------------------------------------------------
# 2. Midnight-wrap block parses and is represented correctly
# ---------------------------------------------------------------------------

def test_midnight_wrap_block_parsed(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_controller_config(path)

    # First block: 22:00 -> 06:00 — midnight wrap
    first = cfg.comfort_program[0]
    assert first["from"] == "22:00"
    assert first["to"] == "06:00"
    assert first["cool"] == 23.5


# ---------------------------------------------------------------------------
# 3. On-grid validation — off-grid temps rejected
# ---------------------------------------------------------------------------

def test_off_grid_celsius_rejected(tmp_path: Path) -> None:
    """23.3 is not a multiple of 0.5 — must raise."""
    bad = VALID_YAML.replace("cool: 23.5", "cool: 23.3")
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="grid"):
        load_controller_config(path)


def test_on_grid_celsius_accepted(tmp_path: Path) -> None:
    """All values divisible by 0.5 — must load cleanly."""
    path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_controller_config(path)
    assert cfg.temp_scale == "C"


def test_off_grid_fahrenheit_rejected(tmp_path: Path) -> None:
    """74.5 is not a whole number — must raise for F scale."""
    bad = VALID_YAML.replace(
        "temp_scale: C",
        "temp_scale: F",
    ).replace(
        "cool: 23.5", "cool: 74.5"
    )
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="grid"):
        load_controller_config(path)


def test_on_grid_fahrenheit_accepted(tmp_path: Path) -> None:
    """Whole-number Fahrenheit values must load cleanly."""
    content = """\
temp_scale: F
comfort_program:
  - {from: "22:00", to: "06:00", cool: 73}
  - {from: "06:00", to: "12:00", cool: 75}
  - {from: "12:00", to: "18:00", cool: 78}
  - {from: "18:00", to: "22:00", cool: 75}
heat_floor: 65
flexibility:     {warm_band: 2, spike_extra: 2}
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, extreme_at: 50}
humidity_guard:  {rh_max_pct: 65, rh_clear_pct: 62}
ceiling:         {comfort_max: 85}
hold_ttl_minutes: 60
modes:           {stage1_ramp: {enabled: false}}
"""
    path = _write_yaml(tmp_path, content)
    cfg = load_controller_config(path)
    assert cfg.temp_scale == "F"
    assert cfg.comfort_program[0]["cool"] == 73


# ---------------------------------------------------------------------------
# 4. Hysteresis order — rh_clear_pct must be < rh_max_pct
# ---------------------------------------------------------------------------

def test_hysteresis_order_rejected(tmp_path: Path) -> None:
    """rh_clear_pct >= rh_max_pct must raise."""
    bad = VALID_YAML.replace(
        "{rh_max_pct: 65, rh_clear_pct: 62}",
        "{rh_max_pct: 65, rh_clear_pct: 65}",
    )
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="rh_clear_pct"):
        load_controller_config(path)


def test_hysteresis_order_equal_rejected(tmp_path: Path) -> None:
    """Equal values also violate strict inequality."""
    bad = VALID_YAML.replace(
        "{rh_max_pct: 65, rh_clear_pct: 62}",
        "{rh_max_pct: 60, rh_clear_pct: 62}",  # clear > max
    )
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="rh_clear_pct"):
        load_controller_config(path)


# ---------------------------------------------------------------------------
# 5. hold_ttl_minutes must be positive
# ---------------------------------------------------------------------------

def test_hold_ttl_zero_rejected(tmp_path: Path) -> None:
    bad = VALID_YAML.replace("hold_ttl_minutes: 60", "hold_ttl_minutes: 0")
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="hold_ttl_minutes"):
        load_controller_config(path)


def test_hold_ttl_negative_rejected(tmp_path: Path) -> None:
    bad = VALID_YAML.replace("hold_ttl_minutes: 60", "hold_ttl_minutes: -5")
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="hold_ttl_minutes"):
        load_controller_config(path)


def test_hold_ttl_positive_accepted(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_controller_config(path)
    assert cfg.hold_ttl_minutes == 60


# ---------------------------------------------------------------------------
# 6. ControllerConfig is frozen (immutable)
# ---------------------------------------------------------------------------

def test_config_is_frozen(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_controller_config(path)
    with pytest.raises((AttributeError, TypeError)):
        cfg.hold_ttl_minutes = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 7. No supervisor key in the config schema
# ---------------------------------------------------------------------------

def test_no_supervisor_key(tmp_path: Path) -> None:
    """ControllerConfig must not have a supervisor attribute."""
    path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_controller_config(path)
    assert not hasattr(cfg, "supervisor")
    assert not hasattr(cfg, "cheap")
    assert not hasattr(cfg, "runtime")
