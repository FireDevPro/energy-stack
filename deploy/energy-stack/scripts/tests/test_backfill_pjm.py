"""Tests for backfill_pjm.py — the one-shot 5-year PJM backfill script.

Covers point construction, ComEd-zone-code constants, year-range
defaults, and the per-spec field/tag mapping. HTTP and Influx are
not exercised here; they're hit live in the smoke test the dev runs
before merging.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backfill_pjm import (
    COMED_PNODE_ID,
    COMED_ZONE_CODE,
    FEED_SPECS,
    ArchiveSpanError,
    FeedSpec,
    build_points,
    default_year_range,
    fetch_year,
    parse_years,
)


# ---- pinned constants -----------------------------------------------------


def test_comed_pnode_id_pinned():
    """ComEd zonal aggregate pnode_id. Verified live against
    /api/v1/da_hrl_lmps?type=Zone. If this changes, the entire backfill
    is wrong."""
    assert COMED_PNODE_ID == 33092371


def test_comed_zone_code_is_CE_not_COMED():
    """Empirically: hrl_load_metered uses zone='CE' for ComEd, not 'COMED'.
    PJM uses cryptic abbreviations in this feed (CE, AE, PS, etc.). If we
    ever get back 0 rows on a backfill, this constant is the first
    suspect."""
    assert COMED_ZONE_CODE == "CE"


def test_feed_specs_have_expected_keys():
    assert set(FEED_SPECS.keys()) == {"da_lmp", "metered_load"}


def test_da_lmp_spec_uses_pnode_id_filter():
    spec = FEED_SPECS["da_lmp"]
    assert spec.feed == "da_hrl_lmps"
    assert spec.extra_filters == {"pnode_id": str(COMED_PNODE_ID)}
    assert spec.measurement == "pjm.lmp_da_hourly"


def test_metered_load_spec_uses_zone_filter():
    spec = FEED_SPECS["metered_load"]
    assert spec.feed == "hrl_load_metered"
    assert spec.extra_filters == {"zone": COMED_ZONE_CODE}
    assert spec.measurement == "pjm.metered_load"


# ---- year range default ---------------------------------------------------


def test_default_year_range_is_five_years():
    assert len(default_year_range()) == 5


def test_default_year_range_ends_at_last_full_year():
    """The 'last completed' calendar year should be the right edge of
    the default window. Live data lags ~1 day, so this is always
    well-populated."""
    expected_last = datetime.now(timezone.utc).year - 1
    assert default_year_range()[-1] == expected_last


def test_parse_years_explicit():
    assert parse_years("2021,2022,2023") == [2021, 2022, 2023]


def test_parse_years_handles_whitespace():
    assert parse_years(" 2024 , 2025 ") == [2024, 2025]


def test_parse_years_default_when_none():
    assert parse_years(None) == default_year_range()


# ---- build_points: DA LMP -------------------------------------------------


def _da_item(hour_ept: int, lmp: float = 30.0) -> dict:
    return {
        "datetime_beginning_ept": f"2024-07-15T{hour_ept:02d}:00:00",
        "datetime_beginning_utc": f"2024-07-15T{hour_ept + 4:02d}:00:00",
        "pnode_id": COMED_PNODE_ID,
        "pnode_name": "COMED",
        "zone": None,
        "type": "ZONE",
        "total_lmp_da": lmp,
        "system_energy_price_da": lmp - 1.0,
        "congestion_price_da": 0.5,
        "marginal_loss_price_da": 0.5,
    }


def test_build_da_lmp_points_count():
    pts = build_points(FEED_SPECS["da_lmp"], [_da_item(h) for h in range(24)])
    assert len(pts) == 24


def test_build_da_lmp_points_carry_pnode_tags():
    [pt] = build_points(FEED_SPECS["da_lmp"], [_da_item(13, 42.5)])
    assert pt["measurement"] == "pjm.lmp_da_hourly"
    assert pt["tags"]["pnode_id"] == str(COMED_PNODE_ID)
    assert pt["tags"]["pnode_name"] == "COMED"
    assert pt["fields"]["total_lmp_da"] == 42.5


def test_build_da_lmp_zone_tag_falls_back_to_pnode_name():
    """For ZONE-type pnodes, PJM returns zone=null; the backfill
    synthesizes the zone tag from pnode_name so downstream queries
    can filter by zone."""
    [pt] = build_points(FEED_SPECS["da_lmp"], [_da_item(0)])
    assert pt["tags"]["zone"] == "COMED"


def test_build_da_lmp_handles_none_price_components():
    """Defensive: if PJM ever drops a price component, write 0 rather
    than crash."""
    item = _da_item(10)
    item["congestion_price_da"] = None
    item["marginal_loss_price_da"] = None
    [pt] = build_points(FEED_SPECS["da_lmp"], [item])
    assert pt["fields"]["congestion_price_da"] == 0.0
    assert pt["fields"]["marginal_loss_price_da"] == 0.0


# ---- build_points: metered load ------------------------------------------


def _load_item(hour_ept: int, mw: float = 12000.0) -> dict:
    return {
        "datetime_beginning_ept": f"2024-07-15T{hour_ept:02d}:00:00",
        "datetime_beginning_utc": f"2024-07-15T{hour_ept + 4:02d}:00:00",
        "is_verified": True,
        "load_area": "CE",
        "mkt_region": "WEST",
        "mw": mw,
        "nerc_region": "RFC",
        "zone": "CE",
    }


def test_build_metered_load_count():
    pts = build_points(FEED_SPECS["metered_load"], [_load_item(h) for h in range(24)])
    assert len(pts) == 24


def test_build_metered_load_tags_and_fields():
    [pt] = build_points(FEED_SPECS["metered_load"], [_load_item(13, 14500.5)])
    assert pt["measurement"] == "pjm.metered_load"
    assert pt["tags"]["zone"] == "CE"
    assert pt["tags"]["load_area"] == "CE"
    assert pt["fields"]["mw"] == 14500.5


def test_build_metered_load_no_zone_fallback_synthesis():
    """Metered load already has zone in the payload (CE). Don't synthesize."""
    [pt] = build_points(FEED_SPECS["metered_load"], [_load_item(0)])
    assert pt["tags"]["zone"] == "CE"


