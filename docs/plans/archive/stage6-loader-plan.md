---
date: 2026-05-12
owner: chris
status: draft
role-label: chris
---

# Stage 6 O2 loader — execution plan

## Spec anchors

- [OSF_FILING.md](../OSF_FILING.md) criterion 14 (real-shape replay validation; Stage 6 is one of the three loader stubs deferred from PR #88)
- [ANALYSIS_PIPELINE.md](../ANALYSIS_PIPELINE.md) Stage 6 (O2 Layer 1/2/3 + detector accuracy)
- [EXPERIMENT_DESIGN.md](../EXPERIMENT_DESIGN.md) §7 / Att. M-2 framing and portfolio-sum scenarios

Stage 6 produces four locked CSVs:

- `o2_layer1.csv` — arm-delta in CPL-kW + capacity dollars
- `o2_layer2.csv` — portfolio-sum scenarios (1500 / 2033.653 / 3000 MW)
- `o2_layer3.csv` — billed-capacity reconciliation from ComEd bills
- `detector_accuracy.csv` — 5CP detector confusion matrix

## Locked decisions (post-brainstorm, including pushback round 2)

| Question | Decision |
|---|---|
| Tariff constants source | Existing `tools/o2_capacity_reconstruction/tariff_constants.json` via `TariffConstants`. Do not invent a new locked JSON. |
| Tariff year semantics | Add new API `TariffConstants.load_for_summer_year(summer_year)`. Internally loads year `summer_year + 1` because Att. M-2 is `CPLC_(Y+1)` from summer Y peaks (summer 2025 → 2026/27 capacity year). The existing `load(year)` keeps its current "load by capacity year" semantics; Stage 6 always goes through the summer-year wrapper. |
| ComEd bills | Read `comed.bill` from the Stage 1 parquet bundle. PDF parsing stays upstream in `tools/parse_comed_bill.py`; Stage 6 never reads PDFs. |
| summer_year | Derive from unique `pjm.coincident_peak{summer_year}` tag. Multiple distinct values → `AMBIGUOUS_SUMMER_YEAR` reason. Missing → `NO_PJM_5CP_HOURS_IN_WINDOW`. Never guess from manifest window. |
| PJM 5CP truth | `pjm.coincident_peak` (PDF-derived; ingested by `scrape_pjm_5cp_pdf.py`). Tags: `summer_year`, `peak_rank` (1..5). |
| ComEd 5CP truth | Derived from `pjm.metered_load{zone=CE}`. Top-5 hourly maxima across distinct **CT** calendar days within the summer-season window. |
| Verified-preferred for `pjm.metered_load` | Explicit `is_verified=true` row selection. For each hour, if any row has `is_verified=true`, use it; otherwise use the preliminary row and emit a per-output `comed_5cp_preliminary` provenance marker. **Do not use `fn: max` on MW.** That conflates "verified happens to be higher" with "verified rule" and breaks when PJM corrects downward. |
| `pjm.coincident_peak` shape | Carries PJM/RTO 5CP hours plus ComEd zone load AT those PJM hours. It does **not** carry ComEd's own five highest ComEd-zone hours. The two are distinct measurements. |
| `hvac.5cp_state` schema | Tags: `scope` (`rto` \| `comed_zone`), `zone` (`RTO` \| `CE`), `is_active` (`"true"` \| `"false"`). Fields: `current_load_mw`, `season_5th_highest_mw`, `load_ratio`, `load_derivative_mw_per_hour`, `forecast_peak_today_mw`. |
| Predicted-hold series | Three derived series per hour: `rto` (any `scope=rto, is_active=true`), `comed_zone` (any `scope=comed_zone, is_active=true`), `combined_any` (either scope). |
| Arm partitioning | Reuse locked `experiment-assignments-summer-2026.csv` loader. Each peak hour maps to the arm active that week. |
| Pre-randomization replay (e.g. 2025-only) | No overlap with the 2026 assignment CSV. Stage 6 emits `NO_ARM_ASSIGNMENTS_IN_WINDOW` and produces header-only Layer 1/2 CSVs. Layer 3 (bills) is independent and still emits if `comed.bill` data is present. |
| Mains kW | **Mean of `power_w` over the hour, divided by 1000.** Refoss poller emits `power_w` (instantaneous, ~30s cadence); the kWh fields are cumulative counters (`day_energy_kwh` etc.), NOT per-interval. **Do not sum a synthetic `energy_wh` field**; the production poller does not write one. (Pre-existing concern: Stage 2/3 loaders filter on `_field == "energy_wh"`. Test fixtures fake that field, but real data has none. Flagged as separate scope.) |
| Per-output optional | No zero-filled stubs in `_load_stage6_inputs`. The loader returns per-CSV input sub-dicts (`layer1_inputs`, `layer2_inputs`, `layer3_inputs`, `detector_inputs`). When a sub-dict is missing required inputs, `stage6_o2` writes that CSV header-only AND emits a reason code for that output, while other CSVs with sufficient input still emit data. |
| Zero-peak arm guard | If `n_peaks_arm_a == 0` or `n_peaks_arm_b == 0`, `compute_a_cust_cpl_kw` returns 0.0 for the empty arm, producing a misleading non-zero delta. Stage 6 must NOT emit a Layer 1 or Layer 2 row in that case. Both CSVs go header-only with reason code `INSUFFICIENT_PEAKS_BY_ARM`. Both arms must have at least one peak hour for the arm-delta to be reportable. |
| Partial ComEd 5CP (<5 distinct days) | If fewer than 5 distinct CT calendar days survive the verified-preferred selection, treat the bundle as incomplete for ComEd-5CP-dependent outputs. **Layer 2 (entire CSV) goes header-only** — all three Layer 2 scenarios depend on `ACustPL` from ComEd peaks, so partial peaks invalidate every scenario row. Detector_accuracy `scope=comed_zone` + `combined_any` rows also go header-only. RTO-only Layer 1 and `scope=rto` detector row can still emit. Reason code `INCOMPLETE_COMED_5CP_IN_WINDOW`. Loader-unit tests that exercise the helper directly may pass < 5 days; the orchestrator-level test asserts the reason code fires. |
| Provenance location | `<out_dir>/stage6/provenance.json` (one per stage output). Markers like `comed_5cp_preliminary`, `tariff_capacity_year`, `comed_distinct_day_tz=CT` live here. **Never write back into the Stage 1 input manifest** — that file describes inputs, not downstream interpretation. |
| ComEd distinct-day timezone | CT (`America/Chicago`). Tariff text is silent on this; CT matches PJM-ComEd-zone operational convention. Documented in provenance. |

## Schema change (pre-OSF, locked at OSF tag)

`DETECTOR_ACCURACY_COLUMNS` gains `scope` as the first column. Output rows: one per `{rto, comed_zone, combined_any}`. The detector is dual-scope; a single aggregate row hides whether RTO or ComEd coverage is failing.

New schema:

```python
DETECTOR_ACCURACY_COLUMNS = (
    "scope",            # rto | comed_zone | combined_any
    "tp", "fp", "fn", "tn", "tpr", "fpr", "fnr",
    "summer_hours_n", "published_5cp_n",
)
```

Update `compute_detector_accuracy` signature to accept per-scope predicted-hold maps and emit a list of rows.

`summer_hours_n` is the count of hours in the exported-window intersection during replay validation — NOT the full PJM summer season. Otherwise a short replay bundle inflates true-negative counts.

### Schema change lives in this PR, as an early standalone commit

Commit message: `schema(stage6): split detector accuracy by 5CP scope`. Contents:

- `DETECTOR_ACCURACY_COLUMNS` adding `scope` as first column
- `compute_detector_accuracy` signature + tests updated for `rto`/`comed_zone`/`combined_any`
- ANALYSIS_PIPELINE.md note on the dual-scope rationale

Reason for not splitting into its own PR: the schema change is part of making Stage 6 truthful. Reviewer needs to see the full chain `hvac.5cp_state.scope → per-scope predictions → scoped accuracy rows` together.

## Phases (vertical slices)

Each phase ends with the pipeline emitting at least header-only output for ALL four Stage 6 CSVs, so downstream stages keep running. Each phase moves at least one CSV from header-only to populated.

### Phase 0 — schema change commit (no behavior change)

- Flip `DETECTOR_ACCURACY_COLUMNS` to include `scope`.
- Update `compute_detector_accuracy` to accept per-scope inputs and emit one row per scope.
- Update existing unit tests.
- Add ANALYSIS_PIPELINE.md note.
- Pipeline still works against synthetic fixtures (3-row detector_accuracy.csv instead of 1).

### Phase 1 — tracer: Layer 1 from PJM 5CP + refoss `power_w` + assignments + tariff

Smallest end-to-end slice through every layer Stage 6 touches.

- New helpers (in `tools/analysis/pipeline.py` near existing Stage 6 code):
  - `_load_pjm_5cp_hours(manifest, stage1_dir) -> (summer_year, list[datetime]) | None`. Reads `pjm.coincident_peak`, asserts unique `summer_year`. Multiple distinct values → returns sentinel that maps to `AMBIGUOUS_SUMMER_YEAR`.
  - `_load_hourly_mains_kw(manifest, stage1_dir) -> dict[datetime, float]`. Mean of `refoss.channel` `power_w` (channels em:1 + em:7) per UTC hour, divided by 1000. Comment explicitly that this is the production-shape path (no `energy_wh` field exists).
  - `_partition_peak_hours_by_arm(peak_hours, assignments) -> dict[str, list[datetime]]`.
- New API on `TariffConstants`: `load_for_summer_year(summer_year, path=None)` returns `load(summer_year + 1)`. Document the Y+1 rule.
- Wire `_load_stage6_inputs` to return per-CSV sub-dicts. Layer 1 populated; others return `None` because their inputs aren't wired yet.
- `stage6_o2` handles per-CSV None: writes header-only + emits reason code for that CSV; other CSVs still populate.
- Acceptance test (outside-in, real-shape fixture, no monkeypatches at the loader level): build a stage1 bundle with **two `pjm.coincident_peak` rows in different Monday-weeks** (so one falls in an Arm A week and one in an Arm B week per the synthetic assignment CSV) plus matching `refoss.channel` hours. Run `stage6_o2`. Assert `o2_layer1.csv` has one populated row with `n_peaks_arm_a >= 1` AND `n_peaks_arm_b >= 1`. Assert other Stage 6 CSVs are header-only AND `stage6/reason_report.json` contains the right codes.
- Companion test (zero-peak arm guard): build a stage1 bundle with one peak hour falling in an Arm-A week ONLY. Run `stage6_o2`. Assert `o2_layer1.csv` is header-only AND `reason_report.json` contains `INSUFFICIENT_PEAKS_BY_ARM`. This pins the zero-arm guard so a future regression can't quietly emit a 0.0-vs-real delta.

Phase 1 tests use injected assignment CSV path via the existing `ASSIGNMENT_CSV_PATH` monkeypatch pattern (per memory `feedback_verify_production_wireup` — the production CSV is for 2026 Mondays only; tests build their own assignment fixture).

### Phase 2 — derive ComEd 5CP from `pjm.metered_load{zone=CE}`

- New flux query at `tools/analysis/queries/pjm.metered_load.flux` (Stage 1 export needs this measurement).
- New helper `_load_comed_5cp_hours(manifest, stage1_dir, summer_year) -> (list[datetime], list[str])`. Reads `pjm.metered_load` parquet:
  - Filter `zone == "CE"` and the summer-season window (Jun 1 to Sep 30 CT, of summer_year).
  - Group by `_time`. For each timestamp: prefer the row with `is_verified == "true"`; fall back to the unverified row.
  - Track which selected rows were preliminary; return those timestamps in a separate list for provenance.
  - Aggregate to hourly (the rows are already hourly by PJM publish cadence).
  - Convert each row's UTC `_time` to CT date. Group by CT date; take the max-MW hour per date.
  - Return the top-5 distinct CT calendar days by their day-max hour.
- Provenance: if any of the top-5 selected hours came from preliminary rows, the orchestrator writes `comed_5cp_preliminary: true` plus the per-hour breakdown into `stage6/provenance.json`.
- Partial-bundle handling: if the helper finds fewer than 5 distinct CT calendar days, it returns what it has plus a `partial=True` marker. The orchestrator emits `INCOMPLETE_COMED_5CP_IN_WINDOW` and downgrades ComEd-dependent outputs to header-only (per the per-output decision table). Loader-unit tests are allowed to exercise the helper directly with < 5 days; the orchestrator-level test asserts the reason code fires.
- Acceptance tests:
  - **Verified preferred regardless of MW magnitude**: build a stage1 bundle with synthetic `pjm.metered_load{zone=CE}` rows including one hour where verified MW < preliminary MW. Assert the verified row is selected.
  - **Partial < 5 days**: build a bundle with only 3 distinct CT-day peaks. Assert `INCOMPLETE_COMED_5CP_IN_WINDOW` fires in `reason_report.json` and `o2_layer2.csv` ComEd-scenario rows are header-only.

### Phase 3 — Layer 2 portfolio-sum scenarios

- `_load_stage6_inputs` returns `tariff_constants = TariffConstants.load_for_summer_year(summer_year)`.
- `compute_layer2_scenarios` (already in pipeline.py) is invoked with the three named scenarios from `tariff_constants.json` (`low=1500`, `anchor_2021=2033.653`, `high=3000`).
- Provenance: `stage6/provenance.json` records `tariff_capacity_year = summer_year + 1` for auditability.
- Acceptance test: bundle with peak hours and mains kW produces a non-empty `o2_layer2.csv` with three scenario rows.

### Phase 4 — Layer 3 from `comed.bill` parquet

- New helper `_load_comed_bills_for_capacity_year(manifest, stage1_dir, capacity_year) -> list[dict]`. Filters `comed.bill` rows to the **locked bill months (May-Sep, months 5-9)** of `capacity_year` (per Att. M-2 lag and `compute_layer3_bill_capacity_dollars`'s default `months=(5, 6, 7, 8, 9)`), pivots to `{year, month, capacity_charge_dollars}` dicts.
- The orchestrator passes the resulting list directly to `compute_layer3_bill_capacity_dollars(comed_bills, year_y_plus_1=capacity_year)` so the month set is enforced both at load time and at compute time.
- **Capacity year still requires summer_year**. Layer 3 is independent of arm assignments AND independent of ComEd 5CP completeness, but it is NOT independent of `pjm.coincident_peak`. The capacity year (`summer_year + 1`) is the only authoritative way to know which year's bills are in-window; deriving it from bill rows would be quiet inference. If `pjm.coincident_peak` is missing or ambiguous, Stage 6 emits the existing `NO_PJM_5CP_HOURS_IN_WINDOW` / `AMBIGUOUS_SUMMER_YEAR` codes for Layer 3 too. Layer 3 header-only in that case.
- Independence beyond summer_year: Layer 3 still fires when Layer 1/2 emit `NO_ARM_ASSIGNMENTS_IN_WINDOW`, `INSUFFICIENT_PEAKS_BY_ARM`, or `INCOMPLETE_COMED_5CP_IN_WINDOW`. Per-output gating.
- Acceptance tests:
  - Bundle with `pjm.coincident_peak` + `comed.bill` rows for May-Sep of Y+1 → non-empty `o2_layer3.csv`.
  - Bundle with `comed.bill` rows only outside May-Sep → header-only + `NO_COMED_BILLS_IN_WINDOW`.
  - Bundle with `comed.bill` rows but NO `pjm.coincident_peak` → header-only + `NO_PJM_5CP_HOURS_IN_WINDOW` propagates to Layer 3.

### Phase 5 — detector_accuracy per scope

- New helper `_load_predicted_holds_by_scope(manifest, stage1_dir, summer_year) -> dict[str, dict[datetime, bool]]`. For `scope` in `{"rto", "comed_zone"}`: keys are UTC hour timestamps in the exported-window intersection (NOT the full PJM summer), values are bool (any `is_active="true"` in that hour for that scope).
- `combined_any` derived from union of the two scope dicts (per-hour OR).
- `published_5cp_hours` becomes `{"rto": pjm_5cp_hours, "comed_zone": comed_5cp_hours}`. `combined_any` published hours = union of both.
- `summer_hours_n` = count of distinct hours in the exported-window intersection.
- Acceptance test: bundle with `hvac.5cp_state` rows + PJM 5CP + ComEd 5CP. Assert three rows in `detector_accuracy.csv`, one per scope, with correct TP/FP/FN/TN counts.

### Phase 6 — reason codes + provenance + docs

- New reason codes added to `tools/analysis/replay/reason_codes.py`:
  - `AMBIGUOUS_SUMMER_YEAR`
  - `NO_COMED_5CP_HOURS_IN_WINDOW` (zero distinct CT days)
  - `INCOMPLETE_COMED_5CP_IN_WINDOW` (1-4 distinct CT days; partial)
  - `NO_REFOSS_MAINS_IN_WINDOW`
  - `NO_5CP_STATE_IN_WINDOW`
  - `INSUFFICIENT_PEAKS_BY_ARM` (zero peaks for either arm in the window)
- `stage6_o2` wires reason-code emission per CSV output (independent gating per the per-output-optional decision above).
- `stage6/provenance.json` schema documented in the replay module README. Fields:
  - `comed_5cp_preliminary` (bool)
  - `comed_5cp_preliminary_hours` (list of ISO timestamps)
  - `tariff_capacity_year` (int)
  - `comed_distinct_day_tz` (`"CT"`)
  - `summer_year` (int)
- Update `docs/REPLAY_VALIDATION.md`: source-type catalog mentions `pjm.metered_load` as the basis for ComEd 5CP derivation.
- Update `docs/ANALYSIS_PIPELINE.md` Stage 6 description: scope distinction in detector_accuracy, ComEd 5CP source, preliminary marker, per-output optional behavior.
- Archive plan to `docs/plans/archive/stage6-loader-plan.md`.

## Out of scope this PR

- Backfilling historical `pjm.metered_load` data into Influx.
- Multi-year bundle support. This PR requires a single summer_year tag value in `pjm.coincident_peak`; ambiguous bundles emit a reason code.
- Detector tuning. The detector's behavior is locked in `pjm_5cp.py`; Stage 6 measures its accuracy, doesn't change it.
- **Fixing the Stage 2/3 refoss `energy_wh` assumption.** Their loaders filter on a field the production poller does not emit. Test fixtures fake it, so existing tests pass; real-data replay would yield zero rows. Separate PR.
- **Reviewing `pjm_5cp.py`'s `fn: max` verified-preferred logic.** Likely has the same bug Stage 6 corrects, but live scheduler behavior is out of scope here.

## Risks

- 2025 cooling-season `pjm.metered_load{zone=CE}` data must be ingested before Phase 2's e2e test runs. **Verify in Influx pre-Phase-2.**
- Existing tests for `compute_detector_accuracy` use the single-row schema. The Phase 0 commit must update them in the same change.
- Per-output optional makes the orchestrator branchier than the current implementation. Risk of subtle bugs where Layer N's inputs partly succeed; mitigation: each loader returns a tagged result (`Ok` / `Missing(reason)`), the orchestrator dispatches per-output.

## Tracking

- Sequential commits per phase (Phase 0 first, then 1-6). Each phase commits its tests + code.
- Single PR `feature/stage6-loader`, base `main`. No stacking.
- Draft PR opened after Phase 0 + Phase 1 land; phase commits push to it.
- Archive plan in closing commit.
