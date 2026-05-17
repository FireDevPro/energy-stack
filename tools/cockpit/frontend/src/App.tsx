import { useMemo } from 'react'
import { loadFixtureFromUrl } from './lib/loadFixture'
import { Header } from './components/Header'
import { FeedHealthStrip } from './components/FeedHealthStrip'
import { ThermostatCard } from './components/ThermostatCard'
import { DecisionFlow } from './components/DecisionFlow'

export default function App() {
  const snapshot = useMemo(() => loadFixtureFromUrl(), [])
  return (
    <div className="flex min-h-screen flex-col bg-zinc-950 text-zinc-100">
      <Header snapshot={snapshot} />
      <FeedHealthStrip snapshot={snapshot} />
      <main className="flex flex-1 overflow-hidden">
        <ThermostatCard snapshot={snapshot} />
        <section className="flex-1">
          <DecisionFlow snapshot={snapshot} />
        </section>
      </main>
    </div>
  )
}
