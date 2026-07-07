---
date: 2026-05-18
owner: chris
status: active
role-label: code-team
---

# energy-stack

Docker Compose project running on Pi-lab (`192.168.20.10`) — InfluxDB + Grafana + 19 pollers/services (16 always-on + 3 under the `mqtt` profile for the ComfortNet pipeline) + log aggregation, optimizing residential energy use against ComEd Hourly Pricing and PJM 5CP windows.

> **Per-service detail:** [`../../docs/SERVICES.md`](../../docs/SERVICES.md)
> **HVAC controller logic:** [`../../docs/superpowers/specs/2026-06-20-commissioning-controller-design.md`](../../docs/superpowers/specs/2026-06-20-commissioning-controller-design.md)
> **Backup/restore:** [`backup/RESTORE.md`](backup/RESTORE.md)

## Services at a glance

| Service | Purpose | Port | Detail |
|---|---|---|---|
| `influxdb` | Time-series storage (energy bucket) | 8086 | [SERVICES.md#influxdb](../../docs/SERVICES.md#influxdb) |
| `grafana` | Visualization (dashboards + Loki Explore) | 3000 | [SERVICES.md#grafana](../../docs/SERVICES.md#grafana) |
| `cockpit` | Controller Cockpit — read-only HVAC dashboard (FastAPI proxy + built frontend, same-origin) | 8765 | [SERVICES.md#cockpit](../../docs/SERVICES.md#cockpit) |
| `eagle-poller` | EAGLE-3 smart meter, billing-grade demand + summation, 30 s | — | [SERVICES.md#eagle-poller](../../docs/SERVICES.md#eagle-poller) |
| `comed-poller` | ComEd Hourly Pricing 5min + hourly_avg, 60 s | — | [SERVICES.md#comed-poller](../../docs/SERVICES.md#comed-poller) |
| `refoss-poller` | Refoss EM16P 18 channels, 30 s | — | [SERVICES.md#refoss-poller](../../docs/SERVICES.md#refoss-poller) |
| `nws-poller` | NWS forecast (today/tomorrow/day2) + alerts, 30 min | — | [SERVICES.md#nws-poller](../../docs/SERVICES.md#nws-poller) |
| `pjm-dm2-poller` | PJM DataMiner 2 zonal feeds (DA LMP, load forecast, metered, peak, NSPL), per-feed schedule | — | [SERVICES.md#pjm-dm2-poller](../../docs/SERVICES.md#pjm-dm2-poller) |
| `hvac-scheduler` | Price-reactive comfort controller (comfort baseline + warm-only RTP drift); device writes via stubbed `ThermostatClient` seam | — | [SERVICES.md#hvac-scheduler](../../docs/SERVICES.md#hvac-scheduler) |
| `hvac-scheduler-watchdog` | Out-of-band controller-liveness beacon (writes `hvac.heartbeat controller_alive=false` when no `hvac.arm_mode` row appears in 10 min) | — | [SERVICES.md#hvac-scheduler-watchdog](../../docs/SERVICES.md#hvac-scheduler-watchdog) |
| `thermostat-poller` | Continuous 10-min thermostat reads (stubbed `ThermostatClient` seam) + override detection | — | [SERVICES.md#thermostat-poller](../../docs/SERVICES.md#thermostat-poller) |
| `haven-ingest` | HAVEN cloud API poller → `haven.indoor` + `haven.outdoor` (5-min cadence) | — | [SERVICES.md#haven-ingest](../../docs/SERVICES.md#haven-ingest) |
| `ecowitt-ingest` | GW1200 push receiver: WN31 shaded → `ch1_*` (canonical analysis field per spec §6) + gateway alias `outdoor_*` (descriptive), WS90 sun → `ws90_*` + wind/solar/rain, 60-s cadence | 8088 | [SERVICES.md#ecowitt-ingest](../../docs/SERVICES.md#ecowitt-ingest) |
| `influx-init` | One-shot: provisions `energy-longterm` bucket + 1-min downsample task | — | [SERVICES.md#influx-init](../../docs/SERVICES.md#influx-init) |
| `telegram-notifier` | Daily 8 AM summary + 5-min alert checker | — | [SERVICES.md#telegram-notifier](../../docs/SERVICES.md#telegram-notifier) |
| `loki` | Log storage (7-day retention) | 3100 | [SERVICES.md#loki--promtail](../../docs/SERVICES.md#loki--promtail) |
| `promtail` | Container log shipper → Loki | — | [SERVICES.md#loki--promtail](../../docs/SERVICES.md#loki--promtail) |
| `mosquitto` | MQTT broker for ComfortNet pipeline (TLS, profile=mqtt) | 8883 | [COMFORTNET_USE_CASES.md](../../docs/COMFORTNET_USE_CASES.md) · [comfortnet repo](https://github.com/Promithius-DR/comfortnet) |
| `mosquitto-init` | Generates broker password file from env vars (one-shot) | — | [COMFORTNET_USE_CASES.md](../../docs/COMFORTNET_USE_CASES.md) · [comfortnet repo](https://github.com/Promithius-DR/comfortnet) |
| `telegraf` | MQTT consumer → InfluxDB (continuous → energy, events → energy-longterm) | — | [COMFORTNET_USE_CASES.md](../../docs/COMFORTNET_USE_CASES.md) · [comfortnet repo](https://github.com/Promithius-DR/comfortnet) |

## Authoring & deployment

Author on Windows under `D:\Projects\energy-proxy\deploy\energy-stack\`. **Deployment is automatic via GitHub Actions** — `git push` to `main` triggers the [Deploy to Pi workflow](../../.github/workflows/deploy.yml), which runs on a GitHub-hosted `ubuntu-latest` runner. The runner joins our Tailscale tailnet as an ephemeral `tag:ci` node, SSHes to Pi-lab via Tailscale SSH (no OpenSSH keys in GHA secrets — short-lived per-deploy certs issued by tailscaled), rsyncs the changed compose project into place, runs `docker compose build && docker compose up -d`, and verifies services are healthy. Single-service deploys complete in ~60-120 s. Detection uses `git diff HEAD^ HEAD` (single-commit lookback) — multi-commit pushes touching `deploy/**` in earlier commits may need a manual workflow_dispatch to deploy.

The prior topology was a self-hosted runner installed on Pi-lab itself. It was retired ahead of the public-repo flip (self-hosted runners executing arbitrary code from public-repo PRs is a known security anti-pattern). The Tailscale-based replacement preserves the "merge to deploy" UX with zero public ingress on Pi.

Manual deployment is still supported for local-only testing:

```bash
rsync -av --delete --exclude '.env' \
  "D:/Projects/energy-proxy/deploy/energy-stack/" \
  chris@192.168.20.10:~/energy-stack/
ssh chris@192.168.20.10 \
  "cd ~/energy-stack && docker compose build hvac-scheduler && docker compose up -d hvac-scheduler"
```

But anything you push to `main` will overwrite local-only edits on the next deploy. Best practice is one of: commit your change → push → CI deploys, OR work on a branch.

The `.env` file lives ONLY on Pi-lab (chmod 600). Never committed (`.gitignore`), never rsynced (CI workflow excludes it, manual rsync above excludes it). The SOPS-encrypted equivalent (`secrets/env.sops.env`) IS committed and IS deployed — that's the recovery path.

To force a redeploy without making a code change: GitHub Actions UI → "Deploy to Pi" workflow → "Run workflow" → pick `energy-stack`.

### Tailscale (deploy runner)

The deploy pipeline depends on Tailscale; the operational surface is small but worth knowing:

- **Pi-side daemon**: `tailscaled` runs on Pi-lab tagged `tag:server`, with `--ssh` enabled so tailscaled mediates SSH on the tailscale interface. The Pi's `100.x` address is reachable from any tailnet member; the WAN-side has no SSH ingress at all.
- **Runner-side**: each deploy job uses [`tailscale/github-action@v3`](https://github.com/tailscale/github-action) to mint a one-time auth key (via the `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` repo secrets), join the tailnet as ephemeral `tag:ci`, then auto-deregister at job end. No persistent node, no key reuse.
- **ACL**: defined at <https://login.tailscale.com/admin/acls/file>. The contract: `tag:ci` may reach `tag:server:22` only — no other ports, no other devices. A compromised secret can't pivot anywhere else on the tailnet. Edit ACLs only when adding new server nodes or new CI sources.
- **Per-deploy audit**: every job leaves a connection-event trail in <https://login.tailscale.com/admin/machines> (look at the ephemeral `github-actions-*` node) and the corresponding GitHub Actions run. Time-aligned across both surfaces.
- **Kill-switch**: rotate the OAuth client at <https://login.tailscale.com/admin/settings/oauth>. The Tailscale side is single-rotation; the new client secret then needs updating in GitHub repo secrets. All in-flight deploys finish; all future deploys fail until the secret is updated.
- **SOC integration (optional)**: Tailscale supports streaming network + audit logs as JSON to Splunk/Datadog/Mezmo/S3. Configure at <https://login.tailscale.com/admin/logs>. Not currently wired into our SOC pipeline; documented here as the path when we are.

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

## Running tests

Pure-logic unit tests live alongside each service's source as `test_<service>.py` (not shipped to the container — every Dockerfile `COPY`s only the production source modules; test files live alongside but are excluded by virtue of not being listed). Tests run on the operator's machine against the source.

One-time setup:
```bash
pip install -r deploy/energy-stack/requirements-dev.lock
# Plus each service's runtime requirements for its imports:
pip install -r deploy/energy-stack/<service>/requirements.txt
```

Run all service tests in one go via the wrapper:
```bash
bash deploy/energy-stack/run_tests.sh
```

Or focus on one service:
```bash
cd deploy/energy-stack/<service>/
python -m pytest .
```

> Why a wrapper rather than a single `pytest` invocation: every service has its own `app.py` (or `poller.py`) source file, and pytest's default `prepend` import mode caches the first one it loads in `sys.modules`. A single `pytest deploy/energy-stack` call would let one service's tests "see" another service's app.py. The wrapper runs each service in its own pytest process, sidestepping the collision. Note: every service is now a proper Python package (`<service>/__init__.py` + relative imports + `python -m <service>.<entrypoint>`), so package isolation is real at import time — the wrapper survives only because pytest discovery would still flatten the modules into `sys.modules` under shared names. See [pytest.ini](pytest.ini) for context.

`pytest.ini` at `deploy/energy-stack/` is the single source of truth for shared config (currently `asyncio_mode = auto` for the async-using services). Pytest's config discovery walks up from cwd, so individual services don't carry their own.

Currently covered (22 test files across 11 services + scripts/ suite as of 2026-05-18):

- `comed_poller/test_comed_poller.py`
- `eagle_poller/test_eagle_poller.py`
- `ecowitt_ingest/test_ecowitt_ingest.py`
- `haven_ingest/test_haven_ingest.py`
- `hvac_scheduler/` — five test files: `test_hvac_scheduler.py` (release_hold action, lazy decision recompute, safety supervisor), `test_decision_trace.py` (decision_trace.* event family), `test_pjm_5cp.py` (5CP detector), `test_precool.py` (§7 cheap-window search), `test_price_overlay.py` (tier state machine), `test_integration_2025_replay.py` (offline replay against 2025 PJM data)
- `hvac_scheduler_watchdog/test_hvac_scheduler_watchdog.py`
- `nws_poller/test_nws_poller.py`
- `pjm_dm2_poller/test_pjm_dm2_poller.py`
- `refoss_poller/test_refoss_poller.py`
- `telegram_notifier/test_telegram_notifier.py`
- `thermostat_poller/test_thermostat_poller.py`
- `scripts/tests/` — four test files: `test_backfill_pjm.py`, `test_influx.py`, `test_parser.py`, `test_scrape_pjm_5cp_pdf.py` (run automatically by `run_tests.sh` extras block)

Services without dedicated test files: `influx-init`, `mosquitto-init` (one-shot provisioning shell), `telegraf` (config-only). The compose-controlled containers (`influxdb`, `grafana`, `loki`, `promtail`, `mosquitto`) are all upstream images with no Python under test.

> **History note**: until 2026-05-07 every service's tests file was named `tests.py`. The CodeX review caught that pytest couldn't collect more than one of them in a single invocation (duplicate top-level `tests` module name). The rename to `test_<service>.py` makes single-pass collection from the stack root work correctly.

## Type checking

Every Python service in this stack — plus `cockpit/backend` — is enforced under `mypy --strict` and an import-linter contract. The enforcement is the project's structural defense against the freshness-class drift that motivated the 2026-05 type-checker rollout (see archived plan at `docs/plans/archive/type-checker-plan.md` and shipped spec at `docs/superpowers/specs/2026-05-20-type-checker-design.md`).

Run locally:

```bash
bash deploy/energy-stack/run_typecheck.sh
```

The wrapper invokes mypy per-target (each service in `service_dirs`, plus `cockpit/backend` in `repo_targets`) so an error in one service doesn't mask errors in another. Final step runs `import-linter` against the active contract.

**Contract currently enforced**: `Only influx_adapter may import influxdb_client`. Direct use of `influxdb_client.*` outside `hvac_scheduler/influx_adapter.py` is a build failure. Adapter pattern documented in `docs/type-debt-backlog.md` (which also tracks future adapter candidates like the `ThermostatClient` device-client seam).

**Test-relaxation overrides**: each service's `test_*.py` module appears in the `[[tool.mypy.overrides]]` block of `pyproject.toml` with `disallow_untyped_defs = false` + `disallow_untyped_decorators = false`. Production code is strict; tests get a controlled relaxation per spec §5.6. Other strict-mode flags (`disallow_incomplete_defs`, `disallow_any_generics`) still apply to tests — partial annotations must be completed.

**CI enforcement**: `.github/workflows/typecheck.yml` runs the same `run_typecheck.sh` invocation on every push + PR to `main`. Required-status-check rule is set so a red type-check blocks merge.

**Tactical `# type: ignore` discipline**: every ignore must carry an error code (`# type: ignore[no-untyped-call]`, etc.) AND a cited reason. Bare `# type: ignore` is a pattern violation. Concentrated ignores on `influxdb_client` imports + Point chain heads + `close()` are expected because the upstream stubs are incomplete at those surfaces.

## Ports

| Port | Service | Reachable from |
|---|---|---|
| 8086 | InfluxDB API + UI | Trusted VLAN 10 (ZBF: Trusted→Homelab), Pi-lab localhost |
| 3000 | Grafana UI | Trusted VLAN 10 (ZBF: Trusted→Homelab), Pi-lab localhost |
| 3100 | Loki (Grafana + cockpit query it via container network) | Reachable on the homelab 192.168.20.x subnet (used by the workstation dev cockpit per `cockpit/.env.example`) + Pi-lab localhost |
| 8088 | ecowitt-ingest HTTP receiver | LAN — GW1200B pushes here every 60 s |
| 8765 | cockpit (Controller Cockpit UI + API) | Trusted VLAN 10 (ZBF: Trusted→Homelab), Pi-lab localhost |

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
| `hvac_scheduler_data` | hvac-scheduler | `/data/overrides.json` (manual / vacation overrides). (`/data/director_token.json` was the Control4 token of the retired pyControl4 path — no longer written behind the `ThermostatClient` seam.) |
| `thermostat_poller_data` | thermostat-poller | (`/data/director_token.json` was its own Control4 token under the retired pyControl4 path — no longer written behind the `ThermostatClient` seam.) |
| `haven_ingest_data` | haven-ingest | `/data/haven_token.json` (path overridable via `HAVEN_TOKEN_FILE`) — HAVEN Auth0 refresh token, rotates on every refresh and persisted across restarts |
| `loki_data` | loki | Log chunks (7-day retention configured in `loki/loki-config.yml`) |
| `promtail_positions` | promtail | Cursor positions per container log file (avoids re-shipping after restart) |

## ComfortNet pipeline deployment (profile=mqtt)

The Mosquitto broker, password-init container, and Telegraf consumer are profile-gated so the standard `compose up -d` ignores them. They run only when `--profile mqtt` is set or `COMPOSE_PROFILES=mqtt` is in the environment. Live publisher: [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet). Historical design: [`../../docs/archive/COMFORTNET_PIPELINE.md`](../../docs/archive/COMFORTNET_PIPELINE.md).

**One-time setup on Pi-lab:**

```bash
# 1. Generate TLS material (CA + server cert + server key, 10-year expiry).
ssh chris@192.168.20.10
sudo mkdir -p /opt/mosquitto-certs
cd /opt/mosquitto-certs
sudo OUT_DIR=. sh ~/energy-stack/mosquitto/scripts/gen-certs.sh
sudo chown -R 1883:1883 /opt/mosquitto-certs
# ca.key stays on Pi-lab (back it up offline if you want easy renewal).

# 2. Distribute ca.crt to clients.
# Pi 3B publisher (live as of May 2026):
scp /opt/mosquitto-certs/ca.crt comfortnet:/etc/comfortnet/ca.crt

# 3. Set the three MQTT passwords in .env (see .env.example for the keys).
# Then re-encrypt secrets/env.sops.env per the SOPS section below.
```

**Bring the pipeline up:**

```bash
docker compose --profile mqtt up -d
# or (persistent across deploys):
echo 'COMPOSE_PROFILES=mqtt' >> .env
docker compose up -d
```

**Smoke test from Pi-lab** (broker manual publish, useful for verifying broker config independent of the live publisher):

```bash
# Subscribe in one terminal
mosquitto_sub -h localhost -p 8883 --cafile /opt/mosquitto-certs/ca.crt \
  -u telegraf -P "$MOSQUITTO_TELEGRAF_PASSWORD" \
  -t 'home/utility-room/hvac/comfortnet/+'

# Publish a test message in another
mosquitto_pub -h localhost -p 8883 --cafile /opt/mosquitto-certs/ca.crt \
  -u comfortnet-publisher -P "$MOSQUITTO_PUBLISHER_PASSWORD" \
  -t 'home/utility-room/hvac/comfortnet/heat_actual_pct' \
  -m '{"value": 35.0, "ts": "2026-05-05T22:00:00Z"}'

# Check it landed in InfluxDB
docker exec -it influxdb influx query \
  'from(bucket:"energy") |> range(start:-1m) |> filter(fn:(r) => r._measurement == "home/utility-room/hvac/comfortnet/heat_actual_pct") |> last()'
```

The broker stays up across `compose up -d` runs. To stop: `docker compose --profile mqtt stop mosquitto telegraf mosquitto-init`.

**Cert renewal**: `gen-certs.sh` is safe to re-run; it'll skip CA generation if `ca.key` and `ca.crt` already exist, and regenerate `server.crt` + `server.key` against the same CA. Restart the broker after renewal: `docker compose restart mosquitto`. Clients (publisher, Telegraf) only need the CA cert and don't change.

## Related

- Top-level project: [`../../README.md`](../../README.md)
- Project history & decisions: [`../../docs/PROJECT.md`](../../docs/PROJECT.md)
- Per-service detail: [`../../docs/SERVICES.md`](../../docs/SERVICES.md)
- HVAC controller logic: [`../../docs/superpowers/specs/2026-06-20-commissioning-controller-design.md`](../../docs/superpowers/specs/2026-06-20-commissioning-controller-design.md)
- Backup/restore: [`backup/RESTORE.md`](backup/RESTORE.md)
- Infra context (VLAN/ZBF/Pi-lab state): `D:\Projects\Network_Management\CLAUDE.md`
