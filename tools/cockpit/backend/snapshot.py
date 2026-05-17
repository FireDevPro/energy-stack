"""Snapshot assembler — combines Influx + Loki query results into the
Snapshot dict that the frontend consumes.

This module is the contract seam between live data and the cockpit UI.
The shape it produces MUST match the TS `Snapshot` interface defined
at tools/cockpit/frontend/src/types.ts. Drift surfaces in
test_snapshot.py via the paired fixture comparison.

For Phase 3 initial landing, this module produces a canned snapshot
equivalent to the summer_normal fixture. Actual Influx/Loki query
wire-up lands in a follow-on PR after the contract is verified end-
to-end via mocked tests + frontend integration smoke.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.cockpit.backend.tests.fixtures.summer_normal import SUMMER_NORMAL


def build_snapshot_canned() -> dict[str, Any]:
    """Return the canned summer_normal snapshot.

    Used in two contexts: (1) early-Phase-3 backend smoke before
    Influx/Loki queries are wired, (2) test_snapshot round-trip
    against the paired Python fixture.
    """
    return _deep_copy(SUMMER_NORMAL)


def build_snapshot_live(
    *,
    influx_results: dict[str, Any],
    loki_results: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Live snapshot assembler. NOT yet wired.

    Phase 3 follow-on will populate this from real query results. The
    signature is stable: tests can construct canned `influx_results`
    and `loki_results` dicts and assert the assembled output matches
    the paired fixture.
    """
    # Stub: until the live wiring lands, raise so a caller doesn't
    # silently get an empty snapshot.
    raise NotImplementedError(
        "Live snapshot assembly lands in the Phase 3 follow-on. "
        "Use build_snapshot_canned() for Phase 3 backend bring-up."
    )


def now_iso() -> str:
    """ISO-8601 timestamp in CT (America/Chicago). Phase 3 follow-on
    will replace SUMMER_NORMAL's hardcoded timestamps with live values
    rooted at this helper."""
    _ = now  # noqa: F841 (placeholder until wire-up)
    return datetime.now(timezone.utc).isoformat()


def _deep_copy(obj: Any) -> Any:
    """Local deep-copy for the canned snapshot. Standard library copy
    would suffice; rolled here so the snapshot assembler has zero
    runtime deps beyond fastapi + influx-client."""
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy(v) for v in obj]
    return obj
