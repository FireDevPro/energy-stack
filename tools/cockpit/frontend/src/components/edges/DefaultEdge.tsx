import { BaseEdge, getBezierPath, type EdgeProps } from '@xyflow/react'

export function DefaultEdge(props: EdgeProps) {
  const [path] = getBezierPath(props)
  return (
    <BaseEdge
      id={props.id}
      path={path}
      style={{ stroke: '#3f3f46', strokeWidth: 1.5 }}
    />
  )
}
