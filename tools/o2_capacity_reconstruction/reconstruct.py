"""Att. M-2 §2 CPLC reconstruction for O2 Layer 2.

Pure functions: take household-observed (`ACustCPL_Y`, `ACustPL_Y`)
plus the stipulated portfolio constants for year Y, return
`CPLC_(Y+1)` and the dollar capacity charge it converts to.

Per EXPERIMENT_DESIGN.md §6:

    Layer 1 (primary): ACustCPL difference at the 5 PJM Five Peak hours.
    Layer 2 (descriptive): full CPLC_(Y+1) reconstruction including the
        second branch's portfolio term.

This module implements Layer 2. The pipeline calls it once per arm
(A, B) per summer year, using each arm's observed ACustCPL and
ACustPL across its qualifying weeks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class TariffConstants:
    """Stipulated values from the locked tariff snapshot, for one year."""
    year: int
    comed_npl_mw: float          # ComEdNPL_Y
    a_comed_cpl_mw: float        # AComEdCPL_Y
    portfolio_sum_mw: float      # Σ_5Pc(ACustPL - ACustCPL)
    rate_dollars_per_kw_month: float
    is_placeholder: bool

    @classmethod
    def load(cls, year: int, path: Path | None = None) -> "TariffConstants":
        path = path or (HERE / "tariff_constants.json")
        with open(path) as f:
            j = json.load(f)
        return cls(
            year=year,
            comed_npl_mw=float(j["ComEdNPL_mw_by_year"][str(year)]),
            a_comed_cpl_mw=float(j["AComEdCPL_mw_by_year"][str(year)]),
            portfolio_sum_mw=float(j["portfolio_sum_mw_by_year"][str(year)]),
            rate_dollars_per_kw_month=float(
                j["capacity_rate_dollars_per_kw_month_by_year"][str(year)]
            ),
            is_placeholder=bool(j.get("PLACEHOLDER", False)),
        )


def cplc_kw(
    a_cust_cpl_kw: float,
    a_cust_pl_kw: float,
    constants: TariffConstants,
) -> float:
    """Compute CPLC_(Y+1) in kW per Att. M-2 §2.

    First branch when ACustCPL >= ACustPL:
        CPLC = ACustCPL.
    Second branch otherwise:
        CPLC = ACustCPL + (ComEdNPL - AComEdCPL) *
                (ACustPL - ACustCPL) / Σ_5Pc(ACustPL - ACustCPL).
    """
    if a_cust_cpl_kw >= a_cust_pl_kw:
        return a_cust_cpl_kw
    # Branch 2: portfolio adjustment.
    # All MW values in the portfolio terms are converted to kW for
    # arithmetic consistency. The ratio is dimensionless so it
    # doesn't actually matter, but we keep kW throughout for clarity.
    gap = (constants.comed_npl_mw - constants.a_comed_cpl_mw) * 1000.0
    portfolio = constants.portfolio_sum_mw * 1000.0
    adjustment = gap * (a_cust_pl_kw - a_cust_cpl_kw) / portfolio
    return a_cust_cpl_kw + adjustment


def annual_capacity_charge_dollars(
    cplc_kw_value: float,
    constants: TariffConstants,
    months_billed: int = 5,
) -> float:
    """Convert a kW CPLC value to a dollar Y+1 capacity charge.

    Default 5 months billed: ComEd applies the residential capacity
    rate over May-Sep per the locked tariff schedule.
    """
    return cplc_kw_value * constants.rate_dollars_per_kw_month * months_billed


def sensitivity_band(
    a_cust_cpl_kw: float,
    a_cust_pl_kw: float,
    constants: TariffConstants,
    portfolio_pct: float = 0.10,
) -> dict[str, float]:
    """Return {low, point, high} CPLC_(Y+1) kW under ±portfolio_pct
    sensitivity on the portfolio_sum_mw stipulated constant.

    Per EXPERIMENT_DESIGN.md §6, Layer 2 is reported with ±10%
    sensitivity on the portfolio constant. This helper produces
    that band so the report can be assembled without re-loading
    the constants.
    """
    if a_cust_cpl_kw >= a_cust_pl_kw:
        point = a_cust_cpl_kw  # branch 1 — portfolio constant unused
        return {"low": point, "point": point, "high": point}
    point = cplc_kw(a_cust_cpl_kw, a_cust_pl_kw, constants)
    low_constants = TariffConstants(
        year=constants.year,
        comed_npl_mw=constants.comed_npl_mw,
        a_comed_cpl_mw=constants.a_comed_cpl_mw,
        portfolio_sum_mw=constants.portfolio_sum_mw * (1 + portfolio_pct),
        rate_dollars_per_kw_month=constants.rate_dollars_per_kw_month,
        is_placeholder=constants.is_placeholder,
    )
    high_constants = TariffConstants(
        year=constants.year,
        comed_npl_mw=constants.comed_npl_mw,
        a_comed_cpl_mw=constants.a_comed_cpl_mw,
        portfolio_sum_mw=constants.portfolio_sum_mw * (1 - portfolio_pct),
        rate_dollars_per_kw_month=constants.rate_dollars_per_kw_month,
        is_placeholder=constants.is_placeholder,
    )
    return {
        "low": cplc_kw(a_cust_cpl_kw, a_cust_pl_kw, low_constants),
        "point": point,
        "high": cplc_kw(a_cust_cpl_kw, a_cust_pl_kw, high_constants),
    }
