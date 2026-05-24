import { useCallback, useMemo } from 'react'
import { loadFixtureFromUrl } from '../lib/loadFixture'
import { shadowCurrent } from '../fixtures/shadow_current'
import { fetchSnapshot } from '../lib/api'
import { usePolling } from '../lib/usePolling'
import { Header } from '../components/Header'
import type { Snapshot } from '../types'
import { HeroPanel } from './components/HeroPanel'
import { DayAtAGlance } from './components/DayAtAGlance'
import { ActionLog } from './components/ActionLog'
import { WhyThisDecision } from './components/WhyThisDecision'
import { DayAheadPanel } from './components/DayAheadPanel'
import { DecisionPipeline } from './components/DecisionPipeline'
import './narrative.css'

const POLL_INTERVAL_MS = 5_000

function shouldUseFixture(): boolean {
  return new URLSearchParams(window.location.search).has('fixture')
}

export default function NarrativeCockpit() {
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
      <div className="narrative" data-testid="narrative-cockpit">
        <Header snapshot={snapshot} />

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

        <main className="narrative-main">
          <HeroPanel snapshot={snapshot} />
          <div className="narrative-center">
            <DayAtAGlance />
            <ActionLog />
          </div>
          <div className="narrative-right">
            <WhyThisDecision snapshot={snapshot} />
            <DayAheadPanel />
          </div>
        </main>

        <div className="narrative-ribbon">
          <DecisionPipeline snapshot={snapshot} />
        </div>
      </div>
    </>
  )
}
