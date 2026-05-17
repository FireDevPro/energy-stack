import { BaseNode, Stat } from './BaseNode'
import type { BaseNodeEnvelope, ScheduleDetails } from '../../types'

export function ScheduleNode({
  data,
  pos,
  nodeW,
  nodeH,
}: {
  data: BaseNodeEnvelope<ScheduleDetails>
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
}) {
  const d = data.details
  return (
    <BaseNode
      id="schedule"
      testId="node-schedule"
      pos={pos}
      nodeW={nodeW}
      nodeH={nodeH}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
    >
      <div className="node-stats">
        {d.base_schedule_cool_f !== d.effective_schedule_cool_f ? (
          <>
            <Stat k="base" v={`${d.base_schedule_cool_f}°`} />
            <Stat
              k="effective"
              v={`${d.effective_schedule_cool_f}°`}
              tone="cool"
            />
          </>
        ) : (
          <Stat k="cool" v={`${d.effective_schedule_cool_f}°`} tone="cool" />
        )}
        {d.humid_override_active && (
          <Stat k="humid override" v="active" tone="live" />
        )}
      </div>
    </BaseNode>
  )
}
