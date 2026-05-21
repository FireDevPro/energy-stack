---
name: pre-osf-doc-audit-truth-tables-2026-05-18
date: 2026-05-18
owner: chris
status: active
role-label: audit-evidence
related:
  - pre-osf-doc-audit-execution-2026-05-18.md
  - pre-osf-doc-audit-findings-2026-05-18.md
---

# D5 Truth-Table Verification — 4 Superseded Docs

Verification pass against current code, math artifacts, and binding spec for every retained-content claim in the four half-superseded docs. Provides the evidence basis for D5's per-doc actions.

**Ground-truth hierarchy (per operator-locked protocol):**
1. Current code (`deploy/energy-stack/`, `tools/analysis/`, `deploy/energy-stack/pjm_dm2_poller/`)
2. Current math / analysis artifacts (`tools/comed_2025_analysis/`, `tools/o2_capacity_reconstruction/`)
3. Binding spec or recorded decision (`docs/plans/sced-rebaseline-spec-2026-05-13.md`)
4. Operator judgment (for ethics / procedural prose)

Other prose docs are NOT used as proof unless they're the recorded decision source.

**Classifications:**
- `matches-truth` — safe to keep, extract, or cite
- `drift` — discard or correct elsewhere
- `unique-unverified` — needs operator decision
- `historical-only` — archive
- `chris-judgment-required` — operator-judgment item

## Summary

Retained content holds up well for the locked HVAC-controller numerics (day-type thresholds, RTP triggers/hysteresis/setpoints, layer-priority formula, 5CP load-ratio/window/fallbacks, pre-cool deepen rule). EXPERIMENT_DESIGN Appendix A matches code essentially verbatim, including the `20,375 / 151,525 MW` dual-scope fallbacks that fixed the 130,000 MW RTO-scale bug. EXPERIMENT_DESIGN §O2 also matches the current `tools/o2_capacity_reconstruction/tariff_snapshot.md` (already on the named-scenarios framing with 2,033.653 MW disclosed).

The two areas with material drift: **ANALYSIS_PIPELINE.md §2.1 measurements table** has multiple wrong cadences, one wrong field name, an `ecowitt.weather` outdoor-field listing that conflicts with the binding spec's §6 retraction in favor of `ch1_*`, and a fabricated `hvac.thermostat.running` field that no service writes. **ARM_B_IMPLEMENTATION.md** carries known stale items (130 GW fallback at line 466, MILD<82°F / NORMAL 82-94°F / HOT≥95°F "current" thresholds at lines 122-125 that describe a pre-recalibration state, §10 acceptance criteria duplicating the now-retired OSF_FILING.md list) — but its operational-content sections are otherwise consistent with the deployed code.

OSF_FILING.md pre-flight checklist is almost entirely superseded by spec §11 and references retired artifacts (`randomize_arms.py`, the 20260601 seed, the assignments CSV); only the OSF-platform mechanics in §3-§5 are still operative, and those are operator-judgment / platform-UI items, not code-verifiable.

---

## Doc 1: EXPERIMENT_DESIGN.md

### Truth table — Appendix A + §O2 + §11

