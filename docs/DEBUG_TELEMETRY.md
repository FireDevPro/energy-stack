---
date: 2026-05-26
owner: chris
status: active
role-label: spec
name: debug-telemetry
---

# Debug telemetry quick-reference

A flat catalog: what telemetry exists in the `energy` Influx bucket, what the controller and analysis actually consume, and what's collected-but-not-consumed. Agents bring their own reasoning — this doc just keeps you from chasing fields no project code reads, or treating non-canonical sources as truth.

Seeded from the 2026-05-25 AC-spike debug session, where the agent flailed by pulling a too-narrow time window, using `hvac.comfortnet.outdoor_temp_f` (condenser-exhaust contaminated, not consumed by any project code) as if it were truth, and conflating Arm A (thermostat program) with Arm B (`hvac_scheduler` day-type schedules).

## §1 — Arm A schedule (CTK04AE programmed)

Arm A is the thermostat's autonomous 4-event daily schedule. Identical Mon–Sun. Drives the thermostat during Arm A periods and during pre-OSF baseline.

| Period | Time CT | Heat °F | Cool °F | Fan |
|---|---|---|---|---|
| Wake   | 5:00 AM  | 68 | 73 | Automatic |
| Leave  | 1:00 PM  | 66 | 78 | Circulate |
| Return | 7:00 PM  | 68 | 75 | Automatic |
| Sleep  | 10:00 PM | 65 | 73 | Automatic |

