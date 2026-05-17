import type { NodeProps, Node } from '@xyflow/react'
import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, WinnerDetails } from '../../types'

type WinnerNodeType = Node<BaseNodeEnvelope<WinnerDetails>>

const LAYER_LABEL: Record<WinnerDetails['winning_layer'], string> = {
  schedule: 'Schedule',
  price_overlay: 'Price Overlay',
  fivecp: '5CP',
  tie: 'Tie (warmer wins)',
}

export function WinnerNode({ data, id }: NodeProps<WinnerNodeType>) {
  const d = data.details
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={LAYER_LABEL[d.winning_layer]}
      testId="node-winner"
      changed={d.changed}
    >
      <div>
        cool:{' '}
        <span className="font-mono text-base text-sky-300">
          {d.effective_cool_f}°F
        </span>
      </div>
      {d.changed && (
        <div className="text-zinc-500">was {d.prev_effective_cool_f}°F</div>
      )}
      <div className="font-mono text-[10px] text-zinc-500">{d.reason_code}</div>
    </BaseNode>
  )
}
