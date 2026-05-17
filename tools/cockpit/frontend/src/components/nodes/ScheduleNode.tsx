import type { NodeProps, Node } from '@xyflow/react'
import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, ScheduleDetails } from '../../types'

type ScheduleNodeType = Node<BaseNodeEnvelope<ScheduleDetails>>

export function ScheduleNode({ data, id }: NodeProps<ScheduleNodeType>) {
  const d = data.details
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      testId="node-schedule"
    >
      <div>
        action: <span className="font-mono">{d.action_label}</span>
      </div>
      <div>
        cool: {d.base_schedule_cool_f}°F
        {d.base_schedule_cool_f !== d.effective_schedule_cool_f && (
          <>
            {' '}
            →{' '}
            <span className="text-sky-300">
              {d.effective_schedule_cool_f}°F
            </span>
          </>
        )}
      </div>
      {d.humid_override_active && (
        <div className="text-amber-300">
          humid override → {d.humid_override_setpoint_f}°F
        </div>
      )}
      {d.precool_window && (
        <div className="text-cyan-300">
          precool {d.precool_window.hour_ct}:00 → {d.precool_window.depth_f}°F
        </div>
      )}
    </BaseNode>
  )
}
