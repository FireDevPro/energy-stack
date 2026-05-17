import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, FiveCPDetails } from '../../types'

export function FiveCPNode({ data }: { data: BaseNodeEnvelope<FiveCPDetails> }) {
  const d = data.details
  const headline = d.fivecp_active
    ? `${d.fivecp_cool_f ?? 85}°F shutoff`
    : d.in_season
      ? 'no risk'
      : 'off season'
  return (
    <BaseNode
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title="Capacity"
      headline={headline}
      testId="node-fivecp"
    >
      {d.fivecp_scopes_fired.length > 0 && (
        <div>
          scopes:{' '}
          <span className="font-mono text-rose-300">
            {d.fivecp_scopes_fired.join(', ')}
          </span>
        </div>
      )}
      <div className="text-[10px] text-zinc-500">
        {d.in_season ? 'in season' : 'out of season'}
      </div>
    </BaseNode>
  )
}
