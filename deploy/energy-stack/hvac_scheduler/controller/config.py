"""Rev 4 controller config loader. Spec: rev 4 §Config is the experimental surface.

Every tunable is REQUIRED — no code defaults, so no seed can fossilize into a
fallback. temp_scale must match the TEMP_SCALE env (coherence check). Temps
must sit on the scale's grid (0.5 for C, 1.0 for F). config_id = sha256 of the
file bytes, stamped into telemetry for provenance.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import yaml


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ControllerConfig:
    temp_scale: str
    elevated_at: float
    scarcity_at: float
    hysteresis_cents: float
    elevated_offset: float
    scarcity_absolute: float
    heat_floor: float
    rh_max_pct: float
    rh_clear_pct: float
    hold_ttl_minutes: int
    release_confirm_buckets: int
    stale_release_minutes: int
    config_id: str


def _require(mapping: dict[str, Any], key: str, ctx: str = "") -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigError(f"missing required config key: {ctx}{key}")
    return mapping[key]


def _on_grid(value: float, scale: str, name: str) -> float:
    step = 0.5 if scale == "C" else 1.0
    if round(value / step) * step != value:
        raise ConfigError(f"{name}={value} is off the {scale} grid (step {step})")
    return float(value)


def load_config(path: str, temp_scale_env: str) -> ControllerConfig:
    with open(path, "rb") as f:
        raw_bytes = f.read()
    doc = yaml.safe_load(raw_bytes)
    if not isinstance(doc, dict):
        raise ConfigError("config root must be a mapping")

    scale = str(_require(doc, "temp_scale"))
    if scale not in ("C", "F"):
        raise ConfigError(f"temp_scale must be C or F, got {scale!r}")
    if scale != temp_scale_env:
        raise ConfigError(
            f"TEMP_SCALE env ({temp_scale_env!r}) != yaml temp_scale ({scale!r})"
        )

    tiers = _require(doc, "price_tiers_cents")
    elevated_at = float(_require(tiers, "elevated_at", "price_tiers_cents."))
    scarcity_at = float(_require(tiers, "scarcity_at", "price_tiers_cents."))
    hysteresis = float(_require(tiers, "hysteresis_cents", "price_tiers_cents."))
    guard = _require(doc, "humidity_guard")
    rh_max = float(_require(guard, "rh_max_pct", "humidity_guard."))
    rh_clear = float(_require(guard, "rh_clear_pct", "humidity_guard."))

    cfg = ControllerConfig(
        temp_scale=scale,
        elevated_at=elevated_at,
        scarcity_at=scarcity_at,
        hysteresis_cents=hysteresis,
        elevated_offset=_on_grid(float(_require(doc, "elevated_offset")), scale, "elevated_offset"),
        scarcity_absolute=_on_grid(float(_require(doc, "scarcity_absolute")), scale, "scarcity_absolute"),
        heat_floor=_on_grid(float(_require(doc, "heat_floor")), scale, "heat_floor"),
        rh_max_pct=rh_max,
        rh_clear_pct=rh_clear,
        hold_ttl_minutes=int(_require(doc, "hold_ttl_minutes")),
        release_confirm_buckets=int(_require(doc, "release_confirm_buckets")),
        stale_release_minutes=int(_require(doc, "stale_release_minutes")),
        config_id=hashlib.sha256(raw_bytes).hexdigest(),
    )

    if not (0 < cfg.elevated_at < cfg.scarcity_at):
        raise ConfigError("invariant: 0 < elevated_at < scarcity_at")
    if cfg.hysteresis_cents <= 0:
        raise ConfigError("invariant: hysteresis_cents > 0")
    if cfg.elevated_offset <= 0:
        raise ConfigError("invariant: elevated_offset > 0 (warm-only)")
    if not (0 < cfg.rh_clear_pct < cfg.rh_max_pct):
        raise ConfigError("invariant: rh_clear_pct < rh_max_pct")
    if cfg.hold_ttl_minutes < 15 or cfg.release_confirm_buckets < 1 \
            or cfg.stale_release_minutes < 5:
        raise ConfigError("invariant: hold_ttl_minutes>=15, release_confirm_buckets>=1, stale_release_minutes>=5")
    return cfg
