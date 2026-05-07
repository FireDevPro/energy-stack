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
- [Honeywell ISU settings (the "set once" stuff)](#honeywell-isu-settings-the-set-once-stuff)
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
| Thermostat | Honeywell VisionPRO 8000 (RedLINK Wi-Fi) | TCC-cloud-only (no local API); reached via Control4 EA-5 + Cinegration driver |

The 2-stage compressor is what makes the pre-cool strategy viable — stage 1 (~32k BTU) can run for 2-4 hours at low output during pre-cool windows without short-cycling the compressor. Stage 2 (full 48k BTU) is reserved for fast recovery from coast.

---

## Decision flow

The scheduler runs three cycles:

1. **Daily decision @ 21:00 local** — read tomorrow's NWS forecast (+ day-after for streak detection) + latest ComEd 5-min price. Classify into a day-type. Write `hvac.decisions` row with the reasoning.
2. **Intra-day revisit @ each `SCHEDULER_REVISIT_HOURS` (default 06:00, 11:00 local)** — re-poll today's forecast and re-classify. If the day-type shifted (e.g. yesterday's NORMAL forecast became today's HOT after a morning forecast update), overwrite the stored decision. The schedule check picks up the new value on its next tick. **Already-fired actions stay fired**; future actions in the new schedule fire at their scheduled times. Catches the ~1-in-3 marginal-day forecast bust documented in NWS verification literature ([NSSL/Brooks public-forecast verification](https://www.nssl.noaa.gov/users/brooks/public_html/media/okcmed.html); [UW Atmos MOS verification](https://atmos.uw.edu/~jbaars/mvn_paper/mvn_extended.htm)).
3. **Schedule check every minute** — at HH:MM matching any `ScheduleAction` in today's schedule, fire that action: read thermostat state snapshot, push setpoints + fan, log to `hvac.actions`.

**Why intra-day revisit but not multi-source/multi-model**: NWS api.weather.gov is already NBM-blended (HRRR + GFS + ECMWF + RAP + GEFS, MOS-corrected, URMA-bias-corrected per the [NOAA NBM docs](https://vlab.noaa.gov/web/mdl/nbm)). The dominant residual error for a single home is local site bias, not forecast-model choice — so the highest-leverage upgrade is bias correction with a personal weather station against NWS, not switching forecast sources. Bias correction lands when the Ecowitt has 14+ days of paired observation history.

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

`_classify_one_day` thresholds:
- `MILD` if forecast high < 82°F
- `NORMAL` if 82-94°F
- `HOT` if ≥ 95°F **OR** active heat advisory

---

## Day types

| Day type | Trigger | Schedule | Behavior |
|---|---|---|---|
| `MILD` | High < 82°F | (empty) | Thermostat baseline runs. No active scheduling. |
| `NORMAL` | 82-94°F | `NORMAL_SCHEDULE` | Standard pre-cool / coast / recover / sleep |
| `HOT_5CP_RISK` | ≥ 95°F OR heat advisory | `HOT_SCHEDULE` | Aggressive pre-cool, hard 5CP shutoff window |
| `HOT_STREAK_DAY1` | HOT + day-after also HOT | `HOT_STREAK_DAY1_SCHEDULE` | Even deeper / earlier pre-cool to bank thermal mass for the multi-day event. Day 2 of the streak runs the regular `HOT_SCHEDULE` (the mass is already there). |

---

## Schedules

All schedules express `(hour, minute, label, cool_setpoint_f, heat_setpoint_f=65, fan_mode=None, cool_setpoint_humid_f=None)`. Heat is always paired so Auto mode works.

### NORMAL — typical 82-94°F day

| Time | Label | Cool °F | Fan | Notes |
|---|---|---|---|---|
| 06:00 | PRE_COOL | 70 | Auto | Bank thermal mass off-peak (~3-5¢/kWh) |
| 13:00 | COAST | 79 (75 if humid) | Circulate | Drift through ComEd peak; circulate fan moves air without compressor; humid override drops to 75 if dewpoint > 65°F |
| 19:00 | RECOVER | 75 | Auto | Off-peak begins, recover for evening |
| 21:00 | SLEEP | 73 | Auto | Cooler sleep, captures full DTOD overnight cheap window |

### HOT_5CP_RISK — ≥ 95°F or heat advisory

| Time | Label | Cool °F | Fan | Notes |
|---|---|---|---|---|
| 04:00 | HOT_PRE_COOL | 68 | Auto | Aggressive pre-cool, deeper mass charge |
| 12:00 | HOT_COAST | 80 (76 if humid) | Circulate | Coast through pre-peak |
| 14:00 | HOT_5CP_SHUTOFF | 85 | (unchanged) | **Hard shutoff** for 5CP window; 85°F effectively turns AC off unless the house overshoots |
| 18:00 | HOT_RECOVER_LOW | 78 | Auto | Gentle recovery start (5CP window ends 17:00 typically, 18:00 conservatively) |
| 19:00 | HOT_RECOVER | 75 | (unchanged) | |
| 21:00 | SLEEP | 73 | (unchanged) | Same as NORMAL |

### HOT_STREAK_DAY1 — HOT today AND HOT tomorrow forecast

| Time | Label | Cool °F | Fan | Notes |
|---|---|---|---|---|
| 03:00 | STREAK_PRE_COOL_EARLY | 66 | Auto | One hour earlier, two degrees deeper than HOT — banks extra mass for day 2 |
| 12:00 | HOT_COAST | 80 (76 if humid) | Circulate | Same as HOT |
| 14:00 | HOT_5CP_SHUTOFF | 85 | (unchanged) | Same as HOT |
| 18:00 | HOT_RECOVER_LOW | 78 | Auto | Same as HOT |
| 19:00 | HOT_RECOVER | 75 | (unchanged) | |
| 21:00 | SLEEP | 73 | (unchanged) | Same as HOT |

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

1. **Honeywell ISU 300 deadband enforcement:** the VisionPRO 8000 enforces a minimum deadband (typically 3-5°F) between heat and cool setpoints in Auto mode. If you push cool=68 without re-asserting heat=65, the thermostat may auto-widen the heat setpoint (to e.g. 63) — that's not a bug, that's the thermostat protecting itself, but it's unpredictable. Pinning heat=65 every time keeps the deadband stable and the behavior predictable.

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

**Why this exists:** the supervisor is shared infrastructure that gates every controller — baseline RBC today, model-informed Arm B controller in the SCED experiment, anything later. The intent is that no future controller (including a buggy one mid-development) can put unsafe setpoints on the thermostat without crossing this gate.

**Bounds rationale:**

- **Cool `[65, 86]`** — accommodates VACATION setpoints (cool=83) and `HOT_5CP_SHUTOFF` (cool=85) with a small margin. Below 65°F is wasteful overcooling; above 86°F is outside the equipment's design operating envelope.
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

The schedule programmed directly into the VisionPRO 8000 (via TCC web UI, applies same 7 days). Used when the Pi-based scheduler is unavailable (Pi down, network outage, scheduler bug, dry-run disabled).

| Period | Time | Heat °F | Cool °F | Fan | Reasoning |
|---|---|---|---|---|---|
| Wake | 5:00 AM | 68 | 74 | Auto | Light pre-cool window, off-peak pricing |
| Leave | 1:00 PM | 68 | 78 | Circulate | **Peak avoidance** — coast through 1-7pm with circulate fan for perceived comfort. The critical setpoint. |
| Return | 7:00 PM | 68 | 75 | Auto | Off-peak begins, recover to evening comfort |
| Sleep | 10:00 PM | 65 | 74 | Auto | Cool sleep, all off-peak |

**Deadbands** (Honeywell requires 5°F minimum in Auto): Wake 6°✓, Leave 10°✓, Return 7°✓, Sleep 9°✓.

**What this fallback DOES:** competent peak avoidance for an "average summer day" + reasonable winter setbacks. ~90% of what the Pi scheduler would do for a typical NORMAL day.

**What this fallback DOES NOT:**
- Doesn't react to today's forecast (always pre-cools, even on 65°F days = wasted)
- Doesn't escalate pre-cool deeper on extreme heat days
- Doesn't shift the peak window if PJM 5CP is forecast outside 2-6pm
- Doesn't react to ComEd hourly pricing spikes

That's fine — fallback only activates when the Pi is offline (rare).

---

## Honeywell ISU settings (the "set once" stuff)

Settings programmed directly into the VisionPRO 8000 via the installer setup menu (ISU). These shape how the thermostat interprets every setpoint push.

| ISU | Setting | Value | Why |
|---|---|---|---|
| 300 | Deadband (heat ↔ cool gap, Auto mode) | 5°F (Honeywell minimum) | All schedule deadbands satisfy this |
| 305 | Number of cool stages | 2 | Matches ASXC16 2-stage compressor |
| 308 | Number of heat stages | 2 | Modulating furnace presents as 2-stage to the thermostat (W1/W2) |
| 314 | Cool stage 2 differential | 2°F | Stage 1 handles loads; stage 2 only on >2°F overshoot — maximizes runtime on low stage = better dehumidification + efficiency |
| 315 | Cool inter-stage time delay | 20 min | Default; prevents stage 2 from firing for short bursts |
| 408 | Compressor minimum off time | 5 min | Compressor protection |
| 409 | **Adaptive (Intelligent) Recovery — OFF** | **OFF** | **Critical for this scheduler.** With AR on, the thermostat starts cooling 30-60 min BEFORE the scheduled setpoint change, which (a) pulls AC runtime into peak pricing and (b) makes setpoint changes from the Pi unpredictable. With AR off, "schedule says 78°F at 13:00" means exactly that. |
| (UI) | Fan mode | "Schedule" (not "On" or "Auto") | Lets per-period Auto/Circulate setting take effect |
| (UI) | Finish-with-high-cool-stage | OFF (finish on LOW) | Better dehumidification + efficiency at end of cycle |
| (UI) | Fan type | ECM / Variable Speed | Important for Circulate mode to run at low W instead of full blast |
| (UI) | Continuous fan circulate % | 33% (default) | ~20 min/hour — quiet, cheap, mixes air |

These ISU values are not in code anywhere — they're physical thermostat configuration. **If you ever factory-reset the thermostat, re-apply this list.**

---

## Capacity peak context (PJM 5CP + ComEd 5CP)

ComEd Hourly Pricing capacity charges depend on the customer's demand during **two separate sets** of peak hours:

- **PJM 5CP.** The 5 highest RTO-wide demand hours of the year, set by PJM (the regional transmission operator covering Illinois). 2025 empirical data:
  - 4 of 5 RTO peaks landed in the 16:00-17:00 CDT hour.
  - 1 (June 25, 2025) hit 13:00-14:00 CDT.
- **ComEd 5CP.** The 5 highest ComEd-zone demand hours of the year, set by ComEd. Historical window: **noon-18:00 weekdays** in summer per the ComEd Hourly Pricing FAQ. ComEd's zone peaks can land earlier in the day than PJM's RTO-wide peaks because metro-Chicago load shape differs from the broader RTO.

For residential customers on Hourly Pricing + Delivery TOD, each kW shaved during *any* 5CP hour (PJM or ComEd) saves approximately **$240-480/yr in next-year capacity charges**. The math is the same regardless of which peak set the hour belongs to.

The scheduler doesn't need to predict WHICH days are 5CP days (neither PJM nor ComEd declares them until after the season); it just needs to be aggressive on every HOT day.

### Schedule coverage of the two peak windows

| Time | Action | PJM 5CP coverage | ComEd 5CP coverage |
|------|--------|------------------|---------------------|
| 12:00-14:00 | `HOT_COAST` 80°F | low risk (1/5 historical) | **partial** — 80°F limits compressor calls but isn't a hard cutoff |
| 14:00-18:00 | `HOT_5CP_SHUTOFF` 85°F | high risk (4/5 historical) | high risk (back half of ComEd window) |
| 18:00+ | `HOT_RECOVER_LOW` 78°F → `HOT_RECOVER` 75°F | post-window recovery | post-window recovery |

The 12:00-14:00 ComEd-only window is currently covered by the looser COAST setpoint rather than full shutoff. Open tradeoff: tighten that window by extending the shutoff back to 12:00, at a real comfort cost during those two hours. Empirically, ComEd 5CP hours haven't all clustered at noon-14; if/when actual zone-peak history shows that window matters more, the schedule can shift.

---

## What this scheduler does NOT do

Deliberately scoped out (for clarity, and to avoid future scope creep):

- **Heating-side optimization.** Heat is paired (heat=65) for safety only; no active heat scheduling. Reason: gas furnace, so heating cost is gas (cheap, flat-rate) — DTOD savings only apply to electric (the ECM blower's ~400-600 W). Not worth optimizing.
- **Multi-zone scheduling.** House is single-zone per the original install. If zoning is added later, the scheduler architecture supports it (one ScheduleAction per zone), but no current logic.
- **EV charging coordination.** No EV in service yet. When one arrives, charging would naturally overlap with the off-peak overnight window, no scheduler involvement needed initially.
- **Solar awareness.** No PV system. If installed, would need additional inputs (production forecast) and the COAST window logic would change (run AC during midday solar surplus instead of coasting).
- **Demand response participation in OpenADR.** ComEd doesn't currently expose a residential VTN. Would be straightforward to add as another input to the day-type decision when it does.
- **Day-ahead pricing forecasts.** ComEd doesn't expose. PJM DataMiner2 day-ahead LMP is now ingested by `pjm-dm2-poller` into `pjm.lmp_da_hourly` (Phase 9, May 2026), but the scheduler's day-type classifier doesn't yet consume it — that work feeds the forthcoming 5CP-probability classifier and the Arm B model-informed controller.
- **Comfort-aware adaptation.** Override-event logging shipped May 2026 via `thermostat-poller` (writes `hvac.overrides`). The scheduler doesn't yet *consume* that data to tighten/loosen coast ceilings; that's a follow-on once enough data accumulates.
