# Phase 8 — ComEd Bill Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single Python script that parses ComEd bill PDFs and writes structured bill data to InfluxDB, plus a Grafana reconciliation dashboard.

**Architecture:** One script (`parse_comed_bill.py`) checked into the repo. Run by hand monthly: `scp` the PDF to Pi-lab, `ssh` and run the script. Parser uses pypdf to extract text, normalizes whitespace, runs regex against the flat stream, and writes two measurements (`comed.bill` for top-line + `comed.bill_lineitems` for the GL breakdown). Idempotent on `bill_id = sha256(account || from || to)`.

**Tech Stack:** Python 3.12, pypdf, influxdb-client. No container, no service loop. Tests run against pre-extracted text fixtures so PDFs aren't needed at test time.

**Spec:** `docs/archive/phase-8-comed-bill-ingest-design.md`

---

## File Structure

| Path | Purpose |
|---|---|
| `deploy/energy-stack/scripts/parse_comed_bill.py` | Main script — argparse + orchestration |
| `deploy/energy-stack/scripts/comed_parser.py` | Pure parsing logic (PDF → `Bill` dataclass) |
| `deploy/energy-stack/scripts/comed_influx.py` | Bill → InfluxDB line protocol + writer |
| `deploy/energy-stack/scripts/requirements.txt` | `pypdf`, `influxdb-client` |
| `deploy/energy-stack/scripts/README.md` | Operator usage docs |
| `deploy/energy-stack/scripts/tests/__init__.py` | (empty) |
| `deploy/energy-stack/scripts/tests/test_parser.py` | Unit tests against text fixtures |
| `deploy/energy-stack/scripts/tests/test_influx.py` | Unit tests for line protocol generation |
| `deploy/energy-stack/scripts/tests/fixtures/hourly_single_apr2026.txt` | Pre-extracted text from `d54d630c` (April 2026, Hourly Single) |
| `deploy/energy-stack/scripts/tests/fixtures/fixed_single_sep2025.txt` | Pre-extracted text from `dff224a6` (Sept 2025, Residential - Single) |
| `deploy/energy-stack/scripts/tests/fixtures/transition_aug2025.txt` | Pre-extracted text from `4ace3f0e` (transition stub) |
| `deploy/energy-stack/grafana/dashboards/comed-bill-reconciliation.json` | New dashboard (4 panels) |
| `PROJECT.md` | Append Phase 8 entry |
| `docs/SERVICES.md` | Append script entry |
| `.gitignore` (root) | Add `deploy/energy-stack/inbox/` |

---

## Task 1: Scaffold Directory + Dependencies

**Files:**
- Create: `deploy/energy-stack/scripts/requirements.txt`
- Create: `deploy/energy-stack/scripts/tests/__init__.py` (empty)
- Modify: `.gitignore` (root)

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p deploy/energy-stack/scripts/tests/fixtures
touch deploy/energy-stack/scripts/tests/__init__.py
```

- [ ] **Step 2: Write `requirements.txt`**

File: `deploy/energy-stack/scripts/requirements.txt`
```
pypdf==5.10.0
influxdb-client==1.48.0
pytest==8.3.0
```

- [ ] **Step 3: Add inbox to `.gitignore`**

Append to `.gitignore` at repo root:
```
# ComEd bill ingest runtime — bills contain personal info, never commit
deploy/energy-stack/inbox/
```

- [ ] **Step 4: Verify the install works**

Run: `cd deploy/energy-stack/scripts && python -m venv .venv && source .venv/Scripts/activate && pip install -r requirements.txt && python -c "import pypdf, influxdb_client; print('ok')"`
Expected: prints `ok`

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/ .gitignore
git commit -m "chore: scaffold comed-bill-ingest script structure"
```

---

## Task 2: Generate Text Fixtures from Real Bills

**Files:**
- Create: `deploy/energy-stack/scripts/tests/fixtures/hourly_single_apr2026.txt`
- Create: `deploy/energy-stack/scripts/tests/fixtures/fixed_single_sep2025.txt`
- Create: `deploy/energy-stack/scripts/tests/fixtures/transition_aug2025.txt`

These fixtures are the pypdf-extracted text from three real bills, one per format. Tests run against these instead of PDFs so the test suite has no binary deps and diffs are reviewable.

- [ ] **Step 1: Write a one-off extraction script (not committed)**

Run this in `deploy/energy-stack/scripts/`:

```python
# extract_fixtures.py — temporary, not committed
import pypdf, re, pathlib

BILLS = {
    "hourly_single_apr2026.txt": r"D:\Chris\Downloads\d54d630c-815b-4b42-a7f1-f2cf97f920e5.pdf",
    "fixed_single_sep2025.txt":  r"D:\Chris\Downloads\dff224a6-0509-4e73-9985-5e6bff158329.pdf",
    "transition_aug2025.txt":    r"D:\Chris\Downloads\4ace3f0e-37ce-4df8-b0af-43d8c953b6ef.pdf",
}

out = pathlib.Path("tests/fixtures")
out.mkdir(parents=True, exist_ok=True)
for name, path in BILLS.items():
    reader = pypdf.PdfReader(path)
    text = "".join(p.extract_text() + "\n" for p in reader.pages[:2])
    (out / name).write_text(text, encoding="utf-8")
    print(f"wrote {name}")
```

Run: `python extract_fixtures.py`
Expected: 3 `.txt` files in `tests/fixtures/`

- [ ] **Step 2: Delete the extraction script (it was temporary)**

```bash
rm extract_fixtures.py
```

- [ ] **Step 3: Verify fixtures look reasonable**

Run: `head -5 tests/fixtures/hourly_single_apr2026.txt`
Expected: bill header text starting with "Return only this portion..."

- [ ] **Step 4: Commit**

```bash
git add deploy/energy-stack/scripts/tests/fixtures/
git commit -m "test: add comed bill text fixtures (hourly, fixed, transition)"
```

---

