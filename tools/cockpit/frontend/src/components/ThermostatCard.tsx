import type { Snapshot } from '../types'
import { ThermostatRing } from './ThermostatRing'
import { PriceChip } from './PriceChip'

export function ThermostatCard({ snapshot }: { snapshot: Snapshot }) {
  const t = snapshot.thermostat
  return (
    <aside className="flex w-[30%] min-w-[360px] flex-col gap-4 border-r border-zinc-800 bg-zinc-900/40 p-4">
      <div className="flex flex-col items-center">
        <ThermostatRing
          indoor_f={t.indoor_temp_f}
          cool_f={t.cool_setpoint_f}
          heat_f={t.heat_setpoint_f}
        />
        <div className="mt-1 font-mono text-sm text-zinc-300">
          cool{' '}
          <span
            data-testid="thermostat-cool-setpoint"
            className="text-sky-300"
          >
            {t.cool_setpoint_f}
          </span>
          °F
          <span className="px-2 text-zinc-600">·</span>
          heat <span className="text-rose-300">{t.heat_setpoint_f}</span>°F
        </div>
        <div className="mt-1 text-xs text-zinc-400">
          {t.hvac_mode} · {t.fan_mode} fan · {t.indoor_humidity_pct}% RH
        </div>
      </div>

      <PriceChip price={snapshot.price} />

      <div className="text-xs text-zinc-400">
        <span className="uppercase tracking-wide text-zinc-500">scheduler:</span>{' '}
        {snapshot.scheduler_mode} · {snapshot.arm_mode.mode_actual}
      </div>

      <div
        data-testid="thermostat-tick-footer"
        className="mt-auto border-t border-zinc-800 pt-2 font-mono text-[10px] text-zinc-500"
      >
        tick {snapshot.latest_tick_id} · {snapshot.thermostat.freshness_label}
      </div>
    </aside>
  )
}
