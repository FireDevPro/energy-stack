# Thermal Observer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**2026-05-20 amendment:** User clarified that "read-only" means no writes at all for this slice: no thermostat settings, no scheduler inputs, no derived InfluxDB measurement, and no JSON artifact. Any older task detail below that mentions line protocol, JSON artifacts, or write-mode runs is superseded by this amendment.

**Goal:** Build a strictly read-only thermal observer that fits the house's warm-up/cool-down behavior from existing telemetry and prints validated model diagnostics without changing HVAC control behavior or writing derived data.

**Architecture:** Add a small scripts-side analysis package: one pure Python module for thermal-response math, one InfluxDB read adapter module, and one CLI entrypoint that always runs read-only. The first version estimates envelope time constant, stage cooling rates, solar coupling, fit quality, and sample-filter counts, then prints diagnostics for manual or cron-log inspection.

**Tech Stack:** Python 3, `numpy` for least-squares fitting, `influxdb-client` for reads, pytest for unit tests, existing `deploy/energy-stack/scripts` operational-script pattern.

---

## Scope

This plan implements a separate thermal-observer subproject. It does not modify `hvac-scheduler`, `safety_supervisor.py`, experiment assignments, OSF-bound controller behavior, Docker compose services, thermostat setpoint logic, or any existing InfluxDB measurement.

The implementation uses current telemetry:

- `hvac.thermostat`: `indoor_temp_f`, `cool_setpoint_f`
- `hvac.comfortnet`: `cool_actual_pct`, `heat_actual_pct`
- Local weather measurement configured by CLI, defaulting to `ecowitt.outdoor`: `outdoor_temp_f`, `solar_radiation_w_m2`

Setpoint-change masking is inferred from `hvac.thermostat.cool_setpoint_f` changes in the resampled data.

There is no output measurement in this slice. Later post-experiment persistence of model diagnostics requires a separate plan.

## File Structure

- Create `deploy/energy-stack/scripts/thermal_observer.py`
  - Pure dataclasses, sample filtering, design-matrix construction, OLS fit, validation, and JSON-safe result conversion.
- Create `deploy/energy-stack/scripts/thermal_observer_influx.py`
  - Flux query builders and Influx row parsing only.
- Create `deploy/energy-stack/scripts/fit_thermal_observer.py`
  - CLI entrypoint for Pi-lab/manual runs. Reads env, fetches telemetry, runs fit, and prints summary.
- Create `deploy/energy-stack/scripts/tests/test_thermal_observer.py`
  - Unit tests for fitting, filtering, physical gates, and validation skill.
- Create `deploy/energy-stack/scripts/tests/test_thermal_observer_influx.py`
  - Unit tests for Flux query strings and row parsing.
- Modify `deploy/energy-stack/scripts/requirements.txt`
  - Add `numpy`.
- Modify `deploy/energy-stack/scripts/README.md`
  - Add operator usage, env vars, printed diagnostics, and suggested cron.
- Modify `docs/THERMAL_MODEL_DESIGN.md`
  - Add a short status note that the first implemented artifact is a read-only thermal observer, not scheduler integration.

## Task 1: Pure Thermal-Response Engine

**Files:**
- Create: `deploy/energy-stack/scripts/thermal_observer.py`
- Create: `deploy/energy-stack/scripts/tests/test_thermal_observer.py`
- Modify: `deploy/energy-stack/scripts/requirements.txt`

- [ ] **Step 1: Add the numerical dependency**

Add this line to `deploy/energy-stack/scripts/requirements.txt`:

```text
numpy==2.2.6
```

- [ ] **Step 2: Write failing tests for synthetic fits and physical gates**

Create `deploy/energy-stack/scripts/tests/test_thermal_observer.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from thermal_observer import (
    FitConfig,
    ThermalSample,
    build_intervals,
    fit_thermal_response,
)


def make_sample(i: int, indoor: float, outdoor: float, cool_pct: float = 0.0, solar: float = 0.0) -> ThermalSample:
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
```

