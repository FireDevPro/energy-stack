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


def test_cli_renders_and_writes_file(monkeypatch, tmp_path):
    """End-to-end: with mocked Loki + Influx + Telegram, render a report
    file to tmp_path. Assert file written + content has expected
    sections + Telegram called with all-green status."""
    from unittest.mock import MagicMock
    from tools.decision_trace_report.cli import main

    # Mock LokiClient + InfluxClient + TelegramClient construction
    fake_loki = MagicMock()
    fake_loki.fetch_decision_traces.return_value = []
    fake_influx = MagicMock()
    fake_influx.fetch_hvac_decisions.return_value = []
    fake_influx.fetch_precool_window.return_value = None
    fake_influx.fetch_hvac_actions.return_value = []
    fake_influx.fetch_comed_prices_above.return_value = []
    fake_influx.last_write_time.return_value = None
    fake_telegram = MagicMock()

    import tools.decision_trace_report.cli as cli_mod
    monkeypatch.setattr(cli_mod, "LokiClient", lambda *a, **kw: fake_loki)
    monkeypatch.setattr(cli_mod, "InfluxClient", lambda *a, **kw: fake_influx)
    monkeypatch.setattr(cli_mod, "TelegramClient", lambda *a, **kw: fake_telegram)

    # Provide minimal env
    monkeypatch.setenv("LOKI_URL", "http://loki.test")
    monkeypatch.setenv("INFLUXDB_URL", "http://influx.test")
    monkeypatch.setenv("INFLUXDB_TOKEN", "t")
    monkeypatch.setenv("INFLUXDB_ORG", "o")
    monkeypatch.setenv("INFLUXDB_BUCKET", "energy")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    output = tmp_path / "report.md"
    rc = main([
        "--date", "2026-05-15",
        "--output", str(output),
        "--no-telegram",
    ])
    assert rc == 0
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert "Decision-trace commissioning report" in content
    assert "2026-05-15" in content
    assert "§1" in content or "Night-before" in content


def test_cli_renders_custom_from_to_range(monkeypatch, tmp_path):
    """--from / --to renders an arbitrary CT-local range end-to-end:

    1. The CT range is correctly resolved to UTC bounds.
    2. Loki receives the resolved (start_utc, end_utc) on every
       fetch_decision_traces call.
    3. Influx range-mode helpers receive the resolved (start_ct, end_ct)
       datetime objects — NOT the report label string. This is the
       regression catch for `target = label` propagating into
       date-mode helpers.
    4. Date-mode Influx helpers (fetch_hvac_decisions,
       fetch_precool_window, fetch_hvac_actions(target),
       fetch_comed_prices_above(target)) MUST NOT be called in
       custom-range mode.
    5. Output filename + report content reflect the range label, not a
       single date.
    """
    from datetime import datetime
    from unittest.mock import MagicMock
    from zoneinfo import ZoneInfo
    from tools.decision_trace_report.cli import main

    captured_loki_ranges: list[tuple[str, str]] = []
    captured_influx_action_ranges: list[tuple] = []
    captured_influx_price_ranges: list[tuple] = []

    fake_loki = MagicMock()
    def record_range(event_name, start, end, limit=5000):
        captured_loki_ranges.append((start, end))
        return []
    fake_loki.fetch_decision_traces.side_effect = record_range
    fake_loki.count_reason_codes.return_value = {}

    fake_influx = MagicMock()
    def record_hvac_actions_range(*, start_ct, end_ct):
        captured_influx_action_ranges.append((start_ct, end_ct))
        return []
    def record_price_range(*, start_ct, end_ct, threshold_cents):
        captured_influx_price_ranges.append((start_ct, end_ct, threshold_cents))
        return []
    fake_influx.fetch_hvac_actions_by_range.side_effect = record_hvac_actions_range
    fake_influx.fetch_comed_prices_above_by_range.side_effect = record_price_range
    fake_influx.last_write_time.return_value = None
    # Date-mode helpers — they should NOT be called in custom-range
    # mode. Configure them to raise so an accidental call would crash
    # loudly instead of silently passing a label as a date.
    def _date_mode_must_not_be_called(*a, **kw):
        raise AssertionError(
            "date-mode Influx helper called in custom-range mode — "
            "the target=label bug is back"
        )
    fake_influx.fetch_hvac_decisions.side_effect = _date_mode_must_not_be_called
    fake_influx.fetch_precool_window.side_effect = _date_mode_must_not_be_called
    fake_influx.fetch_hvac_actions.side_effect = _date_mode_must_not_be_called
    fake_influx.fetch_comed_prices_above.side_effect = _date_mode_must_not_be_called

    import tools.decision_trace_report.cli as cli_mod
    monkeypatch.setattr(cli_mod, "LokiClient", lambda *a, **kw: fake_loki)
    monkeypatch.setattr(cli_mod, "InfluxClient", lambda *a, **kw: fake_influx)
    monkeypatch.setattr(cli_mod, "TelegramClient", lambda *a, **kw: MagicMock())

    monkeypatch.setenv("LOKI_URL", "http://loki.test")
    monkeypatch.setenv("INFLUXDB_URL", "http://influx.test")
    monkeypatch.setenv("INFLUXDB_TOKEN", "t")
    monkeypatch.setenv("INFLUXDB_ORG", "o")
    monkeypatch.setenv("INFLUXDB_BUCKET", "energy")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    output = tmp_path / "custom-range.md"
    rc = main([
        "--from", "2026-05-14T13:00",
        "--to", "2026-05-14T19:30",
        "--output", str(output),
        "--no-telegram",
    ])

    assert rc == 0
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    # (5) Report header / filename includes both endpoints
    assert "2026-05-14T13:00" in content
    assert "2026-05-14T19:30" in content

    # (2) Loki received the resolved UTC range, not the label.
    # CT 13:00 CDT = UTC 18:00; CT 19:30 CDT = UTC 00:30 next day.
    assert ("2026-05-14T18:00:00Z", "2026-05-15T00:30:00Z") in captured_loki_ranges

    # (3) Influx range-mode helpers received resolved CT DATETIMES (not
    # the label).
    ct = ZoneInfo("America/Chicago")
    expected_start = datetime(2026, 5, 14, 13, 0, tzinfo=ct)
    expected_end = datetime(2026, 5, 14, 19, 30, tzinfo=ct)
    assert (expected_start, expected_end) in captured_influx_action_ranges
    assert (expected_start, expected_end, 10.0) in captured_influx_price_ranges

    # (4) Date-mode helpers were never called (side_effect would have
    # raised AssertionError; reach here means no call happened).


