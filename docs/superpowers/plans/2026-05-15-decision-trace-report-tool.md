# Decision-Trace Commissioning Report Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pre-OSF commissioning report tool spec'd in [`docs/superpowers/specs/2026-05-15-decision-trace-report-tool-design.md`](../specs/2026-05-15-decision-trace-report-tool-design.md). Renders a daily markdown report from `decision_trace.*` Loki lines + `hvac.*` InfluxDB measurements; runs on the Windows workstation; surfaces commissioning anomalies before June 1.

**Architecture:** Standalone Python package under `tools/decision_trace_report/`. Pure-function section modules, thin HTTP client wrappers for Loki and InfluxDB and Telegram. CLI entry via `python -m tools.decision_trace_report`. No Pi-side service. All tests mock HTTP — no live LAN calls.

**Tech stack:** Python 3.11+, `requests` (Loki + Telegram HTTP), `influxdb-client` (InfluxDB Flux), `pytest` (test runner), standard library only otherwise (argparse, datetime, zoneinfo, json, pathlib).

---

## File structure

```
tools/decision_trace_report/
├── __init__.py
├── cli.py                              # argparse entry, dispatch to renderer
├── loki_client.py                      # LokiClient: query_range + JSON-field filter
├── influx_client.py                    # InfluxClient: hvac.* queries + feed health
├── telegram_client.py                  # TelegramClient: send_message via Bot API
├── renderer.py                         # build_report(sections, anomalies) -> markdown
├── decision_codes_loader.py            # imports + reflects DayTypeCode et al.
├── sections/
│   ├── __init__.py
│   ├── night_before.py
│   ├── day_of.py
│   ├── price_spikes.py
│   ├── feed_health.py
│   └── coverage_scorecard.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py                     # shared fixtures
│   ├── fixtures/
│   │   ├── happy_day.md                # snapshot fixture
│   │   └── anomaly_day.md              # snapshot fixture
│   ├── test_loki_client.py
│   ├── test_influx_client.py
│   ├── test_telegram_client.py
│   ├── test_renderer.py
│   ├── test_sections_night_before.py
│   ├── test_sections_day_of.py
│   ├── test_sections_price_spikes.py
│   ├── test_sections_feed_health.py
│   ├── test_sections_coverage_scorecard.py
│   └── test_cli.py
├── .env.example
├── README.md
└── requirements.txt
```

Plus:
- `docs/test-reports/.gitkeep` + `docs/test-reports/README.md` — tracked directory, contents gitignored.
- `.gitignore` updates — already covers `.env`/`.env.*`; verify path-scoped exclusion for `docs/test-reports/*.md`.

---

## Phase 0 — Scaffolding

### Task 0.1: Create tool package skeleton

**Files:**
- Create: `tools/decision_trace_report/__init__.py`
- Create: `tools/decision_trace_report/requirements.txt`
- Create: `tools/decision_trace_report/.env.example`

- [ ] **Step 1: Write skeleton files**

```python
# tools/decision_trace_report/__init__.py
"""Decision-trace commissioning report tool.

See: docs/superpowers/specs/2026-05-15-decision-trace-report-tool-design.md
"""
```

```
# tools/decision_trace_report/requirements.txt
requests>=2.31
influxdb-client>=1.40
pytest>=8.0
```

```
# tools/decision_trace_report/.env.example
LOKI_URL=http://192.168.20.10:3100
INFLUXDB_URL=http://192.168.20.10:8086
INFLUXDB_TOKEN=<copy from Pi ~/energy-stack/.env>
INFLUXDB_ORG=<copy from Pi ~/energy-stack/.env>
INFLUXDB_BUCKET=energy
TELEGRAM_BOT_TOKEN=<copy from Pi ~/energy-stack/.env>
TELEGRAM_CHAT_ID=<copy from Pi ~/energy-stack/.env>
```

- [ ] **Step 2: Verify importable**

Run: `python -c "import tools.decision_trace_report"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add tools/decision_trace_report/__init__.py tools/decision_trace_report/requirements.txt tools/decision_trace_report/.env.example
git commit -m "feat(report-tool): scaffold decision_trace_report package"
```

### Task 0.2: Set up test reports directory

**Files:**
- Create: `docs/test-reports/.gitkeep`
- Create: `docs/test-reports/README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write README + gitkeep**

```
# docs/test-reports/.gitkeep
(empty file)
```

```markdown
<!-- docs/test-reports/README.md -->
# Decision-trace commissioning reports

This directory holds the daily markdown reports produced by
`tools/decision_trace_report`. Contents are **gitignored** — these are
transient commissioning artifacts, not permanent docs.

Tracked: this README + `.gitkeep`. Everything else under here ignored.