- [ ] **Step 3: Run tests and verify they fail because the module does not exist**

Run:

```bash
cd deploy/energy-stack/scripts
python -m pytest tests/test_thermal_observer.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'thermal_observer'
```

- [ ] **Step 4: Implement the pure module**

Create `deploy/energy-stack/scripts/thermal_observer.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import sqrt
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
    ordered = sorted(samples, key=lambda s: s.ts)
    counts = {
        "input_samples": len(ordered),
        "gap": 0,
        "stage_transition": 0,
        "setpoint_change": 0,
        "heating_active": 0,
        "valid": 0,
    }
    intervals: list[ThermalInterval] = []
    expected_hours = cfg.sample_minutes / 60.0
    max_gap_hours = expected_hours * cfg.max_gap_factor

    for prev, cur in zip(ordered, ordered[1:]):
        gap_hours = (cur.ts - prev.ts).total_seconds() / 3600.0
        if gap_hours <= 0 or gap_hours > max_gap_hours:
            counts["gap"] += 1
            continue
        if cur.setpoint_changed:
            counts["setpoint_change"] += 1
            continue
        if _stage_bucket(prev, cfg) != _stage_bucket(cur, cfg):
            counts["stage_transition"] += 1
            continue
        if prev.heat_actual_pct > 0 or cur.heat_actual_pct > 0:
            counts["heating_active"] += 1
            continue

        stage1 = 1 if prev.cool_actual_pct >= cfg.stage1_min_pct else 0
        stage2 = 1 if prev.cool_actual_pct >= cfg.stage2_min_pct else 0
        intervals.append(ThermalInterval(
            start_ts=prev.ts,
            end_ts=cur.ts,
            indoor_temp_f=prev.indoor_temp_f,
            outdoor_temp_f=prev.outdoor_temp_f,
            solar_radiation_w_m2=prev.solar_radiation_w_m2,
            indoor_delta_f=cur.indoor_temp_f - prev.indoor_temp_f,
            hours=gap_hours,
            stage1_active=stage1,
            stage2_active=stage2,
        ))
        counts["valid"] += 1

    return intervals, counts


def fit_thermal_response(samples: list[ThermalSample], cfg: FitConfig) -> ThermalFitResult:
    intervals, counts = build_intervals(samples, cfg)
    if len(intervals) < cfg.min_samples:
        return _rejected(intervals, counts, cfg, ("not_enough_samples",))

    split = max(1, int(len(intervals) * 0.8))
    train = intervals[:split]
    test = intervals[split:]
    x_train = _design_matrix(train)
    y_train = np.array([i.indoor_delta_f_per_hr for i in train], dtype=float)
    coef, *_ = np.linalg.lstsq(x_train, y_train, rcond=None)

    env_coef, stage1_coef, stage2_delta_coef, solar_coef, intercept = [float(v) for v in coef]
    tau_hours = None if env_coef <= 0 else 1.0 / env_coef
    stage1_cooling = -stage1_coef
    stage2_cooling = -(stage1_coef + stage2_delta_coef)

    rejection_reasons: list[str] = []
    if tau_hours is None or tau_hours < cfg.tau_min_hours or tau_hours > cfg.tau_max_hours:
        rejection_reasons.append("tau_out_of_bounds")
    if stage2_cooling < cfg.stage2_min_cooling_f_per_hr or stage2_cooling > cfg.stage2_max_cooling_f_per_hr:
        rejection_reasons.append("stage2_cooling_out_of_bounds")

    train_rmse = _rmse_delta_f(train, coef)
    test_rmse = _rmse_delta_f(test, coef) if test else None
    persistence_rmse = _persistence_rmse_delta_f(test) if test else None
    skill = None
    if test_rmse is not None and persistence_rmse is not None and persistence_rmse > 0:
        skill = 1.0 - (test_rmse / persistence_rmse)

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
        skill_score=skill,
        accepted=not rejection_reasons,
        rejection_reasons=tuple(rejection_reasons),
        fit_window_start=intervals[0].start_ts.isoformat(),
        fit_window_end=intervals[-1].end_ts.isoformat(),
        sample_minutes=cfg.sample_minutes,
    )


def _design_matrix(intervals: list[ThermalInterval]) -> np.ndarray:
    return np.array([
        [
            i.envelope_delta_f,
            float(i.stage1_active),
            float(i.stage2_active),
            i.solar_radiation_w_m2,
            1.0,
        ]
        for i in intervals
    ], dtype=float)


def _rmse_delta_f(intervals: list[ThermalInterval], coef: np.ndarray) -> float:
    if not intervals:
        return 0.0
    predicted_per_hr = _design_matrix(intervals) @ coef
    errors = [
        (predicted_per_hr[idx] * interval.hours) - interval.indoor_delta_f
        for idx, interval in enumerate(intervals)
    ]
    return sqrt(sum(float(e) ** 2 for e in errors) / len(errors))


def _persistence_rmse_delta_f(intervals: list[ThermalInterval]) -> float:
    if not intervals:
        return 0.0
    return sqrt(sum(i.indoor_delta_f ** 2 for i in intervals) / len(intervals))


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
```

