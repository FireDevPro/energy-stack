import { useEffect } from 'react'
import {
  useMotionValue,
  useTransform,
  useSpring,
  type MotionValue,
} from 'framer-motion'

// Returns a MotionValue that smoothly tracks `target`. Display via
// `useTransform(value, (v) => v.toFixed(decimals))` and bind into a
// `<motion.tspan>` or `<motion.span>`. When MotionConfig has
// `reducedMotion="user"` and the OS requests reduced motion, the spring
// jumps directly to the target without interpolating — count-up
// degrades to instant value-set, which is the correct accessibility
// behavior.
export function useCountUp(target: number, decimals = 1): MotionValue<string> {
  const raw = useMotionValue(target)
  const spring = useSpring(raw, { stiffness: 100, damping: 30 })
  const display = useTransform(spring, (v) => v.toFixed(decimals))
  useEffect(() => {
    raw.set(target)
  }, [target, raw])
  return display
}
