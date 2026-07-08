"""vigil_config — parses tier thresholds + humidity gate from the mounted
controller config (single source of truth)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ..vigil_config import config_path, load_config

# The real controller config, reachable from the repo at test time.
_REAL = (
    Path(__file__).resolve().parents[3] / "hvac_scheduler" / "commissioning-controller.yaml"
)


def test_loads_real_controller_config() -> None:
    cfg = load_config(_REAL)
    assert cfg.elevated_at == 10.0
    assert cfg.scarcity_at == 20.0
    assert cfg.hysteresis_cents == 2.0
    assert cfg.rh_max_pct == 61.0
    assert cfg.rh_clear_pct == 58.0
    assert cfg.hold_ttl_minutes == 30


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIGIL_CONFIG_PATH", str(_REAL))
    assert config_path() == _REAL
