"""Tests for controller_config — YAML loader + validations.

TDD: tests written before implementation. All must fail RED until
controller_config.py exists and passes them.

Run: cd deploy/energy-stack/hvac_scheduler && python -m pytest test_controller_config.py
"""
from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from . import app
from .controller_config import ControllerConfig, load_controller_config
from .controller_core import comfort_baseline_cool


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
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, extreme_at: 50, hysteresis_cents: 2}
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
    assert cfg.price_tiers_cents.hysteresis_cents == 2
    assert cfg.humidity_guard.rh_max_pct == 65
    assert cfg.humidity_guard.rh_clear_pct == 62
    assert cfg.ceiling.comfort_max == 29.0
    assert cfg.hold_ttl_minutes == 60
    assert cfg.modes.stage1_ramp.enabled is False
    # B3: config_id is the file's SHA256 hex digest (config provenance for
    # the hvac.actions/hvac.price_overlay telemetry rows).
    import hashlib
    expected_sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert cfg.config_id == expected_sha
    assert len(cfg.config_id) == 64  # full hex SHA-256


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
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, extreme_at: 50, hysteresis_cents: 2}
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
    """Equal values (rh_clear_pct == rh_max_pct) must raise — strict < required."""
    bad = VALID_YAML.replace(
        "{rh_max_pct: 65, rh_clear_pct: 62}",
        "{rh_max_pct: 65, rh_clear_pct: 65}",
    )
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="rh_clear_pct"):
        load_controller_config(path)


def test_hysteresis_order_inverted_rejected(tmp_path: Path) -> None:
    """rh_clear_pct > rh_max_pct (inverted) must also raise."""
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


def test_hold_ttl_below_quarter_hour_rejected(tmp_path: Path) -> None:
    # Device hold expiry lands on 15-min slots (floor); a TTL under 15 can
    # floor to a slot at-or-before now, i.e. an already-lapsed hold.
    bad = VALID_YAML.replace("hold_ttl_minutes: 60", "hold_ttl_minutes: 10")
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="hold_ttl_minutes"):
        load_controller_config(path)


# ---------------------------------------------------------------------------
# 5b. hysteresis_cents must be > 0 and < elevated_at (release-threshold sense)
# ---------------------------------------------------------------------------

def test_hysteresis_cents_zero_rejected(tmp_path: Path) -> None:
    bad = VALID_YAML.replace("hysteresis_cents: 2", "hysteresis_cents: 0")
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="hysteresis_cents"):
        load_controller_config(path)


def test_hysteresis_cents_at_or_above_elevated_rejected(tmp_path: Path) -> None:
    """A release threshold at/above the elevated trigger (hyst >= elevated_at)
    would make the elevated tier impossible to leave — must raise."""
    bad = VALID_YAML.replace("hysteresis_cents: 2", "hysteresis_cents: 10")
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="hysteresis_cents"):
        load_controller_config(path)


def test_hysteresis_cents_valid_accepted(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_controller_config(path)
    assert cfg.price_tiers_cents.hysteresis_cents == 2


# ---------------------------------------------------------------------------
# 5c. ceiling.comfort_max must be >= the warmest comfort baseline
# ---------------------------------------------------------------------------

def test_comfort_max_below_baseline_rejected(tmp_path: Path) -> None:
    """A comfort ceiling below the warmest comfort baseline (25.5) makes the
    warm-drift ceiling sit below the floor — degenerate; must raise."""
    bad = VALID_YAML.replace("comfort_max: 29.0", "comfort_max: 25.0")
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="comfort_max"):
        load_controller_config(path)


def test_comfort_max_equal_baseline_accepted(tmp_path: Path) -> None:
    """comfort_max == the warmest baseline is allowed (zero warm room, but
    not degenerate — effective stays at baseline)."""
    ok = VALID_YAML.replace("comfort_max: 29.0", "comfort_max: 25.5")
    path = _write_yaml(tmp_path, ok)
    cfg = load_controller_config(path)
    assert cfg.ceiling.comfort_max == 25.5


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


