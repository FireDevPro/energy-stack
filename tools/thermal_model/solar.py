"""Approximate irradiance on a south-facing vertical surface from the horizontal pyranometer.

The fitted solar coupling per horizontal W/m2 rises every month from June to
November, the signature of low sun reaching south glazing. Scaling horizontal
irradiance by the beam ratio Rb = cos(incidence on a south wall) / sin(elevation)
gives an input that tracks window gain across seasons. This is an approximation:
it treats all irradiance as beam and caps Rb at 5.
"""
from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd

LAT, LON = 41.9, -88.0  # approximate site (Chicago area, NWS LOT grid); fine for a seasonal ratio


def sun_position(index: pd.DatetimeIndex, bin_minutes: float = 15) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Solar elevation and azimuth (from north), radians, at the centre of each bin (NOAA approximation)."""
    t = index.tz_convert("UTC")
    doy = t.dayofyear.to_numpy()
    hr = (t.hour + t.minute / 60 + bin_minutes / 120).to_numpy()
    g = 2 * np.pi / 365 * (doy - 1 + (hr - 12) / 24)
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g) - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    ha = np.radians((hr * 60 + eqt + 4 * LON) / 4 - 180)
    lat = np.radians(LAT)
    elev = np.arcsin(np.clip(np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha), -1, 1))
    az = np.arctan2(np.sin(ha), np.cos(ha) * np.sin(lat) - np.tan(decl) * np.cos(lat)) + np.pi
    return elev, az


def south_vertical_ratio(index: pd.DatetimeIndex) -> npt.NDArray[np.float64]:
    elev, az = sun_position(index)
    cos_inc = np.clip(np.cos(elev) * np.cos(az - np.pi), 0, None)
    low = np.radians(3)
    return np.where(elev > low, np.clip(cos_inc / np.sin(np.maximum(elev, low)), 0, 5), 0.0)


def blended_solar(w: pd.DataFrame, south_fraction: float) -> pd.Series:
    """(1 - f) * horizontal + f * south-vertical estimate, in W/m2."""
    rb = south_vertical_ratio(pd.DatetimeIndex(w.index))
    return w["solar"] * ((1 - south_fraction) + south_fraction * rb)
