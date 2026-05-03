# energy-stack

Docker Compose project running on Pi-lab (`192.168.20.10`) — InfluxDB + Grafana + 7 pollers/services + log aggregation, optimizing residential energy use against ComEd Hourly Pricing and PJM 5CP windows.

> **Per-service detail:** [`../../docs/SERVICES.md`](../../docs/SERVICES.md)
> **HVAC scheduler logic + thermostat fallback:** [`../../docs/HVAC_LOGIC.md`](../../docs/HVAC_LOGIC.md)
> **Backup/restore:** [`backup/RESTORE.md`](backup/RESTORE.md)

## Services at a glance

| Service | Purpose | Port | Detail |
|---|---|---|---|
| `influxdb` | Time-series storage (energy bucket) | 8086 | [SERVICES.md#influxdb](../../docs/SERVICES.md#influxdb) |
| `grafana` | Visualization (dashboards + Loki Explore) | 3000 | [SERVICES.md#grafana](../../docs/SERVICES.md#grafana) |
| `eagle-poller` | EAGLE-3 smart meter, billing-grade demand + summation, 30 s | — | [SERVICES.md#eagle-poller](../../docs/SERVICES.md#eagle-poller) |
| `comed-poller` | ComEd Hourly Pricing 5min + hourly_avg, 60 s | — | [SERVICES.md#comed-poller](../../docs/SERVICES.md#comed-poller) |
| `refoss-poller` | Refoss EM16P 18 channels, 30 s | — | [SERVICES.md#refoss-poller](../../docs/SERVICES.md#refoss-poller) |
| `nws-poller` | NWS forecast (today/tomorrow/day2) + alerts, 30 min | — | [SERVICES.md#nws-poller](../../docs/SERVICES.md#nws-poller) |
| `hvac-scheduler` | Day-type decision @ 21:00 + Control4 setpoint pushes | — | [SERVICES.md#hvac-scheduler](../../docs/SERVICES.md#hvac-scheduler) |
| `thermostat-poller` | Continuous 10-min Control4 reads + override detection | — | [SERVICES.md#thermostat-poller](../../docs/SERVICES.md#thermostat-poller) |
| `haven-ingest` | Watches `inbox/haven/` for Haven IAQ CSV exports → InfluxDB | — | [SERVICES.md#haven-ingest](../../docs/SERVICES.md#haven-ingest) |
| `telegram-notifier` | Daily 8 AM summary + 5-min alert checker | — | [SERVICES.md#telegram-notifier](../../docs/SERVICES.md#telegram-notifier) |
| `webdashboard` | nginx static (sci-fi HUD, live data via API) | 8081 | [SERVICES.md#webdashboard](../../docs/SERVICES.md#webdashboard) |
| `webdashboard-api` | FastAPI backend for webdashboard | (8082, internal) | [SERVICES.md#webdashboard-api](../../docs/SERVICES.md#webdashboard-api) |
| `loki` | Log storage (7-day retention) | 3100 | [SERVICES.md#loki--promtail](../../docs/SERVICES.md#loki--promtail) |
| `promtail` | Container log shipper → Loki | — | [SERVICES.md#loki--promtail](../../docs/SERVICES.md#loki--promtail) |

## Authoring & deployment

Author on Windows under `D:\Projects\energy-proxy\deploy\energy-stack\`. Deploy with:

```bash
rsync -av --delete --exclude '.env' \
  "D:/Projects/energy-proxy/deploy/energy-stack/" \
  chris@192.168.20.10:~/energy-stack/
```

The `.env` file lives ONLY on Pi-lab (chmod 600). Never committed (`.gitignore`), never rsynced (`--exclude`). The SOPS-encrypted equivalent (`secrets/env.sops.env`) IS committed and IS rsynced — that's the recovery path.

Rebuild only the service you changed:

```bash
ssh chris@192.168.20.10 \
  "cd ~/energy-stack && docker compose build hvac-scheduler && docker compose up -d hvac-scheduler"
```

## First-time bootstrap (Pi-lab)

```bash
ssh chris@192.168.20.10
cd ~/energy-stack

# Restore .env from SOPS-encrypted copy (preferred path)
sops --decrypt secrets/env.sops.env > .env
chmod 600 .env

# OR start from template (only for fresh deployment, not recovery)
# cp .env.example .env && nano .env

docker compose pull
docker compose up -d
docker compose ps
```

InfluxDB bootstraps the org/bucket/admin user **only on first run against an empty `influxdb_data` volume**. To re-bootstrap from scratch: `docker compose down -v` (irreversibly destroys data — also kills your Refoss baseline).

## Operations

| Task | Command |
|---|---|
| View status | `docker compose ps` |
| Tail all logs | `docker compose logs -f` |
| Tail one service | `docker compose logs -f hvac-scheduler` |
| Restart one | `docker compose restart hvac-scheduler` |
| Stop (keep data) | `docker compose down` |
| Start | `docker compose up -d` |
| Upgrade pinned image | Edit tag in `docker-compose.yml`, `docker compose pull && docker compose up -d` |
| Inspect Influx via CLI | `docker exec -it influxdb influx query 'from(bucket:"energy")\|>range(start:-5m)\|>limit(n:1)'` |
| Run a manual InfluxDB backup | `docker exec influxdb influx backup /tmp/backup -t "$INFLUXDB_INIT_ADMIN_TOKEN"` |
| **Destroy all data** | `docker compose down -v` (irreversible) |

For service-specific operations (manually trigger an HVAC decision, force a poller cycle, etc.) see the [SERVICES.md per-service section](../../docs/SERVICES.md).

## Ports

| Port | Service | Reachable from |
|---|---|---|
| 8086 | InfluxDB API + UI | Trusted VLAN 10 (ZBF: Trusted→Homelab), Pi-lab localhost |
| 3000 | Grafana UI | Trusted VLAN 10 (ZBF: Trusted→Homelab), Pi-lab localhost |
| 8081 | Webdashboard (nginx) | Trusted VLAN 10 (ZBF: Trusted→Homelab), Pi-lab localhost |
| 3100 | Loki (Grafana queries it via container network) | Pi-lab localhost only |

No firewall changes required — existing "Allow Trusted to Homelab" ZBF rule covers the user-facing ports.

## Secrets

### Runtime file

All secrets live in `~/energy-stack/.env` on Pi-lab (chmod 600, owner chris). Never commit plaintext `.env`. `.env.example` is the committed template.

The InfluxDB admin token is captured in `.env` (`INFLUXDB_INIT_ADMIN_TOKEN`) and reused by Grafana's provisioned datasource and every poller. To rotate: generate new token in InfluxDB UI → update `.env` → `docker compose up -d` to re-read across all services.

### SOPS-encrypted backup

`secrets/env.sops.env` is an age-encrypted copy of `.env`, committed to the repo. Safe to commit/share — values are encrypted, only keys are visible.

**Recipients (both can decrypt):**
- `age1damv2x...nvrjm6` — Windows workstation (`%APPDATA%\sops\age\keys.txt`)
- `age1gfyca3...r2r2gek` — Pi-lab runtime host (`~/.config/sops/age/keys.txt`)

Rules file: `.sops.yaml` at the directory root.

**Decrypt to recover `.env` (e.g., on a fresh host):**

```bash
cd ~/energy-stack
sops --decrypt secrets/env.sops.env > .env
chmod 600 .env
```

**Re-encrypt after editing `.env`** (rotation, new env var, etc.):

```bash
cd ~/energy-stack
sops --encrypt --input-type dotenv --output-type dotenv .env > secrets/env.sops.env
```

SOPS auto-picks recipients from `.sops.yaml`. After encrypting on Pi, pull back to Windows: `scp chris@192.168.20.10:~/energy-stack/secrets/env.sops.env D:/Projects/energy-proxy/deploy/energy-stack/secrets/`.

**Edit encrypted values in place** (no plaintext on disk):

```bash
sops secrets/env.sops.env
```

Opens in `$EDITOR` with values decrypted; re-encrypts on save.

**Private key backup:** store both age private keys in 1Password. Without a private key, the corresponding host cannot decrypt. If both are lost, the SOPS file is unrecoverable.

**Adding a new recipient:** add the new public key to `.sops.yaml`, then `sops updatekeys secrets/env.sops.env` to re-wrap the data key. Requires an existing recipient's private key.

## Volumes

| Volume | Service | Persists |
|---|---|---|
| `influxdb_data` | influxdb | All time-series data (organizations, buckets, points) |
| `influxdb_config` | influxdb | InfluxDB config |
| `grafana_data` | grafana | Dashboards (only modifications; provisioned dashboards are read-only mounts), users, alerts state |
| `hvac_scheduler_data` | hvac-scheduler | `/data/director_token.json` (Control4 token), `/data/overrides.json` (manual day-type / vacation overrides) |
| `thermostat_poller_data` | thermostat-poller | `/data/director_token.json` (its own Control4 token, independent of hvac-scheduler) |
| (bind mount) `./inbox/haven` | haven-ingest | CSV inbox: drop new Haven exports here. Service moves them to `processed/` on success or `failed/` on parse error. |
| `loki_data` | loki | Log chunks (7-day retention configured in `loki/loki-config.yml`) |
| `promtail_positions` | promtail | Cursor positions per container log file (avoids re-shipping after restart) |

## Related

- Top-level project: [`../../README.md`](../../README.md)
- Project history & decisions: [`../../PROJECT.md`](../../PROJECT.md)
- Per-service detail: [`../../docs/SERVICES.md`](../../docs/SERVICES.md)
- HVAC scheduler logic: [`../../docs/HVAC_LOGIC.md`](../../docs/HVAC_LOGIC.md)
- Backup/restore: [`backup/RESTORE.md`](backup/RESTORE.md)
- Infra context (VLAN/ZBF/Pi-lab state): `D:\Projects\Network_Management\CLAUDE.md`
