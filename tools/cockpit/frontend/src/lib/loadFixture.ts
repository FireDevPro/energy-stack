import { summerNormal } from '../fixtures/summer_normal'
import { shadowCurrent } from '../fixtures/shadow_current'
import { priceSpike } from '../fixtures/price_spike'
import { fivecpRisk } from '../fixtures/fivecp_risk'
import { supervisorClamp } from '../fixtures/supervisor_clamp'
import { controllerDown } from '../fixtures/controller_down'
import { feedOutage } from '../fixtures/feed_outage'
import { mildDay } from '../fixtures/mild_day'
import { armSwitch } from '../fixtures/arm_switch'
import type { Snapshot } from '../types'

// No `as Snapshot` casts. The TS fixtures already declare `: Snapshot` at
// their definition; `satisfies Record<string, Snapshot>` reaffirms shape
// without widening the inferred type.
const FIXTURES = {
  normal: summerNormal,
  summer_normal: summerNormal,
  shadow: shadowCurrent,
  shadow_current: shadowCurrent,
  price_spike: priceSpike,
  fivecp_risk: fivecpRisk,
  supervisor_clamp: supervisorClamp,
  controller_down: controllerDown,
  feed_outage: feedOutage,
  mild_day: mildDay,
  arm_switch: armSwitch,
} satisfies Record<string, Snapshot>

export const FIXTURE_NAMES: readonly string[] = Object.keys(FIXTURES)

export function loadFixtureFromUrl(): Snapshot {
  const params = new URLSearchParams(window.location.search)
  const name = params.get('fixture') ?? 'summer_normal'
  return (
    (FIXTURES as Record<string, Snapshot>)[name] ?? FIXTURES.summer_normal
  )
}
