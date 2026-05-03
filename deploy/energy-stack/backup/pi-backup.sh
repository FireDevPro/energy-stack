#!/bin/bash
set -euo pipefail

LOG=/var/log/pi-backup.log
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

log() { echo "[${STAMP}] $*" | tee -a "$LOG"; }

source /home/chris/.config/restic/b2.env

# Telegram creds live in energy-stack .env (same bot as the daily summary / alerts)
TELEGRAM_BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' /home/chris/energy-stack/.env | cut -d= -f2-)
TELEGRAM_CHAT_ID=$(grep '^TELEGRAM_CHAT_ID=' /home/chris/energy-stack/.env | cut -d= -f2-)

telegram() {
    local message="$1"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
         --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
         --data-urlencode "parse_mode=HTML" \
         --data-urlencode "disable_web_page_preview=true" \
         --data-urlencode "text=${message}" > /dev/null
}

log "=== Pi backup started ==="

STAGE_DIR="$(mktemp -d /tmp/pi-backup.XXXXXX)"
trap 'rm -rf "$STAGE_DIR"' EXIT

# Optional: shadow-postgres dump (only if container is running)
if docker ps --format '{{.Names}}' | grep -qx shadow-postgres; then
    log "Dumping shadow-postgres"
    docker exec shadow-postgres pg_dump -U n8n n8n > "$STAGE_DIR/shadow-postgres.sql" || \
        log "WARN: shadow-postgres dump failed, continuing"
fi

# InfluxDB backup (energy-stack)
if docker ps --format '{{.Names}}' | grep -qx influxdb; then
    log "Backing up InfluxDB"
    mkdir -p "$STAGE_DIR/influxdb"
    docker exec influxdb influx backup /tmp/influx-backup -t "$(grep '^INFLUXDB_INIT_ADMIN_TOKEN=' /home/chris/energy-stack/.env | cut -d= -f2-)" 2>&1 | tee -a "$LOG"
    docker cp influxdb:/tmp/influx-backup "$STAGE_DIR/influxdb/"
    docker exec influxdb rm -rf /tmp/influx-backup
else
    log "WARN: influxdb container not running, skipping InfluxDB backup"
fi

# n8n Postgres dump (n8n-stack). Captures all workflows, executions,
# credentials. Pair with /home/chris/n8n in the restic paths below — the .env
# there holds the encryption key that decrypts the credentials in this dump.
if docker ps --format '{{.Names}}' | grep -qx n8n-postgres; then
    log "Dumping n8n Postgres"
    PGPASSWORD=$(grep '^POSTGRES_PASSWORD=' /home/chris/n8n/.env | cut -d= -f2-) \
        docker exec -e PGPASSWORD n8n-postgres pg_dump -U n8n n8n > "$STAGE_DIR/n8n-postgres.sql" || \
        log "WARN: n8n-postgres dump failed, continuing"
fi

BACKUP_OK=0

# Run backup as root to capture Docker-owned files
restic backup \
  /home/chris/energy-stack \
  /home/chris/n8n \
  /home/chris/chris-brain \
  /home/chris/dns-stack \
  /home/chris/Network_Management \
  /home/chris/udm-scripts \
  /home/chris/.config/restic \
  /home/chris/.ssh/ \
  /usr/local/bin/pi-backup.sh \
  "$STAGE_DIR" \
  --exclude='*/.git' \
  --exclude='*/node_modules' \
  --exclude='*/__pycache__' \
  --exclude='*/.venv' \
  --exclude='*/venv' \
  --verbose 2>&1 | tee -a "$LOG" && BACKUP_OK=1

if [ "$BACKUP_OK" -eq 1 ]; then
    log "Backup completed successfully — running prune"
    restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune 2>&1 | tee -a "$LOG"
    log "=== Backup and prune finished ==="
    SNAPSHOT_COUNT=$(restic snapshots --json 2>/dev/null | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))' 2>/dev/null || echo "?")
    REPO_SIZE=$(restic stats --mode raw-data --json 2>/dev/null | python3 -c 'import sys,json;b=json.load(sys.stdin)["total_size"];print(f"{b/1024/1024:.0f} MiB" if b<1073741824 else f"{b/1024/1024/1024:.2f} GiB")' 2>/dev/null || echo "?")
    telegram "<b>Pi Backup OK</b>
pi-lab backup completed at ${STAMP}
<b>Snapshots:</b> ${SNAPSHOT_COUNT}
<b>Repo size:</b> ${REPO_SIZE}"
    exit 0
else
    log "ERROR: restic backup failed"
    LOG_TAIL=$(tail -20 "$LOG" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')
    telegram "<b>Pi Backup FAILED</b> 🚨
pi-lab backup failed at ${STAMP}

<b>Last 20 log lines:</b>
<pre>${LOG_TAIL}</pre>"
    exit 1
fi
