---
date: 2026-05-21
owner: chris
status: draft
role-label: methodology
name: THERMAL_COMMISSIONING
related:
  - docs/archive/THERMAL_MODEL_DESIGN.md
  - docs/plans/sced-rebaseline-spec-2026-05-13.md
  - docs/THERMAL_COMMISSIONING_CHECKLIST.md
---

# Thermal Commissioning Methodology

How we derive and cross-validate this house's whole-house thermal
parameters (UA, τ) for the SCED scheduler, using methods that fit a
single-family residence with the instrumentation we already run.

## Purpose

The `hvac-scheduler` (Arm B) needs two house-specific numbers to make
rational precooling and COAST decisions:

- **τ (thermal time constant, hours)** — how fast indoor temperature
  drifts toward outdoor when no equipment runs. Sets how aggressively
  the scheduler can pre-cool and how late it can shut off.
- **UA (overall heat transfer coefficient, BTU/hr/°F)** — how much
  capacity the system needs at steady state for any outdoor condition.
  Sets the load expectation the scheduler plans against.

The existing `thermal_observer` (see [archived design](archive/THERMAL_MODEL_DESIGN.md))
fits these from opportunistic operating data. Through May 2026 the fit
keeps rejecting on skill score. Investigation found two root causes:

1. **The thermostat reports indoor air at 1°F integer resolution.**
   96.5% of 10-min intervals show no change, so the persistence baseline
   ("predict no change") is mathematically near-optimal. The model
   cannot beat it.
2. **May has too few stage-2 cooling cycles.** Only 21 stage-2 intervals
   in 10 days. Not enough HVAC duty-cycle variation to identify
   coefficients precisely.

The first is structural (sensor hardware). The second is seasonal. Both
resolve naturally with WH31 ecowitt sensors (arriving 2026-05-21) and
summer cooling load, but the published literature on residential
envelope identification (Bacher–Madsen 2011, Pakanen et al. 2018,
co-heating reliability studies) consistently shows that **a single
opportunistic fit is fragile**. Triangulating against an independent
method strengthens the result and is what we should defend in the OSF
filing.

## What we're measuring

A lumped first-order RC envelope model:

```
C · dT_in/dt = (T_out - T_in) / R + Q_solar + Q_hvac + ε
```

where `τ = R · C`. We don't separate `R` and `C` — `τ` is what the
scheduler needs directly. `UA = 1/R` in the steady-state limit.

Bacher–Madsen reports τ ∈ [20, 200] hours for Danish residential
buildings. Pakanen's analysis of >10,000 US/EU residential buildings
shows summer τ ∈ [1, 18] hours (windows opened, infiltration high) and
winter τ ∈ [15, 55] hours. We have no prior on this house specifically
— deriving it is the point.

## Why three methods

The published research uses one method at a time and tolerates ±5-15%
uncertainty. We have continuous instrumentation that lets us run three
methods on the same building over the same season and check whether
they agree. Three independent methods agreeing is much stronger than
one method repeated. Disagreement is itself a diagnostic.

### Method 1 — Planned passive decay night

One carefully-chosen night where HVAC is deliberately turned off and
indoor temperature is logged at 1-minute cadence against a stable
outdoor reference. Fit an exponential decay to extract τ directly.

**Closest published analogue:** the "selected decay nights" approach
in Pakanen et al. (Syracuse / IBPC 2018) and Pasanen et al. on
unoccupied school buildings (Journal of Building Engineering 2025).
Both select "ideal" nights from operating data. We extend this by
*planning* a night to maximize signal quality.

**Conditions for a valid test** (synthesized from the literature):

| Requirement | Number | Source |
|---|---|---|
| Outdoor temperature variation across window | < 2°C (3.6°F) | Pakanen / Syracuse IBPC |
| HVAC fully off (compressor + blower) | yes | All sources |
| Solar gain | none — start ≥ 1 hr after sunset, end before sunrise | Co-heating literature |
| Wind | daily mean recorded; practical limit < 5 mph mean | CIBSE Module 120 (no standardized threshold) |
| Precipitation | none during window | Implicit across sources |
| Initial indoor-outdoor ΔT | ≥ 10°C (18°F) | Co-heating reliability studies |
| Window length | 6-8 hours | Pakanen / project checklist |
| Indoor sensor resolution | sub-degree (0.18°F or better) | Implicit — see root cause above |
| Sample cadence | 1-5 min | General consensus |

