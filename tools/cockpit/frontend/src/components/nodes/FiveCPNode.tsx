import type { NodeProps, Node } from '@xyflow/react'
import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, FiveCPDetails } from '../../types'

type FiveCPNodeType = Node<BaseNodeEnvelope<FiveCPDetails>>

export function FiveCPNode({ data, id }: NodeProps<FiveCPNodeType>) {
  const d = data.details
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      testId="node-fivecp"
    >
      <div>
        active:{' '}
        <span
          className={d.fivecp_active ? 'text-rose-300' : 'text-zinc-500'}
        >
          {d.fivecp_active ? 'yes' : 'no'}
        </span>
      </div>
      <div>in season: {d.in_season ? 'yes' : 'no'}</div>
      {d.fivecp_scopes_fired.length > 0 && (
        <div>
          scopes:{' '}
          <span className="font-mono">{d.fivecp_scopes_fired.join(', ')}</span>
        </div>
      )}
      {d.fivecp_cool_f !== null && <div>shutoff: {d.fivecp_cool_f}°F</div>}
    </BaseNode>
  )
}
