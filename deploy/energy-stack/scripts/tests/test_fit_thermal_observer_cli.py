from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import fit_thermal_observer


class FakeRecord:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values


class FakeTable:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.records = [FakeRecord(row) for row in rows]


class FakeQueryApi:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def query(self, org: str, query: str) -> list[FakeTable]:
        return [FakeTable(self.rows)]


class FailingWriteApi:
    def write(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("thermal observer CLI must not write")


class FakeInfluxClient:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.write_api_requested = False

    def __enter__(self) -> FakeInfluxClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def query_api(self) -> FakeQueryApi:
        return FakeQueryApi(self.rows)

    def write_api(self) -> FailingWriteApi:
        self.write_api_requested = True
        return FailingWriteApi()


def test_cli_normal_run_only_reads_existing_influx_fields(monkeypatch, tmp_path):
    client = FakeInfluxClient(_synthetic_rows())
    fake_module = types.SimpleNamespace(InfluxDBClient=lambda **kwargs: client)
    monkeypatch.setitem(fit_thermal_observer.sys.modules, "influxdb_client", fake_module)
    for name in fit_thermal_observer.REQUIRED_ENV_VARS:
        monkeypatch.setenv(name, "test")

    output_path = tmp_path / "thermal_observer_latest.json"

    exit_code = fit_thermal_observer.main(["--output-json", str(output_path)])

    assert exit_code == 0
    assert client.write_api_requested is False
    assert not output_path.exists()


def _synthetic_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    indoor = 76.0
    start = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    for i in range(96):
        outdoor = 86.0 if i < 48 else 78.0
        cool_pct = 0.0
        if 20 <= i < 45:
            cool_pct = 50.0
        if 45 <= i < 60:
            cool_pct = 100.0
        solar = 500.0 if 36 <= i < 72 else 0.0
        rows.append(
            {
                "_time": start + timedelta(minutes=10 * i),
                "indoor_temp_f": indoor,
                "outdoor_temp_f": outdoor,
                "solar_radiation_w_m2": solar,
                "cool_actual_pct": cool_pct,
                "heat_actual_pct": 0.0,
                "cool_setpoint_f": 75.0,
            }
        )

        stage1 = 1.0 if cool_pct >= 5.0 else 0.0
        stage2 = 1.0 if cool_pct >= 75.0 else 0.0
        dtdt = 0.1 * (outdoor - indoor) - 1.8 * stage1 - 1.2 * stage2 + 0.001 * solar
        indoor = indoor + dtdt * (10 / 60.0)
    return rows
