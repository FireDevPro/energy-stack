import type { Snapshot } from '../types'
import { formatClock } from '../lib/tone'

// Three-column summary footer below the flow stage. Latest action,
// winner→final cool, snapshot+tick metadata. Pulls from snapshot.flow.

// Translate scheduler action_label codes to operator-friendly text. Codes
// preserved in the small footer for audit-trail lookup.
function friendlyActionLabel(code: string | null | undefined): string {
  if (!code) return '—'
  if (code.startsWith('MID_PERIOD_REPUSH:')) {
    const period = code.split(':')[1].toLowerCase()
    return `${period} schedule · mid-period re-eval`
  }
  const map: Record<string, string> = {
    SLEEP: 'sleep schedule start',
    MORNING_START: 'morning schedule start',
    AFTERNOON_START: 'afternoon schedule start',
    AFTERNOON_COAST: 'afternoon coast',
    RECOVER: 'recovery',
    HOT_PRE_COOL: 'pre-cool (hot day)',
    HOT_COAST: 'hot day coast',
    HOT_RECOVER: 'hot recovery',
    STREAK_PRE_COOL_EARLY: 'hot-streak pre-cool (early)',
    MILD_RELEASE_HOLD: 'mild day · release prior hold',
    afternoon_start: 'afternoon schedule start',
    afternoon_coast: 'afternoon coast',
    mild_coast: 'mild day coast',
    overnight_hold: 'overnight hold',
    price_spike_response: 'price spike response',
    fivecp_shutoff: '5CP capacity shutoff',
  }
  return map[code] || code.toLowerCase().replace(/_/g, ' ')
}

export function ActionStrip({ snapshot }: { snapshot: Snapshot }) {
  const a = snapshot.flow.action
  const s = snapshot.flow.supervisor.details
  const w = snapshot.flow.winner.details
  const applied = a.details.applied
  const dryRun = a.details.dry_run
  const label = a.details.action_label
  const reason = a.details.setpoint_reason

  const stateText =
    applied === true
      ? 'applied'
      : applied === false
        ? dryRun
          ? 'shadow'
          : 'skipped'
        : 'no recent action'

  const tone =
    applied === true
      ? ''
      : applied === false && !dryRun
        ? 'warn'
        : applied === null
          ? 'danger'
          : 'muted'

  const finalCool = s.final_cool_f ?? w.effective_cool_f

  return (
    <div className="action-strip">
      <div className="action-cell">
        <span className="k">latest action</span>
        <span className={`v ${tone}`}>
          {friendlyActionLabel(label)}{' '}
          <span
            className="v muted"
            style={{ fontSize: 13, fontWeight: 400 }}
          >
            · {stateText}
          </span>
        </span>
        {(label || reason) && (
          <span
            className="reason"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: 'var(--ink-5)',
              letterSpacing: '0.04em',
            }}
          >
            {label || '—'} · {reason || '—'}
          </span>
        )}
      </div>
      <div className="action-cell">
        <span className="k">winner → final cool</span>
        <span className="v">
          {w.winning_layer}{' '}
          <span style={{ color: 'var(--ink-4)' }}>·</span>{' '}
          <span style={{ color: 'var(--ice)' }}>{finalCool}°F</span>
        </span>
        {s.supervisor_reason && (
          <span className="reason" style={{ color: 'var(--warn)' }}>
            {s.supervisor_reason}
          </span>
        )}
      </div>
      <div className="action-cell">
        <span className="k">snapshot</span>
        <span
          className="v"
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 13,
            letterSpacing: '0.04em',
          }}
        >
          tick {snapshot.latest_tick_id}
        </span>
        <span
          className="reason"
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 10.5,
            letterSpacing: '0.06em',
            color: 'var(--ink-5)',
          }}
        >
          {formatClock(snapshot.snapshot_ts)} · {snapshot.scheduler_mode}
        </span>
      </div>
    </div>
  )
}
