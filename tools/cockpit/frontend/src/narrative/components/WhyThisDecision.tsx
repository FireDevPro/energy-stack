import type {
  Snapshot,
  ActionDetails,
  PriceOverlayDetails,
  ScheduleDetails,
  SupervisorDetails,
  WinnerDetails,
} from '../../types'

export function WhyThisDecision({ snapshot }: { snapshot: Snapshot }) {
  return (
    <section
      className="narrative-why"
      data-testid="narrative-why-this-decision"
    >
      <header className="narrative-da-header">
        <div className="narrative-da-title">Why this decision</div>
        <div className="narrative-da-sub">
          arbitration · schedule + price overlay → winner → supervisor
        </div>
      </header>
      <div className="narrative-why-cards">
        <ScheduleCard details={snapshot.flow.schedule.details} />
        <PriceOverlayCard
          details={snapshot.flow.price_overlay.details}
          currentCents={snapshot.price.current_cents_per_kwh}
        />
        <WinnerCard details={snapshot.flow.winner.details} />
        <SupervisorCard details={snapshot.flow.supervisor.details} />
      </div>
      <ActionSentFooter
        details={snapshot.flow.action.details}
        arm={snapshot.arm_mode.arm}
      />
    </section>
  )
}

function ActionSentFooter({
  details,
  arm,
}: {
  details: ActionDetails
  arm: 'A' | 'B' | null
}) {
  const label = details.action_label ?? details.last_fire?.action_label
  const fireTs = details.fire_ts ?? details.last_fire?.fire_ts ?? null
  const applied =
    details.applied ?? details.last_fire?.applied ?? null
  const coolF = details.cool_setpoint_f ?? details.last_fire?.cool_setpoint_f
  const heatF = details.heat_setpoint_f
  const fanMode = details.fan_mode
  if (!label) {
    return (
      <footer
        className="narrative-action-sent"
        data-testid="narrative-action-sent"
        data-applied="false"
      >
        <div className="narrative-action-sent-head">ACTION SENT</div>
        <div className="narrative-why-row">
          <span className="k">status</span>
          <span className="v dim">no action fired yet today</span>
        </div>
      </footer>
    )
  }
  return (
    <footer
      className="narrative-action-sent"
      data-testid="narrative-action-sent"
      data-applied={String(applied)}
    >
      <div className="narrative-action-sent-head">ACTION SENT</div>
      <div className="narrative-action-sent-row">
        <span className="action-name">{label}</span>
        <span
          className={`action-status ${applied ? 'applied' : 'dry-run'}`}
        >
          {applied ? 'applied' : 'dry-run'}
        </span>
        {fireTs && (
          <span className="action-time">{formatFireTs(fireTs)}</span>
        )}
      </div>
      <div className="narrative-action-sent-meta">
        {coolF !== null && coolF !== undefined && (
          <span>
            cool <span className="cool">{coolF}°F</span>
          </span>
        )}
        {heatF !== null && heatF !== undefined && (
          <span>
            heat <span className="ember">{heatF}°F</span>
          </span>
        )}
        {fanMode && <span>fan {fanMode}</span>}
        {arm && <span>arm {arm}</span>}
      </div>
    </footer>
  )
}

function formatFireTs(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function ScheduleCard({ details }: { details: ScheduleDetails }) {
  return (
    <article
      className="narrative-why-card"
      data-testid="why-card-schedule"
      data-card="schedule"
    >
      <header className="narrative-why-card-head">
        <span className="narrative-why-card-title">Schedule</span>
        <span className="narrative-why-card-badge">baseline</span>
      </header>
      <div className="narrative-why-row">
        <span className="k">setpoint</span>
        <span className="v cool">{details.effective_schedule_cool_f}°F</span>
      </div>
      <div className="narrative-why-row">
        <span className="k">action</span>
        <span className="v">{details.action_label}</span>
      </div>
      {details.humid_override_active && (
        <div className="narrative-why-row">
          <span className="k">humid override</span>
          <span className="v warn">
            → {details.humid_override_setpoint_f}°F
          </span>
        </div>
      )}
    </article>
  )
}

function PriceOverlayCard({
  details,
  currentCents,
}: {
  details: PriceOverlayDetails
  currentCents: number
}) {
  const winning = details.outcome === 'upgraded' || details.outcome === 'held'
  return (
    <article
      className="narrative-why-card"
      data-testid="why-card-price-overlay"
      data-card="price-overlay"
      data-tier={details.new_tier}
      data-winning={winning}
    >
      <header className="narrative-why-card-head">
        <span className="narrative-why-card-title">Price overlay</span>
        <span className={`narrative-why-card-badge tier-${details.new_tier}`}>
          {details.new_tier}
        </span>
      </header>
      <div className="narrative-why-row">
        <span className="k">price</span>
        <span className="v">{currentCents.toFixed(1)}¢/kWh</span>
      </div>
      <div className="narrative-why-row">
        <span className="k">outcome</span>
        <span className="v">{details.outcome}</span>
      </div>
      <div className="narrative-why-row">
        <span className="k">reason</span>
        <span className="v small">{details.reason_code}</span>
      </div>
      {details.hold_minutes_remaining !== null &&
        details.hold_minutes_remaining > 0 && (
          <div className="narrative-why-row">
            <span className="k">hold left</span>
            <span className="v">{details.hold_minutes_remaining}m</span>
          </div>
        )}
    </article>
  )
}

function WinnerCard({ details }: { details: WinnerDetails }) {
  const layerLabel =
    details.winning_layer === 'price_overlay' ? 'Price overlay' : 'Schedule'
  return (
    <article
      className="narrative-why-card winner"
      data-testid="why-card-winner"
      data-card="winner"
      data-changed={details.changed}
    >
      <header className="narrative-why-card-head">
        <span className="narrative-why-card-title">Winner</span>
        <span className="narrative-why-card-badge winner">{layerLabel}</span>
      </header>
      <div className="narrative-why-row">
        <span className="k">effective</span>
        <span className="v cool large">{details.effective_cool_f}°F</span>
      </div>
      {details.changed && (
        <div className="narrative-why-row">
          <span className="k">prev</span>
          <span className="v dim">{details.prev_effective_cool_f}°F</span>
        </div>
      )}
      <div className="narrative-why-row">
        <span className="k">reason</span>
        <span className="v small">{details.reason_code}</span>
      </div>
    </article>
  )
}

function SupervisorCard({ details }: { details: SupervisorDetails }) {
  const decision = details.decision ?? 'pending'
  const clamped = decision === 'clamped'
  const emergency = decision === 'emergency'
  return (
    <article
      className={`narrative-why-card supervisor decision-${decision}`}
      data-testid="why-card-supervisor"
      data-card="supervisor"
      data-decision={decision}
    >
      <header className="narrative-why-card-head">
        <span className="narrative-why-card-title">Supervisor</span>
        <span className={`narrative-why-card-badge decision-${decision}`}>
          {decision}
        </span>
      </header>
      {details.proposed_cool_f !== null && details.final_cool_f !== null && (
        <div className="narrative-why-row">
          <span className="k">proposed → final</span>
          <span className="v">
            {details.proposed_cool_f}°F →{' '}
            <span className={clamped || emergency ? 'warn' : 'cool'}>
              {details.final_cool_f}°F
            </span>
          </span>
        </div>
      )}
      {details.supervisor_reason && (
        <div className="narrative-why-row">
          <span className="k">reason</span>
          <span className="v small">{details.supervisor_reason}</span>
        </div>
      )}
    </article>
  )
}
