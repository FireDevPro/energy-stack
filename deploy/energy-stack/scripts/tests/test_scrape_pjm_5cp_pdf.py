"""Tests for scrape_pjm_5cp_pdf.py — the annual PJM 5CP PDF scraper.

Tests the pure-parsing layer with text fixtures derived from real
PJM PDFs (Summer 2024 used as the canonical fixture). HTTP and Influx
are not exercised here.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from scrape_pjm_5cp_pdf import (
    CoincidentPeak,
    CoincidentPeakParseError,
    PEAK_ROW_RE,
    COMED_ROW_RE,
    extract_summer_year,
    parse_5cp_block,
    parse_comed_zonal_row,
)


# ---- text fixtures derived from the real 2024 PDF -------------------------

PAGE1_TEXT_2024 = """\
PJM 2024  11/14/2024
PJM Interconnection
Summer 2024 Weather Normalized RTO Coincident Peaks (MW)
Zone Peak
AE 2,370
COMED 19,040
PJM RTO 152,803.7
Summer 2024 RTO Coincident Peaks (5CP)
Day Date Hour MW
Tuesday 7/16/2024 18:00 152,307
Monday 7/15/2024 18:00 151,103
Thursday 8/1/2024 18:00 148,240
Friday 6/21/2024 18:00 147,271
Wednesday 8/28/2024 18:00 147,249
Revised 11/14/2024 for ATSI and Dominion Non-Retail BTMG.
"""

PAGE2_TEXT_2024 = """\
Date 7/16/2024 7/15/2024 8/1/2024 6/21/2024 8/28/2024
Hour End (EPT) 18:00 18:00 18:00 18:00 18:00
AE 2,522.3      2,521.4      2,506.0      2,236.1      2,469.9
COMED 16,972.5    19,014.1    17,556.2    18,568.7    16,726.8
PJM RTO 152,307.4  151,103.1  148,239.7  147,270.6  147,248.5
"""


# ---- regex pinning --------------------------------------------------------


def test_peak_row_regex_matches_real_format():
    m = PEAK_ROW_RE.match("Tuesday 7/16/2024 18:00 152,307")
    assert m is not None
    assert m["day"] == "Tuesday"
    assert m["date"] == "7/16/2024"
    assert m["hour"] == "18:00"
    assert m["mw"] == "152,307"


def test_peak_row_regex_handles_decimal_mw():
    """2025 PDF used decimal MW for some entries; regex must accept both."""
    m = PEAK_ROW_RE.match("Monday 7/29/2025 18:00 156,035.4")
    assert m is not None
    assert m["mw"] == "156,035.4"


def test_comed_row_regex():
    m = COMED_ROW_RE.match("COMED 16,972.5    19,014.1    17,556.2    18,568.7    16,726.8")
    assert m is not None
    assert [m[f"m{i}"] for i in range(1, 6)] == [
        "16,972.5", "19,014.1", "17,556.2", "18,568.7", "16,726.8",
    ]


# ---- parse_5cp_block ------------------------------------------------------


def test_parse_5cp_block_returns_five_peaks():
    peaks = parse_5cp_block(PAGE1_TEXT_2024, summer_year=2024)
    assert len(peaks) == 5


def test_parse_5cp_block_converts_hour_ending_to_hour_beginning():
    """PDF says 18:00 'Hour Ending'; we store 17:00 as the hour-beginning
    timestamp so it lines up with how everything else in the stack is
    timestamped."""
    peaks = parse_5cp_block(PAGE1_TEXT_2024, summer_year=2024)
    for hb, _ in peaks:
        assert hb.hour == 17


def test_parse_5cp_block_descending_mw_order():
    """PDF lists peaks in descending RTO MW order. Our parser preserves
    that, which is critical because page 2's columns line up by the same
    ordering."""
    peaks = parse_5cp_block(PAGE1_TEXT_2024, summer_year=2024)
    mws = [mw for _, mw in peaks]
    assert mws == sorted(mws, reverse=True)


def test_parse_5cp_block_year_within_target_range():
    peaks = parse_5cp_block(PAGE1_TEXT_2024, summer_year=2024)
    for hb, _ in peaks:
        assert hb.year == 2024
        assert hb.month in (6, 7, 8, 9, 10)


def test_parse_5cp_block_raises_when_count_wrong():
    truncated = """\
