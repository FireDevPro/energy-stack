import type { NodeProps, Node } from '@xyflow/react'
import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, PriceOverlayDetails } from '../../types'

type PriceOverlayNodeType = Node<BaseNodeEnvelope<PriceOverlayDetails>>

const TIER_TEXT = {
  normal: 'text-emerald-300',
  elevated: 'text-amber-300',
  scarcity: 'text-rose-300',
} as const

export function PriceOverlayNode({
  data,
  id,
}: NodeProps<PriceOverlayNodeType>) {
  const d = data.details
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      testId="node-price-overlay"
    >
      <div>
        price:{' '}
        <span className="font-mono">
          {d.price_cents !== null ? `${d.price_cents.toFixed(1)}¢` : '—'}
        </span>
      </div>
      <div>
        tier: <span className={TIER_TEXT[d.new_tier]}>{d.new_tier}</span>
        {d.prev_tier !== d.new_tier && (
          <span className="text-zinc-500"> (was {d.prev_tier})</span>
        )}
      </div>
      <div className="font-mono text-[10px] text-zinc-500">{d.reason_code}</div>
    </BaseNode>
  )
}
