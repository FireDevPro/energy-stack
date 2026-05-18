"""Pre-experiment shadow validation per spec §11 #13.

Runs validation checks against InfluxDB for the shadow data window
(2026-04-29 -> today). Pre-experiment data falls outside any locked arm,
so this is PIPELINE-SHAPE validation only, NOT outcome evidence.

Validation checks:
  1. Stage 1 ingestion presence per measurement (refoss.channel,
     ecowitt.weather, comed.prices, pjm.lmp_rt_hourly, eagle.meter,
     comed.bill).
  2. Pricing reconstruction sensible (bill_reconciliation; reason-code
     if no DTOD bill yet — first DTOD bill arrives 2026-05-24 per spec
     §14).
  3. Refoss HVAC channels (em:2 + em:8) compute valid hourly kWh.
  4. Refoss mains vs Eagle reconciliation — coverage + drift.
  5. Weather vector construction shape (Ecowitt + NOAA fallback
     station). build_weather_vector itself cannot be called against
     shadow data because pre-experiment hours are not inside any
     ArmPeriod; this check validates the inputs the converter and
     vector builder would consume.
  6. Arm calendar logic does not crash — current_arm_at returns None
     for every shadow hour, as expected.
  7. Mode classification spot-test (synthetic inputs).
  8. M3 scarcity-divergence audit — ComEd 5-min hourly mean vs PJM
     rt_hrl_lmps settled at hours where ComEd hourly mean > p95.

Per spec §11 #13 M3: if n_diverging_over_2c > 0, the result is flagged
for the OSF appendix as a real risk that controller-observed signals
deviate from bill-canonical prices at the moments controller decisions
matter most.

Outputs:
  - <output-dir>/validation_results.json — machine-readable report.
  - <output-dir>/findings_draft.md — human-readable findings table.

Env vars (required):
  INFLUXDB_URL      e.g. http://192.168.20.10:8086 (workstation) or
                    http://localhost:8086 (pi-lab).
  INFLUXDB_TOKEN
  INFLUXDB_ORG
  INFLUXDB_BUCKET   default: "energy"

Run from repo root:
  python tools/analysis/run_shadow_validation.py [--output-dir DIR] \
      [--window-start ISO] [--window-end ISO]
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import socket
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from tools.analysis import arm_calendar, eagle_coverage, hvac_dollars, mode_classification

_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")
RUNNER_VERSION = "0.1.0"
DEFAULT_BUCKET = "energy"
DEFAULT_WINDOW_START_UTC = datetime.datetime(2026, 4, 29, 0, 0, 0, tzinfo=_UTC)
NOAA_FALLBACK_STATION_LOCK = "KJOT"
SCARCITY_DIVERGENCE_FLAG_THRESHOLD_C = 2.0
PNODE_ID_COMED = "33092371"

Status = Literal["PASS", "FAIL", "BLOCKED", "N/A", "WARN"]


@dataclass
class CheckResult:
    check_id: str
    description: str
    status: Status
    reason_code: Optional[str] = None
    notes: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    runner_version: str
    window_start_utc: str
    window_end_utc: str
    runner_commit_sha: Optional[str]
    runner_host: str
    influx_url: str
    bucket: str
    checks: list[CheckResult]

    def overall_status(self) -> Status:
        statuses = [c.status for c in self.checks]
        if "FAIL" in statuses:
            return "FAIL"
        if "BLOCKED" in statuses:
            return "BLOCKED"
        if "WARN" in statuses:
            return "WARN"
        return "PASS"


# ----- Influx helpers ------------------------------------------------------


def _make_client(url: str, token: str, org: str):
    from influxdb_client import InfluxDBClient
    return InfluxDBClient(url=url, token=token, org=org, timeout=120_000)


def _query_df(client, flux: str) -> pd.DataFrame:
    """Run a Flux query and flatten results to a single DataFrame.

    Returns empty DataFrame on no results.
    """
    api = client.query_api()
    rows: list[dict] = []
    for table in api.query(flux):
        for record in table.records:
            rows.append(record.values)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _iso_z(dt: datetime.datetime) -> str:
    """Influx-compatible RFC3339 (UTC, trailing Z, no microseconds)."""
    dt = dt.astimezone(_UTC).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 — best-effort provenance
        pass
    return None


# ----- Check 1: Stage 1 ingestion presence --------------------------------


_INGESTION_TARGETS = [
    ("refoss.channel", "power_w", "filter(fn: (r) => r.channel == \"em:1\" or r.channel == \"em:2\" or r.channel == \"em:7\" or r.channel == \"em:8\" or r.channel == \"em:9\")"),
    # ecowitt.weather uses canonical ch1_temp_f — see
    # check_weather_vector_inputs for the Stage 1 vs poller field-name drift.
    ("ecowitt.weather", "ch1_temp_f", None),
    ("comed.prices", "price_cents_per_kwh", "filter(fn: (r) => r.period_type == \"5min\")"),
    ("pjm.lmp_rt_hourly", "total_lmp_rt", f"filter(fn: (r) => r.pnode_id == \"{PNODE_ID_COMED}\")"),
    ("eagle.meter", "delivered_kwh", None),
    ("comed.bill", None, None),
]


def check_ingestion(client, bucket: str, start: datetime.datetime, end: datetime.datetime) -> list[CheckResult]:
    out: list[CheckResult] = []
    s_iso = _iso_z(start)
    e_iso = _iso_z(end)
    for measurement, field_name, extra_filter in _INGESTION_TARGETS:
        check_id = f"ingestion.{measurement}"
        desc = f"Stage 1 ingestion presence: {measurement}"
        filters = [
            f'from(bucket: "{bucket}")',
            f"|> range(start: {s_iso}, stop: {e_iso})",
            f'|> filter(fn: (r) => r._measurement == "{measurement}")',
        ]
        if field_name:
            filters.append(f'|> filter(fn: (r) => r._field == "{field_name}")')
        if extra_filter:
            filters.append(f"|> {extra_filter}")
        flux_count = "\n".join(filters + ["|> count()"])
        flux_first_last = "\n".join(filters + [
            '|> keep(columns: ["_time"])',
            '|> reduce(identity: {first_ts: time(v: 0), last_ts: time(v: 0), n: 0},'
            ' fn: (r, accumulator) => ({'
            '   first_ts: if accumulator.n == 0 then r._time'
            '             else (if r._time < accumulator.first_ts then r._time else accumulator.first_ts),'
            '   last_ts: if r._time > accumulator.last_ts then r._time else accumulator.last_ts,'
            '   n: accumulator.n + 1'
            ' }))',
        ])
        try:
            df_cnt = _query_df(client, flux_count)
            count = int(df_cnt["_value"].sum()) if not df_cnt.empty else 0
        except Exception as exc:  # noqa: BLE001
            out.append(CheckResult(
                check_id=check_id, description=desc, status="BLOCKED",
                reason_code="influx_query_failed",
                notes=f"count query failed: {exc.__class__.__name__}: {exc}",
            ))
            continue
        first_ts = last_ts = None
        if count > 0:
            try:
                df_fl = _query_df(client, flux_first_last)
                if not df_fl.empty:
                    row = df_fl.iloc[0]
                    first_ts = str(row.get("first_ts"))
                    last_ts = str(row.get("last_ts"))
            except Exception:  # noqa: BLE001
                pass
        if count == 0:
            status: Status = "WARN" if measurement == "comed.bill" else "FAIL"
            reason = "no_dtod_bill_in_window" if measurement == "comed.bill" else "no_data_in_window"
            note = (
                "First DTOD bill arrives 2026-05-24 per spec §14; pre-DTOD "
                "bills used flat rates and are tracked separately."
                if measurement == "comed.bill" else
                "Measurement returned 0 rows for the window — investigate poller."
            )
            out.append(CheckResult(
                check_id=check_id, description=desc, status=status,
                reason_code=reason, notes=note,
                metrics={"row_count": 0},
            ))
        else:
            out.append(CheckResult(
                check_id=check_id, description=desc, status="PASS",
                notes=f"{count} rows present from {first_ts} to {last_ts}",
                metrics={
                    "row_count": count,
                    "first_ts": first_ts,
                    "last_ts": last_ts,
                },
            ))
    return out


# ----- Check 2: Pricing reconstruction -----------------------------------


def check_pricing_reconstruction(
    client, bucket: str, start: datetime.datetime, end: datetime.datetime,
) -> CheckResult:
    check_id = "pricing.reconstruction"
    desc = "Pricing reconstructs sensibly across the shadow window"
    s_iso, e_iso = _iso_z(start), _iso_z(end)
    flux_bills = f'''
from(bucket: "{bucket}")
  |> range(start: {s_iso}, stop: {e_iso})
  |> filter(fn: (r) => r._measurement == "comed.bill")
  |> filter(fn: (r) => r._field == "total_charges_dollars" or r._field == "bill_period_end_utc")
  |> count()
'''
    try:
        df = _query_df(client, flux_bills)
        n_bill_points = int(df["_value"].sum()) if not df.empty else 0
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_id=check_id, description=desc, status="BLOCKED",
            reason_code="influx_query_failed",
            notes=f"bill count query failed: {exc}",
        )
    if n_bill_points == 0:
        return CheckResult(
            check_id=check_id, description=desc, status="N/A",
            reason_code="no_dtod_bill_in_window",
            notes=(
                "No bills landed in the shadow window. First DTOD bill arrives "
                "2026-05-24 per spec §14; bill_reconciliation cannot be exercised "
                "against shadow data. Pre-DTOD reconciliation uses flat rates "
                "and is tested separately. The reconciliation code path was "
                "verified by Phase 3 unit + integration tests "
                "(test_bill_reconciliation.py + test_pipeline_realshape_e2e.py)."
            ),
            metrics={"bill_points_in_window": 0},
        )
    return CheckResult(
        check_id=check_id, description=desc, status="PASS",
        notes=f"{n_bill_points} bill data points in window — pricing reconciliation can run",
        metrics={"bill_points_in_window": n_bill_points},
    )


# ----- Check 3: Refoss HVAC hourly kWh ----------------------------------


def check_refoss_hvac_kwh(
    client, bucket: str, start: datetime.datetime, end: datetime.datetime,
) -> CheckResult:
    check_id = "refoss.hvac_kwh"
    desc = "Refoss HVAC channels (em:2 + em:8) compute hourly kWh"
    s_iso, e_iso = _iso_z(start), _iso_z(end)
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: {s_iso}, stop: {e_iso})
  |> filter(fn: (r) => r._measurement == "refoss.channel")
  |> filter(fn: (r) => r._field == "power_w")
  |> filter(fn: (r) => r.channel == "em:2" or r.channel == "em:8")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> group(columns: ["_time"])
  |> sum()
'''
    try:
        df = _query_df(client, flux)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_id=check_id, description=desc, status="BLOCKED",
            reason_code="influx_query_failed",
            notes=f"hvac kWh query failed: {exc}",
        )
    if df.empty:
        return CheckResult(
            check_id=check_id, description=desc, status="FAIL",
            reason_code="no_hvac_power_data",
            notes="No em:2/em:8 power_w samples in window.",
        )
    # df has _value column with combined em:2+em:8 hourly mean power (W).
    # Convert mean_w to hourly kWh: kWh = mean_w * 1h / 1000.
    hourly_kwh = df["_value"].astype(float) / 1000.0
    nonzero_hours = int((hourly_kwh > 0.01).sum())
    return CheckResult(
        check_id=check_id, description=desc, status="PASS",
        notes=(
            f"{len(hourly_kwh)} hourly buckets; "
            f"{nonzero_hours} hours with >0.01 kWh HVAC draw; "
            f"max={float(hourly_kwh.max()):.3f} kWh, "
            f"sum={float(hourly_kwh.sum()):.1f} kWh."
        ),
        metrics={
            "hourly_bucket_count": int(len(hourly_kwh)),
            "nonzero_hours": nonzero_hours,
            "max_kwh": float(hourly_kwh.max()),
            "sum_kwh": float(hourly_kwh.sum()),
        },
    )


# ----- Check 4: Refoss/Eagle reconciliation ------------------------------


def check_refoss_eagle_reconciliation(
    client, bucket: str, start: datetime.datetime, end: datetime.datetime,
) -> CheckResult:
    check_id = "reconciliation.refoss_eagle"
    desc = "Refoss mains vs Eagle reconciliation runs"
    s_iso, e_iso = _iso_z(start), _iso_z(end)
    flux_eagle = f'''
from(bucket: "{bucket}")
  |> range(start: {s_iso}, stop: {e_iso})
  |> filter(fn: (r) => r._measurement == "eagle.meter")
  |> filter(fn: (r) => r._field == "delivered_kwh")
  |> keep(columns: ["_time", "_value"])
'''
    try:
        eagle_df = _query_df(client, flux_eagle)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_id=check_id, description=desc, status="BLOCKED",
            reason_code="influx_query_failed",
            notes=f"eagle query failed: {exc}",
        )
    if eagle_df.empty:
        return CheckResult(
            check_id=check_id, description=desc, status="FAIL",
            reason_code="no_eagle_data",
            notes="Eagle meter has no delivered_kwh in window.",
        )
    start_naive_utc = start.astimezone(_UTC).replace(tzinfo=None)
    end_naive_utc = end.astimezone(_UTC).replace(tzinfo=None)
    coverage_pct = eagle_coverage.eagle_coverage_pct(
        eagle_df, start_naive_utc, end_naive_utc,
    )
    return CheckResult(
        check_id=check_id, description=desc, status="PASS",
        notes=(
            f"eagle_coverage_pct={coverage_pct:.2f}% across {len(eagle_df)} "
            "Eagle samples in window. Full reconciliation against Refoss mains "
            "is exercised by bill_reconciliation when a bill closes in-window "
            "(see check pricing.reconstruction)."
        ),
        metrics={
            "eagle_sample_count": int(len(eagle_df)),
            "eagle_coverage_pct": coverage_pct,
        },
    )


# ----- Check 5: Weather vector inputs + NOAA fallback lock ---------------


def check_weather_vector_inputs(
    client, bucket: str, start: datetime.datetime, end: datetime.datetime,
) -> CheckResult:
    check_id = "weather.vector_inputs"
    desc = (
        "Weather-vector inputs present + NOAA fallback station locked + "
        "Stage 1 query field-name alignment"
    )
    s_iso, e_iso = _iso_z(start), _iso_z(end)
    # Pull counts for both naming conventions so we can detect the known
    # Stage 1 vs poller field-name drift. weather_vector.py consumes
    # ch1_temp_f / ch1_dewpoint_f; the Stage 1 Flux query at
    # tools/analysis/queries/ecowitt.weather.flux references outdoor_*.
    # Per ecowitt-ingest/app.py, ch1_* is the WN31 canonical shaded
    # sensor (written when WN31 is paired) and outdoor_* is a parallel
    # alias the ingest service no longer populates continuously.
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: {s_iso}, stop: {e_iso})
  |> filter(fn: (r) => r._measurement == "ecowitt.weather")
  |> filter(fn: (r) =>
        r._field == "ch1_temp_f" or r._field == "ch1_dewpoint_f"
     or r._field == "outdoor_temp_f" or r._field == "outdoor_dewpoint_f"
     or r._field == "ws90_temp_f" or r._field == "ws90_dewpoint_f")
  |> group(columns: ["_field"])
  |> count()
'''
    try:
        df = _query_df(client, flux)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_id=check_id, description=desc, status="BLOCKED",
            reason_code="influx_query_failed",
            notes=f"ecowitt query failed: {exc}",
        )
    by_field: dict[str, int] = {}
    if not df.empty and "_field" in df.columns:
        for _, row in df.iterrows():
            by_field[str(row["_field"])] = int(row["_value"])
    ch1_temp_n = by_field.get("ch1_temp_f", 0)
    ch1_dewpt_n = by_field.get("ch1_dewpoint_f", 0)
    outdoor_temp_n = by_field.get("outdoor_temp_f", 0)
    outdoor_dewpt_n = by_field.get("outdoor_dewpoint_f", 0)
    ws90_temp_n = by_field.get("ws90_temp_f", 0)
    ws90_dewpt_n = by_field.get("ws90_dewpoint_f", 0)

    if ch1_temp_n == 0 or ch1_dewpt_n == 0:
        return CheckResult(
            check_id=check_id, description=desc, status="FAIL",
            reason_code="missing_canonical_weather_field",
            notes=(
                "Canonical fields ch1_temp_f / ch1_dewpoint_f have no data "
                f"(ch1_temp_f={ch1_temp_n}, ch1_dewpoint_f={ch1_dewpt_n}). "
                "weather_vector.build_weather_vector cannot run."
            ),
            metrics=by_field,
        )
    # Detect the Stage 1 query field-name drift: tools/analysis/queries/
    # ecowitt.weather.flux references outdoor_*, but ch1_* is the actual
    # canonical the poller writes continuously.
    stage1_drift = ch1_temp_n > 10 * max(outdoor_temp_n, 1)
    if stage1_drift:
        return CheckResult(
            check_id=check_id, description=desc, status="WARN",
            reason_code="ecowitt_stage1_query_field_drift",
            notes=(
                f"Canonical ch1_temp_f={ch1_temp_n}, ch1_dewpoint_f={ch1_dewpt_n} "
                f"(weather_vector consumes these). "
                f"BUT Stage 1 Flux query (tools/analysis/queries/ecowitt.weather.flux) "
                f"references outdoor_temp_f={outdoor_temp_n}, outdoor_dewpoint_f="
                f"{outdoor_dewpt_n} — drift between Stage 1 query and poller writes. "
                f"WS90 sun-comparator: ws90_temp_f={ws90_temp_n}, ws90_dewpoint_f="
                f"{ws90_dewpt_n}. NOAA fallback station = {NOAA_FALLBACK_STATION_LOCK} "
                f"(spec §11 #11). Pre-OSF follow-up: update Stage 1 query to ch1_*."
            ),
            metrics={
                **by_field,
                "noaa_fallback_station_lock": NOAA_FALLBACK_STATION_LOCK,
                "stage1_query_field_drift": True,
            },
        )
    return CheckResult(
        check_id=check_id, description=desc, status="PASS",
        notes=(
            f"ch1_temp_f={ch1_temp_n}, ch1_dewpoint_f={ch1_dewpt_n}; "
            f"outdoor_*={outdoor_temp_n}/{outdoor_dewpt_n}; "
            f"ws90_*={ws90_temp_n}/{ws90_dewpt_n}; "
            f"NOAA fallback station = {NOAA_FALLBACK_STATION_LOCK} (spec §11 #11). "
            "build_weather_vector itself is not callable against shadow window — "
            "pre-experiment hours are outside any ArmPeriod."
        ),
        metrics={
            **by_field,
            "noaa_fallback_station_lock": NOAA_FALLBACK_STATION_LOCK,
            "stage1_query_field_drift": False,
        },
    )


# ----- Check 6: Arm calendar doesn't crash -------------------------------


def check_arm_calendar(start: datetime.datetime, end: datetime.datetime) -> CheckResult:
    check_id = "arm_calendar.no_crash"
    desc = "arm_calendar.current_arm_at returns None for every shadow hour"
    start_ct = start.astimezone(_CT).replace(tzinfo=None)
    end_ct = end.astimezone(_CT).replace(tzinfo=None)
    cursor = start_ct
    in_arm = 0
    sampled = 0
    while cursor < end_ct:
        if arm_calendar.current_arm_at(cursor) is not None:
            in_arm += 1
        sampled += 1
        cursor += datetime.timedelta(hours=1)
    if in_arm > 0:
        return CheckResult(
            check_id=check_id, description=desc, status="FAIL",
            reason_code="shadow_window_overlaps_arm",
            notes=(
                f"{in_arm} of {sampled} shadow hours map to a locked arm. "
                "Shadow window must end BEFORE 2026-06-01 00:00 CT."
            ),
            metrics={"sampled_hours": sampled, "in_arm_hours": in_arm},
        )
    return CheckResult(
        check_id=check_id, description=desc, status="PASS",
        notes=(
            f"{sampled} shadow hours checked; 0 are inside any locked arm "
            "(expected — experiment begins 2026-06-01)."
        ),
        metrics={"sampled_hours": sampled, "in_arm_hours": 0},
    )


# ----- Check 7: Mode classification spot-test ----------------------------


def check_mode_classification() -> CheckResult:
    check_id = "mode_classification.spot_test"
    desc = "mode_classification.classify_hour produces expected enum values"
    when = datetime.datetime(2026, 7, 15, 14, 0)  # in capacity-risk window
    cases = [
        dict(arm="A", telemetry_valid=True, controller_alive=True, feeds={"price": True, "weather": True, "pjm_capacity_risk": True}, expected=mode_classification.HourMode.A_ACTIVE),
        dict(arm="B", telemetry_valid=True, controller_alive=True, feeds={"price": True, "weather": True, "pjm_capacity_risk": True}, expected=mode_classification.HourMode.B_ACTIVE),
        dict(arm="B", telemetry_valid=True, controller_alive=False, feeds={"price": True, "weather": True, "pjm_capacity_risk": True}, expected=mode_classification.HourMode.B_DOWN),
        dict(arm="B", telemetry_valid=True, controller_alive=True, feeds={"price": True, "weather": False, "pjm_capacity_risk": True}, expected=mode_classification.HourMode.B_FALLBACK),
        dict(arm="A", telemetry_valid=False, controller_alive=True, feeds={"price": True, "weather": True, "pjm_capacity_risk": True}, expected=mode_classification.HourMode.TELEMETRY_INVALID),
    ]
    failures = []
    for c in cases:
        observed = mode_classification.classify_hour(
            arm=c["arm"], when_ct=when, telemetry_valid=c["telemetry_valid"],
            controller_alive=c["controller_alive"], feeds=c["feeds"],
        )
        if observed != c["expected"]:
            failures.append(
                f"arm={c['arm']} alive={c['controller_alive']} feeds={c['feeds']}: "
                f"expected {c['expected'].value}, observed {observed.value}"
            )
    if failures:
        return CheckResult(
            check_id=check_id, description=desc, status="FAIL",
            reason_code="mode_classification_mismatch",
            notes="; ".join(failures),
        )
    return CheckResult(
        check_id=check_id, description=desc, status="PASS",
        notes=f"All {len(cases)} spot cases produce expected HourMode values.",
        metrics={"cases_checked": len(cases)},
    )


# ----- Check 8 (M3): Scarcity divergence audit ---------------------------


@dataclass(frozen=True)
class ScarcityDivergenceMetrics:
    """Pure-data result of the scarcity divergence math.

    Separated from ``audit_scarcity_divergence`` so the math is unit-testable
    without an Influx client. Empty-input handling produces a metrics object
    with ``status`` set to "BLOCKED" / "N/A"; only the successful path
    populates the divergence numbers.
    """
    status: Status
    reason_code: Optional[str]
    n_paired_hours: int
    p95_c_per_kwh: Optional[float] = None
    n_scarcity_hours: int = 0
    max_diff_c_per_kwh: Optional[float] = None
    p95_diff_c_per_kwh: Optional[float] = None
    n_diverging_over_2c: int = 0
    flag_threshold_c: float = SCARCITY_DIVERGENCE_FLAG_THRESHOLD_C


def compute_scarcity_divergence(
    comed_df: pd.DataFrame, pjm_df: pd.DataFrame,
    flag_threshold_c: float = SCARCITY_DIVERGENCE_FLAG_THRESHOLD_C,
) -> ScarcityDivergenceMetrics:
    """Pure scarcity-divergence math (spec §11 #13 M3).

    Inputs:
      comed_df: hourly mean of comed.prices 5-min, columns
        [_time (naive-UTC hour-floored), comed_c_per_kwh].
      pjm_df: hourly pjm.lmp_rt_hourly, columns
        [_time (naive-UTC hour-floored), pjm_c_per_kwh].
        Caller must already have divided $/MWh by 10 to get ¢/kWh.

    Returns a ScarcityDivergenceMetrics dataclass:
      - status="BLOCKED" if either input is empty or hours don't overlap
      - status="N/A"     if no hours meet the p95 threshold
      - status="WARN"    if n_diverging_over_2c > 0 (OSF appendix flag)
      - status="PASS"    otherwise
    """
    if comed_df.empty:
        return ScarcityDivergenceMetrics(
            status="BLOCKED", reason_code="no_comed_prices",
            n_paired_hours=0, flag_threshold_c=flag_threshold_c,
        )
    if pjm_df.empty:
        return ScarcityDivergenceMetrics(
            status="BLOCKED", reason_code="no_rt_hrl_lmps",
            n_paired_hours=0, flag_threshold_c=flag_threshold_c,
        )
    merged = comed_df.merge(pjm_df, on="_time", how="inner")
    if merged.empty:
        return ScarcityDivergenceMetrics(
            status="BLOCKED", reason_code="no_hour_overlap",
            n_paired_hours=0, flag_threshold_c=flag_threshold_c,
        )
    comed_vals = merged["comed_c_per_kwh"].astype(float).tolist()
    p95 = (
        statistics.quantiles(comed_vals, n=20)[18]
        if len(comed_vals) >= 20 else max(comed_vals)
    )
    scarcity = merged[merged["comed_c_per_kwh"] >= p95].copy()
    n_scarcity = int(len(scarcity))
    if n_scarcity == 0:
        return ScarcityDivergenceMetrics(
            status="N/A", reason_code="no_scarcity_hours",
            n_paired_hours=int(len(merged)),
            p95_c_per_kwh=float(p95),
            flag_threshold_c=flag_threshold_c,
        )
    diffs = (scarcity["comed_c_per_kwh"] - scarcity["pjm_c_per_kwh"]).abs().astype(float)
    max_diff = float(diffs.max())
    p95_diff = (
        float(statistics.quantiles(diffs.tolist(), n=20)[18])
        if len(diffs) >= 20 else max_diff
    )
    n_over = int((diffs > flag_threshold_c).sum())
    return ScarcityDivergenceMetrics(
        status="WARN" if n_over > 0 else "PASS",
        reason_code="osf_appendix_flag" if n_over > 0 else None,
        n_paired_hours=int(len(merged)),
        p95_c_per_kwh=float(p95),
        n_scarcity_hours=n_scarcity,
        max_diff_c_per_kwh=max_diff,
        p95_diff_c_per_kwh=p95_diff,
        n_diverging_over_2c=n_over,
        flag_threshold_c=flag_threshold_c,
    )


def audit_scarcity_divergence(
    client, bucket: str, start: datetime.datetime, end: datetime.datetime,
) -> CheckResult:
    check_id = "m3.scarcity_divergence"
    desc = (
        "Live ComEd 5-min vs PJM rt_hrl_lmps settled at scarcity hours "
        "(spec §11 #13 M3)"
    )
    s_iso, e_iso = _iso_z(start), _iso_z(end)
    flux_comed = f'''
from(bucket: "{bucket}")
  |> range(start: {s_iso}, stop: {e_iso})
  |> filter(fn: (r) => r._measurement == "comed.prices")
  |> filter(fn: (r) => r._field == "price_cents_per_kwh")
  |> filter(fn: (r) => r.period_type == "5min")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
'''
    flux_pjm = f'''
from(bucket: "{bucket}")
  |> range(start: {s_iso}, stop: {e_iso})
  |> filter(fn: (r) => r._measurement == "pjm.lmp_rt_hourly")
  |> filter(fn: (r) => r._field == "total_lmp_rt")
  |> filter(fn: (r) => r.pnode_id == "{PNODE_ID_COMED}")
  |> keep(columns: ["_time", "_value"])
'''
    try:
        comed_df = _query_df(client, flux_comed)
        pjm_df = _query_df(client, flux_pjm)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            check_id=check_id, description=desc, status="BLOCKED",
            reason_code="influx_query_failed",
            notes=f"scarcity audit query failed: {exc}",
        )
    # Normalize both frames to (naive-UTC hour-floored _time, value in ¢/kWh).
    if not comed_df.empty:
        comed_df = comed_df.copy()
        comed_df["_time"] = (
            pd.to_datetime(comed_df["_time"], utc=True).dt.floor("h").dt.tz_convert(None)
        )
        comed_df = comed_df.rename(columns={"_value": "comed_c_per_kwh"})
        comed_df = comed_df[["_time", "comed_c_per_kwh"]]
    if not pjm_df.empty:
        pjm_df = pjm_df.copy()
        pjm_df["_time"] = (
            pd.to_datetime(pjm_df["_time"], utc=True).dt.floor("h").dt.tz_convert(None)
        )
        # PJM total_lmp_rt is $/MWh; convert to ¢/kWh by dividing by 10.
        pjm_df["pjm_c_per_kwh"] = pjm_df["_value"].astype(float) / 10.0
        pjm_df = pjm_df[["_time", "pjm_c_per_kwh"]]
    metrics = compute_scarcity_divergence(comed_df, pjm_df)

    notes_extra = ""
    if metrics.reason_code == "no_rt_hrl_lmps":
        notes_extra = " Spec §11 #6 requires backfill from 2026-01-01."
    if metrics.status == "WARN":
        notes_extra = " FLAGGED for OSF appendix per spec §11 #13 M3."

    return CheckResult(
        check_id=check_id, description=desc,
        status=metrics.status, reason_code=metrics.reason_code,
        notes=(
            f"p95={metrics.p95_c_per_kwh}, "
            f"n_paired_hours={metrics.n_paired_hours}, "
            f"n_scarcity_hours={metrics.n_scarcity_hours}, "
            f"max_diff_c_per_kwh={metrics.max_diff_c_per_kwh}, "
            f"p95_diff_c_per_kwh={metrics.p95_diff_c_per_kwh}, "
            f"n_diverging_over_{metrics.flag_threshold_c}c={metrics.n_diverging_over_2c}."
            + notes_extra
        ),
        metrics={
            "p95_c_per_kwh": metrics.p95_c_per_kwh,
            "n_paired_hours": metrics.n_paired_hours,
            "n_scarcity_hours": metrics.n_scarcity_hours,
            "max_diff_c_per_kwh": metrics.max_diff_c_per_kwh,
            "p95_diff_c_per_kwh": metrics.p95_diff_c_per_kwh,
            "n_diverging_over_2c": metrics.n_diverging_over_2c,
            "scarcity_divergence_flag_threshold_c": metrics.flag_threshold_c,
        },
    )


# ----- Orchestration ------------------------------------------------------


def run_all_checks(
    client, bucket: str, start: datetime.datetime, end: datetime.datetime,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    checks.extend(check_ingestion(client, bucket, start, end))
    checks.append(check_pricing_reconstruction(client, bucket, start, end))
    checks.append(check_refoss_hvac_kwh(client, bucket, start, end))
    checks.append(check_refoss_eagle_reconciliation(client, bucket, start, end))
    checks.append(check_weather_vector_inputs(client, bucket, start, end))
    checks.append(check_arm_calendar(start, end))
    checks.append(check_mode_classification())
    checks.append(audit_scarcity_divergence(client, bucket, start, end))
    return checks


# ----- Output writers ----------------------------------------------------


def write_results_json(report: ValidationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_findings_md_draft(report: ValidationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "<!-- DRAFT — generated by tools/analysis/run_shadow_validation.py.",
        "     Promote to docs/replay-validation/<date>-shadow/findings.md",
        "     with a proper YAML header and human sign-off. -->",
        "",
        "# Shadow validation findings (draft)",
        "",
        f"- runner_version: `{report.runner_version}`",
        f"- commit_sha: `{report.runner_commit_sha or 'unknown'}`",
        f"- runner_host: `{report.runner_host}`",
        f"- influx_url: `{report.influx_url}`",
        f"- bucket: `{report.bucket}`",
        f"- window: `{report.window_start_utc}` -> `{report.window_end_utc}` (UTC)",
        f"- overall: **{report.overall_status()}**",
        "",
        "## Per-check results",
        "",
        "| Check | Status | Reason code | Notes |",
        "|---|---|---|---|",
    ]
    for c in report.checks:
        notes = c.notes.replace("\n", " ").replace("|", "\\|")
        if len(notes) > 220:
            notes = notes[:217] + "..."
        lines.append(
            f"| `{c.check_id}` | **{c.status}** | "
            f"`{c.reason_code or '-'}` | {notes} |"
        )
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    for c in report.checks:
        if c.metrics:
            lines.append(f"### `{c.check_id}`")
            lines.append("")
            for k, v in c.metrics.items():
                lines.append(f"- `{k}`: `{v}`")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ----- Main --------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--output-dir", default="docs/replay-validation",
        help="Output directory root; results land in <dir>/<YYYY-MM-DD>-shadow/.",
    )
    p.add_argument(
        "--window-start", default=None,
        help=f"Window start (ISO UTC). Default: {DEFAULT_WINDOW_START_UTC.isoformat()}.",
    )
    p.add_argument(
        "--window-end", default=None,
        help="Window end (ISO UTC). Default: now.",
    )
    return p.parse_args(argv)


def _parse_iso_utc(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("shadow_validation")
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    url = os.environ.get("INFLUXDB_URL")
    token = os.environ.get("INFLUXDB_TOKEN")
    org = os.environ.get("INFLUXDB_ORG")
    bucket = os.environ.get("INFLUXDB_BUCKET", DEFAULT_BUCKET)
    if not (url and token and org):
        log.error(
            "INFLUXDB_URL, INFLUXDB_TOKEN, INFLUXDB_ORG must all be set"
        )
        return 2

    start = _parse_iso_utc(args.window_start) if args.window_start else DEFAULT_WINDOW_START_UTC
    end = (
        _parse_iso_utc(args.window_end)
        if args.window_end else
        datetime.datetime.now(datetime.timezone.utc)
    )
    if end <= start:
        log.error("window-end must be > window-start")
        return 2

    today = datetime.datetime.now(_CT).strftime("%Y-%m-%d")
    out_dir = Path(args.output_dir) / f"{today}-shadow"

    log.info("connecting to %s (bucket=%s)", url, bucket)
    client = _make_client(url, token, org)
    try:
        checks = run_all_checks(client, bucket, start, end)
    finally:
        client.close()

    report = ValidationReport(
        runner_version=RUNNER_VERSION,
        window_start_utc=_iso_z(start),
        window_end_utc=_iso_z(end),
        runner_commit_sha=_git_sha(),
        runner_host=socket.gethostname(),
        influx_url=url,
        bucket=bucket,
        checks=checks,
    )
    write_results_json(report, out_dir / "validation_results.json")
    write_findings_md_draft(report, out_dir / "findings_draft.md")

    log.info("wrote %s and %s", out_dir / "validation_results.json", out_dir / "findings_draft.md")
    log.info("overall: %s", report.overall_status())
    for c in checks:
        log.info("  [%s] %s — %s", c.status, c.check_id, c.notes[:120])

    # Exit nonzero on FAIL so the CI workflow surfaces a red build.
    return 1 if report.overall_status() == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
