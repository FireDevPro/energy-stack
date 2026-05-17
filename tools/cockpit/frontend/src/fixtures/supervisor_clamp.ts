import type { Snapshot } from '../types'

// Hypothetical schedule corruption: a malformed ScheduleAction
// loaded effective_schedule_cool_f=92°F (out of range — supervisor cool
// ceiling is 86°F). 5CP is NOT the source (production 5CP layer is hard-
// capped at COOL_SHUTOFF_F=85). The corrupt schedule wins arbitration;
// safety supervisor catches the out-of-range proposal and clamps to 86.
// Action node shows the proposed → final transition. Exercises the
// rose-bordered clamp role_state visual.
export const supervisorClamp: Snapshot = {
  snapshot_ts: '2026-07-22T14:00:30-05:00',
  latest_tick_id: 'd4e5f6a7',
  latest_tick_time: '2026-07-22T14:00:00-05:00',
  scheduler_mode: 'experiment',
  thermostat: {
    indoor_temp_f: 75.4,
    indoor_humidity_pct: 49,
    cool_setpoint_f: 86,
    heat_setpoint_f: 65,
    hvac_mode: 'cool',
    fan_mode: 'auto',
    source_ts: '2026-07-22T13:54:00-05:00',
    freshness: 'fresh',
    freshness_label: '6m ago',
  },
  price: {
    current_cents_per_kwh: 9.2,
    tier: 'normal',
    source_ts: '2026-07-22T14:00:00-05:00',
    freshness: 'fresh',
    freshness_label: '30s ago',
  },
  arm_mode: {
    mode_actual: 'B-active',
    arm: 'B',
    source_ts: '2026-07-22T14:00:00-05:00',
    freshness: 'fresh',
    freshness_label: '30s ago',
  },
  controller: {
    alive: true,
    last_heartbeat_ts: null,
    freshness: 'fresh',
  },
  feed_health: [
    { name: 'ComEd', status: 'fresh', label: '30s ago' },
    { name: 'NWS', status: 'fresh', label: '1m ago' },
    { name: 'PJM forecast', status: 'fresh', label: '0m ago' },
    { name: 'PJM RT LMP', status: 'fresh', label: '1h ago' },
    { name: 'Refoss', status: 'fresh', label: '15s ago' },
    { name: 'EAGLE', status: 'fresh', label: '15s ago' },
    { name: 'Thermostat', status: 'fresh', label: '6m ago' },
  ],
  flow: {
    weather: {
      role_state: 'context',
      freshness: 'fresh',
      freshness_label: '23m ago',
      title: 'Weather',
      subtitle: 'high 87°F, dewpoint 67°F',
      details: {
        current_outdoor_f: 86.1,
        today_high_f: 87,
        apparent_max_f: 92,
        dewpoint_max_f: 67,
        heat_advisory: false,
      },
      source: {
        event: 'nws.forecast',
        tick_id: null,
        ts: '2026-07-22T14:00:00-05:00',
      },
    },
    day_type: {
      role_state: 'context',
      freshness: 'fresh',
      freshness_label: 'decided 21:00 last night',
      title: 'Day Type',
      subtitle: 'HOT_5CP_RISK',
      details: {
        winning_day_type: 'HOT_5CP_RISK',
        decision_for_date: '2026-07-22',
        reason_code: 'DAY_TYPE_HOT_HIGH_GE_85',
        evaluation_tape: [
          {
            code: 'DAY_TYPE_HOT_HEAT_ADVISORY',
            fired: false,
            actual: false,
            threshold: true,
          },
          {
            code: 'DAY_TYPE_HOT_HIGH_GE_85',
            fired: true,
            actual: 87,
            threshold: 85,
          },
        ],
      },
      source: {
        event: 'decision_trace.day_type_decision',
        tick_id: 'z7v6u5t4',
        ts: '2026-07-21T21:00:00-05:00',
      },
    },
    schedule: {
      role_state: 'winning',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: 'Schedule',
      subtitle: 'HOT afternoon: 92°F (corrupt — supervisor catches)',
      details: {
        action_label: 'afternoon_coast',
        base_schedule_cool_f: 78,
        // Simulated corruption: a malformed ScheduleAction was loaded
        // with cool_setpoint_f=92. This is the only realistic source of
        // a 92F proposal in production (5CP is capped at COOL_SHUTOFF_F=85,
        // price overlay scarcity is fixed at 85).
        effective_schedule_cool_f: 92,
        humid_override_active: false,
        humid_override_setpoint_f: null,
        precool_window: null,
      },
      source: {
        event: 'decision_trace.layer_resolution',
        tick_id: 'd4e5f6a7',
        ts: '2026-07-22T14:00:00-05:00',
      },
    },
    price_overlay: {
      role_state: 'dimmed',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: 'Price Overlay',
      subtitle: 'normal — no override',
      details: {
        price_cents: 9.2,
        prev_tier: 'normal',
        new_tier: 'normal',
        outcome: 'held',
        reason_code: 'PRICE_OVERLAY_NORMAL_BELOW_TRIGGER',
        hold_minutes_remaining: 0,
      },
      source: {
        event: 'decision_trace.price_overlay_eval',
        tick_id: 'd4e5f6a7',
        ts: '2026-07-22T14:00:00-05:00',
      },
    },
    fivecp: {
      role_state: 'dimmed',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: '5CP Risk',
      subtitle: 'no risk — within season',
      details: {
        fivecp_active: false,
        fivecp_scopes_fired: [],
        fivecp_cool_f: null,
        in_season: true,
      },
      source: {
        event: 'decision_trace.layer_resolution',
        tick_id: 'd4e5f6a7',
        ts: '2026-07-22T14:00:00-05:00',
      },
    },
    winner: {
      role_state: 'winning',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: 'Winner',
      subtitle: 'Schedule',
      details: {
        winning_layer: 'schedule',
        effective_cool_f: 92,
        prev_effective_cool_f: 76,
        changed: true,
        reason_code: 'LAYER_RESOLUTION_SCHEDULE_WINS',
      },
      source: {
        event: 'decision_trace.layer_resolution',
        tick_id: 'd4e5f6a7',
        ts: '2026-07-22T14:00:00-05:00',
      },
    },
    supervisor: {
      role_state: 'clamped',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: 'Supervisor',
      subtitle: 'clamped',
      details: {
        decision: 'clamped',
        proposed_cool_f: 92,
        proposed_heat_f: 65,
        final_cool_f: 86,
        final_heat_f: 65,
        supervisor_reason: 'cool 92F exceeds ceiling 86F; clamped to 86F',
        reason_code: 'SUPERVISOR_CLAMPED_COOL_CEILING',
        indoor_temp_available: true,
      },
      source: {
        event: 'decision_trace.supervisor',
        tick_id: 'd4e5f6a7',
        ts: '2026-07-22T14:00:00-05:00',
      },
    },
    action: {
      role_state: 'winning',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: 'Action',
      subtitle: 'afternoon_coast applied (clamped)',
      details: {
        applied: true,
        dry_run: false,
        action_label: 'afternoon_coast',
        cool_setpoint_f: 86,
        heat_setpoint_f: 65,
        fan_mode: 'auto',
        setpoint_reason: 'schedule (supervisor clamped corrupt 92 → 86)',
        fire_ts: '2026-07-22T14:00:00-05:00',
        error: null,
      },
      source: {
        event: 'hvac.actions',
        tick_id: 'd4e5f6a7',
        ts: '2026-07-22T14:00:00-05:00',
      },
    },
  },
}