**Expected output:** one τ point estimate with uncertainty bound
(~±10% based on similar published methods). Run on at least two
different nights spanning different outdoor baselines for statistical
confidence.

### Method 2 — Continuous opportunistic fit (`thermal_observer`)

Already implemented. Reads HVAC, indoor, outdoor, solar from Influx
and fits the model on whatever operating data exists. Produces a
running distribution of τ and cooling-rate estimates over the season.

**Closest published analogue:** Bacher-Madsen Model 1 (the
methodological anchor in the existing design doc) and the
opportunistic-night-selection approach in Pakanen.

**What changes after WH31 deployment:** the indoor signal becomes
0.18°F resolution and the persistence baseline stops being near-optimal.
Skill score should rise from ~0.02 (current, May) to a meaningful
value (target ≥ 0.35 at 10-min cadence per the design doc's fallback
paragraph; target ≥ 0.5 if we go back to 5-min cadence). The model
math is unchanged.

**Expected output:** continuous distribution of τ estimates over many
fit windows; median and IQR are what we'd report.

### Method 3 — Bus-derived steady-state UA

For any period where the HVAC has been running steadily for ≥30
minutes at a known outdoor temperature, energy balance gives:

```
UA_implicit = (cool_actual_pct/100 · rated_capacity_BTU_hr) / (T_out - T_in)
```

