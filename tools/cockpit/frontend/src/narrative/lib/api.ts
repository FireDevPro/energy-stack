import type { DayAtAGlance } from '../types'

/** Fetch the narrative cockpit's day-at-a-glance payload from the
 * FastAPI backend. Throws on non-2xx; caller's polling hook handles
 * errors and decides on fallback. */
export async function fetchDayAtAGlance(
  signal?: AbortSignal,
): Promise<DayAtAGlance> {
  const res = await fetch('/api/day_at_a_glance', { signal })
  if (!res.ok) {
    throw new Error(
      `day_at_a_glance fetch failed: ${res.status} ${res.statusText}`,
    )
  }
  return (await res.json()) as DayAtAGlance
}
