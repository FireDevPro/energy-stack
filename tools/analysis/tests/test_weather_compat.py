"""Weather-derived Ecowitt compatibility converter tests.

Phase 1 tracer bullet: a single-hour KORD ASOS fetch flows through the
IEM bulk CGI parser, lands in an Ecowitt-shaped parquet, and the
Stage 3 loader reads it back into the correct day index. IEM HTTP is
mocked at the system boundary; everything below it is the real
production code path.

Plan: docs/plans/weather-compat-plan.md
Spec: OSF_FILING.md criterion 14 source-type
      `weather_derived_compatibility` + docs/REPLAY_VALIDATION.md
"""
from __future__ import annotations

import datetime
from pathlib import Path

import pandas as pd
import pytest

from tools.analysis import pipeline
from tools.analysis.replay.manifest import (
    WEATHER_DERIVED_COMPATIBILITY,
    read_manifest,
)


# Fixture: a tiny IEM ASOS bulk-CGI CSV response for one hour at KORD.
# The bulk CGI emits a `valid` UTC timestamp + per-field columns. Values
# below are realistic for a Chicago July afternoon.
IEM_SINGLE_HOUR_CSV = """station,valid,tmpf,dwpf,relh,sknt,mslp,report_type
KORD,2025-07-15 19:00,82.4,67.8,55.2,8.0,1012.5,1
"""


def _fake_iem_get(monkeypatch, csv_text: str) -> None:
    """Patch the HTTP boundary to return a canned IEM CSV for the
    mesonet host and an empty Open-Meteo payload for the open-meteo
    host. Existing tests that don't care about solar use this helper."""
    from tools.analysis.replay import weather_compat

    class _FakeCSVResponse:
        def __init__(self, text: str):
            self.text = text
            self.status_code = 200
        def raise_for_status(self): pass

    class _FakeJSONResponse:
        def __init__(self, payload: dict):
            self._payload = payload
            self.status_code = 200
        def raise_for_status(self): pass
        def json(self): return self._payload

    empty_solar = {"hourly": {"time": [], "shortwave_radiation": []}}

    def _fake_get(url, params=None, timeout=None):
        if "mesonet" in url:
            return _FakeCSVResponse(csv_text)
        if "open-meteo" in url:
            return _FakeJSONResponse(empty_solar)
        raise ValueError(f"unexpected url {url!r}")

    monkeypatch.setattr(weather_compat, "_http_get", _fake_get)


def test_phase1_tracer_single_hour_round_trip(tmp_path, monkeypatch):
    """End-to-end Phase 1 acceptance:

    1. Mocked IEM returns one KORD row for 2025-07-15 19:00 UTC (= 14:00 CDT).
    2. weather_compat.fetch writes a compat bundle (parquet + manifest)
       with `source_type=weather_derived_compatibility`.
    3. pipeline._stage3_daily_avg_temps_f reads the row back and places
       it in the correct day index of a CT week starting Monday
       2025-07-14.

    Narrow assertions only: the row lands in the right day, other days
    stay at the empty-day default, and the manifest source_type tag is
    correct. Aggregate outputs over the rest of the week are NOT
    meaningful with a single hour of data and are NOT asserted here.
    """
    from tools.analysis.replay import weather_compat

    _fake_iem_get(monkeypatch, IEM_SINGLE_HOUR_CSV)

    bundle_dir = tmp_path / "compat"
    manifest = weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 20, 0, tzinfo=datetime.timezone.utc),
        out_dir=bundle_dir,
    )

    # 1. Manifest written.
    assert (bundle_dir / "manifest.json").exists()

    # 2. Manifest entry tagged weather_derived_compatibility.
    read_back = read_manifest(bundle_dir / "manifest.json")
    entries = read_back.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.source_type == WEATHER_DERIVED_COMPATIBILITY
    assert entry.row_count >= 1
    assert "outdoor_temp_f" in entry.field_set
    parquet_path = bundle_dir / entry.parquet_path
    assert parquet_path.exists()

    # 3. Stage 3 loader reads it back into the right day index.
    # Week start: 2025-07-14 (Monday). 2025-07-15 = Tuesday = index 1.
    df = pd.read_parquet(parquet_path)
    daily = pipeline._stage3_daily_avg_temps_f(df, datetime.date(2025, 7, 14))
    assert len(daily) == 7
    assert daily[0] == 0.0           # Monday: no data
    assert daily[1] == pytest.approx(82.4, abs=0.5)   # Tuesday: the fetched row
    assert daily[2] == 0.0           # Wednesday: no data


