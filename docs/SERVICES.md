# Services

Per-service reference for the energy-stack Docker Compose project. Companion to [`deploy/energy-stack/README.md`](../deploy/energy-stack/README.md) (which covers operational tasks across the whole stack).

## Contents

- [influxdb](#influxdb) — time-series storage
- [grafana](#grafana) — visualization
- [eagle-poller](#eagle-poller) — billing-grade smart meter
- [comed-poller](#comed-poller) — ComEd Hourly Pricing
- [refoss-poller](#refoss-poller) — per-circuit power
- [nws-poller](#nws-poller) — weather forecast + alerts
- [hvac-scheduler](#hvac-scheduler) — Control4 setpoint pushes
- [thermostat-poller](#thermostat-poller) — continuous thermostat reads + override detection
- [haven-ingest](#haven-ingest) — Haven IAQ CSV ingest
- [telegram-notifier](#telegram-notifier) — daily summary + alert checker
- [webdashboard](#webdashboard) — nginx static
- [webdashboard-api](#webdashboard-api) — FastAPI live data backend
- [loki & promtail](#loki--promtail) — log aggregation

---

## influxdb

Image: `influxdb:2.7` · Port: `8086` · Volumes: `influxdb_data`, `influxdb_config`

Single-node InfluxDB 2.7. Bootstraps org/bucket/admin user **only on first run** against an empty `influxdb_data` volume. After that the env vars are ignored — re-bootstrapping requires destroying the volume.

**Bootstrap env (first-run only):**
- `DOCKER_INFLUXDB_INIT_USERNAME` — admin user
- `DOCKER_INFLUXDB_INIT_PASSWORD` — admin password
- `DOCKER_INFLUXDB_INIT_ORG` — org name
- `DOCKER_INFLUXDB_INIT_BUCKET` — bucket name (typically `energy`)
- `DOCKER_INFLUXDB_INIT_RETENTION` — typically empty (infinite)
- `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` — admin token (also used by every poller and Grafana — single-token deployment)

**Healthcheck:** `influx ping` every 30 s. Most depends_on rules in compose use `condition: service_healthy` to wait for InfluxDB before starting downstream services.

**Measurements written by other services:**

| Measurement | Written by | Notes |
|---|---|---|
| `eagle.meter` | eagle-poller | demand_kw, delivered_kwh, received_kwh |
| `comed.prices` | comed-poller | tag `period_type`: `5min`, `hourly_avg` |
| `refoss.channel` | refoss-poller | per `em:N` channel, power/voltage/current/PF/energy buckets |
| `refoss.system` | refoss-poller | uptime, RSSI, cfg_rev |
| `nws.forecast` | nws-poller | tag `for_period`: `today`, `tomorrow`, `day2`; high_f, max_dewpoint_f, is_heat_advisory, alert_summary |
| `nws.observation` | nws-poller | nearest-station observation when available |
| `hvac.decisions` | hvac-scheduler | tag `decision_for_date`, `day_type`; high_f, dewpoint, reason, comed_price_at_decision |
| `hvac.actions` | hvac-scheduler | tag `action_label`, `day_type`, `dry_run`; cool_setpoint_f, heat_setpoint_f, fan_mode, applied, error, thermostat snapshot before |
| `telegram.alerts` | telegram-notifier | dedupe state for fired alerts |

**Operations:**

```bash
# CLI shell
docker exec -it influxdb influx

# One-shot query
docker exec influxdb influx query \
  'from(bucket:"energy") |> range(start:-5m) |> limit(n:1)'

# Manual backup (used by pi-backup.sh nightly)
docker exec influxdb influx backup /tmp/backup -t "$INFLUXDB_INIT_ADMIN_TOKEN"
docker cp influxdb:/tmp/backup ./backup-$(date +%F)
docker exec influxdb rm -rf /tmp/backup

# Restore (overwrites everything)
docker cp ./backup influxdb:/tmp/restore
docker exec influxdb influx restore /tmp/restore -t "$INFLUXDB_INIT_ADMIN_TOKEN" --full
```

---

## grafana

Image: `grafana/grafana-oss:11.4.0` · Port: `3000` · Volumes: `grafana_data`, `./grafana/provisioning:ro`, `./grafana/dashboards:ro`

InfluxDB and Loki provisioned as datasources (read-only via `editable: false`). Three dashboards provisioned from disk:

| Dashboard | UID | Purpose |
|---|---|---|
| Home Energy — Full | (in `grafana/dashboards/home-energy-full.json`) | Whole-stack view: prices, mains, per-circuit, HVAC scheduler, equipment-health row |
| Home Energy — Overview | (in `home-energy-overview.json`) | High-level (cost, demand, indoor temp) |
| HVAC Scheduler | (in `hvac-scheduler.json`) | Day-type history, action timeline, setpoint vs indoor temp |

**Env:**
- `GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD` — Grafana admin login
- `GF_USERS_ALLOW_SIGN_UP=false`, `GF_AUTH_ANONYMOUS_ENABLED=false` — locked down
- `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET` — used by datasource provisioning at startup

**Healthcheck:** `wget /api/health` every 30 s (must report `database: ok`).

**Editing dashboards:** modify JSON in `grafana/dashboards/`, restart Grafana (`docker compose restart grafana`) — provisioning is read-only at runtime, so UI edits to provisioned dashboards don't persist after restart. To iterate: edit in UI → Dashboard Settings → JSON Model → save back to file → commit.

---

## eagle-poller

Build: `./eagle-poller` · Cycle: `EAGLE_POLL_INTERVAL` (default 30 s)

Polls the Rainforest EAGLE-3 Local API (XML over HTTPS, basic auth, self-signed cert) at `https://192.168.20.192:443/cgi-bin/post_manager`. Reads three Zigbee variables per cycle (one `device_query` each — the firmware silently drops list positions ≥ 2).

**Env:**
- `EAGLE_IP`, `EAGLE_CLOUD_ID`, `EAGLE_INSTALL_CODE` — device + auth
- `EAGLE_METER_HW_ADDRESS` — Zigbee meter address (e.g., `0x001350050037ac6b`); poller exits with status 3 if not present in `device_list` at startup
- `EAGLE_POLL_INTERVAL` — seconds (default 30)
- `INFLUXDB_*` — standard four

**Writes** (measurement `eagle.meter`):

| Field | Source | Meaning |
|---|---|---|
| `demand_kw` | `zigbee:InstantaneousDemand` | Real-time draw, signed (negative = export) |
| `delivered_kwh` | `zigbee:CurrentSummationDelivered` | Monotonic, billing-grade |
| `received_kwh` | `zigbee:CurrentSummationReceived` | Currently 0 (no solar) |

Tags: `hw_address`, `source=eagle3`.

**Healthcheck:** marker file `/tmp/last_poll_ok` touched after each successful cycle; Docker HEALTHCHECK fails if file is older than 5 min.

**Failure modes:**
- EAGLE-3 read failure → logged + retried next cycle (no crash)
- InfluxDB write failure → container exits, `restart: unless-stopped` brings it back (we want the signal)

**Design notes:** [`docs/phase-3.3-eagle-poller-design.md`](phase-3.3-eagle-poller-design.md)

---

## comed-poller

Build: `./comed-poller` · Cycle: `COMED_POLL_INTERVAL` (default 60 s)

Polls the public ComEd Hourly Pricing API (`hourlypricing.comed.com/api`). Two endpoints per cycle:
- `?type=5minutefeed&format=json` — last ~24 h of 5-min intervals (latest written each cycle)
- `?type=currenthouraverage&format=json` — current hour average (idempotent — overwrites same hour-truncated timestamp)

**Env:** `COMED_POLL_INTERVAL`, `COMED_API_BASE`, `INFLUXDB_*`

**Writes** (measurement `comed.prices`, field `price_cents_per_kwh`):

| Tag value | Description | Timestamp source |
|---|---|---|
| `period_type=5min` | Latest 5-min interval | `millisUTC` from API |
| `period_type=hourly_avg` | Current hour average | Hour-truncated UTC (idempotent — repeated polls upsert) |

> **Note:** ComEd does NOT publicly expose day-ahead forecast prices. The undocumented `?type=daynexttoday` endpoint returns today's *settled* day-ahead prices (not tomorrow's). PJM DataMiner2 is the upstream source but requires an API key gated by member status. See [PROJECT.md decision log](../PROJECT.md).

**Healthcheck:** `/tmp/last_poll_ok` marker, same pattern as eagle-poller.

---

## refoss-poller

Build: `./refoss-poller` · Cycle: `REFOSS_POLL_INTERVAL` (default 30 s)

Polls the Refoss EM16P at `http://<REFOSS_IP>/rpc` (HTTP, no auth — local LAN, single-user threat model). One `Refoss.Status.Get` call per cycle returns all 18 `em:N` channels and `sys`/`wifi` blocks.

Channel labels (`name` field) are pulled from the device itself via `Refoss.Config.Get` and cached. Cache refreshes when device's `sys.cfg_rev` counter increments — renaming a circuit in the Refoss app propagates within one poll cycle.

**Env:** `REFOSS_IP`, `REFOSS_POLL_INTERVAL`, `INFLUXDB_*`

**Writes:**

| Measurement | Tags | Fields |
|---|---|---|
| `refoss.channel` | `channel` (`em:1`..`em:18`), `name` | `power_w`, `voltage_v`, `current_a`, `power_factor`, `day_energy_kwh`, `day_ret_energy_kwh`, `week_energy_kwh`, `week_ret_energy_kwh`, `month_energy_kwh`, `month_ret_energy_kwh` |
| `refoss.system` | (none) | `uptime_s`, `wifi_rssi_dbm`, `cfg_rev` |

**Critical: energy fields are bucketed, not lifetime totalizers.** `day_energy_kwh` resets at midnight, `week_energy_kwh` on Monday, `month_energy_kwh` on the 1st — inside the device. For monotonically-increasing cumulative kWh in Grafana, integrate `power_w` over time in Flux.

**Whole-home aggregate:** firmware 3.1.8 returns empty `emmerge:N` rollups, so `em:1 + em:7` (the two split-phase mains) is the correct whole-home aggregate.

**Polarity quirk:** `em:5` (Master Bedroom) and `em:14` (Basement Micro/Fridge) report negative power because the CT polarity is flipped on these B-phase circuits. Fix in the Refoss app by setting per-channel `factor=-1` — poller picks it up automatically on next cfg_rev change.

**Healthcheck:** `/tmp/last_poll_ok` marker.

---

## nws-poller

Build: `./nws-poller` · Cycle: `NWS_POLL_INTERVAL` (default 1800 s = 30 min)

Polls `api.weather.gov` (no key, but a `User-Agent` header is required and identifying). Resolves the configured lat/lon to a gridpoint on first call, then fetches:
- Hourly forecast (next 7 days)
- Daily forecast (today/tomorrow/day2 high/low/dewpoint summarized)
- Active alerts for the area (heat advisory, cold warning, etc.)

**Env:**
- `NWS_LAT`, `NWS_LON` — coordinates (default Plainfield IL: 41.6151, -88.2018)
- `NWS_USER_AGENT` — identification per NWS policy (e.g., `energy-stack/1.0 (contact@local)`)
- `NWS_POLL_INTERVAL` — seconds (default 1800)
- `INFLUXDB_*`

**Writes** (measurement `nws.forecast`):

| Tag | Field | Notes |
|---|---|---|
| `for_period=today`, `tomorrow`, `day2` | `high_f`, `low_f`, `max_dewpoint_f`, `precip_prob`, `is_heat_advisory` (0/1), `alert_summary` (string) | One point per period per poll; idempotent on the period's date |
| `for_period=hourly` | `temp_f`, `dewpoint_f`, `precip_prob`, `cloud_cover_pct` | Hourly granularity, 7-day horizon |

**Important:** the gridpoint resolution call can be slow on first cycle — poller uses 30 s aiohttp timeout (was 15 s earlier, increased after observed timeouts).

**Healthcheck:** `/tmp/last_poll_ok` marker. Allows up to 5 min staleness (poll interval is 30 min, so the marker is touched every 30 min normally).

---

## hvac-scheduler

Build: `./hvac-scheduler` · Cycle: 1-min ticker · Volume: `hvac_scheduler_data` (`/data`)

Decides tomorrow's day-type at 21:00 local, then fires schedule actions throughout the next day. Each action sets cool + heat setpoints (heat always paired for Auto-mode safety), optionally sets fan mode, then pins `Hold = Permanent` so the thermostat baseline doesn't override.

**Auth path:** Pi → Control4 EA-5 (`192.168.1.30`) via pyControl4 v2.0.2 → Honeywell VisionPRO via Cinegration C4 driver → TCC cloud → physical thermostat. Token persisted at `/data/director_token.json`. Reauth on 401 with fresh `get_account_bearer_token` → `get_director_bearer_token`.

**Env:**
- `CONTROL4_EMAIL`, `CONTROL4_PASSWORD` — Control4 cloud login
- `CONTROL4_CONTROLLER_IP` — local Director (default `192.168.1.30`)
- `CONTROL4_THERMOSTAT_ID` — item ID of the THERMOSTAT proxy (default `3231` — **NOT** `3230` which is the Cinegration backing driver)
- `SCHEDULER_DRY_RUN` — if `true`, logs actions but doesn't push (default `true` until you flip it; safer)
- `SCHEDULER_DECISION_HOUR` — hour-of-day for daily decision (default 21)
- `SCHEDULER_TZ` — IANA tz (default `America/Chicago`)
- `INFLUXDB_*`

**Writes:**

| Measurement | Tags | Fields |
|---|---|---|
| `hvac.decisions` | `decision_for_date`, `day_type` | `high_f`, `max_dewpoint_f`, `is_heat_advisory`, `alert_summary`, `reason`, `comed_price_at_decision` |
| `hvac.actions` | `day_type`, `action_label`, `dry_run` | `cool_setpoint_f`, `heat_setpoint_f`, `fan_mode`, `setpoint_reason` (`standard`/`humid_override`), `applied` (0/1), `error`, plus thermostat-state-before snapshot (`indoor_temp_before_f`, `cool_setpoint_before_f`, etc.) |

**Override mechanism** (`/data/overrides.json`): manual day-type forces (e.g., "today is a holiday, force NORMAL") or full vacation flat-setpoint mode. Format and examples documented in [`HVAC_LOGIC.md`](HVAC_LOGIC.md#overrides).

**Healthcheck:** `/tmp/last_tick_ok` touched every minute regardless of whether actions fired — failure means the scheduler is wedged.

**Detailed schedule logic, day-type decision tree, ASHRAE 55 humidity math, ISU settings:** [`HVAC_LOGIC.md`](HVAC_LOGIC.md).

---

## thermostat-poller

Build: `./thermostat-poller` · Cycle: `THERMOSTAT_POLL_INTERVAL` (default 600 s = 10 min, the TCC rate-limit floor) · Volume: `thermostat_poller_data` (`/data`)

Continuous reads of VisionPRO state via Control4 EA-5. Independent of `hvac-scheduler` — has its own persisted Control4 token at `/data/director_token.json`. The two services sharing the same Control4 account is fine; the bearer token is per-token, not per-process.

**Two outputs:**

1. **`hvac.thermostat`** — every poll cycle:

   | Tag | Field | Notes |
   |---|---|---|
   | `thermostat_id` | `indoor_temp_f`, `humidity_pct`, `cool_setpoint_f`, `heat_setpoint_f`, `hvac_mode`, `hvac_state` (running/idle), `fan_mode`, `hold_mode` | Continuous time-series of full thermostat state. Use for Grafana panels, calibration vs. Haven, anomaly detection. |

2. **`hvac.overrides`** — only when current setpoints differ from the last `hvac.actions` row by ≥ 0.5°F AND the last action was > `OVERRIDE_GRACE_MIN` ago (default 5 min):

   | Tag | Field | Notes |
   |---|---|---|
   | `thermostat_id`, `source="manual_override"` | `expected_cool_setpoint_f`, `actual_cool_setpoint_f`, `delta_cool_f`, `expected_heat_setpoint_f`, `actual_heat_setpoint_f`, `delta_heat_f`, `last_action_label`, `minutes_since_last_action`, `indoor_temp_f`, `humidity_pct`, `hvac_mode` | One row per poll cycle while overridden. v1 doesn't dedupe — at 10-min cadence, the data volume is tiny. |

**Env:**
- `CONTROL4_EMAIL`, `CONTROL4_PASSWORD`, `CONTROL4_CONTROLLER_IP`, `CONTROL4_THERMOSTAT_ID` — same as `hvac-scheduler`
- `THERMOSTAT_POLL_INTERVAL` — seconds (default 600)
- `OVERRIDE_GRACE_MIN` — minutes after a scheduler action before counting setpoint mismatch as an override (default 5)
- `INFLUXDB_*` — standard

**Healthcheck:** `/tmp/last_poll_ok` marker, `find -mmin -15` (allows up to 15 min staleness on a 10-min poll interval).

**Why this exists:** the `hvac-scheduler` snapshots thermostat state only at action-firing moments (~4-7 timestamps per day). That's too sparse for proper time-series correlation against Haven's 5-min cadence, and provides no foundation for override detection. This poller fills both gaps.

---

## haven-ingest

Build: `./haven-ingest` · Cycle: `HAVEN_SCAN_INTERVAL` (default 60 s) · Bind mount: `./inbox/haven:/inbox/haven`

Watches `~/energy-stack/inbox/haven/` for Haven IAQ CSV exports from the homeowner portal (my.haveniaq.com). Filename pattern: `CAM_<device-id>_<start>_to_<end>.csv`. Parses each file, writes points to InfluxDB, then moves to `inbox/haven/processed/` on success or `inbox/haven/failed/` on error (with a sidecar `.error` file containing the parse error).

**Why CSV instead of API:** Haven has no public API at any tier (verified May 2026 via dealer-portal probe + Pi-hole DNS analysis showing the device only talks to `haven-r1.azure-devices.net` over Azure IoT Hub MQTT with cert pinning). The homeowner portal's CSV export is the only way out.

**Env:**
- `HAVEN_INBOX_DIR` — directory to watch (default `/inbox/haven`)
- `HAVEN_SCAN_INTERVAL` — seconds between directory scans (default 60)
- `INFLUXDB_*` — standard

**Writes** (measurement `haven.airquality`):

| Tag | Field | Coverage | Notes |
|---|---|---|---|
| `device_id` (from filename, e.g. `0000-6267`) | `temp_f`, `temp_c`, `humidity_pct`, `tvoc_ppb` | 100% | Continuous — sensors don't need flowing air |
| | `pm25_ugm3`, `airflow_cfm` | ~3% | Flow-dependent — only populated when blower is running |
| | `pm25_status`, `tvoc_status`, `combined_status` | 100% | "good" / "fair" / "poor" buckets |

**Idempotent:** all points use the timestamp from the CSV (ISO 8601 UTC). Re-importing the same week is a no-op (Influx upserts on measurement+tags+timestamp).

**Workflow:**
1. Export weekly from my.haveniaq.com → save somewhere accessible (e.g. Downloads)
2. Drop the CSV into `~/energy-stack/inbox/haven/` on Pi-lab (`scp` from Windows works)
3. Within 60 s, the service ingests it and moves to `inbox/haven/processed/`
4. If something goes wrong, file lands in `inbox/haven/failed/` with `.error` sidecar — fix the issue, drop it back in the inbox

**Manual trigger:**
```bash
# Force an immediate scan (bypass the 60s wait)
docker compose restart haven-ingest
```

**Healthcheck:** `/tmp/last_scan_ok` marker, `find -mmin -5`.

**Crucial flow-dependent insight:** the sparse `airflow_cfm` and `pm25_ugm3` rows aren't a defect — they're **only populated when the blower is moving air past the duct sensor**. This means:
- Non-null `airflow_cfm` rows = blower runtime ground truth (cross-validate against Refoss em:9 furnace blower power)
- Non-null `airflow_cfm` value = real measured CFM at that moment, useful for delivered-BTU calc when paired with future supply-air temp instrumentation

---

## telegram-notifier

Build: `./telegram-notifier` · Two cycles: daily summary at `SUMMARY_HOUR` + alert check every `ALERT_CHECK_INTERVAL_S` seconds

Single-bot Telegram client (`@EnergyStackBot`, separate from any other Telegram bots Chris uses). Sends:

- **Daily summary** at 8 AM local (HTML-formatted): yesterday's cost, kWh, peak demand, fridge anomaly check, HVAC schedule fired, weather forecast for today.
- **Alerts**, checked every 5 min (deduplicated 30 min): poller silent (no marker file write in 10 min), price spike (current 5min > $TELEGRAM_PRICE_SPIKE_C ¢/kWh), fridge anomaly (recent-6h mean > 1.5× and Δ > 50 W vs. 14-day baseline).
- **Backup notifications** (separate path — not by this service, but by `pi-backup.sh` reading the same `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` from `.env`).

**Env:**
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — bot creds + recipient
- `SUMMARY_HOUR` — hour-of-day for daily summary (default 8)
- `ALERT_CHECK_INTERVAL_S` — seconds between alert checks (default 300)
- `POLLER_SILENT_MIN` — minutes of silence before flagging a poller (default 10)
- `PRICE_SPIKE_THRESHOLD_C` — ¢/kWh threshold for price-spike alert (default 20)
- `ALERT_DEDUPE_MIN` — minutes to suppress repeated identical alerts (default 30)
- `SCHEDULER_TZ` — for "8 AM local" interpretation (default `America/Chicago`)
- `INFLUXDB_*`

**Cost calculation** uses hourly price-weighted Flux integral with `timeSrc:"_start"` alignment and `timezone.location` for local-midnight truncation — important fix from the bug where naive sum-of-power × current-price over-estimated cost ~2×.

**Healthcheck:** runs as a long-lived async loop; container restart on crash.

---

## webdashboard

Image: `nginx:alpine` · Port: `8081` · Volume: `./webdashboard:/usr/share/nginx/html:ro`

Static React (Babel-standalone, no build step) serving a sci-fi HUD originally sourced from `claude.ai/design`. Wires to `/api/*` endpoints proxied by nginx to `webdashboard-api` over the container network.

**Files in `webdashboard/`:**
- `index.html` — entry, loads Babel + React + JSX files in order
- `data.jsx` — fetches from `/api/energy/snapshot`, polls every 5 s
- `app.jsx` — top-level layout, wires data into panels
- `hero.jsx`, `rails.jsx`, `primitives.jsx` — UI components
- `styles.css` — sci-fi theme
- `nginx.conf` — proxies `/api/*` → `http://webdashboard-api:8082`

**No env.** All wiring is in `nginx.conf` (the proxy upstream is hardcoded to the container DNS name `webdashboard-api:8082`).

---

## webdashboard-api

Build: `./webdashboard-api` · Internal port: 8082 (only reachable from `webdashboard` over the container network)

FastAPI backend that the webdashboard fetches from. Serves a single `/api/energy/snapshot` endpoint returning the most recent values for: current price (5min and hourly), instantaneous demand (eagle), per-circuit power top N (refoss), today's cost-so-far, and HVAC state (today's day-type, current schedule action, indoor T/RH, setpoints).

**Env:**
- `INFLUXDB_*` — standard
- `API_PORT` — bind port (default 8082)
- `CACHE_TTL_S` — response cache TTL (default 5 s — keeps Influx query rate sane while supporting 5 s frontend polling)

**Cost calc** uses the same Flux pattern as `telegram-notifier` (timeSrc-aligned hourly join of refoss mains × hourly_avg ComEd price, in local-day window).

---

## loki & promtail

Images: `grafana/loki:3.3.2`, `grafana/promtail:3.3.2` · Loki port: `3100` (Pi-localhost only) · Volumes: `loki_data`, `promtail_positions`, `/var/lib/docker/containers:ro`, `/var/run/docker.sock:ro`

Single-node Loki, 7-day retention, tsdb v13 schema, filesystem storage. Promtail uses Docker service discovery filtered to `com.docker.compose.project=energy-stack` so it only ships logs from this stack's containers (not all Docker activity on the Pi).

JSON parsing pipeline lifts `level` and `msg` fields from each log line as Loki labels, so Grafana Explore queries can filter by `{compose_service="hvac-scheduler", level="error"}`.

**No env on either container** — config files mount-mounted from `loki/loki-config.yml` and `promtail/promtail-config.yml`.

**Loki healthcheck:** `wget /ready`.

**Querying logs:**

```logql
{compose_service="hvac-scheduler"}                       # all logs from one service
{compose_service="hvac-scheduler", level="error"}         # only errors
{compose_service=~"eagle-poller|refoss-poller"} |= "fail" # multi-service grep
```

Available in Grafana → Explore → Loki datasource.

---

## parse_comed_bill.py (manual script, not a service)

**Path**: `deploy/energy-stack/scripts/parse_comed_bill.py`
**Run by**: operator, by hand, on Pi-lab when a new bill arrives
**Frequency**: ~12x/year

**Env vars**:
- `INFLUX_URL` (default `http://localhost:8086`)
- `INFLUX_TOKEN` (required)
- `INFLUX_ORG` (default `home`)
- `INFLUX_BUCKET` (default `energy`)

**Writes**:
- `comed.bill` measurement: 1 point per bill, timestamped at service_to 23:59:59 CDT
  - Tags: `account_no`, `rate_plan`, `bill_type`
  - Fields: `total_due`, `kwh`, `peak_kw` (null on fixed-rate),
            `supply_total`, `delivery_total`, `taxes_total`, `misc_total`,
            `effective_rate_per_kwh`, `service_days`, `issued_date`,
            `service_from`, `service_to`
- `comed.bill_lineitems`: 18-25 points per bill, same timestamp
  - Tags: `account_no`, `category` (SUPPLY|DELIVERY|TAXES_FEES_CREDITS|MISC),
          `line_item`
  - Fields: `amount`, `quantity` (optional), `unit` (optional), `rate` (optional)

**Workflow**: see `deploy/energy-stack/scripts/README.md`
