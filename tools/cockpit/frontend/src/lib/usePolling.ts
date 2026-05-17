import { useEffect, useRef, useState } from 'react'

export interface PollingState<T> {
  data: T | null
  loading: boolean
  error: Error | null
  lastFetchedAt: number | null
}

/**
 * Polling hook for a fetch function. Calls `fetchFn` immediately then
 * every `intervalMs`. Aborts any in-flight request before issuing a
 * new one. Cleans up on unmount.
 *
 * Pass `enabled: false` to skip polling entirely (e.g., when the
 * operator pinned the app to a fixture via URL param). The hook still
 * returns a stable PollingState; loading is set to false to indicate
 * "intentionally not fetching."
 *
 * Errors surface via `state.error` and `state.lastFetchedAt`; the
 * caller decides whether to fall back to a fixture.
 */
export function usePolling<T>(
  fetchFn: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  enabled: boolean = true,
): PollingState<T> {
  const [state, setState] = useState<PollingState<T>>({
    data: null,
    loading: enabled,
    error: null,
    lastFetchedAt: null,
  })
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!enabled) {
      // Make sure any in-flight request from a previous enabled cycle
      // is cancelled.
      abortRef.current?.abort()
      setState((prev) => ({ ...prev, loading: false }))
      return
    }

    let cancelled = false

    const tick = async () => {
      if (cancelled) return
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl
      try {
        const data = await fetchFn(ctrl.signal)
        if (cancelled || ctrl.signal.aborted) return
        setState({
          data,
          loading: false,
          error: null,
          lastFetchedAt: Date.now(),
        })
      } catch (err) {
        if (cancelled || ctrl.signal.aborted) return
        setState((prev) => ({
          ...prev,
          loading: false,
          error: err instanceof Error ? err : new Error(String(err)),
          lastFetchedAt: Date.now(),
        }))
      }
    }

    tick()
    const handle = window.setInterval(tick, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(handle)
      abortRef.current?.abort()
    }
  }, [fetchFn, intervalMs, enabled])

  return state
}
