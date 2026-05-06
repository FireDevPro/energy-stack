#!/bin/sh
# Generate the Mosquitto password file from env vars.
# Idempotent; runs on every `compose up -d` and regenerates the file fresh.
# Design: docs/COMFORTNET_PIPELINE.md

set -eu

PASSWORD_FILE="/mosquitto/passwords/passwords"

: "${MOSQUITTO_PUBLISHER_PASSWORD:?must be set}"
: "${MOSQUITTO_TELEGRAF_PASSWORD:?must be set}"
: "${MOSQUITTO_N8N_PASSWORD:?must be set}"

log() { echo "[mosquitto-init] $*"; }

# Start fresh so removed users actually disappear from the file.
: > "$PASSWORD_FILE"

# -b: read password from CLI (not stdin). -c: create file (only the first call).
mosquitto_passwd -b -c "$PASSWORD_FILE" comfortnet-publisher "$MOSQUITTO_PUBLISHER_PASSWORD"
mosquitto_passwd -b    "$PASSWORD_FILE" telegraf            "$MOSQUITTO_TELEGRAF_PASSWORD"
mosquitto_passwd -b    "$PASSWORD_FILE" n8n                 "$MOSQUITTO_N8N_PASSWORD"

# Mosquitto refuses world-readable password files at startup.
chmod 600 "$PASSWORD_FILE"
chown 1883:1883 "$PASSWORD_FILE"

log "wrote 3 identities to $PASSWORD_FILE"
