---
date: 2026-06-01
owner: chris
status: active
role-label: research
name: ctk04-thermostat-reference
---

# CTK04 / CTK04AE Thermostat — Subject-Matter Reference

Deep reference on how our **Amana CTK04AE** ComfortNet communicating thermostat
actually senses temperature and decides to run the Amana HVAC. The emphasis is the
question that matters most for our control stack: **what temperature does the
thermostat compare against the setpoint, and how do its built-in sensor and the
RedLINK wireless indoor sensors combine to produce it.**

> **Sourcing note.** Direct PDF fetch was blocked (HTTP 403) in the research
> environment, so the quotes below are extracted by web search against the
> underlying authoritative manuals rather than read byte-for-byte. Confidence is
> flagged per claim. Items needing a direct manual read to reach high confidence
> are collected in [Open / unverified](#open--unverified-items). Primary sources
> are listed at the end.

## Contents

- [TL;DR](#tldr)
- [Platform identity: it's Prestige-IAQ-class, NOT a VisionPRO](#platform-identity-its-prestige-iaq-class-not-a-visionpro)
- [The control-sensor question (the headline)](#the-control-sensor-question-the-headline)
- [RedLINK ecosystem + how it coexists with CT-485](#redlink-ecosystem--how-it-coexists-with-ct-485)
- [Setpoint → actual control math](#setpoint--actual-control-math)
- [Staging on our communicating equipment](#staging-on-our-communicating-equipment)
- [Adaptive Intelligent Recovery (ISU 4090)](#adaptive-intelligent-recovery-isu-4090)
- [Temperature resolution, display & calibration](#temperature-resolution-display--calibration)
- [Humidity & dehumidification](#humidity--dehumidification)
- [CTK04 ISU quick reference](#ctk04-isu-quick-reference)
- [How this maps onto our stack](#how-this-maps-onto-our-stack)
- [Repo references to correct](#repo-references-to-correct)
- [Open / unverified items](#open--unverified-items)
- [Sources](#sources)

---

## TL;DR

1. **The CTK04 is a Honeywell-OEM communicating thermostat on the Prestige IAQ
   (RedLINK 2.0) platform — not a VisionPRO 8000.** Its grouped 4-digit ISU
   numbering (3000 / 3030 / 9070 / 5040 …) matches the Prestige IAQ installation
   guide (Honeywell **69-2490**) exactly; the VisionPRO 8000's `03xx`/`09xx`
   scheme does not match. The "VisionPRO 8000" label currently sitting in several
   repo files is **inaccurate** and should be corrected (see
   [below](#repo-references-to-correct)).
2. **The temperature the CTK04 controls to is the *average of whichever sensors
   are assigned "for temperature control"* — and the built-in wall sensor can be
   excluded entirely.** With our RedLINK wireless indoor sensor assigned as the
   sole control sensor and the built-in sensor excluded, the thermostat controls
   purely off the remote. The home-screen temperature, the TCC value, and
   therefore our polled `indoor_temp_f` are all **that control-sensor temperature**,
   not the wall unit's internal reading. This is exactly what our docs mean by
   "RedLINK primary BR = control sensor."
3. The CTK04 supports **up to 6 RedLINK wireless indoor sensors** (Goodman/Honeywell
   **C7189R1004**), each individually assignable to temperature control,
   humidification, and/or dehumidification.
4. It controls by **cycle-rate (CPH)** logic, not a fixed differential; cooling
   default ≈ **3 CPH**. In **communicating (CT-485)** mode the thermostat issues a
   *demand* and the furnace IFC / outdoor board own staging, modulation, and blower
   CFM — so several thermostat-side staging ISUs matter most in legacy 24 V mode.

---

## Platform identity: it's Prestige-IAQ-class, NOT a VisionPRO

The CTK04 / CTK04AE is the Goodman / Amana / Daikin **ComfortNet communicating
thermostat**, manufactured by Honeywell (docs carry "© Honeywell International").
Install guide: Honeywell form **69-2688** / Goodman literature code
**I/O-CHTSTAT03**; the manual's own title is *"ComfortNet CTK04 Communicating
Thermostat **With wireless accessories** System."* `[high]`

The closest Honeywell retail analog — and the correct cross-reference for its
internal logic — is the **Prestige IAQ / Prestige 2.0 (THX9321 / THX9421)** with
the RedLINK Equipment Interface Module, documented in Honeywell **69-2490**. The
shared installer-setup numbering is the proof:

| Function | CTK04 ISU | Prestige IAQ (69-2490) | VisionPRO 8000 |
|---|---|---|---|
| Temperature-control sensor selection / averaging | 5040 (5000–6190 family) | **5040** ✓ | n/a (much simpler) |
| Dehumidification equipment | 9000 | **9000** ✓ | `09xx` (different) |
| Dehumidification control method | 9080 | **9080** ✓ | — |
| Dehumidify in which modes | 9120 | **9120** ✓ | — |
| Auto-changeover deadband | 3000-area | 3xxx-area | `0300` / `0310` ✗ |

Plus the platform-class features that the VisionPRO 8000 does **not** have but the
Prestige IAQ and CTK04 both do: **up to 6 wireless indoor sensors**, **RedLINK 2.0**,
**EIM-style equipment interface**, and full **IAQ dehumidification (overcool /
reheat)**. `[high]`

> One web source explicitly pushes back on calling the CTK04 a VisionPRO ("the
> CTK04 is its own product line"). The honest framing: **Honeywell-OEM, Prestige-IAQ-class
> RedLINK 2.0 platform**, relabeled and firmware-tailored for ComfortNet/CT-485
> communicating equipment. Treat 69-2490 as the platform reference and 69-2688 as
> the CTK04-specific authority; where they disagree, 69-2688 wins.

---

## The control-sensor question (the headline)

**How the "actual" temperature is formed.** Each indoor sensor — *including the
thermostat's own built-in sensor* — is individually assignable, in installer
setup, to be used **for temperature control** (Yes/No), and separately for
humidification and dehumidification. The thermostat then **controls to the simple
average of every sensor set to "control."** `[high]`

Verbatim-as-extracted from the Prestige IAQ / CTK04 documentation:

- *"The thermostat can be set to respond to its **internal** temperature sensor, or
  to an optional **remote** indoor sensor. **If multiple sensors are used, the
  thermostat will average** the temperature detected at each sensor."*
- *"**Installer Setup option 5040** allows selection of which sensors will be used
  for temperature control, with the default being **'Sensors are Averaged.'**"*
- *"If more than one sensor is installed, the display shows the **average** of
  temperature readings from all [control] sensors… You can select **'No'** if you do
  **not** want a specific sensor to be used for temperature control or be part of
  the temperature average."*
- *"The temperature reading displayed on the home screen is from the **sensor(s)
  that are being used for temperature control**."*
- CTK04 spec language: *"When paired with a Wireless Indoor Sensor(s) you have the
  ability to **choose which sensor(s) to use** for temperature, humidification and
  dehumidification. They can be used in combination for **temperature averaging — or
  individually**."*

**Consequences that matter for us:**

- **Yes, the built-in wall sensor can be excluded from control** — with one
  important caveat for *our* setup (below). Assign the RedLINK indoor sensor as the
  control sensor and set the built-in to "No," and the CTK04 controls **purely off
  the remote** — the "RedLINK primary BR (control sensor)" configuration our thermal
  docs assume.
- **⚠ Single-remote ambiguity — verify on the unit.** Sources conflict on whether a
  **single** remote fully excludes the built-in sensor. The per-sensor menu *appears*
  to allow excluding the internal (set it "No"), and one extract says you can select
  "No" so a sensor is "not part of the temperature average." But multiple field
  reports state the internal sensor is only reliably dropped once **2 + remotes** are
  enrolled for control; with a single remote, some firmware keeps the **internal in
  the control average**. `[low — contradictory across sources]` **This matters
  because we run exactly one RedLINK control sensor:** our `indoor_temp_f` is then
  either the pure remote reading **or** `(remote + wall)/2`. Easy to check on the
  device: in ISU 5040, confirm the built-in is set to *not* control, then verify the
  home-screen temp tracks the RedLINK sensor exactly rather than sitting between it
  and the thermostat's wall location. (Display vs. control participation are
  configured **separately** — the platform keeps a "display average" distinct from a
  "control average," so the built-in can show on-screen yet be excluded from control,
  or vice-versa.) `[med-high]`
- **Averaging is a plain mean**, not a weighted blend. `[high — no source describes
  any weighting; medium that no weighting exists]` With N control sensors the
  control temperature is `(s1 + … + sN) / N`.
- **Display follows control.** The home-screen number, the TCC value, and therefore
  our polled `indoor_temp_f` / `indoor_temp_f_hires` are the **control-sensor
  temperature** (the average of the "Yes" sensors) — not the thermostat's own wall
  reading when the wall sensor is excluded. So when we relocated the RedLINK control
  sensor (primary BR → 2F hallway, 2026-05-26), we moved *the very temperature the
  setpoint is compared against and the value we record*.
- **Up to 6 wireless indoor sensors** are supported on this platform. `[high]`

**Sensor dropout / lost RF / dead battery.** The thermostat raises a
"wireless sensor not responding" / low-battery alert (it warns ~2 months before
depletion; the C7189R LED flashes red ~2–3 weeks out). What it does for *control*
when a control sensor goes silent is **not cleanly documented and may be worse than
a silent fallback**: field/technician reports indicate it flags a **sensor fault**
and can **withhold operation** (drive toward "System Off") until comms are restored,
rather than quietly reverting to the built-in sensor. `[low–medium — forum-derived,
needs primary-manual confirmation]` **This is an operational risk for us:** since we
control off a single RedLINK sensor, a dead C7189R battery could either silently
shift the control temperature (and our logged `indoor_temp_f`) onto the wall unit
**or** stall the system entirely. Worth a monitoring alert on sensor-not-responding
and on `indoor_temp_f` discontinuities. Resolve the exact behavior against 69-2688
before relying on either interpretation.

---

## RedLINK ecosystem + how it coexists with CT-485

**RedLINK** is Honeywell's proprietary RF protocol in the **900 MHz ISM band
(902–928 MHz)**. `[high]` Our CTK04 is the **RedLINK 2.0** generation — *not* the
later RedLINK 3.0 / ElitePRO (which supports 20 sensors; do not conflate). `[high]`

Accessory family (relevant subset):

| Accessory | Model | Role |
|---|---|---|
| Wireless **indoor** air sensor | **C7189R1004** | Enrolls as a remote temp **+ humidity** sensor; battery-powered (2 cells); RF range ~200 ft. The thing we control off. `[high]` |
| Wireless **outdoor** sensor | C7089R1013 | RedLINK wireless (not wired) outdoor temp/humidity. `[high]` |
| RedLINK **Internet Gateway** | THM6000R7001 | Ethernet → Total Connect Comfort cloud; up to **4 thermostats** per gateway. `[high]` |
| Portable Comfort Control | REM5000 | Handheld remote control/sensing. `[high]` |
| Equipment Interface Module (EIM) | THM5421R | Equipment bridge for *conventional* installs. **Not used on the CTK04** — see below. `[med-high]` |

**Enrollment:** on the thermostat, MENU → Installer Options → (date-code password)
→ **Wireless Device Manager → Add Device**, then press CONNECT on each accessory.
`[high]`

**The coexistence answer (this was the genuinely ambiguous part):**

- **Equipment** is driven over the **CT-485 ComfortNet communicating bus** — 4 wires
  (terminals **1, 2, R, C**) from thermostat to the furnace IFC; data lines 1 & 2
  continue from the IFC to the outdoor unit; ≤ 100 ft at 18 AWG. **Not** Y/W/G relay
  wiring. `[high]`
- **RedLINK runs alongside it**, purely for accessories and cloud — *and* the
  RedLINK wireless **indoor sensors actively feed the control temperature**, not
  just the app. So: **CT-485 carries the demand to the equipment; RedLINK carries
  the temperature the demand is based on.** `[high]`
- **No EIM needed on the CTK04.** Because the ComfortNet IFC is itself the
  communicating equipment controller, the CTK04 talks to it directly over CT-485.
  (The THM5421R EIM exists for conventional Prestige/VisionPRO installs on
  non-communicating equipment.) `[med-high — inferred: no CTK04 source mentions an
  EIM; direct wiring is documented]`
- **No built-in Wi-Fi.** Remote/app access requires the **THM6000R RedLINK Internet
  Gateway** → TCC. `[high]` (In our stack we don't even use the TCC app path
  directly — we reach TCC via Control4 EA-5 + the Cinegration driver. The gateway
  is still what puts the thermostat on TCC in the first place.)

---

## Setpoint → actual control math

**Cycle-rate (CPH), not a fixed differential.** The CTK04 family uses a
**Cycles-Per-Hour** anticipator-style algorithm (Honeywell's P+I control) rather
than a user-set on/off temperature differential. CPH caps the *maximum* cycles per
hour, measured at 50 % load. `[high]`

- A setting of **3 CPH** means at 50 % load the system cycles ≤ 3×/hr (≈ 10 min on /
  10 min off); it cycles **less** often at higher or lower load. `[high]`
- **Default cooling / compressor CPH = 3** (range 1–6); this is CTK04 **ISU 3140
  "Cool/Compressor Cycles Per Hour."** `[high on ISU label; med on default=3, inferred
  from platform]`
- Practical swing is on the order of **±1 °F** around setpoint — an emergent
  property of the CPH/anticipation loop, not a configured deadband. `[medium]`

**Staging philosophy (selectable):**

- **Droop / degrees-from-setpoint** — the next stage energizes when the room temp
  reaches a chosen number of degrees away from setpoint; **or**
- **"Droopless" / continuous-run upstaging** — the higher stage energizes when the
  control senses stage 1 is running at ~90 % capacity (adaptive), independent of a
  fixed offset. `[high]`
- An **upstage timer** also forces the next stage when it expires, whichever comes
  first. `[high]`
- **"Finish With High Cool Stage" = CTK04 ISU 3020** — keep the high stage running
  to setpoint vs. dropping back to low near setpoint. We run this **OFF (finish on
  low)** for better end-of-cycle dehumidification. `[high]`
- **"Staging Control – Cool Differentials" = CTK04 ISU 3030.** Exact degree defaults
  not surfaced verbatim. `[med-high on label; default unverified]`
- **"Minimum Compressor Off Time" = CTK04 ISU 3240**, default commonly **5 min**
  short-cycle protection. `[high on label; medium on 5-min default]`
- **Auto-changeover deadband = ISU 3000-area**, default 3 °F, range ~2–9 °F; when
  dehumidification + Auto are both enabled the **minimum is forced up (≈ 5 °F)**.
  This is why our scheduler's "pin heat = 65 every push" keeps a stable, predictable
  deadband. `[high]`

**Communicating vs legacy — the important distinction:**

In **communicating (CT-485)** mode the thermostat does **not** energize discrete
stage relays. It transmits a **heat/cool demand** over the bus, and the **equipment
controls** (furnace IFC and the outdoor unit's board) decide staging, modulation
rate, and blower CFM. `[med-high — architecture well corroborated; the precise claim
that a numeric demand-% is transmitted and mapped to a firing rate is consistent
with modulating-furnace behavior but not quoted verbatim]` Therefore several
thermostat-side staging ISUs (droop, finish-with-high-stage, upstage timers) have
their **clearest effect in legacy 24 V mode**; in full communicating mode the
equipment can override or own those decisions. **Which ISUs are ignored in
communicating mode is not crisply documented** — open item.

---

## Staging on our communicating equipment

For our **ASXC160481BE (2-stage AC)** + **AMVM971005CN (modulating furnace)**:

- **Cooling:** in communicating mode the **outdoor board** decides when to bring in
  stage 1 vs stage 2 from the transmitted cool demand, rather than the thermostat's
  Y1/Y2. In legacy mode the same unit would stage off the thermostat's
  CPH/droop/upstage logic above. `[med-high]`
- **Heating:** the AMVM97's variable-speed ECM **blower CFM is derived from burner
  firing rate**, and the **IFC** varies inducer speed — these decisions live in the
  equipment control when communicating, not in the thermostat. `[med-high]` This is
  exactly why the CT-485 bus sniffer (`Promithius-DR/comfortnet`, `hvac.comfortnet`)
  is the only way we see true modulation %, not just stage 1/2.

This is consistent with our `HVAC_LOGIC.md` "2-stage compressor makes pre-cool
viable" reasoning and the ComfortNet capture giving us firing-rate visibility the
thermostat alone can't.

---

## Adaptive Intelligent Recovery (ISU 4090)

AIR makes the thermostat **start early so the next scheduled setpoint is reached AT
the scheduled time** (e.g. heat comes on before a 6:00 AM Wake so it's 70 °F *at*
6:00, not later). `[high]`

- It **computes the recovery ramp** from (a) how far room temp is from the target,
  (b) prior equipment performance, and (c) weather history, ramping gradually rather
  than blasting. `[high]`
- It **learns over ~1 week** and self-corrects daily (reached too early/late → ramp
  adjusted next day). `[high]`
- **Early-start window is variable** — roughly **15 min to > 2 hr** depending on the
  required swing; no hard documented cap found. `[medium]`
- CTK04 behavior note: **backup/aux heat is locked out during a programmed AIR
  recovery.** `[med-high]`
- The display shows **"Recovery"** while active; during the ramp the control compares
  measured temp to the *moving* ramp target, not just the final setpoint. `[high]`
- **CTK04 ISU 4090** toggles it. **This is the single per-arm difference on the
  thermostat in our experiment: ON for Arm A, OFF for Arm B** so the Pi's setpoint
  pushes land at the exact scheduled minute (see `THERMOSTAT_ARM_A_SCHEDULE.md`,
  `HVAC_LOGIC.md`). The standalone Prestige/VisionPRO equivalent toggle is a
  different ISU number — irrelevant to us, we use 4090.

---

## Temperature resolution, display & calibration

**Headline: the panel shows whole °F, but the thermostat senses and *controls* on a
finer ~0.1 °C value.** Three distinct resolutions, don't conflate them:

| Layer | Resolution | Confidence |
|---|---|---|
| Front-panel display | whole °F (0.5 °C in Celsius mode) | high |
| Sensed / represented temperature | native **0.1 °C (~0.18 °F)** | **high — proven on our unit** |
| Control decision (cool on/off) | uses the fine value, not the rounded display | **high (mechanism + behavior)** |

### Proven on our own hardware (the decisive evidence)

`THERMAL_ROUGH_CUT_2026-05-26.md` captured the Director exposing, simultaneously,
`TEMPERATURE_F = 78` and `TEMPERATURE_C = 25.5`. The arithmetic settles it:

- `25.5 °C → 77.9 °F` exactly.
- `78 °F → 25.56 °C`, which at 0.1° resolution would round to **25.6**, not 25.5.

So if the device only *knew* "78 °F" and converted for the API, it would emit 25.6.
It emitted **25.5** — i.e. the **native value is 77.9 °F (= 25.5 °C)** and the panel's
"78" is the rounded-up whole-degree *display*. The CTK04's actual sensed/reported
temperature carries tenths-of-°C; the whole number is cosmetic. Our
`indoor_temp_f_hires` field is exactly that native value surfaced. `[high]`

### Why the control loop uses the fine value, not the rounded display

1. **The algorithm is mathematically continuous.** Honeywell's documented control is
   **proportional-plus-integral (P+I)**: proportional error = (sensed temp − setpoint),
   plus an integral-over-time term, with a proprietary droop-elimination correction. A
   P+I / CPH anticipator loop **cannot** run on whole-degree-quantized input — it needs
   the sub-degree error. Honeywell's own phrasing: *"even though the unit is displaying
   temperature in whole numbers, it is calculating its algorithm in tenths or
   hundredths."* `[high]`
2. **Observed switching is sub-degree** — field reports have this platform regulating
   to windows *"as narrow as 0.25 °F"* while the display never leaves the whole number.
   `[medium]`
3. **Official Honeywell statement:** *"Honeywell thermostats round in the display to the
   nearest whole number (half number in Celsius)… the actual temperature did fall to 71
   or up to 73 and that is what turned on the heating or cooling, but the display will
   stay at 72."* The *actual* (fine) temperature drives the equipment; the display is
   deliberately damped to avoid flicker. `[high]`

This also explains the rough-cut's "mechanical zero slope" on `indoor_temp_f` during
AC-on windows: the control holds the *fine* temp near setpoint, so the whole-degree
display simply doesn't move — which is why `indoor_temp_f_hires` was added.

### Caveats so we don't over-claim

- **The fineness is on the *measured* side.** In **°F mode the setpoint is still
  whole-degree** (78, not 78.5); the loop is `error = sensed(0.1 °C) − setpoint(whole
  °F)`. Switch the panel to **°C** and the setpoint also gets 0.5 ° steps. Either way
  the *measured* temperature is resolved to tenths.
- **Quantization is ~0.1 °C (≈0.18 °F), not true 0.1 °F.** The °F-tenths in
  `indoor_temp_f_hires` are a conversion of the 0.1 °C native step, so they arrive in
  ~0.18 °F increments.
- **We control off the RedLINK C7189R remote**, so the relevant value is the remote's
  reading as the thermostat represents it — which telemetry shows arriving at 0.1 °C.
  The C7189R's raw RF payload resolution isn't separately published, but the exposed
  control temperature is unambiguously at tenths-of-°C. `[med-high]`

### Making it first-hand definitive (on-device probes)

1. **Director probe over a cooling cycle** — log `TEMPERATURE_C` each poll and watch it
   step in 0.1 °C increments while `TEMPERATURE_F` holds; correlate compressor on/off
   (`hvac.comfortnet`) with sub-whole-degree setpoint crossings.
2. **Flip the panel to °C for a day** — the home screen shows 0.5 °C steps, visually
   proving sub-1 °F resolution, then flip back.

### Other display/calibration facts
- **Sensor accuracy ≈ ±1.5 °F at 70 °F** (±0.75 °C at 21 °C). `[high]` (Note: this is
  per-sensor; with multiple averaged control sensors the errors partially average
  out.)
- **Temperature display offset / calibration** lets you trim the reading ±3 °F (±1.5 °C)
  to match a reference thermometer; importantly it **shifts both the displayed AND
  the control temperature** — it re-calibrates control, not just cosmetics. `[high
  for the feature/range; the exact CTK04 ISU number is unverified — open item]`
- **Our `indoor_temp_f_hires` provenance is consistent with all this:** the Control4
  Director exposes `TEMPERATURE_C` at 0.1 °C (~0.18 °F), which is the same underlying
  control-sensor temperature exposed at finer resolution than the whole-degree
  `TEMPERATURE_F`. The CTK04's own front-panel whole-degree display is a *display*
  choice, not the resolution limit of the value — matching the
  `THERMAL_ROUGH_CUT_2026-05-26.md` finding.

---

## Humidity & dehumidification

- The CTK04 has a **built-in humidity sensor**; current RH can be shown on the home
  screen (**ISU 9020**). `[high; ±%RH accuracy not obtained — open item]`
- **Dehumidification equipment = ISU 9000**: None / Dehumidifier / **Cooling System**.
  We use the cooling system. `[high]`
- **Overcool-to-dehumidify = ISU 9070**, limit **0–3 °F** (default 3 °F): when
  dehumidifying with the AC, the thermostat will pull indoor temp **up to 3 °F below
  the active cool setpoint** to hit the humidity target. `[high]`
- **Control method = ISU 9080.** **"Basic"** *"should only be used if the equipment
  can lower the fan speed in a call for dehumidification"* — this is the hook into
  ComfortNet's blower-CFM drop. `[high]` Related: **Min On Time (ISU 9090)** and
  **High Humidity Comfort Reset (ISU 9100, up to 5 °F below cool setpoint).** `[high
  on labels; medium that 5 °F is specifically 9100's parameter]`
- **Equipment side (AMVM97):** Goodman/Amana variable-speed ComfortNet units provide
  **enhanced dehumidification** — the ECM **reduces blower CFM during a combined
  cool+dehumidify call** to raise latent removal. Exact CFM-reduction % not obtained.
  `[high mechanism; medium on %]` This is what our `IFC user-menu DEHUM = ON` row in
  `HVAC_LOGIC.md` enables, and what the scheduler's humid-override leans on.

So a high indoor-humidity reading doesn't just toggle a separate output — via the
cooling system it **extends compressor runtime and effectively lowers the operating
temperature below the cool setpoint** (≤ 3 °F via 9070, or ≤ 5 °F via 9100) until the
humidity target is met, with the IFC dropping CFM for latent removal.

---

## CTK04 ISU quick reference

CTK04-native installer-setup codes referenced across our docs, with confidence.
Access: MENU → Installer Options → date-code password → edit (install guide pp. 11–13).

| ISU | Label | Our value | Conf. |
|---|---|---|---|
| 3000 | Auto Changeover Deadband | 3–5 °F (5 °F effective w/ dehum+Auto) | high |
| 3020 | Finish With High Cool Stage | OFF (finish low) | high |
| 3030 | Staging Control – Cool Differentials | default (~2 °F to call stage 2) | med-high |
| 3140 | Cool / Compressor Cycles Per Hour | default (~3) | high label / med value |
| 3240 | Minimum Compressor Off Time | 5 min | high label / med value |
| 4090 | **Adaptive Intelligent Recovery** | **ON (Arm A) / OFF (Arm B)** | high |
| 5040 | **Temperature-control sensor selection / averaging** | RedLINK indoor as control sensor | high |
| 9000 | Dehumidification Equipment | Cooling System | high |
| 9020 | Humidity shown on home screen | ON | high |
| 9070 | Dehumidify Overcool Limit (0–3 °F) | 3 °F | high |
| 9080 | Dehumidification Control Method | Basic (equipment drops CFM) | high |
| 9090 | Dehumidify Minimum On Time | — | high (label) |
| 9100 | High Humidity Comfort Reset (≤ 5 °F) | — | high (label) |

> The `1054 / 1056 / 1059` outdoor-equipment-type codes cited in `HVAC_LOGIC.md`
> could **not** be confirmed against CTK04 sources in this pass (search returned no
> CTK04 binding for those exact numbers; the CTK04 emphasizes auto-detection of
> communicating equipment, and uses an `8050`-series for the outdoor sensor). Treat
> those specific `10xx` numbers as **unverified for the CTK04** — open item.

---

## How this maps onto our stack

- **`indoor_temp_f` IS the control-sensor temperature.** Because the RedLINK indoor
  sensor is the assigned control sensor (built-in excluded), the value the CTK04
  reports to TCC → Control4 → our poller is the temperature the setpoint comparison
  uses. Our "RedLINK primary BR = control sensor" framing in
  `THERMAL_ROUGH_CUT_2026-05-26.md` is correct and now has a documented mechanism
  (ISU 5040, averaging of "control" sensors). `[high]`
- **Relocating the control sensor relocates the control loop.** The 2026-05-26 move
  (primary BR → 2F hallway) changed *the* temperature the thermostat regulates and
  that we log — not merely a logging sensor. Treat it as a control-loop change, not a
  telemetry tweak.
- **Whole-degree quantization** in `indoor_temp_f` is a *display* artifact of the
  CTK04 front panel, not a sensing limit; `indoor_temp_f_hires` (from `TEMPERATURE_C`)
  exposes the finer underlying value — consistent with the rough-cut's degenerate
  AC-on slope finding.
- **AIR ISU 4090 is the one thermostat-side experiment lever** (Arm A ON / Arm B OFF);
  everything else on the thermostat is held constant across arms.
- **ComfortNet (CT-485) sniffing is necessary** precisely because communicating-mode
  staging/modulation lives in the equipment, not the thermostat.

---

## Repo references to correct

The following call the device a **VisionPRO 8000**, which is inaccurate — it's a
Prestige-IAQ-class ComfortNet thermostat. Recommend correcting the wording (CTK04 /
"Prestige-IAQ-class RedLINK 2.0 platform"):

- `deploy/energy-stack/thermostat_poller/poller.py:1` — module docstring
  *"Honeywell VisionPRO 8000 → InfluxDB poller."*
- `deploy/energy-stack/hvac_scheduler/app.py:135` — comment referencing
  *"VisionPRO 8000."*
- `deploy/energy-stack/.env.example:79` — *"VisionPRO state."*
- `docs/THERMOSTAT_ARM_A_SCHEDULE.md:63` — *"the VisionPRO 8000 firmware behind the
  CTK04AE OEM relabel."* **⚠ This file is `status: locked` / OSF-freeze** — changing
  it post-OSF would be a protocol deviation; correct only via the amendment
  procedure, or before the OSF filing.
- `docs/HVAC_LOGIC.md:39` and `docs/PROJECT.md` — "Honeywell-OEM whitelabel"
  phrasing is fine in spirit but should name **Prestige IAQ**, not VisionPRO, where a
  platform is named.
- `docs/archive/README-LEGACY.md` — archive; lower priority.

These are **descriptive labels, not behavioral parameters**, so correcting the
non-frozen ones is harmless to the experiment. The frozen `THERMOSTAT_ARM_A_SCHEDULE.md`
line needs the amendment path. *(Left unedited pending your go-ahead given the
OSF-freeze sensitivity — say the word and I'll fix the safe ones.)*

---

## Open / unverified items

Need a direct read of the CTK04 install guide (69-2688) and/or Prestige IAQ 69-2490
to reach high confidence:

1. **Whether a SINGLE remote fully excludes the built-in sensor from control.**
   Contradictory across sources (menu seems to allow it; field reports say the
   internal only drops at ≥ 2 remotes). Directly affects whether our `indoor_temp_f`
   is the pure RedLINK reading or `(remote + wall)/2`. **Verifiable on our own unit
   via ISU 5040** — highest-value thing to confirm.
2. **Control-fallback on wireless-sensor RF loss / dead battery** — alert is
   documented; control behavior is not. May *withhold operation* (sensor fault /
   System-Off) rather than revert to internal. *(Operationally important — a dead
   C7189R battery could move the control temperature or stall the system.)*
2. **Exact CTK04 ISU number for temperature display offset/calibration** (range ±3 °F
   confirmed; CTK04 code not pinned).
3. **ISU 3030 cool-stage differential default degrees**, and the **5-min** min-off-time
   default — labels confirmed, exact numbers not quoted.
4. **Which thermostat staging ISUs are overridden in communicating (CT-485) mode** vs
   legacy — the single biggest behavioral gap.
5. **`1054 / 1056 / 1059` outdoor-equipment-type ISU numbers** — unconfirmed for the
   CTK04 (likely a different/auto-detected scheme).
6. **Built-in humidity sensor accuracy (±%RH)** and the **AMVM97 dehum CFM-reduction %.**
7. Whether the **CTK04 exposes a "Reheat"** dehum option (documented on the Prestige
   platform; not separately confirmed for CTK04).

PDF fetch was blocked in-environment; resolving these needs a direct download of
69-2688 / 69-2490 (or an authenticated fetch) — straightforward as a follow-up.

---

## Sources

Primary (authoritative):

- ComfortNet **CTK04** System Installation Guide — Honeywell **69-2688** /
  I/O-CHTSTAT03: `https://documents.alpinehomeair.com/product/IO-CHTSTAT03-4D.pdf`;
  mirror `https://literature.neuco.com/CTK04.pdf`;
  `https://cdn.daikincloud.io/PIM/Assets/Documents/ctk04.pdf`
- CTK04 install guide (HTML): thermostat.guide —
  `https://thermostat.guide/comfortnet/comfortnet-ctk04-communicating-thermostat-system-installation-guide/`
- CTK04 manual on ManualsLib (ISU p.12, Programmed Recovery p.16, dehum p.19):
  `https://www.manualslib.com/manual/884071/Comfortnet-Ctk04.html`
- Goodman ComfortNet CTK04 training:
  `https://apps.goodmanmfg.com/training/files/55bbe87147fcbComfortNet%20CTK04%20IC-CNR-1506-PP-01.pdf`
- **Prestige IAQ / 2.0 (THX9321/THX9421) with EIM** — Honeywell **69-2490** (platform
  cross-reference, shared ISU scheme):
  `https://customer.resideo.com/resources/Techlit/TechLitDocuments/69-0000s/69-2490.pdf`;
  user manual (Remote Indoor Sensors, p.130):
  `https://www.manualsdir.com/manuals/91472/honeywell-prestige-thx9321-prestige-thx9421.html?page=130`

Accessories / RF:

- C7189R1004 wireless indoor sensor —
  `https://www.supplyhouse.com/Honeywell-Home-C7189R1004-RedLINK-Enabled-Wireless-Indoor-Air-Sensor-Wireless`
- THM5421R EIM ("for all Prestige IAQ and new-RedLINK VisionPRO") —
  `https://www.supplyhouse.com/Honeywell-Home-THM5421R1021-Equipment-Interface-Module-EIM-for-all-Prestige-IAQ-and-new-RedLINK-VisionPRO-Thermostats`
- THM6000R RedLINK Internet Gateway (900 MHz / 902–928) —
  `https://www.southernpipe.com/7346239/Product/Honeywell%20Thermostats%20and%20HVAC%20Parts_THM6000R7001/U`

Equipment (communicating staging / dehum):

- Goodman ComfortNet ASXC16 service manual:
  `https://www.manualslib.com/manual/1407157/Goodman-Comfortnet-Asxc160-1aa-Series.html`
- Goodman GMVM97/AMVM97 service (enhanced dehumidification, variable-speed CFM):
  `https://hvacdirect.com/media/hvac/pdf/GMVM97-Service.pdf`

Control model / CPH (platform corroboration):

- Honeywell cycle-rate (CPH) documentation: `https://www.honeywellmanual.com/pdf/8000.pdf`
  (used only for the CPH *model*; ISU numbers above are CTK04/Prestige, not this doc's)
