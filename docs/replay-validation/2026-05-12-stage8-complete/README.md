---
date: 2026-05-12
owner: chris
status: live
role-label: chris
---

# Stage 8 close-out replay validation (2026-05-12)

Phase 5 of the Stage 8 decomposition loader thread. Replay-validation 7d
+ 90d run against pi-lab Influx, exercising the Phase 0-5 Stage 8 code
path end-to-end against real production data.

## What was run

Two windows, both ending 2026-05-12T22:00Z:

| Window | Start (UTC) | End (UTC) | Path |
|---|---|---|---|
| 7d | 2026-05-05T22:00Z | 2026-05-12T22:00Z | `7d/` |
| 90d | 2026-02-11T22:00Z | 2026-05-12T22:00Z | `90d/` |

Pipeline stages exercised: Stage 1 (extract) -> Stage 2 (quality) ->
Stage 3 (weekly) -> Stage 8 (decomposition + layer attribution).

Stages 4-7 not exercised in this run; Stage 8 does not depend on them.

## Expected behavior (pre-experiment)

The experiment randomization starts 2026-06-01. We are running 19 days
before that, so the locked assignment CSV has no Mondays in the replay
windows. The pipeline correctly reasons out upstream:

- Stage 2: `NO_ARM_ASSIGNMENTS_IN_WINDOW` for both windows.
- Stage 3: empty weekly.csv (no Stage 2 rows feed in).
- Stage 8: `NO_QUALIFYING_DAYS_FROM_STAGE3` emitted for BOTH outputs
  (decomposition.csv + layer_attribution.csv).

This validates the Phase 5 reason-code contract end-to-end.

## What both runs prove

- Stage 1 extracts cleanly against pi-lab; no schema drift surfaced.
- Stage 8 emits the Phase 5 reason codes correctly for empty-upstream
  cases (validates Gate 1).
- `stage8/provenance.json` populates with all seven sections
  (validates Gate 2). With empty inputs the derived sections are empty
  dicts/lists; `bundle_window` correctly carries the manifest's window.
- No `placeholder-zero` outcome rows leak into the output (validates
  Gate 3) — decomposition.csv is header-only with reason, not zeros.
- Layer attribution side-table is header-only with the same reason,
  as expected when no qualifying days exist.

## Limitations of this replay

- No qualifying days means no day-exclusion / forecast-classification /
  layer-attribution paths exercised against real data. Those paths are
  pinned by 19+ synthetic-fixture acceptance tests in
  `tools/analysis/tests/test_stage8_loader_realshape.py`.
- First post-experiment-start weekly run (summer 2026) is when Stage 8
  produces meaningful output. The Stage 8 feature is feature-complete
  at the code level after this PR merges; observability of real
  decomposition values waits for the first qualifying week.

## Artifacts in this directory

Each window directory contains:
- `inspection.json` — structured per-stage summary from
  `tools.analysis.replay_inspect`.
- `stage8/decomposition.csv` — header-only as expected.
- `stage8/layer_attribution.csv` — header-only as expected.
- `stage8/reason_report.json` — Phase 5 reason codes.
- `stage8/provenance.json` — all 7 sections, sort_keys=True.

Raw Stage 1 parquet / Stage 2-3 CSVs not committed (they live in
`analysis/exports/` which is gitignored).

## Reproduction

Bring up the local SSH tunnel forwarding pi-lab's Influx to
`localhost:18086` (host details in `HANDOFF.md`).

A one-shot driver script captured the token and ran the pipeline; it
is not committed (lives outside the working tree). Equivalent steps:

```python
import datetime, os
os.environ["INFLUXDB_URL"] = "http://localhost:18086"
os.environ["INFLUXDB_INIT_ADMIN_TOKEN"] = "<from pi-lab .env>"
os.environ["INFLUXDB_INIT_ORG"] = "<from pi-lab .env>"
os.environ["INFLUXDB_INIT_BUCKET"] = "<from pi-lab .env>"

from tools.analysis import pipeline
from pathlib import Path

end = datetime.datetime.now(tz=datetime.timezone.utc).replace(
    minute=0, second=0, microsecond=0)
start = end - datetime.timedelta(days=7)  # or 90
out = Path("analysis/exports/phase5-replay")
out.mkdir(parents=True, exist_ok=True)
pipeline.stage1_extract(start, end, out)
pipeline.stage2_quality(out / "stage1", out)
pipeline.stage3_weekly(out / "stage1", out / "stage2", out)
pipeline.stage8_decomposition(out / "stage1", out / "stage3", out)
```

Then `python -m tools.analysis.replay_inspect <out_dir>` to generate
`inspection.json`.
