import { BaseNode } from './BaseNode'
import type { BaseNodeEnvelope, WeatherDetails } from '../../types'

export function WeatherNode({ data }: { data: BaseNodeEnvelope<WeatherDetails> }) {
  const d = data.details
  return (
    <BaseNode
      role_state={data.role_state}
      freshness={data.freshness}
      freshness_label={data.freshness_label}
      title="Weather"
      headline={`${Math.round(d.today_high_f)}°F high`}
      testId="node-weather"
    >
      <div>{Math.round(d.current_outdoor_f)}°F now · dew {Math.round(d.dewpoint_max_f)}°F</div>
      {d.heat_advisory && (
        <div className="font-semibold text-rose-300">heat advisory</div>
      )}
    </BaseNode>
  )
}