**Closest published analogue:** ENERGY STAR HVAC commissioning
checklist (the ΔT method for charge verification) and Section 2.1 of
[Chris's HVAC Self-Commissioning Checklist](file://C:/Users/Chris/Box/HVAC%20Self-Commissioning%20Checklist.md).

**When this method works:** peak-summer steady cooling at large ΔT
(≥15°F). The formula assumes envelope conduction dominates the AC's
load, which is only true when dry-bulb ΔT is large. At smaller ΔT,
the AC's capacity is going mostly to latent cooling, internal heat,
solar gain, and duct-leakage compensation — inflating apparent UA.

**Pre-OSF verification (2026-05-21):** ran on 14 days of May data
including two 86°F+ days. Result: ~8,000 BTU/hr/°F. Implausible (typical
residential UA is 500-1,500). Conclusion: **May data is insufficient**
for this method. Park until July when sustained 90°F+ outdoor produces
the necessary ΔT.

**Expected output (when conditions support it):** point UA estimates
from multiple stable hot-afternoon windows; median and IQR.

## Cross-validation rules

We declare the model "commissioned" when:

1. Method 1 (planned decay) and Method 2 (continuous observer) agree
   within ±15% on τ. Both are decay-based; close agreement validates
   that the continuous observer isn't being misled by data filters.
2. Method 3 (bus-UA) is added once summer data is available. UA from
   Method 3 should be consistent with `τ × m·Cp` derived from
   Methods 1 and 2 within ±25% (looser bound because thermal mass
   `m·Cp` is itself uncertain).

If methods disagree, the disagreement is the finding. Likely
explanations and their resolutions are documented in the [checklist](THERMAL_COMMISSIONING_CHECKLIST.md).

## What feeds back into SCED

The methodology produces three values consumable by the scheduler:

- `τ_hours` — used in the COAST window length calculation and the
  pre-cool depth integration. See [docs/HVAC_LOGIC.md](HVAC_LOGIC.md).
- `P_cool_stage1_F_per_hr`, `P_cool_stage2_F_per_hr` — used in the
  stage-2 admission advisory (currently read-only per Step 1 design).
- `UA_BTU_per_hr_F` — used as a sanity check on the τ-derived
  parameters; not directly consumed by the scheduler.

Acceptance for scheduler use: a ratified fit per the
[design doc's plausibility gates](archive/THERMAL_MODEL_DESIGN.md#methodology)
(`τ ∈ [2, 48]h`, `P_cool_stage2 ∈ [1.5, 8.0]°F/hr`, skill score above
cadence-appropriate threshold). The scheduler falls back to fixed-rule
constants whenever a ratified fit is unavailable.

## Open questions surfaced during methodology design

These came out of the 2026-05-21 brainstorm and need separate
follow-up, but don't block the methodology itself:

1. **Skill threshold vs cadence.** Design doc specs `S ≥ 0.5` at 5-min
   cadence. Actual implementation runs at 10-min (Honeywell TCC
   600s rate floor). Design doc's own fallback paragraph says
   `S ≥ 0.35` at 15-min. The 10-min cadence sits between; the threshold
   should likely be ~0.4. Tracked: needs explicit spec update before
   OSF freeze.
2. **Setpoint mask vs Arm B operation.** The 30-minute mask after each
   setpoint change drops valid intervals. Arm B (RTP-aware) will
   change setpoints frequently during operation. Most Arm B fit data
   could be masked. The mask was sized for a stable house, not a
   dynamically-priced one. Tracked: needs analysis once Arm B starts.
3. **Fan-only sample preservation.** Design doc says fan-only periods
   (`hvac_state == "fan"`) are the cleanest envelope-relaxation
   samples and should be kept. Implementation's stage-transition
   filter may drop them inadvertently when fan turns on/off. Tracked:
   needs implementation audit.
4. **The thermostat reports upstairs.** Confirmed 2026-05-21 via
   two-Inkbird comparison: the thermostat's `indoor_temp_f` matches
   the upstairs RedLINK sensor to within 0.1°F, not its own internal
   sensor. So the SCED "indoor temperature" is upstairs bedroom air,
   not house-average. Not wrong — but should be stated explicitly in
   the binding spec.

## References

- Bacher, P. and Madsen, H. (2011). *Identifying suitable models for
  the heat dynamics of buildings*. Energy and Buildings 43(7),
  1511-1522. [doi:10.1016/j.enbuild.2011.02.005](https://doi.org/10.1016/j.enbuild.2011.02.005)
- Pakanen, J. et al. *Estimating time constants for over 10,000
  residential buildings*. IBPC Syracuse 2018.
  https://surface.syr.edu/cgi/viewcontent.cgi?article=1283&context=ibpc
- Johnston, D. *Whole House Heat Loss Test Method (Coheating)*. Leeds
  Beckett University, June 2013.
  https://www.leedsbeckett.ac.uk/-/media/files/research/leeds-sustainability-institute/coheating-method-for-whole-house-heat-loss/lsi_cebe_coheating_test_method_june2013.pdf
- CIBSE Journal Module 120. *Assessing co-heating tests* (December
  2017). https://www.cibsejournal.com/cpd/modules/2017-12-mon/
- *First evidence for the reliability of building co-heating tests*.
  Building Research & Information, 2017. doi:10.1080/09613218.2017.1299523
- *Characterisation of thermal energy dynamics of residential buildings
  with scarce data*. Energy and Buildings, 2020.
  https://www.sciencedirect.com/science/article/pii/S0378778820317503
- *Thermal time constant estimation of unoccupied school buildings
  from field measurements over summer*. Journal of Building Engineering,
  2025. https://www.sciencedirect.com/science/article/abs/pii/S2352710225005480
- ENERGY STAR Certified Homes HVAC Commissioning Checklist (PDF
  reference cited in project checklist).

## See also

- [THERMAL_COMMISSIONING_CHECKLIST.md](THERMAL_COMMISSIONING_CHECKLIST.md)
  — operator playlist for actually running these tests
- [archive/THERMAL_MODEL_DESIGN.md](archive/THERMAL_MODEL_DESIGN.md)
  — the original observer design (Bacher-Madsen grounding)
- [HVAC_LOGIC.md](HVAC_LOGIC.md) — where τ and UA are consumed by the
  scheduler
