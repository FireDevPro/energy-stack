from datetime import date
from pathlib import Path

from comed_parser import (
    Bill,
    LineItem,
    bill_id,
    normalize_text,
    parse_account_no,
    parse_issued_date,
    parse_rate_plan,
    parse_service_period,
)

FIXTURES = Path(__file__).parent / "fixtures"


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


def test_bill_id_is_deterministic():
    a = bill_id("9999999991", date(2026, 3, 24), date(2026, 4, 23))
    b = bill_id("9999999991", date(2026, 3, 24), date(2026, 4, 23))
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_bill_id_changes_with_inputs():
    a = bill_id("9999999991", date(2026, 3, 24), date(2026, 4, 23))
    b = bill_id("9999999991", date(2026, 3, 24), date(2026, 4, 24))  # different to-date
    assert a != b


# ---- Task 5: normalize_text ----


def test_normalize_text_collapses_whitespace():
    assert normalize_text("a   b\nc\t d") == "a b c d"


def test_normalize_text_handles_real_fixture():
    raw = (FIXTURES / "hourly_single_apr2026.txt").read_text(encoding="utf-8")
    norm = normalize_text(raw)
    # The Capacity Charge line should now be one continuous token sequence
    assert "Capacity Charge 6.56 kW" in norm


# ---- Task 6: header extractors ----


def _norm_fixture(name: str) -> str:
    return normalize_text((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_service_period_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    result = parse_service_period(text)
    assert result == (date(2026, 3, 24), date(2026, 4, 23), 30)


def test_parse_service_period_fixed():
    text = _norm_fixture("fixed_single_sep2025.txt")
    result = parse_service_period(text)
    assert result == (date(2025, 8, 25), date(2025, 9, 23), 29)


def test_parse_issued_date_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    assert parse_issued_date(text) == date(2026, 4, 24)


def test_parse_account_no():
    text = _norm_fixture("hourly_single_apr2026.txt")
    assert parse_account_no(text) == "9999999991"


def test_parse_rate_plan_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    assert parse_rate_plan(text) == "Residential - Hourly Single"


def test_parse_rate_plan_fixed():
    text = _norm_fixture("fixed_single_sep2025.txt")
    assert parse_rate_plan(text) == "Residential - Single"