| Claim | Location | Ground-truth source | Verified value | Classification |
|---|---|---|---|---|
| Day-type MILD <75°F | EXPERIMENT_DESIGN.md:543 | `hvac_scheduler/app.py:877` `NORMAL_TEMP_THRESHOLD_F = 75`, app.py:913 fallback to MILD when `high_f < 75` | 75°F | matches-truth |
| Day-type NORMAL 75-85°F | EXPERIMENT_DESIGN.md:543 | app.py:875,897,901 | matches | matches-truth |
| Day-type HOT ≥85°F OR apparent ≥90°F OR heat advisory | EXPERIMENT_DESIGN.md:544 | app.py:875-876 `HOT_TEMP_THRESHOLD_F=85`, `HOT_APPARENT_THRESHOLD_F=90`; app.py:895-900 | matches (heat advisory also a HOT trigger) | matches-truth |
| HOT_STREAK_DAY1 trigger (HOT today + HOT tomorrow) | EXPERIMENT_DESIGN.md:545 | app.py:1060-1075 `decide_day_type` multi-day path; ALSO single-day §7 path via `should_deepen_precool` (app.py:1083) | matches; single-day forecast-5CP path documented later in Appendix A | matches-truth |
| Elevated trigger 10¢/kWh | EXPERIMENT_DESIGN.md:555 | `price_overlay.py:77` `trigger_price_cents_per_kwh=10.0` | 10.0 | matches-truth |
| Elevated release 8¢/kWh | EXPERIMENT_DESIGN.md:556 | `price_overlay.py:78` `release_price_cents_per_kwh=8.0` | 8.0 | matches-truth |
| Scarcity trigger 20¢/kWh | EXPERIMENT_DESIGN.md:557 | `price_overlay.py:69` | 20.0 | matches-truth |
| Scarcity release 18¢/kWh | EXPERIMENT_DESIGN.md:558 | `price_overlay.py:70` | 18.0 | matches-truth |
| Minimum hold 30 min | EXPERIMENT_DESIGN.md:559 | `price_overlay.py:87` `DEFAULT_MINIMUM_HOLD_MINUTES = 30` | 30 | matches-truth |
| Elevated +3°F offset | EXPERIMENT_DESIGN.md:560 | `price_overlay.py:79` `cool_setpoint_offset_f=3` | 3 | matches-truth |
| Scarcity = 85°F effective setpoint | EXPERIMENT_DESIGN.md:561 | `price_overlay.py:72` `cool_setpoint_override_f=85`; `app.py:1151` `COOL_SHUTOFF_F=85` | 85 | matches-truth |
| 5CP load-ratio trigger 0.95, release 0.90 | EXPERIMENT_DESIGN.md:578 | `pjm_5cp.py:74-75` `LOAD_RATIO_TRIGGER = 0.95`, `LOAD_RATIO_RELEASE = 0.90` | matches | matches-truth |
| 5CP window 13:00-20:00 CT | EXPERIMENT_DESIGN.md:579 | `pjm_5cp.py:76-77` | matches | matches-truth |
| 5CP summer eligibility gate Jun 1 - Sep 30 | EXPERIMENT_DESIGN.md:580 | `pjm_5cp.py:164-165, 168-174` | matches | matches-truth |
| 5CP hold = end-of-hour + 30 min per scope | EXPERIMENT_DESIGN.md:581 | `pjm_5cp.py:79` `HOLD_TAIL_MINUTES = 30`; `pjm_5cp.py:266-275` `hold_end_time` | matches | matches-truth |
| ComEd-zone pre-season fallback 20,375 MW | EXPERIMENT_DESIGN.md:582 | `pjm_5cp.py:97` `COMED_PRE_SEASON_FALLBACK_5TH_MW = 20375.0` | matches | matches-truth |
| RTO pre-season fallback 151,525 MW | EXPERIMENT_DESIGN.md:583 | `pjm_5cp.py:105` `RTO_PRE_SEASON_FALLBACK_5TH_MW = 151525.0` | matches | matches-truth |
| Pre-cool deepen: tomorrow_peak > season_5th × 1.05 AND high ≥90°F | EXPERIMENT_DESIGN.md:584 | `precool.py:35-36` `DEEPEN_PEAK_RATIO=1.05`, `DEEPEN_TEMP_THRESHOLD_F=90`; `precool.py:96-115` `should_deepen_precool` | matches | matches-truth |
| Pre-cool deepen action: 03:00 start at 66°F (vs default 04:00 at 68°F) | EXPERIMENT_DESIGN.md:585 | `app.py:213` `ScheduleAction(4, 0, "HOT_PRE_COOL", cool_setpoint_f=68)`; `app.py:234` `ScheduleAction(3, 0, "STREAK_PRE_COOL_EARLY", cool_setpoint_f=66)` | matches | matches-truth |
| Layer priority formula `max(schedule + humid_override, schedule + price_overlay, 5cp_shutoff_setpoint)` then clamp 65-86 | EXPERIMENT_DESIGN.md:590-594 | `app.py:1200-1206` `resolve_layer_priority`; `safety_supervisor.py:52-53` `SAFE_COOL_MIN_F=65, SAFE_COOL_MAX_F=86` | Code computes humid_override inside `resolve_cool_setpoint` (app.py:1226-1232) BEFORE entering layer priority; formula is equivalent | matches-truth |
| §O2 layer 1/2/3 framing, named scenarios 1500/2033.653/3000 MW with FERC ER22-1520-001 citation | EXPERIMENT_DESIGN.md:263-295 | `tools/o2_capacity_reconstruction/tariff_snapshot.md:5,76-110` | EXPERIMENT_DESIGN.md:280 cites 2,033.653 MW and ER22-1520-001 Exhibits 1(b)/2(b)(i)/2(b)(ii); §282-290 named scenarios match snapshot §101-109 exactly | matches-truth (already on the new framing, NOT pre-rebaseline) |
| Bootstrap CI for O2 Layer 1 across qualifying-weeks distribution | EXPERIMENT_DESIGN.md:297 | Binding spec §9.5 retires bootstrap CI in favor of per-pair descriptive | weekly-bootstrap framing is the retired piece | drift (carve out at extraction) |
| §11 ethics framing (building-as-subject, COPE proportionality, companion-animal welfare, FWA-bound 45 CFR 46 reasoning) | EXPERIMENT_DESIGN.md:436-465 | Not code-verifiable; operator/philosophy text | n/a | chris-judgment-required — **APPROVED 2026-05-18** |

