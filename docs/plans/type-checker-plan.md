---
date: 2026-05-20
owner: chris
status: draft
role-label: chris
---

# Python Type-Checker Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [docs/superpowers/specs/2026-05-20-type-checker-design.md](../superpowers/specs/2026-05-20-type-checker-design.md) — rev 7, architecture approved (4 dual-review rounds + 2 operator pre-execution reviews).

**Goal:** Land enforced `mypy --strict` type-checking on all 11 scheduler-stack Python services + cockpit-backend, before the OSF freeze locks in the binding implementation. Adapter-boundary enforcement via `import-linter`. Full package model (every service is a regular Python package with `__init__.py`, package-relative imports, `python -m <service>.<entrypoint>` Dockerfile CMD).

**Architecture:** Three-layer (pyproject.toml mypy config + run_typecheck.sh per-service invocation + CI workflow), with per-service rollout. Each service PR includes: directory rename to underscore, `__init__.py` addition, sibling-import conversion to relative form, Dockerfile WORKDIR + CMD update, greenfield mypy clean, and import-linter contracts for adapters. Bootstrap mypy pass over a `files` list keeps the two freshness modules covered from PR 1 until each service migrates in. Bootstrap pass guarded by a tomllib check so it skips cleanly once `files` is empty (post-Phase-4).

**Tech Stack:** Python 3.13, mypy 2.x with `--strict` (exact `==X.Y.Z` version captured at PR 1 implementation; mypy 2.0 GA 2026-05-06), pydantic v2 mypy plugin, import-linter (`lint-imports` CLI), GitHub Actions (ubuntu-latest runner), Docker Compose, pytest. Dev dependencies live in a `requirements-dev.in` (human intent) / `requirements-dev.lock` (all direct + transitive deps pinned `==X.Y.Z`) pair for the OSF-locked study window — see spec §8.2 for the lockfile rationale and the mypy 2 default-flag changes that may surface findings.

**Branching:** **PR 0** (docs prelude) landed via `feat/type-checker` → `main` as PR #11 on 2026-05-20 and contained ONLY the spec + plan documents (no infrastructure). Two follow-up docs PRs revised the plan and spec before PR 1 execution: PR #12 (rev 6 pre-execution feedback) and PR #14 (rev 7 mypy 2.x + .in/.lock pivot). **PR 1** (`feat/type-checker-pr1`, merged as PR #17 on 2026-05-21) landed the infrastructure: `pyproject.toml`, `run_typecheck.sh`, `typecheck.yml`, `type-debt-backlog.md`, `requirements-dev.in` + `requirements-dev.lock`. Plan rev 5 (this revision) folded back five small execution-time discoveries from PR 1 so subsequent PRs don't replay them. **PRs 2-15** each branch from `main` after the prior PR merges. No stacking; one PR at a time per AGENTS.md.

---

## Outside-in Acceptance Test

Per AGENTS.md outside-in TDD: the feature-level acceptance test must produce a visible pass/fail signal at every PR boundary.

**The acceptance test:** "Deliberately introducing a type error in any enforced module causes CI to fail."

This is verified at PR 1's acceptance gate (deliberately break `freshness.py`, observe CI red, revert). Subsequent PRs extend the verification surface to their newly-enforced service. The test passes when CI emits the `type-check` status check and that check is blocking on `main`.

There is no `pytest`-runnable acceptance test for this rollout because the test IS the CI gate. The xfail-strict discipline doesn't apply directly; the equivalent is "CI is configured but not yet required" (PR 1) → "CI is required and blocks regressions" (PR 1 post-merge).

---

## Phase 1: Infrastructure (PR 1)

**Goal:** Set up mypy + import-linter infrastructure. Enforce the two freshness modules. Configure GitHub branch protection. Ship the spec + plan as part of this PR.

**Files created in this phase:**
- `pyproject.toml` (repo root): mypy config + plugin + overrides + import-linter skeleton
- `deploy/energy-stack/run_typecheck.sh`: per-service mypy + import-linter runner
- `.github/workflows/typecheck.yml`: CI workflow
- `docs/type-debt-backlog.md`: adapter wishlist + library priorities
- `deploy/energy-stack/requirements-dev.in`: human-intent dev dependencies (test + type-checking, unpinned; ranges OK). Replaces the prior `requirements-dev.txt`.
- `deploy/energy-stack/requirements-dev.lock`: generated lockfile — every direct + transitive dep pinned `==X.Y.Z` via clean Python 3.13 `pip install -r .in && pip freeze`.

**Files modified in this phase:**
- `docs/superpowers/specs/2026-05-20-type-checker-design.md`: already on branch (no further change)
- `docs/plans/type-checker-plan.md`: this file (no further change)

---

### Task 1: Verify working tree + inspect existing requirements-dev.txt baseline

**Files:**
- Read: `deploy/energy-stack/requirements-dev.txt` (the pre-rev-7 baseline file; will be retired in Task 2 in favor of the `.in` / `.lock` pair, but its current contents — pytest, pytest-asyncio, tzdata — must be preserved into `requirements-dev.in`).

- [ ] **Step 1: Confirm we're on the correct branch with a clean tree**

