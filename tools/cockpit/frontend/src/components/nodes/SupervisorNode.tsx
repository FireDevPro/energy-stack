import { BaseNode } from './BaseNode'
import type {
  BaseNodeEnvelope,
  SupervisorDetails,
  RoleState,
} from '../../types'

function supervisorRoleOverride(
  d: SupervisorDetails,
  fallback: RoleState,
): RoleState {
  if (d.decision === 'emergency') return 'emergency'
  if (d.decision === 'clamped') return 'clamped'
  return fallback
}

export function SupervisorNode({
  data,
}: {
  data: BaseNodeEnvelope<SupervisorDetails>
}) {
  const d = data.details
  const role = supervisorRoleOverride(d, data.role_state)
  const headline =
    d.decision === null
      ? 'idle'
      : d.decision === 'clamped'
        ? `clamped → ${d.final_cool_f}°F`
        : d.decision === 'emergency'
          ? `emergency → ${d.final_cool_f}°F`
          : `approved ${d.final_cool_f}°F`
  return (
    <BaseNode
      role_state={role}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title="Supervisor"
      headline={headline}
      testId="node-supervisor"
    >
      {d.decision === null ? (
        <div className="text-zinc-600">no proposal this tick</div>
      ) : (
        <>
          {d.proposed_cool_f !== d.final_cool_f && (
            <div>
              proposed{' '}
              <span className="text-zinc-400">{d.proposed_cool_f}°F</span>
            </div>
          )}
          {d.indoor_temp_available === false && (
            <div className="text-amber-300">indoor temp unavailable</div>
          )}
          <div className="font-mono text-[10px] text-zinc-500">
            {d.reason_code}
          </div>
        </>
      )}
    </BaseNode>
  )
}
