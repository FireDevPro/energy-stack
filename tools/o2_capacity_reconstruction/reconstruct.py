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
        """Load locked tariff constants for a capacity year.

        ``year`` is the Att. M-2 capacity / delivery year (Jun Y through
        May Y+1 billing window). For loading by summer-of-peaks instead,
        use :meth:`load_for_summer_year`.

        `portfolio_sum_mw` is set to the `low` scenario denominator
        (1,500 MW) by convention. Layer 2 reporting must use
        `scenarios(...)` to enumerate all three pre-registered
        denominators; using the single value here would silently
        discard the scenario framing.
        """
        path = path or (HERE / "tariff_constants.json")
        with open(path) as f:
            j = json.load(f)
        return cls(
            year=year,
            comed_npl_mw=float(j["ComEdNPL_mw_by_year"][str(year)]),
            a_comed_cpl_mw=float(j["AComEdCPL_mw_by_year"][str(year)]),
            portfolio_sum_mw=float(j["portfolio_sum_mw_scenarios"]["low"]),
            rate_dollars_per_kw_month=float(
                j["capacity_rate_dollars_per_kw_month_by_year"][str(year)]
            ),
            is_placeholder=bool(j.get("PLACEHOLDER", False)),
        )

    @classmethod
    def load_for_summer_year(
        cls, summer_year: int, path: Path | None = None,
    ) -> "TariffConstants":
        """Load constants applicable to a summer's 5CP determination.

        Att. M-2 specifies ``CPLC_(Y+1)`` from summer Y peaks: the 5CPs
        observed in summer Y feed the capacity year Y+1 (which runs
        Jun Y+1 through May Y+2). This wrapper takes the natural
        ``summer_year`` argument and dispatches to ``load(summer_year + 1)``,
        which is the capacity year keyed in tariff_constants.json.

        Example: summer_year=2025 → load(2026), which returns the
        2026/27 capacity-year constants applied to summer 2025 peaks.
        """
        return cls.load(year=summer_year + 1, path=path)


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


# Locked Layer 2 portfolio_sum_mw scenarios (per EXPERIMENT_DESIGN.md §6
# Layer 2). Three named denominators replace the prior ±pct confidence band:
#   low          = prior low / planning case (matches portfolio_sum_mw_by_year)
#   anchor_2021  = FERC ER22-1520-001 deficiency response, Exhibits 1(b),
#                  2(b)(i), 2(b)(ii) — Weather Sensitive Customers, Summer 2021
#   high         = wide upper sensitivity near historical system-gap scale
PORTFOLIO_SUM_SCENARIOS_MW: dict[str, float] = {
    "low": 1500.0,
    "anchor_2021": 2033.653,
    "high": 3000.0,
}


def scenarios(
    a_cust_cpl_kw: float,
    a_cust_pl_kw: float,
    constants: TariffConstants,
    portfolio_sums_mw: dict[str, float] | None = None,
) -> dict[str, float]:
    """Return CPLC_(Y+1) kW for each named portfolio_sum_mw scenario.

    Per EXPERIMENT_DESIGN.md §6, Layer 2 is descriptive and reported
    across pre-registered named denominators on the portfolio sum,
    not as a confidence band. The denominator is computed inside
    ComEd's billing/allocation system from customer-register data
    and is not publicly observable in current-year form.

    Default scenarios match the locked preregistration:
        low          = 1,500 MW (prior planning case)
        anchor_2021  = 2,033.653 MW (FERC ER22-1520-001 deficiency
                       response, Weather Sensitive Customers, Summer 2021)
        high         = 3,000 MW (wide upper sensitivity)

    When branch 1 applies (`ACustCPL >= ACustPL`) the portfolio
    denominator is unused and all scenarios return the same kW value.
    """
    sums = portfolio_sums_mw if portfolio_sums_mw is not None else PORTFOLIO_SUM_SCENARIOS_MW
    if a_cust_cpl_kw >= a_cust_pl_kw:
        point = a_cust_cpl_kw  # branch 1 — denominator unused
        return {name: point for name in sums}
    results: dict[str, float] = {}
    for name, portfolio_mw in sums.items():
        scenario_constants = TariffConstants(
            year=constants.year,
            comed_npl_mw=constants.comed_npl_mw,
            a_comed_cpl_mw=constants.a_comed_cpl_mw,
            portfolio_sum_mw=portfolio_mw,
            rate_dollars_per_kw_month=constants.rate_dollars_per_kw_month,
            is_placeholder=constants.is_placeholder,
        )
        results[name] = cplc_kw(a_cust_cpl_kw, a_cust_pl_kw, scenario_constants)
    return results
