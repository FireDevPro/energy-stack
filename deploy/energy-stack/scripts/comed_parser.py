"""ComEd bill parser.

Reads a residential ComEd bill PDF and returns a structured `Bill` object
with per-section totals and individual line items. Designed to handle both
modern bills (where pypdf preserves whitespace) and older 2024-2025 bills
(where pypdf jams adjacent words together).

Top-down read order:
  1. Dataclasses: `LineItem`, `Bill`.
  2. Identity helper: `bill_id`.
  3. Text normalization: `normalize_text`.
  4. Header extractors: service period, issued date, account, rate plan, kWh.
  5. Block extractors: SUPPLY, DELIVERY, TAXES, MISCELLANEOUS.
  6. Line-item extractor: `parse_line_items` (4 shapes).
  7. Composer + validation: `parse_bill`, `parse_total_due`, `BillParseError`.
  8. PDF entrypoint: `parse_pdf`.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Union


# ---- Dataclasses ----


@dataclass
class LineItem:
    category: str  # "SUPPLY" | "DELIVERY" | "TAXES_FEES_CREDITS" | "MISC"
    line_item: str
    amount: float
    quantity: Optional[float] = None
    unit: Optional[str] = None
    rate: Optional[float] = None


@dataclass
class Bill:
    account_no: str
    rate_plan: str  # "Residential - Single" | "Residential - Hourly Single"
    bill_type: str  # "normal" | "transition"
    issued_date: date
    service_from: date
    service_to: date
    service_days: int
    kwh: int
    peak_kw: Optional[float]
    total_due: float
    supply_total: float
    delivery_total: float
    taxes_total: float
    misc_total: float
    line_items: list[LineItem] = field(default_factory=list)

    @property
    def effective_rate_per_kwh(self) -> float:
        if self.kwh == 0:
            return 0.0
        return round(self.total_due / self.kwh, 6)


# ---- Identity ----


def bill_id(account_no: str, service_from: date, service_to: date) -> str:
    """Deterministic SHA-256 hash for idempotent ingest. Same account +
    service window always yields the same id."""
    payload = f"{account_no}|{service_from.isoformat()}|{service_to.isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ---- Text normalization ----


def normalize_text(text: str) -> str:
    """Collapse whitespace AND insert missing spaces at common boundaries
    where pypdf failed to detect word breaks in older bills.

    pypdf's text extraction sometimes loses spaces between adjacent glyphs
    (older bills are jammed: "SERVICEFROM8/25/25THROUGH9/23/25"). Newer
    bills already have spaces. This normalizer:
      1. Collapses any whitespace run (spaces/tabs/newlines) to one space.
      2. Inserts a space at lower->upper, ALLCAPS->Word, letter<->digit,
         and before $.
      3. Repairs known electrical units split by step 2 ("k Wh" -> "kWh").

    Note: this does NOT split UPPER->UPPER glue (e.g. "SERVICEFROM"). Regex
    callers must use \\s* between literal multi-word tokens to tolerate that.
    """
    text = re.sub(r"\s+", " ", text).strip()
    # Insert space between lowercase-uppercase: "ChargeCustomer" -> "Charge Customer"
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Insert space at "ALLCAPSWord" boundaries: "ACInterruption" -> "AC Interruption",
    # "ILElectricity" -> "IL Electricity", "UPDATESComEd" -> "UPDATES ComEd".
    # The rule: at least two consecutive uppercase letters followed by an
    # uppercase + lowercase pair. Run-of-caps stays intact ("METERINFORMATION").
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    # Insert space between letter-digit and digit-letter: "Charge1715" -> "Charge 1715"
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    # Insert space before $ if attached: "Charge$42.15" -> "Charge $42.15"
    text = re.sub(r"([a-zA-Z0-9])(\$)", r"\1 \2", text)
    # Repair electrical units broken by the lower->upper rule.
    # "k Wh" -> "kWh", "k W" -> "kW". Use lookarounds instead of \b so we
    # also catch "k WX" (older fixtures jam W and the multiplier X together).
    text = re.sub(r"(?<!\w)k Wh(?!\w)", "kWh", text)
    text = re.sub(r"(?<!\w)k W(?=\s|X|$)", "kW", text)
    # Repair "Com Ed" -> "ComEd" anywhere it survived the boundary rules
    # (older bills may have it glued to the next word: "Com Edprovides...").
    text = text.replace("Com Ed", "ComEd")
    return text


# ---- Header extractors ----


def _parse_mdy(s: str) -> date:
    """Parse 'M/D/YY' or 'M/D/YYYY' to date. Two-digit years assume 20xx."""
    m, d, y = s.split("/")
    yy = int(y)
    if yy < 100:
        yy += 2000
    return date(yy, int(m), int(d))


def parse_service_period(text: str) -> tuple[date, date, int]:
    """Returns (from_date, to_date, days). Tolerates SERVICE FROM glued in
    older bills ('SERVICEFROM') via \\s* between every token."""
    m = re.search(
        r"SERVICE\s*FROM\s*(\d+/\d+/\d+)\s*THROUGH\s*(\d+/\d+/\d+)\s*\(\s*(\d+)\s*DAYS?\s*\)",
        text,
        re.IGNORECASE,
    )
    if not m:
        raise ValueError("could not find SERVICE FROM ... THROUGH ... (N DAYS)")
    return (_parse_mdy(m.group(1)), _parse_mdy(m.group(2)), int(m.group(3)))


def parse_issued_date(text: str) -> date:
    m = re.search(r"Issued\s*(\d+/\d+/\d+)", text)
    if not m:
        raise ValueError("could not find 'Issued M/D/YY'")
    return _parse_mdy(m.group(1))


def parse_account_no(text: str) -> str:
    m = re.search(r"Account\s*#\s*(\d+)", text)
    if not m:
        raise ValueError("could not find 'Account # N'")
    return m.group(1)


def parse_rate_plan(text: str) -> str:
    """Returns 'Residential - Hourly Single' or 'Residential - Single'.
    Order matters: try 'Hourly Single' first since 'Single' is a substring."""
    m = re.search(r"Residential\s*-\s*Hourly\s*Single\b", text, re.IGNORECASE)
    if m:
        return "Residential - Hourly Single"
    m = re.search(r"Residential\s*-\s*Single\b", text, re.IGNORECASE)
    if m:
        return "Residential - Single"
    raise ValueError("could not find rate plan")


def parse_kwh(text: str) -> int:
    """Extract billed kWh.

    Strategy: a transition (cycle-adjust) bill reports billed usage in the
    'Current Month NN.N avg.temp 0 kWh 0% from last year' summary even though
    the meter row may show a non-zero raw read for the partial cycle. So we
    check that summary first -- if it carries the '% from last year' marker,
    use it (it's the bill's stated billed-kWh). Otherwise fall back to the
    METER INFORMATION row which is the canonical figure for normal bills.
    """
    # Transition marker: "Current Month <temp>[degree] avg.temp <N> kWh <N>%
    # from last year". The degree symbol in our fixtures is the masculine
    # ordinal indicator (chr 186), which Python's \W does NOT match -- so
    # we use [^A-Za-z0-9.] to skip any one degree-like glyph.
    m = re.search(
        r"Current\s*Month\s+[\d.]+\s*[^A-Za-z0-9.]?\s*avg\.?\s*temp\s+(\d+)\s*kWh\s+\d+\s*%\s*from\s*last\s*year",
        text,
        re.IGNORECASE,
    )
    if m:
        return int(m.group(1))

    # Normal: meter row pattern. After normalization, fixed/transition bills
    # show "Totalk Wh" instead of "Total kWh" because the lower->upper rule
    # split kWh -- so we use \s* and tolerate that.
    m = re.search(
        r"General\s*Service\s+Total\s*k\s*Wh\s+Actual\s+Actual\s+(\d+)",
        text,
    )
    if m:
        return int(m.group(1))

    raise ValueError("could not find kWh in meter info or transition summary")


# ---- Money + block extractors ----


def _money(s: str) -> float:
    """Parse '$1,234.56' or '-$1,234.56' or '$0.00' to float."""
    s = s.replace(",", "").replace("$", "").strip()
    return float(s)


def _extract_block(text: str, start_pat: str, end_pat: str) -> tuple[float, str]:
    """Generic block extractor. start_pat must capture the block's $ total
    in group 1. end_pat is the start anchor of the next section.
    Returns (total, body_between_start_and_end)."""
    start = re.search(start_pat, text)
    if not start:
        raise ValueError(f"could not find block start: {start_pat}")
    end = re.search(end_pat, text[start.end():])
    if not end:
        raise ValueError(f"could not find block end: {end_pat}")
    total = _money(start.group(1))
    body = text[start.end(): start.end() + end.start()]
    return total, body


def extract_supply_block(text: str) -> tuple[float, str]:
    return _extract_block(
        text,
        r"SUPPLY\s*-\s*ComEd\s*(-?\$?[\d,]+\.\d{2})",
        r"DELIVERY\s*-\s*ComEd",
    )


def extract_delivery_block(text: str) -> tuple[float, str]:
    return _extract_block(
        text,
        r"DELIVERY\s*-\s*ComEd\s*(-?\$?[\d,]+\.\d{2})",
        r"TAXES",
    )


# Match the TAXES header. Two real variants we've seen, regardless of
# whether whitespace was stripped during PDF extraction:
#   "TAXES & FEES $6.35"  / "TAXES&FEES $6.35"
#   "TAXES, FEES & OTHER CREDITS -$27.98" / "TAXES, FEES & OTHER CREDITS-$27.98"
_TAXES_HEADER = (
    r"TAXES"
    r"(?:[ ,]?\s*(?:&|FEES|OTHER|CREDITS|\s)*)*"
    r"\s*(-?\$?[\d,]+\.\d{2})"
)


def extract_taxes_block(text: str) -> tuple[float, str]:
    return _extract_block(
        text,
        _TAXES_HEADER,
        r"(?:Service\s*Period\s*Total|MISCELLANEOUS)",
    )


def extract_misc_block(text: str) -> tuple[float, str]:
    return _extract_block(
        text,
        r"MISCELLANEOUS\s*(-?\$?[\d,]+\.\d{2})",
        r"(?:Total\s*Amount\s*Due|UPDATES)",
    )


# ---- Line-item extractor ----

# An amount always carries a $; a credit can attach the leading minus to
# either the rate (X-0.03186) or the dollar (-$54.64) or both back-to-back
# ("X-0.03186-$54.64"). Quantities never have $.
_AMOUNT = r"-?\$[\d,]+\.\d{2}"
# Label: starts with capital, then word chars + spaces/&/-//. No digits, no $.
_LABEL = r"[A-Z][A-Za-z][A-Za-z &/.\-]*?[A-Za-z]"

# Shape A: label + qty + unit + X + rate + amount
#   "Distribution Facility Charge 1,715 kWh X 0.06267 $107.48"
#   "Capacity Charge 6.56 kWX 8.32925 $54.64"            (kW glued to X)
#   "Carbon-Free Energy Resource Adj 1,715 kWh X-0.03186-$54.64"
_SHAPE_A = re.compile(
    r"(" + _LABEL + r")\s+([\d,]+(?:\.\d+)?)\s*(kWh|kW)\s*X\s*(-?[\d.]+)\s*(" + _AMOUNT + r")"
)

# Shape A2: label + qty + unit + amount (NO X, NO rate)
#   "Electricity Supply Charge 1,715 kWh $42.15"
_SHAPE_A2 = re.compile(
    r"(" + _LABEL + r")\s+([\d,]+(?:\.\d+)?)\s*(kWh|kW)\s+(" + _AMOUNT + r")"
)

# Shape B: label + $base + X + percentage + amount
#   "Franchise Cost $117.67 X 1.73900%$2.05"
#   "Low Income Discount $260.71 X-5% -$13.04"
_SHAPE_B = re.compile(
    r"(" + _LABEL + r")\s+\$([\d,]+(?:\.\d+)?)\s*X\s*(-?[\d.]+)\s*%\s*(" + _AMOUNT + r")"
)

# Shape C: label + amount (no qty, no rate)
#   "Customer Charge $15.35", "Purchased Electricity Adjustment $30.41",
#   "AC Interruption Option Credit -$10.00", "State Tax $5.66"
_SHAPE_C = re.compile(
    r"(" + _LABEL + r")\s+(" + _AMOUNT + r")"
)

_LINE_ITEM_PATTERNS = [_SHAPE_A, _SHAPE_A2, _SHAPE_B, _SHAPE_C]


def parse_line_items(body: str, category: str) -> list[LineItem]:
    """Extract every line item in a normalized block body.

    Walks the body left-to-right. At each position, tries each shape; the
    longest match wins (most specific). On no match, advance one char and
    retry. This naturally handles bodies that begin with whitespace and
    rejects fragments that don't terminate in a $ amount.
    """
    items: list[LineItem] = []
    pos = 0
    while pos < len(body):
        best_match = None
        best_pat_idx = None
        for idx, pat in enumerate(_LINE_ITEM_PATTERNS):
            m = pat.match(body, pos)
            if m and (best_match is None or m.end() > best_match.end()):
                best_match = m
                best_pat_idx = idx
        if best_match is None:
            pos += 1
            continue
        groups = best_match.groups()
        label = groups[0].strip()
        if best_pat_idx == 0:
            # Shape A: qty + unit + X + rate + amount
            items.append(LineItem(
                category=category, line_item=label,
                amount=_money(groups[4]),
                quantity=float(groups[1].replace(",", "")),
                unit=groups[2], rate=float(groups[3]),
            ))
        elif best_pat_idx == 1:
            # Shape A2: qty + unit + amount (no rate)
            items.append(LineItem(
                category=category, line_item=label,
                amount=_money(groups[3]),
                quantity=float(groups[1].replace(",", "")),
                unit=groups[2], rate=None,
            ))
        elif best_pat_idx == 2:
            # Shape B: $-base * percentage
            items.append(LineItem(
                category=category, line_item=label,
                amount=_money(groups[3]),
                quantity=float(groups[1].replace(",", "")),
                unit="$", rate=float(groups[2]) / 100.0,
            ))
        else:
            # Shape C: bare label + amount
            items.append(LineItem(
                category=category, line_item=label,
                amount=_money(groups[1]),
            ))
        pos = best_match.end()
    return items


# ---- Top-level composer ----


EXPECTED_ACCOUNT = "9999999991"


class BillParseError(Exception):
    """Raised when a bill cannot be parsed cleanly: missing required fields,
    wrong account, or section totals that don't reconcile to total due."""