- [ ] **Step 5: Run tests and verify they pass**

Run:

```bash
cd deploy/energy-stack/scripts
python -m pytest tests/test_thermal_observer.py -v
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add deploy/energy-stack/scripts/requirements.txt deploy/energy-stack/scripts/thermal_observer.py deploy/energy-stack/scripts/tests/test_thermal_observer.py
git commit -m "feat: add read-only thermal observer fit engine"
```

## Task 2: Influx Query and Serialization Layer

**Files:**
- Create: `deploy/energy-stack/scripts/thermal_observer_influx.py`
- Create: `deploy/energy-stack/scripts/tests/test_thermal_observer_influx.py`

- [ ] **Step 1: Write failing tests for query generation and line protocol**

Create `deploy/energy-stack/scripts/tests/test_thermal_observer_influx.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from thermal_observer import ThermalFitResult
from thermal_observer_influx import (
    build_line_protocol,
    build_query,
    json_artifact_payload,
)


def make_result() -> ThermalFitResult:
    return ThermalFitResult(
        tau_hours=9.5,
        stage1_cooling_f_per_hr=1.6,
        stage2_cooling_f_per_hr=3.1,
        solar_coupling_f_per_hr_per_w_m2=0.0012,
        intercept_f_per_hr=0.02,
        train_sample_count=80,
        test_sample_count=20,
        total_interval_count=100,
        filter_counts={"input_samples": 120, "valid": 100, "gap": 3},
        train_rmse_f_per_sample=0.05,
        test_rmse_f_per_sample=0.07,
        persistence_rmse_f_per_sample=0.2,
        skill_score=0.65,
        accepted=True,
        rejection_reasons=(),
        fit_window_start="2026-07-01T00:00:00+00:00",
        fit_window_end="2026-07-15T00:00:00+00:00",
        sample_minutes=10,
    )


def test_build_query_uses_configured_local_weather_measurement():
    query = build_query(
        bucket="energy",
        start="-14d",
        sample_minutes=10,
        outdoor_measurement="ecowitt.outdoor",
        outdoor_temp_field="outdoor_temp_f",
        solar_field="solar_radiation_w_m2",
    )

    assert 'r._measurement == "ecowitt.outdoor"' in query
    assert 'r._measurement == "hvac.thermostat"' in query
    assert 'r._measurement == "hvac.comfortnet"' in query
    assert 'aggregateWindow(every: 10m, fn: mean' in query


def test_line_protocol_contains_read_only_tags_and_fields():
    line = build_line_protocol(
        make_result(),
        outdoor_measurement="ecowitt.outdoor",
        generated_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    )

    assert line.startswith("hvac.thermal_observer,")
    assert "model_version=thermal_observer.v1" in line
    assert "outdoor_measurement=ecowitt.outdoor" in line
    assert "read_only=true" in line
    assert "accepted=true" in line
    assert "tau_hours=9.5" in line
    assert "stage2_cooling_f_per_hr=3.1" in line
    assert "filter_gap=3i" in line


def test_json_artifact_payload_is_stable_and_explicitly_read_only():
    payload = json_artifact_payload(
        make_result(),
        outdoor_measurement="ecowitt.outdoor",
        generated_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    )

    assert payload["model_version"] == "thermal_observer.v1"
    assert payload["read_only"] is True
    assert payload["outdoor_measurement"] == "ecowitt.outdoor"
    assert payload["result"]["tau_hours"] == 9.5
```

