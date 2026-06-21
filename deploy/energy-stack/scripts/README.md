---
date: 2026-05-18
owner: chris
status: active
role-label: code-team
---

# energy-stack scripts

Operator scripts for the energy-stack. Each is a single-purpose Python script
run by hand (not a service). Tests run with `pytest`; deps in `requirements.txt`.

## fit_thermal_observer.py — read-only house thermal response fit

Fits the house thermal response from existing telemetry and prints diagnostics.
This script is **strictly read-only**: it does not write thermostat setpoints,
does not write derived InfluxDB measurements, does not write JSON artifacts, and
does not feed `hvac-scheduler` decisions.

Inputs:

- `hvac.thermostat` for indoor temperature and thermostat state.
- `hvac.comfortnet` for equipment stage/runtime signals.
- Local weather, defaulting to `ecowitt.outdoor`.

Override the weather measurement when needed:

```bash
python fit_thermal_observer.py --outdoor-measurement weather.ecowitt
```

### One-time setup

On Pi-lab:

```bash
cd ~/energy-stack/scripts
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Manual runs

Run against the existing stack environment:

```bash
cd ~/energy-stack/scripts
source .venv/bin/activate
set -a; source ~/energy-stack/.env; set +a
export INFLUX_URL="${INFLUXDB_URL:-${INFLUX_URL:-http://localhost:8086}}"
export INFLUX_TOKEN="${INFLUXDB_TOKEN:-${INFLUXDB_INIT_ADMIN_TOKEN:-${INFLUX_TOKEN}}}"
export INFLUX_ORG="${INFLUXDB_ORG:-${INFLUXDB_INIT_ORG:-${INFLUX_ORG}}}"
export INFLUX_BUCKET="${INFLUXDB_BUCKET:-${INFLUXDB_INIT_BUCKET:-${INFLUX_BUCKET}}}"
python fit_thermal_observer.py --window-days 14
```

`--dry-run` is accepted as a compatibility no-op. All runs are read-only.

### Printed diagnostics

Key printed fields:

| Name | Notes |
|---|---|
| `accepted` | Whether plausibility and skill gates accepted the fit. |
| `tau_hours` | Fitted thermal time constant. |
| `stage1_cooling_f_per_hr` | Stage-1 cooling rate. |
| `stage2_cooling_f_per_hr` | Full stage-2 cooling rate. |
| `skill_score` | Holdout skill vs baseline. |
| `filter_counts` | Gap, setpoint mask, heating-active, non-finite, and valid interval counts. |

### Suggested cron

After several manual runs have been validated, log nightly output only:

```cron
17 3 * * * bash -lc 'cd ~/energy-stack/scripts && . .venv/bin/activate && set -a; . ~/energy-stack/.env; set +a; export INFLUX_URL="${INFLUXDB_URL:-${INFLUX_URL:-http://localhost:8086}}"; export INFLUX_TOKEN="${INFLUXDB_TOKEN:-${INFLUXDB_INIT_ADMIN_TOKEN:-${INFLUX_TOKEN}}}"; export INFLUX_ORG="${INFLUXDB_ORG:-${INFLUXDB_INIT_ORG:-${INFLUX_ORG}}}"; export INFLUX_BUCKET="${INFLUXDB_BUCKET:-${INFLUXDB_INIT_BUCKET:-${INFLUX_BUCKET}}}"; python fit_thermal_observer.py --window-days 14 >> /var/log/thermal-observer.log 2>&1'
```

## randomize_arms.py — DEPRECATED (pre-rebaseline historical artifact)

> [!WARNING]
> **Retired 2026-05-13** by the SCED rebaseline (its spec doc has since been removed in the controller demolition). The current experiment uses **deterministic 14-day arm alternation** (12 arms total, 6 Arm A + 6 Arm B) — no PRNG seed, no 4-week blocks, no weekly assignment CSV.
>
> The canonical arm calendar is now generated programmatically by [`tools/analysis/arm_calendar.py`](../../../tools/analysis/arm_calendar.py) (analysis-side) and [`deploy/energy-stack/hvac_scheduler/arm_calendar.py`](../hvac_scheduler/arm_calendar.py) (controller-side; byte-identical, CI hash-sync checked). The original `randomize_arms.py` script and its output `docs/experiment-assignments-summer-2026.csv` are preserved in-tree as historical artifacts (and to keep `tests/test_randomize_arms.py` pinning the original algorithm for audit traceability) but **should not be run** for any current operation. Tracked since [PR #137 F3 deferral](https://github.com/FireDevPro/energy-stack/pull/137).

Generates the original Arm A / Arm B week-level assignment for the residential
HVAC controls field study described in [`docs/archive/EXPERIMENT_DESIGN.md`](../../../docs/archive/EXPERIMENT_DESIGN.md).
Block-of-2 randomization using a pre-committed seed (default `20260601` from
EXPERIMENT_DESIGN.md §13). Same seed + same date range → same CSV, every time.

Default run produces [`docs/experiment-assignments-summer-2026.csv`](../../../docs/experiment-assignments-summer-2026.csv):

```bash
python randomize_arms.py
# → 18 rows, 9 Arm A weeks + 9 Arm B weeks, 2026-06-01 .. 2026-09-28
```

Other invocations:

- Future cooling-season run: `--seed 20270601 --start 2027-06-01 --end 2027-09-30`
- Inspect without writing: `--dry-run`
- Custom output path: `--output /tmp/foo.csv`

This script WAS the binding artifact behind the original OSF pre-registration draft.
Pre-rebaseline, the directive read: **Do not modify the algorithm or default seed without filing an OSF amendment.** The
pinned-snapshot test in `tests/test_randomize_arms.py` still fails loud if the
seed-to-output mapping ever drifts, preserving the original artifact for audit.

## commission_decision_trace_path_c.py — removed 2026-06-20

This synthetic decision-trace event exerciser was removed with the
day-type / precool / 5CP / overrides / forecast controller tear-out. It
imported deleted rule functions (`decide_day_type`,
`resolve_layer_priority`, `validate_setpoints`,
`compute_price_aware_precool_window`) and their `_trace_*` helpers, all of
which no longer exist, so it could only ImportError. The commissioning
controller is reactive-price-overlay only; there is no day-type / precool
trace path left to exercise.

## log_arm_transition.py — removed 2026-06-15

This manual audit logger was removed. It was built on a per-arm AIR-toggle
concept that was never the design (AIR is fixed OFF in both arms), was never
run (its `hvac.arm_transitions` measurement has zero rows), and was redundant
with the deterministic arm calendar.

**When to run:** every Monday at the arm-boundary crossover (2026-06-01,
2026-06-15, 2026-06-29, ... per the canonical arm calendar at
[`tools/analysis/arm_calendar.py`](../../../tools/analysis/arm_calendar.py)).
Once `hvac-scheduler` writes `hvac.switch_event` automatically per spec §11
#3, this script becomes a manual override / backfill tool only.

## backfill_pjm.py — one-shot 5-year ComEd PJM backfill

Pulls historical hourly data the live `pjm-dm2-poller` doesn't cover (it only
writes today's / forward-looking points). Two feeds, both ComEd-zone-only:

- `da_hrl_lmps` for `pnode_id=33092371` → `pjm.lmp_da_hourly`
- `hrl_load_metered` for `zone=CE` → `pjm.metered_load`

**Note**: ComEd's PJM zone code is `CE` (Commonwealth Edison), not `COMED`,
in `hrl_load_metered`. Filtering on `zone=COMED` returns zero rows.
Empirically verified — see the `test_comed_zone_code_is_CE_not_COMED`
test guard.

### Usage

```bash
python backfill_pjm.py                          # default 2021..(last full year)
python backfill_pjm.py --years 2024,2025
python backfill_pjm.py --feed metered_load      # one feed only
python backfill_pjm.py --dry-run
```

### Archive boundary (phase 1 limitation)

PJM splits `da_hrl_lmps` data into "standard" (last 731 days) and
"archived" (older). Archive queries reject the `pnode_id` filter, so they
need different query logic (filter by `type=Zone`, parse client-side).
This script targets the standard tier only and **skips** years that fall
entirely or partially before the archive boundary, with a clear log line:

```
2022: SKIPPED (archive) -- da_hrl_lmps year 2022: starts 2022-01-01,
older than archive cutoff 2024-05-05 (731-day window). Phase 1 backfill
skips this; archive-tier support is phase 2.
```

`hrl_load_metered` has no archive cutoff (`archiveCutoffDays=null` in its
metadata), so all years backfill cleanly regardless of age.

### Output (verified live, dry-run)

For `python backfill_pjm.py --years 2024,2025 --dry-run`:

```
da_lmp  2024: SKIPPED (archive)
da_lmp  2025: 8760 rows -> 8760 points
metered 2024: 8784 rows -> 8784 points (leap year, 366 days × 24h)
metered 2025: 8760 rows -> 8760 points
Total: 26,304 points in ~37 seconds
```

Idempotent: re-runs upsert by `(measurement, tag set, timestamp)`. Safe to
re-run for "this year" weekly to keep partial-year data fresh without
double-counting.

### Sequencing

After `pjm-dm2-poller` is live and writing forward, this script lands the
historical training corpus the future 5CP-probability classifier needs.
Run order on pi-lab:

```bash
# After pjm-dm2-poller deployed and a few days of live data are in:
python backfill_pjm.py --feed metered_load --dry-run     # sanity check
python backfill_pjm.py --feed metered_load               # 5 yrs metered load
python backfill_pjm.py --feed da_lmp                     # last 1-2 yrs DA LMP
python scrape_pjm_5cp_pdf.py --years 2021,2022,2023,2024,2025  # 5CP labels
```

## scrape_pjm_5cp_pdf.py — annual PJM 5CP coincident-peak PDF ingest

The 5 hours per summer that determine the next year's PJM capacity charges
are not exposed in the Data Miner 2 API at the Non-Member tier. PJM publishes
them in an annual PDF, revised mid-November of the same year. This script
fetches and parses that PDF, then writes each peak as a point to the
`pjm.coincident_peak` InfluxDB measurement.

### Usage

```bash
python scrape_pjm_5cp_pdf.py                      # last summer (default)
python scrape_pjm_5cp_pdf.py --year 2024
python scrape_pjm_5cp_pdf.py --years 2021,2022,2023,2024,2025  # backfill
python scrape_pjm_5cp_pdf.py --pdf /path/to/local.pdf --year 2024
python scrape_pjm_5cp_pdf.py --year 2024 --dry-run
```

### Annual cron (pi-lab)

The PDF is published mid-November. A single cron entry around November 20
each year keeps the dataset current:

```cron
0 9 20 11 * cd ~/energy-stack/scripts && source .venv/bin/activate && \
  set -a; source ~/energy-stack/.env; set +a && \
  python scrape_pjm_5cp_pdf.py >> /var/log/pjm-5cp.log 2>&1
