"""Grey-box RC thermal model of the house (deg F, hours, 15-min steps).

2R2C (diagnostic only; fails closed-loop replay):
    dTi/dt = a_env (To - Ti) + a_im (Tm - Ti) + b_air S + c - k_cool g(u_c) + k_heat u_h
    dTm/dt = r a_im (Ti - Tm) + b_mass S
1R1C (primary; = 2R2C with a_im = b_mass = 0, pinned via ``fixed``):
    dTi/dt = a_env (To - Ti) + b_air S + c - k_cool g(u_c) + k_heat u_h

S is solar in kW/m2, u_c / u_h the ComfortNet actual cool / heat fraction, and
g() maps ComfortNet cool % to delivered capacity. ComfortNet reports stage 1 as
50%; ``stage1_capacity`` sets what fraction of full capacity that delivers
(0.5 = linear, which the summer data marginally prefers).

Fitting forward-simulates each 24 h day from the measured start temperature
and minimises simulated-vs-measured error over the whole day. That makes it
robust to sensor quantisation, unlike fitting instantaneous dT/dt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.optimize import least_squares

DT = 0.25
STEPS = 96
SUBSTEPS = 3  # Euler sub-steps per 15 min for the fast air node

Arr = npt.NDArray[np.float64]

PARAM_NAMES = ("a_env", "a_im", "r", "b_air", "b_mass", "c", "k_cool", "k_heat")
X0 = np.array([0.02, 0.5, 0.05, 0.3, 0.1, 0.2, 2.0, 2.0])
LB = np.array([0, 0.01, 0.001, 0, 0, -2, 0, 0])
UB = np.array([1, 5, 1, 10, 5, 3, 15, 15])


@dataclass
class Params:
    a_env: float
    a_im: float
    r: float
    b_air: float
    b_mass: float
    c: float
    k_cool: float
    k_heat: float
    stage1_capacity: float = 0.5

    @classmethod
    def from_vector(cls, x: Arr, stage1_capacity: float = 0.5) -> "Params":
        return cls(*(float(v) for v in x), stage1_capacity=stage1_capacity)

    def vector(self) -> Arr:
        return np.array([getattr(self, n) for n in PARAM_NAMES])

    @property
    def tau_hours(self) -> float:
        return 1.0 / self.a_env if self.a_env > 0 else float("inf")


def cool_capacity(u: Arr | float, stage1: float) -> Arr:
    """Map ComfortNet cool fraction (stage 1 = 0.5, stage 2 = 1.0) to delivered capacity fraction."""
    u = np.asarray(u, dtype=float)
    return np.where(u <= 0.5, u / 0.5 * stage1, stage1 + (u - 0.5) / 0.5 * (1 - stage1))


def cool_capacity_inverse(g: Arr | float, stage1: float) -> Arr:
    g = np.asarray(g, dtype=float)
    return np.where(g <= stage1, g / stage1 * 0.5, 0.5 + (g - stage1) / (1 - stage1) * 0.5)


def step(p: Params, ti: float, tm: float, to: float, s: float, g_cool: float, u_heat: float) -> tuple[float, float]:
    """Advance (Ti, Tm) by one 15-min step. ``g_cool`` is delivered capacity fraction."""
    h = DT / SUBSTEPS
    for _ in range(SUBSTEPS):
        dti = p.a_env * (to - ti) + p.a_im * (tm - ti) + p.b_air * s + p.c - p.k_cool * g_cool + p.k_heat * u_heat
        dtm = p.r * p.a_im * (ti - tm) + p.b_mass * s
        ti, tm = ti + h * dti, tm + h * dtm
    return ti, tm


@dataclass
class Segment:
    t0: pd.Timestamp
    ti: Arr
    to: Arr
    s: Arr
    uc: Arr
    uh: Arr
    tm0: float
    extra: dict[str, Arr] = field(default_factory=dict)


def segments(
    w: pd.DataFrame, start: str, stop: str, target: str = "house_f", solar_col: str = "solar", days: int = 1
) -> list[Segment]:
    """Complete ``days``-long blocks starting at local midnight in [start, stop] with no unfillable gaps.

    Multi-day blocks matter for the 2R2C model: with 1-day blocks the hidden mass
    temperature is re-initialised every midnight, so the fit can park solar heat in
    the mass and never pay for releasing it overnight.
    """
    d = w.loc[start:stop, [target, "out_f", solar_col, "cool", "heat"]].copy()
    d[[target, "out_f", solar_col]] = d[[target, "out_f", solar_col]].interpolate(limit=4, limit_area="inside")
    trail = w[target].rolling("24h", min_periods=48).mean()
    out: list[Segment] = []
    n = STEPS * days
    for day in pd.date_range(d.index[0].normalize(), d.index[-1].normalize(), freq=f"{days}D"):
        blk = d.loc[day : day + pd.Timedelta(hours=24 * days) - pd.Timedelta(minutes=15)]
        if len(blk) != n or blk.isna().any().any():
            continue
        tm0 = trail.get(day, np.nan)
        out.append(
            Segment(
                t0=day,
                ti=blk[target].to_numpy(float),
                to=blk["out_f"].to_numpy(float),
                s=blk[solar_col].to_numpy(float) / 1000.0,
                uc=blk["cool"].to_numpy(float),
                uh=blk["heat"].to_numpy(float),
                tm0=float(tm0) if np.isfinite(tm0) else float(blk[target].iloc[0]),
            )
        )
    return out


def simulate(p: Params, seg: Segment) -> Arr:
    """Open-loop day simulation driven by the measured HVAC inputs."""
    g = cool_capacity(seg.uc, p.stage1_capacity)
    n = len(seg.ti)
    ti_out = np.empty(n)
    ti, tm = float(seg.ti[0]), seg.tm0
    ti_out[0] = ti
    for k in range(n - 1):
        ti, tm = step(p, ti, tm, seg.to[k], seg.s[k], float(g[k]), seg.uh[k])
        ti_out[k + 1] = ti
    return ti_out


@dataclass
class FitResult:
    params: Params
    n_days: int
    free: tuple[str, ...]


def fit(segs: list[Segment], stage1_capacity: float = 0.5, fixed: dict[str, float] | None = None) -> FitResult:
    """Least-squares fit of the 2R2C model on whole-day simulation error.

    ``fixed`` pins named parameters (e.g. ``{"k_heat": 2.0}`` for a cooling-only
    window where heating is unidentifiable).
    """
    fixed = fixed or {}
    x = X0.copy()
    for k, v in fixed.items():
        x[PARAM_NAMES.index(k)] = v
    free_idx = [i for i, n in enumerate(PARAM_NAMES) if n not in fixed]

    def resid(z: Arr) -> Arr:
        xx = x.copy()
        xx[free_idx] = z
        p = Params.from_vector(xx, stage1_capacity)
        return np.concatenate([simulate(p, s)[1:] - s.ti[1:] for s in segs])

    r = least_squares(resid, x[free_idx], bounds=(LB[free_idx], UB[free_idx]), x_scale="jac", max_nfev=400)
    x[free_idx] = r.x
    return FitResult(Params.from_vector(x, stage1_capacity), len(segs), tuple(PARAM_NAMES[i] for i in free_idx))


def score(p: Params, segs: list[Segment]) -> dict[str, float]:
    err, pers, at6 = [], [], []
    for s in segs:
        e = simulate(p, s) - s.ti
        err.append(e[1:])
        at6.append(e[24])
        pers.append(s.ti[1:] - s.ti[0])
    e_all, p_all = np.concatenate(err), np.concatenate(pers)
    return {
        "n_days": float(len(segs)),
        "rmse_24h": float(np.sqrt(np.mean(e_all**2))),
        "rmse_persistence": float(np.sqrt(np.mean(p_all**2))),
        "bias": float(np.mean(e_all)),
        "p95_abs": float(np.percentile(np.abs(e_all), 95)),
        "rmse_at_6h": float(np.sqrt(np.mean(np.square(at6)))),
    }
