import { type ReactNode, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import type { RoleState, Freshness } from '../../types'

// Direction-A redesign: nodes are typographic blocks, not boxed cards
// embedded in a React Flow canvas. Hierarchy is driven by role_state
// alone — winning lanes get a strong radium left-bar and bright body
// text; losers go zinc + low-emphasis; the eye picks the winner from
// across the room.

const BODY_TONE: Record<RoleState, string> = {
  winning: 'text-zinc-50',
  dimmed: 'text-zinc-500',
  stale: 'text-amber-200',
  missing: 'text-zinc-700',
  not_applicable: 'text-zinc-600',
  clamped: 'text-rose-100',
  emergency: 'text-rose-50',
  context: 'text-zinc-300',
}

const HEADER_TONE: Record<RoleState, string> = {
  winning: 'text-radium-500',
  dimmed: 'text-zinc-500',
  stale: 'text-amber-400',
  missing: 'text-zinc-700',
  not_applicable: 'text-zinc-600',
  clamped: 'text-rose-400',
  emergency: 'text-rose-300',
  context: 'text-zinc-500',
}

const BAR_TONE: Record<RoleState, string> = {
  // The left-bar IS the visual indicator of role. It carries more
  // weight than the body color because it's the first thing the eye
  // resolves at a glance.
  winning: 'bg-radium-500',
  dimmed: 'bg-zinc-800',
  stale: 'bg-amber-500',
  missing: 'bg-zinc-900',
  not_applicable: 'bg-zinc-800',
  clamped: 'bg-rose-500',
  emergency: 'bg-rose-400 motion-safe:animate-pulse',
  context: 'bg-zinc-700',
}

const SURFACE: Record<RoleState, string> = {
  winning: 'bg-zinc-900',
  dimmed: 'bg-transparent',
  stale: 'bg-amber-950/30',
  missing: 'bg-transparent',
  not_applicable: 'bg-transparent',
  clamped: 'bg-rose-950/40',
  emergency: 'bg-rose-900/40',
  context: 'bg-transparent',
}

const FRESHNESS_DOT: Record<Freshness, string> = {
  fresh: 'bg-emerald-400',
  warn: 'bg-amber-400',
  stale: 'bg-rose-500',
  missing: 'bg-zinc-600',
}

export interface BaseNodeProps {
  role_state: RoleState
  freshness: Freshness
  freshness_label: string
  title: string
  headline: string
  children?: ReactNode
  testId: string
  changed?: boolean
}

export function BaseNode({
  role_state,
  freshness,
  freshness_label,
  title,
  headline,
  children,
  testId,
  changed,
}: BaseNodeProps) {
  // Re-fire the pulse animation on role_state transition only.
  const prevRoleRef = useRef(role_state)
  const [pulseKey, setPulseKey] = useState(0)
  useEffect(() => {
    if (prevRoleRef.current !== role_state) {
      setPulseKey((k) => k + 1)
      prevRoleRef.current = role_state
    }
  }, [role_state])

  const flashKeyframes =
    role_state === 'emergency'
      ? {
          boxShadow: [
            '0 0 0 rgba(244,63,94,0)',
            '0 0 28px rgba(244,63,94,0.6)',
            '0 0 0 rgba(244,63,94,0)',
          ],
        }
      : role_state === 'clamped'
        ? {
            boxShadow: [
              '0 0 0 rgba(244,63,94,0)',
              '0 0 16px rgba(244,63,94,0.45)',
              '0 0 0 rgba(244,63,94,0)',
            ],
          }
        : undefined

  return (
    <motion.div
      key={pulseKey}
      data-testid={testId}
      data-role-state={role_state}
      data-changed={changed ? 'true' : 'false'}
      className={`relative grid grid-cols-[3px_1fr] overflow-hidden rounded-sm ${SURFACE[role_state]}`}
      initial={{ scale: 1 }}
      animate={
        flashKeyframes
          ? { scale: [1, 1.02, 1], ...flashKeyframes }
          : { scale: [1, 1.02, 1] }
      }
      transition={{ duration: 0.3, ease: 'easeInOut' }}
    >
      <div className={`${BAR_TONE[role_state]}`} aria-hidden="true" />
      <div className="px-3 py-2.5">
        <div
          className={`font-sans text-[10px] font-semibold uppercase tracking-[0.15em] ${HEADER_TONE[role_state]}`}
        >
          {title}
        </div>
        <div
          className={`mt-1 text-base font-semibold leading-tight ${BODY_TONE[role_state]}`}
        >
          {headline}
        </div>
        {children && (
          <div
            className={`mt-1.5 space-y-0.5 text-[11px] leading-snug ${BODY_TONE[role_state]}`}
          >
            {children}
          </div>
        )}
        <div className="mt-2 flex items-center gap-1.5 text-[10px] text-zinc-500">
          <span
            className={`inline-block h-1 w-1 rounded-full ${FRESHNESS_DOT[freshness]}`}
            aria-hidden="true"
          />
          <span className="font-mono">{freshness_label}</span>
        </div>
      </div>
    </motion.div>
  )
}
