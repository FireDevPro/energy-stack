"""Tests for the §5 coverage scorecard section."""
from tools.decision_trace_report.sections.coverage_scorecard import render


def test_coverage_scorecard_observed_vs_not_observed():
    """For each reason_code in the reference enum, show whether it was
    observed cumulatively + last 7 days."""
    reference_codes = {
        "PriceOverlayCode": [
            "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER",
            "PRICE_OVERLAY_UPGRADED_TO_ELEVATED",
            "PRICE_OVERLAY_STALE_FEED_RELEASED",
        ],
    }
    cumulative_counts = {
        "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER": 12000,
        "PRICE_OVERLAY_UPGRADED_TO_ELEVATED": 0,
        "PRICE_OVERLAY_STALE_FEED_RELEASED": 0,
    }
    recent_7d_counts = {
        "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER": 2000,
        "PRICE_OVERLAY_UPGRADED_TO_ELEVATED": 0,
        "PRICE_OVERLAY_STALE_FEED_RELEASED": 0,
    }

    out = render(
        reference_codes=reference_codes,
        cumulative_counts=cumulative_counts,
        recent_7d_counts=recent_7d_counts,
    )

    assert "PriceOverlayCode" in out
    assert "PRICE_OVERLAY_NORMAL_BELOW_TRIGGER" in out
    # Observed -> ✅; not observed -> ⚪
    assert "✅" in out
    assert "⚪" in out
    # Counts should be visible
    assert "12000" in out or "12,000" in out
    assert "2000" in out or "2,000" in out


def test_coverage_scorecard_flags_unexpected_codes():
    """Any cumulative_counts key NOT in reference_codes flat-list is an
    'unexpected reason code' anomaly."""
    reference_codes = {
        "SupervisorCode": ["SUPERVISOR_APPROVED", "SUPERVISOR_CLAMPED_COOL_FLOOR"],
    }
    cumulative_counts = {
        "SUPERVISOR_APPROVED": 100,
        "SUPERVISOR_CLAMPED_COOL_FLOOR": 0,
        "SUPERVISOR_UNKNOWN_PHANTOM": 3,
    }
    recent_7d_counts = {"SUPERVISOR_APPROVED": 10}

    out = render(
        reference_codes=reference_codes,
        cumulative_counts=cumulative_counts,
        recent_7d_counts=recent_7d_counts,
    )

    assert "SUPERVISOR_UNKNOWN_PHANTOM" in out
    assert "unexpected" in out.lower()
