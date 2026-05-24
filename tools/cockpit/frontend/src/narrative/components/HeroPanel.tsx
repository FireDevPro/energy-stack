import { useLayoutEffect, useRef, useState } from 'react'
import type { Snapshot } from '../../types'
import { ThermostatRing } from '../../components/ThermostatRing'
import { PriceChip } from '../../components/PriceChip'

export function HeroPanel({ snapshot }: { snapshot: Snapshot }) {
  return (
    <aside className="hero" data-testid="narrative-hero">
      <ThermostatHero snapshot={snapshot} />
      <PriceChip price={snapshot.price} />
      <HeroContext snapshot={snapshot} />
    </aside>
  )
}

function ThermostatHero({ snapshot }: { snapshot: Snapshot }) {
  const t = snapshot.thermostat
  const wrapRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState(300)

  useLayoutEffect(() => {
    if (!wrapRef.current) return
    const ro = new ResizeObserver(([e]) => {
      const w = Math.min(e.contentRect.width, 480)
      setSize(Math.max(240, w))
    })
    ro.observe(wrapRef.current)
    return () => ro.disconnect()
  }, [])

  const whole = Math.trunc(t.indoor_temp_f)
  const dec = (Math.abs(t.indoor_temp_f - whole) * 10).toFixed(0)

  return (
    <>
      <div
        className="hero-ring-wrap"
        ref={wrapRef}
        style={{ minHeight: size }}
      >
        <ThermostatRing
          size={size}
          indoor_f={t.indoor_temp_f}
          cool_f={t.cool_setpoint_f}
          heat_f={t.heat_setpoint_f}
        />
        <div className="indoor-readout">
          <div className="indoor-eyebrow">indoor · now</div>
          <div
            className="indoor-temp"
            data-testid="narrative-indoor-temp"
          >
            {whole}
            <span className="indoor-temp-dec">.{dec}</span>
            <span className="indoor-temp-unit">°F</span>
          </div>
          <div className="indoor-hum">
            {t.indoor_humidity_pct}% RH · {t.hvac_mode} · {t.fan_mode} fan
          </div>
        </div>
      </div>

      <div className="setpoint-row">
        <div className="sp-tile cool">
          <div className="sp-eyebrow">
            <span className="sp-dot" />
            cool
          </div>
          <div className="sp-val">
            <span data-testid="narrative-cool-setpoint">
              {t.cool_setpoint_f}
            </span>
            <span className="sp-unit">°F</span>
          </div>
          <div className="sp-meta">setpoint · {t.freshness_label}</div>
        </div>
        <div className="sp-tile heat">
          <div className="sp-eyebrow">
            <span className="sp-dot" />
            heat
          </div>
          <div className="sp-val">
            {t.heat_setpoint_f}
            <span className="sp-unit">°F</span>
          </div>
          <div className="sp-meta">setpoint</div>
        </div>
      </div>
    </>
  )
}

function HeroContext({ snapshot }: { snapshot: Snapshot }) {
  const w = snapshot.flow.weather.details
  const d = snapshot.flow.day_type.details
  return (
    <div className="hero-context" data-testid="narrative-hero-context">
      <div className="hero-context-row">
        <span className="k">outdoor</span>
        <span>
          {w.current_outdoor_f}°F · high {w.today_high_f}°F
          {w.heat_advisory ? ' · advisory' : ''}
        </span>
      </div>
      <div className="hero-context-row">
        <span className="k">day type</span>
        <span>{d.winning_day_type}</span>
      </div>
      <div className="hero-context-row">
        <span className="k">tick</span>
        <span>{snapshot.latest_tick_id}</span>
      </div>
    </div>
  )
}
