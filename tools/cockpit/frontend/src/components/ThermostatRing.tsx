import { motion } from 'framer-motion'
import { useCountUp } from '../lib/useCountUp'

// 270° SVG ring. Indoor temp inside; cool + heat setpoints as ticks on
// the perimeter, color-coded. Range 60-90°F maps to the 270° sweep.

const RANGE_MIN_F = 60
const RANGE_MAX_F = 90
const SWEEP_DEG = 270
const START_DEG = 135 // bottom-left, sweeping clockwise to bottom-right

function tempToAngle(f: number) {
  const clamped = Math.max(RANGE_MIN_F, Math.min(RANGE_MAX_F, f))
  const frac = (clamped - RANGE_MIN_F) / (RANGE_MAX_F - RANGE_MIN_F)
  return START_DEG + frac * SWEEP_DEG
}

function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = ((deg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

export function ThermostatRing({
  indoor_f,
  cool_f,
  heat_f,
}: {
  indoor_f: number
  cool_f: number
  heat_f: number
}) {
  const indoorDisplay = useCountUp(indoor_f, 1)
  const size = 240
  const cx = size / 2
  const cy = size / 2
  const r = 96

  const trackStart = polar(cx, cy, r, START_DEG)
  const trackEnd = polar(cx, cy, r, START_DEG + SWEEP_DEG)
  const trackPath = `M ${trackStart.x} ${trackStart.y} A ${r} ${r} 0 1 1 ${trackEnd.x} ${trackEnd.y}`

  const coolTick = polar(cx, cy, r, tempToAngle(cool_f))
  const heatTick = polar(cx, cy, r, tempToAngle(heat_f))

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className="block"
    >
      <path
        d={trackPath}
        stroke="#27272a"
        strokeWidth={8}
        fill="none"
        strokeLinecap="round"
      />
      <circle cx={coolTick.x} cy={coolTick.y} r={6} fill="#38bdf8" />
      <circle cx={heatTick.x} cy={heatTick.y} r={6} fill="#fb7185" />
      <motion.text
        x={cx}
        y={cy + 4}
        textAnchor="middle"
        className="fill-zinc-50 font-sans"
        fontSize={56}
        fontWeight={700}
        data-testid="thermostat-indoor-temp"
      >
        {indoorDisplay}
      </motion.text>
      <text
        x={cx}
        y={cy + 36}
        textAnchor="middle"
        className="fill-zinc-400"
        fontSize={14}
      >
        °F
      </text>
    </svg>
  )
}
