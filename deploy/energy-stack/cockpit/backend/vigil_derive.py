"""Pure derivations for the Vigil board — no I/O, fully unit-tested.

These turn raw rev-4 Influx rows into the shapes ``vigil.html`` renders:
tier, hold, this_spike / episodes, the why-line, avoided-cost, liveness.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional, TypedDict

ASSUMED_KW = 2.35  # single v1 constant (spec: 2.35 / 3.22 — no thermal model)
ALIVE_THRESHOLD_SEC = 600  # matches the watchdog's 10-min DOWN threshold
RELEASE_DISPLAY_MIN = 15  # how long the stand-down banner lingers after a spike


class Transition(TypedDict):
    tier: str          # new_tier
    at: datetime       # transition time (UTC)
    cents: float       # current_price_cents at the transition


class Episode(TypedDict):
    started_at: datetime
    ended_at: Optional[datetime]
    tiers_walked: list[dict[str, Any]]   # [{tier, at(iso)}] — object form
    tiers_walked_strs: list[str]         # ["elevated", ...] — string form
    peak_cents: float
    resolution: str                      # released | ongoing
    duration_min: int
    representative_cents: float


def c_to_f(c: float | None) -> float | None:
    if c is None:
        return None
    return round(c * 9.0 / 5.0 + 32.0, 1)


def tier_from_cents(cents: float | None, elevated_at: float, scarcity_at: float) -> str:
    if cents is None:
        return "normal"
    if cents >= scarcity_at:
        return "scarcity"
    if cents >= elevated_at:
        return "elevated"
    return "normal"


def alive_from(
    last_tick_age_sec: float | None,
    watchdog_down: bool,
    threshold_sec: int = ALIVE_THRESHOLD_SEC,
) -> bool:
    if watchdog_down or last_tick_age_sec is None:
        return False
    return last_tick_age_sec < threshold_sec


def avoided_cost(minutes: float, price_cents: float, kw: float = ASSUMED_KW) -> float:
    """v1 estimate (always labeled 'est.'): energy the coasting compressor
    did NOT draw, valued at the spike price."""
    return round((minutes / 60.0) * kw * (price_cents / 100.0), 2)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def build_episodes(
    transitions: Iterable[Transition],
    *,
    now: datetime,
    peak_lookup: Optional[Iterable[tuple[datetime, float]]] = None,
) -> list[Episode]:
    """Group ``hvac.price_overlay`` tier-transition rows into engage→release
    episodes. An episode opens when the tier leaves ``normal`` and closes on
    the next transition back to ``normal``; a still-open run is ``ongoing``.

    ``peak_lookup`` is the 5-min price series (``[(t, cents)]``); when given,
    ``peak_cents`` is the true max over the episode window rather than just
    the max at transition points.
    """
    rows = sorted(transitions, key=lambda r: r["at"])
    series = sorted(peak_lookup or [], key=lambda p: p[0])
    episodes: list[Episode] = []
    cur: Optional[Episode] = None
    cur_prices: list[float] = []

    def close(ep: Episode, ended_at: Optional[datetime], resolution: str) -> None:
        ep["ended_at"] = ended_at
        ep["resolution"] = resolution
        end = ended_at or now
        ep["duration_min"] = max(0, int((end - ep["started_at"]).total_seconds() // 60))
        window = [c for (t, c) in series if ep["started_at"] <= t <= end]
        candidates = cur_prices + window
        ep["peak_cents"] = round(max(candidates), 1) if candidates else 0.0
        ep["representative_cents"] = (
            round(sum(cur_prices) / len(cur_prices), 1) if cur_prices else ep["peak_cents"]
        )

    for r in rows:
        if r["tier"] == "normal":
            if cur is not None:
                close(cur, r["at"], "released")
                episodes.append(cur)
                cur = None
                cur_prices = []
            continue
        # non-normal transition
        if cur is None:
            cur = Episode(
                started_at=r["at"], ended_at=None, tiers_walked=[],
                tiers_walked_strs=[], peak_cents=0.0, resolution="ongoing",
                duration_min=0, representative_cents=0.0,
            )
            cur_prices = []
        cur["tiers_walked"].append({"tier": r["tier"], "at": _iso(r["at"])})
        cur["tiers_walked_strs"].append(r["tier"])
        cur_prices.append(r["cents"])

    if cur is not None:
        close(cur, None, "ongoing")
        episodes.append(cur)
    return episodes


def this_spike_from_episodes(
    episodes: list[Episode],
    *,
    now: datetime,
    tier_current: str,
    hold_active: bool,
) -> Optional[dict[str, Any]]:
    """The hero spike object for ``/now``. Ongoing → engaged (ended=False);
    just-closed and back to normal with no hold → release (ended=True)."""
    if not episodes:
        return None
    ep = episodes[-1]
    est = avoided_cost(ep["duration_min"], ep["representative_cents"])
    if ep["resolution"] == "ongoing":
        return {
            "tiers_walked": ep["tiers_walked"],
            "peak_cents": ep["peak_cents"],
            "duration_min": ep["duration_min"],
            "est_avoided_cost_usd": est,
            "ended": False,
        }
    # closed episode: show the stand-down for a short window
    ended_at = ep["ended_at"]
    if (
        ended_at is not None
        and tier_current == "normal"
        and not hold_active
        and (now - ended_at).total_seconds() <= RELEASE_DISPLAY_MIN * 60
    ):
        return {
            "tiers_walked": ep["tiers_walked"],
            "peak_cents": ep["peak_cents"],
            "duration_min": ep["duration_min"],
            "est_avoided_cost_usd": est,
            "ended": True,
        }
    return None


def build_hold(
    action: dict[str, Any] | None,
    *,
    now: datetime,
    compressor_on: bool,
    hold_expires_at: datetime | None,
) -> Optional[dict[str, Any]]:
    """Assemble the ``hold`` object from the latest ``hvac.actions`` row when
    an own-hold is active (expiry in the future). Returns None otherwise."""
    if action is None or hold_expires_at is None or hold_expires_at <= now:
        return None
    pushed_at = action.get("_time")
    minutes_held = (
        max(0, int((now - pushed_at).total_seconds() // 60)) if pushed_at else None
    )
    minutes_to_expiry = max(0, int((hold_expires_at - now).total_seconds() // 60))
    return {
        "commanded_cool_f": c_to_f(action.get("commanded_cool")),
        "schedule_cool_f": c_to_f(action.get("schedule_cool")),
        "minutes_held": minutes_held,
        "minutes_to_expiry": minutes_to_expiry,
        "coasting": not compressor_on,
    }


def compose_why(
    *,
    tier: str,
    cents: float | None,
    commanded_cool_f: float | None,
    minutes_to_expiry: int | None,
    gated: bool,
    fresh: bool,
    alive: bool,
    releasing: bool,
) -> str:
    c = f"{cents:.1f}" if cents is not None else "—"
    cmd = f"{round(commanded_cool_f)}" if commanded_cool_f is not None else "—"
    mins = f"{minutes_to_expiry}" if minutes_to_expiry is not None else "—"
    if not alive:
        return "⚠ Controller not responding — thermostat is on its own program."
    if not fresh:
        return "Price feed stale — standing down to your schedule."
    if releasing:
        return "Spike over — releasing back to your schedule."
    if tier == "scarcity":
        why = f"SCARCITY, {c}¢ — holding {cmd}°, riding the thermal battery, ~{mins} min left on this hold."
    elif tier == "elevated":
        why = f"Elevated power, {c}¢ — holding {cmd}°, ~{mins} min left on this hold."
    else:
        why = f"Cheap power, {c}¢ — thermostat's running its own schedule."
    if gated:
        why += " (humidity gate: cooling to dry the air)"
    return why
