import { summerNormal } from '../fixtures/summer_normal'
import { shadowCurrent } from '../fixtures/shadow_current'
import type { Snapshot } from '../types'

// No `as Snapshot` casts. The TS fixtures already declare `: Snapshot` at
// their definition; `satisfies Record<string, Snapshot>` reaffirms shape
// without widening the inferred type (so `FIXTURES.summer_normal` keeps
// its literal-field types).
const FIXTURES = {
  normal: summerNormal,
  summer_normal: summerNormal,
  shadow: shadowCurrent,
  shadow_current: shadowCurrent,
} satisfies Record<string, Snapshot>

export function loadFixtureFromUrl(): Snapshot {
  const params = new URLSearchParams(window.location.search)
  const name = params.get('fixture') ?? 'summer_normal'
  return (
    (FIXTURES as Record<string, Snapshot>)[name] ?? FIXTURES.summer_normal
  )
}
