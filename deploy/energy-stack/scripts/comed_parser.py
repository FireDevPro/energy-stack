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
    # Repair "Com Ed" -> "ComEd" (the literal brand string).
    text = re.sub(r"\bCom Ed\b", "ComEd", text)
    return text
