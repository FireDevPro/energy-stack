---
date: 2026-05-20
owner: chris
status: draft
role-label: chris
---

# Python Type-Checker Rollout — Design Spec

## §1. Background & motivation

The scheduler stack is critical infrastructure: a 24/7 controller that drives real HVAC behavior in a pre-registered SCED field study (OSF filing target: early June 2026). Type hints exist throughout the codebase but are not enforced. The recent 2026-05-19 19:18Z freshness-blindness bug was an instance of "hints without verification" — `fetch_latest_comed` was annotated `-> float | None` but nothing checked whether callers handled both branches or whether the returned float represented current vs stale data. The cockpit + scheduler both had `price_is_stale` references that drifted out of sync.

That bug class is exactly what static type-checking exists to catch. The codebase currently has zero enforcement of its own type hints. For critical infrastructure heading into an OSF-locked behavioral commitment, "instructions without enforcement" is the wrong stance.

This spec scopes a per-service rollout of `mypy --strict` to the production Python services that ship with the SCED experiment.

The original handoff document (`STALE_DATA_HANDOFF.md`) called out adding a type checker as request #3:

> "Add a Python type checker (`mypy --strict` or equivalent) to the test suite for `deploy/energy-stack/hvac-scheduler/` and `tools/cockpit/backend/`. The project currently has no type checker enforcing the type hints in the code. This is a contributing factor to bugs that should have been caught at write-time but were caught only at runtime via review."

This spec broadens that scope to include all scheduler-stack Python services (the pollers, watchdog, telegram-notifier), because freshness-class bugs can originate anywhere in the data pipeline, not just the controller.

## §2. Goals & success criteria

### 2.1 Primary goal

Establish enforced static type-checking on all production Python code paths in the scheduler stack and cockpit-backend, before OSF freeze locks in the binding implementation.

### 2.2 Concrete deliverables

1. Mypy `--strict` infrastructure (config + CI workflow + per-service invocation script) landed and proven against the two `freshness.py` modules.
2. All 11 scheduler-stack Python services + cockpit-backend brought into the enforced set, each via its own per-service PR that includes: directory rename to underscore, `__init__.py` addition, greenfield type-check, bug fixes for findings.
3. A "type-debt backlog" document tracking high-value typed-adapter wrapping opportunities for untyped third-party libraries.
4. Per-service migration template documented so future services and contributors follow the pattern mechanically.

### 2.3 Success criteria

- Every service in scope is in the enforced set with zero `mypy --strict` errors.
- CI blocks merges that re-introduce type errors in any enforced service.
- Bugs found during per-service migration are fixed in the same PR (treated as bug fixes, not "type debt" to defer).
- Pattern is mechanical enough that adding a new service is "follow the template," not "design a new approach."
- Pre-OSF: at minimum the scheduler is in the enforced set (highest-priority service). Other services may complete post-OSF; the pattern is established.

### 2.4 Non-goals

- Adding type checking to the cockpit FRONTEND (TypeScript; separate tooling, separate concerns).
- Running mypy in advisory-only mode. The point is enforcement, not hints.
- Stubbing every third-party library exhaustively. Targeted typed adapters for high-value surfaces only; everything else is module-level ignored.

## §3. Scope

### 3.1 In scope

**Python services in `deploy/energy-stack/`:**
- `hvac-scheduler/` → renamed to `hvac_scheduler/` (the controller; highest-priority)
- `comed-poller/` → `comed_poller/`
- `eagle-poller/` → `eagle_poller/`
- `ecowitt-ingest/` → `ecowitt_ingest/`
- `haven-ingest/` → `haven_ingest/`
- `hvac-scheduler-watchdog/` → `hvac_scheduler_watchdog/`
- `nws-poller/` → `nws_poller/`
- `pjm-dm2-poller/` → `pjm_dm2_poller/`
- `refoss-poller/` → `refoss_poller/`
- `telegram-notifier/` → `telegram_notifier/`
- `thermostat-poller/` → `thermostat_poller/`

**Tools:**
- `tools/cockpit/backend/` (already a valid Python path; no rename needed)

**Tests:** included with strict mode. `# type: ignore[misc]` annotations accepted for mock-heavy fixtures.

### 3.2 Out of scope

