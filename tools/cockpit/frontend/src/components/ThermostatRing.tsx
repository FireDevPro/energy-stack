import { motion } from 'framer-motion'
import { useCountUp } from '../lib/useCountUp'

// 270° SVG ring with cool/heat setpoint markers AND a filled arc from
// the cool setpoint to the indoor temperature (so the ring reads as
// "how far above the setpoint are we?" at a glance). Color of the arc
// signals: sky if indoor at-or-below cool setpoint, amber if above.

const RANGE_MIN_F = 60
const RANGE_MAX_F = 90
const SWEEP_DEG = 270
const START_DEG = 135 // bottom-left
const SIZE = 220
const STROKE = 10
const RADIUS = SIZE / 2 - STROKE - 4

function tempToAngle(f: number) {
  const clamped = Math.max(RANGE_MIN_F, Math.min(RANGE_MAX_F, f))
  const frac = (clamped - RANGE_MIN_F) / (RANGE_MAX_F - RANGE_MIN_F)
  return START_DEG + frac * SWEEP_DEG
}

function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = ((deg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

function arcPath(cx: number, cy: number, r: number, startDeg: number, endDeg: number) {
  const start = polar(cx, cy, r, startDeg)
  const end = polar(cx, cy, r, endDeg)
  const largeArc = endDeg - startDeg > 180 ? 1 : 0
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y}`
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
  const cx = SIZE / 2
  const cy = SIZE / 2

  const trackPath = arcPath(cx, cy, RADIUS, START_DEG, START_DEG + SWEEP_DEG)
  const indoorAngle = tempToAngle(indoor_f)
  const coolAngle = tempToAngle(cool_f)
  const heatAngle = tempToAngle(heat_f)

  // Fill arc from heat setpoint to indoor temp shows the "live" range.
  const fillStart = Math.min(heatAngle, indoorAngle)
  const fillEnd = Math.max(heatAngle, indoorAngle)
  const fillPath = arcPath(cx, cy, RADIUS, fillStart, fillEnd)

  // Indoor sits above cool setpoint? Arc tints amber. Otherwise cool blue.
  const aboveCool = indoor_f > cool_f
  const fillStroke = aboveCool ? '#fbbf24' : '#38bdf8'

  const coolTick = polar(cx, cy, RADIUS, coolAngle)
  const heatTick = polar(cx, cy, RADIUS, heatAngle)
  const indoorTick = polar(cx, cy, RADIUS, indoorAngle)

  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      className="block"
    >
      {/* Background track */}
      <path
        d={trackPath}
        stroke="#1f1f23"
        strokeWidth={STROKE}
        fill="none"
        strokeLinecap="round"
      />
      {/* Filled arc (heat → indoor) */}
      <path
        d={fillPath}
        stroke={fillStroke}
        strokeWidth={STROKE}
        fill="none"
        strokeLinecap="round"
        opacity={0.75}
      />
      {/* Heat marker (rose) */}
      <circle
        cx={heatTick.x}
        cy={heatTick.y}
        r={STROKE / 2 + 2}
        fill="#fb7185"
      />
      {/* Cool marker (sky) */}
      <circle
        cx={coolTick.x}
        cy={coolTick.y}
        r={STROKE / 2 + 2}
        fill="#38bdf8"
      />
      {/* Indoor indicator (radium — the live value) */}
      <circle
        cx={indoorTick.x}
        cy={indoorTick.y}
        r={5}
        fill="#a3ff70"
      />
      {/* Inner hairline ring for visual finish */}
      <circle
        cx={cx}
        cy={cy}
        r={RADIUS - STROKE / 2 - 4}
        fill="none"
        stroke="#18181b"
        strokeWidth={1}
      />

      {/* Hero temp */}
      <motion.text
        x={cx}
        y={cy - 4}
        textAnchor="middle"
        dominantBaseline="central"
        className="fill-zinc-50 font-sans"
        fontSize={62}
        fontWeight={700}
        letterSpacing="-2"
        data-testid="thermostat-indoor-temp"
      >
        {indoorDisplay}
      </motion.text>
      <text
        x={cx}
        y={cy + 28}
        textAnchor="middle"
        className="fill-zinc-500 font-sans uppercase tracking-[0.25em]"
        fontSize={9}
        fontWeight={600}
      >
        Indoor · °F
      </text>
    </svg>
  )
}
