import '@testing-library/jest-dom/vitest'

// React Flow requires browser APIs that jsdom doesn't ship.

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
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
    height: rect.height || 80,
  } as DOMRect
}