## Task 3: `Bill` and `LineItem` Dataclasses

**Files:**
- Create: `deploy/energy-stack/scripts/comed_parser.py`
- Create: `deploy/energy-stack/scripts/tests/test_parser.py`

- [ ] **Step 1: Write the failing test**

File: `deploy/energy-stack/scripts/tests/test_parser.py`
```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd deploy/energy-stack/scripts && pytest tests/test_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'comed_parser'`

- [ ] **Step 3: Create `comed_parser.py` with the dataclasses**

File: `deploy/energy-stack/scripts/comed_parser.py`
```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: add Bill and LineItem dataclasses"
```

---

## Task 4: `bill_id` Derivation

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_parser.py`
- Modify: `deploy/energy-stack/scripts/tests/test_parser.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_parser.py`:
```python
from comed_parser import bill_id


def test_bill_id_is_deterministic():
    a = bill_id("9999999991", date(2026, 3, 24), date(2026, 4, 23))
    b = bill_id("9999999991", date(2026, 3, 24), date(2026, 4, 23))
    assert a == b
    assert len(a) == 64  # SHA-256 hex


def test_bill_id_changes_with_inputs():
    a = bill_id("9999999991", date(2026, 3, 24), date(2026, 4, 23))
    b = bill_id("9999999991", date(2026, 3, 24), date(2026, 4, 24))  # different to-date
    assert a != b
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_parser.py -v -k bill_id`
Expected: FAIL with `cannot import name 'bill_id'`

- [ ] **Step 3: Implement `bill_id`**

Append to `comed_parser.py`:
```python
import hashlib


def bill_id(account_no: str, service_from: date, service_to: date) -> str:
    payload = f"{account_no}|{service_from.isoformat()}|{service_to.isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: add bill_id sha256 derivation for idempotency"
```

---

## Task 5: PDF Text Extraction + Whitespace Normalization

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_parser.py`
- Modify: `deploy/energy-stack/scripts/tests/test_parser.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_parser.py`:
```python
from pathlib import Path
from comed_parser import normalize_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_normalize_text_collapses_whitespace():
    assert normalize_text("a   b\nc\t d") == "a b c d"


def test_normalize_text_handles_real_fixture():
    raw = (FIXTURES / "hourly_single_apr2026.txt").read_text(encoding="utf-8")
    norm = normalize_text(raw)
    # The Capacity Charge line should now be one continuous token sequence
    assert "Capacity Charge 6.56 kW" in norm
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_parser.py -v -k normalize`
Expected: FAIL with import error

- [ ] **Step 3: Implement `normalize_text`**

Append to `comed_parser.py`:
```python
import re


def normalize_text(text: str) -> str:
    """Collapse all whitespace runs to single spaces. This hides the
    pypdf rendering artifact in older bills where words were jammed
    together (`Capacity Charge6.56kW` becomes `Capacity Charge 6.56 kW`)
    versus newer bills (already had spaces). Only call this before regex.
    """
    return re.sub(r"\s+", " ", text).strip()
```

Wait — older bills have jammed words *without* whitespace, so `\s+` collapse alone does NOT add the missing spaces. We need to also handle word-boundary insertion. Look at the fixture before running.

- [ ] **Step 4: Inspect the fixture to confirm normalization need**

Run: `grep -o 'Capacity Charge[^$]*' tests/fixtures/hourly_single_apr2026.txt | head -1`
Expected: shows whether the bill has spaces or not. If `Capacity Charge6.56` (no space), update normalize.

If older fixed-rate fixture has jammed words: update `normalize_text` to insert spaces at lowercase→uppercase boundaries and digit→letter boundaries:

```python
def normalize_text(text: str) -> str:
    """Collapse whitespace AND insert missing spaces at common boundaries
    where pypdf failed to detect word breaks in older bills."""
    text = re.sub(r"\s+", " ", text).strip()
    # Insert space between lowercase-uppercase: "ChargeCustomer" -> "Charge Customer"
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Insert space between letter-digit and digit-letter: "Charge1715" -> "Charge 1715"
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)
    # Insert space before $ if attached: "Charge$42.15" -> "Charge $42.15"
    text = re.sub(r"([a-zA-Z0-9])(\$)", r"\1 \2", text)
    return text
```

- [ ] **Step 5: Run to verify all parser tests pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: text normalization (whitespace + missing word boundaries)"
```

---

## Task 6: Header Extractors (service period, issued date, account, rate plan)

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_parser.py`
- Modify: `deploy/energy-stack/scripts/tests/test_parser.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_parser.py`:
```python
from comed_parser import (
    parse_service_period, parse_issued_date,
    parse_account_no, parse_rate_plan,
)


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_parser.py -v -k "parse_service_period or parse_issued or parse_account or parse_rate"`
Expected: FAIL with import errors

- [ ] **Step 3: Implement the four extractors**

Append to `comed_parser.py`:
```python
from datetime import date


def _parse_mdy(s: str) -> date:
    """Parse 'M/D/YY' or 'M/D/YYYY' to date. Two-digit years assume 20xx."""
    m, d, y = s.split("/")
    yy = int(y)
    if yy < 100:
        yy += 2000
    return date(yy, int(m), int(d))


def parse_service_period(text: str) -> tuple[date, date, int]:
    """Returns (from, to, days)."""
    m = re.search(
        r"SERVICE FROM\s*(\d+/\d+/\d+)\s*THROUGH\s*(\d+/\d+/\d+)\s*\((\d+)\s*DAYS?\)",
        text, re.IGNORECASE,
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
    """Returns 'Residential - Hourly Single' or 'Residential - Single'."""
    m = re.search(r"Residential\s*-\s*(Hourly Single|Single)\b", text)
    if not m:
        raise ValueError("could not find rate plan")
    return f"Residential - {m.group(1)}"
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: header extractors (service period, issued date, account, rate plan)"
```

---

