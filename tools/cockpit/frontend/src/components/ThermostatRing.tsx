// 270° arc ring matching cockpit-hero.jsx RingArc. Embers gradient over
// indoor progress, tick scale around the perimeter, cool/heat setpoint
// markers. Size auto-scales to container via ResizeObserver.

const RANGE_MIN_F = 60
const RANGE_MAX_F = 90

function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = ((deg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

function tempToFrac(f: number) {
  const c = Math.max(RANGE_MIN_F, Math.min(RANGE_MAX_F, f))
  return (c - RANGE_MIN_F) / (RANGE_MAX_F - RANGE_MIN_F)
}

export function ThermostatRing({
  size,
  indoor_f,
  cool_f,
  heat_f,
}: {
  size: number
  indoor_f: number
  cool_f: number
  heat_f: number
}) {
  const cx = size / 2
  const cy = size / 2
  const r = size * 0.4
  const sweep = 270
  const start = 135
  const a = (frac: number) => start + frac * sweep

  const trackStart = polar(cx, cy, r, start)
  const trackEnd = polar(cx, cy, r, start + sweep)
  const trackPath = `M ${trackStart.x} ${trackStart.y} A ${r} ${r} 0 1 1 ${trackEnd.x} ${trackEnd.y}`

  const indoorFrac = tempToFrac(indoor_f)
  const indoorEnd = polar(cx, cy, r, a(indoorFrac))
  const indoorPath = `M ${trackStart.x} ${trackStart.y} A ${r} ${r} 0 ${
    indoorFrac > 0.5 ? 1 : 0
  } 1 ${indoorEnd.x} ${indoorEnd.y}`

  const coolPt = polar(cx, cy, r, a(tempToFrac(cool_f)))
  const heatPt = polar(cx, cy, r, a(tempToFrac(heat_f)))

  const ticks: React.ReactNode[] = []
  for (let i = 0; i <= 30; i++) {
    const frac = i / 30
    const inner = polar(cx, cy, r - 6, a(frac))
    const outer = polar(cx, cy, r - 1, a(frac))
    ticks.push(
      <line
        key={i}
        x1={inner.x}
        y1={inner.y}
        x2={outer.x}
        y2={outer.y}
        stroke={i % 5 === 0 ? 'var(--ink-4)' : 'var(--line)'}
        strokeWidth={i % 5 === 0 ? 1.2 : 0.8}
      />,
    )
  }

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <defs>
        <linearGradient id="ringInd" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="var(--ember)" stopOpacity="0.95" />
          <stop offset="100%" stopColor="var(--ember-2)" stopOpacity="0.95" />
        </linearGradient>
        <filter id="ringGlow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
      </defs>
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke="url(#ringInd)"
        strokeWidth="3"
        opacity="0.18"
        filter="url(#ringGlow)"
      />
      <path
        d={trackPath}
        stroke="var(--line)"
        strokeWidth={6}
        fill="none"
        strokeLinecap="round"
      />
      <g>{ticks}</g>
      <path
        d={indoorPath}
        stroke="url(#ringInd)"
        strokeWidth={6}
        fill="none"
        strokeLinecap="round"
        filter="url(#ringGlow)"
        opacity="0.95"
      />
      <path
        d={indoorPath}
        stroke="url(#ringInd)"
        strokeWidth={3.5}
        fill="none"
        strokeLinecap="round"
      />
      <g>
        <circle cx={coolPt.x} cy={coolPt.y} r={7} fill="var(--ice)" />
        <circle cx={coolPt.x} cy={coolPt.y} r={3.2} fill="var(--bg-base)" />
        <circle cx={heatPt.x} cy={heatPt.y} r={7} fill="var(--ember)" />
        <circle cx={heatPt.x} cy={heatPt.y} r={3.2} fill="var(--bg-base)" />
      </g>
    </svg>
  )
}
