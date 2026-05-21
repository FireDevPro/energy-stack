---
name: pre-osf-doc-audit-findings-2026-05-18
date: 2026-05-18
owner: chris
status: active
role-label: audit-evidence
related:
  - pre-osf-doc-audit-execution-2026-05-18.md
  - pre-osf-doc-audit-truth-tables-2026-05-18.md
  - pre-osf-doc-audit-codex-2026-05-18.md
---

# Pre-OSF Doc Audit — Consolidated Findings (Claude)

Evidence-bearing companion to the execution plan. Lists every drift finding surfaced by the Claude audit, with severity, confidence, file:line, evidence, and recommended fix. Use this file as the audit-trail-of-record for any individual finding.

The Codex audit is captured separately in [`pre-osf-doc-audit-codex-2026-05-18.md`](pre-osf-doc-audit-codex-2026-05-18.md). The reconciled accounting (which findings from each audit are valid, duplicated, contradicted, or net-new) is summarized in the "Reconciliation summary" section below.

## Summary

**Total findings (after corrections): 144** — 12 P0, 37 P1, ~48 P2, ~47 P3.

**P0 drivers:**
- Arm B identity drift across root entry-point docs (6 items)
- Binding spec internal contradictions (4 items: CFE formula, w_peak, CFE per-pair columns, excluded-hour schema)
- Spec-vs-impl drift (2 items: per-pair column shape, weather vector dimensionality)

**P1 drivers:**
- Retired-artifact references across PROJECT / README / HANDOFF (8 items)
- Operational docs lagging code (SERVICES env var + cadences + endpoint; 6 items)
- Stale replay-validation artifacts (2 items: JSON, draft markdown)
- Cockpit + Dockerfile claim drift (3 items)
- Codex-only net-new items (5 items: spec TBD tolerance, .env.example cadence, impl plan Phase 4 summary, REPLAY_VALIDATION stub, pipeline.py binding docstring)

**Adjustments to original 156-finding count:**
- Dropped 10 items: 8 "Recommended fix: None / Acceptable" verifications + 1 self-corrected duplicate (SPEC-016) + 1 contradicted-by-repo (OPS-021 HAVEN_TOKEN_FILE)
- Reversed direction on ROOT-012 (compose count 19 is correct; the deploy README ~14 is the drift)
- Demoted to operator-checklist (not drift findings): SPEC-010, SPEC-012, SPEC-018
- Recategorized as open evidence questions (not defects yet): DOC-014, DOC-045, DOC-047, OPS-013
- Reframed OPS-017 (strip the "Trusted VLAN 10" naming, keep the contradiction)
- Added 5 Codex-only items: F-002, F-005, F-006, F-007, F-009

## Canonicality questions

Where sources conflict, the question is raised but no winner is picked. Operator decisions (D1-D7, F-007) resolve these — see execution plan.

### CQ-A — Arm B identity (resolved by D1)
- **Spec §3 line 81:** `B | hvac-scheduler active. Day-type classification + price overlays + capacity-risk overlays + precool deepening + safety supervisor.` No thermal-model text anywhere.
- **PROJECT.md:29, 235, 244, 273; README.md:12, 163; AGENTS.md:14:** "thermal-model-informed", "Step 1 model-informed", "envelope-ODE", "Bacher-Madsen 2011".

### CQ-B — Arm calendar shape (resolved by D1+PR2)
- **Spec §2 lines 40-42:** 12 arms × 14 days, 6A/6B, deterministic alternation, no PRNG.
- **PROJECT.md:274; README.md:164:** 18 weeks × 7 days, 9A/9B, switch at "week boundary", reads CSV.

### CQ-C — Is `experiment-assignments-summer-2026.csv` / `randomize_arms.py` live?
- **Spec §0 + EXPERIMENT_DESIGN.md banner:** retired (no PRNG seed, no CSV, no 4-week blocks).
- **PROJECT.md:235, 245, 274, 275; README.md:164; ARM_B_IMPLEMENTATION.md:556; OSF_FILING.md:56-60:** cited as live binding artifacts.

### CQ-D — Pre-registration binding document
- **EXPERIMENT_DESIGN.md:3-12 banner:** declares its OSF-binding analysis content as SUPERSEDED.
- **README.md:12, 161; PROJECT.md:246, 272:** still cites EXPERIMENT_DESIGN.md as "the pre-registration draft".

### CQ-E — HVAC$ formula includes CFE? (resolved by D1+PR1+SPEC-001)
- **Spec §4 lines 109-119:** formula lists LMP/10 + PEA + transmission + misc + DTOD + IEDT + variable_riders. No CFE.
- **Spec §8 line 313 + §10 line 427 + Impl §3.4/§3.8 lines 1820-1857, 2448:** CFE applied as credit; `carbon_free_credit_c_per_kwh` is a `HourlyRateInputs` field.

### CQ-F — Weather vector dimensionality (resolved by IMPL-002+PR1)
- **Spec §6 lines 213-220, 230 + landed code:** 4 components.
- **Impl §3.6 lines 2086-2092:** `WeatherVector(100, 1000, 5, 80, 60)` with 5 positional args; `assert z.shape == (2, 5)`.