## Task 7: kWh Extractor

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_parser.py`
- Modify: `deploy/energy-stack/scripts/tests/test_parser.py`

The kWh value lives in the METER INFORMATION row — last numeric column after `Difference Multiplier Usage`. On the transition stub bill it's 0.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_parser.py`:
```python
from comed_parser import parse_kwh


def test_parse_kwh_hourly():
    text = _norm_fixture("hourly_single_apr2026.txt")
    assert parse_kwh(text) == 1715


def test_parse_kwh_fixed():
    text = _norm_fixture("fixed_single_sep2025.txt")
    assert parse_kwh(text) == 1367


def test_parse_kwh_transition_is_zero():
    text = _norm_fixture("transition_aug2025.txt")
    assert parse_kwh(text) == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_parser.py -v -k parse_kwh`
Expected: FAIL with import error

- [ ] **Step 3: Implement `parse_kwh`**

Append to `comed_parser.py`:
```python
def parse_kwh(text: str) -> int:
    """Extract billed kWh from METER INFORMATION row.

    The pattern is: dates, meter#, 'General Service', 'Total kWh',
    'Actual', 'Actual', then the kWh integer right before METER INFO ends
    or before the next section. The transition bill has 0 kWh and a
    different meter row layout, so we fall back to looking for the
    'Current Month NN.N° avg.temp 0 kWh' summary block.
    """
    # Primary: meter row pattern
    m = re.search(
        r"General Service\s+Total kWh\s+Actual\s+Actual\s+(\d+)",
        text,
    )
    if m:
        return int(m.group(1))
    # Fallback: transition stub doesn't have a meter row; "0 kWh" appears
    # in the "Current Month" summary as "0 kWh 0% from last year"
    m = re.search(r"Current Month\s+[\d.]+\s*°?\s*avg\.\s*temp\s+(\d+)\s*kWh", text)
    if m:
        return int(m.group(1))
    raise ValueError("could not find kWh in meter info or summary")
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: kWh extractor (meter info + transition fallback)"
```

---

## Task 8: Block Extractor (find SUPPLY / DELIVERY / TAXES sections)

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_parser.py`
- Modify: `deploy/energy-stack/scripts/tests/test_parser.py`

Block boundaries on a normalized line:
- SUPPLY block: starts at `SUPPLY - ComEd $<total>`, ends at `DELIVERY - ComEd`
- DELIVERY block: starts at `DELIVERY - ComEd $<total>`, ends at `TAXES`
- TAXES block: starts at `TAXES` (matches `TAXES & FEES` or `TAXES, FEES & OTHER CREDITS`), ends at `Service Period Total` or `MISCELLANEOUS`
- MISC block: starts at `MISCELLANEOUS $<total>`, ends at `Total Amount Due` or `UPDATES`

Each block extractor returns `(total: float, body: str)`.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_parser.py`:
```python
from comed_parser import (
    extract_supply_block, extract_delivery_block,
    extract_taxes_block, extract_misc_block,
)


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_parser.py -v -k extract_`
Expected: FAIL with import errors

- [ ] **Step 3: Implement the block extractors**

Append to `comed_parser.py`:
```python
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
    body = text[start.end():start.end() + end.start()]
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


def extract_taxes_block(text: str) -> tuple[float, str]:
    return _extract_block(
        text,
        r"TAXES[ ,]?\s*(?:&|FEES|OTHER|CREDITS|\s)*\s*(-?\$?[\d,]+\.\d{2})",
        r"(?:Service Period Total|MISCELLANEOUS)",
    )


def extract_misc_block(text: str) -> tuple[float, str]:
    return _extract_block(
        text,
        r"MISCELLANEOUS\s*(-?\$?[\d,]+\.\d{2})",
        r"(?:Total Amount Due|UPDATES)",
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: block extractors (supply/delivery/taxes/misc)"
```

---

## Task 9: Line-Item Extractor (parses line items within a block body)

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_parser.py`
- Modify: `deploy/energy-stack/scripts/tests/test_parser.py`

Within a block body, line items take three shapes:
1. **Label + amount only**: `Customer Charge $15.35` or `Purchased Electricity Adjustment $30.41`
2. **Label + qty/unit + rate + amount**: `Distribution Facility Charge 1,646 kWh X 0.06228 $102.51` or `Capacity Charge 6.56 kW X 8.32925 $54.64`
3. **Label + multiplier + amount**: `Franchise Cost $113.61 X 1.73900% $1.98` (multiplier on a $-base, not kWh)

Strategy: scan the block body for **trailing $ amounts** (or `-$` for credits). Each amount is the END of one line item. Walk back from each amount to find the label and any optional `(qty unit X rate)` middle.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_parser.py`:
```python
from comed_parser import parse_line_items


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_parser.py -v -k parse_line_items`
Expected: FAIL with import error

- [ ] **Step 3: Implement `parse_line_items`**