def test_phase1_manifest_window_reflects_fetch_range(tmp_path, monkeypatch):
    """The manifest's CT window matches the fetch's UTC range converted
    to CT. A 2025-07-15 19:00 UTC → 20:00 UTC fetch is 14:00–15:00 CDT."""
    from tools.analysis.replay import weather_compat

    _fake_iem_get(monkeypatch, IEM_SINGLE_HOUR_CSV)

    manifest = weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 20, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )

    # Window strings are ISO with CT offset (-05:00 during CDT).
    assert manifest.export_window_start_ct.startswith("2025-07-15T14:00")
    assert manifest.export_window_end_ct.startswith("2025-07-15T15:00")


def test_phase1_field_value_conversion_is_identity_for_temp(tmp_path, monkeypatch):
    """ASOS `tmpf` is already °F. The parquet's _value for the
    outdoor_temp_f row must match the IEM input exactly (within float
    precision)."""
    from tools.analysis.replay import weather_compat

    _fake_iem_get(monkeypatch, IEM_SINGLE_HOUR_CSV)

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 20, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )

    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    temp_rows = df[df["_field"] == "outdoor_temp_f"]
    # Phase 2 forward-fills the single native row across the 5-min grid
    # for the fetched hour, so we expect every emitted row to carry the
    # native value (identity conversion preserves it through ffill).
    assert len(temp_rows) >= 1
    for v in temp_rows["_value"]:
        assert float(v) == pytest.approx(82.4)


# ----------------------------------------------------------------------
# Phase 2: 5-min cadence + all non-solar fields + per-row provenance
# ----------------------------------------------------------------------

# Two-hour native 5-min HFMETAR response: 24 rows at exact 5-min ticks.
# Real IEM 5-min ASOS reports include all five core fields.
def _native_5min_csv() -> str:
    header = "station,valid,tmpf,dwpf,relh,sknt,mslp,report_type"
    lines = [header]
    base = datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc)
    for i in range(24):
        ts = base + datetime.timedelta(minutes=5 * i)
        lines.append(
            f"KORD,{ts.strftime('%Y-%m-%d %H:%M')},"
            f"{82.0 + 0.1*i},{67.0 + 0.05*i},{55.0},{8.0},{1012.5},1"
        )
    return "\n".join(lines) + "\n"


# Hourly-only METAR response: 2 rows at the top of each hour.
def _hourly_only_csv() -> str:
    return (
        "station,valid,tmpf,dwpf,relh,sknt,mslp,report_type\n"
        "KORD,2025-07-15 19:00,82.0,67.0,55.0,8.0,1012.5,3\n"
        "KORD,2025-07-15 20:00,83.0,68.0,54.0,9.0,1011.8,3\n"
    )


def test_phase2_all_five_fields_present(tmp_path, monkeypatch):
    """Phase 2 fans out from single-field to all five non-solar fields."""
    from tools.analysis.replay import weather_compat
    _fake_iem_get(monkeypatch, _native_5min_csv())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    expected = {
        "outdoor_temp_f", "outdoor_dewpoint_f", "outdoor_rh_pct",
        "pressure_inhg", "wind_mph",
    }
    assert expected.issubset(set(entry.field_set))


def test_phase2_unit_conversions(tmp_path, monkeypatch):
    """ASOS mslp (mb) → inHg via /33.8639. sknt (knots) → mph via *1.15078."""
    from tools.analysis.replay import weather_compat
    _fake_iem_get(monkeypatch, _native_5min_csv())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    pressure = df[df["_field"] == "pressure_inhg"].iloc[0]["_value"]
    wind = df[df["_field"] == "wind_mph"].iloc[0]["_value"]
    assert float(pressure) == pytest.approx(1012.5 / 33.8639, abs=0.01)
    assert float(wind) == pytest.approx(8.0 * 1.15078, abs=0.01)