### CQ-G — Sensitivity weight name (resolved by SPEC-002+PR1)
- **Spec §6:230:** `w_peak`. No component named "peak" in the 4-tuple.
- **Spec §12:454:** `w_mean_daily_max_temp`. Name maps to a real component.

### CQ-H — Per-pair `valid_pair_hours` column shape (resolved by D2+PR1)
- **Spec §9:337:** single column.
- **Impl test:312:** asserts `valid_pair_hours_a == valid_pair_hours_b` against columns spec doesn't define.

### CQ-I — Per-pair `cfe_c_per_kwh_a/_b` columns (resolved by SPEC-003+PR1)
- **Spec §9 lines 322-359:** table omits them.
- **Spec §10:427 + Impl test:324:** require them.

### CQ-J — Excluded-hours provenance schema (resolved by D2+PR1)
- **Spec §5:192:** `excluded_hours_a_count` / `excluded_hours_b_count`.
- **Spec §9:338-340:** single `excluded_hours_count` + `excluded_hours_breakdown_a/_b`.

### CQ-K — Mode count "4 modes" header (resolved by SPEC-005+SPEC-006+PR1)
- **Spec §5:137 + §7:271:** "4 modes" header.
- **Same table body + `HourMode` enum:** 5 members.

### CQ-L — `SCHEDULER_DRY_RUN` env var status (resolved by DOC-001+PR3)
- **Spec §3 + Impl line 70 + code (`app.py:644-662`):** retired; replaced by `SCHEDULER_MODE`; fails closed on invalid.
- **docs/SERVICES.md:322:** still documents `SCHEDULER_DRY_RUN`.

### CQ-M — `hrl_load_metered` poll cadence (resolved by DOC-002+PR3)
- **Code (`pjm_dm2_poller/app.py:203`):** hourly.
- **docs/SERVICES.md:276, INFLUXDB_RETENTION.md:39, PJM_DM2_INTEGRATION.md:45:** Sundays 02:00 weekly.

### CQ-N — NWS poller forecast endpoint (resolved by DOC-003+PR3)
- **Code (`nws-poller/app.py:7`):** `forecastGridData` (migrated).
- **docs/SERVICES.md:254, THERMAL_MODEL_DESIGN.md:104:** `forecastHourly`.

### CQ-O — Pre-season 5CP fallback constant (resolved by DOC-004+PR6)
- **EXPERIMENT_DESIGN.md:582 (locked Appendix A) + code (`pjm_5cp.py`):** scope-aware (20,375 MW ComEd-zone, 151,525 MW RTO). Notes "Replaces a prior 130,000 MW value that was RTO-scale misapplied".
- **docs/ARM_B_IMPLEMENTATION.md:466, 677:** still cites 130,000 MW.

### CQ-P — Day type enum naming
- **Spec / EXPERIMENT_DESIGN Appendix A / ARM_B_IMPLEMENTATION:** `HOT`.
- **Code + docs/HVAC_LOGIC.md:94, 128:** `HOT_5CP_RISK` (code-internal constant).

### CQ-Q — Cockpit Pi-lab env file path (resolved by OPS-001+PR3)
- **tools/cockpit/README.md:20:** `/home/chris/energy-proxy/.env`.
- **deploy/energy-stack/README.md:46, backup/RESTORE.md:7, deploy.yml:84:** `~/energy-stack/.env`.

### CQ-R — Cockpit PowerShell window style (resolved by OPS-002+PR3)
- **tools/cockpit/README.md:31:** "two visible pwsh windows".
- **start-cockpit.ps1:94,97:** `-WindowStyle Hidden`.

### CQ-S — Dockerfile COPY claim (resolved by OPS-003+PR3)
- **deploy/energy-stack/README.md:92:** "every Dockerfile only `COPY app.py .`".
- **hvac-scheduler/Dockerfile:10:** 7-file COPY. **pjm_dm2_poller/Dockerfile:10:** 2-file COPY.

### CQ-T — `validation_results.json` provenance (resolved by D6+RV-001+PR4)
- **docs/replay-validation/2026-05-18-shadow/findings.md:39:** canonical is GitHub Actions run `26056757401` at SHA `4ad147e`.
- **Committed JSON at the same path:** pre-retraction artifact with retracted reason_code and em:2+em:8-only framing. Not the cited artifact.

### CQ-U — `findings_draft.md` vs `findings.md` (resolved by D6+RV-002+PR4)
- Three independent signals (YAML header, workflow shape, content freshness) say `findings.md` is canonical and `findings_draft.md` is stale runner-template embedding pre-retraction OI-1 framing.

### CQ-V — ARM_TRANSITIONS / DRY_RUN_VALIDATION banner mode-name (resolved by PR3 scope addition)
- **Both banners:** `SCHEDULER_MODE` values are `shadow` for Arm A, **`active`** for Arm B.
- **Spec §3 + code (`app.py:641`):** `VALID_SCHEDULER_MODES = ("shadow", "experiment", "production")`. `active` is not a valid value.

---

## Findings table (severity-sorted)

### P0 (12 items)

