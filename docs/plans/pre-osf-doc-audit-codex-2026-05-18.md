---
date: 2026-05-18
owner: chris
status: locked
role-label: code-team
audit_role: raw-codex-input (pre-reconciliation)
reconciled_by: pre-osf-doc-audit-findings-2026-05-18.md
reconciled_findings: pre-osf-doc-audit-execution-2026-05-18.md
---

> [!WARNING]
> **Raw Codex audit input — preserved as evidence, NOT operative.** This is Codex's audit verbatim from 2026-05-18 before reconciliation with the Claude audit. Some findings here were rejected during reconciliation (notably F-004's "unresolved canonical field choice" framing — repo evidence at spec §6:209 + commit 4ad147e OI-1 retraction conclusively makes `ch1_*` canonical; drift is one-directional, not unresolved). The reconciled accounting + canonical recommended fixes live in [`pre-osf-doc-audit-findings-2026-05-18.md`](pre-osf-doc-audit-findings-2026-05-18.md) (§"Reconciliation summary"). The execution plan that drove the resulting 9 PRs is [`pre-osf-doc-audit-execution-2026-05-18.md`](pre-osf-doc-audit-execution-2026-05-18.md). When investigating any finding ID prefixed `F-N`, cross-check against the reconciliation summary before acting.

# Documentation drift audit before OSF filing

Scope: active docs in `docs/*.md`, replay-validation docs, SCED rebaseline spec and implementation plan, root docs, `deploy/energy-stack` docs, and `tools/cockpit` docs. Excluded archives and test reports except for supersession/reference checks.

Review only. No source files were edited during the audit. This file was created afterward from the audit report at operator request.

## Executive summary

High-risk drift: repo still contains two competing OSF stories. The rebaseline spec says deterministic 12-arm alternation, no seed, no assignment CSV, no bootstrap CI, and no SCED randomization test. Root and active docs still point readers to `EXPERIMENT_DESIGN.md`, assignment CSV, seed, weekly/bootstrap framing, or retired scheduler mode.

Operational drift: active runbooks still mention `SCHEDULER_DRY_RUN`, while code accepts `SCHEDULER_MODE=shadow|experiment|production`. PJM poller docs and `.env.example` also lag the current 5-minute loop and hourly metered-load feed.

Verification run: 33 scoped docs, 0 broken Markdown links, 8 links to archive docs, 19 scoped docs missing YAML front matter.

## Canonicality questions

1. **OSF binding source.** Is `docs/plans/sced-rebaseline-spec-2026-05-13.md` the OSF-binding source, or does `docs/EXPERIMENT_DESIGN.md` remain binding? Evidence: `AGENTS.md:48`, `README.md:12`, `PROJECT.md:28` point to `EXPERIMENT_DESIGN.md`; rebaseline spec `:17` says it wins over prior docs.
2. **Ecowitt canonical weather fields.** Are `ch1_*` or `outdoor_*` canonical for weather-vector analysis? Evidence: spec `:209` and `tools/analysis/weather_vector.py:97` use `ch1_*`; `docs/SERVICES.md:78` and `deploy/energy-stack/ecowitt-ingest/app.py:48` call `outdoor_*` canonical.
3. **Runtime scheduler mode.** Should operators use `SCHEDULER_MODE=shadow|experiment|production`, or retired `SCHEDULER_DRY_RUN` / `active` language? Evidence: spec `:91-98`, app `:641`, versus `docs/ARM_TRANSITIONS.md:43`.
4. **PJM poll cadence.** Is live poller cadence the 5-minute loop with hourly metered load, or `.env.example` 3600 seconds and weekly metered load? Evidence: app `:172,203-206`, `.env.example:33`, `docs/SERVICES.md:276`.
5. **Replay validation lock state.** Is `docs/REPLAY_VALIDATION.md` locked, or still pending injection-case generator work? Evidence: `docs/REPLAY_VALIDATION.md:6`, `:104`, `:151`.

## Findings