```

### Output schema

One Influx point per (summer, peak_rank) pair:

| Element | Value |
|---|---|
| Measurement | `pjm.coincident_peak` |
| Tag | `summer_year` (e.g. `"2024"`) |
| Tag | `peak_rank` (`"1"` highest RTO MW through `"5"` lowest of the five) |
| Field | `peak_load_mw` (RTO peak in MW) |
| Field | `comed_zone_load_mw` (ComEd's coincident load in MW) |
| Timestamp | The actual 5CP hour, **hour-beginning EPT**. PJM publishes "Hour Ending EPT" so we subtract 1 hour for stack-wide hour-beginning consistency. |

### Validation gates (will refuse to write if any fail)

- Exactly 5 peaks parsed from page 1
- Exactly 5 ComEd-zonal MW values from page 2
- All 5 dates fall within (June 1, October 31) of the target year
- RTO peak MW values within `[50_000, 250_000]` (sanity)
- ComEd zonal MW values within `[5_000, 30_000]` (sanity)
- Year extracted from PDF header matches `--year` flag

If any gate fails, no Influx writes happen and the script exits non-zero.
This is the layout-drift-detection mechanism: if PJM changes the PDF format
materially, we fail loud rather than silently writing garbage.

### URL pattern

`https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/summer-{YEAR}-peaks-and-5cps.pdf`

Confirmed working for summers 2021 through 2025. Older years (≤2020) either
don't exist at that URL pattern or PJM never published them at that location;
manual handling required if pre-2021 data is ever needed.

## parse_comed_bill.py — ComEd bill ingest

Parses a ComEd bill PDF and writes the structured data to InfluxDB:

- One `comed.bill` point (top-line: total_due, kwh, peak_kw, supply/delivery/taxes/misc totals)
- N `comed.bill_lineitems` points (full breakdown by category + line item)

### One-time setup

On Pi-lab:
```bash
cd ~/energy-stack/scripts
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mkdir -p ~/energy-stack/inbox/comed
```

Add to `~/energy-stack/.env` (or shell profile):
```
INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=<from energy-stack/.env>
INFLUX_ORG=depaola-home
INFLUX_BUCKET=energy
```

### Monthly workflow

The actual normal path: Chris hands the PDF to Claude Code in chat once a
month and asks it to ingest. Claude runs the same scp + ssh flow below — the
operator-direct path is the fallback for when Chris wants to do it himself.

1. ComEd email arrives → click "View bill" on portal → download PDF (2FA required)
2. From workstation (or from Claude after Chris drops the PDF):
   ```
   scp <bill>.pdf pi-lab:~/energy-stack/inbox/comed/
   ssh pi-lab "cd ~/energy-stack/scripts && source .venv/bin/activate && \
     set -a; source ~/energy-stack/.env; set +a && \
     python parse_comed_bill.py ~/energy-stack/inbox/comed/<bill>.pdf"
   ```
3. Script: parses → writes to Influx → moves PDF to `inbox/comed/processed/comed-YYYY-MM-DD-...pdf`
4. Claude (or operator) verifies the new bill landed and the dashboard updates correctly. Common quick-checks:
   - `comed.bill_lineitems` for the new period has expected categories present (SUPPLY, DELIVERY, TAXES_FEES_CREDITS)
   - `total_due` ≈ `supply + delivery + taxes + misc` (the script's validation gate enforces this; if it wrote, totals balanced)
   - Capacity Charge `peak_kw` and `amount` match the prior month for any bill before June 2026 (PJM 5CP is locked annually); after June 2026, watch for the step-change.

### Optional: workstation alias for the operator-direct path

In your workstation shell profile:
```bash
comed-ingest() {
    scp "$1" pi-lab:~/energy-stack/inbox/comed/ &&
    ssh pi-lab "cd ~/energy-stack/scripts && source .venv/bin/activate && set -a; source ~/energy-stack/.env; set +a; python parse_comed_bill.py ~/energy-stack/inbox/comed/$(basename "$1")"
}
```

Then: `comed-ingest ~/Downloads/comed-bill.pdf`

### Idempotency

Re-running the same bill upserts the same Influx points because the
`(measurement, tag set, timestamp)` tuple — `(comed.bill, account_no +
rate_plan + bill_type, service_to @ 23:59:59 CDT)` — is identical between
runs. Safe to retry on errors.

If ComEd re-issues a bill with corrections (same service window, different
amounts), re-running this script overwrites the previous values for that
window. There's no audit history kept of the prior amounts.

### Validation

The parser refuses to write if any of these fail (PDF stays in inbox,
non-zero exit code):
- Required fields missing (kWh, total due, rate plan, etc.)
- `supply + delivery + taxes + misc != total_due` (within $0.01)
- `service_days` doesn't match calendar diff by more than 1 day

### Exit codes

| Code | Meaning                                          |
|------|--------------------------------------------------|
| 0    | Success — Influx written, PDF moved              |
| 2    | Argument is not a file                           |
| 3    | Parse failed (validation gate or extractor)      |
| 4    | `INFLUX_TOKEN` env var missing                   |
| 5    | Influx write succeeded but PDF move failed (safe to re-run) |

### Dry run

Add `--dry-run` to parse + print the generated line protocol without writing:
```
python parse_comed_bill.py file.pdf --dry-run
```

### Backfill

Loop over historical bills using the same script per file:
```
for f in ~/energy-stack/inbox/comed/backfill/*.pdf; do
    python parse_comed_bill.py "$f"
done
```
Idempotency makes this re-runnable. Duplicates collapse automatically.

### Generating line protocol locally (rare path)

For batch backfill of many bills at once, or for debugging a parse offline,
you can call `comed_parser.parse_pdf` + `comed_influx.bill_to_line_protocol`
directly to write `.lp` files, then apply with `influx write --file`. **One
gotcha if you do this on Windows**: Python's text-mode file writes convert
`\n` to `\r\n`, and InfluxDB's `influx write` rejects every line as "bad
timestamp" because the trailing `\r` attaches to the ns integer. Two ways to
avoid:

- **Strip on Pi after scp**: `sed -i 's/\r$//' /tmp/backfill.lp` then apply
- **Write LF directly from Python**: open the file with `newline=""` (or
  `open(path, "wb")` and encode manually)

The production script (`parse_comed_bill.py`) is immune — it writes via the
`influxdb_client` library over HTTP, which never serializes through a file,
so the CRLF issue cannot occur on the normal path.
