import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, PriceOverlayDetails } from '../../types'

const TIER_TEXT = {
  normal: 'text-emerald-300',
  elevated: 'text-amber-300',
  scarcity: 'text-rose-300',
} as const

export function PriceOverlayNode({
  data,
}: {
  data: BaseNodeEnvelope<PriceOverlayDetails>
}) {
  const d = data.details
  const headline = `${(d.price_cents ?? 0).toFixed(1)}¢`
  return (
    <BaseNode
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title="Price"
      headline={headline}
      testId="node-price-overlay"
    >
      <div>
        <span className={`font-semibold ${TIER_TEXT[d.new_tier]}`}>
          {d.new_tier}
        </span>
        {d.prev_tier !== d.new_tier && (
          <span className="text-zinc-500"> · was {d.prev_tier}</span>
        )}
      </div>
      <div className="font-mono text-[10px] text-zinc-500">{d.reason_code}</div>
    </BaseNode>
  )
}
