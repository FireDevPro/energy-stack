import { useCallback, useMemo } from 'react'
import { usePolling } from '../../lib/usePolling'
import { fetchTodayActions } from '../lib/api'
import { summerNormalTodayActions } from '../fixtures/today_actions'
import type { TodayActionItem, TodayActions } from '../types'

const POLL_INTERVAL_MS = 60_000

function shouldUseFixture(): boolean {
  return new URLSearchParams(window.location.search).has('fixture')
}

export function ActionLog() {
  const useFixture = useMemo(() => shouldUseFixture(), [])
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchTodayActions(signal),
    [],
  )
  const polling = usePolling<TodayActions>(
    fetcher,
    POLL_INTERVAL_MS,
    !useFixture,
  )

  const data: TodayActions | null = useFixture
    ? summerNormalTodayActions
    : polling.data ?? null

  if (!data) {
    return (
      <section
        className="narrative-placeholder"
        data-testid="narrative-action-log"
      >
        <div className="narrative-placeholder-title">Action log</div>
        <div>loading…</div>
      </section>
    )
  }

  return (
    <section
      className="narrative-action-log"
      data-testid="narrative-action-log"
      data-day-type={data.day_type}
    >
      <header className="narrative-da-header">
        <div className="narrative-da-title">Action log</div>
        <div className="narrative-da-sub">
          {data.day_type} schedule · {data.actions.length} action
          {data.actions.length === 1 ? '' : 's'}
        </div>
      </header>
      <ol className="narrative-action-list">
        {data.actions.map((a) => (
          <ActionRow key={`${a.hour}:${a.minute}:${a.label}`} action={a} />
        ))}
      </ol>
    </section>
  )
}

function ActionRow({ action }: { action: TodayActionItem }) {
  const time = formatTime(action.hour, action.minute)
  return (
    <li
      className={`narrative-action-row status-${action.status}`}
      data-testid={`action-row-${action.label}`}
    >
      <div className="narrative-action-time">
        <span className="narrative-action-marker" aria-hidden="true" />
        {time}
      </div>
      <div className="narrative-action-body">
        <div className="narrative-action-label">{action.label}</div>
        {action.cool_setpoint_f !== null ? (
          <div className="narrative-action-setpoint">
            cool {action.cool_setpoint_f}°F
          </div>
        ) : (
          <div className="narrative-action-setpoint dim">
            release hold (no setpoint change)
          </div>
        )}
      </div>
      <StatusBadge status={action.status} />
    </li>
  )
}

function StatusBadge({ status }: { status: TodayActionItem['status'] }) {
  const label = status === 'current' ? 'now' : status
  return (
    <span className={`narrative-action-status status-${status}`}>{label}</span>
  )
}

function formatTime(hour: number, minute: number): string {
  const h12 = hour === 0 ? 12 : hour <= 12 ? hour : hour - 12
  const ampm = hour < 12 ? 'a' : 'p'
  const mm = minute.toString().padStart(2, '0')
  return `${h12}:${mm}${ampm}`
}
