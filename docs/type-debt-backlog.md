---
date: 2026-05-20
owner: chris
status: active
role-label: chris
---

# Type-Debt Backlog

Tracks third-party libraries used in the energy-stack that warrant
typed-adapter wrapping (per spec §5.2-5.5). Each library on this
backlog should eventually have a thin typed wrapper module that we
own and that mypy can verify against; direct use of the library
outside its adapter module is forbidden via an import-linter contract.

Default for libraries NOT on this backlog: `[[tool.mypy.overrides]]
ignore_missing_imports = true` in pyproject.toml. The library is
treated as `Any` everywhere. Bugs in our USE of those libraries are
not caught by mypy. This is acceptable for low-risk surfaces; the
backlog tracks high-value surfaces where adapter wrapping pays off.

---

## Adapter Candidates

### influxdb-client (`influxdb_client.*`) — Priority: P0

**Status:** Adapter lands in PR 3 (`deploy/energy-stack/hvac_scheduler/influx_adapter.py`).

**Surface:** the `record.get_value() / record.get_time() / record.get_field()` API used by `fetch_latest_comed` and related Influx query helpers. The 2026-05-19 19:18Z freshness bug was directly enabled by this surface returning `Any` — nothing checked whether callers handled both branches of `float | None` or whether the float represented current vs stale data.

**Operator follow-up (PR 1 acceptance):** VERIFIED 2026-05-21 — `influxdb_client==1.48.0` ships `py.typed`. The `[[tool.mypy.overrides]] ignore_missing_imports = true` block for `influxdb_client.*` has been removed from pyproject.toml; upstream stubs are now authoritative. The adapter pattern in PR 3 still applies as a projection seam.

### Device client (`ThermostatClient` seam) — Priority: P1

**Status:** Not yet scheduled. The Control4/`pyControl4` client this entry originally tracked has been retired; the device client is now a stubbed `ThermostatClient` seam, with TCC/`aiosomecomfort` wiring deferred.

**Surface:** HVAC command API used by the scheduler to drive setpoint changes. Smaller surface than influxdb-client; bugs here surface as commanded-setpoint mismatches.

**Adapter scope (future):** the `ThermostatClient` seam is already the typed projection point — wrap the eventual TCC (`aiosomecomfort`) command-issuance methods with typed dataclasses for inputs and a typed result for outputs.

### Other libraries

Libraries that ship `py.typed` natively (no adapter needed, no override needed):
- `fastapi`, `httpx`, `pydantic`, `uvicorn`, `python-dotenv` (used by cockpit-backend)
- `aiohttp` (>=3.8) — used by `nws-poller`, `pjm-dm2-poller`, `telegram-notifier`. Bundled stubs surface usage issues; expect findings in those service PRs.

Libraries with PyPI stub packages (`types-*` — add to `requirements-dev.in`, regenerate `requirements-dev.lock`):
- `types-requests` for `requests` (if/when a service uses it)

---

## Stub-Status Verification Log

PR 1 acceptance includes verifying the stub status of `influxdb_client==1.48.0`. Record the finding here:

- `influxdb_client==1.48.0`: ships `py.typed` (verified 2026-05-21 via local install on Python 3.13). Override block in pyproject.toml has been removed; upstream stubs are now active for mypy. The adapter pattern in PR 3 still applies as a projection seam.

---

## Adding to This Backlog

A library belongs here when:
1. We hit a bug-shape using it (like the 19:18Z freshness bug)
2. Our usage is wide enough that wrapping pays off
3. The library lacks typed stubs upstream

Add an entry: library name, priority (P0-P2), current status, surface description, adapter scope. Link to the relevant incident or PR if applicable.
