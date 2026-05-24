import type { TodayActions } from '../types'

// Mirrors the backend's build_today_actions_canned() output, anchored
// to the same mid-summer afternoon as summer_normal.
export const summerNormalTodayActions: TodayActions = {
  now: '2026-07-14T13:00:30-05:00',
  day_type: 'NORMAL',
  actions: [
    { hour: 6, minute: 0, label: 'PRE_COOL', cool_setpoint_f: 70, status: 'past' },
    { hour: 13, minute: 0, label: 'COAST', cool_setpoint_f: 79, status: 'current' },
    { hour: 19, minute: 0, label: 'RECOVER', cool_setpoint_f: 75, status: 'future' },
    { hour: 21, minute: 0, label: 'SLEEP', cool_setpoint_f: 73, status: 'future' },
  ],
}
