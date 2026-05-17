#!/usr/bin/env python3
"""Parse a ComEd bill PDF and write its data to InfluxDB.

Usage:
    python parse_comed_bill.py <bill.pdf>

Environment (with defaults pointing at Pi-lab energy-stack):
    INFLUX_URL    (default http://localhost:8086)
    INFLUX_TOKEN  (required)
    INFLUX_ORG    (default 'depaola-home')
    INFLUX_BUCKET (default 'energy')

On success: moves the PDF to inbox/comed/processed/comed-YYYY-MM-DD-<period>.pdf
On failure: leaves the PDF in place, prints error, exits non-zero.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

from comed_parser import parse_pdf, BillParseError
from comed_influx import write_bill


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Path to a ComEd bill PDF")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse + print line protocol; do not write to Influx")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.is_file():
        print(f"error: {pdf_path} is not a file", file=sys.stderr)
        sys.exit(2)

    try:
        bill = parse_pdf(pdf_path)
    except (BillParseError, ValueError) as e:
        print(f"error: parse failed: {e}", file=sys.stderr)
        sys.exit(3)

    print(
        f"Parsed: {bill.service_from} -> {bill.service_to} ({bill.service_days}d) "
        f"{bill.kwh} kWh  ${bill.total_due:.2f}  "
        f"plan={bill.rate_plan}  type={bill.bill_type}  "
        f"peak_kw={bill.peak_kw}"
    )

    if args.dry_run:
        from comed_influx import bill_to_line_protocol
        print()
        print(bill_to_line_protocol(bill))
        return

    token = os.environ.get("INFLUX_TOKEN")
    if not token:
        print("error: INFLUX_TOKEN env var is required", file=sys.stderr)
        sys.exit(4)

    write_bill(
        bill,
        url=os.environ.get("INFLUX_URL", "http://localhost:8086"),
        token=token,
        org=os.environ.get("INFLUX_ORG", "depaola-home"),
        bucket=os.environ.get("INFLUX_BUCKET", "energy"),
    )
    print(f"  wrote to InfluxDB: 1 comed.bill + {len(bill.line_items)} bill_lineitems")

    # Move PDF to processed/. Influx write already succeeded above, so on
    # filesystem failure here we tell the operator the data is safe and the
    # move is the only thing left to retry (idempotent — re-running the script
    # upserts the same Influx points and tries the move again).
    processed_dir = pdf_path.parent / "processed"
    processed_dir.mkdir(exist_ok=True)
    new_name = f"comed-{bill.service_to.isoformat()}-{bill.service_from.isoformat()}.pdf"
    dest = processed_dir / new_name
    try:
        shutil.move(str(pdf_path), str(dest))
    except OSError as e:
        print(
            f"warning: Influx write succeeded but move failed: {e}\n"
            f"  PDF still at {pdf_path}; safe to re-run (Influx write is idempotent).",
            file=sys.stderr,
        )
        sys.exit(5)
    print(f"  moved to {dest}")


if __name__ == "__main__":
    main()
