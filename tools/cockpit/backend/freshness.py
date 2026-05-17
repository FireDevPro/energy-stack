"""Staleness thresholds per source cadence.

Python mirror of tools/cockpit/frontend/src/freshness.ts. The two are
hand-paired; a Phase 4+ codegen step could derive one from the other,
but for now keep the values in sync manually.

hvac.actions is event-driven and NOT a staleness signal — Action node
uses NOT-FIRED-THIS-TICK / last-fire / APPLIED / SHADOW semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Freshness = Literal["fresh", "warn", "stale", "missing"]


@dataclass(frozen=True)
class Thresholds:
    fresh_max_ms: int
    warn_max_ms: int
    stale_max_ms: int


def _min(n: float) -> int:
    return int(n * 60 * 1000)


def _hr(n: float) -> int:
    return int(n * 60 * 60 * 1000)


THRESHOLDS: dict[str, Thresholds] = {
    "decision_trace.price_overlay_eval": Thresholds(_min(6), _min(10), _min(15)),
    "decision_trace.layer_resolution":   Thresholds(_min(6), _min(10), _min(15)),
    "decision_trace.day_type_decision":  Thresholds(_hr(16), _hr(30), _hr(72)),
    "decision_trace.precool_decision":   Thresholds(_hr(26), _hr(40), _hr(72)),
    "hvac.arm_mode":                     Thresholds(_min(6), _min(10), _min(15)),
    "hvac.thermostat":                   Thresholds(_min(12), _min(20), _min(30)),
    "comed.prices":                      Thresholds(_min(6), _min(10), _min(15)),
    "nws.forecast":                      Thresholds(_min(35), _min(90), _hr(12)),
    "pjm.load_forecast":                 Thresholds(_hr(14), _hr(28), _hr(50)),
    "pjm.rt_hrl_lmps":                   Thresholds(_min(75), _hr(3), _hr(12)),
    "refoss.channel":                    Thresholds(_min(1), _min(3), _min(10)),
    "eagle.meter":                       Thresholds(_min(1), _min(3), _min(10)),
}


def classify(source: str, age_ms: int) -> Freshness:
    """Map an age in ms to a freshness bucket per source cadence."""
    t = THRESHOLDS.get(source)
    if t is None:
        return "fresh"
    if age_ms <= t.fresh_max_ms:
        return "fresh"
    if age_ms <= t.warn_max_ms:
        return "warn"
    if age_ms <= t.stale_max_ms:
        return "stale"
    return "missing"
