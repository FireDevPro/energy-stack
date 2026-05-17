import { BaseNode, Stat } from './BaseNode'
import type { BaseNodeEnvelope, DayTypeDetails } from '../../types'

export function DayTypeNode({
  data,
  pos,
  nodeW,
  nodeH,
}: {
  data: BaseNodeEnvelope<DayTypeDetails>
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
}) {
  const d = data.details
  return (
    <BaseNode
      id="day_type"
      testId="node-day-type"
      pos={pos}
      nodeW={nodeW}
      nodeH={nodeH}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
    >
      <div className="eval-tape">
        {d.evaluation_tape.map((c) => (
          <div
            key={c.code}
            className="eval-cell"
            data-fired={c.fired ? 'true' : 'false'}
            title={c.code}
          />
        ))}
      </div>
      <div className="node-stats">
        <Stat k="rule" v={d.winning_day_type} tone="live" />
      </div>
    </BaseNode>
  )
}
