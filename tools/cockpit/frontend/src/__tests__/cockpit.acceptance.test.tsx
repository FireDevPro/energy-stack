import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import App from '../App'
import { summerNormal } from '../fixtures/summer_normal'
import { shadowCurrent } from '../fixtures/shadow_current'
import type { Snapshot } from '../types'

// Fixtures declare `: Snapshot` at their definition (TS narrows the
// literal types there). Re-asserting via direct assignment (no cast)
// gives a second layer of compile-time drift detection: if a future
// fixture refactor loses the inline annotation, this file fails to
// typecheck before any test runs.
const _typeCheckSummer: Snapshot = summerNormal
const _typeCheckShadow: Snapshot = shadowCurrent
void _typeCheckSummer
void _typeCheckShadow

describe('Cockpit Phase 1 outside-in acceptance', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/')
  })

  it('renders summer_normal fixture with Schedule winning', () => {
    render(<App />)

    expect(screen.getByTestId('chip-scheduler-mode')).toHaveTextContent(
      'experiment',
    )
    expect(screen.getByTestId('chip-arm-mode')).toHaveTextContent('B-active')
    expect(screen.getByTestId('chip-controller-alive')).toBeInTheDocument()

    const feedHealth = screen.getByTestId('feed-health-strip')
    expect(within(feedHealth).getAllByTestId(/^feed-chip-/).length).toBe(7)

    expect(screen.getByTestId('thermostat-indoor-temp')).toHaveTextContent(
      '74.8',
    )
    expect(screen.getByTestId('thermostat-cool-setpoint')).toHaveTextContent(
      '76',
    )
    expect(screen.getByTestId('thermostat-price-chip')).toHaveTextContent('8.4')
    expect(screen.getByTestId('thermostat-price-chip')).toHaveAttribute(
      'data-tier',
      'normal',
    )

    expect(screen.getByTestId('thermostat-tick-footer')).toHaveTextContent(
      'a1b2c3d4',
    )

    expect(screen.getByTestId('node-schedule')).toHaveAttribute(
      'data-role-state',
      'winning',
    )
    expect(screen.getByTestId('node-price-overlay')).toHaveAttribute(
      'data-role-state',
      'dimmed',
    )
    expect(screen.getByTestId('node-fivecp')).toHaveAttribute(
      'data-role-state',
      'dimmed',
    )
    expect(screen.getByTestId('node-weather')).toHaveAttribute(
      'data-role-state',
      'context',
    )
    expect(screen.getByTestId('node-day-type')).toHaveAttribute(
      'data-role-state',
      'context',
    )
    expect(screen.getByTestId('node-winner')).toHaveAttribute(
      'data-role-state',
      'winning',
    )
    expect(screen.getByTestId('node-supervisor')).toHaveAttribute(
      'data-role-state',
      'winning',
    )
    expect(screen.getByTestId('node-action')).toHaveAttribute(
      'data-role-state',
      'winning',
    )

    expect(screen.getByTestId('action-badge')).toHaveTextContent('APPLIED')
    // Edge style isn't directly assertable in jsdom because React Flow's
    // edge SVG depends on viewport measurement that jsdom doesn't compute.
    // Wrapper-level data attributes mirror what edges render in a real
    // browser; assertion lives there.
    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-writes-allowed',
      'true',
    )
    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-winning-lanes',
      'schedule',
    )

    expect(screen.getByTestId('node-winner')).toHaveTextContent('76')
    expect(screen.getByTestId('node-winner')).toHaveAttribute(
      'data-changed',
      'true',
    )
  })

  it('renders shadow_current fixture with SHADOW badge and dashed edge', () => {
    window.history.replaceState({}, '', '/?fixture=shadow')
    render(<App />)

    expect(screen.getByTestId('chip-scheduler-mode')).toHaveTextContent(
      'shadow',
    )
    expect(screen.getByTestId('chip-arm-mode')).toHaveTextContent(
      'outside-window',
    )
    expect(screen.getByTestId('action-badge')).toHaveTextContent('SHADOW')
    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-writes-allowed',
      'false',
    )
  })

  it('honors prefers-reduced-motion on the active edge', () => {
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

    render(<App />)

    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-motion-allowed',
      'false',
    )
  })
})
