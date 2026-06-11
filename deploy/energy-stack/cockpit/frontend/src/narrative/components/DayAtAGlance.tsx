import { useCallback, useMemo, useState } from 'react'
import { usePolling } from '../../lib/usePolling'
import { useSize } from '../../lib/useSize'
import { fetchDayAtAGlance } from '../lib/api'
import { summerNormalDayAtAGlance } from '../fixtures/day_at_a_glance'
import type { DayAtAGlance as DayAtAGlanceData, HourlyBar } from '../types'

const POLL_INTERVAL_MS = 60_000

// Minimum chart height so the bars + lines stay legible even when the
// section collapses (e.g. action log grew tall enough to crowd the chart).
const CHART_MIN_HEIGHT = 320
const TOP_PAD = 28
const BOTTOM_PAD = 36
const LEFT_PAD = 44
const RIGHT_PAD = 44

const TEMP_MIN = 60
const TEMP_MAX = 90

// ¢/kWh tier thresholds — matches deploy/energy-stack/hvac_scheduler/
// price_overlay.py PRICE_TIERS (elevated=10c trigger, scarcity=20c
// trigger).
const PRICE_GREEN = 10
const PRICE_RED = 20

// Log-scale Y-axis spans 1c..100c. Cents are roughly log-normal:
// calm summer hours run 3-12c, scarcity events spike to 50-200c+. A
// linear axis would crush the bottom range; log preserves resolution
// at 5/10/20c (where decisions happen) while still showing 50/100c
// spikes proportionally. Values <1 are clipped to 1c for display.
const PRICE_AXIS_MIN = 1
const PRICE_AXIS_MAX = 100
const Y_AXIS_TICKS = [1, 2, 5, 10, 20, 50, 100]

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

  // Callback ref so the ResizeObserver attaches whenever the chart-wrap
  // mounts — including after a loading → data render transition. A
  // useRef+useLayoutEffect pair would silently skip the install if the
  // wrap wasn't in the tree on first render (the loading branch returns
  // a different <section> with no ref). See Cockpit Scaling Audit #00.
  const [chartRef, { w: measuredW, h: measuredH }] = useSize()
  const chartHeight = Math.max(CHART_MIN_HEIGHT, Math.floor(measuredH))
  const chartWidth = Math.floor(measuredW)

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
      <div ref={chartRef} className="narrative-da-chart-wrap">
        {chartWidth > 0 && (
          <Chart data={data} width={chartWidth} height={chartHeight} />
        )}
      </div>
    </section>
  )
}

interface HoverState {
  hour: number
  x: number
  y: number
  forecast: number | null
  realized: number | null
  status: 'past' | 'current' | 'future'
}

