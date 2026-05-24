import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { fetchDayAtAGlance } from '../lib/api'
import { summerNormalDayAtAGlance } from '../fixtures/day_at_a_glance'
import type { DayAtAGlance as DayAtAGlanceData, HourlyBar } from '../types'

const POLL_INTERVAL_MS = 60_000

const CHART_HEIGHT = 320
const TOP_PAD = 28
const BOTTOM_PAD = 36
const LEFT_PAD = 44
const RIGHT_PAD = 44

const TEMP_MIN = 60
const TEMP_MAX = 90

// ¢/kWh tier thresholds — matches deploy/energy-stack/hvac_scheduler/
// price_overlay.py PRICE_TIERS (elevated=10c trigger, scarcity=20c
// trigger). The chart's chosen visual cap is generous (24c) so a
// 20c scarcity bar still has headroom and doesn't get clipped.
const PRICE_GREEN = 10
const PRICE_RED = 20
const PRICE_AXIS_MAX = 24

function shouldUseFixture(): boolean {
  return new URLSearchParams(window.location.search).has('fixture')
}

export function DayAtAGlance() {
  const useFixture = useMemo(() => shouldUseFixture(), [])
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchDayAtAGlance(signal),
    [],
  )
  const polling = usePolling<DayAtAGlanceData>(
    fetcher,
    POLL_INTERVAL_MS,
    !useFixture,
  )

  const data: DayAtAGlanceData | null = useFixture
    ? summerNormalDayAtAGlance
    : polling.data ?? null

  const wrapRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)

  useLayoutEffect(() => {
    if (!wrapRef.current) return
    const ro = new ResizeObserver(([e]) => {
      setWidth(Math.max(360, Math.floor(e.contentRect.width)))
    })
    ro.observe(wrapRef.current)
    return () => ro.disconnect()
  }, [])

  if (!data) {
    return (
      <section
        className="narrative-placeholder"
        data-testid="narrative-day-at-a-glance"
      >
        <div className="narrative-placeholder-title">Day at a glance</div>
        <div>loading…</div>
      </section>
    )
  }

  return (
    <section
      ref={wrapRef}
      className="narrative-day-at-a-glance"
      data-testid="narrative-day-at-a-glance"
      data-day-type={data.day_type}
    >
      <header className="narrative-da-header">
        <div className="narrative-da-title">Day at a glance</div>
        <div className="narrative-da-sub">
          {data.day_type} · DA forecast (PJM zonal COMED) vs realized (ComEd
          hourly avg)
        </div>
      </header>
      <Chart data={data} width={width} />
    </section>
  )
}

