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
plugins = ["pydantic.mypy"]

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
# in mock-heavy fixtures. Specific error-code set tuned in PR 2 based
# on what fires against the scheduler's test suite — not preemptively.
[[tool.mypy.overrides]]
module = ["*.test_*", "*.conftest"]
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
# in PR 2 with the influx_adapter.
[tool.importlinter]
root_packages = []
# Populated per-service as adapters are added. Example after PR 2:
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
# Per-service mypy runner. Mirrors run_tests.sh.
#
# Each enforced service is type-checked in its own mypy invocation.
# This works around the hyphenated-directory issue (until each service
# is renamed and added to the enforced set) without requiring all
# services to migrate simultaneously.
#
# Two target types are supported:
#   - service_dirs: services under deploy/energy-stack/ (path is
#     "$STACK_DIR/$svc")
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
# (use underscore directory names, post-rename)
service_dirs=(
    # populated as services migrate in via per-service PRs
)

# Paths outside deploy/energy-stack/ in the enforced set
# (relative to REPO_ROOT)
repo_targets=(
    # populated as non-stack modules are added; e.g.,
    # tools/cockpit/backend  (after PR 3)
)

# Services not yet migrated — listed for visibility. The array is
# documentation only; it is not iterated. Each entry should appear in
# exactly one of: service_dirs (after rename + enforce), or
# not_yet_enforced. Never both.
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

