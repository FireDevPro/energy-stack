"""Pull season telemetry from InfluxDB on pi-lab and assemble one 15-min frame.

Pulls run over SSH + ``docker exec influxdb influx query`` (same route as the
2026-05-26 rough cut), so no Influx token is needed on the workstation. Raw
CSVs are cached per month and source under ``.data/raw``; the assembled frame
is cached as ``.data/season.pkl``. Both are gitignored.

Units and naming traps handled here (see README):
- ``hvac.thermostat`` passes the device's display unit straight through. It
  was deg F before the TCC cutover (~2026-06-22) and is deg C after, in fields
  still named ``*_f``. Converted back to deg F.
- ``hvac.thermostat.indoor_temp_f`` and ``ecowitt.weather.indoor_temp_f``
  share a field name. They are pulled separately and the thermostat one is
  renamed ``tstat_indoor_raw``.
"""
from __future__ import annotations

import io
import os
import subprocess
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

PI_SSH = "chris@192.168.20.10"
# Override with THERMAL_MODEL_CACHE to pull into a separate (e.g. clean) cache.
CACHE = Path(os.environ.get("THERMAL_MODEL_CACHE", Path(__file__).resolve().parent / ".data"))
RAW = CACHE / "raw"
TZ = "America/Chicago"


@dataclass(frozen=True)
class Source:
    key: str
    bucket: str
    filter: str
    rename: str | None = None  # collapse every field in this source to one name


SOURCES: tuple[Source, ...] = (
    Source(
        "eco",
        "energy",
        'r._measurement == "ecowitt.weather" and contains(value: r._field, set: '
        '["ch1_temp_f","ch1_dewpoint_f","solar_wm2","wind_mph","ch2_temp_f","ch3_temp_f","ch8_temp_f","indoor_temp_f"])',
    ),
    Source(
        "cn",
        "energy",
        'r._measurement == "hvac.comfortnet" and contains(value: r._field, set: '
        '["cool_actual_pct","heat_actual_pct","fan_actual_pct","blower_cfm","dehumidify_actual_pct"])',
    ),
    Source("hv", "energy", 'r._measurement == "haven.indoor" and r._field == "temperature_c"'),
    Source(
        "ts",
        "energy",
        'r._measurement == "hvac.thermostat" and contains(value: r._field, set: ["cool_setpoint_f","heat_setpoint_f"])',
    ),
    Source(
        "tsin",
        "energy",
        'r._measurement == "hvac.thermostat" and r._field == "indoor_temp_f"',
        rename="tstat_indoor_raw",
    ),
    Source(
        "px",
        "energy",
        'r._measurement == "comed.prices" and r._field == "price_cents_per_kwh" and r.period_type == "5min"',
    ),
    # PJM day-ahead LMP, ComEd zone ($/MWh): what a real controller could know a day ahead.
    Source(
        "da",
        "energy",
        'r._measurement == "pjm.lmp_da_hourly" and r._field == "total_lmp_da" and r.zone == "COMED"',
    ),
    # Refoss from the 1-min longterm aggregates: raw 30 s x 6 months is too heavy for the Pi.
    Source(
        "rf",
        "energy-longterm",
        'r._measurement == "refoss.channel" and r._field == "power_w" and contains(value: r.channel, set: ["em:2","em:8","em:9"])',
    ),
)


