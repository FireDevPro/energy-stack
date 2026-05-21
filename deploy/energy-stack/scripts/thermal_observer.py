from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from math import isfinite, sqrt
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ThermalSample:
    ts: datetime
    indoor_temp_f: float
    outdoor_temp_f: float
    solar_radiation_w_m2: float
    cool_actual_pct: float
    heat_actual_pct: float
    cool_setpoint_f: float | None
    setpoint_changed: bool


@dataclass(frozen=True)
class ThermalInterval:
    start_ts: datetime
    end_ts: datetime
    indoor_temp_f: float
    outdoor_temp_f: float
    solar_radiation_w_m2: float
    indoor_delta_f: float
    hours: float
    stage1_active: int
    stage2_active: int

    @property
    def indoor_delta_f_per_hr(self) -> float:
        return self.indoor_delta_f / self.hours

    @property
    def envelope_delta_f(self) -> float:
        return self.outdoor_temp_f - self.indoor_temp_f


@dataclass(frozen=True)
class FitConfig:
    sample_minutes: int = 10
    max_gap_factor: float = 1.5
    stage1_min_pct: float = 5.0
    stage2_min_pct: float = 75.0
    min_samples: int = 72
    min_stage1_samples: int = 3
    min_stage2_samples: int = 3
    min_skill_score: float = 0.5
    setpoint_mask_minutes: int = 30
    tau_min_hours: float = 2.0
    tau_max_hours: float = 48.0
    stage2_min_cooling_f_per_hr: float = 1.5
    stage2_max_cooling_f_per_hr: float = 8.0


@dataclass(frozen=True)
class ThermalFitResult:
    tau_hours: float | None
    stage1_cooling_f_per_hr: float | None
    stage2_cooling_f_per_hr: float | None
    solar_coupling_f_per_hr_per_w_m2: float | None
    intercept_f_per_hr: float | None
    train_sample_count: int
    test_sample_count: int
    total_interval_count: int
    filter_counts: dict[str, int]
    train_rmse_f_per_sample: float | None
    test_rmse_f_per_sample: float | None
    persistence_rmse_f_per_sample: float | None
    skill_score: float | None
    accepted: bool
    rejection_reasons: tuple[str, ...]
    fit_window_start: str | None
    fit_window_end: str | None
    sample_minutes: int

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_intervals(samples: list[ThermalSample], cfg: FitConfig) -> tuple[list[ThermalInterval], dict[str, int]]:
    ordered = sorted(samples, key=lambda sample: sample.ts)
    counts = {
        "input_samples": len(ordered),
        "gap": 0,
        "stage_transition": 0,
        "setpoint_change": 0,
        "heating_active": 0,
        "non_finite": 0,
        "valid": 0,
    }
    intervals: list[ThermalInterval] = []
    expected_hours = cfg.sample_minutes / 60.0
    max_gap_hours = expected_hours * cfg.max_gap_factor
    setpoint_mask_until: datetime | None = None

    for prev, cur in zip(ordered, ordered[1:]):
        gap_hours = (cur.ts - prev.ts).total_seconds() / 3600.0
        if gap_hours <= 0 or gap_hours > max_gap_hours:
            counts["gap"] += 1
            continue
        if setpoint_mask_until is not None and prev.ts < setpoint_mask_until:
            counts["setpoint_change"] += 1
            continue
        if cur.setpoint_changed:
            counts["setpoint_change"] += 1
            setpoint_mask_until = cur.ts + timedelta(minutes=cfg.setpoint_mask_minutes)
            continue
        if not _has_finite_hvac_controls(prev) or not _has_finite_hvac_controls(cur):
            counts["non_finite"] += 1
            continue
        if _stage_bucket(prev, cfg) != _stage_bucket(cur, cfg):
            counts["stage_transition"] += 1
            continue
        if prev.heat_actual_pct > 0 or cur.heat_actual_pct > 0:
            counts["heating_active"] += 1
            continue

        stage1 = 1 if prev.cool_actual_pct >= cfg.stage1_min_pct else 0
        stage2 = 1 if prev.cool_actual_pct >= cfg.stage2_min_pct else 0
        intervals.append(
            ThermalInterval(
                start_ts=prev.ts,
                end_ts=cur.ts,
                indoor_temp_f=prev.indoor_temp_f,
                outdoor_temp_f=prev.outdoor_temp_f,
                solar_radiation_w_m2=prev.solar_radiation_w_m2,
                indoor_delta_f=cur.indoor_temp_f - prev.indoor_temp_f,
                hours=gap_hours,
                stage1_active=stage1,
                stage2_active=stage2,
            )
        )
        counts["valid"] += 1

    return intervals, counts


