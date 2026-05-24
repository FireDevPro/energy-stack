import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'

describe('Narrative cockpit (v2) skeleton — PR 1', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/narrative?fixture=summer_normal')
  })

  it('renders the narrative shell on /narrative, not the classic cockpit', () => {
    render(<App />)
    expect(screen.getByTestId('narrative-cockpit')).toBeInTheDocument()
    // Classic cockpit ships ThermostatCard with this testid; absence
    // proves the route shim picked the narrative view.
    expect(screen.queryByTestId('thermostat-indoor-temp')).toBeNull()
  })

  it('hero panel renders real snapshot data (indoor, setpoint, RTP)', () => {
    render(<App />)
    expect(screen.getByTestId('narrative-hero')).toBeInTheDocument()
    expect(screen.getByTestId('narrative-indoor-temp')).toHaveTextContent('74')
    expect(screen.getByTestId('narrative-cool-setpoint')).toHaveTextContent(
      '76',
    )
    expect(screen.getByTestId('thermostat-price-chip')).toHaveTextContent('8.4')
    expect(screen.getByTestId('thermostat-price-chip')).toHaveAttribute(
      'data-tier',
      'normal',
    )
    expect(screen.getByTestId('narrative-hero-context')).toHaveTextContent(
      'NORMAL',
    )
  })

  it('placeholder panels render with their semantic test ids', () => {
    render(<App />)
    expect(screen.getByTestId('narrative-day-at-a-glance')).toBeInTheDocument()
    expect(screen.getByTestId('narrative-action-log')).toBeInTheDocument()
    expect(screen.getByTestId('narrative-why-this-decision')).toBeInTheDocument()
    expect(screen.getByTestId('narrative-decision-pipeline')).toBeInTheDocument()
  })

  it('header chips (scheduler mode, arm, controller) still render', () => {
    render(<App />)
    expect(screen.getByTestId('chip-scheduler-mode')).toHaveTextContent(
      'experiment',
    )
    expect(screen.getByTestId('chip-arm-mode')).toHaveTextContent('B-active')
    expect(screen.getByTestId('chip-controller-alive')).toBeInTheDocument()
  })
})

describe('Route shim — / still loads classic cockpit', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/?fixture=summer_normal')
  })

  it('renders classic cockpit at /, not narrative', () => {
    render(<App />)
    // Classic ThermostatCard testid present; narrative shell absent.
    expect(screen.getByTestId('thermostat-indoor-temp')).toBeInTheDocument()
    expect(screen.queryByTestId('narrative-cockpit')).toBeNull()
  })
})
