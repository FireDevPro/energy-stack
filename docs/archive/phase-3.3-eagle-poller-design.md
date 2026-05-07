# Phase 3.3 — EAGLE-3 → InfluxDB Poller (Design)

**Date:** April 2026
**Scope:** Narrow, purpose-built poller. Does one job: reads the Rainforest
EAGLE-3 every 30 seconds and writes to the `energy` bucket. No ComEd, no Sense,
no Honeywell — those are separate future services.

## Goal

Get billing-grade meter data (instantaneous demand + cumulative summation)
landing in InfluxDB so Grafana dashboards have real data to query. This is the
ground-truth source that everything else is compared against.

## Architecture

A new `eagle-poller` service added to the existing `energy-stack` Docker compose
project on Pi-lab. Python 3.13 slim container. Single process, single file,
tight loop.

```
EAGLE-3 (192.168.20.192:443)  ←── HTTPS POST, Basic Auth, XML
          │
          ▼
   eagle-poller container     ──── device_query every 30 s → parse → write
          │
          ▼
   influxdb (service name)    ←── line protocol, token auth
```

`depends_on: influxdb (service_healthy)` ensures the poller waits for Influx on
boot. `restart: unless-stopped` handles recovery.

## Schema

**Measurement:** `eagle.meter`

| Kind | Name | Source / Type |
|---|---|---|
| Tag | `hw_address` | Zigbee `HardwareAddress` of the meter (`0x001350050037ac6b`). Lets queries survive a meter swap. |
| Tag | `source` | Literal `eagle3`. Lets future multi-source queries filter cleanly. |
| Field | `demand_kw` | float, from `zigbee:InstantaneousDemand`. Real-time power (kW). Negative when exporting (future solar). |
| Field | `delivered_kwh` | float, from `zigbee:CurrentSummationDelivered`. Cumulative energy delivered to home (monotonic counter). |
| Field | `received_kwh` | float, from `zigbee:CurrentSummationReceived`. Cumulative energy exported from home (0 today). |

One measurement, one point per poll, three fields. All three values come from
the same meter read at the same timestamp. Per-interval consumption (kWh in the
last N minutes) is computed in Flux with `derivative()` on `delivered_kwh`.

Measurement namespace convention (matches existing code): `<source>.<shape>`.

## Polling behavior

**Startup:**
1. Read and validate environment (missing var → exit 2).
2. One `device_list` call to confirm the configured meter is present. If not:
   log `meter_not_found` with discovered addresses → exit 3. Docker restarts.
3. Connect to InfluxDB, construct a synchronous write API.

**Loop** (every `EAGLE_POLL_INTERVAL` seconds, default 30):
1. POST `device_query` once **per variable** (3 requests per cycle — see
   firmware quirk below).
2. Parse each response's `<Value>` text (like `"1.602 kW"`), take the numeric
   prefix, coerce to float.
3. Write a single point (3 fields) to bucket `energy`.
4. Log a structured JSON line: `{"level":"info","msg":"poll_ok","demand_kw":...,"delivered_kwh":...,"received_kwh":...}`.

**Firmware quirk — one request per variable:**

During initial deploy we discovered that the EAGLE-3 (firmware observed April
2026) silently drops the variable at list position 2 when multiple variables
are requested in a single `device_query`. Verified pairwise and reordered:
position 2 is always dropped, positions 1 and 3 come through. Separate
`<Component>` blocks collapse to only the last. There is no documented limit
and no error signal — the dropped variable is simply missing from the response.

Workaround: issue one `device_query` per variable. Each round-trip on the local
network is ~100–300 ms; three of them fit comfortably in the 30 s budget with
no perceptible overhead.

**Error handling:**

| Failure | Behavior |
|---|---|
| EAGLE read (network, 5xx, parse) | `warn` log, skip cycle, try next one. No crash. |
| InfluxDB write | Uncaught → process exits → Docker restarts. Loud. |
| Partial meter reading | `warn` log naming which fields were received, still write what we have. |
| SIGTERM / SIGINT | Finish any in-flight cycle, close Influx client, exit 0. Sleep broken into 1-s chunks for responsiveness. |

