---
date: 2026-05-21
owner: chris
status: active
role-label: chris
---

# Decision-trace commissioning report — 2026-05-19

> This file is the **style exemplar** for the daily decision-trace report.
> The scheduled task at `~/.claude/scheduled-tasks/decision-trace-report/SKILL.md`
> points here to learn the tone, section ordering, level of detail, and how
> to render reason codes in English. Treat it as the reference shape, not as
> data — every actual run produces fresh content for yesterday.

## Executive summary

May 19 was a NORMAL day (high 77°F, apparent max 77°F, max dewpoint 65–68°F, no heat advisory). The scheduler ran in shadow mode throughout the 24-hour window. The afternoon saw a sustained price event between roughly 1:40 PM and 3:35 PM CT, during which the price overlay cycled through elevated and scarcity tiers; the controller responded correctly to each transition. All major action labels fired as expected (PRE_COOL, PRICE_AWARE_PRECOOL, COAST, MID_PERIOD_REPUSH, RECOVER, SLEEP). Feed health is generally good with one exception noted below.

---

## What needs attention

> Lead each item with an explicit **start–end CT** time window so a reader can
> judge staleness without reading the timeline. Show an end time only when
> normal scheduler activity resumed after it; if the issue was still active at
> report close, or the stream simply went silent, write **"ongoing"** (add
> **"— verify"** for silence) instead of an end time.

- **06:01 CT (single tick), ongoing — verify — the `haven.indoor` feed had no reading.** The scheduler ran one tick at 6:01 AM CT without an indoor temperature reading; the reading was present for every other tick, so the gap was brief. Feed state at report close is unconfirmed — verify `haven.indoor` is writing now if it is expected to be active.

- **14:30–14:45 CT — audit rows for the afternoon price spikes show no price-overlay transitions in the forward audit window.** The trace stream confirms the controller was already in the scarcity tier during this window (holding, not transitioning), so the absence of audit rows is consistent with no tier change — not a missed response. Bounded and benign; noted for completeness.

---

## Timeline

**~2:00 AM CT** — The overnight day-type decision was recorded: NORMAL, high 78°F, day-type reason "high 75 to 84." ComEd price at decision time was 4.7¢/kWh.

**6:00 AM CT** — First tick of the target window. Layer resolution: schedule won, cool setpoint 70°F (PRE_COOL action). One tick at 6:01 AM ran without an indoor temperature reading; all subsequent ticks had the reading available.

**8:00 AM CT** — Schedule transition to 67°F (PRICE_AWARE_PRECOOL action fired at 8:00 AM CT per the hvac.actions audit row at 13:00:08Z). The precool window was set with hour 8 and depth 67°F. No precool decision_trace events are present in the fact packet, but the hvac.actions audit confirms the action fired.

**1:00 PM CT** — Schedule transition to 79°F (COAST action). Layer resolution: schedule won, price overlay tier normal.

**1:40–1:48 PM CT** — First price spike of the afternoon: 17.5¢/kWh at 1:40 PM CT. Decision traces from 1:35–1:44 PM show the price overlay staying in the normal tier (price below trigger). At 1:48 PM CT the hvac.price_overlay audit records a transition from normal to elevated (current price 17.5¢, lag ~486 seconds). The corresponding layer-resolution trace at 1:48 PM confirms the price overlay tier moved to elevated, the price overlay won layer resolution, and the effective cool setpoint became 82°F. The 8-minute gap between the 1:40 PM spike and the 1:48 PM audit row is consistent with ComEd publish delay, poller cycle, and scheduler-tick latency.

**1:45 PM CT** — A second price sample at 12.7¢/kWh is present. The same 1:48 PM audit row covers this spike (lag ~186 seconds from the 1:45 PM sample). The trace at 1:48 PM shows the price overlay upgrading to elevated.

**1:48 PM – 2:18 PM CT** — Price overlay held in the elevated tier. Traces confirm the price overlay held in tier continuously across this window.

**2:15–2:18 PM CT** — Price sample at 14.8¢/kWh. The audit row at 2:18 PM records a transition from elevated to normal (current price 2.5¢, lag ~189 seconds), consistent with prices briefly dropping. The trace at 2:18 PM confirms the price overlay released to normal.

**2:20–2:27 PM CT** — Price spikes to 30.1¢/kWh at 2:20 PM. Traces show the price overlay upgrading from normal to elevated at 2:23 PM, then from elevated to scarcity at 2:27 PM. Audit rows confirm: normal to elevated at 2:23 PM (lag ~183 s), elevated to scarcity at 2:27 PM (lag ~426 s). Layer resolution at 2:27 PM: price overlay tier scarcity, effective cool setpoint 85°F.

**2:25–2:45 PM CT** — Multiple price samples (18.8¢, 15¢, 26¢, 18.4¢, 20.8¢). Controller remained in the scarcity tier throughout, holding. No tier transitions occurred; no audit rows for these spikes are expected or present. Traces confirm the price overlay held in tier continuously.