| id | conf | file:line | issue | recommended fix |
|---|---|---|---|---|
| ROOT-001 | high | PROJECT.md:29 | Arm B mis-described as "model-informed" / Bacher-Madsen 2011 grounded | Replace with spec §3 wording: "scheduler-active (RTP+DTOD+5CP-risk-aware RBC, safety supervisor)" |
| ROOT-002 | high | PROJECT.md:235 | Phase 11 calls Arm B "RBC + Step 1 model-informed" | Replace with "scheduler-active" |
| ROOT-003 | high | PROJECT.md:244 | "Arm B controller (Step 1 model-informed) critical path" lists 3 envelope-ODE integration points not in binding spec | Replace with spec §11 pre-OSF dependencies |
| ROOT-004 | high | README.md:12 | README headline frames Arm B as "thermal-model-informed" | Reword per spec §3 wording |
| ROOT-005 | high | README.md:163 | Critical-path section claims 3 envelope-ODE integration points | Replace with spec §11 critical-path items |
| ROOT-006 | high | AGENTS.md:14 | One-line description: "Arm A baseline RBC vs Arm B thermal-model-informed controller" | Reword to "CTK04AE programmed schedule vs hvac-scheduler active" |
| SPEC-001 | high | sced-rebaseline-spec-2026-05-13.md:109-119 | §4 HVAC$ formula omits CFE credit; §8:313 + §10:427 + impl all require it | Add `+ carbon_free_credit_per_kwh` to §4 formula |
| SPEC-002 | high | sced-rebaseline-spec-2026-05-13.md:230 | `w_peak` names non-existent vector component; §12:454 uses `w_mean_daily_max_temp` | Rename §6:230 to `w_mean_daily_max_temp` |
| SPEC-003 | high | sced-rebaseline-spec-2026-05-13.md:322-359 vs 427 | §9 per-pair table omits `cfe_c_per_kwh_a/_b`; §10 + impl require them | Add rows to §9 table |
| SPEC-004 | high | sced-rebaseline-spec-2026-05-13.md:192 vs 338-340 | Excluded-hours provenance schema inconsistent between §5 and §9 | Reconcile to §9 single-count + breakdowns (per D2) |
| IMPL-001 | high | sced-rebaseline-implementation-2026-05-13.md:312 | Acceptance test asserts `valid_pair_hours_a == valid_pair_hours_b` against undefined columns | Rewrite test for single-column assertion (per D2) |
| IMPL-002 | high | sced-rebaseline-implementation-2026-05-13.md:2086-2092 | `WeatherVector(100, 1000, 5, 80, 60)` with 5 args + `(2, 5)` shape contradicts 4-component spec lock | Update snippet to 4 components |

### P1 (37 items)

