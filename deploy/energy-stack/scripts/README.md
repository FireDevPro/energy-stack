# energy-stack scripts

Operator scripts for the energy-stack. Each is a single-purpose Python script
run by hand (not a service). Tests run with `pytest`; deps in `requirements.txt`.

## randomize_arms.py — pre-registered experiment-arm assignment

Generates the binding Arm A / Arm B week-level assignment for the residential
HVAC controls field study described in [`docs/EXPERIMENT_DESIGN.md`](../../../docs/EXPERIMENT_DESIGN.md).
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

This script is the binding artifact behind the OSF pre-registration. **Do not
modify the algorithm or default seed without filing an OSF amendment.** The
pinned-snapshot test in `tests/test_randomize_arms.py` will fail loud if the
seed-to-output mapping ever drifts.

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
INFLUX_ORG=home
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
- `account_no != 9999999991` (the household's account; guard against
  feeding someone else's bill)
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
