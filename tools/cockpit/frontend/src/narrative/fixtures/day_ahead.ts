import type { DayAhead } from '../types'

// Mirrors backend build_day_ahead_canned() — tomorrow is HOT_5CP_RISK
// with a qualifying §7 pre-cool window at 3am CT, 4°F below baseline.
export const summerNormalDayAhead: DayAhead = {
  now: '2026-07-14T13:00:30-05:00',
  target_date: '2026-07-15',
  day_type: {
    decided: true,
    day_type: 'HOT_5CP_RISK',
    high_f: 89.0,
    max_dewpoint_f: 71.0,
    is_heat_advisory: false,
    alert_summary: '',
    reason: 'HOT_HIGH_GE_85',
    decided_at: '2026-07-13T21:00:30-05:00',
  },
  precool: {
    selected: true,
    hour_ct: 3,
    depth_f: 4,
    decided_at: '2026-07-13T21:00:30-05:00',
  },
}
