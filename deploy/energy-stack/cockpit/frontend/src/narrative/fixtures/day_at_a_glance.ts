import type { DayAtAGlance } from '../types'

// Fixture mirroring the backend's build_day_at_a_glance_canned() output,
// anchored to the same mid-summer afternoon as the summer_normal
// snapshot fixture. Lets ?fixture=summer_normal render the chart
// without needing a live backend.

const NOW_CT_ISO = '2026-07-14T13:00:30-05:00'
const NOW_CT = new Date(NOW_CT_ISO)

function tsMinutesAgo(minutesAgo: number): string {
  return new Date(NOW_CT.getTime() - minutesAgo * 60_000).toISOString()
}

const INDOOR_SAMPLES = [
  76.4, 76.2, 76.1, 76.0, 75.8, 75.7, 75.6, 75.6,
  75.4, 75.3, 75.2, 75.0, 74.9, 74.9, 74.8, 74.7,
  74.7, 74.8, 74.8, 74.7, 74.8, 74.7, 74.8, 74.8,
]

const FORECAST_CURVE = [
  5.2, 4.8, 4.6, 4.7, 5.1, 6.2,
  7.4, 7.8, 8.1, 8.0, 8.2, 8.3,
  8.4, 9.1, 11.2, 14.6, 18.1, 16.2,
  11.8, 8.9, 7.4, 6.5, 5.8, 5.2,
]

const REALIZED_HOURS = NOW_CT.getUTCHours() // 18 UTC = 13 CT (no DST adj — fixture)
const NOW_HOUR_CT = 13

export const summerNormalDayAtAGlance: DayAtAGlance = {
  now: NOW_CT_ISO,
  day_type: 'NORMAL',
  indoor_history: INDOOR_SAMPLES.map((temp_f, i) => ({
    ts: tsMinutesAgo(15 * (INDOOR_SAMPLES.length - 1 - i)),
    temp_f,
  })),
  setpoint_history: INDOOR_SAMPLES.map((_, i) => {
    const ts = new Date(
      NOW_CT.getTime() - 15 * 60_000 * (INDOOR_SAMPLES.length - 1 - i),
    )
    const hour = ts.getUTCHours() - 5 // approximate CT
    return {
      ts: ts.toISOString(),
      cool_f: hour < 13 ? 70 : 79,
    }
  }),
  setpoint_planned: [
    { hour: 6, minute: 0, label: 'PRE_COOL', cool_setpoint_f: 70 },
    { hour: 13, minute: 0, label: 'COAST', cool_setpoint_f: 79 },
    { hour: 19, minute: 0, label: 'RECOVER', cool_setpoint_f: 75 },
    { hour: 21, minute: 0, label: 'SLEEP', cool_setpoint_f: 73 },
  ],
  bars: FORECAST_CURVE.map((forecast_cents, hour) => ({
    hour,
    forecast_cents,
    realized_cents: hour <= NOW_HOUR_CT ? forecast_cents : null,
  })),
}

// Suppress unused-var warning for REALIZED_HOURS (kept for documentation).
void REALIZED_HOURS