# ---------------------------------------------------------------------------
# 8. Off-grid flexibility delta rejected
# ---------------------------------------------------------------------------

def test_off_grid_flexibility_delta_rejected(tmp_path: Path) -> None:
    """warm_band: 0.3 is not on the 0.5-step C grid — must raise.

    The on-grid check covers temperature deltas (offsets) too, not just
    absolute setpoints. This test proves it catches off-grid flex values.
    """
    bad = VALID_YAML.replace(
        "flexibility:     {warm_band: 1.0, spike_extra: 1.0}",
        "flexibility:     {warm_band: 0.3, spike_extra: 1.0}",
    )
    path = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="grid"):
        load_controller_config(path)


# ---------------------------------------------------------------------------
# 9. Config.from_env CONTROLLER_CONFIG_FILE wiring
# ---------------------------------------------------------------------------

_BASE_ENV = {
    "SCHEDULER_MODE": "shadow",
    "INFLUXDB_TOKEN": "x",
    "INFLUXDB_ORG": "x",
    "INFLUXDB_BUCKET": "x",
}


def test_from_env_with_controller_config_file_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CONTROLLER_CONFIG_FILE set to a valid file → Config.controller_config is a ControllerConfig."""
    yaml_path = _write_yaml(tmp_path, VALID_YAML)
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CONTROLLER_CONFIG_FILE", yaml_path)
    # A3: VALID_YAML has temp_scale: C — env must agree or fail-fast fires.
    monkeypatch.setenv("TEMP_SCALE", "C")

    cfg = app.Config.from_env()

    assert cfg.controller_config is not None
    assert isinstance(cfg.controller_config, ControllerConfig)
    assert cfg.controller_config.temp_scale == "C"


def test_from_env_without_controller_config_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONTROLLER_CONFIG_FILE unset → Config.controller_config is None (default behaviour unchanged)."""
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CONTROLLER_CONFIG_FILE", raising=False)

    cfg = app.Config.from_env()

    assert cfg.controller_config is None


# ---------------------------------------------------------------------------
# 10. A3 — temp_scale coherence: fail-fast on mismatch + end-to-end on-grid
# ---------------------------------------------------------------------------
#
# Two unit sources must agree:
#   - Config.temp_scale  (from env TEMP_SCALE, default "F")
#   - ControllerConfig.temp_scale  (from YAML — the experimental surface)
#
# When they disagree the controller would operate in one unit while config
# values are authored in another (silent unit bug). A3 fails fast at load.

_VALID_YAML_F = """\
temp_scale: F
comfort_program:
  - {from: "22:00", to: "06:00", cool: 73}
  - {from: "06:00", to: "12:00", cool: 75}
  - {from: "12:00", to: "18:00", cool: 78}
  - {from: "18:00", to: "22:00", cool: 75}
heat_floor: 65
flexibility:     {warm_band: 2, spike_extra: 2}
price_tiers_cents: {elevated_at: 10, scarcity_at: 20, extreme_at: 50, hysteresis_cents: 2}
humidity_guard:  {rh_max_pct: 65, rh_clear_pct: 62}
ceiling:         {comfort_max: 85}
hold_ttl_minutes: 60
modes:           {stage1_ramp: {enabled: false}}
"""


def test_a3_temp_scale_mismatch_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A3: YAML temp_scale: C with env TEMP_SCALE=F must fail fast (sys.exit)."""
    # VALID_YAML has temp_scale: C; env says F — mismatch must cause sys.exit(2).
    yaml_path = _write_yaml(tmp_path, VALID_YAML)
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CONTROLLER_CONFIG_FILE", yaml_path)
    monkeypatch.setenv("TEMP_SCALE", "F")  # deliberately disagrees with YAML C

    with pytest.raises(SystemExit) as exc_info:
        app.Config.from_env()
    assert exc_info.value.code == 2


def test_a3_temp_scale_mismatch_reverse_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A3: YAML temp_scale: F with env TEMP_SCALE=C must also fail fast."""
    yaml_path = _write_yaml(tmp_path, _VALID_YAML_F)
    for k, v in _BASE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CONTROLLER_CONFIG_FILE", yaml_path)
    monkeypatch.setenv("TEMP_SCALE", "C")  # deliberately disagrees with YAML F

    with pytest.raises(SystemExit) as exc_info:
        app.Config.from_env()
    assert exc_info.value.code == 2