- [ ] **Step 2: Run tests and verify they fail because the module does not exist**

Run:

```bash
cd deploy/energy-stack/scripts
python -m pytest tests/test_thermal_observer_influx.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'thermal_observer_influx'
```

- [ ] **Step 3: Implement query builders and output serialization**

Create `deploy/energy-stack/scripts/thermal_observer_influx.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from thermal_observer import ThermalFitResult

MODEL_VERSION = "thermal_observer.v1"
MEASUREMENT = "hvac.thermal_observer"


def build_query(
    bucket: str,
    start: str,
    sample_minutes: int,
    outdoor_measurement: str,
    outdoor_temp_field: str,
    solar_field: str,
) -> str:
    window = f"{sample_minutes}m"
    return f'''
thermostat = from(bucket: "{bucket}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "hvac.thermostat")
  |> filter(fn: (r) => r._field == "indoor_temp_f" or r._field == "cool_setpoint_f")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "indoor_temp_f", "cool_setpoint_f"])

comfortnet = from(bucket: "{bucket}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "hvac.comfortnet")
  |> filter(fn: (r) => r._field == "cool_actual_pct" or r._field == "heat_actual_pct")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "cool_actual_pct", "heat_actual_pct"])

weather = from(bucket: "{bucket}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "{outdoor_measurement}")
  |> filter(fn: (r) => r._field == "{outdoor_temp_field}" or r._field == "{solar_field}")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> rename(columns: {{"{outdoor_temp_field}": "outdoor_temp_f", "{solar_field}": "solar_radiation_w_m2"}})
  |> keep(columns: ["_time", "outdoor_temp_f", "solar_radiation_w_m2"])

join1 = join(tables: {{thermostat: thermostat, comfortnet: comfortnet}}, on: ["_time"])
join(tables: {{joined: join1, weather: weather}}, on: ["_time"])
  |> sort(columns: ["_time"])
'''


def build_line_protocol(result: ThermalFitResult, outdoor_measurement: str, generated_at: datetime) -> str:
    tags = {
        "model_version": MODEL_VERSION,
        "outdoor_measurement": outdoor_measurement,
        "read_only": "true",
        "accepted": "true" if result.accepted else "false",
    }
    fields: dict[str, int | float | str] = {
        "sample_minutes": result.sample_minutes,
        "train_sample_count": result.train_sample_count,
        "test_sample_count": result.test_sample_count,
        "total_interval_count": result.total_interval_count,
        "rejection_reasons": ",".join(result.rejection_reasons),
    }
    optional_float_fields = {
        "tau_hours": result.tau_hours,
        "stage1_cooling_f_per_hr": result.stage1_cooling_f_per_hr,
        "stage2_cooling_f_per_hr": result.stage2_cooling_f_per_hr,
        "solar_coupling_f_per_hr_per_w_m2": result.solar_coupling_f_per_hr_per_w_m2,
        "intercept_f_per_hr": result.intercept_f_per_hr,
        "train_rmse_f_per_sample": result.train_rmse_f_per_sample,
        "test_rmse_f_per_sample": result.test_rmse_f_per_sample,
        "persistence_rmse_f_per_sample": result.persistence_rmse_f_per_sample,
        "skill_score": result.skill_score,
    }
    for name, value in optional_float_fields.items():
        if value is not None:
            fields[name] = float(value)
    for name, value in result.filter_counts.items():
        fields[f"filter_{name}"] = int(value)

    tag_text = ",".join(f"{_esc(k)}={_esc(str(v))}" for k, v in tags.items())
    field_text = ",".join(f"{_esc(k)}={_field(v)}" for k, v in fields.items())
    ts_ns = int(generated_at.timestamp() * 1_000_000_000)
    return f"{MEASUREMENT},{tag_text} {field_text} {ts_ns}"


def json_artifact_payload(
    result: ThermalFitResult,
    outdoor_measurement: str,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "model_version": MODEL_VERSION,
        "read_only": True,
        "generated_at": generated_at.isoformat(),
        "outdoor_measurement": outdoor_measurement,
        "result": result.to_json_dict(),
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp.write("\\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _field(value: int | float | str) -> str:
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    return json.dumps(value)


def _esc(value: str) -> str:
    return value.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
```

