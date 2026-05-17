import { type ReactNode } from 'react'

export type ChipTone =
  | 'neutral'
  | 'sky'
  | 'emerald'
  | 'amber'
  | 'rose'
  | 'zinc'

const TONE: Record<ChipTone, string> = {
  neutral: 'bg-zinc-800 text-zinc-100 border-zinc-700',
  sky: 'bg-sky-500/20 text-sky-200 border-sky-500/40',
  emerald: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/40',
  amber: 'bg-amber-400/20 text-amber-200 border-amber-400/40',
  rose: 'bg-rose-500/20 text-rose-200 border-rose-500/40',
  zinc: 'bg-zinc-900 text-zinc-400 border-zinc-700',
}

export function Chip({
  tone = 'neutral',
  children,
  testId,
}: {
  tone?: ChipTone
  children: ReactNode
  testId?: string
}) {
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${TONE[tone]}`}
    >
      {children}
    </span>
  )
}
