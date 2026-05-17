import type { Snapshot } from '../types'
import { armTone, modeTone, formatClock, formatDate } from '../lib/tone'

export function Header({ snapshot }: { snapshot: Snapshot }) {
  const arm = snapshot.arm_mode
  return (
    <header className="cockpit-header">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true" />
        <div>
          <div className="brand-name">
            Reticule
            <span className="brand-sub">cockpit · v3</span>
          </div>
        </div>
      </div>

      <div className="h-divider" />

      <span
        data-testid="chip-scheduler-mode"
        className={`chip chip-${modeTone(snapshot.scheduler_mode)} chip-mono`}
      >
        <span className="chip-dot" />
        {snapshot.scheduler_mode}
      </span>

      <span
        data-testid="chip-arm-mode"
        className={`chip chip-${armTone(arm.mode_actual)} chip-mono`}
      >
        <span className="chip-dot" />
        {arm.mode_actual}
      </span>

      {arm.arm && (
        <span
          data-testid="chip-arm-letter"
          className="chip chip-neutral chip-arm-letter"
        >
          ARM · {arm.arm}
        </span>
      )}

      <span
        className={`chip chip-${snapshot.controller.alive ? 'live' : 'danger'}`}
        style={{ paddingLeft: 8 }}
      >
        <span
          data-testid="chip-controller-alive"
          className={`controller-dot ${snapshot.controller.alive ? 'alive' : 'down'}`}
          aria-hidden="true"
        />
        controller {snapshot.controller.alive ? 'alive' : 'down'}
      </span>

      <div className="h-right">
        <div className="h-clock">
          <div>{formatClock(snapshot.snapshot_ts)} CT</div>
          <div className="h-clock-date">{formatDate(snapshot.snapshot_ts)}</div>
        </div>
      </div>
    </header>
  )
}