| id | conf | file:line | issue | recommended fix |
|---|---|---|---|---|
| ROOT-007 | high | PROJECT.md:274 | `experiment-assignments-summer-2026.csv` listed as live; retired by spec §0 | Remove or annotate as superseded |
| ROOT-008 | high | PROJECT.md:275 | `randomize_arms.py` listed as live artifact | Move under "superseded" |
| ROOT-009 | high | PROJECT.md:245 | Known-Follow-up tells future agents to read CSV at "week boundary" | Rewrite per spec §11 (SCHEDULER_MODE=experiment + arm_calendar.py) |
| ROOT-010 | high | README.md:164 | Same CSV/week-boundary critical-path text | Same fix |
| ROOT-011 | high | PROJECT.md:246 | Identifies EXPERIMENT_DESIGN.md as binding pre-reg doc; that doc has self-superseded | Point to docs/plans/sced-rebaseline-spec-2026-05-13.md |
| ROOT-013 | high | HANDOFF.md:14-23 | Stale by 6 days; references non-existent branch and pre-rebaseline PRs | Banner with `status: superseded` or rewrite |
| ROOT-014 | high | HANDOFF.md:26-56 | Entire "Locked priority queue" section pre-rebaseline | Banner or rewrite |
| SPEC-005 | high | sced-rebaseline-spec-2026-05-13.md:137 | "4 modes" header but table has 5 rows | Change to "5 modes" matching `HourMode` enum |
| SPEC-006 | high | sced-rebaseline-spec-2026-05-13.md:271 | "4 modes" list of 5 items | Fix count to 5 |
| SPEC-007 | med | sced-rebaseline-spec-2026-05-13.md:80 | Retired "dry-run/shadow/observation" vocab | Replace with "shadow-mode" per §3 |
| SPEC-008 | med | sced-rebaseline-spec-2026-05-13.md:433 | §11 #1 "Arm A dry-run/shadow only" leak | Replace with "shadow / no-write only per §3" |
| IMPL-003 | high | sced-rebaseline-implementation-2026-05-13.md:43,791,2791,3007,3197 | Placeholder paths `2026-05-2X-shadow` and `2026-05-XX-noaa-fallback-station-selection` | Replace with `2026-05-18` |
| IMPL-004 | high | sced-rebaseline-implementation-2026-05-13.md:1435-1436 | Phase 3 records pre-OSF spec-amendment request for poor-weather caliper; not actioned | Resolve via D3 (drop flag entirely); mark amendment resolved-by-D3 |
| IMPL-005 | med | sced-rebaseline-implementation-2026-05-13.md:74 | Codex adversarial review PARKED; subsequent phases shipped without it | Operator decision: rerun on freeze commit or document waiver |
| DOC-001 | high | docs/SERVICES.md:322 | `SCHEDULER_DRY_RUN` documented; code requires `SCHEDULER_MODE` (fails closed) | Replace with `SCHEDULER_MODE` row |
| DOC-002 | high | docs/SERVICES.md:276 | `hrl_load_metered` cadence "Sundays 02:00"; code runs hourly | Update to "hourly, 5d lookback"; add RTO companion |
| DOC-003 | high | docs/SERVICES.md:254 | nws-poller documented as `forecastHourly`; code uses `forecastGridData` | Update to `forecastGridData` |
| DOC-004 | high | docs/ARM_B_IMPLEMENTATION.md:466 | Pre-season fallback "130,000 MW"; locked Appendix A is per-scope (20,375 / 151,525) | Update §3 unit-test bullet + line 677 example |
| DOC-005 | high | docs/INFLUXDB_RETENTION.md:39 | `pjm.metered_load` cadence "weekly" | Update to "hourly" |
| DOC-006 | high | docs/PJM_DM2_INTEGRATION.md:45 | `hrl_load_metered` "Weekly (Sundays, 02:00 CT)" | Update cadence; add RTO companion |
| DOC-007 | high | docs/INFLUXDB_RETENTION.md:22-43 | `ecowitt.weather` missing from source-inventory; it's spec §6 primary weather source | Add row (60s cadence → 1-min mean+max longterm) |
| DOC-008 | high | docs/EXPERIMENT_DESIGN.md (whole) | Banner correct but body still cites `randomize_arms.py`, PRNG seed `20260601`, assignment CSV | Section-level callouts or per-§ retire-stamps |
| DOC-009 | high | docs/OSF_FILING.md:56-60,233-235 | Pre-flight criterion 7 requires regenerating assignment CSV via retired `randomize_arms.py` | Replace with `arm_calendar.py` hash check (or archive per D5) |
| DOC-010 | high | docs/EXPERIMENT_DESIGN.md:336,99 | §7 SAP uses Mahalanobis against ERA5 baseline; spec §6 replaces with Hungarian on within-sample-z 4-component vector | Per-section retire-stamp |
| RV-001 | high | docs/replay-validation/2026-05-18-shadow/validation_results.json:16,89 | Committed JSON is pre-retraction (reason_code `ecowitt_shaded_channel_unset`, HVAC em:2+em:8 only) | Delete (per D6); commit canonical at freeze-commit only |
| RV-002 | high | docs/replay-validation/2026-05-18-shadow/findings_draft.md:19,28,89 | Stale draft embeds retracted OI-1 framing | Delete (per D6) |
| OPS-001 | high | tools/cockpit/README.md:20 | Wrong Pi-lab env path `/home/chris/energy-proxy/.env`; canonical is `~/energy-stack/.env` | Update path |
| OPS-002 | high | tools/cockpit/README.md:31 | "Two visible pwsh windows" — launcher uses `-WindowStyle Hidden`; logs go to files | Rewrite per actual behavior |
| OPS-003 | high | deploy/energy-stack/README.md:92 | Claims "every Dockerfile only `COPY app.py .`" — false for hvac-scheduler (7 files) and pjm-dm2-poller (2 files) | Rewrite to "Dockerfiles `COPY` production source modules; test files excluded by not being listed" |
| F-002 | med | docs/plans/sced-rebaseline-spec-2026-05-13.md:423,500 | Spec still has TBD Refoss/Eagle tolerance | Resolve via D4 (distribution-not-flag); reframes the TBD |
| F-005 | high | deploy/energy-stack/.env.example:33 | `PJM_DM2_POLL_INTERVAL=3600`; compose default + code is 300 | Change to 300 |
| F-006 | med | docs/plans/sced-rebaseline-implementation-2026-05-13.md:18 | Architecture summary says Phase 4 pulls historical baseline; contradicted by :2780,2784 | Rewrite phase summary |
| F-007 | med | docs/REPLAY_VALIDATION.md:6,104,151 | "Locked methodology" with stub injection-case list; audit invariant #6 makes it binding | Lock 19-case list per execution plan PR4 |
| ROOT-012 (reversed) | high | deploy/energy-stack/README.md:3 | "~14 pollers/services" — actual is 19 (16 always-on + 3 mqtt) | Update count to 19 |
| OPS-017 (reframed) | med | deploy/energy-stack/README.md:139 | "Loki Pi-lab localhost only" — actually port 3100 host-exposed | Clarify "reachable on homelab 192.168.20.x subnet" |
| F-009 | med | tools/analysis/pipeline.py:3 | Docstring still says old ANALYSIS_PIPELINE.md is binding | Update to point at rebaseline spec / arm_period_pipeline |
| ROOT-022 | med | PROJECT.md:235 | Phase 11 omits binding spec's exclusion-rule mechanics (§5/§5.1) | Add one-sentence pointer to spec §5 |

### P2 (~48 items)

