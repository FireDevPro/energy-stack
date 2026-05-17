import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, WinnerDetails } from '../../types'

const LAYER_LABEL: Record<WinnerDetails['winning_layer'], string> = {
  schedule: 'Schedule',
  price_overlay: 'Price',
  fivecp: '5CP',
  tie: 'Tie',
}

export function WinnerNode({ data }: { data: BaseNodeEnvelope<WinnerDetails> }) {
  const d = data.details
  return (
    <BaseNode
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title="Winner"
      headline={`${d.effective_cool_f}°F`}
      testId="node-winner"
      changed={d.changed}
    >
      <div>
        from{' '}
        <span className="font-semibold text-radium-500">
          {LAYER_LABEL[d.winning_layer]}
        </span>
      </div>
      {d.changed && (
        <div className="text-zinc-500">was {d.prev_effective_cool_f}°F</div>
      )}
    </BaseNode>
  )
}