- [ ] **Step 4: Run Influx-layer tests and verify they pass**

Run:

```bash
cd deploy/energy-stack/scripts
python -m pytest tests/test_thermal_observer_influx.py -v
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add deploy/energy-stack/scripts/thermal_observer_influx.py deploy/energy-stack/scripts/tests/test_thermal_observer_influx.py
git commit -m "feat: add thermal observer Influx serialization"
```

## Task 3: CLI Entrypoint

**Files:**
- Create: `deploy/energy-stack/scripts/fit_thermal_observer.py`
- Modify: `deploy/energy-stack/scripts/tests/test_thermal_observer_influx.py`

- [ ] **Step 1: Add parser tests for real Influx rows**

Append this test to `deploy/energy-stack/scripts/tests/test_thermal_observer_influx.py`:

```python
from thermal_observer_influx import rows_to_samples


def test_rows_to_samples_marks_setpoint_changes():
    rows = [
        {
            "_time": datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
            "indoor_temp_f": 74.0,
            "outdoor_temp_f": 84.0,
            "solar_radiation_w_m2": 0.0,
            "cool_actual_pct": 0.0,
            "heat_actual_pct": 0.0,
            "cool_setpoint_f": 75.0,
        },
        {
            "_time": datetime(2026, 7, 15, 0, 10, tzinfo=timezone.utc),
            "indoor_temp_f": 74.2,
            "outdoor_temp_f": 84.0,
            "solar_radiation_w_m2": 0.0,
            "cool_actual_pct": 0.0,
            "heat_actual_pct": 0.0,
            "cool_setpoint_f": 72.0,
        },
    ]

    samples = rows_to_samples(rows)

    assert len(samples) == 2
    assert samples[0].setpoint_changed is False
    assert samples[1].setpoint_changed is True
```

- [ ] **Step 2: Implement row parsing in the Influx helper**

Add this function to `deploy/energy-stack/scripts/thermal_observer_influx.py`:

```python
from thermal_observer import ThermalSample


def rows_to_samples(rows: list[dict[str, Any]]) -> list[ThermalSample]:
    samples: list[ThermalSample] = []
    previous_setpoint: float | None = None
    for row in rows:
        if row.get("indoor_temp_f") is None or row.get("outdoor_temp_f") is None:
            continue
        cool_setpoint = _optional_float(row.get("cool_setpoint_f"))
        setpoint_changed = (
            previous_setpoint is not None
            and cool_setpoint is not None
            and abs(cool_setpoint - previous_setpoint) >= 0.5
        )
        if cool_setpoint is not None:
            previous_setpoint = cool_setpoint
        samples.append(ThermalSample(
            ts=row["_time"],
            indoor_temp_f=float(row["indoor_temp_f"]),
            outdoor_temp_f=float(row["outdoor_temp_f"]),
            solar_radiation_w_m2=float(row.get("solar_radiation_w_m2") or 0.0),
            cool_actual_pct=float(row.get("cool_actual_pct") or 0.0),
            heat_actual_pct=float(row.get("heat_actual_pct") or 0.0),
            cool_setpoint_f=cool_setpoint,
            setpoint_changed=setpoint_changed,
        ))
    return samples


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
```

