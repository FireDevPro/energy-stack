import { type ReactNode } from 'react'
import type { RoleState, Freshness } from '../../types'

// Absolute-positioned node card, styled by the .node class from
// index.css. Receives a center coordinate (from the layout fn) and
// renders itself offset by half its width/height.

export interface BaseNodeProps {
  id: string
  testId: string
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
  role_state: RoleState
  freshness: Freshness
  freshness_label: string
  title: string
  subtitle: string
  changed?: boolean
  children?: ReactNode
}

function badgeText(role: RoleState, freshness: Freshness): string {
  if (role === 'winning') return 'WINNING'
  if (role === 'clamped') return 'CLAMPED'
  if (role === 'emergency') return 'EMERGENCY'
  if (role === 'missing') return 'NO TRACE'
  if (role === 'not_applicable') return 'N/A'
  if (freshness === 'stale') return 'STALE'
  return 'CONTEXT'
}

export function BaseNode({
  id,
  testId,
  pos,
  nodeW,
  nodeH,
  role_state,
  freshness,
  freshness_label,
  title,
  subtitle,
  changed,
  children,
}: BaseNodeProps) {
  return (
    <div
      className="node"
      data-id={id}
      data-role={role_state}
      data-role-state={role_state}
      data-changed={changed ? 'true' : 'false'}
      data-testid={testId}
      style={{
        left: pos.x - nodeW / 2,
        top: pos.y - nodeH / 2,
        width: nodeW,
        minHeight: nodeH,
      }}
    >
      <div className="node-head">
        <div className="node-title">{title}</div>
        <div
          className="node-badge"
          data-state={freshness === 'fresh' ? 'fresh' : freshness}
        >
          {badgeText(role_state, freshness)}
        </div>
      </div>
      <div className="node-sub">{subtitle}</div>
      {children}
      <div className="node-foot">{freshness_label}</div>
    </div>
  )
}

export function Stat({
  k,
  v,
  tone,
}: {
  k: string
  v: string
  tone?: 'cool' | 'heat' | 'live' | 'warn' | 'danger' | ''
}) {
  return (
    <div className="node-stat">
      <span className="k">{k}</span>
      <span className={`v ${tone || ''}`}>{v}</span>
    </div>
  )
}
