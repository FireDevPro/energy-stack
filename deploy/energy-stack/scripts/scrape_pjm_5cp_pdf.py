#!/usr/bin/env python3
"""Scrape PJM's annual 5CP coincident-peak PDF and write to InfluxDB.

The official PJM 5CP-coincident-peak hours (the 5 hours per summer that
determine the next year's capacity charges) are NOT exposed in the Data
Miner 2 API at the Non-Member tier. PJM publishes them in an annual PDF,
revised mid-November of the same year (after sufficient meter data is in).

URL pattern:
    https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/summer-YYYY-peaks-and-5cps.pdf

This script fetches and parses that PDF for one or more years, and writes
each peak as a point to the `pjm.coincident_peak` InfluxDB measurement.
Schema (per docs/PJM_DM2_INTEGRATION.md):

  measurement: pjm.coincident_peak
  tags:        summer_year (e.g. "2024"), peak_rank (1..5)
  fields:      peak_load_mw (RTO peak at the 5CP hour, MW)
               comed_zone_load_mw (ComEd's coincident load at that hour, MW)
  timestamp:   the actual 5CP hour, hour-beginning EPT (PJM publishes
               hour-ending; we subtract 1h for consistency with the rest of
               the stack which uses hour-beginning timestamps)

Usage:
    python scrape_pjm_5cp_pdf.py                    # scrape last summer
    python scrape_pjm_5cp_pdf.py --year 2024
    python scrape_pjm_5cp_pdf.py --years 2020,2021,2022,2023,2024
    python scrape_pjm_5cp_pdf.py --pdf /path/to/local.pdf --year 2024
    python scrape_pjm_5cp_pdf.py --year 2024 --dry-run

Environment (matches parse_comed_bill.py):
    INFLUX_URL    (default http://localhost:8086)
    INFLUX_TOKEN  (required unless --dry-run)
    INFLUX_ORG    (default depaola-home)
    INFLUX_BUCKET (default energy)

Validation gates (script refuses to write if any fail):
  * Exactly 5 peaks parsed from page 1
  * Exactly 5 ComEd-zonal MW values parsed from page 2
  * RTO peak MW values are within plausibility range [50_000, 250_000]
  * ComEd zonal MW values are within plausibility range [5_000, 30_000]
  * All 5 dates fall within (June 1, October 31) of the target year
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pypdf import PdfReader


PDF_URL_TEMPLATE = (
    "https://www.pjm.com/-/media/DotCom/planning/res-adeq/load-forecast/"
    "summer-{year}-peaks-and-5cps.pdf"
)

DAY_NAMES = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

# "Tuesday 7/16/2024 18:00 152,307"
PEAK_ROW_RE = re.compile(
    r"^(?P<day>[A-Z][a-z]+)\s+"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<hour>\d{1,2}:\d{2})\s+"
    r"(?P<mw>[\d,]+(?:\.\d+)?)\s*$"
)

# "COMED 16,972.5    19,014.1    17,556.2    18,568.7    16,726.8"
COMED_ROW_RE = re.compile(
    r"^COMED\s+"
    r"(?P<m1>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<m2>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<m3>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<m4>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<m5>[\d,]+(?:\.\d+)?)"
)


class CoincidentPeakParseError(Exception):
    """Raised when the PDF doesn't match the expected layout (validation gate)."""


