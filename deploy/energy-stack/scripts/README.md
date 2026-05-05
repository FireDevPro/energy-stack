# energy-stack scripts

Operator scripts for the energy-stack. Each is a single-purpose Python script
run by hand (not a service). Tests run with `pytest`; deps in `requirements.txt`.

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

1. ComEd email arrives → click "View bill" on portal → download PDF (2FA required)
2. From workstation:
   ```
   scp <bill>.pdf pi-lab:~/energy-stack/inbox/comed/
   ssh pi-lab
   cd ~/energy-stack/scripts && source .venv/bin/activate
   set -a; source ~/energy-stack/.env; set +a
   python parse_comed_bill.py ~/energy-stack/inbox/comed/<bill>.pdf
   ```
3. Script: parses → writes to Influx → moves PDF to `inbox/comed/processed/comed-YYYY-MM-DD-...pdf`

### Optional: workstation alias

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

Loop over historical bills:
```
for f in ~/energy-stack/inbox/comed/backfill/*.pdf; do
    python parse_comed_bill.py "$f"
done
```
Idempotency makes this re-runnable. Duplicates collapse automatically.