Append to `comed_parser.py`:
```python
# Match a single line-item ending in a $ amount. Three shapes captured by
# alternation; first match wins per position.
#
# Shape A: label + qty + unit (kWh|kW) + X + rate + amount
#   Distribution Facility Charge 1,646 kWh X 0.06228 $102.51
#
# Shape B: label + base$ + X + percentage + amount   (Franchise Cost only)
#   Franchise Cost $113.61 X 1.73900% $1.98
#
# Shape C: label + amount   (Customer Charge $15.35; Purchased Elec Adj $30.41)
#   Note: trailing label fragments can be greedy; stop at next known label
#   start, end-of-block, or before another $ amount.
_AMOUNT = r"-?\$?[-\d,]+\.\d{2}"
_LINE_ITEM_PATTERNS = [
    # Shape A — qty/unit/rate
    re.compile(
        r"([A-Z][A-Za-z &/-]+?)\s+([\d,]+)\s+(kWh|kW)\s*X\s*(-?[\d.]+)\s+(" + _AMOUNT + r")"
    ),
    # Shape B — Franchise-style $-base × percentage
    re.compile(
        r"([A-Z][A-Za-z &/-]+?)\s+\$([\d,]+\.\d{2})\s*X\s*([\d.]+)%\s+(" + _AMOUNT + r")"
    ),
    # Shape C — bare label + amount
    re.compile(
        r"([A-Z][A-Za-z &/-]+?)\s+(" + _AMOUNT + r")(?=\s|$)"
    ),
]


def parse_line_items(body: str, category: str) -> list[LineItem]:
    """Extract every line item in a block body. Tries shape A first
    (most specific), then B, then C. Each character position is consumed
    by at most one match."""
    items: list[LineItem] = []
    pos = 0
    while pos < len(body):
        best_match = None
        best_pat_idx = None
        for idx, pat in enumerate(_LINE_ITEM_PATTERNS):
            m = pat.match(body, pos)
            if m:
                # Prefer the longest match starting at this position
                if best_match is None or m.end() > best_match.end():
                    best_match = m
                    best_pat_idx = idx
        if best_match is None:
            pos += 1
            continue
        groups = best_match.groups()
        label = groups[0].strip()
        if best_pat_idx == 0:
            # Shape A: qty + unit + rate + amount
            items.append(LineItem(
                category=category, line_item=label,
                amount=_money(groups[4]),
                quantity=float(groups[1].replace(",", "")),
                unit=groups[2], rate=float(groups[3]),
            ))
        elif best_pat_idx == 1:
            # Shape B: $-base × percentage
            items.append(LineItem(
                category=category, line_item=label,
                amount=_money(groups[3]),
                quantity=float(groups[1].replace(",", "")),
                unit="$", rate=float(groups[2]) / 100.0,
            ))
        else:
            # Shape C: label + amount
            items.append(LineItem(
                category=category, line_item=label,
                amount=_money(groups[1]),
            ))
        pos = best_match.end()
    return items
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (all)

If any fail, the most likely cause is the label regex `[A-Z][A-Za-z &/-]+?` capturing too much or too little. Print `body[:500]` from the failing test to see what shape the actual text has, and tighten the regex (e.g. require the label to end before a digit or $).

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: line-item extractor (3 shapes: qty/rate, percentage, bare)"
```

---

## Task 10: Top-Level `parse_bill` Composer + Validation

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_parser.py`
- Modify: `deploy/energy-stack/scripts/tests/test_parser.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_parser.py`:
```python
from comed_parser import parse_bill, BillParseError


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
```

Add `import pytest` at top of test file if not present.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_parser.py -v -k parse_bill`
Expected: FAIL with import error

- [ ] **Step 3: Implement `parse_bill`**

Append to `comed_parser.py`:
```python
EXPECTED_ACCOUNT = "9999999991"


class BillParseError(Exception):
    pass


def parse_total_due(text: str) -> float:
    m = re.search(r"Total Amount Due\s*(-?\$?[\d,]+\.\d{2})", text)
    if not m:
        m = re.search(r"Service Period Total\s*(-?\$?[\d,]+\.\d{2})", text)
    if not m:
        raise BillParseError("could not find Total Amount Due")
    return _money(m.group(1))


def parse_bill(text: str) -> Bill:
    """Compose a Bill from extracted parts. Validates totals and account."""
    # text should already be normalized
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

    # Day count sanity
    actual_days = (service_to - service_from).days + 1
    if actual_days != service_days:
        # ComEd's day count is inclusive; if off by one, accept stated value
        # but flag if off by more than that
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

    # Capacity charge → peak_kw
    peak_kw = None
    for li in line_items:
        if li.line_item == "Capacity Charge" and li.unit == "kW":
            peak_kw = li.quantity
            break

    # Validate totals balance
    component_sum = supply_total + delivery_total + taxes_total + misc_total
    if abs(component_sum - total_due) >= 0.01:
        raise BillParseError(
            f"totals do not balance: "
            f"supply {supply_total} + delivery {delivery_total} + "
            f"taxes {taxes_total} + misc {misc_total} = {component_sum}, "
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: parse_bill composer with totals + account validation"
```

---

## Task 11: PDF File → Bill (top-level entrypoint)

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_parser.py`
- Modify: `deploy/energy-stack/scripts/tests/test_parser.py`

- [ ] **Step 1: Add failing test (with skip marker — needs a real PDF)**

Append to `tests/test_parser.py`:
```python
import os


@pytest.mark.skipif(
    not os.environ.get("COMED_TEST_PDF"),
    reason="set COMED_TEST_PDF to a bill PDF path to run",
)
def test_parse_bill_from_pdf_file_endtoend():
    from comed_parser import parse_pdf
    bill = parse_pdf(os.environ["COMED_TEST_PDF"])
    assert bill.account_no == "9999999991"
    assert bill.total_due > 0
```

- [ ] **Step 2: Implement `parse_pdf`**

Append to `comed_parser.py`:
```python
import pypdf
from pathlib import Path


def parse_pdf(path: str | Path) -> Bill:
    """Read a ComEd bill PDF and return a parsed Bill."""
    reader = pypdf.PdfReader(str(path))
    # Page 1 + 2 contain everything (charge details, meter info, totals)
    text = "".join(p.extract_text() + "\n" for p in reader.pages[:2])
    return parse_bill(normalize_text(text))
```

- [ ] **Step 3: Verify against a real PDF**

Run: `COMED_TEST_PDF="D:\Chris\Downloads\d54d630c-815b-4b42-a7f1-f2cf97f920e5.pdf" pytest tests/test_parser.py -v -k endtoend`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add deploy/energy-stack/scripts/comed_parser.py deploy/energy-stack/scripts/tests/test_parser.py
git commit -m "feat: parse_pdf entrypoint (PDF file -> Bill)"
```

---

## Task 12: InfluxDB Line Protocol Generation

**Files:**
- Create: `deploy/energy-stack/scripts/comed_influx.py`
- Create: `deploy/energy-stack/scripts/tests/test_influx.py`

