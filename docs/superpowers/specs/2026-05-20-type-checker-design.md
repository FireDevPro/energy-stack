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
2. All 11 scheduler-stack Python services + cockpit-backend brought into the enforced set, each via its own per-service PR that includes: directory rename to underscore, package conversion (`__init__.py`, relative imports, Dockerfile entrypoint update), greenfield type-check, bug fixes for findings.
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
- `deploy/energy-stack/influx-init/` — verified shell-only (`FROM influxdb:2.7` + shell scripts in `tasks/`; no Python). No action.
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

A new `pyproject.toml` at the repo root. Contains the `[tool.mypy]` section, per-module overrides, and the `[tool.importlinter]` adapter-boundary configuration.

```toml
[tool.mypy]
# All service Dockerfiles use FROM python:3.13-slim. Mypy MUST match the
# runtime Python version, not a guess. Locking 3.13 keeps version-gated
# narrowing (Self, PEP 695 type aliases, etc.) consistent between local
# dev, CI, and the deployed container.
python_version = "3.13"
strict = true
warn_unreachable = true
warn_redundant_casts = true
warn_unused_ignores = true
# Full package model: every renamed service dir has __init__.py and
# is a regular Python package. Sibling imports use relative form
# (`from .freshness import classify`). Dockerfile WORKDIR is set so
# the service package is on sys.path; CMD invokes via
# `python -m <service>.app`. This is the modern-Python idiom and the
# only one that works with mypy + import-linter + pytest cleanly.
# Namespace packages were considered and rejected because
# import-linter (via grimp) raises NamespacePackageEncountered on
# PEP 420 root packages, blocking the adapter-boundary contracts.
# See §6.1 for the full per-service conversion steps.
plugins = ["pydantic.mypy"]
# deploy/energy-stack is on sys.path so each service directory is a
# top-level package (e.g., hvac_scheduler.app, hvac_scheduler.freshness).
# cockpit-backend uses absolute imports under the tools.cockpit.backend
# namespace, which is already a regular package, so it's discoverable
# from repo root without additional mypy_path entries.
mypy_path = "deploy/energy-stack"

# files list grows as services migrate into enforced set.
# PR 1 (infrastructure) enforces only the two freshness modules via
# this explicit files list. As each service migrates in (PR 2 onwards),
# the migration template removes that service's individual file entries
# from this list — the per-service mypy invocation in run_typecheck.sh
# then covers the whole renamed directory. Avoids double-invocation
# (each file checked once, not twice).
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

# Test files: keep strict checks that catch real test bugs; relax
# annotation-completeness checks that just produce bookkeeping noise
# in mock-heavy fixtures. Mypy override `module` patterns are
# fully-qualified dotted module names, NOT filename globs. Each service
# enumerates its own test module list when it migrates in. Below is
# the seed list for the freshness modules (PR 1). PR 3 (scheduler
# enforcement) appends scheduler's test modules. Each subsequent
# service PR appends its own.
[[tool.mypy.overrides]]
module = [
    # Populated as services migrate. Examples:
    # "hvac_scheduler.test_hvac_scheduler",
    # "hvac_scheduler.test_freshness",
    # "hvac_scheduler.test_decision_trace",
    # "hvac_scheduler.conftest",
]
disallow_untyped_defs = false
disallow_untyped_decorators = false
# Other strict checks (disallow_any_generics, no_implicit_optional,
# warn_redundant_casts, warn_return_any) remain ON for test code.

# Add more overrides per-library as services are migrated in

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true

# Adapter-boundary enforcement (see §5.5). Each typed adapter ships a
# corresponding import-linter contract banning direct use of the
# wrapped library outside the adapter module. First contract lands
# in PR 3 (formerly 2b) with the influx_adapter.
[tool.importlinter]
root_packages = []
include_external_packages = true  # required for `forbidden` contracts
                                  # targeting third-party modules
# Populated per-service as adapters are added. Example after PR 3:
#   root_packages = ["hvac_scheduler"]
# with a contract block:
#   [[tool.importlinter.contracts]]
#   name = "Only influx_adapter may import influxdb_client"
#   type = "forbidden"
#   source_modules = ["hvac_scheduler"]
#   forbidden_modules = ["influxdb_client"]
#   ignore_imports = ["hvac_scheduler.influx_adapter -> influxdb_client"]
```

`strict = true` enables the standard mypy strict mode (per the official mypy docs): `disallow_untyped_defs`, `disallow_incomplete_defs`, `check_untyped_defs`, `disallow_untyped_decorators`, `no_implicit_optional`, `warn_redundant_casts`, `warn_unused_ignores`, `warn_return_any`, `no_implicit_reexport`, `strict_equality`, plus a few more. We additionally enable `warn_unreachable` (catches dead-code branches) on top of strict.

The `files` list is the explicit enforced set during PR 1. Files NOT in this list and not under an enforced-service directory are not type-checked. As each service migrates in, its file entries are removed from this list and the per-service mypy invocation in `run_typecheck.sh` covers the whole renamed directory.

### 4.2 Layer 2: per-service invocation script

A new `deploy/energy-stack/run_typecheck.sh`. Mirrors `run_tests.sh` in structure. Must be run under bash (Git Bash or WSL on Windows; native bash on Linux/macOS).

