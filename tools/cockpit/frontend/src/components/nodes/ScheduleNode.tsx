import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, ScheduleDetails } from '../../types'

export function ScheduleNode({ data }: { data: BaseNodeEnvelope<ScheduleDetails> }) {
  const d = data.details
  const setpoint =
    d.base_schedule_cool_f !== d.effective_schedule_cool_f
      ? `${d.effective_schedule_cool_f}°F`
      : `${d.base_schedule_cool_f}°F`
  return (
    <BaseNode
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title="Schedule"
      headline={setpoint}
      testId="node-schedule"
    >
      <div className="font-mono text-[10px] text-zinc-500">
        {d.action_label}
      </div>
      {d.base_schedule_cool_f !== d.effective_schedule_cool_f && (
        <div className="text-zinc-500">
          base {d.base_schedule_cool_f}°F → {d.effective_schedule_cool_f}°F
        </div>
      )}
      {d.humid_override_active && (
        <div className="text-amber-300">humid override</div>
      )}
      {d.precool_window && (
        <div className="text-cyan-300">
          precool {d.precool_window.hour_ct}:00 / {d.precool_window.depth_f}°F
        </div>
      )}
    </BaseNode>
  )
}
