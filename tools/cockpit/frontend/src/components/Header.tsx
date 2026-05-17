import type { Snapshot, ArmModeActual, SchedulerMode } from '../types'

// Thin mission bar — secondary information. The hero numbers belong in
// the thermostat panel and the status banner; the header is just a
// quiet line of context.

function armColor(mode: ArmModeActual): string {
  switch (mode) {
    case 'B-active':
      return 'text-radium-500'
    case 'B-fallback':
      return 'text-amber-300'
    case 'B-down':
      return 'text-rose-400'
    case 'A-active':
      return 'text-zinc-300'
    case 'off-protocol-shadow':
    case 'off-protocol-production':
      return 'text-sky-300'
    case 'outside-window':
      return 'text-zinc-400'
  }
}

function modeColor(mode: SchedulerMode): string {
  if (mode === 'production') return 'text-radium-500'
  if (mode === 'experiment') return 'text-sky-300'
  return 'text-zinc-400'
}

export function Header({ snapshot }: { snapshot: Snapshot }) {
  const time = new Date(snapshot.snapshot_ts).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'America/Chicago',
  })
  const alive = snapshot.controller.alive

  return (
    <header className="flex items-center gap-6 border-b border-zinc-800 bg-zinc-950 px-6 py-2.5 font-sans text-[11px] uppercase tracking-[0.15em]">
      <div className="font-semibold text-zinc-100">Controller Cockpit</div>

      <Divider />

      <div className="flex items-center gap-2">
        <span className="text-zinc-600">MODE</span>
        <span
          data-testid="chip-scheduler-mode"
          className={`font-semibold ${modeColor(snapshot.scheduler_mode)}`}
        >
          {snapshot.scheduler_mode}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-zinc-600">ARM</span>
        <span
          data-testid="chip-arm-mode"
          className={`font-semibold ${armColor(snapshot.arm_mode.mode_actual)}`}
        >
          {snapshot.arm_mode.mode_actual}
        </span>
        {snapshot.arm_mode.arm && (
          <span
            data-testid="chip-arm-letter"
            className="font-mono text-zinc-500"
          >
            arm {snapshot.arm_mode.arm}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <span className="text-zinc-600">CTRL</span>
        <span
          data-testid="chip-controller-alive"
          className={`inline-block h-1.5 w-1.5 rounded-full ${alive ? 'bg-radium-500' : 'bg-rose-500'}`}
          title={alive ? 'controller alive' : 'controller down'}
        />
        <span
          className={`font-semibold ${alive ? 'text-zinc-300' : 'text-rose-400'}`}
        >
          {alive ? 'alive' : 'down'}
        </span>
      </div>

      <div className="ml-auto font-mono normal-case tracking-normal text-zinc-400">
        {time} CT
      </div>
    </header>
  )
}

function Divider() {
  return <div className="h-3 w-px bg-zinc-800" aria-hidden="true" />
}
