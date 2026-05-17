import type { Price, PriceTier } from '../types'

const TIER_BG: Record<PriceTier, string> = {
  normal: 'bg-emerald-500/15 border-emerald-500/40',
  elevated: 'bg-amber-400/20 border-amber-400/50',
  scarcity: 'bg-rose-500/25 border-rose-500/60',
}

const TIER_TEXT: Record<PriceTier, string> = {
  normal: 'text-emerald-200',
  elevated: 'text-amber-100',
  scarcity: 'text-rose-100',
}

const TIER_PULSE: Record<PriceTier, string> = {
  // `motion-safe:` gates each pulse on prefers-reduced-motion: no-preference.
  // Tier color still applies via TIER_BG; only the pulse animation drops.
  normal: 'motion-safe:animate-pulse-slow',
  elevated: 'motion-safe:animate-pulse-slow',
  scarcity: 'motion-safe:animate-pulse',
}

export function PriceChip({ price }: { price: Price }) {
  return (
    <div
      data-testid="thermostat-price-chip"
      data-tier={price.tier}
      className={`rounded-lg border px-4 py-3 ${TIER_BG[price.tier]} ${TIER_PULSE[price.tier]}`}
    >
      <div className={`font-mono text-3xl font-bold ${TIER_TEXT[price.tier]}`}>
        {price.current_cents_per_kwh.toFixed(1)}{' '}
        <span className="text-base font-medium">¢/kWh</span>
      </div>
      <div
        className={`text-xs uppercase tracking-wide ${TIER_TEXT[price.tier]}`}
      >
        {price.tier} tier · {price.freshness_label}
      </div>
    </div>
  )
}