```bash
#!/usr/bin/env bash
# Per-service mypy + import-linter runner. Mirrors run_tests.sh.
#
# Full package model: each renamed service dir under deploy/energy-stack/
# is a regular Python package (with __init__.py). deploy/energy-stack is
# on sys.path so each service is a top-level package (e.g.,
# `hvac_scheduler.app`). Mypy reads pyproject.toml from the repo root.
#
# Two target types are supported:
#   - service_dirs: service package names under deploy/energy-stack/
#     (underscore names, post-rename)
#   - repo_targets: paths relative to repo root (used for cockpit-backend
#     and any other enforced module living outside deploy/energy-stack/)
#
# Also runs import-linter to enforce adapter boundaries (see §5.5).
# Exits non-zero if any enforced check fails.
#
# Usage (requires bash; use Git Bash or WSL on Windows):
#   bash deploy/energy-stack/run_typecheck.sh

set -euo pipefail
STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$STACK_DIR/../.." && pwd)"

# Services in deploy/energy-stack/ currently in the enforced set
# (underscore package names, post-rename + __init__.py)
service_dirs=(
    # populated as services migrate in via per-service PRs
)

# Paths outside deploy/energy-stack/ in the enforced set
# (relative to REPO_ROOT)
repo_targets=(
    # populated as non-stack modules are added; e.g.,
    # tools/cockpit/backend  (added in PR 4 — cockpit enforcement)
)

failed=()

# Bootstrap mypy pass: enforces files listed in pyproject.toml `files`.
# PR 1 uses this for the two freshness modules. As each service migrates
# in, its freshness.py entry is REMOVED from `files` and the per-service
# invocation below covers it. The bootstrap pass is GUARDED so it only
# runs when the files list is non-empty — once the rollout completes
# and `files` is removed entirely, this guard skips the bootstrap and
# `run_typecheck.sh` does not fail on the empty-targets condition that
# mypy returns code 2 for.
if python -c "
import tomllib, sys
with open('$REPO_ROOT/pyproject.toml', 'rb') as f:
    cfg = tomllib.load(f)
files = cfg.get('tool', {}).get('mypy', {}).get('files', [])
sys.exit(0 if files else 1)
" 2>/dev/null; then
    echo "=== type-checking pyproject.toml files list ==="
    if ! (cd "$REPO_ROOT" && python -m mypy); then
        failed+=("pyproject-files")
    fi
fi

# Per-service enforced checks. Each service is a top-level package
# (hvac_scheduler, comed_poller, etc.) via the mypy_path entry in
# pyproject.toml. Invocation from repo root resolves cleanly.
for svc in "${service_dirs[@]}"; do
    echo
    echo "=== type-checking $svc ==="
    if ! (cd "$REPO_ROOT" && python -m mypy "deploy/energy-stack/$svc"); then
        failed+=("$svc")
    fi
done

# Repo-relative targets (cockpit-backend etc.). Invoked from repo root
# because their imports use absolute paths like `tools.cockpit.backend.X`.
for tgt in "${repo_targets[@]}"; do
    echo
    echo "=== type-checking $tgt ==="
    if ! (cd "$REPO_ROOT" && python -m mypy "$tgt"); then
        failed+=("$tgt")
    fi
done

# Import-linter (adapter-boundary enforcement). Runs whenever any
# enforced target exists. PYTHONPATH includes deploy/energy-stack so
# service packages resolve. The CLI is `lint-imports`, NOT
# `python -m importlinter` (the package has no __main__).
if [[ ${#service_dirs[@]} -gt 0 || ${#repo_targets[@]} -gt 0 ]]; then
    echo
    echo "=== checking import-linter contracts ==="
    if ! (cd "$REPO_ROOT" && PYTHONPATH="$STACK_DIR:${PYTHONPATH:-}" lint-imports --config pyproject.toml); then
        failed+=("import-linter")
    fi
fi

echo
if (( ${#failed[@]} > 0 )); then
    echo "TYPE-CHECK FAILED: ${failed[*]}"
    exit 1
fi
echo "OK"
```

**Lifecycle of the `files` list.** PR 1 enforces the two `freshness.py` modules via the `pyproject.toml` `files` list, because no service is yet in the enforced set. The bootstrap mypy pass is guarded by a tomllib check that reads the actual `files` list at runtime: it runs when `files` is non-empty, skips when empty. This handles the lifecycle cleanly:
- PR 1: `files = [scheduler/freshness.py, cockpit/freshness.py]`. Bootstrap runs.
- PR 2 (rename): same `files` (paths updated to underscore). Bootstrap runs.
- PR 3 (scheduler enforce): scheduler's freshness.py removed from `files`; `service_dirs` adds `hvac_scheduler`. Cockpit's freshness.py STAYS in `files`. Bootstrap still runs (files non-empty) — cockpit freshness still covered. Per-service runs scheduler. Both covered, no double-check.
- PR 4 (cockpit enforce): cockpit's freshness.py removed from `files`; `repo_targets` adds `tools/cockpit/backend`. `files` is now empty. Bootstrap skipped (files empty). Per-service runs scheduler + cockpit. Both covered.
- Phase 4 cleanup: `files` key removed entirely from pyproject.toml. Bootstrap stays skipped.

