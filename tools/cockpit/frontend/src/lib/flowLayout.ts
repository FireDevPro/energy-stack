import type { Node, Edge } from '@xyflow/react'

// Manual coordinates for the 8-node parallel-then-merge layout.
// LTR flow: Weather (0) → Day Type (1) → [Schedule, Price, 5CP] → Winner (5) → Supervisor (6) → Action (7)

export const NODE_POSITIONS: Record<string, { x: number; y: number }> = {
  weather: { x: 0, y: 180 },
  day_type: { x: 200, y: 180 },
  schedule: { x: 420, y: 40 },
  price_overlay: { x: 420, y: 180 },
  fivecp: { x: 420, y: 320 },
  winner: { x: 640, y: 180 },
  supervisor: { x: 840, y: 180 },
  action: { x: 1040, y: 180 },
}

// Width/height set explicitly so React Flow skips ResizeObserver-based
// measurement. jsdom's ResizeObserver mock is a no-op, so without these
// React Flow never marks nodes as "measured" and edges never render in
// the test environment. Production browsers measure real dimensions and
// override these defaults via the normal observer path.
const NODE_WIDTH = 200
const NODE_HEIGHT = 110

export const STATIC_NODES: Node[] = Object.entries(NODE_POSITIONS).map(
  ([id, position]) => ({
    id,
    position,
    type: id, // each node type registered with the same key
    data: {}, // populated at render time from the Snapshot
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    measured: { width: NODE_WIDTH, height: NODE_HEIGHT },
  }),
)

export const STATIC_EDGES: Edge[] = [
  {
    id: 'e-weather-daytype',
    source: 'weather',
    target: 'day_type',
    type: 'default',
  },
  {
    id: 'e-daytype-schedule',
    source: 'day_type',
    target: 'schedule',
    type: 'active',
  },
  {
    id: 'e-daytype-price',
    source: 'day_type',
    target: 'price_overlay',
    type: 'active',
  },
  {
    id: 'e-daytype-fivecp',
    source: 'day_type',
    target: 'fivecp',
    type: 'active',
  },
  {
    id: 'e-schedule-winner',
    source: 'schedule',
    target: 'winner',
    type: 'active',
  },
  {
    id: 'e-price-winner',
    source: 'price_overlay',
    target: 'winner',
    type: 'active',
  },
  {
    id: 'e-fivecp-winner',
    source: 'fivecp',
    target: 'winner',
    type: 'active',
  },
  {
    id: 'e-winner-supervisor',
    source: 'winner',
    target: 'supervisor',
    type: 'active',
    data: { testId: 'edge-winner-active' },
  },
  {
    id: 'e-supervisor-action',
    source: 'supervisor',
    target: 'action',
    type: 'actionEdge',
    data: { testId: 'edge-supervisor-to-action' },
  },
]
