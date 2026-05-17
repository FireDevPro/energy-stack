import type { ArmModeActual, SchedulerMode, Snapshot, WinnerDetails } from '../types'

// Chip tone keys used by the reference design — map to .chip-{tone} CSS.
export type Tone =
  | 'live'
  | 'ember'
  | 'ice'
  | 'warn'
  | 'danger'
  | 'neutral'
  | 'violet'

export function armTone(mode: ArmModeActual): Tone {
  switch (mode) {
    case 'B-active':
      return 'live'
    case 'B-fallback':
      return 'warn'
    case 'B-down':
      return 'danger'
    case 'A-active':
      return 'neutral'
    case 'off-protocol-shadow':
    case 'off-protocol-production':
      return 'ice'
    case 'outside-window':
      return 'neutral'
  }
}

export function modeTone(mode: SchedulerMode): Tone {
  if (mode === 'production') return 'live'
  if (mode === 'experiment') return 'ice'
  return 'violet'
}

export function writesAllowed(snapshot: Snapshot): boolean {
  return (
    snapshot.scheduler_mode === 'production' ||
    (snapshot.scheduler_mode === 'experiment' &&
      snapshot.arm_mode.mode_actual === 'B-active')
  )
}

export function winningLanes(
  layer: WinnerDetails['winning_layer'],
): Set<string> {
  if (layer === 'tie')
    return new Set(['schedule', 'price_overlay', 'fivecp'])
  if (layer === 'price_overlay') return new Set(['price_overlay'])
  if (layer === 'fivecp') return new Set(['fivecp'])
  return new Set(['schedule'])
}

export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: '2-digit',
  })
}
