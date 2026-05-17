import { useLayoutEffect, useState, type RefObject } from 'react'

// Track an element's bounding box via ResizeObserver. Used by the
// decision flow to lay out nodes against the available canvas.
export function useSize(ref: RefObject<HTMLElement | null>): {
  w: number
  h: number
} {
  const [size, setSize] = useState({ w: 0, h: 0 })
  useLayoutEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const cr = e.contentRect
        setSize({ w: cr.width, h: cr.height })
      }
    })
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref])
  return size
}
