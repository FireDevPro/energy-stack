import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import App from '../App'
import { summerNormal } from '../fixtures/summer_normal'
import { shadowCurrent } from '../fixtures/shadow_current'
import { priceSpike } from '../fixtures/price_spike'
import { fivecpRisk } from '../fixtures/fivecp_risk'
import { supervisorClamp } from '../fixtures/supervisor_clamp'
import { controllerDown } from '../fixtures/controller_down'
import { feedOutage } from '../fixtures/feed_outage'
import { mildDay } from '../fixtures/mild_day'
import { armSwitch } from '../fixtures/arm_switch'
import type { Snapshot } from '../types'

// Fixtures declare `: Snapshot` at their definition (TS narrows the
// literal types there). Re-asserting via direct assignment (no cast)
// gives a second layer of compile-time drift detection: if a future
// fixture refactor loses the inline annotation, this file fails to
// typecheck before any test runs.
const _typeChecks: Snapshot[] = [
  summerNormal,
  shadowCurrent,
  priceSpike,
  fivecpRisk,
  supervisorClamp,
  controllerDown,
  feedOutage,
  mildDay,
  armSwitch,
]
void _typeChecks

describe('Cockpit Phase 1 outside-in acceptance', () => {
  beforeEach(() => {
    // Always pin to a fixture URL so App skips the live fetch path.
    // Phase 3 added live polling; tests that want live behavior live
    // in cockpit.live.test.tsx.
    window.history.replaceState({}, '', '/?fixture=summer_normal')
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

  it('honors prefers-reduced-motion on the decision flow', () => {
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

describe('Cockpit Phase 2 fixtures', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/?fixture=summer_normal')
  })

  it('price_spike: RTP Spike wins with scarcity tier', () => {
    window.history.replaceState({}, '', '/?fixture=price_spike')
    render(<App />)

    expect(screen.getByTestId('thermostat-price-chip')).toHaveAttribute(
      'data-tier',
      'scarcity',
    )
    expect(screen.getByTestId('thermostat-price-chip')).toHaveTextContent(
      '53.4',
    )
    expect(screen.getByTestId('node-price-overlay')).toHaveAttribute(
      'data-role-state',
      'winning',
    )
    expect(screen.getByTestId('node-schedule')).toHaveAttribute(
      'data-role-state',
      'dimmed',
    )
    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-winning-lanes',
      'price_overlay',
    )
    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-writes-allowed',
      'true',
    )
    expect(screen.getByTestId('action-badge')).toHaveTextContent('APPLIED')
  })

  it('fivecp_risk: 5CP wins with COMED scope', () => {
    window.history.replaceState({}, '', '/?fixture=fivecp_risk')
    render(<App />)

    expect(screen.getByTestId('node-fivecp')).toHaveAttribute(
      'data-role-state',
      'winning',
    )
    expect(screen.getByTestId('node-fivecp')).toHaveTextContent('COMED')
    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-winning-lanes',
      'fivecp',
    )
    expect(screen.getByTestId('node-winner')).toHaveTextContent('85')
  })

  it('supervisor_clamp: Supervisor renders clamped role with rose border', () => {
    window.history.replaceState({}, '', '/?fixture=supervisor_clamp')
    render(<App />)

    expect(screen.getByTestId('node-supervisor')).toHaveAttribute(
      'data-role-state',
      'clamped',
    )
    expect(screen.getByTestId('node-supervisor')).toHaveTextContent('86')
    expect(screen.getByTestId('node-supervisor')).toHaveTextContent('92')
    expect(screen.getByTestId('node-action')).toHaveTextContent('86')
  })

  it('controller_down: B-down arm, controller dead, nodes missing', () => {
    window.history.replaceState({}, '', '/?fixture=controller_down')
    render(<App />)

    expect(screen.getByTestId('chip-arm-mode')).toHaveTextContent('B-down')
    expect(screen.getByTestId('node-schedule')).toHaveAttribute(
      'data-role-state',
      'missing',
    )
    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-writes-allowed',
      'false',
    )
  })

  it('feed_outage: PJM RT LMP stale, B-fallback, price_overlay stale', () => {
    window.history.replaceState({}, '', '/?fixture=feed_outage')
    render(<App />)

    expect(screen.getByTestId('chip-arm-mode')).toHaveTextContent(
      'B-fallback',
    )
    expect(screen.getByTestId('node-price-overlay')).toHaveAttribute(
      'data-role-state',
      'stale',
    )
    expect(screen.getByTestId('decision-flow')).toHaveAttribute(
      'data-writes-allowed',
      'false',
    )
    // Feed-health chip for PJM RT LMP should reflect stale status. The
    // chip is keyed by display name with spaces replaced by spaces in
    // the testid attribute, so use the as-rendered name.
    const stalePjmChip = screen.getByTestId('feed-chip-PJM RT LMP')
    expect(stalePjmChip).toHaveTextContent('3h 14m ago')
  })

  it('mild_day: MILD day type, full evaluation tape', () => {
    window.history.replaceState({}, '', '/?fixture=mild_day')
    render(<App />)

    expect(screen.getByTestId('node-day-type')).toHaveTextContent('MILD')
    expect(screen.getByTestId('node-day-type')).toHaveTextContent(
      'DAY_TYPE_MILD_HIGH_LT_75',
    )
    expect(screen.getByTestId('node-day-type')).toHaveTextContent(
      '5 rules evaluated',
    )
  })

  it('arm_switch: boundary moment with arm B-active', () => {
    window.history.replaceState({}, '', '/?fixture=arm_switch')
    render(<App />)

    expect(screen.getByTestId('chip-arm-mode')).toHaveTextContent('B-active')
    expect(screen.getByTestId('chip-arm-letter')).toHaveTextContent('arm B')
    expect(screen.getByTestId('node-action')).toHaveTextContent('overnight_hold')
  })
})
