import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

// jsdom doesn't implement matchMedia. Default to "no-preference" (motion
// allowed); individual tests override with vi.fn() to test the reduced-
// motion path.
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((q: string) => ({
      matches: false,
      media: q,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

// jsdom (vitest 4 + jsdom 29) doesn't implement ResizeObserver. ThermostatRing
// (and the narrative HeroPanel) use ResizeObserver to size the SVG against
// container width. Stub it so component mounts don't throw.
if (typeof globalThis.ResizeObserver === 'undefined') {
  class StubResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = StubResizeObserver as unknown as typeof ResizeObserver
}