- [ ] **Step 1: Write the failing test**

File: `deploy/energy-stack/scripts/tests/test_influx.py`
```python
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
    bill_lines = [l for l in lines if l.startswith("comed.bill ")]
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
    li_lines = [l for l in lines if l.startswith("comed.bill_lineitems ")]
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
    bill_line = next(l for l in lines if l.startswith("comed.bill "))
    assert "peak_kw=" not in bill_line  # field omitted when null


def test_timestamp_is_service_to_2359_chicago():
    """service_to=2026-04-23 → 2026-04-23 23:59:59 America/Chicago →
       2026-04-24 04:59:59 UTC → ns timestamp."""
    lines = bill_to_line_protocol(make_bill()).splitlines()
    bill_line = next(l for l in lines if l.startswith("comed.bill "))
    # Last token is the nanosecond timestamp
    ts = int(bill_line.rsplit(" ", 1)[1])
    # Expected: 2026-04-24 04:59:59 UTC
    expected = 1777352399 * 1_000_000_000
    assert ts == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_influx.py -v`
Expected: FAIL — `comed_influx` doesn't exist

- [ ] **Step 3: Implement `comed_influx.py`**

File: `deploy/energy-stack/scripts/comed_influx.py`
```python
"""Bill -> InfluxDB line protocol generation.

Line protocol reference:
  measurement,tag1=value1,tag2=value2 field1=val1,field2=val2 timestamp_ns
Tag/field keys and string values containing spaces or commas must be
backslash-escaped.
"""
from datetime import datetime, timezone, timedelta
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
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `pytest tests/test_influx.py -v`
Expected: PASS (all 4)

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_influx.py deploy/energy-stack/scripts/tests/test_influx.py
git commit -m "feat: bill_to_line_protocol generator with tag/field escaping"
```

---

## Task 13: InfluxDB Writer + Main Script

**Files:**
- Modify: `deploy/energy-stack/scripts/comed_influx.py`
- Create: `deploy/energy-stack/scripts/parse_comed_bill.py`

- [ ] **Step 1: Add `write_bill` to `comed_influx.py`**

Append to `comed_influx.py`:
```python
import os
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


def write_bill(bill: Bill, *, url: str, token: str, org: str, bucket: str) -> None:
    """Write a parsed bill to InfluxDB. Idempotent: re-writing the same bill
    produces upserts because (measurement, tags, timestamp) collide."""
    lp = bill_to_line_protocol(bill)
    with InfluxDBClient(url=url, token=token, org=org, timeout=30_000) as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=bucket, record=lp)
```

- [ ] **Step 2: Write the main script**

File: `deploy/energy-stack/scripts/parse_comed_bill.py`
```python
#!/usr/bin/env python3
"""Parse a ComEd bill PDF and write its data to InfluxDB.

Usage:
    python parse_comed_bill.py <bill.pdf>

Environment (with defaults pointing at Pi-lab energy-stack):
    INFLUX_URL    (default http://localhost:8086)
    INFLUX_TOKEN  (required)
    INFLUX_ORG    (default 'home')
    INFLUX_BUCKET (default 'energy')

On success: moves the PDF to inbox/comed/processed/comed-YYYY-MM-DD-<period>.pdf
On failure: leaves the PDF in place, prints error, exits non-zero.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

from comed_parser import parse_pdf, BillParseError
from comed_influx import write_bill


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Path to a ComEd bill PDF")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse + print line protocol; do not write to Influx")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.is_file():
        print(f"error: {pdf_path} is not a file", file=sys.stderr)
        sys.exit(2)

    try:
        bill = parse_pdf(pdf_path)
    except (BillParseError, ValueError) as e:
        print(f"error: parse failed: {e}", file=sys.stderr)
        sys.exit(3)

    print(
        f"Parsed: {bill.service_from} → {bill.service_to} ({bill.service_days}d) "
        f"{bill.kwh} kWh  ${bill.total_due:.2f}  "
        f"plan={bill.rate_plan}  type={bill.bill_type}  "
        f"peak_kw={bill.peak_kw}"
    )

    if args.dry_run:
        from comed_influx import bill_to_line_protocol
        print()
        print(bill_to_line_protocol(bill))
        return

    token = os.environ.get("INFLUX_TOKEN")
    if not token:
        print("error: INFLUX_TOKEN env var is required", file=sys.stderr)
        sys.exit(4)

    write_bill(
        bill,
        url=os.environ.get("INFLUX_URL", "http://localhost:8086"),
        token=token,
        org=os.environ.get("INFLUX_ORG", "home"),
        bucket=os.environ.get("INFLUX_BUCKET", "energy"),
    )
    print(f"  wrote to InfluxDB: 1 comed.bill + {len(bill.line_items)} bill_lineitems")

    # Move PDF to processed/
    processed_dir = pdf_path.parent / "processed"
    processed_dir.mkdir(exist_ok=True)
    new_name = f"comed-{bill.service_to.isoformat()}-{bill.service_from.isoformat()}.pdf"
    dest = processed_dir / new_name
    shutil.move(str(pdf_path), str(dest))
    print(f"  moved to {dest}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test the script in dry-run mode**

Run: `cd deploy/energy-stack/scripts && python parse_comed_bill.py "D:\Chris\Downloads\d54d630c-815b-4b42-a7f1-f2cf97f920e5.pdf" --dry-run`
Expected: prints parse summary + line protocol; PDF NOT moved (because of --dry-run)

- [ ] **Step 4: Verify all tests still pass**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/energy-stack/scripts/comed_influx.py deploy/energy-stack/scripts/parse_comed_bill.py
git commit -m "feat: main script (parse + Influx write + processed/ move)"
```

---

## Task 14: Operator README

**Files:**
- Create: `deploy/energy-stack/scripts/README.md`

- [ ] **Step 1: Write the README**