See `docs/superpowers/specs/2026-05-15-decision-trace-report-tool-design.md`
for what the reports contain and how to render them.
```

- [ ] **Step 2: Update .gitignore**

Append to `.gitignore`:

```
# Decision-trace commissioning reports (transient artifacts)
docs/test-reports/*.md
!docs/test-reports/README.md
```

- [ ] **Step 3: Verify gitignore behavior**

Create a throwaway file + check git status:
```bash
echo "test" > docs/test-reports/sample.md
git status --porcelain docs/test-reports/
```
Expected output: nothing (sample.md is ignored, README is tracked).

Remove the test file:
```bash
rm docs/test-reports/sample.md
```

- [ ] **Step 4: Commit**

```bash
git add docs/test-reports/.gitkeep docs/test-reports/README.md .gitignore
git commit -m "feat(report-tool): set up docs/test-reports/ directory + gitignore"
```

### Task 0.3: Create README for the tool

**Files:**
- Create: `tools/decision_trace_report/README.md`

- [ ] **Step 1: Write operator-facing README**

```markdown
<!-- tools/decision_trace_report/README.md -->
# Decision-trace commissioning report tool

Daily + on-demand markdown report rendering `decision_trace.*` events
from Loki and `hvac.*` measurements from InfluxDB. Runs on the Windows
workstation, queries Pi-lab over the LAN.

See full design spec: `docs/superpowers/specs/2026-05-15-decision-trace-report-tool-design.md`

## Quick start

1. Install dependencies (one-time):
   ```
   pip install -r tools/decision_trace_report/requirements.txt
   ```

2. Set required environment variables. Either via shell env, your
   Windows Task Scheduler entry, or `--env-file PATH`. See
   `.env.example` in this directory for the full list.

3. Render yesterday's CT day (default):
   ```
   python -m tools.decision_trace_report
   ```

4. Render a specific past day:
   ```
   python -m tools.decision_trace_report --date 2026-05-14
   ```

5. Suppress Telegram heartbeat (e.g., ad-hoc reruns):
   ```
   python -m tools.decision_trace_report --date 2026-05-14 --no-telegram
   ```

## Output

By default writes to:
`D:\Projects\energy-proxy\docs\test-reports\YYYY-MM-DD-decision-trace.md`

That directory is gitignored — reports are transient artifacts.

## Daily automation

Schedule a Windows Task Scheduler entry to run the tool at 08:00 CT
every day. Trigger: 08:00 daily. Action: `python -m tools.decision_trace_report`
with working directory `D:\Projects\energy-proxy`.

A run on 2026-05-16 at 08:00 CT renders `2026-05-15-decision-trace.md`.

## Tests

```
python -m pytest tools/decision_trace_report/tests/
```

No live HTTP. All Loki + InfluxDB + Telegram calls are mocked.
```

- [ ] **Step 2: Commit**

```bash
git add tools/decision_trace_report/README.md
git commit -m "docs(report-tool): operator README"
```

### Task 0.4: Set up tests package + conftest

**Files:**
- Create: `tools/decision_trace_report/tests/__init__.py`
- Create: `tools/decision_trace_report/tests/conftest.py`

- [ ] **Step 1: Write tests/__init__.py**

```python
# tools/decision_trace_report/tests/__init__.py
```

- [ ] **Step 2: Write conftest with shared fixtures**

```python
# tools/decision_trace_report/tests/conftest.py
"""Shared fixtures for decision_trace_report tests."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

CT = ZoneInfo("America/Chicago")


@pytest.fixture
def target_date_iso() -> str:
    """Default target date for tests (CT calendar day)."""
    return "2026-05-15"


@pytest.fixture
def now_ct() -> datetime:
    """Frozen 'now' for deterministic feed-health tests."""
    return datetime(2026, 5, 16, 8, 0, tzinfo=CT)


@pytest.fixture
def loki_url() -> str:
    return "http://loki.test:3100"


@pytest.fixture
def influx_url() -> str:
    return "http://influx.test:8086"


def make_loki_stream(values: list[tuple[str, str]]) -> dict:
    """Build a Loki query_range response stream wrapper.

    `values` is a list of (epoch_ns_str, log_line) tuples — matches
    Loki's wire format.
    """
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {"stream": {"container": "hvac-scheduler"}, "values": list(values)},
            ],
        },
    }


def make_empty_loki_response() -> dict:
    return {
        "status": "success",
        "data": {"resultType": "streams", "result": []},
    }
```

- [ ] **Step 3: Verify pytest discovers the package**

```
python -m pytest tools/decision_trace_report/tests/ -v
```
Expected: `collected 0 items`, exit 0.

- [ ] **Step 4: Commit**

```bash
git add tools/decision_trace_report/tests/__init__.py tools/decision_trace_report/tests/conftest.py
git commit -m "test(report-tool): scaffold tests package + shared fixtures"
```

---

## Phase 1 — Loki client

### Task 1.1: LokiClient.query_range — basic URL + params

**Files:**
- Create: `tools/decision_trace_report/loki_client.py`
- Create: `tools/decision_trace_report/tests/test_loki_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tools/decision_trace_report/tests/test_loki_client.py
"""Tests for LokiClient — pure HTTP wrapper, no live calls."""
from unittest.mock import MagicMock

import pytest

from tools.decision_trace_report.loki_client import LokiClient


def test_query_range_builds_correct_url_and_params(monkeypatch, loki_url):
    """query_range hits /loki/api/v1/query_range with query, start, end, limit."""
    captured = {}

    def fake_get(url, params=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["params"] = params
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)

    client = LokiClient(loki_url)
    client.query_range(
        '{container="hvac-scheduler"}',
        "2026-05-15T00:00:00Z",
        "2026-05-15T23:59:59Z",
        limit=500,
    )

    assert captured["url"] == f"{loki_url}/loki/api/v1/query_range"
    assert captured["params"]["query"] == '{container="hvac-scheduler"}'
    assert captured["params"]["start"] == "2026-05-15T00:00:00Z"
    assert captured["params"]["end"] == "2026-05-15T23:59:59Z"
    assert captured["params"]["limit"] == 500
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tools/decision_trace_report/tests/test_loki_client.py::test_query_range_builds_correct_url_and_params -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.decision_trace_report.loki_client'`.

- [ ] **Step 3: Write minimal implementation**

```python
# tools/decision_trace_report/loki_client.py
"""Loki HTTP API wrapper for decision-trace report queries.

No live Pi/LAN calls in tests; mocks handle the requests layer.
"""
from typing import Any

import requests


class LokiClient:
    """Thin wrapper around Loki's `/loki/api/v1/query_range` endpoint."""

    def __init__(self, base_url: str, timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def query_range(
        self,
        query: str,
        start: str,
        end: str,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Run a LogQL query over a time range. Returns the parsed JSON response."""
        response = requests.get(
            f"{self.base_url}/loki/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "limit": limit},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 4: Run to verify it passes**

```
python -m pytest tools/decision_trace_report/tests/test_loki_client.py::test_query_range_builds_correct_url_and_params -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/loki_client.py tools/decision_trace_report/tests/test_loki_client.py
git commit -m "feat(report-tool): LokiClient.query_range basic wrapper"
```

### Task 1.2: LokiClient — JSON-field filter helper

**Files:**
- Modify: `tools/decision_trace_report/loki_client.py`
- Modify: `tools/decision_trace_report/tests/test_loki_client.py`

- [ ] **Step 1: Write failing test for parse_trace_lines**

Append to `test_loki_client.py`:

```python
def test_parse_trace_lines_extracts_json_from_loki_response(loki_url):
    """parse_trace_lines pulls each log line's JSON payload out of Loki's
    streams/values wire format."""
    raw_response = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"container": "hvac-scheduler"},
                    "values": [
                        ("1779328800000000000",
                         '{"msg": "decision_trace.day_type_decision", '
                         '"decision_for_date": "2026-05-15", "winning_day_type": "NORMAL"}'),
                        ("1779328900000000000",
                         '{"msg": "decision_trace.day_type_decision", '
                         '"decision_for_date": "2026-05-15", "winning_day_type": "NORMAL"}'),
                    ],
                },
            ],
        },
    }

    client = LokiClient(loki_url)
    parsed = client.parse_trace_lines(raw_response)

    assert len(parsed) == 2
    assert parsed[0]["msg"] == "decision_trace.day_type_decision"
    assert parsed[0]["decision_for_date"] == "2026-05-15"
    assert parsed[0]["_loki_ts_ns"] == 1779328800000000000


def test_parse_trace_lines_skips_non_json(loki_url):
    """Malformed JSON lines are silently skipped — we don't want one bad
    line to crash the entire report rendering."""
    raw_response = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {},
                    "values": [
                        ("1779328800000000000", '{"msg": "ok"}'),
                        ("1779328900000000000", "not json at all"),
                        ("1779329000000000000", '{"msg": "also ok"}'),
                    ],
                },
            ],
        },
    }
    client = LokiClient(loki_url)
    parsed = client.parse_trace_lines(raw_response)
    assert len(parsed) == 2
    assert parsed[0]["msg"] == "ok"
    assert parsed[1]["msg"] == "also ok"
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tools/decision_trace_report/tests/test_loki_client.py::test_parse_trace_lines_extracts_json_from_loki_response tools/decision_trace_report/tests/test_loki_client.py::test_parse_trace_lines_skips_non_json -v
```
Expected: FAIL with `AttributeError: 'LokiClient' object has no attribute 'parse_trace_lines'`.

- [ ] **Step 3: Implement parse_trace_lines**

Append to `loki_client.py`:

```python
    @staticmethod
    def parse_trace_lines(response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract JSON-payload dicts from a Loki query_range response.

        Each Loki value is a (ns_timestamp, log_line) tuple. We parse the
        log_line as JSON and inject `_loki_ts_ns` so downstream sorters
        can use either the parsed event's own `ts` or Loki's ingest time.
        Malformed JSON is silently skipped — one bad line must not crash
        the report.
        """
        import json
        out: list[dict[str, Any]] = []
        for stream in response.get("data", {}).get("result", []):
            for ts_ns, line in stream.get("values", []):
                try:
                    parsed = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                parsed["_loki_ts_ns"] = int(ts_ns)
                out.append(parsed)
        return out
```

- [ ] **Step 4: Run to verify both new tests pass**

```
python -m pytest tools/decision_trace_report/tests/test_loki_client.py -v
```
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/loki_client.py tools/decision_trace_report/tests/test_loki_client.py
git commit -m "feat(report-tool): LokiClient.parse_trace_lines extracts JSON payloads"
```

### Task 1.3: LokiClient — fetch_decision_traces convenience method

**Files:**
- Modify: `tools/decision_trace_report/loki_client.py`
- Modify: `tools/decision_trace_report/tests/test_loki_client.py`

- [ ] **Step 1: Write failing test**

Append to `test_loki_client.py`:

```python
def test_fetch_decision_traces_filters_by_event_name(monkeypatch, loki_url):
    """fetch_decision_traces queries with the right LogQL filter for an
    event_name and time range, then parses the response."""
    captured = {}

    def fake_get(url, params=None, timeout=None, **kwargs):
        captured["params"] = params
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {},
                        "values": [
                            ("1779328800000000000",
                             '{"msg": "decision_trace.day_type_decision", '
                             '"decision_for_date": "2026-05-15"}'),
                        ],
                    },
                ],
            },
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)

    client = LokiClient(loki_url)
    events = client.fetch_decision_traces(
        event_name="day_type_decision",
        start="2026-05-15T00:00:00Z",
        end="2026-05-15T23:59:59Z",
    )

    assert 'decision_trace.day_type_decision' in captured["params"]["query"]
    assert '{container="hvac-scheduler"}' in captured["params"]["query"]
    assert len(events) == 1
    assert events[0]["decision_for_date"] == "2026-05-15"
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tools/decision_trace_report/tests/test_loki_client.py::test_fetch_decision_traces_filters_by_event_name -v
```
Expected: FAIL — `fetch_decision_traces` doesn't exist.

- [ ] **Step 3: Implement fetch_decision_traces**

Append to `loki_client.py`:

```python
    def fetch_decision_traces(
        self,
        event_name: str,
        start: str,
        end: str,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Pull decision_trace.<event_name> log lines from Loki over a
        time range. Returns the JSON-parsed events sorted by `ts`.

        `event_name` is the suffix after `decision_trace.` (e.g.,
        `"day_type_decision"`, `"price_overlay_eval"`).
        """
        full_event = f"decision_trace.{event_name}"
        query = f'{{container="hvac-scheduler"}} |= "{full_event}"'
        raw = self.query_range(query, start, end, limit=limit)
        events = self.parse_trace_lines(raw)
        # Sort by trace's own `ts` if present, else Loki ingest time.
        events.sort(key=lambda e: e.get("ts", "") or e["_loki_ts_ns"])
        return events
```

- [ ] **Step 4: Run to verify it passes**

```
python -m pytest tools/decision_trace_report/tests/test_loki_client.py -v
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/loki_client.py tools/decision_trace_report/tests/test_loki_client.py
git commit -m "feat(report-tool): LokiClient.fetch_decision_traces convenience method"
```

### Task 1.4: LokiClient.count_reason_codes — for §5 coverage scorecard

**Files:**
- Modify: `tools/decision_trace_report/loki_client.py`
- Modify: `tools/decision_trace_report/tests/test_loki_client.py`

`§5 Coverage scorecard` needs the live count of every `reason_code` value seen in `decision_trace.*` events over (a) cumulative since-trace-started and (b) the last 7 days. LogQL's JSON parsing for grouped aggregation is awkward; doing it client-side after `parse_trace_lines` is straightforward.

- [ ] **Step 1: Write failing test**

Append to `test_loki_client.py`:

```python
def test_count_reason_codes_aggregates_across_events(monkeypatch, loki_url):
    """count_reason_codes queries all decision_trace.* events in a time
    range and returns a dict mapping reason_code -> count."""
    captured = {}

    def fake_get(url, params=None, timeout=None, **kwargs):
        captured["params"] = params
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {},
                        "values": [
                            ("1779328800000000000",
                             '{"msg": "decision_trace.price_overlay_eval", '
                             '"reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"}'),
                            ("1779328860000000000",
                             '{"msg": "decision_trace.price_overlay_eval", '
                             '"reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"}'),
                            ("1779328920000000000",
                             '{"msg": "decision_trace.supervisor", '
                             '"reason_code": "SUPERVISOR_APPROVED"}'),
                        ],
                    },
                ],
            },
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)
    client = LokiClient(loki_url)
    counts = client.count_reason_codes(
        start="2026-05-08T00:00:00Z",
        end="2026-05-15T00:00:00Z",
    )

    # Query matches `decision_trace.` events broadly
    assert "decision_trace" in captured["params"]["query"]
    assert counts["PRICE_OVERLAY_NORMAL_BELOW_TRIGGER"] == 2
    assert counts["SUPERVISOR_APPROVED"] == 1


def test_count_reason_codes_ignores_events_without_reason_code(monkeypatch, loki_url):
    """Some trace lines may not carry reason_code (defensive); skip them
    rather than counting blank as a 'code'."""

    def fake_get(url, params=None, timeout=None, **kwargs):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {},
                        "values": [
                            ("1779328800000000000",
                             '{"msg": "decision_trace.startup"}'),  # no reason_code
                            ("1779328860000000000",
                             '{"msg": "decision_trace.supervisor", '
                             '"reason_code": "SUPERVISOR_APPROVED"}'),
                        ],
                    },
                ],
            },
        }
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.get", fake_get)
    client = LokiClient(loki_url)
    counts = client.count_reason_codes(
        start="2026-05-08T00:00:00Z",
        end="2026-05-15T00:00:00Z",
    )
    assert counts == {"SUPERVISOR_APPROVED": 1}
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_loki_client.py::test_count_reason_codes_aggregates_across_events tools/decision_trace_report/tests/test_loki_client.py::test_count_reason_codes_ignores_events_without_reason_code -v
```
Expected: 2 FAIL on missing method.

- [ ] **Step 3: Implement**

Append to `loki_client.py`:

```python
    def count_reason_codes(
        self,
        start: str,
        end: str,
        limit: int = 50000,
    ) -> dict[str, int]:
        """Count occurrences of each `reason_code` value across
        `decision_trace.*` events in `[start, end]`.

        Returns `{reason_code: count}`. Events without a `reason_code`
        field are ignored (some decision_trace.* events may not carry
        one — defensive). Used by §5 coverage scorecard.

        `limit` defaults to 50k because verbose commissioning emits
        ~3500 lines/day and a 30-day cumulative window is ~100k. Loki
        capped at its own server limit if 50k is exceeded.
        """
        query = '{container="hvac-scheduler"} |= "decision_trace"'
        raw = self.query_range(query, start, end, limit=limit)
        events = self.parse_trace_lines(raw)
        counts: dict[str, int] = {}
        for event in events:
            code = event.get("reason_code")
            if not isinstance(code, str):
                continue
            counts[code] = counts.get(code, 0) + 1
        return counts
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_loki_client.py -v
```
Expected: 6 PASSED (4 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/loki_client.py tools/decision_trace_report/tests/test_loki_client.py
git commit -m "feat(report-tool): LokiClient.count_reason_codes for §5 coverage"
```

---

## Phase 2 — InfluxDB client

### Task 2.1: InfluxClient — scaffold + hvac.decisions query

**Files:**
- Create: `tools/decision_trace_report/influx_client.py`
- Create: `tools/decision_trace_report/tests/test_influx_client.py`

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_influx_client.py
"""Tests for InfluxClient — pure wrapper, no live calls."""
from unittest.mock import MagicMock

import pytest

from tools.decision_trace_report.influx_client import InfluxClient


def test_fetch_hvac_decisions_builds_flux_with_target_date(influx_url):
    """fetch_hvac_decisions filters by decision_for_date tag."""
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []  # no rows

    client = InfluxClient(
        url=influx_url, token="t", org="o", bucket="energy",
        query_api=fake_query_api,
    )
    client.fetch_hvac_decisions("2026-05-15")

    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "hvac.decisions"' in flux
    assert 'r.decision_for_date == "2026-05-15"' in flux


def test_fetch_hvac_decisions_pivots_fields_into_rows(influx_url):
    """The function returns one dict per decision row, with all fields
    flattened (high_f, day_type, reason, etc.)."""
    fake_record = MagicMock()
    fake_record.values = {
        "decision_for_date": "2026-05-15",
        "day_type": "NORMAL",
        "high_f": 80.0,
        "reason": "high_75_to_84",
    }
    fake_record.get_time.return_value = "2026-05-15T02:00:00Z"
    fake_table = MagicMock()
    fake_table.records = [fake_record]
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = [fake_table]

    client = InfluxClient(
        url=influx_url, token="t", org="o", bucket="energy",
        query_api=fake_query_api,
    )
    rows = client.fetch_hvac_decisions("2026-05-15")

    assert len(rows) == 1
    assert rows[0]["day_type"] == "NORMAL"
    assert rows[0]["high_f"] == 80.0
    assert rows[0]["decision_for_date"] == "2026-05-15"
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tools/decision_trace_report/tests/test_influx_client.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

```python
# tools/decision_trace_report/influx_client.py
"""InfluxDB Flux wrapper for decision-trace report queries.

Construct via from_env() in production. Tests inject query_api directly.
No live Pi/LAN calls in tests.
"""
from typing import Any


class InfluxClient:
    """Wrapper around influxdb-client's query_api for hvac.* + feed health."""

    def __init__(self, url: str, token: str, org: str, bucket: str, query_api=None):
        self.url = url
        self.token = token
        self.org = org
        self.bucket = bucket
        if query_api is None:
            # Production path — defer the import so tests don't require
            # the influxdb-client package on the test path.
            from influxdb_client import InfluxDBClient
            self._client = InfluxDBClient(url=url, token=token, org=org)
            self._query_api = self._client.query_api()
        else:
            self._client = None
            self._query_api = query_api

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "InfluxClient":
        return cls(
            url=env["INFLUXDB_URL"],
            token=env["INFLUXDB_TOKEN"],
            org=env["INFLUXDB_ORG"],
            bucket=env.get("INFLUXDB_BUCKET", "energy"),
        )

    def fetch_hvac_decisions(self, target_date_iso: str) -> list[dict[str, Any]]:
        """All hvac.decisions rows for `target_date_iso`."""
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: -7d)
              |> filter(fn: (r) => r._measurement == "hvac.decisions"
                                    and r.decision_for_date == "{target_date_iso}")
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        """
        return self._flatten_query(flux)

    def _flatten_query(self, flux: str) -> list[dict[str, Any]]:
        """Run a Flux query and flatten each record into a single dict."""
        out: list[dict[str, Any]] = []
        for table in self._query_api.query(flux):
            for record in table.records:
                row = dict(record.values)
                row["_time"] = record.get_time()
                out.append(row)
        return out
```

- [ ] **Step 4: Run to verify both pass**

```
python -m pytest tools/decision_trace_report/tests/test_influx_client.py -v
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/influx_client.py tools/decision_trace_report/tests/test_influx_client.py
git commit -m "feat(report-tool): InfluxClient scaffold + fetch_hvac_decisions"
```

### Task 2.2: InfluxClient — fetch_precool_window + fetch_hvac_actions + fetch_comed_prices

**Files:**
- Modify: `tools/decision_trace_report/influx_client.py`
- Modify: `tools/decision_trace_report/tests/test_influx_client.py`

- [ ] **Step 1: Write failing tests**

Append to `test_influx_client.py`:

```python
def test_fetch_precool_window_filters_by_target_date(influx_url):
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    client.fetch_precool_window("2026-05-15")
    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "hvac.precool_window"' in flux
    assert 'r.target_date == "2026-05-15"' in flux


def test_fetch_hvac_actions_for_ct_day_cdt(influx_url):
    """Summer (CDT, UTC-5): CT day 2026-05-15 → UTC [05:00 May 15, 05:00 May 16)."""
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    client.fetch_hvac_actions("2026-05-15")
    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "hvac.actions"' in flux
    assert "2026-05-15T05:00:00Z" in flux
    assert "2026-05-16T05:00:00Z" in flux


def test_fetch_hvac_actions_for_ct_day_cst(influx_url):
    """Winter (CST, UTC-6): CT day 2026-01-15 → UTC [06:00 Jan 15, 06:00 Jan 16).
    Proves the tz arithmetic isn't hardcoded to CDT — DST hygiene check."""
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    client.fetch_hvac_actions("2026-01-15")
    flux = fake_query_api.query.call_args[0][0]
    assert "2026-01-15T06:00:00Z" in flux
    assert "2026-01-16T06:00:00Z" in flux


def test_fetch_comed_prices_spikes_only(influx_url):
    """fetch_comed_prices_above filters by threshold; used for §3
    price-spike audit."""
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    client.fetch_comed_prices_above("2026-05-15", threshold_cents=10.0)
    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "comed.prices"' in flux
    assert "r._value >= 10.0" in flux
```

- [ ] **Step 2: Run to verify they fail**

```
python -m pytest tools/decision_trace_report/tests/test_influx_client.py -v
```
Expected: 3 new FAIL on missing methods.

- [ ] **Step 3: Implement helpers**

Append to `influx_client.py`:

```python
    def fetch_precool_window(self, target_date_iso: str) -> dict[str, Any] | None:
        """The hvac.precool_window row for `target_date_iso`, or None."""
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: -7d)
              |> filter(fn: (r) => r._measurement == "hvac.precool_window"
                                    and r.target_date == "{target_date_iso}")
              |> last()
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        """
        rows = self._flatten_query(flux)
        return rows[0] if rows else None

    def fetch_hvac_actions(self, target_date_iso: str) -> list[dict[str, Any]]:
        """All hvac.actions rows for the CT day `target_date_iso`.

        The CT day spans UTC 05:00 → 05:00 next day during CDT (and
        06:00 → 06:00 during CST). Using a fixed CDT offset here is
        acceptable for the May-Sep cooling season the report targets;
        revisit if winter-season reports are ever needed.
        """
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        start_ct = datetime.fromisoformat(target_date_iso).replace(tzinfo=ct)
        end_ct = start_ct + timedelta(days=1)
        start_utc = start_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: {start_utc}, stop: {end_utc})
              |> filter(fn: (r) => r._measurement == "hvac.actions")
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        """
        return self._flatten_query(flux)

    def fetch_comed_prices_above(
        self,
        target_date_iso: str,
        threshold_cents: float,
    ) -> list[dict[str, Any]]:
        """ComEd 5-min prices on `target_date_iso` (CT) with value
        >= `threshold_cents`. Field name is `price_cents_per_kwh`."""
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        start_ct = datetime.fromisoformat(target_date_iso).replace(tzinfo=ct)
        end_ct = start_ct + timedelta(days=1)
        start_utc = start_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: {start_utc}, stop: {end_utc})
              |> filter(fn: (r) => r._measurement == "comed.prices"
                                    and r._field == "price_cents_per_kwh"
                                    and r._value >= {threshold_cents})
        """
        return self._flatten_query(flux)
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_influx_client.py -v
```
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/influx_client.py tools/decision_trace_report/tests/test_influx_client.py
git commit -m "feat(report-tool): InfluxClient fetch_precool_window/actions/comed_prices"
```

### Task 2.3: InfluxClient — feed-health max_time queries

**Files:**
- Modify: `tools/decision_trace_report/influx_client.py`
- Modify: `tools/decision_trace_report/tests/test_influx_client.py`

- [ ] **Step 1: Write failing test**

Append to `test_influx_client.py`:

```python
def test_last_write_time_returns_utc_datetime(influx_url):
    """last_write_time queries max(_time) of a measurement and returns
    a timezone-aware UTC datetime, or None if no rows."""
    from datetime import datetime, timezone
    fake_record = MagicMock()
    fake_record.values = {}
    fake_record.get_time.return_value = datetime(2026, 5, 15, 17, 0, tzinfo=timezone.utc)
    fake_table = MagicMock()
    fake_table.records = [fake_record]
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = [fake_table]

    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    last = client.last_write_time("comed.prices")

    flux = fake_query_api.query.call_args[0][0]
    assert 'r._measurement == "comed.prices"' in flux
    assert "|> last()" in flux
    assert last == datetime(2026, 5, 15, 17, 0, tzinfo=timezone.utc)


def test_last_write_time_returns_none_for_empty_result(influx_url):
    fake_query_api = MagicMock()
    fake_query_api.query.return_value = []
    client = InfluxClient(influx_url, "t", "o", "energy", query_api=fake_query_api)
    assert client.last_write_time("nws.forecast") is None
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_influx_client.py -v
```
Expected: 2 new FAIL.

- [ ] **Step 3: Implement**

Append to `influx_client.py`:

```python
    def last_write_time(self, measurement: str) -> "datetime | None":
        """`max(_time)` for `measurement` over the last 7 days, or None.

        7-day window is wide enough to catch event feeds (e.g., PJM
        DA LMP fires once a day) but bounded so Flux isn't scanning
        forever.
        """
        from datetime import datetime
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: -7d)
              |> filter(fn: (r) => r._measurement == "{measurement}")
              |> last()
        """
        for table in self._query_api.query(flux):
            for record in table.records:
                return record.get_time()
        return None
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_influx_client.py -v
```
Expected: 7 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/influx_client.py tools/decision_trace_report/tests/test_influx_client.py
git commit -m "feat(report-tool): InfluxClient.last_write_time for feed health"
```

---

## Phase 3 — Telegram client

### Task 3.1: TelegramClient — send_message via Bot API

**Files:**
- Create: `tools/decision_trace_report/telegram_client.py`
- Create: `tools/decision_trace_report/tests/test_telegram_client.py`

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_telegram_client.py
"""Tests for TelegramClient. No live HTTP."""
from unittest.mock import MagicMock

from tools.decision_trace_report.telegram_client import TelegramClient


def test_send_message_posts_to_bot_api(monkeypatch):
    """send_message POSTs to api.telegram.org/bot<token>/sendMessage
    with the chat_id and text."""
    captured = {}

    def fake_post(url, data=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json or data
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True}
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.post", fake_post)

    client = TelegramClient(bot_token="abc123", chat_id="-100456")
    client.send_message("hello")

    assert captured["url"] == "https://api.telegram.org/botabc123/sendMessage"
    assert captured["payload"]["chat_id"] == "-100456"
    assert captured["payload"]["text"] == "hello"


def test_send_message_swallows_telegram_errors(monkeypatch, caplog):
    """Telegram failure must not crash the report tool — log and move
    on. Failure to deliver the heartbeat does not invalidate the
    rendered report file."""
    import requests

    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("Telegram unreachable")

    monkeypatch.setattr("requests.post", fake_post)

    client = TelegramClient(bot_token="abc123", chat_id="-100456")
    # Must not raise.
    client.send_message("hello")
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_telegram_client.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# tools/decision_trace_report/telegram_client.py
"""Telegram Bot API wrapper for heartbeat messages.

Failure to deliver the heartbeat must NOT crash the report tool —
the rendered file is the artifact; Telegram is the heartbeat.
"""
import logging

import requests

log = logging.getLogger(__name__)


class TelegramClient:
    """Posts to https://api.telegram.org/bot<token>/sendMessage."""

    def __init__(self, bot_token: str, chat_id: str, timeout_s: float = 10.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_s = timeout_s

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, text: str) -> None:
        """Send a plain-text message. Swallows network/HTTP errors."""
        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=self.timeout_s,
            )
            response.raise_for_status()
        except Exception as exc:
            log.warning("telegram heartbeat send failed: %s", exc)
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_telegram_client.py -v
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/telegram_client.py tools/decision_trace_report/tests/test_telegram_client.py
git commit -m "feat(report-tool): TelegramClient.send_message with error swallow"
```

---

## Phase 4 — Section modules

### Task 4.1: §5 coverage_scorecard — enum reflection

**Files:**
- Create: `tools/decision_trace_report/decision_codes_loader.py`
- Create: `tools/decision_trace_report/sections/__init__.py`
- Create: `tools/decision_trace_report/sections/coverage_scorecard.py`
- Create: `tools/decision_trace_report/tests/test_sections_coverage_scorecard.py`

Starting with §5 because it has the simplest data model (a list of enum values vs. a count of observations). Builds the test pattern for the other sections.

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_sections_coverage_scorecard.py
"""Tests for the §5 coverage scorecard section."""
from tools.decision_trace_report.sections.coverage_scorecard import render


def test_coverage_scorecard_observed_vs_not_observed():
    """For each reason_code in the reference enum, show whether it was
    observed cumulatively + last 7 days."""
    reference_codes = {
        "PriceOverlayCode": [
            "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
            "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
            "PRICE_OVERLAY_STALE_FEED_RELEASED",
        ],
    }
    cumulative_counts = {
        "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER": 12000,
        "PRICE_OVERLAY_UPGRADED_TO_ELEVATED": 0,
        "PRICE_OVERLAY_STALE_FEED_RELEASED": 0,
    }
    recent_7d_counts = {
        "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER": 2000,
        "PRICE_OVERLAY_UPGRADED_TO_ELEVATED": 0,
        "PRICE_OVERLAY_STALE_FEED_RELEASED": 0,
    }

    out = render(
        reference_codes=reference_codes,
        cumulative_counts=cumulative_counts,
        recent_7d_counts=recent_7d_counts,
    )

    assert "PriceOverlayCode" in out
    assert "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER" in out
    # Observed -> ✅; not observed -> ⚪
    assert "✅" in out
    assert "⚪" in out
    # Counts should be visible
    assert "12000" in out or "12,000" in out
    assert "2000" in out or "2,000" in out


def test_coverage_scorecard_flags_unexpected_codes():
    """Any cumulative_counts key NOT in reference_codes flat-list is an
    'unexpected reason code' anomaly."""
    reference_codes = {
        "SupervisorCode": ["SUPERVISOR_APPROVED", "SUPERVISOR_CLAMPED_COOL_FLOOR"],
    }
    cumulative_counts = {
        "SUPERVISOR_APPROVED": 100,
        "SUPERVISOR_CLAMPED_COOL_FLOOR": 0,
        "SUPERVISOR_UNKNOWN_PHANTOM": 3,
    }
    recent_7d_counts = {"SUPERVISOR_APPROVED": 10}

    out = render(
        reference_codes=reference_codes,
        cumulative_counts=cumulative_counts,
        recent_7d_counts=recent_7d_counts,
    )

    assert "SUPERVISOR_UNKNOWN_PHANTOM" in out
    assert "unexpected" in out.lower()
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_sections_coverage_scorecard.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement scoped helpers**

```python
# tools/decision_trace_report/sections/__init__.py
```

```python
# tools/decision_trace_report/decision_codes_loader.py
"""Reflect the reason_code enums from the hvac-scheduler's decision_codes
module. Imports the actual module so adding a new code there is picked
up automatically by §5 coverage."""
import importlib
import sys
from pathlib import Path


def load_reference_codes() -> dict[str, list[str]]:
    """Return `{enum_class_name: [code_value, ...]}` for every str-Enum
    declared in `deploy/energy-stack/hvac-scheduler/decision_codes.py`.

    The path is computed relative to this file so the tool works from
    any cwd as long as the repo root is on disk.
    """
    repo_root = Path(__file__).resolve().parents[2]
    scheduler_dir = repo_root / "deploy" / "energy-stack" / "hvac-scheduler"
    if str(scheduler_dir) not in sys.path:
        sys.path.insert(0, str(scheduler_dir))
    module = importlib.import_module("decision_codes")
    importlib.reload(module)  # in case tests run multiple times

    from enum import Enum
    out: dict[str, list[str]] = {}
    for name in dir(module):
        cls = getattr(module, name)
        if isinstance(cls, type) and issubclass(cls, Enum) and cls is not Enum:
            out[name] = [member.value for member in cls]
    return out
```

```python
# tools/decision_trace_report/sections/coverage_scorecard.py
"""§5 coverage scorecard — which reason_codes have been observed live."""


def render(
    *,
    reference_codes: dict[str, list[str]],
    cumulative_counts: dict[str, int],
    recent_7d_counts: dict[str, int],
) -> str:
    """Render the coverage scorecard as a markdown string.

    For each enum group, list every reference code with status (observed
    live vs not) + cumulative count + last-7d count. Codes appearing in
    cumulative_counts but NOT in any enum group are reported as
    'unexpected reason codes' (anomaly).
    """
    lines: list[str] = ["## §5 Coverage scorecard", ""]
    all_reference: set[str] = {c for codes in reference_codes.values() for c in codes}

    # Unexpected codes — counts but not in any enum
    unexpected = sorted(set(cumulative_counts) - all_reference)
    if unexpected:
        lines.append("### ⚠️ Unexpected reason codes (in trace but NOT in any enum)")
        lines.append("")
        for code in unexpected:
            n_cum = cumulative_counts.get(code, 0)
            n_7d = recent_7d_counts.get(code, 0)
            lines.append(f"- `{code}` — cumulative: {n_cum:,}, last 7d: {n_7d:,}")
        lines.append("")

    # Per-enum tables
    for enum_name in sorted(reference_codes):
        codes = reference_codes[enum_name]
        lines.append(f"### {enum_name}")
        lines.append("")
        lines.append("| Code | Status | Cumulative | Last 7 days |")
        lines.append("|---|---|---:|---:|")
        for code in codes:
            n_cum = cumulative_counts.get(code, 0)
            n_7d = recent_7d_counts.get(code, 0)
            status = "✅ observed live" if n_cum > 0 else "⚪ not observed live"
            lines.append(f"| `{code}` | {status} | {n_cum:,} | {n_7d:,} |")
        lines.append("")

    return "\n".join(lines)


def count_unexpected(
    reference_codes: dict[str, list[str]],
    cumulative_counts: dict[str, int],
) -> int:
    """Number of cumulative_counts keys not in any reference enum.
    Used by the top-level anomaly summary."""
    all_reference = {c for codes in reference_codes.values() for c in codes}
    return len(set(cumulative_counts) - all_reference)
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_sections_coverage_scorecard.py -v
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/decision_codes_loader.py tools/decision_trace_report/sections/__init__.py tools/decision_trace_report/sections/coverage_scorecard.py tools/decision_trace_report/tests/test_sections_coverage_scorecard.py
git commit -m "feat(report-tool): §5 coverage scorecard section + enum reflection"
```

### Task 4.2: §4 feed_health — fresh/warn/stale per feed

**Files:**
- Create: `tools/decision_trace_report/sections/feed_health.py`
- Create: `tools/decision_trace_report/tests/test_sections_feed_health.py`

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_sections_feed_health.py
"""Tests for §4 feed health section."""
from datetime import datetime, timedelta, timezone

from tools.decision_trace_report.sections.feed_health import (
    classify_age,
    render,
)


def test_classify_age_fresh_warn_stale():
    """Three buckets for a continuous feed with a 5-min warn / 10-min
    stale threshold."""
    assert classify_age(timedelta(minutes=1), warn=timedelta(minutes=5),
                         stale=timedelta(minutes=10)) == "fresh"
    assert classify_age(timedelta(minutes=7), warn=timedelta(minutes=5),
                         stale=timedelta(minutes=10)) == "warn"
    assert classify_age(timedelta(minutes=15), warn=timedelta(minutes=5),
                         stale=timedelta(minutes=10)) == "stale"


def test_render_reports_fresh_feed():
    """A fresh continuous feed shows ✅ status + age in the markdown."""
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    feeds = [
        {
            "name": "comed.prices",
            "kind": "continuous",
            "last_write": datetime(2026, 5, 16, 7, 59, tzinfo=timezone.utc),
            "warn": timedelta(minutes=5),
            "stale": timedelta(minutes=10),
        },
    ]
    out = render(now=now, feeds=feeds)
    assert "comed.prices" in out
    assert "✅" in out


def test_render_stale_feed_counted_for_anomaly():
    """Stale feed shows 🔴 status + can be counted via count_stale."""
    from tools.decision_trace_report.sections.feed_health import count_stale
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    feeds = [
        {
            "name": "nws.forecast",
            "kind": "continuous",
            "last_write": datetime(2026, 5, 16, 4, 0, tzinfo=timezone.utc),  # 4h ago
            "warn": timedelta(minutes=60),
            "stale": timedelta(hours=2),
        },
    ]
    out = render(now=now, feeds=feeds)
    assert "🔴" in out
    assert count_stale(now=now, feeds=feeds) == 1


def test_event_feed_uses_expected_next_fire_window():
    """Event feed (e.g., DA LMP at 17:00 CT daily) is stale only if
    last_write is older than the most-recent expected fire window
    plus a grace period."""
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)  # 03:00 CT on May 16
    feeds = [
        {
            "name": "pjm.lmp_da_hourly",
            "kind": "event",
            "last_write": datetime(2026, 5, 15, 22, 0, tzinfo=timezone.utc),
            # Last write = 17:00 CT May 15. Most recent expected fire
            # was 17:00 CT May 15. So fresh.
            "expected_fire_description": "17:00 CT daily",
            "last_expected_fire_utc": datetime(2026, 5, 15, 22, 0, tzinfo=timezone.utc),
            "grace": timedelta(hours=2),
        },
    ]
    out = render(now=now, feeds=feeds)
    assert "✅" in out


def test_missing_feed_renders_loudly_not_silently():
    """A feed with last_write=None (never seen) must NOT disappear from
    the report. Surface it as 'missing' and count it toward stale —
    silent-feed disappearance is exactly the kind of commissioning
    failure the report exists to catch."""
    from tools.decision_trace_report.sections.feed_health import count_stale
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    feeds = [
        {
            "name": "pjm.metered_load",
            "kind": "event",
            "last_write": None,  # never written / poller never ran
            "expected_fire_description": "Sunday 02:00 CT weekly",
            "last_expected_fire_utc": datetime(2026, 5, 11, 7, 0, tzinfo=timezone.utc),
            "grace": timedelta(hours=2),
        },
    ]
    out = render(now=now, feeds=feeds)
    # Feed name MUST appear — not silently dropped
    assert "pjm.metered_load" in out
    # Some visible "missing" indicator
    assert ("missing" in out.lower()
            or "never seen" in out.lower()
            or "🔴" in out)
    # Counts as stale for anomaly summary
    assert count_stale(now=now, feeds=feeds) == 1
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_sections_feed_health.py -v
```
Expected: 4 FAIL.

- [ ] **Step 3: Implement**

```python
# tools/decision_trace_report/sections/feed_health.py
"""§4 feed + telemetry health.

Continuous feeds (ComEd 5-min prices, NWS forecast, Refoss, etc.)
use simple age thresholds — `warn` = first concern, `stale` = real
problem.

Event feeds (PJM DA LMP daily, weekly metered load) are stale only
if last_write predates the most-recent expected fire window beyond
the grace period; using simple age would falsely flag them every
hour they haven't fired.
"""
from datetime import datetime, timedelta
from typing import Literal


Status = Literal["fresh", "warn", "stale"]


def classify_age(age: timedelta, *, warn: timedelta, stale: timedelta) -> Status:
    if age < warn:
        return "fresh"
    if age < stale:
        return "warn"
    return "stale"


def _classify_feed(now: datetime, feed: dict) -> tuple[Status, str]:
    """Return (status, age_or_freshness_label) for one feed dict.

    A missing feed (last_write=None) is ALWAYS stale — surfaced loudly.
    The report is a commissioning monitor; a poller that never wrote
    is exactly the failure we must catch, not silently filter."""
    if feed.get("last_write") is None:
        return "stale", "missing — no data found in Influx"
    if feed["kind"] == "continuous":
        age = now - feed["last_write"]
        status = classify_age(age, warn=feed["warn"], stale=feed["stale"])
        return status, _format_timedelta(age)
    if feed["kind"] == "event":
        expected = feed["last_expected_fire_utc"]
        grace = feed.get("grace", timedelta(hours=2))
        if feed["last_write"] >= expected:
            return "fresh", f"caught up through {expected.isoformat()}"
        if now - expected < grace:
            return "warn", f"missed expected fire at {expected.isoformat()}"
        return "stale", f"missed expected fire at {expected.isoformat()} (grace exceeded)"
    raise ValueError(f"unknown feed kind: {feed['kind']!r}")


def _format_timedelta(delta: timedelta) -> str:
    total_s = int(delta.total_seconds())
    if total_s < 60:
        return f"{total_s}s"
    if total_s < 3600:
        return f"{total_s // 60}m"
    return f"{total_s // 3600}h{(total_s % 3600) // 60}m"


def render(*, now: datetime, feeds: list[dict]) -> str:
    lines: list[str] = ["## §4 Feed + telemetry health", ""]
    lines.append("| Feed | Kind | Last write | Age / status | Verdict |")
    lines.append("|---|---|---|---|---|")
    for feed in feeds:
        status, label = _classify_feed(now, feed)
        icon = {"fresh": "✅", "warn": "⚠️", "stale": "🔴"}[status]
        last = feed["last_write"].isoformat() if feed["last_write"] else "—"
        lines.append(
            f"| `{feed['name']}` | {feed['kind']} | {last} | {label} | {icon} {status} |"
        )
    lines.append("")
    return "\n".join(lines)


def count_stale(*, now: datetime, feeds: list[dict]) -> int:
    return sum(1 for feed in feeds if _classify_feed(now, feed)[0] == "stale")
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_sections_feed_health.py -v
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/sections/feed_health.py tools/decision_trace_report/tests/test_sections_feed_health.py
git commit -m "feat(report-tool): §4 feed health section"
```

### Task 4.3: §1 night_before — day_type + precool + reconciliation

**Files:**
- Create: `tools/decision_trace_report/sections/night_before.py`
- Create: `tools/decision_trace_report/tests/test_sections_night_before.py`

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_sections_night_before.py
"""Tests for §1 night-before decision audit."""
from tools.decision_trace_report.sections.night_before import (
    count_discrepancies,
    render,
)


def test_renders_winning_day_type_and_evaluation_tape():
    """Happy path: section renders the day-type winner + tape."""
    day_type_events = [
        {
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
        },
    ]
    precool_events = [
        {
            "msg": "decision_trace.precool_decision",
            "decision_for_date": "2026-05-15",
            "selected": False,
            "reason_code": "PRECOOL_REJECTED_NO_CHEAP_WINDOW",
        },
    ]
    hvac_decisions = [
        {"decision_for_date": "2026-05-15", "day_type": "NORMAL"},
    ]
    hvac_precool_window = None

    out = render(
        target_date="2026-05-15",
        day_type_events=day_type_events,
        precool_events=precool_events,
        hvac_decisions=hvac_decisions,
        hvac_precool_window=hvac_precool_window,
    )

    assert "NORMAL" in out
    assert "DAY_TYPE_HOT_HIGH_GE_85" in out
    assert "DAY_TYPE_NORMAL_HIGH_75_TO_84" in out
    assert "PRECOOL_REJECTED_NO_CHEAP_WINDOW" in out
    # Section heading present
    assert "§1" in out or "§1 Night-before" in out


def test_flags_trace_vs_influx_disagreement():
    """If the trace says day_type=NORMAL but the hvac.decisions row says
    day_type=HOT, that's an anomaly."""
    day_type_events = [
        {
            "msg": "decision_trace.day_type_decision",
            "decision_for_date": "2026-05-15",
            "winning_day_type": "NORMAL",
            "winning_reason": "high_75_to_84",
            "evaluation_tape": [],
            "high_f": 80.0,
            "apparent_max_f": 82.0,
        },
    ]
    hvac_decisions = [
        {"decision_for_date": "2026-05-15", "day_type": "HOT_5CP_RISK"},
    ]

    out = render(
        target_date="2026-05-15",
        day_type_events=day_type_events,
        precool_events=[],
        hvac_decisions=hvac_decisions,
        hvac_precool_window=None,
    )
    assert "disagree" in out.lower() or "mismatch" in out.lower()
    assert count_discrepancies(
        day_type_events=day_type_events,
        precool_events=[],
        hvac_decisions=hvac_decisions,
        hvac_precool_window=None,
    ) == 1


def test_no_day_type_events_renders_gracefully():
    """No 21:00 decision trace found -> note it in the section, don't
    crash. Counts as a discrepancy if Influx has the row."""
    hvac_decisions = [
        {"decision_for_date": "2026-05-15", "day_type": "MILD"},
    ]
    out = render(
        target_date="2026-05-15",
        day_type_events=[],
        precool_events=[],
        hvac_decisions=hvac_decisions,
        hvac_precool_window=None,
    )
    assert "no decision_trace.day_type_decision" in out.lower()
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_sections_night_before.py -v
```
Expected: 3 FAIL.

- [ ] **Step 3: Implement**

```python
# tools/decision_trace_report/sections/night_before.py
"""§1 night-before decision audit.

Renders the 21:00 day-type decision + revisits, §7 precool decision,
and reconciles trace events against the hvac.decisions /
hvac.precool_window Influx rows. Discrepancies count toward the top
anomaly summary.
"""
from typing import Any


def render(
    *,
    target_date: str,
    day_type_events: list[dict[str, Any]],
    precool_events: list[dict[str, Any]],
    hvac_decisions: list[dict[str, Any]],
    hvac_precool_window: dict[str, Any] | None,
) -> str:
    lines: list[str] = [f"## §1 Night-before decision audit — {target_date}", ""]

    # Day-type
    lines.append("### Day-type decision")
    lines.append("")
    if not day_type_events:
        lines.append("⚠️ No `decision_trace.day_type_decision` events found for this date.")
        if hvac_decisions:
            lines.append("")
            lines.append(
                f"`hvac.decisions` row present: `{hvac_decisions[0].get('day_type')}` "
                "— possible trace/Influx disagreement (no trace to compare)."
            )
    else:
        for evt in day_type_events:
            lines.append(
                f"- **`{evt.get('winning_day_type')}`** "
                f"(reason: `{evt.get('winning_reason')}`)"
            )
            lines.append(
                f"  high_f={evt.get('high_f')}, apparent_max_f={evt.get('apparent_max_f')}"
            )
            tape = evt.get("evaluation_tape", [])
            if tape:
                lines.append("")
                lines.append("  | rule | threshold | actual | fired | reason_code |")
                lines.append("  |---|---|---|---|---|")
                for entry in tape:
                    fired = "✅" if entry["fired"] else "❌"
                    lines.append(
                        f"  | {entry['rule']} | {entry['threshold']} | "
                        f"{entry['actual']} | {fired} | `{entry['reason_code']}` |"
                    )
            lines.append("")

    # Trace vs Influx reconciliation
    trace_dt = day_type_events[0]["winning_day_type"] if day_type_events else None
    influx_dt = hvac_decisions[0]["day_type"] if hvac_decisions else None
    if trace_dt and influx_dt and trace_dt != influx_dt:
        lines.append(
            f"### ⚠️ Reconciliation mismatch: trace says `{trace_dt}`, "
            f"hvac.decisions says `{influx_dt}` — investigate."
        )
        lines.append("")

    # Precool decision
    lines.append("### §7 Precool decision")
    lines.append("")
    if not precool_events:
        lines.append("⚠️ No `decision_trace.precool_decision` event found for this date.")
    else:
        evt = precool_events[0]
        lines.append(
            f"- selected: **{evt.get('selected')}**, "
            f"reason_code: `{evt.get('reason_code')}`"
        )
        if evt.get("selected"):
            lines.append(
                f"  hour_ct={evt.get('hour_ct')}, depth_f={evt.get('depth_f')}"
            )
    lines.append("")

    # Precool reconciliation
    if precool_events and hvac_precool_window is not None:
        trace_sel = precool_events[0].get("selected")
        influx_sel = hvac_precool_window is not None and "hour_ct" in hvac_precool_window
        if trace_sel != influx_sel:
            lines.append(
                f"### ⚠️ Precool reconciliation mismatch: trace selected={trace_sel}, "
                f"hvac.precool_window row present={influx_sel}"
            )
            lines.append("")

    return "\n".join(lines)


def count_discrepancies(
    *,
    day_type_events: list[dict[str, Any]],
    precool_events: list[dict[str, Any]],
    hvac_decisions: list[dict[str, Any]],
    hvac_precool_window: dict[str, Any] | None,
) -> int:
    n = 0
    trace_dt = day_type_events[0]["winning_day_type"] if day_type_events else None
    influx_dt = hvac_decisions[0]["day_type"] if hvac_decisions else None
    if trace_dt and influx_dt and trace_dt != influx_dt:
        n += 1
    if precool_events and hvac_precool_window is not None:
        trace_sel = precool_events[0].get("selected")
        influx_sel = "hour_ct" in hvac_precool_window
        if trace_sel != influx_sel:
            n += 1
    return n
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_sections_night_before.py -v
```
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/sections/night_before.py tools/decision_trace_report/tests/test_sections_night_before.py
git commit -m "feat(report-tool): §1 night-before section + reconciliation"
```

### Task 4.4: §2 day_of — chronological timeline + action-fire reconciliation

**Files:**
- Create: `tools/decision_trace_report/sections/day_of.py`
- Create: `tools/decision_trace_report/tests/test_sections_day_of.py`

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_sections_day_of.py
"""Tests for §2 live day-of decision audit."""
from tools.decision_trace_report.sections.day_of import (
    count_action_fire_mismatches,
    count_supervisor_non_approved,
    render,
)


def test_chronological_timeline_grouped_by_tick_id():
    """Events sharing tick_id render as a group, chronologically ordered."""
    layer_events = [
        {
            "msg": "decision_trace.layer_resolution",
            "ts": "2026-05-15T13:00:01-05:00",
            "tick_id": "tick_aaaa1234567890",
            "winning_layer": "schedule",
            "schedule_cool_f": 79,
            "price_cool_f": 79,
            "fivecp_cool_f": 79,
            "effective_cool_f": 79,
        },
    ]
    supervisor_events = [
        {
            "msg": "decision_trace.supervisor",
            "ts": "2026-05-15T13:00:01-05:00",
            "tick_id": "tick_aaaa1234567890",
            "decision": "approved",
            "reason_code": "SUPERVISOR_APPROVED",
        },
    ]
    out = render(
        target_date="2026-05-15",
        layer_events=layer_events,
        supervisor_events=supervisor_events,
        hvac_actions=[],
    )
    assert "tick_aaaa" in out
    assert "schedule" in out
    assert "SUPERVISOR_APPROVED" in out


def test_counts_supervisor_non_approved():
    layer_events = []
    supervisor_events = [
        {"decision": "approved", "tick_id": "a", "reason_code": "SUPERVISOR_APPROVED",
         "ts": "2026-05-15T12:00:00-05:00"},
        {"decision": "clamped", "tick_id": "b",
         "reason_code": "SUPERVISOR_CLAMPED_COOL_FLOOR",
         "ts": "2026-05-15T13:00:00-05:00"},
        {"decision": "emergency", "tick_id": "c",
         "reason_code": "SUPERVISOR_EMERGENCY_OVERHEAT",
         "ts": "2026-05-15T14:00:00-05:00"},
    ]
    assert count_supervisor_non_approved(supervisor_events) == 2


def test_action_fire_reconciliation_flags_missing_influx_row():
    """An action-fire trace (non-MID_PERIOD_REPUSH action_label) must
    have a matching hvac.actions row within +/- 2 minutes of the trace
    ts. Missing -> anomaly."""
    from datetime import datetime, timezone
    layer_events = [
        {
            "msg": "decision_trace.layer_resolution",
            "tick_id": "tick_x",
            "ts": "2026-05-15T18:00:00+00:00",
            "action_label": "COAST",      # action-fire, NOT MID_PERIOD_REPUSH
            "effective_cool_f": 78,
        },
    ]
    hvac_actions = []  # no row -> mismatch
    assert count_action_fire_mismatches(layer_events=layer_events,
                                          hvac_actions=hvac_actions) == 1


def test_action_fire_matches_only_by_label_AND_nearby_timestamp():
    """Reconciliation must match by (action_label, time-window). A
    COAST hvac.actions row at 13:00 should NOT satisfy a COAST trace
    event at 22:00 — one Influx row can't cover two separate firings
    of the same label on the same day."""
    from datetime import datetime, timezone
    layer_events = [
        {
            "msg": "decision_trace.layer_resolution",
            "tick_id": "tick_a",
            "ts": "2026-05-15T18:00:00+00:00",  # 13:00 CT
            "action_label": "COAST",
            "effective_cool_f": 78,
        },
        {
            "msg": "decision_trace.layer_resolution",
            "tick_id": "tick_b",
            "ts": "2026-05-16T03:00:00+00:00",  # 22:00 CT — second firing
            "action_label": "COAST",
            "effective_cool_f": 79,
        },
    ]
    hvac_actions = [
        {
            "action_label": "COAST",
            "_time": datetime(2026, 5, 15, 18, 0, 30, tzinfo=timezone.utc),  # matches tick_a
        },
        # No row for tick_b's 22:00 firing -> 1 mismatch expected
    ]
    assert count_action_fire_mismatches(layer_events=layer_events,
                                          hvac_actions=hvac_actions) == 1


def test_action_fire_matches_within_two_minute_window():
    """Match window is +/- 2 minutes to allow for tick-fire vs Influx-
    write latency (Pi clock skew, write batching, etc.)."""
    from datetime import datetime, timezone
    layer_events = [
        {
            "tick_id": "tick_x",
            "ts": "2026-05-15T18:00:00+00:00",
            "action_label": "COAST",
            "effective_cool_f": 78,
            "msg": "decision_trace.layer_resolution",
        },
    ]
    hvac_actions = [
        {
            "action_label": "COAST",
            "_time": datetime(2026, 5, 15, 18, 1, 30, tzinfo=timezone.utc),  # 90s later
        },
    ]
    assert count_action_fire_mismatches(layer_events=layer_events,
                                          hvac_actions=hvac_actions) == 0


def test_mid_period_repush_does_not_count_as_mismatch():
    """Mid-period repush traces without a matching action are NORMAL
    (most ticks emit trace + supervisor but write no row). Only
    action-fire labels are reconciled."""
    layer_events = [
        {
            "msg": "decision_trace.layer_resolution",
            "tick_id": "tick_y",
            "ts": "2026-05-15T13:00:00-05:00",
            "action_label": "MID_PERIOD_REPUSH:COAST",
            "effective_cool_f": 78,
        },
    ]
    hvac_actions = []
    assert count_action_fire_mismatches(layer_events=layer_events,
                                          hvac_actions=hvac_actions) == 0
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_sections_day_of.py -v
```
Expected: 4 FAIL.

- [ ] **Step 3: Implement**

```python
# tools/decision_trace_report/sections/day_of.py
"""§2 live day-of decision audit.

Chronological timeline of layer_resolution + supervisor events.
Reconciliation against hvac.actions is narrowed to action-fire events
only (action_label NOT prefixed `MID_PERIOD_REPUSH:`) so the common
mid-period-no-op case doesn't false-flag.
"""
from typing import Any


def _is_action_fire(event: dict[str, Any]) -> bool:
    """True when the event's action_label indicates a scheduled-action
    firing (not a mid-period repush)."""
    label = event.get("action_label")
    if not isinstance(label, str):
        return False
    return not label.startswith("MID_PERIOD_REPUSH:")


def render(
    *,
    target_date: str,
    layer_events: list[dict[str, Any]],
    supervisor_events: list[dict[str, Any]],
    hvac_actions: list[dict[str, Any]],
) -> str:
    lines: list[str] = [f"## §2 Live day-of decision audit — {target_date}", ""]
    all_events = sorted(
        [*layer_events, *supervisor_events],
        key=lambda e: e.get("ts", ""),
    )
    if not all_events:
        lines.append("No `decision_trace.layer_resolution` or `decision_trace.supervisor` "
                     "events for this date.")
        lines.append("")
        return "\n".join(lines)

    lines.append("| time | tick_id | event | winning_layer / decision | "
                  "effective_cool_f | reason_code |")
    lines.append("|---|---|---|---|---|---|")
    for evt in all_events:
        ts = evt.get("ts", "")[-14:]  # HH:MM:SS.xxx-zz suffix-ish
        tick = (evt.get("tick_id") or "")[:8]
        kind = "layer" if evt.get("msg") == "decision_trace.layer_resolution" else "sup"
        if kind == "layer":
            secondary = evt.get("winning_layer", "")
            eff = evt.get("effective_cool_f", "")
            reason = ""
        else:
            secondary = evt.get("decision", "")
            eff = ""
            reason = f"`{evt.get('reason_code', '')}`" if evt.get('reason_code') else ""
        lines.append(f"| {ts} | `{tick}` | {kind} | {secondary} | {eff} | {reason} |")
    lines.append("")

    # Reconciliation summary
    fire_mismatches = count_action_fire_mismatches(
        layer_events=layer_events, hvac_actions=hvac_actions,
    )
    if fire_mismatches:
        lines.append(
            f"### ⚠️ Action-fire reconciliation: {fire_mismatches} trace event(s) "
            "without matching `hvac.actions` row."
        )
    return "\n".join(lines)


def count_supervisor_non_approved(supervisor_events: list[dict[str, Any]]) -> int:
    return sum(1 for evt in supervisor_events if evt.get("decision") != "approved")


def count_action_fire_mismatches(
    *,
    layer_events: list[dict[str, Any]],
    hvac_actions: list[dict[str, Any]],
    match_window_s: int = 120,
) -> int:
    """A layer_resolution event with an action-fire label and no
    matching hvac.actions row within +/- match_window_s seconds is
    a mismatch. Mid-period repushes are not reconciled.

    Matches by BOTH action_label AND timestamp window so that one
    Influx row can't accidentally satisfy two separate trace events
    sharing the same label (e.g., two COAST firings same day)."""
    from datetime import datetime
    from datetime import timedelta as _td

    def parse_ts(s: Any) -> datetime | None:
        if not isinstance(s, str):
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    # Build a list of (label, ts) tuples we can mark as consumed
    available = [
        (a.get("action_label"), a.get("_time"))
        for a in hvac_actions
        if a.get("action_label") and a.get("_time") is not None
    ]
    consumed = [False] * len(available)

    n_mismatch = 0
    window = _td(seconds=match_window_s)
    for evt in layer_events:
        if not _is_action_fire(evt):
            continue
        evt_label = evt.get("action_label")
        evt_ts = parse_ts(evt.get("ts"))
        if evt_ts is None or not evt_label:
            continue
        # Find first unconsumed action row matching label + within window
        matched = False
        for i, (label, row_ts) in enumerate(available):
            if consumed[i] or label != evt_label:
                continue
            if abs(row_ts - evt_ts) <= window:
                consumed[i] = True
                matched = True
                break
        if not matched:
            n_mismatch += 1
    return n_mismatch
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_sections_day_of.py -v
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/sections/day_of.py tools/decision_trace_report/tests/test_sections_day_of.py
git commit -m "feat(report-tool): §2 day-of section + action-fire reconciliation"
```

### Task 4.5: §3 price_spikes — coarse explainability check

**Files:**
- Create: `tools/decision_trace_report/sections/price_spikes.py`
- Create: `tools/decision_trace_report/tests/test_sections_price_spikes.py`

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_sections_price_spikes.py
"""Tests for §3 price spike reaction audit."""
from tools.decision_trace_report.sections.price_spikes import (
    count_unexplained,
    is_explained,
    render,
)


def test_explained_when_normal_tier_below_threshold():
    """A price under the elevated threshold with tier=normal IS
    explained (the controller is correctly inactive)."""
    spike = {"price_cents": 9.5, "_time": "2026-05-15T13:00:00Z"}
    trace = {
        "ts": "2026-05-15T13:00:01-05:00",
        "outcome": "held",
        "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
        "prev_tier": "normal",
        "new_tier": "normal",
    }
    assert is_explained(spike, trace) is True


def test_explained_when_elevated_upgrade_after_spike():
    """A spike >=10c followed by an UPGRADED_TO_ELEVATED is explained."""
    spike = {"price_cents": 12.0, "_time": "2026-05-15T13:00:00Z"}
    trace = {
        "outcome": "upgraded",
        "reason_code": "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
        "prev_tier": "normal",
        "new_tier": "elevated",
    }
    assert is_explained(spike, trace) is True


def test_unexplained_when_spike_but_normal_tier_held():
    """Price >=10c with overlay HELD_IN_TIER at normal -> unexplained."""
    spike = {"price_cents": 15.0, "_time": "2026-05-15T13:00:00Z"}
    trace = {
        "outcome": "held",
        "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
        "prev_tier": "normal",
        "new_tier": "normal",
    }
    assert is_explained(spike, trace) is False


def test_render_no_spikes_today():
    """Empty spike list -> 'No spikes today' message."""
    out = render(target_date="2026-05-15", spikes=[], overlay_events=[])
    assert "no spikes today" in out.lower() or "no spikes" in out.lower()


def test_count_unexplained_aggregates():
    spikes = [
        {"price_cents": 15.0, "_time": "2026-05-15T13:00:00Z"},
        {"price_cents": 11.0, "_time": "2026-05-15T13:05:00Z"},
    ]
    overlay_events = [
        {"ts": "2026-05-15T13:00:01-05:00", "reason_code": "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
         "prev_tier": "normal", "new_tier": "normal", "outcome": "held"},
        {"ts": "2026-05-15T13:05:01-05:00", "reason_code": "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
         "prev_tier": "normal", "new_tier": "elevated", "outcome": "upgraded"},
    ]
    # First spike: held in normal -> unexplained. Second: upgrade -> explained.
    assert count_unexplained(spikes=spikes, overlay_events=overlay_events) == 1
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_sections_price_spikes.py -v
```
Expected: 5 FAIL.

- [ ] **Step 3: Implement**

```python
# tools/decision_trace_report/sections/price_spikes.py
"""§3 price spike reaction audit.

Coarse correctness check: for each ComEd 5-min price >=10c, look at
the nearest decision_trace.price_overlay_eval and check whether the
observed reason_code is in a small allow-list for the observed tier.
NOT a re-implementation of the state machine — false positives at
minimum-hold edges are accepted as a v1 trade-off.
"""
from typing import Any

# Allow-list per observed tier
ALLOWED_BY_TIER: dict[str, set[str]] = {
    "elevated": {
        "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
        "PRICE_OVERLAY_UPGRADED_TO_SCARCITY",
        "PRICE_OVERLAY_HELD_IN_TIER",
    },
    "scarcity": {
        "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
        "PRICE_OVERLAY_UPGRADED_TO_SCARCITY",
        "PRICE_OVERLAY_HELD_IN_TIER",
    },
    "normal": {
        "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
        "PRICE_OVERLAY_STALE_FEED_RELEASED",
        "PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED",
    },
}


def is_explained(spike: dict[str, Any], trace: dict[str, Any]) -> bool:
    """Coarse v1 check — observed reason_code in tier-specific allow-list.

    Special case: if spike price >= 10c and observed tier is `normal`,
    the only "explained" reason_codes are stale-feed / feed-unavailable.
    NORMAL_BELOW_TRIGGER does not explain a real spike."""
    tier = trace.get("new_tier") or "normal"
    code = trace.get("reason_code", "")
    if tier == "normal" and spike.get("price_cents", 0) >= 10.0:
        return code in {"PRICE_OVERLAY_STALE_FEED_RELEASED",
                         "PRICE_OVERLAY_FEED_UNAVAILABLE_TIER_PRESERVED"}
    return code in ALLOWED_BY_TIER.get(tier, set())


def _nearest_trace(
    spike_time_iso: str,
    overlay_events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the overlay event closest in time to spike_time_iso."""
    if not overlay_events:
        return None
    # Lexicographic compare on ISO timestamps works for same-tz strings.
    nearest = min(
        overlay_events,
        key=lambda e: abs(_iso_to_sortable(e.get("ts", "")) -
                          _iso_to_sortable(spike_time_iso)),
    )
    return nearest


def _iso_to_sortable(s: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def render(
    *,
    target_date: str,
    spikes: list[dict[str, Any]],
    overlay_events: list[dict[str, Any]],
) -> str:
    lines: list[str] = [f"## §3 Price spike reaction audit — {target_date}", ""]
    if not spikes:
        lines.append("No spikes today (no `comed.prices` ≥10¢ on this date).")
        lines.append("")
        return "\n".join(lines)

    lines.append("| spike time | price ¢/kWh | nearest trace | tier | reason_code | explained |")
    lines.append("|---|---:|---|---|---|---|")
    for spike in spikes:
        trace = _nearest_trace(spike.get("_time", ""), overlay_events)
        if trace is None:
            lines.append(
                f"| {spike.get('_time')} | {spike.get('price_cents'):.2f} | "
                "(no nearby trace) | — | — | ❌ no trace |"
            )
            continue
        tier = trace.get("new_tier", "")
        code = trace.get("reason_code", "")
        ok = is_explained(spike, trace)
        icon = "✅" if ok else "❌"
        lines.append(
            f"| {spike.get('_time')} | {spike.get('price_cents'):.2f} | "
            f"{trace.get('ts', '')[-14:]} | `{tier}` | `{code}` | {icon} |"
        )
    lines.append("")
    return "\n".join(lines)


def count_unexplained(
    *,
    spikes: list[dict[str, Any]],
    overlay_events: list[dict[str, Any]],
) -> int:
    n = 0
    for spike in spikes:
        trace = _nearest_trace(spike.get("_time", ""), overlay_events)
        if trace is None or not is_explained(spike, trace):
            n += 1
    return n
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_sections_price_spikes.py -v
```
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/sections/price_spikes.py tools/decision_trace_report/tests/test_sections_price_spikes.py
git commit -m "feat(report-tool): §3 price spike section + coarse explainability check"
```

---

## Phase 5 — Renderer + anomaly summary

### Task 5.1: Renderer — header, ToC, anomaly summary assembly

**Files:**
- Create: `tools/decision_trace_report/renderer.py`
- Create: `tools/decision_trace_report/tests/test_renderer.py`

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_renderer.py
"""Tests for renderer.build_report — assembles section markdown into one
file + anomaly summary."""
from datetime import datetime, timezone

from tools.decision_trace_report.renderer import AnomalySummary, build_report


def test_build_report_starts_with_header_and_toc():
    """Every report has a YAML-style frontmatter or header + ToC."""
    summary = AnomalySummary(
        unexpected_codes=0,
        supervisor_non_approved=0,
        stale_feeds=0,
        trace_influx_discrepancies=0,
        unexplained_spikes=0,
        query_errors=0,
    )
    out = build_report(
        target_date="2026-05-15",
        rendered_at=datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc),
        sections={
            "night_before": "## §1 Night-before\n\nbody",
            "day_of": "## §2 Day-of\n\nbody",
            "price_spikes": "## §3 Price spikes\n\nbody",
            "feed_health": "## §4 Feed health\n\nbody",
            "coverage_scorecard": "## §5 Coverage\n\nbody",
        },
        anomaly_summary=summary,
    )
    assert "# Decision-trace commissioning report — 2026-05-15" in out
    assert "## Table of contents" in out or "## ToC" in out
    assert "§1" in out and "§2" in out and "§3" in out and "§4" in out and "§5" in out


def test_build_report_includes_anomaly_summary():
    summary = AnomalySummary(
        unexpected_codes=1,
        supervisor_non_approved=2,
        stale_feeds=0,
        trace_influx_discrepancies=0,
        unexplained_spikes=0,
        query_errors=0,
    )
    out = build_report(
        target_date="2026-05-15",
        rendered_at=datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc),
        sections={},
        anomaly_summary=summary,
    )
    assert "Anomaly summary" in out or "anomaly" in out.lower()
    assert "Unexpected reason codes" in out
    assert "1" in out  # the count
    assert "2" in out


def test_anomaly_summary_all_green():
    summary = AnomalySummary(0, 0, 0, 0, 0, 0)
    assert summary.is_all_green() is True
    assert "all green" in summary.heartbeat_status().lower()


def test_anomaly_summary_open_report():
    summary = AnomalySummary(1, 0, 0, 0, 0, 0)
    assert summary.is_all_green() is False
    assert "open the report" in summary.heartbeat_status().lower()
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_renderer.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# tools/decision_trace_report/renderer.py
"""Assembles section markdown into a full report + anomaly summary."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AnomalySummary:
    unexpected_codes: int
    supervisor_non_approved: int
    stale_feeds: int
    trace_influx_discrepancies: int
    unexplained_spikes: int
    query_errors: int

    def total(self) -> int:
        return (
            self.unexpected_codes
            + self.supervisor_non_approved
            + self.stale_feeds
            + self.trace_influx_discrepancies
            + self.unexplained_spikes
            + self.query_errors
        )

    def is_all_green(self) -> bool:
        return self.total() == 0

    def heartbeat_status(self) -> str:
        return "all green" if self.is_all_green() else "open the report"

    def render_table(self) -> str:
        lines = [
            "## Anomaly summary",
            "",
            "| Type | Count |",
            "|---|---:|",
            f"| Unexpected reason codes | {self.unexpected_codes} |",
            f"| Supervisor non-approved decisions | {self.supervisor_non_approved} |",
            f"| Stale feeds | {self.stale_feeds} |",
            f"| Trace-vs-Influx discrepancies | {self.trace_influx_discrepancies} |",
            f"| Unexplained price spikes | {self.unexplained_spikes} |",
            f"| Query errors | {self.query_errors} |",
            "",
            f"**Status: {self.heartbeat_status()}**",
            "",
        ]
        return "\n".join(lines)


def build_report(
    *,
    target_date: str,
    rendered_at: datetime,
    sections: dict[str, str],
    anomaly_summary: AnomalySummary,
) -> str:
    """Assemble the full markdown report."""
    lines = [
        f"# Decision-trace commissioning report — {target_date}",
        "",
        f"_Rendered at {rendered_at.isoformat()}_",
        "",
        "## Table of contents",
        "",
        "- [Anomaly summary](#anomaly-summary)",
        "- [§1 Night-before decision audit](#1-night-before-decision-audit)",
        "- [§2 Live day-of decision audit](#2-live-day-of-decision-audit)",
        "- [§3 Price spike reaction audit](#3-price-spike-reaction-audit)",
        "- [§4 Feed + telemetry health](#4-feed--telemetry-health)",
        "- [§5 Coverage scorecard](#5-coverage-scorecard)",
        "",
        anomaly_summary.render_table(),
    ]
    for key in ["night_before", "day_of", "price_spikes", "feed_health", "coverage_scorecard"]:
        body = sections.get(key)
        if body:
            lines.append(body)
            lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_renderer.py -v
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/renderer.py tools/decision_trace_report/tests/test_renderer.py
git commit -m "feat(report-tool): renderer + AnomalySummary dataclass"
```

---

## Phase 6 — CLI + end-to-end

### Task 6.1: CLI scaffold — argparse + defaults

**Files:**
- Create: `tools/decision_trace_report/cli.py`
- Create: `tools/decision_trace_report/__main__.py`
- Create: `tools/decision_trace_report/tests/test_cli.py`

- [ ] **Step 1: Write failing test**

```python
# tools/decision_trace_report/tests/test_cli.py
"""Tests for the CLI entry point (argparse + default behavior)."""
from datetime import date, timedelta

from tools.decision_trace_report.cli import parse_args, default_target_date


def test_parse_args_defaults():
    """No flags -> target=None (resolves to yesterday CT), output=default."""
    args = parse_args([])
    assert args.date is None
    assert args.no_telegram is False
    assert args.verbose is False


def test_parse_args_date_flag():
    args = parse_args(["--date", "2026-05-14"])
    assert args.date == "2026-05-14"


def test_parse_args_no_telegram():
    args = parse_args(["--no-telegram"])
    assert args.no_telegram is True


def test_default_target_date_is_yesterday_ct():
    """When --date is omitted, the target is yesterday CT (rendered
    day = run day - 1)."""
    target = default_target_date(now=date(2026, 5, 16))
    assert target == "2026-05-15"
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_cli.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# tools/decision_trace_report/cli.py
"""CLI entry point for `python -m tools.decision_trace_report`."""
import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("decision_trace_report")


CT = ZoneInfo("America/Chicago")
DEFAULT_OUTPUT_DIR = Path(r"D:\Projects\energy-proxy\docs\test-reports")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tools.decision_trace_report",
        description=("Render a daily decision-trace commissioning report from "
                      "Loki + InfluxDB. See docs/superpowers/specs/2026-05-15-"
                      "decision-trace-report-tool-design.md."),
    )
    parser.add_argument("--date", help="CT calendar day to render (YYYY-MM-DD). "
                        "Default: yesterday CT.")
    parser.add_argument("--from", dest="from_ct",
                        help="Custom range start (CT, ISO local).")
    parser.add_argument("--to", dest="to_ct",
                        help="Custom range end (CT, ISO local).")
    parser.add_argument("--output", help="Override output file path.")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Suppress Telegram heartbeat.")
    parser.add_argument("--verbose", action="store_true",
                        help="Echo Loki + Influx query bodies (debug queries).")
    parser.add_argument("--loki-url", help="Override LOKI_URL env.")
    parser.add_argument("--influx-url", help="Override INFLUXDB_URL env.")
    parser.add_argument("--env-file",
                        help="Optional dotenv file to load before running.")
    return parser.parse_args(argv)


def default_target_date(*, now: date | None = None) -> str:
    """Yesterday's CT calendar day in YYYY-MM-DD format."""
    if now is None:
        now = datetime.now(CT).date()
    return (now - timedelta(days=1)).isoformat()


def load_env_file(path: str) -> None:
    """Read a dotenv-style file into os.environ. Keys already set in
    the environment are NOT overwritten."""
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _last_da_lmp_fire_utc(now_utc: datetime) -> datetime:
    """Most-recent expected 17:00 CT daily publish, in UTC.

    Used for §4 feed-health event-feed staleness check on
    `pjm.lmp_da_hourly`. If `now` is BEFORE today's 17:00 CT publish,
    last expected fire is yesterday's; otherwise today's."""
    now_ct = now_utc.astimezone(CT)
    today_17 = now_ct.replace(hour=17, minute=0, second=0, microsecond=0)
    if now_ct < today_17:
        today_17 = today_17 - timedelta(days=1)
    return today_17.astimezone(timezone.utc).replace(tzinfo=timezone.utc)


def _last_metered_load_fire_utc(now_utc: datetime) -> datetime:
    """Most-recent expected Sunday 02:00 CT weekly publish, in UTC.

    Used for §4 feed-health event-feed staleness check on
    `pjm.metered_load`."""
    now_ct = now_utc.astimezone(CT)
    # weekday: Monday=0 ... Sunday=6
    days_since_sunday = (now_ct.weekday() + 1) % 7
    last_sunday = (now_ct - timedelta(days=days_since_sunday)).replace(
        hour=2, minute=0, second=0, microsecond=0,
    )
    # If we're early Sunday before 02:00, use the previous Sunday's fire
    if last_sunday > now_ct:
        last_sunday = last_sunday - timedelta(days=7)
    return last_sunday.astimezone(timezone.utc).replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    target = args.date or default_target_date()
    log.info("rendering decision-trace report for target CT day %s", target)
    # End-to-end render wired in Task 6.2.
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```python
# tools/decision_trace_report/__main__.py
from tools.decision_trace_report.cli import main
import sys

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_cli.py -v
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/cli.py tools/decision_trace_report/__main__.py tools/decision_trace_report/tests/test_cli.py
git commit -m "feat(report-tool): CLI scaffold (argparse + defaults)"
```

### Task 6.2: CLI end-to-end — wire all sections together

**Files:**
- Modify: `tools/decision_trace_report/cli.py`
- Modify: `tools/decision_trace_report/tests/test_cli.py`
- Create: `tools/decision_trace_report/tests/fixtures/happy_day.md`

- [ ] **Step 1: Write end-to-end happy-day test**

Append to `test_cli.py`:

```python
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
```

- [ ] **Step 2: Run to verify fail**

```
python -m pytest tools/decision_trace_report/tests/test_cli.py::test_cli_renders_and_writes_file -v
```
Expected: FAIL — main() doesn't render yet.

- [ ] **Step 3: Implement end-to-end in cli.py**

Replace the `main` function in `cli.py`:

```python
def main(argv: list[str] | None = None) -> int:
    from datetime import datetime, timezone

    from tools.decision_trace_report import (
        decision_codes_loader,
        renderer,
    )
    from tools.decision_trace_report.influx_client import InfluxClient
    from tools.decision_trace_report.loki_client import LokiClient
    from tools.decision_trace_report.sections import (
        coverage_scorecard,
        day_of,
        feed_health,
        night_before,
        price_spikes,
    )
    from tools.decision_trace_report.telegram_client import TelegramClient

    args = parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    target = args.date or default_target_date()
    log.info("rendering decision-trace report for target CT day %s", target)

    loki = LokiClient(args.loki_url or os.environ["LOKI_URL"])
    influx = InfluxClient(
        url=args.influx_url or os.environ["INFLUXDB_URL"],
        token=os.environ["INFLUXDB_TOKEN"],
        org=os.environ["INFLUXDB_ORG"],
        bucket=os.environ.get("INFLUXDB_BUCKET", "energy"),
    )

    sections_md: dict[str, str] = {}
    query_errors = 0

    # CT-day UTC range for Loki queries
    from datetime import timedelta as td
    start_ct = datetime.fromisoformat(target).replace(tzinfo=CT)
    end_ct = start_ct + td(days=1)
    start_utc = start_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = end_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # §1 night-before
    try:
        day_type_events = [
            e for e in loki.fetch_decision_traces(
                "day_type_decision",
                # Cover prior-night 21:00 + revisits
                start=(start_ct - td(hours=4)).astimezone(timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M:%SZ"),
                end=end_utc,
            ) if e.get("decision_for_date") == target
        ]
        precool_events = [
            e for e in loki.fetch_decision_traces(
                "precool_decision",
                start=(start_ct - td(hours=4)).astimezone(timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M:%SZ"),
                end=end_utc,
            ) if e.get("decision_for_date") == target
        ]
        hvac_decisions = influx.fetch_hvac_decisions(target)
        hvac_precool = influx.fetch_precool_window(target)
        sections_md["night_before"] = night_before.render(
            target_date=target,
            day_type_events=day_type_events,
            precool_events=precool_events,
            hvac_decisions=hvac_decisions,
            hvac_precool_window=hvac_precool,
        )
        nb_disc = night_before.count_discrepancies(
            day_type_events=day_type_events,
            precool_events=precool_events,
            hvac_decisions=hvac_decisions,
            hvac_precool_window=hvac_precool,
        )
    except Exception as exc:
        log.warning("§1 query failed: %s", exc)
        sections_md["night_before"] = f"## §1 Night-before\n\n⚠️ query failed: `{exc}`\n"
        nb_disc = 0
        day_type_events, precool_events, hvac_decisions, hvac_precool = [], [], [], None
        query_errors += 1

    # §2 day-of
    try:
        layer_events = loki.fetch_decision_traces(
            "layer_resolution", start=start_utc, end=end_utc,
        )
        supervisor_events = loki.fetch_decision_traces(
            "supervisor", start=start_utc, end=end_utc,
        )
        hvac_actions = influx.fetch_hvac_actions(target)
        sections_md["day_of"] = day_of.render(
            target_date=target,
            layer_events=layer_events,
            supervisor_events=supervisor_events,
            hvac_actions=hvac_actions,
        )
        sup_non_approved = day_of.count_supervisor_non_approved(supervisor_events)
        action_mismatches = day_of.count_action_fire_mismatches(
            layer_events=layer_events, hvac_actions=hvac_actions,
        )
    except Exception as exc:
        log.warning("§2 query failed: %s", exc)
        sections_md["day_of"] = f"## §2 Day-of\n\n⚠️ query failed: `{exc}`\n"
        sup_non_approved, action_mismatches = 0, 0
        query_errors += 1

    # §3 price spikes
    try:
        spikes = influx.fetch_comed_prices_above(target, threshold_cents=10.0)
        overlay_events = loki.fetch_decision_traces(
            "price_overlay_eval", start=start_utc, end=end_utc,
        )
        sections_md["price_spikes"] = price_spikes.render(
            target_date=target, spikes=spikes, overlay_events=overlay_events,
        )
        unexplained = price_spikes.count_unexplained(
            spikes=spikes, overlay_events=overlay_events,
        )
    except Exception as exc:
        log.warning("§3 query failed: %s", exc)
        sections_md["price_spikes"] = (
            f"## §3 Price spikes\n\n⚠️ query failed: `{exc}`\n"
        )
        unexplained = 0
        query_errors += 1

    # §4 feed health
    try:
        from datetime import timedelta as td2
        now_utc = datetime.now(timezone.utc)
        # Compute expected last-fire timestamps for event feeds, in UTC.
        last_da_lmp_fire = _last_da_lmp_fire_utc(now_utc)
        last_metered_load_fire = _last_metered_load_fire_utc(now_utc)
        feeds = [
            {"name": "comed.prices", "kind": "continuous",
             "last_write": influx.last_write_time("comed.prices"),
             "warn": td2(minutes=5), "stale": td2(minutes=10)},
            {"name": "nws.forecast", "kind": "continuous",
             "last_write": influx.last_write_time("nws.forecast"),
             "warn": td2(minutes=60), "stale": td2(hours=2)},
            {"name": "refoss.channel", "kind": "continuous",
             "last_write": influx.last_write_time("refoss.channel"),
             "warn": td2(minutes=2), "stale": td2(minutes=5)},
            {"name": "hvac.thermostat", "kind": "continuous",
             "last_write": influx.last_write_time("hvac.thermostat"),
             "warn": td2(minutes=15), "stale": td2(minutes=30)},
            {"name": "haven.indoor", "kind": "continuous",
             "last_write": influx.last_write_time("haven.indoor"),
             "warn": td2(minutes=10), "stale": td2(minutes=30)},
            {"name": "pjm.inst_load", "kind": "continuous",
             "last_write": influx.last_write_time("pjm.inst_load"),
             "warn": td2(minutes=10), "stale": td2(minutes=30)},
            # Event feeds — spec §4 explicitly requires both. Missing
            # last_write (None) renders as "missing" + counts as stale
            # per feed_health._classify_feed.
            {"name": "pjm.lmp_da_hourly", "kind": "event",
             "last_write": influx.last_write_time("pjm.lmp_da_hourly"),
             "expected_fire_description": "17:00 CT daily",
             "last_expected_fire_utc": last_da_lmp_fire,
             "grace": td2(hours=2)},
            {"name": "pjm.metered_load", "kind": "event",
             "last_write": influx.last_write_time("pjm.metered_load"),
             "expected_fire_description": "Sunday 02:00 CT weekly",
             "last_expected_fire_utc": last_metered_load_fire,
             "grace": td2(hours=6)},
        ]
        # DO NOT filter feeds with last_write=None — those are surfaced
        # as "missing" by feed_health._classify_feed. Silently dropping
        # them is the exact failure mode this report is meant to catch.
        sections_md["feed_health"] = feed_health.render(now=now_utc, feeds=feeds)
        stale = feed_health.count_stale(now=now_utc, feeds=feeds)
    except Exception as exc:
        log.warning("§4 query failed: %s", exc)
        sections_md["feed_health"] = f"## §4 Feed health\n\n⚠️ query failed: `{exc}`\n"
        stale = 0
        query_errors += 1

    # §5 coverage scorecard
    try:
        reference_codes = decision_codes_loader.load_reference_codes()
        # Live counts via LokiClient.count_reason_codes over two windows.
        # 30d for cumulative is a Loki-retention-friendly proxy for
        # "since trace started" — if retention is shorter, Loki returns
        # what it has and the function still produces a usable summary.
        from datetime import timedelta as td3
        cumulative_start = (now_utc - td3(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_start = (now_utc - td3(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cumulative_counts = loki.count_reason_codes(
            start=cumulative_start,
            end=end_utc,
        )
        recent_7d_counts = loki.count_reason_codes(
            start=recent_start,
            end=end_utc,
        )
        sections_md["coverage_scorecard"] = coverage_scorecard.render(
            reference_codes=reference_codes,
            cumulative_counts=cumulative_counts,
            recent_7d_counts=recent_7d_counts,
        )
        unexpected = coverage_scorecard.count_unexpected(
            reference_codes=reference_codes,
            cumulative_counts=cumulative_counts,
        )
    except Exception as exc:
        log.warning("§5 failed: %s", exc)
        sections_md["coverage_scorecard"] = (
            f"## §5 Coverage scorecard\n\n⚠️ query/load failed: `{exc}`\n"
        )
        unexpected = 0
        query_errors += 1

    summary = renderer.AnomalySummary(
        unexpected_codes=unexpected,
        supervisor_non_approved=sup_non_approved,
        stale_feeds=stale,
        trace_influx_discrepancies=nb_disc + action_mismatches,
        unexplained_spikes=unexplained,
        query_errors=query_errors,
    )

    full_md = renderer.build_report(
        target_date=target,
        rendered_at=datetime.now(timezone.utc),
        sections=sections_md,
        anomaly_summary=summary,
    )

    output_path = Path(args.output) if args.output else (
        DEFAULT_OUTPUT_DIR / f"{target}-decision-trace.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_md, encoding="utf-8")
    log.info("wrote report to %s (%d lines)", output_path, full_md.count("\n"))

    # Heartbeat
    if not args.no_telegram:
        try:
            tg = TelegramClient(
                bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
                chat_id=os.environ["TELEGRAM_CHAT_ID"],
            )
            tg.send_message(
                f"Decision-trace commissioning report ready: {target}\n"
                f"Anomalies: {summary.total()} "
                f"({summary.unexpected_codes} unexpected codes, "
                f"{summary.supervisor_non_approved} sup non-approved, "
                f"{summary.stale_feeds} stale feeds, "
                f"{summary.query_errors} query errors)\n"
                f"Status: {summary.heartbeat_status()}\n"
                f"File: {output_path}"
            )
        except Exception as exc:
            log.warning("telegram heartbeat failed: %s", exc)

    return 0
```

Note: imports of `LokiClient` / `InfluxClient` / `TelegramClient` happen at the function-local level so the test's `monkeypatch.setattr` on `cli_mod.LokiClient` etc. needs them at module level. Add them at the top of `cli.py`:

```python
# Add near top of cli.py (after the os/sys/etc imports):
from tools.decision_trace_report.influx_client import InfluxClient
from tools.decision_trace_report.loki_client import LokiClient
from tools.decision_trace_report.telegram_client import TelegramClient
```

And remove the duplicated imports inside `main()`.

- [ ] **Step 4: Verify**

```
python -m pytest tools/decision_trace_report/tests/test_cli.py -v
```
Expected: 5 PASSED.

Full suite:
```
python -m pytest tools/decision_trace_report/ -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/decision_trace_report/cli.py tools/decision_trace_report/tests/test_cli.py
git commit -m "feat(report-tool): wire CLI end-to-end + per-section error isolation"
```

---

## Phase 7 — Operator documentation

### Task 7.1: Document Task Scheduler setup

**Files:**
- Modify: `tools/decision_trace_report/README.md`

- [ ] **Step 1: Add a "Daily automation on Windows" section to the README**

Append to `tools/decision_trace_report/README.md`:

```markdown

## Daily automation — Windows Task Scheduler

Goal: render yesterday's report every morning at 08:00 CT without manual
invocation.

1. Open Task Scheduler (Win+R → `taskschd.msc`).
2. Create Task → name: `decision-trace-report-daily`.
3. Trigger: Daily at 08:00. Synchronize across time zones: off.
4. Action: Start a program.
   - Program: `python.exe` (or the full path to your venv's python)
   - Arguments: `-m tools.decision_trace_report`
   - Start in: `D:\Projects\energy-proxy`
5. Conditions: uncheck "Start the task only if the computer is on AC power"
   if you want it to run on battery (desktop = AC always).
6. Settings: tick "Run task as soon as possible after a scheduled start
   is missed" so a brief reboot at 08:00 doesn't lose that day's run.
7. Environment variables: set `LOKI_URL`, `INFLUXDB_URL`, `INFLUXDB_TOKEN`,
   `INFLUXDB_ORG`, `INFLUXDB_BUCKET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   either as system env vars OR put them in a `.env` file and use
   `python -m tools.decision_trace_report --env-file C:\path\to\.env`
   as the action's arguments instead.

To verify: right-click the task → Run. Wait ~30s. Check
`D:\Projects\energy-proxy\docs\test-reports\` for the new file.
```

- [ ] **Step 2: Commit**

```bash
git add tools/decision_trace_report/README.md
git commit -m "docs(report-tool): Windows Task Scheduler setup guide"
```

---

## Phase 8 — Open PR

### Task 8.1: Push branch + open PR

- [ ] **Step 1: Verify full suite passes**

```
python -m pytest tools/decision_trace_report/ -v
```
Expected: all PASS, 0 failures, 0 errors.

- [ ] **Step 2: Push branch**

```bash
git push -u origin plan/decision-trace-report-tool
```

- [ ] **Step 3: Open PR against main**

Use `gh pr create --base main` with a body summarizing:
- Goal (commissioning report tool per spec PR #123)
- File layout
- Sections implemented
- Test coverage summary
- How to invoke (default + on-demand)
- Out of scope (rule-compliance verdict, etc.)

---

## Self-review

**Spec coverage:**
- §1 night-before — Task 4.3 ✅
- §2 day-of — Task 4.4 (reconciliation hardened to label + 2-min ts window) ✅
- §3 price-spike — Task 4.5 ✅
- §4 feed health — Task 4.2 (loud "missing" rendering for last_write=None) ✅
- §5 coverage scorecard — Task 4.1 ✅
- LokiClient — Tasks 1.1–1.4 (1.4 added: `count_reason_codes` for §5 live counts) ✅
- InfluxClient — Tasks 2.1–2.3 (2.2 has CST + CDT tests for DST hygiene) ✅
- TelegramClient — Task 3.1 ✅
- Renderer + AnomalySummary — Task 5.1 ✅
- CLI — Tasks 6.1–6.2 (6.2 wires `count_reason_codes` to §5; adds `pjm.lmp_da_hourly` + `pjm.metered_load` event feeds; surfaces missing feeds loudly instead of filtering) ✅
- Heartbeat content with all-green vs open-report status — Task 5.1 + 6.2 ✅
- Gitignored `docs/test-reports/` — Task 0.2 ✅
- `.env.example` tracked, `.env*` ignored — Task 0.1 (env-example) + repo `.gitignore` (already covers `.env*`) ✅
- Daily Task Scheduler entry — Task 7.1 ✅
- No live HTTP in tests — every test uses mocks/MagicMock ✅
- Per-section error isolation — Task 6.2 ✅
- Exit codes (0 rendered, 1 crash, 2 invalid args) — argparse handles 2 natively; main returns 0 always; uncaught exceptions yield 1 via `sys.exit` default — sufficient ✅
- All 8 critical feeds from spec §4 (comed.prices, nws.forecast, pjm.lmp_da_hourly, pjm.inst_load, pjm.metered_load, refoss.channel, hvac.thermostat, haven.indoor) — Task 6.2 ✅
- §5 live counts (cumulative + last 7d) sourced from real Loki queries, NOT empty dicts — Task 6.2 ✅
- DST hygiene — Task 2.2 includes CST + CDT cases — ✅

**Placeholder scan:** no TBD / TODO / "implement later" in any task. Every step has code or a concrete command. Every reason_code referenced is from the spec's enum list.

**Type consistency:** method names used consistently across tasks (e.g., `fetch_decision_traces`, `count_reason_codes`, `render`, `count_*`). `AnomalySummary` field names match between dataclass definition and consumer. Feed-dict shape consistent across `_classify_feed`, `render`, `count_stale`.

**Review-finding fixes applied (P1/P1/P2/P3):**
- P1 (§5 wiring): Task 1.4 adds `count_reason_codes` to LokiClient; Task 6.2 calls it for cumulative + 7d counts instead of empty dicts.
- P1 (§4 missing feeds + loud missing): Task 6.2 includes `pjm.lmp_da_hourly` + `pjm.metered_load` with event-feed expected-fire timestamps via `_last_da_lmp_fire_utc` / `_last_metered_load_fire_utc` helpers; Task 4.2 `_classify_feed` renders `last_write=None` as `🔴 stale — missing — no data found in Influx`; Task 6.2 no longer filters such feeds out.
- P2 (§2 reconciliation): Task 4.4 `count_action_fire_mismatches` now matches by `(action_label, +/- 2-min ts window)` with row-consumption tracking so one Influx row can't satisfy two same-label trace events.
- P3 (DST hygiene): Task 2.2 has both CDT (summer) and CST (winter) test cases proving the tz arithmetic isn't hardcoded.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-decision-trace-report-tool.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