def test_phase2_native_5min_rows_tagged_not_upsampled(tmp_path, monkeypatch):
    """When IEM returns native 5-min HFMETAR rows, every row carries
    upsampled=False on the provenance column."""
    from tools.analysis.replay import weather_compat
    _fake_iem_get(monkeypatch, _native_5min_csv())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    assert "upsampled" in df.columns
    assert "weather_source" in df.columns
    assert "station" in df.columns
    assert "cadence" in df.columns
    # All native: every row upsampled=False
    assert (~df["upsampled"]).all()
    assert (df["weather_source"] == "iem_asos").all()
    assert (df["station"] == "KORD").all()
    assert (df["cadence"] == "5min").all()


def test_phase2_hourly_only_forward_filled_to_5min(tmp_path, monkeypatch):
    """When IEM returns only hourly METAR, the converter forward-fills
    to 5-min and tags every non-native row upsampled=True."""
    from tools.analysis.replay import weather_compat
    _fake_iem_get(monkeypatch, _hourly_only_csv())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    temp = df[df["_field"] == "outdoor_temp_f"].sort_values("_time")
    # 19:00 and 20:00 native; 19:05-19:55 filled from 19:00; 20:05-... not
    # emitted (only 24 5-min slots covered by the two natives' hour each).
    # Total = 24 slots (12 per hour × 2 hours = 24).
    assert len(temp) == 24
    # First row at 19:00 is native (upsampled=False).
    first = temp.iloc[0]
    assert pd.Timestamp(first["_time"]) == pd.Timestamp("2025-07-15 19:00:00", tz="UTC")
    assert bool(first["upsampled"]) is False
    # Second row at 19:05 is upsampled (forward-filled from 19:00).
    second = temp.iloc[1]
    assert pd.Timestamp(second["_time"]) == pd.Timestamp("2025-07-15 19:05:00", tz="UTC")
    assert bool(second["upsampled"]) is True
    # All filled rows in the 19:00 hour carry the 19:00 temperature.
    nineteen = temp[temp["_time"] < pd.Timestamp("2025-07-15 20:00:00", tz="UTC")]
    assert (nineteen["_value"] == 82.0).all()


def test_phase2_provenance_columns_audit_only_not_in_field_set(tmp_path, monkeypatch):
    """Provenance columns (station, cadence, upsampled, weather_source)
    are parquet columns alongside _time/_measurement/_field/_value, NOT
    distinct Influx fields. They must not appear in the manifest's
    field_set."""
    from tools.analysis.replay import weather_compat
    _fake_iem_get(monkeypatch, _native_5min_csv())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    field_set = set(entry.field_set)
    audit_only = {"station", "cadence", "upsampled", "weather_source", "solar_source"}
    assert audit_only.isdisjoint(field_set)


def test_phase2_stage3_loader_ignores_provenance_columns(tmp_path, monkeypatch):
    """Stage 3 loader reads parquet through pipeline._stage3_daily_avg_temps_f
    and similar; it must produce identical numeric output regardless of
    whether provenance columns are present. Smoke test against a real
    fetch + Stage 3 read."""
    from tools.analysis.replay import weather_compat
    _fake_iem_get(monkeypatch, _native_5min_csv())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    daily = pipeline._stage3_daily_avg_temps_f(df, datetime.date(2025, 7, 14))
    # Tuesday (index 1) has 24 5-min rows from 82.0 ramping to ~84.3.
    # Mean of the ramp lands ~83.15.
    assert daily[1] == pytest.approx(83.15, abs=0.5)
    # Other days empty.
    assert daily[0] == 0.0
    assert daily[2] == 0.0


# ----------------------------------------------------------------------
# Phase 3: Open-Meteo / ERA5 solar enrichment
# ----------------------------------------------------------------------

