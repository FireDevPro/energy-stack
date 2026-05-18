"""Unit tests for tools/analysis/run_shadow_validation.py.

Covers pure-function helpers + the scarcity-divergence math + the
arm-calendar / mode-classification spot checks. Influx-dependent checks
(check_ingestion / check_pricing_reconstruction / check_refoss_hvac_kwh /
check_refoss_eagle_reconciliation / check_weather_vector_inputs /
audit_scarcity_divergence) are not exercised here — they need a live
Influx and live data, and are evidence-of-record in the run-time
findings doc, not in CI unit tests.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from tools.analysis.run_shadow_validation import (
    CheckResult,
    ScarcityDivergenceMetrics,
    SCARCITY_DIVERGENCE_FLAG_THRESHOLD_C,
    ValidationReport,
    _iso_z,
    _parse_iso_utc,
    check_arm_calendar,
    check_mode_classification,
    check_refoss_hvac_kwh,
    compute_scarcity_divergence,
    exit_code_for_status,
    main,
    write_findings_md_draft,
    write_results_json,
)
import tools.analysis.run_shadow_validation as shadow_validation


# ----- _iso_z + _parse_iso_utc -------------------------------------------


def test_iso_z_drops_microseconds_and_uses_z_suffix():
    dt = datetime.datetime(2026, 5, 18, 14, 26, 35, 123456, tzinfo=datetime.timezone.utc)
    assert _iso_z(dt) == "2026-05-18T14:26:35Z"


def test_iso_z_converts_aware_non_utc_to_utc():
    from zoneinfo import ZoneInfo
    dt_ct = datetime.datetime(2026, 5, 18, 9, 26, 35, tzinfo=ZoneInfo("America/Chicago"))
    # CT is UTC-5 in May (CDT).
    assert _iso_z(dt_ct) == "2026-05-18T14:26:35Z"


def test_parse_iso_utc_handles_z_suffix():
    dt = _parse_iso_utc("2026-05-18T14:26:35Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == datetime.timedelta(0)
    assert dt.year == 2026 and dt.hour == 14


def test_parse_iso_utc_handles_naive_input_as_utc():
    dt = _parse_iso_utc("2026-04-29T00:00:00")
    assert dt.utcoffset() == datetime.timedelta(0)
    assert dt.day == 29 and dt.hour == 0


# ----- check_mode_classification (pure spot-test) ------------------------


def test_check_mode_classification_passes():
    result = check_mode_classification()
    assert isinstance(result, CheckResult)
    assert result.status == "PASS"
    assert result.metrics.get("cases_checked", 0) >= 5


# ----- check_arm_calendar -------------------------------------------------


def test_arm_calendar_pre_experiment_window_passes():
    """2026-04-29 → 2026-05-18 is before arm 1 (2026-06-01); expect 0 arm hours."""
    from zoneinfo import ZoneInfo
    utc = datetime.timezone.utc
    start = datetime.datetime(2026, 4, 29, 0, 0, tzinfo=utc)
    end = datetime.datetime(2026, 5, 18, 0, 0, tzinfo=utc)
    result = check_arm_calendar(start, end)
    assert result.status == "PASS"
    assert result.metrics["in_arm_hours"] == 0
    assert result.metrics["sampled_hours"] > 0


def test_arm_calendar_window_overlapping_arm1_fails():
    """A window spanning into 2026-06-01 hits arm 1 and must FAIL."""
    utc = datetime.timezone.utc
    start = datetime.datetime(2026, 5, 31, 0, 0, tzinfo=utc)
    end = datetime.datetime(2026, 6, 3, 0, 0, tzinfo=utc)
    result = check_arm_calendar(start, end)
    assert result.status == "FAIL"
    assert result.metrics["in_arm_hours"] > 0
    assert result.reason_code == "shadow_window_overlaps_arm"


# ----- compute_scarcity_divergence ---------------------------------------


def _hourly_frame(times, values, col_name):
    return pd.DataFrame({"_time": pd.to_datetime(times), col_name: values})


def test_scarcity_divergence_blocked_when_comed_empty():
    pjm = _hourly_frame(["2026-05-01T00:00:00"], [3.0], "pjm_c_per_kwh")
    r = compute_scarcity_divergence(pd.DataFrame(), pjm)
    assert r.status == "BLOCKED"
    assert r.reason_code == "no_comed_prices"


def test_scarcity_divergence_blocked_when_pjm_empty():
    comed = _hourly_frame(["2026-05-01T00:00:00"], [3.0], "comed_c_per_kwh")
    r = compute_scarcity_divergence(comed, pd.DataFrame())
    assert r.status == "BLOCKED"
    assert r.reason_code == "no_rt_hrl_lmps"


def test_scarcity_divergence_blocked_when_no_hour_overlap():
    comed = _hourly_frame(["2026-05-01T00:00:00"], [3.0], "comed_c_per_kwh")
    pjm = _hourly_frame(["2026-05-02T00:00:00"], [3.0], "pjm_c_per_kwh")
    r = compute_scarcity_divergence(comed, pjm)
    assert r.status == "BLOCKED"
    assert r.reason_code == "no_hour_overlap"


def test_scarcity_divergence_pass_when_no_divergence():
    """Identical series at every hour -> 0 divergence at scarcity hours -> PASS."""
    times = pd.date_range("2026-05-01", periods=40, freq="h")
    # Values 1..40; p95 = 38.05 (linear-interpolated p95 of 40 values).
    vals = list(range(1, 41))
    comed = _hourly_frame(times, vals, "comed_c_per_kwh")
    pjm = _hourly_frame(times, vals, "pjm_c_per_kwh")
    r = compute_scarcity_divergence(comed, pjm)
    assert r.status == "PASS"
    assert r.n_diverging_over_2c == 0
    assert r.max_diff_c_per_kwh == 0.0
    assert r.n_scarcity_hours >= 1


def test_scarcity_divergence_warn_when_over_threshold():
    """Inject a 5 ¢/kWh divergence at a scarcity hour -> WARN + osf flag."""
    times = pd.date_range("2026-05-01", periods=40, freq="h")
    comed_vals = list(range(1, 41))  # 1..40 ¢/kWh
    pjm_vals = list(comed_vals)
    # The top values (39, 40) are scarcity hours under linear-p95 ≈ 38.05.
    # Push PJM at the highest hour 5 ¢ below to inject divergence.
    pjm_vals[-1] = comed_vals[-1] - 5.0
    comed = _hourly_frame(times, comed_vals, "comed_c_per_kwh")
    pjm = _hourly_frame(times, pjm_vals, "pjm_c_per_kwh")
    r = compute_scarcity_divergence(comed, pjm)
    assert r.status == "WARN"
    assert r.reason_code == "osf_appendix_flag"
    assert r.n_diverging_over_2c == 1
    assert r.max_diff_c_per_kwh == pytest.approx(5.0, abs=1e-6)


def test_scarcity_divergence_no_scarcity_when_window_too_tight():
    """Single-hour window: max==p95, no hour strictly above p95 -> N/A.

    Strict `>` per spec §11 #13 wording ("EXCEEDED its 95th percentile").
    """
    times = pd.date_range("2026-05-01", periods=1, freq="h")
    comed = _hourly_frame(times, [3.0], "comed_c_per_kwh")
    pjm = _hourly_frame(times, [3.0], "pjm_c_per_kwh")
    r = compute_scarcity_divergence(comed, pjm)
    assert r.status == "N/A"
    assert r.reason_code == "no_scarcity_hours"
    assert r.n_paired_hours == 1
    assert r.n_scarcity_hours == 0


def test_scarcity_divergence_strict_gt_boundary():
    """Two-hour window with equal values: neither strictly exceeds p95.

    Tests that `>` is used (not `>=`) per spec §11 #13 ("exceeded").
    """
    times = pd.date_range("2026-05-01", periods=2, freq="h")
    comed = _hourly_frame(times, [3.0, 3.0], "comed_c_per_kwh")
    pjm = _hourly_frame(times, [3.0, 3.0], "pjm_c_per_kwh")
    r = compute_scarcity_divergence(comed, pjm)
    assert r.status == "N/A"
    assert r.n_scarcity_hours == 0  # neither hour strictly above p95


def test_scarcity_divergence_threshold_is_configurable():
    """A 1.5 ¢/kWh divergence flagged when threshold lowered to 1.0."""
    times = pd.date_range("2026-05-01", periods=40, freq="h")
    comed_vals = list(range(1, 41))
    pjm_vals = list(comed_vals)
    pjm_vals[-1] = comed_vals[-1] - 1.5
    comed = _hourly_frame(times, comed_vals, "comed_c_per_kwh")
    pjm = _hourly_frame(times, pjm_vals, "pjm_c_per_kwh")
    r_default = compute_scarcity_divergence(comed, pjm)
    assert r_default.status == "PASS"  # 1.5 < 2.0 default threshold
    r_strict = compute_scarcity_divergence(comed, pjm, flag_threshold_c=1.0)
    assert r_strict.status == "WARN"
    assert r_strict.flag_threshold_c == 1.0


# ----- write_results_json + write_findings_md_draft ----------------------


def _sample_report() -> ValidationReport:
    return ValidationReport(
        runner_version="0.1.0",
        window_start_utc="2026-04-29T00:00:00Z",
        window_end_utc="2026-05-18T14:00:00Z",
        runner_commit_sha="abc1234",
        runner_host="testhost",
        influx_url="http://example:8086",
        bucket="energy",
        checks=[
            CheckResult(check_id="t.pass", description="d", status="PASS", notes="ok",
                        metrics={"n": 10}),
            CheckResult(check_id="t.warn", description="d", status="WARN",
                        reason_code="osf_appendix_flag",
                        notes="diverged|piped",
                        metrics={"n_diverging_over_2c": 3}),
        ],
    )


def test_write_results_json_round_trips(tmp_path: Path):
    report = _sample_report()
    path = tmp_path / "validation_results.json"
    write_results_json(report, path)
    data = json.loads(path.read_text())
    assert data["runner_version"] == "0.1.0"
    assert data["window_end_utc"] == "2026-05-18T14:00:00Z"
    assert len(data["checks"]) == 2
    assert data["checks"][0]["status"] == "PASS"
    assert data["checks"][1]["reason_code"] == "osf_appendix_flag"


def test_overall_status_promotes_to_warn():
    report = _sample_report()
    assert report.overall_status() == "WARN"


def test_overall_status_fail_dominates():
    report = _sample_report()
    report.checks.append(
        CheckResult(check_id="t.fail", description="d", status="FAIL", notes="boom")
    )
    assert report.overall_status() == "FAIL"


def test_overall_status_blocked_beats_warn():
    """A BLOCKED check should dominate WARN (escalates above)."""
    report = ValidationReport(
        runner_version="0.1.0",
        window_start_utc="x", window_end_utc="y",
        runner_commit_sha=None, runner_host="t",
        influx_url="x", bucket="b",
        checks=[
            CheckResult(check_id="a", description="d", status="WARN", notes=""),
            CheckResult(check_id="b", description="d", status="BLOCKED", notes=""),
        ],
    )
    assert report.overall_status() == "BLOCKED"


def test_write_findings_md_draft_includes_each_check(tmp_path: Path):
    report = _sample_report()
    path = tmp_path / "findings_draft.md"
    write_findings_md_draft(report, path)
    body = path.read_text()
    # Header + table + metrics sections present.
    assert "# Shadow validation findings (draft)" in body
    assert "| `t.pass` | **PASS**" in body
    assert "| `t.warn` | **WARN**" in body
    assert "### `t.warn`" in body
    # Pipe in notes is escaped to keep the table valid.
    assert "diverged\\|piped" in body
    # Overall status is reported.
    assert "overall: **WARN**" in body


def test_scarcity_metrics_dataclass_defaults():
    """ScarcityDivergenceMetrics defaults are sane for the BLOCKED case."""
    m = ScarcityDivergenceMetrics(
        status="BLOCKED", reason_code="no_comed_prices", n_paired_hours=0,
    )
    assert m.p95_c_per_kwh is None
    assert m.n_scarcity_hours == 0
    assert m.flag_threshold_c == SCARCITY_DIVERGENCE_FLAG_THRESHOLD_C


# ----- exit_code_for_status (CI red/green contract) ----------------------


def test_exit_code_fail_returns_1():
    assert exit_code_for_status("FAIL") == 1


def test_exit_code_blocked_returns_2():
    """BLOCKED means the runner could not actually validate; CI sees red."""
    assert exit_code_for_status("BLOCKED") == 2


def test_exit_code_warn_returns_0():
    """WARN signals (M3 OSF-appendix flag, expected-empty bills) are
    appendix flags, not run-failures. Stay green."""
    assert exit_code_for_status("WARN") == 0


def test_exit_code_pass_returns_0():
    assert exit_code_for_status("PASS") == 0


def test_exit_code_na_returns_0():
    assert exit_code_for_status("N/A") == 0
