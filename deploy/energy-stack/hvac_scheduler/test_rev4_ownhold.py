from __future__ import annotations

import json

from .controller.ownhold import OwnHoldRecord, clear_record, load_record, save_record


def test_roundtrip(tmp_path):
    d = str(tmp_path)
    assert load_record(d) is None
    rec = OwnHoldRecord(value=27.0, until_minutes=870, expiry_utc="2026-07-10T19:30:00+00:00")
    save_record(d, rec)
    assert load_record(d) == rec
    clear_record(d)
    assert load_record(d) is None
    assert json.loads((tmp_path / "own_hold.json").read_text()) is None


def test_corrupt_file_reads_as_none(tmp_path):
    (tmp_path / "own_hold.json").write_text("{not json", encoding="utf-8")
    assert load_record(str(tmp_path)) is None
