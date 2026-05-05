from datetime import date
from comed_parser import Bill, LineItem
from comed_influx import bill_to_line_protocol


def make_bill():
    return Bill(
        account_no="9999999991",
        rate_plan="Residential - Hourly Single",
        bill_type="normal",
        issued_date=date(2026, 4, 24),
        service_from=date(2026, 3, 24),
        service_to=date(2026, 4, 23),
        service_days=30,
        kwh=1715,
        peak_kw=6.56,
        total_due=247.67,
        supply_total=146.83,
        delivery_total=128.82,
        taxes_total=-27.98,
        misc_total=0.0,
        line_items=[
            LineItem("SUPPLY", "Capacity Charge", 54.64, 6.56, "kW", 8.32925),
            LineItem("DELIVERY", "Customer Charge", 15.35),
        ],
    )


def test_bill_to_line_protocol_emits_bill_point():
    lines = bill_to_line_protocol(make_bill()).splitlines()
    bill_lines = [l for l in lines if l.startswith("comed.bill,")]
    assert len(bill_lines) == 1
    line = bill_lines[0]
    # Tags
    assert "account_no=9999999991" in line
    # Note: spaces in rate_plan must be escaped
    assert "rate_plan=Residential\\ -\\ Hourly\\ Single" in line
    assert "bill_type=normal" in line
    # Fields
    assert "total_due=247.67" in line
    assert "kwh=1715i" in line
    assert "peak_kw=6.56" in line


def test_bill_to_line_protocol_emits_lineitem_points():
    lines = bill_to_line_protocol(make_bill()).splitlines()
    li_lines = [l for l in lines if l.startswith("comed.bill_lineitems,")]
    assert len(li_lines) == 2
    cap = next(l for l in li_lines if "Capacity\\ Charge" in l)
    assert "category=SUPPLY" in cap
    assert "amount=54.64" in cap
    assert "quantity=6.56" in cap
    assert "unit=\"kW\"" in cap
    assert "rate=8.32925" in cap


def test_bill_to_line_protocol_handles_null_peak_kw():
    bill = make_bill()
    bill.peak_kw = None
    lines = bill_to_line_protocol(bill).splitlines()
    bill_line = next(l for l in lines if l.startswith("comed.bill,"))
    assert "peak_kw=" not in bill_line  # field omitted when null


def test_timestamp_is_service_to_2359_chicago():
    """service_to=2026-04-23 → 2026-04-23 23:59:59 America/Chicago →
       2026-04-24 04:59:59 UTC → ns timestamp."""
    lines = bill_to_line_protocol(make_bill()).splitlines()
    bill_line = next(l for l in lines if l.startswith("comed.bill,"))
    # Last token is the nanosecond timestamp
    ts = int(bill_line.rsplit(" ", 1)[1])
    # Expected: 2026-04-24 04:59:59 UTC = unix 1777006799
    expected = 1777006799 * 1_000_000_000
    assert ts == expected
