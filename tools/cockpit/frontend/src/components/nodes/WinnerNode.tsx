import { BaseNode, Stat } from './BaseNode'
import type { BaseNodeEnvelope, WinnerDetails } from '../../types'

export function WinnerNode({
  data,
  pos,
  nodeW,
  nodeH,
}: {
  data: BaseNodeEnvelope<WinnerDetails>
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
}) {
  const d = data.details
  return (
    <BaseNode
      id="winner"
      testId="node-winner"
      pos={pos}
      nodeW={nodeW}
      nodeH={nodeH}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      changed={d.changed}
    >
      <div className="node-stats">
        <Stat k="layer" v={d.winning_layer} tone="live" />
        <Stat k="cool" v={`${d.effective_cool_f}°`} tone="cool" />
        {d.changed && (
          <Stat
            k="Δ"
            v={`${d.prev_effective_cool_f}→${d.effective_cool_f}`}
            tone="live"
          />
        )}
      </div>
    </BaseNode>
  )
}