**2:45–2:58 PM CT** — Price sample at 20.8¢/kWh at 2:45 PM. The audit row at 2:58 PM records a transition from scarcity to normal (current price 4.8¢, lag ~790 seconds). The longer lag here is consistent with prices needing to fall and hold below threshold before release. The trace at 2:58 PM confirms the schedule won layer resolution again with the price overlay tier back to normal and effective cool setpoint 79°F.

**3:10–3:18 PM CT** — Price sample at 15.6¢/kWh at 3:10 PM. Traces show the price overlay staying in the normal tier through 3:17 PM, then the audit row at 3:18 PM records normal to elevated (lag ~485 s). The trace at 3:18 PM confirms the price overlay upgrading to elevated.

**3:20–3:35 PM CT** — Price samples at 15.4¢ and 17.1¢. Controller held in the elevated tier. No further audit transitions recorded for these spikes; the controller was already elevated and held. Traces confirm the price overlay held in tier.

**7:00 PM CT** — Schedule transition to 75°F (RECOVER action, audit row at 00:00:07Z). Layer resolution: schedule won.

**9:00 PM CT** — Schedule transition to 73°F (SLEEP action, audit row at 02:00:01Z). Layer resolution: schedule won.

**Remainder of night** — Controller ran on schedule at 73°F cool setpoint, price overlay normal, safety supervisor approving every tick. Indoor temperature readings of 74–75°F throughout.

---

## Feed health now

| Feed                | Kind         | Status                                                       |
| ------------------- | ------------ | ------------------------------------------------------------ |
| `comed.prices`      | continuous   | ✅ Last write 12:50 PM CT — current                           |
| `nws.forecast`      | continuous   | ✅ Last write 12:48 PM CT — current                           |
| `pjm.lmp_da_hourly` | event        | ✅ Last write 1:00 PM CT — within expected daily cadence      |
| `pjm.inst_load`     | continuous   | ✅ Last write 1:00 PM CT — current                            |
| `pjm.metered_load`  | event_lagged | ✅ Latest published settlement data through 2026-05-19 03:00 UTC — consistent with ~2-day settlement lag |
| `refoss.channel`    | continuous   | ✅ Last write 12:59 PM CT — current                           |
| `hvac.thermostat`   | continuous   | ✅ Last write 12:59 PM CT — current                           |
| `haven.indoor`      | continuous   | ⚠️ No data — needs investigation                              |

---

## Scheduler activity

The scheduler operated normally for 1,440 ticks today (one per minute, full day coverage). Notable departures from baseline behavior across the day:

- Price overlay won layer resolution 201 times (during elevated and scarcity tier holds)
- Price overlay held in elevated or scarcity tier 291 times
- Price overlay upgraded to elevated 8 times
- Price overlay released to normal 9 times
- Price overlay upgraded to scarcity 3 times
- Pre-cool selected for tomorrow once; pre-cool rejected once during the week (no spike window found after the required gap)

No emergency safety overrides fired. No supervisor clamping fired. No 5CP layer activity.

---

## Equipment behavior

> Include this section only when something notable was flagged. Omit entirely
> on unremarkable days. Example content below — replace with actual findings.

**Stage 2 extended run — 10:05 PM to 1:50 AM CT (3h 45m)**

The compressor ran at Stage 2 (100% capacity) from ~10:05 PM CT through 1:50 AM CT. Indoor temperature reached the 73°F cool setpoint around 11:00 PM CT; Stage 2 continued for roughly 2.75 hours after setpoint was achieved, with outdoor temperature falling from 67°F to 62°F during that window. At that outdoor delta (73°F inside vs 62°F outside), Stage 1 or normal cycling should be sufficient to hold setpoint.

Most likely cause: PID integral accumulation from the earlier Return-period pulldown. The CTK04 uses a PID controller (not a simple deadband), and integral buildup during the 7–10 PM work period can hold the output at full capacity well past setpoint. Watch the next several nights for recurrence — a single event is consistent with normal PID behavior at cooling-season start; a recurring pattern on mild nights warrants a service check.

---

## Appendix

**Note on the 2:15 PM spike and the audit row sequence:** The 2:15 PM spike entry shows three audit rows in its forward window — a normal-to-elevated at 2:23 PM, an elevated-to-normal at 2:18 PM, and an elevated-to-scarcity at 2:27 PM. The 2:18 PM row predates the 2:23 PM row in wall-clock time; this is because the forward window for the 2:15 PM spike starts at 2:15 PM and captures all rows through 2:30 PM. The sequence is: elevated to normal at 2:18 PM (brief price drop to 2.5¢), then normal to elevated at 2:23 PM (price back up to 14.8¢), then elevated to scarcity at 2:27 PM (price at 30.1¢). The trace stream corroborates this ordering exactly.
