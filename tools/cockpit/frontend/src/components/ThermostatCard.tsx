import type { Snapshot } from '../types'
import { ThermostatRing } from './ThermostatRing'
import { PriceChip } from './PriceChip'
import { FeedHealthStrip } from './FeedHealthStrip'

// Direction-A redesign: fixed-width 400px instrument panel. Vertical
// stack — ring → setpoints → mode/fan/RH → price chip → feed health →
// tick footer. No empty bottom; every region carries data.

export function ThermostatCard({ snapshot }: { snapshot: Snapshot }) {
  const t = snapshot.thermostat
  return (
    <aside className="flex w-[400px] shrink-0 flex-col gap-4 overflow-y-auto border-r border-zinc-800 bg-zinc-950 p-5">
      {/* Ring + setpoint readout */}
      <div className="flex flex-col items-center">
        <ThermostatRing
          indoor_f={t.indoor_temp_f}
          cool_f={t.cool_setpoint_f}
          heat_f={t.heat_setpoint_f}
        />
        <div className="mt-3 flex items-baseline gap-6 font-sans">
          <SetpointReadout
            label="Cool"
            value={t.cool_setpoint_f}
            tone="text-sky-300"
            testId="thermostat-cool-setpoint"
          />
          <div className="h-6 w-px bg-zinc-800" />
          <SetpointReadout
            label="Heat"
            value={t.heat_setpoint_f}
            tone="text-rose-300"
          />
        </div>
        <div className="mt-2 font-sans text-[11px] uppercase tracking-[0.18em] text-zinc-500">
          {t.hvac_mode} · {t.fan_mode} fan · {t.indoor_humidity_pct}% RH
        </div>
      </div>

      {/* Price chip — second-largest instrument */}
      <PriceChip price={snapshot.price} />

      {/* Feed health — compact secondary */}
      <FeedHealthStrip snapshot={snapshot} />

      {/* Tick footer at the very bottom */}
      <div
        data-testid="thermostat-tick-footer"
        className="mt-auto border-t border-zinc-800 pt-3 font-mono text-[10px] text-zinc-500"
      >
        tick {snapshot.latest_tick_id} · {snapshot.thermostat.freshness_label}
      </div>
    </aside>
  )
}

function SetpointReadout({
  label,
  value,
  tone,
  testId,
}: {
  label: string
  value: number
  tone: string
  testId?: string
}) {
  return (
    <div className="flex flex-col items-center">
      <div className="font-sans text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
        {label}
      </div>
      <div className="mt-0.5 flex items-baseline gap-1">
        <span
          data-testid={testId}
          className={`font-mono text-2xl font-bold leading-none ${tone}`}
        >
          {value}
        </span>
        <span className="font-mono text-xs text-zinc-500">°F</span>
      </div>
    </div>
  )
}
