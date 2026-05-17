import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, ActionDetails } from '../../types'

function actionBadge(d: ActionDetails): { label: string; tone: string } {
  if (d.applied === true && d.dry_run === false) {
    return {
      label: 'APPLIED',
      tone: 'bg-radium-500/20 text-radium-500 ring-1 ring-radium-500/40',
    }
  }
  if (d.dry_run === true) {
    return {
      label: 'SHADOW',
      tone: 'bg-sky-500/20 text-sky-200 ring-1 ring-sky-500/40',
    }
  }
  if (d.error) {
    return {
      label: 'ERROR',
      tone: 'bg-rose-500/25 text-rose-100 ring-1 ring-rose-500/50',
    }
  }
  return {
    label: 'IDLE',
    tone: 'bg-zinc-800 text-zinc-400 ring-1 ring-zinc-700',
  }
}

export function ActionNode({ data }: { data: BaseNodeEnvelope<ActionDetails> }) {
  const d = data.details
  const badge = actionBadge(d)
  const headline =
    d.cool_setpoint_f !== null ? `${d.cool_setpoint_f}°F` : '—'
  return (
    <BaseNode
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title="Action"
      headline={headline}
      testId="node-action"
    >
      <div className="flex items-center gap-2">
        <span
          data-testid="action-badge"
          className={`rounded px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-wider ${badge.tone}`}
        >
          {badge.label}
        </span>
        {d.action_label && (
          <span className="font-mono text-[10px] text-zinc-500">
            {d.action_label}
          </span>
        )}
      </div>
      {d.fire_ts && (
        <div className="font-mono text-[10px] text-zinc-600">
          fired {new Date(d.fire_ts).toLocaleTimeString()}
        </div>
      )}
      {d.error && <div className="text-rose-300">{d.error}</div>}
    </BaseNode>
  )
}