Summer 2024 RTO Coincident Peaks (5CP)
Day Date Hour MW
Tuesday 7/16/2024 18:00 152,307
Monday 7/15/2024 18:00 151,103
"""
    with pytest.raises(CoincidentPeakParseError, match="parsed 2"):
        parse_5cp_block(truncated, summer_year=2024)


def test_parse_5cp_block_raises_when_year_mismatched():
    """If the date in a row doesn't match the target summer year, the
    layout has drifted and we should fail rather than write bad data."""
    bad = PAGE1_TEXT_2024.replace("7/16/2024", "7/16/2023", 1)
    with pytest.raises(CoincidentPeakParseError, match="outside summer"):
        parse_5cp_block(bad, summer_year=2024)


def test_parse_5cp_block_raises_when_month_outside_summer_window():
    """Defensive: a row with a January date, even if the year is right,
    means the layout has changed (PJM never publishes a Jan 5CP)."""
    bad = PAGE1_TEXT_2024.replace("7/16/2024", "1/16/2024", 1)
    with pytest.raises(CoincidentPeakParseError, match="outside summer"):
        parse_5cp_block(bad, summer_year=2024)


# ---- parse_comed_zonal_row -----------------------------------------------


def test_parse_comed_zonal_row_returns_five_floats():
    vals = parse_comed_zonal_row(PAGE2_TEXT_2024)
    assert vals == [16972.5, 19014.1, 17556.2, 18568.7, 16726.8]


def test_parse_comed_zonal_row_raises_when_missing():
    no_comed = PAGE2_TEXT_2024.replace("COMED", "AECO")
    with pytest.raises(CoincidentPeakParseError, match="ComEd zonal row not found"):
        parse_comed_zonal_row(no_comed)


# ---- extract_summer_year -------------------------------------------------


def test_extract_summer_year():
    assert extract_summer_year(PAGE1_TEXT_2024) == 2024


def test_extract_summer_year_returns_none_when_missing():
    assert extract_summer_year("nothing relevant here") is None


# ---- end-to-end parse_pdf with monkey-patched extract --------------------


def test_parse_pdf_end_to_end(monkeypatch):
    """Stub PdfReader to return our fixture pages so we don't need a binary
    PDF in the repo."""
    import scrape_pjm_5cp_pdf as mod

    class _StubPage:
        def __init__(self, text: str) -> None:
            self._text = text
        def extract_text(self) -> str:
            return self._text

    class _StubReader:
        def __init__(self, _stream) -> None:
            self.pages = [_StubPage(PAGE1_TEXT_2024), _StubPage(PAGE2_TEXT_2024)]

    monkeypatch.setattr(mod, "PdfReader", _StubReader)

    peaks = mod.parse_pdf(b"unused", expected_year=2024)
    assert len(peaks) == 5

    rank1 = peaks[0]
    assert rank1.peak_rank == 1
    assert rank1.summer_year == 2024
    assert rank1.hour_beginning_local == datetime(2024, 7, 16, 17, 0)
    assert rank1.rto_peak_mw == 152307.0
    assert rank1.comed_zone_mw == 16972.5

    rank5 = peaks[4]
    assert rank5.peak_rank == 5
    assert rank5.hour_beginning_local == datetime(2024, 8, 28, 17, 0)
    assert rank5.rto_peak_mw == 147249.0
    assert rank5.comed_zone_mw == 16726.8


def test_parse_pdf_raises_on_year_mismatch(monkeypatch):
    """If the PDF says 'Summer 2023' and we asked for 2024, refuse to write."""
    import scrape_pjm_5cp_pdf as mod

    bad_page1 = PAGE1_TEXT_2024.replace("Summer 2024", "Summer 2023")
    class _StubPage:
        def __init__(self, text): self._text = text
        def extract_text(self): return self._text
    class _StubReader:
        def __init__(self, _stream): self.pages = [_StubPage(bad_page1), _StubPage(PAGE2_TEXT_2024)]
    monkeypatch.setattr(mod, "PdfReader", _StubReader)

    with pytest.raises(CoincidentPeakParseError, match="year mismatch"):
        mod.parse_pdf(b"unused", expected_year=2024)


def test_parse_pdf_plausibility_gate_rto_too_low(monkeypatch):
    """If RTO peak comes back at 1,000 MW (clearly wrong), refuse rather
    than write bad data. Catches PDF-format-drift bugs that yield
    nonsensical values."""
    import scrape_pjm_5cp_pdf as mod

    bad_page1 = PAGE1_TEXT_2024.replace("152,307", "1,000")
    class _StubPage:
        def __init__(self, text): self._text = text
        def extract_text(self): return self._text
    class _StubReader:
        def __init__(self, _stream): self.pages = [_StubPage(bad_page1), _StubPage(PAGE2_TEXT_2024)]
    monkeypatch.setattr(mod, "PdfReader", _StubReader)

    with pytest.raises(CoincidentPeakParseError, match="RTO peak.*outside"):
        mod.parse_pdf(b"unused", expected_year=2024)


def test_parse_pdf_plausibility_gate_comed_too_high(monkeypatch):
    """If ComEd zonal comes back at 50,000 MW, that's larger than the RTO
    peak — definitely wrong."""
    import scrape_pjm_5cp_pdf as mod

    bad_page2 = PAGE2_TEXT_2024.replace("16,972.5", "50,000.5")
    class _StubPage:
        def __init__(self, text): self._text = text
        def extract_text(self): return self._text
    class _StubReader:
        def __init__(self, _stream): self.pages = [_StubPage(PAGE1_TEXT_2024), _StubPage(bad_page2)]
    monkeypatch.setattr(mod, "PdfReader", _StubReader)

    with pytest.raises(CoincidentPeakParseError, match="ComEd zonal.*outside"):
        mod.parse_pdf(b"unused", expected_year=2024)
