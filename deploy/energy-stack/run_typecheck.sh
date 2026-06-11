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
#   - repo_targets: paths relative to repo root (used for the
#     cockpit backend, whose package root is nested at
#     deploy/energy-stack/cockpit/backend rather than being a
#     service_dirs-style top-level package)
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
    comed_poller
    pjm_dm2_poller
    hvac_scheduler_watchdog
    nws_poller
    eagle_poller
    refoss_poller
    thermostat_poller
    haven_ingest
    ecowitt_ingest
    telegram_notifier
)

# Path-based targets in the enforced set (relative to REPO_ROOT)
repo_targets=(
    deploy/energy-stack/cockpit/backend
)

failed=()

# Bootstrap mypy pass: enforces files listed in pyproject.toml `files`.
# The rollout that introduced this mechanism (PRs 0-14) is complete and
# `files` is no longer set, so the guard below skips the pass — but the
# infrastructure stays as forward-compat: if a future PR ever needs a
# one-off single-file enforcement, adding `files = [...]` to
# pyproject.toml re-engages the pass automatically.
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

# Repo-relative path targets (cockpit-backend etc.). Invoked from repo
# root; mypy roots the package at the first non-package ancestor, so the
# cockpit backend checks as the top-level `backend` package.
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
