import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react'

type ActionEdgeData = { shadow?: boolean; testId?: string }

export function ActionEdge(props: EdgeProps) {
  const [path] = getBezierPath(props)
  const data = props.data as ActionEdgeData | undefined
  const shadow = data?.shadow ?? false
  const style = shadow ? 'dashed' : 'solid'
  return (
    <g data-testid={data?.testId} data-edge-style={style}>
      <BaseEdge
        id={props.id}
        path={path}
        style={{
          stroke: shadow ? '#71717a' : '#10b981',
          strokeWidth: 2,
          strokeDasharray: shadow ? '8 5' : undefined,
        }}
      />
    </g>
  )
}
