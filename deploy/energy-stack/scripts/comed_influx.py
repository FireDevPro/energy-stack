"""Bill -> InfluxDB line protocol generation.

Line protocol reference:
  measurement,tag1=value1,tag2=value2 field1=val1,field2=val2 timestamp_ns
Tag/field keys and string values containing spaces or commas must be
backslash-escaped.
"""
import os
from datetime import datetime, timezone, timedelta

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from comed_parser import Bill, LineItem


CHICAGO_OFFSET_HOURS = -5  # CDT in summer, CST in winter — accept up to 1h drift
# Use an explicit fixed offset rather than zoneinfo; ComEd bills cross the
# DST boundary, but the timestamp is "end of service period" which is a
# coarse marker, not a precise alignment point. Use -5 (CDT) year-round
# — produces consistent ordering and dashboards bucket by day anyway.


def _esc_tag(s: str) -> str:
    """Escape spaces, commas, equals in tag keys/values."""
    return s.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")


def _esc_str_field(s: str) -> str:
    """Escape inside double-quoted string field values."""
    return s.replace("\\", r"\\").replace('"', r"\"")


def _service_to_ns(d) -> int:
    """Convert service_to date to nanosecond UTC timestamp,
    anchored at 23:59:59 America/Chicago (CDT)."""
    naive = datetime(d.year, d.month, d.day, 23, 59, 59)
    aware = naive.replace(tzinfo=timezone(timedelta(hours=CHICAGO_OFFSET_HOURS)))
    utc = aware.astimezone(timezone.utc)
    return int(utc.timestamp()) * 1_000_000_000


def bill_to_line_protocol(bill: Bill) -> str:
    """Generate Influx line protocol for a Bill: 1 comed.bill point + N comed.bill_lineitems points."""
    ts = _service_to_ns(bill.service_to)
    lines: list[str] = []

    # comed.bill
    tags = [
        f"account_no={_esc_tag(bill.account_no)}",
        f"rate_plan={_esc_tag(bill.rate_plan)}",
        f"bill_type={_esc_tag(bill.bill_type)}",
    ]
    fields = [
        f"total_due={bill.total_due}",
        f"kwh={bill.kwh}i",
        f"supply_total={bill.supply_total}",
        f"delivery_total={bill.delivery_total}",
        f"taxes_total={bill.taxes_total}",
        f"misc_total={bill.misc_total}",
        f"effective_rate_per_kwh={bill.effective_rate_per_kwh}",
        f"service_days={bill.service_days}i",
        f'issued_date="{bill.issued_date.isoformat()}"',
        f'service_from="{bill.service_from.isoformat()}"',
        f'service_to="{bill.service_to.isoformat()}"',
    ]
    if bill.peak_kw is not None:
        fields.insert(2, f"peak_kw={bill.peak_kw}")
    lines.append(f"comed.bill,{','.join(tags)} {','.join(fields)} {ts}")

    # comed.bill_lineitems
    for li in bill.line_items:
        li_tags = [
            f"account_no={_esc_tag(bill.account_no)}",
            f"category={_esc_tag(li.category)}",
            f"line_item={_esc_tag(li.line_item)}",
        ]
        li_fields = [f"amount={li.amount}"]
        if li.quantity is not None:
            li_fields.append(f"quantity={li.quantity}")
        if li.unit is not None:
            li_fields.append(f'unit="{_esc_str_field(li.unit)}"')
        if li.rate is not None:
            li_fields.append(f"rate={li.rate}")
        lines.append(
            f"comed.bill_lineitems,{','.join(li_tags)} {','.join(li_fields)} {ts}"
        )

    return "\n".join(lines)


def write_bill(bill: Bill, *, url: str, token: str, org: str, bucket: str) -> None:
    """Write a parsed bill to InfluxDB. Idempotent: re-writing the same bill
    produces upserts because (measurement, tags, timestamp) collide."""
    lp = bill_to_line_protocol(bill)
    with InfluxDBClient(url=url, token=token, org=org, timeout=30_000) as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=bucket, record=lp)
