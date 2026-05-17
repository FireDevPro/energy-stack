import { type ReactNode, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Handle, Position } from '@xyflow/react'
import type { RoleState, Freshness } from '../../types'

const BODY_BG: Record<RoleState, string> = {
  winning: 'bg-zinc-900',
  dimmed: 'bg-zinc-900/60',
  stale: 'bg-zinc-900/40',
  missing: 'bg-zinc-900/20',
  not_applicable: 'bg-zinc-900/30',
  clamped: 'bg-rose-900/40',
  emergency: 'bg-rose-700/50',
  context: 'bg-zinc-900/40',
}

const BODY_BORDER: Record<RoleState, string> = {
  winning: 'border-zinc-700',
  dimmed: 'border-zinc-800',
  stale: 'border-amber-500/40',
  missing: 'border-zinc-800',
  not_applicable: 'border-zinc-800',
  clamped: 'border-rose-500',
  emergency: 'border-rose-400 motion-safe:animate-pulse',
  context: 'border-zinc-800',
}

const TEXT: Record<RoleState, string> = {
  winning: 'text-zinc-100',
  dimmed: 'text-zinc-400',
  stale: 'text-amber-200',
  missing: 'text-zinc-600',
  not_applicable: 'text-zinc-500',
  clamped: 'text-rose-100',
  emergency: 'text-rose-50',
  context: 'text-zinc-400',
}

const FRESHNESS_DOT: Record<Freshness, string> = {
  fresh: 'bg-emerald-400',
  warn: 'bg-amber-400',
  stale: 'bg-rose-500',
  missing: 'bg-zinc-600',
}

export interface BaseNodeProps {
  nodeId: string
  role_state: RoleState
  freshness: Freshness
  freshness_label: string
  title: string
  subtitle: string
  children?: ReactNode
  testId: string
  changed?: boolean
}

export function BaseNode({
  nodeId,
  role_state,
  freshness,
  freshness_label,
  title,
  subtitle,
  children,
  testId,
  changed,
}: BaseNodeProps) {
  // Re-fire the pulse animation on role_state transition. Bumping `pulseKey`
  // remounts the motion component, which re-runs `animate` from `initial`.
  const prevRoleRef = useRef(role_state)
  const [pulseKey, setPulseKey] = useState(0)
  useEffect(() => {
    if (prevRoleRef.current !== role_state) {
      setPulseKey((k) => k + 1)
      prevRoleRef.current = role_state
    }
  }, [role_state])

  const ring =
    role_state === 'winning'
      ? 'ring-2 ring-sky-400/80 shadow-[0_0_24px_rgba(56,189,248,0.35)]'
      : ''

  // Supervisor clamped + emergency: one-shot red-flash transition on top of
  // the persistent border-rose styling. Driven by an `animate` keyframe
  // sequence; gated automatically by MotionConfig reducedMotion.
  const flashKeyframes =
    role_state === 'emergency'
      ? {
          boxShadow: [
            '0 0 0 rgba(244,63,94,0)',
            '0 0 32px rgba(244,63,94,0.7)',
            '0 0 0 rgba(244,63,94,0)',
          ],
        }
      : role_state === 'clamped'
        ? {
            boxShadow: [
              '0 0 0 rgba(244,63,94,0)',
              '0 0 18px rgba(244,63,94,0.5)',
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
      className={`relative w-[200px] rounded-lg border p-3 ${BODY_BG[role_state]} ${BODY_BORDER[role_state]} ${ring}`}
      initial={{ scale: 1 }}
      animate={
        flashKeyframes
          ? { scale: [1, 1.04, 1], ...flashKeyframes }
          : { scale: [1, 1.04, 1] }
      }
      transition={{ duration: 0.3, ease: 'easeInOut' }}
    >
      <Handle
        id={`${nodeId}-in`}
        type="target"
        position={Position.Left}
        className="!bg-zinc-700"
      />
      <Handle
        id={`${nodeId}-out`}
        type="source"
        position={Position.Right}
        className="!bg-zinc-700"
      />

      <div
        className={`text-[11px] uppercase tracking-wider ${TEXT[role_state]}`}
      >
        {title}
      </div>
      <div className={`mt-0.5 text-sm font-medium ${TEXT[role_state]}`}>
        {subtitle}
      </div>
      {children && (
        <div className={`mt-2 space-y-1 text-xs ${TEXT[role_state]}`}>
          {children}
        </div>
      )}
      <div className="mt-2 flex items-center gap-1.5 text-[10px] text-zinc-500">
        <span
          className={`inline-block h-1 w-1 rounded-full ${FRESHNESS_DOT[freshness]}`}
        />
        <span className="font-mono">{freshness_label}</span>
      </div>
    </motion.div>
  )
}
