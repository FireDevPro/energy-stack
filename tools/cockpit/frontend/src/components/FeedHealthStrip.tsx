import type { Snapshot } from '../types'

export function FeedHealthStrip({ snapshot }: { snapshot: Snapshot }) {
  return (
    <div className="feed-strip" data-testid="feed-health-strip">
      <span className="feed-strip-label">feeds</span>
      {snapshot.feed_health.map((f) => (
        <span
          key={f.name}
          className="feed-chip"
          data-status={f.status}
          data-testid={`feed-chip-${f.name}`}
        >
          <span className="feed-dot" data-status={f.status} />
          <span className="feed-name">{f.name}</span>
          <span className="feed-age">{f.label}</span>
        </span>
      ))}
    </div>
  )
}