**Timeouts:** connect 5 s, read 10 s. EAGLE-3 is local, so anything longer means
something is wrong.

## Secrets

Added to the existing `~/energy-stack/.env` on Pi-lab and re-encrypted into
`secrets/env.sops.env` via the Phase 3.1 SOPS pattern. No new files, no new
trust domain.

```
EAGLE_IP=192.168.20.192
EAGLE_CLOUD_ID=<from EAGLE device>
EAGLE_INSTALL_CODE=<from EAGLE device>
EAGLE_METER_HW_ADDRESS=0x001350050037ac6b
EAGLE_POLL_INTERVAL=30
```

The InfluxDB admin token is **reused** (no write-scoped token for the poller
yet). Justified by YAGNI for a single-user home lab; noted as a follow-up if
later collectors arrive.

## Files

**New:**
- `deploy/energy-stack/eagle-poller/poller.py` — main process (~180 lines).
- `deploy/energy-stack/eagle-poller/requirements.txt` — pinned `requests`,
  `influxdb-client`.
- `deploy/energy-stack/eagle-poller/Dockerfile` — `python:3.13-slim`, non-root
  user, `PYTHONUNBUFFERED=1`.

**Modified:**
- `deploy/energy-stack/docker-compose.yml` — third service with env-var
  wiring and `depends_on: influxdb (service_healthy)`.
- `deploy/energy-stack/.env.example` — placeholder `EAGLE_*` vars.
- `deploy/energy-stack/README.md` — ops section.
- `~/energy-stack/.env` on Pi-lab — real EAGLE credentials.
- `deploy/energy-stack/secrets/env.sops.env` — re-encrypted.

## Verification

1. `docker compose build eagle-poller` succeeds.
2. `docker compose up -d eagle-poller` — container starts, stays up.
3. `docker compose logs eagle-poller` shows `startup` → `meter_found` → then
   `poll_ok` every 30 s with sane values.
4. Grafana Explore (Flux): `from(bucket:"energy") |> range(start:-5m) |> filter(fn:(r) => r._measurement=="eagle.meter")` returns rows for all three fields.
5. Sanity check: compare the `demand_kw` value to whatever the Rainforest app
   or EAGLE web UI shows at the same moment — should match within one poll.
6. Restart test: `docker compose restart eagle-poller` — resumes writing.

## Out of scope

- ComEd, Sense, Honeywell integrations (future sessions).
- Grafana dashboards (Phase 3.4).
- Write-scoped InfluxDB token (reusing admin for now).
- Healthcheck directive on the poller service (relies on log inspection for now).
- Historical backfill — starting data collection from the moment the poller
  comes up.
- Multi-meter support — code tolerates it via the `hw_address` tag but no
  config surface for multiple meters yet.
- Alerting on staleness — if the poller goes silent for N cycles, nothing fires
  yet. Add when dashboards are in place.

## Rationale — picked options

| Choice | Option chosen | Why not the alternative |
|---|---|---|
| Scope | Narrow, EAGLE-only | Modular collector was premature; we have one source to validate shape against. |
| Runtime | Docker service in existing compose | Same network, same secrets, same ops pattern as Influx/Grafana. Systemd or a separate compose stack would fragment the mental model. |
| Cadence | 30 s | Matches session package; low load; crisp enough charts. 10 s is available via the env var if desired. |
| HTTP lib | `requests` | Async (`httpx`) is overkill for one source, one target, 30-s loop. Stdlib `urllib` is awkward for self-signed HTTPS. |
| Error policy | Retry on read fail; crash on write fail | EAGLE is flakier than Influx; cycling the container on every transient meter hiccup would be noise. Influx being down is a real signal and should be loud. |
| Bucket design | Single `eagle.meter` measurement | Same meter read, same timestamp, always together. Querying is simpler; schema matches the ComEd/Sense namespace convention. |
