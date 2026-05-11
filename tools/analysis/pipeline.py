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


def stage1_extract(
    start: datetime.datetime,
    end: datetime.datetime,
    out_dir: Path,
    influx_url: str | None = None,
    influx_token: str | None = None,
    influx_org: str | None = None,
    influx_bucket: str | None = None,
) -> Path:
    """Pull every measurement listed in §2.1 within the window."""
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
    stage_dir.mkdir(parents=True, exist_ok=True)

    queries_dir = Path(__file__).resolve().parent / "queries"
    measurements = [p.stem for p in queries_dir.glob("*.flux")]
    if not measurements:
        raise RuntimeError(f"no .flux queries found under {queries_dir}")

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
            df.to_parquet(stage_dir / f"{meas}.parquet")
    return stage_dir


def stage2_quality(stage1_dir: Path, out_dir: Path) -> Path:
    """Apply the 10 data-quality rules from §4.

    This function orchestrates rule1..rule10 (each below). The
    actual data plumbing — reading parquet files, joining by week,
    walking gaps — is delegated to per-rule helpers.

    A full implementation reads each parquet, applies its
    corresponding rule, and writes the consolidated CSVs. This
    skeleton emits an empty qualifying_weeks.csv with the locked
    schema so downstream stages can be unit-tested against
    synthetic data.
    """
    stage_dir = out_dir / "stage2"
    stage_dir.mkdir(parents=True, exist_ok=True)
    qual_path = stage_dir / "qualifying_weeks.csv"
    with open(qual_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "week_start_ct", "arm", "qualifying", "exclusion_reason",
                "imputed_hvac_kwh_pct", "imputed_price_hours_pct",
                "override_operational_count", "override_vacation_days",
            ]
        )
    (stage_dir / "imputed_intervals.csv").touch()
    (stage_dir / "outages.csv").touch()
    return stage_dir


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


def stage3_weekly(stage1_dir: Path, stage2_dir: Path, out_dir: Path) -> Path:
    """Compute per-(week, arm) outcome inputs and weather summary vector.

    Skeleton: a full implementation reads the parquet HVAC/price/weather
    data and produces weekly aggregates. This emits an empty CSV with
    the locked schema for synthetic-data testing.
    """
    stage_dir = out_dir / "stage3"
    stage_dir.mkdir(parents=True, exist_ok=True)
    with open(stage_dir / "weekly.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "week_start_ct", "arm", "qualifies",
                "o1_dollars_per_cdd", "o3_peak_hvac_kw",
                "o4_dollars_per_cdd_whole_home",
                *WEATHER_VECTOR_COMPONENTS,
            ]
        )
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


def stage5_effects(stage3_dir: Path, stage4_dir: Path, out_dir: Path) -> Path:
    """Compute matched-pair median Δ + stationary bootstrap 95% CI per outcome."""
    stage_dir = out_dir / "stage5"
    stage_dir.mkdir(parents=True, exist_ok=True)

    weekly = {}
    with open(stage3_dir / "weekly.csv") as f:
        for row in csv.DictReader(f):
            weekly[(row["week_start_ct"], row["arm"])] = row

    pairs = []
    with open(stage4_dir / "matched_pairs.csv") as f:
        for row in csv.DictReader(f):
            if row["quality"] == "primary":
                pairs.append(row)

    outcomes = ("o1_dollars_per_cdd", "o3_peak_hvac_kw", "o4_dollars_per_cdd_whole_home")
    with open(stage_dir / "effects.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outcome", "n_pairs", "median_diff", "ci_low_95", "ci_high_95"])
        for i_outcome, outcome in enumerate(outcomes):
            diffs = []
            for p in pairs:
                wa = weekly.get((p["week_a"], "A"))
                wb = weekly.get((p["week_b"], "B"))
                if wa is None or wb is None:
                    continue
                try:
                    diff = float(wb[outcome]) - float(wa[outcome])
                except ValueError:
                    continue
                diffs.append(diff)
            res = stationary_bootstrap_median_diff(
                diffs, rng_seed=PRNG_SEED + i_outcome,
            )
            w.writerow(
                [outcome, res["n"], f"{res['point']:.6f}",
                 f"{res['ci_low']:.6f}", f"{res['ci_high']:.6f}"]
            )
    return stage_dir


def stage6_o2(stage1_dir: Path, out_dir: Path) -> Path:
    """O2 Layer 1, Layer 2, Layer 3 + detector accuracy."""
    stage_dir = out_dir / "stage6"
    stage_dir.mkdir(parents=True, exist_ok=True)
    for name in ("o2_layer1", "o2_layer2", "o2_layer3", "detector_accuracy"):
        (stage_dir / f"{name}.csv").touch()
    return stage_dir


def stage7_sced(stage5_dir: Path, out_dir: Path) -> Path:
    """SCED randomization test on the pair differences from Stage 5."""
    stage_dir = out_dir / "stage7"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "sced_pvalues.csv").touch()
    return stage_dir


def stage8_decomposition(stage1_dir: Path, stage3_dir: Path, out_dir: Path) -> Path:
    """Forecast-correlated vs grid-event day decomposition (§7)."""
    stage_dir = out_dir / "stage8"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "decomposition.csv").touch()
    (stage_dir / "layer_attribution.csv").touch()
    return stage_dir


def stage9_sensitivity(
    stage1_dir: Path, stage2_dir: Path, stage3_dir: Path, out_dir: Path,
) -> Path:
    """Six pre-committed sensitivities per §7."""
    stage_dir = out_dir / "stage9"
    stage_dir.mkdir(parents=True, exist_ok=True)
    for sid in (
        "euclidean_zscore", "include_washout", "em2_em8_only",
        "five_min_pricing", "day_of_week", "threshold_robustness",
    ):
        (stage_dir / f"{sid}.csv").touch()
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
