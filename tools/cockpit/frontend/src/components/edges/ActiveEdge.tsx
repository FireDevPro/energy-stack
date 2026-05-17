import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react'

type ActiveEdgeData = { active?: boolean; testId?: string }

export function ActiveEdge(props: EdgeProps) {
  const [path] = getBezierPath(props)
  const data = props.data as ActiveEdgeData | undefined
  const active = data?.active ?? false
  const motionOk =
    typeof window === 'undefined'
      ? true
      : !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const stroke = active ? '#38bdf8' : '#3f3f46'
  const animatedAttr = active && motionOk ? 'true' : 'false'

  return (
    <g data-testid={data?.testId} data-animated={animatedAttr}>
      <BaseEdge
        id={props.id}
        path={path}
        style={{
          stroke,
          strokeWidth: active ? 2 : 1.5,
          strokeDasharray: active ? '6 6' : undefined,
        }}
        className={active && motionOk ? 'animate-march' : undefined}
      />
    </g>
  )
}
