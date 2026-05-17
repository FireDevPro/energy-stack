import { Chip, type ChipTone } from './chips/Chip'
import type { Snapshot, ArmModeActual, SchedulerMode } from '../types'

function armTone(mode: ArmModeActual): ChipTone {
  switch (mode) {
    case 'B-active':
      return 'emerald'
    case 'B-fallback':
      return 'amber'
    case 'B-down':
      return 'rose'
    case 'A-active':
      return 'zinc'
    case 'off-protocol-shadow':
      return 'sky'
    case 'off-protocol-production':
      return 'sky'
    case 'outside-window':
      return 'neutral'
  }
}

function modeTone(mode: SchedulerMode): ChipTone {
  if (mode === 'production') return 'emerald'
  if (mode === 'experiment') return 'sky'
  return 'neutral'
}

export function Header({ snapshot }: { snapshot: Snapshot }) {
  const time = new Date(snapshot.snapshot_ts).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  return (
    <header className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-950 px-4 py-2">
      <Chip
        tone={modeTone(snapshot.scheduler_mode)}
        testId="chip-scheduler-mode"
      >
        {snapshot.scheduler_mode}
      </Chip>
      <Chip
        tone={armTone(snapshot.arm_mode.mode_actual)}
        testId="chip-arm-mode"
      >
        {snapshot.arm_mode.mode_actual}
      </Chip>
      {snapshot.arm_mode.arm && (
        <Chip tone="zinc" testId="chip-arm-letter">
          arm {snapshot.arm_mode.arm}
        </Chip>
      )}
      <span
        data-testid="chip-controller-alive"
        className={`inline-block h-2 w-2 rounded-full ${snapshot.controller.alive ? 'bg-emerald-400' : 'bg-rose-500'}`}
        title={snapshot.controller.alive ? 'controller alive' : 'controller down'}
      />
      <span className="ml-auto font-mono text-xs text-zinc-400">{time}</span>
    </header>
  )
}