### Doc-action recommendation: EXPERIMENT_DESIGN.md

**Verdict: high-value retained content. Targeted extraction, do not archive whole.**

- **Appendix A (lines 528-617):** 17/17 quantitative claims match code (matches-truth on every threshold, fallback, window, formula). Cleanest piece of retained content in the four-doc set. **Recommendation: extract Appendix A wholesale into new `docs/CONTROLLER_CONSTANTS.md`** (or spec annex). No factual drift to clean up. Cite this single page from HVAC_LOGIC.md instead of leaving Appendix A buried at the bottom of a superseded doc.

- **§O2 (lines 263-299):** Already on the named-scenarios framing matching `tariff_snapshot.md`. Does NOT predate the rebaseline. **Recommendation: extract §O2 verbatim** into successor doc `docs/O2_CAPACITY_RECONSTRUCTION.md` co-located with `tools/o2_capacity_reconstruction/`. Strip line 297 bootstrap-CI reference at extraction.

- **§11 ethics framing (lines 436-465):** operator-judgment content (building-as-subject framing, 45 CFR 46 reasoning, COPE proportionality, AVMA companion-animal welfare, self-experimentation precedent). Survives the spec rebaseline because spec §11 covers operational pre-OSF deliverables, NOT philosophical framing. Chris approved 2026-05-18. **Recommendation: extract §11 into `docs/ETHICS_FRAMING.md`.**

- **Remainder:** Archive.

---

## Doc 2: ANALYSIS_PIPELINE.md §2.1 measurements table