function Chart({ data, width }: { data: DayAtAGlanceData; width: number }) {
  const chartW = Math.max(360, width - LEFT_PAD - RIGHT_PAD)
  const chartH = CHART_HEIGHT - TOP_PAD - BOTTOM_PAD
  const barSlot = chartW / 24
  const barWidth = Math.max(6, barSlot - 4)

  const nowCt = new Date(data.now)
  const nowHour = nowCt.getHours()
  const nowMinute = nowCt.getMinutes()
  const nowFracHour = nowHour + nowMinute / 60

  const xForHour = (h: number) => LEFT_PAD + h * barSlot + barSlot / 2
  const xForFracHour = (h: number) => LEFT_PAD + h * barSlot

  const yForTemp = (f: number) => {
    const clamped = Math.max(TEMP_MIN, Math.min(TEMP_MAX, f))
    return TOP_PAD + chartH * (1 - (clamped - TEMP_MIN) / (TEMP_MAX - TEMP_MIN))
  }
  const yForCents = (c: number) => {
    const clamped = Math.max(0, Math.min(PRICE_AXIS_MAX, c))
    return TOP_PAD + chartH * (1 - clamped / PRICE_AXIS_MAX)
  }
  const yBaseline = TOP_PAD + chartH

  const indoorPath = useSeriesPath(
    data.indoor_history.map((p) => {
      const frac = tsToFracHour(p.ts, data.now)
      return {
        x: frac !== null ? xForFracHour(frac) : null,
        y: yForTemp(p.temp_f),
      }
    }),
  )

  const setpointPastPath = useSeriesPath(
    data.setpoint_history.map((p) => {
      const frac = tsToFracHour(p.ts, data.now)
      return {
        x: frac !== null ? xForFracHour(frac) : null,
        y: yForTemp(p.cool_f),
      }
    }),
  )

  // Future planned setpoint: step path. Anchor at (nowFracHour, currentCool)
  // and step at each PlannedAction whose hour:minute > now.
  const lastSetpoint = data.setpoint_history.at(-1)?.cool_f ?? 76
  const futureSteps: { x: number; y: number }[] = []
  let currentCool = lastSetpoint
  futureSteps.push({ x: xForFracHour(nowFracHour), y: yForTemp(currentCool) })
  for (const action of data.setpoint_planned) {
    const actionFracHour = action.hour + action.minute / 60
    if (actionFracHour <= nowFracHour) continue
    if (action.cool_setpoint_f === null) continue
    futureSteps.push({
      x: xForFracHour(actionFracHour),
      y: yForTemp(currentCool),
    })
    currentCool = action.cool_setpoint_f
    futureSteps.push({
      x: xForFracHour(actionFracHour),
      y: yForTemp(currentCool),
    })
  }
  futureSteps.push({ x: xForFracHour(24), y: yForTemp(currentCool) })
  const setpointFuturePath = futureSteps
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(' ')

  const nowX = xForFracHour(nowFracHour)

  return (
    <svg
      width={width}
      height={CHART_HEIGHT}
      viewBox={`0 0 ${width} ${CHART_HEIGHT}`}
      role="img"
      aria-label="Day at a glance — indoor temperature, setpoints, and price bars"
    >
      <YAxisTemp yForTemp={yForTemp} chartW={chartW} />
      <YAxisCents yForCents={yForCents} chartW={chartW} width={width} />
      <XAxisHours xForHour={xForHour} yBaseline={yBaseline} chartW={chartW} />

      <g data-testid="da-bars">
        {data.bars.map((bar) => (
          <Bar
            key={bar.hour}
            bar={bar}
            isPast={bar.hour < Math.floor(nowFracHour)}
            isCurrent={bar.hour === Math.floor(nowFracHour)}
            x={xForHour(bar.hour) - barWidth / 2}
            yBaseline={yBaseline}
            yForCents={yForCents}
            barWidth={barWidth}
          />
        ))}
      </g>

      {setpointPastPath && (
        <path
          d={setpointPastPath}
          stroke="var(--ice)"
          strokeWidth={2}
          fill="none"
          opacity={0.9}
          data-testid="da-setpoint-past"
        />
      )}
      <path
        d={setpointFuturePath}
        stroke="var(--ice)"
        strokeWidth={2}
        fill="none"
        strokeDasharray="4 4"
        opacity={0.75}
        data-testid="da-setpoint-future"
      />
      {indoorPath && (
        <path
          d={indoorPath}
          stroke="var(--ember)"
          strokeWidth={2.4}
          fill="none"
          opacity={0.95}
          data-testid="da-indoor"
        />
      )}

      <line
        x1={nowX}
        x2={nowX}
        y1={TOP_PAD}
        y2={yBaseline}
        stroke="var(--ink-3)"
        strokeWidth={1}
        strokeDasharray="2 4"
        opacity={0.7}
        data-testid="da-now-marker"
      />
      <text
        x={nowX + 4}
        y={TOP_PAD + 10}
        fill="var(--ink-3)"
        fontFamily="var(--font-mono)"
        fontSize={10}
        opacity={0.7}
      >
        NOW
      </text>
    </svg>
  )
}

function Bar({
  bar,
  isPast,
  isCurrent,
  x,
  yBaseline,
  yForCents,
  barWidth,
}: {
  bar: HourlyBar
  isPast: boolean
  isCurrent: boolean
  x: number
  yBaseline: number
  yForCents: (c: number) => number
  barWidth: number
}) {
  const forecast = bar.forecast_cents ?? 0
  const realized = bar.realized_cents
  const yForecast = yForCents(forecast)
  const barH = yBaseline - yForecast

  let fillColor = 'rgba(255,255,255,0.06)'
  let strokeColor = 'var(--ink-5)'
  let tier = 'future'
  if (realized !== null && realized !== undefined) {
    tier = realized < PRICE_GREEN
      ? 'green'
      : realized < PRICE_RED
        ? 'yellow'
        : 'red'
    if (tier === 'green') {
      fillColor = 'color-mix(in oklab, var(--live) 38%, transparent)'
      strokeColor = 'var(--live)'
    } else if (tier === 'yellow') {
      fillColor = 'color-mix(in oklab, var(--warn) 45%, transparent)'
      strokeColor = 'var(--warn)'
    } else {
      fillColor = 'color-mix(in oklab, var(--danger) 50%, transparent)'
      strokeColor = 'var(--danger)'
    }
  }

  return (
    <g data-testid={`da-bar-${bar.hour}`} data-tier={tier}>
      {barH > 0 && (
        <rect
          x={x}
          y={yForecast}
          width={barWidth}
          height={barH}
          fill={fillColor}
          stroke={strokeColor}
          strokeWidth={isCurrent ? 1.5 : 1}
          opacity={isPast || isCurrent ? 1 : 0.65}
          rx={1.5}
        />
      )}
    </g>
  )
}

