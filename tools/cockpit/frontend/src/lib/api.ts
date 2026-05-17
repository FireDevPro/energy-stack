import type { Snapshot } from '../types'

/**
 * Fetch the latest cockpit snapshot from the FastAPI backend.
 *
 * Throws on non-2xx response. Caller (usePolling) handles errors and
 * triggers fixture fallback when configured.
 */
export async function fetchSnapshot(signal?: AbortSignal): Promise<Snapshot> {
  const res = await fetch('/api/snapshot', { signal })
  if (!res.ok) {
    throw new Error(
      `snapshot fetch failed: ${res.status} ${res.statusText}`,
    )
  }
  return (await res.json()) as Snapshot
}
