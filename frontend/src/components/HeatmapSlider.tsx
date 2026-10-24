import { useMemo, useState, type Ref } from 'react'
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
  timeZone?: string // the plan's zone (the origin's); the slider's clock reads in it
  inert?: boolean // while a new plan loads, this panel still shows the old one's forecast
  visible: boolean
  onToggle: () => void
  onHourChange: (hour: number) => void
  ref?: Ref<HTMLElement>
}

// Map overlay: weather on/off, the 0-48 h slider and the colour legend.
export function HeatmapSlider({ state, timeZone, inert, visible, onToggle, onHourChange, ref }: Props) {
  const { status } = state
  return (
    <section ref={ref} className="heat-panel" aria-label="Weather overlay" inert={inert}>
      {(status === 'loading' || status === 'ready') && (
        <button type="button" className="heat-toggle" aria-pressed={visible} onClick={onToggle}>
          <span className="heat-switch" aria-hidden />
          Weather overlay
        </button>
      )}
      {/* Stays mounted while hidden so the chosen hour survives a toggle. */}
      {state.status === 'ready' && (
        <HourSlider
          key={state.data.plan_id}
          heatmap={state.data}
          timeZone={timeZone}
          hidden={!visible}
          onChange={onHourChange}
        />
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
      {status === 'idle' && (
        <p className="heat-muted">Plan a trip to see the weather risk along each route.</p>
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
  timeZone?: string
  hidden: boolean
  onChange: (hour: number) => void
}

// The worst level anywhere on the grid over the whole forecast, and the first hour it shows up
function worstHour(heatmap: Heatmap) {
  let level = 0
  let hour = 0
  for (let h = 0; h <= heatmap.hours; h++) {
    for (const p of heatmap.points) {
      if (p.v[h] > level) {
        level = p.v[h]
        hour = h
      }
    }
  }
  return { level, hour }
}

// Owns the hour so a slide re-renders only this component; the map is told directly.
function HourSlider({ heatmap, timeZone, hidden, onChange }: SliderProps) {
  const [hour, setHour] = useState(0)
  const worst = useMemo(() => worstHour(heatmap), [heatmap])
  // Hour 0 is start_time, a UTC instant (the departure floored to the hour)
  const clockAt = (h: number) =>
    formatTime(new Date(Date.parse(heatmap.start_time) + h * HOUR_MS).toISOString(), timeZone)
  const clock = clockAt(hour)
  const worstLevel = LEVELS[worst.level]

  function go(next: number) {
    setHour(next)
    onChange(next)
  }

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
        onChange={(e) => go(Number(e.target.value))}
      />
      <Scale hours={heatmap.hours} />
      {/* Most hours are calm, so say where the bad weather is instead of making people hunt */}
      {worst.level > 0 ? (
        <p className="heat-worst">
          Worst in the next {heatmap.hours} h:{' '}
          <span className="heat-worst-level">
            <span className="heat-swatch" style={{ background: LEVEL_COLOR[worstLevel] }} />
            {LEVEL_LABEL[worstLevel]}
          </span>{' '}
          at {clockAt(worst.hour)}
          {hour !== worst.hour && (
            <button type="button" className="heat-jump" onClick={() => go(worst.hour)}>
              Show
            </button>
          )}
        </p>
      ) : (
        <p className="heat-worst heat-muted">Calm: nothing above Low along these routes for {heatmap.hours} h.</p>
      )}
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
