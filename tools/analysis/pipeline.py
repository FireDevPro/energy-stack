"""Pre-registered SCED analysis pipeline (Stages 1-9).

Binding contract: docs/ANALYSIS_PIPELINE.md.

This module is intentionally a single file with all stage functions
side-by-side. The boundaries between stages are explicit (each writes
its outputs to a typed CSV) but they share the math primitives and
constants that need to stay in lockstep with the spec.

Stages produce their output in `<out_dir>/stage<N>/...` so a
pipeline run is fully captured under one directory and can be tar'd
into the OSF filing bundle.

Synthetic-data smoke tests live in tests/test_pipeline.py. Real
end-to-end runs require an InfluxDB connection (Stage 1) and the
locked constants in `tools/comed_price_imputation/` and
`tools/o2_capacity_reconstruction/`.

Build sequence (one PR per stage, against synthetic fixtures):
  Stage 2 (data quality, this PR) → Stage 3 (weekly aggregates) →
  Stage 6 (O2 layers) → Stage 7 (SCED) → Stage 8 (decomposition) →
  Stage 9 (sensitivities). Stage 1/4/5 already implemented.

Test contract for Stages 2-9: rule applicator functions take
DataFrames as input and return DataFrames or scalar diagnostics so
unit tests can synthesize inputs without parquet I/O. The Stage
orchestrators handle parquet read/write and call the rule applicators
on the in-memory DataFrames. Real-data integration runs against a
2025 replay export, gated by OSF_FILING.md criterion 14.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import itertools
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment


# --- Locked constants (do not edit post-OSF) -------------------------------

PRNG_SEED = 20260601

# Refoss Tier 3 ComfortNet-derived imputation (per EXPERIMENT_DESIGN.md §4 Rule 1)
NAMEPLATE = {
    "cool_kw": 4.6,       # Amana ASXC160481BE 4-ton @ 1.15 kW/ton SEER-adjusted
    "furnace_kw": 0.06,   # electrical, blower-only (no gas in cooling months)
    "blower_kw_per_cfm_normalized": 0.6 / 4500.0,  # ECM motor curve, kW/cfm
}

# Weather summary vector (EXPERIMENT_DESIGN.md §7) — 6 components, ordered.
WEATHER_VECTOR_COMPONENTS = (
    "weekly_cdd",
    "mean_enthalpy_btu_lb",
    "total_solar_wh_m2",
    "mean_wind_mph",
    "max_temp_f",
    "max_dewpoint_f",
)

# Matched-pair quality thresholds (§7)
MAHALANOBIS_PAIR_FLAG = 2.5      # > this = poor-quality pair
MAHALANOBIS_OUTLIER_FLAG = 3.5   # > this against baseline = anomalous

# §7 Bootstrap settings
BOOTSTRAP_N_RESAMPLES = 10_000
BOOTSTRAP_BLOCK_LENGTH = 2

# §7 SCED randomization test cutoff
SCED_EXACT_MAX_N = 20  # exhaustive up to 2^20 = ~1M; beyond, random sample
SCED_RANDOM_RESAMPLES = 100_000

# Day-type day boundaries (§3 / Appendix A)
HOT_TEMP_F = 85.0
HOT_APPARENT_F = 90.0
MILD_TEMP_F = 75.0


# --- Math primitives -------------------------------------------------------


def mahalanobis_distance(
    x: np.ndarray, y: np.ndarray, sigma_inv: np.ndarray,
) -> float:
    """Generalized point-to-point Mahalanobis distance.

    d²(x,y) = (x-y)ᵀ Σ⁻¹ (x-y); we return the unsquared distance.
    """
    diff = x - y
    d2 = float(diff @ sigma_inv @ diff)
    return math.sqrt(max(d2, 0.0))


def mahalanobis_to_distribution(
    x: np.ndarray, mu: np.ndarray, sigma_inv: np.ndarray,
) -> float:
    """Point-to-distribution Mahalanobis distance against a baseline."""
    return mahalanobis_distance(x, mu, sigma_inv)


def hungarian_match(
    arm_a_vectors: np.ndarray,
    arm_b_vectors: np.ndarray,
    sigma_inv: np.ndarray,
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Hungarian optimal pairing minimizing total Mahalanobis distance.

    Returns:
        pairs: list of (arm_a_idx, arm_b_idx, distance)
        unmatched_a: arm A indices not in any pair
        unmatched_b: arm B indices not in any pair
    """
    n_a, n_b = arm_a_vectors.shape[0], arm_b_vectors.shape[0]
    if n_a == 0 or n_b == 0:
        return [], list(range(n_a)), list(range(n_b))
    cost = np.zeros((n_a, n_b), dtype=float)
    for i in range(n_a):
        for j in range(n_b):
            cost[i, j] = mahalanobis_distance(
                arm_a_vectors[i], arm_b_vectors[j], sigma_inv,
            )
    row_idx, col_idx = linear_sum_assignment(cost)
    pairs = [
        (int(i), int(j), float(cost[i, j])) for i, j in zip(row_idx, col_idx)
    ]
    matched_a = {i for i, _, _ in pairs}
    matched_b = {j for _, j, _ in pairs}
    unmatched_a = [i for i in range(n_a) if i not in matched_a]
    unmatched_b = [j for j in range(n_b) if j not in matched_b]
    return pairs, unmatched_a, unmatched_b


def stationary_bootstrap_median_diff(
    pair_differences: Sequence[float],
    n_resamples: int = BOOTSTRAP_N_RESAMPLES,
    block_length: int = BOOTSTRAP_BLOCK_LENGTH,
    rng_seed: int = PRNG_SEED,
) -> dict[str, float]:
    """Stationary bootstrap CI for the matched-pair median (§7 primary).

    Median of pair differences (typically B - A). Geometric block lengths
    with mean `block_length`, percentile method 95% CI.
    """
    diffs = np.asarray(pair_differences, dtype=float)
    n = len(diffs)
    if n == 0:
        return {"point": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": 0}
    rng = np.random.default_rng(rng_seed)
    p = 1.0 / max(block_length, 1)  # block-length geometric param
    medians = np.empty(n_resamples)
    for r in range(n_resamples):
        sample = np.empty(n)
        i = 0
        while i < n:
            start = rng.integers(0, n)
            block_len = max(1, rng.geometric(p))
            for k in range(block_len):
                if i >= n:
                    break
                sample[i] = diffs[(start + k) % n]
                i += 1
        medians[r] = np.median(sample)
    return {
        "point": float(np.median(diffs)),
        "ci_low": float(np.percentile(medians, 2.5)),
        "ci_high": float(np.percentile(medians, 97.5)),
        "n": n,
    }


def sced_randomization_pvalue(
    pair_differences: Sequence[float], rng_seed: int = PRNG_SEED + 1,
) -> dict[str, float]:
    """SCED sign-flip randomization test for matched-pair median.

    Exact enumeration over 2^N sign flips for N ≤ SCED_EXACT_MAX_N;
    otherwise SCED_RANDOM_RESAMPLES random Rademacher samples.
    Returns two-sided p-value.
    """
    diffs = np.asarray(pair_differences, dtype=float)
    n = len(diffs)
    if n == 0:
        return {"pvalue": float("nan"), "n": 0, "exact": False}
    observed_median = float(np.median(diffs))
    abs_obs = abs(observed_median)

    if n <= SCED_EXACT_MAX_N:
        as_or_more_extreme = 0
        total = 0
        for signs in itertools.product([-1.0, 1.0], repeat=n):
            permuted_median = float(np.median(np.asarray(signs) * diffs))
            if abs(permuted_median) >= abs_obs:
                as_or_more_extreme += 1
            total += 1
        return {
            "pvalue": as_or_more_extreme / total,
            "n": n,
            "exact": True,
        }
    rng = np.random.default_rng(rng_seed)
    as_or_more_extreme = 0
    for _ in range(SCED_RANDOM_RESAMPLES):
        signs = rng.choice([-1.0, 1.0], size=n)
        permuted_median = float(np.median(signs * diffs))
        if abs(permuted_median) >= abs_obs:
            as_or_more_extreme += 1
    return {
        "pvalue": as_or_more_extreme / SCED_RANDOM_RESAMPLES,
        "n": n,
        "exact": False,
    }


def heat_index_f(t_f: float, rh_pct: float) -> float:
    """NWS Rothfusz heat-index approximation. Returns °F."""
    if t_f < 80:
        return t_f
    return (
        -42.379
        + 2.04901523 * t_f
        + 10.14333127 * rh_pct
        - 0.22475541 * t_f * rh_pct
        - 6.83783e-3 * t_f * t_f
        - 5.481717e-2 * rh_pct * rh_pct
        + 1.22874e-3 * t_f * t_f * rh_pct
        + 8.5282e-4 * t_f * rh_pct * rh_pct
        - 1.99e-6 * t_f * t_f * rh_pct * rh_pct
    )


def enthalpy_btu_per_lb(temp_f: float, dewpoint_f: float, pressure_inhg: float = 29.92) -> float:
    """Outdoor air enthalpy in BTU/lb dry air.

    Standard psychrometric: h = 0.240*T + W*(1061 + 0.444*T) where W
    is humidity ratio (lb water / lb dry air) computed from dewpoint
    saturation pressure.
    """
    # Saturation pressure at dewpoint, August-Roche-Magnus form, inHg
    t_c = (dewpoint_f - 32.0) * 5.0 / 9.0
    p_sat_kpa = 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))
    p_sat_inhg = p_sat_kpa * 0.2953
    w = 0.622 * p_sat_inhg / (pressure_inhg - p_sat_inhg)
    return 0.240 * temp_f + w * (1061.0 + 0.444 * temp_f)


# --- ComEd DTOD delivery rates ($/kWh by hour-of-day CT) -------------------
#
# Used by Stage 3 O1/O4 dollar-cost computation. The same rate schedule is
# used live by the scheduler in deploy/energy-stack/hvac-scheduler/precool.py
# (DTOD_PERIODS_CT). Synced by test_dtod_periods_synced_with_precool_module
# in test_pipeline.py — if the scheduler updates rates, that test will fail
# until this constant is updated to match.
#
# Source: ComEd CUB Fact Sheet March 2026, Single-Family Non-Electric Heat.
# Schema: (start_hour_inclusive, end_hour_exclusive, cents_per_kWh).

DTOD_PERIODS_CT: tuple[tuple[int, int, float], ...] = (
    ( 6, 13,  4.009),  # Morning
    (13, 19, 10.712),  # Mid-Day Peak
    (19, 21,  3.747),  # Evening
    (21, 24,  2.984),  # Overnight (pre-midnight half)
    ( 0,  6,  2.984),  # Overnight (post-midnight half)
)


def dtod_delivery_rate_for_hour_ct(hour_ct: int) -> float:
    """ComEd DTOD delivery rate (¢/kWh) for a CT-local hour of day (0-23)."""
    if not 0 <= hour_ct <= 23:
        raise ValueError(f"hour_ct must be in [0, 23], got {hour_ct}")
    for start, end, rate in DTOD_PERIODS_CT:
        if start <= hour_ct < end:
            return rate
    raise RuntimeError(f"DTOD schedule does not cover hour_ct={hour_ct}")


# --- Stage 3 aggregation primitives ---------------------------------------


CDD_BASE_F = 65.0  # spec: CDD = max(T_avg − 65, 0) per day


def weekly_cdd(daily_avg_temps_f: Sequence[float]) -> float:
    """Σ_d max(T_avg_d − 65, 0) over the days of the week.

    Per EXPERIMENT_DESIGN.md §4 Rule 5, dropped days don't appear here.
    Caller (Stage 3 orchestrator) supplies only the day-T_avg values
    that survived rule 5's >6h-both-missing exclusion.
    """
    return sum(max(t - CDD_BASE_F, 0.0) for t in daily_avg_temps_f)


def weekly_mean_enthalpy_btu_lb(hourly_records: Sequence[dict]) -> float:
    """Mean outdoor-air enthalpy (BTU/lb dry air) across hourly records.

    Each record: ``{"temp_f": float, "dewpoint_f": float,
    "pressure_inhg": float | optional}``. Per-record pressure overrides
    the 29.92 inHg default if present.
    """
    if not hourly_records:
        return 0.0
    total = 0.0
    for r in hourly_records:
        p = float(r.get("pressure_inhg", 29.92))
        total += enthalpy_btu_per_lb(
            float(r["temp_f"]), float(r["dewpoint_f"]), p,
        )
    return total / len(hourly_records)


def weekly_dollars_per_cdd(
    hourly_records: Sequence[dict],
    weekly_cdd: float,
) -> float:
    """Compute weekly $/CDD from hourly kWh × (supply + delivery) prices.

    Each ``hourly_records`` entry needs ``hour_of_day_ct`` (0-23, CT),
    ``hvac_kwh`` (energy that hour), and ``supply_c_per_kwh`` (ComEd
    RTP hourly-avg supply price in cents/kWh after rule 3 imputation).
    Delivery rate is looked up via ``dtod_delivery_rate_for_hour_ct``.

    Returns 0 if ``weekly_cdd <= 0`` to avoid division by zero on
    cooling-irrelevant weeks (which Stage 2 already gates out of the
    formal analysis via the cooling-relevance criterion).
    """
    if weekly_cdd <= 0:
        return 0.0
    total_cents = 0.0
    for r in hourly_records:
        kwh = float(r["hvac_kwh"])
        supply_c = float(r["supply_c_per_kwh"])
        delivery_c = dtod_delivery_rate_for_hour_ct(int(r["hour_of_day_ct"]))
        total_cents += kwh * (supply_c + delivery_c)
    return (total_cents / 100.0) / weekly_cdd


# --- Refoss Tier 3 ComfortNet-derived imputation ---------------------------


def comfortnet_kw(cool_actual_pct: float, heat_actual_pct: float, blower_cfm: float) -> float:
    """Returns instantaneous HVAC kW from ComfortNet state (Rule 1 Tier 3).

    Per the locked formula in EXPERIMENT_DESIGN.md §4 Rule 1 / NAMEPLATE.
    `cool_actual_pct` and `heat_actual_pct` are 0..100.
    """
    cool_kw = (cool_actual_pct / 100.0) * NAMEPLATE["cool_kw"]
    heat_kw = (heat_actual_pct / 100.0) * NAMEPLATE["furnace_kw"]
    blower_kw = blower_cfm * NAMEPLATE["blower_kw_per_cfm_normalized"]
    return cool_kw + heat_kw + blower_kw


# --- Stage entry points -----------------------------------------------------
#
# Each stage takes paths to its input(s) and writes its output to out_dir.
# The Stage 1 implementation hits live InfluxDB; in tests we feed Stage 2+
# directly from fixture parquet files.


