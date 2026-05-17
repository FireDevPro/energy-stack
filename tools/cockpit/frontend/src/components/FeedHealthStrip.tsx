import type { FeedHealthEntry, Snapshot, Freshness } from '../types'

const DOT: Record<Freshness, string> = {
  // `motion-safe:` gates the pulse on prefers-reduced-motion: no-preference.
  // Static color renders for users who request reduced motion.
  fresh: 'bg-emerald-400 motion-safe:animate-pulse-slow',
  warn: 'bg-amber-400',
  stale: 'bg-rose-500',
  missing: 'bg-zinc-600',
}

const TEXT: Record<Freshness, string> = {
  fresh: 'text-zinc-300',
  warn: 'text-amber-300',
  stale: 'text-rose-300',
  missing: 'text-zinc-500',
}

function FeedChip({ entry }: { entry: FeedHealthEntry }) {
  return (
    <span
      data-testid={`feed-chip-${entry.name}`}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs"
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${DOT[entry.status]}`}
      />
      <span className="font-medium text-zinc-100">{entry.name}</span>
      <span className={`font-mono ${TEXT[entry.status]}`}>{entry.label}</span>
    </span>
  )
}

export function FeedHealthStrip({ snapshot }: { snapshot: Snapshot }) {
  return (
    <div
      data-testid="feed-health-strip"
      className="flex flex-wrap items-center gap-2 border-b border-zinc-800 bg-zinc-950 px-4 py-1.5"
    >
      {snapshot.feed_health.map((f) => (
        <FeedChip key={f.name} entry={f} />
      ))}
    </div>
  )
}
