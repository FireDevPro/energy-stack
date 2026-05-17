"""Per-hour HVAC telemetry validity per docs/plans/sced-rebaseline-spec-2026-05-13.md
§7.

Rules:
- >=110 samples per HVAC channel in the hour (= 92% of nominal 120 at 30s cadence)
- No single-channel intra-hour gap > 120 seconds (2 min)
- ANY-channel-failure rule: if ANY of {em:2, em:8, em:9} fails either threshold,
  the hour is telemetry-invalid.
"""
from __future__ import annotations

import datetime

import pandas as pd


MIN_SAMPLES_PER_CHANNEL = 110
MAX_INTRA_HOUR_GAP_SECONDS = 120
HVAC_CHANNELS: tuple[str, ...] = ("em:2", "em:8", "em:9")


def hour_is_telemetry_valid(
    refoss_df: pd.DataFrame,
    hour_start_utc: datetime.datetime,
) -> bool:
    """Returns True iff every HVAC channel meets spec §7 thresholds in the
    [hour_start_utc, hour_start_utc + 1h) window.

    ``refoss_df`` is a long-form DataFrame with at least columns:
    ``_time`` (datetime), ``channel`` (str). Other columns are ignored.
    """
    hour_end = hour_start_utc + datetime.timedelta(hours=1)
    in_hour = (refoss_df["_time"] >= hour_start_utc) & (refoss_df["_time"] < hour_end)
    hour_df = refoss_df.loc[in_hour, ["_time", "channel"]]
    for ch in HVAC_CHANNELS:
        ch_df = hour_df.loc[hour_df["channel"] == ch].sort_values("_time")
        n = len(ch_df)
        if n < MIN_SAMPLES_PER_CHANNEL:
            return False
        if n >= 2:
            gaps = ch_df["_time"].diff().dropna().dt.total_seconds()
            if (gaps > MAX_INTRA_HOUR_GAP_SECONDS).any():
                return False
    return True
