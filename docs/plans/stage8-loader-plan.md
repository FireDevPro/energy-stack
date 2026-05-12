---
date: 2026-05-12
owner: chris
status: draft
role-label: chris
---

# Stage 8 decomposition loader — execution plan

## Spec anchors

- [OSF_FILING.md](../OSF_FILING.md) criterion 14 (Stage 8 is one of the three loader stubs deferred from PR #88).
- [ANALYSIS_PIPELINE.md](../ANALYSIS_PIPELINE.md) Stage 8 (forecast-correlated vs grid-event decomposition).
- [EXPERIMENT_DESIGN.md](../EXPERIMENT_DESIGN.md) §7 (spike-day classification, layer attribution).

Stage 8 produces two CSVs:

- `decomposition.csv` — per (outcome × spike category): Arm A median daily value vs Arm B median daily value + B−A delta.
- `layer_attribution.csv` — per grid-event Arm B day: which layer triggered (`price_spike_reactivity`, `5cp_detection`, `both`, or `neither`), indoor temp at price-peak hour, action label.

## Locked decisions (post-brainstorm, including round-2 pushback)

| Question | Decision |
|---|---|
| Producer-schema mapping (forecast) | Loader maps `nws.forecast.high_f` → classifier's `max_forecast_temp_f` argument. `apparent_max_f` passes through directly. `classify_spike_day` keeps its spec-aligned signature; the loader adapts producer schema to analysis vocabulary. |
| Daily outcome semantics | **DAILY DOLLARS, not $/CDD.** Zero-CDD grid-event days (mild-weather price events) remain part of the decomposition. Dropping them would erase exactly the kind of event this decomposition exists to measure. |
| Per-outcome names & formulas (daily) | Stage 8 defines its OWN outcome names (Stage 3's weekly names lie about the units when reused here): `o1_daily_hvac_dollars`, `o3_daily_peak_hvac_kw`, `o4_daily_mains_dollars`. Formulas: `o1 = sum_h (hvac_kwh[h] × (supply_¢[h] + delivery_¢[h])) / 100`; `o4 = same with mains channels`; `o3 = max_h hvac_kwh[h]` (kW, NOT dollars). |
| Stage 8 outcomes are descriptive | Stage 8's daily outcomes are **descriptive only** — they are NOT fed into Stage 5 (matched-pair effects) or Stage 7 (SCED sign-flip). Those stages consume `STAGE5_OUTCOMES` (weekly aggregates from Stage 3). Mixing the two would double-count and conflate units. Stage 8's outputs (decomposition.csv, layer_attribution.csv) are reported as a side table in the OSF write-up, not entered into the primary inference chain. |
| Loader internal naming | `daily_records[d]["outcomes"]` keyed by Stage-8 outcome name (NOT `["costs"]`, which lies about `o3`). Orchestrator's variable names follow suit. |
| `STAGE8_DECOMPOSITION_COLUMNS` schema | Renamed to drop dollar-cost connotation: `outcome, category, arm_a_n_days, arm_a_median_value, arm_b_n_days, arm_b_median_value, delta_median_value`. Plus a new `unit` column (`dollars` for o1/o4; `kw` for o3) so a reviewer can't misread an o3 value as dollars. |
| Layer attribution enum | `price_spike_reactivity`, `5cp_detection`, `both`, `neither`, `unknown`. No forced winner when both fire. `neither` means **price layer reconstructed as inactive AND 5CP detector inactive** at the price-peak hour. `unknown` means **the price-overlay state machine could not determine the active tier** (no prior `hvac.price_overlay` transition row in the lookback window — typically a bundle that doesn't extend back far enough to capture the last tier change before the day's price-peak hour). `unknown` preserves the day without lying about layer state. NEVER defaults unknown to `neither` or `normal`. |
| Layer attribution granularity | **One row per grid-event Arm B day**, anchored at the day's price-peak hour. Schema is `date + hour_ct`; one row per day keeps cardinality bounded. |
| Price-layer state source | **State-machine reconstruction from `hvac.price_overlay` transitions.** That measurement is written only on tier change, so for each hour we walk transitions in reverse-chrono and find the latest `new_tier` at `_time ≤ hour_end` within a 24-hour lookback. If `new_tier ∈ {"elevated", "scarcity"}`, price layer was active at the hour. If `new_tier == "normal"`, price layer was inactive. **If NO transition row exists in the lookback window, the state is `unknown`** — do not default to `"normal"`. The reviewer/auditor can't distinguish "scheduler never set a tier in this window" from "tier transitioned to normal long enough ago that the bundle missed it." **Do NOT use `hvac.actions` tags as a continuous state signal** — that measurement is written on scheduled-action / mid-period-repush moments, not every tick when nothing changes, so the price-peak hour may have NO `hvac.actions` row even when the price layer is active. |
| 5CP-active state source | `hvac.5cp_state` row in the hour with `is_active == "true"` (any scope). That measurement IS tick-cadence (~5 min), so "any active in the hour" is well-defined. |
| `hvac.actions` use | **Only for action label / actual push evidence**, not for layer state. Read most-recent `action_label` tag at the price-peak hour for the `layer_attribution.csv` action column. Absence of an action row at the hour is fine — `layer_triggered` is still determined from the reconstructed price-layer state + 5CP state. |
| `nws.forecast` 21:00-prior lookup | For day D's classification, find `nws.forecast` rows with `_time` within 21:00 CT ± 30 min on D−1 and `for_period` corresponding to D. **Verify producer's `for_period` tag values via quick Influx query before Phase 2 starts.** |
| Quiet-zero arm guard | If one arm has ZERO days in an (outcome, category) cell, **do not compute a delta against zero.** Write the row with the populated arm's median, `arm_X_n_days=0, arm_X_median_value=""`, `delta_median_value=""`, AND emit `INSUFFICIENT_ARM_DAYS_FOR_CATEGORY` in `stage8/reason_report.json` keyed on the (outcome, category) cell. Both arms zero → skip the row entirely (already the existing behavior). |
| Descriptive vs matched-pair framing | Stage 8's decomposition is **descriptive arm-level category medians**, NOT matched-pair median Δ. Stage 4-style matching operates at the WEEK level; per-day decomposition pools across qualifying days and takes medians. The spec wording at `EXPERIMENT_DESIGN.md:334` / `:363` and `ANALYSIS_PIPELINE.md:239` calls it "matched-pair" — that's inconsistent with the implementation. **Phase 0 fixes the docs to say "arm-level category median" or "descriptive category median" for Stage 8 specifically, leaving Stage 4/5/7 matched-pair language untouched (those are actually matched).** |
| Pre-experiment behavior | Stage 8 depends on Stage 3 `weekly.csv`. If Stage 3 is header-only (no qualifying weeks), Stage 8 emits header-only + reason code `NO_QUALIFYING_DAYS_FROM_STAGE3`. |
| Day-level exclusion respect | **Stage 8 must NOT expand each qualifying week into 7 days without filtering.** Stage 2's `rule8_pi_apply` already computes day-level exclusions via `rule1_tier4_days ∪ rule7_outage_days ∪ rule9_vacation_days`. A week with 5-6 qualifying days qualifies overall (per the ≥5 threshold), but the 1-2 excluded days within it MUST NOT enter Stage 8's daily decomposition — that would silently reintroduce vacation days, Tier 4 Refoss-gap days, scheduler-outage days, etc. as if they were normal. **Phase 1's first action checks whether Stage 2 currently exposes day-level exclusions in a machine-readable form** (the existing `outages.csv` has a `date` column for some kinds; `imputed_intervals.csv` has tier-tagged interval rows that need expansion to day-of-`start_ts`). If the existing files don't cleanly expose "this day is excluded," Phase 0 adds a `stage2/qualifying_days.csv` (locked schema addition) that lists `(week_start_ct, arm, date, included:bool, exclusion_source:str)` rows for every day in every qualifying week. Rule 5 weather-dropped days are also excluded (no day-mean temp → can't classify reliably). |
| Per-output gating | Same pattern as Stage 6: `decomposition.csv` and `layer_attribution.csv` gate independently. |

## Phases (vertical slices)

### Phase 0 — schema + naming + spec wording + day-exclusion exposure

Lands first because the wrong names + missing day-exclusion data would leak into every subsequent oracle test.

- `STAGE8_DECOMPOSITION_COLUMNS` renamed: `arm_a_median_cost` → `arm_a_median_value`, `arm_b_median_cost` → `arm_b_median_value`, `delta_median` → `delta_median_value`. Add `unit` column as the second column (after `outcome`).
- New Stage-8-specific outcome name constant: `STAGE8_OUTCOMES = ("o1_daily_hvac_dollars", "o3_daily_peak_hvac_kw", "o4_daily_mains_dollars")` with parallel `STAGE8_OUTCOME_UNITS` mapping. Stage 8 orchestrator stops reusing `STAGE5_OUTCOMES`.
- New `ReasonCode.INSUFFICIENT_ARM_DAYS_FOR_CATEGORY` for the quiet-zero guard.
- New `ReasonCode.NO_QUALIFYING_DAYS_FROM_STAGE3`.
- New `ReasonCode.NO_NWS_FORECAST_FOR_CLASSIFICATION`.
- Existing `stage8_decomposition` orchestrator updated for the new column names + the zero-arm-days guard (write row with blank delta + emit per-cell reason).
- **Stage 2 day-exclusion exposure (new locked-schema output).** Audit the existing `stage2/outages.csv` + `stage2/imputed_intervals.csv` to determine whether day-level exclusions are recoverable in a clean machine-readable form. If yes, document the loader path. If no, add `stage2/qualifying_days.csv` with locked columns `(week_start_ct, arm, date, included, exclusion_source)`:
  - `included` is `true` when the day passes all of rule 1 (no Tier 4 gap), rule 5 (≤6h ecowitt-both-missing), rule 7 (no scheduler outage overlapping a control-relevant window), rule 9 (not a vacation day). `false` otherwise.
  - `exclusion_source` ∈ `{rule1_tier4, rule5_weather_gap, rule7_scheduler_outage, rule9_vacation}` when `included=false`; empty when `included=true`.
  - One row per (qualifying-week-monday × arm × day-in-week), 7 rows per qualifying week.
- Doc updates:
  - `docs/EXPERIMENT_DESIGN.md` §7 line ~334 and §8 line ~363: replace "matched-pair median Arm B − Arm A cost difference, decomposed by day classification" with "descriptive Arm B − Arm A median value difference per (outcome × category)" or similar. Matched-pair stays for Stages 4-5-7 elsewhere in the doc.
  - `docs/ANALYSIS_PIPELINE.md` §Stage 8 description: same fix; also add the new `qualifying_days.csv` (if introduced) to the Stage 2 output schema list.
- Acceptance tests (Phase 0):
  - Schema column constant audit (string-compare against locked list).
  - Synthetic-fixture quiet-zero guard: feed `_load_stage8_inputs` mock returning Arm-A-only days in a category → orchestrator writes row with blank delta + reason file has `INSUFFICIENT_ARM_DAYS_FOR_CATEGORY` keyed on (outcome, category).
  - **Day-exclusion correctness**: synthetic week with 6 qualifying days + 1 vacation day. Stage 2 outputs `qualifying_days.csv` with the vacation day marked `included=false, exclusion_source=rule9_vacation`. The week itself qualifies (≥5 days). Stage 8 must NOT see the vacation day in its daily decomposition.

### Phase 1 — tracer: one no-spike day, one populated decomposition row

End-to-end slice through every layer Stage 8 will eventually touch.

- New helpers:
  - `_load_qualifying_days_from_stage2_stage3(stage2_dir, stage3_dir) -> list[dict]` — joins Stage 3's `weekly.csv` (which weeks qualify) with Stage 2's day-level exclusion data (`qualifying_days.csv` from Phase 0). Returns day rows ONLY for `(qualifying week × included=true day)`. Excluded days carry their `exclusion_source` reason in the dropped log for provenance.
  - `_load_daily_hourly_records(manifest, stage1_dir, day_ct, channels) -> list[dict]` — 24-hour version of Stage 3's hourly-refoss/prices helper, scoped to one CT calendar day for a channel set.
- Wire `_load_stage8_inputs` to return per-output sub-dicts (`decomposition`, `layer_attribution`). For Phase 1: `decomposition` populated when Stage 3 has at least one qualifying week; all days classified as `no_spike` (Phase 2 adds real classification); `layer_attribution` returns `None` until Phase 4.
- Acceptance tests (oracles):
  - **One no-spike day → exactly one decomposition row.** Hand-computed daily dollars for `o1_daily_hvac_dollars`: 24 hours × constant hvac_kwh × (constant supply_¢ + delivery_¢ at each hour's DTOD tier) → exact pennies-tolerant value.
  - **Zero-CDD no-spike day still counted** — fixture with CDD=0 but real HVAC dollars; row exists in decomposition.csv.
  - **`unit` column = `dollars` for o1/o4, `kw` for o3.**

### Phase 2 — spike classification: comed.prices hourly + nws.forecast 21:00-prior

**Phase 2 first step: quick Influx query to learn the `for_period` tag values in `nws.forecast`** (one of `today/tomorrow/day_2/...`?). Plan adapts to whatever is real.

- New helpers:
  - `_hourly_prices_for_day_ct(prices_df, day_ct) -> list[float]` — 24 hourly means of `period_type=5min` rows, day-scoped (mirrors Stage 3's `_stage3_hourly_supply_prices` shape).
  - `_forecast_for_day_ct(forecast_df, day_ct) -> dict | None` — find rows in 21:00 CT ± 30 min on D−1 with `for_period` matching D. Map `nws.forecast.high_f` → `max_forecast_temp_f`; pass through `apparent_max_f`. Returns `None` if no issuance found.
- Per-day classification calls `classify_spike_day(hourly_prices, max_forecast_temp_f, apparent_max_f)`.
- Acceptance tests (oracles):
  - **`high_f` mapping pinned**: a fixture with `nws.forecast.high_f=87.0` produces a classifier call with `max_forecast_temp_f=87.0`. Catches a regression that maps the wrong field by name.
  - **21:00-prior issuance lookup**: fixture with two issuances on D−1 (21:00 + 14:00); the 21:00 row is selected for D.
  - **Three-category fixture**: one no-spike day, one forecast-correlated, one grid-event; each surfaces in decomposition.csv with the right `category`.
  - **Missing 21:00 issuance** → day reason-codes out with `NO_NWS_FORECAST_FOR_CLASSIFICATION` (per-day note in provenance; day's contribution dropped from the median).

### Phase 3 — per-day outcome arithmetic (dollars + peak kW)

- New helpers:
  - `_daily_outcome_values(manifest, stage1_dir, day_ct) -> dict` — returns `{"o1_daily_hvac_dollars": float, "o3_daily_peak_hvac_kw": float, "o4_daily_mains_dollars": float}` for one CT day. Dollar formulas use `dtod_delivery_rate_for_hour_ct` + supply prices × hourly kwh per the locked decision table.
- Acceptance tests (oracles):
  - **Asymmetric hourly fixture → exact daily dollar value** (hand-computed within ¢ tolerance). Non-uniform kwh AND non-uniform supply prices so the test pins both the per-hour multiplication and the per-day summation.
  - **`o3_daily_peak_hvac_kw` is `max(hourly_kwh)`, not mean.** Fixture with one spiky hour; oracle value is that hour's kWh, not the day's mean.
  - **HVAC vs mains channel split** at the day level mirrors Stage 3 (em:1+em:7 for o4; em:2+em:8+em:9 for o1/o3).

### Phase 4 — layer attribution from reconstructed price-overlay state + in-hour 5CP

This is the load-bearing accuracy phase. The previous plan's "hvac.actions tags in hour" approach was wrong: `hvac.actions` is written on schedule decision moments, not as a continuous state log.

- New helpers:
  - `_price_overlay_state_at_hour(price_overlay_df, hour_utc, lookback=datetime.timedelta(hours=24)) -> str` — walks `hvac.price_overlay` rows in reverse-chrono within `lookback` window; returns the `new_tier` value of the latest row with `_time ≤ hour_utc + 1h`. Returns `"normal"` (or `"unknown"`?) if no transitions found in the lookback. **Decide which** based on whether bundles routinely cover scheduler boot-time (where prior state is unknowable from the bundle alone).
  - `_fivecp_active_in_hour(fivecp_state_df, hour_utc) -> bool` — any `hvac.5cp_state` row in the hour with `is_active == "true"` (across both rto and comed_zone scopes).
  - `_price_peak_hour_ct(prices_df, day_ct) -> int | None` — hour-of-day (0..23 CT) with max hourly supply price for that day.
  - `_indoor_temp_at_hour(thermostat_df, hour_utc) -> float | None` — mean `indoor_temp_f` from `hvac.thermostat` rows within the hour.
  - `_action_label_at_hour(actions_df, hour_utc) -> str | None` — most recent `action_label` tag in the hour. May be `None` if no action was logged in that hour (normal — `hvac.actions` isn't continuous).
- Build `layer_attribution` for each grid-event Arm B day:
  1. Find day's price-peak hour.
  2. Reconstruct price-layer state at the hour (state machine over `hvac.price_overlay`).
  3. Check 5CP active in the hour (`hvac.5cp_state`).
  4. `layer_triggered` from the (price_active, fivecp_active) combo: both/either/neither per locked enum.
  5. Read indoor_temp_f (may be None if thermostat poller had a gap).
  6. Read action_label (may be None — that's OK; report empty in CSV).
- Acceptance tests (oracles):
  - **Price-overlay state machine, single transition**: fixture has one `hvac.price_overlay` row at 14:00 with `new_tier="elevated"`. Price-peak hour is 17:00. Helper returns `"elevated"`. Catches the "use hvac.actions tags" regression.
  - **Price-overlay state machine, multiple transitions**: rows at 14:00 (`elevated`), 16:00 (`scarcity`), 18:00 (`normal`). Peak hour 17:00. Helper returns `"scarcity"` (latest `_time ≤ 17:59`).
  - **Price-overlay state machine, no transition in lookback**: zero rows. Helper returns sentinel `"unknown"` (NOT `"normal"`). The orchestrator translates that into `layer_triggered="unknown"` on the layer_attribution row + records the day in `provenance.price_overlay_state_unknown_days`.
  - **`layer_triggered` combinatorics**: 5 fixtures (price-only / 5cp-only / both / neither / unknown). Each asserts the exact enum value. The `unknown` fixture has no `hvac.price_overlay` transitions in the lookback at all.
  - **Price-peak hour identification**: asymmetric 24-hour price fixture; pin the exact peak hour selection.
  - **Action-row absence at peak hour does NOT force `layer_triggered="neither"`** — if price-overlay state machine says `elevated`, the row writes `price_spike_reactivity` even with no `hvac.actions` evidence. Action column writes empty.

### Phase 5 — orchestrator integration + reason codes + provenance + replay re-run

- `_load_stage8_inputs` returns per-output sub-dicts each `{"data": ...}` or `{"reason_code": ReasonCode.X}`.
- `stage8_decomposition` orchestrator: per-output gating mirroring Stage 6.
- Reason codes wired:
  - `NO_QUALIFYING_DAYS_FROM_STAGE3` — decomposition + layer_attribution both header-only.
  - `NO_NWS_FORECAST_FOR_CLASSIFICATION` — decomposition rows dropped (or all categorized `unknown`?); pick in Phase 5.
  - `INSUFFICIENT_ARM_DAYS_FOR_CATEGORY` — per-(outcome, category) cell quiet-zero guard.
  - `NO_GRID_EVENT_ARM_B_DAYS` — decomposition has rows but no grid-event days fell in Arm B; layer_attribution header-only.
- Provenance sidecar `stage8/provenance.json`:
  - `spike_classification_summary`: counts per category per arm
  - `layer_attribution_summary`: counts per `layer_triggered` value (including `unknown`)
  - `missing_forecast_classification_days`: list of days dropped from the decomposition because the 21:00 CT prior issuance was missing. (Locked behavior: drop the day, no fallback. The provenance list lets a reviewer see how many days the decomposition lost to forecast-data gaps.)
  - `price_overlay_state_unknown_days`: list of grid-event Arm B days whose `layer_triggered = "unknown"` because the price-overlay state machine had no transition in the lookback window.
  - `day_exclusions_summary`: counts per `exclusion_source` (rule1_tier4 / rule5_weather_gap / rule7_scheduler_outage / rule9_vacation) across all qualifying weeks. Audit trail for why some weeks contributed fewer than 7 days.
- Documentation updates: `docs/ANALYSIS_PIPELINE.md` Stage 8 description reflects the dollar-not-$/CDD daily semantics + descriptive-not-matched framing. `docs/REPLAY_VALIDATION.md` notes any new measurement source-type requirements (none expected — all sources already cataloged).
- Replay re-run after merge: rebase the replay-validation branch (or open a new dated one) onto main and re-run 7d + 90d. Stage 8 was previously header-only via the None loader. Post-Phase-5 it should populate or reason-code its empties for explicit causes. Expect to surface 1-2 more schema-drift bugs by the same pattern as PRs #94–#97. **Specifically watch for**: `hvac.price_overlay.new_tier` tag value strings (does the producer write `"normal"` exactly?), `nws.forecast.for_period` tag values, `hvac.thermostat.indoor_temp_f` field name confirmation.

## Out of scope this PR

- Stage 9 sensitivity loader (separate PR; depends on this).
- `pjm_5cp.py` `fn:max` verified-preferred fix (separate live-scheduler PR).
- FERC exhibit cross-check for `cplc_kw` (small follow-on test in `o2_capacity_reconstruction/`).
- Refactoring `classify_spike_day` to take a forecast row directly instead of named args.
- Re-running summer 2026 with real Arm A / Arm B data — Stage 2 still reasons out pre-2026-06-01. First post-experiment-start weekly run is when Stage 8 produces meaningful output.
- Implementing actual matched-pair daily decomposition (alternative to the current descriptive shape). Out of scope; would require a Stage 8' that consumes Stage 4 pair_ids and computes within-pair daily deltas. Not the path locked in §7.

## Risks

- **Price-overlay state-machine lookback boundary.** A 7-day or 90-day bundle may not include the `hvac.price_overlay` transition that established the active tier at the bundle's earliest hours. Mitigation: 24-hour default lookback. Days where the state remains unknowable are written with `layer_triggered="unknown"` AND recorded in `provenance.price_overlay_state_unknown_days`. NEVER default unknown to `neither` or `normal`.
- **`for_period` tag values unknown.** Phase 2 first action.
- **Day-arithmetic edge case at DST transition.** Spring-forward has 23 hours, fall-back has 25. Daily dollar sum naturally handles variable hour count; test the boundary day in Phase 3.
- **Pre-experiment replay** still reasons out at Stage 2. Stage 8 cannot exercise its full output paths until summer 2026 data lands. The OSF claim Stage 8 supports here is "the loader's code paths are exercised end-to-end against real-shape synthetic fixtures + the pre-experiment real-data replay produces reason-coded empties not silent ones."

## Tracking

- Sequential commits per phase (Phases 0–5). Each phase commits its tests + code.
- Single PR `feature/stage8-loader`, base `main`. No stacking.
- Draft PR opened after Phase 0 + Phase 1 land.
- Archive plan in closing commit.
