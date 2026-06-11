import type { DayAhead, DayAtAGlance, TodayActions } from '../types'

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

/** Fetch today's planned ScheduleActions + per-item status. Powers
 * the ActionLog panel. */
export async function fetchTodayActions(
  signal?: AbortSignal,
): Promise<TodayActions> {
  const res = await fetch('/api/today_actions', { signal })
  if (!res.ok) {
    throw new Error(
      `today_actions fetch failed: ${res.status} ${res.statusText}`,
    )
  }
  return (await res.json()) as TodayActions
}

/** Fetch tomorrow's day-type decision + §7 pre-cool window. Powers
 * the Day-Ahead panel. */
export async function fetchDayAhead(
  signal?: AbortSignal,
): Promise<DayAhead> {
  const res = await fetch('/api/day_ahead', { signal })
  if (!res.ok) {
    throw new Error(
      `day_ahead fetch failed: ${res.status} ${res.statusText}`,
    )
  }
  return (await res.json()) as DayAhead
}
