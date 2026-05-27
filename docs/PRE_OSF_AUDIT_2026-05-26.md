---
date: 2026-05-26
owner: chris
status: active
role-label: audit-report
name: pre-osf-audit-2026-05-26
purpose: cross-validation between Claude (this audit) and Codex (independent review)
---

# Pre-OSF audit report (2026-05-26)

Three knowledge-graph-driven audits run against the locked scheduler architecture during the 4-day pre-OSF window. Drafted as the basis for Codex independent review before the 2026-05-30 freeze commit.

## Audits run

### 1. Process completeness — signal-to-action trace audit

**Scope:** 8 starter traces verifying that every detection has a corresponding action chain. Edge types walked: `imports`, `calls`, `writes`, `reads`. Source: `.understand-anything/knowledge-graph.json`.

**Result: 8/8 traces complete, 0 orphans.**

Traces verified: (1) ComEd price spike → setpoint push, (2) day-type classification → schedule selection, (3) 5CP risk → effective_cool_f (intentional gap — demoted per spec §11 #14), (4) humid override trigger, (5) manual override detection → `hvac.overrides`, (6) scheduler watchdog → `hvac.heartbeat`, (7) telegram notifier (7 pollers monitored), (8) decision-trace-report n8n workflow.

No orphaned writes (every measurement has consumers). No orphaned reads (every consumer's source exists). No unreachable decision_code constants. No unreachable setpoint_reason values. No unreachable ScheduleAction labels.

### 2. Structural hazards — cycles, dead-ends, retry loops, feedback

**Scope:** 6 hazard categories. Import cycles, call cycles, dead-end function paths, try/except silent swallows, state-machine completeness, unbounded retry loops. Plus targeted search for feedback-loop patterns and the haven-ingest write-file / read-env split pattern that bit us tonight (token expiry 22 days silent).

**Result: clean with 2 NOTES, no critical hazards.**

NOTE 1: `_trace()` at `hvac_scheduler/app.py:347-383` swallows all exceptions intentionally to prevent trace failures from breaking decision flow. Cost: silent telemetry-write failures lose diagnostic data without surfacing in logs. **Disposition:** known-design; flag for post-experiment observability check.

NOTE 2: (revised after Codex review) — earlier draft framed `price_overlay.py:173-208` as having "no exception handlers around InfluxDB query result mutations." On re-read that region is pure state-transition code (tier-priority comparisons, `PriceOverlayState` construction); no InfluxDB query happens here. The query-result projection is upstream in `fetch_latest_comed` + `project_record`, which already type/project malformed rows. The earlier framing was a misread; there is no actionable defense-in-depth gap at lines 173-208. **Disposition:** withdrawn; no follow-up needed.

Verified: the haven-ingest token-persistence pattern (write to file, fall back to env on missing file) is UNIQUE to that service. No other service has the same write-to-file / read-from-env split. The 22-day silent-failure mode that bit us tonight cannot recur elsewhere.

### 3. End-to-end test coverage

**Scope:** does a synthetic-fixture test exercise the full tick path? Per-component coverage status. Specific scenario gaps. Dynamic-hazard test coverage.

**Result: no synthetic-fixture end-to-end tick test exists.** Per-component unit tests cover decision logic, day-type classification, supervisor checks, and price overlay state transitions in isolation. No test invokes `compute_decision()` against fixture inputs and asserts the full output shape.

**Agent classification:** CRITICAL pre-OSF.

**Author's pushback:** the CRITICAL classification assumes "no synthetic test = unvalidated." For this project, the scheduler has been running in shadow mode against live conditions for 22+ days (~190,000 ticks against real ComEd prices, NWS forecasts, ComfortNet outdoor temp, refoss power, RedLINK indoor). Every **decision and telemetry-write** code path the agent flagged as "no test" has been exercised thousands of times against real-world inputs. (Caveat per Codex review: the actual Control4 write side effect is gated off in shadow mode via `_writes_allowed()` and remains unproven by shadow operation — that path activates only when Arm B periods go active on 2026-06-01.) The multi-layer interaction the agent flagged (schedule + price overlay + supervisor) was exercised live this evening on a sustained-spike day (3+ supervisor escalations to 83°F and 85°F observed in `hvac.actions`). The daily decision-trace narrative reports surface anomalies. Replay validation findings.md confirmed PASS on 2026-05-18 shadow data.

The author's read: live shadow operation IS the integration test. The 24-week experiment is the validation methodology. Classifying CRITICAL because no CI fixture exists conflates "untested in CI" with "unvalidated."

**Real gap:** dynamic hazards (race conditions between ticks, TOCTOU, network partition mid-decision, container restart with in-flight state, idempotency of `hvac.actions` writes). Neither unit tests NOR shadow operation cover these. The watchdog + main-tick exception handling is the only defense. Agent classified MEDIUM pre-OSF / CRITICAL post-OSF — author agrees.

## Disposition recommended

**Pre-OSF (5/26-5/30):** document the test-coverage gap honestly in the OSF deposit; do not backfill tests. Reasoning: (a) 4-day window is consumed by freeze-commit mechanics + apparatus photos + overnight passive-cooling test; (b) the validation methodology IS operate-and-observe across the 24-week experiment; (c) backfilling tests could surface issues that demand fixes, compounding the timeline; (d) per memory `feedback_frame_spec_deviations_honestly`, honest gap-documentation is more valuable to reviewers than half-finished test coverage.

**Post-OSF (post-2026-11-16 wrap):** build the full synthetic-fixture tick-path test suite using ground-truth outcomes from the 24-week run. Add dynamic-hazard tests (race / partition / idempotency). Address the two structural NOTES (price overlay query validation, `_trace` observability).

**Already done tonight (not part of audit but worth knowing for Codex):** RedLINK control sensor relocated from primary bedroom to 2F hallway at 18:30 CDT 2026-05-26; `indoor_temp_f_hires` field added to `thermostat_poller` (PR #65 merged, deployed, verified writing fractional 77.9°F vs whole-degree 78°F); Haven feed restored after 22-day Auth0 silent failure (token re-bootstrapped, persistence file verified). NTP source pinned to time.cloudflare.com / time.nist.gov / time.google.com via systemd-timesyncd drop-in for 24-week timestamp stability.

## What Codex is asked to challenge

1. **Severity classification on the test-coverage gap.** Author says MEDIUM-with-documentation; agent said CRITICAL pre-OSF. Which is right for an N-of-1 SCED pre-registered field study where the experiment itself is the validation? Cite published norms for residential MPC field-study test coverage if any exist (Khabbazi et al. 2025 meta-review, Kuznik 2023, Sturzenegger 2016 — referenced in `docs/archive/THERMAL_MODEL_DESIGN.md`).

2. **Is "live shadow operation across 22+ days" a defensible validation surrogate for synthetic tests?** If not, what's the minimum synthetic test that would meaningfully reduce the gap, achievable in <4 hours of work?

3. **Pre-OSF time allocation.** Should the 4 remaining days prioritize freeze-commit mechanics + apparatus photos + overnight test (author's plan) or backfilling tests (agent's implied recommendation)? What's the opportunity cost calculation?

4. **The two structural NOTES.** Author classified both as post-experiment fixes. Codex's read on whether either is actually pre-OSF-blocking given the 24-week unattended-operation window. Specifically: should `price_overlay.py:173-208` get a result-shape guard added pre-OSF, or does the parent tick-loop exception handler provide enough defense-in-depth?

5. **Dynamic hazards (race / partition / idempotency).** Agent flagged CRITICAL post-OSF. Author agrees. Does Codex see any of these as actually pre-OSF-blocking given that the experiment can survive a few-day pi-lab outage and the watchdog catches scheduler-process death?

## Audit reproducibility

All three audits are re-runnable via `/understand-anything:understand-chat` against the committed knowledge graph at `.understand-anything/knowledge-graph.json` (last analyzed at commit `360f3f7`, post tonight's PR #65 merge). The agent prompts used are not committed but the trace pattern is captured in this report. Codex can re-derive the same findings by walking the same graph edges.

## Source-of-truth docs Codex should read alongside this audit

- `docs/plans/sced-rebaseline-spec-2026-05-13.md` (binding spec, §11 pre-OSF dependencies, §13 PR #109 disposition)
- `docs/OSF_FILING_MECHANICS.md` (the 6 freeze-day steps)
- `docs/plans/public-flip-readiness-2026-05-19.md` (already 10/12 done, repo flipped public)
- `docs/replay-validation/2026-05-18-shadow/findings.md` (replay validation PASS — LIM-0 noted)
- `deploy/energy-stack/hvac_scheduler/app.py` (the scheduler tick path)
- `deploy/energy-stack/hvac_scheduler/safety_supervisor.py` (supervisor logic)
- `deploy/energy-stack/hvac_scheduler/price_overlay.py` (tier transitions + the NOTE 2 path)

Author's preferred next-action: this audit lands in the freeze commit alongside the spec status flip. Codex's review of this doc lands as a comment on the OSF deposit prep PR (or as a follow-up issue). Author does not plan to act on Codex feedback during the OSF freeze window unless Codex flags something genuinely pre-OSF-blocking that the author missed.

## Codex review outcome (received 2026-05-26 post-PR-open)

**Verdict: author is right. MEDIUM-with-documentation pre-OSF, not CRITICAL. Codex would not block PR #66.**

Codex's specific findings:
- PR is docs-only; `git diff --check main...HEAD` clean.
- `hvac_scheduler` tests pass: 345 passed in 11.86s.
- Existing test coverage is not empty — Codex enumerated: `_evaluate_layer_inputs`, `run_schedule_check`, supervisor continuity, stale-price gate, write gating, and 2025 price-overlay replay all have tests.
- Gap is real but narrowly scoped: no single synthetic full-tick fixture with a deterministic oracle. Live shadow validates real feed shape + decision telemetry; it does NOT validate Control4 write side effects or dynamic hazards (Control4 write activates only when Arm B goes live 2026-06-01).
- Published field-study norms support operate-and-observe over CI fixture coverage for this category: Khabbazi et al. 2025 reviews 24 residential + 80 commercial HVAC MPC/RL field studies and flags unreliable experiment protocols + duration + real-building uncertainty + reporting — NOT missing synthetic unit fixtures (arXiv). Sturzenegger 2016 reports 7 months MPC in a fully occupied building (ETH).

Codex's two doc nits (both addressed inline above):
1. NOTE 2 framing of `price_overlay.py:173-208` as "InfluxDB query-result mutation" was wrong; that region is pure state transition. **Corrected above.**
2. "Every code path" in the author's pushback was too strong; should distinguish decision/telemetry path (exercised in shadow) from Control4 write side effects (not exercised in shadow). **Caveat added above.**

Codex's optional suggestion (not blocking): one `run_schedule_check` synthetic fixture asserting schedule + price overlay + supervisor + `write_action` output shape. Estimated <4 hours. Codex framed it "nice, not blocker." Author's disposition: defer to post-experiment unless pre-freeze time opens up.

Cross-validation outcome: both reviewers agree the audit doc is fit for OSF deposit at the freeze commit. The disagreement with the earlier agent on CRITICAL classification is resolved in favor of MEDIUM-with-documentation.
