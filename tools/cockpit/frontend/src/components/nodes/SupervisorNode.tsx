import type { NodeProps, Node } from '@xyflow/react'
import { BaseNode } from './BaseNode'
import type {
  BaseNodeEnvelope,
  SupervisorDetails,
  RoleState,
} from '../../types'

type SupervisorNodeType = Node<BaseNodeEnvelope<SupervisorDetails>>

function supervisorRoleOverride(
  d: SupervisorDetails,
  fallback: RoleState,
): RoleState {
  if (d.decision === 'emergency') return 'emergency'
  if (d.decision === 'clamped') return 'clamped'
  return fallback
}

export function SupervisorNode({ data, id }: NodeProps<SupervisorNodeType>) {
  const d = data.details
  const role = supervisorRoleOverride(d, data.role_state)
  return (
    <BaseNode
      nodeId={id}
      role_state={role}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={d.decision ?? 'not invoked'}
      testId="node-supervisor"
    >
      {d.decision === null ? (
        <div className="text-zinc-500">no setpoint proposed this tick</div>
      ) : (
        <>
          <div>
            cool: {d.proposed_cool_f}
            {d.proposed_cool_f !== d.final_cool_f && (
              <>
                {' '}
                → <span className="text-rose-300">{d.final_cool_f}°F</span>
              </>
            )}
          </div>
          <div>heat: {d.proposed_heat_f}</div>
          <div className="font-mono text-[10px] text-zinc-500">
            {d.reason_code}
          </div>
          {d.indoor_temp_available === false && (
            <div className="text-amber-300">indoor temp unavailable</div>
          )}
        </>
      )}
    </BaseNode>
  )
}
