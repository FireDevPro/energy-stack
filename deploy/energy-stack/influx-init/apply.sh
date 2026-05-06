#!/bin/sh
# Idempotent post-bootstrap provisioning for the energy-stack InfluxDB.
# Runs on every `compose up -d`; safe to re-run.
#
# Provisions:
#   1. The `energy-longterm` bucket (infinite retention) for downsampled aggregates.
#   2. The `downsample-energy-1m` Flux task that rolls per-frame measurements
#      from `energy` into `energy-longterm` at 1-minute resolution.
#
# Design: docs/INFLUXDB_RETENTION.md

set -eu

INFLUX_HOST="${INFLUX_HOST:-http://influxdb:8086}"
INFLUX_ORG="${INFLUXDB_INIT_ORG:?must be set}"
INFLUX_TOKEN="${INFLUXDB_INIT_ADMIN_TOKEN:?must be set}"
LONGTERM_BUCKET="energy-longterm"
TASK_NAME="downsample-energy-1m"
TASK_FILE="/app/tasks/downsample-energy-1m.flux"

log() { echo "[influx-init] $*"; }

# Configure the CLI to use the admin token. `config create` fails if the
# config name already exists, so we tolerate that and force-set instead.
influx config create \
    --config-name init \
    --host-url "$INFLUX_HOST" \
    --token "$INFLUX_TOKEN" \
    --org "$INFLUX_ORG" \
    --active >/dev/null 2>&1 || \
influx config set \
    --config-name init \
    --host-url "$INFLUX_HOST" \
    --token "$INFLUX_TOKEN" \
    --org "$INFLUX_ORG" \
    --active >/dev/null

# 1. Bucket: create if missing, otherwise leave alone (don't change retention
#    on an existing bucket without an explicit migration). `bucket list --name`
#    exits 0 even with no match, so test for non-empty output instead.
if influx bucket list --name "$LONGTERM_BUCKET" --hide-headers 2>/dev/null | grep -q .; then
    log "bucket $LONGTERM_BUCKET: already exists"
else
    log "bucket $LONGTERM_BUCKET: creating with infinite retention"
    influx bucket create \
        --name "$LONGTERM_BUCKET" \
        --retention 0 \
        --org "$INFLUX_ORG"
fi

# 2. Task: look up by name; update if present, create if not.
EXISTING_ID=$(influx task list --hide-headers 2>/dev/null \
    | awk -v n="$TASK_NAME" '$2 == n { print $1; exit }')

if [ -n "$EXISTING_ID" ]; then
    log "task $TASK_NAME: updating ($EXISTING_ID)"
    influx task update --id "$EXISTING_ID" --file "$TASK_FILE" >/dev/null
else
    log "task $TASK_NAME: creating"
    influx task create --file "$TASK_FILE" --org "$INFLUX_ORG" >/dev/null
fi

log "complete"
