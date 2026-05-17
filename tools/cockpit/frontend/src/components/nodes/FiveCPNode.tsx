import { BaseNode, Stat } from './BaseNode'
import type { BaseNodeEnvelope, FiveCPDetails } from '../../types'

export function FiveCPNode({
  data,
  pos,
  nodeW,
  nodeH,
}: {
  data: BaseNodeEnvelope<FiveCPDetails>
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
}) {
  const d = data.details
  return (
    <BaseNode
      id="fivecp"
      testId="node-fivecp"
      pos={pos}
      nodeW={nodeW}
      nodeH={nodeH}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
    >
      {!d.in_season ? (
        <div className="node-stats">
          <Stat k="season" v="out" />
        </div>
      ) : !d.fivecp_active ? (
        <div className="node-stats">
          <Stat k="state" v="idle" />
        </div>
      ) : (
        <div className="node-stats">
          <Stat
            k="scope"
            v={d.fivecp_scopes_fired.join(',')}
            tone="warn"
          />
          <Stat k="cap" v={`${d.fivecp_cool_f}°`} tone="warn" />
        </div>
      )}
    </BaseNode>
  )
}