| id | severity | confidence | file:line | issue | evidence | recommended fix |
|---|---|---|---|---|---|---|
| F-001 | P0 | high | `AGENTS.md:48`, `README.md:12`, `PROJECT.md:28` | Root docs still identify old prereg doc / seed as binding. | Spec says it wins over `EXPERIMENT_DESIGN.md` and old seed/CSV are retired at `docs/plans/sced-rebaseline-spec-2026-05-13.md:17`; `PROJECT.md:235` agrees, but earlier root text conflicts. | Make root docs name rebaseline spec, Arm A schedule, HVAC logic, and shadow findings as binding; mark `EXPERIMENT_DESIGN.md` historical/superseded. |
| F-002 | P0 | high | `docs/plans/sced-rebaseline-spec-2026-05-13.md:423,500` | Binding spec still contains `TBD` Refoss/Eagle tolerance. | Same spec calls itself binding at `:17`; tolerance remains "TBD at audit phase." | Lock numeric tolerance or state "raw drift reported only, no tolerance gate." |
| F-003 | P1 | high | `docs/ARM_TRANSITIONS.md:4,43-44`; `docs/SERVICES.md:322`; `docs/DRY_RUN_VALIDATION.md:4` | Active ops docs use retired scheduler mode. | Code accepts only `shadow`, `experiment`, `production` at `hvac-scheduler/app.py:641`; docs say `active` and `SCHEDULER_DRY_RUN`. | Replace with `SCHEDULER_MODE=shadow` pre-study and `SCHEDULER_MODE=experiment` during study; no per-arm env flip. |
| F-004 | P1 | high | `docs/SERVICES.md:78,471`; `ecowitt-ingest/app.py:48`; `docs/replay-validation/2026-05-18-shadow/findings.md:191,199,205` | Weather field vocabulary conflicts. | Spec and code consume `ch1_*` (`spec:209`, `weather_vector.py:97,118`); services/app docs call `outdoor_*` canonical. Final findings also contain stale post-retraction `outdoor_*` language. | Resolve canonical field choice once; align service docs, app docstring, shadow findings, cockpit notes. |
| F-005 | P1 | high | `.env.example:33`; `docs/SERVICES.md:61,264,276,289`; `docs/PJM_DM2_INTEGRATION.md:25,45,57,156` | PJM docs/env would misconfigure live 5CP inputs. | Code default is 300 seconds and `inst_load` fires every 5 minutes; metered load is hourly `zone=CE/RTO` (`app.py:172,203-206`). Docs say 3600 seconds, weekly, `zone="COMED"`, 6 calls/day. | Set docs/env example to 300 seconds; document hourly `hrl_load_metered` with `CE/RTO`, 5-minute `inst_load`, updated call budget. |
| F-006 | P1 | medium | `docs/plans/sced-rebaseline-implementation-2026-05-13.md:18` | Plan architecture says Phase 4 pulls historical weather baseline. | Same plan later says no historical pull at `:2780,2784`; spec says within-sample standardization, no external baseline at `spec:228`. | Update phase summary to NOAA fallback station selection only. |
| F-007 | P1 | medium | `docs/REPLAY_VALIDATION.md:6,104,151` | Locked injection-case list is still stub/pending. | Acceptance says bundle must include listed cases, but list says "Stub" and "subject to refinement." | Either remove from OSF gate or lock final case list before filing. |
| F-008 | P1 | medium | `HANDOFF.md:14,32`; `docs/replay-validation/2026-05-12-stage8-complete/README.md:61` | `HANDOFF.md` is live but stale. | Says Stage 8/9 stub and in flight; later replay says Stage 8 feature-complete, code has Stage 8/9 functions. | Mark handoff superseded/archive, or refresh to 2026-05-18 state. |
| F-009 | P1 | medium | `tools/analysis/pipeline.py:3`; `docs/ANALYSIS_PIPELINE.md:4,36,187,234` | Code docstring still says old pipeline doc is binding. | `ANALYSIS_PIPELINE.md` says superseded, but module says "Binding contract: docs/ANALYSIS_PIPELINE.md." | Point docstring to rebaseline spec / `arm_period_pipeline`, or label old module historical. |
| F-010 | P2 | high | `docs/OSF_FILING.md:4,55,62,256`; `docs/ARM_B_IMPLEMENTATION.md:4,756-757` | Superseded docs remain active with stale checklist content. | Top banners say superseded, later sections still instruct assignment CSV / frozen `EXPERIMENT_DESIGN.md`. | Add YAML `status: superseded` and move stale checklists behind "historical only," or archive. |
| F-011 | P2 | medium | `deploy/energy-stack/README.md:3`; `PROJECT.md:265` | Deploy service count drift. | README says about 14 services; compose has 19 containers, PROJECT says 19. | Align count or avoid exact count. |
| F-012 | P3 | high | 19 scoped docs | YAML header/status hygiene missing. | Missing headers include `docs/EXPERIMENT_DESIGN.md`, `docs/HVAC_LOGIC.md`, `docs/SERVICES.md`, `docs/OSF_FILING.md`, deploy READMEs, `findings_draft.md`. | Add YAML with `date`, `owner`, `status`, `role-label`; use `superseded` where applicable. |