| id | conf | file:line | issue | recommended fix |
|---|---|---|---|---|
| ROOT-015 | high | PROJECT.md:160 | "Arm B (model-informed scheduler)" qualifier | Drop qualifier |
| ROOT-016 | high | PROJECT.md:230 | Frames Arm B as "future model-informed" distinct from RBC | Reword |
| ROOT-017 | med | PROJECT.md:273 | THERMAL_MODEL_DESIGN.md row claims it's the Arm B model | Annotate or retire (D7: archive entirely) |
| ROOT-018 | med | PROJECT.md:424 | EMHASS/Predheat row adjacent to Arm B framing | Ensure not linked back to Arm B critical path |
| ROOT-019 | med | README.md:172 | "Pre-cool depth retune … as an Arm B variant" implies mid-experiment variants | Reword |
| ROOT-020 | med | PROJECT.md:251 | Same Arm-B-variant framing | Same |
| ROOT-021 | high | PROJECT.md:272 | EXPERIMENT_DESIGN.md row labeled "Pre-registration draft" — doc self-supersedes | Reword to historical research-design pointer |
| ROOT-028 | med | PROJECT.md:30 | "Repo tagged at OSF commit hash; Zenodo issues DOI" — present tense for not-yet-happened action | Mark conditional |
| SPEC-009 | high | sced-rebaseline-spec-2026-05-13.md:116 | §4 keeps DTOD/IEDT separate; §8 + impl combine | Align §4 to combined form or document the separation |
| SPEC-011 | med | sced-rebaseline-spec-2026-05-13.md:7 | `supersedes:` list omits OSF_FILING.md though that doc self-declares superseded | Add OSF_FILING.md to supersedes list |
| SPEC-017 | med | sced-rebaseline-spec-2026-05-13.md:425 | Arm 11 fall-back materiality argument asserts "<$0.01" without arithmetic | Append one-line arithmetic |
| IMPL-007 | med | sced-rebaseline-implementation-2026-05-13.md:1830 | Phase 1 telemetry snippets use pseudocode for InfluxDB Point API | Standing-rule disclaimer sufficient |
| DOC-011 | high | docs/HVAC_LOGIC.md:94,118,128-143 | Day type table uses code-internal `HOT_5CP_RISK`; spec uses `HOT` | Alias once or pick one user-facing label |
| DOC-012 | med | docs/COMFORTNET_USE_CASES.md:5 | Status "pre-integration"; shipped per SERVICES.md:545 | Replace with shipped-summary |
| DOC-013 | high | docs/SCHEDULER_TIMING.md:43 | DTOD rates use base; spec §8 uses resultant | Annotate or update |
| DOC-015 | med | docs/ARM_B_IMPLEMENTATION.md:556 | Step 1 reads retired assignment CSV | Replace with arm_calendar.py + spec §2 ref |
| DOC-016 | med | docs/ARM_B_IMPLEMENTATION.md:706-707 | "Randomization begins" — retired | Replace with "Arm A begins" |
| DOC-017 | high | docs/DRY_RUN_VALIDATION.md:8-13 | Body uses retired `SCHEDULER_DRY_RUN=false/true` | Global sed to `SCHEDULER_MODE` |
| DOC-018 | med | docs/THERMAL_MODEL_DESIGN.md:104,113,119 | nws-poller pre-migration description | Update or archive per D7 |
| DOC-019 | high | docs/SCHEDULER_TIMING.md:67 | "Recent fix (PR #121)" — well-merged | "Per PR #121 (merged)" |
| DOC-020 | med | docs/SCHEDULER_TIMING.md:123 | "PR #121 (merged or pending — check repo state)" | Resolve to "merged" |
| DOC-021 | high | docs/ANALYSIS_PIPELINE.md | Banner supersedes whole doc but Stages 1-2 catalog still authoritative | Qualify banner OR resolve via D5 PR6 |
| DOC-022 | high | docs/EXPERIMENT_DESIGN.md:201 | Rule 7 cites "~2.5 min" cadence; code throttles to 5-min per scope | Reconcile cadence |
| DOC-023 | med | docs/PJM_DM2_INTEGRATION.md:32 | RTO companions mentioned in non-goals but absent from schema/sequencing tables | Add to schema |
| DOC-046 | med | docs/SERVICES.md:200 | ComEd day-ahead note disconnected from pjm-dm2-poller section | Cross-reference |
| DOC-048 | med | docs/OSF_FILING.md:83-87 | Criterion 12 transitively binds via superseded ANALYSIS_PIPELINE.md §2.1 | Move schema or resolve via D5 PR6 |
| DOC-051 | high | docs/THERMAL_MODEL_DESIGN.md:5,105 | References measurement `weather.ecowitt`; shipped name is `ecowitt.weather` | Replace throughout (PR5) |
| RV-004 | high | docs/replay-validation/2026-05-18-shadow/findings.md:199 | LIM-2 says "OI-1 follow-up PR will re-fixture to outdoor_*" — OI-1 RETRACTED | Drop re-fixture language |
| RV-005 | high | docs/replay-validation/2026-05-18-shadow/findings.md:191 | LIM-0 calls `ch1_*` "deviator"; retraction confirms canonical | Strip "deviator ch1_*" |
| RV-006 | med | docs/replay-validation/2026-05-18-shadow/findings.md:205-207 | LIM-4 says "non-canonical ch1_* writes" — ch1_* IS canonical | Rewrite around post-retraction frame |
| RV-008 | high | docs/replay-validation/2026-05-12/README.md:1-6 | Two same-date READMEs with no cross-reference | Add reciprocal pointers |
| RV-009 | med | docs/replay-validation/2026-05-12-stage8-complete/README.md:1-6 | `status: live` vs `locked/active/archived` used elsewhere | Pick canonical status vocab |
| RV-010 | med | docs/replay-validation/2026-05-18-shadow/findings_draft.md | Co-exists with findings.md without disambiguator | Gitignore or `status: auto-generated` (per D6: delete) |
| RV-011 | med | docs/replay-validation/2026-05-18-shadow/findings.md:11 | `companion-pr-109-disposition: §13` ambiguous | Rename or point to spec explicitly |
| OPS-004 | high | deploy/energy-stack/README.md:116-130 | Test coverage list "16 files across 11 services" omits 6 tests under scripts/ | Add scripts/ suite |
| OPS-005 | high | deploy/energy-stack/scripts/README.md | `commission_decision_trace_path_c.py` + `log_arm_transition.py` undocumented | Add purpose/invocation sections |
| OPS-009 | med | tools/cockpit/README.md:103 | `COCKPIT_BACKEND_MODE=live` documented as "default in launcher" but bare uvicorn may default to canned | Note "set explicitly when running uvicorn manually" |
| OPS-010 | high | tools/cockpit/README.md:104 | Cites `docs/plans/archive/cockpit-plan.md` as "locked design decisions" — archived plan treated as binding | Move decisions to non-archive doc |
| OPS-012 | high | deploy/energy-stack/README.md:207,29-31 | mosquitto/telegraf rows co-list archived COMFORTNET_PIPELINE.md alongside live COMFORTNET_USE_CASES.md | Drop archive link from row-detail |
| OPS-014 | high | deploy/energy-stack/README.md:35,144 | Compose detection caveat (multi-commit pushes may miss earlier-commit changes) | Add caveat |
| OPS-015 | med | deploy/energy-stack/scripts/README.md:11 | Linked PR `FireDevPro/energy-stack/pull/137` — repo name clash with `energy-proxy` working dir | No fix; clarity hazard |
| OPS-020 | high | deploy/energy-stack/backup/RESTORE.md | Restore procedure has no verification step | Append verification snippet (PR7) |

