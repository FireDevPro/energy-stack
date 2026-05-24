import { useCallback, useMemo } from 'react'
import { usePolling } from '../../lib/usePolling'
import { fetchDayAhead } from '../lib/api'
import { summerNormalDayAhead } from '../fixtures/day_ahead'
import type { DayAhead, DayTypeCard, PrecoolCard } from '../types'

const POLL_INTERVAL_MS = 300_000 // 5 minutes — decisions update at most a few times a day

function shouldUseFixture(): boolean {
  return new URLSearchParams(window.location.search).has('fixture')
}

export function DayAheadPanel() {
  const useFixture = useMemo(() => shouldUseFixture(), [])
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchDayAhead(signal),
    [],
  )
  const polling = usePolling<DayAhead>(fetcher, POLL_INTERVAL_MS, !useFixture)

  const data: DayAhead | null = useFixture
    ? summerNormalDayAhead
    : polling.data ?? null

  if (!data) {
    return (
      <section
        className="narrative-why"
        data-testid="narrative-day-ahead"
        style={{ paddingTop: 0 }}
      >
        <header className="narrative-da-header">
          <div className="narrative-da-title">Day ahead</div>
          <div className="narrative-da-sub">loading…</div>
        </header>
      </section>
    )
  }

  const targetLabel = formatTargetDate(data.target_date)

  return (
    <section
      className="narrative-why"
      data-testid="narrative-day-ahead"
      style={{ paddingTop: 0 }}
    >
      <header className="narrative-da-header">
        <div className="narrative-da-title">Day ahead · {targetLabel}</div>
        <div className="narrative-da-sub">
          decided at 21:00 the night before
        </div>
      </header>
      <div className="narrative-why-cards">
        <DayTypeCardView day={data.day_type} />
        <PrecoolCardView pre={data.precool} />
      </div>
    </section>
  )
}

function DayTypeCardView({ day }: { day: DayTypeCard }) {
  if (!day.decided) {
    return (
      <article
        className="narrative-why-card"
        data-testid="day-ahead-day-type"
        data-decided="false"
      >
        <header className="narrative-why-card-head">
          <span className="narrative-why-card-title">Day type</span>
          <span className="narrative-why-card-badge">pending</span>
        </header>
        <div className="narrative-why-row">
          <span className="k">decision</span>
          <span className="v dim">not yet decided (awaits 21:00)</span>
        </div>
      </article>
    )
  }
  return (
    <article
      className="narrative-why-card"
      data-testid="day-ahead-day-type"
      data-decided="true"
      data-day-type={day.day_type ?? ''}
    >
      <header className="narrative-why-card-head">
        <span className="narrative-why-card-title">Day type</span>
        <span className="narrative-why-card-badge winner">
          {day.day_type ?? '—'}
        </span>
      </header>
      {day.high_f !== null && (
        <div className="narrative-why-row">
          <span className="k">forecast high</span>
          <span className="v">{Math.round(day.high_f)}°F</span>
        </div>
      )}
      {day.max_dewpoint_f !== null && (
        <div className="narrative-why-row">
          <span className="k">dewpoint</span>
          <span className="v">{Math.round(day.max_dewpoint_f)}°F</span>
        </div>
      )}
      {day.is_heat_advisory && (
        <div className="narrative-why-row">
          <span className="k">heat advisory</span>
          <span className="v warn">active</span>
        </div>
      )}
      {day.reason && (
        <div className="narrative-why-row">
          <span className="k">reason</span>
          <span className="v small">{day.reason}</span>
        </div>
      )}
      {day.decided_at && (
        <div className="narrative-why-row">
          <span className="k">decided</span>
          <span className="v small dim">
            {formatDecidedAt(day.decided_at)}
          </span>
        </div>
      )}
    </article>
  )
}

function PrecoolCardView({ pre }: { pre: PrecoolCard }) {
  if (!pre.selected) {
    return (
      <article
        className="narrative-why-card"
        data-testid="day-ahead-precool"
        data-selected="false"
      >
        <header className="narrative-why-card-head">
          <span className="narrative-why-card-title">Pre-cool window</span>
          <span className="narrative-why-card-badge">none</span>
        </header>
        <div className="narrative-why-row">
          <span className="k">decision</span>
          <span className="v dim">
            no §7 window selected (no qualifying cheap+spike pattern)
          </span>
        </div>
      </article>
    )
  }
  return (
    <article
      className="narrative-why-card"
      data-testid="day-ahead-precool"
      data-selected="true"
    >
      <header className="narrative-why-card-head">
        <span className="narrative-why-card-title">Pre-cool window</span>
        <span className="narrative-why-card-badge winner">selected</span>
      </header>
      <div className="narrative-why-row">
        <span className="k">starts</span>
        <span className="v">{formatHourCt(pre.hour_ct ?? 0)} CT</span>
      </div>
      <div className="narrative-why-row">
        <span className="k">depth</span>
        <span className="v">{pre.depth_f}°F below baseline</span>
      </div>
      {pre.decided_at && (
        <div className="narrative-why-row">
          <span className="k">decided</span>
          <span className="v small dim">
            {formatDecidedAt(pre.decided_at)}
          </span>
        </div>
      )}
    </article>
  )
}

function formatTargetDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

function formatHourCt(h: number): string {
  if (h === 0) return '12a'
  if (h === 12) return '12p'
  return h < 12 ? `${h}a` : `${h - 12}p`
}

function formatDecidedAt(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('en-US', {
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
  })
}