def _fake_iem_and_solar(monkeypatch, iem_csv: str, solar_json: dict) -> None:
    """Patch _http_get to dispatch on URL: IEM gets the CSV body,
    Open-Meteo gets the JSON body."""
    from tools.analysis.replay import weather_compat

    class _FakeCSVResponse:
        def __init__(self, text: str):
            self.text = text
            self.status_code = 200
        def raise_for_status(self): pass

    class _FakeJSONResponse:
        def __init__(self, payload: dict):
            self._payload = payload
            self.status_code = 200
        def raise_for_status(self): pass
        def json(self): return self._payload

    def _fake_get(url, params=None, timeout=None):
        if "mesonet" in url:
            return _FakeCSVResponse(iem_csv)
        if "open-meteo" in url:
            return _FakeJSONResponse(solar_json)
        raise ValueError(f"unexpected url {url!r}")

    monkeypatch.setattr(weather_compat, "_http_get", _fake_get)


def _solar_json_two_hours() -> dict:
    """Realistic Open-Meteo response: two hourly slots for KORD
    19:00–20:00 UTC on 2025-07-15 (14:00 CDT mid-afternoon)."""
    return {
        "hourly": {
            "time": ["2025-07-15T19:00", "2025-07-15T20:00"],
            "shortwave_radiation": [650.0, 580.0],
        },
    }


def _solar_json_night() -> dict:
    return {
        "hourly": {
            "time": ["2025-07-16T05:00", "2025-07-16T06:00"],
            "shortwave_radiation": [0.0, 0.0],
        },
    }


def test_phase3_solar_field_emitted_when_open_meteo_responds(tmp_path, monkeypatch):
    """Solar from Open-Meteo lands in the parquet as `solar_wm2`."""
    from tools.analysis.replay import weather_compat
    _fake_iem_and_solar(monkeypatch, _native_5min_csv(), _solar_json_two_hours())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    assert "solar_wm2" in entry.field_set


def test_phase3_solar_values_match_open_meteo_payload(tmp_path, monkeypatch):
    """Solar values pass through identity from Open-Meteo shortwave_radiation
    (already W/m²). 19:00 → 650, 20:00 → 580."""
    from tools.analysis.replay import weather_compat
    _fake_iem_and_solar(monkeypatch, _native_5min_csv(), _solar_json_two_hours())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    solar = df[df["_field"] == "solar_wm2"].sort_values("_time")
    # Forward-fill from hourly to 5-min: 12 slots per hour × 2 hours = 24.
    assert len(solar) == 24
    # 19:00 native = 650; 20:00 native = 580.
    nineteen = solar[solar["_time"] < pd.Timestamp("2025-07-15 20:00:00", tz="UTC")]
    assert (nineteen["_value"] == 650.0).all()
    twenty = solar[solar["_time"] >= pd.Timestamp("2025-07-15 20:00:00", tz="UTC")]
    assert (twenty["_value"] == 580.0).all()


def test_phase3_solar_rows_carry_solar_source_tag(tmp_path, monkeypatch):
    """Solar rows tag `solar_source=open_meteo_era5`. Non-solar rows
    leave that column null."""
    from tools.analysis.replay import weather_compat
    _fake_iem_and_solar(monkeypatch, _native_5min_csv(), _solar_json_two_hours())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    solar = df[df["_field"] == "solar_wm2"]
    assert (solar["solar_source"] == "open_meteo_era5").all()
    # Temp rows from ASOS leave solar_source unset.
    temp = df[df["_field"] == "outdoor_temp_f"]
    assert temp["solar_source"].isna().all()


def test_phase3_solar_nighttime_zeros(tmp_path, monkeypatch):
    """Open-Meteo reports ~0 W/m² at night; solar rows must reflect that."""
    from tools.analysis.replay import weather_compat
    # IEM CSV at the same night-hour to satisfy the ASOS side.
    night_csv = (
        "station,valid,tmpf,dwpf,relh,sknt,mslp,report_type\n"
        "KORD,2025-07-16 05:00,72.0,65.0,80.0,4.0,1013.0,3\n"
        "KORD,2025-07-16 06:00,72.0,65.0,80.0,4.0,1013.0,3\n"
    )
    _fake_iem_and_solar(monkeypatch, night_csv, _solar_json_night())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 16, 5, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 16, 7, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    solar = df[df["_field"] == "solar_wm2"]
    assert len(solar) == 24
    assert (solar["_value"] == 0.0).all()


