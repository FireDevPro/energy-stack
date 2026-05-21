from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import thermal_observer
from thermal_observer import (
    FitConfig,
    ThermalSample,
    build_intervals,
    fit_thermal_response,
)


def make_sample(
    i: int,
    indoor: float,
    outdoor: float,
    cool_pct: float = 0.0,
    solar: float = 0.0,
) -> ThermalSample:
    return ThermalSample(
        ts=datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=10 * i),
        indoor_temp_f=indoor,
        outdoor_temp_f=outdoor,
        solar_radiation_w_m2=solar,
        cool_actual_pct=cool_pct,
        heat_actual_pct=0.0,
        cool_setpoint_f=75.0,
        setpoint_changed=False,
    )


def test_build_intervals_drops_stage_transition_and_setpoint_change():
    samples = [
        make_sample(0, 74.0, 84.0, cool_pct=0.0),
        make_sample(1, 74.3, 84.0, cool_pct=0.0),
        make_sample(2, 73.8, 84.0, cool_pct=50.0),
        make_sample(3, 73.3, 84.0, cool_pct=50.0),
        ThermalSample(
            ts=datetime(2026, 7, 15, 0, 40, tzinfo=timezone.utc),
            indoor_temp_f=72.9,
            outdoor_temp_f=84.0,
            solar_radiation_w_m2=0.0,
            cool_actual_pct=50.0,
            heat_actual_pct=0.0,
            cool_setpoint_f=72.0,
            setpoint_changed=True,
        ),
    ]

    intervals, counts = build_intervals(samples, FitConfig(sample_minutes=10))

    assert len(intervals) == 2
    assert counts["stage_transition"] == 1
    assert counts["setpoint_change"] == 1
    assert intervals[0].indoor_delta_f == pytest.approx(0.3)
    assert intervals[1].stage1_active == 1


def test_build_intervals_masks_thirty_minutes_after_setpoint_change():
    samples = [make_sample(i, 74.0 - (0.1 * i), 84.0, cool_pct=50.0) for i in range(7)]
    samples[2] = ThermalSample(
        ts=samples[2].ts,
        indoor_temp_f=samples[2].indoor_temp_f,
        outdoor_temp_f=samples[2].outdoor_temp_f,
        solar_radiation_w_m2=samples[2].solar_radiation_w_m2,
        cool_actual_pct=samples[2].cool_actual_pct,
        heat_actual_pct=samples[2].heat_actual_pct,
        cool_setpoint_f=72.0,
        setpoint_changed=True,
    )

    intervals, counts = build_intervals(samples, FitConfig(sample_minutes=10))

    assert [(interval.start_ts, interval.end_ts) for interval in intervals] == [
        (samples[0].ts, samples[1].ts),
        (samples[5].ts, samples[6].ts),
    ]
    assert counts["setpoint_change"] == 4
    assert counts["valid"] == 2


def test_build_intervals_drops_non_finite_hvac_control_samples():
    samples = [
        make_sample(0, 74.0, 84.0, cool_pct=0.0),
        make_sample(1, 74.1, 84.0, cool_pct=np.nan),
        ThermalSample(
            ts=datetime(2026, 7, 15, 0, 20, tzinfo=timezone.utc),
            indoor_temp_f=74.2,
            outdoor_temp_f=84.0,
            solar_radiation_w_m2=0.0,
            cool_actual_pct=0.0,
            heat_actual_pct=np.inf,
            cool_setpoint_f=75.0,
            setpoint_changed=False,
        ),
        make_sample(3, 74.3, 84.0, cool_pct=0.0),
    ]

    intervals, counts = build_intervals(samples, FitConfig(sample_minutes=10))

    assert intervals == []
    assert counts["non_finite"] == 3
    assert counts["valid"] == 0
    assert counts["stage_transition"] == 0
    assert counts["heating_active"] == 0


def test_fit_thermal_response_recovers_synthetic_tau_and_cooling_rates():
    # Synthetic model:
    # dT/dt = (1/10h) * (Tout - Tin) - 1.8*stage1 - 1.2*stage2_delta + 0.001*solar
    cfg = FitConfig(sample_minutes=10, min_samples=40)
    samples: list[ThermalSample] = []
    indoor = 76.0
    for i in range(96):
        outdoor = 86.0 if i < 48 else 78.0
        cool_pct = 0.0
        if 20 <= i < 45:
            cool_pct = 50.0
        if 45 <= i < 60:
            cool_pct = 100.0
        solar = 500.0 if 36 <= i < 72 else 0.0
        samples.append(make_sample(i, indoor, outdoor, cool_pct=cool_pct, solar=solar))

        stage1 = 1.0 if cool_pct >= cfg.stage1_min_pct else 0.0
        stage2 = 1.0 if cool_pct >= cfg.stage2_min_pct else 0.0
        dtdt = 0.1 * (outdoor - indoor) - 1.8 * stage1 - 1.2 * stage2 + 0.001 * solar
        indoor = indoor + dtdt * (cfg.sample_minutes / 60.0)

    result = fit_thermal_response(samples, cfg)

    assert result.accepted is True
    assert result.tau_hours == pytest.approx(10.0, rel=0.12)
    assert result.stage1_cooling_f_per_hr == pytest.approx(1.8, rel=0.15)
    assert result.stage2_cooling_f_per_hr == pytest.approx(3.0, rel=0.15)
    assert result.skill_score > 0.80


