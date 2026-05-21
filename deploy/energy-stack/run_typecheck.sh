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
    hvac_scheduler
)

# Paths outside deploy/energy-stack/ in the enforced set
# (relative to REPO_ROOT)
repo_targets=(
    tools/cockpit/backend
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
#
# PYTHONPATH uses a cwd-relative path ("deploy/energy-stack") inside
# the subshell — same pattern as the bootstrap-guard fix in PR 1.
# An absolute path via $STACK_DIR is MSYS-form under Git Bash on
# Windows (e.g., /d/Projects/...) which Windows-native Python's
# PYTHONPATH parser rejects with "Could not find package X in your
# Python path".
#
# The `${PYTHONPATH:+:${PYTHONPATH}}` expansion appends `:$PYTHONPATH`
# only when PYTHONPATH is set+non-empty. Avoids a trailing colon when
# PYTHONPATH is unset — Windows Python reads `deploy/energy-stack:`
# (with a trailing empty entry) as a malformed path and reports the
# package as not found. Linux Python silently ignores trailing colons,
# which is why this only bites on Windows.
if [[ ${#service_dirs[@]} -gt 0 || ${#repo_targets[@]} -gt 0 ]]; then
    echo
    echo "=== checking import-linter contracts ==="
    if ! (cd "$REPO_ROOT" && PYTHONPATH="deploy/energy-stack${PYTHONPATH:+:${PYTHONPATH}}" lint-imports --config pyproject.toml); then
        failed+=("import-linter")
    fi
fi

echo
if (( ${#failed[@]} > 0 )); then
    echo "TYPE-CHECK FAILED: ${failed[*]}"
    exit 1
fi
echo "OK"