def test_phase3_solar_rows_marked_upsampled_when_ffilled(tmp_path, monkeypatch):
    """Open-Meteo is hourly; the 5-min grid forward-fills. Native-hour
    slots (xx:00) carry upsampled=False, the 11 intervening slots carry
    upsampled=True."""
    from tools.analysis.replay import weather_compat
    _fake_iem_and_solar(monkeypatch, _native_5min_csv(), _solar_json_two_hours())

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    solar = df[df["_field"] == "solar_wm2"].sort_values("_time").reset_index(drop=True)
    # 19:00 slot is native-hour aligned → upsampled=False.
    nineteen_00 = solar[
        solar["_time"] == pd.Timestamp("2025-07-15 19:00:00", tz="UTC")
    ].iloc[0]
    assert bool(nineteen_00["upsampled"]) is False
    # 19:05 slot is a ffill → upsampled=True.
    nineteen_05 = solar[
        solar["_time"] == pd.Timestamp("2025-07-15 19:05:00", tz="UTC")
    ].iloc[0]
    assert bool(nineteen_05["upsampled"]) is True


# ----------------------------------------------------------------------
# Phase 4: ERA5 gap-fill for ASOS material outages
# ----------------------------------------------------------------------

def _open_meteo_full_payload(times: list[str]) -> dict:
    """Open-Meteo payload with all fields used by Phase 3 (solar) and
    Phase 4 (gap-fill). Requested units: temperature_unit=fahrenheit,
    wind_speed_unit=mph, so the values mirror the unit the converter
    emits without further math."""
    n = len(times)
    return {
        "hourly": {
            "time": times,
            "shortwave_radiation": [500.0] * n,
            "temperature_2m": [80.0] * n,
            "dew_point_2m": [65.0] * n,
            "relative_humidity_2m": [60.0] * n,
            "pressure_msl": [1013.0] * n,
            "wind_speed_10m": [10.0] * n,
        },
    }


def test_phase4_empty_iem_fills_entire_window_from_era5(tmp_path, monkeypatch):
    """When IEM returns no rows at all, the entire 5-min grid fills
    from Open-Meteo. Rows tag weather_source=open_meteo_era5_gap_fill."""
    from tools.analysis.replay import weather_compat
    _fake_iem_and_solar(
        monkeypatch,
        iem_csv="station,valid,tmpf,dwpf,relh,sknt,mslp,report_type\n",
        solar_json=_open_meteo_full_payload(
            ["2025-07-15T19:00", "2025-07-15T20:00"],
        ),
    )

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    # All five ASOS-equivalent fields present from gap-fill, plus solar.
    expected = {
        "outdoor_temp_f", "outdoor_dewpoint_f", "outdoor_rh_pct",
        "pressure_inhg", "wind_mph", "solar_wm2",
    }
    assert expected.issubset(set(entry.field_set))
    # Non-solar rows tagged gap-fill.
    non_solar = df[df["_field"] != "solar_wm2"]
    assert (non_solar["weather_source"] == "open_meteo_era5_gap_fill").all()
    assert non_solar["solar_source"].isna().all()
    # All gap-fill rows are upsampled.
    assert non_solar["upsampled"].all()


