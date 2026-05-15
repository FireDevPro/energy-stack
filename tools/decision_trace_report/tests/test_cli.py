"""Tests for the CLI entry point (argparse + default behavior)."""
from datetime import date, timedelta

import pytest

from tools.decision_trace_report.cli import parse_args, default_target_date


def test_parse_args_defaults():
    """No flags -> target=None (resolves to yesterday CT), output=default."""
    args = parse_args([])
    assert args.date is None
    assert args.from_ct is None
    assert args.to_ct is None
    assert args.no_telegram is False
    assert args.verbose is False


def test_parse_args_date_flag():
    args = parse_args(["--date", "2026-05-14"])
    assert args.date == "2026-05-14"


def test_parse_args_from_to_flags():
    args = parse_args(["--from", "2026-05-14T13:00", "--to", "2026-05-14T19:00"])
    assert args.from_ct == "2026-05-14T13:00"
    assert args.to_ct == "2026-05-14T19:00"


def test_parse_args_no_telegram():
    args = parse_args(["--no-telegram"])
    assert args.no_telegram is True


def test_default_target_date_is_yesterday_ct():
    """When --date is omitted, the target is yesterday CT (rendered
    day = run day - 1)."""
    target = default_target_date(now=date(2026, 5, 16))
    assert target == "2026-05-15"


def test_validate_args_rejects_partial_range():
    """--from without --to (or vice versa) is invalid."""
    from tools.decision_trace_report.cli import validate_args
    args = parse_args(["--from", "2026-05-14T13:00"])
    with pytest.raises(SystemExit):
        validate_args(args)


def test_validate_args_rejects_date_with_range():
    """--date and --from/--to are mutually exclusive."""
    from tools.decision_trace_report.cli import validate_args
    args = parse_args([
        "--date", "2026-05-14",
        "--from", "2026-05-14T13:00",
        "--to", "2026-05-14T19:00",
    ])
    with pytest.raises(SystemExit):
        validate_args(args)


def test_resolve_window_default_yesterday_ct():
    """No --date / --from / --to -> yesterday CT, 00:00 to 24:00."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tools.decision_trace_report.cli import resolve_window

    args = parse_args([])
    label, start_ct, end_ct = resolve_window(args, now=date(2026, 5, 16))
    ct = ZoneInfo("America/Chicago")
    assert label == "2026-05-15"
    assert start_ct == datetime(2026, 5, 15, 0, 0, tzinfo=ct)
    assert end_ct == datetime(2026, 5, 16, 0, 0, tzinfo=ct)


def test_resolve_window_date_flag():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tools.decision_trace_report.cli import resolve_window

    args = parse_args(["--date", "2026-05-10"])
    label, start_ct, end_ct = resolve_window(args, now=date(2026, 5, 16))
    ct = ZoneInfo("America/Chicago")
    assert label == "2026-05-10"
    assert start_ct == datetime(2026, 5, 10, 0, 0, tzinfo=ct)
    assert end_ct == datetime(2026, 5, 11, 0, 0, tzinfo=ct)


def test_resolve_window_from_to_arbitrary_range():
    """--from / --to use CT-local clock values, ZoneInfo handles DST."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tools.decision_trace_report.cli import resolve_window

    args = parse_args(["--from", "2026-05-14T13:00", "--to", "2026-05-14T19:30"])
    label, start_ct, end_ct = resolve_window(args, now=date(2026, 5, 16))
    ct = ZoneInfo("America/Chicago")
    assert start_ct == datetime(2026, 5, 14, 13, 0, tzinfo=ct)
    assert end_ct == datetime(2026, 5, 14, 19, 30, tzinfo=ct)
    # Label includes both endpoints for non-day-aligned ranges so the
    # output filename + headers reflect the actual window queried.
    assert "2026-05-14T13:00" in label and "2026-05-14T19:30" in label


def test_resolve_window_from_to_winter_cst_no_dst_drift():
    """Winter (CST) range computes correctly — DST hygiene."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tools.decision_trace_report.cli import resolve_window

    args = parse_args(["--from", "2026-01-15T00:00", "--to", "2026-01-16T00:00"])
    label, start_ct, end_ct = resolve_window(args, now=date(2026, 1, 17))
    ct = ZoneInfo("America/Chicago")
    assert start_ct == datetime(2026, 1, 15, 0, 0, tzinfo=ct)
    assert end_ct == datetime(2026, 1, 16, 0, 0, tzinfo=ct)
