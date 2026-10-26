import { useEffect, useId, useState } from 'react'
import { LIVE_LAYERS, type LiveLayer, type MapWeather } from '../lib/liveWeather'
import type { LiveStatus } from './liveWeatherLayer'
import './LiveWeather.css'

interface Props {
  choice: MapWeather
  onChoose: (choice: MapWeather) => void
  hasPlan: boolean // with a plan, turning the live weather off brings back the route risk
  status: LiveStatus | null
  timeZone?: string // the plan's zone; without a plan, the viewer's own
  onRetry: () => void
}

const MINUTE = 60_000

// The map's weather switch, and for a live layer its badge (what it is and when it was
// observed) and a key to its colours.
export function LiveWeather({ choice, onChoose, hasPlan, status, timeZone, onRetry }: Props) {
  const labelId = useId()
  const options: Array<{ value: MapWeather; label: string }> = [
    { value: 'off', label: hasPlan ? 'Route risk' : 'Off' },
    { value: 'radar', label: 'Radar now' },
    { value: 'satellite', label: 'Satellite now' },
  ]
  return (
    <div className="live-weather">
      <div className="live-switch" role="group" aria-labelledby={labelId}>
        <span id={labelId} className="live-switch-label">
          Map weather
        </span>
        {options.map((o) => (
          <button key={o.value} type="button" aria-pressed={choice === o.value} onClick={() => onChoose(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
      {choice !== 'off' && status && (
        <LiveCard layer={choice} status={status} timeZone={timeZone} hasPlan={hasPlan} onRetry={onRetry} />
      )}
    </div>
  )
}

interface CardProps {
  layer: LiveLayer
  status: LiveStatus
  timeZone?: string
  hasPlan: boolean
  onRetry: () => void
}

function LiveCard({ layer, status, timeZone, hasPlan, onRetry }: CardProps) {
  const live = status.state === 'live'
  const { name, staleMs } = LIVE_LAYERS[layer]
  const observed = live && status.observed ? Date.parse(status.observed) : null
  const now = useClock(observed !== null, observed)
  const ageMs = observed === null ? 0 : Math.max(0, now - observed)
  const stale = ageMs > staleMs

  let headline
  if (status.state === 'loading') headline = `Loading live ${name}…`
  else if (status.state === 'unavailable') headline = `Live ${name} unavailable`
  else {
    const kind = stale ? `${capitalize(name)} delayed` : `Live ${name}`
    const at =
      observed === null
        ? 'latest image'
        : `${layer === 'radar' ? 'observed ' : ''}${clockTime(observed, timeZone)}`
    headline = (
      <>
        <span className="live-kind">
          <span className="live-dot" aria-hidden />
          {kind}
        </span>{' '}
        <span className="live-sep" aria-hidden>
          ·
        </span>{' '}
        {at}
      </>
    )
  }

  return (
    <div className={`live-card${stale ? ' stale' : ''}`}>
      <p className="live-badge">
        {/* Announced when the layer loads or fails, not every minute as its age ticks up */}
        <span role="status">{headline}</span>
        {observed !== null && <span className="live-age">({Math.round(ageMs / MINUTE)} min ago)</span>}
      </p>
      {status.state === 'unavailable' && (
        <button type="button" className="live-retry" onClick={onRetry}>
          Try again
        </button>
      )}
      {live && <Legend layer={layer} />}
      {live && hasPlan && (
        <p className="live-note">Weather now, not when you get there. The route dots still show risk at your ETA.</p>
      )}
    </div>
  )
}

// Swatches: light to heavy echoes, then the colder (higher) the cloud tops, the stormier
const LEGEND: Record<LiveLayer, { label: string; keys: string[] }> = {
  radar: { label: 'Radar colours', keys: ['Light', 'Moderate', 'Heavy', 'Storms', 'Hail'] },
  satellite: { label: 'Infrared colours', keys: ['Clear', 'Low cloud', 'High cloud', 'Storm tops'] },
}

function Legend({ layer }: { layer: LiveLayer }) {
  const { label, keys } = LEGEND[layer]
  return (
    <div className={`live-legend live-legend-${layer}`} role="img" aria-label={`${label}: ${keys.join(', ')}`}>
      <span className="live-legend-bar" />
      <span className="live-legend-keys" aria-hidden>
        {keys.map((k) => (
          <span key={k}>{k}</span>
        ))}
      </span>
    </div>
  )
}

// The time now, read again every half minute while on and whenever a new image arrives,
// so "8 min ago" stays true
function useClock(on: boolean, image: unknown) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!on) return
    const tick = () => setNow(Date.now())
    tick()
    const timer = setInterval(tick, MINUTE / 2)
    return () => clearInterval(timer)
  }, [on, image])
  return now
}

// "2:31 PM", or "2:31 PM CDT" on a plan's clock, which may not be the viewer's
function clockTime(at: number, timeZone?: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    timeZone,
    timeZoneName: timeZone ? 'short' : undefined,
  }).format(at)
}

function capitalize(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1)
}
