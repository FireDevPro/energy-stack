import { BaseNode, Stat } from './BaseNode'
import type {
  BaseNodeEnvelope,
  SupervisorDetails,
  RoleState,
} from '../../types'

function supervisorRole(d: SupervisorDetails, fallback: RoleState): RoleState {
  if (d.decision === 'emergency') return 'emergency'
  if (d.decision === 'clamped') return 'clamped'
  return fallback
}

export function SupervisorNode({
  data,
  pos,
  nodeW,
  nodeH,
}: {
  data: BaseNodeEnvelope<SupervisorDetails>
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
}) {
  const d = data.details
  const role = supervisorRole(d, data.role_state)
  return (
    <BaseNode
      id="supervisor"
      testId="node-supervisor"
      pos={pos}
      nodeW={nodeW}
      nodeH={nodeH}
      role_state={role}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
    >
      {!d.decision ? (
        <div className="node-stats">
          <Stat k="state" v="bypass" />
        </div>
      ) : (
        <div className="node-stats">
          <Stat
            k="result"
            v={d.decision}
            tone={
              d.decision === 'clamped'
                ? 'warn'
                : d.decision === 'emergency'
                  ? 'danger'
                  : 'live'
            }
          />
          {d.proposed_cool_f != null &&
            d.proposed_cool_f !== d.final_cool_f && (
              <Stat
                k="cool"
                v={`${d.proposed_cool_f}→${d.final_cool_f}°`}
                tone="warn"
              />
            )}
          {d.proposed_cool_f != null &&
            d.proposed_cool_f === d.final_cool_f && (
              <Stat k="cool" v={`${d.final_cool_f}°`} tone="cool" />
            )}
        </div>
      )}
    </BaseNode>
  )
}
