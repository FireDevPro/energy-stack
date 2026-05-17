import { useMemo, useRef } from 'react'
import type { Snapshot } from '../types'
import { useSize } from '../lib/useSize'
import { winningLanes, writesAllowed } from '../lib/tone'

import { WeatherNode } from './nodes/WeatherNode'
import { DayTypeNode } from './nodes/DayTypeNode'
import { ScheduleNode } from './nodes/ScheduleNode'
import { PriceOverlayNode } from './nodes/PriceOverlayNode'
import { FiveCPNode } from './nodes/FiveCPNode'
import { WinnerNode } from './nodes/WinnerNode'
import { SupervisorNode } from './nodes/SupervisorNode'
import { ActionNode } from './nodes/ActionNode'

const NODE_IDS = [
  'weather',
  'day_type',
  'schedule',
  'price_overlay',
  'fivecp',
  'winner',
  'supervisor',
  'action',
] as const
type NodeId = (typeof NODE_IDS)[number]

const EDGES: Array<[NodeId, NodeId]> = [
  ['weather', 'day_type'],
  ['day_type', 'schedule'],
  ['day_type', 'price_overlay'],
  ['day_type', 'fivecp'],
  ['schedule', 'winner'],
  ['price_overlay', 'winner'],
  ['fivecp', 'winner'],
  ['winner', 'supervisor'],
  ['supervisor', 'action'],
]

type Anchor = 'left' | 'right' | 'top' | 'bottom' | 'auto'
interface NodePos {
  x: number
  y: number
  anchorOut: Anchor
  anchorIn: Anchor
}
type Layout = { nodeW: number; nodeH: number } & Record<NodeId, NodePos>

function computeLayout(W: number, H: number, density: string): Layout {
  const capW = density === 'compact' ? 180 : density === 'generous' ? 232 : 208
  const capH = density === 'compact' ? 92 : density === 'generous' ? 130 : 110
  const padX = 24
  const cols = 6
  const gap = 18
  const nodeW = Math.max(
    150,
    Math.min(capW, (W - padX * 2 - gap * (cols - 1)) / cols),
  )
  const nodeH = capH
  const colW = (W - padX * 2 - nodeW) / (cols - 1)
  const x = (i: number) => padX + nodeW / 2 + i * colW
  const cy = H / 2
  const laneSpread = Math.min(H * 0.34, nodeH + 56)

  const out: Anchor = 'right'
  const inA: Anchor = 'left'

  return {
    nodeW,
    nodeH,
    weather: { x: x(0), y: cy, anchorOut: out, anchorIn: inA },
    day_type: { x: x(1), y: cy, anchorOut: out, anchorIn: inA },
    schedule: {
      x: x(2),
      y: cy - laneSpread,
      anchorOut: out,
      anchorIn: inA,
    },
    price_overlay: { x: x(2), y: cy, anchorOut: out, anchorIn: inA },
    fivecp: { x: x(2), y: cy + laneSpread, anchorOut: out, anchorIn: inA },
    winner: { x: x(3), y: cy, anchorOut: out, anchorIn: inA },
    supervisor: { x: x(4), y: cy, anchorOut: out, anchorIn: inA },
    action: { x: x(5), y: cy, anchorOut: out, anchorIn: inA },
  }
}

function anchorPoint(
  pos: NodePos,
  side: Anchor,
  nodeW: number,
  nodeH: number,
  otherPos: NodePos,
): { x: number; y: number } {
  if (side === 'left') return { x: pos.x - nodeW / 2, y: pos.y }
  if (side === 'right') return { x: pos.x + nodeW / 2, y: pos.y }
  if (side === 'top') return { x: pos.x, y: pos.y - nodeH / 2 }
  if (side === 'bottom') return { x: pos.x, y: pos.y + nodeH / 2 }
  const dx = otherPos.x - pos.x
  const dy = otherPos.y - pos.y
  if (Math.abs(dx) * nodeH > Math.abs(dy) * nodeW) {
    return { x: pos.x + Math.sign(dx) * (nodeW / 2), y: pos.y }
  }
  return { x: pos.x, y: pos.y + Math.sign(dy) * (nodeH / 2) }
}

