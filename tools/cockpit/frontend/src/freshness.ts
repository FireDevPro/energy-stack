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
  // 7-min fresh = controller's downgrade-recency cutoff. Cockpit mirrors
  // the scheduler so operator sees exactly the same actionability the
  // controller does. Bucket-age sawtooth typically spans 6-11 min between
  // publishes, so the freshness indicator naturally cycles green→warn→green
  // every 5-min publish cycle. Warn does NOT indicate a feed problem —
  // it indicates the controller would refuse a downgrade decision if
  // asked this tick. See spec §3.1.
  // Hand-paired with deploy/energy-stack/hvac_scheduler/freshness.py.
  'comed.prices': {
    fresh_max_ms: min(7),
    warn_max_ms: min(16),
    stale_max_ms: min(30),
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
  // Refoss EM16P + EAGLE-3 both poll at 30s cadence. Without these
  // entries, classifyFreshness returns 'fresh' for any age, masking real
  // outages once Phase 3 wires live data.
  'refoss.channel': {
    fresh_max_ms: min(1),
    warn_max_ms: min(3),
    stale_max_ms: min(10),
  },
  'eagle.meter': {
    fresh_max_ms: min(1),
    warn_max_ms: min(3),
    stale_max_ms: min(10),
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
