import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


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
    rate_plan: str  # "Residential-Single" | "Residential-HourlySingle"
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


def bill_id(account_no: str, service_from: date, service_to: date) -> str:
    """Deterministic SHA-256 hash for idempotent ingest. Same account +
    service window always yields the same id."""
    payload = f"{account_no}|{service_from.isoformat()}|{service_to.isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()


def normalize_text(text: str) -> str:
    """Collapse whitespace AND insert missing spaces at common boundaries
    where pypdf failed to detect word breaks in older bills.

    pypdf's text extraction sometimes loses spaces between adjacent glyphs
    (older bills are jammed: "SERVICEFROM8/25/25THROUGH9/23/25"). Newer
    bills already have spaces. This normalizer:
      1. Collapses any whitespace run (spaces/tabs/newlines) to one space.
      2. Inserts a space at lower->upper, letter<->digit, and before $.

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
# Use a non-greedy `.*?` between TAXES and the dollar amount so we only
# consume the header line itself, not the entire body. The amount is the
# first dollar value (with optional leading minus and dollar sign) following.
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