- [ ] **Step 3: Create the CLI**

Create `deploy/energy-stack/scripts/fit_thermal_observer.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from influxdb_client import InfluxDBClient, WritePrecision

from thermal_observer import FitConfig, fit_thermal_response
from thermal_observer_influx import (
    build_line_protocol,
    build_query,
    json_artifact_payload,
    rows_to_samples,
    write_json_atomic,
)


def main() -> int:
    args = parse_args()
    cfg = FitConfig(
        sample_minutes=args.sample_minutes,
        min_samples=args.min_samples,
        stage1_min_pct=args.stage1_min_pct,
        stage2_min_pct=args.stage2_min_pct,
    )
    influx_url = env_required("INFLUX_URL")
    influx_token = env_required("INFLUX_TOKEN")
    influx_org = env_required("INFLUX_ORG")
    bucket = env_required("INFLUX_BUCKET")

    query = build_query(
        bucket=bucket,
        start=f"-{args.window_days}d",
        sample_minutes=args.sample_minutes,
        outdoor_measurement=args.outdoor_measurement,
        outdoor_temp_field=args.outdoor_temp_field,
        solar_field=args.solar_field,
    )

    with InfluxDBClient(url=influx_url, token=influx_token, org=influx_org) as client:
        rows = query_rows(client, query)
        samples = rows_to_samples(rows)
        result = fit_thermal_response(samples, cfg)
        generated_at = datetime.now(timezone.utc)
        print_summary(result, sample_count=len(samples), dry_run=args.dry_run)

        if args.dry_run:
            return 0 if result.total_interval_count > 0 else 1

        payload = json_artifact_payload(result, args.outdoor_measurement, generated_at)
        write_json_atomic(args.output_json, payload)
        line = build_line_protocol(result, args.outdoor_measurement, generated_at)
        client.write_api().write(bucket=bucket, org=influx_org, record=line, write_precision=WritePrecision.NS)
    return 0 if result.total_interval_count > 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fit read-only thermal response model from energy-stack telemetry.")
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--sample-minutes", type=int, default=10)
    parser.add_argument("--min-samples", type=int, default=72)
    parser.add_argument("--stage1-min-pct", type=float, default=5.0)
    parser.add_argument("--stage2-min-pct", type=float, default=75.0)
    parser.add_argument("--outdoor-measurement", default="ecowitt.outdoor")
    parser.add_argument("--outdoor-temp-field", default="outdoor_temp_f")
    parser.add_argument("--solar-field", default="solar_radiation_w_m2")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path.home() / "energy-stack" / "runtime" / "thermal_observer_latest.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def query_rows(client: InfluxDBClient, query: str) -> list[dict]:
    rows: list[dict] = []
    for table in client.query_api().query(query):
        for record in table.records:
            rows.append(record.values)
    return rows


def print_summary(result, sample_count: int, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "WRITE"
    print(f"thermal_observer mode={mode} input_samples={sample_count} intervals={result.total_interval_count}")
    print(f"accepted={result.accepted} reasons={','.join(result.rejection_reasons) or 'none'}")
    print(f"tau_hours={result.tau_hours}")
    print(f"stage1_cooling_f_per_hr={result.stage1_cooling_f_per_hr}")
    print(f"stage2_cooling_f_per_hr={result.stage2_cooling_f_per_hr}")
    print(f"skill_score={result.skill_score}")
    print(f"filters={result.filter_counts}")


def env_required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd deploy/energy-stack/scripts
python -m pytest tests/test_thermal_observer.py tests/test_thermal_observer_influx.py -v
```

Expected:

```text
7 passed
```

- [ ] **Step 5: Run dry-run help command**

Run:

```bash
cd deploy/energy-stack/scripts
python fit_thermal_observer.py --help
```

Expected:

```text
usage: fit_thermal_observer.py
```

- [ ] **Step 6: Commit Task 3**

Run:

```bash
git add deploy/energy-stack/scripts/fit_thermal_observer.py deploy/energy-stack/scripts/thermal_observer_influx.py deploy/energy-stack/scripts/tests/test_thermal_observer_influx.py
git commit -m "feat: add thermal observer CLI"
```

## Task 4: Operator Documentation

**Files:**
- Modify: `deploy/energy-stack/scripts/README.md`
- Modify: `docs/THERMAL_MODEL_DESIGN.md`

- [ ] **Step 1: Add the script README section**

Append this section to `deploy/energy-stack/scripts/README.md`:

```markdown
## fit_thermal_observer.py — read-only house thermal response fit

Fits a read-only thermal response model from existing telemetry. This is a
diagnostic observer, not an HVAC controller. It does not write setpoints and
does not feed `hvac-scheduler`.

### Inputs

- `hvac.thermostat`: indoor temperature and cooling setpoint
- `hvac.comfortnet`: cooling/heating actual percentage
- local weather measurement, default `ecowitt.outdoor`: outdoor temperature and solar radiation

If the deployed local-weather measurement uses a different name, pass it at
runtime:

```bash
python fit_thermal_observer.py --outdoor-measurement weather.ecowitt --dry-run
```

### One-time setup on Pi-lab

```bash
cd ~/energy-stack/scripts
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Dry-run

```bash
cd ~/energy-stack/scripts
source .venv/bin/activate
set -a; source ~/energy-stack/.env; set +a
export INFLUX_URL=http://localhost:8086
export INFLUX_TOKEN="$INFLUXDB_INIT_ADMIN_TOKEN"
export INFLUX_ORG="$INFLUXDB_INIT_ORG"
export INFLUX_BUCKET="$INFLUXDB_INIT_BUCKET"
python fit_thermal_observer.py --window-days 14 --dry-run
```

### Write results

```bash
python fit_thermal_observer.py --window-days 14
```

### Output

The script writes one `hvac.thermal_observer` point and one JSON artifact at
`~/energy-stack/runtime/thermal_observer_latest.json` by default. The
measurement is separate from `hvac.thermal_model` so read-only exploratory fits
cannot be confused with ratified scheduler inputs.

Key fields:

| Field | Meaning |
|---|---|
| `tau_hours` | Estimated envelope time constant |
| `stage1_cooling_f_per_hr` | Estimated stage-1 cooling rate |
| `stage2_cooling_f_per_hr` | Estimated full stage-2 cooling rate |
| `solar_coupling_f_per_hr_per_w_m2` | Estimated solar coupling |
| `skill_score` | Holdout skill vs persistence |
| `accepted` tag | Physical plausibility gates passed |
| `read_only` tag | Always `true` |

### Suggested cron after manual validation

