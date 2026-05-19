---
date: 2026-05-18
owner: chris
status: active
role-label: code-team
binding_for: scheduler-controller logic per binding spec §3 + §11 #12
---

# HVAC Scheduler Logic

Companion to [`SERVICES.md#hvac-scheduler`](SERVICES.md#hvac-scheduler) (which covers operational concerns: env vars, what it writes to InfluxDB, healthcheck). This doc covers the **logic** — day-type decision tree, all schedules, override mechanism, comfort math, and the fallback schedule programmed into the thermostat for Pi-down scenarios.

## Contents

- [Equipment](#equipment)
- [Decision flow](#decision-flow)
- [Day types](#day-types)
- [Schedules](#schedules)
- [Humid override](#humid-override)
- [Auto-mode safety (deadband + heat floor)](#auto-mode-safety-deadband--heat-floor)
- [Safety supervisor (every setpoint push)](#safety-supervisor-every-setpoint-push)
- [Overrides](#overrides)
- [Thermostat fallback (when Pi is offline)](#thermostat-fallback-when-pi-is-offline)
- [Equipment settings (CTK04AE installer menu)](#equipment-settings-ctk04ae-installer-menu)
- [Capacity peak context (PJM 5CP + ComEd 5CP)](#capacity-peak-context-pjm-5cp--comed-5cp)
- [What this scheduler does NOT do](#what-this-scheduler-does-not-do)

---

## Equipment

Installed 2/27/2019 by Comfort Services Heating & A/C; 10-year parts & labor warranty (exp ~2029-02-27, conditional on annual maintenance). Capable of supporting a serious pre-cool / coast strategy because the 2-stage compressor + variable-speed ECM blower handle long runtimes at low output without short-cycling.

| Component | Model | Spec |
|---|---|---|
| Outdoor AC | Amana ASXC160481BE | 16 SEER, **2-stage** compressor, 4 ton (48,000 BTU/hr); low stage ≈ 32k BTU |
| Indoor evap coil | Amana CAPF4961C6 | 4-ton cased A-coil, 21" cabinet |
| Furnace | Amana AMVM971005CN | 97% AFUE modulating gas, **variable-speed ECM blower**, 100,000 BTU/hr input |
| Thermostat | Amana CTK04AE (Honeywell-OEM whitelabel; native RedLINK Wi-Fi + CT-485 communicating bus) | TCC-cloud reached via Control4 EA-5 + Cinegration driver; CT-485 bus traffic decoded read-only by [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet) for `hvac.comfortnet` measurement |

The 2-stage compressor is what makes the pre-cool strategy viable — stage 1 (~32k BTU) can run for 2-4 hours at low output during pre-cool windows without short-cycling the compressor. Stage 2 (full 48k BTU) is reserved for fast recovery from coast.

---

## Decision flow

The scheduler runs three cycles:

1. **Daily decision @ 21:00 local** — read tomorrow's NWS forecast (+ day-after for streak detection) + latest ComEd 5-min price. Classify into a day-type. Write `hvac.decisions` row with the reasoning.
2. **Intra-day revisit @ each `SCHEDULER_REVISIT_HOURS` (default 06:00, 11:00 local)** — re-poll today's forecast and re-classify. If the day-type shifted (e.g. yesterday's NORMAL forecast became today's HOT after a morning forecast update), overwrite the stored decision. The schedule check picks up the new value on its next tick. **Already-fired actions stay fired**; future actions in the new schedule fire at their scheduled times. Catches the ~1-in-3 marginal-day forecast bust documented in NWS verification literature ([NSSL/Brooks public-forecast verification](https://www.nssl.noaa.gov/users/brooks/public_html/media/okcmed.html); [UW Atmos MOS verification](https://atmos.uw.edu/~jbaars/mvn_paper/mvn_extended.htm)).
3. **Schedule check every minute** — at HH:MM matching any `ScheduleAction` in today's schedule, fire that action: read thermostat state snapshot, push setpoints + fan, log to `hvac.actions`.

**Why intra-day revisit but not multi-source/multi-model**: `api.weather.gov` returns the official WFO grid forecast (per the [NWS API docs](https://www.weather.gov/documentation/services-web-api), forecasts are "created at each NWS Weather Forecast Office (WFO) on their own grid definition, at a resolution of about 2.5km x 2.5km"). NBM (National Blend of Models) is a major input forecasters use, but the API doesn't guarantee that a given gridpoint's response is raw NBM output — WFOs apply local edits and may incorporate additional guidance. For our use the operational point is that NWS gives us a single calibrated, official, free, low-friction source. The dominant residual error for a single home is local site bias, not forecast-source choice — so the highest-leverage upgrade is paired-observation bias correction against an on-site Ecowitt station, not switching forecast sources. Bias correction lands when the Ecowitt has 14+ days of paired observation history.

```
21:00 ─► Read forecast(tomorrow), forecast(day2), comed_price
         │
         ▼
       _classify_one_day(tomorrow)
         │
         ├─ HOT?  AND  _classify_one_day(day2) == HOT  ─► HOT_STREAK_DAY1
         ├─ HOT?                                       ─► HOT
         ├─ NORMAL                                      ─► NORMAL
         └─ MILD                                        ─► MILD
         │
         ▼
       Write hvac.decisions(decision_for_date=tomorrow, day_type=...)

06:00 + 11:00 (revisit) ─► Read forecast(today), forecast(tomorrow), comed_price
                            │
                            ▼
                          decide_day_type(today, day2_forecast=tomorrow)
                            │
                            ├─ same as stored decision  ─► no-op (log only)
                            └─ different                ─► Overwrite hvac.decisions
                                                          (next schedule-check tick
                                                          uses new day_type)

Each minute ─► day_type = fetch_today_decision(today) (or override)
              ─► For each ScheduleAction whose (hour,minute) matches now:
                   ─► Resolve cool setpoint (humid override if dewpoint > 65°F)
                   ─► Read thermostat snapshot
                   ─► If hvac_mode in {Cool, Auto} AND not dry_run:
                        ─► set_cool_setpoint(N), set_heat_setpoint(65),
                            optionally set_fan_mode(Circulate), set_hold(Permanent)
                   ─► Write hvac.actions row
```

`_classify_one_day` thresholds (recalibrated against 2025 ComEd RTP price-spike distribution — see [`CONTROLLER_CONSTANTS.md`](CONTROLLER_CONSTANTS.md) for the locked threshold values + derivation):
- `MILD` if forecast high < 75°F
- `NORMAL` if 75-85°F max (and apparent < 90°F)
- `HOT` if forecast max ≥ 85°F **OR** apparent ≥ 90°F **OR** active heat advisory

---

## Day types

> **Naming note.** Binding spec §11 #12 and EXPERIMENT_DESIGN Appendix A call this enum `MILD / NORMAL / HOT / HOT_STREAK_DAY1`. The code-internal constant for the HOT value is `DAYTYPE_HOT = "HOT_5CP_RISK"` (see `deploy/energy-stack/hvac-scheduler/app.py`); the tables below use the code-canonical string. Both names refer to the same enum value with the same trigger rule.

| Day type | Trigger | Schedule | Behavior |
|---|---|---|---|
| `MILD` | High < 75°F | `MILD_RELEASE_HOLD` at 00:05 | Single action: clear any permanent hold left over from yesterday so the CTK04AE baseline schedule resumes for the day. No active scheduling beyond that. |
| `NORMAL` | 75-85°F max (and apparent < 90°F) | `NORMAL_SCHEDULE` | Standard pre-cool / coast / recover / sleep |
| `HOT_5CP_RISK` (spec name: `HOT`) | ≥ 85°F max OR apparent ≥ 90°F OR heat advisory | `HOT_SCHEDULE` | Aggressive pre-cool. Shutoff timing is dynamic: the real-time price-overlay and 5CP-detector layers drive shutoff per the locked logic below; there is no fixed shutoff clock on HOT days. |
| `HOT_STREAK_DAY1` | HOT today AND day-after also HOT, OR single-day HOT with forecast 5CP-risk escalation (PJM tomorrow-peak forecast > season-5th-highest × 1.05 AND tomorrow's high ≥ 90°F per `precool.should_deepen_precool`) | `HOT_STREAK_DAY1_SCHEDULE` | Even deeper / earlier pre-cool. Day 2 of a multi-day streak runs the regular `HOT_SCHEDULE` (the mass is already there). The §7 single-day forecast 5CP-risk path catches grid-stress days that aren't multi-day heat events. See `decide_day_type` in `deploy/energy-stack/hvac-scheduler/app.py` (both escalation paths return `HOT_STREAK_DAY1`). |

---

## Schedules

All schedules express `(hour, minute, label, cool_setpoint_f, heat_setpoint_f=65, fan_mode=None, cool_setpoint_humid_f=None)`. Heat is always paired so Auto mode works.

### MILD — high < 75°F

| Time | Label | Action | Notes |
|---|---|---|---|
| 00:05 | `MILD_RELEASE_HOLD` | `release_hold=True` | Clear any permanent hold left over from yesterday so the CTK04AE baseline schedule resumes for the day. No setpoints pushed. |

### NORMAL — typical 75-85°F day

| Time | Label | Cool °F | Fan | Notes |
|---|---|---|---|---|
| 06:00 | PRE_COOL | 70 | Auto | Bank thermal mass off-peak (~3-5¢/kWh) |
| 13:00 | COAST | 79 (75 if humid) | Circulate | Drift through ComEd peak; circulate fan moves air without compressor; humid override drops to 75 if dewpoint > 65°F |
| 19:00 | RECOVER | 75 | Auto | Off-peak begins, recover for evening |
| 21:00 | SLEEP | 73 | Auto | Cooler sleep, captures full DTOD overnight cheap window |

### HOT_5CP_RISK — ≥ 85°F max or apparent ≥ 90°F (per EXPERIMENT_DESIGN Appendix A)

| Time | Label | Cool °F | Fan | Notes |
|---|---|---|---|---|
| 04:00 | HOT_PRE_COOL | 68 | Auto | Aggressive pre-cool, deeper mass charge |
| 12:00 | HOT_COAST | 80 (76 if humid) | Circulate | Coast through the high-risk afternoon |
| 19:00 | HOT_RECOVER | 75 | Auto | Transition out of coast |
| 21:00 | SLEEP | 73 | (unchanged) | Same as NORMAL |

**Shutoff timing is dynamic, not scheduled.** Per EXPERIMENT_DESIGN.md §3 (Arm B), the fixed 14:00-18:00 CT shutoff window from the original scheduler is dropped. The §2 real-time RTP price-spike reactivity (scarcity tier ≥ 20¢ → 85°F effective shutoff) and §3 dual-scope 5CP detector (ComEd-zone + PJM-RTO, OR'd) drive 85°F shutoff timing per-tick during the 13:00-20:00 CT eligibility window. "Warmer wins" layer priority lets these dynamic layers push the effective cool above the schedule's coast baseline when conditions warrant.

### HOT_STREAK_DAY1 — HOT today AND HOT tomorrow forecast, OR single-day HOT with forecast 5CP-risk escalation

Two escalation paths produce `HOT_STREAK_DAY1` (both return the same schedule below):

1. **Multi-day heat path:** tomorrow's forecast is HOT AND the day after that is also HOT.
2. **Single-day forecast 5CP-risk path (§7):** tomorrow's forecast is HOT AND `precool.should_deepen_precool` returns True (PJM tomorrow-peak forecast > season-5th-highest × 1.05 AND tomorrow's high ≥ 90°F).

| Time | Label | Cool °F | Fan | Notes |
|---|---|---|---|---|
| 03:00 | STREAK_PRE_COOL_EARLY | 66 | Auto | One hour earlier, two degrees deeper than HOT — banks extra mass for the next-day grid event |
| 12:00 | HOT_COAST | 80 (76 if humid) | Circulate | Same as HOT |
| 19:00 | HOT_RECOVER | 75 | Auto | Same as HOT |
| 21:00 | SLEEP | 73 | (unchanged) | Same as HOT |

Same dynamic-shutoff semantics as HOT_5CP_RISK above.

> **Open re-tune:** post-research review (May 2026) suggests softening pre-cool depth from 68→71-72°F starting at 3am instead of 4am @ 68°F captures 90%+ of peak shift at materially less off-peak kWh in low-mass wood-frame homes. Per NREL/Davis Energy Group field studies. Worth A/B testing in summer 2026. See [PROJECT.md decision log](../PROJECT.md).

---

## Humid override

Constant: `HUMID_DEWPOINT_F = 65`

If the latest `nws.forecast` for today reports `max_dewpoint_f > 65°F`, the scheduler substitutes `cool_setpoint_humid_f` (if defined for that action) for the standard `cool_setpoint_f`. This affects only the COAST actions in NORMAL and HOT schedules.

**Why 65°F:** historical guidance ASHRAE 55-2017 set the indoor dewpoint comfort cap at 62°F. Above 62-65°F outdoor dewpoint, holding a high coast setpoint without running the AC for latent removal pushes indoor dewpoint past the comfort envelope and creates a brutal latent recovery load when AC restarts. Running stage 1 longer at a slightly lower setpoint keeps the coil dehumidifying.

**Open re-tune** (post-research, May 2026): more rigorous answer per ASHRAE 55-2020 humidity ratio cap (0.012 kg/kg ≈ 62°F dewpoint) is to drop the threshold from 65→62°F. See [PROJECT.md decision log](../PROJECT.md).

---

## Auto-mode safety (deadband + heat floor)

Constant: `HEAT_SETPOINT_FLOOR_F = 65`

Every cool setpoint push is paired with `set_heat_setpoint_f(65)` — even on a 95°F day where heat will obviously not run. Two reasons:

1. **Deadband enforcement (CTK04 ISU 3000 — Auto Changeover Deadband):** the CTK04AE enforces a minimum deadband of 3-5°F between heat and cool setpoints in Auto mode. If you push cool=68 without re-asserting heat=65, the thermostat may auto-widen the heat setpoint (to e.g. 63) — that's not a bug, that's the thermostat protecting itself, but it's unpredictable. Pinning heat=65 every time keeps the deadband stable and the behavior predictable.

2. **Winter freeze protection** as a backstop. 65°F is well above pipe-freeze territory and gives a comfortable 15°F+ deadband against typical cool setpoints (70-80°F).

This pattern is encoded in `execute_action()`: `set_cool_setpoint_f(...)` → `set_heat_setpoint_f(65)` → optional `set_fan_mode(...)` → `set_hold_mode("Permanent")`. The Hold ensures the thermostat baseline schedule doesn't override the scheduler's setpoint a few minutes later.

---

## Safety supervisor (every setpoint push)

`safety_supervisor.validate_setpoints(proposed_cool_f, proposed_heat_f, snapshot)` runs in `execute_action` before any setpoint reaches Control4. The function returns a `SupervisorDecision` whose `decision` field is one of three kinds:

| Decision | Trigger | Action |
|---|---|---|
| `approved` | Proposed values inside safe ranges and indoor temp not at emergency level | Pass through unchanged |
| `clamped` | Cool outside `[65, 86]°F` or heat outside `[55, 75]°F` | Clamp to nearest bound; substitute clamped values |
| `emergency` | Snapshot reports `indoor_temp_f >= 86°F` | Override cool to 74°F regardless of schedule |

Precedence is emergency > clamp > approved (first match wins). Each `hvac.actions` row is tagged with `supervisor_decision` and carries `supervisor_reason` + `cool_setpoint_proposed_f` fields, so an audit query can compare what the controller asked for against what actually got pushed. The supervisor logs at `warn` for `clamped` and `error` for `emergency` (so Loki LogQL `{compose_service="hvac-scheduler", level=~"warn|error"}` surfaces them immediately).

**Why this exists:** the supervisor is shared infrastructure that gates every controller variant — current `hvac-scheduler` (Arm B in the SCED experiment) and any future variants. The intent is that no controller (including a buggy one mid-development) can put unsafe setpoints on the thermostat without crossing this gate.

**Bounds rationale:**

- **Cool `[65, 86]`** — accommodates VACATION setpoints (cool=83) and the 85°F shutoff that the §2 price-scarcity tier and §3 5CP detector layers push when conditions warrant, with a small margin. Below 65°F is wasteful overcooling; above 86°F is outside the equipment's design operating envelope.
- **Heat `[55, 75]`** — well above pipe-freeze territory at the low end, well below summer setpoints at the high end (so it never displaces a real comfort intent).
- **Emergency `86°F` indoor / `74°F` cool target** — 86°F indoor is uncomfortable enough that no scheduled action should leave the AC sitting idle; 74°F cool target is aggressive enough to actually pull the indoor temp down quickly while staying inside the safe range above.

**What the v1 supervisor does NOT do (deferred):**

- **Setpoint slew-rate limits** across consecutive applies (would need state across calls; not currently tracked between scheduler ticks)
- **Forecast staleness check** at decision time (cleaner integration with `decide_day_type` than with `execute_action`)
- **Manual halt sentinel** (e.g. touch `/data/scheduler_halt` to disable all pushes)
- **Telegram alert routing** on `emergency` (needs the existing `telegram-notifier` queue/topic — separate PR)

Source: [`deploy/energy-stack/hvac-scheduler/safety_supervisor.py`](../deploy/energy-stack/hvac-scheduler/safety_supervisor.py).

---

## Overrides

File: `/data/overrides.json` (inside the `hvac_scheduler_data` volume).

Two override types:
1. **Day-type override** — force today's day-type regardless of forecast. Useful for "today is a holiday and I'm home, force NORMAL on a forecast-MILD day" or testing.
2. **Vacation override** — flat setpoint across the whole day, with periodic re-affirm pings (every `VACATION_PING_INTERVAL_HOURS`) to keep the Hold pinned in case something briefly clears it.

Format:
```json
[
  {
    "from_date": "2026-07-04",
    "to_date": "2026-07-04",
    "day_type": "NORMAL",
    "cool_setpoint_f": null,
    "heat_setpoint_f": null,
    "note": "July 4th — home all day, force NORMAL not HOT_STREAK"
  },
  {
    "from_date": "2026-08-15",
    "to_date": "2026-08-25",
    "day_type": null,
    "cool_setpoint_f": 82,
    "heat_setpoint_f": 60,
    "fan_mode": "Auto",
    "note": "Vacation — flat 82°F cool"
  }
]
```

Either set `day_type` (force a schedule) OR set `cool_setpoint_f` (vacation flat hold). Setting both together is undefined behavior.

**Editing on the Pi:**
```bash
docker exec -it hvac-scheduler nano /data/overrides.json
# OR (since editor in container may be missing):
docker cp hvac-scheduler:/data/overrides.json /tmp/o.json
nano /tmp/o.json
docker cp /tmp/o.json hvac-scheduler:/data/overrides.json
docker compose restart hvac-scheduler  # (override is re-read each schedule_check, so restart not strictly needed)
```

---

## Thermostat fallback (when Pi is offline)

The schedule programmed directly into the CTK04AE (via TCC web UI, applies same 7 days). Used when the Pi-based scheduler is unavailable (Pi down, network outage, scheduler bug, dry-run disabled).

| Period | Time | Heat °F | Cool °F | Fan | Reasoning |
|---|---|---|---|---|---|
| Wake | 5:00 AM | 68 | 73 | Auto | Light pre-cool window, off-peak pricing |
| Leave | 1:00 PM | 66 | 78 | Circulate | **Peak avoidance** — coast through 1-7pm with circulate fan for perceived comfort. The critical setpoint. |
| Return | 7:00 PM | 68 | 75 | Auto | Off-peak begins, recover to evening comfort |
| Sleep | 10:00 PM | 65 | 74 | Auto | Cool sleep, all off-peak |

**Deadbands** (Honeywell requires 5°F minimum in Auto): Wake 5°✓, Leave 12°✓, Return 7°✓, Sleep 9°✓.

This table is the authoritative Arm-A-schedule freeze; the same content with full provenance lives in [`THERMOSTAT_ARM_A_SCHEDULE.md`](THERMOSTAT_ARM_A_SCHEDULE.md). The two are kept in sync as a Phase 5 OSF-freeze commitment.

**What this fallback DOES:** competent peak avoidance for an "average summer day" + reasonable winter setbacks. ~90% of what the Pi scheduler would do for a typical NORMAL day.

**What this fallback DOES NOT:**
- Doesn't react to today's forecast (always pre-cools, even on 65°F days = wasted)
- Doesn't escalate pre-cool deeper on extreme heat days
- Doesn't shift the peak window if PJM 5CP is forecast outside 2-6pm
- Doesn't react to ComEd hourly pricing spikes

That's fine — fallback only activates when the Pi is offline (rare).

---

## Equipment settings (CTK04AE installer menu)

The CTK04AE has full settings control via its installer menu — every dealer-tunable parameter on the IFC (integrated furnace control) and AC outdoor control board is reachable from the thermostat. Some settings are stored on the equipment boards and the CTK04AE writes through to them via the CT-485 communicating bus; from the operator's perspective there's a single point of configuration.

**Current state is not duplicated here.** The authoritative readout for what's currently on the IFC and AC board is `docs/SETTING_REVIEW.md` in [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet), which decodes user-menu traffic on the CT-485 bus and explains each setting against OEM defaults. To capture a fresh readout, navigate the CTK04AE installer menu while `comfortnet-capture` is running on the Pi 3B; the user-menu decoder then publishes the values.

The settings most relevant to this scheduler, with CTK04AE ISU codes per the *CTK04 ComfortNet Communicating Thermostat Installation Guide* (`I/O-CHTSTAT03 69-2688`, "Installer options (ISU)" pages 11-13). Where a setting is on the IFC user-menu rather than the thermostat ISU menu, the row is labelled as such.

| Source | Code/Label | Setting | Current | Why it matters for the scheduler |
|---|---|---|---|---|
| CTK04 ISU | 3000 | Auto Changeover Deadband (heat ↔ cool, Auto mode) | 3-5°F (verified at CTK04AE menu) | All schedule deadbands satisfy this; pinning heat=65 every push keeps it stable (see "Auto-mode safety" above). |
| CTK04 ISU | 1054, 1056, 1059 | Outdoor Equipment Type / Air Conditioner Communication / AC Type | ASXC16 communicating, 2-stage | Stage count is auto-detected from the equipment via CT-485 self-identification. Pre-cool strategy depends on stage 1 holding long runtimes at low output. |
| CTK04 ISU | 3030 | Staging Control - Cool Differentials | Default (~2°F to call stage 2) | Stage 1 handles loads; stage 2 only on overshoot — maximizes runtime on low stage = better dehumidification + efficiency. |
| CTK04 ISU | 3140 | Cool/Compressor Cycles Per Hour | Default | Default; prevents stage 2 from firing for short bursts. |
| CTK04 ISU | 3240 | Minimum Compressor Off Time | 5 min | Compressor protection. |
| CTK04 ISU | 4090 | **Adaptive Intelligent Recovery** | **OFF** | **Critical for this scheduler.** With it on, the thermostat starts cooling 30-60 min before the scheduled setpoint change, which (a) pulls AC runtime into peak pricing and (b) makes setpoint changes from the Pi unpredictable. With it off, "schedule says 78°F at 13:00" means exactly that. |
| CTK04 ISU | 3020 | Finish With High Cool Stage | OFF (finish on LOW) | Better dehumidification + efficiency at end of cycle. |
| CTK04 ISU | 9000-9080 | Dehumidification Equipment / Control | DEHUM equipment ON, paired with humidity setpoint | IFC drops blower speed during combined cool+DH calls, lengthening runtime and improving latent removal. Off-by-default OEM; toggled ON because Chicago summers are humid enough that latent-load handling matters. |
| CTK04 home screen | Fan | Fan mode | "Schedule" (not "On" or "Auto") | Lets the scheduler's per-period Auto/Circulate setting take effect on each push. |
| CTK04 home screen | Fan | Continuous fan circulate % | 33% (default) | ~20 min/hour — quiet, cheap, mixes air during COAST. |
| IFC user-menu | DEHUM | Dehumidification active flag | ON | Enables the IFC's blower-slowdown behavior during cool+DH calls. Sniffed read-only via ComfortNET; current state in [`SETTING_REVIEW.md`](https://github.com/Promithius-DR/comfortnet/blob/main/docs/SETTING_REVIEW.md). |
| IFC user-menu | CL OFF | Cool blower-off delay | 60s (OEM default) | Blower runs 60s after compressor cutoff, pulling residual latent cooling off the still-wet coil. |
| IFC user-menu | HT TRM, HT ON, HT OFF, HT ADJ, CL TRM, CL PRFL, CL ON | Heat/cool airflow trim, ramping profile, on/off delays | See comfortnet `SETTING_REVIEW.md` | Equipment-side parameters that don't directly affect the scheduler logic but are captured on the bus for monitoring. |

Two distinct "current state" sources to keep in mind:

- **CTK04 ISU values** (thermostat-side): set via the CTK04AE installer menu, persisted in the thermostat's own NVRAM. Confirm by navigating MENU → INSTALLER OPTIONS → VIEW/EDIT CURRENT SETUP on the device.
- **IFC and AC user-menu values** (equipment-side): set via the CTK04AE installer menu but stored on the furnace IFC and AC outdoor control board. Confirm by capturing CT-485 user-menu traffic with `Promithius-DR/comfortnet`'s `comfortnet-capture` on the Pi 3B; current readout lives in that repo's `docs/SETTING_REVIEW.md`.

If you ever factory-reset the CTK04AE, re-apply both rows: CTK04 ISU values from this table, and IFC/AC user-menu values from the comfortnet repo's `SETTING_REVIEW.md`.

---

## Capacity peak context (PJM 5CP + ComEd 5CP)

ComEd Hourly Pricing capacity charges depend on the customer's demand during **two separate sets** of peak hours:

- **PJM 5CP.** The 5 highest RTO-wide demand hours of the year, set by PJM (the regional transmission operator covering Illinois). 2025 empirical data:
  - 4 of 5 RTO peaks landed in the 16:00-17:00 CDT hour.
  - 1 (June 25, 2025) hit 13:00-14:00 CDT.
- **ComEd 5CP.** The 5 highest ComEd-zone demand hours of the year, set by ComEd. Historical window: **noon-18:00 weekdays** in summer per the ComEd Hourly Pricing FAQ. ComEd's zone peaks can land earlier in the day than PJM's RTO-wide peaks because metro-Chicago load shape differs from the broader RTO.

For residential customers on Hourly Pricing + Delivery TOD, capacity-charge impact is computed via [PJM OATT Attachment M-2 (ComEd) §2](https://www.pjm.com/pjmfiles/directory/etariff/MasterTariffs/23TariffSections/18111.pdf):

- A load reduction during one **PJM Five Peak** hour shifts the customer's `ACustCPL` (Average Customer Coincident Peak Load over the five PJM peaks) by roughly `kW / 5`.
- A load reduction during one **ComEd Five Peak** hour shifts `ACustPL` (Average Customer Peak Load over the five ComEd peaks) by roughly `kW / 5`.
- If a single physical hour is **both** a PJM and ComEd Five Peak, the reduction affects both averages.
- The next-year `CPLC_(Y+1)` is then determined by Att. M-2's branching formula:
  - If `ACustCPL >= ACustPL`: `CPLC = ACustCPL`.
  - If `ACustCPL <  ACustPL`: `CPLC = ACustCPL + (ComEdNPL - AComEdCPL) * (ACustPL - ACustCPL) / Σ_5Pc(ACustPL - ACustCPL)` where `ComEdNPL` is ComEd's weather-normalized peak and `AComEdCPL` is the ComEd zone's average coincident peak at the PJM five peaks.

Casual phrasings like "each kW = same full-year dollar value" gloss over this — a single-hour reduction is diluted through a five-hour average, and the dollar value depends on which Att. M-2 branch the customer's annual usage profile sits in. The scheduler still doesn't need to predict WHICH days are 5CP days (PJM/ComEd don't declare them until post-season); aggressive shedding on every HOT day is still the right operational strategy. See [`O2_CAPACITY_RECONSTRUCTION.md`](O2_CAPACITY_RECONSTRUCTION.md) for the three-layer measurement framing and named-scenarios denominator approach.

The scheduler doesn't need to predict WHICH days are 5CP days (neither PJM nor ComEd declares them until after the season); it just needs to be aggressive on every HOT day.

### Schedule coverage of the two peak windows

| Time | Schedule baseline | Dynamic shutoff (Arm B) | PJM 5CP coverage | ComEd 5CP coverage |
|------|-------------------|--------------------------|------------------|---------------------|
| 12:00-13:00 | `HOT_COAST` 80°F | inactive (outside 13-20 CT eligibility window) | low risk (1/5 historical) | **partial** — 80°F limits compressor calls |
| 13:00-19:00 | `HOT_COAST` 80°F | §2 scarcity tier (≥20¢) and §3 5CP detector can push to 85°F per-tick | high risk (4/5 historical RTO; back half of ComEd window) | high risk |
| 19:00+ | `HOT_RECOVER` 75°F | inactive (outside 13-20 CT eligibility window) | post-window recovery | post-window recovery |

Post-prereg (Arm B): the **schedule baseline** is HOT_COAST 80°F through the afternoon. The 85°F shutoff timing comes from dynamic layers (real-time price-spike reactivity + dual-scope 5CP detector), not a hard-coded clock window. The dynamic layers track actual price/load conditions instead of historical-cluster assumptions, which the 2025 RTO peak hour (18:00 CT, not 14-17 as the old fixed window assumed) made visible.

---

## What this scheduler does NOT do

Deliberately scoped out (for clarity, and to avoid future scope creep):

- **Heating-side optimization.** Heat is paired (heat=65) for safety only; no active heat scheduling. Reason: gas furnace, so heating cost is gas (cheap, flat-rate) — DTOD savings only apply to electric (the ECM blower's ~400-600 W). Not worth optimizing.
- **Multi-zone scheduling.** House is single-zone per the original install. If zoning is added later, the scheduler architecture supports it (one ScheduleAction per zone), but no current logic.
- **EV charging coordination.** No EV in service yet. When one arrives, charging would naturally overlap with the off-peak overnight window, no scheduler involvement needed initially.
- **Solar awareness.** No PV system. If installed, would need additional inputs (production forecast) and the COAST window logic would change (run AC during midday solar surplus instead of coasting).
- **Demand response participation in OpenADR.** ComEd doesn't currently expose a residential VTN. Would be straightforward to add as another input to the day-type decision when it does.
- **Day-ahead pricing forecasts.** ComEd doesn't expose. PJM DataMiner2 day-ahead LMP is now ingested by `pjm-dm2-poller` into `pjm.lmp_da_hourly` (Phase 9, May 2026), but the scheduler's day-type classifier doesn't yet consume it — that work feeds a forthcoming 5CP-probability classifier as a future Arm B enhancement.
- **Comfort-aware adaptation.** Override-event logging shipped May 2026 via `thermostat-poller` (writes `hvac.overrides`). The scheduler doesn't yet *consume* that data to tighten/loosen coast ceilings; that's a follow-on once enough data accumulates.