**Invocation pattern.** Mypy is invoked from repo root, with `deploy/energy-stack` on the mypy search path via `mypy_path` in pyproject.toml. Each service is a regular Python package (with `__init__.py`); imports are package-relative (`from .freshness import classify`) or absolute (`from hvac_scheduler.freshness import classify`). No special cwd handling. Import-linter is invoked via `lint-imports` (the actual CLI), not `python -m importlinter` (which doesn't exist).

### 4.3 Layer 3: CI workflow

A new `.github/workflows/typecheck.yml`. Triggers on push to any branch and PRs to main. Runs on GitHub-hosted Ubuntu runner (build-time tooling; no Pi-lab dependency).

```yaml
# Workflow name and job name are both "type-check" so the required
# status check appears in GitHub branch protection as the predictable
# string "type-check" (or "type-check / type-check" if GitHub adds the
# job-name suffix). Operator configures branch protection AFTER the
# first successful run, copying the exact emitted check name from the
# Actions UI to avoid configuring a name that doesn't match.
name: type-check

on:
  push:
    branches: ["**"]
  pull_request:
    branches: [main]

jobs:
  type-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"  # match service Dockerfiles (FROM python:3.13-slim)
      - name: Cache mypy
        uses: actions/cache@v4
        with:
          path: ~/.mypy_cache
          key: mypy-${{ runner.os }}-${{ hashFiles('pyproject.toml') }}
      - name: Install dev dependencies
        run: pip install -r deploy/energy-stack/requirements-dev.txt
      - name: Install all service requirements
        # Install every service's requirements.txt unconditionally so
        # mypy can resolve typed library symbols regardless of which
        # services are currently in the enforced set. Pip deduplicates
        # shared deps (most services pin influxdb-client==1.48.0).
        # Avoids the "add a service to enforced set, forget to add its
        # requirements to CI" failure mode.
        run: |
          for req in deploy/energy-stack/*/requirements.txt tools/cockpit/backend/requirements.txt; do
            if [[ -f "$req" ]]; then
              pip install -r "$req"
            fi
          done
      - name: Run type checks
        run: bash deploy/energy-stack/run_typecheck.sh
```

`requirements-dev.txt` (existing or new) pins `mypy>=1.10,<2.0`, `pydantic` (needed for the pydantic mypy plugin from PR 1, even before cockpit-backend migrates in), `import-linter`, and any `types-*` stub packages. Service-specific requirements are installed because mypy needs to import them to resolve types from typed libraries.

**Required-check enforcement.** Adding the workflow file does NOT block merges by itself; that only happens once the type-check job is declared a required status check in the GitHub repository's branch protection rules (or rulesets) for `main`. PR 1's acceptance criteria include configuring this: after the workflow first runs green, the operator opens the Actions UI, copies the EXACT emitted check name (current GitHub behavior emits `type-check / type-check` — the workflow-name / job-name pattern — but copy the actual string the UI shows, since GitHub's collapsing behavior has changed over time), and adds it to the required-checks list for `main`. Configuring the wrong name fails open (no enforcement) or fails closed (all merges blocked). Without this step the workflow runs advisorily and the entire point of the rollout is defeated.

### 4.4 File structure (end state)

```
pyproject.toml                                      # NEW (PR 1): mypy + import-linter config
deploy/energy-stack/
├── run_tests.sh                                    # gradually loses hyphens as services migrate
├── run_typecheck.sh                                # NEW (PR 1)
├── pytest.ini                                      # workaround docstring shrinks as services migrate
├── requirements-dev.txt                            # NEW or UPDATED: mypy, pydantic, import-linter
├── hvac_scheduler/                                 # renamed in PR 2 from hvac-scheduler/
│   ├── app.py                                      # entrypoint unchanged: still `python app.py`
│   ├── freshness.py                                # enforced after PR 1
│   ├── influx_adapter.py                           # NEW (PR 3 — first typed adapter)
│   ├── Dockerfile                                  # updated in PR 3: COPY line adds influx_adapter.py
│   ├── __init__.py                                 # NEW (PR 2): empty; makes hvac_scheduler a regular Python package
│   └── ...
├── comed_poller/                                   # renamed in its PR
└── ...
tools/cockpit/backend/                              # no rename needed
├── freshness.py                                    # enforced after PR 1
└── ...                                             # added to repo_targets in PR 3 (same PR as scheduler enforcement)
.github/workflows/
├── deploy.yml                                      # existing — unchanged
├── check-freshness-drift.yml                       # PATH FILTER UPDATED in PR 2 (hvac-scheduler → hvac_scheduler)
└── typecheck.yml                                   # NEW (PR 1); workflow + job both named `type-check`
docs/
├── type-debt-backlog.md                            # NEW (PR 1)
└── plans/
    └── type-checker-plan.md                        # NEW — unified implementation plan
```

Note: each service's `Dockerfile` is updated during its per-service PR — WORKDIR is set so the service package is on `sys.path`, and CMD becomes `python -m <service>.app`. Every service gets an `__init__.py` (full package model). The migration template (§6.1) makes this explicit.

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

**The adapter boundary is enforced, not conventional.** See §5.5 for the enforcement mechanism.

**Pre-PR-2 verification on influxdb-client stub status.** Before PR 2's adapter is designed in detail, PR 1 verifies whether `influxdb_client==1.48.0` ships its own type stubs (via `py.typed` marker). If it does, the `ignore_missing_imports = true` override has no effect on the library's typed surface and the adapter design rationale shifts from "the library is untyped" to "we want to own a narrower projection." The adapter pattern remains valid in both cases (it isolates our usage from upstream API drift) but the rationale and the override block change. PR 1's acceptance criteria document the finding in `type-debt-backlog.md`.

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
- Run `mypy --install-types --non-interactive` during per-service migration to discover other available stub packages.
- Add discovered stubs to `requirements-dev.txt`.

**Libraries that ship typed stubs natively (`py.typed` marker; no override or adapter needed):**
- `fastapi`, `httpx`, `pydantic`, `uvicorn`, `python-dotenv` — used by cockpit-backend.
- `aiohttp` (>= 3.8) — used by `nws-poller`, `pjm-dm2-poller`, `telegram-notifier`. The currently-pinned `aiohttp==3.13.4` ships full type stubs. Migrating these services means mypy WILL enforce aiohttp usage against actual types, surfacing genuine usage findings rather than silently treating it as `Any`. This is a feature, not a bug, but should be expected in those service PRs.
- `influxdb_client` — needs verification (see §5.2). PR 1's acceptance includes documenting whether the installed version (`1.48.0`) ships `py.typed`. If it does, the `ignore_missing_imports` override block in `pyproject.toml` is removed and we use the upstream stubs directly. The adapter pattern in §5.2 is still valuable as a projection seam.

### 5.5 Adapter boundary enforcement (import-linter)

For libraries we wrap with typed adapters, "do not import the library directly outside the adapter module" is an enforced rule, not a convention. Enforced via `import-linter` (PyPI package, runs from the same `pyproject.toml`).

Each adapter ships with a corresponding `[[tool.importlinter.contracts]]` block in `pyproject.toml`. Example for the influxdb-client adapter (lands in PR 2):

```toml
[tool.importlinter]
root_packages = ["hvac_scheduler"]

[[tool.importlinter.contracts]]
name = "Only influx_adapter may import influxdb_client"
type = "forbidden"
source_modules = ["hvac_scheduler"]
forbidden_modules = ["influxdb_client"]
ignore_imports = [
    "hvac_scheduler.influx_adapter -> influxdb_client",
]
```

`run_typecheck.sh` invokes `python -m importlinter` after the mypy passes whenever any per-service target exists. A direct `from influxdb_client import ...` anywhere outside `influx_adapter.py` fails the check and blocks the PR. The rule applies even if mypy would have been happy with the import (since `ignore_missing_imports` makes the library invisible to mypy).

As subsequent adapters are added (e.g., `pyControl4` adapter in a later service PR), each adapter PR adds its own contract block. `root_packages` grows with each new service in the enforced set.

**Why enforce, not convention.** The whole project rationale is "enforcement, not hints." A convention is a hint by another name. A future contributor adding a direct `record.get_value()` call would silently undermine the adapter, recreating the exact bug-shape the adapter exists to prevent. Enforced boundaries close that loop.

### 5.6 Test-file relaxation pattern

Tests are in the enforced set, but with a targeted `[[tool.mypy.overrides]]` block on `test_*.py` and `conftest.py` files (shown in §4.1):

```toml
[[tool.mypy.overrides]]
module = ["*.test_*", "*.conftest"]
disallow_untyped_defs = false
disallow_untyped_decorators = false
```

**What stays strict in tests:**
- `disallow_any_generics` — catches `List` instead of `List[Foo]` in test signatures
- `no_implicit_optional` — catches `def foo(x: str = None)` patterns
- `warn_redundant_casts` — catches dead `cast(...)` calls
- `warn_return_any` — catches functions that accidentally return Any
- `strict_equality` — catches comparisons of unrelated types

**What's relaxed in tests:**
- `disallow_untyped_defs` — test functions don't require return-type annotations (they're all `-> None` anyway)
- `disallow_untyped_decorators` — mock/parametrize decorators often have less-than-perfect types

The specific error-code set is tuned during PR 2 against the scheduler's actual test suite. If the relaxed set still produces high-volume bookkeeping noise, the tuning continues. If it produces too few findings to be useful, the strictness ratchets up. Decision driven by data, not guesses.

## §6. Per-service migration template

Every service-rollout PR (PR 2 onwards) follows this template verbatim. Documented in `docs/type-debt-backlog.md` or alongside the unified plan.

### 6.1 Steps

1. **Rename:** `git mv deploy/energy-stack/<service>/ deploy/energy-stack/<service_underscored>/`
2. **Add `__init__.py`** (empty file) in the renamed directory. The dir is now a regular Python package.
3. **Convert sibling imports to package-relative form.** Inside the service directory, `sed` cross-file imports from script-style to relative form:
   - `from freshness import X` → `from .freshness import X`
   - `from price_overlay import X` → `from .price_overlay import X`
   - `import freshness` → `from . import freshness`
   - Imports of standard library or external libraries (e.g., `from datetime import datetime`, `from influxdb_client import InfluxDBClient`) are unaffected.
   - Test files (`test_*.py`, `conftest.py`) inside the service follow the same pattern.
   - Verify the conversion with a quick grep: `grep -rn "^from [a-z_]* import\|^import [a-z_]*$" deploy/energy-stack/<service_underscored>/` — bare top-level imports of project modules (those that match other files in the service) need to be relative form.
4. **Update docker-compose:** edit `deploy/energy-stack/docker-compose.yml`:
   - Change the service's `build.context:` path to the new underscore name.
   - **DO NOT** change the service name (the YAML key, e.g., `hvac-scheduler:` stays hyphenated).
5. **Update Dockerfile:**
   - WORKDIR is set to `/app` (or similar parent of the service package); the COPY places the service package at `/app/<service_underscored>/`.
   - `ENV PYTHONPATH=/app` (or whichever parent makes the service package importable).
   - CMD is `["python", "-m", "<service_underscored>.app"]` (or `.poller` for poller services). Replaces the existing `python app.py` invocation.
   - If the Dockerfile has explicit `COPY <file>.py` lines listing source files (not just `COPY . .`), update them to include any new typed-adapter modules being added in this PR.
   - Verify with `docker compose build <service-name>` and `docker compose run --rm <service-name>` locally if possible — confirms the package import path resolves at runtime.
6. **Update `run_tests.sh`:** in the `services=(...)` array near the top of the script, change this service's entry from hyphen to underscore.
7. **Update `run_typecheck.sh`:** add the service to `service_dirs` (underscore name). For cockpit-backend specifically, add `tools/cockpit/backend` to `repo_targets` instead.
8. **Update `pyproject.toml`:**
   - Remove the service's individual `freshness.py` entry from the `files = [...]` list (the per-service mypy invocation now covers the whole directory; no double-checking).
   - Add this service's test module names to the test-relaxation `[[tool.mypy.overrides]]` block. Use full dotted forms: `<service>.test_<service>`, `<service>.conftest`, etc. The block's `module` list grows per service.
   - If this PR adds an adapter, append the adapter's `[[tool.importlinter.contracts]]` block. Add the service's package name to `[tool.importlinter] root_packages`.
9. **Update CI workflow path triggers:** any existing workflow that hardcodes the old hyphenated directory path (most notably `.github/workflows/check-freshness-drift.yml`) must be updated to the underscore path. Otherwise the existing safety gate silently stops triggering. PR 2 (scheduler) specifically must update `check-freshness-drift.yml`.
10. **Update path references:** sed markdown and shell scripts that reference `./<service>/` (the filesystem path) to use the underscore. Operational identifiers — the docker-compose service name, Grafana JSON references, Influx tag values, Telegraf labels, log labels — STAY hyphenated.
11. **Local type-check:** `bash deploy/energy-stack/run_typecheck.sh` — iterate until clean.
12. **Triage each finding:**
    - Real bug → fix in the same PR (treat as bug-fix value).
    - Missing/incorrect annotation → fix the annotation.
    - Untyped library call → add to `[[tool.mypy.overrides]]` in `pyproject.toml` and consider adapter wrapping if surface is high-value.
13. **Tests pass:** `cd deploy/energy-stack/<service_underscored>/ && python -m pytest .` — verifies the rename didn't break test collection. Test imports may need to be updated to match the new package layout (e.g., `from <service> import freshness` in tests).
14. **Container runs:** if possible, `docker compose up -d <service-name>` locally — confirms the new entrypoint works. Otherwise, verified by post-merge deploy.yml run on Pi-lab.
15. **Rename verification (two greps):**
    - **Operational identifier preserved** (positive grep): `grep -rn "<service-hyphenated-name>" tools/cockpit deploy/energy-stack/grafana deploy/energy-stack/telegraf deploy/energy-stack/docker-compose.yml deploy/energy-stack/promtail` — confirms the hyphenated identifier still appears in the operational layer. Unexpected absences = restore.
    - **No stale filesystem paths remain** (negative grep): `git grep -n "deploy/energy-stack/<service-hyphenated-name>/" -- ':!*.json' ':!docs/archive/**'` — should return ZERO matches outside of historical archive docs. Any non-archive match is a stale filesystem path that needs updating to the underscore form.
16. **Dual-review** per AGENTS.md: superpowers:requesting-code-review + codex adversarial-review in parallel.
17. **Open PR** with `--base main`. Per AGENTS.md, no stacking; wait for prior PR to merge before opening the next service PR.
18. **Operator merges** in GitHub UI. The deploy.yml workflow handles the rest (since the rename touches `deploy/**`, it triggers redeploy of the renamed service).

### 6.2 Expected outcome per service PR

- Net file changes per service: ~20-60 (rename moves, `__init__.py` add, every cross-file import updated to relative form, `pyproject.toml` edits, Dockerfile WORKDIR/CMD update, compose context update, run-script edits, doc reference updates, plus any bug-fix changes). Full package conversion; the cost is real but mechanical.
- Test count: unchanged (rename doesn't add or remove tests; type-fixes may add anti-regression tests if real bugs are found).
- Net mypy errors after PR: zero on this service (greenfield discipline).
- Operational impact: zero downtime; deploy.yml rebuilds the service in-place.

### 6.3 Order of services

Canonical PR numbering under the default plan (Phase 2 split):

- **PR 1** — Infrastructure (mypy config, run_typecheck.sh, CI workflow, type-debt backlog).
- **PR 2** — Phase 2a: scheduler rename + infrastructure-only (no enforcement yet).
- **PR 3** — Phase 2b: scheduler enforced + first typed adapter. The bootstrap-pass guard in `run_typecheck.sh` keeps cockpit-backend's `freshness.py` covered via the `files` list until PR 4 takes over — no coverage gap.
- **PR 4** — Cockpit-backend enforcement + annotation + bug fixes (specifically the `price_is_stale` consumer fix deferred from the freshness PR). Adds `tools/cockpit/backend` to `repo_targets`; removes the cockpit freshness.py entry from `files`.
- **PR 5** — `comed-poller` (data pipeline; freshness-bug-adjacent).
- **PR 6** — `pjm-dm2-poller` (DM2 / 5CP risk inputs).
- **PR 7** — `hvac-scheduler-watchdog` (depends on scheduler module shape).
- **PR 8-12** — Remaining pollers + ingest services in any order: `nws-poller`, `eagle-poller`, `ecowitt-ingest`, `haven-ingest`, `refoss-poller`, `thermostat-poller`.
- **PR 13** — `telegram-notifier` (simplest code; serves as final-template demonstration).
- **PR 14** — Cleanup (Phase 4: remove `pytest.ini` workaround docstring, mark spec shipped).

This order can be revised based on what bugs surface in early PRs.

**Note for the cockpit-backend PR (PR 4):** the §6.1 template applies in spirit but several steps are skipped because `tools/cockpit/backend/` is already a valid Python path (and already has `__init__.py`):
- Step 1 (rename) — skip.
- Step 2 (`__init__.py`) — already exists.
- Step 3 (import conversion) — cockpit-backend already uses absolute imports under `tools.cockpit.backend.*`. Verify they all work under mypy; any remaining sibling-style imports need conversion to relative form.
- Step 4 (docker-compose) — cockpit-backend isn't in docker-compose (runs locally for operator monitoring); no compose change.
- Step 5 (Dockerfile) — n/a.
- Step 6 (`run_tests.sh`) — cockpit-backend isn't in `run_tests.sh`; its tests live under `tools/cockpit/backend/tests/` and run via the cockpit's own pytest invocation. Document in the PR.
- Step 7 (`run_typecheck.sh`) — add `tools/cockpit/backend` to `repo_targets`.
- Step 8 (`pyproject.toml`) — remove `tools/cockpit/backend/freshness.py` from `files`; add cockpit test module names to test-relaxation block.
- Step 9 (CI workflow path) — `check-freshness-drift.yml` already handles cockpit-backend correctly (no rename); no change needed.
- Step 10 (path references) — minimal; cockpit-backend's path was never hyphenated.
- Step 14 (container runs) — n/a; cockpit-backend runs locally, not in compose.
- Other steps (mypy iteration, bug fixes including the deferred `price_is_stale` consumer fix, dual-review) apply as written.

## §7. Phased rollout

### Phase 1: Infrastructure + freshness modules (PR 1)

**Deliverables:**
- `pyproject.toml` at repo root with mypy config (Python 3.13, strict, pydantic plugin, overrides for `influxdb_client.*` and `pyControl4.*`, test-file relaxation block), and an `[tool.importlinter]` skeleton ready for adapter contracts.
- `deploy/energy-stack/run_typecheck.sh` (empty `service_dirs` and `repo_targets`; freshness modules enforced via pyproject.toml `files` list during this phase only).
- `.github/workflows/typecheck.yml` (runs `run_typecheck.sh` on every push/PR; Python 3.13 to match service Dockerfiles).
- `docs/type-debt-backlog.md` (seeded with influxdb_client and pyControl4 as known-priority adapter candidates).
- `deploy/energy-stack/requirements-dev.txt` updated with `mypy>=1.10,<2.0`, `pydantic`, `import-linter`, and `types-requests`.
- `STALE_DATA_HANDOFF.md`-class documentation: brief note in `AGENTS.md` (or referenced from there) describing the per-service migration template location.

**Acceptance criteria:**
- `bash deploy/energy-stack/run_typecheck.sh` passes locally and in CI on a clean branch.
- Deliberately introducing a type error in `freshness.py` causes CI to fail. (Verified via a temporary commit + revert.)
- The two `freshness.py` modules (scheduler + cockpit) type-check clean under `mypy --strict`.
- **GitHub branch protection / ruleset update:** after the workflow first runs green, operator opens the Actions UI, copies the EXACT emitted check name (current GitHub behavior emits `type-check / type-check`), and adds it to the list of required status checks for `main`. Configuring a wrong name silently fails open (no enforcement). Without this step the workflow runs advisorily and the rollout's enforcement claim is false. PR 1 status remains "shipped (pending required-check enable)" until verified.
- **influxdb-client stub-status verification:** `python -c "import influxdb_client; print(influxdb_client.__file__)"` plus inspecting whether the package ships `py.typed`. Finding documented in `docs/type-debt-backlog.md` so PR 2's adapter design starts from facts, not assumption.
- **`types-*` discovery:** run `mypy --install-types --non-interactive` against the installed deps. **Review each suggested stub package BEFORE adding to `requirements-dev.txt`:** only add stubs for libraries that do NOT already ship `py.typed` natively. If a stub package is suggested for a library we have `ignore_missing_imports = true` for, prefer removing the override (and using the stubs) over installing AND keeping the ignore — having both can cause stub/override conflicts. Document each kept/dropped stub in the type-debt backlog.
- **Pydantic plugin loads:** mypy starts without error against the actual installed pydantic version (`pydantic>=2.5`).

### Phase 2: Scheduler — split into two PRs by default

The scheduler is the largest service and the highest-density type-hint surface (~350 annotated lines, ~3300 total scheduler lines, 300+ test functions, 500+ Mock references). Splitting Phase 2 into PR 2a (rename + infrastructure-only) and PR 2b (enforcement + adapter + type-fixes) is the DEFAULT plan. Combining them into a single PR is allowed only if PR 2a's actual diff is tiny AND the post-rename `mypy --strict` run on the scheduler surfaces under ~20 findings — both checked AFTER PR 2a's work is done, not predicted in advance.

#### Phase 2a: Scheduler rename + package conversion (PR 2)

**Deliverables:**
- Rename `deploy/energy-stack/hvac-scheduler/` → `hvac_scheduler/` via `git mv`.
- Add empty `__init__.py` to the renamed dir.
- Convert all sibling imports in the scheduler from script-style to relative form (`from freshness import X` → `from .freshness import X` etc.). Includes test files and `conftest.py`.
- Update docker-compose `build.context:` for the scheduler service. Keep service name `hvac-scheduler` (hyphenated).
- Update `hvac_scheduler/Dockerfile`: WORKDIR `/app`, `ENV PYTHONPATH=/app`, CMD `["python", "-m", "hvac_scheduler.app"]`. COPY places the package at `/app/hvac_scheduler/`.
- Update `run_tests.sh` services array (hyphen → underscore).
- Update `.github/workflows/check-freshness-drift.yml` path filter from `hvac-scheduler/freshness.py` to `hvac_scheduler/freshness.py`.
- Update path references in markdown/shell scripts pointing to `./hvac-scheduler/`.
- Update the `pyproject.toml` `files` list entry path: `hvac-scheduler/freshness.py` → `hvac_scheduler/freshness.py`.
- `run_typecheck.sh` still has empty `service_dirs` — scheduler NOT yet in enforced set.

**Acceptance criteria:**
- All scheduler tests pass via `cd deploy/energy-stack/hvac_scheduler/ && python -m pytest .`
- `bash deploy/energy-stack/run_tests.sh` succeeds.
- `bash deploy/energy-stack/run_typecheck.sh` still passes (freshness.py path-updated entry still type-checks).
- `docker compose up -d hvac-scheduler` succeeds locally (or verified by post-merge deploy.yml run on Pi-lab) — verifies `python -m hvac_scheduler.app` entrypoint works.
- Both verification greps in §6.1 step 15 pass.

#### Phase 2b: Scheduler enforced + adapter (PR 3)

**Deliverables:**
- First typed adapter: `deploy/energy-stack/hvac_scheduler/influx_adapter.py` wrapping `influxdb_client` record API.
- Refactor `fetch_latest_comed` and any other callers of `record.get_*()` to use the typed adapter.
- Add `hvac_scheduler` to `service_dirs` in `run_typecheck.sh`.
- Remove `deploy/energy-stack/hvac_scheduler/freshness.py` from `pyproject.toml` `files` list (per-service mypy invocation covers it). Cockpit's freshness.py STAYS in `files` until PR 4 — the bootstrap-guard mechanism keeps it covered.
- Add scheduler's test module names to the test-relaxation `[[tool.mypy.overrides]]` block.
- Update `[tool.importlinter]` `root_packages` to include `"hvac_scheduler"`. Add the influxdb_client `forbidden` contract block.
- Update `hvac_scheduler/Dockerfile` `COPY` lines to include `influx_adapter.py`.
- Triage all `mypy --strict` findings on the scheduler.
- Fix real bugs; annotate missing types; document any necessary ignores.

**Acceptance criteria:**
- `bash deploy/energy-stack/run_typecheck.sh` returns 0 with `service_dirs=(hvac_scheduler)`, `repo_targets=()`, and cockpit's `freshness.py` still in `files`. Both the bootstrap pass (covers cockpit freshness) and the scheduler per-service pass succeed.
- `lint-imports --config pyproject.toml` (via `run_typecheck.sh`) returns zero violations against the scheduler's `forbidden` contract for `influxdb_client`.
- All scheduler tests pass.
- The `query_api` parameter is typed across the 10+ scheduler functions where it currently lacks an annotation. (Most direct descendent of the 19:18Z bug class.)
- Real bugs found are fixed in-PR with anti-regression tests where applicable. Each "real bug" entry in the PR description includes the file:line and a one-line description.

### Phase 3: Remaining services (PR 4 — cockpit-backend; PR 5-13 — pollers, watchdog, telegram-notifier)

Each remaining service migrates per the §6 template. PR 4 is cockpit-backend (enforcement + annotation + the `price_is_stale` consumer fix). PR 5 is `comed-poller`. PR 6-13 follow per §6.3 ordering. One PR per service. No stacking; wait for prior PR to merge before opening the next.

**Acceptance criteria per PR:**
- That service is in the enforced set in `run_typecheck.sh` (added to `service_dirs` or `repo_targets`).
- `mypy --strict` returns zero errors for that service.
- `lint-imports` returns zero violations.
- Service tests pass.
- Container builds and runs with new `python -m <service>.app` entrypoint.
- Service deploys successfully (verified post-merge via deploy.yml).
- Any real bugs found are fixed in-PR.
- Both verification greps in §6.1 step 15 pass.

### Phase 4: Cleanup (PR 14, after all services enforced)

**Deliverables:**
- Remove the `files` key entirely from `pyproject.toml` `[tool.mypy]` (all services are now covered by per-service invocations).
- The bootstrap-pass guard in `run_typecheck.sh` (tomllib check) will then skip the bootstrap pass entirely — no `python -m mypy` call with empty targets.
- Simplify `pytest.ini` docstring to remove the deferred-rename note (rename is complete).
- Mark spec status `shipped`.
- Verify the type-check workflow is still a required status check on `main` (exact check name as configured in PR 1).

## §8. Risks & mitigations

### 8.1 Risk: scheduler type-check surface is large; many findings

The scheduler is ~350+ annotated lines and many more unannotated. Strict mode may surface dozens of findings.

**Mitigation:** triage explicitly. Each finding is categorized as bug, missing-annotation, or library-edge. Bugs are fixed in-PR. Annotations are added inline. Library edges go to overrides or adapter backlog. If scheduler PR balloons beyond reasonable review size, split into "rename + infrastructure" and "type-fixes per file region" PRs — but maintain greenfield discipline (the service is not in the enforced set until its full directory passes).

### 8.2 Risk: external library behavior changes when upgrading mypy

Mypy releases regularly; new versions surface new findings. CI could go red on dependency updates unrelated to our code changes.

**Mitigation:** pin `mypy` version in `requirements-dev.txt`. Upgrades are deliberate PRs that triage new findings. Pin to `mypy>=1.10,<2.0` for the major version we land on.

### 8.3 Risk: hyphenated-dir collisions during partial migration

Until all services are renamed, some have underscore names and some hyphen. `run_tests.sh` and `run_typecheck.sh` both need to know which is which.

**Mitigation:** scripts are explicit. The `service_dirs` (underscore-named, in enforced set) and `repo_targets` (cockpit-backend etc.) arrays in `run_typecheck.sh` enumerate exactly what is currently enforced. Services not in those arrays are not enforced, which is visible by inspection. `run_tests.sh` updates its services array as each service migrates. No script needs to handle "either name might exist."

### 8.4 Risk: docker-compose service-name vs directory-name confusion

A maintainer might rename both, breaking historical Influx tag continuity and Grafana queries.

**Mitigation:** the migration template explicitly preserves the service identifier. Spec §6.1 step 3 calls it out. Service PRs include a verification step: `grep -rn "<service>" tools/cockpit deploy/energy-stack/grafana deploy/energy-stack/telegraf` should confirm operational references to the hyphenated identifier are unchanged.

### 8.5 Risk: pydantic plugin compatibility

`pydantic.mypy` may have version requirements. Cockpit-backend uses `pydantic>=2.5`. Pydantic v2's mypy plugin works differently than v1's.

**Mitigation:** PR 1 verifies pydantic plugin loads correctly against the actual installed pydantic version. Pin if necessary. If plugin behavior is surprising, document the relevant config in `pyproject.toml` and reference it from the spec.

### 8.6 Risk: test files in scope produce excessive ignore noise

Mock-heavy tests would need many `# type: ignore[misc]` annotations under full strict mode.

**Mitigation:** test-file relaxation pattern (§5.6) is enabled from PR 1. Tests keep strict checks that catch real test bugs (`disallow_any_generics`, `no_implicit_optional`, `warn_redundant_casts`, `warn_return_any`, `strict_equality`); annotation-completeness checks (`disallow_untyped_defs`, `disallow_untyped_decorators`) are relaxed. Specific error-code set tuned in PR 2b against the scheduler's actual test suite — not preemptively guessed.

### 8.7 Risk: scope creep during scheduler PR (the largest service)

Scheduler is the highest-density type-hint surface and the largest single service. Combined PR (rename + enforcement + adapter + bug fixes) is genuinely likely to balloon.

**Mitigation: the PR 2 / PR 3 split (Phase 2a / Phase 2b) is the DEFAULT plan, not a contingency.** See §7 Phase 2. Combining into a single PR is allowed only if PR 2's diff is tiny AND post-rename mypy surfaces under ~20 findings; both checked after PR 2's work, not predicted. Subagent-driven-development pattern from the freshness PR is the model: small task-level commits within each PR; phase checkpoints with dual-review.

If PR 3 itself balloons (say, >500 lines of net type-fix changes), a further split is acceptable:
- PR 3a: adapter + boundary contract + refactor `fetch_latest_comed` + add cockpit-backend to repo_targets
- PR 3b: remaining type-fixes by file region

The greenfield invariant holds: a service is not in `service_dirs` (or `repo_targets`) until its full directory passes `mypy --strict` AND `import-linter` passes.

### 8.8 Risk: missing types-* PyPI packages

Some libraries we use may have stubs published as separate `types-*` packages that we don't know about. Without them, mypy treats the library as untyped even when stubs exist.

**Mitigation:** during per-service migration, run `mypy --install-types --non-interactive` to suggest available stub packages. Install any relevant ones into `requirements-dev.txt`. Document in the type-debt backlog.

## §9. Acceptance gates

### 9.1 Per-PR gate (each service PR)

- `mypy --strict` returns 0 errors on the service.
- `python -m importlinter` returns 0 violations.
- Service tests pass.
- Dual-review run; findings addressed or explicitly deferred with rationale.
- Deploy.yml succeeds post-merge (verified by GitHub Actions run).
- Operational identifier (compose service name) unchanged — verified by `grep` for the hyphenated name still appearing in compose, grafana JSON, telegraf, log labels.

### 9.2 Project-level gate (when "all services enforced")

- `run_typecheck.sh` returns 0 with all 11 services + cockpit-backend in `service_dirs` or `repo_targets`.
- `pyproject.toml` `files` key removed entirely (all enforced via per-service invocation; bootstrap pass guard skips when no `files` key exists).
- type-check workflow is a required status check on `main` in GitHub branch protection (exact check name as configured in PR 1).
- `pytest.ini` workaround docstring removed.
- Spec status updated to `shipped`.

### 9.3 Pre-OSF gate (minimum required before filing)

- Scheduler is in `service_dirs` (`mypy --strict` + `import-linter` both pass).
- Cockpit-backend is in `repo_targets` (added in PR 3 alongside scheduler enforcement).
- PR 4 (cockpit-backend annotation + `price_is_stale` consumer fix) has merged.
- `type-check` is a required status check on `main` (exact check name verified against the Actions UI emission).
- All findings from those two services are either fixed or documented as accepted (with rationale in the PR).

Other services may complete post-OSF.

## §10. Status & history

| Date | Status | Note |
|---|---|---|
| 2026-05-20 | draft | Initial spec. Captures decisions from brainstorming session 2026-05-20: scope (C — all scheduler-stack services + cockpit-backend, scripts/ deferred); tool (mypy --strict); rollout strategy (per-service greenfield, infrastructure-PR-first); hyphenated-dir resolution (rename per service while preserving operational identifier); external library strategy (option B — module-level ignore + targeted typed adapters tracked as backlog); pydantic plugin enabled; tests included with strict; PR 1 bootstrap with the two freshness modules. Open for first-pass dual-review (superpowers:requesting-code-review + codex adversarial-review). |
| 2026-05-20 | draft (rev 2) | First-pass dual-review findings applied. Critical fixes: Python 3.13 (matches service Dockerfiles, not 3.11); `run_typecheck.sh` rewritten to handle cockpit-backend `repo_targets`, prevent dual-invocation of `files` list, and run import-linter; CI workflow installs ALL service requirements via loop (not hardcoded list); `.github/workflows/check-freshness-drift.yml` path-filter update added to migration template. Design decisions: package model is "hybrid" (rename + `__init__.py` for tooling; script entrypoints stay); adapter boundaries enforced via `import-linter` (not deferred to backlog); tests get targeted relaxation of annotation-completeness checks (specific codes tuned in PR 2b against scheduler suite). Phase 2 split into PR 2a (rename) + PR 2b (enforcement) as DEFAULT plan, not contingency. Pre-OSF gate now requires `Type Check` to be a GitHub-required status check, not just a workflow file. Influxdb-client stub-status verification + `mypy --install-types` discovery added to PR 1 acceptance. Aiohttp note added (ships typed stubs; surfaces in nws/pjm/telegram service PRs). Open for second-pass dual-review. |
| 2026-05-20 | draft (rev 3) | Second-pass dual-review findings applied. Critical fixes: (1) cockpit coverage gap closed by bundling `tools/cockpit/backend` into `repo_targets` in PR 3 (Phase 2b) — same PR that adds scheduler to `service_dirs`. The cockpit-specific PR (now PR 4) is annotation + bug-fixes only. (2) Package model corrected from "hybrid" to "namespace packages, no `__init__.py`" — adding `__init__.py` while keeping script-style sibling imports created an internal contradiction (mypy would resolve `from freshness import X` as `hvac_scheduler.freshness`, breaking). Now: rename for tool-friendly identifier, no `__init__.py`, namespace packages, mypy/import-linter invoked from within service dir for correct `sys.path` behavior. (3) `run_typecheck.sh` invokes mypy from inside service dir with `--config-file`; import-linter invoked from `deploy/energy-stack/` with `PYTHONPATH` set. (4) `[tool.importlinter]` gets `include_external_packages = true` (required for `forbidden` contracts on third-party modules). (5) CI workflow/job both named `type-check` for predictable required-check string; operator copies the EXACT emitted name from Actions UI when configuring branch protection. (6) `--install-types` review guidance tightened (review each suggested stub before keeping). (7) §6.1 step 13 gains a NEGATIVE grep for stale filesystem paths in addition to the positive grep for operational identifiers. (8) PR numbering normalized across §4.4, §6.3, §7 (PR 1 = infra, PR 2 = scheduler rename, PR 3 = scheduler enforce + cockpit enforce, PR 4 = cockpit annotation, PR 5-13 = remaining services, PR 14 = cleanup). (9) Dropped `not_yet_enforced` array from `run_typecheck.sh` (silent-rot risk; tracking moved to plan doc). Open for third-pass dual-review if any remaining gaps surface; otherwise ready for plan/execute. |
| 2026-05-20 | draft (rev 4) | Third-pass dual-review findings applied. **Major design pivot:** the rev 3 namespace-package model is incompatible with import-linter (grimp raises `NamespacePackageEncountered` for PEP 420 root packages). After three rounds of trying to minimize per-service blast radius via clever invocation patterns, the operator's call: "stop trying to work around things — what's the right way to do it." Switching to FULL PACKAGE MODEL: every renamed service gets `__init__.py`, every cross-file sibling import is converted from script-style (`from freshness import X`) to relative form (`from .freshness import X`), every Dockerfile WORKDIR + CMD is updated to use `python -m <service>.app` invocation. Per-service blast radius is larger but all tooling now works natively (mypy + import-linter + pytest, no special invocation paths). Other rev-4 fixes: (a) bootstrap mypy pass guarded by tomllib check that reads actual `files` list — skips when empty, preventing Phase 4 CI breakage; (b) test-relaxation override changed from invalid `*.test_*` glob to literal per-service module names; (c) `python -m importlinter` corrected to `lint-imports` (actual CLI); (d) cockpit-backend mypy invocation moved to repo root (preserves `tools.cockpit.backend.*` absolute imports); (e) PR 3/4 lifecycle reverted to cleaner shape (PR 3 = scheduler only, PR 4 = cockpit) — bootstrap guard prevents coverage gap; (f) remaining stale `Type Check` references updated to `type-check`. Ready for plan/execute. |