## Vocabulary clusters

| cluster | preferred vocabulary | stale or conflicting vocabulary |
|---|---|---|
| Binding artifact | `SCED rebaseline spec`; `docs/plans/sced-rebaseline-spec-2026-05-13.md` | `EXPERIMENT_DESIGN prereg` as active binding source |
| Arm schedule | `deterministic 14-day alternation`; `12 arms`; `6 A + 6 B`; `arm_calendar.py` | `randomization`, `seed`, `assignment CSV`, `4-week blocks`, `18 weeks` |
| Mode gating | `SCHEDULER_MODE=shadow|experiment|production` | `SCHEDULER_DRY_RUN`, `active` |
| Weather fields | unresolved: `ch1_*` vs `outdoor_*` | docs currently use both as "canonical" |
| Analysis unit | `arm-period`, `within-sample z-score`, `descriptive per-pair` | `weekly`, `bootstrap CI`, `SCED randomization test`, `ERA5 baseline` |
| PJM feeds | `inst_load` 5-minute, `metered_load` hourly, `zone=CE/RTO` | weekly Sunday, 3600 seconds, `zone=COMED`, 6 calls/day |

## Archive and supersession concerns

No broken Markdown links were found in scoped docs.

Eight active docs link to archive paths:

| file:line | archive target | concern |
|---|---|---|
| `deploy/energy-stack/README.md:207` | `docs/archive/COMFORTNET_PIPELINE.md` | Labeled historical. OK if current source remains comfortnet repo. |
| `docs/SCHEDULER_TIMING.md:211` | `docs/plans/archive/decision-trace-plan.md` | Labeled archived plan. Low risk. |
| `docs/SERVICES.md:179` | `docs/archive/phase-3.3-eagle-poller-design.md` | Labeled historical. Low risk. |
| `docs/SERVICES.md:545` | `docs/archive/COMFORTNET_PIPELINE.md` | Historical context. Low risk. |
| `PROJECT.md:186` | `docs/archive/phase-3.3-eagle-poller-design.md` | Historical context. Low risk. |
| `PROJECT.md:247` | `docs/archive/COMFORTNET_PIPELINE.md` | Historical context. Low risk. |
| `PROJECT.md:281` | `docs/archive/README.md` | Index. Low risk. |
| `tools/cockpit/README.md:104` | `docs/plans/archive/cockpit-plan.md` | Says "Locked design decisions" live in archived plan. Archive may be treated as active binding by readers. |

Additional concern: `docs/replay-validation/2026-05-18-shadow/findings_draft.md` remains in active replay-validation scope, lacks YAML, and preserves the pre-retraction `outdoor_*` premise. Either mark generated draft as superseded or move outside active scope.

## Suggested remediation sequence

1. Fix OSF canonical source references in root docs and AGENTS.
2. Resolve spec `TBD` and replay injection-list gap.
3. Fix scheduler mode docs before any operator runbook use.
4. Resolve Ecowitt field canonicality everywhere.
5. Fix PJM cadence / zone docs and `.env.example`.
6. Mark/archive superseded docs and add YAML headers.
7. Re-run link/header scan before OSF filing.

## Audit verification notes

- `git status --short --branch`: clean on `main...origin/main` before audit artifact creation.
- `git stash list`: empty.
- Scoped Markdown link scan: 33 files, 0 broken links, 8 archive links.
- Scoped YAML scan: 33 files, 19 missing YAML headers.
