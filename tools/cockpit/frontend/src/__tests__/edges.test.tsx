import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { ReactFlowProvider, Position } from '@xyflow/react'
import { ActiveEdge } from '../components/edges/ActiveEdge'
import { ActionEdge } from '../components/edges/ActionEdge'

// Phase 1 outside-in acceptance test asserts edge state via wrapper-level
// data attributes on DecisionFlow (data-writes-allowed, data-motion-allowed,
// data-winning-lanes). That covers the SNAPSHOT → DECISION pipeline but
// does NOT cover the EDGE COMPONENT → DOM ATTRIBUTE derivation. These
// unit tests close that coverage gap by rendering each edge in isolation
// with explicit props and asserting on the emitted data-* attributes.
//
// The edge components are pure presentation: given (active, shadow, source/
// target coords) they emit deterministic data attributes. No ReactFlow
// context needed for getBezierPath — it's a pure function of the props.

const baseProps = {
  id: 'e-test',
  source: 's',
  target: 't',
  sourceX: 100,
  sourceY: 100,
  targetX: 300,
  targetY: 100,
  sourcePosition: Position.Right,
  targetPosition: Position.Left,
  selected: false,
  animated: false,
  type: 'default' as const,
}

function renderInFlow(element: React.ReactElement) {
  return render(
    <ReactFlowProvider>
      <svg>{element}</svg>
    </ReactFlowProvider>,
  )
}

describe('ActiveEdge', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((q: string) => ({
        matches: false,
        media: q,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
  })

  it('emits data-animated="true" when active and motion allowed', () => {
    const { container } = renderInFlow(
      <ActiveEdge
        {...baseProps}
        data={{ active: true, testId: 'edge-test-active' }}
      />,
    )
    const edge = container.querySelector('[data-testid="edge-test-active"]')
    expect(edge).not.toBeNull()
    expect(edge).toHaveAttribute('data-animated', 'true')
  })

  it('emits data-animated="false" when inactive', () => {
    const { container } = renderInFlow(
      <ActiveEdge
        {...baseProps}
        data={{ active: false, testId: 'edge-test-active' }}
      />,
    )
    const edge = container.querySelector('[data-testid="edge-test-active"]')
    expect(edge).toHaveAttribute('data-animated', 'false')
  })

  it('emits data-animated="false" when prefers-reduced-motion is set', () => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((q: string) => ({
        matches: q.includes('reduce'),
        media: q,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
    const { container } = renderInFlow(
      <ActiveEdge
        {...baseProps}
        data={{ active: true, testId: 'edge-test-active' }}
      />,
    )
    const edge = container.querySelector('[data-testid="edge-test-active"]')
    expect(edge).toHaveAttribute('data-animated', 'false')
  })
})

describe('ActionEdge', () => {
  it('emits data-edge-style="solid" when shadow is false', () => {
    const { container } = renderInFlow(
      <ActionEdge
        {...baseProps}
        data={{ shadow: false, testId: 'edge-action' }}
      />,
    )
    const edge = container.querySelector('[data-testid="edge-action"]')
    expect(edge).toHaveAttribute('data-edge-style', 'solid')
  })

  it('emits data-edge-style="dashed" when shadow is true', () => {
    const { container } = renderInFlow(
      <ActionEdge
        {...baseProps}
        data={{ shadow: true, testId: 'edge-action' }}
      />,
    )
    const edge = container.querySelector('[data-testid="edge-action"]')
    expect(edge).toHaveAttribute('data-edge-style', 'dashed')
  })

  it('defaults to dashed (conservative) when shadow is unset', () => {
    // Solid claims writes are physically happening. If the snapshot data
    // is missing the `shadow` flag entirely, we don't actually know — the
    // safe default is dashed (no writes assumed).
    const { container } = renderInFlow(
      <ActionEdge {...baseProps} data={{ testId: 'edge-action' }} />,
    )
    const edge = container.querySelector('[data-testid="edge-action"]')
    expect(edge).toHaveAttribute('data-edge-style', 'dashed')
  })
})
