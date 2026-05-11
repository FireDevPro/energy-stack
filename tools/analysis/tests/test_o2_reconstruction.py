"""Tests for tools/o2_capacity_reconstruction/reconstruct.py."""
from __future__ import annotations

import pytest

from tools.o2_capacity_reconstruction.reconstruct import (
    TariffConstants,
    cplc_kw,
    annual_capacity_charge_dollars,
    sensitivity_band,
)


@pytest.fixture
def cons() -> TariffConstants:
    return TariffConstants(
        year=2026,
        comed_npl_mw=22000.0,
        a_comed_cpl_mw=18500.0,
        portfolio_sum_mw=3500.0,
        rate_dollars_per_kw_month=2.5,
        is_placeholder=True,
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


def test_sensitivity_band_collapses_in_branch_1(cons):
    band = sensitivity_band(3.0, 2.5, cons)
    assert band["low"] == band["point"] == band["high"]


def test_sensitivity_band_widens_with_portfolio(cons):
    band = sensitivity_band(3.0, 3.5, cons, portfolio_pct=0.10)
    assert band["low"] < band["point"] < band["high"]
    # +10% portfolio shrinks the adjustment; -10% expands it.
    # adjustment_high = 3.5e6 * 0.5 / (3.5e6 * 0.9) = 0.555...
    # adjustment_low  = 3.5e6 * 0.5 / (3.5e6 * 1.1) = 0.454...
    assert band["high"] == pytest.approx(3.0 + 0.5 / 0.9, abs=1e-4)
    assert band["low"] == pytest.approx(3.0 + 0.5 / 1.1, abs=1e-4)
