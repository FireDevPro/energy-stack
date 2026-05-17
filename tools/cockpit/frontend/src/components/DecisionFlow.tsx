import { useMemo } from 'react'
import { ReactFlow, Background } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { Snapshot, Flow, WinnerDetails } from '../types'
import { STATIC_NODES, STATIC_EDGES } from '../lib/flowLayout'

import { WeatherNode } from './nodes/WeatherNode'
import { DayTypeNode } from './nodes/DayTypeNode'
import { ScheduleNode } from './nodes/ScheduleNode'
import { PriceOverlayNode } from './nodes/PriceOverlayNode'
import { FiveCPNode } from './nodes/FiveCPNode'
import { WinnerNode } from './nodes/WinnerNode'
import { SupervisorNode } from './nodes/SupervisorNode'
import { ActionNode } from './nodes/ActionNode'

import { DefaultEdge } from './edges/DefaultEdge'
import { ActiveEdge } from './edges/ActiveEdge'
import { ActionEdge } from './edges/ActionEdge'

const NODE_TYPES = {
  weather: WeatherNode,
  day_type: DayTypeNode,
  schedule: ScheduleNode,
  price_overlay: PriceOverlayNode,
  fivecp: FiveCPNode,
  winner: WinnerNode,
  supervisor: SupervisorNode,
  action: ActionNode,
}

const EDGE_TYPES = {
  default: DefaultEdge,
  active: ActiveEdge,
  actionEdge: ActionEdge,
}

// Returns the set of lane node IDs that should glow as "winning." The
// `'tie'` case is real data (LAYER_RESOLUTION_TIE_WARMER_WINS fires when
// multiple lanes propose the same warmest setpoint); rendering it as all
// three lanes glowing matches the semantic "multiple lanes agreed at the
// warmest." A future Phase 2 polish task may add an explicit "tie" badge
// on the Winner node body; until then, the lane-set rendering is the
// only visible cue.
function winningLanes(layer: WinnerDetails['winning_layer']): Set<string> {
  if (layer === 'tie')
    return new Set(['schedule', 'price_overlay', 'fivecp'])
  if (layer === 'price_overlay') return new Set(['price_overlay'])
  if (layer === 'fivecp') return new Set(['fivecp'])
  return new Set(['schedule'])
}

export function DecisionFlow({ snapshot }: { snapshot: Snapshot }) {
  const nodes = useMemo(() => {
    return STATIC_NODES.map((n) => ({
      ...n,
      data: snapshot.flow[n.id as keyof Flow],
    }))
  }, [snapshot])

  const edges = useMemo(() => {
    const lanes = winningLanes(snapshot.flow.winner.details.winning_layer)
    const supervisorInvoked =
      snapshot.flow.supervisor.details.decision !== null
    // Edge style represents the DELIVERY CHANNEL state: are setpoint writes
    // PHYSICALLY HAPPENING right now? Solid when yes:
    //   - scheduler_mode='production' — writes always happen (regardless of
    //     arm_mode value, including off-protocol-production where writes
    //     are happening but outside the experiment protocol).
    //   - scheduler_mode='experiment' AND arm_mode='B-active' — writes
    //     during the B-arm of the locked calendar.
    // Dashed when no:
    //   - scheduler_mode='shadow' — never writes.
    //   - scheduler_mode='experiment' with arm_mode='A-active' /
    //     'B-fallback' / 'B-down' / 'outside-window'.
    const writesAllowed =
      snapshot.scheduler_mode === 'production' ||
      (snapshot.scheduler_mode === 'experiment' &&
        snapshot.arm_mode.mode_actual === 'B-active')
    const shadowEdge = !writesAllowed

    return STATIC_EDGES.map((e) => {
      // Day Type → lane: every lane in the winning set is active.
      if (e.source === 'day_type') {
        return {
          ...e,
          data: { ...(e.data ?? {}), active: lanes.has(e.target) },
        }
      }
      // Lane → Winner: every lane in the winning set is active.
      if (e.target === 'winner') {
        return {
          ...e,
          data: { ...(e.data ?? {}), active: lanes.has(e.source) },
        }
      }
      // Winner → Supervisor: active when supervisor was invoked.
      if (e.id === 'e-winner-supervisor') {
        return {
          ...e,
          data: { ...(e.data ?? {}), active: supervisorInvoked },
        }
      }
      // Supervisor → Action: dashed when writes are not currently allowed.
      if (e.id === 'e-supervisor-action') {
        return { ...e, data: { ...(e.data ?? {}), shadow: shadowEdge } }
      }
      return e
    })
  }, [snapshot])

  // Compute edge state at the wrapper level too so it's asserted without
  // depending on React Flow's edge SVG (which jsdom can't fully render).
  // In a real browser, the values here match what ActiveEdge / ActionEdge
  // render visually; in tests, assertions key off these data attributes.
  const winnerSet = winningLanes(snapshot.flow.winner.details.winning_layer)
  const writesAllowed =
    snapshot.scheduler_mode === 'production' ||
    (snapshot.scheduler_mode === 'experiment' &&
      snapshot.arm_mode.mode_actual === 'B-active')
  const motionOk =
    typeof window === 'undefined'
      ? true
      : !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const supervisorActive =
    snapshot.flow.supervisor.details.decision !== null

  // ReactFlow requires its container to have concrete pixel dimensions.
  // The outer flex layout supplies those at runtime via flex-1; the
  // explicit min sizes here are belt-and-suspenders so the canvas always
  // has measurable bounds even in jsdom (where flex doesn't compute).
  return (
    <div
      data-testid="decision-flow"
      data-winning-lanes={[...winnerSet].join(',')}
      data-writes-allowed={writesAllowed ? 'true' : 'false'}
      data-motion-allowed={motionOk ? 'true' : 'false'}
      data-supervisor-active={supervisorActive ? 'true' : 'false'}
      className="h-full min-h-[480px] w-full min-w-[1100px]"
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
      >
        <Background gap={24} size={1} color="#27272a" />
      </ReactFlow>
    </div>
  )
}
