#!/usr/bin/env bash
# Run every service's pytest suite in its own pytest invocation.
#
# Why one-per-service: every service has its own `app.py` (or `poller.py`)
# source file, and pytest's default `prepend` import mode caches the first
# `app` it imports in sys.modules. A single `pytest deploy/energy-stack`
# call would let one service's tests "see" another service's app.py.
#
# Until the directories get renamed to underscores and given `__init__.py`
# (so each service can be a proper Python package), this wrapper is the
# right way to run everything. Exits non-zero if any service fails.
#
# Usage:
#   bash deploy/energy-stack/run_tests.sh
#
# From the repo root or anywhere — script resolves its own location.

set -euo pipefail

STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

services=(
    comed_poller
    eagle_poller
    ecowitt_ingest
    haven_ingest
    hvac_scheduler
    hvac_scheduler_watchdog
    nws_poller
    pjm_dm2_poller
    refoss_poller
    telegram_notifier
    thermostat_poller
)

# scripts/tests/ and cockpit/backend/tests/ are proper packages with
# __init__.py; safe to collect in one shot each.
extras=(
    scripts
    cockpit/backend
)

failed=()

for svc in "${services[@]}"; do
    # Sentinel: any test_*.py in the service dir. (Was the literal
    # test_<service>.py filename, which silently skipped a whole suite
    # when that file was renamed/deleted — e.g. the rev 4 cutover.)
    test_files=("$STACK_DIR/$svc"/test_*.py)
    if [[ ! -e "${test_files[0]}" ]]; then
        # Service has no tests yet — skip silently.
        continue
    fi
    echo
    echo "=== $svc ==="
    if ! python -m pytest "$STACK_DIR/$svc"; then
        failed+=("$svc")
    fi
done

for extra in "${extras[@]}"; do
    extra_path="$STACK_DIR/$extra"
    if [[ -d "$extra_path/tests" ]]; then
        echo
        echo "=== $extra ==="
        if ! python -m pytest "$extra_path"; then
            failed+=("$extra")
        fi
    fi
done

echo
if (( ${#failed[@]} > 0 )); then
    echo "FAILED: ${failed[*]}"
    exit 1
fi
echo "OK"
