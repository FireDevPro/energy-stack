# n8n-stack

Self-hosted [n8n](https://n8n.io) (workflow automation) on Pi-lab, alongside the energy-stack. Postgres-backed for hot backups and future scale.

URL once running: **http://192.168.20.10:5678/**

## Why this exists

Companion to the bespoke Python services in `../energy-stack/`. Use n8n for:

- Simple ETL (incoming webhooks → InfluxDB)
- Cron-driven HTTP polls
- Threshold alerts → Telegram / email
- Anything that's "fetch JSON, transform a bit, push elsewhere"

Keep bespoke Python for: complex stateful logic (the HVAC scheduler), SDK integrations (pyControl4), tight cost-integration math, sub-minute polling.

First planned workflow: **Ecowitt receiver** (when GW1200 hardware arrives) — accept HTTP POST from the gateway every 60 s, parse, write to `ecowitt.outdoor` measurement in InfluxDB.

## First-time bootstrap

```bash
ssh chris@192.168.20.10
cd ~/n8n
cp .env.example .env
chmod 600 .env

# Generate encryption key + Postgres password
echo "POSTGRES_PASSWORD=$(openssl rand -base64 32)" >> .env
echo "N8N_ENCRYPTION_KEY=$(openssl rand -base64 32)" >> .env
# Edit .env to remove the placeholder lines after generating

docker compose pull
docker compose up -d
docker compose ps
```

First load at http://192.168.20.10:5678/ prompts to create the owner account. Pick whatever email + strong password — only used for n8n UI login.

## Authoring & deployment

Author on Windows under `D:\Projects\energy-proxy\deploy\n8n-stack\`. Deploy compose changes with:

```bash
rsync -av --delete --exclude '.env' \
  "D:/Projects/energy-proxy/deploy/n8n-stack/" \
  chris@192.168.20.10:~/n8n/
```

Workflows themselves are stored in Postgres, not in this repo. Export individual workflows from the n8n UI → File → Save as → JSON → commit to `workflows/<name>.json` if you want them version-controlled.

## Operations

| Task | Command |
|---|---|
| View status | `docker compose ps` |
| Tail logs | `docker compose logs -f n8n` |
| Restart n8n | `docker compose restart n8n` |
| Stop (keep data) | `docker compose down` |
| Start | `docker compose up -d` |
| Upgrade | `docker compose pull && docker compose up -d` |
| Postgres dump | `docker compose exec postgres pg_dump -U n8n n8n > /tmp/n8n-dump-$(date +%F).sql` |
| **Destroy all data** | `docker compose down -v` (irreversible) |

## Networking

n8n runs on its own Docker network (n8n-stack default). To reach **InfluxDB** in the energy-stack, use the **Pi's LAN IP** rather than container names:

- InfluxDB: `http://192.168.20.10:8086` (token in n8n credentials)
- Refoss: `http://192.168.20.140` (per-circuit power)
- Eagle: `https://192.168.20.192` (basic auth)
- ComEd: `https://hourlypricing.comed.com/api`

For incoming webhooks (e.g. Ecowitt → n8n), expose the n8n webhook URL to the source device. Standard pattern is `http://192.168.20.10:5678/webhook/<id>` (n8n provides the path per workflow). Devices on other VLANs may need firewall rules to reach Pi-lab.

## n8n MCP integration

n8n now ships its own **official Instance-level MCP server** that supersedes the older community options (`czlonkowski/n8n-mcp` etc.). It exposes:

- Knowledge of all n8n nodes and their properties (so Claude can design accurate workflows)
- Workflow management (list, read, create, update workflows)
- Data tables access

**Setup:**

1. In n8n UI: **Settings → Instance-level MCP** (requires owner/admin role). Enable the MCP server and generate an API key.
2. Add to your Claude Desktop / Claude Code MCP config (exact format per the [official setup docs](https://docs.n8n.io/advanced-ai/mcp/accessing-n8n-mcp-server/)) — typically `N8N_API_URL=http://192.168.20.10:5678/api/v1` and `N8N_API_KEY=<key from step 1>`.

Once configured, Claude can describe a workflow in English and the n8n MCP builds it directly inside this n8n instance.

There's also the separate **MCP Server Trigger node** for the reverse direction — exposing individual workflows AS MCP tools that Claude can call (e.g. "n8n workflow that fetches today's energy cost" becomes an MCP tool in your Claude session).

## Backups

n8n persists everything in:
- **`postgres_data` volume** — workflows, executions, credentials
- **`n8n_data` volume** — encryption key, instance config, logs

Both volumes live under `/var/lib/docker/volumes/n8n-stack_*/`. The nightly `pi-backup.sh` script runs a Postgres dump of the n8n container before restic archives `/home/chris/n8n` (alongside the existing InfluxDB backup), so workflows, executions, and credentials are captured each cycle.

## Related

- Energy-stack: `../energy-stack/` (separate compose project)
- Top-level project: `../../README.md`