def test_phase4_mid_window_asos_gap_filled_by_era5(tmp_path, monkeypatch):
    """IEM has rows at 19:00 and 22:00 (3-hour gap); Open-Meteo fills
    the middle slots 20:00-21:55 with weather_source=open_meteo_era5_gap_fill.
    Non-gap slots keep weather_source=iem_asos."""
    from tools.analysis.replay import weather_compat
    iem_csv = (
        "station,valid,tmpf,dwpf,relh,sknt,mslp,report_type\n"
        "KORD,2025-07-15 19:00,82.0,67.0,55.0,8.0,1012.5,3\n"
        "KORD,2025-07-15 22:00,80.0,66.0,58.0,7.0,1013.0,3\n"
    )
    _fake_iem_and_solar(
        monkeypatch,
        iem_csv=iem_csv,
        solar_json=_open_meteo_full_payload([
            "2025-07-15T19:00", "2025-07-15T20:00",
            "2025-07-15T21:00", "2025-07-15T22:00",
        ]),
    )

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 23, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    temp = df[df["_field"] == "outdoor_temp_f"].sort_values("_time").reset_index(drop=True)

    # 48 5-min slots across 19:00 to 22:55. 19:00 IEM ffills 60 min
    # ahead (limit=12 slots), covering 19:00 to 20:00 = 13 slots. 22:00
    # IEM ffills through 22:55 = 12 slots (window ends at 23:00). Gap
    # (no ASOS coverage) is 20:05 to 21:55 = 23 slots, filled by ERA5.
    assert len(temp) == 48
    asos_rows = temp[temp["weather_source"] == "iem_asos"]
    gap_rows = temp[temp["weather_source"] == "open_meteo_era5_gap_fill"]
    assert len(asos_rows) == 25
    assert len(gap_rows) == 23

    # Native IEM rows at 19:00 and 22:00 are not upsampled.
    native_ts = {
        pd.Timestamp("2025-07-15 19:00:00", tz="UTC"),
        pd.Timestamp("2025-07-15 22:00:00", tz="UTC"),
    }
    for ts in native_ts:
        row = temp[temp["_time"] == ts]
        assert len(row) == 1
        assert bool(row.iloc[0]["upsampled"]) is False


def test_phase4_sub_material_gap_not_filled(tmp_path, monkeypatch):
    """30-minute gap (less than the 60-min material-gap threshold) is
    covered by ASOS forward-fill alone; no gap-fill rows emitted."""
    from tools.analysis.replay import weather_compat
    iem_csv = (
        "station,valid,tmpf,dwpf,relh,sknt,mslp,report_type\n"
        "KORD,2025-07-15 19:00,82.0,67.0,55.0,8.0,1012.5,3\n"
        "KORD,2025-07-15 19:30,82.5,67.0,55.0,8.0,1012.5,3\n"
    )
    _fake_iem_and_solar(
        monkeypatch,
        iem_csv=iem_csv,
        solar_json=_open_meteo_full_payload(["2025-07-15T19:00"]),
    )

    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 20, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )
    manifest = read_manifest(tmp_path / "compat" / "manifest.json")
    entry = manifest.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )[0]
    df = pd.read_parquet(tmp_path / "compat" / entry.parquet_path)
    temp = df[df["_field"] == "outdoor_temp_f"]
    # No gap-fill rows: every non-solar row carries iem_asos.
    assert (temp["weather_source"] == "iem_asos").all()


# ----------------------------------------------------------------------
# Phase 5: CLI + bundle merge
# ----------------------------------------------------------------------

def test_phase5_merge_combines_compat_into_stage1_bundle(tmp_path, monkeypatch):
    """Take a fetched compat bundle and a pre-existing stage1 bundle
    with one observed_recent measurement; merge produces a stage1
    bundle whose manifest has BOTH entries and whose parquet files
    are present."""
    from tools.analysis.replay import weather_compat
    from tools.analysis.tests.fixture_real_shape import (
        build_refoss_channel_df, write_bundle,
    )

    # Build a stage1 bundle with observed_recent refoss.channel data.
    stage1_dir = tmp_path / "stage1"
    refoss_start = datetime.datetime(
        2025, 7, 15, 5, 0, tzinfo=datetime.timezone.utc,
    )
    refoss_end = datetime.datetime(
        2025, 7, 16, 5, 0, tzinfo=datetime.timezone.utc,
    )
    refoss = build_refoss_channel_df(refoss_start, refoss_end, cadence_minutes=5)
    write_bundle(
        stage1_dir=stage1_dir,
        measurement_dataframes={"refoss.channel": refoss},
        window_start_ct="2025-07-15T00:00:00-05:00",
        window_end_ct="2025-07-16T00:00:00-05:00",
    )

    # Build a compat bundle alongside it.
    _fake_iem_and_solar(monkeypatch, _native_5min_csv(), _solar_json_two_hours())
    compat_dir = tmp_path / "compat"
    weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
        out_dir=compat_dir,
    )

    # Merge.
    weather_compat.merge(compat_dir=compat_dir, target_stage1_dir=stage1_dir)

    # Stage 1 manifest now has both entries.
    merged = read_manifest(stage1_dir / "manifest.json")
    refoss_entries = [e for e in merged.entries if e.measurement == "refoss.channel"]
    ecowitt_entries = merged.entries_for(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )
    assert len(refoss_entries) == 1
    assert len(ecowitt_entries) == 1
    # Both parquet files exist in the merged target.
    assert (stage1_dir / refoss_entries[0].parquet_path).exists()
    assert (stage1_dir / ecowitt_entries[0].parquet_path).exists()


