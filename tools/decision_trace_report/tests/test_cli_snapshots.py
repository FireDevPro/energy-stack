"""End-to-end CLI snapshot tests per spec §9 line 312-323.

Two scenarios, each compared byte-for-byte against a small fixture:

  - happy_day.md   — NORMAL day, all-green, no anomalies (131 lines)
  - anomaly_day.md — trips every anomaly counter (132 lines)

Spec §9 line 325 prefers "under ~100 lines". Both fixtures sit at
131/132 because §5 coverage scorecard renders one row per reference
reason_code (33 codes across 5 enums); the ~30-line coverage table
dominates. Per-section split (spec fallback) was considered and
rejected: a single end-to-end fixture is more reviewable, locks the
full assembled output (which IS the spec §9 requirement), and the
overage is structural — not driven by scenario richness.

Determinism comes from `cli.main(..., now=frozen)`. The mocked Loki /
Influx / Telegram clients return curated synthetic data; the assembled
markdown is asserted equal to the checked-in fixture.

If a renderer / section behavior change is intended, update the
fixture in the same commit so review can see the diff. If a fixture
diff is unintended, the test fails — that's the point.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from tools.decision_trace_report.cli import main

CT = ZoneInfo("America/Chicago")
FIXTURES = Path(__file__).parent / "fixtures"


def _common_env(monkeypatch):
    for var in ("LOKI_URL", "INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG",
                 "INFLUXDB_BUCKET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(var, "x")


def _wire_clients(monkeypatch, fake_loki, fake_influx):
    import tools.decision_trace_report.cli as cli_mod
    monkeypatch.setattr(cli_mod, "LokiClient", lambda *a, **kw: fake_loki)
    monkeypatch.setattr(cli_mod, "InfluxClient", lambda *a, **kw: fake_influx)
    monkeypatch.setattr(cli_mod, "TelegramClient", lambda *a, **kw: MagicMock())


# Frozen run-time for both scenarios. Renders on the day AFTER the
# target_date (mirrors the daily Task Scheduler cadence).
FROZEN_NOW = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)


def _make_loki(event_map):
    """Build a fake LokiClient whose `fetch_decision_traces` returns
    rows keyed by event_name. `count_reason_codes` returns {} by default."""
    fake = MagicMock()
    def fetch(event_name, start, end, limit=5000):
        return list(event_map.get(event_name, []))
    fake.fetch_decision_traces.side_effect = fetch
    fake.count_reason_codes.return_value = {}
    return fake


def _assert_matches_fixture(actual_path: Path, fixture_name: str) -> None:
    """Byte-compare written output to fixture, normalizing line endings.

    `Path.write_text` emits CRLF on Windows and LF on POSIX, and Git's
    autocrlf=input setting normalizes the checked-in fixture to LF in
    the working tree on every platform. Comparing raw bytes would
    falsely fail on at least one platform. Normalize both sides to LF
    before the equality check."""
    actual = actual_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    expected = (FIXTURES / fixture_name).read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert actual == expected, (
        f"{fixture_name} drifted — review the diff. If the change is "
        "intentional, regenerate the fixture in the same commit."
    )


def test_cli_happy_day_matches_snapshot(monkeypatch, tmp_path):
    """Happy day: NORMAL day-type, precool rejected, one approved tick,
    no spikes, all feeds fresh, no unexpected reason codes."""
    _common_env(monkeypatch)

    fake_loki = _make_loki({
        "day_type_decision": [{
            "msg": "decision_trace.day_type_decision",
            "ts": "2026-05-14T21:00:00-05:00",
            "decision_for_date": "2026-05-15",
            "winning_day_type": "NORMAL",
            "winning_reason": "high_75_to_84",
            "evaluation_tape": [
                {"rule": "high_ge_hot", "threshold": 85, "actual": 80,
                 "fired": False, "reason_code": "DAY_TYPE_HOT_HIGH_GE_85"},
                {"rule": "high_ge_normal", "threshold": 75, "actual": 80,
                 "fired": True, "reason_code": "DAY_TYPE_NORMAL_HIGH_75_TO_84"},
            ],
            "high_f": 80.0,
            "apparent_max_f": 82.0,
        }],
        "precool_decision": [{
            "msg": "decision_trace.precool_decision",
            "decision_for_date": "2026-05-15",
            "selected": False,
            "reason_code": "PRECOOL_REJECTED_NO_CHEAP_WINDOW",
        }],
        "layer_resolution": [{
            "msg": "decision_trace.layer_resolution",
            "ts": "2026-05-15T13:00:01-05:00",
            "tick_id": "tick_aaaa1234567890",
            "winning_layer": "schedule",
            "schedule_cool_f": 79,
            "price_cool_f": 79,
            "fivecp_cool_f": 79,
            "effective_cool_f": 79,
        }],
        "supervisor": [{
            "msg": "decision_trace.supervisor",
            "ts": "2026-05-15T13:00:01-05:00",
            "tick_id": "tick_aaaa1234567890",
            "decision": "approved",
            "reason_code": "SUPERVISOR_APPROVED",
        }],
        "price_overlay_eval": [],
    })
    # Coverage counts: a couple of codes observed live, rest not-observed.
    fake_loki.count_reason_codes.return_value = {
        "DAY_TYPE_NORMAL_HIGH_75_TO_84": 5,
        "PRECOOL_REJECTED_NO_CHEAP_WINDOW": 5,
        "LAYER_RESOLUTION_SCHEDULE_WINS": 200,
        "SUPERVISOR_APPROVED": 250,
        "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER": 50,
    }

    fake_influx = MagicMock()
    fake_influx.fetch_hvac_decisions.return_value = [
        {"decision_for_date": "2026-05-15", "day_type": "NORMAL"},
    ]
    fake_influx.fetch_precool_window.return_value = None
    fake_influx.fetch_hvac_actions_by_range.return_value = []
    fake_influx.fetch_comed_prices_above_by_range.return_value = []
    # All continuous feeds fresh (recent last_write); event feeds caught up.
    # Event-feed freshness requires last_write >= expected_fire — match
    # the expected-fire UTC exactly so the report shows "caught up".
    fresh = FROZEN_NOW - timedelta(minutes=1)
    da_lmp_fresh = datetime(2026, 5, 15, 22, 0, tzinfo=timezone.utc)
    metered_fresh = datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)
    feed_fresh = {
        "comed.prices": fresh,
        "nws.forecast": fresh,
        "refoss.channel": fresh,
        "hvac.thermostat": fresh,
        "haven.indoor": fresh,
        "pjm.inst_load": fresh,
        "pjm.lmp_da_hourly": da_lmp_fresh,
        "pjm.metered_load": metered_fresh,
    }
    fake_influx.last_write_time.side_effect = lambda m: feed_fresh.get(m)

    _wire_clients(monkeypatch, fake_loki, fake_influx)

    output = tmp_path / "happy.md"
    rc = main(
        ["--date", "2026-05-15", "--output", str(output), "--no-telegram"],
        now=FROZEN_NOW,
    )
    assert rc == 0

    _assert_matches_fixture(output, "happy_day.md")


def test_cli_anomaly_day_matches_snapshot(monkeypatch, tmp_path):
    """Anomaly day: trips every counter — silent-skip §1 discrepancy,
    supervisor non-approved §2, unexplained spike §3, stale feed §4,
    unexpected reason code §5."""
    _common_env(monkeypatch)

    fake_loki = _make_loki({
        # day_type_decision MISSING — but hvac.decisions row is present,
        # which is the silent-skip anomaly per §1 count_discrepancies.
        "day_type_decision": [],
        "precool_decision": [{
            "msg": "decision_trace.precool_decision",
            "decision_for_date": "2026-05-15",
            "selected": False,
            "reason_code": "PRECOOL_REJECTED_NO_CHEAP_WINDOW",
        }],
        "layer_resolution": [{
            "msg": "decision_trace.layer_resolution",
            "ts": "2026-05-15T14:00:00-05:00",
            "tick_id": "tick_bbbb9999",
            "winning_layer": "price_overlay",
            "schedule_cool_f": 79,
            "price_cool_f": 75,
            "fivecp_cool_f": 79,
            "effective_cool_f": 75,
        }],
        "supervisor": [{
            "msg": "decision_trace.supervisor",
            "ts": "2026-05-15T14:00:00-05:00",
            "tick_id": "tick_bbbb9999",
            "decision": "clamped",
            "reason_code": "SUPERVISOR_CLAMPED_COOL_FLOOR",
        }],
        "price_overlay_eval": [{
            "ts": "2026-05-15T14:00:00-05:00",
            "outcome": "held",
            "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
            "prev_tier": "normal",
            "new_tier": "normal",
        }],
    })
    # Coverage: introduce an unexpected code not in any enum.
    fake_loki.count_reason_codes.return_value = {
        "PRECOOL_REJECTED_NO_CHEAP_WINDOW": 3,
        "LAYER_RESOLUTION_PRICE_OVERLAY_WINS": 1,
        "SUPERVISOR_CLAMPED_COOL_FLOOR": 1,
        "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER": 10,
        "MYSTERY_UNDOCUMENTED_CODE": 2,  # §5 unexpected
    }

    fake_influx = MagicMock()
    fake_influx.fetch_hvac_decisions.return_value = [
        {"decision_for_date": "2026-05-15", "day_type": "NORMAL"},
    ]
    fake_influx.fetch_precool_window.return_value = None
    fake_influx.fetch_hvac_actions_by_range.return_value = []
    # One spike at 14:00 CT, nearest trace is normal-tier-held -> unexplained.
    fake_influx.fetch_comed_prices_above_by_range.return_value = [
        {"_time": datetime(2026, 5, 15, 14, 0, tzinfo=CT), "price_cents": 14.25},
    ]
    fresh = FROZEN_NOW - timedelta(minutes=1)
    da_lmp_fresh = datetime(2026, 5, 15, 22, 0, tzinfo=timezone.utc)
    metered_fresh = datetime(2026, 5, 10, 7, 0, tzinfo=timezone.utc)
    stale_haven = FROZEN_NOW - timedelta(hours=3)  # over 30m stale threshold
    feeds = {
        "comed.prices": fresh,
        "nws.forecast": fresh,
        "refoss.channel": fresh,
        "hvac.thermostat": fresh,
        "haven.indoor": stale_haven,
        "pjm.inst_load": fresh,
        "pjm.lmp_da_hourly": da_lmp_fresh,
        "pjm.metered_load": metered_fresh,
    }
    fake_influx.last_write_time.side_effect = lambda m: feeds.get(m)

    _wire_clients(monkeypatch, fake_loki, fake_influx)

    output = tmp_path / "anomaly.md"
    rc = main(
        ["--date", "2026-05-15", "--output", str(output), "--no-telegram"],
        now=FROZEN_NOW,
    )
    assert rc == 0

    _assert_matches_fixture(output, "anomaly_day.md")
