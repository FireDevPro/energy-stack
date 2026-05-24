import { useCallback, useMemo } from 'react'
import { loadFixtureFromUrl } from './lib/loadFixture'
import { shadowCurrent } from './fixtures/shadow_current'
import { fetchSnapshot } from './lib/api'
import { usePolling } from './lib/usePolling'
import { writesAllowed } from './lib/tone'
import { Header } from './components/Header'
import { FeedHealthStrip } from './components/FeedHealthStrip'
import { ThermostatCard } from './components/ThermostatCard'
import { DecisionBoard } from './components/DecisionBoard'
import { ActionStrip } from './components/ActionStrip'
import type { Snapshot } from './types'

const POLL_INTERVAL_MS = 5_000

function shouldUseFixture(): boolean {
  return new URLSearchParams(window.location.search).has('fixture')
}

export default function ClassicCockpit() {
  const useFixture = useMemo(() => shouldUseFixture(), [])
  const fixtureSnapshot = useMemo<Snapshot | null>(
    () => (useFixture ? loadFixtureFromUrl() : null),
    [useFixture],
  )

  const fetcher = useCallback(
    (signal: AbortSignal) => fetchSnapshot(signal),
    [],
  )

  const polling = usePolling<Snapshot>(fetcher, POLL_INTERVAL_MS, !useFixture)

  const snapshot: Snapshot | null = useFixture
    ? fixtureSnapshot
    : polling.data
      ? polling.data
      : polling.error
        ? shadowCurrent
        : null

  if (!snapshot) {
    return (
      <>
        <div className="ambient" aria-hidden="true" />
        <div
          style={{
            position: 'relative',
            zIndex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: '100vh',
            fontFamily: 'var(--font-mono)',
            fontSize: 13,
            color: 'var(--ink-3)',
            letterSpacing: '0.06em',
          }}
        >
          loading snapshot…
        </div>
      </>
    )
  }

  return (
    <>
      <div className="ambient" aria-hidden="true" />
      <div className="cockpit">
        <Header snapshot={snapshot} />
        <FeedHealthStrip snapshot={snapshot} />

        {!useFixture && polling.error && (
          <div
            style={{
              padding: '6px 18px',
              borderBottom: '1px solid var(--line-soft)',
              background:
                'color-mix(in oklab, var(--warn) 12%, var(--bg-base))',
              color: 'var(--warn)',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
            }}
          >
            {polling.data
              ? `Backend unreachable — showing last successful fetch · last try ${
                  polling.lastFetchedAt
                    ? new Date(polling.lastFetchedAt).toLocaleTimeString()
                    : '—'
                }`
              : `Backend unreachable — showing fallback fixture · last try ${
                  polling.lastFetchedAt
                    ? new Date(polling.lastFetchedAt).toLocaleTimeString()
                    : '—'
                }`}
          </div>
        )}

        <main className="cockpit-main">
          <ThermostatCard snapshot={snapshot} />
          <section className="flow-pane" data-layout="horizontal">
            <div className="flow-toolbar">
              <div>
                <div className="flow-title">Decision flow</div>
                <div className="flow-subtitle">
                  weather → day type → arbitration → supervisor → action
                </div>
              </div>
              <div
                style={{
                  marginLeft: 'auto',
                  display: 'flex',
                  gap: 8,
                  alignItems: 'center',
                }}
              >
                <span
                  className="chip chip-neutral chip-mono"
                  style={{
                    textTransform: 'uppercase',
                    letterSpacing: '0.1em',
                  }}
                >
                  {writesAllowed(snapshot) ? 'writes live' : 'shadow'}
                </span>
              </div>
            </div>
            <DecisionBoard snapshot={snapshot} />
            <ActionStrip snapshot={snapshot} />
          </section>
        </main>
      </div>
    </>
  )
}
