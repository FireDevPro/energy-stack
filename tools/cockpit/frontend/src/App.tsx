import { useCallback, useMemo } from 'react'
import { loadFixtureFromUrl } from './lib/loadFixture'
import { shadowCurrent } from './fixtures/shadow_current'
import { fetchSnapshot } from './lib/api'
import { usePolling } from './lib/usePolling'
import { Header } from './components/Header'
import { FeedHealthStrip } from './components/FeedHealthStrip'
import { ThermostatCard } from './components/ThermostatCard'
import { DecisionFlow } from './components/DecisionFlow'
import type { Snapshot } from './types'

// Polling cadence for the live backend. Matches the 5-min trace cadence;
// faster won't surface new data. Phase 3 follow-on may tune this once
// the backend is wired against live Influx/Loki.
const POLL_INTERVAL_MS = 5_000

// If the URL has `?fixture=<name>`, the operator wants the fixture path
// — used for offline demo, screenshots, browser smoke. Otherwise the
// app polls the live backend.
function shouldUseFixture(): boolean {
  return new URLSearchParams(window.location.search).has('fixture')
}

export default function App() {
  const useFixture = useMemo(() => shouldUseFixture(), [])
  const fixtureSnapshot = useMemo<Snapshot | null>(
    () => (useFixture ? loadFixtureFromUrl() : null),
    [useFixture],
  )

  // Stable reference for the polling hook so it doesn't re-mount on
  // every render.
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchSnapshot(signal),
    [],
  )

  // Skip polling entirely when the operator pinned a fixture. Avoids
  // pointless network requests + matchers in fixture-mode tests.
  const polling = usePolling<Snapshot>(fetcher, POLL_INTERVAL_MS, !useFixture)

  // Snapshot resolution priority:
  //   1. URL fixture (if `?fixture=` present) — explicit operator choice
  //   2. Live polling data (if non-null)
  //   3. Live error fallback → shadow_current. Its scheduler_mode=shadow
  //      and mode_actual=outside-window honestly represent "we don't
  //      actually know what the controller is doing right now." Falling
  //      back to summer_normal (Arm B-active in July) would be a lie —
  //      it would render a plausible active-control state while the
  //      backend is unreachable.
  //   4. Initial loading → null until first fetch completes
  const snapshot: Snapshot | null = useFixture
    ? fixtureSnapshot
    : polling.data
      ? polling.data
      : polling.error
        ? shadowCurrent
        : null

  if (!snapshot) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
        <div className="font-mono text-sm">loading snapshot…</div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen flex-col bg-zinc-950 text-zinc-100">
      <Header snapshot={snapshot} />
      <FeedHealthStrip snapshot={snapshot} />
      {!useFixture && polling.error && (
        <div className="border-b border-amber-500/40 bg-amber-500/10 px-4 py-1 text-xs text-amber-200">
          {polling.data ? (
            <>
              backend unreachable — showing last successful fetch · last try{' '}
              {polling.lastFetchedAt
                ? new Date(polling.lastFetchedAt).toLocaleTimeString()
                : '—'}
            </>
          ) : (
            <>
              backend unreachable — showing fallback fixture · last try{' '}
              {polling.lastFetchedAt
                ? new Date(polling.lastFetchedAt).toLocaleTimeString()
                : '—'}
            </>
          )}
        </div>
      )}
      <main className="flex flex-1 overflow-hidden">
        <ThermostatCard snapshot={snapshot} />
        <section className="flex-1">
          <DecisionFlow snapshot={snapshot} />
        </section>
      </main>
    </div>
  )
}
