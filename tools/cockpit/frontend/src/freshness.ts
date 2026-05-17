// Staleness thresholds per source cadence. Used by future Phase 3 backend
// AND by any frontend code that derives `freshness` from a raw timestamp.
//
// IMPORTANT: hvac.actions is event-driven and NOT a staleness signal.
// Action node renders NOT-FIRED-THIS-TICK / last-fire / APPLIED / SHADOW
// semantics. Liveness comes from hvac.arm_mode + hvac.heartbeat + 5-min
// trace cadence.

import type { Freshness } from './types'

export interface FreshnessThresholds {
  fresh_max_ms: number
  warn_max_ms: number
  stale_max_ms: number
}

const min = (n: number) => n * 60 * 1000
const hr = (n: number) => n * 60 * 60 * 1000

export const FRESHNESS_THRESHOLDS: Record<string, FreshnessThresholds> = {
  'decision_trace.price_overlay_eval': {
    fresh_max_ms: min(6),
    warn_max_ms: min(10),
    stale_max_ms: min(15),
  },
  'decision_trace.layer_resolution': {
    fresh_max_ms: min(6),
    warn_max_ms: min(10),
    stale_max_ms: min(15),
  },
  'decision_trace.day_type_decision': {
    fresh_max_ms: hr(16),
    warn_max_ms: hr(30),
    stale_max_ms: hr(72),
  },
  'decision_trace.precool_decision': {
    fresh_max_ms: hr(26),
    warn_max_ms: hr(40),
    stale_max_ms: hr(72),
  },
  'hvac.arm_mode': {
    fresh_max_ms: min(6),
    warn_max_ms: min(10),
    stale_max_ms: min(15),
  },
  'hvac.thermostat': {
    fresh_max_ms: min(12),
    warn_max_ms: min(20),
    stale_max_ms: min(30),
  },
  'comed.prices': {
    fresh_max_ms: min(6),
    warn_max_ms: min(10),
    stale_max_ms: min(15),
  },
  'nws.forecast': {
    fresh_max_ms: min(35),
    warn_max_ms: min(90),
    stale_max_ms: hr(12),
  },
  'pjm.load_forecast': {
    fresh_max_ms: hr(14),
    warn_max_ms: hr(28),
    stale_max_ms: hr(50),
  },
  'pjm.rt_hrl_lmps': {
    fresh_max_ms: min(75),
    warn_max_ms: hr(3),
    stale_max_ms: hr(12),
  },
}

export function classifyFreshness(source: string, ageMs: number): Freshness {
  const t = FRESHNESS_THRESHOLDS[source]
  if (!t) return 'fresh'
  if (ageMs <= t.fresh_max_ms) return 'fresh'
  if (ageMs <= t.warn_max_ms) return 'warn'
  if (ageMs <= t.stale_max_ms) return 'stale'
  return 'missing'
}
