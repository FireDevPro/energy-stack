// Narrative cockpit (v2) payload shapes — not part of the locked
// Snapshot contract in ../types.ts. Mirrors
// tools/cockpit/backend/day_at_a_glance.py output.

export interface IndoorPoint {
  ts: string
  temp_f: number
}

export interface SetpointPoint {
  ts: string
  cool_f: number
}

export interface PlannedSetpoint {
  hour: number
  minute: number
  label: string
  cool_setpoint_f: number | null
}

export interface HourlyBar {
  hour: number
  forecast_cents: number | null
  realized_cents: number | null
}

export interface DayAtAGlance {
  now: string
  day_type: string
  indoor_history: IndoorPoint[]
  setpoint_history: SetpointPoint[]
  setpoint_planned: PlannedSetpoint[]
  bars: HourlyBar[]
}

export type ActionStatus = 'past' | 'current' | 'future'

export interface TodayActionItem {
  hour: number
  minute: number
  label: string
  cool_setpoint_f: number | null
  status: ActionStatus
}

export interface TodayActions {
  now: string
  day_type: string
  actions: TodayActionItem[]
}

export interface DayTypeCard {
  decided: boolean
  day_type: string | null
  high_f: number | null
  max_dewpoint_f: number | null
  is_heat_advisory: boolean
  alert_summary: string
  reason: string
  decided_at: string | null
}

export interface PrecoolCard {
  selected: boolean
  hour_ct: number | null
  depth_f: number | null
  decided_at: string | null
}

export interface DayAhead {
  now: string
  target_date: string
  day_type: DayTypeCard
  precool: PrecoolCard
}
