---
name: shadow-validation-2026-05-18
date: 2026-05-18
owner: chris
status: active
role-label: code-team
companion-runner: tools/analysis/run_shadow_validation.py
spec-anchor: docs/plans/sced-rebaseline-spec-2026-05-13.md §11 #13
plan-anchor: docs/plans/sced-rebaseline-implementation-2026-05-13.md Phase 6
companion-pr-109-disposition: §13
---

# SCED rebaseline — Phase 6 shadow validation findings

Pre-experiment shadow validation per spec §11 #13. Validates pipeline shape against real Pi-lab production data ahead of the 2026-06-01 experiment start. **Not outcome evidence** — pre-experiment hours fall outside any locked Arm.

## Inputs window

- **Start:** 2026-04-29T00:00:00Z (spec-locked)
- **End:**   2026-05-18T16:08:24Z (run time)
- **Duration:** ~19.5 days
- **Source:** Pi-lab InfluxDB at `192.168.20.10:8086`, bucket `energy`, org `depaola-home`

## Commands run

Phase 6 introduced a new GitHub Actions workflow `.github/workflows/shadow-validation.yml` that runs the runner on the self-hosted pi-lab runner. **However, `workflow_dispatch` requires the workflow file to be on the default branch before it can be triggered against a feature branch.** Because Phase 6 IS the PR that adds the workflow file, this initial run is the documented fallback per the goal: runner executed locally from the Windows workstation against pi-lab's InfluxDB over the LAN.

Local fallback invocation:

```bash
INFLUXDB_URL=http://192.168.20.10:8086 \
INFLUXDB_TOKEN=<redacted> \
INFLUXDB_ORG=depaola-home \
INFLUXDB_BUCKET=energy \
PYTHONPATH=. \
python tools/analysis/run_shadow_validation.py
```

