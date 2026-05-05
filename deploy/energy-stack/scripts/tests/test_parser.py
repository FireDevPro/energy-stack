import os
from datetime import date
from pathlib import Path

import pytest

from comed_parser import (
    Bill,
    BillParseError,
    LineItem,
    bill_id,
    extract_delivery_block,
    extract_misc_block,
    extract_supply_block,
    extract_taxes_block,
    normalize_text,
    parse_account_no,
    parse_bill,
    parse_issued_date,
    parse_kwh,
    parse_line_items,
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


# ---- Task 7: kWh extractor ----


def test_parse_kwh_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    assert parse_kwh(text) == 1715


def test_parse_kwh_fixed():
    text = _norm_fixture("fixed_single_sep2025.txt")
    assert parse_kwh(text) == 1367


def test_parse_kwh_transition_is_zero():
    text = _norm_fixture("transition_aug2025.txt")
    assert parse_kwh(text) == 0


# ---- Task 8: block extractors ----


def test_extract_supply_block_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    total, body = extract_supply_block(text)
    assert total == 146.83
    assert "Capacity Charge" in body
    assert "Electricity Supply Charge" in body


def test_extract_supply_block_fixed():
    text = _norm_fixture("fixed_single_sep2025.txt")
    total, body = extract_supply_block(text)
    assert total == 137.36
    assert "Capacity Charge" not in body  # fixed-rate has no capacity
    assert "Electricity Supply Charge" in body


def test_extract_delivery_block_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    total, body = extract_delivery_block(text)
    assert total == 128.82
    assert "Customer Charge" in body
    assert "Distribution Facility Charge" in body


def test_extract_taxes_block_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    total, body = extract_taxes_block(text)
    assert total == -27.98
    assert "Carbon-Free Energy Resource Adj" in body


def test_extract_taxes_block_fixed_has_ac_credit():
    text = _norm_fixture("fixed_single_sep2025.txt")
    total, body = extract_taxes_block(text)
    assert total == 6.35
    assert "AC Interruption Option Credit" in body


def test_extract_misc_block_normal_is_zero():
    text = _norm_fixture("hourly_single_apr2026.txt")
    total, body = extract_misc_block(text)
    assert total == 0.0


# ---- Task 9: line-item extractor ----


def test_parse_line_items_supply_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    _, body = extract_supply_block(text)
    items = parse_line_items(body, category="SUPPLY")
    labels = {i.line_item: i for i in items}
    assert labels["Electricity Supply Charge"].amount == 42.15
    assert labels["Capacity Charge"].amount == 54.64
    assert labels["Capacity Charge"].quantity == 6.56
    assert labels["Capacity Charge"].unit == "kW"
    assert labels["Capacity Charge"].rate == 8.32925
    assert labels["Transmission Services Charge"].amount == 18.57
    assert labels["Misc Procurement Components Chg"].amount == 1.06
    assert labels["Purchased Electricity Adjustment"].amount == 30.41


def test_parse_line_items_supply_fixed_no_capacity():
    text = _norm_fixture("fixed_single_sep2025.txt")
    _, body = extract_supply_block(text)
    items = parse_line_items(body, category="SUPPLY")
    labels = {i.line_item for i in items}
    assert "Capacity Charge" not in labels
    assert "Electricity Supply Charge" in labels


def test_parse_line_items_delivery_includes_fixed_charges():
    text = _norm_fixture("hourly_single_apr2026.txt")
    _, body = extract_delivery_block(text)
    items = parse_line_items(body, category="DELIVERY")
    labels = {i.line_item: i for i in items}
    assert labels["Customer Charge"].amount == 15.35
    assert labels["Customer Charge"].quantity is None  # fixed, no qty
    assert labels["Distribution Facility Charge"].amount == 107.48
    assert labels["Distribution Facility Charge"].quantity == 1715
    assert labels["Distribution Facility Charge"].unit == "kWh"
    assert labels["Distribution Facility Charge"].rate == 0.06267


def test_parse_line_items_taxes_with_credit():
    text = _norm_fixture("hourly_single_apr2026.txt")
    _, body = extract_taxes_block(text)
    items = parse_line_items(body, category="TAXES_FEES_CREDITS")
    labels = {i.line_item: i for i in items}
    assert labels["Carbon-Free Energy Resource Adj"].amount == -54.64
    assert labels["Carbon-Free Energy Resource Adj"].rate == -0.03186


def test_parse_line_items_sums_to_block_total_hourly_supply():
    text = _norm_fixture("hourly_single_apr2026.txt")
    total, body = extract_supply_block(text)
    items = parse_line_items(body, category="SUPPLY")
    assert abs(sum(i.amount for i in items) - total) < 0.01


def test_parse_line_items_sums_to_block_total_hourly_taxes():
    text = _norm_fixture("hourly_single_apr2026.txt")
    total, body = extract_taxes_block(text)
    items = parse_line_items(body, category="TAXES_FEES_CREDITS")
    assert abs(sum(i.amount for i in items) - total) < 0.01


# ---- Task 10: parse_bill composer + validation ----


def test_parse_bill_hourly_april():
    text = _norm_fixture("hourly_single_apr2026.txt")
    bill = parse_bill(text)
    assert bill.account_no == "9999999991"
    assert bill.rate_plan == "Residential - Hourly Single"
    assert bill.bill_type == "normal"
    assert bill.service_from == date(2026, 3, 24)
    assert bill.service_to == date(2026, 4, 23)
    assert bill.service_days == 30
    assert bill.kwh == 1715
    assert bill.peak_kw == 6.56
    assert bill.total_due == 247.67
    assert bill.supply_total == 146.83
    assert bill.delivery_total == 128.82
    assert bill.taxes_total == -27.98
    assert bill.misc_total == 0.00
    # Line items aggregated across all blocks
    assert len(bill.line_items) >= 18  # 5 supply + 4 delivery + 12 taxes


def test_parse_bill_fixed_september():
    text = _norm_fixture("fixed_single_sep2025.txt")
    bill = parse_bill(text)
    assert bill.rate_plan == "Residential - Single"
    assert bill.kwh == 1367
    assert bill.peak_kw is None  # no capacity charge on fixed
    assert bill.total_due == 247.83


def test_parse_bill_transition_is_marked():
    text = _norm_fixture("transition_aug2025.txt")
    bill = parse_bill(text)
    assert bill.bill_type == "transition"
    assert bill.kwh == 0
    assert bill.service_days < 10


def test_parse_bill_validates_totals():
    """If supply + delivery + taxes + misc != total_due, raise."""
    text = _norm_fixture("hourly_single_apr2026.txt")
    # Tamper the text so totals won't balance
    bad = text.replace("Total Amount Due 247.67", "Total Amount Due 999.99") \
              .replace("Total Amount Due $247.67", "Total Amount Due $999.99")
    with pytest.raises(BillParseError, match="totals do not balance"):
        parse_bill(bad)


def test_parse_bill_rejects_wrong_account():
    text = _norm_fixture("hourly_single_apr2026.txt")
    bad = text.replace("9999999991", "9999999999")
    with pytest.raises(BillParseError, match="account_no"):
        parse_bill(bad)


# ---- Task 11: PDF -> Bill ----


@pytest.mark.skipif(
    not os.environ.get("COMED_TEST_PDF"),
    reason="set COMED_TEST_PDF to a bill PDF path to run",
)
def test_parse_bill_from_pdf_file_endtoend():
    from comed_parser import parse_pdf
    bill = parse_pdf(os.environ["COMED_TEST_PDF"])
    assert bill.account_no == "9999999991"
    assert bill.total_due > 0