def parse_total_due(text: str) -> float:
    m = re.search(r"Total\s*Amount\s*Due\s*(-?\$?[\d,]+\.\d{2})", text)
    if not m:
        m = re.search(r"Service\s*Period\s*Total\s*(-?\$?[\d,]+\.\d{2})", text)
    if not m:
        raise BillParseError("could not find Total Amount Due")
    return _money(m.group(1))


def parse_bill(text: str) -> Bill:
    """Compose a Bill from extracted parts. Validates account_no and
    that the per-block totals reconcile to total_due (within $0.01)."""
    account_no = parse_account_no(text)
    if account_no != EXPECTED_ACCOUNT:
        raise BillParseError(
            f"account_no {account_no} does not match expected {EXPECTED_ACCOUNT}"
        )

    rate_plan = parse_rate_plan(text)
    issued = parse_issued_date(text)
    service_from, service_to, service_days = parse_service_period(text)
    kwh = parse_kwh(text)
    total_due = parse_total_due(text)

    # Day-count sanity: ComEd's count is inclusive of the start date, so
    # calendar diff + 1 should equal stated days. Tolerate +/- 1, raise otherwise.
    actual_days = (service_to - service_from).days + 1
    if abs(actual_days - service_days) > 1:
        raise BillParseError(
            f"service_days {service_days} disagrees with calendar diff {actual_days}"
        )

    bill_type = "transition" if (kwh == 0 and service_days < 10) else "normal"

    supply_total, supply_body = extract_supply_block(text)
    delivery_total, delivery_body = extract_delivery_block(text)
    taxes_total, taxes_body = extract_taxes_block(text)
    try:
        misc_total, misc_body = extract_misc_block(text)
    except ValueError:
        misc_total, misc_body = 0.0, ""

    line_items = (
        parse_line_items(supply_body, "SUPPLY")
        + parse_line_items(delivery_body, "DELIVERY")
        + parse_line_items(taxes_body, "TAXES_FEES_CREDITS")
        + (parse_line_items(misc_body, "MISC") if misc_body else [])
    )

    # Capacity charge -> peak_kw (only present on Hourly plans).
    peak_kw = None
    for li in line_items:
        if li.line_item == "Capacity Charge" and li.unit == "kW":
            peak_kw = li.quantity
            break

    component_sum = supply_total + delivery_total + taxes_total + misc_total
    if abs(component_sum - total_due) >= 0.01:
        raise BillParseError(
            f"totals do not balance: "
            f"supply {supply_total} + delivery {delivery_total} + "
            f"taxes {taxes_total} + misc {misc_total} = {component_sum:.2f}, "
            f"but total_due = {total_due}"
        )

    return Bill(
        account_no=account_no,
        rate_plan=rate_plan,
        bill_type=bill_type,
        issued_date=issued,
        service_from=service_from,
        service_to=service_to,
        service_days=service_days,
        kwh=kwh,
        peak_kw=peak_kw,
        total_due=total_due,
        supply_total=supply_total,
        delivery_total=delivery_total,
        taxes_total=taxes_total,
        misc_total=misc_total,
        line_items=line_items,
    )


# ---- PDF entrypoint ----


def parse_pdf(path: Union[str, Path]) -> Bill:
    """Read a ComEd bill PDF and return a parsed Bill.

    Pages 1+2 carry the entire charge breakdown, meter info, and totals;
    page 3+ is boilerplate. Imports pypdf lazily so unit tests on extracted
    text fixtures don't require the dependency.
    """
    import pypdf  # local import to keep test-fixture path dependency-free
    reader = pypdf.PdfReader(str(path))
    text = "".join(p.extract_text() + "\n" for p in reader.pages[:2])
    return parse_bill(normalize_text(text))