def test_fit_rejects_holdout_skill_below_threshold():
    cfg = FitConfig(sample_minutes=10, min_samples=40, min_skill_score=0.5)
    samples: list[ThermalSample] = []
    indoor = 76.0
    for i in range(96):
        outdoor = 86.0 if i < 48 else 78.0
        cool_pct = 0.0
        if 20 <= i < 45:
            cool_pct = 50.0
        if 45 <= i < 60:
            cool_pct = 100.0
        solar = 500.0 if 36 <= i < 72 else 0.0
        samples.append(make_sample(i, indoor, outdoor, cool_pct=cool_pct, solar=solar))

        stage1 = 1.0 if cool_pct >= cfg.stage1_min_pct else 0.0
        stage2 = 1.0 if cool_pct >= cfg.stage2_min_pct else 0.0
        dtdt = 0.1 * (outdoor - indoor) - 1.8 * stage1 - 1.2 * stage2 + 0.001 * solar
        if i >= 77:
            dtdt = -dtdt * 3.0
        indoor = indoor + dtdt * (cfg.sample_minutes / 60.0)

    result = fit_thermal_response(samples, cfg)

    assert result.accepted is False
    assert result.skill_score is not None
    assert result.skill_score < cfg.min_skill_score
    assert "skill_below_threshold" in result.rejection_reasons


def test_fit_rejects_when_stage2_is_not_observed_in_training_window():
    cfg = FitConfig(sample_minutes=10, min_samples=40, min_skill_score=0.0)
    samples: list[ThermalSample] = []
    indoor = 76.0
    for i in range(96):
        outdoor = 86.0 if i < 48 else 78.0
        cool_pct = 50.0 if 20 <= i < 70 else 0.0
        samples.append(make_sample(i, indoor, outdoor, cool_pct=cool_pct))

        stage1 = 1.0 if cool_pct >= cfg.stage1_min_pct else 0.0
        dtdt = 0.1 * (outdoor - indoor) - 2.0 * stage1
        indoor = indoor + dtdt * (cfg.sample_minutes / 60.0)

    result = fit_thermal_response(samples, cfg)

    assert result.accepted is False
    assert "not_enough_stage2_samples" in result.rejection_reasons


def test_fit_rejects_when_stage2_cooling_is_less_than_stage1():
    cfg = FitConfig(sample_minutes=10, min_samples=40, min_skill_score=0.0)
    samples: list[ThermalSample] = []
    indoor = 76.0
    for i in range(96):
        outdoor = 86.0 if i < 48 else 78.0
        cool_pct = 0.0
        if 20 <= i < 45:
            cool_pct = 50.0
        if 45 <= i < 65:
            cool_pct = 100.0
        samples.append(make_sample(i, indoor, outdoor, cool_pct=cool_pct))

        stage1 = 1.0 if cool_pct >= cfg.stage1_min_pct else 0.0
        stage2 = 1.0 if cool_pct >= cfg.stage2_min_pct else 0.0
        dtdt = 0.1 * (outdoor - indoor) - 3.0 * stage1 + 1.0 * stage2
        indoor = indoor + dtdt * (cfg.sample_minutes / 60.0)

    result = fit_thermal_response(samples, cfg)

    assert result.accepted is False
    assert "stage_order_invalid" in result.rejection_reasons


def test_fit_rejects_implausible_tau():
    cfg = FitConfig(sample_minutes=10, min_samples=30, tau_min_hours=2.0, tau_max_hours=48.0)
    samples: list[ThermalSample] = []
    indoor = 74.0
    for i in range(60):
        samples.append(make_sample(i, indoor, 95.0))
        indoor += 0.001

    result = fit_thermal_response(samples, cfg)

    assert result.accepted is False
    assert "tau_out_of_bounds" in result.rejection_reasons


def test_fit_drops_non_finite_intervals_before_min_sample_check():
    cfg = FitConfig(sample_minutes=10, min_samples=4)
    samples = [
        make_sample(0, 74.0, 84.0),
        make_sample(1, 74.1, 84.0),
        make_sample(2, 74.2, np.inf),
        make_sample(3, 74.3, 84.0),
        make_sample(4, 74.4, 84.0),
    ]

    result = fit_thermal_response(samples, cfg)

    assert result.accepted is False
    assert "not_enough_samples" in result.rejection_reasons
    assert result.filter_counts["non_finite"] == 1
    assert result.filter_counts["valid"] == 3
    assert result.total_interval_count == 3


def test_fit_rejects_linear_algebra_failure(monkeypatch: pytest.MonkeyPatch):
    cfg = FitConfig(sample_minutes=10, min_samples=4)
    samples = [make_sample(i, 74.0 + (0.1 * i), 84.0) for i in range(8)]

    def fail_lstsq(*args: object, **kwargs: object) -> None:
        raise np.linalg.LinAlgError("synthetic fit failure")

    monkeypatch.setattr(thermal_observer.np.linalg, "lstsq", fail_lstsq)

    result = fit_thermal_response(samples, cfg)

    assert result.accepted is False
    assert result.rejection_reasons == ("linear_fit_failed",)
    assert result.filter_counts["non_finite"] == 0
    assert result.filter_counts["valid"] == 7
