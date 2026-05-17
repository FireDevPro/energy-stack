import { useCallback, useMemo } from 'react'
import { loadFixtureFromUrl } from './lib/loadFixture'
import { shadowCurrent } from './fixtures/shadow_current'
import { fetchSnapshot } from './lib/api'
import { usePolling } from './lib/usePolling'
import { Header } from './components/Header'
import { StatusBanner } from './components/StatusBanner'
import { ThermostatCard } from './components/ThermostatCard'
import { DecisionBoard } from './components/DecisionBoard'
import type { Snapshot } from './types'

const POLL_INTERVAL_MS = 5_000

function shouldUseFixture(): boolean {
  return new URLSearchParams(window.location.search).has('fixture')
}

export default function App() {
  const useFixture = useMemo(() => shouldUseFixture(), [])
  const fixtureSnapshot = useMemo<Snapshot | null>(
    () => (useFixture ? loadFixtureFromUrl() : null),
    [useFixture],
  )

  const fetcher = useCallback(
    (signal: AbortSignal) => fetchSnapshot(signal),
    [],
  )

  // Skip polling entirely when the operator pinned a fixture.
  const polling = usePolling<Snapshot>(fetcher, POLL_INTERVAL_MS, !useFixture)

  // Snapshot resolution priority:
  //   1. URL fixture (explicit operator choice)
  //   2. Live polling data
  //   3. Live error: prefer last good data; fall back to shadow_current
  //      (honest "we don't know" state) if no good data yet
  //   4. Initial loading → null
  const snapshot: Snapshot | null = useFixture
    ? fixtureSnapshot
    : polling.data
      ? polling.data
      : polling.error
        ? shadowCurrent
        : null

  if (!snapshot) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 font-mono text-sm text-zinc-500">
        loading snapshot…
      </div>
    )
  }

  return (
    <div className="flex min-h-screen flex-col bg-zinc-950 text-zinc-100">
      <Header snapshot={snapshot} />
      {!useFixture && polling.error && (
        <div className="border-b border-amber-500/40 bg-amber-500/10 px-6 py-1.5 font-mono text-[11px] uppercase tracking-[0.15em] text-amber-200">
          {polling.data ? (
            <>
              Backend unreachable — showing last successful fetch · last try{' '}
              {polling.lastFetchedAt
                ? new Date(polling.lastFetchedAt).toLocaleTimeString()
                : '—'}
            </>
          ) : (
            <>
              Backend unreachable — showing fallback fixture · last try{' '}
              {polling.lastFetchedAt
                ? new Date(polling.lastFetchedAt).toLocaleTimeString()
                : '—'}
            </>
          )}
        </div>
      )}
      <main className="flex flex-1 overflow-hidden">
        <ThermostatCard snapshot={snapshot} />
        <section className="flex flex-1 flex-col overflow-auto">
          <StatusBanner snapshot={snapshot} />
          <DecisionBoard snapshot={snapshot} />
        </section>
      </main>
    </div>
  )
}
