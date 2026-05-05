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