@dataclass(frozen=True)
class CoincidentPeak:
    summer_year: int
    peak_rank: int                  # 1 = highest RTO MW, 5 = lowest of the 5
    hour_beginning_local: datetime  # tz-naive EPT, hour-beginning convention
    rto_peak_mw: float
    comed_zone_mw: float

    def to_dict(self) -> dict[str, object]:
        return {
            "summer_year": self.summer_year,
            "peak_rank": self.peak_rank,
            "hour_beginning_ept": self.hour_beginning_local.isoformat(),
            "rto_peak_mw": self.rto_peak_mw,
            "comed_zone_mw": self.comed_zone_mw,
        }


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def parse_5cp_block(text: str, summer_year: int) -> list[tuple[datetime, float]]:
    """Parse the page-1 '<Year> RTO Coincident Peaks (5CP)' table.
    Returns 5 (hour_beginning_ept, rto_mw) tuples in descending-MW order.

    Hour conversion: PDF lists 'Hour Ending EPT'. We subtract 1 hour so the
    timestamp marks the start of the peak hour (consistent with the rest of
    the stack which timestamps everything hour-beginning)."""
    out: list[tuple[datetime, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not any(line.startswith(d) for d in DAY_NAMES):
            continue
        m = PEAK_ROW_RE.match(line)
        if not m:
            continue
        date = datetime.strptime(m["date"], "%m/%d/%Y").date()
        hour, minute = map(int, m["hour"].split(":"))
        hour_ending = datetime(date.year, date.month, date.day, hour, minute)
        hour_beginning = hour_ending - timedelta(hours=1)
        out.append((hour_beginning, _to_float(m["mw"])))
    if len(out) != 5:
        raise CoincidentPeakParseError(
            f"expected 5 peaks for summer {summer_year}, parsed {len(out)}"
        )
    # All within target year, June-October
    for hb, _ in out:
        if hb.year != summer_year or hb.month not in (6, 7, 8, 9, 10):
            raise CoincidentPeakParseError(
                f"peak {hb.isoformat()} outside summer {summer_year} window"
            )
    return out


def parse_comed_zonal_row(text: str) -> list[float]:
    """Parse the page-2 zonal-coincident table to extract the 5 ComEd MW values.
    Returns them in the same column order as the page header (which matches
    page-1 descending-MW order)."""
    for line in text.splitlines():
        m = COMED_ROW_RE.match(line.strip())
        if not m:
            continue
        return [_to_float(m[f"m{i}"]) for i in range(1, 6)]
    raise CoincidentPeakParseError("ComEd zonal row not found on page 2")


def extract_summer_year(text: str) -> int | None:
    """Find the 'Summer YYYY ... Coincident Peaks (5CP)' marker for sanity-check."""
    m = re.search(r"Summer\s+(\d{4}).*Coincident Peaks", text)
    if m:
        return int(m[1])
    return None


def parse_pdf(pdf_bytes: bytes, expected_year: int) -> list[CoincidentPeak]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) < 2:
        raise CoincidentPeakParseError(f"expected >= 2 pages, got {len(reader.pages)}")
    page1 = reader.pages[0].extract_text() or ""
    page2 = reader.pages[1].extract_text() or ""

    parsed_year = extract_summer_year(page1)
    if parsed_year is not None and parsed_year != expected_year:
        raise CoincidentPeakParseError(
            f"year mismatch: caller said {expected_year}, PDF says {parsed_year}"
        )

    peaks = parse_5cp_block(page1, expected_year)  # 5 (datetime, rto_mw)
    comed_mw = parse_comed_zonal_row(page2)         # 5 floats

    out: list[CoincidentPeak] = []
    for rank, ((hb, rto), comed) in enumerate(zip(peaks, comed_mw), start=1):
        # Plausibility gates
        if not (50_000 <= rto <= 250_000):
            raise CoincidentPeakParseError(
                f"rank {rank} RTO peak {rto} MW outside [50_000, 250_000]"
            )
        if not (5_000 <= comed <= 30_000):
            raise CoincidentPeakParseError(
                f"rank {rank} ComEd zonal {comed} MW outside [5_000, 30_000]"
            )
        out.append(CoincidentPeak(
            summer_year=expected_year,
            peak_rank=rank,
            hour_beginning_local=hb,
            rto_peak_mw=rto,
            comed_zone_mw=comed,
        ))
    return out


def fetch_pdf(year: int) -> bytes:
    url = PDF_URL_TEMPLATE.format(year=year)
    req = urllib.request.Request(url, headers={"User-Agent": "energy-stack/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise CoincidentPeakParseError(f"HTTP {resp.status} fetching {url}")
        return resp.read()


def write_influx(peaks: list[CoincidentPeak]) -> None:
    """Write peaks to InfluxDB. Imports here so --dry-run never needs the deps."""
    from influxdb_client import InfluxDBClient, Point
    from influxdb_client.client.write_api import SYNCHRONOUS

    url = os.environ.get("INFLUX_URL", "http://localhost:8086")
    token = os.environ.get("INFLUX_TOKEN")
    org = os.environ.get("INFLUX_ORG", "depaola-home")
    bucket = os.environ.get("INFLUX_BUCKET", "energy")
    if not token:
        raise SystemExit("INFLUX_TOKEN env var is required (or use --dry-run)")

    points = []
    for cp in peaks:
        # We treat hour-beginning EPT as a wall-clock value here. EPT follows
        # DST; ZoneInfo("America/New_York") matches PJM's EPT convention. We
        # localize then convert to UTC so the Influx timestamp is unambiguous.
        from zoneinfo import ZoneInfo
        ept = ZoneInfo("America/New_York")
        ts_utc = cp.hour_beginning_local.replace(tzinfo=ept).astimezone(timezone.utc)
        p = (
            Point("pjm.coincident_peak")
            .tag("summer_year", str(cp.summer_year))
            .tag("peak_rank", str(cp.peak_rank))
            .field("peak_load_mw", cp.rto_peak_mw)
            .field("comed_zone_load_mw", cp.comed_zone_mw)
            .time(ts_utc)
        )
        points.append(p)

    with InfluxDBClient(url=url, token=token, org=org) as client:
        client.write_api(write_options=SYNCHRONOUS).write(bucket=bucket, record=points)


def _print_peaks(peaks: list[CoincidentPeak]) -> None:
    print(f"Year: {peaks[0].summer_year}")
    print(f"{'Rank':<5}{'Hour-beginning EPT':<22}{'RTO MW':>14}  ComEd MW")
    for cp in peaks:
        print(f"{cp.peak_rank:<5}{cp.hour_beginning_local.isoformat():<22}"
              f"{cp.rto_peak_mw:>14,.1f}  {cp.comed_zone_mw:>10,.1f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--year", type=int, help="Scrape a single year")
    g.add_argument("--years", type=str, help="Comma-separated list, e.g. 2020,2021,2022,2023,2024")
    ap.add_argument("--pdf", type=Path, help="Parse a local PDF instead of fetching (requires --year)")
    ap.add_argument("--dry-run", action="store_true", help="Parse + print without writing to Influx")
    args = ap.parse_args(argv)

    if args.years:
        years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
    elif args.year:
        years = [args.year]
    else:
        # Default: last summer (PDFs publish in mid-November of same year)
        now = datetime.now()
        years = [now.year - 1 if now.month < 11 else now.year]

    if args.pdf and len(years) != 1:
        raise SystemExit("--pdf requires exactly one --year")

    all_peaks: list[CoincidentPeak] = []
    for year in years:
        try:
            if args.pdf:
                pdf_bytes = args.pdf.read_bytes()
            else:
                pdf_bytes = fetch_pdf(year)
            peaks = parse_pdf(pdf_bytes, expected_year=year)
            print(f"--- Summer {year} ---")
            _print_peaks(peaks)
            all_peaks.extend(peaks)
        except CoincidentPeakParseError as e:
            print(f"summer {year}: {e}", file=sys.stderr)
            return 3
        except Exception as e:
            print(f"summer {year}: unexpected error: {e}", file=sys.stderr)
            return 4

    if args.dry_run:
        print(f"\n[dry-run] would write {len(all_peaks)} points to pjm.coincident_peak")
        return 0

    write_influx(all_peaks)
    print(f"\nWrote {len(all_peaks)} points to pjm.coincident_peak across {len(years)} year(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