function Chart({
  data,
  width,
  height,
}: {
  data: DayAtAGlanceData
  width: number
  height: number
}) {
  // Floor at 0 to keep the math safe; the narrative-center column has
  // its own min-width so the chart never actually has to render that
  // narrow.
  const chartW = Math.max(0, width - LEFT_PAD - RIGHT_PAD)
  const chartH = Math.max(0, height - TOP_PAD - BOTTOM_PAD)
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
  // Log mapping: log10(PRICE_AXIS_MIN) -> baseline, log10(PRICE_AXIS_MAX) -> top.
  const logMin = Math.log10(PRICE_AXIS_MIN)
  const logMax = Math.log10(PRICE_AXIS_MAX)
  const yForCents = (c: number) => {
    const clamped = Math.max(PRICE_AXIS_MIN, Math.min(PRICE_AXIS_MAX, c))
    const frac = (Math.log10(clamped) - logMin) / (logMax - logMin)
    return TOP_PAD + chartH * (1 - frac)
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

  const [hover, setHover] = useState<HoverState | null>(null)

  return (
    <>
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Day at a glance — indoor temperature, setpoints, and price bars"
      onMouseLeave={() => setHover(null)}
    >
      <YAxisTemp yForTemp={yForTemp} chartW={chartW} />
      <YAxisCents yForCents={yForCents} chartW={chartW} width={width} />
      <XAxisHours xForHour={xForHour} yBaseline={yBaseline} chartW={chartW} />

      <g data-testid="da-bars">
        {data.bars.map((bar) => {
          const isPast = bar.hour < Math.floor(nowFracHour)
          const isCurrent = bar.hour === Math.floor(nowFracHour)
          const status: HoverState['status'] = isCurrent
            ? 'current'
            : isPast
              ? 'past'
              : 'future'
          return (
            <Bar
              key={bar.hour}
              bar={bar}
              isPast={isPast}
              isCurrent={isCurrent}
              x={xForHour(bar.hour) - barWidth / 2}
              yBaseline={yBaseline}
              yForCents={yForCents}
              barWidth={barWidth}
              onEnter={() =>
                setHover({
                  hour: bar.hour,
                  x: xForHour(bar.hour),
                  y: yForCents(bar.forecast_cents ?? 0),
                  forecast: bar.forecast_cents ?? null,
                  realized: bar.realized_cents ?? null,
                  status,
                })
              }
            />
          )
        })}
      </g>

      {/* Render order matters: indoor first, past-actual setpoint on
          top with a dotted stroke so it's visible even when coincident
          with indoor (common in shadow mode when AC pins to setpoint). */}
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
      {/* Past-actual setpoint on TOP of indoor, dotted ("2 3", tighter
          than the future line's "4 4" dash) so the indoor line stays
          visible through the gaps when the AC pins indoor exactly at
          the setpoint and the two paths are coincident. They visibly
          diverge when the price overlay fires or the supervisor
          clamps. */}
      {setpointPastPath && (
        <path
          d={setpointPastPath}
          stroke="var(--ice)"
          strokeWidth={2}
          fill="none"
          strokeDasharray="2 3"
          opacity={0.95}
          data-testid="da-setpoint-past"
        />
      )}

      <Legend xRight={LEFT_PAD + chartW} />

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
    {hover && (
      <BarTooltip hover={hover} chartWidth={width} chartHeight={height} />
    )}
    </>
  )
}

function BarTooltip({
  hover,
  chartWidth,
  chartHeight,
}: {
  hover: HoverState
  chartWidth: number
  chartHeight: number
}) {
  // Anchor the tooltip just above the bar tip. Keep it inside the
  // chart wrap by flipping left/right when too close to an edge.
  const TOOLTIP_W = 168
  const VERTICAL_OFFSET = 10
  let left = hover.x - TOOLTIP_W / 2
  if (left < 4) left = 4
  if (left + TOOLTIP_W > chartWidth - 4) left = chartWidth - TOOLTIP_W - 4
  const top = Math.max(0, hover.y - VERTICAL_OFFSET - 64)
  const statusLabel =
    hover.status === 'current'
      ? 'in progress'
      : hover.status === 'past'
        ? 'past'
        : 'forecast'
  return (
    <div
      className="narrative-da-tooltip"
      style={{ left, top, width: TOOLTIP_W }}
      role="tooltip"
      data-testid={`da-tooltip-${hover.hour}`}
    >
      <div className="narrative-da-tooltip-head">
        <span className="narrative-da-tooltip-hour">{hourLabel(hover.hour)}</span>
        <span className={`narrative-da-tooltip-status status-${hover.status}`}>
          {statusLabel}
        </span>
      </div>
      <div className="narrative-da-tooltip-row">
        <span className="k">forecast</span>
        <span className="v">{fmtCents(hover.forecast)}</span>
      </div>
      {hover.status !== 'future' && (
        <div className="narrative-da-tooltip-row">
          <span className="k">
            {hover.status === 'current' ? 'so far' : 'actual'}
          </span>
          <span className="v">{fmtCents(hover.realized)}</span>
        </div>
      )}
      {/* keep chartHeight referenced so future positioning logic that needs the
          baseline doesn't need a refactor; no-op visually */}
      <span style={{ display: 'none' }}>{chartHeight}</span>
    </div>
  )
}

function fmtCents(c: number | null): string {
  if (c === null || c === undefined) return '—'
  return `${c.toFixed(c < 10 ? 2 : 1)}¢`
}

function Bar({
  bar,
  isPast,
  isCurrent,
  x,
  yBaseline,
  yForCents,
  barWidth,
  onEnter,
}: {
  bar: HourlyBar
  isPast: boolean
  isCurrent: boolean
  x: number
  yBaseline: number
  yForCents: (c: number) => number
  barWidth: number
  onEnter: () => void
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
    <g
      data-testid={`da-bar-${bar.hour}`}
      data-tier={tier}
      onMouseEnter={onEnter}
    >
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
      {/* Invisible full-height hit target so hover works above empty
          space too (a thin forecast bar still gets hovered the whole
          column). */}
      <rect
        x={x}
        y={0}
        width={barWidth}
        height={yBaseline}
        fill="transparent"
      />
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
  return (
    <g aria-hidden="true">
      {/* Log-scale gridlines + labels at 1, 2, 5, 10, 20, 50, 100¢. */}
      {Y_AXIS_TICKS.map((t) => (
        <g key={t}>
          <line
            x1={LEFT_PAD}
            x2={LEFT_PAD + chartW}
            y1={yForCents(t)}
            y2={yForCents(t)}
            stroke="var(--line)"
            strokeOpacity={0.12}
            strokeDasharray="1 5"
          />
          <text
            x={LEFT_PAD + chartW + 6}
            y={yForCents(t) + 3}
            fill="var(--ink-4)"
            fontFamily="var(--font-mono)"
            fontSize={9}
          >
            {t}¢
          </text>
        </g>
      ))}
      {/* Tier-threshold guidelines — colored hairlines at the
          green→yellow (10¢) and yellow→red (20¢) boundaries so the
          operator can see at a glance how today sits vs the
          scheduler's bands. */}
      <line
        x1={LEFT_PAD}
        x2={LEFT_PAD + chartW}
        y1={yForCents(PRICE_GREEN)}
        y2={yForCents(PRICE_GREEN)}
        stroke="var(--warn)"
        strokeWidth={1}
        strokeOpacity={0.35}
        strokeDasharray="2 4"
      />
      <line
        x1={LEFT_PAD}
        x2={LEFT_PAD + chartW}
        y1={yForCents(PRICE_RED)}
        y2={yForCents(PRICE_RED)}
        stroke="var(--danger)"
        strokeWidth={1}
        strokeOpacity={0.35}
        strokeDasharray="2 4"
      />
      <text
        x={width - 4}
        y={TOP_PAD - 8}
        fill="var(--ink-5)"
        fontFamily="var(--font-mono)"
        fontSize={9}
        textAnchor="end"
      >
        ¢/kWh · log
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

function Legend({ xRight }: { xRight: number }) {
  // Top-right inside the chart area. Three rows stacked vertically:
  //   indoor          (orange, solid)
  //   setpoint actual (cyan, dotted)
  //   setpoint plan   (cyan, dashed)
  // Stroke patterns match the actual line treatments below so they
  // read as keys without needing a separate visual register.
  const fontSize = 12
  const swatchW = 30
  const labelDx = 38
  const rowH = 17
  const legendW = 170
  const x = xRight - legendW
  const y = TOP_PAD - 4
  const rows: Array<{
    label: string
    stroke: string
    width: number
    dash?: string
    cap?: 'round' | 'butt'
  }> = [
    { label: 'indoor', stroke: 'var(--ember)', width: 2.4 },
    { label: 'setpoint (actual)', stroke: 'var(--ice)', width: 2, dash: '2 3' },
    {
      label: 'setpoint (planned)',
      stroke: 'var(--ice)',
      width: 2,
      dash: '4 4',
    },
  ]
  return (
    <g aria-hidden="true" data-testid="da-legend">
      {rows.map((r, i) => {
        const cy = y + i * rowH
        return (
          <g key={r.label}>
            <line
              x1={x}
              x2={x + swatchW}
              y1={cy}
              y2={cy}
              stroke={r.stroke}
              strokeWidth={r.width}
              strokeDasharray={r.dash}
              strokeLinecap={r.cap ?? 'butt'}
            />
            <text
              x={x + labelDx}
              y={cy + fontSize * 0.34}
              fill="var(--ink-3)"
              fontFamily="var(--font-mono)"
              fontSize={fontSize}
            >
              {r.label}
            </text>
          </g>
        )
      })}
    </g>
  )
}