def test_cli_date_mode_still_uses_date_helpers(monkeypatch, tmp_path):
    """Regression guard for date-mode: --date / default mode MUST call
    the date-mode Influx helpers (`fetch_hvac_decisions`,
    `fetch_precool_window`) AND the range-mode helpers (since §2 + §3
    use them in both modes), with the right target_date / range."""
    from datetime import datetime
    from unittest.mock import MagicMock
    from zoneinfo import ZoneInfo
    from tools.decision_trace_report.cli import main

    date_mode_calls: list[tuple[str, str]] = []  # (method_name, target_date)

    fake_loki = MagicMock()
    fake_loki.fetch_decision_traces.return_value = []
    fake_loki.count_reason_codes.return_value = {}

    fake_influx = MagicMock()
    fake_influx.fetch_hvac_decisions.side_effect = lambda d: (
        date_mode_calls.append(("fetch_hvac_decisions", d)) or []
    )
    fake_influx.fetch_precool_window.side_effect = lambda d: (
        date_mode_calls.append(("fetch_precool_window", d)) or None
    )
    fake_influx.fetch_hvac_actions_by_range.return_value = []
    fake_influx.fetch_comed_prices_above_by_range.return_value = []
    fake_influx.last_write_time.return_value = None

    import tools.decision_trace_report.cli as cli_mod
    monkeypatch.setattr(cli_mod, "LokiClient", lambda *a, **kw: fake_loki)
    monkeypatch.setattr(cli_mod, "InfluxClient", lambda *a, **kw: fake_influx)
    monkeypatch.setattr(cli_mod, "TelegramClient", lambda *a, **kw: MagicMock())
    for var in ("LOKI_URL", "INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG",
                 "INFLUXDB_BUCKET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(var, "x")

    output = tmp_path / "date-mode.md"
    rc = main([
        "--date", "2026-05-15",
        "--output", str(output),
        "--no-telegram",
    ])
    assert rc == 0
    # Date-mode helpers received the exact YYYY-MM-DD date — NOT a label.
    assert ("fetch_hvac_decisions", "2026-05-15") in date_mode_calls
    assert ("fetch_precool_window", "2026-05-15") in date_mode_calls
