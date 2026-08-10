---
date: 2026-05-18
owner: chris
status: active
role-label: code-team
---

# Services

Per-service reference for the energy-stack Docker Compose project. Companion to [`deploy/energy-stack/README.md`](../deploy/energy-stack/README.md) (which covers operational tasks across the whole stack).

## Contents

- [influxdb](#influxdb) — time-series storage
- [influx-init](#influx-init) — one-shot longterm bucket + downsample task provisioning
- [grafana](#grafana) — deployed but UNUSED; real UI is the surface-kiosk (see [DASHBOARDS.md](DASHBOARDS.md))
- [cockpit](#cockpit) — Controller Cockpit (read-only HVAC dashboard)
- [eagle-poller](#eagle-poller) — billing-grade smart meter
- [comed-poller](#comed-poller) — ComEd Hourly Pricing
- [refoss-poller](#refoss-poller) — per-circuit power
- [nws-poller](#nws-poller) — weather forecast + alerts
- [pjm-dm2-poller](#pjm-dm2-poller) — PJM zonal market data (DA LMP, load forecast, metered, peak, NSPL)
- [hvac-scheduler](#hvac-scheduler) — rev 4 spike-only controller (warm-only timed holds on RTP spikes; device program is the baseline; device-owned safety)
- [hvac-scheduler-watchdog](#hvac-scheduler-watchdog) — out-of-band controller-liveness beacon (`hvac.heartbeat`)
- [thermostat-poller](#thermostat-poller) — continuous thermostat reads
- [haven-ingest](#haven-ingest) — HAVEN IAQ cloud API poller
- [ecowitt-ingest](#ecowitt-ingest) — Ecowitt GW1200 push receiver (`ecowitt.weather`)
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
| `nws.forecast` | nws-poller | tag `for_period`: `today`, `tomorrow`, `day2`; `period_date`, `high_f`, `low_f`, `max_dewpoint_f`, `apparent_max_f`, `precip_prob`, `is_heat_advisory` (0/1), `alert_summary` (string roll-up of any active heat advisory), `hours_covered` (int; pinned int-not-float by `test_hours_covered_is_int_not_float` to avoid an Influx type-collision with the existing bucket schema). Daily summaries only. |
| `nws.alerts` | nws-poller | tag `event`, `severity`; one point per active alert with `active=1`, `expires_unix`, `headline` (≤200 chars). Granular per-alert detail. |
| `pjm.lmp_da_hourly` | pjm-dm2-poller | day-ahead LMP for ComEd zonal pnode (`33092371`) — tagged `pnode_id`, `pnode_name`, `zone` |
| `pjm.lmp_rt_hourly` | pjm-dm2-poller | real-time hourly LMP for ComEd zonal pnode — tagged `pnode_id`, `pnode_name`, `zone`. Scheduled at 12:00 CT (rt_hrl_lmps feed). |
| `pjm.inst_load` | pjm-dm2-poller | 5-min instantaneous load, both scopes (`area=COMED` from `inst_load` feed + `area=PJM RTO` from `inst_load_rto`) — retained as 5CP telemetry |
| `pjm.load_forecast` | pjm-dm2-poller | 7-day load forecast for `forecast_area=COMED` — tagged `evaluated_at_iso` so revisions stay distinct |
| `pjm.metered_load` | pjm-dm2-poller | weekly hrl_load_metered for `zone=CE` — tagged `is_verified` |
| `pjm.peak_forecast_rto` | pjm-dm2-poller | RTO peak-day forecast (cooling season only) |
| `pjm.nspl_zonal` | pjm-dm2-poller | annual NSPL for `zone=COMED` |
| `pjm.coincident_peak` | `scrape_pjm_5cp_pdf.py` (annual cron) | tagged `summer_year`, `peak_rank` |
| `pjm.feed_status` | pjm-dm2-poller | tagged `feed`, `success`; one point per feed attempt with `points_written`, `error_type`, `error_msg` fields. **The per-feed health signal** — telegram-notifier's `check_pjm_feed_failures` and `check_pjm_feed_freshness` both consume it |
| `pjm.poller_heartbeat` | pjm-dm2-poller | one row per loop cycle (hourly), single field `alive=1`. Liveness signal for telegram-notifier's `check_poller_silence` so the long quiet stretches between scheduled feeds don't look like a dead poller |
| `hvac.actions` | hvac-scheduler | Scale-neutral (`unit` tag = `F`/`C`; values stay in the controller's `temp_scale`). Tags: `unit`, `tier`, `action_label` (`SPIKE`/`RELEASE`), `dry_run`. Fields: `commanded_cool`, `commanded_heat`, `baseline_cool` (= observed device `ScheduleCoolSp`), `schedule_cool` (same value, explicit name), `drift` (`= commanded_cool − schedule_cool`), `humidity_gated` (0/1), `setpoint_reason`, `applied` (0/1), `error`, `config_id` (loaded config's SHA256), `hold_expires_at` (RFC3339, `""` when no hold), `actual_indoor_temp`, `actual_cool_before`, `actual_heat_before`, `actual_humidity`. One row per action attempt |
| `hvac.price_overlay` | hvac-scheduler | tag `prev_tier`, `new_tier`, `unit` (`F`/`C`); fields `current_price_cents`, `baseline_cool`, `commanded_cool`, `triggered_at_utc`. Warm-only RTP tier transitions only (not every tick) |
| `hvac.arm_mode` | hvac-scheduler | tag `scheduler_mode`, `arm` (when applicable); field `mode_actual` ∈ {`outside-window`, `off-protocol-<mode>`}. **Canonical scheduler-alive signal** — the watchdog reads this measurement to determine whether to emit a `hvac.heartbeat` down-beacon |
| `hvac.heartbeat` | hvac-scheduler-watchdog | field `controller_alive` (only `false` is ever written — no-news-is-good-news; absence of recent rows says nothing about liveness, the canonical signal is recent `hvac.arm_mode`) |
| `hvac.thermostat` | thermostat-poller | continuous 10-min thermostat state |
| `ecowitt.weather` | ecowitt-ingest | tag `gateway` (GW1200 PASSKEY). Field families: **canonical analysis source per spec §6** is `ch1_temp_f` / `ch1_rh_pct` / `ch1_dewpoint_f` (the paired WN31 channel; ch1 is the shaded N/E-wall sensor by Chris's wiring). Gateway also writes a descriptive alias `outdoor_temp_f` / `outdoor_rh_pct` / `outdoor_dewpoint_f` mirroring the configured shaded channel when `ECOWITT_SHADED_CHANNEL` is set — descriptive only, NOT consumed by the analysis weather vector. WS90 sun-exposed comparator + wind/solar/UV/rain (`ws90_temp_f`/`rh_pct`/`dewpoint_f`, `wind_mph`, `solar_wm2`, `rain_*_in`, etc.); GW1200 internal (`indoor_temp_f`, `indoor_rh_pct`, `baro_abs_inhg`, `pressure_inhg`). Plus `ch{N}_temp_f`/`rh_pct`/`dewpoint_f` for any other paired WH31 channels. |
| `haven.indoor` | haven-ingest | tagged `device_id`; temp/RH/tVOC continuous, PM2.5/airflow CFM flow-dependent |
| `haven.outdoor` | haven-ingest | tagged `station_id`; outdoor station readings |
| `comed.bill` | `parse_comed_bill.py` (manual) | one point per bill |
| `comed.bill_lineitems` | `parse_comed_bill.py` (manual) | full GL breakdown |
| `hvac.comfortnet` | telegraf MQTT consumer | live (May 2026); fields `cool_actual_pct`, `heat_actual_pct`, `fan_actual_pct`, `blower_cfm`, `dehumidify_actual_pct` flowing from the Pi-3B publisher |
| `telegram.alerts` | telegram-notifier | dedupe state for fired alerts |

**Legacy measurements still present in the `energy` bucket** (not actively written; documented here so they don't surprise an operator running `schema.measurements`): `sense.device`, `sense.realtime`, `sense.trend` (sense-poller retired April 2026); `haven.airquality` (CSV-watcher predecessor of haven-ingest, retired mid-May 2026); `mqtt_consumer` (telegraf default measurement name from an earlier config); `hvac.overrides` (thermostat-poller override detection retired at the rev 4 cutover, July 2026 — manual holds are first-class operator action now; the `tools/log_override.py` annotation script was deleted in the 2026-07-06 repo cleanup); `hvac.switch_event`, `hvac.input_feed_health` (rev 3 scheduler measurements; writers deleted at the rev 4 cutover); `hvac.decisions`, `hvac.precool_window`, `hvac.5cp_state` (day-type/precool/5CP writers removed in the June 2026 demolition). No new writes occur to any of these.

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
2. Apply the 1-min downsample Flux task at `tasks/downsample-energy-1m.flux`. Task aggregates per-frame measurements (`eagle.meter`, `refoss.channel`, `refoss.system`, future `hvac.comfortnet`) from `energy` to `energy-longterm` with `mean` and `max` reducers. Event measurements (e.g. `hvac.actions`) are written to `energy-longterm` directly by their producers.

**Env:**
- `INFLUXDB_INIT_ORG`, `INFLUXDB_INIT_ADMIN_TOKEN` — same admin credentials as `influxdb` bootstrap

Design: [`docs/INFLUXDB_RETENTION.md`](INFLUXDB_RETENTION.md).

---

## grafana

> **Deployed but UNUSED (2026-07).** Grafana is no longer the visualization layer — the live UI is the **surface-kiosk** wall display (custom weather board + Vigil cockpit). See [DASHBOARDS.md](DASHBOARDS.md). The container still runs; this section documents it for reference (removal candidate).

Image: `grafana/grafana-oss:11.4.0` · Port: `3000` · Volumes: `grafana_data`, `./grafana/provisioning:ro`, `./grafana/dashboards:ro`

InfluxDB and Loki provisioned as datasources (read-only via `editable: false`). Five dashboards provisioned from disk (`grafana/dashboards/`):

Dashboards are maintained in the separate [FireDevPro/grafana-dashboards](https://github.com/FireDevPro/grafana-dashboards) repo (mounted on the Pi at `/opt/grafana-dashboards`); listed here for reference only.

| Dashboard | File | Purpose |
|---|---|---|
| Home Energy — Full | `home-energy-full.json` | Whole-stack view: prices, mains, per-circuit, HVAC scheduler, equipment-health row |
| Home Energy — Overview | `home-energy-overview.json` | High-level (cost, demand, indoor temp) |
| HVAC Scheduler | `hvac-scheduler.json` | Action timeline, tier history, setpoint vs indoor temp (pre-rev-4 panels pending dashboard-repo refresh) |
| ComEd Bill Reconciliation | `comed-bill-reconciliation.json` | Bill-vs-projected, EAGLE-vs-billed-kWh, capacity-charge tracker, forward-projection (stub) |
| IAQ Comparison | `iaq-comparison.json` | HAVEN return-air mix vs thermostat wall reading; tVOC; blower cross-validation |

**Env:**
- `GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD` — Grafana admin login
- `GF_USERS_ALLOW_SIGN_UP=false`, `GF_AUTH_ANONYMOUS_ENABLED=false` — locked down
- `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET` — used by datasource provisioning at startup

**Healthcheck:** `wget /api/health` every 30 s (must report `database: ok`).

**Editing dashboards:** modify JSON in `grafana/dashboards/`, restart Grafana (`docker compose restart grafana`) — provisioning is read-only at runtime, so UI edits to provisioned dashboards don't persist after restart. To iterate: edit in UI → Dashboard Settings → JSON Model → save back to file → commit.

---

## cockpit

Build: `./cockpit` (multi-stage: Node builds the Vite frontend, Python runtime serves it) · Port: `8765`

Controller Cockpit — read-only narrative HVAC dashboard at `http://192.168.20.10:8765/`. One container serves both the FastAPI proxy (`/api/snapshot`, `/api/health`, `/api/day_ahead`, `/api/today_actions`, `/api/day_at_a_glance`) and the production frontend build, same-origin. Assembles its snapshot from existing `hvac.*` Influx measurements and `decision_trace.*` Loki logs; no writes, no control-path surface. Full detail in [`deploy/energy-stack/cockpit/README.md`](../deploy/energy-stack/cockpit/README.md), including the workstation dev loop.

**Env:**
- `COCKPIT_BACKEND_MODE=live` — live Influx/Loki assembly (`canned` serves the offline fixture)
- `INFLUXDB_URL`, `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET` — Influx reads
- `LOKI_URL` (+ optional `LOKI_CONTAINER`, default `hvac-scheduler`) — decision-trace reads

**Healthcheck:** `GET /api/health` every 30 s.

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

**Historical design notes:** [`docs/archive/phase-3.3-eagle-poller-design.md`](archive/phase-3.3-eagle-poller-design.md)

---

## comed-poller

Build: `./comed_poller` · Cycle: `COMED_POLL_INTERVAL` (default 60 s)

Polls the public ComEd Hourly Pricing API (`hourlypricing.comed.com/api`). Two endpoints per cycle:
- `?type=5minutefeed&format=json` — last ~24 h of 5-min intervals (latest written each cycle)
- `?type=currenthouraverage&format=json` — current hour average (idempotent — overwrites same hour-truncated timestamp)

**Env:** `COMED_POLL_INTERVAL`, `COMED_API_BASE`, `INFLUXDB_*`

**Writes** (measurement `comed.prices`, field `price_cents_per_kwh`):

| Tag value | Description | Timestamp source |
|---|---|---|
| `period_type=5min` | Latest 5-min interval | `millisUTC` from API |
| `period_type=hourly_avg` | Current hour average | Hour-truncated UTC (idempotent — repeated polls upsert) |

> **Note:** ComEd does NOT publicly expose day-ahead forecast prices. The undocumented `?type=daynexttoday` endpoint returns today's *settled* day-ahead prices (not tomorrow's). PJM DataMiner2 is the upstream source but requires an API key gated by member status. See [PROJECT.md decision log](PROJECT.md). For day-ahead LMP, the `pjm-dm2-poller` writes `pjm.lmp_da_hourly` from `da_hrl_lmps` at 17:00 CT daily — see [pjm-dm2-poller](#pjm-dm2-poller) below.

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

**Channel mapping** (Refoss app labels A/B/C ↔ InfluxDB `em:N` tag ↔ monitored circuit). The EM16P device exposes 18 channels; this household monitors 5:

| Refoss app label | InfluxDB tag | Circuit | Spec role |
|---|---|---|---|
| A1 | `em:1` | Mains leg A (split-phase 240V) | mains-sanity subset `em:1 + em:7` per spec §10:420 |
| B1 | `em:7` | Mains leg B (split-phase 240V) | mains-sanity subset |
| A2 | `em:2` | HVAC compressor leg A (split-phase 240V) | HVAC analysis subset `em:2 + em:8 + em:9` per spec §4:121 |
| B2 | `em:8` | HVAC compressor leg B (split-phase 240V) | HVAC analysis subset |
| B3 | `em:9` | Furnace blower / control board (single-phase 120V) | HVAC analysis subset |

Other `em:N` channels are device-side capacity, not monitored for this study. The A/B/em:N gap (mains = `em:1 + em:7` rather than adjacent numbers) is because the Refoss EM16P puts A-side and B-side channels in different numeric bands — A1 and B1 are physically the two legs of the same split-phase breaker.

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

> **No hourly forecast points are written today.** The poller calls the `forecastGridData` endpoint internally but rolls those periods up into the three daily aggregates above. If a future consumer needs hourly outdoor temperature, that's a future poller change.

**Important:** the gridpoint resolution call can be slow on first cycle — poller uses 30 s aiohttp timeout (was 15 s earlier, increased after observed timeouts).

**Healthcheck:** `/tmp/last_poll_ok` marker. Allows up to 5 min staleness (poll interval is 30 min, so the marker is touched every 30 min normally).

---

## pjm-dm2-poller

Build: `./pjm_dm2_poller` · Cycle: `PJM_DM2_POLL_INTERVAL` (default 300 s = 5 min, sourced from `.env` / `.env.example`. `docker-compose.yml`'s `${PJM_DM2_POLL_INTERVAL:-300}` substitution matches; the wake loop ticks every 5 min so sub-hourly feeds like `inst_load` fire on every tick and hourly feeds fire on the `:00` tick.)

Hourly wake loop; each feed has its own `Schedule` and silently skips on cycles where it shouldn't fire. Auth header `Ocp-Apim-Subscription-Key: $PJM_DM2_API_KEY`. Non-Member tier (6 calls/min ceiling, 50,000 rows/call) is plenty for the steady-state load.

**Per-feed schedule** (all times local per `PJM_DM2_TZ`):

| Feed | Schedule | Output measurement |
|---|---|---|
| `da_hrl_lmps` (ComEd zonal pnode `33092371`) | 17:00 daily | `pjm.lmp_da_hourly` |
| `rt_hrl_lmps` (ComEd zonal pnode) | 12:00 daily (~1h after PJM 11–12 ET publish per Phase 2 spec §8) | `pjm.lmp_rt_hourly` |
| `inst_load` (`area=COMED`) + `inst_load_rto` (`area=PJM RTO`) | every 5 min, both scopes | `pjm.inst_load` (single measurement, distinguished by `area` tag) |
| `load_frcstd_7_day` (`forecast_area=COMED`) | 06:00 + 13:00 daily | `pjm.load_forecast` |
| `hrl_load_metered` (`zone=CE` — note: ComEd's PJM zone code is `CE`, not `COMED`, for this feed) | every hour, 5-day lookback | `pjm.metered_load` |
| `hrl_load_metered_rto` (`zone=RTO` — RTO-wide aggregate companion for the §3 dual-scope 5CP detector) | every hour, 5-day lookback | `pjm.metered_load` (distinguished by `zone` tag) |
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
- `INFLUX_URL`, `INFLUXDB_INIT_ADMIN_TOKEN`, `INFLUXDB_INIT_ORG`, `INFLUXDB_INIT_BUCKET` — Influx connection. `INFLUX_URL` is a short-form outlier; the other three mirror the bootstrap `INFLUXDB_INIT_*` convention rather than the `INFLUXDB_*` form used by sibling pollers.

**Healthcheck:** `/tmp/last_poll_ok` is **loop liveness only** — touched on every clean cycle (CodeX pass 2, 2026-05-07 walked back an earlier feed-success-gating attempt). 90-min staleness budget catches a wedged or crashed loop, which is what container restart can fix. Persistent feed failures (expired API key, schema drift, PJM 4xx/5xx) are NOT a container-restart-fixable problem and are deliberately not surfaced via this marker.

**Per-feed health surface:** every feed attempt writes one row to `pjm.feed_status` tagged `feed` and `success`, with `points_written`, `error_type`, and (truncated) `error_msg` fields. The [`telegram-notifier`](#telegram-notifier) consumes this measurement two ways: `check_pjm_feed_failures` fires immediately on any `success=false` row with the actual error context, and `check_pjm_feed_freshness` fires on feeds that *had* succeeded before but went longer than their per-feed tolerance without another success (the "had history, went stale" case — backstops e.g. a fetcher that silently returns 0 points). Grafana freshness panels can use the same data.

**Liveness heartbeat:** every loop cycle the poller writes one `pjm.poller_heartbeat` row regardless of whether any feed fired, so `telegram-notifier` `check_poller_silence` can deadman-check the poller process itself (130-min tolerance — one missed cycle is fine, two consecutive misses fire). Without this, the long quiet stretches between scheduled feeds (e.g., 6 days between weekly metered fires) would look like a dead poller.

**Failure modes:**
- Single-feed failure logged + skipped; cycle continues with other feeds (no abort). Failure recorded in `pjm.feed_status` with `success=false` and the exception type. Container marker still ticks.
- Auth (401) → poller keeps looping (so it can recover when the key is fixed) and every cycle's status row records the failure. Container stays "healthy" (loop is alive); operator notices via the data-layer alert on `pjm.feed_status`.
- 429 / 5xx → handled by per-call retry; cycle skip if persistent.

**Design + schema:** [`PJM_DM2_INTEGRATION.md`](PJM_DM2_INTEGRATION.md). Feed catalog: [`PJM_DM2_FEEDS.md`](PJM_DM2_FEEDS.md).

---

## hvac-scheduler

Build: `./hvac_scheduler` · Cycle: 1-min ticker (`python -m hvac_scheduler.controller`) · Volume: `hvac_scheduler_data` (`/data`)

The rev 4 **spike-only controller**: the thermostat runs its own onboard comfort program untouched, and the controller pushes a *warmer* timed hold only while ComEd RTP power is expensive. That is the entire controller — no schedule copy in config (the device's own `ScheduleCoolSp` is the baseline, read live), no day-types, no precool, no software safety supervisor.

**Three tiers exactly** — `normal` / `elevated` / `scarcity` — driven by the ComEd 5-min price with hysteresis (`hysteresis_cents`). Engage on **1 fresh-strict bucket** (bucket age ≤ 720 s); release only after `release_confirm_buckets` consecutive fresh-strict buckets at/below the release threshold; a **stale backstop** hard-releases after `stale_release_minutes` without fresh-strict data while holding. Targets: elevated = device program + `elevated_offset`; scarcity = `scarcity_absolute`. **Warm-only is a code invariant** — the commanded cool never drops below the device's current program value; heat is pinned at/below `heat_floor` on every push (deadband-safe).

**Holds are timed, never Permanent.** Every push is a `TemporaryHold` with a quarter-hour-floored expiry ≤ `hold_ttl_minutes` ahead; if the controller dies, the device drops the hold at expiry and resumes its program unaided (safety is device-owned: thermostat min/max limits are the hard cap). Applied holds are recorded in an **own-hold record** (`/data/own_hold.json`); on later ticks the record scopes device reads (pure normal ticks with no record never touch the device), distinguishes the controller's own hold from a manual operator hold (manual holds are first-class, not "overrides"), and drives **own-hold cleanup**: a normally-lapsed or foreign hold drops the stale record, and a zombie hold (record says ours, device still holding after expiry-equivalent) is released.

A **humidity guard** (hysteresis on thermostat RH: blocks at `rh_max_pct`, clears below `rh_clear_pct`, missing RH blocks) stops spike holds from extending while it's humid — the hold lapses on its TTL rather than being renewed.

**Modes:** `SCHEDULER_MODE=shadow` computes + traces every tick with zero device writes (including cleanup releases); `production` writes live. Sole write gate; invalid value → exit 2.

**Alert pair (telegram-notifier):** `check_controller_down` fires on the watchdog's `hvac.heartbeat controller_alive=false` down-beacon (controller silent ≥ 10 min); `check_device_status_failures` fires per class when the newest N `hvac.device_status` attempts of that class are all failures (read 3, write 3, crash 1). Because the controller writes a row per attempt — success *and* failure — a single success breaks the run, so self-healing blips stay silent. Unit-tested in `telegram_notifier/`.

**Config** (`commissioning-controller.yaml`, mounted read-only — **the experimental surface, tune freely**): `temp_scale`, `price_tiers_cents` (`elevated_at`/`scarcity_at`/`hysteresis_cents`), `elevated_offset`, `scarcity_absolute`, `heat_floor`, `humidity_guard` (`rh_max_pct`/`rh_clear_pct`), `hold_ttl_minutes`, `release_confirm_buckets`, `stale_release_minutes`. Every key is **required** — no code defaults; a missing key is a startup error naming the key. Temps must sit on the scale's grid (0.5 °C / 1 °F). `config_id` (SHA256 of the file bytes) is stamped into telemetry so tuning epochs stay interpretable. Schema example: `commissioning-controller.example.yaml`.

**Env:**
- `TCC_USERNAME`, `TCC_PASSWORD`, `TCC_DEVICE_ID` — Total Connect Comfort creds + Honeywell device id (default `4750378`); the device client is the `aiosomecomfort` `TCCClient` behind the `TccClimateAdapter` seam.
- `CONTROLLER_CONFIG_FILE` — path to the config YAML (compose mounts `./hvac_scheduler/commissioning-controller.yaml` at `/config/commissioning-controller.yaml`).
- `TEMP_SCALE` — must match the YAML's `temp_scale` (`C`/`F`; compose default `C`) or the loader fail-fasts. All temps flow scale-native; no F↔C conversion anywhere.
- `SCHEDULER_MODE` — `shadow` | `production` (see Modes above; compose default `shadow`).
- `SCHEDULER_TZ` — IANA tz (default `America/Chicago`).
- `INFLUXDB_URL`, `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET` — standard.

**Writes:**

All setpoint values are scale-neutral: the `unit` tag records the controller's `temp_scale` (`F`/`C`) and values stay in that scale — no conversion.

| Measurement | Tags | Fields |
|---|---|---|
| `hvac.actions` | `unit`, `tier`, `action_label` (`SPIKE`/`RELEASE`), `dry_run` | `commanded_cool`, `commanded_heat`, `baseline_cool` (= observed device `ScheduleCoolSp`), `schedule_cool` (same value, explicit name), `drift` (`= commanded_cool − schedule_cool`), `humidity_gated` (0/1), `setpoint_reason` (rev 4 reason code), `applied` (0/1), `error` (`""` when none), `config_id`, `hold_expires_at` (RFC3339, `""` when no hold), `actual_indoor_temp`, `actual_cool_before`, `actual_heat_before`, `actual_humidity`. One row per action attempt. |
| `hvac.price_overlay` | `prev_tier`, `new_tier`, `unit` | `current_price_cents`, `baseline_cool`, `commanded_cool`, `triggered_at_utc`. Tier transitions only (not every tick). |
| `hvac.arm_mode` | `scheduler_mode`, `arm` (when in an arm window) | `mode_actual` ∈ {`outside-window`, `off-protocol-<mode>`}. Written on a ≤5-min cadence — canonical scheduler-alive signal that the watchdog reads. (The `experiment` arm-gated branch is the retained 2027 layer, not implemented in rev 4.) |

Plus one `decision_trace.rev4_tick` JSON log line **every tick, 24/7** (transitions at `info`, holds at `debug`; never suppressed — a silent healthy controller is indistinguishable from a hung one).

**Healthcheck:** `/tmp/last_tick_ok` touched only when a tick completes without raising — a sustained failure flips the container unhealthy. A transient tick error logs one `rev4_tick_failed` line and the loop continues.

**Detailed controller logic (tiers, fresh-strict, hold math, own-hold lifecycle, device-owned safety):** [commissioning-controller spec, revision 4](superpowers/specs/2026-06-20-commissioning-controller-design.md).

---

## hvac-scheduler-watchdog

Build: `./hvac_scheduler_watchdog` · Cycle: `WATCHDOG_INTERVAL_SECONDS` (default 60)

Out-of-band controller-liveness check. Single-purpose container so it cannot fail-with-the-controller. Runs an Influx query each cycle: if zero `hvac.arm_mode` rows appear in the last `WATCHDOG_THRESHOLD_MINUTES` (default 10), writes `hvac.heartbeat controller_alive=false`. Otherwise writes nothing — **no-news-is-good-news**.

The canonical scheduler-liveness signal is recent `hvac.arm_mode` rows from the scheduler itself; the heartbeat is purely a "DOWN beacon" emitted by an out-of-band observer. The absence of recent `hvac.heartbeat` rows tells you nothing about controller liveness on its own.

**Env:**
- `INFLUXDB_URL` (default `http://influxdb:8086`)
- `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET` — standard credentials
- `WATCHDOG_INTERVAL_SECONDS` — wake interval (default 60)
- `WATCHDOG_THRESHOLD_MINUTES` — silence budget before declaring down (default 10)

**Writes** (only when controller is down):

| Measurement | Tags | Fields |
|---|---|---|
| `hvac.heartbeat` | — | `controller_alive` (always `false` when this beacon writes) |

**Healthcheck:** none — single-purpose loop with `restart: unless-stopped`. If the watchdog itself crashes, docker compose brings it back.

**Consumed by:** Controller Cockpit (`deploy/energy-stack/cockpit/backend/influx.py::query_latest_heartbeat`) for the header controller-alive light. Telegram-notifier alerts on the same signal.

Source: [`deploy/energy-stack/hvac_scheduler_watchdog/check.py`](../deploy/energy-stack/hvac_scheduler_watchdog/check.py).

---

## thermostat-poller

Build: `./thermostat-poller` · Cycle: `THERMOSTAT_POLL_INTERVAL` (default 600 s = 10 min, the TCC rate-limit floor) · Volume: `thermostat_poller_data` (`/data`)

Continuous reads of CTK04AE state through the device client. Independent of `hvac-scheduler`. The Control4/pyControl4 read path has been retired; reads now go through the stubbed `ThermostatClient` seam (TCC/`aiosomecomfort` deferred).

**Output — `hvac.thermostat`**, every poll cycle:

| Tag | Field | Notes |
|---|---|---|
| `thermostat_id` | `indoor_temp_f`, `indoor_temp_f_hires`, `humidity_pct`, `cool_setpoint_f`, `heat_setpoint_f`, `hvac_mode`, `hvac_state` (running/idle), `fan_mode`, `hold_mode` | Continuous time-series of full thermostat state. Use for Grafana panels, calibration vs. Haven, anomaly detection. `indoor_temp_f` is whole-degree (from Director `TEMPERATURE_F`); `indoor_temp_f_hires` is fractional ~0.18°F resolution (derived from Director `TEMPERATURE_C`). Same underlying CTK04AE sensor; prefer the hires field for thermal characterization and stage-cooling-rate work. See [docs/THERMAL_ROUGH_CUT_2026-05-26.md](THERMAL_ROUGH_CUT_2026-05-26.md). |

Override detection (`hvac.overrides`) was **retired at the rev 4 cutover** (July 2026): under the spike-only controller the `hvac.actions` row it compared against is days old or absent, and manual holds are first-class operator action, not "overrides".

**Env:**
- `CONTROL4_THERMOSTAT_ID` — same as `hvac-scheduler` (flagged for rename at TCC wire-up)
- `THERMOSTAT_POLL_INTERVAL` — seconds (default 600)
- `INFLUXDB_*` — standard

**Healthcheck:** `/tmp/last_poll_ok` marker, `find -mmin -15` (allows up to 15 min staleness on a 10-min poll interval).

**Why this exists:** the `hvac-scheduler` snapshots thermostat state only at action moments. That's too sparse for proper time-series correlation against Haven's 5-min cadence. This poller fills the gap.

---

## haven-ingest

Build: `./haven-ingest` · Cycle: `HAVEN_POLL_INTERVAL` (default 300 s = 5 min) · Volume: `haven_ingest_data` (`/data`)

Polls the official HAVEN Pro API at `https://havenapi.tzoa.io` every 5 minutes for one indoor device + paired outdoor station. Auth flow: Auth0 refresh-token grant against `${HAVEN_AUTH0_DOMAIN}` using `HAVEN_CLIENT_ID` + `HAVEN_REFRESH_TOKEN`. The refresh token rotates on every refresh; the new token is persisted to `/data/haven_token.json` so restarts survive across rotations. The bootstrap `HAVEN_REFRESH_TOKEN` is captured once from a browser login session at `my.haveniaq.com`; if the refresh token expires (typically 30-90 days of disuse), a new bootstrap token is obtained via fresh browser login. On startup, the service backfills the last `HAVEN_BACKFILL_DAYS` of history (default 7) via the API range endpoints before entering steady-state polling.

**History:** Originally shipped as a CSV watcher (May 3, 2026) that monitored an inbox directory for `CAM_*.csv` exports from `my.haveniaq.com`. Replaced with this API-based poller mid-May 2026 (commit `3cccd63`) using the official `havenapi.tzoa.io` Pro API endpoints. Code references: API base URL at [`deploy/energy-stack/haven_ingest/app.py:62`](../deploy/energy-stack/haven_ingest/app.py#L62), endpoint paths at lines 189, 195, 205.

**Env:**
- `HAVEN_AUTH0_DOMAIN` — Auth0 tenant (default `haven-production.auth0.com`)
- `HAVEN_CLIENT_ID` — Auth0 client ID for the HAVEN tenant
- `HAVEN_REFRESH_TOKEN` — bootstrap refresh token captured from a browser login session; rotates per-call after first use, persisted to `/data/haven_token.json`. Re-bootstrap from a fresh browser login if the persisted token expires (typically 30-90 days of disuse).
- `HAVEN_DEVICE_ID` — indoor sensor device ID (default `3645`)
- `HAVEN_OUTDOOR_ID` — paired outdoor station ID (default `60585`)
- `HAVEN_POLL_INTERVAL` — seconds between polls (default 300)
- `HAVEN_BACKFILL_DAYS` — startup backfill window (default 7)
- `INFLUXDB_*` — standard

**Writes:**

| Measurement | Tags | Fields | Coverage |
|---|---|---|---|
| `haven.indoor` | `device_id` | `temp_f`, `temp_c`, `humidity_pct`, `tvoc_ppb`, status enums | 100% on temp/RH/tVOC; `pm25_ugm3` and `airflow_cfm` are flow-dependent (~3%) |
| `haven.outdoor` | `station_id` | `temperature_c` (Celsius only), `humidity`, `dew_point`, AQI/pollutants, pollen | 100% |

**Note on `haven.outdoor` units:** unlike `haven.indoor` which writes both `temp_f` and `temp_c`, `haven.outdoor` writes outdoor temperature in Celsius only (field name `temperature_c`). Downstream queries that need Fahrenheit must convert: `temp_f = temp_c * 9/5 + 32`. Consumers (Grafana panels, scheduler queries, etc.) should account for this asymmetry. If we need outdoor Fahrenheit as a first-class field for Arm B controller queries, the haven-ingest service should be extended to write `temp_f` alongside `temp_c` for symmetry with `haven.indoor`.

**Idempotent on timestamp.** Backfill on startup re-writes the same `(measurement, device_id, ts)` tuples that the prior run wrote — Influx upserts and the data doesn't double.

**Healthcheck:** `/tmp/last_poll_ok` marker.

**Crucial flow-dependent insight:** the sparse `airflow_cfm` and `pm25_ugm3` rows aren't a defect — they're **only populated when the blower is moving air past the duct sensor**. This means:
- Non-null `airflow_cfm` rows = blower runtime ground truth (cross-validate against Refoss `em:9` furnace blower power)
- Non-null `airflow_cfm` value = real measured CFM at that moment, useful for delivered-BTU calc when paired with future supply-air temp instrumentation

---

## ecowitt-ingest

Build: `./ecowitt-ingest` · **Push receiver**, not a poller · Listen port: `${ECOWITT_LISTEN_PORT:-8088}`

HTTP receiver for the Ecowitt GW1200 gateway's "Customized Server" upload. The GW1200 POSTs form-encoded sensor payloads to `http://<pi-lab-LAN-IP>:8088/data/report/` on a fixed 60-second cadence; this service parses the payload, maps Ecowitt protocol fields to the project's canonical `ecowitt.weather` schema, computes dewpoints via the Magnus formula, and writes to InfluxDB.

**Two-stream design (shaded canonical + sun comparator):** the WS90 7-in-1 sits in direct sun on the pergola — its onboard temp/RH (`tempf`/`humidity`) is a sun-exposure comparator, NOT canonical outdoor air temperature for meteorological work. A standalone WN31 on a WH31 channel, mounted in a UV-shielded enclosure on the shaded N/E wall, provides the canonical shaded reading. `ECOWITT_SHADED_CHANNEL` (1–8) tells the parser which channel is the shaded reference; without that env var set, the `outdoor_*` fields are not written — fail-loud rather than silently substituting sun data for shaded data.

**Hardware in this deployment:**
- GW1200B v1.4.7 — gateway, indoor (basement/utility room). Source of `tempinf`/`humidityin`/`baromrelin`.
- WS90 — 7-in-1 on pergola, S/E facing. Wind, solar, UV, piezo rain, lightning, AND onboard temp/RH (sun-exposed at `tempf`/`humidity`).
- WN31 — multi-channel temp/RH on the shaded N/E wall under a UV shield. Dip switches 1–3 set channel 1–8 at the sensor; `ECOWITT_SHADED_CHANNEL` tells the parser which channel is canonical.

**Gateway-side config** (WSView app → Weather Services → Customized): Protocol `Ecowitt`, Server `<pi-lab LAN IP>`, Path `/data/report/`, Port `8088`, Upload `60 seconds`.

**Env:**
- `ECOWITT_LISTEN_PORT` — TCP port to bind (default `8088`)
- `ECOWITT_SHADED_CHANNEL` — WH31 channel (1–8) hosting the shaded reference sensor. When set, `outdoor_temp_f`/`rh_pct`/`dewpoint_f` are sourced from that channel. When unset, those fields are not written.
- `INFLUXDB_URL` (default `http://influxdb:8086`)
- `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET` — standard credentials

**Writes** (single measurement `ecowitt.weather`, tag `gateway` = GW1200 PASSKEY):

| Family | Fields | Source |
|---|---|---|
| Canonical shaded outdoor (analysis source per spec §6) | `ch1_temp_f`, `ch1_rh_pct`, `ch1_dewpoint_f` | WN31 on channel 1 (shaded N/E wall) — emitted via the paired-channel path whenever `ECOWITT_SHADED_CHANNEL` is unset (production config). |
| Conditional emit-alias (descriptive only, NOT consumed by analysis) | `outdoor_temp_f`, `outdoor_rh_pct`, `outdoor_dewpoint_f` | WN31 on `ECOWITT_SHADED_CHANNEL` when that env IS set. Routes the shaded-channel reading through a renamed alias instead of `ch{N}_*`. Not used in production — kept for operator-facing dashboards. |
| WS90 sun-exposed comparator | `ws90_temp_f`, `ws90_rh_pct`, `ws90_dewpoint_f` | WS90 onboard |
| WS90 wind / solar / rain / UV | `wind_mph`, `wind_gust_mph`, `wind_dir_deg`, `wind_gust_max_daily_mph`, `solar_wm2`, `uv_index`, `rain_rate_inhr`, `rain_event_in`, `rain_daily_in`, `rain_state` (0/1), `pressure_inhg` | WS90 + GW1200 relative baro |
| GW1200 internal | `indoor_temp_f`, `indoor_rh_pct`, `baro_abs_inhg` | GW1200 onboard |
| Other paired WH31 channels | `ch{N}_temp_f`, `ch{N}_rh_pct`, `ch{N}_dewpoint_f` | Any WH31/WN31 paired channel `!= ECOWITT_SHADED_CHANNEL`. With `ECOWITT_SHADED_CHANNEL` unset (production), this path includes channel 1. |

**Consumed by:** Controller Cockpit (`deploy/energy-stack/cockpit/backend/influx.py::query_outdoor_now` reads `ch1_*` for live outdoor display). Future bias-correction work pairs `ch1_temp_f` against NWS forecast for affine intercept+slope fit.

**Healthcheck:** `/tmp/last_push_ok` marker.

**Ports:** `8088` is bound on the host (not container-network-only) so the GW1200 on the LAN can reach it. No firewall changes — already allowed by the LAN→Homelab ZBF.

---

## telegram-notifier

Build: `./telegram-notifier` · Two cycles: daily summary at `SUMMARY_HOUR` + alert check every `ALERT_CHECK_INTERVAL_S` seconds

Single-bot Telegram client (`@EnergyStackBot`, separate from any other Telegram bots Chris uses). Sends:

- **Daily summary** at 8 AM local (HTML-formatted): yesterday's cost, kWh, peak demand, fridge anomaly check, HVAC schedule fired, weather forecast for today.
- **Alerts**, checked every 5 min (deduplicated 30 min):
  - **Poller silent**: no recent write to its measurement, per-poller tolerance — sub-minute pollers default 10 min, NWS 70 min, thermostat / comfortnet-publisher 30 min, pjm-dm2-poller 130 min (hourly heartbeat, two missed cycles fire).
  - **Price spike**: current 5-min ComEd price > `$TELEGRAM_PRICE_SPIKE_C` ¢/kWh.
  - **Fridge anomaly**: recent-6h mean > 1.5× and Δ > 50 W vs. 14-day baseline.
  - **HVAC scheduler errors**: latest `hvac.actions` row in the last hour with a non-skip error.
  - **PJM feed failure**: any `pjm.feed_status` row with `success=false` written in the last 10 min fires immediately with the actual `error_type` / `error_msg` from the poller. Real-time signal for fetch failures (auth, schema drift, 4xx/5xx) — no SLA tolerance, no in-season gate.
  - **PJM feed staleness**: per-feed deadman on `pjm.feed_status` for feeds that *had* succeeded before but went longer than their tolerance without another success — DA LMP 25 h, load forecast 14 h, weekly metered 192 h, RTO peak 14 h cooling-season-only, NSPL 168 h within Dec/Jan window. Backstop for the case where the poller is alive (heartbeat fires) and the feed isn't reporting failures (no failure-row alert) but data has nonetheless gone stale — e.g., a fetcher that silently returns 0 points or a PJM publishing-schedule shift. Cold-start (no rows of any kind) stays silent here; the failure-row alert above handles broken-since-deploy cases as soon as the feed actually attempts.
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

**Status:** live as of May 2026. Broker, telegraf consumer, and the Pi-3B-side `comfortnet-publisher` systemd unit are all deployed and healthy; `hvac.comfortnet` is flowing fields `cool_actual_pct`, `heat_actual_pct`, `fan_actual_pct`, `blower_cfm`, `dehumidify_actual_pct` plus their `*_demand_pct` counterparts. Live publisher implementation at [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet); historical design at [`archive/COMFORTNET_PIPELINE.md`](archive/COMFORTNET_PIPELINE.md). Open follow-on: extend the decoder to handle the write side of the user-menu protocol (currently only `0xC1` GetUserMenuResponse is decoded).

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
