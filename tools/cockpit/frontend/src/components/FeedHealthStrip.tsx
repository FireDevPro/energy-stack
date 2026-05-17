import type { FeedHealthEntry, Snapshot, Freshness } from '../types'

// Compact two-column feed-health list. Lives inside the thermostat
// panel — secondary information, never the eye's first stop.

const DOT: Record<Freshness, string> = {
  fresh: 'bg-emerald-400 motion-safe:animate-pulse-slow',
  warn: 'bg-amber-400',
  stale: 'bg-rose-500',
  missing: 'bg-zinc-700',
}

const LABEL: Record<Freshness, string> = {
  fresh: 'text-zinc-400',
  warn: 'text-amber-300',
  stale: 'text-rose-300',
  missing: 'text-zinc-600',
}

function FeedRow({ entry }: { entry: FeedHealthEntry }) {
  return (
    <div
      data-testid={`feed-chip-${entry.name}`}
      className="flex items-center gap-2 py-0.5 text-[11px]"
    >
      <span
        className={`inline-block h-1 w-1 rounded-full ${DOT[entry.status]}`}
        aria-hidden="true"
      />
      <span className="text-zinc-300">{entry.name}</span>
      <span className={`ml-auto font-mono ${LABEL[entry.status]}`}>
        {entry.label}
      </span>
    </div>
  )
}

export function FeedHealthStrip({ snapshot }: { snapshot: Snapshot }) {
  return (
    <div data-testid="feed-health-strip" className="grid grid-cols-1 gap-y-0.5">
      <div className="mb-1 font-sans text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
        Feed Health
      </div>
      {snapshot.feed_health.map((f) => (
        <FeedRow key={f.name} entry={f} />
      ))}
    </div>
  )
}