# PR 1 transition state: enforce the two freshness modules via the
# pyproject.toml files list. Each service-rollout PR removes its
# freshness file entry from that list (the per-service invocation
# below covers it).
if [[ ${#service_dirs[@]} -eq 0 && ${#repo_targets[@]} -eq 0 ]]; then
    echo "=== type-checking pyproject.toml files list (PR 1 bootstrap) ==="
    if ! (cd "$REPO_ROOT" && python -m mypy); then
        failed+=("pyproject-files")
    fi
fi

# Per-service enforced checks (uses underscore-named dirs)
for svc in "${service_dirs[@]}"; do
    echo
    echo "=== type-checking $svc ==="
    if ! (cd "$REPO_ROOT" && python -m mypy "$STACK_DIR/$svc"); then
        failed+=("$svc")
    fi
done

# Repo-relative targets (cockpit-backend etc.)
for tgt in "${repo_targets[@]}"; do
    echo
    echo "=== type-checking $tgt ==="
    if ! (cd "$REPO_ROOT" && python -m mypy "$tgt"); then
        failed+=("$tgt")
    fi
done

# Import-linter (adapter-boundary enforcement). Runs whenever any
# enforced target exists, since contracts target enforced-service code.
if [[ ${#service_dirs[@]} -gt 0 || ${#repo_targets[@]} -gt 0 ]]; then
    echo
    echo "=== checking import-linter contracts ==="
    if ! (cd "$REPO_ROOT" && python -m importlinter); then
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

**Lifecycle of the `files` list:** PR 1 enforces the two `freshness.py` modules via the `pyproject.toml` `files` list, because no service is yet in the enforced set. As each service migrates in via its per-service PR, that service's individual file entries are removed from `files` and added to `service_dirs` (or `repo_targets` for cockpit-backend). The per-service mypy invocation then covers the whole renamed directory. Each file gets checked exactly once.

**Once `service_dirs` and `repo_targets` are both empty AND the `files` list contains entries** (PR 1 only), only the bootstrap path runs. Once any per-service target exists, the bootstrap path is skipped (preventing double-invocation).

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

**Required-check enforcement.** Adding the workflow file does NOT block merges by itself; that only happens once `Type Check` is declared a required status check in the GitHub repository's branch protection rules (or rulesets) for `main`. PR 1's acceptance criteria include configuring this: the operator adds `Type Check` to the required-checks list for `main` immediately after the workflow first runs green. Without that step the workflow runs advisorily and the entire point of the rollout is defeated.

### 4.4 File structure (end state)

```
pyproject.toml                                      # NEW (PR 1): mypy + import-linter config
deploy/energy-stack/
├── run_tests.sh                                    # gradually loses hyphens as services migrate
├── run_typecheck.sh                                # NEW (PR 1)
├── pytest.ini                                      # workaround docstring shrinks as services migrate
├── requirements-dev.txt                            # NEW or UPDATED: mypy, pydantic, import-linter
├── hvac_scheduler/                                 # renamed in PR 2 from hvac-scheduler/
│   ├── __init__.py                                 # NEW (PR 2): empty; tooling marker only
│   ├── app.py                                      # entrypoint unchanged: still `python app.py`
│   ├── freshness.py                                # enforced after PR 1
│   ├── influx_adapter.py                           # NEW (PR 2 — first typed adapter)
│   ├── Dockerfile                                  # updated in PR 2: COPY line adds influx_adapter.py
│   └── ...
├── comed_poller/                                   # renamed in its PR
└── ...
tools/cockpit/backend/                              # no rename needed
├── freshness.py                                    # enforced after PR 1
└── ...
.github/workflows/
├── deploy.yml                                      # existing — unchanged
├── check-freshness-drift.yml                       # PATH FILTER UPDATED in PR 2 (hvac-scheduler → hvac_scheduler)
└── typecheck.yml                                   # NEW (PR 1)
docs/
├── type-debt-backlog.md                            # NEW (PR 1)
└── plans/
    └── type-checker-plan.md                        # NEW — unified implementation plan
```

Note: each service's `Dockerfile` may need its `COPY` line updated when new typed-adapter modules are added to that service (e.g., `influx_adapter.py` in PR 2). The migration template (§6.1) makes this explicit.

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
2. **Add package marker:** create empty `__init__.py` in the renamed directory. This is a TOOLING MARKER ONLY. The runtime entrypoint stays `python app.py` (or `python poller.py`); script-style imports like `from freshness import classify` continue to work because Python adds cwd to `sys.path`. The `__init__.py` enables mypy + pytest + IDE tooling to treat the dir as a package; it does NOT require converting imports to package-relative form or changing Dockerfile CMD.
3. **Update docker-compose:** edit `deploy/energy-stack/docker-compose.yml`:
   - Change the service's `build.context:` path to the new underscore name.
   - **DO NOT** change the service name (the YAML key, e.g., `hvac-scheduler:` stays hyphenated).
4. **Update Dockerfiles:** if the service's `Dockerfile` has explicit `COPY` lines listing source files (not just `COPY . .`), update them to include any new typed-adapter modules being added in this PR. Verify with a local `docker compose build <service-name>` if possible.
5. **Update `run_tests.sh`:** in the `services=(...)` array near the top of the script, change this service's entry from hyphen to underscore.
6. **Update `run_typecheck.sh`:** remove service from `not_yet_enforced`; add to `service_dirs` (underscore name). For cockpit-backend specifically, add `tools/cockpit/backend` to `repo_targets` instead.
7. **Update `pyproject.toml`:**
   - Remove the service's individual `freshness.py` entry from the `files = [...]` list (the per-service mypy invocation now covers the whole directory; no double-checking).
   - If this PR adds an adapter, append the adapter's `[[tool.importlinter.contracts]]` block. Add the service's package name to `[tool.importlinter] root_packages`.
8. **Update CI workflow path triggers:** any existing workflow that hardcodes the old hyphenated directory path (most notably `.github/workflows/check-freshness-drift.yml`) must be updated to the underscore path. Otherwise the existing safety gate silently stops triggering. PR 2 (scheduler) specifically must update `check-freshness-drift.yml`.
9. **Update path references:** sed markdown and shell scripts that reference `./<service>/` (the filesystem path) to use the underscore. Operational identifiers — the docker-compose service name, Grafana JSON references, Influx tag values, Telegraf labels, log labels — STAY hyphenated. Tighten with explicit grep verification at end of step.
10. **Local type-check:** `bash deploy/energy-stack/run_typecheck.sh` — iterate until clean.
11. **Triage each finding:**
    - Real bug → fix in the same PR (treat as bug-fix value).
    - Missing/incorrect annotation → fix the annotation.
    - Untyped library call → add to `[[tool.mypy.overrides]]` in `pyproject.toml` and consider adapter wrapping if surface is high-value.
12. **Tests pass:** `cd deploy/energy-stack/<service_underscored>/ && python -m pytest .` — verifies the rename didn't break test collection.
13. **Operational-identifier preservation grep:** `grep -rn "<service-hyphenated-name>" tools/cockpit deploy/energy-stack/grafana deploy/energy-stack/telegraf deploy/energy-stack/docker-compose.yml` — confirms the operational identifier still appears in the right places. If unexpected matches changed, restore.
14. **Dual-review** per AGENTS.md: superpowers:requesting-code-review + codex adversarial-review in parallel.
15. **Open PR** with `--base main`. Per AGENTS.md, no stacking; wait for prior PR to merge before opening the next service PR.
16. **Operator merges** in GitHub UI. The deploy.yml workflow handles the rest (since the rename touches `deploy/**`, it triggers redeploy of the renamed service).

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
- Step 3 (docker-compose) — cockpit-backend isn't in docker-compose (runs locally for operator monitoring); no compose change.
- Step 4 (Dockerfile) — n/a.
- Step 5 (`run_tests.sh`) — cockpit-backend isn't in `run_tests.sh`; its tests live under `tools/cockpit/backend/tests/` and run via the cockpit's own pytest invocation. Document in the PR.
- Step 6: add `tools/cockpit/backend` to `repo_targets` (not `service_dirs`) in `run_typecheck.sh`. Remove `tools/cockpit/backend/freshness.py` from `pyproject.toml` `files` list.
- Step 8 (CI workflow path) — `check-freshness-drift.yml` already handles cockpit-backend correctly (no rename); no change needed unless an adapter is added.
- Step 9 (path references) — minimal; cockpit-backend's path was never hyphenated.
- Other steps (pyproject.toml updates, mypy iteration, bug fixes, the deferred `price_is_stale` consumer fix, dual-review) apply as written.

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
- **GitHub branch protection / ruleset update:** operator adds `Type Check` to the list of required status checks for `main`. Without this step the workflow runs advisorily and the rollout's enforcement claim is false. PR 1 status remains "shipped (pending required-check enable)" until verified.
- **influxdb-client stub-status verification:** `python -c "import influxdb_client; print(influxdb_client.__file__)"` plus inspecting whether the package ships `py.typed`. Finding documented in `docs/type-debt-backlog.md` so PR 2's adapter design starts from facts, not assumption.
- **`types-*` discovery:** run `mypy --install-types --non-interactive` against the installed deps; record any suggested stub packages in `requirements-dev.txt` and the backlog.
- **Pydantic plugin loads:** mypy starts without error against the actual installed pydantic version (`pydantic>=2.5`).

### Phase 2: Scheduler — split into two PRs by default

The scheduler is the largest service and the highest-density type-hint surface (~350 annotated lines, ~3300 total scheduler lines, 300+ test functions, 500+ Mock references). Splitting Phase 2 into PR 2a (rename + infrastructure-only) and PR 2b (enforcement + adapter + type-fixes) is the DEFAULT plan. Combining them into a single PR is allowed only if PR 2a's actual diff is tiny AND the post-rename `mypy --strict` run on the scheduler surfaces under ~20 findings — both checked AFTER PR 2a's work is done, not predicted in advance.

#### Phase 2a: Scheduler rename + infrastructure-only (PR 2)

**Deliverables:**
- Rename `deploy/energy-stack/hvac-scheduler/` → `hvac_scheduler/`.
- Add `__init__.py` to renamed dir (tooling marker; entrypoint unchanged).
- Update docker-compose `build.context:` for the scheduler service.
- Update `run_tests.sh` services array (hyphen → underscore).
- Update `.github/workflows/check-freshness-drift.yml` path filter.
- Update path references in markdown/scripts that point to `./hvac-scheduler/`.
- Operational-identifier preservation grep verified.
- `run_typecheck.sh` still has empty `service_dirs` — scheduler NOT yet in enforced set.
- `pyproject.toml` `files` list still includes `deploy/energy-stack/hvac_scheduler/freshness.py` (updated path).

**Acceptance criteria:**
- All scheduler tests still pass via `cd deploy/energy-stack/hvac_scheduler/ && python -m pytest .`
- `bash deploy/energy-stack/run_tests.sh` succeeds.
- `bash deploy/energy-stack/run_typecheck.sh` still passes (freshness.py path-updated entry still type-checks).
- `docker compose up -d hvac-scheduler` succeeds locally (or verified by post-merge deploy.yml run on Pi-lab).
- Operational identifier `hvac-scheduler` still appears in compose service-name field, Grafana JSON, Influx tag values, Telegraf labels.

#### Phase 2b: Scheduler in enforced set + adapter + type-fixes (PR 3)

**Deliverables:**
- First typed adapter: `deploy/energy-stack/hvac_scheduler/influx_adapter.py` wrapping `influxdb_client` record API.
- Refactor `fetch_latest_comed` and any other callers of `record.get_*()` to use the typed adapter.
- Add `hvac_scheduler` to `service_dirs` in `run_typecheck.sh`.
- Remove `deploy/energy-stack/hvac_scheduler/freshness.py` from `pyproject.toml` `files` list (per-service mypy invocation now covers it).
- Add `[tool.importlinter]` contract banning direct `influxdb_client` imports outside `influx_adapter.py`.
- Update `hvac_scheduler/Dockerfile` `COPY` lines to include `influx_adapter.py`.
- Triage all `mypy --strict` findings on the scheduler.
- Fix real bugs; annotate missing types; document any necessary ignores.

**Acceptance criteria:**
- `mypy --strict deploy/energy-stack/hvac_scheduler/` returns zero errors.
- `python -m importlinter` returns zero violations.
- All scheduler tests pass.
- The `query_api` parameter is typed across the 10+ scheduler functions where it currently lacks an annotation. (This is the most direct descendent of the 19:18Z bug class.)
- Real bugs found are fixed in-PR with anti-regression tests where applicable. Each "real bug" entry in the PR description includes the file:line and a one-line description.

**Subsequent service PRs renumber accordingly: PR 4 = cockpit-backend, PR 5 = comed-poller, etc.**

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

Mock-heavy tests would need many `# type: ignore[misc]` annotations under full strict mode.

**Mitigation:** test-file relaxation pattern (§5.6) is enabled from PR 1. Tests keep strict checks that catch real test bugs (`disallow_any_generics`, `no_implicit_optional`, `warn_redundant_casts`, `warn_return_any`, `strict_equality`); annotation-completeness checks (`disallow_untyped_defs`, `disallow_untyped_decorators`) are relaxed. Specific error-code set tuned in PR 2b against the scheduler's actual test suite — not preemptively guessed.

### 8.7 Risk: scope creep during scheduler PR (the largest service)

Scheduler is the highest-density type-hint surface and the largest single service. Combined PR (rename + enforcement + adapter + bug fixes) is genuinely likely to balloon.

**Mitigation: the PR 2a / PR 2b split is the DEFAULT plan, not a contingency.** See §7 Phase 2. Combined PR is allowed only if PR 2a's diff is tiny AND post-rename mypy surfaces under ~20 findings; both checked after PR 2a's work, not predicted. Subagent-driven-development pattern from freshness PR is the model: small task-level commits within each PR; phase checkpoints with dual-review.

If PR 2b itself balloons (say, >500 lines of net type-fix changes), a further split is acceptable:
- PR 2b.1: adapter + boundary contract + refactor `fetch_latest_comed`
- PR 2b.2: remaining type-fixes by file region

The greenfield invariant holds: the scheduler is not in `service_dirs` until its full directory passes `mypy --strict` AND `import-linter` passes.

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
- `not_yet_enforced` array is empty.
- `Type Check` is a required status check on `main` in GitHub branch protection.
- `pytest.ini` workaround docstring removed.
- Spec status updated to `shipped`.

### 9.3 Pre-OSF gate (minimum required before filing)

- Scheduler is in the enforced set (`mypy --strict` + `import-linter` both pass).
- Cockpit-backend is in the enforced set (since the freshness PR left a known-broken `price_is_stale` consumer there).
- `Type Check` is a required status check on `main`.
- All findings from those two services are either fixed or documented as accepted (with rationale in the PR).

Other services may complete post-OSF.

## §10. Status & history

| Date | Status | Note |
|---|---|---|
| 2026-05-20 | draft | Initial spec. Captures decisions from brainstorming session 2026-05-20: scope (C — all scheduler-stack services + cockpit-backend, scripts/ deferred); tool (mypy --strict); rollout strategy (per-service greenfield, infrastructure-PR-first); hyphenated-dir resolution (rename per service while preserving operational identifier); external library strategy (option B — module-level ignore + targeted typed adapters tracked as backlog); pydantic plugin enabled; tests included with strict; PR 1 bootstrap with the two freshness modules. Open for first-pass dual-review (superpowers:requesting-code-review + codex adversarial-review). |
| 2026-05-20 | draft (rev 2) | First-pass dual-review findings applied. Critical fixes: Python 3.13 (matches service Dockerfiles, not 3.11); `run_typecheck.sh` rewritten to handle cockpit-backend `repo_targets`, prevent dual-invocation of `files` list, and run import-linter; CI workflow installs ALL service requirements via loop (not hardcoded list); `.github/workflows/check-freshness-drift.yml` path-filter update added to migration template. Design decisions: package model is "hybrid" (rename + `__init__.py` for tooling; script entrypoints stay); adapter boundaries enforced via `import-linter` (not deferred to backlog); tests get targeted relaxation of annotation-completeness checks (specific codes tuned in PR 2b against scheduler suite). Phase 2 split into PR 2a (rename) + PR 2b (enforcement) as DEFAULT plan, not contingency. Pre-OSF gate now requires `Type Check` to be a GitHub-required status check, not just a workflow file. Influxdb-client stub-status verification + `mypy --install-types` discovery added to PR 1 acceptance. Aiohttp note added (ships typed stubs; surfaces in nws/pjm/telegram service PRs). Open for second-pass dual-review. |