def test_a3_celsius_config_baseline_on_half_degree_grid(tmp_path: Path) -> None:
    """A3 end-to-end: C config with 0.5-step values → baseline always on 0.5 grid.

    Drives config temp_scale through load_controller_config → comfort_baseline_cool
    and asserts every block's setpoint is a multiple of 0.5. Reuses _on_grid from
    controller_config (indirectly via the loaded ControllerConfig) without
    duplicating the grid math.
    """
    yaml_path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_controller_config(yaml_path)

    assert cfg.temp_scale == "C"
    # Sample one time per block; assert returned baseline is on the 0.5 grid.
    sample_times = [
        datetime(2026, 6, 20, 3, 0),   # 22:00-06:00 block → 23.5
        datetime(2026, 6, 20, 9, 0),   # 06:00-12:00 block → 24.5
        datetime(2026, 6, 20, 15, 0),  # 12:00-18:00 block → 25.5
        datetime(2026, 6, 20, 20, 0),  # 18:00-22:00 block → 24.5
    ]
    for when in sample_times:
        baseline = comfort_baseline_cool(cfg.comfort_program, when)
        # On the C grid: value * 2 must be an integer.
        assert (baseline * 2) == round(baseline * 2), (
            f"baseline {baseline} at {when.strftime('%H:%M')} is not on the "
            f"0.5°C grid (temp_scale={cfg.temp_scale})"
        )


def test_a3_fahrenheit_config_baseline_on_whole_degree_grid(tmp_path: Path) -> None:
    """A3 end-to-end: F config with whole-number values → baseline always whole degrees.

    Mirrors the C test but for the F scale: every block setpoint must be
    a whole number (no fractional degrees).
    """
    yaml_path = _write_yaml(tmp_path, _VALID_YAML_F)
    cfg = load_controller_config(yaml_path)

    assert cfg.temp_scale == "F"
    sample_times = [
        datetime(2026, 6, 20, 3, 0),   # 22:00-06:00 block → 73
        datetime(2026, 6, 20, 9, 0),   # 06:00-12:00 block → 75
        datetime(2026, 6, 20, 15, 0),  # 12:00-18:00 block → 78
        datetime(2026, 6, 20, 20, 0),  # 18:00-22:00 block → 75
    ]
    for when in sample_times:
        baseline = comfort_baseline_cool(cfg.comfort_program, when)
        # On the F grid: value must be a whole number.
        assert baseline == round(baseline), (
            f"baseline {baseline} at {when.strftime('%H:%M')} is not a whole "
            f"degree (temp_scale={cfg.temp_scale})"
        )


# ---------------------------------------------------------------------------
# 11. The deployed config file loads + validates (guards the real config)
# ---------------------------------------------------------------------------

def test_deployed_commissioning_config_loads_and_validates() -> None:
    """The committed commissioning-controller.yaml (the real config mounted
    into the container) must load + pass validation — a broken edit here would
    crash the controller at startup via the config-required assert."""
    cfg_path = Path(__file__).parent / "commissioning-controller.yaml"
    cfg = load_controller_config(str(cfg_path))
    assert cfg.temp_scale == "C"
    assert len(cfg.comfort_program) == 4
    assert cfg.comfort_program[0]["cool"] == 23.5      # sleep (74°F)
    assert cfg.ceiling.comfort_max == 28.0             # 82°F operational cap
    assert cfg.hold_ttl_minutes == 60
