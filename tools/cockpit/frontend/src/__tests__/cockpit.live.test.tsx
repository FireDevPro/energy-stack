import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import App from '../App'
import { summerNormal } from '../fixtures/summer_normal'

// Phase 3 live-fetch tests. Mocks fetch('/api/snapshot') to return a
// canned Snapshot dict (sourced from the TS summer_normal fixture so
// drift between frontend Snapshot interface and live wire format
// surfaces here). Verifies:
//   - The polling hook drives the initial render from the mocked fetch.
//   - When fetch fails, App falls back to a fixture and shows the
//     "backend unreachable" banner.

describe('Cockpit Phase 3 live fetch path', () => {
  beforeEach(() => {
    // No ?fixture= param — App uses the live polling path.
    window.history.replaceState({}, '', '/')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders live snapshot from mocked fetch', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => summerNormal,
    } as Response)
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    // Initial render shows loading state until first fetch resolves.
    await waitFor(() => {
      expect(screen.getByTestId('chip-scheduler-mode')).toHaveTextContent(
        'experiment',
      )
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/snapshot',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(screen.getByTestId('thermostat-indoor-temp')).toHaveTextContent(
      '74.8',
    )
  })

  it('falls back to fixture and shows banner when fetch fails', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('ECONNREFUSED'))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await waitFor(() => {
      // Header chips render from the fallback fixture (shadow_current).
      expect(screen.getByTestId('chip-scheduler-mode')).toHaveTextContent(
        'shadow',
      )
    })

    // Banner says fallback fixture (because polling.data never populated).
    expect(
      screen.getByText(/showing fallback fixture/i),
    ).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalled()
  })

  it('polling hook re-fires on the configured interval', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => summerNormal,
    } as Response)
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    // Initial mount fires fetch(es). StrictMode in main.tsx may double-
    // invoke effects, so we only assert that the count is at least one.
    await vi.runOnlyPendingTimersAsync()
    const initialCount = fetchMock.mock.calls.length
    expect(initialCount).toBeGreaterThanOrEqual(1)

    // Advance past the 5s polling interval and verify another fetch
    // fired beyond the initial count.
    await vi.advanceTimersByTimeAsync(5_100)
    expect(fetchMock.mock.calls.length).toBeGreaterThan(initialCount)

    const afterFirstTick = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(5_100)
    expect(fetchMock.mock.calls.length).toBeGreaterThan(afterFirstTick)

    vi.useRealTimers()
  })
})
