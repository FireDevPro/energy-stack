---
date: 2026-05-18
owner: chris
status: active
role-label: code-team
---

# Pi-lab restore procedure

The Pi backs up to Backblaze B2 nightly at 02:00 CDT via `/usr/local/bin/pi-backup.sh` (root cron). Repo: `b2:crd-pi-backups`.

## Credentials live in
- `/home/chris/.config/restic/b2.env` — sourced by every restic command. Holds `B2_ACCOUNT_ID`, `B2_ACCOUNT_KEY`, `RESTIC_PASSWORD_FILE`, `RESTIC_REPOSITORY`. (Pushover keys are still here but unused — see Telegram section below.)
- `/home/chris/energy-stack/.env` — holds `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for backup notifications. Same bot as the daily summary / poller alerts (@EnergyStackBot).
- The restic repo password file path is `RESTIC_PASSWORD_FILE` inside `b2.env`.

If the Pi is bricked, you'll need to retrieve `b2.env` from your password manager / 1Password before any restic operation. Without the repo password, B2 contents are unrecoverable.

## What's in each snapshot
- `/home/chris/energy-stack` — compose file, pollers, dashboards, scheduler, .env (encrypted secrets in `secrets/env.sops.env`)
- `/home/chris/n8n` — n8n compose file + .env (Postgres password + N8N_ENCRYPTION_KEY — critical for restoring n8n credentials)
- `/home/chris/chris-brain` — code only (vector data lives in Supabase cloud)
- `/home/chris/dns-stack`, `/home/chris/Network_Management`, `/home/chris/udm-scripts`
- `/home/chris/.ssh`, `/home/chris/.config/restic`
- `/usr/local/bin/pi-backup.sh` — the backup script itself
- `/tmp/pi-backup.<random>` — per-run staging dir containing:
  - `influxdb/influx-backup/` (Influx 2.7 backup format: tar.gz shards + bolt + sqlite + manifest)
  - `n8n-postgres.sql` (plain SQL dump of the n8n Postgres database — workflows, executions, credentials)

## Listing snapshots
```bash
source ~/.config/restic/b2.env
restic snapshots
restic snapshots --latest 1 --json | jq
```

## Restoring a single file/dir
```bash
source ~/.config/restic/b2.env
restic restore latest --target /tmp/restore --include /home/chris/energy-stack/.env
# files land at /tmp/restore/home/chris/energy-stack/.env
```

## Full Pi rebuild from scratch
1. Reflash Pi OS, install docker, restic, jq, curl.
2. Recreate `/home/chris/.config/restic/b2.env` from password manager.
3. `source ~/.config/restic/b2.env && restic snapshots` to confirm access.
4. Restore home dirs:
   ```bash
   sudo restic restore latest --target / \
       --include /home/chris/energy-stack \
       --include /home/chris/chris-brain \
       --include /home/chris/dns-stack \
       --include /home/chris/Network_Management \
       --include /home/chris/udm-scripts \
       --include /home/chris/.ssh \
       --include /home/chris/.config/restic
   sudo chown -R chris:chris /home/chris
   ```
5. Restore the backup script + cron:
   ```bash
   sudo restic restore latest --target / --include /usr/local/bin/pi-backup.sh
   sudo crontab -l   # confirm "0 2 * * * /usr/local/bin/pi-backup.sh ..." present, otherwise re-add
   ```
6. Decrypt `~/energy-stack/secrets/env.sops.env` and produce `~/energy-stack/.env` (sops needs your age key in `~/.config/sops/age/keys.txt` — that key is **not** in the backup; keep it in your password manager).
7. `cd ~/energy-stack && docker compose up -d` to bring services up. InfluxDB will start empty.
8. Restore InfluxDB data — see next section.

## Restoring InfluxDB
The Influx backup is staged inside the snapshot at `/tmp/pi-backup.<random>/influxdb/influx-backup/`. It's the output of `influx backup` (Influx 2.7 native format).

```bash
source ~/.config/restic/b2.env
# Find the staging dir name in the snapshot
restic ls latest 2>/dev/null | grep '/tmp/pi-backup' | head -3
# e.g. /tmp/pi-backup.orn57H/influxdb/influx-backup/

# Restore to a host directory
mkdir -p /tmp/influx-restore
sudo restic restore latest --target /tmp/influx-restore --include "/tmp/pi-backup.*/influxdb/influx-backup"
# files land at /tmp/influx-restore/tmp/pi-backup.<random>/influxdb/influx-backup/

# Copy into the running influxdb container and restore
STAGE=$(ls -d /tmp/influx-restore/tmp/pi-backup.*/influxdb/influx-backup | head -1)
docker cp "$STAGE" influxdb:/tmp/influx-backup
TOKEN=$(grep '^INFLUXDB_INIT_ADMIN_TOKEN=' ~/energy-stack/.env | cut -d= -f2-)
docker exec influxdb influx restore /tmp/influx-backup -t "$TOKEN" --full
docker exec influxdb rm -rf /tmp/influx-backup
```

`--full` overwrites everything in the target Influx instance with the backup. For a fresh post-rebuild Pi this is what you want. For partial restore (e.g. just one bucket) read `influx restore --help`.

### Verify the restore landed data

`influx restore --full` exits 0 even if the source backup was empty or partial. Always verify the restored database has data before considering the restore complete:

```bash
TOKEN=$(grep '^INFLUXDB_INIT_ADMIN_TOKEN=' ~/energy-stack/.env | cut -d= -f2-)
ORG=$(grep '^INFLUXDB_INIT_ORG=' ~/energy-stack/.env | cut -d= -f2-)

