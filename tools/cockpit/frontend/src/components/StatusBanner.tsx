import type { Snapshot, ArmModeActual, WinnerDetails } from '../types'

// Single plain-English statement answering "what is the controller
// doing right now?" — the line the operator reads first.

const LAYER_PHRASE: Record<WinnerDetails['winning_layer'], string> = {
  schedule: 'Schedule sets the setpoint',
  price_overlay: 'Price overlay overrode the schedule',
  fivecp: '5CP risk overrode the schedule',
  tie: 'Two layers agreed on the warmest setpoint',
}

const MODE_PHRASE: Record<ArmModeActual, string> = {
  'A-active': 'Arm A — thermostat program runs autonomously.',
  'B-active': 'Arm B — controller is pushing setpoints.',
  'B-fallback': 'Arm B — fallback mode, no overlay decisions.',
  'B-down': 'Controller is down. CTK04AE program runs alone.',
  'off-protocol-shadow': 'Off-protocol shadow mode — no writes.',
  'off-protocol-production': 'Off-protocol production — writes outside protocol.',
  'outside-window': 'Outside experiment window — shadow only.',
}

export function StatusBanner({ snapshot }: { snapshot: Snapshot }) {
  const winner = snapshot.flow.winner.details
  const supervisor = snapshot.flow.supervisor.details
  const action = snapshot.flow.action.details
  const armMode = snapshot.arm_mode.mode_actual

  const headline = _composeHeadline(snapshot)
  const subline = _composeSubline({
    winner,
    supervisor,
    action,
    armMode,
  })

  return (
    <div
      data-testid="status-banner"
      className="border-b border-zinc-800 bg-zinc-950 px-6 py-5"
    >
      <div className="font-sans text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
        Right now
      </div>
      <div className="mt-1 font-sans text-2xl font-semibold leading-tight text-zinc-50">
        {headline}
      </div>
      <div className="mt-1 text-sm leading-snug text-zinc-400">{subline}</div>
    </div>
  )
}

function _composeHeadline(snapshot: Snapshot): string {
  const winner = snapshot.flow.winner.details
  const supervisor = snapshot.flow.supervisor.details
  const action = snapshot.flow.action.details
  const armMode = snapshot.arm_mode.mode_actual

  if (!snapshot.controller.alive) {
    return 'Controller is down.'
  }
  if (armMode === 'B-down') {
    return 'Controller down — thermostat program runs alone.'
  }
  if (supervisor.decision === 'emergency') {
    return `Emergency override — cooling to ${supervisor.final_cool_f}°F.`
  }
  if (supervisor.decision === 'clamped') {
    return `Supervisor clamped to ${supervisor.final_cool_f}°F (proposed ${supervisor.proposed_cool_f}°F).`
  }
  if (armMode === 'A-active') {
    return `Cooling to ${winner.effective_cool_f}°F — thermostat program.`
  }
  const verb = action.dry_run ? 'Would cool' : 'Cooling'
  return `${verb} to ${winner.effective_cool_f}°F.`
}

function _composeSubline(args: {
  winner: WinnerDetails
  supervisor: { decision: 'approved' | 'clamped' | 'emergency' | null }
  action: { dry_run: boolean | null; applied: boolean | null }
  armMode: ArmModeActual
}): string {
  const { winner, supervisor, action, armMode } = args
  const parts: string[] = []
  parts.push(LAYER_PHRASE[winner.winning_layer])
  if (winner.changed) {
    parts.push(`up from ${winner.prev_effective_cool_f}°F last tick`)
  }
  if (supervisor.decision !== null && supervisor.decision !== 'approved') {
    // Already in headline; skip duplicating.
  }
  if (action.dry_run === true) {
    parts.push('shadow — no write to thermostat')
  } else if (action.applied === true) {
    parts.push('written to thermostat')
  }
  parts.push(MODE_PHRASE[armMode])
  return parts.join(' · ')
}
