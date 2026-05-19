---
name: pre-osf-doc-audit-execution-2026-05-18
date: 2026-05-18
owner: chris
status: in-progress
role-label: execution-plan
osf-filing-target: 2026-05-30
related:
  - pre-osf-doc-audit-findings-2026-05-18.md
  - pre-osf-doc-audit-truth-tables-2026-05-18.md
  - pre-osf-doc-audit-codex-2026-05-18.md
  - sced-rebaseline-spec-2026-05-13.md
  - sced-rebaseline-implementation-2026-05-13.md
progress_2026-05-18:
  - PR1 spec/impl P0 fixes — merged (#152, 7103769)
  - PR2 root OSF story — merged (#153, 615af9c)
  - PR3 runtime ops — merged (#156, 63f38b1)
  - PR4a replay doc-fixes + injection-case lock — merged (#159, ecc9260)
  - PR5 weather naming — merged (#154, b09dd4a)
  - PR6 supersession — merged (#157, b08b5a0)
  - PR7 coverage/ops docs — merged (#155, 0e1c7be)
  - PR8 YAML hygiene — merged (#158, fa537c1)
  - PR1-cleanup code-side — merged (#160, e7a8906)
remaining:
  - PR4b 19-case injection generator code (~1-2 days; can ship between OSF deposit and 2026-06-01 experiment start)
  - Q2 Flux query against pjm.coincident_peak → HVAC_LOGIC.md:316-318 RTO peak distribution edit (operator task)
  - PR9 freeze-day (last; status flips + Zenodo tag + OSF open-ended registration + README badge per OSF_FILING_MECHANICS.md)
---

# Pre-OSF Documentation Audit — Execution Plan

## Context

Two parallel audits ran on 2026-05-18 (Claude + Codex) covering the active docs corpus before OSF filing target 2026-05-30. The audits surfaced drift across the binding spec, root entry-point docs, runtime ops docs, replay-validation artifacts, and four half-superseded docs. After reconciliation between the two audits, a verification pass against current code, and operator decisions on contested items, this plan captures the executable remediation as a 9-PR sequence with dependencies.

**Inputs:**
- 144 corrected drift findings (severity-sorted, see [findings doc](pre-osf-doc-audit-findings-2026-05-18.md))
- 7 operator decisions (D1-D7) + F-007 + 5 evidence-question resolutions (this doc)
- D5 truth-table verification against current code (see [truth-tables doc](pre-osf-doc-audit-truth-tables-2026-05-18.md))
- Codex audit (see [Codex audit](pre-osf-doc-audit-codex-2026-05-18.md))

**Key dates:**
- 2026-05-18 — audits ran, decisions locked, plan written (today)
- 2026-05-30 — OSF deposit target (12 days out)
- 2026-06-01 — experiment start
- 2026-11-16 — experiment end

**Binding spec:** `docs/plans/sced-rebaseline-spec-2026-05-13.md` (locks at OSF-freeze commit)
**OSF template choice:** open-ended with binding spec attached via Zenodo DOI (per D-decision)

## Operator decisions (locked)

### D1 — Arm B identity
Arm B is the existing scheduler that has been running on the Pi. Day-type classification + price overlays + capacity-risk overlays + precool deepening + safety supervisor. No thermal model. No MPC. No envelope-ODE. The thermal-model design work (`THERMAL_MODEL_DESIGN.md`, Bacher-Madsen 2011 references) is NOT a component of OSF-bound Arm B for the 2026 summer study. Treat as future-arm research design.

### D2 — Per-pair table column shape
Single reported count + internal invariant check. Specifically:
- `valid_pair_hours`: single column, meaning valid hours per side after cost-matched exclusion
- `excluded_hours_count`: single column, meaning excluded hours per side (not total across both arms)
- `excluded_hours_breakdown_a`: side-specific exclusion reasons for Arm A (split kept — reasons are genuinely asymmetric)
- `excluded_hours_breakdown_b`: side-specific exclusion reasons for Arm B (split kept)
- `cfe_c_per_kwh_a` / `cfe_c_per_kwh_b`: split kept — CFE genuinely differs by side when a pair straddles bill cycles
- Pipeline asserts `valid_hours_a == valid_hours_b` and `excluded_hours_a == excluded_hours_b` before emitting and raises on divergence (defense in depth)

Spec text on exclusion mechanism (clarification): when one arm has X invalidated hours at a given price profile, the opposite arm excludes X hours at comparable prices. Matching is price-matched, not time-index-matched. Physical excluded hours and reasons can differ by side, but counts are symmetric by construction.

### D3 — Poor-weather match flag
Drop the flag and the sensitivity entirely. Report match quality as descriptive provenance only.

Frame in the plan:
- Keep Hungarian weather matching as the pairing method
- Report match quality for every pair (weather-vector distance, temporal gap days, Ecowitt/NOAA source split)
- Do NOT classify pairs as poor/good
- Do NOT run a pre-baked "drop poor pairs" sensitivity
- State explicitly that the study is descriptive for one house / equipment setup; match-quality columns are provided for reader interpretation

This aligns with the spec's existing §9.5 framing (discovery study, no statistical decision rule).

### D4 — Refoss vs Eagle tolerance
Distribution-not-flag. Refoss-mains vs Eagle agreement is descriptive provenance only. Report overlap coverage and agreement metrics per pair / bill period. No pre-registered tolerance threshold or binary flag is applied.

Concrete fields to report:
- Eagle coverage %
- Refoss mains coverage %
- Refoss fallback usage %
- Overlapping-hours count
- Eagle kWh
- Refoss mains kWh
- Absolute difference kWh
- Percent difference vs Eagle

Eagle is the bill-canonical whole-home reference when available. Refoss mains is sanity/fallback. If agreement looks weird in the final analysis, discuss in the final narrative with the actual numbers visible.

### D5 — Half-superseded docs: per-doc actions
After truth-table verification, locked actions per doc:

- **EXPERIMENT_DESIGN.md** — Extract Appendix A → new `docs/CONTROLLER_CONSTANTS.md` (or spec annex). Extract §O2 → new `docs/O2_CAPACITY_RECONSTRUCTION.md` (co-located with `tools/o2_capacity_reconstruction/`). Strip the line 297 bootstrap-CI reference at extraction. Extract §11 ethics → new `docs/ETHICS_FRAMING.md` (Chris-approved as-written). Archive the remainder.

- **ANALYSIS_PIPELINE.md** — Rewrite §2.1 measurement catalog against current code (verification found 8 drift entries including fabricated `hvac.thermostat.running` field and fabricated `hvac.comfortnet` writer). Land corrected version into `SERVICES.md`. Archive the remainder.

- **OSF_FILING.md** — Archive. Replace with 1-page `docs/OSF_FILING_MECHANICS.md` documenting the open-ended-template workflow.

- **ARM_B_IMPLEMENTATION.md** — Tighten banner to mark §0a/§0b/§10 explicitly superseded. Fix line 466 (130 GW → 20,375/151,525 per scope). Relabel lines 122-125 as "Prior thresholds (pre-recalibration)". Defer full archive until `HVAC_LOGIC.md` absorbs a controller-layers primer citing `CONTROLLER_CONSTANTS.md`.

### D6 — Replay artifact policy
Option C: gitignore JSON and draft markdown day-to-day; commit canonical artifact at OSF-freeze commit only; `findings.md` stays canonical narrative throughout.

Operational changes:
1. Delete `docs/replay-validation/2026-05-18-shadow/validation_results.json` (stale pre-retraction)
2. Gitignore `docs/replay-validation/**/validation_results.json` and `docs/replay-validation/**/findings_draft.md`
3. At OSF freeze, run validation one final time; commit the artifact at that commit
4. Spec §11 #13 amendment: "Final validation artifact is committed at the OSF-freeze commit; intermediate runs live on GH Actions and are not committed."

### D7 — THERMAL_MODEL_DESIGN disposition
Archive (Option B), as part of PR2. All incoming references removed entirely (no breadcrumb). Preserves work for future use without surfacing it in the active corpus.

Concrete:
- Move `docs/THERMAL_MODEL_DESIGN.md` → `docs/archive/THERMAL_MODEL_DESIGN.md`
- Delete AGENTS.md:49 entry-point bullet
- Delete PROJECT.md:273 project-files table row
- Grep `THERMAL_MODEL_DESIGN` across active corpus and remove all surface references

### F-007 — Replay injection-case list
Lock the methodology at a 19-case list (curated from the pre-rebaseline draft, with 3 pre-rebaseline cases dropped, 3 new cases added, and 11 vocabulary translations from Rule-N framing to spec-§-framing). Drop the "(Stub — subject to refinement)" caveat in `REPLAY_VALIDATION.md:104`.

Final 19 cases (full list in PR4 scope below).

### OSF template choice
Open-ended template with binding spec attached via Zenodo DOI. Rationale:
- Binding spec is more detailed than the structured form would produce
- Structured form's NHST framing (hypotheses, inference criteria) contradicts the study's discovery posture (spec §9.5)
- Audience finds the study via the published paper, not via OSF discovery
- Frees the OSF entry to be a 1-page narrative + DOI link rather than 17 paraphrased form fields

### Evidence question resolutions

| Q | Resolution |
|---|---|
| Q1 Refoss range + A/B↔em:N | Device emits em:1..em:18 (verified at `refoss-poller/poller.py:5-7,17`). Study monitors 5 channels; SERVICES.md gets explicit A/B-label-to-em:N mapping table (see PR3). |
| Q2 RTO peak distribution | **Pending: Flux query against `pjm.coincident_peak` filtered to 2025 5CP hours, grouped by hour-of-day.** Blocks HVAC_LOGIC.md edits in PR3. Run before PR3 starts. |
| Q3 Deploy timing | Reframe to "single-service deploys typically complete in ~1 minute; cache misses may extend this." No stopwatch. |
| Q4 PJM catalog cadence | Add header line: "Catalog refreshed annually, or on PJM OpenAPI schema changes; last refresh: YYYY-MM-DD." |
| Q5 Loki host exposure wording | "Reachable on the homelab 192.168.20.x subnet (used by the workstation cockpit per `tools/cockpit/.env.example:9`)" — strip the made-up "Trusted VLAN 10" label. |

### Refoss A/B ↔ em:N mapping (Chris-confirmed)

Physical labels from the Refoss app side; em:N is the InfluxDB tag. SERVICES.md gets this table for unambiguity.

| Refoss app label | InfluxDB tag | Circuit | Spec role |
|---|---|---|---|
| A1 | em:1 | Mains leg A | mains-sanity subset (em:1 + em:7) per spec §10:420 |
| B1 | em:7 | Mains leg B | mains-sanity subset |
| A2 | em:2 | HVAC compressor leg A | HVAC analysis subset (em:2 + em:8 + em:9) per spec §4:121 |
| B2 | em:8 | HVAC compressor leg B | HVAC analysis subset |
| B3 | em:9 | Furnace blower / control board | HVAC analysis subset |

Other em:N channels are device-side capacity, not monitored for this study.

## Pre-PR0 — Run before any PR starts

1. **Q2 Flux query** against `pjm.coincident_peak` filtered to 2025 5CP hours, grouped by hour-of-day. Output either confirms the "4 of 5 in 16:00-17:00 CDT" claim in HVAC_LOGIC.md:316-318 or replaces it with the actual distribution. **Blocks PR3.**
2. Confirm OSF target date 2026-05-30 still holds.
3. Confirm external account readiness: Zenodo account active, OSF account active, GitHub release-tagging permissions verified.

## Dependency graph

```
[Pre-PR0: Q2 query] ─┐
                     ├─→ [PR1 Spec/Impl P0]
                     │     │
                     │     ├─→ [PR2 Root OSF Story] ─┐
                     │     ├─→ [PR3 Runtime Ops] ────┼─→ [PR8 YAML Hygiene] ─→ [PR9 Freeze-Day]
                     │     ├─→ [PR4 Replay] ─────────┤
                     │     ├─→ [PR5 Weather Naming] ─┤
                     │     ├─→ [PR6 Supersession] ───┤
                     │     └─→ [PR7 Coverage Docs] ──┘
                     └─→ (blocks PR3 specifically)
```

PR1 is the trunk. PR2-PR7 are leaves on PR1, parallelizable among themselves. PR8 (YAML hygiene) lands after PR2 + PR6 so headers match final disposition. PR9 (freeze-day) is last.

---

## PR1 — Spec/Impl P0 fixes

**Goal:** Fix the binding spec's internal contradictions and the implementation plan's placeholder/contradiction issues.

**Branch:** `pre-osf/pr1-spec-impl-p0`

**Files touched:**
- `docs/plans/sced-rebaseline-spec-2026-05-13.md`
- `docs/plans/sced-rebaseline-implementation-2026-05-13.md`

**Spec edits:**
- §4 line 109-119: add `+ carbon_free_credit_per_kwh` to HVAC$ formula (SPEC-001)
- §6 line 230: rename `w_peak` → `w_mean_daily_max_temp` (SPEC-002)
- §6: drop `poor_weather_match_flag` and percentile-method language; replace with the descriptive-provenance wording from D3
- §9 per-pair table: add `cfe_c_per_kwh_a` and `cfe_c_per_kwh_b` rows (SPEC-003); note single-column `valid_pair_hours` emitted after pipeline invariant check (D2); drop `poor_weather_match_flag` row (D3)
- §5 line 192 + §9 lines 338-340: reconcile to single `excluded_hours_count` + split `excluded_hours_breakdown_a/_b` (D2 + SPEC-004)
- §5 line 137 + §7 line 271: "4 modes" header → "5 modes" matching `HourMode` enum (SPEC-005, SPEC-006)
- §3 line 80 + §11 line 433: replace retired "dry-run/shadow/observation" vocab with "shadow / no-write" per §3 enum (SPEC-007, SPEC-008)
- §10 line 423: replace "tolerance band TBD at audit phase" with the distribution-not-flag wording from D4
- §15 line 500 first bullet: mark resolved-by-reframe per D4
- §12 line 456: drop `exclude_poor_weather_match_pairs` row (D3)
- §11 #13: add one-sentence amendment per D6 about freeze-commit artifact

**Impl plan edits:**
- Line 18: rewrite Phase 4 architecture summary to "NOAA fallback station selection only; no historical baseline pull" (F-006)
- Line 312: rewrite acceptance test to assert single `valid_pair_hours` matches expected count (IMPL-001)
- Lines 2086-2092: update `WeatherVector` snippet to 4 positional args + `(2, 4)` shape assertion (IMPL-002)
- Lines 43, 791, 2791, 3007, 3197: replace `2026-05-2X` and `2026-05-XX` placeholders with `2026-05-18` (IMPL-003)
- Lines 1435-1436: mark poor-weather amendment recommendation as resolved-by-D3 (IMPL-004)

**Acceptance criteria:**
- All P0 spec items resolved
- Spec passes self-consistency review (SPEC-001 through SPEC-008 cluster all closed)
- Impl plan placeholder dates resolved
- Impl plan acceptance tests buildable against current code shape

**Dependencies:** None. First in queue.
**Blocks:** PR2, PR3, PR4, PR5, PR6.

---

## PR2 — Root OSF Story

**Goal:** Fix Arm B identity sweep + binding-doc identity + retired-artifact retirement across reader-entry-point docs. Most reader-visible PR.

**Branch:** `pre-osf/pr2-root-osf-story`

**Files touched:**
- `PROJECT.md`
- `README.md`
- `AGENTS.md`
- `HANDOFF.md`
- `CLAUDE.md`
- Move `docs/THERMAL_MODEL_DESIGN.md` → `docs/archive/THERMAL_MODEL_DESIGN.md`

**Edits:**
- Strip "thermal-model-informed" / "Step 1 model-informed" / "envelope-ODE" framing from Arm B references across all five files (ROOT-001 through ROOT-006). Replace with spec §3 wording.
- Retire references to `randomize_arms.py`, `experiment-assignments-summer-2026.csv`, PRNG seed `20260601` (ROOT-007, ROOT-008, ROOT-009). Replace with `arm_calendar.py` per spec §2.
- Re-point "binding pre-reg doc" references from `EXPERIMENT_DESIGN.md` to `docs/plans/sced-rebaseline-spec-2026-05-13.md` (ROOT-011)
- HANDOFF.md: flip `status: live` → `status: superseded` with banner (ROOT-013, ROOT-014). Avoids risky rewrite.
- PROJECT.md service count: leave as-is (19 containers is correct).
- Drop "Arm B variant" / "model-informed" qualifiers from PROJECT.md:160, 230, 251; README.md:172 (ROOT-015, ROOT-016, ROOT-019, ROOT-020)
- Reframe PROJECT.md:272 EXPERIMENT_DESIGN.md row as historical-research-design pointer (ROOT-021)
- PROJECT.md:30: mark Zenodo DOI claim conditional (ROOT-028)
- Convert ISO-8601 dates where load-bearing (ROOT-025)

**Per D7 (THERMAL_MODEL_DESIGN archive):**
- Move file to `docs/archive/`
- Delete AGENTS.md:49 entry-point bullet
- Delete PROJECT.md:273 project-files table row
- Grep `THERMAL_MODEL_DESIGN` across active corpus; remove all surface references

**Acceptance criteria:**
- Reviewer opening PROJECT.md, README.md, or AGENTS.md finds the spec's Arm B definition
- No retired-randomization-artifact references in any root doc
- No THERMAL_MODEL_DESIGN reference in any active doc
- HANDOFF.md unambiguously marked stale or current

**Dependencies:** PR1.

---

## PR3 — Runtime Ops

**Goal:** Bring reader-facing service documentation into agreement with running code.

**Branch:** `pre-osf/pr3-runtime-ops`

**Files touched:**
- `docs/SERVICES.md`
- `docs/INFLUXDB_RETENTION.md`
- `docs/PJM_DM2_INTEGRATION.md`
- `docs/PJM_DM2_FEEDS.md`
- `docs/SCHEDULER_TIMING.md`
- `docs/HVAC_LOGIC.md` (after Q2 query)
- `docs/ARM_TRANSITIONS.md`
- `docs/DRY_RUN_VALIDATION.md`
- `deploy/energy-stack/.env.example`
- `deploy/energy-stack/README.md`
- `tools/cockpit/README.md`

**Edits:**
- SERVICES.md:322 — replace `SCHEDULER_DRY_RUN` row with `SCHEDULER_MODE` (DOC-001)
- SERVICES.md:276 + INFLUXDB_RETENTION.md:39 + PJM_DM2_INTEGRATION.md:45 — `hrl_load_metered` cadence "Sundays 02:00" → "hourly, 5d lookback"; add `hrl_load_metered_rto` row (DOC-002, DOC-005, DOC-006)
- SERVICES.md:254 — nws-poller `forecastHourly` → `forecastGridData` (DOC-003)
- INFLUXDB_RETENTION.md:22-43 — add `ecowitt.weather` row (DOC-007)
- SERVICES.md:220 — Refoss row rewritten per the A/B ↔ em:N mapping table above
- SERVICES.md:200 — cross-link ComEd day-ahead note to pjm-dm2-poller section (DOC-046)
- HVAC_LOGIC.md — fix DAYTYPE_HOT vs HOT naming via single alias (DOC-011)
- HVAC_LOGIC.md:316-318 — update RTO peak distribution per Q2 Flux query results (BLOCKER: Q2 must run first)
- SCHEDULER_TIMING.md:43 — annotate DTOD rates as "base rates (sensitivity)" or update to spec §8 resultant rates (DOC-013)
- SCHEDULER_TIMING.md:67, :123 — "Recent fix (PR #121)" → "Per PR #121 (merged)" (DOC-019, DOC-020)
- PJM_DM2_FEEDS.md:3 — add cadence header line per Q4 wording
- PJM_DM2_INTEGRATION.md — add RTO companions to schema/sequencing tables (DOC-023)
- `.env.example:33` — `PJM_DM2_POLL_INTERVAL=3600` → `300` (F-005)
- ARM_TRANSITIONS.md banner — `active` → `experiment` for SCHEDULER_MODE value (CQ-V)
- DRY_RUN_VALIDATION.md body — SCHEDULER_DRY_RUN → SCHEDULER_MODE wording (DOC-017)
- `deploy/energy-stack/README.md:3` — "~14 pollers/services" → "16 always-on + 3 mqtt-profile = 19 services" (F-011)
- `deploy/energy-stack/README.md:92` — rewrite "every Dockerfile only `COPY app.py`" claim (OPS-003)
- `deploy/energy-stack/README.md:116-130` — add scripts/ test suite to coverage list (OPS-004)
- `deploy/energy-stack/README.md:139` — Loki exposure wording per Q5 + OPS-017
- `deploy/energy-stack/README.md:35` — deploy timing per Q3 / OPS-013
- `tools/cockpit/README.md:20` — Pi-lab env path `/energy-proxy` → `/energy-stack` (OPS-001)
- `tools/cockpit/README.md:31` — "two visible pwsh windows" → "hidden background processes" (OPS-002)
- `tools/cockpit/README.md:103` — uvicorn-manual COCKPIT_BACKEND_MODE note (OPS-009)

**Acceptance criteria:**
- Every operational claim traces to current code behavior
- Operator copying `.env.example` to `.env` and deploying gets a working stack
- Cockpit doc gets you to a working dashboard
- Q2 Flux query results incorporated into HVAC_LOGIC.md before merge

**Dependencies:** PR1 (spec text), Pre-PR0 Q2 query (HVAC_LOGIC.md edits).

---

## PR4 — Replay Validation

**Goal:** Fix stale artifacts, ship injection-case generator code, lock methodology.

**Branch:** `pre-osf/pr4-replay-validation`

**Files touched:**
- `docs/replay-validation/2026-05-18-shadow/validation_results.json` (delete)
- `docs/replay-validation/2026-05-18-shadow/findings_draft.md` (delete)
- `docs/replay-validation/2026-05-18-shadow/findings.md` (edit)
- `docs/replay-validation/2026-05-12/README.md` (edit)
- `docs/replay-validation/2026-05-12-stage8-complete/README.md` (edit)
- `docs/REPLAY_VALIDATION.md` (edit)
- `.gitignore` (new entries)
- `tools/analysis/replay/` (new generator code)

**Edits per D6:**
- Delete stale `validation_results.json` (RV-001)
- Delete stale `findings_draft.md` (RV-002, RV-003, RV-010)
- Add gitignore entries for `docs/replay-validation/**/validation_results.json` and `docs/replay-validation/**/findings_draft.md`
- `findings.md`: fix LIM-0/LIM-2/LIM-4 to drop retracted-OI-1 "outdoor_*-canonical" framing (RV-004, RV-005, RV-006)
- `findings.md`: tag OI-2 with `Status: informational, open` (RV-007)
- `findings.md` line 225: fix memory-name reference (RV-018)
- 2026-05-12 + 2026-05-12-stage8-complete READMEs: reciprocal cross-references + scope banners (RV-008, RV-009, RV-015, RV-016)

**Edits per F-007 (lock 19-case list):**

`REPLAY_VALIDATION.md:104` — replace stub caveat with the locked list:

Bad-data / quality-rule coverage:
1. Missing weather rows (NWS fallback)
2. Duplicate timestamps
3. Partial price hours
4. Stale price feed (≥6 consecutive same value)
5. Scheduler outages (≥5-min gap in 5cp_state + actions)
6. Refoss channel gaps (Tier 1-4)
7. Detector false positive (holding at non-5CP hour)
8. Detector false negative (non-holding at published 5CP hour)
9. Injected published 5CP hour list (post-summer truth proxy)
10. Bill window with no comed.bill entry
11. Bill window with one comed.bill entry
12. CT-slip-like Refoss channel
13. Manual operational override
14. Synthetic arm transition on 2026-06-01
15. Successful verification action within 6h
16. Failure to verify
17. Missed/late arm switch (verify affected hours classified as not-fully-valid)
18. Arm period at exactly 259 valid hours (validity gate boundary)
19. Arm period that fails the ≥259 gate (verify drop-from-matching + descriptive publication)

Dropped (pre-rebaseline framing, no translation): old Cases 17 (10% imputed kWh threshold), 18 (20% imputed price hours threshold), 21 (tiered imputation cap).

**Code changes:**
- `tools/analysis/replay/` — generator code for 19 injection cases. Most trivial (fixture row generation). Estimated 1-2 days.

**Acceptance criteria:**
- All 19 cases have generator code that produces fixture rows
- `findings.md` internally consistent (no retracted-OI-1 contradictions)
- Repo no longer carries pre-retraction artifacts
- REPLAY_VALIDATION.md no longer carries "Stub" caveat
- Methodology lockable as-written at OSF freeze

**Dependencies:** PR1.

---

## PR5 — Weather Naming

**Goal:** Enforce canonical naming across docs and fixtures.

**Branch:** `pre-osf/pr5-weather-naming`

**Files touched:**
- `docs/archive/THERMAL_MODEL_DESIGN.md` (if PR2 archived) or `docs/THERMAL_MODEL_DESIGN.md` (if not yet archived)
- Any fixture files in `tools/analysis/tests/` referencing `weather.ecowitt`
- Any remaining docs referencing `outdoor_*` as analysis-canonical

**Edits:**
- THERMAL_MODEL_DESIGN.md:5, 105 — `weather.ecowitt` → `ecowitt.weather` (DOC-051)
- Grep `weather\.ecowitt` across repo; fix all instances
- Grep for any `outdoor_*` claimed as analysis-canonical (vs descriptive); reconcile to `ch1_*` per spec §6:209

**Acceptance criteria:**
- No active doc references `weather.ecowitt` as a measurement name
- No active doc presents `outdoor_*` as analysis-canonical (descriptive references in spec §6 OK)

**Dependencies:** PR1, PR2.

---

## PR6 — Supersession

**Goal:** Implement D5 doc-by-doc actions. Extract retained content; archive; tighten banners.

**Branch:** `pre-osf/pr6-supersession`

**Files touched:**
- `docs/EXPERIMENT_DESIGN.md` (extract content, then archive)
- `docs/CONTROLLER_CONSTANTS.md` (new)
- `docs/O2_CAPACITY_RECONSTRUCTION.md` (new)
- `docs/ETHICS_FRAMING.md` (new)
- `docs/ANALYSIS_PIPELINE.md` (archive)
- `docs/SERVICES.md` (absorb corrected §2.1 measurement catalog)
- `docs/OSF_FILING.md` (archive)
- `docs/OSF_FILING_MECHANICS.md` (new)
- `docs/ARM_B_IMPLEMENTATION.md` (tighten banner + fixes)
- `docs/archive/EXPERIMENT_DESIGN.md` (move target)
- `docs/archive/ANALYSIS_PIPELINE.md` (move target)
- `docs/archive/OSF_FILING.md` (move target)
- `tools/analysis/pipeline.py` (docstring fix)

**Edits per D5:**

EXPERIMENT_DESIGN.md:
- Extract Appendix A → `docs/CONTROLLER_CONSTANTS.md` verbatim (17/17 verified matches per truth-table)
- Extract §O2 → `docs/O2_CAPACITY_RECONSTRUCTION.md`. Strip line 297 bootstrap-CI reference at extraction.
- Extract §11 → `docs/ETHICS_FRAMING.md` verbatim
- Archive remainder

ANALYSIS_PIPELINE.md:
- Rewrite §2.1 against current code (correct all 8 verified drift entries from truth-table). Land into SERVICES.md.
- Archive remainder

OSF_FILING.md:
- Replace with 1-page `docs/OSF_FILING_MECHANICS.md`:
  1. Tag repo at freeze commit
  2. Generate Zenodo DOI
  3. Create OSF open-ended registration
  4. Narrative content with Zenodo DOI link
  5. Submit + 48-hour approval
  6. Update README badge
- Archive original

ARM_B_IMPLEMENTATION.md:
- Fix line 466: 130,000 MW → "scope-aware fallbacks: 20,375 MW (ComEd-zone) and 151,525 MW (RTO)" (DOC-004)
- Relabel lines 122-125 from "Current thresholds" to "Prior thresholds (pre-recalibration)"
- Tighten banner: §0a/§0b → "completed migrations, historical"; §10 → "superseded by spec §11"
- Defer full archive to follow-up

tools/analysis/pipeline.py:3:
- "Binding contract: docs/ANALYSIS_PIPELINE.md" → "Deprecated. Binding contract: docs/plans/sced-rebaseline-spec-2026-05-13.md; current pipeline at tools/analysis/arm_period_pipeline.py" (F-009)

**Acceptance criteria:**
- Top-level `docs/` contains only current binding or extraction-target content
- Archive contains the four superseded docs (3 full archives + ARM_B_IMPLEMENTATION still in place with tighter banner)
- All cross-references point at extracted destinations OR archive paths OR are removed
- pipeline.py docstring matches reality

**Dependencies:** PR1, PR5.

---

## PR7 — Coverage / Ops Docs

**Goal:** Close coverage gaps in operational docs. Small PR.

**Branch:** `pre-osf/pr7-coverage-ops`

**Files touched:**
- `docs/COMFORTNET_USE_CASES.md`
- `deploy/energy-stack/scripts/README.md`
- `deploy/energy-stack/backup/RESTORE.md`

**Edits:**
- COMFORTNET_USE_CASES.md:5 — update status from "pre-integration" to shipped per SERVICES.md:545 (DOC-012)
- scripts/README.md — add sections for `commission_decision_trace_path_c.py` and `log_arm_transition.py` (OPS-005)
- backup/RESTORE.md — add verification step at end of restore procedure (OPS-020)

**Acceptance criteria:**
- ComfortNet status reflects shipped state
- All scripts under `deploy/energy-stack/scripts/` documented
- Restore procedure has a verification gate

**Dependencies:** None substantive. Parallel with PR2-PR6.

---

## PR8 — YAML / Status Hygiene

**Goal:** Mechanical sweep. Add missing YAML headers per AGENTS.md doc-hygiene Layer 1.

**Branch:** `pre-osf/pr8-yaml-hygiene`

**Files touched (18 total):**

13 active docs in `docs/` missing headers (THERMAL_MODEL_DESIGN moves to archive in PR2 — 1 less):
- ANALYSIS_PIPELINE, ARM_B_IMPLEMENTATION, ARM_TRANSITIONS, COMFORTNET_USE_CASES, DRY_RUN_VALIDATION, EXPERIMENT_DESIGN, HVAC_LOGIC, INFLUXDB_RETENTION, OSF_FILING, PJM_DM2_FEEDS, PJM_DM2_INTEGRATION, REPLAY_VALIDATION, SERVICES

3 ops docs missing headers:
- `deploy/energy-stack/README.md` (OPS-006)
- `deploy/energy-stack/scripts/README.md` (OPS-007)
- `deploy/energy-stack/backup/RESTORE.md` (OPS-008)

1 replay-validation doc header normalization (RV-014)

**Each header carries:** `date`, `owner`, `status`, `role-label` per AGENTS.md Doc hygiene Layer 1.

**Status vocabulary:** standardize on `active` / `superseded` / `archived` / `locked`. Replace bespoke statuses (`live`, `path-c-complete-b1-pending`).

**Acceptance criteria:**
- Every doc in scope has a complete YAML header
- All statuses use the canonical vocabulary

**Dependencies:** Land after PR2 + PR6 so headers match each doc's final disposition.

---

## PR9 — Freeze-Day Checklist

**Goal:** Land AT the OSF-deposit commit. Status flips + artifact captures.

**Branch:** `pre-osf/pr9-freeze-day`

**Files touched:**
- `docs/plans/sced-rebaseline-spec-2026-05-13.md` (header)
- `docs/plans/sced-rebaseline-implementation-2026-05-13.md` (header)
- `docs/replay-validation/<freeze-date>-shadow/validation_results.json` (one-time commit per D6)
- `README.md` (OSF link + badge)
- New: `CITATION.cff` or equivalent for Zenodo

**Operations:**
1. Run shadow validation one final time. Commit artifact at the freeze commit (per D6).
2. Flip spec header `status: draft` → `status: frozen`; add `frozen_at_commit: <SHA>` (SPEC-010, SPEC-018)
3. Flip impl header `status: in-progress` → `status: phases-1-6-complete-phase-7-deferred` (IMPL-006)
4. Confirm `arm_calendar.py` hash recorded in the freeze commit
5. Tag repo at freeze commit
6. Generate Zenodo DOI for the tagged release
7. Create OSF open-ended registration per OSF_FILING_MECHANICS.md
8. Submit OSF registration; await 48-hour auto-approve
9. Update README with OSF link + pre-reg badge
10. Optional: post-filing comms (your call on scope)

**Acceptance criteria:**
- Spec + impl plan have `status: frozen` headers
- Repo tag exists at freeze commit
- Zenodo DOI exists
- OSF registration submitted
- README badge + link live

**Dependencies:** All prior PRs landed.

---

## Status tracking

| PR | Branch | Status | Merged commit | PR # | Notes |
|---|---|---|---|---|---|
| Pre-PR0 | — | pending | — | — | Q2 Flux query before HVAC_LOGIC.md:316-318 follow-up edit |
| PR1 Spec/Impl P0 | pre-osf/pr1-spec-impl-p0 | merged | 7103769 | #152 | Trunk; resolved 12 P0 + 5 P1 |
| PR2 Root OSF Story | pre-osf/pr2-root-osf-story | merged | 615af9c | #153 | Includes THERMAL_MODEL archive |
| PR3 Runtime Ops | pre-osf/pr3-runtime-ops | merged | 63f38b1 | #156 | HVAC_LOGIC RTO-peak edit still pending Q2 |
| PR4a Replay doc-fixes | pre-osf/pr4a-replay-doc-fixes | merged | ecc9260 | #159 | Methodology lock + stale-artifact cleanup |
| PR4b Replay generator code | — | pending | — | — | 19-case generator; can ship between OSF deposit and 2026-06-01 |
| PR5 Weather Naming | pre-osf/pr5-weather-naming | merged | b09dd4a | #154 | |
| PR6 Supersession | pre-osf/pr6-supersession | merged | b08b5a0 | #157 | 3 archives + 4 new top-level docs |
| PR7 Coverage Docs | pre-osf/pr7-coverage-ops | merged | 0e1c7be | #155 | |
| PR8 YAML Hygiene | pre-osf/pr8-yaml-hygiene | merged | fa537c1 | #158 | Headers + status vocab |
| PR1-cleanup code-side | pre-osf/pr1-cleanup-spec-amendment-code | merged | e7a8906 | #160 | Removed caliper_p90_distance + redundant valid_pair_hours_a/_b |
| PR9 Freeze-Day | pre-osf/pr9-freeze-day | pending | — | — | OSF deposit commit; last |

## Post-freeze cleanup

Items deferred to post-freeze (non-blocking):
- ARM_B_IMPLEMENTATION.md full archive (after HVAC_LOGIC.md absorbs controller-layers primer citing CONTROLLER_CONSTANTS.md)
- Optional README badge + post-filing comms scope decisions
- ANALYSIS_PIPELINE.md full archive if OSF_FILING_MECHANICS criterion 12 detangled cleanly

## Items NOT in any PR (orphan check — verified clean)

All 144 corrected findings + 7 D-decisions + F-007 + 5 evidence answers mapped to a PR home. No orphans.

The two items that span multiple PRs:
- SCHEDULER_MODE / SCHEDULER_DRY_RUN rename: SERVICES.md (PR3) + ARM_TRANSITIONS.md (PR3) + DRY_RUN_VALIDATION.md (PR3) + impl plan task line 70 (PR1)
- Refoss A/B ↔ em:N mapping table: SERVICES.md only (PR3); surface in CONTROLLER_CONSTANTS.md if useful (PR6)
