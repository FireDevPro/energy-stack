import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, DayTypeDetails } from '../../types'

export function DayTypeNode({ data }: { data: BaseNodeEnvelope<DayTypeDetails> }) {
  const d = data.details
  return (
    <BaseNode
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title="Day Type"
      headline={d.winning_day_type}
      testId="node-day-type"
    >
      <div className="font-mono text-[10px] text-zinc-500">{d.reason_code}</div>
      <div className="text-[10px] text-zinc-600">
        {d.evaluation_tape.length} rules evaluated
      </div>
    </BaseNode>
  )
}
