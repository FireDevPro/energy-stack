import { useMemo } from 'react'
import ClassicCockpit from './ClassicCockpit'
import NarrativeCockpit from './narrative/NarrativeCockpit'

function selectView(pathname: string): 'narrative' | 'classic' {
  return pathname.startsWith('/narrative') ? 'narrative' : 'classic'
}

export default function App() {
  const view = useMemo(() => selectView(window.location.pathname), [])
  return view === 'narrative' ? <NarrativeCockpit /> : <ClassicCockpit />
}