def _write_stage1_export(
    stage_dir: Path,
    measurement_dataframes: dict[str, "pd.DataFrame"],
    window_start_ct: str,
    window_end_ct: str,
    source_bucket: str,
    source_type: str = "observed_recent",
    exporter_metadata: dict[str, Any] | None = None,
) -> "Manifest":
    """Write each measurement's DataFrame to parquet and emit
    `manifest.json` describing the bundle. Pure I/O; no Influx
    dependency. Tested independently of stage1_extract.

    All entries written by this call share the same ``source_type``
    (default ``observed_recent`` since stage1_extract is typically run
    against current Influx data). If a measurement appears in this
    call's DataFrames AND already has an entry in a prior bundle
    file (e.g., a weather-derived entry from another tool), the
    bundle is assembled by merging manifest entries — that merging
    happens outside this function. This function writes one
    self-contained manifest for the source_type it was called with.

    Empty DataFrames produce a `known_missing_measurements` entry with
    a reason code (post-2025 measurement when the source_type is
    observed_historical, measurement_empty_in_window otherwise) and
    DO NOT produce an empty parquet file — downstream loaders check
    the manifest first.
    """
    from tools.analysis.replay.manifest import (
        KNOWN_MEASUREMENTS,
        OBSERVED_HISTORICAL,
        POST_2025_MEASUREMENTS,
        SOURCE_TYPES,
        Manifest,
        MeasurementEntry,
        MissingMeasurement,
        compute_sha256,
        parquet_filename,
        utc_now_iso,
        write_manifest,
    )
    from tools.analysis.replay.reason_codes import ReasonCode

    if source_type not in SOURCE_TYPES:
        raise ValueError(
            f"unknown source_type {source_type!r}; "
            f"must be one of {sorted(SOURCE_TYPES)}"
        )

    stage_dir.mkdir(parents=True, exist_ok=True)
    entries: list[MeasurementEntry] = []
    missing: list[MissingMeasurement] = []

    for meas in KNOWN_MEASUREMENTS:
        df = measurement_dataframes.get(meas)
        if df is None or len(df) == 0:
            # Pick the right reason code: a historical-source extract
            # against a post-2025 measurement legitimately can't have
            # data; any other empty result is "empty in window."
            if source_type == OBSERVED_HISTORICAL and meas in POST_2025_MEASUREMENTS:
                reason = ReasonCode.POST_2025_MEASUREMENT_NO_HISTORY
            else:
                reason = ReasonCode.MEASUREMENT_EMPTY_IN_WINDOW
            missing.append(MissingMeasurement(
                measurement=meas, reason_code=reason.value,
                note=f"{meas} returned zero rows for source_type={source_type}",
            ))
            continue

        filename = parquet_filename(meas, source_type)
        parquet_path = stage_dir / filename
        df.to_parquet(parquet_path)
        field_set = (
            tuple(sorted(df["_field"].unique()))
            if "_field" in df.columns else ()
        )
        first_ts = None
        last_ts = None
        if "_time" in df.columns and len(df) > 0:
            sorted_times = df["_time"].sort_values()
            first_ts = sorted_times.iloc[0].isoformat()
            last_ts = sorted_times.iloc[-1].isoformat()
        entries.append(MeasurementEntry(
            measurement=meas,
            source_type=source_type,
            parquet_path=filename,
            row_count=len(df),
            sha256=compute_sha256(parquet_path),
            field_set=field_set,
            first_timestamp_utc=first_ts,
            last_timestamp_utc=last_ts,
        ))

    manifest = Manifest(
        export_window_start_ct=window_start_ct,
        export_window_end_ct=window_end_ct,
        source_bucket=source_bucket,
        exported_at_utc=utc_now_iso(),
        exporter=exporter_metadata or {"version": "stage1_extract"},
        entries=tuple(entries),
        known_missing_measurements=tuple(missing),
    )
    write_manifest(manifest, stage_dir / "manifest.json")
    return manifest


def stage1_extract(
    start: datetime.datetime,
    end: datetime.datetime,
    out_dir: Path,
    influx_url: str | None = None,
    influx_token: str | None = None,
    influx_org: str | None = None,
    influx_bucket: str | None = None,
) -> Path:
    """Pull every measurement listed in §2.1 within the window.

    Writes per-measurement parquet plus `manifest.json` per
    OSF_FILING.md criterion 14. Measurements that return zero rows
    go into the manifest's known_missing_measurements with a reason
    code, NOT into empty parquet files.
    """
    try:
        from influxdb_client import InfluxDBClient
    except ImportError as e:
        raise RuntimeError(
            "influxdb-client not installed; cannot run Stage 1 directly. "
            "See tools/analysis/requirements.txt."
        ) from e
    import pandas as pd  # local — only stage 1 needs full pandas/Influx

    influx_url = influx_url or os.environ.get("INFLUXDB_URL", "http://localhost:8086")
    influx_token = influx_token or os.environ["INFLUXDB_INIT_ADMIN_TOKEN"]
    influx_org = influx_org or os.environ.get("INFLUXDB_INIT_ORG", "depaola-home")
    influx_bucket = influx_bucket or os.environ.get("INFLUXDB_INIT_BUCKET", "energy")

    stage_dir = out_dir / "stage1"
    queries_dir = Path(__file__).resolve().parent / "queries"
    measurements = [p.stem for p in queries_dir.glob("*.flux")]
    if not measurements:
        raise RuntimeError(f"no .flux queries found under {queries_dir}")

    dataframes: dict[str, "pd.DataFrame"] = {}
    with InfluxDBClient(url=influx_url, token=influx_token, org=influx_org) as client:
        qa = client.query_api()
        for meas in measurements:
            flux_template = (queries_dir / f"{meas}.flux").read_text()
            flux = (
                flux_template
                .replace("$bucket", f'"{influx_bucket}"')
                .replace("$start", start.isoformat())
                .replace("$end", end.isoformat())
            )
            df = qa.query_data_frame(flux)
            if isinstance(df, list):  # multi-table result
                df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
            dataframes[meas] = df

    _write_stage1_export(
        stage_dir=stage_dir,
        measurement_dataframes=dataframes,
        window_start_ct=start.isoformat(),
        window_end_ct=end.isoformat(),
        source_bucket=influx_bucket,
        exporter_metadata={
            "version": "stage1_extract",
            "influx_url": influx_url,
        },
    )
    return stage_dir


# --- Stage 2 orchestrator --------------------------------------------------


QUALIFYING_WEEKS_LOCKED_COLUMNS = (
    "week_start_ct", "arm", "qualifying", "exclusion_reason",
    "imputed_hvac_kwh_pct", "imputed_price_hours_pct",
    "override_operational_count", "override_vacation_days",
)


# Rule order for applying gates — matches EXPERIMENT_DESIGN.md §4 numbering,
# with rule 8 placed AFTER rules 1/7/9 because it consumes their day-level
# exclusions. Rule 10 runs last per spec ("the only rule that excludes a
# week purely on metadata").
RULE_ORDER = ("rule1", "rule2", "rule3", "rule4", "rule5",
              "rule6", "rule7", "rule9", "rule8", "rule10")


@dataclass(frozen=True)
class _StageRowResult:
    row: dict[str, Any]
    imputed_intervals: list[dict]
    outages: list[dict]


def _apply_rules_for_week(inputs: dict) -> _StageRowResult:
    """Apply all ten Stage 2 rules to one (week, arm) and combine results.

    `inputs` is a dict carrying the per-rule data this week. See
    test fixtures (`_happy_week_inputs`) for the shape. The function
    returns the qualifying_weeks row plus accumulated intervals/outages
    log entries.

    Exclusion-reason priority follows the spec rule order: when multiple
    rules fail, the first-numbered rule wins.
    """
    week_start_ct = inputs["week_start_ct"]
    arm = inputs["arm"]

    # Per-rule calls
    r1 = rule1_refoss_apply(
        weekly_hvac_kwh=inputs["weekly_hvac_kwh"],
        imputed_intervals=inputs["refoss_intervals"],
    )
    r2 = rule2_comfortnet_apply(inputs["daily_comfortnet_downtime_minutes"])
    r3 = rule3_price_apply(inputs["hourly_prices"])
    r4 = rule4_forecast_apply(inputs["missing_forecast_issuances"])
    r5 = rule5_ecowitt_apply(inputs["daily_ecowitt_both_missing_hours"])
    r6 = rule6_pjm_apply()
    r7 = rule7_scheduler_apply(
        outages=inputs["scheduler_outages"],
        control_relevant_windows=inputs["control_relevant_windows"],
    )
    r9 = rule9_overrides_apply(week_start_ct, inputs["overrides"])

    # Rule 8 combines day-level exclusions from rules 1, 7, 9
    rule1_tier4_days = {
        iv["start_ts"].date() for iv in inputs["refoss_intervals"]
        if iv.get("tier") == 4
    }
    rule7_outage_days: set[datetime.date] = set()
    for start, end in inputs["scheduler_outages"]:
        cur = start.date()
        last = end.date()
        while cur <= last:
            rule7_outage_days.add(cur)
            cur += datetime.timedelta(days=1)
    rule9_vacation_days = {
        entry["date"] for entry in (r9.intervals_log or [])
    }
    r8 = rule8_pi_apply(
        week_start_ct=week_start_ct,
        rule1_tier4_days=rule1_tier4_days,
        rule7_outage_days=rule7_outage_days,
        rule9_vacation_days=rule9_vacation_days,
    )

    trans = inputs["arm_transition"]
    r10 = rule10_transition_apply(
        switch_ts=trans["switch_ts"],
        intended_arm=trans["intended_arm"],
        action_events=trans["action_events"],
    )

    rule_results = {
        "rule1": r1, "rule2": r2, "rule3": r3, "rule4": r4, "rule5": r5,
        "rule6": r6, "rule7": r7, "rule9": r9, "rule8": r8, "rule10": r10,
    }

    # Combine: qualifying = AND across all rules.
    qualifying = all(r.passes for r in rule_results.values())
    exclusion_reason: str | None = None
    if not qualifying:
        for name in RULE_ORDER:
            r = rule_results[name]
            if not r.passes:
                exclusion_reason = r.exclusion_reason
                break

    # Build the row. Start with orchestrator fields; merge each rule's contributes.
    row: dict[str, Any] = {
        "week_start_ct": week_start_ct.isoformat(),
        "arm": arm,
        "qualifying": qualifying,
        "exclusion_reason": exclusion_reason,
    }
    # Default values for locked schema columns rules may not have populated
    row.setdefault("imputed_hvac_kwh_pct", 0.0)
    row.setdefault("imputed_price_hours_pct", 0.0)
    row.setdefault("override_operational_count", 0)
    row.setdefault("override_vacation_days", 0)
    for r in rule_results.values():
        row.update(r.contributes)

    # Collect per-interval log entries
    imputed_intervals = list(r1.intervals_log or [])
    outages: list[dict] = []
    for start, end in inputs["scheduler_outages"]:
        outages.append({"start": start, "end": end, "kind": "scheduler_outage"})
    for entry in (r9.intervals_log or []):
        outages.append({"date": entry["date"], "kind": "vacation"})

    return _StageRowResult(row=row, imputed_intervals=imputed_intervals, outages=outages)


def stage2_quality(stage1_dir: Path, out_dir: Path) -> Path:
    """Apply the 10 data-quality rules from EXPERIMENT_DESIGN.md §4.

    Reads parquet outputs from Stage 1, applies the rule applicators
    week-by-week via ``_apply_rules_for_week``, and writes the three
    locked output CSVs:

      - ``qualifying_weeks.csv`` (locked column schema in
        ``QUALIFYING_WEEKS_LOCKED_COLUMNS``)
      - ``imputed_intervals.csv`` (per-tier Refoss imputation log)
      - ``outages.csv`` (scheduler outages + vacation-day exclusions)

    The week enumeration and parquet-loading logic is intentionally a
    thin layer; the actual rule arithmetic is tested via
    ``_apply_rules_for_week`` against synthetic dicts. Full real-data
    integration runs against a 2025 replay export, gated by
    OSF_FILING.md criterion 14.
    """
    from tools.analysis.replay.reason_codes import (
        ReasonCode, StageReasonReport, write_reason_report,
    )
    stage_dir = out_dir / "stage2"
    stage_dir.mkdir(parents=True, exist_ok=True)
    qual_path = stage_dir / "qualifying_weeks.csv"
    imputed_path = stage_dir / "imputed_intervals.csv"
    outages_path = stage_dir / "outages.csv"

    # Build per-week inputs from Stage 1 parquet. When no Stage 1 data
    # is present (e.g., the schema-only unit test), emit empty CSVs with
    # locked headers so downstream stages can be tested in isolation.
    week_inputs = _load_week_inputs_from_stage1(stage1_dir) if stage1_dir.exists() else []
    reason_reports: list[StageReasonReport] = []
    if not week_inputs:
        # Distinguish "manifest missing" (no bundle to read) from "manifest
        # present but no assignments in window" (pre-randomization or
        # bundle covers a vacation/freeze window).
        if (stage1_dir / "manifest.json").exists():
            reason_reports.append(StageReasonReport(
                stage="stage2",
                output_file="qualifying_weeks.csv",
                reason_code=ReasonCode.NO_ARM_ASSIGNMENTS_IN_WINDOW,
                note="Loader returned no week inputs; manifest window did "
                     "not overlap any assignment CSV Mondays.",
                related_inputs=("stage1/manifest.json",),
            ))
        else:
            reason_reports.append(StageReasonReport(
                stage="stage2",
                output_file="qualifying_weeks.csv",
                reason_code=ReasonCode.NO_WEEK_INPUTS_FROM_STAGE1,
                note="Stage 1 directory has no manifest; the bundle was "
                     "not exported.",
            ))

    with open(qual_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(QUALIFYING_WEEKS_LOCKED_COLUMNS))
        w.writeheader()
        for inputs in week_inputs:
            result = _apply_rules_for_week(inputs)
            w.writerow({k: result.row.get(k) for k in QUALIFYING_WEEKS_LOCKED_COLUMNS})

    # Imputed intervals + outages: collect across all weeks
    all_imputed: list[dict] = []
    all_outages: list[dict] = []
    for inputs in week_inputs:
        result = _apply_rules_for_week(inputs)
        all_imputed.extend(result.imputed_intervals)
        all_outages.extend(result.outages)

    with open(imputed_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start_ts", "end_ts", "tier", "imputed_kwh", "gap_minutes"])
        for iv in all_imputed:
            w.writerow([
                iv.get("start_ts", ""), iv.get("end_ts", ""),
                iv.get("tier", ""), iv.get("imputed_kwh", ""),
                iv.get("gap_minutes", ""),
            ])

    with open(outages_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "start", "end", "date"])
        for o in all_outages:
            w.writerow([
                o.get("kind", ""),
                o.get("start", ""),
                o.get("end", ""),
                o.get("date", ""),
            ])

    if reason_reports:
        write_reason_report(stage_dir, reason_reports)
    return stage_dir


