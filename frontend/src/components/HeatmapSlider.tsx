import { useState, type Ref } from 'react'
import type { Heatmap } from '../api/types'
import { LEVELS, LEVEL_COLOR, LEVEL_LABEL, formatTime } from '../lib/risk'
import './HeatmapSlider.css'

export type HeatmapState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: Heatmap }
  | { status: 'error'; message: string }

const HOUR_MS = 60 * 60 * 1000
const HOURS = 48

interface Props {
  state: HeatmapState
  visible: boolean
  onToggle: () => void
  onHourChange: (hour: number) => void
  ref?: Ref<HTMLElement>
}

// Map overlay: weather on/off, the 0-48 h slider and the colour legend.
export function HeatmapSlider({ state, visible, onToggle, onHourChange, ref }: Props) {
  const { status } = state
  return (
    <section ref={ref} className="heat-panel" aria-label="Weather overlay">
      {(status === 'loading' || status === 'ready') && (
        <button type="button" className="heat-toggle" aria-pressed={visible} onClick={onToggle}>
          <span className="heat-switch" aria-hidden />
          Weather overlay
        </button>
      )}
      {/* Stays mounted while hidden so the chosen hour survives a toggle. */}
      {state.status === 'ready' && (
        <HourSlider key={state.data.plan_id} heatmap={state.data} hidden={!visible} onChange={onHourChange} />
      )}
      {/* Same shape as the slider, so the panel doesn't jump when the forecast arrives. */}
      {status === 'loading' && (
        <div className="heat-slider" hidden={!visible}>
          <span className="heat-time heat-muted" role="status">
            Loading forecast…
          </span>
          <input type="range" min={0} max={HOURS} defaultValue={0} disabled aria-label="Forecast hour" />
          <Scale hours={HOURS} />
        </div>
      )}
      {state.status === 'error' && (
        <p className="heat-error" role="status">
          Weather overlay unavailable. {state.message}
        </p>
      )}
      <ul className="heat-legend" aria-label="Risk levels">
        {LEVELS.map((l) => (
          <li key={l}>
            <span className="heat-swatch" style={{ background: LEVEL_COLOR[l] }} />
            {LEVEL_LABEL[l]}
          </li>
        ))}
      </ul>
      {(status === 'loading' || status === 'ready') && visible && (
        <p className="heat-note">Shading: forecast risk at that hour. Dots: risk when you pass.</p>
      )}
    </section>
  )
}

interface SliderProps {
  heatmap: Heatmap
  hidden: boolean
  onChange: (hour: number) => void
}

// Owns the hour so a slide re-renders only this component; the map is told directly.
function HourSlider({ heatmap, hidden, onChange }: SliderProps) {
  const [hour, setHour] = useState(0)
  const clock = formatTime(new Date(Date.parse(heatmap.start_time) + hour * HOUR_MS).toISOString())

  return (
    <div className="heat-slider" hidden={hidden}>
      <output htmlFor="heat-hour" className="heat-time">
        {clock} <span className="heat-muted">{hour === 0 ? 'departure' : `+${hour} h`}</span>
      </output>
      <input
        id="heat-hour"
        type="range"
        min={0}
        max={heatmap.hours}
        step={1}
        value={hour}
        aria-label="Forecast hour"
        aria-valuetext={hour === 0 ? `${clock}, at departure` : `${clock}, ${hour} hours after departure`}
        onChange={(e) => {
          const next = Number(e.target.value)
          setHour(next)
          onChange(next)
        }}
      />
      <Scale hours={heatmap.hours} />
    </div>
  )
}

function Scale({ hours }: { hours: number }) {
  return (
    <div className="heat-scale" aria-hidden>
      <span>Depart</span>
      <span>+{hours / 2} h</span>
      <span>+{hours} h</span>
    </div>
  )
}
