"""Per-hour HVAC telemetry validity per docs/plans/sced-rebaseline-spec-2026-05-13.md
§7.

Rules:
- >=110 samples per HVAC channel in the hour (= 92% of nominal 120 at 30s cadence)
- No single-channel intra-hour gap > 120 seconds (2 min). The gap rule
  applies to all gaps within the hour, INCLUDING the boundary gaps
  (hour_start -> first sample, last sample -> hour_end).
- ANY-channel-failure rule: if ANY of {em:2, em:8, em:9} fails either threshold,
  the hour is telemetry-invalid.

Input contract:
- ``refoss_df`` is long-form with columns: ``_time``, ``channel``, plus
  optionally ``_field`` and ``_value``. If ``_field`` is present, only
  rows with ``_field == "power_w"`` are counted (the analysis layer only
  cares about real-power samples). Callers that pre-filter may omit
  ``_field`` entirely.
- ``_time`` is a pandas datetime. Caller is responsible for keeping
  ``refoss_df["_time"]`` and ``hour_start_utc`` in the SAME timezone
  convention (both naive UTC, or both tz-aware UTC). Mixing will raise.
"""
from __future__ import annotations

import datetime

import pandas as pd


MIN_SAMPLES_PER_CHANNEL = 110
MAX_INTRA_HOUR_GAP_SECONDS = 120
HVAC_CHANNELS: tuple[str, ...] = ("em:2", "em:8", "em:9")
POWER_FIELD = "power_w"


def hour_is_telemetry_valid(
    refoss_df: pd.DataFrame,
    hour_start_utc: datetime.datetime,
) -> bool:
    """Returns True iff every HVAC channel meets spec §7 thresholds in the
    [hour_start_utc, hour_start_utc + 1h) window.
    """
    hour_end = hour_start_utc + datetime.timedelta(hours=1)
    df = refoss_df
    if "_field" in df.columns:
        df = df[df["_field"] == POWER_FIELD]
    in_hour = (df["_time"] >= hour_start_utc) & (df["_time"] < hour_end)
    hour_df = df.loc[in_hour, ["_time", "channel"]]
    for ch in HVAC_CHANNELS:
        ch_df = hour_df.loc[hour_df["channel"] == ch].sort_values("_time")
        n = len(ch_df)
        if n < MIN_SAMPLES_PER_CHANNEL:
            return False
        # Build the full timeline including hour boundaries so a trailing
        # outage or a delayed first sample is caught.
        times = ch_df["_time"].tolist()
        timeline = [hour_start_utc] + times + [hour_end]
        gaps_s = [
            (timeline[i + 1] - timeline[i]).total_seconds()
            for i in range(len(timeline) - 1)
        ]
        if any(g > MAX_INTRA_HOUR_GAP_SECONDS for g in gaps_s):
            return False
    return True
