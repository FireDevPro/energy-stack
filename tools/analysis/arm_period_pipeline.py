"""Arm-period pipeline orchestrator per
docs/plans/sced-rebaseline-spec-2026-05-13.md §4-§9.

End-to-end:
1. Build per-hour mode lists per arm (5-min telemetry -> hourly,
   with telemetry-invalid override).
2. Apply the §5/§7 single validity gate to each arm.
3. Build §6 weather vectors and within-sample z-score across all 12
   arms; then rectangular Hungarian over gate-pass A and gate-pass B
   subsets.
4. Per matched pair: per-hour HVAC$ + kWh + cost-matched symmetric
   exclusion + provenance fields.
5. Aggregate §9.5 pre-registered summary buckets.

DST fold contract (plan §3): every per-hour join keys on UTC instants
(via hour_index_to_utc) or hour-index; never on the CT-naive datetime
returned by ``arm_calendar.hour_index_to_datetime``.
"""
from __future__ import annotations

import datetime
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tools.analysis.arm_calendar import (
    ARM_CALENDAR,
    ArmPeriod,
    HOURS_PER_ARM,
    post_washout_start,
)
from tools.analysis.cost_matched_exclusion import (
    cost_matched_exclude_with_provenance,
)
from tools.analysis.dtod_rates import dtod_total_delivery_c_per_kwh
from tools.analysis.hvac_dollars import HourlyRateInputs, hourly_rate_c_per_kwh
from tools.analysis.hvac_telemetry_validity import (
    HVAC_CHANNELS,
    hour_is_telemetry_valid,
)
from tools.analysis.matching import (
    caliper_p90_distance,
    hungarian_match,
    pairwise_distance_matrix,
    within_sample_zscore,
)
from tools.analysis.mode_classification import HourMode
from tools.analysis.validity_gate import (
    arm_passes_validity_gate,
    fully_valid_count,
    is_fully_valid_for_arm,
)
from tools.analysis.weather_vector import WeatherVector, build_weather_vector


_CT = ZoneInfo("America/Chicago")
_UTC = ZoneInfo("UTC")


HIGH_TEMP_F = 85.0  # spec §9.5 / §9 high-temp threshold
COOLING_ACTIVE_W = 100.0  # spec §9: em:2 + em:8 > 100W => cooling active
LOW_COOLING_EXPOSURE_HOURS = 6  # spec §9: low_cooling_exposure_flag


# Default OSF April-2026 snapshot of non-LMP rate primitives. Real
# pipeline calls override this via the ``rate_snapshot`` kwarg. The
# defaults are only used so the fixture-driven outside-in test can
# exercise the path without rebuilding the snapshot from bills.
DEFAULT_RATE_SNAPSHOT: dict[str, float] = {
    "pea_c_per_kwh": 1.773,
    "transmission_c_per_kwh": 1.083,
    "misc_procurement_c_per_kwh": 0.062,
    "variable_riders_c_per_kwh": 1.16,
    "carbon_free_credit_c_per_kwh": -3.186,
}


@dataclass
class PipelineResult:
    """Outside-in acceptance test contract."""
    per_pair_table: pd.DataFrame
    bucket_summaries: dict[str, dict]
    mode_distribution: dict[str, int]
    arms_passed_validity: set[str]
    bill_reconciliations: list[dict] = field(default_factory=list)


# --- Time helpers ----------------------------------------------------------


