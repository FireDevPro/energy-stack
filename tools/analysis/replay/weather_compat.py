"""Weather-derived Ecowitt compatibility converter.

Fetches ASOS observations from Iowa State Mesonet's bulk CGI endpoint
and writes them into an Ecowitt-shaped long-format parquet plus a
manifest entry tagged ``source_type=weather_derived_compatibility``.
The Stage 3 loader then consumes the parquet via the same code path it
uses for real Ecowitt data.

Naming rule: these are "weather-derived Ecowitt compatibility rows",
not "Ecowitt-from-NWS". The source is ASOS and ERA5, not nws.forecast.

Plan: docs/plans/weather-compat-plan.md
Spec: OSF_FILING.md criterion 14 source-type weather_derived_compatibility

Phase 1 scope (tracer bullet): single-field (outdoor_temp_f) end-to-end
slice through fetch + parquet + manifest. Phases 2-5 expand cadence,
solar, gap-fill, and CLI polish.
"""
from __future__ import annotations

import datetime
import io
from pathlib import Path
from typing import Any

import pandas as pd

from tools.analysis.replay.manifest import (
    Manifest,
    MeasurementEntry,
    WEATHER_DERIVED_COMPATIBILITY,
    compute_sha256,
    parquet_filename,
    read_manifest,
    write_manifest,
)


IEM_BULK_CGI_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Station -> (latitude, longitude). KORD is the locked default; other
# stations can be added or pulled from IEM's network metadata in a
# follow-on. Phase 3 only needs KORD.
STATION_COORDS: dict[str, tuple[float, float]] = {
    "KORD": (41.97861, -87.90472),
}


def _http_get(url: str, params: dict[str, Any] | None = None, timeout: int = 60):
    """Single HTTP boundary. Tests patch this; production wires
    `requests.get` (the import is deferred so the production dependency
    doesn't bleed into test environments that mock the boundary)."""
    import requests
    return requests.get(url, params=params, timeout=timeout)


def _ct_iso(utc_dt: datetime.datetime) -> str:
    """Convert a UTC datetime to an ISO 8601 string in America/Chicago."""
    from zoneinfo import ZoneInfo
    return utc_dt.astimezone(ZoneInfo("America/Chicago")).isoformat()


def _iem_csv_to_rows(csv_text: str) -> list[dict[str, Any]]:
    """Parse the IEM bulk CGI CSV response into a list of dicts.

    Empty (header-only) responses yield an empty list.
    """
    df = pd.read_csv(io.StringIO(csv_text))
    if len(df) == 0:
        return []
    return df.to_dict(orient="records")


# Locked field mapping. Each entry: IEM-CSV column name -> (Ecowitt
# output field, unit-conversion lambda). Identity functions are
# explicit so the table reads as a single source of truth.
ASOS_FIELD_MAP: dict[str, tuple[str, Any]] = {
    "tmpf": ("outdoor_temp_f", lambda v: v),
    "dwpf": ("outdoor_dewpoint_f", lambda v: v),
    "relh": ("outdoor_rh_pct", lambda v: v),
    "mslp": ("pressure_inhg", lambda v: v / 33.8639),
    "sknt": ("wind_mph", lambda v: v * 1.15078),
}

# Open-Meteo hourly fields, mapped to Ecowitt output fields. We request
# temperature_unit=fahrenheit and wind_speed_unit=mph from the API so
# the values arrive in the units we emit. Pressure stays hPa (mb) and
# converts to inHg via the same divisor as ASOS mslp.
OPEN_METEO_FIELD_MAP: dict[str, tuple[str, Any]] = {
    "temperature_2m": ("outdoor_temp_f", lambda v: v),
    "dew_point_2m": ("outdoor_dewpoint_f", lambda v: v),
    "relative_humidity_2m": ("outdoor_rh_pct", lambda v: v),
    "pressure_msl": ("pressure_inhg", lambda v: v / 33.8639),
    "wind_speed_10m": ("wind_mph", lambda v: v),
}