| Claim | Location | Ground-truth source | Verified value | Classification |
|---|---|---|---|---|
| `comed.prices` fields = `price_cents` | ANALYSIS_PIPELINE.md:60 | `comed_poller/poller.py:128,137` writes `price_cents_per_kwh` (NOT `price_cents`) | actual field: `price_cents_per_kwh` | drift |
| `comed.prices` cadence "5-min" | ANALYSIS_PIPELINE.md:60 | `comed_poller/poller.py:71` `COMED_POLL_INTERVAL` default 60s — poller runs every 60s, writes 5-min print + hourly_avg | poller cadence 60s; source data is 5-min + hourly_avg; rows arrive ~1/min | drift (conflates source resolution with poll cadence) |
| `comed.prices` missing tag `period_type` ("5min" / "hourly_avg") | ANALYSIS_PIPELINE.md:60 | `comed_poller/poller.py:11,127,136` — `period_type` IS a tag | tag exists, table omits it | drift (omission) |
| `refoss.channel` cadence "1-min" | ANALYSIS_PIPELINE.md:61 | `refoss_poller/poller.py:27,93` `REFOSS_POLL_INTERVAL` default 30 (seconds) | cadence is 30s, not 1-min | drift |
| `refoss.channel` per-channel tag `channel` `em:1..em:9` | ANALYSIS_PIPELINE.md:61 | `refoss_poller/poller.py:5,16` "EM16P exposes 18 `em:N` channel entries"; poller.py:235-241 iterates ALL `em:` keys | tags em:1..em:18, NOT em:1..em:9 only | drift (analysis subsets to em:2/em:8/em:9 for HVAC; em:1+em:7 for mains; measurement contains all channels) |
| `eagle.meter` fields, tags, ~30-sec cadence | ANALYSIS_PIPELINE.md:63 | `eagle_poller/poller.py:85` `EAGLE_POLL_INTERVAL` default 30; poller.py:188-191 Point("eagle.meter").tag("hw_address").tag("source", "eagle3"); poller.py:48-50 fields `demand_kw`, `delivered_kwh`, `received_kwh` | matches | matches-truth |
| `hvac.thermostat` fields = `indoor_temp_f, cool_setpoint_f, heat_setpoint_f, running, hvac_mode`, cadence 1-min | ANALYSIS_PIPELINE.md:64 | `thermostat-poller/poller.py:6-9` fields written are `indoor_temp_f, humidity_pct, cool_setpoint_f, heat_setpoint_f, hvac_mode, hvac_state, fan_mode, hold_mode`; poller.py:98 `THERMOSTAT_POLL_INTERVAL` default 600s (10 min); `running` is NEVER written | **`running` field does NOT exist; cadence is 10-min not 1-min; missing `humidity_pct, hvac_state, fan_mode, hold_mode`** | drift (multi-fault: fabricated field name, wrong cadence) |
| `hvac.comfortnet` from `thermostat-poller (CT-485 ingestion)` with fields `cool_actual_pct, heat_actual_pct, blower_cfm`, 1-min | ANALYSIS_PIPELINE.md:65 | grep across `deploy/energy-stack/` for "hvac.comfortnet" returns ONLY a reference in `telegram-notifier/app.py:358` (alert mapping); NO writer service exists | **`hvac.comfortnet` is NOT written by any production service today; table claims a CT-485 path that doesn't exist** | drift (measurement not produced) |
| `hvac.overrides` written by thermostat-poller AND tools/log_override.py | ANALYSIS_PIPELINE.md:66 | `thermostat-poller/poller.py:299` `Point("hvac.overrides")`; `tools/log_override.py:113` same | both writers exist | matches-truth |
| `hvac.actions` fields | ANALYSIS_PIPELINE.md:67 | `hvac_scheduler/app.py:1721-1752` writes 14+ fields + 6+ tags; doc lists only `action, arm, cool_setpoint_f, source, override_category, applied, dry_run` — `arm`, `source`, `override_category` are NOT written | drift (doc lists tags/fields not present; omits many that ARE present) |
| `hvac.5cp_state` cadence "~2.5-min (matches PJM inst_load cadence)" | ANALYSIS_PIPELINE.md:69 | `hvac_scheduler/app.py:2317` `_FIVECP_AUDIT_INTERVAL = timedelta(minutes=5)`; comments at 1355-1358 and 2356-2358 state ~5-min cadence (288 rows/day per scope) | actual cadence: 5-min, not 2.5-min | drift |
| `hvac.price_overlay` cadence "~30-min (price-overlay evaluation cadence)" | ANALYSIS_PIPELINE.md:70 | `hvac_scheduler/app.py:1683-1685` `write_price_overlay_transition` writes "ONLY on tier transitions"; app.py:2355 docstring confirms "Writes hvac.price_overlay on tier transitions only" | cadence: event-on-transition, NOT ~30-min | drift |
| `hvac.precool_window` event cadence | ANALYSIS_PIPELINE.md:71 | `app.py:1519` writes Point("hvac.precool_window") at the precool-decision boundary | event-driven matches | matches-truth |
| `hvac.arm_transitions` from scripts/log_arm_transition.py | ANALYSIS_PIPELINE.md:72 | `deploy/energy-stack/scripts/log_arm_transition.py:63` writes Point("hvac.arm_transitions") | matches | matches-truth |
| `nws.forecast` fields including `apparent_max_f`, `rh_max_pct`, `sky_cover_avg_pct`, `wind_gust_max_mph` | ANALYSIS_PIPELINE.md:73 | `nws_poller/app.py:379-387` writes Point("nws.forecast") with field-loop over computed daily summary | likely matches (migration to forecastGridData per ARM_B §0a) | unique-unverified |
| `ecowitt.weather` fields = `outdoor_temp_f, outdoor_dewpoint_f, outdoor_rh_pct, wind_mph, solar_wm2, pressure_inhg`, 5-min | ANALYSIS_PIPELINE.md:75 | `ecowitt-ingest/app.py:242-258` writes EXACTLY `outdoor_temp_f, outdoor_rh_pct, outdoor_dewpoint_f, ws90_temp_f, ws90_rh_pct, ws90_dewpoint_f, wind_mph, solar_wm2, pressure_inhg, indoor_temp_f, indoor_rh_pct, ch{N}_temp_f, ch{N}_rh_pct, ch{N}_dewpoint_f` for paired channels | Code DOES write `outdoor_*` (when shaded WN31 channel configured); ALSO writes `ch{N}_*` per channel. Binding spec §6 canonicalizes `ch1_*` for analysis (per OI-1 retraction at commit 4ad147e) | drift (doc lists outdoor_* as analysis source; spec §6 prefers ch1_*) |
| `pjm.inst_load` cadence "~5-min" | ANALYSIS_PIPELINE.md:76 | `pjm_dm2_poller/app.py:200,205,206` FEED_SCHEDULE `inst_load` and `inst_load_rto` every 5-min | 5-min | matches-truth |
| `pjm.metered_load` cadence "hourly" | ANALYSIS_PIPELINE.md:77 | `pjm_dm2_poller/app.py:203-204` `hrl_load_metered` + `hrl_load_metered_rto`: `Schedule(hours=tuple(range(0, 24)))` | hourly | matches-truth |
| `pjm.peak_forecast_rto` cadence "daily 06:00 / 13:00" | ANALYSIS_PIPELINE.md:78 | `pjm_dm2_poller/app.py:207` `Schedule(hours=(6, 13), months=(6,7,8,9))` | 06:00 + 13:00, cooling-season only | drift (table omits cooling-season-only restriction) |
| `pjm.lmp_da_hourly` cadence "daily 17:00" | ANALYSIS_PIPELINE.md:79 | `pjm_dm2_poller/app.py:201` `da_hrl_lmps: Schedule(hours=(17,))` | matches | matches-truth |
| `pjm.poller_heartbeat` "per minute" | ANALYSIS_PIPELINE.md:83 | `pjm_dm2_poller/app.py:902` writes Point("pjm.poller_heartbeat") in main loop tick (~every 5 min, not per-minute) | likely 5-min not 1-min | unique-unverified |

