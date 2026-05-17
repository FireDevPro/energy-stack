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
      // Header chips render from the fallback fixture.
      expect(screen.getByTestId('chip-scheduler-mode')).toBeInTheDocument()
    })

    // Banner text indicates the live backend is unreachable.
    expect(screen.getByText(/backend unreachable/i)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalled()
  })
})
