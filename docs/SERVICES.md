# Services

Per-service reference for the energy-stack Docker Compose project. Companion to [`deploy/energy-stack/README.md`](../deploy/energy-stack/README.md) (which covers operational tasks across the whole stack).

## Contents

- [influxdb](#influxdb) — time-series storage
- [influx-init](#influx-init) — one-shot longterm bucket + downsample task provisioning
- [grafana](#grafana) — visualization
- [eagle-poller](#eagle-poller) — billing-grade smart meter
- [comed-poller](#comed-poller) — ComEd Hourly Pricing
- [refoss-poller](#refoss-poller) — per-circuit power
- [nws-poller](#nws-poller) — weather forecast + alerts
- [pjm-dm2-poller](#pjm-dm2-poller) — PJM zonal market data (DA LMP, load forecast, metered, peak, NSPL)
- [hvac-scheduler](#hvac-scheduler) — Control4 setpoint pushes (with safety supervisor)
- [thermostat-poller](#thermostat-poller) — continuous thermostat reads + override detection
- [haven-ingest](#haven-ingest) — HAVEN IAQ cloud API poller
- [telegram-notifier](#telegram-notifier) — daily summary + alert checker
- [loki & promtail](#loki--promtail) — log aggregation
- [mosquitto, mosquitto-init, telegraf](#mosquitto-mosquitto-init-telegraf-comfortnet-pipeline-profile-mqtt) — ComfortNet MQTT pipeline (profile `mqtt`)

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

**Buckets:**

- `energy` — primary, raw writes, infinite retention. All pollers write here.
- `energy-longterm` — 1-min downsampled aggregates (mean + max for per-frame measurements) plus event measurements written through. Provisioned and populated by `influx-init` (see below) and a Flux task running on a 1-min cadence. See [`docs/INFLUXDB_RETENTION.md`](INFLUXDB_RETENTION.md).

**Measurements written by other services:**

| Measurement | Written by | Notes |
|---|---|---|
| `eagle.meter` | eagle-poller | demand_kw, delivered_kwh, received_kwh |
| `comed.prices` | comed-poller | tag `period_type`: `5min`, `hourly_avg` |
| `refoss.channel` | refoss-poller | per `em:N` channel, power/voltage/current/PF/energy buckets |
| `refoss.system` | refoss-poller | uptime, RSSI, cfg_rev |
| `nws.forecast` | nws-poller | tag `for_period`: `today`, `tomorrow`, `day2`; high_f, max_dewpoint_f, is_heat_advisory, alert_summary |
| `pjm.lmp_da_hourly` | pjm-dm2-poller | day-ahead LMP for ComEd zonal pnode (`33092371`) — tagged `pnode_id`, `pnode_name`, `zone` |
| `pjm.load_forecast` | pjm-dm2-poller | 7-day load forecast for `forecast_area=COMED` — tagged `evaluated_at_iso` so revisions stay distinct |
| `pjm.metered_load` | pjm-dm2-poller | weekly hrl_load_metered for `zone=CE` — tagged `is_verified` |
| `pjm.peak_forecast_rto` | pjm-dm2-poller | RTO peak-day forecast (cooling season only) |
| `pjm.nspl_zonal` | pjm-dm2-poller | annual NSPL for `zone=COMED` |
| `pjm.coincident_peak` | `scrape_pjm_5cp_pdf.py` (annual cron) | tagged `summer_year`, `peak_rank` |
| `pjm.feed_status` | pjm-dm2-poller | tagged `feed`, `success`; one point per feed attempt with `points_written`, `error_type`, `error_msg` fields. **The data-freshness signal** — the container healthcheck is loop liveness only |
| `hvac.decisions` | hvac-scheduler | tag `decision_for_date`, `day_type`; high_f, dewpoint, reason, comed_price_at_decision |
| `hvac.actions` | hvac-scheduler | tag `action_label`, `day_type`, `dry_run`, `supervisor_decision`; cool_setpoint_f, heat_setpoint_f, fan_mode, applied, error, supervisor_reason, cool_setpoint_proposed_f, thermostat snapshot before |
| `hvac.thermostat` | thermostat-poller | continuous 10-min thermostat state |
| `hvac.overrides` | thermostat-poller | one row per poll while setpoints diverge from last `hvac.actions` by ≥0.5°F past `OVERRIDE_GRACE_MIN` |
| `haven.indoor` | haven-ingest | tagged `device_id`; temp/RH/tVOC continuous, PM2.5/airflow CFM flow-dependent |
| `haven.outdoor` | haven-ingest | tagged `station_id`; outdoor station readings |
| `comed.bill` | `parse_comed_bill.py` (manual) | one point per bill |
| `comed.bill_lineitems` | `parse_comed_bill.py` (manual) | full GL breakdown |
| `hvac.comfortnet` | telegraf MQTT consumer (planned) | not yet flowing — depends on Pi-3B publisher |
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

## influx-init

Build: `./influx-init` · `restart: "no"` · One-shot per `compose up -d`

Idempotent post-bootstrap provisioning. Runs after `influxdb` is healthy and exits cleanly. Two responsibilities:

1. Create the `energy-longterm` bucket if it doesn't exist (with the configured retention).
2. Apply the 1-min downsample Flux task at `tasks/downsample-energy-1m.flux`. Task aggregates per-frame measurements (`eagle.meter`, `refoss.channel`, `refoss.system`, future `hvac.comfortnet`) from `energy` to `energy-longterm` with `mean` and `max` reducers. Event measurements (`hvac.actions`, `hvac.overrides`) are written to `energy-longterm` directly by their producers.

**Env:**
- `INFLUXDB_INIT_ORG`, `INFLUXDB_INIT_ADMIN_TOKEN` — same admin credentials as `influxdb` bootstrap

Design: [`docs/INFLUXDB_RETENTION.md`](INFLUXDB_RETENTION.md).

---

## grafana

Image: `grafana/grafana-oss:11.4.0` · Port: `3000` · Volumes: `grafana_data`, `./grafana/provisioning:ro`, `./grafana/dashboards:ro`

InfluxDB and Loki provisioned as datasources (read-only via `editable: false`). Five dashboards provisioned from disk (`grafana/dashboards/`):

| Dashboard | File | Purpose |
|---|---|---|
| Home Energy — Full | `home-energy-full.json` | Whole-stack view: prices, mains, per-circuit, HVAC scheduler, equipment-health row |
| Home Energy — Overview | `home-energy-overview.json` | High-level (cost, demand, indoor temp) |
| HVAC Scheduler | `hvac-scheduler.json` | Day-type history, action timeline, setpoint vs indoor temp |
| ComEd Bill Reconciliation | `comed-bill-reconciliation.json` | Bill-vs-projected, EAGLE-vs-billed-kWh, capacity-charge tracker, forward-projection (stub) |
| IAQ Comparison | `iaq-comparison.json` | HAVEN return-air mix vs thermostat wall reading; tVOC; blower cross-validation |

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

> **No hourly forecast points are written today.** The poller calls the `forecastHourly` endpoint internally but rolls those periods up into the three daily aggregates above. If the thermal-model fit (or any other consumer) needs hourly outdoor temperature, that's a future poller change — see [`THERMAL_MODEL_DESIGN.md`](THERMAL_MODEL_DESIGN.md) data-cadence section.

**Important:** the gridpoint resolution call can be slow on first cycle — poller uses 30 s aiohttp timeout (was 15 s earlier, increased after observed timeouts).

**Healthcheck:** `/tmp/last_poll_ok` marker. Allows up to 5 min staleness (poll interval is 30 min, so the marker is touched every 30 min normally).

---

## pjm-dm2-poller

Build: `./pjm-dm2-poller` · Cycle: `PJM_DM2_POLL_INTERVAL` (default 3600 s = 1 h)

Hourly wake loop; each feed has its own `Schedule` and silently skips on cycles where it shouldn't fire. Auth header `Ocp-Apim-Subscription-Key: $PJM_DM2_API_KEY`. Non-Member tier (6 calls/min ceiling, 50,000 rows/call) is plenty for the steady-state load.

**Per-feed schedule** (all times local per `PJM_DM2_TZ`):

| Feed | Schedule | Output measurement |
|---|---|---|
| `da_hrl_lmps` (ComEd zonal pnode `33092371`) | 17:00 daily | `pjm.lmp_da_hourly` |
| `load_frcstd_7_day` (`forecast_area=COMED`) | 06:00 + 13:00 daily | `pjm.load_forecast` |
| `hrl_load_metered` (`zone=CE` — note: ComEd's PJM zone code is `CE`, not `COMED`, for this feed) | Sundays 02:00 (last 7 days) | `pjm.metered_load` |
| `ops_sum_frcst_peak_rto` (`area=PJM RTO`) | 06:00 + 13:00 in Jun–Sep only | `pjm.peak_forecast_rto` |
| `annual_zonal_nspl` (`zone=COMED` — note: this feed uses `COMED`, not `CE`) | Dec 1, 03:00 | `pjm.nspl_zonal` |

The zone-code-by-feed mismatch (`CE` vs `COMED`) is empirically verified, not a bug. Constants are at the top of `app.py` (`COMED_PNODE_ID`, `COMED_FORECAST_AREA`, `COMED_METERED_ZONE`, `COMED_NSPL_ZONE`).

**Out-of-band scripts** in `deploy/energy-stack/scripts/`:

- `backfill_pjm.py` — one-shot 5-year history backfill (DA LMP + metered load + load forecast). Run once on first deploy.
- `scrape_pjm_5cp_pdf.py` — annual scraper for the official PJM 5CP PDF; writes `pjm.coincident_peak`. Cron once on November 15.

**Env:**
- `PJM_DM2_API_KEY` — Non-Member tier subscription key
- `PJM_DM2_POLL_INTERVAL` — wake interval seconds (default 3600)
- `PJM_DM2_TZ` — IANA tz for schedule decisions (default `America/Chicago`, sourced from `SCHEDULER_TZ`)
- `INFLUX_URL`, `INFLUXDB_INIT_ADMIN_TOKEN`, `INFLUXDB_INIT_ORG`, `INFLUXDB_INIT_BUCKET` — Influx connection (note the env var names mirror the bootstrap convention rather than `INFLUXDB_*`)

**Healthcheck:** `/tmp/last_poll_ok` is **loop liveness only** — touched on every clean cycle (CodeX pass 2, 2026-05-07 walked back an earlier feed-success-gating attempt). 90-min staleness budget catches a wedged or crashed loop, which is what container restart can fix. Persistent feed failures (expired API key, schema drift, PJM 4xx/5xx) are NOT a container-restart-fixable problem and are deliberately not surfaced via this marker.

**Per-feed health surface:** every feed attempt writes one row to `pjm.feed_status` tagged `feed` and `success`, with `points_written`, `error_type`, and (truncated) `error_msg` fields. The [`telegram-notifier`](#telegram-notifier) `check_pjm_feed_freshness` consumes this measurement with per-feed SLAs and fires "DA LMP hasn't reported success in 25 h" style alerts. Grafana freshness panels can use the same data.

**Failure modes:**
- Single-feed failure logged + skipped; cycle continues with other feeds (no abort). Failure recorded in `pjm.feed_status` with `success=false` and the exception type. Container marker still ticks.
- Auth (401) → poller keeps looping (so it can recover when the key is fixed) and every cycle's status row records the failure. Container stays "healthy" (loop is alive); operator notices via the data-layer alert on `pjm.feed_status`.
- 429 / 5xx → handled by per-call retry; cycle skip if persistent.

**Design + schema:** [`PJM_DM2_INTEGRATION.md`](PJM_DM2_INTEGRATION.md). Feed catalog: [`PJM_DM2_FEEDS.md`](PJM_DM2_FEEDS.md).

---

## hvac-scheduler

Build: `./hvac-scheduler` · Cycle: 1-min ticker · Volume: `hvac_scheduler_data` (`/data`)

Decides tomorrow's day-type at 21:00 local, then fires schedule actions throughout the next day. Each action sets cool + heat setpoints (heat always paired for Auto-mode safety), optionally sets fan mode, then pins `Hold = Permanent` so the thermostat baseline doesn't override.

Every proposed setpoint passes through `safety_supervisor.validate_setpoints()` before reaching Control4: clamps cool to `[65, 86]°F`, heat to `[55, 75]°F`, and overrides cool to 74°F if the thermostat snapshot reports indoor ≥ 86°F. Decision logged to `hvac.actions` (tag `supervisor_decision`, fields `supervisor_reason`, `cool_setpoint_proposed_f`). Detail in [`HVAC_LOGIC.md#safety-supervisor-every-setpoint-push`](HVAC_LOGIC.md#safety-supervisor-every-setpoint-push).

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

Build: `./haven-ingest` · Cycle: `HAVEN_POLL_INTERVAL` (default 300 s = 5 min) · Volume: `haven_ingest_data` (`/data`)

Polls the HAVEN cloud API every 5 minutes for one indoor device + paired outdoor station. Auth flow: Auth0 refresh-token grant against `${HAVEN_AUTH0_DOMAIN}` using `HAVEN_CLIENT_ID` + `HAVEN_REFRESH_TOKEN`. The refresh token rotates on every refresh; the new token is persisted to `/data/haven_token.json` so restarts survive across rotations. On startup, the service backfills the last `HAVEN_BACKFILL_DAYS` of history (default 7) before entering steady-state polling.

**Originally shipped as a CSV watcher (May 3, 2026)** that monitored an inbox directory for `CAM_*.csv` exports from `my.haveniaq.com`. Replaced with this API-based poller mid-May 2026 (commit `3cccd63`) once the mobile-app traffic was sniffed and the Auth0 credentials extracted. HAVEN is shipping an official Pro API at `havenapi.tzoa.io` in summer 2026; we'll switch when that lands.

**Env:**
- `HAVEN_AUTH0_DOMAIN` — Auth0 tenant (default `haven-production.auth0.com`)
- `HAVEN_CLIENT_ID` — Auth0 client ID (extracted from mobile app traffic)
- `HAVEN_REFRESH_TOKEN` — initial refresh token; rotates per-call after first use, persisted to `/data/haven_token.json`
- `HAVEN_DEVICE_ID` — indoor sensor device ID (default `3645`)
- `HAVEN_OUTDOOR_ID` — paired outdoor station ID (default `60585`)
- `HAVEN_POLL_INTERVAL` — seconds between polls (default 300)
- `HAVEN_BACKFILL_DAYS` — startup backfill window (default 7)
- `INFLUXDB_*` — standard

**Writes:**

| Measurement | Tags | Fields | Coverage |
|---|---|---|---|
| `haven.indoor` | `device_id` | `temp_f`, `temp_c`, `humidity_pct`, `tvoc_ppb`, status enums | 100% on temp/RH/tVOC; `pm25_ugm3` and `airflow_cfm` are flow-dependent (~3%) |
| `haven.outdoor` | `station_id` | outdoor temp/RH/etc. from the paired station | 100% |

**Idempotent on timestamp.** Backfill on startup re-writes the same `(measurement, device_id, ts)` tuples that the prior run wrote — Influx upserts and the data doesn't double.

**Healthcheck:** `/tmp/last_poll_ok` marker.

**Crucial flow-dependent insight:** the sparse `airflow_cfm` and `pm25_ugm3` rows aren't a defect — they're **only populated when the blower is moving air past the duct sensor**. This means:
- Non-null `airflow_cfm` rows = blower runtime ground truth (cross-validate against Refoss `em:9` furnace blower power)
- Non-null `airflow_cfm` value = real measured CFM at that moment, useful for delivered-BTU calc when paired with future supply-air temp instrumentation

---

## telegram-notifier

Build: `./telegram-notifier` · Two cycles: daily summary at `SUMMARY_HOUR` + alert check every `ALERT_CHECK_INTERVAL_S` seconds

Single-bot Telegram client (`@EnergyStackBot`, separate from any other Telegram bots Chris uses). Sends:

- **Daily summary** at 8 AM local (HTML-formatted): yesterday's cost, kWh, peak demand, fridge anomaly check, HVAC schedule fired, weather forecast for today.
- **Alerts**, checked every 5 min (deduplicated 30 min):
  - **Poller silent**: no recent write to its measurement, per-poller tolerance — sub-minute pollers default 10 min, NWS 70 min, thermostat / comfortnet-publisher 30 min.
  - **Price spike**: current 5-min ComEd price > `$TELEGRAM_PRICE_SPIKE_C` ¢/kWh.
  - **Fridge anomaly**: recent-6h mean > 1.5× and Δ > 50 W vs. 14-day baseline.
  - **HVAC scheduler errors**: latest `hvac.actions` row in the last hour with a non-skip error.
  - **PJM feed freshness**: per-feed deadman on `pjm.feed_status` with per-feed SLAs (DA LMP 25 h, load forecast 14 h, weekly metered 192 h, RTO peak 14 h cooling-season-only, NSPL 168 h within Dec/Jan window). Surface for catching expired API keys / persistent 4xx that the container healthcheck deliberately doesn't catch (loop liveness only, see [`pjm-dm2-poller`](#pjm-dm2-poller)).
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

**Cost calculation** uses hourly price-weighted Flux integral with `timeSrc:"_start"` alignment and `timezone.location` for local-midnight truncation — important fix from the bug where naive sum-of-power × current-price over-estimated cost ~2×. Same Flux pattern is shared with the Grafana cost panels.

**Healthcheck:** runs as a long-lived async loop; container restart on crash.

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

## mosquitto, mosquitto-init, telegraf (ComfortNet pipeline, profile `mqtt`)

Three services gated behind compose profile `mqtt`. The standard `docker compose up -d` ignores them; bring them up with `docker compose --profile mqtt up -d` (or set `COMPOSE_PROFILES=mqtt`).

**Status:** broker side deployed and healthy on Pi-lab. The Pi-3B-side `comfortnet-publisher` systemd unit (which reads decoder output and publishes frames) is not yet implemented — so no `hvac.comfortnet` data is flowing yet. See [`COMFORTNET_PIPELINE.md`](COMFORTNET_PIPELINE.md).

| Service | Image / Build | Purpose |
|---|---|---|
| `mosquitto-init` | `./mosquitto-init` (one-shot) | Regenerates broker password file from env vars on every `--profile mqtt up`. Idempotent. Mosquitto depends on it via `service_completed_successfully`. |
| `mosquitto` | `eclipse-mosquitto:2` | TLS-only MQTT broker on `:8883`. Cert material at `/opt/mosquitto-certs/` on Pi-lab (CA + server cert/key generated via `deploy/energy-stack/mosquitto/scripts/`). ACL file gates publishers/consumers per identity. |
| `telegraf` | `telegraf:1.32-alpine` | MQTT consumer subscribing to `home/utility-room/hvac/comfortnet/+`. Writes received frames into InfluxDB as `hvac.comfortnet` (continuous fields downsample-eligible) and `hvac.comfortnet.events` (event-only, written direct to longterm). |

**Three identities** in the password file: `comfortnet-publisher` (Pi-3B sniffer; write-only on `home/utility-room/hvac/comfortnet/#`), `telegraf` (Pi-lab consumer; read-only on the same subtree), `n8n` (read-only on `home/utility-room/hvac/comfortnet/events/#`).

**Healthcheck:** Mosquitto runs `mosquitto_sub -E` against an authorized topic on every cycle, validating TLS + auth + ACL grant in one round-trip without waiting for a real message. The hostname in the test (`mosquitto`) matches the cert's `DNS:mosquitto` SAN.

**Env:**
- `MOSQUITTO_PUBLISHER_PASSWORD`, `MOSQUITTO_TELEGRAF_PASSWORD`, `MOSQUITTO_N8N_PASSWORD` — the three identity passwords
- Telegraf reads `MOSQUITTO_TELEGRAF_PASSWORD`, `INFLUXDB_INIT_ADMIN_TOKEN`, `INFLUXDB_INIT_ORG`

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