function useSeriesPath(points: { x: number | null; y: number }[]): string {
  return useMemo(() => {
    const segments: string[] = []
    let inSegment = false
    for (const p of points) {
      if (p.x === null) {
        inSegment = false
        continue
      }
      segments.push(
        `${inSegment ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`,
      )
      inSegment = true
    }
    return segments.join(' ')
  }, [points])
}

function tsToFracHour(ts: string, nowIso: string): number | null {
  const t = new Date(ts)
  const now = new Date(nowIso)
  if (Number.isNaN(t.getTime()) || Number.isNaN(now.getTime())) return null
  const sameDay =
    t.getFullYear() === now.getFullYear() &&
    t.getMonth() === now.getMonth() &&
    t.getDate() === now.getDate()
  if (!sameDay) return null
  return t.getHours() + t.getMinutes() / 60 + t.getSeconds() / 3600
}

function YAxisTemp({
  yForTemp,
  chartW,
}: {
  yForTemp: (f: number) => number
  chartW: number
}) {
  const ticks = [60, 65, 70, 75, 80, 85, 90]
  return (
    <g aria-hidden="true">
      {ticks.map((t) => (
        <g key={t}>
          <line
            x1={LEFT_PAD}
            x2={LEFT_PAD + chartW}
            y1={yForTemp(t)}
            y2={yForTemp(t)}
            stroke="var(--line)"
            strokeOpacity={0.25}
            strokeDasharray="2 4"
          />
          <text
            x={LEFT_PAD - 6}
            y={yForTemp(t) + 3}
            fill="var(--ink-4)"
            fontFamily="var(--font-mono)"
            fontSize={9}
            textAnchor="end"
          >
            {t}°
          </text>
        </g>
      ))}
    </g>
  )
}

function YAxisCents({
  yForCents,
  chartW,
  width,
}: {
  yForCents: (c: number) => number
  chartW: number
  width: number
}) {
  const ticks = [0, 5, 10, 15, 20]
  return (
    <g aria-hidden="true">
      {ticks.map((t) => (
        <text
          key={t}
          x={LEFT_PAD + chartW + 6}
          y={yForCents(t) + 3}
          fill="var(--ink-4)"
          fontFamily="var(--font-mono)"
          fontSize={9}
        >
          {t}¢
        </text>
      ))}
      <text
        x={width - 4}
        y={TOP_PAD - 8}
        fill="var(--ink-5)"
        fontFamily="var(--font-mono)"
        fontSize={9}
        textAnchor="end"
      >
        ¢/kWh
      </text>
    </g>
  )
}

function XAxisHours({
  xForHour,
  yBaseline,
  chartW,
}: {
  xForHour: (h: number) => number
  yBaseline: number
  chartW: number
}) {
  const ticks = [0, 4, 8, 12, 16, 20]
  return (
    <g aria-hidden="true">
      <line
        x1={LEFT_PAD}
        x2={LEFT_PAD + chartW}
        y1={yBaseline}
        y2={yBaseline}
        stroke="var(--line)"
        strokeOpacity={0.6}
      />
      {ticks.map((h) => (
        <text
          key={h}
          x={xForHour(h)}
          y={yBaseline + 14}
          fill="var(--ink-4)"
          fontFamily="var(--font-mono)"
          fontSize={9}
          textAnchor="middle"
        >
          {hourLabel(h)}
        </text>
      ))}
    </g>
  )
}

function hourLabel(h: number): string {
  if (h === 0) return '12a'
  if (h === 12) return '12p'
  return h < 12 ? `${h}a` : `${h - 12}p`
}