# ---- timestamp conversion -------------------------------------------------


def test_ept_to_utc_during_dst():
    """July is CDT/EDT. EPT 13:00 in July = 17:00 UTC. The backfill must
    produce UTC timestamps so InfluxDB is unambiguous regardless of
    DST state."""
    [pt] = build_points(FEED_SPECS["metered_load"], [_load_item(13)])
    assert pt["time_utc"].tzinfo == timezone.utc
    assert pt["time_utc"].hour == 17  # 13 EPT + 4 (EDT offset) = 17 UTC


def test_ept_to_utc_during_standard_time():
    """November-March is EST. EPT 13:00 in February = 18:00 UTC.
    Different offset because of DST."""
    item = _load_item(13)
    item["datetime_beginning_ept"] = "2024-02-15T13:00:00"
    [pt] = build_points(FEED_SPECS["metered_load"], [item])
    assert pt["time_utc"].hour == 18  # 13 EST + 5 = 18 UTC


# ---- archive-cutoff handling ----------------------------------------------


def test_da_lmp_skips_year_in_archive():
    """da_hrl_lmps has a 731-day archive cutoff. A year clearly in the past
    (2010) must raise ArchiveSpanError so the caller can skip it. The
    script's main loop catches this and continues to the next year rather
    than aborting the whole backfill."""
    spec = FEED_SPECS["da_lmp"]
    with pytest.raises(ArchiveSpanError, match="archive cutoff"):
        fetch_year(spec, api_key="not-used", year=2010)


def test_metered_load_has_no_archive_cutoff():
    """hrl_load_metered's metadata reports archiveCutoffDays=null, meaning
    all of its history (back to 1993) is in standard tier and accessible
    with the same filter set. This is critical for our 5CP-probability
    training corpus, which needs many years of historical load."""
    assert FEED_SPECS["metered_load"].archive_cutoff_days is None
