import type {
  Snapshot,
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
    </section>
  )
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