**Provenance caveat.** The `runner_commit_sha` recorded in the in-PR `validation_results.json` is `4f16742` (the parent of PR #142's merge commit `3bbd270`, because the runner was invoked from a workstation checked out at `main` before the runner was committed locally — `git rev-parse HEAD` ran at parent SHA). A canonical pi-lab-side run was captured post-merge as GitHub Actions run `26056757401` at SHA `4ad147e` (post-PR-#146 retraction main); that artifact has the correct provenance and should be the one OSF reviewers consume. Both runs use the same shadow data and produce equivalent findings; only the execution host and the recorded SHA differ.

After this PR merges to main, the canonical re-run path is the Actions UI → "Shadow validation" → Run workflow. That run will execute on pi-lab itself (self-hosted runner) and upload the validation JSON + findings draft as a workflow artifact. The local fallback documented here remains valid for ad-hoc re-runs from a developer workstation.

## Pass/fail table

Overall result (post-retraction): **WARN** — 2 WARN signals remaining (1 expected limitation + 1 OSF-appendix flag). No FAIL or BLOCKED.

| Check | Status (post-retraction) | Reason code | Significance |
|---|---|---|---|
| `ingestion.refoss.channel` | **PASS** | — | 269,735+ rows, 2026-04-29 → present. Em:1/2/7/8/9 all live. |
| `ingestion.ecowitt.weather` | **PASS** (was WARN, see OI-1 retraction) | — | 8,309+ rows of canonical `ch1_temp_f` (spec §6). The runner-emitted WARN reflected an inverted canonical assumption corrected in the retraction PR. |
| `ingestion.comed.prices` | **PASS** | — | 5,626 rows of `price_cents_per_kwh`. Continuous coverage. |
| `ingestion.pjm.lmp_rt_hourly` | **PASS** | — | 388 rows of `total_lmp_rt`. Last row 2026-05-15T03:00Z — PJM T+2 settle lag, expected. |
| `ingestion.eagle.meter` | **PASS** | — | 56,617+ rows of `delivered_kwh`. 100% Eagle coverage across window. |
| `ingestion.comed.bill` | **WARN** | `no_dtod_bill_in_window` | **Expected limitation.** First DTOD bill arrives 2026-05-24 per spec §14. Not a blocker; runner filters on `total_charges_dollars` so a schema drift would surface separately. |
| `pricing.reconstruction` | **N/A** | `no_dtod_bill_in_window` | Cannot exercise `bill_reconciliation.reconcile_bill_period` without a bill in window. Code path verified separately by `test_bill_reconciliation.py` + `test_pipeline_realshape_e2e.py`. |
| `refoss.hvac_kwh` | **PASS** | — | 451 hourly buckets; 63 hours of HVAC draw; max 3.39 kWh/hr; sum 81.9 kWh. Cooling-active hours present in window. |
| `reconciliation.refoss_eagle` | **PASS** | — | `eagle_coverage_pct=100.00%` across 56,617+ Eagle samples. Refoss vs Eagle drift is exercised inside `bill_reconciliation` when a bill closes in window. |
| `weather.vector_inputs` | **PASS** (was WARN, see OI-1 retraction) | — | Canonical `ch1_*` stream present per spec §6. The runner-emitted WARN reflected the inverted canonical assumption corrected in the retraction PR. |
| `arm_calendar.no_crash` | **PASS** | — | 473 shadow hours sampled; 0 inside any locked arm (expected — Arm 1 begins 2026-06-01). |
| `mode_classification.spot_test` | **PASS** | — | All 5 synthetic spot cases produce expected `HourMode` values. |
| `m3.scarcity_divergence` | **WARN** | `osf_appendix_flag` | See [M3 audit](#m3-scarcity-divergence-audit) — flagged for OSF appendix per spec §11 #13 M3. |

**WARN signal classification (updated 2026-05-18 after OI-1 retraction):**
- **1 expected limitation:** `ingestion.comed.bill` — pre-DTOD window, not a problem.
- **1 OSF-appendix flag:** `m3.scarcity_divergence` — pre-registered handling already exists in spec §12 (sensitivity `live_price_vs_settled_price`).
- **2 WARN signals retracted as false positives** (OI-1 RETRACTED, see below): `ingestion.ecowitt.weather` and `weather.vector_inputs` were treating `ch1_*` as non-canonical, but spec §6 declares `ch1_*` the canonical shaded outdoor channel. The runner's canonical-detection logic was inverted; corrected in the retraction PR.

## Open issues

### OI-1 — RETRACTED (2026-05-18, post-merge investigation)

**Original conclusion was wrong. The fix path proposed in this finding has been REVERTED. The spec's `ch1_*` canonical is correct and no code change is needed.**

#### What the original OI-1 finding claimed (now retracted)

After this PR merged, the Phase 6 superpowers review identified that `weather_vector.py` was the lone analysis-side module reading `ch1_*` while `pipeline.py`'s superseded weekly-framing code and the poller's `outdoor_*` naming intent suggested `outdoor_*` was canonical. Two follow-up actions landed:

1. **Pi-lab `.env`** was edited to add `ECOWITT_SHADED_CHANNEL=1` at 2026-05-18T17:25Z, causing the poller to re-route the channel-1 WN31 readings from `ch1_*` to `outdoor_*`.
2. A draft code PR was prepared to rename `weather_vector.py` + the synthetic fixture + several test files from `ch1_*` to `outdoor_*`, plus update the spec line 209 to declare `outdoor_*` canonical, plus update the cockpit to read `outdoor_*` directly.

#### Why it was retracted

Deeper research surfaced two facts the original review missed:

1. **Spec §6 line 209 EXPLICITLY declares `ch1_temp_f` and `ch1_dewpoint_f` the canonical channels** and labels `outdoor_*` as a "gateway alias, descriptive only, not used in the vector." This is a deliberate spec author decision (commit `c2f1a3a`, 2026-05-14 Phase 0), not a drift or oversight. The spec is the binding OSF artifact.
2. **The semantic argument for `ch1_*` is stronger than the portability argument for `outdoor_*`.** "Outdoor" describes a SPACE — the WS90 sun-exposed comparator is also outdoor, just biased high in direct sun. `ch1_*` describes a BINDING — channel 1 specifically, which is the operator-paired WN31 shaded reference. Channel-form names are unambiguous; space-form names require external documentation to disambiguate which "outdoor" you mean.
3. **The Phase 3 implementation (PR #138, the largest single block of analysis-side work) committed to `ch1_*` consistently** — `weather_vector.py`, the synthetic fixture, the outside-in acceptance test, the runner's expectations. The cockpit (PR #132) also reads `ch1_*`. The body of code is internally consistent at `ch1_*`; the perceived "deviation" was actually correct per spec.
4. **The data is identical either way.** Both `ch1_*` and `outdoor_*` (when `ECOWITT_SHADED_CHANNEL` is set) reflect the same channel-1 WN31 sensor reading; the poller decides which Influx field name to write based on the env var. The choice is purely a labeling preference, not a sensor-selection question.

#### What was reverted

- **Pi-lab `.env`:** `ECOWITT_SHADED_CHANNEL=1` removed at 2026-05-18T~18:50Z; `ecowitt-ingest` restarted. The poller is back to writing `ch1_*` (the spec-canonical name). The ~90 minutes of `outdoor_*` data accumulated between 17:25Z and 18:50Z is orphaned but harmless.
- **Draft code PR:** discarded. No code change was actually committed; the OI-1 branch was deleted before any commit.
- **The proposed spec edit / cockpit edit / weather_vector rename are NOT applied.** The system stays at `ch1_*` canonical end-to-end.

#### What is preserved from the original OI-1 investigation

- **The Ecowitt field-name landscape is now documented.** See the table below — useful future reference for which Influx field name comes from which gateway path.
- **The Phase 6 runner check (`weather.vector_inputs`)** was originally written under the wrong canonical assumption (`outdoor_*` expected). Corrected in the retraction PR to expect `ch1_*` per spec §6.

#### Ecowitt field-name reference (preserved as documentation, not a finding)

| Influx field | Source | Written when |
|---|---|---|
| `ch1_temp_f` / `ch1_dewpoint_f` | WN31 channel 1, shaded N/E wall | Always (when WN31 paired on ch 1) — UNLESS `ECOWITT_SHADED_CHANNEL=1`, then suppressed |
| `outdoor_temp_f` / `outdoor_dewpoint_f` | Same WN31 channel-1 reading, poller-renamed | Only when `ECOWITT_SHADED_CHANNEL` is set in poller env. Currently unset on Pi-lab. |
| `ws90_temp_f` / `ws90_dewpoint_f` | WS90 onboard sensor, sun-exposed pergola | Always — independent sun comparator, NOT canonical for shaded outdoor |
| `wind_mph`, `solar_wm2`, `rain_*`, `uv_index` | WS90 | Always — the weather-station instruments |
| `indoor_temp_f`, `indoor_rh_pct`, `pressure_inhg` | GW1200B gateway internal | Always |

**Per spec §6:** the weather vector uses `ch1_temp_f` and `ch1_dewpoint_f` as canonical shaded outdoor; `ws90_*` and `outdoor_*` are descriptive only and NOT consumed by the vector.

### OI-2 — Ecowitt continuous coverage starts 2026-05-12, not 2026-05-11

**Status:** informational, open (deferred to next OSF doc pass — small drift, no impact on shadow window validity).

**Severity:** informational. Spec §14 says Ecowitt instrumentation begins 2026-05-11; canonical `ch1_*` writes actually start 2026-05-12T21:03Z. ~1 day later than the spec-claimed limitation. Update the spec or accept the small drift in the next OSF doc pass.

## M3 scarcity-divergence audit

Per spec §11 #13 M3: for shadow-period hours where `comed.prices` 5-min mean exceeded its 95th percentile, compute absolute difference vs `pjm.lmp_rt_hourly` settled at the same hour. Report `n_hours_diverging_>2c`. If `>0`, flag for OSF appendix.

**Result: FLAGGED for OSF appendix — but with a strong shoulder-season caveat that is the headline of this section.**

### What this audit actually measured

| Metric | Value |
|---|---|
| Paired hours (ComEd hourly mean ∩ PJM hourly) | 459 |
| ComEd hourly p95 (¢/kWh) | 7.71 |
| Scarcity hours (ComEd hourly mean > p95) | 23 |
| Max abs divergence at scarcity hours (¢/kWh) | 15.30 |
| p95 abs divergence at scarcity hours (¢/kWh) | 14.80 |
| Hours diverging >2 ¢/kWh | 19 of 23 |
| Threshold (¢/kWh) | 2.0 |

### Why the headline numbers are a lower bound, not a representative finding

The audit ran against shoulder-season data (2026-04-29 → 2026-05-18). AC had barely cycled. The window's "scarcity hours" are p95 of *shoulder-season* prices (7.71¢/kWh) — that's not what real cooling-season scarcity looks like. The cooling-season experiment window (2026-06-01 → 2026-11-16) will be different in kind, not just degree.

Evidence from the historical RTP files in `tools/comed_price_imputation/data/rtp_*.txt` (full May-Sep cooling seasons):

| Year | 5-min slots ≥$1/kWh | 5-min slots ≥$2/kWh | Peak |
|---|---|---|---|
| 2023 | 3 | 0 | $1.69/kWh |
| 2024 | 4 | 1 | $2.42/kWh |
| **2025** | **52** | **12** | **$3.43/kWh** |

The 2025 cooling season had 52 five-min slots ≥$1/kWh and 12 slots ≥$2/kWh, sharply up year-over-year. Our shadow window has 2 slots ≥$2/kWh (one of which occurred during the writing of this PR, 2026-05-18 15:35-15:45 UTC, peak 214.7¢/kWh) and 4 slots ≥$1/kWh.

The 15.30¢ max-divergence number captures **shoulder-season** divergence at **shoulder-season** scarcity. A summer scarcity event at $2/kWh live could pair with a $0.20/kWh PJM settled value (180¢/kWh divergence — an order of magnitude past today's audit ceiling), or it could pair within 10¢ if PJM settles the same way. Today's audit can't tell which.

### What this means for the OSF appendix

Include the M3 finding as **directional evidence that divergence at scarcity hours is non-zero and material**, NOT as a magnitude estimate. The OSF appendix language should:

1. State the metrics as measured (above).
2. Explicitly note the shoulder-season limitation and cite the historical 2023-2025 cooling-season spike frequency as the base rate for what the experiment will actually encounter.
3. Commit to **re-running M3 mid-experiment** (post-Arm 1 close, ~2026-06-15, per Phase 7's first-arm-transition checkpoint) when real cooling-season scarcity hours + the corresponding PJM settled values finally coexist in the data.
4. Surface `live_price_vs_settled_price` as the §12 sensitivity (already pre-registered) that will produce the apples-to-apples per-pair comparison once the experiment closes.

The pre-registered sensitivity machinery in §12 is the right tool; this Phase 6 finding just tells the reader that running it will matter, without overclaiming a magnitude the data can't support.

### Today's spike (2026-05-18 15:35-15:50 UTC) — preview of what's coming

During Phase 6 PR work, ComEd live pricing spiked to **214.7¢/kWh ($2.15/kWh)** for two consecutive 5-min slots — the first $2+/kWh event in our window. The hvac-scheduler responded as designed: upgraded `normal` → `scarcity` tier at 15:41:06Z (the first decision cycle after the spike was queryable), raised cool setpoint from 67°F → 85°F, set a 30-minute hold timer for hysteresis, and stayed in scarcity through the recovery (per decision-trace logs). Scheduler is in shadow mode so no setpoint was actually pushed to the thermostat — that's correct pre-experiment behavior.

PJM settlement for the 15:00-16:00 UTC hour will publish in ~2 days. When it lands, the next workflow_dispatch run of `shadow-validation.yml` will pair the ComEd 5-min hourly mean for this hour (~61¢/kWh per the poller's own published `hourly_avg`) against PJM's settled hourly LMP and produce the first real-scarcity divergence data point. That single hour will likely dominate the audit's max-divergence figure and is worth attaching to the OSF appendix as a concrete example.

## PR #109 disposition verification

Per spec §13: "Close PR #109 as superseded immediately after the cherry-pick PRs land (mid-Phase 3, not end-of-Phase-6)."

- `gh pr view 109 --json state,closedAt` → `{"state":"CLOSED","closedAt":"2026-05-14T21:55:56Z"}`. ✅ Closed as expected.
- `git log --oneline main -- tools/analysis/queries/eagle.meter.flux` → `28f10b9 Phase 3: SCED rebaseline analysis pipeline (#138)`. ✅ The Eagle Flux query landed via Phase 3, satisfying the §13 cherry-pick requirement.
- Additionally verified: `tools/analysis/eagle_coverage.py` exists on main (Phase 3 #138) — the "Eagle coverage helper" salvageable per §13 was re-implemented per Section 10 in Phase 3.

PR #109 is closed and the spec-required cherry-picks are on main.

## Limitations (declared truth, not failures)

### LIM-0 — Runner does not exercise `arm_period_pipeline.run_full_pipeline` (spec deviation)

Spec §11 #13 says: "full dry-run on pre-experiment shadow data, exercising pipeline through Stage 5 outcome table."

The Phase 6 runner exercises Stage 1 ingestion presence + a few isolated analysis primitives (`eagle_coverage`, `mode_classification`, `arm_calendar`) but does NOT call `arm_period_pipeline.run_full_pipeline`. Pre-experiment data has no arm overlap (Arm 1 starts 2026-06-01), so a real `run_full_pipeline` call would either error or produce an empty per-pair table — neither of which is meaningful pipeline-shape validation.

**This is a deviation from the spec wording.** Per `feedback-frame-spec-deviations-honestly`: it's a deviation regardless of whether it's accepted. The deviation is partially compensated by:
- `test_pipeline_realshape_e2e.py` exercises Stage 2 loaders against real-shape fixtures.
- `test_rebaseline_end_to_end_acceptance.py` exercises `run_full_pipeline` end-to-end against synthetic fixtures with hand-pinned expected values.
- OI-1 above is exactly the kind of real-shape gap a literal "Stage 5 dry-run" would have surfaced.

**Pre-OSF recommendation (post-retraction):** OI-1 was retracted on 2026-05-18 — `ch1_*` IS the canonical field per spec §6:209 and the system was already correctly aligned. No `outdoor_*` re-fixturing is needed. The circular-validation concern (test fixtures echoing implementation rather than real-ingest schema) remains a known limitation of the current acceptance-test design, but is unrelated to OI-1's specific framing.

### LIM-1 — Pre-experiment data is outside any locked Arm

Spec §2 locks Arm 1 start at 2026-06-01 00:00 CT. The shadow window 2026-04-29 → 2026-05-18 has zero overlap with any arm. `arm_calendar.current_arm_at` correctly returns `None` for every shadow hour. Per spec §11 #13, this is pipeline-shape validation only — NOT outcome evidence.

### LIM-2 — `weather_vector.build_weather_vector(arm, ecowitt_df)` cannot be exercised against shadow data

The function requires an `ArmPeriod` and the post-washout window of that arm to overlap the data. Schema + NOAA-fallback lock are validated upstream as proxy; the function itself is covered by `test_weather_vector.py` against synthetic `ArmPeriod` fixtures consuming the canonical `ch1_*` fields per spec §6:209. (Pre-retraction this paragraph mentioned an OI-1 follow-up to re-fixture against `outdoor_*`; OI-1 was retracted on 2026-05-18 and no re-fixturing is needed.)

### LIM-3 — First DTOD bill arrives 2026-05-24

Per spec §14. `bill_reconciliation.reconcile_bill_period` cannot be exercised against shadow window data. Pre-DTOD reconciliation uses flat rates and is tracked separately.

### LIM-4 — Ecowitt push receiver started 2026-05-11 but canonical `ch1_*` writes started ~25 hours later

The push receiver came up 2026-05-11 22:30 UTC and wrote 42 rows of `outdoor_*` over ~40 minutes (this is the descriptive gateway-alias field, NOT the canonical analysis source). Continuous canonical `ch1_*` writes began 2026-05-12T21:03Z. Spec §14's "2026-05-11" instrumentation-start date is correct for the push receiver coming online; the canonical-stream start lags by ~25 hours, captured as OI-2. (Pre-retraction this paragraph framed the timeline around `outdoor_*` being canonical; OI-1 was retracted on 2026-05-18 — `ch1_*` is canonical per spec §6:209, so the timeline above is the corrected framing.)

### LIM-5 — PJM `rt_hrl_lmps` data lags real-time by ~2 days

Per PJM's normal T+2 settle lag for `rt_hrl_lmps`. Latest run shows data through 2026-05-18T03:00Z (run captured at 2026-05-18T17:15Z). Not a poller issue. For arm-period analyses ending more than 2 days before render time this is invisible.

### LIM-6 — Local-fallback execution

The runner ran from a Windows workstation against pi-lab Influx over the LAN rather than on pi-lab itself, because `workflow_dispatch` requires the workflow file on the default branch first. The workflow `.github/workflows/shadow-validation.yml` becomes the canonical pi-lab execution path after this PR merges. The shadow data is the same in either path — only the execution host differs.

## Sign-off

Phase 6 shadow validation surfaces:

- **OI-1 RETRACTED** (see [Open issues above](#oi-1--retracted-2026-05-18-post-merge-investigation)). Original conclusion that `outdoor_*` was canonical was wrong; spec §6 declares `ch1_*` canonical and the system was already correctly aligned. The Pi-lab `.env` change was reverted; no code change shipped. Net pre-OSF impact from OI-1: zero, plus a tightened understanding of the Ecowitt field-name landscape documented in the retracted section.
- **One directional, NOT magnitude finding (M3)** — see [M3 section above](#m3-scarcity-divergence-audit). The audit ran against shoulder-season data only; the 15.30¢ max-divergence figure is a lower bound on a season ComEd had barely cycled AC for, NOT a representative number. The OSF appendix should disclose the audit ran, cite the historical 2023-2025 cooling-season spike base rate as the actual expected exposure, and commit to a mid-experiment re-run (Phase 7 first-arm-transition checkpoint, ~2026-06-15) for the real measurement.

All other validation checks are PASS or the expected pre-experiment N/A. Spec deviation re: Stage 5 run is documented as LIM-0.

**Phase 6 sign-off (post-retraction):** runner + workflow + findings deliverable complete. Spec §11 #13 closed.
- OI-1 → RETRACTED. The system was already correctly aligned with spec §6. No follow-up PR required.
- M3 → directional flag for OSF appendix + mid-experiment re-run scheduled into Phase 7's operational checkpoint. No code change for a magnitude estimate; that's data-collection waiting on real cooling-season events.

**Lesson preserved from the back-and-forth:** when a code review surfaces a perceived spec/impl mismatch, read the binding spec text BEFORE recommending which direction to align. The Phase 6 superpowers reviewer reasoned outward from the poller's docstring + pre-rebaseline pipeline.py and concluded `outdoor_*` was canonical; that reasoning missed spec §6 line 209's explicit declaration that `ch1_*` is canonical and `outdoor_*` is "gateway alias, descriptive only." Both directions were technically defensible, but the spec is the binding artifact — the spec wins ties. Memory captured: `feedback-check-binding-spec-before-canonical`.
