import type { NodeProps, Node } from '@xyflow/react'
import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, DayTypeDetails } from '../../types'

type DayTypeNodeType = Node<BaseNodeEnvelope<DayTypeDetails>>

export function DayTypeNode({ data, id }: NodeProps<DayTypeNodeType>) {
  const fired = data.details.evaluation_tape.find((e) => e.fired)
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.details.winning_day_type}
      testId="node-day-type"
    >
      <div className="font-mono text-[10px]">
        {fired?.code ?? data.details.reason_code}
      </div>
      <div className="text-zinc-500">
        {data.details.evaluation_tape.length} rules evaluated
      </div>
    </BaseNode>
  )
}
