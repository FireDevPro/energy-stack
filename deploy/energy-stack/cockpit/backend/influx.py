"""Influx row plumbing shared by the Vigil query builders.

The rev-4 ``query_*`` functions live in ``vigil_queries.py``; this module keeps
only the client protocol and the record-materializing helpers they reuse.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Protocol


class _Record(Protocol):
    @property
    def values(self) -> dict[str, Any]: ...

    def get_value(self) -> Any: ...

    def get_time(self) -> datetime: ...


class _Table(Protocol):
    @property
    def records(self) -> Iterable[_Record]: ...


class QueryApi(Protocol):
    """Subset of influxdb_client.QueryApi we depend on. Tests use a plain
    fake; the real client matches this shape."""

    def query(self, query: str, org: str | None = None) -> Iterable[_Table]: ...


def _first_row(tables: Iterable[_Table]) -> dict[str, Any] | None:
    for table in tables:
        for record in table.records:
            return dict(record.values)
    return None


def _series_rows(tables: Iterable[_Table]) -> list[dict[str, Any]]:
    """Materialize every row across every table into a list of dicts."""
    out: list[dict[str, Any]] = []
    for table in tables:
        for record in table.records:
            out.append(dict(record.values))
    return out
