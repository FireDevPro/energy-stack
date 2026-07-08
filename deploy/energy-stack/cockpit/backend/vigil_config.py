"""Tier thresholds + humidity gate, read from the controller's own config.

Single source of truth: the cockpit mounts the SAME
``commissioning-controller.yaml`` the rev-4 controller reads (read-only bind
mount), so tier thresholds can never drift between the two services. Parsed
once at startup.

Path resolution: ``VIGIL_CONFIG_PATH`` env override → the container mount at
``/config/commissioning-controller.yaml`` → the repo copy (workstation dev).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class VigilConfig:
    elevated_at: float
    scarcity_at: float
    hysteresis_cents: float
    rh_max_pct: float
    rh_clear_pct: float
    hold_ttl_minutes: int


_MOUNT_PATH = Path("/config/commissioning-controller.yaml")
# Workstation dev fallback: backend/ -> cockpit/ -> energy-stack/hvac_scheduler/
_REPO_FALLBACK = (
    Path(__file__).resolve().parent.parent.parent
    / "hvac_scheduler"
    / "commissioning-controller.yaml"
)


def config_path() -> Path:
    env = os.environ.get("VIGIL_CONFIG_PATH")
    if env:
        return Path(env)
    if _MOUNT_PATH.exists():
        return _MOUNT_PATH
    return _REPO_FALLBACK


def load_config(path: Path | None = None) -> VigilConfig:
    p = path or config_path()
    data = yaml.safe_load(p.read_text())
    tiers = data["price_tiers_cents"]
    hg = data["humidity_guard"]
    return VigilConfig(
        elevated_at=float(tiers["elevated_at"]),
        scarcity_at=float(tiers["scarcity_at"]),
        hysteresis_cents=float(tiers["hysteresis_cents"]),
        rh_max_pct=float(hg["rh_max_pct"]),
        rh_clear_pct=float(hg["rh_clear_pct"]),
        hold_ttl_minutes=int(data["hold_ttl_minutes"]),
    )
