from __future__ import annotations

import pytest

from .controller.config import ConfigError, load_config

GOOD = (
    "temp_scale: C\n"
    "price_tiers_cents: {elevated_at: 10, scarcity_at: 20, hysteresis_cents: 2}\n"
    "elevated_offset: 1.5\n"
    "scarcity_absolute: 29.5\n"
    "heat_floor: 18.5\n"
    "humidity_guard: {rh_max_pct: 61, rh_clear_pct: 58}\n"
    "hold_ttl_minutes: 30\n"
    "release_confirm_buckets: 2\n"
    "stale_release_minutes: 30\n"
)


def _write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_loads_all_fields_and_config_id(tmp_path):
    cfg = load_config(_write(tmp_path, GOOD), temp_scale_env="C")
    assert cfg.elevated_at == 10.0 and cfg.scarcity_at == 20.0
    assert cfg.elevated_offset == 1.5 and cfg.scarcity_absolute == 29.5
    assert cfg.release_confirm_buckets == 2 and cfg.stale_release_minutes == 30
    assert len(cfg.config_id) == 64  # sha256 hex of file bytes


def test_missing_key_names_the_key(tmp_path):
    bad = GOOD.replace("release_confirm_buckets: 2\n", "")
    with pytest.raises(ConfigError, match="release_confirm_buckets"):
        load_config(_write(tmp_path, bad), temp_scale_env="C")


def test_no_silent_defaults_every_tunable_required(tmp_path):
    for key in ("elevated_offset", "scarcity_absolute", "heat_floor",
                "hold_ttl_minutes", "stale_release_minutes"):
        bad = "\n".join(l for l in GOOD.splitlines() if not l.startswith(key)) + "\n"
        with pytest.raises(ConfigError, match=key):
            load_config(_write(tmp_path, bad), temp_scale_env="C")


def test_off_grid_temp_rejected_for_celsius(tmp_path):
    bad = GOOD.replace("elevated_offset: 1.5", "elevated_offset: 1.3")
    with pytest.raises(ConfigError, match="grid"):
        load_config(_write(tmp_path, bad), temp_scale_env="C")


def test_temp_scale_env_mismatch_rejected(tmp_path):
    with pytest.raises(ConfigError, match="TEMP_SCALE"):
        load_config(_write(tmp_path, GOOD), temp_scale_env="F")


def test_invariants(tmp_path):
    bad = GOOD.replace("scarcity_at: 20", "scarcity_at: 9")
    with pytest.raises(ConfigError, match="elevated_at"):
        load_config(_write(tmp_path, bad), temp_scale_env="C")
    bad2 = GOOD.replace("rh_clear_pct: 58", "rh_clear_pct: 61")
    with pytest.raises(ConfigError, match="rh_clear"):
        load_config(_write(tmp_path, bad2), temp_scale_env="C")
