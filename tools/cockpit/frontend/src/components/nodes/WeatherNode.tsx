import { BaseNode, Stat } from './BaseNode'
import type { BaseNodeEnvelope, WeatherDetails } from '../../types'

export function WeatherNode({
  data,
  pos,
  nodeW,
  nodeH,
}: {
  data: BaseNodeEnvelope<WeatherDetails>
  pos: { x: number; y: number }
  nodeW: number
  nodeH: number
}) {
  const d = data.details
  return (
    <BaseNode
      id="weather"
      testId="node-weather"
      pos={pos}
      nodeW={nodeW}
      nodeH={nodeH}
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title={data.title}
      subtitle={data.subtitle}
    >
      <div className="node-stats">
        <Stat k="now" v={`${d.current_outdoor_f}°`} />
        <Stat
          k="high"
          v={`${d.today_high_f}°`}
          tone={d.heat_advisory ? 'warn' : ''}
        />
        <Stat k="dew" v={`${d.dewpoint_max_f}°`} />
      </div>
    </BaseNode>
  )
}