def fit_thermal_response(samples: list[ThermalSample], cfg: FitConfig) -> ThermalFitResult:
    intervals, counts = build_intervals(samples, cfg)
    intervals = _finite_intervals(intervals, counts)
    if len(intervals) < cfg.min_samples:
        return _rejected(intervals, counts, cfg, ("not_enough_samples",))

    split = max(1, int(len(intervals) * 0.8))
    train = intervals[:split]
    test = intervals[split:]

    x_train = _design_matrix(train)
    y_train = np.array([interval.indoor_delta_f_per_hr for interval in train], dtype=float)
    try:
        coef, *_ = np.linalg.lstsq(x_train, y_train, rcond=None)
    except np.linalg.LinAlgError:
        return _rejected(intervals, counts, cfg, ("linear_fit_failed",))

    env_coef, stage1_coef, stage2_delta_coef, solar_coef, intercept = [float(value) for value in coef]
    tau_hours = None if env_coef <= 0 else 1.0 / env_coef
    stage1_cooling = -stage1_coef
    stage2_cooling = -(stage1_coef + stage2_delta_coef)
    stage1_only_count = sum(
        1 for interval in train if interval.stage1_active and not interval.stage2_active
    )
    stage2_count = sum(1 for interval in train if interval.stage2_active)

    rejection_reasons: list[str] = []
    if stage1_only_count < cfg.min_stage1_samples:
        rejection_reasons.append("not_enough_stage1_samples")
    if stage2_count < cfg.min_stage2_samples:
        rejection_reasons.append("not_enough_stage2_samples")
    if tau_hours is None or tau_hours < cfg.tau_min_hours or tau_hours > cfg.tau_max_hours:
        rejection_reasons.append("tau_out_of_bounds")
    if stage1_cooling <= 0:
        rejection_reasons.append("stage1_cooling_out_of_bounds")
    if (
        stage2_cooling < cfg.stage2_min_cooling_f_per_hr
        or stage2_cooling > cfg.stage2_max_cooling_f_per_hr
    ):
        rejection_reasons.append("stage2_cooling_out_of_bounds")
    if stage2_cooling < stage1_cooling:
        rejection_reasons.append("stage_order_invalid")

    train_rmse = _rmse_delta_f(train, coef)
    test_rmse = _rmse_delta_f(test, coef) if test else None
    persistence_rmse = _persistence_rmse_delta_f(test) if test else None
    skill_score = None
    if test_rmse is not None and persistence_rmse is not None and persistence_rmse > 0:
        skill_score = 1.0 - (test_rmse / persistence_rmse)
    if skill_score is None:
        rejection_reasons.append("skill_unavailable")
    elif skill_score < cfg.min_skill_score:
        rejection_reasons.append("skill_below_threshold")

    return ThermalFitResult(
        tau_hours=tau_hours,
        stage1_cooling_f_per_hr=stage1_cooling,
        stage2_cooling_f_per_hr=stage2_cooling,
        solar_coupling_f_per_hr_per_w_m2=solar_coef,
        intercept_f_per_hr=intercept,
        train_sample_count=len(train),
        test_sample_count=len(test),
        total_interval_count=len(intervals),
        filter_counts=counts,
        train_rmse_f_per_sample=train_rmse,
        test_rmse_f_per_sample=test_rmse,
        persistence_rmse_f_per_sample=persistence_rmse,
        skill_score=skill_score,
        accepted=not rejection_reasons,
        rejection_reasons=tuple(rejection_reasons),
        fit_window_start=intervals[0].start_ts.isoformat(),
        fit_window_end=intervals[-1].end_ts.isoformat(),
        sample_minutes=cfg.sample_minutes,
    )


def _design_matrix(intervals: list[ThermalInterval]) -> np.ndarray:
    return np.array(
        [
            [
                interval.envelope_delta_f,
                float(interval.stage1_active),
                float(interval.stage2_active),
                interval.solar_radiation_w_m2,
                1.0,
            ]
            for interval in intervals
        ],
        dtype=float,
    )


def _finite_intervals(
    intervals: list[ThermalInterval],
    counts: dict[str, int],
) -> list[ThermalInterval]:
    finite_intervals = [interval for interval in intervals if _is_finite_interval(interval)]
    counts["non_finite"] += len(intervals) - len(finite_intervals)
    counts["valid"] = len(finite_intervals)
    return finite_intervals


def _has_finite_hvac_controls(sample: ThermalSample) -> bool:
    return isfinite(sample.cool_actual_pct) and isfinite(sample.heat_actual_pct)


def _is_finite_interval(interval: ThermalInterval) -> bool:
    return all(
        isfinite(value)
        for value in (
            interval.indoor_temp_f,
            interval.outdoor_temp_f,
            interval.solar_radiation_w_m2,
            interval.indoor_delta_f,
            interval.hours,
            interval.indoor_delta_f_per_hr,
            interval.envelope_delta_f,
        )
    )


def _rmse_delta_f(intervals: list[ThermalInterval], coef: np.ndarray) -> float:
    if not intervals:
        return 0.0
    predicted_per_hr = _design_matrix(intervals) @ coef
    errors = [
        (predicted_per_hr[index] * interval.hours) - interval.indoor_delta_f
        for index, interval in enumerate(intervals)
    ]
    return sqrt(sum(float(error) ** 2 for error in errors) / len(errors))


def _persistence_rmse_delta_f(intervals: list[ThermalInterval]) -> float:
    if not intervals:
        return 0.0
    return sqrt(sum(interval.indoor_delta_f**2 for interval in intervals) / len(intervals))


def _stage_bucket(sample: ThermalSample, cfg: FitConfig) -> str:
    if sample.cool_actual_pct >= cfg.stage2_min_pct:
        return "stage2"
    if sample.cool_actual_pct >= cfg.stage1_min_pct:
        return "stage1"
    return "off"


def _rejected(
    intervals: list[ThermalInterval],
    counts: dict[str, int],
    cfg: FitConfig,
    reasons: tuple[str, ...],
) -> ThermalFitResult:
    return ThermalFitResult(
        tau_hours=None,
        stage1_cooling_f_per_hr=None,
        stage2_cooling_f_per_hr=None,
        solar_coupling_f_per_hr_per_w_m2=None,
        intercept_f_per_hr=None,
        train_sample_count=0,
        test_sample_count=0,
        total_interval_count=len(intervals),
        filter_counts=counts,
        train_rmse_f_per_sample=None,
        test_rmse_f_per_sample=None,
        persistence_rmse_f_per_sample=None,
        skill_score=None,
        accepted=False,
        rejection_reasons=reasons,
        fit_window_start=intervals[0].start_ts.isoformat() if intervals else None,
        fit_window_end=intervals[-1].end_ts.isoformat() if intervals else None,
        sample_minutes=cfg.sample_minutes,
    )
