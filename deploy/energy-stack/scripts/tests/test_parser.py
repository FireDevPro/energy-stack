from datetime import date
from comed_parser import Bill, LineItem


def test_bill_dataclass_construction():
    bill = Bill(
        account_no="9999999991",
        rate_plan="Residential-HourlySingle",
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
        line_items=[],
    )
    assert bill.effective_rate_per_kwh == round(247.67 / 1715, 6)


def test_line_item_dataclass_construction():
    li = LineItem(
        category="SUPPLY",
        line_item="Capacity Charge",
        amount=54.64,
        quantity=6.56,
        unit="kW",
        rate=8.32925,
    )
    assert li.amount == 54.64
    assert li.unit == "kW"