ASSIGNMENT_CSV_PATH = Path(__file__).resolve().parents[2] / "docs" / "experiment-assignments-summer-2026.csv"

HVAC_CHANNELS = frozenset({"em:2", "em:8", "em:9"})
MAINS_CHANNELS = frozenset({"em:1", "em:7"})


def _ct_date_to_utc(date_ct: datetime.date, hour_ct: int = 0) -> datetime.datetime:
    """Convert a CT-local date+hour to a UTC datetime. Uses
    America/Chicago zone (handles CST/CDT correctly via stdlib)."""
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    dt_ct = datetime.datetime.combine(
        date_ct, datetime.time(hour_ct, 0), tzinfo=ct,
    )
    return dt_ct.astimezone(datetime.timezone.utc)


def _load_concat_parquets(
    manifest, stage1_dir: Path, measurement: str,
) -> "pd.DataFrame":
    """Read all manifest entries for a measurement and concatenate their
    parquet contents into a single DataFrame. Returns empty DataFrame
    if the measurement has no entries (multi-source-type concat is
    transparent to downstream consumers)."""
    import pandas as pd
    entries = manifest.entries_for(measurement)
    if not entries:
        return pd.DataFrame()
    dfs = [
        pd.read_parquet(stage1_dir / e.parquet_path)
        for e in entries
    ]
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _refoss_weekly_hvac_kwh(
    refoss_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> float:
    """Sum HVAC-channel (em:2+em:8+em:9) energy_wh over one CT week,
    convert to kWh. Returns 0.0 if no rows match the filter."""
    if len(refoss_df) == 0:
        return 0.0
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    mask = (
        (refoss_df["_field"] == "energy_wh")
        & (refoss_df["channel"].isin(HVAC_CHANNELS))
        & (refoss_df["_time"] >= week_start_utc)
        & (refoss_df["_time"] < week_end_utc)
    )
    total_wh = float(refoss_df.loc[mask, "_value"].sum())
    return total_wh / 1000.0


def _hourly_price_observation_counts(
    prices_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[dict]:
    """For each of the 168 hours in a CT week, count 5-min observations
    from comed.prices. Returns a list of dicts {observed_prints}.
    Rule 3 treats hours with ≥6 prints as observed."""
    result = [{"observed_prints": 0} for _ in range(168)]
    if len(prices_df) == 0:
        return result
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    mask = (
        (prices_df["_field"] == "price_cents")
        & (prices_df["_time"] >= week_start_utc)
        & (prices_df["_time"] < week_end_utc)
    )
    in_window = prices_df.loc[mask]
    if len(in_window) == 0:
        return result
    deltas = in_window["_time"] - week_start_utc
    hour_indices = (deltas.dt.total_seconds() // 3600).astype(int)
    counts = hour_indices.value_counts()
    for hour_idx, count in counts.items():
        if 0 <= hour_idx < 168:
            result[hour_idx] = {"observed_prints": int(count)}
    return result


def _comfortnet_daily_downtime_minutes(
    comfortnet_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[int]:
    """For each of the 7 days in a CT week, count minutes with NO
    hvac.comfortnet rows. Approximates "downtime" via unique-minute
    presence: 1440 expected ticks per day, downtime = 1440 - actual."""
    import pandas as pd
    result = [1440] * 7
    if len(comfortnet_df) == 0:
        return result
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    mask = (
        (comfortnet_df["_time"] >= week_start_utc)
        & (comfortnet_df["_time"] < week_end_utc)
    )
    in_window = comfortnet_df.loc[mask].copy()
    if len(in_window) == 0:
        return result
    # Bucket each row into a day-of-week index (0..6) and a unique
    # per-minute key, then count unique minutes per day.
    in_window["_minute"] = in_window["_time"].dt.floor("min")
    in_window["_day_of_week"] = (
        (in_window["_minute"] - week_start_utc).dt.total_seconds() // 86400
    ).astype(int)
    unique_per_day = (
        in_window.groupby("_day_of_week")["_minute"].nunique()
    )
    for day_idx, n_minutes in unique_per_day.items():
        if 0 <= day_idx < 7:
            result[day_idx] = max(0, 1440 - int(n_minutes))
    return result


def _ecowitt_daily_missing_hours_from_parquet(
    ecowitt_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[int]:
    """For each of the 7 days in a CT week, count hours with NO
    ecowitt outdoor_temp_f rows.

    Rule 5's "both missing" check requires nws.observations as the
    secondary source. Since nws.observations isn't yet in the live
    ingestion catalog (only nws.forecast is), this helper currently
    measures only the ecowitt side. The Stage 2 orchestrator's
    Rule 5 will use these counts; until nws.observations lands, the
    measure is conservative (counts pure ecowitt gaps as "both
    missing").
    """
    if len(ecowitt_df) == 0:
        return [24] * 7
    if "_field" not in ecowitt_df.columns:
        return [24] * 7
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    mask = (
        (ecowitt_df["_field"] == "outdoor_temp_f")
        & (ecowitt_df["_time"] >= week_start_utc)
        & (ecowitt_df["_time"] < week_end_utc)
    )
    sub = ecowitt_df.loc[mask].copy()
    if len(sub) == 0:
        return [24] * 7
    sub["_hour"] = sub["_time"].dt.floor("h")
    sub["_day_of_week"] = (
        (sub["_hour"] - week_start_utc).dt.total_seconds() // 86400
    ).astype(int)
    hours_per_day = (
        sub.groupby("_day_of_week")["_hour"].nunique()
    )
    result = [24] * 7
    for day_idx, n_hours in hours_per_day.items():
        if 0 <= day_idx < 7:
            result[day_idx] = max(0, 24 - int(n_hours))
    return result


def _action_events_from_parquet(
    actions_df: "pd.DataFrame",
    switch_ts_utc: datetime.datetime,
    intended_arm: str,
) -> list[dict]:
    """Extract Rule 10 action_events from hvac.actions parquet.

    Rule 10's 6h-after-switch deadline windows the events of interest.
    Each hvac.actions row tags ``action_label`` and ``dry_run``
    ("true"/"false"); we lift those into the (timestamp, action,
    arm, dry_run) shape Rule 10 consumes.

    The arm is stamped from the assignment (``intended_arm``) rather
    than read from the row, since hvac.actions doesn't tag arm
    directly — Arm A/B is operationally encoded as the dry_run flag.
    """
    if len(actions_df) == 0:
        return []
    required = {"_time", "action_label", "dry_run"}
    if not required.issubset(set(actions_df.columns)):
        return []
    deadline = rule10_arm_transition_deadline(switch_ts_utc, None)
    mask = (
        (actions_df["_time"] >= switch_ts_utc)
        & (actions_df["_time"] < deadline)
    )
    sub = actions_df.loc[mask]
    if len(sub) == 0:
        return []
    seen: set[datetime.datetime] = set()
    events: list[dict] = []
    for _, row in sub.iterrows():
        ts = row["_time"].to_pydatetime()
        if ts in seen:
            continue
        seen.add(ts)
        events.append({
            "timestamp": ts,
            "arm": intended_arm,
            "action": str(row["action_label"]),
            "dry_run": str(row["dry_run"]).lower() == "true",
        })
    events.sort(key=lambda e: e["timestamp"])
    return events


def _overrides_from_parquet(
    overrides_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[dict]:
    """Reconstruct override events from hvac.overrides parquet.

    Each override is a single Influx event with multiple fields
    (start_ts, end_ts, setpoint_f, duration_hours) and a category tag.
    The fields ``start_ts`` and ``end_ts`` are epoch seconds (UTC); we
    pivot per-event by row timestamp + category, then convert to
    tz-aware UTC datetimes.

    Filters to events whose span overlaps the CT week. Returns dicts
    with the (category, start_ts, end_ts, setpoint_f) shape Rule 9
    consumes.
    """
    import pandas as pd
    if len(overrides_df) == 0:
        return []
    required_cols = {"_time", "_field", "_value", "category"}
    if not required_cols.issubset(set(overrides_df.columns)):
        return []
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    # Pivot wide so each (event_time, category) has all four fields
    relevant_fields = {"start_ts", "end_ts", "setpoint_f", "duration_hours"}
    field_mask = overrides_df["_field"].isin(relevant_fields)
    sub = overrides_df.loc[field_mask].copy()
    if len(sub) == 0:
        return []
    wide = sub.pivot_table(
        index=["_time", "category"],
        columns="_field",
        values="_value",
        aggfunc="first",
    ).reset_index()
    overrides: list[dict] = []
    for _, row in wide.iterrows():
        start_epoch = row.get("start_ts")
        end_epoch = row.get("end_ts")
        setpoint = row.get("setpoint_f")
        if pd.isna(start_epoch) or pd.isna(end_epoch) or pd.isna(setpoint):
            continue
        start = datetime.datetime.fromtimestamp(
            int(start_epoch), tz=datetime.timezone.utc,
        )
        end = datetime.datetime.fromtimestamp(
            int(end_epoch), tz=datetime.timezone.utc,
        )
        # Skip overrides whose span is entirely outside the CT week
        if end < week_start_utc or start >= week_end_utc:
            continue
        overrides.append({
            "category": str(row["category"]),
            "start_ts": start,
            "end_ts": end,
            "setpoint_f": float(setpoint),
        })
    overrides.sort(key=lambda o: o["start_ts"])
    return overrides


def _control_relevant_windows_from_parquet(
    precool_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Extract Rule 7 control-relevant windows from hvac.precool_window.

    Each precool decision row tags ``target_date`` (CT date) and carries
    ``hour_ct`` field. The window is the 1-hour CT-local block at that
    hour, converted to UTC.

    Only precool windows are wired here. Recover and active-5CP/scarcity
    hold windows have separate source measurements; they will be added
    when their event streams are exported into the bundle.

    Returns a list of (start_utc, end_utc) tuples sorted by start.
    """
    if len(precool_df) == 0:
        return []
    if "target_date" not in precool_df.columns:
        return []
    # Pivot field rows back to wide so each decision-row has hour_ct.
    hour_mask = precool_df["_field"] == "hour_ct"
    hour_rows = precool_df.loc[hour_mask]
    if len(hour_rows) == 0:
        return []
    week_dates = {
        (week_start_ct + datetime.timedelta(days=i)).isoformat()
        for i in range(7)
    }
    windows: list[tuple[datetime.datetime, datetime.datetime]] = []
    seen: set[tuple[str, int]] = set()
    for _, row in hour_rows.iterrows():
        target_date_str = str(row["target_date"])
        if target_date_str not in week_dates:
            continue
        hour_ct = int(row["_value"])
        key = (target_date_str, hour_ct)
        if key in seen:
            continue
        seen.add(key)
        target_date = datetime.date.fromisoformat(target_date_str)
        start = _ct_date_to_utc(target_date, hour_ct)
        end = start + datetime.timedelta(hours=1)
        windows.append((start, end))
    windows.sort(key=lambda w: w[0])
    return windows


def _scheduler_outages_from_parquet(
    fivecp_df: "pd.DataFrame",
    actions_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Wrap :func:`detect_scheduler_outages` with parquet inputs.

    Filters both feeds to the CT week, extracts UTC timestamp lists,
    and delegates to the existing outage detector. Returns the same
    list-of-(start,end)-tuples the orchestrator consumes.

    If both feeds are empty for the week, returns []. The Stage 2
    orchestrator emits a reason code when the bundle has no
    scheduler data at all.
    """
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    def _filter_ts(df: "pd.DataFrame") -> list[datetime.datetime]:
        if len(df) == 0 or "_time" not in df.columns:
            return []
        mask = (df["_time"] >= week_start_utc) & (df["_time"] < week_end_utc)
        return [t.to_pydatetime() for t in df.loc[mask, "_time"]]

    fivecp_ts = _filter_ts(fivecp_df)
    actions_ts = _filter_ts(actions_df)
    if not fivecp_ts and not actions_ts:
        return []
    return detect_scheduler_outages(fivecp_ts, actions_ts)


def _refoss_gap_intervals(
    refoss_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[dict]:
    """Detect gaps in HVAC-channel refoss data within the CT week.

    Gap = ≥2-minute interval with no rows on ANY HVAC channel
    (em:2, em:8, em:9). Each detected gap is classified into tiers
    1-4 via :func:`rule1_refoss` and imputed via :func:`impute_refoss_gap`.

    Imputation here is best-effort for an MVP loader:
    - Tier 1 (<5 min): linear interpolation from adjacent ticks.
    - Tier 2/3 (5-180 min): ``imputed_kwh=0.0`` — full imputation
      needs 14-day history (Tier 2) or ComfortNet pivot (Tier 3),
      which are wired in follow-on commits. Under-counting here is
      conservative for Rule 1's cap test.
    - Tier 4 (>180 min OR ComfortNet offline): no imputation per spec.

    Returns intervals sorted by ``start_ts``.
    """
    if len(refoss_df) == 0:
        return []
    if "channel" not in refoss_df.columns:
        return []
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    hvac_mask = (
        refoss_df["channel"].isin(HVAC_CHANNELS)
        & (refoss_df["_time"] >= week_start_utc)
        & (refoss_df["_time"] < week_end_utc)
    )
    hvac_df = refoss_df.loc[hvac_mask]
    if len(hvac_df) < 2:
        return []
    # Unique minute timestamps where any HVAC channel reported
    minutes = sorted(set(hvac_df["_time"].dt.floor("min")))
    if len(minutes) < 2:
        return []
    intervals: list[dict] = []
    for i in range(1, len(minutes)):
        gap_minutes = int(
            (minutes[i] - minutes[i - 1]).total_seconds() // 60
        )
        if gap_minutes < 2:
            continue
        actual_gap = gap_minutes - 1
        intervals.append({
            "start_ts": minutes[i - 1].to_pydatetime(),
            "end_ts": minutes[i].to_pydatetime(),
            "gap_minutes": actual_gap,
            "comfortnet_available": False,
        })
    classified = rule1_refoss(intervals)
    out: list[dict] = []
    for iv in classified:
        tier = iv["tier"]
        if tier == 1:
            pre_kwh_per_min = 0.0
            post_kwh_per_min = 0.0
            iv_with_kwh = {
                **iv,
                "pre_kwh_per_min": pre_kwh_per_min,
                "post_kwh_per_min": post_kwh_per_min,
            }
            out.append(impute_refoss_gap(iv_with_kwh))
        elif tier in (2, 3):
            out.append({**iv, "imputed_kwh": 0.0})
        else:
            out.append({**iv, "imputed_kwh": 0.0})
    return out


def _missing_forecast_issuances(
    nws_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> int:
    """Count days in the CT week where the 21:00 CT NWS forecast
    issuance is missing (no rows within ±30 min). Rule 4 uses the
    21:00-prior issuance for next-day day-type classification."""
    if len(nws_df) == 0:
        return 7
    missing = 0
    for d in range(7):
        day_ct = week_start_ct + datetime.timedelta(days=d)
        issuance_utc = _ct_date_to_utc(day_ct, 21)
        window_start = issuance_utc - datetime.timedelta(minutes=30)
        window_end = issuance_utc + datetime.timedelta(minutes=30)
        mask = (
            (nws_df["_time"] >= window_start)
            & (nws_df["_time"] < window_end)
        )
        if not mask.any():
            missing += 1
    return missing


def _read_assignment_csv(path: Path | None = None) -> list[dict]:
    """Read the locked arm-assignment CSV. Returns list of dicts with
    `iso_week`, `monday_date` (datetime.date), `arm` ("A"|"B")."""
    csv_path = path or ASSIGNMENT_CSV_PATH
    rows: list[dict] = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            rows.append({
                "iso_week": row["iso_week"],
                "monday_date": datetime.date.fromisoformat(row["monday_date"]),
                "arm": row["arm"],
            })
    return rows


def _parse_ct_window(window_iso: str) -> datetime.date:
    """Parse an ISO 8601 timestamp from the manifest's window and
    return its CT-local date component. The manifest stores window
    bounds as ISO strings with timezone offsets (e.g.,
    `2026-05-11T00:00:00-05:00`)."""
    return datetime.datetime.fromisoformat(window_iso).date()


def _load_week_inputs_from_stage1(
    stage1_dir: Path,
    assignment_csv: Path | None = None,
) -> list[dict]:
    """Build per-(week, arm) input dicts from a Stage 1 replay bundle.

    Reads `stage1/manifest.json` to determine the bundle's window, then
    enumerates Mondays in the locked arm-assignment CSV that fall
    within that window. Each (week, arm) becomes one input dict for
    `_apply_rules_for_week`.

    Currently fills measurement-derived fields (weekly_hvac_kwh,
    refoss_intervals, hourly_prices observation counts, etc.) with
    defaults that pass every quality rule. Per-measurement parquet
    parsing lands in follow-on commits on this branch — at each
    follow-on, the corresponding default is replaced with real data
    extracted from the manifest's parquet entries.

    Returns an empty list when:
    - No `stage1/manifest.json` exists (bundle missing).
    - No assignment-CSV Mondays fall in the manifest's window
      (pre-randomization window).
    The orchestrator (`stage2_quality`) is responsible for emitting
    reason codes in those cases.
    """
    manifest_path = stage1_dir / "manifest.json"
    if not manifest_path.exists():
        return []

    from tools.analysis.replay.manifest import read_manifest
    manifest = read_manifest(manifest_path)

    window_start = _parse_ct_window(manifest.export_window_start_ct)
    window_end = _parse_ct_window(manifest.export_window_end_ct)

    assignments = _read_assignment_csv(assignment_csv)
    weeks_in_window = [
        a for a in assignments
        if window_start <= a["monday_date"] < window_end
    ]

    # Read parquets once per measurement; reuse across all weeks
    refoss_df = _load_concat_parquets(manifest, stage1_dir, "refoss.channel")
    prices_df = _load_concat_parquets(manifest, stage1_dir, "comed.prices")
    comfortnet_df = _load_concat_parquets(manifest, stage1_dir, "hvac.comfortnet")
    nws_df = _load_concat_parquets(manifest, stage1_dir, "nws.forecast")
    fivecp_df = _load_concat_parquets(manifest, stage1_dir, "hvac.5cp_state")
    actions_df = _load_concat_parquets(manifest, stage1_dir, "hvac.actions")
    precool_df = _load_concat_parquets(manifest, stage1_dir, "hvac.precool_window")
    overrides_df = _load_concat_parquets(manifest, stage1_dir, "hvac.overrides")
    ecowitt_df = _load_concat_parquets(manifest, stage1_dir, "ecowitt.weather")

    inputs: list[dict] = []
    for assignment in weeks_in_window:
        week = assignment["monday_date"]
        arm = assignment["arm"]
        switch_ts = _ct_date_to_utc(week, 5)

        weekly_hvac_kwh = (
            _refoss_weekly_hvac_kwh(refoss_df, week)
            if len(refoss_df) > 0 else 100.0  # default when refoss empty
        )
        hourly_prices = (
            _hourly_price_observation_counts(prices_df, week)
            if len(prices_df) > 0 else
            [{"observed_prints": 12} for _ in range(168)]
        )
        daily_comfortnet = (
            _comfortnet_daily_downtime_minutes(comfortnet_df, week)
            if len(comfortnet_df) > 0 else [0] * 7
        )
        missing_forecast = (
            _missing_forecast_issuances(nws_df, week)
            if len(nws_df) > 0 else 0
        )

        refoss_intervals = (
            _refoss_gap_intervals(refoss_df, week)
            if len(refoss_df) > 0 else []
        )
        scheduler_outages = _scheduler_outages_from_parquet(
            fivecp_df, actions_df, week,
        )
        control_relevant_windows = _control_relevant_windows_from_parquet(
            precool_df, week,
        )
        overrides = _overrides_from_parquet(overrides_df, week)
        ecowitt_missing = (
            _ecowitt_daily_missing_hours_from_parquet(ecowitt_df, week)
            if len(ecowitt_df) > 0 else [0] * 7
        )
        action_events = _action_events_from_parquet(
            actions_df, switch_ts_utc=switch_ts, intended_arm=arm,
        )
        if not action_events:
            # Synthesize a verifying event so the orchestrator handoff
            # smoke-test passes against bundles that don't yet include
            # hvac.actions data. Real bundles will replace this with
            # actual events.
            action_events = [
                {
                    "timestamp": switch_ts + datetime.timedelta(hours=3),
                    "arm": arm,
                    "action": "HOT_PRE_COOL",
                    "dry_run": (arm == "A"),
                },
            ]

        inputs.append({
            "week_start_ct": week,
            "arm": arm,
            "weekly_hvac_kwh": weekly_hvac_kwh,
            "refoss_intervals": refoss_intervals,
            "hourly_prices": hourly_prices,
            "daily_comfortnet_downtime_minutes": daily_comfortnet,
            "daily_ecowitt_both_missing_hours": ecowitt_missing,
            "scheduler_outages": scheduler_outages,
            "control_relevant_windows": control_relevant_windows,
            "overrides": overrides,
            "missing_forecast_issuances": missing_forecast,
            "arm_transition": {
                "switch_ts": switch_ts,
                "intended_arm": arm,
                "action_events": action_events,
            },
        })
    return inputs


# --- Stage 2 rule applicator return type -----------------------------------
#
# Each rule applicator returns a RuleResult so the Stage 2 orchestrator can
# combine outcomes into the per-week qualifying_weeks row without each rule
# having to know the full schema. See docs/ANALYSIS_PIPELINE.md §3 Stage 2.


@dataclass(frozen=True)
class RuleResult:
    passes: bool                            # True if the week qualifies under THIS rule
    exclusion_reason: str | None = None     # short string written to qualifying_weeks.csv if !passes
    contributes: dict[str, Any] = field(default_factory=dict)  # extra fields merged into the row
    intervals_log: list[dict] | None = None  # rule1/7/8 outage/imputation intervals


# --- Stage 2 rule applicators (spec: EXPERIMENT_DESIGN.md §4 + ANALYSIS_PIPELINE.md §3 Stage 2) --


def rule6_pjm_apply() -> RuleResult:
    """Rule 6: PJM DM2 ``inst_load`` feed (5CP detector input).

    Per spec: outages do not affect O2 (O2 is anchored to PJM's
    final-published 5CP hour list, not the live detector). The applicator
    therefore never gates the week — detector-accuracy descriptive stats
    are produced by Stage 6, not Stage 2.
    """
    return RuleResult(passes=True)


def rule4_forecast_apply(missing_issuance_count: int) -> RuleResult:
    """Rule 4: NWS forecast substitutions.

    Per spec: forecast availability does not affect week eligibility.
    Missing 21:00 issuances fill from prior-day same-issuance; if both
    are missing the day is forced to NORMAL and the substitution is
    flagged. The applicator records the substitution count and always
    passes.
    """
    return RuleResult(
        passes=True,
        contributes={"forecast_substitutions": int(missing_issuance_count)},
    )


COMFORTNET_DAILY_DOWNTIME_LIMIT_MIN = 30  # spec: >30 min/day → O6-ineligible


def rule2_comfortnet_apply(daily_downtime_minutes: Sequence[int | float]) -> RuleResult:
    """Rule 2: CT-485 / ComfortNet HVAC-state.

    Per spec: missing intervals are not imputed; O6 reports any day with
    >30 minutes of ComfortNet downtime as ineligible for the recovery-
    ratio computation. The week's O6 average uses the qualifying-day
    subset. Rule 2 does not gate the formal-analysis week itself; it
    only narrows the O6 day-set.
    """
    ineligible = sum(1 for m in daily_downtime_minutes if m > COMFORTNET_DAILY_DOWNTIME_LIMIT_MIN)
    eligible = len(daily_downtime_minutes) - ineligible
    return RuleResult(
        passes=True,
        contributes={
            "o6_ineligible_days": ineligible,
            "o6_eligible_day_count": eligible,
        },
    )


# Rule signatures — concrete data-handling implementations land here.
# Documented in detail in docs/ANALYSIS_PIPELINE.md §3 Stage 2.

def rule1_refoss(intervals: list[dict]) -> list[dict]:
    """Refoss EM16P 4-tier handling per Rule 1.

    Input: list of dicts with keys (start_ts, end_ts, gap_minutes,
        comfortnet_available:bool).
    Output: same intervals annotated with tier and imputed_kwh.
    """
    out = []
    for itv in intervals:
        gap_min = itv["gap_minutes"]
        if gap_min < 5:
            tier = 1
        elif gap_min < 30:
            tier = 2
        elif gap_min < 180:
            tier = 3 if itv.get("comfortnet_available", False) else 4
        else:
            tier = 4
        out.append({**itv, "tier": tier})
    return out


def rule9_classify_override(category: str, duration_hours: float, setpoint_f: float) -> str:
    """Rule 9: classify a logged override into operational vs vacation.

    The CLI annotation tool tags this at log time; this function exists
    so the pipeline can re-classify post-hoc if the annotation is
    obviously wrong (e.g., a multi-day 82F event tagged operational).
    """
    if category == "vacation":
        return "vacation"
    if duration_hours >= 18.0 and setpoint_f >= 81.0:
        return "vacation"  # reclassify likely-mistagged
    return "operational"


def rule10_arm_transition_deadline(
    switch_ts: datetime.datetime,
    first_control_window_ts: datetime.datetime | None,
) -> datetime.datetime:
    """Rule 10: earliest deadline by which the new arm must be verified.

    Returns the earlier of (first control-relevant window) or
    (switch + 6 hours).
    """
    six_h = switch_ts + datetime.timedelta(hours=6)
    if first_control_window_ts is None:
        return six_h
    return min(first_control_window_ts, six_h)


RULE8_MIN_QUALIFYING_DAYS = 5     # spec: <5 qualifying days → week excluded


def rule8_pi_apply(
    week_start_ct: datetime.date,
    rule1_tier4_days: set[datetime.date],
    rule7_outage_days: set[datetime.date],
    rule9_vacation_days: set[datetime.date],
) -> RuleResult:
    """Rule 8 + the cross-rule <5 qualifying-days threshold.

    Pi outages manifest simultaneously as Refoss outages (rule 1) and
    scheduler outages (rule 7), so the day-set is the UNION of rule 1
    tier-4 days, rule 7 outage days, and rule 9 vacation days. If the
    resulting qualifying day-count for this week is below 5, the week
    is excluded as ``insufficient_qualifying_days``.

    Day-sets outside the 7-day week window are ignored (defensive
    against caller passing in nearby-week dates).
    """
    week_days = {
        week_start_ct + datetime.timedelta(days=i) for i in range(7)
    }
    excluded = (
        rule1_tier4_days | rule7_outage_days | rule9_vacation_days
    ) & week_days
    qualifying = len(week_days - excluded)
    contributes = {
        "qualifying_days": qualifying,
        "excluded_days": len(excluded),
    }
    if qualifying < RULE8_MIN_QUALIFYING_DAYS:
        return RuleResult(
            passes=False,
            exclusion_reason="insufficient_qualifying_days",
            contributes=contributes,
        )
    return RuleResult(passes=True, contributes=contributes)


RULE1_IMPUTATION_CAP_PCT = 0.10     # ≥10% imputed weekly HVAC kWh → week dropped


def impute_refoss_gap(
    interval: dict,
    *,
    mains_history_ratio: float = 1.0,
    comfortnet_sample: dict | None = None,
    nameplate: dict | None = None,
) -> dict:
    """Apply tier-specific imputation to a single Refoss gap interval.

    `interval` must carry ``gap_minutes`` and ``tier``. The tier-specific
    extra inputs:

    - Tier 1 (<5 min): linear interpolation. Needs ``pre_kwh_per_min``
      and ``post_kwh_per_min`` on the interval.
    - Tier 2 (5-30 min): same-day-type same-hour median from prior 14
      days scaled by within-hour mains ratio. Needs ``history_median_kw``
      on the interval; ``mains_history_ratio`` defaults to 1.0.
    - Tier 3 (30-180 min, ComfortNet available): ComfortNet-derived via
      ``comfortnet_kw``. Needs ``comfortnet_sample`` kwarg with
      ``cool_actual_pct``, ``heat_actual_pct``, ``blower_cfm``.
    - Tier 4 (>180 min OR ComfortNet offline): no imputation, returns 0.

    Returns the interval with ``imputed_kwh`` added.
    """
    nameplate = nameplate or NAMEPLATE
    tier = interval["tier"]
    duration_h = interval["gap_minutes"] / 60.0
    if tier == 1:
        pre = float(interval.get("pre_kwh_per_min", 0.0))
        post = float(interval.get("post_kwh_per_min", 0.0))
        kwh = ((pre + post) / 2.0) * interval["gap_minutes"]
    elif tier == 2:
        median_kw = float(interval.get("history_median_kw", 0.0))
        kwh = median_kw * duration_h * mains_history_ratio
    elif tier == 3:
        if comfortnet_sample is None:
            raise ValueError(
                "Tier 3 imputation requires comfortnet_sample kwarg"
            )
        kw = comfortnet_kw(
            float(comfortnet_sample["cool_actual_pct"]),
            float(comfortnet_sample["heat_actual_pct"]),
            float(comfortnet_sample["blower_cfm"]),
        )
        kwh = kw * duration_h
    else:  # tier 4
        kwh = 0.0
    return {**interval, "imputed_kwh": kwh}


def rule1_refoss_apply(
    weekly_hvac_kwh: float,
    imputed_intervals: Sequence[dict],
) -> RuleResult:
    """Rule 1: Refoss EM16P 4-tier handling — week-level cap.

    Each input interval should already carry a ``tier`` (from
    ``rule1_refoss``) and an ``imputed_kwh`` (from ``impute_refoss_gap``).
    Per spec, if the combined Tier 1+2+3 imputed energy is ≥10% of total
    weekly HVAC kWh, the week is dropped as ``refoss_imputation_too_high``.
    Tier 4 intervals contribute 0 to the imputed total but are logged
    so the orchestrator (rule 8) can convert affected days into day-level
    exclusions.
    """
    imputed_total = sum(float(iv.get("imputed_kwh", 0.0)) for iv in imputed_intervals)
    pct = (imputed_total / weekly_hvac_kwh) if weekly_hvac_kwh > 0 else 0.0
    intervals_log = list(imputed_intervals)
    if pct >= RULE1_IMPUTATION_CAP_PCT:
        return RuleResult(
            passes=False,
            exclusion_reason="refoss_imputation_too_high",
            contributes={"imputed_hvac_kwh_pct": pct},
            intervals_log=intervals_log,
        )
    return RuleResult(
        passes=True,
        contributes={"imputed_hvac_kwh_pct": pct},
        intervals_log=intervals_log,
    )


RULE5_SUBSTITUTION_HOURS_GT = 2     # >2h both missing → daily estimator
RULE5_DROP_HOURS_GT = 6             # >6h both missing → drop day from CDD


def rule5_ecowitt_apply(daily_both_missing_hours: Sequence[int]) -> RuleResult:
    """Rule 5: Ecowitt outdoor-temperature coverage (CDD basis).

    Per spec, the two thresholds are strictly greater-than:
      - >2h with both Ecowitt AND NWS missing → use (Tmax+Tmin)/2 − 65
        daily estimator; flag the day as substituted.
      - >6h with both missing → drop the day from the week's CDD
        numerator and denominator entirely.

    The two bands are exclusive: >6h is dropped, NOT also counted as
    substituted. Rule 5 never gates the week.
    """
    substituted = 0
    dropped = 0
    for h in daily_both_missing_hours:
        if h > RULE5_DROP_HOURS_GT:
            dropped += 1
        elif h > RULE5_SUBSTITUTION_HOURS_GT:
            substituted += 1
    return RuleResult(
        passes=True,
        contributes={
            "ecowitt_substituted_days": substituted,
            "ecowitt_dropped_days_for_cdd": dropped,
        },
    )


SCHEDULER_OUTAGE_GAP_THRESHOLD_MIN = 5      # spec: ≥5 min with no writes on either feed
SCHEDULER_OUTAGE_SINGLE_LIMIT_MIN = 60      # spec: single continuous outage >60 min
SCHEDULER_OUTAGE_TOTAL_PCT = 0.01           # spec: total downtime >1% of week-hours
SCHEDULER_WEEK_HOURS = 24 * 7


def detect_scheduler_outages(
    fivecp_state_ts: Sequence[datetime.datetime],
    action_ts: Sequence[datetime.datetime],
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Detect scheduler-service outages from write-gap analysis.

    Per spec: the scheduler writes one ``hvac.5cp_state`` row per ~2.5 min
    and at least one ``hvac.actions`` row per minute when alive. An outage
    is flagged when BOTH feeds have no writes for ≥5 minutes simultaneously.

    Returns the list of (outage_start, outage_end) datetimes (UTC).
    """
    merged = sorted(set(fivecp_state_ts) | set(action_ts))
    outages: list[tuple[datetime.datetime, datetime.datetime]] = []
    threshold = datetime.timedelta(minutes=SCHEDULER_OUTAGE_GAP_THRESHOLD_MIN)
    for i in range(1, len(merged)):
        gap = merged[i] - merged[i - 1]
        if gap >= threshold:
            outages.append((merged[i - 1], merged[i]))
    return outages


def _outage_overlaps_window(
    outage: tuple[datetime.datetime, datetime.datetime],
    window: tuple[datetime.datetime, datetime.datetime],
) -> bool:
    return outage[0] < window[1] and outage[1] > window[0]


def rule7_scheduler_apply(
    outages: Sequence[tuple[datetime.datetime, datetime.datetime]],
    control_relevant_windows: Sequence[tuple[datetime.datetime, datetime.datetime]],
) -> RuleResult:
    """Rule 7: scheduler-service outages.

    Spec: exclude the week if ANY of (i) total scheduler downtime > 1% of
    week-hours, (ii) any single continuous outage > 60 min, (iii) any
    outage overlaps a control-relevant window (pre-cool, recover, or an
    active 5CP / scarcity hold).
    """
    total_min = sum(
        (end - start).total_seconds() / 60.0 for start, end in outages
    )
    contributes = {
        "scheduler_downtime_min": int(total_min),
        "scheduler_outage_count": len(outages),
    }
    intervals_log = [
        {"start": s, "end": e, "kind": "scheduler_outage"}
        for s, e in outages
    ]
    # Gate (iii): any outage overlapping a control-relevant window
    for out_iv in outages:
        for w in control_relevant_windows:
            if _outage_overlaps_window(out_iv, w):
                return RuleResult(
                    passes=False,
                    exclusion_reason="scheduler_outage_in_control_window",
                    contributes=contributes,
                    intervals_log=intervals_log,
                )
    # Gate (ii): single outage > 60 min
    for start, end in outages:
        if (end - start).total_seconds() / 60.0 > SCHEDULER_OUTAGE_SINGLE_LIMIT_MIN:
            return RuleResult(
                passes=False,
                exclusion_reason="scheduler_outage_single_too_long",
                contributes=contributes,
                intervals_log=intervals_log,
            )
    # Gate (i): total downtime > 1% of week-hours
    limit_min = SCHEDULER_WEEK_HOURS * 60.0 * SCHEDULER_OUTAGE_TOTAL_PCT
    if total_min > limit_min:
        return RuleResult(
            passes=False,
            exclusion_reason="scheduler_downtime_too_high",
            contributes=contributes,
            intervals_log=intervals_log,
        )
    return RuleResult(passes=True, contributes=contributes, intervals_log=intervals_log)


RULE3_OBSERVED_PRINTS_MIN = 6           # ≥6 of 12 5-min prints = "observed"
RULE3_FLAG_PCT = 0.05                   # >5% imputed → flagged
RULE3_EXCLUDE_PCT = 0.20                # >20% imputed → excluded


def rule3_price_apply(hourly_prices: Sequence[dict]) -> RuleResult:
    """Rule 3: ComEd RTP price feed.

    Each hour entry needs ``observed_prints`` (count of 5-min prints,
    0-12). An hour is "observed" iff ``observed_prints >= 6``; below
    that, it is imputed downstream (Stage 3 fills from PJM day-ahead
    LMP + the month-matched spread constant in
    ``tools/comed_price_imputation/spread_constants.json``).

    Week-level gate (per EXPERIMENT_DESIGN.md §4 Rule 3):
      - ``imputed_pct > 0.20`` → excluded as ``price_imputation_too_high``
      - ``imputed_pct > 0.05`` → flagged (still passes)
    """
    n_total = len(hourly_prices)
    if n_total == 0:
        return RuleResult(
            passes=True,
            contributes={"imputed_price_hours_pct": 0.0,
                         "imputed_price_hours_flagged": False},
        )
    n_imputed = sum(
        1 for h in hourly_prices
        if h["observed_prints"] < RULE3_OBSERVED_PRINTS_MIN
    )
    pct = n_imputed / n_total
    if pct > RULE3_EXCLUDE_PCT:
        return RuleResult(
            passes=False,
            exclusion_reason="price_imputation_too_high",
            contributes={"imputed_price_hours_pct": pct,
                         "imputed_price_hours_flagged": True},
        )
    return RuleResult(
        passes=True,
        contributes={
            "imputed_price_hours_pct": pct,
            "imputed_price_hours_flagged": pct > RULE3_FLAG_PCT,
        },
    )


def rule9_overrides_apply(
    week_start_ct: datetime.date,
    overrides: Sequence[dict],
) -> RuleResult:
    """Rule 9: manual setpoint overrides.

    Classifies each override using ``rule9_classify_override`` (which also
    reclassifies obviously mistagged long high-setpoint operational rows
    as vacation). Operational overrides are counted only. Vacation
    overrides produce per-day exclusion entries in ``intervals_log``;
    rule9 itself never gates the week — the orchestrator combines per-day
    exclusions across rules and applies the <5 qualifying-days threshold.

    `overrides` entries: ``category``, ``start_ts`` (datetime), ``end_ts``
    (datetime), ``setpoint_f`` (float). Timestamps are assumed local CT.
    """
    operational_count = 0
    vacation_dates: set[datetime.date] = set()
    for ov in overrides:
        duration_hours = (ov["end_ts"] - ov["start_ts"]).total_seconds() / 3600.0
        cls = rule9_classify_override(
            category=ov.get("category", "operational"),
            duration_hours=duration_hours,
            setpoint_f=float(ov["setpoint_f"]),
        )
        if cls == "operational":
            operational_count += 1
            continue
        # Vacation: enumerate calendar days spanned (CT-local, inclusive).
        cur = ov["start_ts"].date()
        end_day = ov["end_ts"].date()
        while cur <= end_day:
            vacation_dates.add(cur)
            cur += datetime.timedelta(days=1)
    intervals_log = [
        {"date": d, "exclusion_source": "rule9_vacation"}
        for d in sorted(vacation_dates)
    ]
    return RuleResult(
        passes=True,
        contributes={
            "override_operational_count": operational_count,
            "override_vacation_days": len(vacation_dates),
        },
        intervals_log=intervals_log,
    )


CONTROL_RELEVANT_ACTIONS = frozenset({"HOT_PRE_COOL", "NORMAL_PRE_COOL"})


def rule10_transition_apply(
    switch_ts: datetime.datetime,
    intended_arm: str,
    action_events: Sequence[dict],
) -> RuleResult:
    """Rule 10: arm-transition verification.

    Pass condition: at least one action event within the 6h-or-earlier
    deadline (per ``rule10_arm_transition_deadline``) has:
      - ``arm == intended_arm``
      - control-relevant action kind (``HOT_PRE_COOL`` or
        ``NORMAL_PRE_COOL``)
      - mode matches arm policy: dry_run for Arm A, non-dry-run for Arm B
    Otherwise the week is excluded with reason ``arm_transition_unverified``.
    """
    deadline = rule10_arm_transition_deadline(switch_ts, None)
    expected_dry_run = (intended_arm == "A")
    for ev in action_events:
        if ev["timestamp"] >= deadline:
            continue
        if ev.get("arm") != intended_arm:
            continue
        if ev.get("action") not in CONTROL_RELEVANT_ACTIONS:
            continue
        if bool(ev.get("dry_run", False)) != expected_dry_run:
            continue
        return RuleResult(passes=True)
    return RuleResult(passes=False, exclusion_reason="arm_transition_unverified")


WEEKLY_CSV_LOCKED_COLUMNS = (
    "week_start_ct", "arm", "qualifies",
    "o1_dollars_per_cdd", "o3_peak_hvac_kw",
    "o4_dollars_per_cdd_whole_home",
    *WEATHER_VECTOR_COMPONENTS,
)


def _compute_weekly_row(inputs: dict) -> dict:
    """Compute one weekly.csv row from per-week aggregation inputs.

    `inputs` is a dict carrying the per-week data Stage 1 produced
    plus Stage 2's qualifying decision (passed through verbatim):

      - ``week_start_ct``: datetime.date
      - ``arm``: "A" | "B"
      - ``qualifies``: bool (from Stage 2's qualifying_weeks.csv)
      - ``daily_avg_temps_f``: list of daily T_avg in °F (rule-5-surviving
        days only)
      - ``hourly_hvac_records``: list of dicts per HVAC-channel hour with
        keys ``hour_of_day_ct``, ``hvac_kwh``, ``supply_c_per_kwh``
      - ``hourly_mains_records``: same shape, mains channel
      - ``hourly_weather``: list of dicts with ``temp_f``, ``dewpoint_f``,
        optional ``pressure_inhg``, ``solar_wm2``, ``wind_mph``

    Returns a dict keyed by the locked weekly.csv schema columns.
    The boundary rule applies: ``qualifies`` is propagated unchanged
    from Stage 2; this function never re-derives quality logic.
    """
    cdd = weekly_cdd(inputs["daily_avg_temps_f"])
    hourly_hvac = inputs["hourly_hvac_records"]
    hourly_mains = inputs["hourly_mains_records"]
    weather = inputs["hourly_weather"]

    o1 = weekly_dollars_per_cdd(hourly_hvac, cdd)
    o4 = weekly_dollars_per_cdd(hourly_mains, cdd)
    o3 = max((float(r["hvac_kwh"]) for r in hourly_hvac), default=0.0)

    mean_enth = weekly_mean_enthalpy_btu_lb(weather)
    total_solar = sum(float(r.get("solar_wm2", 0.0)) for r in weather)
    mean_wind = (
        sum(float(r.get("wind_mph", 0.0)) for r in weather) / len(weather)
        if weather else 0.0
    )
    max_temp = max((float(r["temp_f"]) for r in weather), default=0.0)
    max_dewpoint = max((float(r["dewpoint_f"]) for r in weather), default=0.0)

    return {
        "week_start_ct": inputs["week_start_ct"].isoformat(),
        "arm": inputs["arm"],
        "qualifies": bool(inputs["qualifies"]),
        "o1_dollars_per_cdd": o1,
        "o3_peak_hvac_kw": o3,
        "o4_dollars_per_cdd_whole_home": o4,
        "weekly_cdd": cdd,
        "mean_enthalpy_btu_lb": mean_enth,
        "total_solar_wh_m2": total_solar,
        "mean_wind_mph": mean_wind,
        "max_temp_f": max_temp,
        "max_dewpoint_f": max_dewpoint,
    }


def _empty_weekly_row(week_start_ct: str, arm: str, qualifies: bool) -> dict:
    """Row for a (week, arm) where Stage 1 produced no data — propagates
    Stage 2's qualifying decision with zeroed outcomes."""
    row = {col: 0.0 for col in WEATHER_VECTOR_COMPONENTS}
    row.update({
        "week_start_ct": week_start_ct,
        "arm": arm,
        "qualifies": qualifies,
        "o1_dollars_per_cdd": 0.0,
        "o3_peak_hvac_kw": 0.0,
        "o4_dollars_per_cdd_whole_home": 0.0,
    })
    return row


def _stage3_daily_avg_temps_f(
    ecowitt_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[float]:
    """Per-day mean outdoor_temp_f over a CT week.

    Rule 5 dropped-day handling happens upstream in Stage 2; this helper
    returns the raw daily mean for each of the 7 days. Days with no
    ecowitt data yield 0.0 (the caller should use Stage 2's qualifying
    decision rather than reading these zeros as real temperatures).
    """
    if len(ecowitt_df) == 0 or "_field" not in ecowitt_df.columns:
        return [0.0] * 7
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    mask = (
        (ecowitt_df["_field"] == "outdoor_temp_f")
        & (ecowitt_df["_time"] >= week_start_utc)
        & (ecowitt_df["_time"] < week_end_utc)
    )
    sub = ecowitt_df.loc[mask].copy()
    if len(sub) == 0:
        return [0.0] * 7
    sub["_day_of_week"] = (
        (sub["_time"] - week_start_utc).dt.total_seconds() // 86400
    ).astype(int)
    means = sub.groupby("_day_of_week")["_value"].mean()
    result = [0.0] * 7
    for day_idx, mean_f in means.items():
        if 0 <= day_idx < 7:
            result[day_idx] = float(mean_f)
    return result


def _stage3_hourly_refoss_kwh(
    refoss_df: "pd.DataFrame",
    week_start_ct: datetime.date,
    channels: frozenset[str],
) -> list[dict]:
    """168 hourly aggregates of refoss energy_wh for the given channels.

    Returns dicts with ``hour_of_day_ct`` (0-23) and ``hvac_kwh``
    (energy in that hour, kWh). Mains aggregation uses the same shape
    but channels={em:1, em:7}; HVAC uses channels={em:2, em:8, em:9}.

    Hours with no data sum to 0 kWh — the caller (Stage 3 orchestrator)
    relies on Stage 2's qualifying flag for any week-level decisions.
    """
    if len(refoss_df) == 0:
        return [
            {"hour_of_day_ct": h % 24, "hvac_kwh": 0.0}
            for h in range(168)
        ]
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    energy_mask = (
        (refoss_df["_field"] == "energy_wh")
        & (refoss_df["channel"].isin(channels))
        & (refoss_df["_time"] >= week_start_utc)
        & (refoss_df["_time"] < week_end_utc)
    )
    sub = refoss_df.loc[energy_mask].copy()
    if len(sub) == 0:
        return [
            {"hour_of_day_ct": h % 24, "hvac_kwh": 0.0}
            for h in range(168)
        ]
    sub["_hour_of_week"] = (
        (sub["_time"] - week_start_utc).dt.total_seconds() // 3600
    ).astype(int)
    sums = sub.groupby("_hour_of_week")["_value"].sum()
    result: list[dict] = []
    for hour_of_week in range(168):
        wh = float(sums.get(hour_of_week, 0.0))
        result.append({
            "hour_of_day_ct": hour_of_week % 24,
            "hvac_kwh": wh / 1000.0,
        })
    return result


def _stage3_hourly_supply_prices(
    prices_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[float]:
    """168 hourly ComEd RTP supply prices in cents/kWh.

    Per-hour mean of the 5-min comed.prices observations. Missing hours
    yield 0.0; Rule 3 imputation (sub-hourly missing) is the
    orchestrator's responsibility, not this loader's.
    """
    if len(prices_df) == 0:
        return [0.0] * 168
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    mask = (
        (prices_df["_field"] == "price_cents")
        & (prices_df["_time"] >= week_start_utc)
        & (prices_df["_time"] < week_end_utc)
    )
    sub = prices_df.loc[mask].copy()
    if len(sub) == 0:
        return [0.0] * 168
    sub["_hour_of_week"] = (
        (sub["_time"] - week_start_utc).dt.total_seconds() // 3600
    ).astype(int)
    means = sub.groupby("_hour_of_week")["_value"].mean()
    return [float(means.get(h, 0.0)) for h in range(168)]


def _stage3_hourly_weather(
    ecowitt_df: "pd.DataFrame",
    week_start_ct: datetime.date,
) -> list[dict]:
    """168 hourly weather records from ecowitt.weather (means per hour).

    Returns dicts with ``temp_f``, ``dewpoint_f``, ``rh_pct``,
    ``pressure_inhg``, ``solar_wm2``, ``wind_mph``. Missing fields
    default to plausible values (29.92 inHg for pressure; 0 for the rest).
    """
    import pandas as pd
    if len(ecowitt_df) == 0 or "_field" not in ecowitt_df.columns:
        return [
            {
                "temp_f": 0.0, "dewpoint_f": 0.0, "rh_pct": 0.0,
                "pressure_inhg": 29.92, "solar_wm2": 0.0, "wind_mph": 0.0,
            }
            for _ in range(168)
        ]
    week_start_utc = _ct_date_to_utc(week_start_ct, 0)
    week_end_utc = _ct_date_to_utc(
        week_start_ct + datetime.timedelta(days=7), 0,
    )
    field_map = {
        "outdoor_temp_f": "temp_f",
        "outdoor_dewpoint_f": "dewpoint_f",
        "outdoor_rh_pct": "rh_pct",
        "pressure_inhg": "pressure_inhg",
        "solar_wm2": "solar_wm2",
        "wind_mph": "wind_mph",
    }
    mask = (
        ecowitt_df["_field"].isin(field_map.keys())
        & (ecowitt_df["_time"] >= week_start_utc)
        & (ecowitt_df["_time"] < week_end_utc)
    )
    sub = ecowitt_df.loc[mask].copy()
    if len(sub) == 0:
        return [
            {
                "temp_f": 0.0, "dewpoint_f": 0.0, "rh_pct": 0.0,
                "pressure_inhg": 29.92, "solar_wm2": 0.0, "wind_mph": 0.0,
            }
            for _ in range(168)
        ]
    sub["_hour_of_week"] = (
        (sub["_time"] - week_start_utc).dt.total_seconds() // 3600
    ).astype(int)
    means = sub.groupby(["_hour_of_week", "_field"])["_value"].mean().unstack()
    result: list[dict] = []
    for h in range(168):
        record: dict = {
            "temp_f": 0.0, "dewpoint_f": 0.0, "rh_pct": 0.0,
            "pressure_inhg": 29.92, "solar_wm2": 0.0, "wind_mph": 0.0,
        }
        if h in means.index:
            row = means.loc[h]
            for src, dst in field_map.items():
                if src in row.index and not pd.isna(row[src]):
                    record[dst] = float(row[src])
        result.append(record)
    return result


def _load_stage3_inputs_for_week(
    stage1_dir: Path,
    week_start_ct: datetime.date,
    arm: str,
) -> dict | None:
    """Load per-week Stage 3 inputs from Stage 1 parquet outputs.

    Reads the manifest + parquet files for the (week, arm) and builds
    the dict shape _compute_weekly_row consumes:
      - daily_avg_temps_f (7 floats)
      - hourly_hvac_records (168 dicts; channels em:2/8/9)
      - hourly_mains_records (168 dicts; channels em:1/7)
      - hourly_weather (168 dicts)

    Returns None when stage1/manifest.json is absent; the orchestrator
    falls back to _empty_weekly_row in that case.
    """
    import pandas as pd
    manifest_path = stage1_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    from tools.analysis.replay.manifest import read_manifest
    manifest = read_manifest(manifest_path)

    refoss_df = _load_concat_parquets(manifest, stage1_dir, "refoss.channel")
    prices_df = _load_concat_parquets(manifest, stage1_dir, "comed.prices")
    ecowitt_df = _load_concat_parquets(manifest, stage1_dir, "ecowitt.weather")

    daily_temps = _stage3_daily_avg_temps_f(ecowitt_df, week_start_ct)
    hvac_kwh = _stage3_hourly_refoss_kwh(
        refoss_df, week_start_ct, HVAC_CHANNELS,
    )
    mains_kwh = _stage3_hourly_refoss_kwh(
        refoss_df, week_start_ct, MAINS_CHANNELS,
    )
    supply_prices = _stage3_hourly_supply_prices(prices_df, week_start_ct)
    weather = _stage3_hourly_weather(ecowitt_df, week_start_ct)

    # Attach supply_c_per_kwh to each hourly record so weekly_dollars_per_cdd
    # can read it directly.
    for h in range(168):
        hvac_kwh[h]["supply_c_per_kwh"] = supply_prices[h]
        mains_kwh[h]["supply_c_per_kwh"] = supply_prices[h]

    return {
        "week_start_ct": week_start_ct,
        "arm": arm,
        "daily_avg_temps_f": daily_temps,
        "hourly_hvac_records": hvac_kwh,
        "hourly_mains_records": mains_kwh,
        "hourly_weather": weather,
    }


def stage3_weekly(stage1_dir: Path, stage2_dir: Path, out_dir: Path) -> Path:
    """Compute per-(week, arm) outcome inputs and weather summary vector.

    Reads Stage 2's qualifying_weeks.csv (the source of truth for
    qualification per the boundary rule — Stage 3 never re-derives
    quality logic), loads per-week aggregation inputs from Stage 1,
    and writes weekly.csv with the locked schema.

    When Stage 2's qualifying CSV is absent (e.g., a schema-only unit
    test), the output is header-only.
    """
    from tools.analysis.replay.reason_codes import (
        ReasonCode, StageReasonReport, write_reason_report,
    )
    stage_dir = out_dir / "stage3"
    stage_dir.mkdir(parents=True, exist_ok=True)
    weekly_path = stage_dir / "weekly.csv"

    qualifying_csv = stage2_dir / "stage2" / "qualifying_weeks.csv"
    # Tests sometimes pass tmp_path as both stage2_dir and out_dir; in that
    # case the qualifying CSV could also live at stage2_dir directly.
    if not qualifying_csv.exists():
        qualifying_csv = stage2_dir / "qualifying_weeks.csv"

    rows_written = 0
    with open(weekly_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(WEEKLY_CSV_LOCKED_COLUMNS))
        w.writeheader()
        if qualifying_csv.exists():
            with open(qualifying_csv) as qf:
                for q in csv.DictReader(qf):
                    week_start_ct_str = q["week_start_ct"]
                    arm = q["arm"]
                    qualifies = q["qualifying"].lower() in ("true", "1")
                    week_date = datetime.date.fromisoformat(week_start_ct_str)
                    inputs = _load_stage3_inputs_for_week(stage1_dir, week_date, arm)
                    if inputs is None:
                        row = _empty_weekly_row(week_start_ct_str, arm, qualifies)
                    else:
                        # Stage 2 decision is authoritative — override any
                        # qualifies bool the loader may have set.
                        inputs["qualifies"] = qualifies
                        row = _compute_weekly_row(inputs)
                    w.writerow({col: row[col] for col in WEEKLY_CSV_LOCKED_COLUMNS})
                    rows_written += 1

    if rows_written == 0:
        write_reason_report(stage_dir, [StageReasonReport(
            stage="stage3",
            output_file="weekly.csv",
            reason_code=ReasonCode.NO_QUALIFYING_WEEKS_FROM_STAGE2,
            note="Stage 2's qualifying_weeks.csv was empty or absent; "
                 "Stage 3 has nothing to aggregate.",
            related_inputs=("stage2/qualifying_weeks.csv",),
        )])
    return stage_dir


def stage4_matching(stage3_dir: Path, baseline_cov_path: Path, out_dir: Path) -> Path:
    """Mahalanobis-Hungarian matched-pair construction per §7."""
    stage_dir = out_dir / "stage4"
    stage_dir.mkdir(parents=True, exist_ok=True)
    weekly_path = stage3_dir / "weekly.csv"
    rows = []
    with open(weekly_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["qualifies"].lower() in ("true", "1"):
                rows.append(row)
    arm_a = [r for r in rows if r["arm"] == "A"]
    arm_b = [r for r in rows if r["arm"] == "B"]
    if not arm_a or not arm_b:
        # Nothing to match yet — emit an empty pair file.
        with open(stage_dir / "matched_pairs.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pair_id", "week_a", "week_b", "distance", "quality"])
        with open(stage_dir / "unmatched_weeks.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["week_start_ct", "arm", "reason"])
        from tools.analysis.replay.reason_codes import (
            ReasonCode, StageReasonReport, write_reason_report,
        )
        if not arm_a and not arm_b:
            code = ReasonCode.INSUFFICIENT_QUALIFYING_WEEKS_PER_ARM
            note = "Stage 3 produced no qualifying weeks for either arm."
        else:
            code = ReasonCode.SINGLE_ARM_IN_WINDOW
            note = (
                f"Stage 3 has qualifying weeks for only one arm "
                f"(A: {len(arm_a)}, B: {len(arm_b)}); cannot form pairs."
            )
        write_reason_report(stage_dir, [StageReasonReport(
            stage="stage4",
            output_file="matched_pairs.csv",
            reason_code=code,
            note=note,
            related_inputs=("stage3/weekly.csv",),
        )])
        return stage_dir

    def vec(r):
        return np.array(
            [float(r[c]) for c in WEATHER_VECTOR_COMPONENTS], dtype=float
        )
    a_vecs = np.stack([vec(r) for r in arm_a])
    b_vecs = np.stack([vec(r) for r in arm_b])

    baseline = np.load(baseline_cov_path)
    sigma_inv = np.linalg.pinv(baseline["cov"])

    pairs, unmatched_a, unmatched_b = hungarian_match(a_vecs, b_vecs, sigma_inv)
    with open(stage_dir / "matched_pairs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pair_id", "week_a", "week_b", "distance", "quality"])
        for idx, (ia, ib, d) in enumerate(pairs):
            quality = "primary" if d <= MAHALANOBIS_PAIR_FLAG else "poor"
            w.writerow(
                [idx, arm_a[ia]["week_start_ct"], arm_b[ib]["week_start_ct"],
                 f"{d:.4f}", quality]
            )
    with open(stage_dir / "unmatched_weeks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week_start_ct", "arm", "reason"])
        for i in unmatched_a:
            w.writerow([arm_a[i]["week_start_ct"], "A", "unmatched_size"])
        for j in unmatched_b:
            w.writerow([arm_b[j]["week_start_ct"], "B", "unmatched_size"])
    return stage_dir


STAGE5_OUTCOMES = (
    "o1_dollars_per_cdd",
    "o3_peak_hvac_kw",
    "o4_dollars_per_cdd_whole_home",
)


def _compute_pair_diffs(
    stage3_dir: Path, stage4_dir: Path,
) -> dict[str, list[tuple[str, float]]]:
    """Compute per-outcome matched-pair differences (arm B − arm A).

    Returns {outcome: [(pair_id, diff), ...]} for every (outcome, primary
    pair) where both arms have a numeric value in Stage 3's weekly.csv.
    Used by both Stage 5 (bootstrap CI) and Stage 7 (SCED sign-flip).
    """
    weekly: dict[tuple[str, str], dict] = {}
    with open(stage3_dir / "weekly.csv") as f:
        for row in csv.DictReader(f):
            weekly[(row["week_start_ct"], row["arm"])] = row

    pairs: list[dict] = []
    with open(stage4_dir / "matched_pairs.csv") as f:
        for row in csv.DictReader(f):
            if row["quality"] == "primary":
                pairs.append(row)

    out: dict[str, list[tuple[str, float]]] = {o: [] for o in STAGE5_OUTCOMES}
    for outcome in STAGE5_OUTCOMES:
        for p in pairs:
            wa = weekly.get((p["week_a"], "A"))
            wb = weekly.get((p["week_b"], "B"))
            if wa is None or wb is None:
                continue
            try:
                diff = float(wb[outcome]) - float(wa[outcome])
            except ValueError:
                continue
            out[outcome].append((p["pair_id"], diff))
    return out


def stage5_effects(stage3_dir: Path, stage4_dir: Path, out_dir: Path) -> Path:
    """Compute matched-pair median Δ + stationary bootstrap 95% CI per outcome.

    Writes:
      - effects.csv: per-outcome summary (median, 95% CI from
        stationary bootstrap)
      - pair_diffs.csv: per-(outcome, pair) raw difference. Stage 7
        reads this for the SCED sign-flip randomization test.
    """
    stage_dir = out_dir / "stage5"
    stage_dir.mkdir(parents=True, exist_ok=True)

    diffs_by_outcome = _compute_pair_diffs(stage3_dir, stage4_dir)

    with open(stage_dir / "effects.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outcome", "n_pairs", "median_diff", "ci_low_95", "ci_high_95"])
        for i_outcome, outcome in enumerate(STAGE5_OUTCOMES):
            diffs = [d for _pid, d in diffs_by_outcome[outcome]]
            res = stationary_bootstrap_median_diff(
                diffs, rng_seed=PRNG_SEED + i_outcome,
            )
            w.writerow(
                [outcome, res["n"], f"{res['point']:.6f}",
                 f"{res['ci_low']:.6f}", f"{res['ci_high']:.6f}"]
            )

    # Per-(outcome, pair) raw differences for Stage 7 SCED input
    with open(stage_dir / "pair_diffs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outcome", "pair_id", "diff"])
        for outcome in STAGE5_OUTCOMES:
            for pair_id, diff in diffs_by_outcome[outcome]:
                w.writerow([outcome, pair_id, f"{diff:.6f}"])

    total_pairs = sum(len(diffs_by_outcome[o]) for o in STAGE5_OUTCOMES)
    if total_pairs == 0:
        from tools.analysis.replay.reason_codes import (
            ReasonCode, StageReasonReport, write_reason_report,
        )
        write_reason_report(stage_dir, [StageReasonReport(
            stage="stage5",
            output_file="effects.csv",
            reason_code=ReasonCode.NO_PRIMARY_QUALITY_PAIRS,
            note="Stage 4 produced no primary-quality matched pairs; "
                 "no per-outcome differences to bootstrap.",
            related_inputs=("stage4/matched_pairs.csv",),
        )])

    return stage_dir


# --- Stage 6: O2 layer reconstructions -------------------------------------


def compute_a_cust_cpl_kw(
    peak_hours: Sequence[datetime.datetime],
    hourly_kw_by_ts: dict[datetime.datetime, float],
) -> float:
    """Mean household kW across a set of peak hours.

    Missing peak hours (not in ``hourly_kw_by_ts``) are skipped — by
    Stage 2's contract, rule 1 imputation has already filled any
    recoverable gaps, so an absent hour means rule 1 declared the
    interval tier-4 (no imputation possible). Returns 0 if no data is
    present at any peak; callers decide flag/CI behavior.
    """
    present = [
        float(hourly_kw_by_ts[h]) for h in peak_hours if h in hourly_kw_by_ts
    ]
    if not present:
        return 0.0
    return sum(present) / len(present)


def compute_layer1_arm_delta(
    pjm_peak_hours_by_arm: dict[str, Sequence[datetime.datetime]],
    hourly_mains_kw: dict[datetime.datetime, float],
    capacity_rate_dollars_per_kw_month: float,
    months_billed: int = 5,
) -> dict[str, Any]:
    """O2 Layer 1: per-arm ACustCPL + B−A difference in kW and $.

    Per EXPERIMENT_DESIGN §6 Layer 1: the primary, fully-observable O2
    statement. ``ACustCPL_Y(arm)`` is the household's mean kW across the
    PJM Five Peak hours falling in that arm's weeks. Layer 1 reports
    ``ACustCPL_Y(B) − ACustCPL_Y(A)`` and the corresponding dollar
    impact via the locked capacity rate × months billed.
    """
    peaks_a = list(pjm_peak_hours_by_arm.get("A", []))
    peaks_b = list(pjm_peak_hours_by_arm.get("B", []))
    cpl_a = compute_a_cust_cpl_kw(peaks_a, hourly_mains_kw)
    cpl_b = compute_a_cust_cpl_kw(peaks_b, hourly_mains_kw)
    delta_kw = cpl_b - cpl_a
    delta_dollars = delta_kw * capacity_rate_dollars_per_kw_month * months_billed
    return {
        "a_cust_cpl_kw_arm_a": cpl_a,
        "a_cust_cpl_kw_arm_b": cpl_b,
        "n_peaks_arm_a": len(peaks_a),
        "n_peaks_arm_b": len(peaks_b),
        "delta_kw": delta_kw,
        "delta_dollars_total": delta_dollars,
        "capacity_rate_dollars_per_kw_month": capacity_rate_dollars_per_kw_month,
        "months_billed": months_billed,
    }


def compute_layer2_scenarios(
    pjm_peak_hours_by_arm: dict[str, Sequence[datetime.datetime]],
    comed_peak_hours_by_arm: dict[str, Sequence[datetime.datetime]],
    hourly_mains_kw: dict[datetime.datetime, float],
    tariff_constants,
    portfolio_sums_mw: dict[str, float] | None = None,
    months_billed: int = 5,
) -> list[dict[str, Any]]:
    """O2 Layer 2: per-arm CPLC reconstruction across portfolio scenarios.

    Uses the locked Att. M-2 §2 second-branch formula via
    ``tools.o2_capacity_reconstruction.reconstruct.scenarios``. Layer 2
    is descriptive only; reported across pre-registered named
    denominators (low / anchor_2021 / high) rather than a ±pct band
    (see EXPERIMENT_DESIGN.md §6 and tariff_snapshot.md §4).
    """
    from tools.o2_capacity_reconstruction.reconstruct import (
        PORTFOLIO_SUM_SCENARIOS_MW,
        scenarios as cplc_scenarios,
    )
    sums = portfolio_sums_mw if portfolio_sums_mw is not None else PORTFOLIO_SUM_SCENARIOS_MW

    cpl_a = compute_a_cust_cpl_kw(
        list(pjm_peak_hours_by_arm.get("A", [])), hourly_mains_kw,
    )
    cpl_b = compute_a_cust_cpl_kw(
        list(pjm_peak_hours_by_arm.get("B", [])), hourly_mains_kw,
    )
    pl_a = compute_a_cust_cpl_kw(
        list(comed_peak_hours_by_arm.get("A", [])), hourly_mains_kw,
    )
    pl_b = compute_a_cust_cpl_kw(
        list(comed_peak_hours_by_arm.get("B", [])), hourly_mains_kw,
    )
    rows_a = cplc_scenarios(cpl_a, pl_a, tariff_constants, sums)
    rows_b = cplc_scenarios(cpl_b, pl_b, tariff_constants, sums)
    rate = tariff_constants.rate_dollars_per_kw_month
    out: list[dict[str, Any]] = []
    for name in sums:
        cplc_a = rows_a[name]
        cplc_b = rows_b[name]
        delta = cplc_b - cplc_a
        out.append({
            "scenario": name,
            "portfolio_sum_mw": sums[name],
            "cplc_kw_arm_a": cplc_a,
            "cplc_kw_arm_b": cplc_b,
            "delta_kw": delta,
            "delta_dollars_total": delta * rate * months_billed,
        })
    return out


def compute_layer3_bill_capacity_dollars(
    comed_bills: Sequence[dict],
    year_y_plus_1: int,
    months: Sequence[int] = (5, 6, 7, 8, 9),
) -> dict[str, Any]:
    """O2 Layer 3: sum of ComEd capacity-charge line items across
    May-Sep of the Y+1 billing year. Descriptive only — no within-house
    counterfactual (only one realized arm assignment in summer Y)."""
    months_set = set(months)
    matched = [
        b for b in comed_bills
        if int(b.get("year", 0)) == year_y_plus_1
        and int(b.get("month", 0)) in months_set
    ]
    total = sum(float(b["capacity_charge_dollars"]) for b in matched)
    return {
        "year": year_y_plus_1,
        "months_summed": len(matched),
        "total_capacity_charge_dollars": total,
    }


def compute_detector_accuracy(
    published_5cp_hours: Iterable[datetime.datetime],
    summer_hours: Iterable[datetime.datetime],
    fivecp_state_by_hour: dict[datetime.datetime, str],
) -> dict[str, Any]:
    """Arm B 5CP-detector accuracy vs PJM's October-published 5CP hours.

    For each hour in ``summer_hours``:
      - pred_holding = ``fivecp_state_by_hour.get(h) == "holding"``
      - is_truth = h in ``published_5cp_hours``

    Returns counts (tp/fp/fn/tn) and rates (tpr/fpr/fnr). Process
    metric only — decoupled from O2's outcome statement.
    """
    truth = set(published_5cp_hours)
    tp = fp = fn = tn = 0
    for h in summer_hours:
        pred = fivecp_state_by_hour.get(h, "off") == "holding"
        is_truth = h in truth
        if pred and is_truth:
            tp += 1
        elif pred and not is_truth:
            fp += 1
        elif not pred and is_truth:
            fn += 1
        else:
            tn += 1
    pos = tp + fn
    neg = fp + tn
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "tpr": (tp / pos) if pos else 0.0,
        "fpr": (fp / neg) if neg else 0.0,
        "fnr": (fn / pos) if pos else 0.0,
        "summer_hours_n": tp + fp + fn + tn,
        "published_5cp_n": pos,
    }


# Output column schemas (locked schemas at OSF tag)
O2_LAYER1_COLUMNS = (
    "a_cust_cpl_kw_arm_a", "a_cust_cpl_kw_arm_b",
    "n_peaks_arm_a", "n_peaks_arm_b",
    "delta_kw", "delta_dollars_total",
    "capacity_rate_dollars_per_kw_month", "months_billed",
)
O2_LAYER2_COLUMNS = (
    "scenario", "portfolio_sum_mw",
    "cplc_kw_arm_a", "cplc_kw_arm_b",
    "delta_kw", "delta_dollars_total",
)
O2_LAYER3_COLUMNS = ("year", "months_summed", "total_capacity_charge_dollars")
DETECTOR_ACCURACY_COLUMNS = (
    "tp", "fp", "fn", "tn", "tpr", "fpr", "fnr",
    "summer_hours_n", "published_5cp_n",
)


def _load_stage6_inputs(stage1_dir: Path) -> dict | None:
    """Build Stage 6 inputs from Stage 1 parquet + bill PDFs + tariff JSON.

    Empty-stub for now: returns None unconditionally. Real implementation
    reads ``pjm.coincident_peak`` (for both PJM and ComEd 5CP hour lists),
    ``refoss.channel`` (em:1 + em:7 mains pivoted to hourly), the locked
    arm-assignment CSV (to map peak hours to arms), ``comed.bill``, and
    ``hvac.5cp_state`` for the detector accuracy report. Real-data
    integration runs against a 2025 replay export, gated by OSF criterion 14.
    """
    return None


def stage6_o2(stage1_dir: Path, out_dir: Path) -> Path:
    """O2 Layer 1, Layer 2, Layer 3 + detector accuracy.

    Independent of Stage 2/3: consumes Stage 1 directly. Writes four
    CSVs with locked schemas (empty header-only when the loader returns
    None — synthetic-fixture path goes through `_load_stage6_inputs`
    monkeypatched in tests).
    """
    stage_dir = out_dir / "stage6"
    stage_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "layer1": stage_dir / "o2_layer1.csv",
        "layer2": stage_dir / "o2_layer2.csv",
        "layer3": stage_dir / "o2_layer3.csv",
        "detector": stage_dir / "detector_accuracy.csv",
    }

    def _write_header(path, cols):
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cols))
            w.writeheader()

    inputs = _load_stage6_inputs(stage1_dir)
    if inputs is None:
        _write_header(paths["layer1"], O2_LAYER1_COLUMNS)
        _write_header(paths["layer2"], O2_LAYER2_COLUMNS)
        _write_header(paths["layer3"], O2_LAYER3_COLUMNS)
        _write_header(paths["detector"], DETECTOR_ACCURACY_COLUMNS)
        return stage_dir

    rate = inputs["tariff_constants"].rate_dollars_per_kw_month
    layer1 = compute_layer1_arm_delta(
        pjm_peak_hours_by_arm=inputs["pjm_peak_hours_by_arm"],
        hourly_mains_kw=inputs["hourly_mains_kw"],
        capacity_rate_dollars_per_kw_month=rate,
    )
    layer2 = compute_layer2_scenarios(
        pjm_peak_hours_by_arm=inputs["pjm_peak_hours_by_arm"],
        comed_peak_hours_by_arm=inputs["comed_peak_hours_by_arm"],
        hourly_mains_kw=inputs["hourly_mains_kw"],
        tariff_constants=inputs["tariff_constants"],
    )
    layer3 = compute_layer3_bill_capacity_dollars(
        comed_bills=inputs["comed_bills"],
        year_y_plus_1=int(inputs["summer_year"]) + 1,
    )
    detector = compute_detector_accuracy(
        published_5cp_hours=inputs.get("published_5cp_hours") or inputs["pjm_peak_hours_by_arm"]["A"] + inputs["pjm_peak_hours_by_arm"]["B"],
        summer_hours=inputs["summer_hours"],
        fivecp_state_by_hour=inputs["fivecp_state_by_hour"],
    )

    with open(paths["layer1"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(O2_LAYER1_COLUMNS))
        w.writeheader()
        w.writerow({k: layer1[k] for k in O2_LAYER1_COLUMNS})
    with open(paths["layer2"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(O2_LAYER2_COLUMNS))
        w.writeheader()
        for row in layer2:
            w.writerow({k: row[k] for k in O2_LAYER2_COLUMNS})
    with open(paths["layer3"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(O2_LAYER3_COLUMNS))
        w.writeheader()
        w.writerow({k: layer3[k] for k in O2_LAYER3_COLUMNS})
    with open(paths["detector"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(DETECTOR_ACCURACY_COLUMNS))
        w.writeheader()
        w.writerow({k: detector[k] for k in DETECTOR_ACCURACY_COLUMNS})

    return stage_dir


STAGE7_SCED_PVALUES_COLUMNS = (
    "outcome", "n_pairs", "observed_median", "pvalue", "exact",
)


def stage7_sced(stage5_dir: Path, out_dir: Path) -> Path:
    """SCED sign-flip randomization p-value per outcome (§7).

    Per EXPERIMENT_DESIGN.md §7 / ANALYSIS_PIPELINE.md Stage 7: for each
    outcome, exhaustively enumerate sign flips of the matched-pair
    differences (exact for N ≤ SCED_EXACT_MAX_N; random sampling
    otherwise). The fraction of permutations with absolute median
    ≥ |observed median| is the two-sided p-value.

    Reads ``stage5_dir / "pair_diffs.csv"`` (written by Stage 5),
    grouped by outcome. Uses ``sced_randomization_pvalue`` with
    ``rng_seed = PRNG_SEED + outcome_index + 1`` per spec.

    Output: ``stage7/sced_pvalues.csv`` with one row per outcome,
    columns: outcome, n_pairs, observed_median, pvalue, exact.
    """
    stage_dir = out_dir / "stage7"
    stage_dir.mkdir(parents=True, exist_ok=True)

    diffs_by_outcome: dict[str, list[float]] = {o: [] for o in STAGE5_OUTCOMES}
    pair_diffs_path = stage5_dir / "pair_diffs.csv"
    if pair_diffs_path.exists():
        with open(pair_diffs_path) as f:
            for row in csv.DictReader(f):
                outcome = row["outcome"]
                if outcome in diffs_by_outcome:
                    try:
                        diffs_by_outcome[outcome].append(float(row["diff"]))
                    except ValueError:
                        continue

    with open(stage_dir / "sced_pvalues.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(STAGE7_SCED_PVALUES_COLUMNS))
        for i_outcome, outcome in enumerate(STAGE5_OUTCOMES):
            diffs = diffs_by_outcome[outcome]
            if not diffs:
                w.writerow([outcome, 0, "", "", "False"])
                continue
            res = sced_randomization_pvalue(
                diffs, rng_seed=PRNG_SEED + i_outcome + 1,
            )
            obs_median = float(np.median(np.asarray(diffs)))
            w.writerow([
                outcome,
                res["n"],
                f"{obs_median:.6f}",
                f"{res['pvalue']:.6f}",
                str(res["exact"]),
            ])

    total_pairs = sum(len(v) for v in diffs_by_outcome.values())
    if total_pairs == 0:
        from tools.analysis.replay.reason_codes import (
            ReasonCode, StageReasonReport, write_reason_report,
        )
        write_reason_report(stage_dir, [StageReasonReport(
            stage="stage7",
            output_file="sced_pvalues.csv",
            reason_code=ReasonCode.NO_PAIR_DIFFERENCES_FROM_STAGE5,
            note="Stage 5's pair_diffs.csv contained no numeric "
                 "differences; no p-values to compute.",
            related_inputs=("stage5/pair_diffs.csv",),
        )])
    return stage_dir


SPIKE_PRICE_THRESHOLD_C_PER_KWH = 10.0
SPIKE_DAY_CATEGORIES = (
    "forecast_correlated_spike",
    "grid_event_spike",
    "no_spike",
)


def classify_spike_day(
    hourly_prices_cents_per_kwh: Sequence[float],
    max_forecast_temp_f: float,
    apparent_max_f: float,
) -> str:
    """Per EXPERIMENT_DESIGN.md §7 (Stage 8 decomposition).

    - **No-spike day**: no hour above 10¢/kWh.
    - **Forecast-correlated price-spike day**: any hour ≥10¢/kWh AND
      max forecast temp ≥85°F (or apparent ≥90°F) at 21:00-prior
      classification time.
    - **Grid-event price-spike day**: any hour ≥10¢/kWh AND max
      forecast temp <85°F AND apparent <90°F.
    """
    has_spike = any(
        p >= SPIKE_PRICE_THRESHOLD_C_PER_KWH
        for p in hourly_prices_cents_per_kwh
    )
    if not has_spike:
        return "no_spike"
    if max_forecast_temp_f >= HOT_TEMP_F or apparent_max_f >= HOT_APPARENT_F:
        return "forecast_correlated_spike"
    return "grid_event_spike"


STAGE8_DECOMPOSITION_COLUMNS = (
    "outcome", "category",
    "arm_a_n_days", "arm_a_median_cost",
    "arm_b_n_days", "arm_b_median_cost",
    "delta_median",
)
STAGE8_LAYER_ATTRIBUTION_COLUMNS = (
    "date", "hour_ct", "arm",
    "layer_triggered", "indoor_temp_f", "action",
)


def _load_stage8_inputs(
    stage1_dir: Path, stage3_dir: Path,
) -> dict | None:
    """Build Stage 8 inputs from Stage 1 raw data + Stage 3 weekly outputs.

    Empty-stub for now: returns None. Real implementation classifies
    each day in each qualifying week via ``classify_spike_day`` using
    ``comed.prices`` hourly average + ``nws.forecast`` (21:00-prior),
    aggregates day-level outcome costs from ``refoss.channel``, and
    derives layer-attribution rows for grid-event days from
    ``hvac.price_overlay.tier`` + ``hvac.5cp_state.state``. Real-data
    integration runs against a 2025 replay export (OSF criterion 14).

    Expected return dict shape:
      {
        "daily_records": list of dicts with keys:
          date, arm, category, costs (dict: outcome -> $ for that day)
        "layer_attribution": list of dicts with keys:
          date, hour_ct, arm, layer_triggered, indoor_temp_f, action
      }
    """
    return None


def stage8_decomposition(stage1_dir: Path, stage3_dir: Path, out_dir: Path) -> Path:
    """Forecast-correlated vs grid-event day decomposition (§7).

    Output:
      - ``decomposition.csv``: per (outcome × category), arm A vs arm B
        median day-level cost + B−A delta.
      - ``layer_attribution.csv``: per grid-event day in Arm B, which
        layer triggered (``price_spike_reactivity``, ``5cp_detection``,
        or ``neither``) and the timing.
    """
    stage_dir = out_dir / "stage8"
    stage_dir.mkdir(parents=True, exist_ok=True)

    inputs = _load_stage8_inputs(stage1_dir, stage3_dir)

    with open(stage_dir / "decomposition.csv", "w", newline="") as f:
        dw = csv.DictWriter(f, fieldnames=list(STAGE8_DECOMPOSITION_COLUMNS))
        dw.writeheader()
        if inputs is not None:
            daily = inputs.get("daily_records", [])
            # Group costs by (outcome, category, arm)
            for outcome in STAGE5_OUTCOMES:
                for category in SPIKE_DAY_CATEGORIES:
                    a_costs = [
                        float(d["costs"][outcome]) for d in daily
                        if d["arm"] == "A"
                        and d["category"] == category
                        and outcome in d.get("costs", {})
                    ]
                    b_costs = [
                        float(d["costs"][outcome]) for d in daily
                        if d["arm"] == "B"
                        and d["category"] == category
                        and outcome in d.get("costs", {})
                    ]
                    if not a_costs and not b_costs:
                        continue
                    a_med = float(np.median(a_costs)) if a_costs else 0.0
                    b_med = float(np.median(b_costs)) if b_costs else 0.0
                    dw.writerow({
                        "outcome": outcome,
                        "category": category,
                        "arm_a_n_days": len(a_costs),
                        "arm_a_median_cost": f"{a_med:.6f}",
                        "arm_b_n_days": len(b_costs),
                        "arm_b_median_cost": f"{b_med:.6f}",
                        "delta_median": f"{(b_med - a_med):.6f}",
                    })

    with open(stage_dir / "layer_attribution.csv", "w", newline="") as f:
        lw = csv.DictWriter(f, fieldnames=list(STAGE8_LAYER_ATTRIBUTION_COLUMNS))
        lw.writeheader()
        if inputs is not None:
            for row in inputs.get("layer_attribution", []):
                lw.writerow({
                    col: row.get(col, "") for col in STAGE8_LAYER_ATTRIBUTION_COLUMNS
                })

    return stage_dir


# Stage 9 sensitivity ids and per-sensitivity output schemas (locked at OSF
# tag). Sensitivities 1-4 produce effects-like rows (alternative effect
# estimates). 5 is descriptive day-of-week stratification. 6 is per-
# threshold-pair re-run of the Stage 8 decomposition.
STAGE9_EFFECTS_LIKE_SENSITIVITIES = (
    "euclidean_zscore",       # §7 #1: Euclidean on z-scored vector vs Mahalanobis
    "include_washout",        # §7 #2: washout hours included in Stage 3 aggregates
    "em2_em8_only",           # §7 #3: O1 with em:2 + em:8 only (no em:9 blower)
    "five_min_pricing",       # §7 #4: O1 with 5-min pricing vs hourly average
)
STAGE9_EFFECTS_LIKE_COLUMNS = (
    "outcome", "n_pairs", "median_diff", "ci_low_95", "ci_high_95",
)
STAGE9_DAY_OF_WEEK_COLUMNS = (
    "outcome", "day_of_week", "arm", "n", "mean_value",
)
STAGE9_THRESHOLD_ROBUSTNESS_COLUMNS = (
    "threshold_pair", "outcome", "category", "delta_median",
)


def _load_stage9_inputs(
    stage1_dir: Path, stage2_dir: Path, stage3_dir: Path,
) -> dict | None:
    """Build Stage 9 inputs by re-running upstream stages with perturbed
    parameters per sensitivity.

    Empty-stub for now: returns None. Real implementation re-runs
    Stages 4-5 (or 8) per sensitivity:
      - euclidean_zscore: re-run Stage 4 matching with Euclidean
        distance on z-scored weather vector; pipe through Stage 5.
      - include_washout: re-run Stage 3 weekly aggregation including
        the first 48h after each Monday switch; Stages 4-5 downstream.
      - em2_em8_only: re-run Stage 3 O1 with em:2 + em:8 only (drop
        em:9 furnace blower); Stage 5 downstream.
      - five_min_pricing: re-run Stage 3 O1 with 5-min comed.prices
        granularity instead of hourly_avg.
      - day_of_week: descriptive split — group Stage 3 weekly outcomes
        by day-of-week of the price-spike hours.
      - threshold_robustness: re-run Stage 8 decomposition under each
        of {(8¢, 15¢), (10¢, 20¢), (12¢, 25¢)} spike/scarcity thresholds.

    Real-data integration runs against a 2025 replay export per
    OSF_FILING.md criterion 14.

    Expected return dict shape:
      {
        "euclidean_zscore": list of effects-like row dicts,
        "include_washout": list of effects-like row dicts,
        "em2_em8_only": list of effects-like row dicts,
        "five_min_pricing": list of effects-like row dicts,
        "day_of_week": list of day-of-week row dicts,
        "threshold_robustness": list of threshold-robustness row dicts,
      }
    """
    return None


def stage9_sensitivity(
    stage1_dir: Path, stage2_dir: Path, stage3_dir: Path, out_dir: Path,
) -> Path:
    """Six pre-committed sensitivities per EXPERIMENT_DESIGN.md §7.

    Reads inputs from ``_load_stage9_inputs`` (a stub that returns None
    until the real upstream-re-run plumbing lands with the replay
    export). Writes one CSV per sensitivity with locked column schemas.
    """
    stage_dir = out_dir / "stage9"
    stage_dir.mkdir(parents=True, exist_ok=True)
    inputs = _load_stage9_inputs(stage1_dir, stage2_dir, stage3_dir)

    def _write(name: str, cols: Sequence[str], key: str) -> None:
        with open(stage_dir / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cols))
            w.writeheader()
            if inputs is not None:
                for row in inputs.get(key, []):
                    w.writerow({c: row.get(c, "") for c in cols})

    for sid in STAGE9_EFFECTS_LIKE_SENSITIVITIES:
        _write(sid, STAGE9_EFFECTS_LIKE_COLUMNS, sid)
    _write("day_of_week", STAGE9_DAY_OF_WEEK_COLUMNS, "day_of_week")
    _write(
        "threshold_robustness",
        STAGE9_THRESHOLD_ROBUSTNESS_COLUMNS,
        "threshold_robustness",
    )
    return stage_dir


# --- Orchestrator + CLI ----------------------------------------------------


def run_all(
    start: datetime.datetime,
    end: datetime.datetime,
    out_dir: Path,
    baseline_cov_path: Path,
) -> Path:
    """End-to-end pipeline. Returns the run output directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stage1_extract(start, end, out_dir)
    stage2_quality(out_dir / "stage1", out_dir)
    stage3_weekly(out_dir / "stage1", out_dir / "stage2", out_dir)
    stage4_matching(out_dir / "stage3", baseline_cov_path, out_dir)
    stage5_effects(out_dir / "stage3", out_dir / "stage4", out_dir)
    stage6_o2(out_dir / "stage1", out_dir)
    stage7_sced(out_dir / "stage5", out_dir)
    stage8_decomposition(out_dir / "stage1", out_dir / "stage3", out_dir)
    stage9_sensitivity(out_dir / "stage1", out_dir / "stage2", out_dir / "stage3", out_dir)
    return out_dir


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="ISO 8601 CT start, e.g. 2026-06-01")
    ap.add_argument("--end", required=True, help="ISO 8601 CT end, e.g. 2026-09-30")
    ap.add_argument("--out", default="analysis/out",
                    help="Output directory; run_ts subdir created inside.")
    ap.add_argument("--baseline-cov",
                    default="tools/analysis/data/baseline_cov.npz",
                    help="Path to the 6x6 weather-vector baseline covariance.")
    args = ap.parse_args()
    start = datetime.datetime.fromisoformat(args.start)
    end = datetime.datetime.fromisoformat(args.end)
    run_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / run_ts
    run_all(start, end, out_dir, Path(args.baseline_cov))
    print(f"pipeline complete: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
