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


def build_eagle_meter_df(
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    base_kwh: float = 10000.0,
    kwh_per_hour: float = 1.5,
    cadence_seconds: int = 30,
    received_kwh: float = 0.0,
    demand_kw_fn: callable = lambda ts: 1.5,
) -> pd.DataFrame:
    """Eagle.meter at 30-second cadence with monotonic delivered_kwh totalizer.

    Mirrors the production shape from deploy/energy-stack/eagle-poller/poller.py:
    fields ``delivered_kwh`` (cumulative monotonic), ``demand_kw``
    (instantaneous), ``received_kwh`` (export totalizer, future solar)
    plus tags ``hw_address`` and ``source``. ``delivered_kwh`` starts at
    ``base_kwh`` and ramps linearly at ``kwh_per_hour``.
    """
    def delivered(ts, _tag):
        elapsed_seconds = (ts - start_utc).total_seconds()
        return base_kwh + kwh_per_hour * (elapsed_seconds / 3600.0)

    return build_long_format_df(
        measurement="eagle.meter",
        start_utc=start_utc,
        end_utc=end_utc,
        fields={
            "delivered_kwh": delivered,
            "demand_kw": lambda ts, _t: demand_kw_fn(ts),
            "received_kwh": lambda ts, _t: received_kwh,
        },
        cadence=datetime.timedelta(seconds=cadence_seconds),
        tags={
            "hw_address": ["0x001350050037ac6b"],
            "source": ["eagle3"],
        },
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


def build_nws_forecast_df(
    day_cts: list[datetime.date],
    high_f_fn: callable = lambda day_ct: 80.0,
    apparent_max_f_fn: callable = lambda day_ct: 85.0,
) -> pd.DataFrame:
    """nws.forecast issuances at D-1 21:00 CT for each listed CT day.

    Production poller writes per-(time, for_period) rows split across
    several fields. This fixture mirrors the post-Stage-1-split shape
    (numeric fields in ``_value``, the ``period_date`` string field in
    ``_value_text``).

    By default the forecast values (80F high, 85F apparent_max) sit
    below the classify_spike_day hot thresholds (85F / 90F) so a day
    paired with no-spike prices classifies as ``no_spike``. Pass
    custom callables to vary the values per day.
    """
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    rows: list[dict] = []
    for day_ct in day_cts:
        d_minus_1 = day_ct - datetime.timedelta(days=1)
        issuance_ct = datetime.datetime(
            d_minus_1.year, d_minus_1.month, d_minus_1.day,
            21, 0, tzinfo=ct,
        )
        issuance_utc = pd.Timestamp(
            issuance_ct.astimezone(datetime.timezone.utc)
        )
        rows.append({
            "_time": issuance_utc,
            "_measurement": "nws.forecast",
            "_field": "high_f",
            "_value": float(high_f_fn(day_ct)),
            "_value_text": None,
            "for_period": "tomorrow",
        })
        rows.append({
            "_time": issuance_utc,
            "_measurement": "nws.forecast",
            "_field": "apparent_max_f",
            "_value": float(apparent_max_f_fn(day_ct)),
            "_value_text": None,
            "for_period": "tomorrow",
        })
        rows.append({
            "_time": issuance_utc,
            "_measurement": "nws.forecast",
            "_field": "period_date",
            "_value": float("nan"),
            "_value_text": day_ct.isoformat(),
            "for_period": "tomorrow",
        })
    return pd.DataFrame(rows)


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
