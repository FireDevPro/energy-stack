# ComfortNet Pipeline Design

System-level design for getting CT-485 bus telemetry from the ComfortNet sniffer (Pi 3B) into the energy-stack (`pi-lab`). Sibling to [`COMFORTNET_USE_CASES.md`](COMFORTNET_USE_CASES.md) (what we'll do with the data) and [`INFLUXDB_RETENTION.md`](INFLUXDB_RETENTION.md) (how it's stored).

**Status**: broker side shipped (May 2026). The Pi-lab MQTT broker (`mosquitto`), one-shot password provisioner (`mosquitto-init`), and Telegraf MQTT consumer all run under compose profile `mqtt` — `docker compose --profile mqtt up -d`. Cert material lives at `/opt/mosquitto-certs/` on Pi-lab; the Mosquitto password file is regenerated idempotently from `.env` on every profile-up. Pending: the **Pi-3B-side `comfortnet-publisher` systemd unit** that reads decoder output and publishes to `home/utility-room/hvac/comfortnet/<field>`. No `hvac.comfortnet` data is flowing yet. See [`SERVICES.md#mosquitto-mosquitto-init-telegraf-comfortnet-pipeline-profile-mqtt`](SERVICES.md#mosquitto-mosquitto-init-telegraf-comfortnet-pipeline-profile-mqtt) for the operational view.

## Decisions captured

Resolved with Chris on 2026-05-05:

1. **Topic location**: `utility-room` (lowercase, hyphenated; one HVAC in the house, this is where it lives).
2. **MQTT auth**: TLS + username/password. Best-practice middle ground (transport encryption + per-client identity) without the operational overhead of mTLS and cert rotation, which is overkill for a residential single-user LAN.
3. **Form factor**: split publisher and consumer.
   - **Publisher**: native systemd on the Pi 3B (`comfortnet`), reads frames directly from the local capture stream.
   - **Broker + consumer**: Docker on `pi-lab`, alongside the existing `energy-stack`.
4. **Active polling**: deferred. Passive read-only sniffing only in v1. Active polling requires address-claim + arbitration-compliance work that's out of scope for ComfortNet v0.1.

## Architecture

```
Pi 3B (192.168.20.216, VLAN 20)              pi-lab (192.168.20.10, VLAN 20)
┌──────────────────────────────────┐         ┌──────────────────────────────────────┐
│ comfortnet-capture (systemd)     │         │ energy-stack compose                 │
│   ↓ /var/lib/comfortnet/...      │         │                                      │
│ comfortnet-publisher (systemd)   │  TLS    │  mosquitto:8883 (broker, persistent) │
│   reads decoder output           │ ──────▶ │   ↓                                  │
│   publishes to MQTT              │         │  telegraf (mqtt_consumer)            │
│   handles reconnect/queue        │         │   ↓                                  │
│                                  │         │  influxdb (energy / energy-longterm) │
│                                  │         │                                      │
│                                  │         │  n8n (subscribes for events)         │
└──────────────────────────────────┘         └──────────────────────────────────────┘
```

Both hosts on VLAN 20, can reach each other directly. No NAT, no port forwarding, no cloud.

## Mosquitto broker (on `pi-lab`) — shipped

Service in `deploy/energy-stack/docker-compose.yml` under profile `mqtt`. Image: `eclipse-mosquitto:2`. Persistent state on a Docker volume (`mosquitto_data`). Deployed and healthy as of May 2026; healthcheck round-trips `mosquitto_sub -E` against an authorized topic to validate TLS + auth + ACL on every interval.

**Config shape** (`deploy/energy-stack/mosquitto/mosquitto.conf`):

```
listener 8883
protocol mqtt
cafile /mosquitto/config/certs/ca.crt
certfile /mosquitto/config/certs/server.crt
keyfile /mosquitto/config/certs/server.key
require_certificate false
allow_anonymous false
password_file /mosquitto/config/passwords
acl_file /mosquitto/config/acls
persistence true
persistence_location /mosquitto/data/
log_dest stdout
```

Listener on port 8883 (the IANA-assigned MQTT-over-TLS port). No plaintext 1883 listener — TLS-only.

**TLS**: self-signed CA, broker cert with `subjectAltName: DNS:pi-lab.local, IP:192.168.20.10`. One-time generation via `openssl` (script under `deploy/energy-stack/mosquitto/scripts/`). CA cert distributed to clients out-of-band (committed to the energy-stack repo as `mosquitto/certs/ca.crt`; the server key stays out of the repo and only on the Pi). Renewal: pick a long expiry (10 years) since this is a trust anchor for a closed LAN; document the regen procedure in the README.

**Authentication**: `password_file` with bcrypt hashes. Three identities at v1:

| Username | Purpose | Source |
|----------|---------|--------|
| `comfortnet-publisher` | Publishes from Pi 3B | systemd unit env on Pi 3B |
| `telegraf` | Subscribes for InfluxDB writes | `deploy/energy-stack/.env` |
| `n8n` | Subscribes for event automation | n8n credential store |

Passwords stored in `.env` (already SOPS-encrypted in the repo as `secrets/env.sops.env`). The Mosquitto password file is generated at compose-up via an init step similar to the influx-init container, hashing the env-var passwords with `mosquitto_passwd`.

**ACLs** ([`deploy/energy-stack/mosquitto/acls`](../deploy/energy-stack/mosquitto/acls)) — as deployed:

```
user comfortnet-publisher
topic write home/utility-room/hvac/comfortnet/#

user telegraf
topic read home/utility-room/hvac/comfortnet/#

user n8n
topic read home/utility-room/hvac/comfortnet/events/#
```

The publisher can write but not read. Consumers are read-only and scoped to specific subtrees. n8n only sees events (the event-driven automations care about faults and stage transitions, not continuous telemetry). The `#` wildcard already includes the `events/` subtree, so the publisher and telegraf each need a single ACL line.

**Persistence**: Mosquitto persists session state (subscriptions, queued messages for offline durable subscribers) to a Docker volume. Survives broker restarts. Not strictly required since we're using QoS 1 with retain on state topics (see below), but cheap to enable and saves a thundering-herd reconnect storm if the broker restarts while many clients are connected.

## Topic schema

Base prefix: `home/utility-room/hvac/comfortnet/`.

**Continuous state topics** (retain ON, QoS 1):

```
home/utility-room/hvac/comfortnet/heat_demand_pct      float
home/utility-room/hvac/comfortnet/heat_actual_pct      float
home/utility-room/hvac/comfortnet/cool_demand_pct      float
home/utility-room/hvac/comfortnet/cool_actual_pct      float
home/utility-room/hvac/comfortnet/fan_actual_pct       float
home/utility-room/hvac/comfortnet/cfm                  int
home/utility-room/hvac/comfortnet/supply_temp_f        float
home/utility-room/hvac/comfortnet/return_temp_f        float
home/utility-room/hvac/comfortnet/outdoor_temp_f       float
home/utility-room/hvac/comfortnet/humidify_demand_pct  float
home/utility-room/hvac/comfortnet/dehumidify_demand_pct float
home/utility-room/hvac/comfortnet/stage                int
```

Retain ON means subscribers reconnecting see the last known value immediately, no need to wait for the next bus frame. Each topic carries a JSON payload `{"value": <number>, "ts": <iso8601>}` so timestamps survive into Telegraf and InfluxDB without depending on the broker's receive time.

**Event topics** (retain OFF, QoS 1):

```
home/utility-room/hvac/comfortnet/events/fault         payload includes major/minor codes + label
home/utility-room/hvac/comfortnet/events/stage_change  payload includes from/to + reason if known
home/utility-room/hvac/comfortnet/events/demand_actual_mismatch  payload includes thresholds
```

Retain OFF because events are point-in-time. Late subscribers don't want stale fault notifications. Events also carry full JSON payloads with all relevant context (timestamp, severity, decoded labels).

**Why a flat topic tree, not nested by device**: Grafana / Telegraf consume cleanly with one tag per topic-component. The HVAC system as a whole is the unit of analysis; "device" (furnace vs. AC vs. thermostat) becomes a tag inside the InfluxDB write, not a topic-tree level. This also matches the `hvac.comfortnet` measurement design from the retention doc, which expects all continuous fields under one measurement with `device` and `src_node_type` as tags.

**Why `home/utility-room/hvac/comfortnet/` and not just `comfortnet/`**: leaves room for additional rooms or HVAC zones later (`home/garage/hvac/...`) and matches the residential MQTT convention used by Home Assistant and most homelab setups. Cheap to be consistent now.

## Publisher (Pi 3B, native systemd)

Lives in the comfortnet repo as a new module: `comfortnet.publisher`. Reads from the existing decoder pipeline (decoded frame stream) and publishes to MQTT.

**Why native systemd, not Docker**:

- The existing `comfortnet-capture` is already systemd. Splitting one project across systemd + Docker on the same host adds operational surface for no benefit.
- The publisher needs no isolation that the user `chris` doesn't already have (read access to `/var/lib/comfortnet/` is enough).
- Pi 3B has 1 GB RAM; avoiding container overhead is meaningful at that scale.

**Service unit** (`comfortnet/systemd/comfortnet-publisher.service`):

```ini
[Unit]
Description=ComfortNet MQTT publisher
After=network-online.target comfortnet-capture.service
Wants=network-online.target
PartOf=comfortnet.target

[Service]
Type=simple
User=chris
EnvironmentFile=/etc/comfortnet/publisher.env
ExecStart=/opt/comfortnet/.venv/bin/python -m comfortnet.publisher
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

`/etc/comfortnet/publisher.env` (chmod 600, root:chris) holds the broker URL, MQTT credentials, and CA path. **Not** committed.

**Reconnect / queueing behavior**:

- On startup, connect to MQTT with `clean_session=False` and a stable `client_id`. This way the broker's persistent session keeps QoS 1 deliveries queued during transient disconnects.
- On disconnect, **do not** buffer indefinitely in-memory. Drop the oldest continuous-state messages on backpressure (the latest value is what subscribers want anyway, since retain is ON). Events are different: keep a small bounded queue (e.g., last 100 events) and replay on reconnect; lose the oldest if the queue fills. Operator visibility: log structured JSON to systemd journal.
- The publisher does not buffer to disk. If the broker is unreachable for hours, that's a stack-level outage and we accept the gap. Fault events of significance will be re-emitted on the next bus frame matching the fault state, since faults are persistent equipment conditions, not transient signals.

**Source of decoded frames**: the publisher runs the existing decoder pipeline in-process, fed by raw bytes from the serial port. Single systemd service (`comfortnet.service`) that captures + decodes + publishes; capture-only mode remains available via `comfortnet-decode` for ad-hoc debugging.

**Async-compatibility audit of the existing decoder layers** (verified with Chris, 2026-05-05):

| Layer | Async-friendly? | Notes |
|-------|-----------------|-------|
| `comfortnet.decoders.crc.fletcher_variant` | yes — pure CPU | call inline from async context, microseconds per frame at 50 B/s bus rate |
| `Frame` accessors, `iter_mdi` | yes — pure CPU | inline OK |
| `iter_furnace_status` / `iter_furnace_sensors` / `iter_ac_sensors` / `iter_thermostat_config` / `iter_user_menu` | yes — pure CPU | inline OK |
| `parse_user_menu`, `decode_packed_temp` | yes — pure CPU | inline OK |
| `comfortnet.framing.iter_frames` | needs thin adapter | takes a sync `Iterable[tuple[int, int]]`. Write a small `aiter_frames(async_records, gap_ms=...)` that mirrors the gap-slicing logic over an `AsyncIterator`. ~20 lines |
| `comfortnet.replay.iter_records` | sync file reader | one-shot replays from async context: `await loop.run_in_executor(None, list, iter_records(path))`. For live streaming use `aiofiles` or rewrite |
| `comfortnet.capture.capture.py` | **needs replacing for live use** | uses blocking `serial.Serial` (pyserial). Swap to `pyserial-asyncio` or `aioserial`; the writer pattern ports cleanly |

**Implementation skeleton** for the live decode → publish loop:

```python
async def main():
    ser = await serial_asyncio.open_serial_connection(
        url='/dev/hvac485', baudrate=9600,
    )
    async with aiomqtt.Client(...) as mqtt:
        async for ts_ns, b in aiter_serial_bytes(ser):
            async for frame in aiter_frames([(ts_ns, b)], gap_ms=3.5):
                if not (frame.is_well_formed() and frame.is_crc_valid()):
                    continue
                for status in iter_furnace_status(frame):  # sync, fast
                    await mqtt.publish(
                        'home/utility-room/hvac/comfortnet/heat_actual_pct',
                        status.heat_actual_pct,
                    )
                # ... and so on for sensors, AC, user_menu
```

**Concrete prerequisites for the publisher implementation PR**:

1. New module `comfortnet.publisher` with the async loop above plus reconnect / backpressure logic
2. Thin async adapter `comfortnet.framing.aiter_frames(async_records, gap_ms=...)` (~20 lines, mirrors `iter_frames`)
3. Async serial source helper `aiter_serial_bytes(reader)` that produces `(ts_ns, byte)` tuples
4. Add `pyserial-asyncio` and `aiomqtt` to `pyproject.toml`
5. Replace or wrap `comfortnet.capture.capture.py`'s pyserial loop; the rotating-`.bin` writer pattern stays intact for replay-corpus generation

The existing decoder modules (CRC, frame, all `iter_*`) are touched only at the import site; no changes needed for async use.

Update HANDOFF in the comfortnet repo to reflect the merged service shape.

## Consumer (Telegraf in `energy-stack`)

New service in `deploy/energy-stack/docker-compose.yml`. Image: `telegraf:1.31` or current. Mounts `deploy/energy-stack/telegraf/telegraf.conf` read-only.

**Config shape**:

```toml
[[inputs.mqtt_consumer]]
  servers = ["tcp://mosquitto:8883"]  # internal compose network
  topics = [
    "home/utility-room/hvac/comfortnet/+",
    "home/utility-room/hvac/comfortnet/events/+",
  ]
  qos = 1
  client_id = "telegraf-energy-stack"
  persistent_session = true            # only honored if client_id is set
  username = "telegraf"
  password = "${MOSQUITTO_TELEGRAF_PASSWORD}"
  tls_enable = true
  tls_ca = "/etc/telegraf/certs/ca.crt"
  insecure_skip_verify = false
  data_format = "json"
  json_time_key = "ts"
  json_time_format = "2006-01-02T15:04:05Z07:00"
  max_undelivered_messages = 1000

[[outputs.influxdb_v2]]
  urls = ["http://influxdb:8086"]
  token = "${INFLUXDB_INIT_ADMIN_TOKEN}"
  organization = "${INFLUXDB_INIT_ORG}"
  bucket = "energy"
  bucket_tag = "_bucket_override"      # routes events to longterm directly
```

The HANDOFF flagged a few Telegraf gotchas that this config has to handle:

- **`client_id` must be set** for `persistent_session = true` to take effect. Silently ignored otherwise.
- **QoS 1 on both publish and subscribe**, paired with persistent session, gives at-least-once delivery across reconnects.
- **`max_undelivered_messages`** caps the in-flight backlog; tune against `metric_batch_size` to avoid pipeline stalls.
- **Reconnect backoff**: Telegraf defaults are reasonable; cap if needed.

**Routing events to `energy-longterm` directly**: the `bucket_tag` mechanism lets us tag a metric with a per-record bucket override. The publisher tags event-topic payloads with `_bucket_override = "energy-longterm"`; continuous-topic payloads omit the tag and route to `energy` (the default bucket). This means events bypass the downsampling task entirely, matching the retention design.

Alternative: two separate `[[inputs.mqtt_consumer]]` blocks each with its own `[[outputs.influxdb_v2]]`. Simpler to reason about, slightly more config. **Recommend the two-block approach** for clarity; the `bucket_tag` trick is clever but obscure.

## InfluxDB schema

Already specified in [`INFLUXDB_RETENTION.md`](INFLUXDB_RETENTION.md). Recap:

**Measurement `hvac.comfortnet`** (writes to `energy`, downsampled to `energy-longterm` at 1-min mean+max):

- Tags: `device` (`furnace` / `ac` / `thermostat`), `src_node_type` (`0x01` / `0x02` / `0x04`), `location` (`utility-room`)
- Fields: `heat_demand_pct`, `heat_actual_pct`, `cool_demand_pct`, `cool_actual_pct`, `fan_actual_pct`, `cfm`, `supply_temp_f`, `return_temp_f`, `outdoor_temp_f`, `humidify_demand_pct`, `dehumidify_demand_pct`, `stage`

**Measurement `hvac.comfortnet.events`** (writes directly to `energy-longterm`, never aggregated):

- Tags: `device`, `event_type` (`fault` / `stage_change` / `demand_actual_mismatch`), `location`
- Fields: `severity` (string), `major_code` (int), `minor_code` (int), `label` (string for faults), `from_value` / `to_value` (for transitions)

The downsample task already lists `hvac.comfortnet` in its `aggregateMeasurements` set; no Flux changes needed when the publisher comes online.

## Bootstrap and migration

Phased so each phase is independently revertible. None of these touch existing behavior until the publisher actually starts emitting.

1. **TLS material.** Generate the CA + server cert. Commit `ca.crt` to the energy-stack repo. Distribute server key + cert to `pi-lab` via SOPS-encrypted secret in the repo. Distribute `ca.crt` to Pi 3B (publisher) via the same path.
2. **Broker stand-up.** Add Mosquitto service to `docker-compose.yml`, deploy via the standard CI flow. Verify the broker is up, listening on 8883, accepts a TLS connection from a local `mosquitto_sub -t '$SYS/#'` test.
3. **Telegraf consumer.** Add Telegraf service. With no publisher emitting yet, this idles cleanly. Verify Telegraf logs show "subscribed" and no errors.
4. **Publisher implementation.** Land the `comfortnet.publisher` module + systemd unit in the comfortnet repo, behind a feature flag (env var `COMFORTNET_PUBLISH_ENABLED=false` by default). Deploy to Pi 3B; verify the unit comes up and stays up; flip the flag to `true` and watch the broker accept connections.
5. **Smoke test.** Within minutes, `hvac.comfortnet` measurement should have rows in `energy`. After a minute, `energy-longterm` should have downsampled aggregates. Within a bus cycle that produces an event (fault, stage change), the events measurement should record it.
6. **n8n consumer.** Once continuous + event topics are flowing, add an n8n subscription on `events/+` and wire a Telegram message on faults.

## Open decisions for follow-on work

- **Retention of in-memory event queue on the publisher**: 100 events feels right but is a guess. Revisit after a week of operation.
- ~~Whether to merge `comfortnet-capture` and `comfortnet-publisher` into a single service~~: resolved. Merge into one in-process service; the audit above lists exactly which layers are async-safe inline and which need adapters.
- **n8n event consumers**: which events trigger which automations? Recommend starting with fault events → Telegram and demand-vs-actual-mismatch → log-only, expand from there.
- **Active polling**: deferred from this design. If we revisit, the threshold is whether the CTK04's static-fingerprint-only behavior on organic polls is a real problem for any use case the data isn't already covering via the Control4 path. Currently it isn't.