function pathFor(
  fromPos: NodePos,
  toPos: NodePos,
  nodeW: number,
  nodeH: number,
): string {
  const a = anchorPoint(fromPos, fromPos.anchorOut, nodeW, nodeH, toPos)
  const b = anchorPoint(toPos, toPos.anchorIn, nodeW, nodeH, fromPos)
  const k = Math.max(60, (b.x - a.x) * 0.45)
  return `M ${a.x} ${a.y} C ${a.x + k} ${a.y}, ${b.x - k} ${b.y}, ${b.x} ${b.y}`
}

const NODE_COMPONENTS = {
  weather: WeatherNode,
  day_type: DayTypeNode,
  schedule: ScheduleNode,
  price_overlay: PriceOverlayNode,
  fivecp: FiveCPNode,
  winner: WinnerNode,
  supervisor: SupervisorNode,
  action: ActionNode,
} as const

export function DecisionBoard({ snapshot }: { snapshot: Snapshot }) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const { w, h } = useSize(wrapRef)
  const W = w || 1200
  const H = h || 560

  const positions = useMemo(
    () => computeLayout(W, H, 'comfortable'),
    [W, H],
  )

  const lanes = winningLanes(snapshot.flow.winner.details.winning_layer)
  const supervisorInvoked =
    snapshot.flow.supervisor.details.decision !== null
  const writesOk = writesAllowed(snapshot)
  const shadowEdge = !writesOk
  const motionOk =
    typeof window === 'undefined'
      ? true
      : !window.matchMedia('(prefers-reduced-motion: reduce)').matches

  const edges = useMemo(
    () =>
      EDGES.map(([from, to]) => {
        const active =
          from === 'day_type'
            ? lanes.has(to)
            : to === 'winner'
              ? lanes.has(from)
              : from === 'winner' && to === 'supervisor'
                ? supervisorInvoked
                : from === 'supervisor' && to === 'action'
                  ? !shadowEdge && snapshot.flow.action.details.applied === true
                  : true
        const isShadow = from === 'supervisor' && to === 'action' && shadowEdge
        return {
          id: `e-${from}-${to}`,
          from,
          to,
          active,
          shadow: isShadow,
        }
      }),
    [lanes, supervisorInvoked, shadowEdge, snapshot.flow.action.details.applied],
  )

  const ready = w > 0 && h > 0
  const { nodeW, nodeH } = positions

  return (
    <div
      className="flow-stage"
      ref={wrapRef}
      data-testid="decision-flow"
      data-winning-lanes={[...lanes].join(',')}
      data-writes-allowed={writesOk ? 'true' : 'false'}
      data-motion-allowed={motionOk ? 'true' : 'false'}
      data-supervisor-active={supervisorInvoked ? 'true' : 'false'}
    >
      {ready && (
        <svg
          className="edges"
          width={W}
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
        >
          <defs>
            {edges.map((e) => {
              const fp = positions[e.from]
              const tp = positions[e.to]
              const d = pathFor(fp, tp, nodeW, nodeH)
              return (
                <path key={e.id} id={e.id} d={d} fill="none" stroke="none" />
              )
            })}
          </defs>
          {edges.map((e) => {
            const fp = positions[e.from]
            const tp = positions[e.to]
            const d = pathFor(fp, tp, nodeW, nodeH)
            return (
              <path
                key={e.id + '-vis'}
                d={d}
                className={`edge-path ${e.active ? 'active' : ''} ${
                  e.shadow ? 'shadow' : ''
                }`}
              />
            )
          })}
          {motionOk &&
            edges.map((e, i) =>
              e.active && !e.shadow ? (
                <circle key={e.id + '-dot'} r="2.6" className="edge-flow">
                  <animateMotion
                    dur="2.6s"
                    repeatCount="indefinite"
                    begin={`${(i * 0.3) % 1.5}s`}
                  >
                    <mpath href={`#${e.id}`} />
                  </animateMotion>
                </circle>
              ) : null,
            )}
        </svg>
      )}

      {ready && (
        <div className="nodes-layer">
          {NODE_IDS.map((id) => {
            const pos = positions[id]
            const Component = NODE_COMPONENTS[id] as React.ComponentType<{
              data: unknown
              pos: { x: number; y: number }
              nodeW: number
              nodeH: number
            }>
            return (
              <Component
                key={id}
                data={snapshot.flow[id as keyof typeof snapshot.flow]}
                pos={{ x: pos.x, y: pos.y }}
                nodeW={nodeW}
                nodeH={nodeH}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
