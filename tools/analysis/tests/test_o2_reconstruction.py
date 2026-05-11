"""Tests for tools/o2_capacity_reconstruction/reconstruct.py."""
from __future__ import annotations

import pytest

from tools.o2_capacity_reconstruction.reconstruct import (
    PORTFOLIO_SUM_SCENARIOS_MW,
    TariffConstants,
    annual_capacity_charge_dollars,
    cplc_kw,
    scenarios,
)


@pytest.fixture
def cons() -> TariffConstants:
    return TariffConstants(
        year=2026,
        comed_npl_mw=22000.0,
        a_comed_cpl_mw=18500.0,
        portfolio_sum_mw=3500.0,
        rate_dollars_per_kw_month=2.5,
        is_placeholder=False,
    )


def test_branch_1_takes_acust_cpl_directly(cons):
    # ACustCPL >= ACustPL: first branch.
    cplc = cplc_kw(a_cust_cpl_kw=3.0, a_cust_pl_kw=2.5, constants=cons)
    assert cplc == 3.0


def test_branch_2_adds_portfolio_adjustment(cons):
    # ACustCPL < ACustPL: second branch picks up gap.
    # gap = (22000 - 18500) * 1000 = 3.5e6 kW
    # portfolio = 3500 * 1000 = 3.5e6 kW
    # adjustment = 3.5e6 * (3.5 - 3.0) / 3.5e6 = 0.5
    # CPLC = 3.0 + 0.5 = 3.5
    cplc = cplc_kw(a_cust_cpl_kw=3.0, a_cust_pl_kw=3.5, constants=cons)
    assert cplc == pytest.approx(3.5)


def test_dollar_conversion(cons):
    # 3.0 kW at $2.5/kW-month for 5 months = $37.50
    assert annual_capacity_charge_dollars(3.0, cons) == pytest.approx(37.5)


def test_scenarios_collapse_in_branch_1(cons):
    out = scenarios(3.0, 2.5, cons)
    assert set(out.keys()) == {"low", "anchor_2021", "high"}
    assert out["low"] == out["anchor_2021"] == out["high"] == 3.0


def test_scenarios_use_locked_denominators(cons):
    # gap = (22000 - 18500) * 1000 = 3.5e6 kW; customer gap = 0.5 kW
    # adjustment(scenario) = 3.5e6 * 0.5 / (portfolio_mw * 1000)
    out = scenarios(3.0, 3.5, cons)
    for name, portfolio_mw in PORTFOLIO_SUM_SCENARIOS_MW.items():
        expected = 3.0 + (3.5e6 * 0.5) / (portfolio_mw * 1000.0)
        assert out[name] == pytest.approx(expected, abs=1e-6)
    # Larger denominator → smaller adjustment → smaller CPLC.
    assert out["low"] > out["anchor_2021"] > out["high"]


def test_scenarios_accept_custom_denominators(cons):
    out = scenarios(3.0, 3.5, cons, portfolio_sums_mw={"only": 3500.0})
    # adjustment = 3.5e6 * 0.5 / 3.5e6 = 0.5 → CPLC = 3.5
    assert out == {"only": pytest.approx(3.5)}


def test_locked_scenarios_match_preregistration():
    assert PORTFOLIO_SUM_SCENARIOS_MW == {
        "low": 1500.0,
        "anchor_2021": 2033.653,
        "high": 3000.0,
    }
