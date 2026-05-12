"""Helpers for building synthetic real-shape replay bundles for tests.

The shape matches what `stage1_extract` produces via InfluxDB's
`query_data_frame()`: each row has `_time`, `_measurement`, `_field`,
`_value`, plus any tag columns (e.g., `channel` for refoss.channel).

Tests use these helpers to build a `stage1/` directory + `manifest.json`
that's indistinguishable from a real Influx export, then exercise the
analysis pipeline's loaders against that bundle.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from tools.analysis import pipeline
from tools.analysis.replay.manifest import (
    OBSERVED_RECENT,
    Manifest,
)


def build_long_format_df(
    measurement: str,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    fields: dict[str, callable],
    cadence: datetime.timedelta,
    tags: dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """Build a long-format Influx-shaped DataFrame.

    `fields` is a dict of {field_name: value_fn(ts, tag_dict)} where
    value_fn returns the value for that field at that timestamp.

    `tags` is a dict of {tag_name: [tag_values]}. Cartesian product
    across tag-value combinations is generated (so e.g. for refoss
    with `channel: [em:1, em:2, em:7, em:8, em:9]`, you get 5 rows per
    timestamp per field).
    """
    timestamps = []
    ts = start_utc
    while ts < end_utc:
        timestamps.append(ts)
        ts += cadence

    if tags is None:
        tag_combos: list[dict] = [{}]
    else:
        tag_keys = list(tags.keys())
        # Cartesian product across tag values
        from itertools import product
        tag_combos = [
            dict(zip(tag_keys, values))
            for values in product(*[tags[k] for k in tag_keys])
        ]

    rows = []
    for ts in timestamps:
        for tag_combo in tag_combos:
            for field_name, value_fn in fields.items():
                row = {
                    "_time": ts,
                    "_measurement": measurement,
                    "_field": field_name,
                    "_value": value_fn(ts, tag_combo),
                }
                row.update(tag_combo)
                rows.append(row)

    df = pd.DataFrame(rows)
    # Ensure _time is tz-aware UTC for parquet roundtrip stability
    if "_time" in df.columns and len(df) > 0:
        df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def build_refoss_channel_df(
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    power_w_by_channel: dict[str, float] | None = None,
    cadence_minutes: int = 1,
) -> pd.DataFrame:
    """Per-channel refoss.channel data matching production shape.

    Production refoss-poller writes ``power_w`` (instantaneous) plus
    cumulative session counters (``day_energy_kwh`` etc.). It does
    NOT write a per-interval ``energy_wh`` field, so this fixture
    emits only ``power_w``. Stage 2/3 loaders derive hourly kWh by
    averaging ``power_w`` within each hour bucket and integrating
    across 1 h.

    `power_w_by_channel`: dict like {"em:1": 800, "em:2": 1200, ...}
    giving the constant power_w to emit for each channel. Defaults
    to a plausible mains + HVAC load profile.
    """
    power_w_by_channel = power_w_by_channel or {
        "em:1": 800.0,    # mains leg 1
        "em:2": 1200.0,   # HVAC compressor leg
        "em:7": 700.0,    # mains leg 2
        "em:8": 1100.0,   # HVAC compressor leg
        "em:9": 50.0,     # furnace blower
    }
    channels = list(power_w_by_channel.keys())

    def power_w(ts, tag_combo):
        return power_w_by_channel[tag_combo["channel"]]

    return build_long_format_df(
        measurement="refoss.channel",
        start_utc=start_utc,
        end_utc=end_utc,
        fields={"power_w": power_w},
        cadence=datetime.timedelta(minutes=cadence_minutes),
        tags={"channel": channels},
    )


def build_comed_prices_df(
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    price_cents_fn: callable = lambda ts: 5.0,
    cadence_minutes: int = 5,
) -> pd.DataFrame:
    """ComEd RTP prices at 5-min cadence, production-shape.

    Production poller (deploy/energy-stack/comed-poller/poller.py) writes
    `_field=price_cents_per_kwh` with a `period_type=5min` tag. This
    fixture mirrors that schema so loader tests exercise the real path.

    `price_cents_fn(ts) -> float` lets the test inject realistic prices.
    Default: flat 5¢/kWh.
    """
    return build_long_format_df(
        measurement="comed.prices",
        start_utc=start_utc,
        end_utc=end_utc,
        fields={"price_cents_per_kwh": lambda ts, _t: price_cents_fn(ts)},
        cadence=datetime.timedelta(minutes=cadence_minutes),
        tags={"period_type": ["5min"]},
    )


def build_ecowitt_weather_df(
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    temp_f_fn: callable = lambda ts: 75.0,
    dewpoint_f_fn: callable = lambda ts: 60.0,
    rh_pct_fn: callable = lambda ts: 55.0,
    wind_mph_fn: callable = lambda ts: 5.0,
    solar_wm2_fn: callable = lambda ts: 100.0,
    pressure_inhg_fn: callable = lambda ts: 29.92,
    cadence_minutes: int = 5,
) -> pd.DataFrame:
    """Ecowitt weather at 5-min cadence."""
    return build_long_format_df(
        measurement="ecowitt.weather",
        start_utc=start_utc,
        end_utc=end_utc,
        fields={
            "outdoor_temp_f": lambda ts, _: temp_f_fn(ts),
            "outdoor_dewpoint_f": lambda ts, _: dewpoint_f_fn(ts),
            "outdoor_rh_pct": lambda ts, _: rh_pct_fn(ts),
            "wind_mph": lambda ts, _: wind_mph_fn(ts),
            "solar_wm2": lambda ts, _: solar_wm2_fn(ts),
            "pressure_inhg": lambda ts, _: pressure_inhg_fn(ts),
        },
        cadence=datetime.timedelta(minutes=cadence_minutes),
    )


def build_hvac_thermostat_df(
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    indoor_temp_f_fn: callable = lambda ts: 73.0,
    cool_setpoint_f_fn: callable = lambda ts: 75.0,
    cadence_minutes: int = 1,
) -> pd.DataFrame:
    """hvac.thermostat at 1-min cadence."""
    return build_long_format_df(
        measurement="hvac.thermostat",
        start_utc=start_utc,
        end_utc=end_utc,
        fields={
            "indoor_temp_f": lambda ts, _: indoor_temp_f_fn(ts),
            "cool_setpoint_f": lambda ts, _: cool_setpoint_f_fn(ts),
        },
        cadence=datetime.timedelta(minutes=cadence_minutes),
    )


def write_bundle(
    stage1_dir: Path,
    measurement_dataframes: dict[str, pd.DataFrame],
    window_start_ct: str,
    window_end_ct: str,
    source_type: str = OBSERVED_RECENT,
    source_bucket: str = "energy",
    exporter_metadata: dict | None = None,
) -> Manifest:
    """Convenience wrapper around _write_stage1_export for test
    fixtures. Returns the assembled Manifest."""
    return pipeline._write_stage1_export(
        stage_dir=stage1_dir,
        measurement_dataframes=measurement_dataframes,
        window_start_ct=window_start_ct,
        window_end_ct=window_end_ct,
        source_bucket=source_bucket,
        source_type=source_type,
        exporter_metadata=exporter_metadata or {"version": "test_fixture"},
    )