FIVE_MIN = datetime.timedelta(minutes=5)

# Material-gap threshold (per docs/plans/weather-compat-plan.md Phase 4).
# A contiguous ASOS absence of ≥ MATERIAL_GAP_MINUTES across the 5-min
# grid is gap-filled from Open-Meteo / ERA5. Sub-threshold gaps are
# covered by ASOS forward-fill alone.
MATERIAL_GAP_MINUTES = 60
ASOS_FFILL_LIMIT_SLOTS = MATERIAL_GAP_MINUTES // 5


def _parse_iem_native(
    iem_rows: list[dict[str, Any]],
) -> pd.DataFrame:
    """Parse IEM CSV rows into a long-format DataFrame with one row per
    (timestamp, output-field) pair. No resample, no fill — just the
    converted native observations."""
    out: list[dict[str, Any]] = []
    for row in iem_rows:
        valid_str = str(row["valid"])
        ts = pd.to_datetime(valid_str, utc=True)
        if pd.isna(ts):
            continue
        for iem_col, (out_field, conv) in ASOS_FIELD_MAP.items():
            raw = row.get(iem_col)
            if raw is None or pd.isna(raw):
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue
            out.append({
                "_time": ts,
                "_field": out_field,
                "_value": conv(v),
            })
    if not out:
        return pd.DataFrame(columns=["_time", "_field", "_value"])
    df = pd.DataFrame(out)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def _build_ecowitt_long_df(
    iem_rows: list[dict[str, Any]],
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    station: str,
) -> pd.DataFrame:
    """Map IEM ASOS rows into Ecowitt long-format rows on a 5-min grid.

    For each output field present in any native row, builds a 5-min
    UTC grid across [start_utc, end_utc) and forward-fills from the
    nearest preceding native observation. Native rows that land
    exactly on a grid timestamp are tagged ``upsampled=False``;
    forward-filled slots are tagged ``upsampled=True``.

    Per-row provenance columns (``weather_source``, ``solar_source``,
    ``station``, ``cadence``, ``upsampled``) are audit-only — the
    Stage 3 loader reads only ``_time``, ``_measurement``, ``_field``,
    ``_value`` and ignores the rest.

    Empty inputs yield an empty long DataFrame (no rows emitted).
    """
    native = _parse_iem_native(iem_rows)
    if len(native) == 0:
        return pd.DataFrame(columns=[
            "_time", "_measurement", "_field", "_value",
            "weather_source", "solar_source", "station",
            "cadence", "upsampled",
        ])

    total_min = int((end_utc - start_utc).total_seconds() // 60)
    n_slots = max(0, total_min // 5)
    if n_slots == 0:
        return pd.DataFrame(columns=[
            "_time", "_measurement", "_field", "_value",
            "weather_source", "solar_source", "station",
            "cadence", "upsampled",
        ])
    grid = pd.date_range(start=start_utc, periods=n_slots, freq="5min")

    out_rows: list[dict[str, Any]] = []
    for out_field in sorted(native["_field"].unique()):
        sub = (
            native[native["_field"] == out_field]
            .sort_values("_time")
            .set_index("_time")
        )
        native_index = set(sub.index)
        # Union grid + native index, ffill values within the material-gap
        # window, then restrict to grid. Slots beyond the ffill limit
        # become NaN and are handled by Phase 4 ERA5 gap-fill.
        union_index = sub.index.union(grid)
        # ffill limit is in rows. Since the union is ordered by time at
        # 5-min cadence (after merging native into the grid), the limit
        # equates to the material-gap threshold.
        reindexed = (
            sub["_value"]
            .reindex(union_index)
            .ffill(limit=ASOS_FFILL_LIMIT_SLOTS)
        )
        on_grid = reindexed.loc[grid]
        for ts, val in on_grid.items():
            if pd.isna(val):
                continue
            out_rows.append({
                "_time": ts,
                "_measurement": "ecowitt.weather",
                "_field": out_field,
                "_value": float(val),
                "weather_source": "iem_asos",
                "solar_source": None,
                "station": station,
                "cadence": "5min",
                "upsampled": ts not in native_index,
            })
    if not out_rows:
        return pd.DataFrame(columns=[
            "_time", "_measurement", "_field", "_value",
            "weather_source", "solar_source", "station",
            "cadence", "upsampled",
        ])
    df = pd.DataFrame(out_rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def _fetch_open_meteo_full(
    station: str,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
) -> dict[str, Any]:
    """Fetch the Open-Meteo hourly payload for the station's lat/lon.

    Requests shortwave_radiation (Phase 3 solar source) plus the five
    Phase 4 gap-fill fields, in target units (°F for temperature,
    mph for wind speed). Returns the raw payload dict (may be empty
    or missing keys when the archive has no data).

    Raises ``KeyError`` if the station has no lat/lon entry. Phase 5
    will broaden this once configurable stations land.
    """
    if station not in STATION_COORDS:
        raise KeyError(
            f"station {station!r} has no lat/lon entry in STATION_COORDS"
        )
    lat, lon = STATION_COORDS[station]
    hourly_fields = ",".join([
        "shortwave_radiation",
        "temperature_2m",
        "dew_point_2m",
        "relative_humidity_2m",
        "pressure_msl",
        "wind_speed_10m",
    ])
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_utc.date().isoformat(),
        "end_date": end_utc.date().isoformat(),
        "hourly": hourly_fields,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "UTC",
    }
    response = _http_get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=60)
    response.raise_for_status()
    return response.json() or {}


def _open_meteo_native_rows(
    payload: dict[str, Any],
    field_name: str,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
) -> list[dict[str, Any]]:
    """Pull one Open-Meteo hourly field into (timestamp, value) rows
    filtered to [start_utc, end_utc)."""
    hourly = payload.get("hourly", {}) or {}
    times = hourly.get("time", []) or []
    values = hourly.get(field_name, []) or []
    out: list[dict[str, Any]] = []
    for t_str, v in zip(times, values):
        if v is None:
            continue
        ts = pd.to_datetime(t_str, utc=True)
        if pd.isna(ts):
            continue
        if not (start_utc <= ts.to_pydatetime() < end_utc):
            continue
        try:
            out.append({"_time": ts, "_value": float(v)})
        except (TypeError, ValueError):
            continue
    return out


def _build_solar_long_df(
    open_meteo_payload: dict[str, Any],
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    station: str,
) -> pd.DataFrame:
    """Resample Open-Meteo hourly shortwave_radiation to the 5-min
    Ecowitt grid with forward-fill, mirroring the ASOS grid logic.
    Solar rows tag ``solar_source=open_meteo_era5`` and leave
    ``weather_source`` null."""
    cols = [
        "_time", "_measurement", "_field", "_value",
        "weather_source", "solar_source", "station",
        "cadence", "upsampled",
    ]
    solar_rows = _open_meteo_native_rows(
        open_meteo_payload, "shortwave_radiation", start_utc, end_utc,
    )
    if not solar_rows:
        return pd.DataFrame(columns=cols)

    total_min = int((end_utc - start_utc).total_seconds() // 60)
    n_slots = max(0, total_min // 5)
    if n_slots == 0:
        return pd.DataFrame(columns=cols)
    grid = pd.date_range(start=start_utc, periods=n_slots, freq="5min")

    native = pd.DataFrame(solar_rows).sort_values("_time").set_index("_time")
    native_index = set(native.index)
    union_index = native.index.union(grid)
    reindexed = native["_value"].reindex(union_index).ffill()
    on_grid = reindexed.loc[grid]

    out: list[dict[str, Any]] = []
    for ts, val in on_grid.items():
        if pd.isna(val):
            continue
        out.append({
            "_time": ts,
            "_measurement": "ecowitt.weather",
            "_field": "solar_wm2",
            "_value": float(val),
            "weather_source": None,
            "solar_source": "open_meteo_era5",
            "station": station,
            "cadence": "5min",
            "upsampled": ts not in native_index,
        })
    if not out:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(out)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def _build_gap_fill_long_df(
    asos_df: pd.DataFrame,
    open_meteo_payload: dict[str, Any],
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    station: str,
) -> pd.DataFrame:
    """Fill 5-min grid slots not covered by ASOS using Open-Meteo / ERA5.

    For each Ecowitt field in the ASOS field set, identifies grid slots
    where asos_df has no row, then forward-fills those slots from the
    corresponding Open-Meteo hourly field. Output rows tag
    ``weather_source=open_meteo_era5_gap_fill`` and ``upsampled=True``.

    Empty when Open-Meteo has no gap-fill fields or when ASOS already
    covers every slot.
    """
    cols = [
        "_time", "_measurement", "_field", "_value",
        "weather_source", "solar_source", "station",
        "cadence", "upsampled",
    ]
    total_min = int((end_utc - start_utc).total_seconds() // 60)
    n_slots = max(0, total_min // 5)
    if n_slots == 0:
        return pd.DataFrame(columns=cols)
    grid = pd.date_range(start=start_utc, periods=n_slots, freq="5min")

    # ASOS coverage by field: set of timestamps already populated.
    asos_coverage: dict[str, set] = {}
    if len(asos_df) > 0 and "_field" in asos_df.columns:
        for out_field in asos_df["_field"].unique():
            asos_coverage[out_field] = set(
                asos_df.loc[asos_df["_field"] == out_field, "_time"]
            )

    out_rows: list[dict[str, Any]] = []
    for om_field, (out_field, conv) in OPEN_METEO_FIELD_MAP.items():
        native = _open_meteo_native_rows(
            open_meteo_payload, om_field, start_utc, end_utc,
        )
        if not native:
            continue
        native_df = (
            pd.DataFrame(native)
            .sort_values("_time")
            .set_index("_time")
        )
        union_index = native_df.index.union(grid)
        reindexed = native_df["_value"].reindex(union_index).ffill()
        on_grid = reindexed.loc[grid]
        already_covered = asos_coverage.get(out_field, set())
        for ts, val in on_grid.items():
            if pd.isna(val):
                continue
            if ts in already_covered:
                continue
            out_rows.append({
                "_time": ts,
                "_measurement": "ecowitt.weather",
                "_field": out_field,
                "_value": float(conv(val)),
                "weather_source": "open_meteo_era5_gap_fill",
                "solar_source": None,
                "station": station,
                "cadence": "5min",
                "upsampled": True,
            })
    if not out_rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(out_rows)
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df


def _build_manifest(
    df: pd.DataFrame,
    parquet_path: Path,
    out_dir: Path,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    station: str,
) -> Manifest:
    """Assemble a Manifest for the compat bundle.

    When the dataframe is empty we still write a manifest, but with an
    empty entries tuple. The bundle is well-formed and reflects the
    fetch attempt; a downstream merge can still treat it as a no-op.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if len(df) == 0:
        entries: tuple[MeasurementEntry, ...] = ()
    else:
        first_ts = df["_time"].min()
        last_ts = df["_time"].max()
        entries = (MeasurementEntry(
            measurement="ecowitt.weather",
            source_type=WEATHER_DERIVED_COMPATIBILITY,
            parquet_path=parquet_path.name,
            row_count=len(df),
            sha256=compute_sha256(parquet_path),
            field_set=tuple(sorted(df["_field"].unique())),
            first_timestamp_utc=first_ts.isoformat() if pd.notna(first_ts) else None,
            last_timestamp_utc=last_ts.isoformat() if pd.notna(last_ts) else None,
            note=(
                f"IEM ASOS bulk CGI, station={station}, "
                f"report_type=1,3,4 (HFMETAR + routine + special METAR), "
                f"5-min grid with forward-fill upsample"
            ),
        ),)
    return Manifest(
        export_window_start_ct=_ct_iso(start_utc),
        export_window_end_ct=_ct_iso(end_utc),
        source_bucket="iem_asos_bulk_cgi",
        exported_at_utc=now_utc.isoformat(),
        exporter={
            "version": "weather_compat_phase1",
            "station": station,
        },
        entries=entries,
    )


def fetch(
    station: str,
    start_utc: datetime.datetime,
    end_utc: datetime.datetime,
    out_dir: Path,
) -> Manifest:
    """Fetch one ASOS window from IEM and write a compat bundle.

    Phase 1 contract:
      - One HTTP call to the IEM bulk CGI.
      - Parquet at ``<out_dir>/ecowitt.weather.parquet`` (long-format,
        Influx-shaped).
      - Manifest at ``<out_dir>/manifest.json`` with one entry tagged
        ``weather_derived_compatibility`` (or zero entries if IEM
        returned no rows).

    Network calls go through ``_http_get`` so tests can patch the HTTP
    boundary without bringing up a server.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    params = {
        "station": station,
        "year1": start_utc.year,
        "month1": start_utc.month,
        "day1": start_utc.day,
        "hour1": start_utc.hour,
        "minute1": start_utc.minute,
        "year2": end_utc.year,
        "month2": end_utc.month,
        "day2": end_utc.day,
        "hour2": end_utc.hour,
        "minute2": end_utc.minute,
        "data": "tmpf,dwpf,relh,sknt,mslp",
        "tz": "Etc/UTC",
        "format": "onlycomma",
        "report_type": "1,3,4",     # HFMETAR + routine METAR + special
    }
    response = _http_get(IEM_BULK_CGI_URL, params=params, timeout=60)
    response.raise_for_status()
    rows = _iem_csv_to_rows(response.text)
    asos_df = _build_ecowitt_long_df(
        rows, start_utc=start_utc, end_utc=end_utc, station=station,
    )

    # Open-Meteo ERA5 supplies both solar (always; ASOS has no
    # shortwave) and gap-fill for material ASOS outages. A missing-
    # station lat/lon raises (Phase 5 broadens this).
    open_meteo_payload = _fetch_open_meteo_full(station, start_utc, end_utc)
    solar_df = _build_solar_long_df(
        open_meteo_payload,
        start_utc=start_utc, end_utc=end_utc, station=station,
    )
    gap_df = _build_gap_fill_long_df(
        asos_df=asos_df,
        open_meteo_payload=open_meteo_payload,
        start_utc=start_utc, end_utc=end_utc, station=station,
    )

    frames = [d for d in (asos_df, gap_df, solar_df) if len(d) > 0]
    df = pd.concat(frames, ignore_index=True) if frames else asos_df

    parquet_path = out_dir / parquet_filename(
        "ecowitt.weather", WEATHER_DERIVED_COMPATIBILITY,
    )
    if len(df) > 0:
        df.to_parquet(parquet_path, index=False)

    manifest = _build_manifest(
        df=df,
        parquet_path=parquet_path,
        out_dir=out_dir,
        start_utc=start_utc,
        end_utc=end_utc,
        station=station,
    )
    write_manifest(manifest, out_dir / "manifest.json")
    return manifest


def merge(
    compat_dir: Path,
    target_stage1_dir: Path,
) -> Manifest:
    """Merge a fetched compat bundle into an existing stage1 bundle.

    Copies each parquet file from compat_dir to target_stage1_dir and
    appends compat's manifest entries to target's manifest. Rejects
    the merge when target already has an entry for the same
    (measurement, source_type) tuple — the manifest schema requires
    uniqueness on that key. Caller should re-fetch into a different
    window or merge into a different target.

    Returns the merged Manifest written back to target_stage1_dir.
    """
    import shutil
    compat_dir = Path(compat_dir)
    target_stage1_dir = Path(target_stage1_dir)

    compat_manifest_path = compat_dir / "manifest.json"
    target_manifest_path = target_stage1_dir / "manifest.json"
    if not compat_manifest_path.exists():
        raise FileNotFoundError(f"compat manifest missing: {compat_manifest_path}")
    if not target_manifest_path.exists():
        raise FileNotFoundError(f"target manifest missing: {target_manifest_path}")

    compat = read_manifest(compat_manifest_path)
    target = read_manifest(target_manifest_path)

    existing_keys = {(e.measurement, e.source_type) for e in target.entries}
    for entry in compat.entries:
        key = (entry.measurement, entry.source_type)
        if key in existing_keys:
            raise ValueError(
                f"target {target_stage1_dir} already has an entry for "
                f"measurement={entry.measurement!r} "
                f"source_type={entry.source_type!r}"
            )

    # Copy each parquet referenced by compat into the target dir.
    for entry in compat.entries:
        src = compat_dir / entry.parquet_path
        dst = target_stage1_dir / entry.parquet_path
        if not src.exists():
            raise FileNotFoundError(
                f"compat manifest references missing parquet: {src}"
            )
        shutil.copy2(src, dst)

    merged = Manifest(
        export_window_start_ct=target.export_window_start_ct,
        export_window_end_ct=target.export_window_end_ct,
        source_bucket=target.source_bucket,
        exported_at_utc=target.exported_at_utc,
        exporter=target.exporter,
        entries=target.entries + compat.entries,
        known_missing_measurements=target.known_missing_measurements,
    )
    write_manifest(merged, target_manifest_path)
    return merged


def _parse_iso_utc(s: str) -> datetime.datetime:
    """Parse an ISO 8601 timestamp into a tz-aware UTC datetime.
    Accepts both '+00:00' and 'Z' suffixes."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    Subcommands:
      fetch  --station --start --end --out
      merge  --compat-dir --target-stage1-dir

    Both subcommands print the resulting manifest path on success.
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m tools.analysis.replay.weather_compat",
        description=(
            "Weather-derived Ecowitt compatibility converter. Builds "
            "an Ecowitt-shaped parquet from IEM ASOS + Open-Meteo / "
            "ERA5 for replay-validation bundles."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser(
        "fetch",
        help="Fetch a weather-derived compatibility bundle.",
    )
    p_fetch.add_argument("--station", default="KORD",
                         help="ASOS station (default: KORD)")
    p_fetch.add_argument("--start", required=True,
                         help="UTC start, ISO 8601 (e.g. 2025-07-15T19:00:00+00:00)")
    p_fetch.add_argument("--end", required=True, help="UTC end, ISO 8601")
    p_fetch.add_argument("--out", required=True, help="Output bundle directory")

    p_merge = sub.add_parser(
        "merge",
        help="Merge a fetched compat bundle into a stage1 export.",
    )
    p_merge.add_argument("--compat-dir", required=True,
                         help="Source compat bundle directory")
    p_merge.add_argument("--target-stage1-dir", required=True,
                         help="Target stage1 export directory")

    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        manifest = fetch(
            station=args.station,
            start_utc=_parse_iso_utc(args.start),
            end_utc=_parse_iso_utc(args.end),
            out_dir=Path(args.out),
        )
        print(f"wrote {Path(args.out) / 'manifest.json'}")
        print(f"entries: {len(manifest.entries)}")
    elif args.cmd == "merge":
        merge(
            compat_dir=Path(args.compat_dir),
            target_stage1_dir=Path(args.target_stage1_dir),
        )
        print(
            f"merged {args.compat_dir} into "
            f"{Path(args.target_stage1_dir) / 'manifest.json'}"
        )


if __name__ == "__main__":
    main()