### P3 (~47 items)

| id | conf | file:line | issue | recommended fix |
|---|---|---|---|---|
| ROOT-023 | high | HANDOFF.md:1-7 | `status: live` but content stale | Flip to `status: superseded` (PR2) |
| ROOT-024 | high | CLAUDE.md | One-line file (@AGENTS.md), no YAML header | Acceptable as alias |
| ROOT-025 | med | PROJECT.md:160 | Non-ISO date "May 6 2026" | Convert load-bearing dates |
| ROOT-027 | med | PROJECT.md:443 | Ecowitt schema gap acknowledged inline | Resolve (add to SERVICES.md) or move to follow-ups |
| IMPL-009 | high | sced-rebaseline-implementation-2026-05-13.md:2862,2889 | Task 5.1 template shows `<hash>` / `2026-05-XX` placeholders | Update template to as-shipped |
| IMPL-010 | med | sced-rebaseline-implementation-2026-05-13.md:2913 | Task 5.2 enumeration lists non-existent `HOT, HOT_STREAK_DAY2` | Trim to actual day-types |
| IMPL-011 | low | sced-rebaseline-implementation-2026-05-13.md:1543-1544 | `CAPACITY_RISK_WINDOW_END` comment doesn't note exclusive semantics | Add comment |
| DOC-024 through DOC-037 | high | 14 of 16 docs/*.md missing YAML headers | AGENTS.md "Doc hygiene Layer 1" requires headers | One-PR sweep (PR8) |
| DOC-039 | med | docs/EXPERIMENT_DESIGN.md:7 | "Status: revised 2026-05-09" predates rebaseline | Mark "frozen at pre-rebaseline" (or archive per D5) |
| DOC-040 | low | docs/ARM_B_IMPLEMENTATION.md:584-602 | §6 instructs adding `HVAC_SCHEDULER_DRY_RUN` — pre-rebaseline | Update or banner |
| DOC-041 | high | docs/ARM_B_IMPLEMENTATION.md:32-114 | §0a/§0b describe shipped work as "required changes" | Mark "DONE" or convert past-tense |
| DOC-042 | med | docs/ARM_B_IMPLEMENTATION.md:687-707 | Critical-path sequencing future-dated entries past-due | Replace target-date with status |
| DOC-049 | med | docs/REPLAY_VALIDATION.md:53 | Open-Meteo URL — spot-check | Verify |
| DOC-050 | med | docs/OSF_FILING.md:1 | Top of file is WARNING block; no H1 or YAML | Add header (or archive per D5) |
| RV-003 | high | docs/replay-validation/2026-05-18-shadow/findings_draft.md:1-3 | HTML-comment "DRAFT" notice, no YAML header | Add header or delete (per D6) |
| RV-007 | high | docs/replay-validation/2026-05-18-shadow/findings.md:113-115 | OI-2 has no `status:` tag | Tag `informational, open` |
| RV-012 | low | docs/replay-validation/2026-05-14-decision-trace-commissioning/findings.md:4 | Status `path-c-complete-b1-pending` bespoke; B1 unrecorded | Resolve and update |
| RV-013 | low | docs/replay-validation/2026-05-14-decision-trace-commissioning/findings.md:190 | PRECOOL_REJECTED_NO_DA_LMP_DATA follow-up not recorded | Update §5 |
| RV-014 | low | docs/replay-validation/2026-05-12-stage8-complete/README.md:1-6 | Header schema inconsistent | Document common schema |
| RV-015 | low | docs/replay-validation/2026-05-12/README.md | No F3-sweep supersession banner | Add scope banner |
| RV-016 | low | docs/replay-validation/2026-05-12-stage8-complete/README.md | Pre-rebaseline frame, no banner | Add scope banner |
| RV-017 | low | docs/replay-validation/2026-05-18-shadow/findings.md:64-67 | Post-retraction pass count needs re-issued JSON | Tied to RV-001 |
| RV-018 | low | docs/replay-validation/2026-05-18-shadow/findings.md:225 | Memory ref hyphen/underscore mismatch | Rename memory or update ref |
| OPS-006 | high | deploy/energy-stack/README.md | No YAML header on stack guide of record | Add header (PR8) |
| OPS-007 | high | deploy/energy-stack/scripts/README.md | No YAML header | Add header (PR8) |
| OPS-008 | high | deploy/energy-stack/backup/RESTORE.md | No YAML header | Add header (PR8) |
| OPS-011 | med | deploy/energy-stack/README.md:84 | Backslash-escaped pipe operators break copy-paste | Use fenced code block |
| OPS-016 | high | tools/cockpit/README.md:170-176 | Vitest non-gating with no time-bounded plan | Add tracking line |
| OPS-018 | high | deploy/energy-stack/scripts/README.md:9 | GitHub-only `[!WARNING]` callout | Acceptable |
| OPS-019 | med | tools/cockpit/README.md:171-175 | Required-gates list doesn't cross-link to Commands table | Cross-link |
| OPS-022 | med | deploy/energy-stack/README.md:84 | Manual InfluxDB backup writes to /tmp/backup inside container; not host-mounted | Add `docker cp` step |
| SPEC-013 | high | sced-rebaseline-spec-2026-05-13.md:209 | Could cite OI-1 retraction for audit trail | Optional footnote |
| SPEC-014 | high | sced-rebaseline-spec-2026-05-13.md:151 | `pjm_5cp.py:140` line-number cite is brittle | Switch to symbol anchor |
| SPEC-015 | high | sced-rebaseline-spec-2026-05-13.md:151 | Cites EXPERIMENT_DESIGN.md line 569 — superseded doc | Cite PJM Manual 19 directly or move row into spec |

---

## Vocabulary clusters

1. **Arm B identity (highest impact)** — "thermal-model-informed" / "Step 1 model-informed" / "envelope-ODE" (PROJECT/README/AGENTS) vs spec §3 `hvac-scheduler active` with no thermal model.
2. **Arm calendar shape** — "12 arms × 14d, 6A/6B, deterministic" (spec) vs "18 weeks, 9A/9B" (PROJECT) vs "week boundary switching" (PROJECT, README).
3. **Pre-registration binding doc** — `sced-rebaseline-spec-2026-05-13.md` (spec) vs `EXPERIMENT_DESIGN.md` "draft" (README, PROJECT) — latter self-supersedes.
4. **`SCHEDULER_MODE` values** — spec + code `("shadow", "experiment", "production")` vs ARM_TRANSITIONS / DRY_RUN_VALIDATION banner `("shadow", "active")` — `active` is not a valid value.
5. **Mode classification per-hour** — "4 modes" header (spec §5:137, §7:271) vs 5-row table body and `HourMode` enum.
6. **Per-pair column naming** — `valid_pair_hours` (§9) vs `valid_pair_hours_a/_b` (impl test); `excluded_hours_a_count/_b_count` (§5) vs `excluded_hours_count` + breakdowns (§9); `cfe_c_per_kwh_a/_b` missing from §9.
7. **Rate-formula decomposition** — §4 keeps DTOD + IEDT separate; §8 + impl combine into `dtod_total_delivery_c_per_kwh`.
8. **Day-type enum** — spec `HOT` vs code/HVAC_LOGIC `HOT_5CP_RISK`. Plan §5.2 also names non-existent `HOT_STREAK_DAY2`.
9. **"Shadow" / "dry-run" / "decision-trace"** — three non-synonymous terms in adjacent docs.
10. **HVAC channel set** — `{em:2, em:8, em:9}` canonical per spec §4:121 + §7:246/258 + runner. Stale at validation_results.json:89.
11. **Outdoor channel** — `ch1_*` canonical per spec §6:209 + OI-1 retraction. `outdoor_*` is descriptive alias.
12. **Measurement names** — `ecowitt.weather` shipped; THERMAL_MODEL_DESIGN uses `weather.ecowitt`.
13. **Document status labels** — many bespoke values (`active`, `live`, `locked`, `draft`, `in-progress`, `pending-osf-filing`, `superseded`, `archived`, `retracted`, `path-c-complete-b1-pending`).
14. **Pi-lab path** — `~/energy-stack/.env` (5+ docs) vs `/home/chris/energy-proxy/.env` (cockpit README only).
15. **Capacity-risk window** — three names for one concept (documented in §5.1).
16. **Pre-season fallback constant** — 130,000 MW (legacy/wrong) vs 20,375 / 151,525 (scope-aware, correct).
17. **Rebaseline** — noun/directive/verb usages, sufficiently consistent.

---

## Archive / supersession concerns

**Active docs treating archived material as binding:**
- `tools/cockpit/README.md:104` → `docs/plans/archive/cockpit-plan.md` as "locked design decisions" (OPS-010)
- `deploy/energy-stack/README.md:29-31, 207` → archived COMFORTNET_PIPELINE.md co-listed with live COMFORTNET_USE_CASES.md (OPS-012)
- `docs/OSF_FILING.md:83-87` → transitively binds via banner-superseded ANALYSIS_PIPELINE.md §2.1 (DOC-048)
- `sced-rebaseline-spec-2026-05-13.md:130, 151, 315` → EXPERIMENT_DESIGN.md §O2/§7.7 — cited sections are NOT in supersession scope but doc-level banner muddies that (SPEC-015)

**Active docs that may belong in archive (resolved by D5):**
- ANALYSIS_PIPELINE.md — D5 PR6 archives after §2.1 extracted to SERVICES.md
- EXPERIMENT_DESIGN.md — D5 PR6 archives after Appendix A / §O2 / §11 extracted
- OSF_FILING.md — D5 PR6 archives; replaced by OSF_FILING_MECHANICS.md
- ARM_B_IMPLEMENTATION.md — D5 PR6 tightens banner; full archive deferred post-experiment

**Within-doc supersession contradictions:**
- `docs/replay-validation/2026-05-18-shadow/findings.md` — top declares OI-1 RETRACTED with ch1_* canonical; LIM-0/2/4 still carry pre-retraction framing (RV-004, RV-005, RV-006)

**Stale-without-banner replay-validation docs:**
- `docs/replay-validation/2026-05-12/README.md` and `docs/replay-validation/2026-05-12-stage8-complete/README.md` — F3 sweep missed these (RV-015, RV-016)

---

## Reconciliation summary (Claude vs Codex audits)

### Codex findings accepted as net-new and added to this corpus
- **F-002** (P1) — Spec TBD Refoss/Eagle tolerance at lines 423, 500
- **F-005** (P1) — `.env.example:33` still says `PJM_DM2_POLL_INTERVAL=3600`
- **F-006** (P1) — Impl plan top architecture says Phase 4 pulls historical baseline
- **F-007** (P1) — REPLAY_VALIDATION.md injection-case list is stub
- **F-009** (P2) — pipeline.py:3 docstring binding to old ANALYSIS_PIPELINE.md

### Codex findings duplicating mine (consolidated under Claude IDs)
- F-001 → ROOT-001..011, CQ-A..D
- F-003 → DOC-001, DOC-017, CQ-V
- F-004 → RV-004/005/006, DOC-051
- F-008 → ROOT-013, ROOT-014
- F-010 → DOC-008/009/015/016/041/042
- F-011 → ROOT-012 (reversed direction)
- F-012 → DOC-024..037 hygiene cluster

### Codex framing rejected
- **F-004 "unresolved canonical field choice":** repo evidence (spec §6:209 + OI-1 retraction at commit 4ad147e) says `ch1_*` is canonical conclusively. Drift is one-directional (some docs lag), not unresolved. Reframe to "ch1_* canonical; outdoor_* descriptive alias; align lagging docs."

### Claude findings dropped (Codex critique correct)
- ROOT-012 (original direction wrong — reversed)
- OPS-021 (HAVEN_TOKEN_FILE has code default; doc is correct)
- ROOT-026, ROOT-029, ROOT-030, DOC-038, DOC-043, IMPL-008, IMPL-012, OPS-018 (8 "Recommended fix: None" verifications — not findings)
- SPEC-016 (self-corrected duplicate)

### Claude findings recategorized (not drift defects)
- **Operator-checklist items (move to freeze-day PR9):** SPEC-010, SPEC-012, SPEC-018
- **Open evidence questions (need external verification, not drift fixes):** DOC-014 (PJM catalog cadence), DOC-045 (Refoss em range), DOC-047 (RTO peak distribution), OPS-013 (deploy timing)
- **OPS-017 reframed:** strip "Trusted VLAN 10" naming (not established in repo), keep the contradiction

### Net adjusted total
- Original: 156
- After drops: 146
- After Codex net-new: 151
- After operator-checklist recategorization (3 items): 148
- After open-evidence-question recategorization (4 items): **144**

Final breakdown: 12 P0, 37 P1, ~48 P2, ~47 P3.
