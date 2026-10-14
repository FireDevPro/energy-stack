"""Regression tests for the 2026-10-01 AirNow web-service retirement.

AirNow retired ``/aq/observation/latLong/current/`` (HTTP 410) and replaced it
with ``/aq/observation/current/ziplatlong/``: lowerCamelCase keys, ``nowcastAQI``
instead of ``AQI``, ``OZONE`` instead of ``O3``, ``hourObserved`` as ``"HH:MM"``,
and a 200 ``WebServiceError`` envelope instead of ``[]`` when nothing is in range.
Payloads below are verbatim captures from Pi-lab on 2026-10-14.
"""
from datetime import datetime, timedelta, timezone

from airnow_poller.app import AIRNOW_URL, _obs_age_min, build_point, parse_observations

_OBS = [
    {"dateObserved": "2026-10-13", "hourObserved": "19:00", "localTimeZone": "CDT",
     "reportingAreaName": "Joliet and Will County", "siteID": "170434002", "siteName": "Naperville",
     "parameterName": "PM2.5", "nowcastAQI": 62, "aqiCategoryName": "Moderate",
     "reportingAgency": "Illinois EPA", "lookupBehavior": "Closest Reading By Pollutant",
     "consideredMonitors": "All", "lookupBoundary": "50 Miles"},
    {"dateObserved": "2026-10-13", "hourObserved": "19:00", "localTimeZone": "CDT",
     "reportingAreaName": "Joliet and Will County", "siteID": "170311601", "siteName": "LEMONT",
     "parameterName": "OZONE", "nowcastAQI": 30, "aqiCategoryName": "Good",
     "reportingAgency": "Illinois EPA", "lookupBehavior": "Closest Reading By Pollutant",
     "consideredMonitors": "All", "lookupBoundary": "50 Miles"},
    {"dateObserved": "2026-10-13", "hourObserved": "19:00", "localTimeZone": "CDT",
     "reportingAreaName": "Joliet and Will County", "siteID": "170314201", "siteName": "NORTHBRK",
     "parameterName": "PM10", "nowcastAQI": 51, "aqiCategoryName": "Moderate",
     "reportingAgency": "Illinois EPA", "lookupBehavior": "Closest Reading By Pollutant",
     "consideredMonitors": "All", "lookupBoundary": "50 Miles"},
]

_NO_OBS = {"WebServiceError": [{"Message": "There are no observations available for the "
           "requested latitude/longitude: No observations were found for all monitors within 50 miles."}]}


def _fields(point) -> dict:
    return {k: v for k, v in point._fields.items()}


def test_uses_replacement_endpoint():
    assert AIRNOW_URL == "https://www.airnowapi.org/aq/observation/current/ziplatlong/"


def test_build_point_keeps_influx_schema_stable():
    p = build_point(_OBS)
    assert p is not None
    assert p._tags == {"reporting_area": "Joliet and Will County"}
    f = _fields(p)
    assert f["pm25_aqi"] == 62
    assert f["o3_aqi"] == 30          # OZONE -> o3_aqi (unchanged field key)
    assert f["pm10_aqi"] == 51
    assert f["overall_aqi"] == 62
    assert f["dominant"] == "PM2.5"
    assert f["category"] == "Moderate"
    assert isinstance(f["obs_age_min"], int)


def test_dominant_ozone_reported_as_o3():
    obs = [dict(_OBS[1], nowcastAQI=120)] + _OBS[:1]
    f = _fields(build_point(obs))
    assert f["dominant"] == "O3"      # old vocabulary preserved for downstream


def test_no_observation_envelope_is_empty():
    assert parse_observations(_NO_OBS) == []
    assert build_point([]) is None


def test_obs_age_parses_hh_mm_and_cdt():
    # 2026-10-13 19:00 CDT == 2026-10-14 00:00Z
    age = _obs_age_min(_OBS)
    expect = int((datetime.now(timezone.utc) - datetime(2026, 10, 14, tzinfo=timezone.utc)).total_seconds() // 60)
    assert abs(age - expect) <= 1
    assert age >= 0