### Doc-action recommendation: ANALYSIS_PIPELINE.md

**Verdict: §2.1 needs material correction before extraction; rest is dispositioned by banner.**

The measurements table has 8 verified drift entries (wrong field name, fabricated `running` field, wrong cadence on 5 measurements, ecowitt outdoor-vs-ch1 spec mismatch, hvac.actions tag/field mismatch, hvac.comfortnet measurement that no service writes). Don't extract to SERVICES.md until corrected.

**Recommendation: do NOT extract §2.1 as-is.** Rewrite §2.1 against current code as part of `docs/SERVICES.md` (preferred — SERVICES.md is the natural home for a measurements catalog and is already-cited per AGENTS.md), correcting each drift entry, and archive the rest of ANALYSIS_PIPELINE.md.

8 drift / 4 matches-truth / 2 unique-unverified across the verified rows → the table is more wrong than right on cadences/fields.

---

## Doc 3: OSF_FILING.md (post-banner content)

| Claim | Location | Ground-truth source | Verified value | Classification |
|---|---|---|---|---|
| Production-stack gate #1: per-service pytest loop | OSF_FILING.md:26-37 | matches AGENTS.md "Tests" guidance | matches operational reality | matches-truth (but superseded by spec §11 process-list framing) |
| Production-stack gate #2: replay test files | OSF_FILING.md:38-43 | `deploy/energy-stack/hvac_scheduler/test_integration_2025_replay.py` + `test_pjm_5cp.py` both exist | files exist | matches-truth |
| Production-stack gate #5: new measurements `hvac.price_overlay, hvac.5cp_state, hvac.arm_transitions` | OSF_FILING.md:48-53 | all three measurements ARE written by current code | exists | matches-truth (but does not include spec-§11 mandated `hvac.arm_mode, hvac.switch_event, hvac.input_feed_health, controller_alive`) |
| Production-stack gate #7: "Assignment CSV regenerated with the locked seed" + `randomize_arms.py` + `experiment-assignments-summer-2026.csv` | OSF_FILING.md:55-61 | Binding spec §0 + doc's own banner: deterministic alternation per spec §2 retires randomization; `randomize_arms.py` + CSV + seed `20260601` explicitly retired | retired | drift — superseded by spec §11 |
| Production-stack gate #8: "EXPERIMENT_DESIGN.md frozen at OSF commit hash" | OSF_FILING.md:62 | Spec §0: OSF references the rebaseline spec, THERMOSTAT_ARM_A_SCHEDULE.md, HVAC_LOGIC.md, NOT EXPERIMENT_DESIGN.md | drifted | drift — superseded by spec §11/§13 |
| Analysis-bundle gates #9-14 (constants locked, frozen env, replay validation, 4-source manifest) | OSF_FILING.md:64-200 | These describe the pre-rebaseline analysis bundle; rebaseline impl plan Phase 5/6 supersedes this entire bundle structure with the arm-period pipeline | mostly drift | drift — superseded by spec §11 + impl plan Phase 5/6 |
| Step-by-step filing §3 (OSF platform navigation), §4 (post-filing comms), §5 (Arm A on 2026-06-01) | OSF_FILING.md:250-274 | OSF platform UI/API; Arm A 2026-06-01 still aligns with spec §2 calendar (chris-confirmed 2026-05-18) | n/a procedural | chris-judgment-required (OSF platform mechanics) — **resolved 2026-05-18: open-ended template chosen; mechanics scope ≈ §3+§4+§5** |
| "Algorithm change pre-OSF (May 2026)" section about 2-week→4-week randomize_arms blocks | OSF_FILING.md:278-291 | Both 2-week and 4-week framings retired by spec §2's deterministic alternation | historical | historical-only |
| Year-round-vs-summer-only naming note about the CSV | OSF_FILING.md:295-303 | CSV itself is retired; calendar source-of-truth is `tools/analysis/arm_calendar.py` per spec §2 | drift | drift — superseded by spec §2 |

