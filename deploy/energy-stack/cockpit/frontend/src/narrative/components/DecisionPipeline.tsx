import type { Snapshot, RoleState } from '../../types'

// 7-cell live decision pipeline. 5CP intentionally omitted per binding
// spec §11 #14 (5CP no longer participates in live arbitration; its
// role is baked into the day-ahead day-type decision).
export function DecisionPipeline({ snapshot }: { snapshot: Snapshot }) {
  const cells = buildCells(snapshot)
  return (
    <div
      className="narrative-pipeline"
      data-testid="narrative-decision-pipeline"
    >
      <div className="narrative-pipeline-head">
        <span className="narrative-pipeline-title">Decision pipeline</span>
        <span className="narrative-pipeline-sub">this tick</span>
        <span className="narrative-pipeline-tick">
          tick {snapshot.latest_tick_id}
        </span>
      </div>
      <ol className="narrative-pipeline-rail">
        {cells.map((cell, i) => (
          <li
            key={cell.key}
            className={`narrative-pipeline-cell role-${cell.role}`}
            data-testid={`pipeline-cell-${cell.key}`}
            data-role={cell.role}
          >
            <div className="pipeline-cell-title">{cell.title}</div>
            <div className="pipeline-cell-value">{cell.value}</div>
            {cell.sub && (
              <div className="pipeline-cell-sub">{cell.sub}</div>
            )}
            {i < cells.length - 1 && (
              <span className="pipeline-arrow" aria-hidden="true">
                →
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

interface Cell {
  key: string
  title: string
  value: string
  sub?: string
  role: RoleState
}

function buildCells(snapshot: Snapshot): Cell[] {
  const w = snapshot.flow.weather.details
  const dt = snapshot.flow.day_type.details
  const sch = snapshot.flow.schedule.details
  const po = snapshot.flow.price_overlay.details
  const win = snapshot.flow.winner.details
  const sup = snapshot.flow.supervisor.details
  const act = snapshot.flow.action.details
  const price = snapshot.price.current_cents_per_kwh
  const supDec = sup.decision ?? 'pending'

  return [
    {
      key: 'weather',
      title: 'Weather',
      value: `${Math.round(w.current_outdoor_f)}°`,
      sub: `hi ${Math.round(w.today_high_f)}°`,
      role: 'context',
    },
    {
      key: 'day-type',
      title: 'Day Type',
      value: dt.winning_day_type,
      sub: 'context',
      role: 'context',
    },
    {
      key: 'schedule',
      title: 'Schedule',
      value: `${sch.effective_schedule_cool_f}°F`,
      sub: snapshot.flow.schedule.role_state === 'winning'
        ? 'WINNING'
        : 'baseline',
      role: snapshot.flow.schedule.role_state,
    },
    {
      key: 'rtp',
      title: 'RTP Spike',
      value: `${price.toFixed(1)}¢`,
      sub: po.outcome,
      role: snapshot.flow.price_overlay.role_state,
    },
    {
      key: 'winner',
      title: 'Winner',
      value: `${win.effective_cool_f}°F`,
      sub:
        win.winning_layer === 'price_overlay'
          ? 'price'
          : 'schedule',
      role: 'winning',
    },
    {
      key: 'supervisor',
      title: 'Supervisor',
      value: supDec.toUpperCase(),
      sub:
        sup.final_cool_f !== null && sup.final_cool_f !== undefined
          ? `${sup.final_cool_f}°F`
          : undefined,
      role: supDec === 'approved'
        ? 'winning'
        : supDec === 'clamped' || supDec === 'emergency'
          ? 'clamped'
          : 'dimmed',
    },
    {
      key: 'action',
      title: 'Action',
      value: act.applied ? '✓ applied' : (act.dry_run ? 'dry-run' : 'pending'),
      sub:
        act.cool_setpoint_f !== null && act.cool_setpoint_f !== undefined
          ? `${act.cool_setpoint_f}°F`
          : undefined,
      role: act.applied ? 'winning' : 'dimmed',
    },
  ]
}
