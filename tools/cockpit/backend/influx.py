"""Influx query builders for the cockpit snapshot.

Phase 3 follow-on: each function below queries a single Influx
measurement and returns a dict shaped for snapshot.build_snapshot_live.

For initial Phase 3 landing, these are unimplemented stubs. The
contract (function signatures + return shapes) is locked here so the
assembler and tests can build against it before live wire-up.
"""
from __future__ import annotations

from typing import Any, Protocol


class QueryApi(Protocol):
    """Influx query_api shape we depend on. Tests use a mock."""

    def query(self, query: str, org: str | None = None) -> Any: ...


def latest_thermostat(client: QueryApi, *, bucket: str) -> dict[str, Any]:
    """Latest hvac.thermostat row. Returns the dict shape consumed by
    Snapshot.thermostat (without the `freshness` fields — those are
    computed by the assembler from source_ts)."""
    raise NotImplementedError("Phase 3 follow-on")


def latest_arm_mode(client: QueryApi, *, bucket: str) -> dict[str, Any]:
    """Latest hvac.arm_mode row. mode_actual + arm + source_ts."""
    raise NotImplementedError("Phase 3 follow-on")


def latest_price(client: QueryApi, *, bucket: str) -> dict[str, Any]:
    """Latest comed.prices row + tier classification (mirrors
    price_overlay.py logic)."""
    raise NotImplementedError("Phase 3 follow-on")


def latest_heartbeat(
    client: QueryApi, *, bucket: str
) -> dict[str, Any] | None:
    """Last hvac.heartbeat row in last 30 min, or None."""
    raise NotImplementedError("Phase 3 follow-on")


def feed_health_table(client: QueryApi, *, bucket: str) -> list[dict[str, Any]]:
    """Per-feed last-write times + classification.

    Returns a list of dicts shaped for Snapshot.feed_health entries:
    [{name, status, label}].
    """
    raise NotImplementedError("Phase 3 follow-on")
