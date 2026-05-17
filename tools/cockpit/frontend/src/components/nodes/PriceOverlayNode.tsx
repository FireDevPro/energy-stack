import { BaseNode, Stat } from './BaseNode'
import type { BaseNodeEnvelope, PriceOverlayDetails } from '../../types'

export function PriceOverlayNode({
  data,
  pos,
  nodeW,
  nodeH,
}: {
  data: BaseNodeEnvelope<PriceOverlayDetails>
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
}) {
  const d = data.details
  return (
    <BaseNode
      id="price_overlay"
      testId="node-price-overlay"
      pos={pos}
      nodeW={nodeW}
      nodeH={nodeH}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
    >
      {d.price_cents == null ? (
        <div className="node-stats">
          <Stat k="status" v="—" />
        </div>
      ) : (
        <div className="node-stats">
          <Stat
            k="¢"
            v={d.price_cents.toFixed(1)}
            tone={
              d.new_tier === 'scarcity'
                ? 'danger'
                : d.new_tier === 'elevated'
                  ? 'warn'
                  : ''
            }
          />
          <Stat k="tier" v={d.new_tier} />
          {d.hold_minutes_remaining && d.hold_minutes_remaining > 0 && (
            <Stat k="hold" v={`${d.hold_minutes_remaining}m`} />
          )}
        </div>
      )}
    </BaseNode>
  )
}
