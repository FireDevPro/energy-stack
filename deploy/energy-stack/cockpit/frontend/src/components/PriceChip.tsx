import type { Price } from '../types'

// Re-styled to match cockpit-hero.jsx PriceTile: big number split into
// whole/decimal/unit, tier segments bar, three tier labels.
export function PriceChip({ price }: { price: Price }) {
  const tier = price.tier
  const cents = price.current_cents_per_kwh
  const whole = Math.trunc(cents)
  const decFmt = (cents - whole).toFixed(1).slice(1) // ".4"
  return (
    <div
      className="price-tile"
      data-tier={tier}
      data-testid="thermostat-price-chip"
    >
      <div className="price-eyebrow">grid price · {tier}</div>
      <div className="price-row">
        <div className="price-val">
          {whole}
          <span className="price-cents">{decFmt}</span>
          <span className="price-unit">¢/kWh</span>
        </div>
      </div>
      <div className="price-tier-bar">
        <div className="tier-seg normal" />
        <div className="tier-seg elevated" />
        <div className="tier-seg scarcity" />
      </div>
      <div className="price-tier-labels">
        <span data-active={tier === 'normal'}>NORMAL</span>
        <span data-active={tier === 'elevated'}>ELEVATED</span>
        <span data-active={tier === 'scarcity'}>SCARCITY</span>
      </div>
    </div>
  )
}