- `scripts/` directory (utility code; one-shot tools; not part of the running experiment).
- `tools/cockpit/frontend/` (TypeScript).
- `tools/analysis/` and `tools/o2_capacity_reconstruction/` (analysis-only; could be added later as separate sub-projects).
- `deploy/energy-stack/influx-init/` if Python (not yet verified; assume out of scope until inspected).
- Renaming docker-compose SERVICE identifiers. Service names (e.g., `hvac-scheduler` in compose, in Influx tags, in Grafana queries, in Telegraf, in log labels) STAY hyphenated. Only filesystem PATHS are renamed. This preserves operator muscle memory and historical data continuity.

### 3.3 Why this scope

- Scheduler is OSF-critical: bugs there directly affect experimental outcomes.
- Cockpit-backend is operator visibility: bugs there silently break monitoring (the deferred `price_is_stale` consumer bug from PR #9 is an example).
- Pollers are the data pipeline: bugs there propagate forward into scheduler decisions. The 19:18Z bug arguably had a poller-layer component (the poller is also freshness-blind per the handoff document).
- Watchdog and telegram-notifier are operationally critical: bugs there mask other failures.
- `scripts/` is utility code — failure modes don't compromise the experiment.

## §4. Architecture

Three layers.

### 4.1 Layer 1: project-wide mypy config

A new `pyproject.toml` at the repo root. Contains the `[tool.mypy]` section and per-module overrides.

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_unreachable = true
warn_redundant_casts = true
warn_unused_ignores = true
plugins = ["pydantic.mypy"]

# files list grows as services migrate into enforced set
# PR 1 (infrastructure) enforces only the two freshness modules
files = [
    "deploy/energy-stack/hvac-scheduler/freshness.py",
    "tools/cockpit/backend/freshness.py",
]

# Untyped third-party library overrides (option B from design discussion §5)
[[tool.mypy.overrides]]
module = "influxdb_client.*"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "pyControl4.*"
ignore_missing_imports = true

# Add more overrides per-library as services are migrated in

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true
```

`strict = true` enables the standard mypy strict mode (per the official mypy docs): `disallow_untyped_defs`, `disallow_incomplete_defs`, `check_untyped_defs`, `disallow_untyped_decorators`, `no_implicit_optional`, `warn_redundant_casts`, `warn_unused_ignores`, `warn_return_any`, `no_implicit_reexport`, `strict_equality`, plus a few more. We additionally enable `warn_unreachable` (catches dead-code branches) on top of strict.

The `files` list is the explicit enforced set. Files NOT in this list are not type-checked. This makes the per-service rollout explicit: every addition to `files` is a deliberate PR decision.

### 4.2 Layer 2: per-service invocation script

A new `deploy/energy-stack/run_typecheck.sh`. Mirrors `run_tests.sh` exactly in structure.

```bash
#!/usr/bin/env bash
# Per-service mypy runner. Mirrors run_tests.sh.
#
# Each enforced service is type-checked in its own mypy invocation.
# This works around the hyphenated-directory issue (until each service
# is renamed and added to the enforced set) without requiring all
# services to migrate simultaneously.
#
# Exits non-zero if any enforced service fails.
#
# Usage:
#   bash deploy/energy-stack/run_typecheck.sh

set -euo pipefail
STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$STACK_DIR/../.." && pwd)"

# Services currently in the enforced set — mypy --strict must pass
enforced=(
    # populated as services migrate in via per-service PRs
)

# Services not yet migrated — listed here for visibility
not_yet_enforced=(
    hvac-scheduler
    comed-poller
    eagle-poller
    ecowitt-ingest
    haven-ingest
    hvac-scheduler-watchdog
    nws-poller
    pjm-dm2-poller
    refoss-poller
    telegram-notifier
    thermostat-poller
)

failed=()

# Per-service enforced checks (uses underscore-named dirs)
for svc in "${enforced[@]}"; do
    echo
    echo "=== type-checking $svc ==="
    if ! python -m mypy "$STACK_DIR/$svc"; then
        failed+=("$svc")
    fi
done

# Project-wide config covers the freshness modules and cockpit-backend
echo
echo "=== type-checking pyproject.toml-scoped files ==="
if ! (cd "$REPO_ROOT" && python -m mypy); then
    failed+=("pyproject.toml-files")
fi

echo
if (( ${#failed[@]} > 0 )); then
    echo "TYPE-CHECK FAILED: ${failed[*]}"
    exit 1
fi
echo "OK"
```

Per-service `mypy` invocation uses the directory path. Once a service is renamed and its `__init__.py` is added, mypy treats it as a proper package. Mypy reads `pyproject.toml` from the repo root automatically.

### 4.3 Layer 3: CI workflow

A new `.github/workflows/typecheck.yml`. Triggers on push to any branch and PRs to main. Runs on GitHub-hosted Ubuntu runner (build-time tooling; no Pi-lab dependency).

```yaml
name: Type Check

on:
  push:
    branches: ["**"]
  pull_request:
    branches: [main]

jobs:
  mypy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Cache mypy
        uses: actions/cache@v4
        with:
          path: ~/.mypy_cache
          key: mypy-${{ runner.os }}-${{ hashFiles('pyproject.toml') }}
      - name: Install dev dependencies
        run: pip install -r deploy/energy-stack/requirements-dev.txt
      - name: Install service requirements (enforced services only)
        run: |
          # Add per-service requirements installs as services migrate in
          pip install -r deploy/energy-stack/hvac-scheduler/requirements.txt
          pip install -r tools/cockpit/backend/requirements.txt
      - name: Run type checks
        run: bash deploy/energy-stack/run_typecheck.sh
```

`requirements-dev.txt` (existing or new) pins `mypy`, `pydantic`, any `types-*` stub packages. Service-specific requirements are installed because mypy needs to import them to resolve types from typed libraries.

### 4.4 File structure (end state)

```
pyproject.toml                                      # NEW (PR 1)
deploy/energy-stack/
├── run_tests.sh                                    # gradually loses hyphens as services migrate
├── run_typecheck.sh                                # NEW (PR 1)
├── pytest.ini                                      # workaround docstring shrinks as services migrate
├── requirements-dev.txt                            # may need mypy added
├── hvac_scheduler/                                 # renamed in PR 2 from hvac-scheduler/
│   ├── __init__.py                                 # NEW (PR 2)
│   ├── app.py
│   ├── freshness.py                                # enforced after PR 1
│   ├── influx_adapter.py                           # NEW (PR 2 — first typed adapter)
│   └── ...
├── comed_poller/                                   # renamed in its PR
└── ...
tools/cockpit/backend/                              # no rename needed
├── freshness.py                                    # enforced after PR 1
└── ...
.github/workflows/
├── deploy.yml                                      # existing — unchanged
├── check-freshness-drift.yml                       # existing — unchanged
└── typecheck.yml                                   # NEW (PR 1)
docs/
├── type-debt-backlog.md                            # NEW (PR 1)
└── plans/
    └── type-checker-plan.md                        # NEW — unified implementation plan
```

## §5. External library handling strategy

### 5.1 Default: module-level ignore for untyped libraries

Per `[[tool.mypy.overrides]]` blocks in `pyproject.toml`. Each untyped library gets one block:

```toml
[[tool.mypy.overrides]]
module = "influxdb_client.*"
ignore_missing_imports = true
```

Effect: mypy treats every symbol from the library as `Any`. Our code passes mypy regardless of what it does with the library. Centralized; no ignore-comment noise in source files.

Trade-off: bugs in our USE of the library are not caught. Passing a wrong type to `query_api.query(...)` does not raise a mypy error.

### 5.2 Targeted typed adapters for high-value surfaces

For library usage patterns that contributed to past bugs (specifically the influxdb-client `record.get_value() / record.get_time() / record.get_field()` API surface from the 19:18Z freshness bug), build a thin typed wrapper class that we own and that mypy CAN type-check.

Example: `deploy/energy-stack/hvac_scheduler/influx_adapter.py`

```python
"""Typed adapter around influxdb_client record/query results.

We own this surface; mypy enforces it. Direct use of
influxdb_client.* below this adapter is allowed but discouraged."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class TypedRecord:
    """Typed projection of an influxdb_client Record."""
    value: float
    time_utc: datetime
    field: str
    measurement: str

def project_record(record: object) -> TypedRecord:
    """Project a raw influxdb_client.Record into a TypedRecord.
    Raises ValueError if required fields are missing."""
    # implementation reads record.get_value() etc., does type
    # validation, returns the typed dataclass
    ...
```

Code throughout the scheduler then imports `from influx_adapter import project_record, TypedRecord` rather than touching the untyped library directly. The adapter is the typed surface; the library is the untyped impl detail.

A future tightening (tracked in the type-debt backlog, not part of this rollout) is to enforce "no direct `influxdb_client.*` import outside the adapter module" via a custom mypy rule or import-linter. For this rollout we treat the adapter as a convention.

### 5.3 The type-debt backlog

A new doc, `docs/type-debt-backlog.md`, tracks libraries that warrant adapter-wrapping. Updated as bugs surface or as per-service migration reveals problem surfaces. Initial seed list:

- `influxdb_client.*` — record API (top priority; bug-relevant)
- `pyControl4.*` — control-loop API surface (used by scheduler for HVAC commands)
- Any other library that ranks as "we hit a bug-shape here once" in future incidents

Each entry includes: library name, problem surface, estimated effort, link to incident if applicable.

### 5.4 Existing PyPI stub packages

Where available, use the `types-*` stub packages from PyPI (no adapter needed):
- `types-requests` for `requests`
- `types-pytz` if any service uses pytz
- Add to `requirements-dev.txt` per service as needed

`fastapi`, `httpx`, `pydantic`, `uvicorn`, `python-dotenv` — ship with their own types. No action needed.

## §6. Per-service migration template

Every service-rollout PR (PR 2 onwards) follows this template verbatim. Documented in `docs/type-debt-backlog.md` or alongside the unified plan.

### 6.1 Steps

1. **Rename:** `git mv deploy/energy-stack/<service>/ deploy/energy-stack/<service_underscored>/`
2. **Add package marker:** create empty `__init__.py` in the renamed directory.
3. **Update docker-compose:** edit `deploy/energy-stack/docker-compose.yml`:
   - Change the service's `build.context:` path to the new underscore name.
   - **DO NOT** change the service name (the YAML key, e.g., `hvac-scheduler:` stays hyphenated).
4. **Update `run_tests.sh`:** in the `services=(...)` array near the top of the script, change this service's entry from hyphen to underscore.
5. **Update `run_typecheck.sh`:** remove service from `not_yet_enforced`; add to `enforced` (both with underscore name).
6. **Update `pyproject.toml`:** add service paths to `files = [...]` list (or use directory pattern).
7. **Update path references:** sed any markdown or shell scripts that reference `./<service>/` to use the underscore. Operational identifiers stay hyphenated.
8. **Local type-check:** `bash deploy/energy-stack/run_typecheck.sh` — iterate until clean.
9. **Triage each finding:**
   - Real bug → fix in the same PR (treat as bug-fix value).
   - Missing/incorrect annotation → fix the annotation.
   - Untyped library call → add to `[[tool.mypy.overrides]]` in `pyproject.toml` and consider adapter wrapping if surface is high-value.
10. **Tests pass:** `cd deploy/energy-stack/<service_underscored>/ && python -m pytest .` — verifies the rename didn't break test collection.
11. **Dual-review** per AGENTS.md: superpowers:requesting-code-review + codex adversarial-review in parallel.
12. **Open PR** with `--base main`. Per AGENTS.md, no stacking; wait for prior PR to merge before opening the next service PR.
13. **Operator merges** in GitHub UI. The deploy.yml workflow handles the rest (since the rename touches `deploy/**`, it triggers redeploy of the renamed service).

### 6.2 Expected outcome per service PR

- Net file changes: ~10-30 (rename moves, `__init__.py` add, `pyproject.toml` edit, compose edit, run script edits, doc reference updates, plus any bug-fix changes).
- Test count: unchanged (rename doesn't add or remove tests; type-fixes may add anti-regression tests if real bugs are found).
- Net mypy errors after PR: zero on this service (greenfield discipline).
- Operational impact: zero downtime; deploy.yml rebuilds the service in-place.

### 6.3 Order of services

Scheduler is PR 2 (highest priority; OSF-critical). After that, suggested order by likely bug-yield:
1. `cockpit-backend` (PR 3) — operator visibility; closes the deferred `price_is_stale` consumer
2. `comed-poller` (PR 4) — data pipeline; freshness-bug-adjacent
3. `pjm-dm2-poller` (PR 5) — DM2 / 5CP risk inputs
4. `hvac-scheduler-watchdog` (PR 6) — depends on scheduler module shape
5. Remaining pollers and ingest services in any order (PR 7-N)
6. `telegram-notifier` (last) — operator notifications; simplest code

This order can be revised based on what bugs surface in early PRs.

**Note for cockpit-backend (PR 3):** the §6.1 template applies in spirit but several steps are skipped because `tools/cockpit/backend/` is already a valid Python path:
- Step 1 (rename) — skip.
- Step 2 (`__init__.py`) — already exists per earlier inspection.
- Step 4 (`run_tests.sh`) — cockpit-backend isn't in `run_tests.sh`; its tests live under `tools/cockpit/backend/tests/` and run via the cockpit's own pytest invocation. Document this in the PR.
- Step 7 (path references) — minimal; cockpit-backend's path was never hyphenated.
- Other steps (compose update — cockpit-backend isn't in docker-compose either, since it runs locally; `pyproject.toml` files-list update; mypy iteration; bug fixes; dual-review) apply as written.

## §7. Phased rollout

### Phase 1: Infrastructure + freshness modules (PR 1)

**Deliverables:**
- `pyproject.toml` at repo root with mypy config, pydantic plugin, overrides for `influxdb_client.*` and `pyControl4.*`.
- `deploy/energy-stack/run_typecheck.sh` (empty enforced array; freshness modules enforced via pyproject.toml `files`).
- `.github/workflows/typecheck.yml` (runs `run_typecheck.sh` on every push/PR).
- `docs/type-debt-backlog.md` (seeded with influxdb_client and pyControl4 as known-priority).
- `deploy/energy-stack/requirements-dev.txt` updated with `mypy>=1.10` and `pydantic` (if not already present).

**Acceptance criteria:**
- `bash deploy/energy-stack/run_typecheck.sh` passes locally and in CI on a clean branch.
- Deliberately introducing a type error in `freshness.py` causes CI to fail. (Verified via a temporary commit + revert.)
- The two `freshness.py` modules (scheduler + cockpit) type-check clean under `mypy --strict`.

### Phase 2: Scheduler (PR 2)

**Deliverables:**
- Rename `deploy/energy-stack/hvac-scheduler/` → `hvac_scheduler/`.
- Add `__init__.py` to renamed dir.
- Update docker-compose, run_tests.sh, run_typecheck.sh, pyproject.toml.
- First typed adapter: `deploy/energy-stack/hvac_scheduler/influx_adapter.py` wrapping `influxdb_client` record API.
- Refactor `fetch_latest_comed` and any other callers of `record.get_*()` to use the typed adapter.
- Triage all `mypy --strict` findings on the scheduler.
- Fix real bugs; annotate missing types; document any necessary ignores.

**Acceptance criteria:**
- `mypy --strict deploy/energy-stack/hvac_scheduler/` returns zero errors.
- All 337+ scheduler tests pass (no regressions from the rename).
- `docker compose up -d hvac-scheduler` succeeds with the renamed dir (verified locally if possible; otherwise verified by post-merge deploy).
- Service identifier `hvac-scheduler` in compose, Grafana, Influx, logs is unchanged (operator-facing identifier preservation verified by grep).
- Real bugs found are fixed in-PR with anti-regression tests where applicable.

### Phase 3: Remaining services (PR 3-N)

Each service migrates per the §6 template. One PR per service. No stacking.

**Acceptance criteria per PR:**
- That service is in the enforced set in `pyproject.toml`.
- `mypy --strict` returns zero errors for that service.
- Service tests pass.
- Service deploys successfully (verified post-merge via deploy.yml).
- Any real bugs found are fixed in-PR.

### Phase 4: Cleanup (final PR after all services enforced)

**Deliverables:**
- Remove the `not_yet_enforced` array from `run_typecheck.sh` (now empty).
- Simplify `pytest.ini` docstring to remove the deferred-rename note.
- Mark spec status `shipped`.

## §8. Risks & mitigations

### 8.1 Risk: scheduler type-check surface is large; many findings

The scheduler is ~350+ annotated lines and many more unannotated. Strict mode may surface dozens of findings.

**Mitigation:** triage explicitly. Each finding is categorized as bug, missing-annotation, or library-edge. Bugs are fixed in-PR. Annotations are added inline. Library edges go to overrides or adapter backlog. If scheduler PR balloons beyond reasonable review size, split into "rename + infrastructure" and "type-fixes per file region" PRs — but maintain greenfield discipline (the service is not in the enforced set until its full directory passes).

### 8.2 Risk: external library behavior changes when upgrading mypy

Mypy releases regularly; new versions surface new findings. CI could go red on dependency updates unrelated to our code changes.

**Mitigation:** pin `mypy` version in `requirements-dev.txt`. Upgrades are deliberate PRs that triage new findings. Pin to `mypy>=1.10,<2.0` for the major version we land on.

### 8.3 Risk: hyphenated-dir collisions during partial migration

Until all services are renamed, some have underscore names and some hyphen. `run_tests.sh` and `run_typecheck.sh` both need to know which is which.

**Mitigation:** scripts are explicit. The `enforced` (underscore) and `not_yet_enforced` (hyphen) arrays in `run_typecheck.sh` make the split visible. `run_tests.sh` updates its services array as each service migrates. No script needs to handle "either name might exist" — each name is in exactly one list at any time.

### 8.4 Risk: docker-compose service-name vs directory-name confusion

A maintainer might rename both, breaking historical Influx tag continuity and Grafana queries.

**Mitigation:** the migration template explicitly preserves the service identifier. Spec §6.1 step 3 calls it out. Service PRs include a verification step: `grep -rn "<service>" tools/cockpit deploy/energy-stack/grafana deploy/energy-stack/telegraf` should confirm operational references to the hyphenated identifier are unchanged.

### 8.5 Risk: pydantic plugin compatibility

`pydantic.mypy` may have version requirements. Cockpit-backend uses `pydantic>=2.5`. Pydantic v2's mypy plugin works differently than v1's.

**Mitigation:** PR 1 verifies pydantic plugin loads correctly against the actual installed pydantic version. Pin if necessary. If plugin behavior is surprising, document the relevant config in `pyproject.toml` and reference it from the spec.

### 8.6 Risk: test files in scope produce excessive ignore noise

Mock-heavy tests may need many `# type: ignore[misc]` annotations under strict mode.

**Mitigation:** include tests as scoped; accept some `# type: ignore` annotations on mocks. If a service's test directory generates more than ~10 ignore comments, that's a signal to revisit: either selectively relax test settings via `[[tool.mypy.overrides]]` for that module's `test_*.py`, or invest in better-typed test fixtures. Decision made per service, not globally.

### 8.7 Risk: scope creep during scheduler PR (the largest service)

Scheduler PR includes rename + adapter + bug fixes + annotations. Could grow.

**Mitigation:** if PR exceeds ~500 lines net diff or more than 1 day of focused work, split:
- PR 2a: rename + infrastructure (smallest possible)
- PR 2b: scheduler enforced + adapter + type-fixes
- PR 2c: scheduler bug-fix follow-ups, if any are large enough to warrant separate review

Subagent-driven-development pattern from freshness PR is the model: small task-level commits within the PR; phase checkpoints with dual-review.

### 8.8 Risk: missing types-* PyPI packages

Some libraries we use may have stubs published as separate `types-*` packages that we don't know about. Without them, mypy treats the library as untyped even when stubs exist.

**Mitigation:** during per-service migration, run `mypy --install-types --non-interactive` to suggest available stub packages. Install any relevant ones into `requirements-dev.txt`. Document in the type-debt backlog.

## §9. Acceptance gates

### 9.1 Per-PR gate (each service PR)

- `mypy --strict` returns 0 errors on the service.
- Service tests pass.
- Dual-review run; findings addressed or explicitly deferred with rationale.
- Deploy.yml succeeds post-merge (verified by GitHub Actions run).
- Operational identifier (compose service name) unchanged — verified by `grep` for the hyphenated name still appearing in compose, grafana JSON, telegraf, log labels.

### 9.2 Project-level gate (when "all services enforced")

- `run_typecheck.sh` returns 0 with all 11 services + cockpit-backend in `enforced`.
- `not_yet_enforced` array is empty.
- CI gate active on `main` (any PR re-introducing a type error blocks merge).
- `pytest.ini` workaround docstring removed.
- Spec status updated to `shipped`.

### 9.3 Pre-OSF gate (minimum required before filing)

- Scheduler is in the enforced set.
- Cockpit-backend is in the enforced set (since the freshness PR left a known-broken consumer there).
- All findings from those two services are either fixed or documented as accepted (with rationale in the PR).

Other services may complete post-OSF.

## §10. Status & history

| Date | Status | Note |
|---|---|---|
| 2026-05-20 | draft | Initial spec. Captures decisions from brainstorming session 2026-05-20: scope (C — all scheduler-stack services + cockpit-backend, scripts/ deferred); tool (mypy --strict); rollout strategy (per-service greenfield, infrastructure-PR-first); hyphenated-dir resolution (rename per service while preserving operational identifier); external library strategy (option B — module-level ignore + targeted typed adapters tracked as backlog); pydantic plugin enabled; tests included with strict; PR 1 bootstrap with the two freshness modules. Open for first-pass dual-review (superpowers:requesting-code-review + codex adversarial-review). |
