"""Controller error boundary. Spec §Telemetry: infrastructure is NOT a
domain error class."""
from __future__ import annotations


class InfrastructureError(Exception):
    """The substrate the controller runs on failed (Influx query, local
    disk) — not the controller failing at its job.

    Spec §Telemetry: when Influx is down every measurement gaps at once,
    while a thermostat read failure gaps only ``hvac.*``. The record already
    disambiguates the two, so recording infrastructure as ``crash`` would
    mislabel a substrate blip as a controller fault. Total loss is the
    off-box dead-man's job, not the controller's.
    """
