import type { Snapshot } from '../types'

// Monday 00:00 CT — arm boundary moment. The 8th period flips from
// A to B. arm_mode.source_ts is right at the boundary, mode_actual just
// transitioned from A-active to B-active. First few ticks of the new
// arm; the controller is establishing baseline state.
//
// DEVIATION FROM PLAN §2.7: the plan called for "a header-level subtle
// 'arm switch detected' badge when source ts is within last 5 min" AND
// a `switch_event` source on arm_mode. Both are descoped in Phase 2:
//   - The ArmMode type has no `source` field; adding one for one fixture
//     would expand the contract without a corresponding consumer.
//   - The badge component is not built. Operators detect the boundary
//     visually via the freshness_label "just switched (Xs ago)" and via
//     the arm-mode chip's color/text.
// Both are tracked for Phase 4+ as observability enhancements once the
// live data path (Phase 3) is in place and operators can validate what
// they actually want to see at boundary moments.
export const armSwitch: Snapshot = {
  snapshot_ts: '2026-09-07T00:00:30-05:00',
  latest_tick_id: 'b8c9d0e1',
  latest_tick_time: '2026-09-07T00:00:00-05:00',
  scheduler_mode: 'experiment',
  thermostat: {
    indoor_temp_f: 73.5,
    indoor_humidity_pct: 48,
    cool_setpoint_f: 76,
    heat_setpoint_f: 65,
    hvac_mode: 'cool',
    fan_mode: 'auto',
    source_ts: '2026-09-06T23:54:00-05:00',
    freshness: 'fresh',
    freshness_label: '6m ago',
  },
  price: {
    current_cents_per_kwh: 5.2,
    tier: 'normal',
    source_ts: '2026-09-07T00:00:00-05:00',
    freshness: 'fresh',
    freshness_label: '30s ago',
  },
  arm_mode: {
    mode_actual: 'B-active',
    arm: 'B',
    source_ts: '2026-09-07T00:00:00-05:00',
    freshness: 'fresh',
    freshness_label: 'just switched (30s ago)',
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
      subtitle: 'high 80°F, dewpoint 62°F',
      details: {
        current_outdoor_f: 72.8,
        today_high_f: 80,
        apparent_max_f: 82,
        dewpoint_max_f: 62,
        heat_advisory: false,
      },
      source: {
        event: 'nws.forecast',
        tick_id: null,
        ts: '2026-09-07T00:00:00-05:00',
      },
    },
    day_type: {
      role_state: 'context',
      freshness: 'fresh',
      freshness_label: 'decided 21:00 last night',
      title: 'Day Type',
      subtitle: 'NORMAL',
      details: {
        winning_day_type: 'NORMAL',
        decision_for_date: '2026-09-07',
        reason_code: 'DAY_TYPE_NORMAL_HIGH_75_TO_84',
        evaluation_tape: [
          // Production precedence: HEAT_ADV → HIGH_GE_85 → APPARENT_GE_90
          // → NORMAL_HIGH_75_TO_84. NORMAL outcome means all 3 HOT rules
          // were considered and rejected.
          {
            code: 'DAY_TYPE_HOT_HEAT_ADVISORY',
            fired: false,
            actual: false,
            threshold: true,
          },
          {
            code: 'DAY_TYPE_HOT_HIGH_GE_85',
            fired: false,
            actual: 80,
            threshold: 85,
          },
          {
            code: 'DAY_TYPE_HOT_APPARENT_GE_90',
            fired: false,
            actual: 82,
            threshold: 90,
          },
          {
            code: 'DAY_TYPE_NORMAL_HIGH_75_TO_84',
            fired: true,
            actual: 80,
            threshold: 75,
          },
        ],
      },
      source: {
        event: 'decision_trace.day_type_decision',
        tick_id: 'e6f7a8b9',
        ts: '2026-09-06T21:00:00-05:00',
      },
    },
    schedule: {
      role_state: 'winning',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: 'Schedule',
      subtitle: 'NORMAL overnight: 76°F',
      details: {
        action_label: 'overnight_hold',
        base_schedule_cool_f: 76,
        effective_schedule_cool_f: 76,
        humid_override_active: false,
        humid_override_setpoint_f: null,
        precool_window: null,
      },
      source: {
        event: 'decision_trace.layer_resolution',
        tick_id: 'b8c9d0e1',
        ts: '2026-09-07T00:00:00-05:00',
      },
    },
    price_overlay: {
      role_state: 'dimmed',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: 'RTP Spike',
      subtitle: 'normal — no override',
      details: {
        price_cents: 5.2,
        prev_tier: 'normal',
        new_tier: 'normal',
        outcome: 'held',
        reason_code: 'PRICE_OVERLAY_NORMAL_BELOW_TRIGGER',
        hold_minutes_remaining: 0,
      },
      source: {
        event: 'decision_trace.price_overlay_eval',
        tick_id: 'b8c9d0e1',
        ts: '2026-09-07T00:00:00-05:00',
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
        tick_id: 'b8c9d0e1',
        ts: '2026-09-07T00:00:00-05:00',
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
        effective_cool_f: 76,
        prev_effective_cool_f: 76,
        changed: false,
        reason_code: 'LAYER_RESOLUTION_SCHEDULE_WINS',
      },
      source: {
        event: 'decision_trace.layer_resolution',
        tick_id: 'b8c9d0e1',
        ts: '2026-09-07T00:00:00-05:00',
      },
    },
    supervisor: {
      role_state: 'not_applicable',
      freshness: 'fresh',
      freshness_label: 'not invoked this tick',
      title: 'Supervisor',
      subtitle: 'not invoked',
      details: {
        decision: null,
        proposed_cool_f: null,
        proposed_heat_f: null,
        final_cool_f: null,
        final_heat_f: null,
        supervisor_reason: null,
        reason_code: null,
        indoor_temp_available: null,
      },
      source: null,
    },
    action: {
      role_state: 'winning',
      freshness: 'fresh',
      freshness_label: '30s ago',
      title: 'Action',
      subtitle: 'overnight_hold applied (B-arm start)',
      details: {
        applied: true,
        dry_run: false,
        action_label: 'overnight_hold',
        cool_setpoint_f: 76,
        heat_setpoint_f: 65,
        fan_mode: 'auto',
        setpoint_reason: 'arm switch A→B at 00:00 CT, schedule resumed',
        fire_ts: '2026-09-07T00:00:00-05:00',
        error: null,
      },
      source: {
        event: 'hvac.actions',
        tick_id: 'b8c9d0e1',
        ts: '2026-09-07T00:00:00-05:00',
      },
    },
  },
}
