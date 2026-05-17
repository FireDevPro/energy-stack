import { BaseNode, Stat } from './BaseNode'
import type { BaseNodeEnvelope, ActionDetails } from '../../types'

export function ActionNode({
  data,
  pos,
  nodeW,
  nodeH,
}: {
  data: BaseNodeEnvelope<ActionDetails>
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
}) {
  const d = data.details
  return (
    <BaseNode
      id="action"
      testId="node-action"
      pos={pos}
      nodeW={nodeW}
      nodeH={nodeH}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
    >
      {d.applied == null ? (
        <div className="node-stats">
          <span
            data-testid="action-badge"
            className="node-stat"
          >
            <span className="k">state</span>
            <span className="v">NO-FIRE</span>
          </span>
          {d.last_fire && (
            <Stat k="last" v={`${d.last_fire.cool_setpoint_f}°`} />
          )}
        </div>
      ) : (
        <div className="node-stats">
          <span
            data-testid="action-badge"
            className="node-stat"
          >
            <span className="k">state</span>
            <span
              className={`v ${d.applied ? 'live' : d.dry_run ? 'warn' : 'warn'}`}
            >
              {d.applied ? 'APPLIED' : d.dry_run ? 'SHADOW' : 'SKIP'}
            </span>
          </span>
          {d.cool_setpoint_f != null && (
            <Stat k="cool" v={`${d.cool_setpoint_f}°`} tone="cool" />
          )}
        </div>
      )}
    </BaseNode>
  )
}
