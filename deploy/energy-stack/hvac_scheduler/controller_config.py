"""Controller config loader for the commissioning-controller build.

Parses and validates the YAML config file (commissioning-controller.yaml).
Attaches to Config via the CONTROLLER_CONFIG_FILE env var (A1 wiring);
nothing in the control loop consumes it yet — later tasks wire it in.

Interface consumed by later tasks (names are load-bearing — do not rename):
    ControllerConfig  — frozen dataclass mirroring the YAML
    load_controller_config(path)  — parse + validate, log SHA256 at load
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # PyYAML

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-dataclasses (all frozen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComfortBlock:
    """One time-range block in the comfort program."""
    from_time: str  # "HH:MM"
    to_time: str    # "HH:MM"  -- may be < from_time (midnight wrap)
    cool: float


@dataclass(frozen=True)
class Flexibility:
    warm_band: float
    spike_extra: float


@dataclass(frozen=True)
class PriceTiersCents:
    elevated_at: float
    scarcity_at: float
    extreme_at: float


@dataclass(frozen=True)
class HumidityGuard:
    rh_max_pct: float
    rh_clear_pct: float


@dataclass(frozen=True)
class Ceiling:
    comfort_max: float


@dataclass(frozen=True)
class Stage1Ramp:
    enabled: bool


@dataclass(frozen=True)
class Modes:
    stage1_ramp: Stage1Ramp


# ---------------------------------------------------------------------------
# Top-level config dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ControllerConfig:
    """Frozen dataclass mirroring commissioning-controller.yaml.

    No supervisor, cheap, or runtime keys — safety is device-owned.
    Fields are named to match YAML keys (lower_snake_case).
    comfort_program is a list of plain dicts with keys 'from', 'to', 'cool'
    to preserve the original YAML shape for downstream consumers.
    """
    temp_scale: str
    # Plain dicts so downstream code can read ["from"], ["to"], ["cool"]
    # matching the YAML shape directly.
    comfort_program: tuple[dict[str, Any], ...]
    heat_floor: float
    flexibility: Flexibility
    price_tiers_cents: PriceTiersCents
    humidity_guard: HumidityGuard
    ceiling: Ceiling
    hold_ttl_minutes: int
    modes: Modes


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _on_grid(value: float, temp_scale: str) -> bool:
    """Return True if value lies on the scale's allowed grid.

    F: whole numbers (multiples of 1).
    C: multiples of 0.5.
    """
    if temp_scale == "F":
        return value == round(value)
    # C: multiples of 0.5 — multiply by 2, check integer
    return (value * 2) == round(value * 2)


def _collect_temp_values(raw: dict[str, Any]) -> list[float]:
    """Gather all temperature values that must be on-grid.

    Covers: comfort_program[*].cool, heat_floor, ceiling.comfort_max,
    and the flexibility offsets (warm_band, spike_extra) which are
    temperature deltas and must also be on-grid.
    """
    temps: list[float] = []
    for block in raw.get("comfort_program", []):
        temps.append(float(block["cool"]))
    temps.append(float(raw["heat_floor"]))
    temps.append(float(raw["ceiling"]["comfort_max"]))
    flex = raw.get("flexibility", {})
    temps.append(float(flex["warm_band"]))
    temps.append(float(flex["spike_extra"]))
    return temps


def _validate(raw: dict[str, Any]) -> None:
    """Raise ValueError with a clear message on any validation failure."""
    temp_scale = str(raw.get("temp_scale", "F"))

    # 3. On-grid check
    for v in _collect_temp_values(raw):
        if not _on_grid(v, temp_scale):
            grid_desc = "whole numbers" if temp_scale == "F" else "multiples of 0.5"
            raise ValueError(
                f"Temperature value {v} is not on the {temp_scale} grid "
                f"({grid_desc}). All temps in config must be on-grid."
            )

    # 4. Hysteresis order
    hg = raw.get("humidity_guard", {})
    rh_max = float(hg.get("rh_max_pct", 0))
    rh_clear = float(hg.get("rh_clear_pct", 0))
    if rh_clear >= rh_max:
        raise ValueError(
            f"humidity_guard: rh_clear_pct ({rh_clear}) must be strictly "
            f"less than rh_max_pct ({rh_max})."
        )

    # 5. hold_ttl_minutes positive
    ttl = int(raw.get("hold_ttl_minutes", 0))
    if ttl <= 0:
        raise ValueError(
            f"hold_ttl_minutes must be positive, got {ttl}."
        )


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_controller_config(path: str) -> ControllerConfig:
    """Parse, validate, and return a ControllerConfig.

    Logs the file path and its SHA256 at load for config provenance.
    Raises ValueError on validation failure, FileNotFoundError if missing.
    """
    p = Path(path)
    raw_bytes = p.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # Log provenance
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": "info",
        "msg": "controller_config_loaded",
        "path": str(p.resolve()),
        "sha256": sha256,
    }
    print(json.dumps(rec), flush=True)

    raw: dict[str, Any] = yaml.safe_load(raw_bytes.decode()) or {}

    _validate(raw)

    # Build sub-objects
    flex_raw = raw["flexibility"]
    flex = Flexibility(
        warm_band=float(flex_raw["warm_band"]),
        spike_extra=float(flex_raw["spike_extra"]),
    )

    pt_raw = raw["price_tiers_cents"]
    price_tiers = PriceTiersCents(
        elevated_at=float(pt_raw["elevated_at"]),
        scarcity_at=float(pt_raw["scarcity_at"]),
        extreme_at=float(pt_raw["extreme_at"]),
    )

    hg_raw = raw["humidity_guard"]
    humidity_guard = HumidityGuard(
        rh_max_pct=float(hg_raw["rh_max_pct"]),
        rh_clear_pct=float(hg_raw["rh_clear_pct"]),
    )

    ceil_raw = raw["ceiling"]
    ceiling = Ceiling(comfort_max=float(ceil_raw["comfort_max"]))

    modes_raw = raw["modes"]
    modes = Modes(
        stage1_ramp=Stage1Ramp(
            enabled=bool(modes_raw["stage1_ramp"]["enabled"])
        )
    )

    # comfort_program: keep as plain dicts with 'from', 'to', 'cool'
    comfort_program = tuple(
        {"from": str(b["from"]), "to": str(b["to"]), "cool": float(b["cool"])}
        for b in raw["comfort_program"]
    )

    return ControllerConfig(
        temp_scale=str(raw["temp_scale"]),
        comfort_program=comfort_program,
        heat_floor=float(raw["heat_floor"]),
        flexibility=flex,
        price_tiers_cents=price_tiers,
        humidity_guard=humidity_guard,
        ceiling=ceiling,
        hold_ttl_minutes=int(raw["hold_ttl_minutes"]),
        modes=modes,
    )
