import type { NodeProps, Node } from '@xyflow/react'
import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, WeatherDetails } from '../../types'

type WeatherNodeType = Node<BaseNodeEnvelope<WeatherDetails>>

export function WeatherNode({ data, id }: NodeProps<WeatherNodeType>) {
  return (
    <BaseNode
      nodeId={id}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
      testId="node-weather"
    >
      <div>outdoor: {data.details.current_outdoor_f.toFixed(1)}°F</div>
      <div>
        high: {data.details.today_high_f}°F (apparent{' '}
        {data.details.apparent_max_f}°F)
      </div>
      <div>dewpoint: {data.details.dewpoint_max_f}°F</div>
      {data.details.heat_advisory && (
        <div className="text-rose-300">heat advisory</div>
      )}
    </BaseNode>
  )
}
