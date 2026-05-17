import type { NodeProps, Node } from '@xyflow/react'
import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, ActionDetails } from '../../types'

type ActionNodeType = Node<BaseNodeEnvelope<ActionDetails>>

function actionBadge(d: ActionDetails): { label: string; tone: string } {
  if (d.applied === true && d.dry_run === false) {
    return { label: 'APPLIED', tone: 'bg-emerald-500/30 text-emerald-100' }
  }
  if (d.dry_run === true) {
    return { label: 'SHADOW', tone: 'bg-sky-500/30 text-sky-100' }
  }
  if (d.error) {
    return { label: 'ERROR', tone: 'bg-rose-500/30 text-rose-100' }
  }
  return {
    label: 'NOT-FIRED-THIS-TICK',
    tone: 'bg-zinc-700 text-zinc-300',
  }
}

export function ActionNode({ data, id }: NodeProps<ActionNodeType>) {
  const d = data.details
  const badge = actionBadge(d)
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={d.action_label ?? '—'}
      testId="node-action"
    >
      <div className="flex items-center gap-2">
        <span
          data-testid="action-badge"
          className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${badge.tone}`}
        >
          {badge.label}
        </span>
      </div>
      {d.cool_setpoint_f !== null && <div>cool: {d.cool_setpoint_f}°F</div>}
      {d.fire_ts && (
        <div className="font-mono text-[10px] text-zinc-500">
          {new Date(d.fire_ts).toLocaleTimeString()}
        </div>
      )}
      {d.error && <div className="text-rose-300">{d.error}</div>}
    </BaseNode>
  )
}
