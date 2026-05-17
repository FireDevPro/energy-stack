import type { Price, PriceTier } from '../types'

const TIER_RING: Record<PriceTier, string> = {
  normal: 'ring-emerald-500/40 bg-emerald-500/8',
  elevated: 'ring-amber-400/50 bg-amber-400/10',
  scarcity: 'ring-rose-500/60 bg-rose-500/12',
}

const TIER_TEXT: Record<PriceTier, string> = {
  normal: 'text-emerald-200',
  elevated: 'text-amber-200',
  scarcity: 'text-rose-200',
}

const TIER_PULSE: Record<PriceTier, string> = {
  normal: '',
  elevated: 'motion-safe:animate-pulse-slow',
  scarcity: 'motion-safe:animate-pulse',
}

export function PriceChip({ price }: { price: Price }) {
  return (
    <div
      data-testid="thermostat-price-chip"
      data-tier={price.tier}
      className={`rounded-md ring-1 ${TIER_RING[price.tier]} ${TIER_PULSE[price.tier]} p-4`}
    >
      <div className="font-sans text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
        ComEd 5-min
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span
          className={`font-mono text-3xl font-bold leading-none ${TIER_TEXT[price.tier]}`}
        >
          {price.current_cents_per_kwh.toFixed(1)}
        </span>
        <span className="font-mono text-sm text-zinc-400">¢/kWh</span>
      </div>
      <div
        className={`mt-1 font-sans text-[10px] uppercase tracking-[0.2em] ${TIER_TEXT[price.tier]}`}
      >
        {price.tier} tier · {price.freshness_label}
      </div>
    </div>
  )
}
