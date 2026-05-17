import type { Snapshot, WinnerDetails } from '../types'

import { WeatherNode } from './nodes/WeatherNode'
import { DayTypeNode } from './nodes/DayTypeNode'
import { ScheduleNode } from './nodes/ScheduleNode'
import { PriceOverlayNode } from './nodes/PriceOverlayNode'
import { FiveCPNode } from './nodes/FiveCPNode'
import { WinnerNode } from './nodes/WinnerNode'
import { SupervisorNode } from './nodes/SupervisorNode'
import { ActionNode } from './nodes/ActionNode'

// Direction-A redesign: drop React Flow. The 8 nodes live in a fixed
// 4-column grid:
//
//   Column 1: Inputs (Weather + Day Type stacked)
//   Column 2-4: Three competing lanes (Schedule | Price | 5CP) as a row
//   Then the chain (Winner → Supervisor → Action) as a row below
//
// At ≥1280px the layout reads inputs-on-left, three-lanes-in-middle,
// chain-on-right. At smaller widths the chain wraps to a new row below
// the lanes. No clipping at 1280×720 because the grid is fluid.
//
// Topology is conveyed by:
//   - column headers ("INPUTS", "ARBITRATION", "OUTPUT")
//   - a single hairline rule between sections
//   - the winning-lane left-bar (radium) drawing the eye through the
//     graph in one glance
// No SVG edges — they were the worst offender in the React Flow build.

function winningLanes(layer: WinnerDetails['winning_layer']): Set<string> {
  if (layer === 'tie') return new Set(['schedule', 'price_overlay', 'fivecp'])
  if (layer === 'price_overlay') return new Set(['price_overlay'])
  if (layer === 'fivecp') return new Set(['fivecp'])
  return new Set(['schedule'])
}

export function DecisionBoard({ snapshot }: { snapshot: Snapshot }) {
  const lanes = winningLanes(snapshot.flow.winner.details.winning_layer)
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

  return (
    <div
      data-testid="decision-flow"
      data-winning-lanes={[...lanes].join(',')}
      data-writes-allowed={writesAllowed ? 'true' : 'false'}
      data-motion-allowed={motionOk ? 'true' : 'false'}
      data-supervisor-active={supervisorActive ? 'true' : 'false'}
      className="grid grid-cols-1 gap-6 px-6 py-6 lg:grid-cols-[200px_minmax(0,720px)_240px] lg:gap-8"
    >
      {/* COLUMN 1 — Inputs */}
      <Section label="Inputs">
        <WeatherNode data={snapshot.flow.weather} />
        <DayTypeNode data={snapshot.flow.day_type} />
      </Section>

      {/* COLUMN 2 — Arbitration (three lanes side-by-side) */}
      <Section label="Arbitration · who proposes the setpoint">
        <div className="grid grid-cols-3 gap-3">
          <ScheduleNode data={snapshot.flow.schedule} />
          <PriceOverlayNode data={snapshot.flow.price_overlay} />
          <FiveCPNode data={snapshot.flow.fivecp} />
        </div>
      </Section>

      {/* COLUMN 3 — Output chain */}
      <Section label="Output">
        <WinnerNode data={snapshot.flow.winner} />
        <SupervisorNode data={snapshot.flow.supervisor} />
        <ActionNode data={snapshot.flow.action} />
      </Section>
    </div>
  )
}

function Section({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="font-sans text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-600">
        {label}
      </div>
      {children}
    </div>
  )
}
