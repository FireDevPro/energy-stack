import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

// jsdom doesn't implement matchMedia. Default to "no-preference" (motion
// allowed); individual tests override with vi.mocked() / Object.defineProperty
// to test the reduced-motion path.
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

// React Flow requires browser APIs that jsdom doesn't ship.

// ResizeObserver: jsdom no-op breaks React Flow because edges only render
// after nodes are marked "measured" via the observer callback. Mock fires
// the callback synchronously with stub dimensions so the internal store
// transitions nodes into the measured state and edges render.
class ResizeObserverMock {
  private cb: ResizeObserverCallback
  private observed = new Set<Element>()
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
  }
  observe(target: Element) {
    this.observed.add(target)
    const entry = {
      target,
      contentRect: {
        width: 200,
        height: 110,
        top: 0,
        left: 0,
        bottom: 110,
        right: 200,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      },
      borderBoxSize: [{ inlineSize: 200, blockSize: 110 }],
      contentBoxSize: [{ inlineSize: 200, blockSize: 110 }],
      devicePixelContentBoxSize: [{ inlineSize: 200, blockSize: 110 }],
    } as unknown as ResizeObserverEntry
    this.cb([entry], this as unknown as ResizeObserver)
  }
  unobserve(target: Element) {
    this.observed.delete(target)
  }
  disconnect() {
    this.observed.clear()
  }
}
;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock })
  .ResizeObserver = ResizeObserverMock

class DOMMatrixReadOnlyMock {
  m22 = 1
  constructor(transform?: string) {
    if (transform) {
      const scale = transform.match(/scale\(([^,)]+)/)
      if (scale) this.m22 = Number(scale[1])
    }
  }
}
;(globalThis as unknown as { DOMMatrixReadOnly: typeof DOMMatrixReadOnlyMock })
  .DOMMatrixReadOnly = DOMMatrixReadOnlyMock

// React Flow's pointer + scroll paths touch these; jsdom no-ops are fine.
HTMLElement.prototype.scrollIntoView = function () {}
;(HTMLElement.prototype as unknown as { releasePointerCapture: () => void })
  .releasePointerCapture = function () {}
;(HTMLElement.prototype as unknown as { hasPointerCapture: () => boolean })
  .hasPointerCapture = function () {
    return false
  }

// React Flow measures node bounds via getBoundingClientRect; jsdom returns
// zeros which breaks edge path math. Stub realistic values.
const originalGetBCR = HTMLElement.prototype.getBoundingClientRect
HTMLElement.prototype.getBoundingClientRect = function () {
  const rect = originalGetBCR.call(this)
  return {
    ...rect,
    width: rect.width || 200,
    height: rect.height || 110,
  } as DOMRect
}
