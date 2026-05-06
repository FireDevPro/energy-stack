# ComfortNet Use Cases

Pre-integration design doc for the `Promithius-DR/comfortnet` HVAC sniffer. Captures *what we'll do with the data* before standing up Mosquitto + Telegraf + the `hvac.comfortnet` measurement, so the plumbing follows the use cases instead of the other way around.

**Status**: pre-integration. Capture and decode are working on a Pi 3B; bus → MQTT → Telegraf → InfluxDB is HANDOFF steps 7-8, not started.

**Cross-reference**: [`comfortnet/docs/SETTING_REVIEW.md`](https://github.com/Promithius-DR/comfortnet/blob/main/docs/SETTING_REVIEW.md) covers the dealer-tunable settings exposed by the user-menu decoder and the recommended changes (DEHUM=ON, CL OFF=60-90s).

## Context

ComfortNet sniffs the CT-485 communicating bus between the Amana CTK04AE thermostat, the AMVM971005CN modulating gas furnace, and the ASXC160481BE 2-stage AC. It decodes data the equipment talks to itself but the stock thermostat / cloud APIs (Honeywell TCC, Control4) don't expose:

- Furnace firing rate at high resolution (modulation %, not just stage 1/2)
- Blower CFM
- Supply / return air temperatures
- Demand vs. actual percentages (heat, cool, fan, humidify, dehumidify)
- IFC fault history with descriptive labels (`B3 MOTOR LIMITS`, etc.)
- Currently-selected dealer settings
- Outdoor unit stage transitions

The CTK04 thermostat itself does **not** publish live state on CT-485 (returns a static fingerprint to coordinator polls). Room temp / setpoints / humidity continue to come from the existing `thermostat-poller` (Control4 EA-5 → `pyControl4.C4Climate` → `hvac.thermostat`). Consolidation happens in Grafana.

## Capabilities this unlocks

Sorted by leverage. The first one is the only "new capability"; the others are dashboards / events.

### 1. Modulation-aware HVAC scheduling

The current `hvac-scheduler` makes a daily decision at 21:00 ("what's tomorrow's day-type") based on ComEd pricing forecasts and PJM 5CP avoidance. It runs blind to what the equipment is actually doing right now.

Firing rate as an input adds **within-day reactivity**:

- **Stage-aware setback during price spikes.** If ComEd jumps from $0.04 to $0.30 at 4 PM and the AMVM is currently at 35% low-fire, the equipment is already minimal — setting back during the spike doesn't save much. If the AMVM is at 100% high-fire (cold snap, deep ΔT), the spike will hurt; pre-heating in the cheaper hour before the spike lets you ride it at low-fire because the deeper setback gives modulation headroom.
- **5CP recovery accounting.** Today's logic accepts the snap-back call after a 5CP hour. With firing rate visible during the recovery, you can measure how much of the spike-avoidance savings the recovery ate (a 100% high-fire recovery is expensive). That tunes setback depth empirically across a season.

**Required fields**: `hd_act` (heat actual / firing rate %), `cl_act` (cool actual %), `fan_act`, stage transitions, currently-selected demand from the thermostat.

**Cadence**: real-time (per-frame, ~33s coordinator poll cycle). The scheduler reads the latest known value at decision time.

### 2. Real-time HVAC kW attribution

Refoss EM16P already gives kW per circuit (`em:N`). CT-485 doesn't measure compressor amperage — but it tells you *what state the equipment is in* when Refoss is reading those amps. That's the missing piece for clean attribution.

Examples:

- "Refoss reports 1.8 kW on the air handler at 22:15 — was that high-fire furnace + 100% blower, or low-fire + 70% blower?" CT-485 says the answer.
- "PJM 5CP candidate hour. Refoss says HVAC pulled 4.2 kW total. CT-485 says it was AC stage 2 + blower at 100%." Now you can attribute the 5CP contribution and decide whether stage 1 + 85% blower (DEHUM=ON profile) would have been adequate.

**Required fields**: stage (heat / cool, 1 or 2), `hd_act` / `cl_act` %, `fan_act`, CFM.

**Cadence**: ~30s aligned with Refoss polling — close enough that join-on-time gives clean pairs.

### 3. Efficiency and maintenance trends

`(supply_temp - return_temp) / firing_rate %` is an instantaneous "BTUs delivered per BTU burned" proxy. Baseline drifts indicate filter age, coil fouling, blower bearing wear, or ductwork issues.

What you'd see:

- Stable ΔT vs. firing rate trace = healthy
- ΔT trending down month-over-month at the same firing rate = airflow restriction (filter, coil, ductwork)
- ΔT spike at unchanged firing rate = blower CFM dropped (motor failing)
- ΔT mid-band at HT ADJ=Plus (+10%) = over-aired in heating, dial back to HT ADJ=Normal

**Required fields**: supply temp (`0x87` from furnace), return temp (`0x87` from furnace), `hd_act` %, blower CFM.

**Cadence**: per-frame for live; downsample to 1-minute for trend storage. (See "InfluxDB downsampling" in PROJECT.md follow-ups.)

### 4. Fault and demand-vs-actual events to n8n / Telegram

What the existing TCC / Control4 path doesn't expose:

- Furnace IFC fault history with descriptive labels (`B3 MOTOR LIMITS`, `B6 MOTOR VOLTS`, `B4 MOTOR TRIPS`) via the user-menu decoder DIAG page
- Demand vs. actual mismatch (thermostat asks for 50% heat, furnace responds with 35% — lockout? anti-cycling delay? capacity limit?)
- Stage-transition events on a 2-stage condenser (TCC may report mode but not actual outdoor stage)

Routing: per-frame JSON to MQTT (`home/<location>/hvac/comfortnet/<field>`), n8n subscribes, Telegram on faults and demand-vs-actual deltas above threshold. Retain ON for current state (firing rate, current stage, mode), retain OFF for events.

**Required fields**: `fault_critical` / `fault_minor` bytes, `hd_dem` vs. `hd_act`, `cl_dem` vs. `cl_act`, fault history from `0xC1` DIAG page parsed periodically.

**Cadence**: events only. Periodic fault-history snapshot every ~hour (the user-menu poll appears organically when a service tool is active; we may need to handle the case where no one is browsing the menu).

## What is *not* a use case

These came up and got rejected:

- **ASXC outdoor temperature as ambient reference.** The sensor is mounted on the condenser cabinet, used by the equipment for compressor low-ambient lockout and modulating capacity decisions. In summer it's biased by solar gain on the metal cabinet, self-heat from waste airflow, and pavement reflection. Bench-validated within 1°F at idle with no sun (55-57°F vs. 56°F operator-recorded), but not a clean ambient reference under load. **NWS stays the ambient source.** The CT-485 outdoor reading is logged as an *equipment-state signal*, not a temperature reference.
- **Outdoor unit current draw.** Not in the CT-485 protocol. Refoss EM16P is the kW source for the condenser circuit. CT-485 contributes the *interpretation* (stage, demand %), not the measurement.

## Field requirements summary

| Field | Source datagram | Use case(s) |
|-------|-----------------|------|
| `hd_dem`, `hd_act` (heat demand / actual %) | furnace `0x82` `db_len=22` bytes 2 / 15 | Modulation-aware sched, attribution, efficiency trends, fault events |
| `cl_dem`, `cl_act` | furnace `0x82` (and AC `0x82` `db_len=9` once decoded) | Modulation-aware sched, attribution |
| `fan_dem`, `fan_act` | furnace `0x82` byte 5 / 17 | Attribution, efficiency trends |
| Blower CFM | furnace `0x82` bytes 13-14 (u16 LE) | Attribution, efficiency trends |
| Supply / return temp | furnace `0x87` `tag∈{0,1} db_len=2` (16-bit packed °F) | Efficiency trends |
| Outdoor temp | AC `0x87` `tag=0 db_len=2` | Equipment-state context only (not ambient) |
| Stage | derived from demand / actual transitions | Attribution, fault events |
| Fault bytes | furnace `0x82` bytes 0 / 1 | Fault events |
| Fault history with labels | `0xC1` DIAG page | Fault events |
| Currently-selected dealer settings | `0xC1` SETUP / CL SETUP pages | Periodic snapshot for `SETTING_REVIEW.md` validation |

## Open decisions before integration

These need a call before the MQTT pipeline lands:

1. **Topic naming**: `home/<location>/hvac/comfortnet/<field>` is the tentative shape. `<location>` candidates: `mechanical`, `basement`, or omit (single-HVAC household).
2. **Cadence per topic**: per-frame vs. downsampled at the publish layer? Per-frame keeps the data faithful but multiplies cardinality. Recommend per-frame for state topics, on-change for events.
3. **MQTT auth**: username/password on VLAN 20 vs. mTLS from day one. (HANDOFF flags both as live options.)
4. **Poller form factor**: Docker container in `energy-stack` (matches existing `*-poller` pattern) vs. native systemd on the Pi 3B. HANDOFF leans Docker.
5. **Active polling**: do we ever send `GetSensorData (0x07)` from the sniffer to extract more from the CTK04, or stay strictly read-only? Currently strictly read-only by guardrail.

## Out-of-scope for v1

- **Bus writes.** ComfortNet stays read-only. Setting changes go through the CTK04 installer menu, the Cool Cloud HVAC app, the IFC pushbuttons, or the dealer service tool.
- **Setting changes.** See `comfortnet/docs/SETTING_REVIEW.md`.
- **Write-side decoder.** When you make any setting change, run a capture during the change so we can identify the SetUserMenu opcode and extend the decoder in a follow-up PR. Tracked in [comfortnet#6](https://github.com/Promithius-DR/comfortnet/pull/6).