# 1. Bucket exists with data in the last 24h
docker exec influxdb influx query \
    -t "$TOKEN" -o "$ORG" \
    'from(bucket:"energy") |> range(start:-24h) |> count() |> sum()'
# Expect: a non-zero `_value` per measurement. Empty result = restore did not land data.

# 2. Most-recent timestamp is recent (within the backup's last-write window)
docker exec influxdb influx query \
    -t "$TOKEN" -o "$ORG" \
    'from(bucket:"energy") |> range(start:-7d) |> last() |> keep(columns:["_measurement","_time"])'
# Expect: rows for `eagle.meter`, `refoss.channel`, `comed.prices`, `pjm.*`, etc.,
# with `_time` near the timestamp of the source backup. A 7-day gap between
# backup time and the latest restored row means partial / stale data.

# 3. Bill data round-trips (slowest-changing, easiest to eyeball)
docker exec influxdb influx query \
    -t "$TOKEN" -o "$ORG" \
    'from(bucket:"energy") |> range(start:-365d) |> filter(fn:(r) => r._measurement == "comed.bill") |> count()'
# Expect: ≥12 points (one per monthly bill over the past year). Zero = restore lost bill history.
```

If any of these come back empty or with stale timestamps, the restore is incomplete — investigate before bringing services that depend on Influx data (HVAC scheduler, dashboards) into production use. Common causes: source backup tar was corrupted, `--full` collided with a non-empty target, or the `STAGE` path was wrong (no error from `docker cp` if the source dir exists but is empty).

## Restoring n8n

The n8n backup is a plain SQL dump of the Postgres database staged inside the snapshot at `/tmp/pi-backup.<random>/n8n-postgres.sql`. The `~/n8n/.env` (also restored) contains the encryption key and Postgres password — both are required.

```bash
source ~/.config/restic/b2.env
# Restore n8n config + the Postgres dump
sudo restic restore latest --target / \
    --include /home/chris/n8n \
    --include "/tmp/pi-backup.*/n8n-postgres.sql"
sudo chown -R chris:chris /home/chris/n8n

# Bring n8n stack up (creates empty Postgres on first run)
cd ~/n8n
docker compose up -d
sleep 15  # wait for Postgres healthcheck

# Find the dumped SQL
DUMP=$(ls /tmp/pi-backup.*/n8n-postgres.sql | head -1)

# Restore the database (drops and recreates n8n schema)
PGPASSWORD=$(grep '^POSTGRES_PASSWORD=' ~/n8n/.env | cut -d= -f2-) \
  docker exec -i -e PGPASSWORD n8n-postgres psql -U n8n -d n8n -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
PGPASSWORD=$(grep '^POSTGRES_PASSWORD=' ~/n8n/.env | cut -d= -f2-) \
  docker exec -i -e PGPASSWORD n8n-postgres psql -U n8n -d n8n < "$DUMP"

# Restart n8n to re-read schema and decrypt stored credentials
docker compose restart n8n
```

**The N8N_ENCRYPTION_KEY in `.env` MUST match what was active when the credentials were created.** That's why the `.env` restore is critical — losing it means stored credentials in the dump become unrecoverable (workflows still work, but you'd have to re-enter every API key, password, OAuth token).

## Verifying the backup is healthy
```bash
source ~/.config/restic/b2.env
restic check                    # repo integrity (fast)
restic check --read-data-subset=5%   # actually reads 5% of data from B2 (slow)
```

Run `restic check` periodically (manually or in a monthly cron) to catch silent B2 corruption.

## Retention policy
Configured in `pi-backup.sh`: `--keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`. Roughly 17 snapshots retained at any time. With ~16 MiB per daily snapshot, repo stays well under any cost concerns on B2.

## Telegram alerts
Every run sends a Telegram message via @EnergyStackBot to your DM (same channel as the daily energy summary and poller alerts):
- **success:** Title, timestamp, snapshot count, repo size.
- **failure:** Title, timestamp, plus last 20 lines of `/var/log/pi-backup.log` as a code block so you can diagnose without SSHing in.

If you stop seeing the daily success message, something is wrong with the cron job or the script.

The Telegram bot token / chat id are read at runtime from `/home/chris/energy-stack/.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

## Known limitations
- The cron is in **root**'s crontab, not chris's. View with `sudo crontab -l`.
- Log file `/var/log/pi-backup.log` grows unbounded — if it gets too big, rotate it manually with `sudo logrotate -f` after adding a config, or just truncate.
- No backup of `/home/chris/.config/sops/age/keys.txt` (the age key for SOPS) — keep this in your password manager separately. Backing it up encrypted with itself is a chicken-and-egg.
- Supabase data (chris-brain vectors, n8n workflows if applicable) is NOT in this backup. That's a separate cloud-side responsibility.