def _month_chunks(start: pd.Timestamp, stop: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    edges = [start] + list(pd.date_range(start.normalize() + pd.offsets.MonthBegin(1), stop, freq="MS", tz="UTC")) + [stop]
    return [(a, b) for a, b in pairwise(edges) if b > a]


def pull(start: str, stop: str, refresh: bool = False) -> None:
    """Pull every source in month chunks (15-min means) into the raw cache."""
    RAW.mkdir(parents=True, exist_ok=True)
    t0, t1 = pd.Timestamp(start, tz="UTC"), pd.Timestamp(stop, tz="UTC")
    for a, b in _month_chunks(t0, t1):
        for src in SOURCES:
            out = RAW / f"{src.key}_{a:%Y%m%dT%H%M}_{b:%Y%m%dT%H%M}.csv"
            if out.exists() and not refresh:
                continue
            flux = (
                f'from(bucket: "{src.bucket}") |> range(start: {a:%Y-%m-%dT%H:%M:%SZ}, stop: {b:%Y-%m-%dT%H:%M:%SZ}) '
                f"|> filter(fn: (r) => {src.filter}) "
                "|> aggregateWindow(every: 15m, fn: mean, createEmpty: false) "
                '|> keep(columns: ["_time","_field","_value","channel"])'
            )
            res = subprocess.run(
                ["ssh", PI_SSH, "docker exec -i influxdb influx query --raw -f /dev/stdin"],
                input=flux,
                capture_output=True,
                text=True,
                timeout=600,
                check=True,
            )
            out.write_text(res.stdout)
            print(f"pulled {out.name}: {res.stdout.count(chr(10))} lines")


def _read(path: Path) -> pd.DataFrame:
    lines = [ln for ln in path.read_text().splitlines(keepends=True) if ln.strip() and not ln.startswith("#")]
    if len(lines) <= 1:
        return pd.DataFrame(columns=["_time", "_field", "_value", "channel"])
    return pd.read_csv(io.StringIO("".join(lines)))


def assemble() -> pd.DataFrame:
    """Combine the raw cache into one wide 15-min frame (local time) with derived columns."""
    by_key = {s.key: s for s in SOURCES}
    parts = []
    for path in sorted(RAW.glob("*.csv")):
        src = by_key[path.name.split("_", 1)[0]]
        df = _read(path)
        if df.empty:
            continue
        if src.key == "rf":
            df["_field"] = np.where(df["channel"] == "em:9", "blower_w", "compressor_w")
            df = df.groupby(["_time", "_field"], as_index=False)["_value"].sum()
        if src.rename:
            df["_field"] = src.rename
        parts.append(df[["_time", "_field", "_value"]])
    long = pd.concat(parts)
    long["_time"] = pd.to_datetime(long["_time"], utc=True)
    w = long.pivot_table(index="_time", columns="_field", values="_value", aggfunc="mean").asfreq("15min")
    w.index = w.index.tz_convert(TZ)

    for c in ["ch2_temp_f", "ch3_temp_f", "ch8_temp_f", "indoor_temp_f"]:
        w.loc[(w[c] < 50) | (w[c] > 95), c] = np.nan
    w["haven_f"] = w["temperature_c"] * 9 / 5 + 32
    # thermostat display-unit pass-through: deg C after the TCC cutover
    w["tstat_f"] = _to_f(w["tstat_indoor_raw"])
    w.loc[(w["tstat_f"] < 55) | (w["tstat_f"] > 95), "tstat_f"] = np.nan  # old Control4 C/F glitches
    for c in ["cool_setpoint_f", "heat_setpoint_f"]:
        w[c] = _to_f(w[c])

    # Model target: mean of the three 0.1 degC Ecowitt rooms (2F hall, 2F south BR, 1F office).
    if "total_lmp_da" in w:
        w["da_cents_per_kwh"] = (w["total_lmp_da"] / 10).ffill(limit=3)  # hourly -> 15-min
    w["house_f"] = w[["ch2_temp_f", "ch3_temp_f", "indoor_temp_f"]].mean(axis=1, skipna=False)
    w["out_f"] = w["ch1_temp_f"]
    w["solar"] = w["solar_wm2"].clip(lower=0)
    w["cool"] = (w["cool_actual_pct"].fillna(0) / 100).clip(0, 1)
    w["heat"] = (w["heat_actual_pct"].fillna(0) / 100).clip(0, 1)
    fan_base = w["blower_w"][w["cool"] < 0.01].median()
    w["hvac_kw"] = (w["compressor_w"] + (w["blower_w"] - fan_base).clip(lower=0)) / 1000
    return w


def _to_f(s: pd.Series) -> pd.Series:
    return s.where(s > 40, s * 9 / 5 + 32)


def load(rebuild: bool = False) -> pd.DataFrame:
    pkl = CACHE / "season.pkl"
    if pkl.exists() and not rebuild:
        return pd.read_pickle(pkl)
    w = assemble()
    w.to_pickle(pkl)
    return w


def raw_series(name: str, flux: str, refresh: bool = False) -> pd.DataFrame:
    """Run an un-aggregated Flux query over SSH, cache the CSV, return a long frame.

    Used for inputs that must keep native resolution (5-min prices for the
    controller emulator, the controller's own action log).
    """
    RAW.mkdir(parents=True, exist_ok=True)
    out = CACHE / "raw_native" / f"{name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not out.exists():
        res = subprocess.run(
            ["ssh", PI_SSH, "docker exec -i influxdb influx query --raw -f /dev/stdin"],
            input=flux, capture_output=True, text=True, timeout=600, check=True,
        )
        out.write_text(res.stdout)
    df = _read(out)
    if not df.empty:
        df["_time"] = pd.to_datetime(df["_time"], utc=True).dt.tz_convert(TZ)
    return df


CONTROLLER_WINDOW = ("2026-07-06T00:00:00Z", "2026-10-01T05:00:00Z")


def controller_inputs(refresh: bool = False) -> dict[str, pd.DataFrame]:
    """5-min ComEd prices, thermostat RH, and the controller's action log for the live window."""
    a, b = CONTROLLER_WINDOW
    rng = f"range(start: {a}, stop: {b})"
    price = raw_series("price_5min", f'''from(bucket: "energy") |> {rng}
  |> filter(fn: (r) => r._measurement == "comed.prices" and r.period_type == "5min" and r._field == "price_cents_per_kwh")
  |> keep(columns: ["_time", "_value"])''', refresh)
    rh = raw_series("tstat_rh", f'''from(bucket: "energy") |> {rng}
  |> filter(fn: (r) => r._measurement == "hvac.thermostat" and r._field == "humidity_pct")
  |> keep(columns: ["_time", "_value"])''', refresh)
    actions = raw_series("hvac_actions", f'''from(bucket: "energy") |> {rng}
  |> filter(fn: (r) => r._measurement == "hvac.actions")
  |> filter(fn: (r) => contains(value: r._field, set: ["commanded_cool", "schedule_cool", "applied", "humidity_gated", "setpoint_reason", "actual_humidity"]))
  |> map(fn: (r) => ({{r with _value: string(v: r._value)}}))  // fields mix int/float/string
  |> group()
  |> pivot(rowKey: ["_time", "action_label", "tier", "dry_run"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "action_label", "tier", "dry_run", "commanded_cool", "schedule_cool", "applied", "humidity_gated", "setpoint_reason", "actual_humidity"])''', refresh)
    for c in ("commanded_cool", "schedule_cool", "applied", "humidity_gated", "actual_humidity"):
        if c in actions:
            actions[c] = pd.to_numeric(actions[c], errors="coerce")
    return {"price": price, "rh": rh, "actions": actions}