File: `deploy/energy-stack/scripts/README.md`
```markdown
# energy-stack scripts

Operator scripts for the energy-stack. Each is a single-purpose Python script
run by hand (not a service). Tests run with `pytest`; deps in `requirements.txt`.

## parse_comed_bill.py — ComEd bill ingest

Parses a ComEd bill PDF and writes the structured data to InfluxDB:

- One `comed.bill` point (top-line: total_due, kwh, peak_kw, supply/delivery/taxes/misc totals)
- N `comed.bill_lineitems` points (full breakdown by category + line item)

### One-time setup

On Pi-lab:
```bash
cd ~/energy-stack/scripts
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mkdir -p ~/energy-stack/inbox/comed
```

Add to `~/energy-stack/.env` (or shell profile):
```
INFLUX_URL=http://localhost:8086
INFLUX_TOKEN=<from energy-stack/.env>
INFLUX_ORG=home
INFLUX_BUCKET=energy
```

### Monthly workflow

1. ComEd email arrives → click "View bill" on portal → download PDF (2FA required)
2. From workstation:
   ```
   scp <bill>.pdf pi-lab:~/energy-stack/inbox/comed/
   ssh pi-lab
   cd ~/energy-stack/scripts && source .venv/bin/activate
   set -a; source ~/energy-stack/.env; set +a
   python parse_comed_bill.py ~/energy-stack/inbox/comed/<bill>.pdf
   ```
3. Script: parses → writes to Influx → moves PDF to `inbox/comed/processed/comed-YYYY-MM-DD-...pdf`

### Optional: workstation alias

In your workstation shell profile:
```bash
comed-ingest() {
    scp "$1" pi-lab:~/energy-stack/inbox/comed/ &&
    ssh pi-lab "cd ~/energy-stack/scripts && source .venv/bin/activate && set -a; source ~/energy-stack/.env; set +a; python parse_comed_bill.py ~/energy-stack/inbox/comed/$(basename "$1")"
}
```

Then: `comed-ingest ~/Downloads/comed-bill.pdf`

### Idempotency

`bill_id = sha256(account_no || service_from || service_to)` is encoded
implicitly via Influx's (measurement, tag set, timestamp) deduplication.
Re-running the same bill upserts the same points — safe to retry on errors.

### Validation

The parser refuses to write if any of these fail (PDF stays in inbox):
- `account_no != 9999999991` (your account)
- `supply + delivery + taxes + misc != total_due` (within $0.01)
- `service_days` doesn't match calendar diff (off-by-one tolerated)

### Dry run

Add `--dry-run` to parse + print the generated line protocol without writing:
```
python parse_comed_bill.py file.pdf --dry-run
```

### Backfill

Loop over historical bills:
```
for f in ~/energy-stack/inbox/comed/backfill/*.pdf; do
    python parse_comed_bill.py "$f"
done
```
Idempotency makes this re-runnable. Duplicates collapse automatically.
```

- [ ] **Step 2: Commit**

```bash
git add deploy/energy-stack/scripts/README.md
git commit -m "docs: add scripts/README.md (parse_comed_bill workflow)"
```

---

## Task 15: Backfill the 9 Historical Bills

This task is run once, against the 9 PDFs Chris already supplied.

- [ ] **Step 1: Stage the bills on Pi-lab**

From workstation:
```bash
scp D:\Chris\Downloads\05dcb2f9-4c1d-49ab-8d24-8759342cb666.pdf \
    D:\Chris\Downloads\b3aca6ce-6bcc-4d46-a252-64fb80081949.pdf \
    D:\Chris\Downloads\49aa4c59-4e19-4a09-b348-72469a0a5fe9.pdf \
    D:\Chris\Downloads\56174f5a-427b-4a03-b916-dd7bd7b95d7e.pdf \
    D:\Chris\Downloads\dff224a6-0509-4e73-9985-5e6bff158329.pdf \
    D:\Chris\Downloads\4ace3f0e-37ce-4df8-b0af-43d8c953b6ef.pdf \
    D:\Chris\Downloads\d54d630c-815b-4b42-a7f1-f2cf97f920e5.pdf \
    D:\Chris\Downloads\1c785f6f-7f43-444c-9c51-3edfd0c06e4b.pdf \
    D:\Chris\Downloads\a33bdd65-00a0-4d72-88c7-7c982779acf0.pdf \
    D:\Chris\Downloads\42772a11-c668-4732-9782-97a3dd8c74c3.pdf \
    pi-lab:~/energy-stack/inbox/comed/backfill/
```

- [ ] **Step 2: Dry-run all 9 bills first to confirm parses**

On Pi-lab:
```bash
cd ~/energy-stack/scripts && source .venv/bin/activate
for f in ~/energy-stack/inbox/comed/backfill/*.pdf; do
    echo "=== $f ==="
    python parse_comed_bill.py "$f" --dry-run | head -3
done
```
Expected: 10 successful parses (the duplicate parses identically, will dedupe at Influx)

- [ ] **Step 3: Real-write all 10 bills**

```bash
set -a; source ~/energy-stack/.env; set +a
for f in ~/energy-stack/inbox/comed/backfill/*.pdf; do
    python parse_comed_bill.py "$f"
done
```
Expected: 9 unique writes (the 10th — `1c785f6f` — silently overwrites its twin)

- [ ] **Step 4: Verify in Influx**

```bash
docker exec energy-stack-influxdb-1 influx query \
  --org home --token "$INFLUX_TOKEN" \
  'from(bucket:"energy") |> range(start:-2y) |> filter(fn:(r)=>r._measurement=="comed.bill") |> filter(fn:(r)=>r._field=="total_due") |> count()'
```
Expected: count = 9 (one per unique bill)

```bash
docker exec energy-stack-influxdb-1 influx query \
  --org home --token "$INFLUX_TOKEN" \
  'from(bucket:"energy") |> range(start:-2y) |> filter(fn:(r)=>r._measurement=="comed.bill_lineitems") |> count()'
```
Expected: count > 150 (each bill has ~18-22 line items)

- [ ] **Step 5: Move backfill subdirectory contents to processed**

The script already moved each one as it ran. The empty `backfill/` directory can be removed:
```bash
rmdir ~/energy-stack/inbox/comed/backfill
```

(No commit — this task is pure data migration, no code changes.)

---

## Task 16: Grafana Dashboard

**Files:**
- Create: `deploy/energy-stack/grafana/dashboards/comed-bill-reconciliation.json`

The dashboard has 4 panels matching the spec.

- [ ] **Step 1: Write the dashboard JSON**

File: `deploy/energy-stack/grafana/dashboards/comed-bill-reconciliation.json`

Build the dashboard skeleton with 4 panels. Use the existing `home-energy-overview.json` as a template for tags/datasource/refresh interval.

```json
{
  "annotations": { "list": [] },
  "editable": true,
  "fiscalYearStartMonth": 0,
  "graphTooltip": 0,
  "links": [],
  "liveNow": false,
  "panels": [
    {
      "id": 1,
      "title": "A — Bill total vs InfluxDB-projected total",
      "type": "barchart",
      "gridPos": { "h": 9, "w": 12, "x": 0, "y": 0 },
      "datasource": { "type": "influxdb", "uid": "influxdb" },
      "targets": [
        {
          "refId": "Bill",
          "query": "from(bucket:\"energy\")\n  |> range(start: -2y)\n  |> filter(fn:(r)=>r._measurement==\"comed.bill\" and r._field==\"total_due\" and r.bill_type==\"normal\")\n  |> map(fn:(r)=>({ r with _value: r._value, _time: r._time }))\n  |> keep(columns:[\"_time\",\"_value\"])"
        },
        {
          "refId": "Projected",
          "hide": false,
          "query": "import \"timezone\"\noption location = timezone.location(name: \"America/Chicago\")\n// For each bill cycle, sum (Refoss whole-home W * ComEd hourly price) over [from, to)\n// Joins to bill periods via the comed.bill service_from/service_to fields\n// (Implementation note: this query is a stretch — for v1, simply chart\n// the bill total alone and add the projected overlay in a follow-up.)\nfrom(bucket:\"energy\")\n  |> range(start: -2y)\n  |> filter(fn:(r)=>r._measurement==\"comed.bill\" and r._field==\"total_due\")\n  |> map(fn:(r)=>({ r with _value: 0.0 }))"
        }
      ],
      "options": { "legend": { "displayMode": "list", "placement": "bottom" } }
    },
    {
      "id": 2,
      "title": "B — EAGLE kWh vs billed kWh per cycle",
      "type": "barchart",
      "gridPos": { "h": 9, "w": 12, "x": 12, "y": 0 },
      "datasource": { "type": "influxdb", "uid": "influxdb" },
      "targets": [
        {
          "refId": "Billed",
          "query": "from(bucket:\"energy\")\n  |> range(start: -2y)\n  |> filter(fn:(r)=>r._measurement==\"comed.bill\" and r._field==\"kwh\" and r.bill_type==\"normal\")"
        }
      ]
    },
    {
      "id": 3,
      "title": "D — Capacity charge tracker",
      "type": "timeseries",
      "gridPos": { "h": 9, "w": 24, "x": 0, "y": 9 },
      "datasource": { "type": "influxdb", "uid": "influxdb" },
      "targets": [
        {
          "refId": "PeakKW",
          "query": "from(bucket:\"energy\")\n  |> range(start: -2y)\n  |> filter(fn:(r)=>r._measurement==\"comed.bill\" and r._field==\"peak_kw\")"
        },
        {
          "refId": "CapacityCharge",
          "query": "from(bucket:\"energy\")\n  |> range(start: -2y)\n  |> filter(fn:(r)=>r._measurement==\"comed.bill_lineitems\" and r.line_item==\"Capacity Charge\" and r._field==\"amount\")"
        }
      ]
    },
    {
      "id": 4,
      "title": "F — Forward projection (current cycle)",
      "type": "stat",
      "gridPos": { "h": 6, "w": 12, "x": 0, "y": 18 },
      "datasource": { "type": "influxdb", "uid": "influxdb" },
      "targets": [
        {
          "refId": "MostRecentBill",
          "query": "from(bucket:\"energy\")\n  |> range(start: -90d)\n  |> filter(fn:(r)=>r._measurement==\"comed.bill\" and r._field==\"total_due\" and r.bill_type==\"normal\")\n  |> last()"
        }
      ],
      "options": {
        "reduceOptions": { "values": false, "calcs": ["last"] }
      }
    }
  ],
  "refresh": "5m",
  "schemaVersion": 38,
  "style": "dark",
  "tags": ["energy", "comed", "billing"],
  "templating": { "list": [] },
  "time": { "from": "now-2y", "to": "now" },
  "timepicker": {},
  "timezone": "America/Chicago",
  "title": "ComEd Bill Reconciliation",
  "uid": "comed-bill-reconciliation",
  "version": 1,
  "weekStart": ""
}
```

Note: panels A's "Projected" target and panel F's full forward-projection formula are **stubs** in v1 (returning zeros / showing only the most recent bill). The point of this task is to land a working dashboard that displays real bill data; the projection-overlay queries are non-trivial Flux that's better refined in a follow-up after a few cycles of data accumulate. Comment in the JSON marks them.

- [ ] **Step 2: Verify dashboard loads in Grafana**

Sync the file to Pi (it's auto-loaded via Grafana provisioning):
```bash
scp deploy/energy-stack/grafana/dashboards/comed-bill-reconciliation.json pi-lab:~/energy-stack/grafana/dashboards/
ssh pi-lab "docker exec energy-stack-grafana-1 kill -HUP 1"
```
Open Grafana → Dashboards → "ComEd Bill Reconciliation" should appear with all 4 panels rendering data from the backfilled bills.

- [ ] **Step 3: Commit**

```bash
git add deploy/energy-stack/grafana/dashboards/comed-bill-reconciliation.json
git commit -m "feat: add comed bill reconciliation dashboard (4 panels)"
```

---

## Task 17: Update PROJECT.md and SERVICES.md

**Files:**
- Modify: `PROJECT.md`
- Modify: `docs/SERVICES.md`

- [ ] **Step 1: Append Phase 8 entry to `PROJECT.md`**

Insert before the "Open Questions" section, in the "Recent Decisions" section, OR add a new section at the bottom titled `## Phase 8 — ComEd Bill Ingest (May 2026)`:

```markdown
### Phase 8 — ComEd Bill Ingest (May 2026)

Manual-upload Python script (`deploy/energy-stack/scripts/parse_comed_bill.py`)
that parses ComEd bill PDFs into InfluxDB. Run by hand each month after
downloading the PDF from the ComEd portal. Backfilled 9 historical bills
covering 8/2025-4/2026.

**Why bills, not just real-time telemetry**: the dashboard's cost calc
(`Σ power × hourly_supply_price`) was supply-only. Real bills add delivery,
capacity, riders, and taxes — typically 40-70% on top. The capacity charge
in particular is what the HVAC scheduler exists to suppress (latest bill:
$54.64 from `6.56 kW × $8.32925`, locked annually from prior summer's PJM
5CP). Without bill ingest, no way to measure scheduler effectiveness.

**Why script not container**: 12 events/year, parser is the only hard
part. A service loop, Dockerfile, healthcheck, and Telegram failure-alert
wiring add LOC and zero capability over `python parse_comed_bill.py file.pdf`.

**Why pypdf not Docling**: empirically tested both. Docling table inference
conflates layout-adjacent columns on ComEd's multi-column print layout
(SUPPLY values mixed with DELIVERY values in the same table row, or DELIVERY
section header dropped entirely). pypdf's flat reading order keeps each line
item adjacent to its values, so the regex `Capacity Charge\s*([\d.]+)\s*kW`
is unambiguous. Docling stays in the kit for future utility ingests where
the source is genuinely tabular (water, gas, property tax).

**Schema**: `comed.bill` (top-line per cycle: total_due, kwh, peak_kw,
supply/delivery/taxes/misc totals) + `comed.bill_lineitems` (full GL
breakdown). Idempotency: SHA-256 of (account, from, to) → same Influx
(measurement, tags, timestamp) → safe to re-run same bill.

**Dashboard**: `deploy/energy-stack/grafana/dashboards/comed-bill-reconciliation.json` —
bill-vs-projected, EAGLE-vs-billed-kWh, capacity-charge tracker, and a
stub forward-projection panel. The projection panel's full formula
(supply-so-far + delivery estimate + capacity + taxes estimate + days-remaining
extrapolation) is sketched but lands as a follow-on after a few cycles
of data accumulate.

Spec: `docs/archive/phase-8-comed-bill-ingest-design.md`
Plan: `docs/archive/phase-8-comed-bill-ingest-plan.md`
```

- [ ] **Step 2: Append script entry to `docs/SERVICES.md`**

Add a section for `parse_comed_bill.py` in the format used by other entries (env vars, fields written, etc.). Pattern-match the existing `haven-ingest` entry if there is one; otherwise:

```markdown
## parse_comed_bill.py (manual script, not a service)

**Path**: `deploy/energy-stack/scripts/parse_comed_bill.py`
**Run by**: operator, by hand, on Pi-lab when a new bill arrives
**Frequency**: ~12x/year

**Env vars**:
- `INFLUX_URL` (default `http://localhost:8086`)
- `INFLUX_TOKEN` (required)
- `INFLUX_ORG` (default `home`)
- `INFLUX_BUCKET` (default `energy`)

**Writes**:
- `comed.bill` measurement: 1 point per bill, timestamped at service_to 23:59:59 CDT
  - Tags: `account_no`, `rate_plan`, `bill_type`
  - Fields: `total_due`, `kwh`, `peak_kw` (null on fixed-rate),
            `supply_total`, `delivery_total`, `taxes_total`, `misc_total`,
            `effective_rate_per_kwh`, `service_days`, `issued_date`,
            `service_from`, `service_to`
- `comed.bill_lineitems`: 18-25 points per bill, same timestamp
  - Tags: `account_no`, `category` (SUPPLY|DELIVERY|TAXES_FEES_CREDITS|MISC),
          `line_item`
  - Fields: `amount`, `quantity` (optional), `unit` (optional), `rate` (optional)

**Workflow**: see `deploy/energy-stack/scripts/README.md`
```

- [ ] **Step 3: Commit**

```bash
git add PROJECT.md docs/SERVICES.md
git commit -m "docs: PROJECT.md + SERVICES.md entries for Phase 8"
```

---

## Self-Review Checklist (run after writing the plan)

- [x] **Spec coverage** — every spec section has a task:
  - Architecture (script-based) → Task 13
  - Schema (`comed.bill` + `comed.bill_lineitems`) → Tasks 3, 12
  - Parser strategy (two formats, generic block extraction) → Tasks 5-11
  - Validation gates → Task 10
  - Backfill of 9 bills → Task 15
  - Dashboards (A, B, D, F panels) → Task 16
  - PROJECT.md / SERVICES.md updates → Task 17

- [x] **Placeholder scan** — no TBDs in actionable steps. Two stubs are explicitly called out as v1 limitations: panel A's projected overlay and panel F's full formula. These are deferred deliberately, not forgotten.

- [x] **Type consistency** — `Bill`, `LineItem`, `bill_id`, `parse_*`, `extract_*_block`, `parse_line_items`, `bill_to_line_protocol`, `write_bill`, `parse_pdf` — names match across tasks.

- [x] **Test before code** — every parser task has a failing-test step before the implementation step.

- [x] **Frequent commits** — one commit per task (17 commits total).