Source of truth: the deployed CTK04 onboard program + the TCC screenshot it is captured from. These values are reproduced here for at-a-glance reference only. (The dedicated Arm A schedule doc was retired in the controller demolition; the CTK04 onboard schedule's role as the device-owned safety fallback is described in the [commissioning-controller spec, Safety section](superpowers/specs/2026-06-20-commissioning-controller-design.md#safety--device-owned-no-software-supervisor).)

## §2 — Arm B scheduler inputs

What `hvac_scheduler` actively reads to make decisions (Arm B periods only; in shadow mode it still reads but doesn't write).

### From Influx

| Source | Field(s) | Role in decision |
|---|---|---|
| `nws.forecast` (for_period = today / tomorrow / day2) | `high_f`, `max_dewpoint_f`, `is_heat_advisory` | Daily 21:00 decision → day-type (MILD / NORMAL / HOT_5CP_RISK / HOT_STREAK_DAY1) |
| `comed.prices` (period_type = 5min) | `price_cents_per_kwh` | Live RTP price overlay layer |
| `pjm.peak_forecast_rto` | (multi) | 5CP day-ahead planning |
| `pjm.lmp_da_hourly` | (LMP fields) | Day-ahead price reference |
| `pjm.metered_load` | (load) | 5CP season-to-date baseline |
| `pjm.inst_load` | (load) | 5CP live load tracking |
| `pjm.load_forecast` | (forecast) | 5CP day-ahead forecast |
| `hvac.precool_window` | (dead) | Writers removed in the June 2026 demolition; historical rows only |
| `hvac.decisions` | (state) | Read-back of own prior daily decision |

### Not from Influx

| Source | Mechanism | Role |
|---|---|---|
| Thermostat live state | Control4 Director API via `pyControl4.C4Climate` | Current setpoints, hvac_mode, hold_mode — read live, not via Influx. (`hvac.thermostat` measurement is poller *output*, not a scheduler input.) |
| Arm calendar | Python module `arm_calendar.py` (byte-identical copies in scheduler + `tools/analysis/`) | Locked A/B/A/B alternation 2026-06-01 to 2026-11-16 |

## §3 — Influx measurement inventory

Every measurement currently in the `energy` bucket, with consumption status. **FULL** = at least one production consumer (controller, watchdog, notifier, cockpit, or analysis). **PARTIAL** = measurement is read but some specific fields are not consumed. **NONE** = stored for visibility / future use, no current production reader.

| Measurement | Status | Notes |
|---|---|---|
| `eagle.meter` | **FULL** | Whole-house demand → analysis (`tools/analysis/queries/eagle.meter.flux`, `run_shadow_validation`) |
| `refoss.channel` | **FULL** | Per-circuit power → analysis, telegram_notifier, run_shadow_validation |
| `refoss.system` | **NONE** | Refoss device heartbeat / uptime — visibility only |
| `ecowitt.weather` | **PARTIAL** | `ch1_*` is canonical outdoor per binding spec §6; `ws90_*` and the legacy `outdoor_*`/`indoor_*` aliases are not consumed by the controller (see memory `project-ecowitt-canonical-ch1`) |
| `comed.prices` | **FULL** | hvac_scheduler (RTP layer), telegram_notifier, cockpit, analysis |
| `comed.bill` | **FULL** | Monthly billing reconciliation → analysis |
| `comed.bill_lineitems` | **FULL** | Bill line-item breakdown → analysis |
| `nws.forecast` | **FULL** | hvac_scheduler daily decision, telegram_notifier, cockpit, analysis |
| `nws.alerts` | **NONE** | Heat/cold advisories — collected, no current consumer |
| `pjm.coincident_peak` | **FULL** | 5CP analysis → `tools/analysis/queries/pjm.coincident_peak.flux` |
| `pjm.inst_load` | **FULL** | hvac_scheduler/pjm_5cp.py (live 5CP tracking) |
| `pjm.metered_load` | **FULL** | hvac_scheduler/pjm_5cp.py (5CP baseline) |
| `pjm.load_forecast` | **FULL** | hvac_scheduler/pjm_5cp.py (5CP day-ahead) |
| `pjm.lmp_da_hourly` | **FULL** | hvac_scheduler (DA price reference), cockpit |
| `pjm.lmp_rt_hourly` | **PARTIAL** | Analysis-side only (`run_shadow_validation`, queries lib) — not read by controller |
| `pjm.feed_status` | **FULL** | telegram_notifier alerts on feed-down |
| `pjm.poller_heartbeat` | **NONE** | Poller liveness — visibility only |
| `hvac.thermostat` | **FULL** | thermostat_poller output → cockpit, analysis, thermal_observer. NOT a scheduler input (scheduler reads live via Control4 API) |
| `hvac.actions` | **FULL** | Scheduler's action log → thermostat_poller (last-action lookup), telegram_notifier, cockpit, analysis. In shadow mode these are *proposed* actions, not pushed |
| `hvac.arm_mode` | **FULL** | hvac_scheduler_watchdog (alarms on stuck arm), cockpit |
| `hvac.decisions` | **FULL** | hvac_scheduler reads own prior decision, telegram_notifier, cockpit, analysis |
| `hvac.precool_window` | **NONE** | dead since the June 2026 demolition (historical rows only) |
| `hvac.price_overlay` | **FULL** | Scheduler-emit telemetry → analysis (`queries/hvac.price_overlay.flux`) |
| `hvac.5cp_state` | **FULL** | Scheduler-emit telemetry → analysis (`queries/hvac.5cp_state.flux`) |
| `hvac.heartbeat` | **FULL** | hvac_scheduler liveness → cockpit |
| `hvac.input_feed_health` | **NONE** | Emitted by scheduler, no active reader in production code path (visibility only) |
| `hvac.comfortnet` | **PARTIAL** | `cool_actual_pct`, `cool_demand_pct`, `heat_*`, `fan_*`, `blower_cfm`, `humidify_*`, `dehumidify_*` consumed by analysis + thermal_observer. **`outdoor_temp_f` NOT consumed — condenser-exhaust contaminated (+10°F when compressor running); use `ecowitt.weather.ch1_temp_f` instead** |
| `haven.indoor` | **NONE** | HAVEN air-quality sensor indoor — stored for future thermal-model work |
| `haven.outdoor` | **NONE** | HAVEN outdoor — stored, no current consumer |
| `haven.airquality` | **NONE** | HAVEN air-quality fields — stored, no current consumer |
| `sense.device` | **NONE** | Sense energy-monitor device list — legacy, superseded by Refoss + EAGLE |
| `sense.realtime` | **NONE** | Sense real-time power — legacy |
| `sense.trend` | **NONE** | Sense trend data — legacy |
| `mqtt_consumer` | **NONE** | Telegraf auto-generated raw-MQTT measurement — internal plumbing, not a data source |

## §4 — Related references

- Behavior contract: [`AGENTS.md`](../AGENTS.md)
- Doc map: [`INDEX.md`](../INDEX.md)
- Controller spec: [`docs/superpowers/specs/2026-06-20-commissioning-controller-design.md`](superpowers/specs/2026-06-20-commissioning-controller-design.md)
- Per-service detail: [`docs/SERVICES.md`](SERVICES.md)
- Knowledge graph: `.understand-anything/knowledge-graph.json` (grep before grepping the live tree for cross-file questions)