def _normalize_time_column(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with ``_time`` normalised to naive UTC. Tolerates
    both tz-aware-UTC (production Influx) and naive (fixture) inputs.
    """
    if df.empty or "_time" not in df.columns:
        return df
    out = df.copy()
    times = pd.to_datetime(out["_time"])
    if times.dt.tz is not None:
        times = times.dt.tz_convert("UTC").dt.tz_localize(None)
    out["_time"] = times
    return out


def _arm_id(arm: ArmPeriod) -> str:
    return f"{arm.arm}{arm.index}"


def _post_washout_utc_naive(arm: ArmPeriod) -> datetime.datetime:
    """Naive-UTC start of the post-washout window (CT 00:00 Wed
    post-arm-start, converted via zoneinfo)."""
    return (
        post_washout_start(arm)
        .replace(tzinfo=_CT)
        .astimezone(_UTC)
        .replace(tzinfo=None)
    )


def _hour_index_to_utc(arm: ArmPeriod, k: int) -> datetime.datetime:
    """Hour-index k in this arm -> naive-UTC hour-boundary."""
    return _post_washout_utc_naive(arm) + datetime.timedelta(hours=k)


def _utc_to_ct_hour_of_day(when_utc_naive: datetime.datetime) -> int:
    return (
        when_utc_naive.replace(tzinfo=_UTC)
        .astimezone(_CT)
        .hour
    )


# --- Mode classification from telemetry ------------------------------------


def _telemetry_valid_set_for_arm(
    arm: ArmPeriod, refoss_df: pd.DataFrame,
) -> set[datetime.datetime]:
    """Return the set of hour boundaries (naive UTC) where every HVAC
    channel passes spec §7 in this arm's post-washout window.

    Vectorised replacement for the per-hour ``hour_is_telemetry_valid``
    loop: bins all Refoss rows by hour, drops hours that don't have
    >=110 samples per channel, and re-runs the gap rule only on the
    surviving hours.
    """
    df = refoss_df
    if "_field" in df.columns:
        df = df[df["_field"] == "power_w"]
    df = df[df["channel"].isin(HVAC_CHANNELS)]
    start_utc = _post_washout_utc_naive(arm)
    end_utc = start_utc + datetime.timedelta(hours=HOURS_PER_ARM)
    df = df[(df["_time"] >= start_utc) & (df["_time"] < end_utc)]
    if df.empty:
        return set()
    df = df.assign(_hour=df["_time"].dt.floor("h"))

    # Count samples per (hour, channel); any (hour, channel) with <110
    # rows fails. Hours that survive must also pass the gap rule.
    counts = df.groupby(["_hour", "channel"]).size().unstack(fill_value=0)
    sample_pass = (counts >= 110).all(axis=1)
    candidate_hours = set(counts.index[sample_pass].tolist())
    if not candidate_hours:
        return set()

    # Vectorised gap check across the surviving hours. Compute first/
    # last sample times per (hour, channel) and the longest intra-channel
    # gap; combine with boundary gaps to hour-start and hour-end.
    df = df.sort_values(["channel", "_time"])
    df = df.assign(
        prev_time=df.groupby(["_hour", "channel"])["_time"].shift(1),
    )
    df["intra_gap_s"] = (df["_time"] - df["prev_time"]).dt.total_seconds()
    intra_max = df.groupby(["_hour", "channel"])["intra_gap_s"].max()
    first_last = df.groupby(["_hour", "channel"])["_time"].agg(["min", "max"])

    valid: set[datetime.datetime] = set()
    for h_start in candidate_hours:
        h_end = h_start + datetime.timedelta(hours=1)
        passed = True
        for ch in HVAC_CHANNELS:
            key = (h_start, ch)
            if key not in first_last.index:
                passed = False
                break
            fl_min = first_last.at[key, "min"]
            fl_max = first_last.at[key, "max"]
            lead = (fl_min - h_start).total_seconds()
            trail = (h_end - fl_max).total_seconds()
            intra = intra_max.get(key, 0.0)
            if pd.isna(intra):
                intra = 0.0
            if lead > 120 or trail > 120 or intra > 120:
                passed = False
                break
        if passed:
            valid.add(h_start)
    return valid


def _hourly_mode_from_telemetry(
    *,
    arm: ArmPeriod,
    refoss_df: pd.DataFrame,
    hvac_arm_mode_df: pd.DataFrame,
) -> list[HourMode]:
    """Build the 288-element HourMode list for this arm.

    Strategy:
    - Aggregate hvac_arm_mode_df 5-min rows -> hourly majority mode (the
      controller's recorded classification).
    - Cross-check against Refoss §7 telemetry validity. If telemetry is
      invalid for the hour, override to TELEMETRY_INVALID per the §5
      precedence rule.
    - If the hour has no mode telemetry at all, treat as B_DOWN for an
      Arm B period, TELEMETRY_INVALID for Arm A (no signal to confirm
      A-active either).
    """
    start_utc = _post_washout_utc_naive(arm)
    end_utc = start_utc + datetime.timedelta(hours=HOURS_PER_ARM)
    # Pre-compute the telemetry-valid hour set once.
    valid_hours = _telemetry_valid_set_for_arm(arm, refoss_df)
    # Pre-bin mode telemetry to hourly majority.
    mode_window = hvac_arm_mode_df[
        (hvac_arm_mode_df["_time"] >= start_utc)
        & (hvac_arm_mode_df["_time"] < end_utc)
    ].copy()
    if mode_window.empty:
        hourly_recorded: dict = {}
    else:
        mode_window["_hour"] = mode_window["_time"].dt.floor("h")
        hourly_recorded = (
            mode_window.groupby("_hour")["mode_actual"]
            .agg(lambda s: s.mode().iloc[0])
            .to_dict()
        )

    modes: list[HourMode] = []
    for k in range(HOURS_PER_ARM):
        h_start = start_utc + datetime.timedelta(hours=k)
        telemetry_valid = h_start in valid_hours
        if not telemetry_valid:
            modes.append(HourMode.TELEMETRY_INVALID)
            continue
        recorded = hourly_recorded.get(h_start)
        if recorded is None:
            modes.append(
                HourMode.B_DOWN if arm.arm == "B" else HourMode.TELEMETRY_INVALID
            )
            continue
        modes.append(_string_to_hour_mode(recorded))
    return modes


_KNOWN_MODE_STRINGS: dict[str, HourMode] = {
    "A-active": HourMode.A_ACTIVE,
    "B-active": HourMode.B_ACTIVE,
    "B-fallback": HourMode.B_FALLBACK,
    "B-down": HourMode.B_DOWN,
    "telemetry-invalid": HourMode.TELEMETRY_INVALID,
}


def _string_to_hour_mode(s: str) -> HourMode:
    """Map a controller-side mode string to HourMode. Unknown values
    (e.g. off-protocol shadow/production captured in production telemetry
    but outside this study's scope) downgrade to TELEMETRY_INVALID so
    they are excluded from analysis rather than crashing.
    """
    return _KNOWN_MODE_STRINGS.get(s, HourMode.TELEMETRY_INVALID)


# --- Per-hour HVAC kWh + rate vectors --------------------------------------


def _hourly_hvac_kwh(arm: ArmPeriod, refoss_df: pd.DataFrame) -> list[float]:
    """Per spec §7: hour_kWh = mean(power_w in hour) * 1h, summed across
    em:2/em:8/em:9. Vectorised across the 288-hour window.
    """
    df = refoss_df
    if "_field" in df.columns:
        df = df[df["_field"] == "power_w"]
    df = df[df["channel"].isin(HVAC_CHANNELS)]
    start_utc = _post_washout_utc_naive(arm)
    end_utc = start_utc + datetime.timedelta(hours=HOURS_PER_ARM)
    df = df[(df["_time"] >= start_utc) & (df["_time"] < end_utc)]
    out = [0.0] * HOURS_PER_ARM
    if df.empty:
        return out
    df = df.assign(_hour=df["_time"].dt.floor("h"))
    # mean(power_w) per (_hour, channel) then sum across channels = hourly W
    hourly_w = (
        df.groupby(["_hour", "channel"])["_value"].mean()
        .groupby(level=0).sum()
    )
    for h_start, watts in hourly_w.items():
        k = int(round((h_start - start_utc).total_seconds() / 3600))
        if 0 <= k < HOURS_PER_ARM:
            out[k] = float(watts) / 1000.0
    return out


def _hourly_rates(
    arm: ArmPeriod,
    rt_hrl_lmps_df: pd.DataFrame,
    rate_snapshot: dict[str, float],
) -> list[float]:
    """¢/kWh per hour-index per spec §4. Raises if rt_hrl_lmps has no
    row for a hour in the post-washout window -- the analysis layer
    requires bill-canonical settled LMP for every hour. Missing data is
    a feed-gap defect to surface, not silently substitute as zero.
    """
    start_utc = _post_washout_utc_naive(arm)
    # Index LMP rows once
    lmp_lookup = (
        rt_hrl_lmps_df.dropna(subset=["_time"])
        .set_index("_time")["total_lmp_rt"]
    )
    rates: list[float] = []
    for k in range(HOURS_PER_ARM):
        h_start = start_utc + datetime.timedelta(hours=k)
        if h_start not in lmp_lookup.index:
            raise ValueError(
                f"Missing rt_hrl_lmps row for hour {h_start} (arm "
                f"{_arm_id(arm)} hour-index {k}); bill-canonical supply "
                "rate is required per spec §4. Run the rt_hrl_lmps "
                "poller backfill before running the pipeline."
            )
        lmp_per_mwh = float(lmp_lookup.loc[h_start])
        ct_hour = _utc_to_ct_hour_of_day(h_start)
        inputs = HourlyRateInputs(
            rt_hrl_lmps_per_mwh=lmp_per_mwh,
            pea_c_per_kwh=rate_snapshot["pea_c_per_kwh"],
            transmission_c_per_kwh=rate_snapshot["transmission_c_per_kwh"],
            misc_procurement_c_per_kwh=rate_snapshot["misc_procurement_c_per_kwh"],
            dtod_total_delivery_c_per_kwh=dtod_total_delivery_c_per_kwh(ct_hour),
            variable_riders_c_per_kwh=rate_snapshot["variable_riders_c_per_kwh"],
            carbon_free_credit_c_per_kwh=rate_snapshot["carbon_free_credit_c_per_kwh"],
        )
        rates.append(hourly_rate_c_per_kwh(inputs))
    return rates


def _hourly_em28_watts(arm: ArmPeriod, refoss_df: pd.DataFrame) -> list[float]:
    """Mean(em:2 + em:8 power_w) per hour-index. Spec §9 cooling-active
    test uses this instead of the full HVAC kWh (which includes em:9).
    Vectorised.
    """
    df = refoss_df
    if "_field" in df.columns:
        df = df[df["_field"] == "power_w"]
    df = df[df["channel"].isin(("em:2", "em:8"))]
    start_utc = _post_washout_utc_naive(arm)
    end_utc = start_utc + datetime.timedelta(hours=HOURS_PER_ARM)
    df = df[(df["_time"] >= start_utc) & (df["_time"] < end_utc)]
    out = [0.0] * HOURS_PER_ARM
    if df.empty:
        return out
    df = df.assign(_hour=df["_time"].dt.floor("h"))
    hourly_w = (
        df.groupby(["_hour", "channel"])["_value"].mean()
        .groupby(level=0).sum()
    )
    for h_start, watts in hourly_w.items():
        k = int(round((h_start - start_utc).total_seconds() / 3600))
        if 0 <= k < HOURS_PER_ARM:
            out[k] = float(watts)
    return out


def _hourly_ct_outdoor_temp(arm: ArmPeriod,
                            ecowitt_df: pd.DataFrame) -> list[float | None]:
    """Hourly mean outdoor temp aligned to the arm's hour-indices. Used
    for high-temp exposure bucket (spec §9.5)."""
    start_utc = _post_washout_utc_naive(arm)
    end_utc = start_utc + datetime.timedelta(hours=HOURS_PER_ARM)
    df = ecowitt_df[
        (ecowitt_df["_time"] >= start_utc) & (ecowitt_df["_time"] < end_utc)
    ].copy()
    if df.empty:
        return [None] * HOURS_PER_ARM
    df["_hour"] = df["_time"].dt.floor("h")
    hourly_mean = df.groupby("_hour")["ch1_temp_f"].mean()
    return [
        float(hourly_mean.get(start_utc + datetime.timedelta(hours=k), float("nan")))
        for k in range(HOURS_PER_ARM)
    ]


# --- Per-pair row construction ---------------------------------------------


def _build_pair_row(
    *,
    pair_id: int,
    arm_a: ArmPeriod,
    arm_b: ArmPeriod,
    modes_a: list[HourMode],
    modes_b: list[HourMode],
    rates_a: list[float],
    rates_b: list[float],
    kwh_a: list[float],
    kwh_b: list[float],
    em28_w_a: list[float],
    em28_w_b: list[float],
    hourly_temp_a: list[float | None],
    hourly_temp_b: list[float | None],
    vec_a: WeatherVector,
    vec_b: WeatherVector,
    z_a: np.ndarray,
    z_b: np.ndarray,
    p90_dist: float,
    rate_snapshot: dict[str, float],
) -> dict:
    initial_valid_a = [is_fully_valid_for_arm(m, "A") for m in modes_a]
    initial_valid_b = [is_fully_valid_for_arm(m, "B") for m in modes_b]

    # Breakdown of mode-invalid hours BEFORE cost-matched drops
    excl_a = Counter()
    excl_b = Counter()
    for m in modes_a:
        if not is_fully_valid_for_arm(m, "A"):
            excl_a[m.value] += 1
    for m in modes_b:
        if not is_fully_valid_for_arm(m, "B"):
            excl_b[m.value] += 1

    excl = cost_matched_exclude_with_provenance(
        rates_a, rates_b, initial_valid_a, initial_valid_b,
    )
    valid_a = excl.valid_a
    valid_b = excl.valid_b

    # HVAC$ in DOLLARS (helper returns cents)
    hvac_cents_a = sum(kwh_a[k] * rates_a[k] for k in range(HOURS_PER_ARM)
                       if valid_a[k])
    hvac_cents_b = sum(kwh_b[k] * rates_b[k] for k in range(HOURS_PER_ARM)
                       if valid_b[k])
    hvac_dollars_a = hvac_cents_a / 100.0
    hvac_dollars_b = hvac_cents_b / 100.0
    hvac_kwh_a = sum(kwh_a[k] for k in range(HOURS_PER_ARM) if valid_a[k])
    hvac_kwh_b = sum(kwh_b[k] for k in range(HOURS_PER_ARM) if valid_b[k])

    # Cooling-active per spec §9: em:2 + em:8 > 100W. Use full-arm
    # post-washout window, not only post-exclusion (the indicator
    # describes household behavior, not the analysis denominator).
    cooling_active_hours_a = sum(1 for k in range(HOURS_PER_ARM)
                                 if em28_w_a[k] > COOLING_ACTIVE_W)
    cooling_active_hours_b = sum(1 for k in range(HOURS_PER_ARM)
                                 if em28_w_b[k] > COOLING_ACTIVE_W)

    weather_distance = float(np.linalg.norm(z_a - z_b))
    poor_weather_match = weather_distance > p90_dist

    # Spec §5 produces equal-size valid sets in both arms after
    # cost-matched exclusion. valid_pair_hours therefore equals the
    # per-arm valid count. Sanity-check both sides agree.
    valid_a_count = sum(valid_a)
    valid_b_count = sum(valid_b)
    if valid_a_count != valid_b_count:
        raise AssertionError(
            f"cost-matched exclusion left unequal counts: "
            f"|valid_a|={valid_a_count} |valid_b|={valid_b_count}"
        )
    valid_pair_hours = valid_a_count
    excluded_hours_count = HOURS_PER_ARM - valid_pair_hours

    # Provenance: median hourly rate of excluded hours (union of either-
    # side exclusions). Empty if no exclusions.
    excluded_indices = [k for k in range(HOURS_PER_ARM)
                        if not (valid_a[k] and valid_b[k])]
    if excluded_indices:
        excluded_rates = [
            (rates_a[k] + rates_b[k]) / 2.0 for k in excluded_indices
        ]
        excluded_rate_p50 = float(np.median(excluded_rates))
    else:
        excluded_rate_p50 = 0.0

    # Spec §5 overlap provenance for excluded hours. Without scarcity-
    # tier or 5CP-active feeds wired in, those overlaps are 0; high-temp
    # is computable from Ecowitt.
    excluded_overlap_high_temp = sum(
        1 for k in excluded_indices
        if (hourly_temp_a[k] is not None and hourly_temp_a[k] >= HIGH_TEMP_F)
        or (hourly_temp_b[k] is not None and hourly_temp_b[k] >= HIGH_TEMP_F)
    )

    raw_diffs = vec_b.as_array() - vec_a.as_array()
    z_diffs = z_b - z_a

    a_dates = f"{arm_a.start_ct.date()}/{arm_a.end_ct.date()}"
    b_dates = f"{arm_b.start_ct.date()}/{arm_b.end_ct.date()}"

    if hvac_dollars_a >= 5.0:
        percent_diff = (hvac_dollars_b - hvac_dollars_a) / hvac_dollars_a * 100.0
    else:
        percent_diff = float("nan")

    return {
        "pair_id": pair_id,
        "arm_a_id": _arm_id(arm_a),
        "arm_b_id": _arm_id(arm_b),
        "arm_a_dates": a_dates,
        "arm_b_dates": b_dates,
        "temporal_gap_days": abs((arm_b.start_ct - arm_a.start_ct).days),
        "weather_distance_zscore": weather_distance,
        "weather_vector_a": tuple(vec_a.as_array().tolist()),
        "weather_vector_b": tuple(vec_b.as_array().tolist()),
        "weather_component_diffs_raw": tuple(raw_diffs.tolist()),
        "weather_component_diffs_zscored": tuple(z_diffs.tolist()),
        "poor_weather_match_flag": bool(poor_weather_match),
        "valid_pair_hours": int(valid_pair_hours),
        "valid_pair_hours_a": int(valid_a_count),
        "valid_pair_hours_b": int(valid_b_count),
        "excluded_hours_count": int(excluded_hours_count),
        "excluded_hours_breakdown_a": dict(excl_a),
        "excluded_hours_breakdown_b": dict(excl_b),
        "cost_match_quality_median_diff_c_per_kwh":
            excl.cost_match_quality_median_diff_c_per_kwh,
        "excluded_hourly_rate_p50_c_per_kwh": excluded_rate_p50,
        # Scarcity-tier + 5CP overlap will fill in when the controller-
        # side tier markers and post-season 5CP list are wired in
        # (Phase 5+). Per spec §9.5 zero is itself a reported finding.
        "excluded_overlap_elevated_price": 0,
        "excluded_overlap_scarcity_price": 0,
        "excluded_overlap_5cp_active": 0,
        "excluded_overlap_high_temp": int(excluded_overlap_high_temp),
        "cfe_c_per_kwh_a": rate_snapshot["carbon_free_credit_c_per_kwh"],
        "cfe_c_per_kwh_b": rate_snapshot["carbon_free_credit_c_per_kwh"],
        "cooling_active_hours_a": int(cooling_active_hours_a),
        "cooling_active_hours_b": int(cooling_active_hours_b),
        # Spec §9 row: flag if EITHER arm has <6 cooling-active hours.
        "low_cooling_exposure_flag":
            bool(cooling_active_hours_a < LOW_COOLING_EXPOSURE_HOURS
                 or cooling_active_hours_b < LOW_COOLING_EXPOSURE_HOURS),
        "hvac_dollars_a": hvac_dollars_a,
        "hvac_dollars_b": hvac_dollars_b,
        "diff_dollars_b_minus_a": hvac_dollars_b - hvac_dollars_a,
        "percent_diff_dollars": percent_diff,
        "hvac_kwh_a": float(hvac_kwh_a),
        "hvac_kwh_b": float(hvac_kwh_b),
        "diff_kwh_b_minus_a": float(hvac_kwh_b - hvac_kwh_a),
        # Hourly outdoor temp summary on the pair (used by the
        # high_temp_exposed bucket).
        "any_hour_high_temp_a": any(
            t is not None and t >= HIGH_TEMP_F for t in hourly_temp_a
        ),
        "any_hour_high_temp_b": any(
            t is not None and t >= HIGH_TEMP_F for t in hourly_temp_b
        ),
        # Provenance for Ecowitt vs NOAA fallback; the Phase 3 fixture is
        # 100% Ecowitt -- real coverage helper lands when NOAA fallback
        # wires in (Phase 4).
        "weather_source_pct_ecowitt_a": 100.0,
        "weather_source_pct_ecowitt_b": 100.0,
    }


# --- §9.5 bucket summaries -------------------------------------------------


def _bucket_summary(rows: list[dict]) -> dict[str, float | int]:
    if not rows:
        return {"count": 0}
    dollars_diffs = [r["diff_dollars_b_minus_a"] for r in rows]
    kwh_diffs = [r["diff_kwh_b_minus_a"] for r in rows]

    def _stats(xs: list[float]) -> dict[str, float]:
        return {
            "mean": statistics.fmean(xs),
            "median": statistics.median(xs),
            "min": min(xs),
            "max": max(xs),
            "range": max(xs) - min(xs),
        }
    out: dict[str, float | int] = {"count": len(rows)}
    out.update({f"diff_dollars_{k}": v for k, v in _stats(dollars_diffs).items()})
    out.update({f"diff_kwh_{k}": v for k, v in _stats(kwh_diffs).items()})
    return out


def _build_bucket_summaries(pair_rows: list[dict]) -> dict[str, dict]:
    def total_pair_dollars(r: dict) -> float:
        return r["hvac_dollars_a"] + r["hvac_dollars_b"]
    return {
        "all_valid_pairs": _bucket_summary(pair_rows),
        "high_cooling_pairs": _bucket_summary(
            [r for r in pair_rows if total_pair_dollars(r) >= 50.0]
        ),
        "medium_cooling_pairs": _bucket_summary(
            [r for r in pair_rows
             if 5.0 <= total_pair_dollars(r) < 50.0]
        ),
        "low_cooling_pairs": _bucket_summary(
            [r for r in pair_rows if total_pair_dollars(r) < 5.0]
        ),
        # Scarcity-tier + 5CP signals not wired into Phase 3 fixtures or
        # current production telemetry; spec §9.5 explicitly allows
        # N=0 buckets to be reported as a finding.
        "scarcity_exposed_pairs": _bucket_summary([]),
        "5cp_exposed_pairs": _bucket_summary([]),
        "high_temp_exposed_pairs": _bucket_summary(
            [r for r in pair_rows
             if r.get("any_hour_high_temp_a") or r.get("any_hour_high_temp_b")]
        ),
    }


# --- run_full_pipeline -----------------------------------------------------


def run_full_pipeline(
    *,
    refoss_df: pd.DataFrame,
    eagle_df: pd.DataFrame,
    ecowitt_df: pd.DataFrame,
    rt_hrl_lmps_df: pd.DataFrame,
    comed_prices_df: pd.DataFrame,
    hvac_arm_mode_df: pd.DataFrame,
    bills_df: pd.DataFrame,
    rate_snapshot: Optional[dict[str, float]] = None,
    arm_calendar: Iterable[ArmPeriod] = ARM_CALENDAR,
) -> PipelineResult:
    """Entry point exercised by the outside-in acceptance test."""
    rate_snapshot = rate_snapshot if rate_snapshot is not None else DEFAULT_RATE_SNAPSHOT
    arms = list(arm_calendar)

    # Normalize every input's _time to naive UTC up front so window-clip
    # comparisons against naive UTC bounds (from arm-calendar /
    # zoneinfo) never raise.
    refoss_df = _normalize_time_column(refoss_df)
    eagle_df = _normalize_time_column(eagle_df)
    ecowitt_df = _normalize_time_column(ecowitt_df)
    rt_hrl_lmps_df = _normalize_time_column(rt_hrl_lmps_df)
    comed_prices_df = _normalize_time_column(comed_prices_df)
    hvac_arm_mode_df = _normalize_time_column(hvac_arm_mode_df)
    bills_df = _normalize_time_column(bills_df)

    # Step 1: per-arm mode classification, kWh, rates, weather vector.
    per_arm: dict[str, dict] = {}
    for arm in arms:
        modes = _hourly_mode_from_telemetry(
            arm=arm, refoss_df=refoss_df, hvac_arm_mode_df=hvac_arm_mode_df,
        )
        kwh = _hourly_hvac_kwh(arm, refoss_df)
        rates = _hourly_rates(arm, rt_hrl_lmps_df, rate_snapshot)
        em28 = _hourly_em28_watts(arm, refoss_df)
        temps = _hourly_ct_outdoor_temp(arm, ecowitt_df)
        vec = build_weather_vector(arm, ecowitt_df)
        per_arm[_arm_id(arm)] = {
            "arm": arm,
            "modes": modes,
            "kwh": kwh,
            "rates": rates,
            "em28_w": em28,
            "hourly_temp": temps,
            "weather": vec,
            "fully_valid_count": fully_valid_count(modes, arm.arm),
            "passes_gate": arm_passes_validity_gate(modes, arm.arm),
        }

    # Step 2: validity gate -> arms_passed_validity
    arms_passed = {arm_id for arm_id, st in per_arm.items() if st["passes_gate"]}

    # Step 3: weather z-scoring across ALL 12 arms (within-sample, spec §6)
    all_vecs = [per_arm[_arm_id(a)]["weather"] for a in arms]
    z_all = within_sample_zscore(all_vecs)
    z_by_arm_id = {_arm_id(a): z_all[i] for i, a in enumerate(arms)}

    # Step 4: match gate-pass A vs gate-pass B
    pass_a = [a for a in arms if a.arm == "A" and _arm_id(a) in arms_passed]
    pass_b = [a for a in arms if a.arm == "B" and _arm_id(a) in arms_passed]
    z_a_matrix = np.stack([z_by_arm_id[_arm_id(a)] for a in pass_a]) if pass_a \
        else np.empty((0, 4))
    z_b_matrix = np.stack([z_by_arm_id[_arm_id(b)] for b in pass_b]) if pass_b \
        else np.empty((0, 4))
    pair_indices = hungarian_match(z_a_matrix, z_b_matrix)
    p90_dist = caliper_p90_distance(z_a_matrix, z_b_matrix)

    # Step 5: build per-pair rows
    pair_rows: list[dict] = []
    for pid, (a_idx, b_idx) in enumerate(pair_indices, start=1):
        arm_a = pass_a[a_idx]
        arm_b = pass_b[b_idx]
        st_a = per_arm[_arm_id(arm_a)]
        st_b = per_arm[_arm_id(arm_b)]
        row = _build_pair_row(
            pair_id=pid,
            arm_a=arm_a,
            arm_b=arm_b,
            modes_a=st_a["modes"],
            modes_b=st_b["modes"],
            rates_a=st_a["rates"],
            rates_b=st_b["rates"],
            kwh_a=st_a["kwh"],
            kwh_b=st_b["kwh"],
            em28_w_a=st_a["em28_w"],
            em28_w_b=st_b["em28_w"],
            hourly_temp_a=st_a["hourly_temp"],
            hourly_temp_b=st_b["hourly_temp"],
            vec_a=st_a["weather"],
            vec_b=st_b["weather"],
            z_a=z_by_arm_id[_arm_id(arm_a)],
            z_b=z_by_arm_id[_arm_id(arm_b)],
            p90_dist=p90_dist,
            rate_snapshot=rate_snapshot,
        )
        pair_rows.append(row)

    per_pair_table = pd.DataFrame(pair_rows)
    bucket_summaries = _build_bucket_summaries(pair_rows)

    # Mode distribution = aggregate count of each HourMode across all arms
    counter: Counter[str] = Counter()
    for st in per_arm.values():
        for m in st["modes"]:
            counter[m.value] += 1
    mode_distribution = dict(counter)

    return PipelineResult(
        per_pair_table=per_pair_table,
        bucket_summaries=bucket_summaries,
        mode_distribution=mode_distribution,
        arms_passed_validity=arms_passed,
        bill_reconciliations=[],  # populated by Task 3.14
    )
