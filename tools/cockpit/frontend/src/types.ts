// Locked snapshot contract — see docs/plans/archive/cockpit-plan.md

export type Freshness = 'fresh' | 'warn' | 'stale' | 'missing'

export type RoleState =
  | 'winning'
  | 'dimmed'
  | 'stale'
  | 'missing'
  | 'not_applicable'
  | 'clamped'
  | 'emergency'
  | 'context'

export type SchedulerMode = 'shadow' | 'experiment' | 'production'

export type ArmModeActual =
  | 'A-active'
  | 'B-active'
  | 'B-fallback'
  | 'B-down'
  | 'off-protocol-shadow'
  | 'off-protocol-production'
  | 'outside-window'

export type PriceTier = 'normal' | 'elevated' | 'scarcity'

export interface Thermostat {
  indoor_temp_f: number
  indoor_humidity_pct: number
  cool_setpoint_f: number
  heat_setpoint_f: number
  hvac_mode: 'cool' | 'heat' | 'auto' | 'off'
  fan_mode: 'auto' | 'on' | 'circulate'
  source_ts: string
  freshness: Freshness
  freshness_label: string
}

export interface Price {
  current_cents_per_kwh: number
  tier: PriceTier
  source_ts: string
  freshness: Freshness
  freshness_label: string
}

export interface ArmMode {
  mode_actual: ArmModeActual
  arm: 'A' | 'B' | null
  source_ts: string
  freshness: Freshness
  freshness_label: string
}

export interface Controller {
  alive: boolean
  last_heartbeat_ts: string | null
  freshness: Freshness
}

export interface FeedHealthEntry {
  name: string
  status: Freshness
  label: string
}

export interface NodeSource {
  event: string
  tick_id: string | null
  ts: string
}

// Index signature is required so this type satisfies React Flow v12's
// `Node<TData extends Record<string, unknown>>` constraint without forcing
// every node component to do `& Record<string, unknown>` at use site.
// Concrete fields below are the real shape; `[key: string]: unknown` is a
// passthrough for compatibility.
export interface BaseNodeEnvelope<TDetails> {
  role_state: RoleState
  freshness: Freshness
  freshness_label: string
  title: string
  subtitle: string
  details: TDetails
  source: NodeSource | null
  [key: string]: unknown
}

// Per-node details payloads.

export interface WeatherDetails {
  current_outdoor_f: number
  today_high_f: number
  apparent_max_f: number
  dewpoint_max_f: number
  heat_advisory: boolean
}

export interface DayTypeTapeEntry {
  code: string
  fired: boolean
  actual: number | boolean | null
  threshold: number | boolean | null
}

export interface DayTypeDetails {
  winning_day_type: 'MILD' | 'NORMAL' | 'HOT_5CP_RISK' | 'HOT_STREAK_DAY1'
  decision_for_date: string
  reason_code: string
  evaluation_tape: DayTypeTapeEntry[]
}

export interface PrecoolWindow {
  hour_ct: number
  depth_f: number
}

export interface ScheduleDetails {
  action_label: string
  base_schedule_cool_f: number
  effective_schedule_cool_f: number
  humid_override_active: boolean
  humid_override_setpoint_f: number | null
  precool_window: PrecoolWindow | null
}

export interface PriceOverlayDetails {
  price_cents: number | null
  prev_tier: PriceTier
  new_tier: PriceTier
  outcome: 'held' | 'upgraded' | 'downgraded' | 'released'
  reason_code: string
  hold_minutes_remaining: number | null
}

export interface FiveCPDetails {
  fivecp_active: boolean
  fivecp_scopes_fired: Array<'COMED' | 'RTO'>
  fivecp_cool_f: number | null
  in_season: boolean
}

export interface WinnerDetails {
  // Post binding spec §11 #14, 5CP no longer wins layer resolution
  // and the scheduler does not emit "tie" anymore. Cockpit narrows
  // to the only two live values; see tools/cockpit/backend/snapshot.py
  // _build_winner_node for the defensive narrowing path that keeps
  // older stale traces from breaking the UI.
  winning_layer: 'schedule' | 'price_overlay'
  effective_cool_f: number
  prev_effective_cool_f: number
  changed: boolean
  reason_code: string
}

export interface SupervisorDetails {
  decision: 'approved' | 'clamped' | 'emergency' | null
  proposed_cool_f: number | null
  proposed_heat_f: number | null
  final_cool_f: number | null
  final_heat_f: number | null
  supervisor_reason: string | null
  reason_code: string | null
  indoor_temp_available: boolean | null
}

export interface LastActionInfo {
  action_label: string
  fire_ts: string
  applied: boolean
  dry_run: boolean
  cool_setpoint_f: number
}

export interface ActionDetails {
  applied: boolean | null
  dry_run: boolean | null
  action_label: string | null
  cool_setpoint_f: number | null
  heat_setpoint_f: number | null
  fan_mode: string | null
  setpoint_reason: string | null
  fire_ts: string | null
  error: string | null
  last_fire?: LastActionInfo
}

export interface Flow {
  weather: BaseNodeEnvelope<WeatherDetails>
  day_type: BaseNodeEnvelope<DayTypeDetails>
  schedule: BaseNodeEnvelope<ScheduleDetails>
  price_overlay: BaseNodeEnvelope<PriceOverlayDetails>
  fivecp: BaseNodeEnvelope<FiveCPDetails>
  winner: BaseNodeEnvelope<WinnerDetails>
  supervisor: BaseNodeEnvelope<SupervisorDetails>
  action: BaseNodeEnvelope<ActionDetails>
}

export interface Snapshot {
  snapshot_ts: string
  latest_tick_id: string
  latest_tick_time: string
  scheduler_mode: SchedulerMode
  thermostat: Thermostat
  price: Price
  arm_mode: ArmMode
  controller: Controller
  feed_health: FeedHealthEntry[]
  flow: Flow
}