def test_phase5_merge_rejects_duplicate_measurement_source_type(tmp_path, monkeypatch):
    """A merge that would create two manifest entries with the same
    (measurement, source_type) must be rejected. Caller can re-fetch
    or merge into a different bundle."""
    from tools.analysis.replay import weather_compat
    _fake_iem_and_solar(monkeypatch, _native_5min_csv(), _solar_json_two_hours())

    # Two compat bundles for the same source_type.
    compat_a = tmp_path / "compat_a"
    compat_b = tmp_path / "compat_b"
    for d in (compat_a, compat_b):
        weather_compat.fetch(
            station="KORD",
            start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
            end_utc=datetime.datetime(2025, 7, 15, 21, 0, tzinfo=datetime.timezone.utc),
            out_dir=d,
        )

    # Merge first into a fresh stage1 dir; that works.
    stage1_dir = tmp_path / "stage1"
    stage1_dir.mkdir()
    # Seed an empty stage1 manifest so merge has something to merge into.
    from tools.analysis.replay.manifest import Manifest, write_manifest as _wm
    _wm(Manifest(
        export_window_start_ct="2025-07-15T00:00:00-05:00",
        export_window_end_ct="2025-07-16T00:00:00-05:00",
        source_bucket="energy",
        exported_at_utc="2025-07-15T05:00:00+00:00",
        exporter={"version": "test_fixture"},
        entries=(),
    ), stage1_dir / "manifest.json")

    weather_compat.merge(compat_dir=compat_a, target_stage1_dir=stage1_dir)
    # Second merge should raise: same (ecowitt.weather,
    # weather_derived_compatibility) tuple.
    with pytest.raises(ValueError, match=r"already has an entry"):
        weather_compat.merge(compat_dir=compat_b, target_stage1_dir=stage1_dir)


def test_phase5_cli_fetch_help_runs(tmp_path, monkeypatch):
    """`python -m tools.analysis.replay.weather_compat fetch --help`
    must exit cleanly with code 0."""
    from tools.analysis.replay import weather_compat
    with pytest.raises(SystemExit) as exc:
        weather_compat.main(["fetch", "--help"])
    assert exc.value.code == 0


def test_phase5_cli_fetch_dispatches_to_fetch_fn(tmp_path, monkeypatch):
    """The fetch subcommand parses station / start / end / out and
    calls weather_compat.fetch with the parsed values."""
    from tools.analysis.replay import weather_compat
    _fake_iem_and_solar(monkeypatch, _native_5min_csv(), _solar_json_two_hours())

    out_dir = tmp_path / "compat"
    weather_compat.main([
        "fetch",
        "--station", "KORD",
        "--start", "2025-07-15T19:00:00+00:00",
        "--end", "2025-07-15T21:00:00+00:00",
        "--out", str(out_dir),
    ])
    # A manifest landed at the expected location.
    assert (out_dir / "manifest.json").exists()


def test_phase1_no_rows_when_iem_returns_empty(tmp_path, monkeypatch):
    """IEM returning a header-only CSV (no data rows) yields a manifest
    with no ecowitt.weather entry. The bundle is well-formed but empty.
    """
    from tools.analysis.replay import weather_compat

    empty_csv = "station,valid,tmpf,dwpf,relh,sknt,mslp,report_type\n"
    _fake_iem_get(monkeypatch, empty_csv)

    manifest = weather_compat.fetch(
        station="KORD",
        start_utc=datetime.datetime(2025, 7, 15, 19, 0, tzinfo=datetime.timezone.utc),
        end_utc=datetime.datetime(2025, 7, 15, 20, 0, tzinfo=datetime.timezone.utc),
        out_dir=tmp_path / "compat",
    )

    assert manifest.entries_for("ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY) == []