Run:
```bash
git status --short --branch
```
Expected: `## feat/type-checker-pr1` and no modified files. The spec + plan landed in PR 0 (PR #11, merged to `main` on 2026-05-20); this branch is cut fresh off `main` and starts empty.

- [ ] **Step 2: Inspect the existing requirements-dev.txt baseline**

Run:
```bash
ls -la deploy/energy-stack/requirements-dev.txt 2>&1
```

If file exists, read it:
```bash
cat deploy/energy-stack/requirements-dev.txt
```

Note current contents — Task 2 deletes this file and replaces it with the `.in` / `.lock` pair, but the human-intent lines (pytest, pytest-asyncio, tzdata, plus comments) must be preserved into `requirements-dev.in`.

- [ ] **Step 3: Verify Python 3.13 + mypy availability for local testing**

Run:
```bash
python --version
```
Expected: `Python 3.13.x` (matches service Dockerfiles).

If Python is older than 3.13, note this. Local tasks may need to run inside a 3.13 virtualenv or container; CI runs against 3.13 regardless. Continue regardless of local Python version — we don't need 3.13 locally to author files.

---

### Task 2: Replace requirements-dev.txt with requirements-dev.in + requirements-dev.lock pair

**Goal:** Retire the single mixed-pins file. Create a `.in` (human intent, unpinned) + `.lock` (every direct + transitive dep pinned `==X.Y.Z`) pair under Python 3.13. CI and local installs both read `.lock`. Toolchain refreshes are deliberate "refresh typecheck toolchain" PRs that re-run the freeze and update both files in one commit. (Spec §8.2 R7B.)

**Files:**
- Delete: `deploy/energy-stack/requirements-dev.txt` (the pre-rev-7 baseline)
- Create: `deploy/energy-stack/requirements-dev.in`
- Create: `deploy/energy-stack/requirements-dev.lock`

- [ ] **Step 1: Capture baseline content of the existing requirements-dev.txt**

Read the file so the pytest / pytest-asyncio / tzdata lines and their comments are preserved into `requirements-dev.in`:

```bash
cat deploy/energy-stack/requirements-dev.txt
```

Expected (as of `ba067a1`): the file contains a header comment block, then `pytest>=8`, `pytest-asyncio>=0.23`, a Windows tzdata comment, then `tzdata`.

- [ ] **Step 2: Write `requirements-dev.in`**

Create `deploy/energy-stack/requirements-dev.in` with the baseline content above plus the type-checking section. Ranges are acceptable in `.in` because nothing installs from `.in` directly — it is the intent file.

```
# Human-intent dev dependencies for the energy-stack test + type-checking pipeline.
#
# This file is HAND-EDITED. CI and local installs read requirements-dev.lock,
# never this file. Workflow:
#   1. Edit this file to add/remove a dependency.
#   2. Regenerate the lock (see header of requirements-dev.lock).
#   3. Commit BOTH files in the same commit.
# See spec §8.2 R7B for the lockfile rationale.

# --- Test runner -----------------------------------------------------
pytest>=8
pytest-asyncio>=0.23
# Windows: ZoneInfo("America/Chicago") needs the iana database; container
# python:3.13-slim bundles it but Windows Python doesn't.
tzdata

# --- Type-checking (spec §8.2) ---------------------------------------
mypy
pydantic
import-linter
types-requests
```

- [ ] **Step 3: Generate `requirements-dev.lock` from a clean Python 3.13 venv**

```bash
python -m venv /tmp/typecheck-pin
# git-bash on Windows uses the Scripts path; mac/linux uses bin:
source /tmp/typecheck-pin/Scripts/activate    # PowerShell: \Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r deploy/energy-stack/requirements-dev.in
python -m pip freeze > /tmp/lock-body.txt
deactivate
```

Then prepend a generation header and write the result to the repo file:

```
# Lockfile for dev dependencies. EVERY direct + transitive dep pinned ==X.Y.Z.
# DO NOT HAND-EDIT — regenerate via the procedure below when refreshing the toolchain.
#
# How this file was generated (PR 1, 2026-MM-DD, Python 3.13.x):
#   python -m venv /tmp/typecheck-pin
#   source /tmp/typecheck-pin/Scripts/activate
#   python -m pip install --upgrade pip
#   python -m pip install -r deploy/energy-stack/requirements-dev.in
#   python -m pip freeze > deploy/energy-stack/requirements-dev.lock
#
# Refresh: same procedure in a clean venv. Update the date header above
# and commit both .in and .lock together as a "refresh typecheck toolchain"
# PR per spec §8.2 R7B.

<paste verbatim pip freeze output here>
```

Replace `2026-MM-DD` with today's date and `3.13.x` with the actual `python --version` patch.

- [ ] **Step 4: Delete the old `requirements-dev.txt` and update any non-doc references**

```bash
git rm deploy/energy-stack/requirements-dev.txt
git grep -nE "requirements-dev\.txt" -- ':!docs/'
```

Update each non-doc match (e.g., `run_tests.sh`, `Dockerfile` COPY lines, scripts) to `requirements-dev.lock`. Doc references are handled by the rev 7 doc revision, not by this task.

- [ ] **Step 5: Install the lockfile locally as a sanity check**

```bash
pip install -r deploy/energy-stack/requirements-dev.lock
```

If pip install resolves cleanly, the lockfile is valid. If it fails, the lock has a defect — investigate before continuing (don't ship a broken lock).

- [ ] **Step 6: Verify mypy major version matches spec intent**

Confirm the captured mypy is on the 2.x line (matches spec §8.2 R7A intent):

```bash
grep -E '^mypy==' deploy/energy-stack/requirements-dev.lock
```

Expected: `mypy==2.X.Y` (NOT `mypy==1.X.Y`). If the resolve picked 1.x for any reason (e.g., a transitive constraint), STOP and escalate — the spec assumes mypy 2.x.

- [ ] **Step 7: Commit**

```bash
git add deploy/energy-stack/requirements-dev.in deploy/energy-stack/requirements-dev.lock
git rm deploy/energy-stack/requirements-dev.txt    # if not already staged in step 4
git commit -m "feat(type-checker): add requirements-dev.{in,lock} pair (mypy 2.x, all transitives pinned)

Replaces requirements-dev.txt with an .in/.lock pair per spec §8.2 R7B.
.in = human intent (pytest + type-checking, unpinned).
.lock = every direct + transitive dep pinned ==X.Y.Z, generated from a
clean Python 3.13 \`pip install -r .in && pip freeze\`. CI and local
installs both read .lock. mypy 2.x (spec §8.2 R7A: mypy 2.0 GA
2026-05-06; no reason to ship intentionally-older mypy for the
study window)."
```

---

### Task 3: Create pyproject.toml at repo root

**Files:**
- Create: `pyproject.toml` (at repo root)

- [ ] **Step 1: Verify pyproject.toml does not already exist**

Run:
```bash
ls -la pyproject.toml 2>&1
```

If it exists with other content (e.g., from a tool we haven't seen), STOP and report — we need to integrate, not overwrite. If it does not exist, proceed.

- [ ] **Step 2: Write pyproject.toml**

Create `pyproject.toml` at repo root with:

```toml
# Energy-stack project configuration.
# This file is the canonical location for tool config (mypy, import-linter)
# per the type-checker rollout spec (docs/superpowers/specs/2026-05-20-type-checker-design.md).
# The repo is NOT a single Python package; each service under
# deploy/energy-stack/ is its own deployment unit.

[tool.mypy]
# Match service Dockerfiles (FROM python:3.13-slim).
python_version = "3.13"
strict = true
# mypy 2 default flips — explicit per spec §8.2 R7A so intent is
# documented and not subject to default-drift between minor versions.
local_partial_types = true
strict_bytes = true
warn_unreachable = true
warn_redundant_casts = true
warn_unused_ignores = true
plugins = ["pydantic.mypy"]

# Make each renamed service under deploy/energy-stack/ a top-level
# package on the mypy search path (e.g., hvac_scheduler.app).
mypy_path = "deploy/energy-stack"

# Bootstrap files list. PR 1 enforces ONLY these two modules via the
# pyproject.toml files list. As each service migrates in (PR 2 onwards),
# its individual freshness.py entry is removed from `files`; the
# per-service mypy invocation in run_typecheck.sh covers the whole
# package directory.
files = [
    "deploy/energy-stack/hvac-scheduler/freshness.py",
    "tools/cockpit/backend/freshness.py",
]

# Third-party library overrides. Libraries without py.typed get
# `ignore_missing_imports = true` so mypy treats them as Any. Per-library
# typed adapters (see import-linter contracts and spec §5.2-5.5) are
# the path to recovering type safety on bug-prone surfaces.
[[tool.mypy.overrides]]
module = "influxdb_client.*"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "pyControl4.*"
ignore_missing_imports = true

# Test-file relaxation. Mypy override `module` patterns must be
# fully-qualified dotted module names (NOT filename globs). The list
# below is populated per-service as migrations land. Each service PR
# appends its own test module names.
#
# What stays strict in tests:
#   - disallow_any_generics
#   - no_implicit_optional
#   - warn_redundant_casts
#   - warn_return_any
#   - strict_equality
# What's relaxed:
#   - disallow_untyped_defs (test functions return -> None implicitly)
#   - disallow_untyped_decorators (mock/parametrize decorators have
#     less-than-perfect types)
[[tool.mypy.overrides]]
module = [
    # Populated per service. Example after PR 3:
    # "hvac_scheduler.test_hvac_scheduler",
    # "hvac_scheduler.test_freshness",
    # "hvac_scheduler.test_decision_trace",
    # "hvac_scheduler.conftest",
]
disallow_untyped_defs = false
disallow_untyped_decorators = false

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true

# Adapter-boundary enforcement (spec §5.5). Each typed adapter ships a
# corresponding `forbidden` contract banning direct use of the wrapped
# library outside the adapter module. First contract lands in PR 3 with
# the influx_adapter.
[tool.importlinter]
root_packages = []
# Required for `forbidden` contracts targeting third-party modules.
include_external_packages = true
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

Use the Write tool to create the file with exactly this content.

- [ ] **Step 3: Verify it's a valid TOML file**

Run (use absolute path to guard against cwd resets between bash invocations):
```bash
(cd /Users/christopherdepaola/Developer/energy-stack-1 && python -c "import tomllib; print(list(tomllib.load(open('pyproject.toml', 'rb')).keys()))")
```
Expected: `['tool']` (or similar — proves it parses).

If you get a `tomllib.TOMLDecodeError`, fix the syntax error and re-run.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(type-checker): add pyproject.toml with mypy + import-linter config

Python 3.13, mypy --strict, pydantic plugin, namespace-package-free
full-package model via mypy_path. Bootstrap files list covers the two
freshness modules until per-service migration. Import-linter skeleton
ready for adapter contracts (first lands PR 3)."
```

---

### Task 4: Create run_typecheck.sh

**Files:**
- Create: `deploy/energy-stack/run_typecheck.sh`

- [ ] **Step 1: Write run_typecheck.sh**

Create the file with this content:

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
# Also runs import-linter to enforce adapter boundaries (spec §5.5).
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
# run_typecheck.sh does not fail on mypy's "no targets" error (code 2).
if (cd "$REPO_ROOT" && python -c "
import tomllib, sys
with open('pyproject.toml', 'rb') as f:
    cfg = tomllib.load(f)
files = cfg.get('tool', {}).get('mypy', {}).get('files', [])
sys.exit(0 if files else 1)
"); then
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

- [ ] **Step 2: Make it executable**

```bash
chmod +x deploy/energy-stack/run_typecheck.sh
```

- [ ] **Step 3: Verify the script parses (does not run mypy yet — mypy may not be installed)**

```bash
bash -n deploy/energy-stack/run_typecheck.sh
```
Expected: no output (syntax OK).

- [ ] **Step 4: Commit**

```bash
git add deploy/energy-stack/run_typecheck.sh
git commit -m "feat(type-checker): add per-service run_typecheck.sh

Mirrors run_tests.sh structure. service_dirs + repo_targets enumerate
enforced targets; bootstrap pass over pyproject.toml files list covers
modules during the multi-PR migration. Bootstrap is tomllib-guarded so
it skips when files is empty (post-Phase-4 cleanup), avoiding mypy's
'no targets' return-code-2 failure."
```

---

### Task 5: Create CI workflow

**Files:**
- Create: `.github/workflows/typecheck.yml`

- [ ] **Step 1: Inspect existing workflows for style reference**

```bash
ls .github/workflows/
cat .github/workflows/check-freshness-drift.yml | head -30
```

This confirms the GitHub Actions versions in repo convention (`actions/checkout@v6` with explicit `# Node 24, replaces deprecated v4 (Node 20)` comment, per deploy.yml + shadow-validation.yml + check-freshness-drift.yml). Match what's there.

- [ ] **Step 2: Write typecheck.yml**

```yaml
# Workflow name and job name are both "type-check" so the required
# status check appears in GitHub branch protection predictably. The
# operator copies the EXACT emitted check name from the Actions UI
# when configuring branch protection (current GitHub behavior:
# `type-check / type-check`).
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
      - uses: actions/checkout@v6  # Node 24, replaces deprecated v4 (Node 20)
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"  # match service Dockerfiles
      - name: Cache mypy
        uses: actions/cache@v4
        with:
          path: ~/.mypy_cache
          # Hash BOTH pyproject.toml AND the lockfile so a "refresh
          # typecheck toolchain" PR (spec §8.2 R7B), which bumps mypy
          # in requirements-dev.lock without touching pyproject.toml,
          # still invalidates the incremental cache. Otherwise stale
          # mypy_cache could mask findings from a cold run.
          key: mypy-${{ runner.os }}-${{ hashFiles('pyproject.toml', 'deploy/energy-stack/requirements-dev.lock') }}
      - name: Install dev dependencies
        run: pip install -r deploy/energy-stack/requirements-dev.lock
      - name: Install all service requirements
        # Install every IN-SCOPE service's requirements.txt unconditionally
        # so mypy can resolve typed library symbols regardless of which
        # services are currently in the enforced set. Pip deduplicates
        # shared deps. Avoids the "added a service to enforced set,
        # forgot to update CI" failure mode.
        #
        # Explicit enumeration (not a glob) because `deploy/energy-stack/`
        # also contains the out-of-scope `scripts/` directory (per spec
        # §3.2) whose requirements would otherwise be installed and could
        # fail the typecheck job for reasons unrelated to the enforced
        # set. When a new service is added in scope, append it here AND
        # to `run_typecheck.sh`. The migration template (spec §6.1)
        # lists this.
        run: |
          for req in \
            deploy/energy-stack/comed-poller/requirements.txt \
            deploy/energy-stack/eagle-poller/requirements.txt \
            deploy/energy-stack/ecowitt-ingest/requirements.txt \
            deploy/energy-stack/haven-ingest/requirements.txt \
            deploy/energy-stack/hvac-scheduler/requirements.txt \
            deploy/energy-stack/hvac-scheduler-watchdog/requirements.txt \
            deploy/energy-stack/nws-poller/requirements.txt \
            deploy/energy-stack/pjm-dm2-poller/requirements.txt \
            deploy/energy-stack/refoss-poller/requirements.txt \
            deploy/energy-stack/telegram-notifier/requirements.txt \
            deploy/energy-stack/thermostat-poller/requirements.txt \
            tools/cockpit/backend/requirements.txt; do
            if [[ -f "$req" ]]; then
              pip install -r "$req"
            fi
          done
      - name: Run type checks
        run: bash deploy/energy-stack/run_typecheck.sh
```

Use the Write tool. Verify it parses as valid YAML:

- [ ] **Step 3: Validate YAML syntax**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/typecheck.yml'))"
```
Expected: no output (valid YAML). If `yaml` module is missing, install it (`pip install pyyaml`) or skip.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/typecheck.yml
git commit -m "feat(type-checker): add CI workflow

Runs run_typecheck.sh on every push and PR to main. Python 3.13 to
match service Dockerfiles. Caches ~/.mypy_cache for speed. Installs
all service requirements unconditionally so mypy can resolve typed
library symbols."
```

---

### Task 6: Create type-debt-backlog.md

**Files:**
- Create: `docs/type-debt-backlog.md`

- [ ] **Step 1: Write the backlog doc**

```markdown
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

**Operator follow-up (PR 1 acceptance):** verify whether `influxdb_client==1.48.0` ships `py.typed`. If it does, the `[[tool.mypy.overrides]] ignore_missing_imports = true` for `influxdb_client.*` should be REMOVED (the upstream stubs would conflict). The adapter pattern still applies as a projection seam.

### pyControl4 (`pyControl4.*`) — Priority: P1

**Status:** Not yet scheduled.

**Surface:** HVAC command API used by the scheduler to drive setpoint changes. Smaller surface than influxdb-client; bugs here surface as commanded-setpoint mismatches.

**Adapter scope (future):** wrap the `pyControl4` client's command-issuance methods (`set_temperature_offset`, `set_mode`, etc.) with typed dataclasses for inputs and a typed result for outputs.

### Other libraries

Libraries that ship `py.typed` natively (no adapter needed, no override needed):
- `fastapi`, `httpx`, `pydantic`, `uvicorn`, `python-dotenv` (used by cockpit-backend)
- `aiohttp` (>=3.8) — used by `nws-poller`, `pjm-dm2-poller`, `telegram-notifier`. Bundled stubs surface usage issues; expect findings in those service PRs.

Libraries with PyPI stub packages (`types-*` — add to `requirements-dev.in`, regenerate `requirements-dev.lock`):
- `types-requests` for `requests` (if/when a service uses it)

---

## Stub-Status Verification Log

PR 1 acceptance includes verifying the stub status of `influxdb_client==1.48.0`. Record the finding here:

- `influxdb_client==1.48.0`: TBD (verify in PR 1 acceptance gate).

---

## Adding to This Backlog

A library belongs here when:
1. We hit a bug-shape using it (like the 19:18Z freshness bug)
2. Our usage is wide enough that wrapping pays off
3. The library lacks typed stubs upstream

Add an entry: library name, priority (P0-P2), current status, surface description, adapter scope. Link to the relevant incident or PR if applicable.
```

Use the Write tool.

- [ ] **Step 2: Commit**

```bash
git add docs/type-debt-backlog.md
git commit -m "feat(type-checker): add type-debt-backlog.md

Tracks libraries warranting typed-adapter wrapping. Seeded with
influxdb-client (P0; adapter lands PR 3, derived from 19:18Z bug)
and pyControl4 (P1; future). Documents libraries that ship py.typed
natively and PyPI stub packages."
```

---

### Task 7: Local verification — bootstrap mypy pass on freshness modules

**Files:**
- Read: `deploy/energy-stack/hvac-scheduler/freshness.py`
- Read: `tools/cockpit/backend/freshness.py`

- [ ] **Step 1: Install dev dependencies from the pinned requirements-dev.lock**

```bash
pip install -r deploy/energy-stack/requirements-dev.lock
```

This installs the exact `==X.Y.Z` versions captured by Task 2 step 3. Do NOT install via `requirements-dev.in` here — local environment must match what CI installs, which is always `.lock`.

If pip install fails (no Python 3.13 locally), skip to step 4 (the CI run is the canonical verification).

- [ ] **Step 2: Run the bootstrap mypy pass**

```bash
python -m mypy
```

Expected: `Success: no issues found in 2 source files` (or similar wording — mypy reports the two freshness.py modules clean).

If mypy reports errors:
- If they're real bugs, fix them in `freshness.py` (or `tools/cockpit/backend/freshness.py`) in this task. Commit fixes separately.
- If they're spurious (e.g., mypy not finding `pydantic.mypy` plugin), address the install and retry.
- If they suggest type stubs (`types-influxdb-client` etc.), record the suggestion in `type-debt-backlog.md` per Task 9.

- [ ] **Step 3: Run run_typecheck.sh end-to-end**

```bash
bash deploy/energy-stack/run_typecheck.sh
```

Expected output:
```
=== type-checking pyproject.toml files list ===
Success: no issues found in 2 source files
OK
```

The bootstrap pass runs; both per-service loops are empty; import-linter is skipped (no targets); script exits 0.

- [ ] **Step 4: Document the verification result**

If local verification was skipped (no Python 3.13), note this in the PR description: "Local mypy run skipped — Python 3.13 not available locally; CI run is the canonical verification."

If local verification succeeded, note the mypy version in the PR description.

- [ ] **Step 5: NO commit yet** — this is a verification step, no file changes.

---

### Task 8: Verify influxdb-client stub status

**Files:**
- Modify: `docs/type-debt-backlog.md`

- [ ] **Step 1: Inspect the installed influxdb_client package**

```bash
python -c "import influxdb_client; print('FILE:', influxdb_client.__file__); import os; pkg_dir = os.path.dirname(influxdb_client.__file__); print('HAS py.typed:', os.path.exists(os.path.join(pkg_dir, 'py.typed')))"
```

Possible outputs:
- `HAS py.typed: True` — the library ships typed stubs. Update the override in pyproject.toml to remove the `influxdb_client.*` block (the stubs take precedence). Update `type-debt-backlog.md` to reflect this.
- `HAS py.typed: False` — the library is untyped. Keep the override block in pyproject.toml as-is. Update `type-debt-backlog.md` to note the verification finding.
- Module not installable locally — note this and defer the check to CI (the GH Actions runner installs influxdb-client; can verify during PR 1 CI run).

- [ ] **Step 2: Update type-debt-backlog.md with the finding**

Edit `docs/type-debt-backlog.md` to update the "Stub-Status Verification Log" section. Example:

```markdown
## Stub-Status Verification Log

PR 1 acceptance includes verifying the stub status of `influxdb_client==1.48.0`. Record the finding here:

- `influxdb_client==1.48.0`: ships `py.typed` (verified 2026-05-20 via local install). Override block in pyproject.toml has been removed; upstream stubs are now active for mypy. The adapter pattern in PR 3 still applies as a projection seam.
```

or

```markdown
- `influxdb_client==1.48.0`: does NOT ship `py.typed` (verified 2026-05-20 via local install). Override block in pyproject.toml remains active; library is treated as `Any`. Adapter in PR 3 provides typed surface.
```

- [ ] **Step 3: If `py.typed` is present, remove the influxdb_client override**

In `pyproject.toml`, delete this block:
```toml
[[tool.mypy.overrides]]
module = "influxdb_client.*"
ignore_missing_imports = true
```

Add a comment in its place noting the verification:
```toml
# influxdb_client.* — VERIFIED ships py.typed; no override needed.
# Upstream stubs are authoritative. See docs/type-debt-backlog.md.
```

- [ ] **Step 4: Re-run the bootstrap mypy pass**

```bash
python -m mypy
```

If the override removal causes new findings (because upstream stubs now type-check our usage strictly), this is GOOD news — these are real findings the override was hiding. Triage:
- Real bugs → fix in this PR.
- Stub disagreements → either fix our code to match, or add narrower per-module overrides.
- Document any decisions in `type-debt-backlog.md`.

- [ ] **Step 5: Commit**

```bash
git add docs/type-debt-backlog.md pyproject.toml
git commit -m "feat(type-checker): verify influxdb_client stub status

[describe the finding: ships py.typed / does not ship py.typed]
[describe the override decision based on the finding]"
```

---

### Task 9: Run `mypy --install-types` to discover available stub packages

**Files:**
- Modify: `deploy/energy-stack/requirements-dev.in` (if stubs are found and we want them — add to intent file, then regenerate `.lock` per Task 2 step 3 procedure)
- Modify: `deploy/energy-stack/requirements-dev.lock` (regenerated from the updated `.in`)
- Modify: `docs/type-debt-backlog.md`

- [ ] **Step 1: Run plain mypy to surface stub-suggestion notes (no mutation)**

```bash
python -m mypy
```

mypy 2.x emits `note: Hint: "python3 -m pip install types-..."` lines whenever it sees a third-party import without a local override and a stub package exists on PyPI — WITHOUT mutating the environment. This is the safe discovery pattern.

**Do NOT add `--install-types` to this invocation.** That flag tells mypy to install missing-stub packages directly into the active Python env, bypassing the `.in`/`.lock` workflow. (Historical note: earlier mypy versions had `--install-types --dry-run` as a "surface hints without installing" mode; mypy 2.0 dropped the `--dry-run` flag, so the current safe equivalent is plain `python -m mypy`.)

Mypy will analyze imports of files in its working set and may emit one or more `Hint:` notes. Each is for a library that doesn't ship `py.typed` but has a PyPI stub.

- [ ] **Step 2: Review each suggested stub**

For each suggested `types-*` package:
- If the library is one we have an `ignore_missing_imports` override for, INSTALLING the stub would conflict. Choose ONE:
  - Remove the override + install the stub (the stubs are now authoritative, mypy will surface real findings).
  - Keep the override + DON'T install the stub.
- If the library is NOT in our overrides, install the stub: add `types-<lib>` to `requirements-dev.in` and regenerate `requirements-dev.lock` (clean Python 3.13 `pip install -r .in && pip freeze` per Task 2 step 3).
- Document each decision in `type-debt-backlog.md` under a new "Stubs review (PR 1)" subsection.

- [ ] **Step 3: Re-run the bootstrap mypy pass to confirm clean**

```bash
python -m mypy
```

Expected: still clean.

- [ ] **Step 4: Commit (if changes were made)**

```bash
git add deploy/energy-stack/requirements-dev.in deploy/energy-stack/requirements-dev.lock docs/type-debt-backlog.md pyproject.toml
git commit -m "feat(type-checker): review and install --install-types suggestions

[describe each stub added / declined and why]
[note: requirements-dev.lock regenerated from updated .in]"
```

If no changes were made (no stubs suggested or all declined), no commit needed — proceed to next task.

---

### Task 10: Push branch and open PR 1

**Files:**
- None (git operations only)

- [ ] **Step 1: Verify the branch state**

```bash
git status --short --branch
git log --oneline main..HEAD
```

Expected: clean tree; 8-12 commits ahead of main (depending on Task 8/9 changes). Current branch: `feat/type-checker-pr1`.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feat/type-checker-pr1
```

Expected: branch pushed successfully. If a pre-push hook fails, do NOT bypass with `--no-verify` per AGENTS.md. Diagnose the hook failure and fix the underlying issue.

- [ ] **Step 3: Open PR 1**

```bash
gh pr create --base main --title "feat(type-checker): infrastructure + freshness modules enforced (PR 1)" --body "$(cat <<'EOF'
## Summary

PR 1 of the multi-PR type-checker rollout. Spec: \`docs/superpowers/specs/2026-05-20-type-checker-design.md\`. Plan: \`docs/plans/type-checker-plan.md\`.

Lands the mypy + import-linter infrastructure and enforces the two freshness modules (\`deploy/energy-stack/hvac-scheduler/freshness.py\` and \`tools/cockpit/backend/freshness.py\`) via the pyproject.toml \`files\` bootstrap list.

**Subsequent PRs (per spec §6.3):**
- PR 2: scheduler rename + package conversion (no enforcement yet)
- PR 3: scheduler enforced + first typed adapter (influx_adapter)
- PR 4: cockpit-backend enforced + price_is_stale consumer fix + add tools/cockpit/__init__.py
- PR 5-14: pollers + watchdog + telegram-notifier (one per service)
- PR 15: cleanup

**Note:** PR 0 (this PR's predecessor) was PR #11 on 2026-05-20 — it landed the spec + plan documents only. This PR (PR 1) lands the infrastructure.

## What's in this PR

- \`pyproject.toml\` (NEW): mypy \`--strict\` config (Python 3.13, pydantic plugin, library overrides, test-relaxation block, import-linter skeleton).
- \`deploy/energy-stack/run_typecheck.sh\` (NEW): per-service mypy + import-linter runner. tomllib-guarded bootstrap pass for the files-list lifecycle.
- \`.github/workflows/typecheck.yml\` (NEW): CI workflow on Python 3.13 ubuntu-latest. Caches ~/.mypy_cache; installs IN-SCOPE service requirements via explicit enumeration (not a glob — scripts/ is excluded per spec §3.2).
- \`docs/type-debt-backlog.md\` (NEW): adapter wishlist + library priorities. Seeded with influxdb-client (P0) and pyControl4 (P1).
- \`deploy/energy-stack/requirements-dev.in\` (NEW) + \`deploy/energy-stack/requirements-dev.lock\` (NEW): replace the prior \`requirements-dev.txt\` per spec §8.2 R7B. \`.in\` is human intent (pytest + type-checking, unpinned); \`.lock\` pins every direct + transitive dep \`==X.Y.Z\`, captured from a clean Python 3.13 \`pip install -r .in && pip freeze\`. CI installs \`.lock\`. mypy 2.x line (mypy 2.0 GA 2026-05-06 per spec §8.2 R7A).
- \`deploy/energy-stack/requirements-dev.txt\` (DELETED): superseded by the \`.in\` / \`.lock\` pair.

Spec + plan (\`docs/superpowers/specs/2026-05-20-type-checker-design.md\`, \`docs/plans/type-checker-plan.md\`) were merged in PR 0 (PR #11). They are NOT in this PR's diff except for any rev 7 revisions co-bundled here.

## Acceptance criteria (per spec §7 Phase 1)

- [x] \`bash deploy/energy-stack/run_typecheck.sh\` passes locally (or in CI on a clean branch).
- [ ] **Deliberately introducing a type error in \`freshness.py\` causes CI to fail.** Verified after merge via a temporary commit + revert against \`main\`. (Cannot be verified in this PR because the workflow isn't a required check yet.)
- [x] The two \`freshness.py\` modules type-check clean under \`mypy --strict\`.
- [ ] **GitHub branch protection / ruleset update:** operator copies the EXACT \`type-check / type-check\` check name from the Actions UI and adds it to required-checks for \`main\` immediately after this PR's workflow run completes green. Without this step, the workflow runs advisorily and the rollout's enforcement claim is false.
- [x] **influxdb-client stub-status verification:** documented in \`docs/type-debt-backlog.md\`.
- [x] **\`mypy --install-types\` discovery:** reviewed; relevant stubs added to \`requirements-dev.in\` and \`requirements-dev.lock\` regenerated (see backlog).
- [x] **Pydantic plugin loads:** mypy starts without error.

## Operator post-merge tasks

1. Watch the workflow run on this PR's branch (and on main after merge). Verify both succeed.
2. Open GitHub Settings → Branches → main → Branch protection (or Rulesets). Add the emitted status check name (literally what the Actions UI shows, e.g., \`type-check / type-check\`) to "Require status checks to pass before merging."
3. To verify enforcement: open a throwaway branch, deliberately introduce a type error in \`tools/cockpit/backend/freshness.py\` (e.g., add \`x: int = "not an int"\` at top of file), push, open a PR. CI must report red. Close the throwaway PR without merging; the test is done.
4. Open PR 2 (scheduler rename) from \`main\` per spec §7 Phase 2a.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Report PR URL to operator**

PR 1 URL: `<from gh pr create output>`

Per AGENTS.md branching policy, agent stops at `gh pr create`. Operator reviews and merges in the GitHub UI.

- [ ] **Step 5: NO commit needed** — PR creation is a remote operation.

---

### Task 11: Phase 1 dual-review (after operator approves PR 1 in principle)

**Files:** None (review is external).

- [ ] **Step 1: Dispatch in-harness + Codex reviews in parallel**

Per AGENTS.md dual-review discipline, before operator merges PR 1, run the standard dual-review:
- `superpowers:requesting-code-review` (in-harness)
- Codex adversarial review via `codex exec review` CLI

Brief both with: branch `feat/type-checker-pr1`, base `main`, this is PR 1 of a 15-PR rollout (PR 0 docs prelude already merged), focus on infrastructure correctness, file structure, CI integration, no implementation code yet.

- [ ] **Step 2: Synthesize findings**

Apply any critical or important fixes inline. Commit fixes to the branch (CI will re-run automatically).

- [ ] **Step 3: Hand back to operator for merge**

Once reviews are clean, operator merges PR 1 in the GitHub UI.

- [ ] **Step 4: Post-merge cleanup**

```bash
git checkout main
git pull --ff-only origin main
git branch -d feat/type-checker-pr1
```

This is also a good moment for the operator to perform the deliberate-failure verification described in PR 1's acceptance criteria (Step 3 of PR 1's "operator post-merge tasks").

---

## Phase 2: Scheduler Rename + Package Conversion (PR 2)

**Goal:** Convert `hvac-scheduler/` to `hvac_scheduler/` as a proper Python package. Update Dockerfile to `python -m hvac_scheduler.app` entrypoint. NO type-checker enforcement yet (PR 3 does that). Bootstrap mypy pass still covers `hvac_scheduler/freshness.py` via the (path-updated) `files` list.

**Files renamed in this phase:**
- `deploy/energy-stack/hvac-scheduler/` → `deploy/energy-stack/hvac_scheduler/`

**Files modified in this phase:**
- All Python files in the renamed dir: sibling imports converted to relative form (`from .freshness import X`, etc.)
- `deploy/energy-stack/hvac_scheduler/Dockerfile`: WORKDIR, PYTHONPATH, CMD updated
- `deploy/energy-stack/docker-compose.yml`: scheduler's `build.context:` path
- `deploy/energy-stack/run_tests.sh`: services array entry (hyphen → underscore)
- `.github/workflows/check-freshness-drift.yml`: path filter updated
- `pyproject.toml`: `files` list entry path updated
- Various markdown / shell files referencing the old path

**Files NEW in this phase:**
- `deploy/energy-stack/hvac_scheduler/__init__.py` (empty)

---

### Task 12: Branch from main; verify clean working tree

**Files:** None.

- [ ] **Step 1: Switch to main and pull latest**

```bash
git checkout main
git pull --ff-only origin main
```

Verify PR 1 is merged. If not, STOP and wait for operator merge.

- [ ] **Step 2: Create the PR 2 branch**

```bash
git checkout -b feat/type-checker-scheduler-rename
```

- [ ] **Step 3: Verify the source state**

```bash
ls deploy/energy-stack/hvac-scheduler/ | head -20
```
Expected: the existing scheduler files (app.py, freshness.py, etc.) — confirms the rename hasn't happened yet.

```bash
grep "hvac-scheduler" deploy/energy-stack/run_tests.sh
```
Expected: the entry exists.

---

### Task 13: Rename the directory via git mv

**Files:**
- Renamed: `deploy/energy-stack/hvac-scheduler/` → `deploy/energy-stack/hvac_scheduler/`

- [ ] **Step 1: Execute git mv**

```bash
git mv deploy/energy-stack/hvac-scheduler deploy/energy-stack/hvac_scheduler
```

- [ ] **Step 2: Verify the rename was detected as a rename (not delete + add)**

```bash
git status --short
```
Expected: all entries show `R  hvac-scheduler/... -> hvac_scheduler/...` (capital R = rename detected). This preserves history.

If git did NOT detect renames (showed `D` + `A` separately), it's likely because file content was also modified mid-mv. Reset and try again:
```bash
git reset HEAD .
git checkout -- deploy/energy-stack/
git mv deploy/energy-stack/hvac-scheduler deploy/energy-stack/hvac_scheduler
```

- [ ] **Step 3: NO commit yet** — we'll do the rename + __init__.py + import conversion together.

---

### Task 14: Add empty __init__.py

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/__init__.py`

- [ ] **Step 1: Create empty __init__.py**

```bash
touch deploy/energy-stack/hvac_scheduler/__init__.py
```

Or use Write with empty content:

- [ ] **Step 2: Verify**

```bash
ls -la deploy/energy-stack/hvac_scheduler/__init__.py
```
Expected: file exists, 0 bytes.

- [ ] **Step 3: NO commit yet.**

---

### Task 15: Convert sibling imports to relative form

**Files:**
- Modify: all Python files in `deploy/energy-stack/hvac_scheduler/` that import sibling modules

- [ ] **Step 1: Identify sibling modules**

```bash
ls deploy/energy-stack/hvac_scheduler/*.py
```

List the Python files in the scheduler dir. The known sibling modules are (verified against current `app.py` imports):
- `app.py` (main entrypoint)
- `freshness.py`
- `price_overlay.py`
- `decision_codes.py`
- `pjm_5cp.py`
- `arm_calendar.py` (local copy, hash-sync-checked in CI)
- `precool.py`
- `safety_supervisor.py`
- `conftest.py`
- `test_*.py` (multiple test files including `test_precool.py`, `test_pjm_5cp.py`, etc.)

Each `from <name> import X` where `<name>.py` exists as a sibling is a cross-file sibling import that needs conversion. The `from arm_calendar import ...` line has a `# local copy, hash-sync-checked in CI` comment — preserve that comment as it documents the unusual coupling.

- [ ] **Step 2: Generate the conversion list**

Run the pre-conversion grep to find every sibling-import to be touched:
```bash
cd deploy/energy-stack/hvac_scheduler
grep -nE "^from [a-z0-9_]+ import|^import [a-z0-9_]+$|^    from [a-z0-9_]+ import|# noqa: E402" *.py
```

Inspect each match. Categorize:
- Stdlib import (e.g., `import os`, `from datetime import datetime`): SKIP — do not touch.
- External library (e.g., `from influxdb_client import InfluxDBClient`): SKIP — do not touch.
- Sibling module (e.g., `from freshness import classify`, where `freshness.py` exists in this dir): CONVERT to relative form.

- [ ] **Step 3: Apply conversions using Edit/sed**

For each sibling-module import found in Step 2, convert:
- `from <sibling> import X` → `from .<sibling> import X`
- `import <sibling>` → `from . import <sibling>`
- `import <sibling> as <alias>` → `from . import <sibling> as <alias>`

The scheduler has these specific patterns to convert (verified against current `app.py`):
- `from freshness import Freshness, classify, THRESHOLDS` → `from .freshness import Freshness, classify, THRESHOLDS`
- `from price_overlay import ...` → `from .price_overlay import ...`
- `from decision_codes import ...` → `from .decision_codes import ...`
- `from pjm_5cp import ...` → `from .pjm_5cp import ...`
- `from arm_calendar import ARM_CALENDAR, current_arm_at` (with `# local copy, hash-sync-checked in CI` comment) → `from .arm_calendar import ARM_CALENDAR, current_arm_at` (preserve the comment)
- `from precool import (...)` → `from .precool import (...)`
- `from safety_supervisor import validate_setpoints` → `from .safety_supervisor import validate_setpoints`
- Test files importing `from app import X` → `from .app import X`
- Cross-test imports like `from test_hvac_scheduler import some_fixture` → `from .test_hvac_scheduler import some_fixture`

- [ ] **Step 4: Handle special cases**

- **Late module-level imports** (e.g., `from freshness import Freshness, classify, THRESHOLDS  # noqa: E402` mid-file in `app.py` for circular-import avoidance): convert these too, keeping the `# noqa: E402` comment.
- **String module references in monkeypatch / mocks** (e.g., `monkeypatch.setattr("app.fetch_latest_comed", ...)` in tests): these need to become `monkeypatch.setattr("hvac_scheduler.app.fetch_latest_comed", ...)`. Grep:
  ```bash
  cd /Users/christopherdepaola/Developer/energy-stack-1
  git grep -nE "[\"'](app|freshness|price_overlay|decision_codes|pjm_5cp|test_[a-z0-9_]+)\\." deploy/energy-stack/hvac_scheduler/
  ```
  Inspect each match. If it's a Python module reference (in monkeypatch.setattr, importlib.import_module, etc.), prepend `hvac_scheduler.`.

- [ ] **Step 5: Post-conversion verification grep (should return zero unconverted imports)**

```bash
cd /Users/christopherdepaola/Developer/energy-stack-1
grep -rnE "^from (freshness|price_overlay|decision_codes|pjm_5cp|arm_calendar|precool|safety_supervisor|app|test_[a-z0-9_]+) import|^import (freshness|price_overlay|decision_codes|pjm_5cp|arm_calendar|precool|safety_supervisor|app) " deploy/energy-stack/hvac_scheduler/
```
Expected: empty (every cross-file sibling import is now relative).

```bash
git grep -nE "[\"'](app|freshness|price_overlay|decision_codes|pjm_5cp|arm_calendar|precool|safety_supervisor)\\." deploy/energy-stack/hvac_scheduler/
```
Expected: only matches with `hvac_scheduler.` prefix (any unprefixed bare module references need updating).

- [ ] **Step 6: NO commit yet** — combine with Dockerfile + compose in one rename commit.

---

### Task 16: Update Dockerfile (WORKDIR + PYTHONPATH + CMD)

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/Dockerfile`

- [ ] **Step 1: Inspect the current Dockerfile**

```bash
cat deploy/energy-stack/hvac_scheduler/Dockerfile
```

Note the current WORKDIR, COPY pattern, and CMD line.

- [ ] **Step 2: Update Dockerfile**

The new pattern:
```dockerfile
FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Copy requirements first for layer caching
COPY requirements.txt /app/hvac_scheduler/
RUN pip install --no-cache-dir -r /app/hvac_scheduler/requirements.txt

# Copy the package
COPY . /app/hvac_scheduler/

# Run as a module
CMD ["python", "-m", "hvac_scheduler.app"]
```

Adapt the above to the actual existing Dockerfile pattern — preserve any additional ENV vars, healthchecks, USER directives, etc. The key changes:
- WORKDIR is now `/app` (parent of the package), not `/app/hvac-scheduler/`.
- COPY puts the contents into `/app/hvac_scheduler/`.
- PYTHONPATH is `/app` so `hvac_scheduler` is importable as a top-level package.
- CMD is `python -m hvac_scheduler.app` (was `python app.py`).

Use the Edit tool to modify the Dockerfile precisely. Read it first, then update incrementally.

- [ ] **Step 3: Verify Dockerfile syntax**

```bash
docker --version
```

If Docker is available locally:
```bash
docker build -t hvac_scheduler:test deploy/energy-stack/hvac_scheduler/
```
Expected: build succeeds.

```bash
docker run --rm hvac_scheduler:test python -c "import hvac_scheduler.app; print('import ok')"
```
Expected: prints `import ok` (proves the package is importable in the container).

If Docker is not available locally, defer verification to the deploy step or the operator's local run.

- [ ] **Step 4: NO commit yet — combine with rename.**

---

### Task 17: Update docker-compose.yml

**Files:**
- Modify: `deploy/energy-stack/docker-compose.yml`

- [ ] **Step 1: Inspect the current entry**

```bash
grep -n "hvac-scheduler\|hvac_scheduler\|build:" deploy/energy-stack/docker-compose.yml | head -30
```

Find the scheduler's service block. Note the current `build:` context.

- [ ] **Step 2: Update only the build context path**

In `deploy/energy-stack/docker-compose.yml`, find the scheduler service block. Update its `build.context:` (or `build:` if it's a shorthand) from:
- `./hvac-scheduler` → `./hvac_scheduler`

**DO NOT** change:
- The service name (`hvac-scheduler:` as the YAML key STAYS hyphenated — operational identifier).
- Any environment variables that reference the service name.
- Any `depends_on` references.
- Any volume mounts that use the service name as an identifier.

- [ ] **Step 3: Verify the YAML still parses**

```bash
python -c "import yaml; yaml.safe_load(open('deploy/energy-stack/docker-compose.yml'))"
```
Expected: no output (valid YAML).

- [ ] **Step 4: NO commit yet.**

---

### Task 18: Update run_tests.sh services array

**Files:**
- Modify: `deploy/energy-stack/run_tests.sh`

- [ ] **Step 1: Inspect the current array**

```bash
grep -n "hvac-scheduler\|hvac_scheduler" deploy/energy-stack/run_tests.sh
```

Find the `services=(...)` array entry for the scheduler.

- [ ] **Step 2: Update the entry from hyphen to underscore**

In `deploy/energy-stack/run_tests.sh`, change the line:
- `    hvac-scheduler` → `    hvac_scheduler`

- [ ] **Step 3: Verify**

```bash
grep "hvac_scheduler" deploy/energy-stack/run_tests.sh
```
Expected: the entry is now underscore.

- [ ] **Step 4: NO commit yet.**

---

### Task 19: Update check-freshness-drift.yml path filter

**Files:**
- Modify: `.github/workflows/check-freshness-drift.yml`

- [ ] **Step 1: Inspect the path filter**

```bash
grep -n "hvac-scheduler\|hvac_scheduler" .github/workflows/check-freshness-drift.yml
```

Find the path triggers and any script-body references.

- [ ] **Step 2: Replace all hyphenated path occurrences with underscore**

Update every `deploy/energy-stack/hvac-scheduler/...` reference in this file to `deploy/energy-stack/hvac_scheduler/...`. Pay attention to:
- `paths:` trigger block (the path filter)
- Any inline bash script in the workflow that runs `python -m mypy <path>` or `md5sum <path>` — these need the underscore path.

- [ ] **Step 3: Validate the YAML**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/check-freshness-drift.yml'))"
```
Expected: no output.

- [ ] **Step 4: NO commit yet.**

---

### Task 20: Update pyproject.toml files list entry

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update the path**

In `pyproject.toml`, change the `files = [...]` entry:
- `"deploy/energy-stack/hvac-scheduler/freshness.py"` → `"deploy/energy-stack/hvac_scheduler/freshness.py"`

Leave `"tools/cockpit/backend/freshness.py"` unchanged (still in the list).

- [ ] **Step 2: NO commit yet.**

---

### Task 21: Update path references in markdown and shell scripts

**Files:**
- Modify: various markdown files in `docs/`, `AGENTS.md`, `README.md`, etc.

- [ ] **Step 1: Find all files referencing the old hyphenated path**

```bash
git grep -nl "deploy/energy-stack/hvac-scheduler/" -- ':!docs/archive/**' ':!*.json'
```

Expected: a list of markdown / shell / config files that reference the old path. Grafana JSON files are excluded (they reference the operational identifier, not the filesystem path).

- [ ] **Step 2: Update each match**

For each file in the list, replace `deploy/energy-stack/hvac-scheduler/` with `deploy/energy-stack/hvac_scheduler/`. Use the Edit tool with `replace_all=true` where appropriate.

CAUTION: do NOT update references to the SERVICE IDENTIFIER `hvac-scheduler` (in compose service names, log labels, Grafana queries, Influx tag values). Only update filesystem paths (those ending in `/`).

- [ ] **Step 3: NO commit yet.**

---

### Task 22: Local verification — tests pass, mypy passes

**Files:** None (verification only).

- [ ] **Step 1: Run scheduler tests**

```bash
cd deploy/energy-stack/hvac_scheduler/
python -m pytest .
```

Expected: tests pass (count should match the existing scheduler test count from main, probably ~337+ post-freshness-PR).

If tests fail with import errors, the conversion in Task 15 missed something. Triage:
- Look for the failing import line; ensure it's been converted to relative form.
- Check that monkeypatch / mock string references are prefixed with `hvac_scheduler.`.

- [ ] **Step 2: Run run_typecheck.sh**

```bash
cd /Users/christopherdepaola/Developer/energy-stack-1
bash deploy/energy-stack/run_typecheck.sh
```

Expected:
```
=== type-checking pyproject.toml files list ===
Success: no issues found in 2 source files
OK
```

The bootstrap pass type-checks both freshness modules (paths now reference underscore-renamed scheduler). Per-service loops are still empty (`service_dirs=()`); scheduler is NOT yet in the enforced set.

If mypy reports errors on `hvac_scheduler/freshness.py`, something in the rename broke. Diagnose and fix.

- [ ] **Step 3: Run full test stack**

```bash
cd /Users/christopherdepaola/Developer/energy-stack-1
bash deploy/energy-stack/run_tests.sh
```

Expected: scheduler tests pass; other services unchanged.

- [ ] **Step 4: Build and run the Docker container locally (if possible)**

```bash
docker compose -f deploy/energy-stack/docker-compose.yml build hvac-scheduler
docker compose -f deploy/energy-stack/docker-compose.yml up -d hvac-scheduler
sleep 5
docker compose -f deploy/energy-stack/docker-compose.yml logs hvac-scheduler | tail -20
docker compose -f deploy/energy-stack/docker-compose.yml down hvac-scheduler
```

Expected: container builds, starts, logs show the scheduler initializing (no `ModuleNotFoundError`).

If `docker compose` is unavailable locally, skip and rely on post-merge Pi-lab deploy.yml verification.

- [ ] **Step 5: NO commit yet.**

---

### Task 23: Rename verification (two greps)

**Files:** None (verification only).

- [ ] **Step 1: Positive grep — operational identifier preserved**

```bash
grep -rn "hvac-scheduler" tools/cockpit deploy/energy-stack/grafana deploy/energy-stack/telegraf deploy/energy-stack/docker-compose.yml deploy/energy-stack/promtail 2>/dev/null | head -20
```

Expected: the hyphenated identifier `hvac-scheduler` still appears in operational config (Grafana JSON dashboard references, Telegraf labels, log labels in promtail, docker-compose service name). If any expected operational reference is MISSING, you accidentally renamed something that should have stayed hyphenated — restore it.

- [ ] **Step 2: Negative grep — no stale filesystem paths remain**

```bash
git grep -nE "deploy/energy-stack/hvac-scheduler/" -- ':!*.json' ':!docs/archive/**'
```

Expected: ZERO matches outside of archive docs and Grafana JSON. Any non-archive non-JSON match is a stale path reference that needs updating.

If matches appear, fix them (update path to underscore form). Re-run the negative grep until it returns empty.

- [ ] **Step 3: NO commit yet.**

---

### Task 24: Commit the rename + package conversion

**Files:** All changes from Tasks 13-21.

- [ ] **Step 1: Stage all changes**

```bash
git add -A
```

- [ ] **Step 2: Verify the staged diff**

```bash
git status
git diff --cached --stat
```

Expected: the rename + path updates show as a coherent set of changes:
- `R` (rename) entries for every file in `deploy/energy-stack/hvac-scheduler/` → `hvac_scheduler/`
- `A` for the new `__init__.py`
- `M` for the path-updated files (Dockerfile, compose, run_tests.sh, freshness-drift workflow, pyproject.toml, various docs)

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(type-checker): rename hvac-scheduler to hvac_scheduler (PR 2)

Phase 2a of the type-checker rollout. Converts the scheduler directory
to a proper Python package with full-package model:
- git mv hvac-scheduler → hvac_scheduler (preserves history)
- Add empty __init__.py
- Convert all sibling imports to relative form (from .freshness import X)
- Update Dockerfile WORKDIR /app, PYTHONPATH=/app, CMD `python -m hvac_scheduler.app`
- Update docker-compose build.context (service name stays `hvac-scheduler`)
- Update run_tests.sh services array
- Update check-freshness-drift.yml path filter
- Update pyproject.toml files-list path
- Update markdown/shell references to the filesystem path

The scheduler is NOT yet in the enforced set (service_dirs is still
empty). PR 3 (Phase 2b) adds it and lands the first typed adapter.

Verified locally:
- pytest in deploy/energy-stack/hvac_scheduler/ passes
- bash deploy/energy-stack/run_typecheck.sh passes (bootstrap covers
  the moved freshness.py)
- docker compose build hvac-scheduler succeeds (if local Docker
  available)
- Operational identifier `hvac-scheduler` still appears in compose
  service-name, Grafana, Influx, Telegraf, promtail
- No stale `deploy/energy-stack/hvac-scheduler/` path references
  remain outside archive docs and Grafana JSON
EOF
)"
```

- [ ] **Step 4: Push branch + open PR 2**

```bash
git push -u origin feat/type-checker-scheduler-rename

gh pr create --base main --title "feat(type-checker): rename hvac-scheduler to hvac_scheduler package (PR 2)" --body "$(cat <<'EOF'
## Summary

PR 2 of the type-checker rollout. Phase 2a from spec §7.

Renames the scheduler directory to a valid Python package identifier and converts to full-package model. **No type-checker enforcement is added in this PR** — that happens in PR 3.

Spec: \`docs/superpowers/specs/2026-05-20-type-checker-design.md\`
Plan: \`docs/plans/type-checker-plan.md\` (Task 12-24)

## Acceptance criteria (per spec §7 Phase 2a)

- [x] All scheduler tests pass via \`cd deploy/energy-stack/hvac_scheduler/ && python -m pytest .\`
- [x] \`bash deploy/energy-stack/run_tests.sh\` succeeds
- [x] \`bash deploy/energy-stack/run_typecheck.sh\` still passes (freshness.py path-updated entry still type-checks)
- [ ] \`docker compose up -d hvac-scheduler\` succeeds (verified via post-merge deploy.yml run on Pi-lab if not locally)
- [x] Positive grep: operational identifier \`hvac-scheduler\` still in compose service-name, Grafana JSON, Influx, Telegraf, promtail
- [x] Negative grep: no stale \`deploy/energy-stack/hvac-scheduler/\` filesystem-path references outside archive docs and Grafana JSON

## Test plan

- [x] pytest run in renamed dir
- [x] run_tests.sh full-stack
- [x] run_typecheck.sh
- [ ] Docker container builds and starts post-merge

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Report the PR URL. Operator merges in the UI.

---

## Phase 3: Scheduler Enforcement + Adapter (PR 3)

**Goal:** Add `hvac_scheduler` to the enforced set with `mypy --strict` passing. Land the first typed adapter (`influx_adapter.py`) wrapping `influxdb_client` record API. Add import-linter contract banning direct `influxdb_client` usage outside the adapter. Triage all mypy findings; fix real bugs.

This is the largest single PR in the rollout. Per spec §8.7, if PR 3 itself balloons (>500 net diff lines), it can be split into PR 3a (adapter + refactor) and PR 3b (remaining type-fixes). The greenfield invariant holds: scheduler is not in `service_dirs` until mypy + import-linter both pass.

**Files created in this phase:**
- `deploy/energy-stack/hvac_scheduler/influx_adapter.py` (the typed adapter)
- `deploy/energy-stack/hvac_scheduler/test_influx_adapter.py` (adapter unit tests)

**Files modified in this phase:**
- `deploy/energy-stack/hvac_scheduler/app.py`: `fetch_latest_comed` and other `record.get_*()` callers refactored to use the adapter
- `deploy/energy-stack/hvac_scheduler/Dockerfile`: COPY line adds `influx_adapter.py`
- `deploy/energy-stack/run_typecheck.sh`: `service_dirs` adds `hvac_scheduler`
- `pyproject.toml`: removes scheduler's `freshness.py` from `files`; adds `hvac_scheduler` to import-linter `root_packages`; adds the `forbidden` contract; adds scheduler test modules to the test-relaxation override
- Various scheduler `.py` files: annotation additions, bug fixes from mypy triage

---

### Task 25: Branch from main

- [ ] **Step 1: Verify PR 2 merged**

```bash
git checkout main
git pull --ff-only origin main
ls deploy/energy-stack/hvac_scheduler/
```
Expected: `hvac_scheduler` dir exists (PR 2 merged); `hvac-scheduler` does not.

If `hvac-scheduler` still exists, PR 2 hasn't merged. STOP.

- [ ] **Step 2: Create the PR 3 branch**

```bash
git checkout -b feat/type-checker-scheduler-enforce
```

---

### Task 26: Write adapter unit tests (TDD)

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/test_influx_adapter.py`

- [ ] **Step 1: Write the failing tests first**

Create the test file with cases that exercise the adapter's intended behavior. The adapter should:
- Project an influxdb_client record into a typed `TypedRecord` dataclass
- Surface explicit errors for missing required fields
- Preserve the `datetime` for `time_utc` (timezone-aware, UTC)

Example test content:

```python
"""Tests for influx_adapter.py — typed projection layer over influxdb_client records."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from .influx_adapter import TypedRecord, project_record


class TestProjectRecord:
    def test_projects_a_complete_record(self):
        """Happy path: record has all fields; project_record returns a TypedRecord."""
        raw = MagicMock()
        raw.get_value.return_value = 12.5
        raw.get_time.return_value = datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        result = project_record(raw)

        assert isinstance(result, TypedRecord)
        assert result.value == 12.5
        assert result.time_utc == datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        assert result.field == "price_cents_per_kwh"
        assert result.measurement == "comed.prices"

    def test_raises_when_value_is_None(self):
        """If get_value() returns None, project_record raises ValueError."""
        raw = MagicMock()
        raw.get_value.return_value = None
        raw.get_time.return_value = datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        with pytest.raises(ValueError, match="value"):
            project_record(raw)

    def test_raises_when_time_is_None(self):
        """If get_time() returns None, project_record raises ValueError."""
        raw = MagicMock()
        raw.get_value.return_value = 12.5
        raw.get_time.return_value = None
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        with pytest.raises(ValueError, match="time"):
            project_record(raw)

    def test_coerces_naive_datetime_to_utc(self):
        """If get_time() returns a naive datetime, project_record assumes UTC and tags it."""
        raw = MagicMock()
        raw.get_value.return_value = 12.5
        raw.get_time.return_value = datetime(2026, 5, 20, 18, 0)  # naive
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        result = project_record(raw)

        assert result.time_utc.tzinfo == timezone.utc

    def test_value_is_coerced_to_float(self):
        """If get_value() returns an int, project_record coerces to float."""
        raw = MagicMock()
        raw.get_value.return_value = 12  # int, not float
        raw.get_time.return_value = datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
        raw.get_field.return_value = "price_cents_per_kwh"
        raw.get_measurement.return_value = "comed.prices"

        result = project_record(raw)

        assert isinstance(result.value, float)
        assert result.value == 12.0


class TestTypedRecord:
    def test_is_frozen(self):
        """TypedRecord is immutable."""
        rec = TypedRecord(
            value=12.5,
            time_utc=datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc),
            field="price_cents_per_kwh",
            measurement="comed.prices",
        )
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            rec.value = 13.0  # type: ignore[misc]
```

Use the Write tool to create this file.

- [ ] **Step 2: Run the tests to verify they FAIL (RED phase of TDD)**

```bash
cd deploy/energy-stack/hvac_scheduler/
python -m pytest test_influx_adapter.py -v
```

Expected: tests fail with `ImportError: cannot import name 'project_record'` or similar — because `influx_adapter.py` doesn't exist yet.

- [ ] **Step 3: NO commit yet — adapter implementation in next task.**

---

### Task 27: Implement influx_adapter.py to make tests pass

**Files:**
- Create: `deploy/energy-stack/hvac_scheduler/influx_adapter.py`

- [ ] **Step 1: Write the adapter**

```python
"""Typed adapter around influxdb_client record/query results.

The hvac-scheduler reads ComEd prices and other measurements from
Influx via the influxdb_client library. The library's `record` API
(`get_value()`, `get_time()`, `get_field()`, `get_measurement()`) is
weakly typed — every method returns `Any`. The 2026-05-19 19:18Z
freshness bug was directly enabled by this: nothing checked whether
`get_value()` returned a float or None, and nothing checked whether
the float's timestamp was current.

This module is the TYPED PROJECTION SURFACE. Code throughout the
scheduler imports `from .influx_adapter import project_record` (or
`from hvac_scheduler.influx_adapter import project_record` from
outside the package) rather than touching the raw library directly.

The adapter is enforced via an import-linter contract in
`pyproject.toml`: direct `from influxdb_client import ...` anywhere
under `hvac_scheduler` other than this module is a CI failure. See
spec §5.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class TypedRecord:
    """Typed projection of an influxdb_client Record.

    All fields are validated and non-None at construction time. Callers
    receive a TypedRecord or an exception, NEVER a partial / None-valued
    record."""

    value: float
    time_utc: datetime
    field: str
    measurement: str


def project_record(record: Any) -> TypedRecord:
    """Project a raw influxdb_client.Record into a TypedRecord.

    Reads `get_value()`, `get_time()`, `get_field()`, `get_measurement()`
    from the raw record, validates each, and returns a frozen dataclass.

    Raises ValueError if any required field is missing (None) on the
    record. The caller is responsible for translating this into a
    domain-appropriate error (e.g., the scheduler may treat a missing
    value as `freshness="missing"`)."""

    value_raw = record.get_value()
    if value_raw is None:
        raise ValueError("influx record has no value")

    time_raw = record.get_time()
    if time_raw is None:
        raise ValueError("influx record has no time")

    if not isinstance(time_raw, datetime):
        raise ValueError(f"influx record time is not a datetime: {type(time_raw)}")

    # Influx client returns timezone-naive datetimes in some versions;
    # we treat them as UTC because that's what the bucket _time field
    # canonically is.
    if time_raw.tzinfo is None:
        time_utc = time_raw.replace(tzinfo=timezone.utc)
    else:
        time_utc = time_raw.astimezone(timezone.utc)

    field_raw = record.get_field()
    if field_raw is None:
        raise ValueError("influx record has no field")

    measurement_raw = record.get_measurement()
    if measurement_raw is None:
        raise ValueError("influx record has no measurement")

    return TypedRecord(
        value=float(value_raw),
        time_utc=time_utc,
        field=str(field_raw),
        measurement=str(measurement_raw),
    )
```

Use the Write tool.

- [ ] **Step 2: Run tests to verify they PASS (GREEN)**

```bash
cd deploy/energy-stack/hvac_scheduler/
python -m pytest test_influx_adapter.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit (adapter + tests together)**

```bash
git add deploy/energy-stack/hvac_scheduler/influx_adapter.py deploy/energy-stack/hvac_scheduler/test_influx_adapter.py
git commit -m "feat(type-checker): add influx_adapter.py — typed projection over influxdb_client records

The 2026-05-19 19:18Z freshness bug was directly enabled by
record.get_*() returning Any. This adapter projects a raw record
into a frozen TypedRecord dataclass with validated, typed fields.

Callers throughout the scheduler should import from this module
rather than touching influxdb_client directly. The import-linter
contract added in a later commit will enforce this boundary."
```

---

### Task 28: Refactor `fetch_latest_comed` to use the adapter

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/app.py`

- [ ] **Step 1: Locate fetch_latest_comed**

```bash
grep -n "def fetch_latest_comed" deploy/energy-stack/hvac_scheduler/app.py
```

Read the surrounding ~50 lines via the Read tool.

- [ ] **Step 2: Refactor to use project_record**

The current implementation likely accesses `record.get_value()` and `record.get_time()` directly. Refactor to:

```python
from .influx_adapter import project_record, TypedRecord

def fetch_latest_comed(query_api: Any, bucket: str, *, now_utc: datetime) -> Optional[PriceSample]:
    """[existing docstring...]"""
    tables = query_api.query(fq_latest_comed_5min(bucket))
    for table in tables:
        for raw_record in table.records:
            try:
                rec = project_record(raw_record)
            except ValueError:
                # No valid record; treat as missing
                return None

            cents = rec.value
            source_ts = rec.time_utc
            age_sec = (now_utc - source_ts).total_seconds()
            freshness = classify(age_sec, "comed.prices")
            return PriceSample(
                cents_per_kwh=cents,
                source_ts=source_ts,
                freshness=freshness,
            )
    return None
```

Adapt the above to the actual existing function signature. Key changes:
- Add `from .influx_adapter import project_record, TypedRecord` near the top of `app.py` (relative import).
- Replace direct `record.get_value()` / `record.get_time()` calls with `project_record(raw_record)`.
- Use `rec.value` and `rec.time_utc` instead of the raw library calls.
- Drop any manual None-checking on get_value() — the adapter handles it.

- [ ] **Step 3: Run scheduler tests**

```bash
cd deploy/energy-stack/hvac_scheduler/
python -m pytest .
```

Expected: all tests pass. If any fail because they mocked `record.get_value()` directly (rather than mocking the function), the tests need updating. Convert any mock-the-raw-library tests to mock the adapter:
```python
monkeypatch.setattr("hvac_scheduler.influx_adapter.project_record", lambda r: TypedRecord(value=..., time_utc=..., field=..., measurement=...))
```

- [ ] **Step 4: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/app.py
git commit -m "refactor(type-checker): route fetch_latest_comed through influx_adapter

The function now imports project_record from .influx_adapter and uses
the typed TypedRecord instead of accessing record.get_value() /
record.get_time() directly. Tests that mocked the raw library calls
have been updated to mock the adapter where applicable."
```

---

### Task 29: Refactor other `record.get_*()` callers

**Files:**
- Modify: any other functions in `deploy/energy-stack/hvac_scheduler/` that call `record.get_value()`, `record.get_time()`, etc.

- [ ] **Step 1: Find all remaining callers**

```bash
grep -rn "record\.get_value\|record\.get_time\|record\.get_field\|record\.get_measurement" deploy/energy-stack/hvac_scheduler/
```

Inspect each match. Categorize:
- In `influx_adapter.py` itself: KEEP (this is the only allowed direct usage).
- In `test_influx_adapter.py`: KEEP (tests of the adapter use the raw API to set up mocks).
- Elsewhere: REFACTOR to use `project_record()`.

- [ ] **Step 2: Refactor each non-adapter caller**

For each remaining caller (likely `revisit_today_decision`, `run_decision`, `fetch_today_decision` per the STALE_DATA_HANDOFF.md context), apply the same transform as Task 28: replace direct `record.get_*()` calls with `project_record()` returning a `TypedRecord`, then use `.value`, `.time_utc`, `.field`, `.measurement` as needed.

- [ ] **Step 3: Run tests**

```bash
cd deploy/energy-stack/hvac_scheduler/
python -m pytest .
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add deploy/energy-stack/hvac_scheduler/
git commit -m "refactor(type-checker): route remaining record.get_*() callers through influx_adapter

[List the functions touched.] All non-adapter scheduler code now
uses project_record() / TypedRecord. The next commit adds the
import-linter contract that makes this boundary CI-enforced."
```

---

### Task 30: Add the import-linter contract

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update [tool.importlinter]**

In `pyproject.toml`, update the import-linter block. Change `root_packages = []` to `root_packages = ["hvac_scheduler"]` and append a contract:

```toml
[tool.importlinter]
root_packages = ["hvac_scheduler"]
include_external_packages = true

[[tool.importlinter.contracts]]
name = "Only influx_adapter may import influxdb_client"
type = "forbidden"
source_modules = ["hvac_scheduler"]
forbidden_modules = ["influxdb_client"]
ignore_imports = [
    "hvac_scheduler.influx_adapter -> influxdb_client",
]
```

Use the Edit tool to make this change.

- [ ] **Step 2: Run import-linter locally to verify**

```bash
cd /Users/christopherdepaola/Developer/energy-stack-1
PYTHONPATH=deploy/energy-stack lint-imports --config pyproject.toml
```

Expected: contracts pass (zero violations) because we just refactored all the non-adapter callers in Tasks 28-29.

If violations appear, the refactor missed some callers. Identify each violation, refactor it to use `project_record()`, re-run.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat(type-checker): add import-linter contract banning direct influxdb_client outside influx_adapter

Closes the convention loophole: any future code that adds
\`from influxdb_client import ...\` outside influx_adapter.py
fails CI. The contract is enforced by lint-imports invoked from
run_typecheck.sh."
```

---

### Task 31: Add scheduler to service_dirs and remove its freshness.py from files list

**Files:**
- Modify: `deploy/energy-stack/run_typecheck.sh`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update run_typecheck.sh**

In `deploy/energy-stack/run_typecheck.sh`, add `hvac_scheduler` to `service_dirs`:

```bash
service_dirs=(
    hvac_scheduler
)
```

- [ ] **Step 2: Update pyproject.toml files list**

In `pyproject.toml`, remove the scheduler entry from `files`:

```toml
files = [
    "tools/cockpit/backend/freshness.py",
]
```

(Only cockpit's freshness remains; cockpit migrates in PR 4 and removes its own entry.)

- [ ] **Step 3: Run the full type-check end-to-end**

```bash
bash deploy/energy-stack/run_typecheck.sh
```

Expected: NOW the scheduler is enforced. Mypy will likely surface MANY findings on the first run. The next tasks (32, 33) triage and fix them.

DO NOT proceed to step 4 until mypy is fully clean.

- [ ] **Step 4: NO commit yet — finish the mypy triage first.**

---

### Task 32: Add scheduler test modules to the test-relaxation override

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: List the scheduler test module names (check both root and tests/ subdir)**

```bash
ls deploy/energy-stack/hvac_scheduler/test_*.py deploy/energy-stack/hvac_scheduler/conftest.py 2>/dev/null
ls deploy/energy-stack/hvac_scheduler/tests/test_*.py deploy/energy-stack/hvac_scheduler/tests/conftest.py 2>/dev/null
```

Convert each to its dotted module form:
- Root-level: `hvac_scheduler.test_<name>`, `hvac_scheduler.conftest`
- Sub-package `tests/`: `hvac_scheduler.tests.test_<name>`, `hvac_scheduler.tests.conftest` (only if a `tests/__init__.py` exists; otherwise pytest will discover them but mypy may not — check)

Note: the scheduler currently has `tests/fixtures/` (data only, no Python files). If `tests/` ever gains Python files, the dotted form changes accordingly.

- [ ] **Step 2: Update the override block in pyproject.toml**

In `pyproject.toml`, find the test-relaxation `[[tool.mypy.overrides]]` block with `disallow_untyped_defs = false`. Add the scheduler's test modules to the `module` list:

```toml
[[tool.mypy.overrides]]
module = [
    "hvac_scheduler.test_hvac_scheduler",
    "hvac_scheduler.test_freshness",
    "hvac_scheduler.test_decision_trace",
    "hvac_scheduler.test_influx_adapter",
    "hvac_scheduler.test_integration_2025_replay",
    "hvac_scheduler.test_pjm_5cp",
    "hvac_scheduler.test_precool",
    "hvac_scheduler.test_price_overlay",
    "hvac_scheduler.conftest",
    # add others discovered by Step 1
]
disallow_untyped_defs = false
disallow_untyped_decorators = false
```

(Verify the actual list from `ls`; adjust as needed.)

- [ ] **Step 3: Re-run mypy**

```bash
bash deploy/energy-stack/run_typecheck.sh
```

Production-code findings should remain (those need actual fixes). Test-code findings on `disallow_untyped_defs` and `disallow_untyped_decorators` should disappear.

- [ ] **Step 4: NO commit yet — triage first.**

---

### Task 33: Triage and fix mypy findings on scheduler production code

**Files:**
- Modify: various `.py` files in `deploy/energy-stack/hvac_scheduler/`

- [ ] **Step 1: Run mypy and capture findings**

```bash
bash deploy/energy-stack/run_typecheck.sh 2>&1 | tee /tmp/mypy-scheduler-findings.txt
```

Inspect the output. Count and categorize findings.

- [ ] **Step 2: Triage each finding**

For each mypy error, decide:
- **Real bug** (e.g., missing None-check, wrong return type, dead code): fix in the codebase. Add an anti-regression test where applicable. List in the PR description as "bug found by mypy."
- **Missing annotation** (function not annotated but logic is correct): add the annotation.
- **Untyped library call** (a library other than influxdb_client surfaces as Any): add a `[[tool.mypy.overrides]]` block in pyproject.toml for that library, OR add a targeted `# type: ignore[<code>]` annotation with a comment explaining why.
- **Mypy-config gap** (e.g., a strict check that doesn't apply here): relax the specific code in a per-module override.

The expected bug-finding focus per the spec: the `query_api` parameter is untyped across 10+ scheduler functions. Add annotations:
- `query_api: Any` if we accept the library's untyped surface (with a comment referencing the influx_adapter for typed projection)
- `query_api: "QueryApi"` from `influxdb_client.client.query_api import QueryApi` if the stub is available

- [ ] **Step 3: Iterate to clean state, with bounded effort**

After fixes, re-run:
```bash
bash deploy/energy-stack/run_typecheck.sh
```

**Bounded iteration discipline:**
- **Maximum 5 round-trips of fix → re-run.** If after 5 rounds there are remaining errors you cannot resolve without a `# type: ignore` annotation, STOP and escalate to the operator. Report each unresolved error with: file:line, the mypy error message, your attempted fix, and why the fix didn't work.
- **`# type: ignore` requires a comment citing the reason.** Bare `# type: ignore` is NOT acceptable. Example acceptable form: `# type: ignore[assignment]  # upstream stub mismatches our usage of QueryApi.query() — see backlog`. Each `# type: ignore` added becomes a small entry in `docs/type-debt-backlog.md` so it doesn't rot.
- **Don't add `# type: ignore` to silence a real bug.** If the error points at a logic issue (None-handling missing, wrong return type), FIX the logic, don't ignore.
- **Re-state in PR description:** if any `# type: ignore` annotations are added, list each one in the PR description under a "Tactical ignores added" subsection, with the rationale.

- [ ] **Step 4: Commit incrementally**

Make small commits as you go, one per logical chunk of fixes:
```bash
git add <files>
git commit -m "fix(type-checker): annotate query_api parameter in scheduler functions

Closes the most direct descendant of the 19:18Z bug class: mypy now
verifies every caller of fetch_latest_comed et al. passes a valid
query_api object."
```

```bash
git add <files>
git commit -m "fix(scheduler): real bug found by mypy — [file:line] [description]

[Detailed description of the bug, root cause, and fix. Reference the
mypy error message that surfaced it.]"
```

Aim for one commit per real bug fix; bundle annotation-only changes into broader commits.

- [ ] **Step 5: Verify final clean state**

```bash
bash deploy/energy-stack/run_typecheck.sh
```

Expected:
```
=== type-checking pyproject.toml files list ===
Success: no issues found in 1 source file
=== type-checking hvac_scheduler ===
Success: no issues found in N source files
=== checking import-linter contracts ===
Contracts: 1 kept.
OK
```

(N depends on scheduler file count.)

---

### Task 34: Update Dockerfile COPY line to include influx_adapter.py

**Files:**
- Modify: `deploy/energy-stack/hvac_scheduler/Dockerfile`

- [ ] **Step 1: Inspect the existing Dockerfile**

```bash
cat deploy/energy-stack/hvac_scheduler/Dockerfile
```

If the Dockerfile uses `COPY . /app/hvac_scheduler/` (copy everything), no change needed — `influx_adapter.py` and `test_influx_adapter.py` are copied automatically. Skip remaining steps.

If the Dockerfile uses explicit per-file COPY (e.g., `COPY app.py freshness.py /app/hvac_scheduler/`), add `influx_adapter.py` to that list.

- [ ] **Step 2: Update**

Example:
```dockerfile
COPY app.py freshness.py price_overlay.py decision_codes.py pjm_5cp.py influx_adapter.py /app/hvac_scheduler/
```

- [ ] **Step 3: Verify container builds (if Docker available locally)**

```bash
docker compose -f deploy/energy-stack/docker-compose.yml build hvac-scheduler
docker compose -f deploy/energy-stack/docker-compose.yml run --rm hvac-scheduler python -c "from hvac_scheduler.influx_adapter import project_record; print('adapter importable')"
```

Expected: prints `adapter importable`.

- [ ] **Step 4: Commit (if changes made)**

```bash
git add deploy/energy-stack/hvac_scheduler/Dockerfile
git commit -m "chore(type-checker): add influx_adapter.py to Dockerfile COPY list"
```

If no change was needed (COPY . pattern), skip.

---

### Task 35: Push branch and open PR 3

**Files:** None.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/type-checker-scheduler-enforce
```

- [ ] **Step 2: Open PR 3**

```bash
gh pr create --base main --title "feat(type-checker): scheduler enforced + first typed adapter (PR 3)" --body "$(cat <<'EOF'
## Summary

PR 3 of the type-checker rollout. Phase 2b from spec §7.

Adds the scheduler to the enforced set with \`mypy --strict\` passing. Lands the first typed adapter (\`influx_adapter.py\`) wrapping \`influxdb_client\` record API. Adds the import-linter contract banning direct \`influxdb_client\` use outside the adapter.

Spec: \`docs/superpowers/specs/2026-05-20-type-checker-design.md\`
Plan: \`docs/plans/type-checker-plan.md\` (Task 25-35)

## What's in this PR

- \`hvac_scheduler/influx_adapter.py\` (NEW): typed projection layer over influxdb_client records. \`project_record(raw) -> TypedRecord\`.
- \`hvac_scheduler/test_influx_adapter.py\` (NEW): adapter unit tests.
- \`hvac_scheduler/app.py\` and other modules: refactored to use \`project_record()\` instead of direct \`record.get_value()\` / \`get_time()\` calls.
- \`pyproject.toml\`: \`hvac_scheduler\` added to import-linter \`root_packages\`; \`forbidden\` contract added; scheduler test modules added to test-relaxation override; scheduler's \`freshness.py\` removed from \`files\` list.
- \`run_typecheck.sh\`: \`hvac_scheduler\` added to \`service_dirs\`.
- Scheduler annotations added; real bugs fixed (see commit history).

## Acceptance criteria (per spec §7 Phase 2b)

- [x] \`bash deploy/energy-stack/run_typecheck.sh\` returns 0 with \`service_dirs=(hvac_scheduler)\`.
- [x] \`lint-imports --config pyproject.toml\` returns zero violations against the scheduler's \`forbidden\` contract for \`influxdb_client\`.
- [x] All scheduler tests pass.
- [x] The \`query_api\` parameter is typed across all scheduler functions where it previously lacked an annotation.
- [x] Real bugs found in mypy triage are fixed in-PR with anti-regression tests where applicable. See commit history for each "fix(scheduler):" commit.

## Real bugs found by mypy

[List each real bug found during Task 33 triage. Format: file:line — description.]

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Report the PR URL.

---

## Phase 4: Cockpit-Backend Enforcement + Bug Fix (PR 4)

**Goal:** Bring `tools/cockpit/backend` into the enforced set. Fix the deferred `price_is_stale` consumer bug from the freshness PR (cockpit's `snapshot.py` still reads the old field name). Convert any remaining script-style imports to package form.

cockpit-backend is already a regular Python package (has `__init__.py`). The migration is annotation + bug-fixes + adding it to `repo_targets`.

**Files modified in this phase:**
- `tools/cockpit/backend/snapshot.py`: fix the `price_is_stale` consumer
- Various cockpit-backend `.py` files: annotation additions, mypy fixes
- `deploy/energy-stack/run_typecheck.sh`: `repo_targets` adds `tools/cockpit/backend`
- `pyproject.toml`: removes cockpit's `freshness.py` from `files` list; adds cockpit test modules to the test-relaxation override

---

### Task 36: Branch from main

- [ ] **Step 1: Sync main**

```bash
git checkout main
git pull --ff-only origin main
```

Verify PR 3 merged (the scheduler is enforced).

- [ ] **Step 2: Create the PR 4 branch**

```bash
git checkout -b feat/type-checker-cockpit-enforce
```

---

### Task 37: Fix the deferred price_is_stale consumer bug

**Files:**
- Modify: `tools/cockpit/backend/snapshot.py`

- [ ] **Step 1: Find the broken consumer**

```bash
grep -n "price_is_stale" tools/cockpit/backend/snapshot.py
```

Expected: `po.get("price_is_stale")` or similar — reading a field name the scheduler no longer emits.

- [ ] **Step 2: Identify what to read instead**

The freshness PR renamed `price_is_stale` to `price_feed_unavailable`. The cockpit's snapshot.py likely needs to:
- Read `price_feed_unavailable` instead, OR
- Read `bucket_age_sec` and compute its own staleness, OR
- Read both and use the appropriate one for the cockpit's UI logic.

Inspect the scheduler's current trace emission for the canonical field names:
```bash
grep -n "price_feed_unavailable\|bucket_age_sec\|price_overlay_eval" deploy/energy-stack/hvac_scheduler/app.py
```

Decide based on what the cockpit UI needs. The simplest fix: read `bucket_age_sec` and compute staleness using the same thresholds as `hvac_scheduler/freshness.py` (and `tools/cockpit/backend/freshness.py` — they're byte-identical per the drift-check workflow).

- [ ] **Step 3: Apply the fix**

Use Edit to update the snapshot consumer. Add a unit test if reasonable.

- [ ] **Step 4: Commit**

```bash
git add tools/cockpit/backend/snapshot.py
git commit -m "fix(cockpit): replace stale price_is_stale consumer with bucket_age_sec read

The freshness PR renamed the scheduler's trace field from
price_is_stale to price_feed_unavailable (and added bucket_age_sec).
The cockpit's snapshot.py was left reading the old field — silently
returning the default value on every tick since the merge.

Replaces the consumer with bucket_age_sec + the same freshness
classifier the scheduler uses, restoring operator visibility into
data age."
```

---

### Task 38: Close cockpit namespace-package gap + convert any remaining script-style imports

**Files:**
- Create: `tools/cockpit/__init__.py` (empty)
- Modify: `tools/cockpit/backend/*.py` (if any have non-package-form imports)

- [ ] **Step 1: Verify the namespace gap**

Filesystem audit (run before editing):
```bash
ls -la tools/__init__.py tools/cockpit/__init__.py tools/cockpit/backend/__init__.py
```

Expected state BEFORE this step: `tools/__init__.py` and `tools/cockpit/backend/__init__.py` present; `tools/cockpit/__init__.py` ABSENT. This is the namespace-package gap (per spec §3.1 rev 6 audit and §6.3 PR 4 note). `tools/cockpit` is currently a PEP 420 namespace package, which import-linter's grimp backend rejects.

- [ ] **Step 2: Add the missing `__init__.py`**

```bash
touch tools/cockpit/__init__.py
```

Verify it was created (and is empty):
```bash
ls -la tools/cockpit/__init__.py
wc -c tools/cockpit/__init__.py    # expect 0 bytes
```

- [ ] **Step 3: Audit cockpit-backend imports**

```bash
grep -rnE "^from [a-z0-9_]+ import|^import [a-z0-9_]+$" tools/cockpit/backend/*.py
```

For each match:
- Stdlib / external library: SKIP.
- Sibling module in `tools/cockpit/backend/` (e.g., `freshness.py`, `influx.py`, `loki.py`): should already be `from tools.cockpit.backend.X import Y` (absolute form). If it's not, convert.

Most cockpit-backend imports are already absolute form (the backend directory has always had its own `__init__.py`). This step may be a no-op apart from Step 2's new file.

- [ ] **Step 4: Verify mypy can resolve the package chain**

```bash
python -c "import tools.cockpit.backend; print(tools.cockpit.backend.__file__)"
```

Expected: prints the `__init__.py` path for `tools/cockpit/backend/__init__.py`. If this fails, something else is wrong — investigate before continuing.

- [ ] **Step 5: Stage but do NOT commit yet** — commit happens after Task 39's pyproject + run_typecheck.sh edits land. Combined commit:

```bash
git add tools/cockpit/__init__.py tools/cockpit/backend/
git commit -m "refactor(cockpit): close namespace-package gap + normalize imports

Adds the missing tools/cockpit/__init__.py so the full
tools.cockpit.backend.* chain resolves as regular packages, not
via PEP 420 namespace traversal. Required by import-linter's grimp
backend (which rejects namespace packages). Audit during plan rev 6
found tools/__init__.py and tools/cockpit/backend/__init__.py
present but tools/cockpit/__init__.py missing.

Also converts any remaining script-style sibling imports in
cockpit-backend to absolute form (most were already correct)."
```

(If Task 39's edits happen in the same PR, defer this commit until Task 39 step 5 and combine.)

---

### Task 39: Add cockpit-backend to repo_targets

**Files:**
- Modify: `deploy/energy-stack/run_typecheck.sh`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update run_typecheck.sh**

In `deploy/energy-stack/run_typecheck.sh`, add `tools/cockpit/backend` to `repo_targets`:

```bash
repo_targets=(
    tools/cockpit/backend
)
```

- [ ] **Step 2: Remove cockpit's freshness.py from pyproject.toml files**

In `pyproject.toml`:
```toml
files = []
```

(Now empty — both freshness modules are covered by per-service / repo_targets invocations.)

Alternatively, REMOVE the `files = []` line entirely. The bootstrap-pass guard in `run_typecheck.sh` will skip it either way (tomllib check returns False for empty list AND for missing key).

For cleanliness, leave the `files = []` line with a comment explaining its lifecycle:

```toml
# files: empty after Phase 2b (PR 3) + Phase 3 (PR 4). All previously-
# bootstrap-covered freshness modules now covered by their respective
# service's per-service invocation. The Phase 6 (PR 15) cleanup PR
# removes this key entirely.
files = []
```

- [ ] **Step 3: Add cockpit test modules to the test-relaxation override**

```bash
# Check both root and tests/ subdir for cockpit-backend
ls tools/cockpit/backend/test_*.py tools/cockpit/backend/conftest.py 2>/dev/null
ls tools/cockpit/backend/tests/test_*.py tools/cockpit/backend/tests/conftest.py 2>/dev/null
```

Cockpit-backend tests are most likely in `tools/cockpit/backend/tests/`. Verify which path actually contains files.

Add the test module names to the `[[tool.mypy.overrides]]` block in `pyproject.toml`, using the correct dotted form:
- Root-level tests: `tools.cockpit.backend.test_<name>`, `tools.cockpit.backend.conftest`
- `tests/` sub-package: `tools.cockpit.backend.tests.test_<name>`, `tools.cockpit.backend.tests.conftest` (requires `tools/cockpit/backend/tests/__init__.py` to exist — confirm by `ls tools/cockpit/backend/tests/__init__.py`)

- [ ] **Step 4: Run mypy + import-linter**

```bash
bash deploy/energy-stack/run_typecheck.sh
```

Mypy may surface findings in cockpit-backend. The bootstrap pass is now skipped (files = []); per-service runs the scheduler; repo_targets runs cockpit-backend; import-linter checks scheduler contracts.

Triage findings per Task 33's pattern.

- [ ] **Step 5: Iterate to clean state.**

- [ ] **Step 6: NO commit yet — finish triage first.**

---

### Task 40: Triage and fix cockpit mypy findings

**Files:**
- Modify: various `.py` files in `tools/cockpit/backend/`

- [ ] **Step 1: Run mypy and capture findings**

```bash
bash deploy/energy-stack/run_typecheck.sh 2>&1 | tee /tmp/mypy-cockpit-findings.txt
```

- [ ] **Step 2: Triage and fix**

Same pattern as Task 33. Fix bugs, add annotations, document any tactical ignores.

- [ ] **Step 3: Commit incrementally**

```bash
git add <files>
git commit -m "fix(cockpit): [description]"
```

- [ ] **Step 4: Final verification**

```bash
bash deploy/energy-stack/run_typecheck.sh
```

Expected: clean, returns 0.

---

### Task 41: Commit + open PR 4

- [ ] **Step 1: Final commit (if anything uncommitted)**

```bash
git add -A
git commit -m "feat(type-checker): enforce cockpit-backend (PR 4)

Adds tools/cockpit/backend to repo_targets. Removes cockpit's
freshness.py from pyproject.toml files list. Adds cockpit test
modules to the test-relaxation override. Fixes the deferred
price_is_stale consumer bug from PR #9. Triaged all mypy --strict
findings on cockpit-backend; real bugs fixed; remaining annotations
added."
```

(If all individual changes were already committed, this final commit may be empty — skip it.)

- [ ] **Step 2: Push + PR**

```bash
git push -u origin feat/type-checker-cockpit-enforce

gh pr create --base main --title "feat(type-checker): cockpit-backend enforced + price_is_stale fix (PR 4)" --body "$(cat <<'EOF'
## Summary

PR 4 of the type-checker rollout. Brings cockpit-backend into the enforced set and fixes the deferred \`price_is_stale\` consumer bug from PR #9.

Spec: \`docs/superpowers/specs/2026-05-20-type-checker-design.md\`
Plan: \`docs/plans/type-checker-plan.md\` (Task 36-41)

## Acceptance criteria (per spec §7 Phase 3 acceptance per-PR)

- [x] \`bash deploy/energy-stack/run_typecheck.sh\` returns 0 with cockpit-backend in repo_targets.
- [x] \`lint-imports --config pyproject.toml\` returns zero violations.
- [x] cockpit-backend tests pass.
- [x] \`price_is_stale\` consumer in snapshot.py fixed (now reads bucket_age_sec).
- [x] Real bugs found are fixed in-PR.

## Pre-OSF gate status

Per spec §9.3, the pre-OSF gate requires:
- [x] Scheduler enforced (landed in PR 3).
- [x] Cockpit-backend enforced (this PR).
- [ ] \`type-check\` is a required status check on \`main\` (operator-configured after PR 1; please verify).

Once this PR merges, the pre-OSF gate is satisfied. Remaining PRs (5-14 for pollers/watchdog/telegram-notifier, 15 for cleanup) can complete post-OSF if needed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Report the PR URL.

---

## Phase 5: Remaining Services (PR 5-14)

**Goal:** Bring each remaining Python service into the enforced set. Each PR follows the §6.1 template from the spec.

**Service order:**
- PR 5: `comed-poller` (data pipeline; freshness-bug-adjacent)
- PR 6: `pjm-dm2-poller` (DM2 / 5CP risk inputs)
- PR 7: `hvac-scheduler-watchdog` (depends on scheduler module shape; uses `check.py` entrypoint)
- PR 8: `nws-poller`
- PR 9: `eagle-poller`
- PR 10: `ecowitt-ingest`
- PR 11: `haven-ingest`
- PR 12: `refoss-poller`
- PR 13: `thermostat-poller`
- PR 14: `telegram-notifier`

This order can be revised based on what bugs surface in early PRs.

### Task 42: Per-service migration (PR 5, comed-poller)

The following template applies to EACH service PR (5-13). The comed-poller is documented in full detail; subsequent PRs follow the same shape with the per-service variations table below.

**Steps for comed-poller (template for all per-service PRs):**

- [ ] **Step 1: Branch from main**
  ```bash
  git checkout main && git pull --ff-only origin main
  git checkout -b feat/type-checker-comed-poller
  ```

- [ ] **Step 2: Rename the directory**
  ```bash
  git mv deploy/energy-stack/comed-poller deploy/energy-stack/comed_poller
  ```

- [ ] **Step 3: Add empty `__init__.py`**
  ```bash
  touch deploy/energy-stack/comed_poller/__init__.py
  ```

- [ ] **Step 4: Convert sibling imports to relative form**
  - List the Python files: `ls deploy/energy-stack/comed_poller/*.py`
  - For each sibling import (e.g., `from <name> import X` where `<name>.py` exists in the same dir), convert to `from .<name> import X`.
  - Handle special cases: `# noqa: E402` late imports, `as` aliased imports, string module references in monkeypatch (`monkeypatch.setattr("<service>.<module>.X", ...)`), cross-test imports.
  - Verify with grep (see spec §6.1 step 3).

- [ ] **Step 5: Update Dockerfile**
  - WORKDIR `/app`, `ENV PYTHONPATH=/app`.
  - COPY pattern: `COPY . /app/comed_poller/`.
  - CMD: `["python", "-m", "comed_poller.poller"]` (comed-poller's entrypoint module is `poller.py`).

- [ ] **Step 6: Update docker-compose.yml**
  - Change `build.context: ./comed-poller` → `./comed_poller`.
  - DO NOT change the service name `comed-poller:` (operational identifier).

- [ ] **Step 7: Update run_tests.sh services array**
  - `    comed-poller` → `    comed_poller`.

- [ ] **Step 8: Update run_typecheck.sh service_dirs**
  - Add `comed_poller` to the `service_dirs` array.

- [ ] **Step 9: Update pyproject.toml**
  - Add comed-poller test module names to the test-relaxation override.
  - If this service has typed adapters, add an import-linter contract.

- [ ] **Step 10: Update path references in markdown / shell files**
  - `git grep` for `deploy/energy-stack/comed-poller/` and update each non-archive match.

- [ ] **Step 11: Local verification**
  - `cd deploy/energy-stack/comed_poller/ && python -m pytest .` — tests pass.
  - `bash deploy/energy-stack/run_tests.sh` — full suite passes.
  - `bash deploy/energy-stack/run_typecheck.sh` — runs the new per-service check on comed_poller.
  - `docker compose build comed-poller` (if Docker available).

- [ ] **Step 12: Mypy triage**
  - Capture findings, triage per Task 33 pattern. Fix real bugs; add annotations; document tactical ignores.
  - Iterate until clean.

- [ ] **Step 13: Two-grep rename verification**
  - Positive grep: `grep -rn "comed-poller" tools/cockpit deploy/energy-stack/grafana deploy/energy-stack/telegraf deploy/energy-stack/docker-compose.yml deploy/energy-stack/promtail` — operational identifier preserved.
  - Negative grep: `git grep -nE "deploy/energy-stack/comed-poller/" -- ':!*.json' ':!docs/archive/**'` — zero matches.

- [ ] **Step 14: Commit**
  ```bash
  git add -A
  git commit -m "feat(type-checker): enforce comed-poller (PR 5)"
  ```

- [ ] **Step 15: Push + PR**
  ```bash
  git push -u origin feat/type-checker-comed-poller

  gh pr create --base main --title "feat(type-checker): comed-poller enforced (PR 5)" --body "$(cat <<'EOF'
## Summary

PR 5 of the type-checker rollout. Brings comed-poller into the enforced set.

Spec: \`docs/superpowers/specs/2026-05-20-type-checker-design.md\`
Plan: \`docs/plans/type-checker-plan.md\` (Task 42)

## What's in this PR

- \`deploy/energy-stack/comed-poller/\` → \`comed_poller/\` (git mv, history preserved)
- Empty \`__init__.py\` added.
- All cross-file sibling imports converted to relative form (\`from .<sibling> import X\`).
- Dockerfile updated: WORKDIR \`/app\`, \`ENV PYTHONPATH=/app\`, CMD \`python -m comed_poller.poller\`.
- \`docker-compose.yml\` build.context path updated (service name \`comed-poller\` STAYS hyphenated).
- \`run_tests.sh\` services array updated.
- \`comed_poller\` added to \`run_typecheck.sh\` service_dirs.
- \`pyproject.toml\` test-relaxation override extended with comed-poller test modules.
- \`mypy --strict\` triage complete; real bugs fixed.

## Acceptance criteria (per spec §9.1)

- [x] \`mypy --strict\` returns 0 errors on this service.
- [x] \`lint-imports --config pyproject.toml\` returns 0 violations.
- [x] Service tests pass.
- [ ] Container builds with new \`python -m comed_poller.poller\` entrypoint (verified post-merge via deploy.yml).
- [x] Operational identifier (\`comed-poller\` in compose, Grafana, Influx, Telegraf, promtail) unchanged — verified by positive grep.
- [x] No stale \`deploy/energy-stack/comed-poller/\` filesystem-path references outside archive docs — verified by negative grep.

## Real bugs found by mypy

[List each real bug found during triage. Format: file:line — description. Or "None — only annotations added" if no real bugs surfaced.]

## Tactical ignores added (if any)

[List any \`# type: ignore\` annotations added, with rationale and backlog entry.]

## Test plan

- [x] \`cd deploy/energy-stack/comed_poller/ && python -m pytest .\`
- [x] \`bash deploy/energy-stack/run_tests.sh\`
- [x] \`bash deploy/energy-stack/run_typecheck.sh\`
- [ ] \`docker compose build comed-poller\` (if local Docker available)
EOF
)"
  ```

**For PRs 6-13:** use the same PR body template above, substituting:
- The PR number (`5` → `6`, `7`, etc.)
- The service hyphenated name (`comed-poller` → `pjm-dm2-poller`, etc.)
- The service underscore name (`comed_poller` → `pjm_dm2_poller`, etc.)
- The entrypoint module from the variations table (`comed_poller.poller` → e.g., `pjm_dm2_poller.app`)
- Adjust the operational-identifier grep paths if the service has different operational-config locations (most won't)

---

### Per-service variations table (PR 5-14)

Use the comed-poller template above for each subsequent service. The variations are:

| PR | Service (dir, hyphenated) | Renamed (underscore) | Entrypoint module | Notes |
|---|---|---|---|---|
| 5 | comed-poller | comed_poller | `comed_poller.poller` | (template; documented above) |
| 6 | pjm-dm2-poller | pjm_dm2_poller | `pjm_dm2_poller.app` | DM2 / 5CP risk inputs; verify entrypoint with current Dockerfile |
| 7 | hvac-scheduler-watchdog | hvac_scheduler_watchdog | `hvac_scheduler_watchdog.check` | Entrypoint is `check.py`, NOT `app.py` |
| 8 | nws-poller | nws_poller | `nws_poller.app` | Uses `aiohttp` (ships typed stubs — expect more findings) |
| 9 | eagle-poller | eagle_poller | `eagle_poller.poller` | |
| 10 | ecowitt-ingest | ecowitt_ingest | `ecowitt_ingest.app` | Ingest service (FastAPI-based?) |
| 11 | haven-ingest | haven_ingest | `haven_ingest.app` | Ingest service |
| 12 | refoss-poller | refoss_poller | `refoss_poller.poller` | |
| 13 | thermostat-poller | thermostat_poller | `thermostat_poller.poller` | |
| 14 | telegram-notifier | telegram_notifier | `telegram_notifier.app` | Uses `aiohttp` |

**Rules:**
- Always verify the actual entrypoint module by inspecting the current Dockerfile CMD before updating.
- Always preserve the operational identifier (compose service name stays hyphenated).
- Always run both verification greps in Step 13.
- Always update `check-freshness-drift.yml` only if the rename touches paths in its `paths:` trigger — most pollers don't, only the scheduler does.

---

### Tasks 43-51: Apply the template to PR 6 through PR 13

For PRs 6, 7, 8, 9, 10, 11, 12, 13, 14: apply the comed-poller template (Steps 1-15 above) with the per-service variations from the table. Each PR is its own task series in the implementation execution (typically 15 small steps each, ~30-60 min mechanical work per service).

The plan does NOT enumerate each step verbatim per service — they're identical to comed-poller's template. Use the subagent-driven-development pattern: dispatch one subagent per service PR with the comed-poller template + the per-service row from the variations table.

**Per-service additional considerations:**

- **PR 6 pjm-dm2-poller**: PJM DM2 / 5CP risk inputs. May have its own typed-adapter candidates if it uses a third-party PJM client. Verify and update type-debt-backlog.md.
- **PR 7 hvac-scheduler-watchdog**: Depends on scheduler module shape. The watchdog reads `hvac_scheduler` health markers from log files or process state. Import-conversion may need updates if it touches scheduler symbols.
- **PR 8 nws-poller, PR 14 telegram-notifier**: aiohttp ships typed stubs. Expect MORE findings than other pollers. May surface real bugs in aiohttp usage.
- **PR 10 ecowitt-ingest, PR 11 haven-ingest**: Likely FastAPI-based ingest endpoints. Pydantic plugin should handle most things cleanly.

---

## Phase 6: Cleanup (PR 15)

**Goal:** Remove leftover bootstrap scaffolding now that all services are enforced via per-service invocations. Update spec status to `shipped`. Verify required-check is still active.

NOTE: This phase happens AFTER all services in Phase 5 are enforced (PR 14 = `telegram-notifier`). Cleanup is PR 15.

---

### Task 52: Cleanup PR

**Files:**
- Modify: `pyproject.toml`
- Modify: `deploy/energy-stack/pytest.ini`
- Modify: `docs/superpowers/specs/2026-05-20-type-checker-design.md`

- [ ] **Step 1: Branch from main**

```bash
git checkout main && git pull --ff-only origin main
git checkout -b feat/type-checker-cleanup
```

- [ ] **Step 2: Remove the `files` key from pyproject.toml**

In `pyproject.toml`, delete the `files = []` line entirely (and any associated comment). The bootstrap-pass guard in `run_typecheck.sh` reads the live tomllib value; with the key missing, the guard skips the bootstrap pass.

- [ ] **Step 3: Update pytest.ini docstring**

Simplify the docstring in `deploy/energy-stack/pytest.ini` to remove the deferred-rename workaround note. All services are now renamed; the workaround is no longer needed.

- [ ] **Step 4: Update spec status**

In `docs/superpowers/specs/2026-05-20-type-checker-design.md`:
- Change YAML header `status: draft (rev 5.1)` → `status: shipped`.
- Append a final entry to §10 changelog:
  ```
  | 2026-MM-DD | shipped | All 11 scheduler-stack services + cockpit-backend enforced via per-service mypy --strict + import-linter contracts. `files` list removed from pyproject.toml. `pytest.ini` workaround docstring removed. `type-check` required status check on `main` verified. |
  ```

- [ ] **Step 5: Verify**

```bash
bash deploy/energy-stack/run_typecheck.sh
```

Expected: all per-service checks run; bootstrap pass SKIPPED (no `files` key); import-linter runs across all enforced services; everything passes.

- [ ] **Step 6: Verify GitHub branch protection**

Operator confirms via the GitHub Settings UI that `type-check` is still listed as a required status check for `main`. Document confirmation in the PR description.

- [ ] **Step 7: Commit + PR**

```bash
git add -A
git commit -m "chore(type-checker): cleanup — remove bootstrap files list, mark spec shipped

All services now enforced via per-service invocations. The
pyproject.toml \`files\` list is no longer needed; the bootstrap
guard in run_typecheck.sh skips when the key is missing. pytest.ini
deferred-rename docstring removed. Spec status → shipped."

git push -u origin feat/type-checker-cleanup
gh pr create --base main --title "chore(type-checker): cleanup — bootstrap removed, spec shipped" --body "..."
```

Report the PR URL.

---

## Plan Self-Review Checklist

(For the plan author: run after writing.)

- [x] **Spec coverage:** Every spec section (§2 goals, §3 scope, §4 architecture, §5 external libs, §5.5 adapter enforcement, §5.6 test relaxation, §6.1 template, §6.3 PR ordering, §7 phases, §8 risks, §9 gates) has a corresponding task or task-group.
- [x] **Placeholder scan:** No "TBD", "TODO", or vague "implement appropriate X" placeholders. Where the plan defers to operator decision (e.g., per-service entrypoint verification), the deferred decision is explicit and bounded.
- [x] **Type consistency:** `TypedRecord`, `project_record`, `service_dirs`, `repo_targets`, etc. used consistently across tasks. Per-service variations table aligns with §6.3.
- [x] **No half-finished implementations:** Each PR is demoable end-to-end. PR 1 = infrastructure works on the two freshness modules. PR 2 = scheduler is renamed and tests pass without enforcement yet. PR 3 = scheduler enforced + adapter working. PR 4 = cockpit enforced + bug fix. Subsequent per-service PRs each demoable.

---

## Execution Handoff

**Plan complete and saved to `docs/plans/type-checker-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task. Review between tasks. Fast iteration. This was the pattern used for the freshness PR's 22 tasks; worked well.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`. Batch execution with checkpoints for review.

For this rollout, **subagent-driven is the strong recommendation**:
- 14 PRs × ~12 tasks each = ~150-170 tasks total
- Many are mechanical (rename + sed + commit); a fresh subagent per task keeps the work focused
- Per-service PRs (5-13) are templated, making subagent dispatch repeatable
- The operator wants dual-review at PR boundaries (every PR merge), not within-PR review

**Which approach?**