```cron
20 2 * * * cd ~/energy-stack/scripts && . .venv/bin/activate && \
  set -a; . ~/energy-stack/.env; set +a && \
  export INFLUX_URL=http://localhost:8086 INFLUX_TOKEN="$INFLUXDB_INIT_ADMIN_TOKEN" \
  INFLUX_ORG="$INFLUXDB_INIT_ORG" INFLUX_BUCKET="$INFLUXDB_INIT_BUCKET" && \
  python fit_thermal_observer.py --window-days 21 \
  >> /var/log/thermal-observer.log 2>&1
```
```

- [ ] **Step 2: Add a thermal-model design status note**

Add this note near the top of `docs/THERMAL_MODEL_DESIGN.md`, immediately after the existing metadata block:

```markdown
> **Implementation note (2026-05-20):** the first implementation slice is a
> separate read-only `thermal_observer` script under `deploy/energy-stack/scripts`.
> It estimates house thermal response and writes diagnostics to
> `hvac.thermal_observer`; it does not feed scheduler decisions. Scheduler
> integration remains a later, explicit change after the observer produces
> stable and physically plausible fits.
```

- [ ] **Step 3: Commit Task 4**

Run:

```bash
git add deploy/energy-stack/scripts/README.md docs/THERMAL_MODEL_DESIGN.md
git commit -m "docs: document thermal observer workflow"
```

## Task 5: Verification and First Dry Run

**Files:**
- No source-file changes expected unless verification exposes a defect.

- [ ] **Step 1: Run the scripts test suite**

Run:

```bash
cd deploy/energy-stack/scripts
python -m pytest . -v
```

Expected:

```text
passed
```

- [ ] **Step 2: Run the stack wrapper**

Run:

```bash
bash deploy/energy-stack/run_tests.sh
```

Expected:

```text
OK
```

- [ ] **Step 3: Run a local CLI smoke test**

Run:

```bash
cd deploy/energy-stack/scripts
python fit_thermal_observer.py --help
```

Expected:

```text
usage: fit_thermal_observer.py
```

- [ ] **Step 4: Run Pi-lab dry-run against live data**

Run on Pi-lab:

```bash
cd ~/energy-stack/scripts
source .venv/bin/activate
set -a; source ~/energy-stack/.env; set +a
export INFLUX_URL=http://localhost:8086
export INFLUX_TOKEN="$INFLUXDB_INIT_ADMIN_TOKEN"
export INFLUX_ORG="$INFLUXDB_INIT_ORG"
export INFLUX_BUCKET="$INFLUXDB_INIT_BUCKET"
python fit_thermal_observer.py --window-days 14 --dry-run
```

Expected:

```text
thermal_observer mode=DRY-RUN
accepted=
tau_hours=
stage1_cooling_f_per_hr=
stage2_cooling_f_per_hr=
skill_score=
filters=
```

The exact numeric values depend on the current telemetry. A run that prints `accepted=False` is a successful dry-run if it also reports interval counts and rejection reasons; that means the observer is functioning and the data/fit gates are doing their job.

- [ ] **Step 5: Run Pi-lab write after inspecting dry-run output**

Run on Pi-lab:

```bash
python fit_thermal_observer.py --window-days 14
```

Expected:

```text
thermal_observer mode=WRITE
```

- [ ] **Step 6: Verify Influx point exists**

Run on Pi-lab:

```bash
docker exec influxdb influx query 'from(bucket:"energy") |> range(start:-2h) |> filter(fn: (r) => r._measurement == "hvac.thermal_observer") |> last()' \
  --org "$INFLUXDB_INIT_ORG" \
  --token "$INFLUXDB_INIT_ADMIN_TOKEN"
```

Expected:

```text
hvac.thermal_observer
```

- [ ] **Step 7: Verify JSON artifact exists**

Run on Pi-lab:

```bash
test -f ~/energy-stack/runtime/thermal_observer_latest.json
```

Expected:

```text
```

The command exits `0` with no output if the file exists on the Pi-lab host.

- [ ] **Step 8: Commit verification fixes if needed**

If verification required a source fix, run:

```bash
git add deploy/energy-stack/scripts/thermal_observer.py deploy/energy-stack/scripts/thermal_observer_influx.py deploy/energy-stack/scripts/fit_thermal_observer.py deploy/energy-stack/scripts/tests
git commit -m "fix: stabilize thermal observer live dry run"
```

If no source fix was needed, do not create an empty commit.

## Follow-Up After First Week of Fits

After at least seven daily writes, inspect:

- Whether `tau_hours` is stable across fit windows.
- Whether `stage1_cooling_f_per_hr` and `stage2_cooling_f_per_hr` are physically plausible.
- Whether rejected fits correlate with missing local weather, ComfortNet gaps, or frequent setpoint transitions.
- Whether the solar coefficient changes materially between sunny and cloudy stretches.

Only after this read-only observer behaves sensibly should a separate plan consider scheduler integration for pre-cool timing or coast-window timing.
