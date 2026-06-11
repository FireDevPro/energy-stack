import { useCallback, useRef, useState } from 'react'

/**
 * Observe an element's content-box size via ResizeObserver.
 *
 * Returns a tuple of `[callbackRef, size]`:
 *   - `callbackRef` attaches the observer when the element mounts and
 *     disconnects when it unmounts. Critical for components that
 *     conditionally render the observed element (loading states,
 *     error fallbacks) — a useRef + useEffect pair would silently
 *     skip the install if the element wasn't in the tree on first
 *     render. See `Cockpit Scaling Audit` issue #00.
 *   - `size` is `{ w, h }` in CSS pixels, initialized to `{0, 0}` and
 *     updated on every observer fire.
 *
 * Usage:
 *   const [chartRef, { w, h }] = useSize()
 *   return <div ref={chartRef}>{w > 0 && <Chart w={w} h={h} />}</div>
 */
export function useSize(): readonly [
  (node: HTMLElement | null) => void,
  { w: number; h: number },
] {
  const [size, setSize] = useState({ w: 0, h: 0 })
  const roRef = useRef<ResizeObserver | null>(null)

  const ref = useCallback((node: HTMLElement | null) => {
    roRef.current?.disconnect()
    roRef.current = null
    if (!node) return
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const cr = e.contentRect
        setSize({ w: cr.width, h: cr.height })
      }
    })
    ro.observe(node)
    roRef.current = ro
  }, [])

  return [ref, size] as const
}