### Doc-action recommendation: OSF_FILING.md

**Verdict: trim to filing mechanics, replace the rest.**

- Pre-flight checklist (criteria #7 + #8 + #9-14): ~7 of 8 production gates and most of the analysis-bundle gates are superseded (drift) or describe retired artifacts. Only criteria #1 (per-service pytest), #2 (replay tests), #4 (AIR procedure), #6 (shakedown) survive substantially intact, and even those are now duplicated by spec §11.
- "Algorithm change pre-OSF (May 2026)" section is historical-only; the 2-week-vs-4-week dispute is retired by spec §2's deterministic alternation.
- "Year-round vs summer-only naming" — drifted; the CSV itself is retired.

**Recommendation: archive OSF_FILING.md** and replace with a 1-page successor `docs/OSF_FILING_MECHANICS.md` carrying ONLY the open-ended-template workflow (per Chris's OSF template choice 2026-05-18):
1. Tag repo at freeze commit
2. Generate Zenodo DOI
3. Create OSF open-ended registration
4. Narrative content with Zenodo DOI link
5. Submit + 48-hour approval
6. Update README badge

Reference spec §11/§13 for the acceptance-criteria list (which is now binding spec, not OSF doc).

---

## Doc 4: ARM_B_IMPLEMENTATION.md (operational content)

| Claim | Location | Ground-truth source | Verified value | Classification |
|---|---|---|---|---|
| "Current thresholds: MILD <82°F / NORMAL 82-94°F / HOT ≥95°F" | ARM_B_IMPLEMENTATION.md:122-125 | Code today uses MILD<75 / NORMAL 75-85 / HOT≥85 (app.py:875-877). The doc is describing the PRE-recalibration state to motivate the change. | confusing — labeled "Current" but reads as either historical or stale | drift (label is misleading; "Current" was true in May 2026 BEFORE the recalibration landed; after the recalibration these aren't current anymore) |
| "New thresholds: MILD<75 / NORMAL 75-85 / HOT ≥85 OR apparent ≥90" | ARM_B_IMPLEMENTATION.md:127-130 | app.py:875-877 + 897-900 | matches | matches-truth |
| RTP §2 module structure: tier list, hold, override semantics | ARM_B_IMPLEMENTATION.md:158-200 | `price_overlay.py:66-83` PRICE_TIERS exactly matches spec sketch; 30-min hold per DEFAULT_MINIMUM_HOLD_MINUTES | matches | matches-truth |
| RTP §2 "Integration in execute_action() at line 628" | ARM_B_IMPLEMENTATION.md:156 | Actual integration is in `run_schedule_check` (app.py:2673) calling `resolve_layer_priority`; line 628 is not a meaningful anchor in current app.py (3169 lines total) | drift (line-number reference stale) | drift |
| 5CP §3 module + state machine | ARM_B_IMPLEMENTATION.md:300-382 | pjm_5cp.py — verified extensively in EXPERIMENT_DESIGN table above | matches | matches-truth |
| 5CP §3 dual-scope DetectorScope sketch with fallbacks `20375.0` and `151525.0` | ARM_B_IMPLEMENTATION.md:347-355 | `pjm_5cp.py:97,105,240-252` | matches | matches-truth |
| 5CP §3 test bullet "Pre-season fallback (< 5 observations) uses 130,000 MW" | ARM_B_IMPLEMENTATION.md:466 | `pjm_5cp.py:90-96,97` explicitly documents the 130 GW value was the RTO-scale bug; the test now uses 20375 (ComEd) and 151525 (RTO) | 130,000 MW is the retired/buggy value | drift |
| Layer priority sketch `effective_cool = max(schedule_cool, price_cool, fivecp_cool)` then safety supervisor clamp | ARM_B_IMPLEMENTATION.md:481-510 | `app.py:1206` `effective_cool_f = max(schedule_cool_f, price_cool_f, fivecp_cool_f)`; `safety_supervisor.py:121-122` clamps to [65,86] | matches | matches-truth |
| Layer priority test cases | ARM_B_IMPLEMENTATION.md:531-538 | All testable against `resolve_layer_priority(...)`; e.g. schedule=73, scarcity override=85 → effective=85; schedule=68 pre-cool + elevated tier offset=+3 → effective=71 | matches | matches-truth |
| §10 acceptance criteria for OSF filing | ARM_B_IMPLEMENTATION.md:746-759 | Same criteria list as OSF_FILING.md's stale checklist; explicitly superseded per banner | drift | drift — superseded by spec §11 (already dispositioned by banner) |
| §0a NWS poller migration to forecastGridData | ARM_B_IMPLEMENTATION.md:36-80 | nws-poller writes Point("nws.forecast") today; migration completed | matches (describes the WHY) | historical-only (describes completed migration) |
| §0b PJM hourly metered load polling | ARM_B_IMPLEMENTATION.md:82-117 | `pjm_dm2_poller/app.py:203-204` `Schedule(hours=tuple(range(0,24)))` exactly matches the sketch | matches (migration completed) | historical-only |
| §7 Pre-cool deepening rule `peak > season_5th × 1.05 AND temp ≥90` | ARM_B_IMPLEMENTATION.md:628-635 | `precool.py:35-36, 96-115` | matches | matches-truth |
| §7 price-aware pre-cool sketch | ARM_B_IMPLEMENTATION.md:641-660 | `precool.py:184-294` implementation exists (plus DTOD-delivery aware ranking per P2.6) | matches (implementation is richer than sketch) | matches-truth (with additive code-side enhancement) |

### Doc-action recommendation: ARM_B_IMPLEMENTATION.md

**Verdict: tighten banner; eventually archive when HVAC_LOGIC.md absorbs the operational sketches.**

- All four operational sections (day-type, RTP, 5CP, layer priority) are matches-truth against current code. There is exactly ONE known drift in the operational content: the 130,000 MW fallback test bullet at line 466 (production code uses 20375/151525 per scope).
- The "Current thresholds" text at lines 122-125 describes the PRE-recalibration state to motivate the change — this is no longer "current" and should be relabeled "Prior thresholds (pre-recalibration)" or just deleted.
- §0a / §0b describe MIGRATIONS that already landed (matches-truth as historical narrative of what was built; operationally complete and reflected in the deployed code).
- The line-number references inside §2 ("Integration point: line 628") are stale; app.py is now 3169 lines and the integration moved into `run_schedule_check`.

**Recommendation: tighten the banner to mark §0a, §0b, §10 (acceptance criteria) as superseded/historical** (they are already implicitly covered, but the banner currently only retires §10 specifically). Then **plan to archive the whole doc once HVAC_LOGIC.md absorbs a "controller layers" section that cites EXPERIMENT_DESIGN Appendix A** (or its extraction at `CONTROLLER_CONSTANTS.md`). Until then, the operational sketches are still useful as a single-doc primer for someone reading the controller for the first time.

11 matches-truth / 4 drift / 1 historical-only — content is mostly sound, the framing is what's stale.

---

## Items requiring Chris's direct read (now resolved)

All four operator-judgment items from the truth-table verification have been resolved 2026-05-18:

1. **EXPERIMENT_DESIGN.md §11 ethics framing** — Chris read and approved 2026-05-18. Extract verbatim to `docs/ETHICS_FRAMING.md` (or keep with tightened banner in EXPERIMENT_DESIGN.md if not extracting).

2. **OSF_FILING.md §3 (OSF platform navigation)** — Resolved by Chris's open-ended template choice 2026-05-18. New `docs/OSF_FILING_MECHANICS.md` documents the open-ended workflow per the 6-step path in PR9.

3. **OSF_FILING.md §4 (post-filing comms)** — Operator-discretion item. Default scope: README badge update + README OSF link. Other comms (Twitter, email, blog post) are nice-to-have and not blocking. Confirmed 2026-05-18.

4. **OSF_FILING.md §5 (Arm A on 2026-06-01)** — Chris confirmed 2026-05-18: Arm A indeed starts 2026-06-01 per spec §2 calendar. OSF_FILING.md §5 claim is accurate.
